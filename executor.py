"""
Executor Inteligente de Ordens — BotBinance
=============================================
Funcionalidades:
  - Ordens LIMIT (não market) para evitar slippage
  - Trailing Stop automático (stop sobe com o preço)
  - Partial Take Profit (fecha 50% no alvo 1, deixa 50% correr)
  - Monitoramento de posição aberta em thread separada
  - Modo SIMULAÇÃO (paper trading) sem executar ordens reais

Uso:
  from executor import Executor
  ex = Executor(simulacao=True)
  ex.abrir_long(preco, tamanho_btc, stop, target)
"""

import hashlib
import hmac
import threading
import time
import uuid
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import requests

import database
import risco as gestao_risco
from config.runtime_settings import API_KEY, API_SECRET, REST_BASE_URL

# P0-1: endpoint unico vindo do config (spot por padrao) — nada de hardcode
# divergente entre modulos (sinal e execucao no MESMO mercado).
BASE_URL = REST_BASE_URL

# Precisão de FALLBACK por par (usada só se o exchangeInfo falhar no boot).
# A fonte de verdade e o exchangeInfo da Binance — ver _carregar_precisao().
_PRECISAO = {
    "BTCUSDT": {"qty_step": 0.00001, "min_qty": 0.00001, "price_prec": 2, "tick_size": "0.01"},
    "ETHUSDT": {"qty_step": 0.0001, "min_qty": 0.0001, "price_prec": 2, "tick_size": "0.01"},
    "SOLUSDT": {"qty_step": 0.001, "min_qty": 0.001, "price_prec": 2, "tick_size": "0.01"},
}
_PRECISAO_DEFAULT = {"qty_step": 0.001, "min_qty": 0.001, "price_prec": 2, "tick_size": "0.01"}

_precisao_cache: dict[str, dict] = {}


def _decimais(valor_str: str) -> int:
    """Nº de casas decimais de um passo tipo '0.01' -> 2, '1' -> 0."""
    d = Decimal(valor_str).normalize()
    exp = d.as_tuple().exponent
    return -exp if exp < 0 else 0


def _carregar_precisao(symbol: str) -> dict:
    """Busca LOT_SIZE/PRICE_FILTER reais do exchangeInfo (fonte de verdade).
    Cai no _PRECISAO hardcoded se a chamada falhar. Cacheado por símbolo.

    Corrige divergências do hardcode (ex.: SOLUSDT spot tem tick 0.01, não
    price_prec 3 — preço com 3 casas era REJEITADO pelo PRICE_FILTER)."""
    symbol = symbol.upper()
    if symbol in _precisao_cache:
        return _precisao_cache[symbol]
    try:
        r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=8)
        filtros = {f["filterType"]: f for f in r.json()["symbols"][0]["filters"]}
        step = filtros["LOT_SIZE"]["stepSize"]
        tick = filtros["PRICE_FILTER"]["tickSize"]
        prec = {
            "qty_step": float(step),
            "min_qty": float(filtros["LOT_SIZE"]["minQty"]),
            "price_prec": _decimais(tick),
            "tick_size": Decimal(tick).normalize().__str__(),
            "step_size": Decimal(step).normalize().__str__(),
        }
    except Exception as e:
        print(f"[EXEC] exchangeInfo indisponivel p/ {symbol} ({e}) — usando fallback")
        prec = dict(_PRECISAO.get(symbol, _PRECISAO_DEFAULT))
        prec.setdefault("step_size", Decimal(str(prec["qty_step"])).normalize().__str__())
    _precisao_cache[symbol] = prec
    return prec


TRAILING_ATIVACAO = 0.01  # ativa trailing após 1% de ganho
TRAILING_DISTANCIA = 0.008  # stop segue 0.8% abaixo do pico


def avaliar_tick_monitor(
    entrada,
    stop_atual,
    target1,
    target2,
    parcial_feita,
    preco,
    preco_pico,
    trailing_ativacao=TRAILING_ATIVACAO,
    trailing_distancia=TRAILING_DISTANCIA,
):
    """
    Decisão PURA (sem efeitos colaterais) de um tick do monitor de posição LONG.

    Espelha exatamente a ordem e os limiares do loop de _monitorar(): stop loss e
    take-profit final são terminais (encerram); take-profit parcial e trailing
    podem coexistir no mesmo tick. Todas as comparações usam o snapshot da posição
    (stop_atual/target1/target2/parcial_feita), como no loop original.

    Retorna dict:
      fechar_total:        None | "Stop Loss" | "Take Profit Final"
      encerrar:            bool — encerra o loop (break)
      fechar_parcial:      bool — take-profit parcial (50%)
      stop_breakeven:      None | float — novo stop pós-parcial (entrada*1.002)
      preco_pico:          float — pico atualizado
      novo_stop_trailing:  None | float — novo stop do trailing (se subir)
    """
    ganho_pct = (preco - entrada) / entrada
    acao = {
        "fechar_total": None,
        "encerrar": False,
        "fechar_parcial": False,
        "stop_breakeven": None,
        "preco_pico": preco_pico,
        "novo_stop_trailing": None,
    }

    # 1. Stop Loss atingido (terminal)
    if preco <= stop_atual:
        acao["fechar_total"] = "Stop Loss"
        acao["encerrar"] = True
        return acao

    # 2. Partial Take Profit (50% no target1) — não encerra
    if not parcial_feita and preco >= target1:
        acao["fechar_parcial"] = True
        acao["stop_breakeven"] = entrada * 1.002

    # 3. Target 2 (fecha tudo) — terminal
    if parcial_feita and preco >= target2:
        acao["fechar_total"] = "Take Profit Final"
        acao["encerrar"] = True
        return acao

    # 4. Trailing Stop (ativa após ganho >= trailing_ativacao)
    if ganho_pct >= trailing_ativacao:
        pico = preco if preco > preco_pico else preco_pico
        acao["preco_pico"] = pico
        novo_stop = pico * (1 - trailing_distancia)
        if novo_stop > stop_atual:
            acao["novo_stop_trailing"] = novo_stop

    return acao


class Executor:
    def __init__(self, simulacao=True, symbol="BTCUSDT"):
        self.simulacao = simulacao
        self.symbol = symbol.upper()
        self.posicao = None
        self._monitor = None
        self._ativo = False
        self._lock = threading.Lock()  # protege o estado da posicao (M-2)
        prec = _carregar_precisao(self.symbol)  # exchangeInfo real (fonte de verdade)
        self._qty_step = prec["qty_step"]
        self._min_qty = prec["min_qty"]
        self._price_prec = prec["price_prec"]
        # Decimal p/ snapping EXATO ao step/tick — float pode gerar 0.5219999...
        # e derrubar qty/preco valido no filtro da Binance.
        self._step_dec = Decimal(prec.get("step_size", str(prec["qty_step"])))
        self._tick_dec = Decimal(prec.get("tick_size", "0.01"))
        self._offset_ms = 0
        if not simulacao:
            self._sincronizar_relogio()  # P0-4: evita -1021 por clock drift
        modo = "SIMULACAO (Paper Trading)" if simulacao else "REAL (Capital Real)"
        print(f"[EXEC] Executor iniciado — {self.symbol} | Modo: {modo}")

    def _arredondar_qty(self, qty):
        # Floor ao step (nunca arredonda p/ cima — evita exceder saldo/risco).
        d = (Decimal(str(qty)) / self._step_dec).to_integral_value(ROUND_DOWN) * self._step_dec
        return float(d)

    def _arredondar_preco(self, preco):
        # Snap ao tick mais proximo (exato, sem artefato de float).
        d = (Decimal(str(preco)) / self._tick_dec).to_integral_value(ROUND_HALF_UP) * self._tick_dec
        return float(d)

    # ── Assinatura da API ──────────────────────────────────────

    def _assinar(self, params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _headers(self):
        return {"X-MBX-APIKEY": API_KEY}

    def _sincronizar_relogio(self):
        """P0-4: offset serverTime-local evita rejeicao -1021 por clock drift."""
        try:
            r = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
            server_ms = int(r.json()["serverTime"])
            self._offset_ms = server_ms - int(time.time() * 1000)
            if abs(self._offset_ms) > 1000:
                print(f"[EXEC] Clock drift detectado: {self._offset_ms}ms (compensado)")
        except Exception:
            self._offset_ms = 0  # sem sync, segue com relogio local

    def _ts(self):
        return int(time.time() * 1000) + getattr(self, "_offset_ms", 0)

    def _request_assinado(self, metodo, path, params, timeout=10, tentativas=3):
        """P0-4: requisicao assinada com recvWindow + retry/backoff para
        429/-1003/5xx/timeout. Retorna dict da Binance ou {"erro": ...}.
        """
        ultima_falha = "sem tentativa"
        for i in range(tentativas):
            p = dict(params)
            p["timestamp"] = self._ts()
            p["recvWindow"] = 5000
            p["signature"] = self._assinar(p)
            try:
                r = requests.request(
                    metodo, f"{BASE_URL}{path}", params=p, headers=self._headers(), timeout=timeout
                )
                data = r.json()
            except Exception as e:
                ultima_falha = f"falha de rede/resposta: {e}"
                time.sleep(2**i)
                continue
            codigo = data.get("code") if isinstance(data, dict) else None
            # Rate limit / banimento temporario / erro interno: retry com backoff
            if r.status_code in (429, 418, 500, 502, 503) or codigo in (-1003, -1000):
                ultima_falha = f"HTTP {r.status_code} code={codigo}: {data.get('msg', '')}"
                time.sleep(2**i)
                continue
            # -1021 (timestamp fora do recvWindow): ressincroniza e tenta de novo
            if codigo == -1021:
                self._sincronizar_relogio()
                ultima_falha = f"-1021 timestamp: {data.get('msg', '')}"
                continue
            return data
        return {"erro": ultima_falha, "timeout_rede": "falha de rede" in ultima_falha}

    def _consultar_ordem(self, client_order_id):
        """P0-4: apos timeout de rede, confirma na exchange se a ordem existe —
        evita 'ordem fantasma' (enviada e executada, mas resposta perdida)."""
        data = self._request_assinado(
            "GET",
            "/api/v3/order",
            {"symbol": self.symbol, "origClientOrderId": client_order_id},
            timeout=10,
            tentativas=2,
        )
        return data if isinstance(data, dict) and "orderId" in data else None

    # ── Preço atual ────────────────────────────────────────────

    def get_preco(self):
        try:
            r = requests.get(
                f"{BASE_URL}/api/v3/ticker/price", params={"symbol": self.symbol}, timeout=5
            )
            return float(r.json()["price"])
        except Exception:
            return 0.0

    # ── Enviar ordem ───────────────────────────────────────────

    def _enviar_ordem(self, lado, qty, preco=None, tipo="LIMIT"):
        """
        lado:  BUY ou SELL
        tipo:  LIMIT ou MARKET
        """
        qty = self._arredondar_qty(qty)
        if qty < self._min_qty:
            return {"erro": f"Quantidade {qty} abaixo do minimo {self._min_qty}"}

        log = (
            f"[EXEC] {'[SIM]' if self.simulacao else ''} "
            f"ORDEM {tipo} {lado} {qty} {self.symbol}"
            f"{f' @ ${preco:,.2f}' if preco else ''}"
        )
        print(log)

        if self.simulacao:
            preco_exec = preco or self.get_preco()
            return {
                "orderId": f"SIM-{int(time.time())}",
                "status": "FILLED",
                "executedQty": qty,
                "price": preco_exec,
                "simulacao": True,
            }

        # Ordem real (P0-4: idempotente via newClientOrderId + retry + confirmacao pos-timeout)
        client_id = f"bx-{uuid.uuid4().hex[:20]}"
        params = {
            "symbol": self.symbol,
            "side": lado,
            "type": tipo,
            "quantity": qty,
            "newClientOrderId": client_id,
        }
        if tipo == "LIMIT" and preco:
            params["price"] = self._arredondar_preco(preco)
            params["timeInForce"] = "GTC"

        data = self._request_assinado("POST", "/api/v3/order", params)

        # Timeout de rede: a ordem PODE ter sido aceita — confirmar antes de assumir falha
        if isinstance(data, dict) and data.get("timeout_rede"):
            existente = self._consultar_ordem(client_id)
            if existente:
                print(
                    f"[EXEC] Ordem recuperada pos-timeout ({client_id}): {existente.get('status')}"
                )
                return existente

        # Erros da Binance chegam como {"code": -xxxx, "msg": "..."} sem orderId
        if isinstance(data, dict) and "orderId" not in data:
            return {"erro": data.get("erro") or data.get("msg", "resposta sem orderId"), **data}
        return data

    # ── Stop loss NA EXCHANGE (P0-2) ───────────────────────────
    # O monitor local (loop de 10s) vira redundancia: a protecao primaria e uma
    # ordem STOP_LOSS_LIMIT viva na Binance — sobrevive a crash/travamento do bot.

    def _colocar_stop_exchange(self, qty, stop_price):
        """Coloca STOP_LOSS_LIMIT SELL na exchange. Retorna orderId ou None."""
        if self.simulacao:
            return None
        qty = self._arredondar_qty(qty)
        stop = self._arredondar_preco(stop_price)
        # price um pouco abaixo do stopPrice para garantir fill do limit
        params = {
            "symbol": self.symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "quantity": qty,
            "stopPrice": stop,
            "price": self._arredondar_preco(stop * 0.997),
            "timeInForce": "GTC",
            "newClientOrderId": f"bxstop-{uuid.uuid4().hex[:16]}",
        }
        data = self._request_assinado("POST", "/api/v3/order", params)
        if isinstance(data, dict) and "orderId" in data:
            print(f"[EXEC] Stop na exchange @ ${stop:,.2f} (orderId={data['orderId']})")
            return data["orderId"]
        print(f"[EXEC] AVISO: falha ao colocar stop na exchange: {data}")
        return None

    def _cancelar_ordem_exchange(self, order_id):
        """Cancela ordem aberta. True se cancelou (ou ja nao existia)."""
        if self.simulacao or not order_id:
            return True
        data = self._request_assinado(
            "DELETE", "/api/v3/order", {"symbol": self.symbol, "orderId": order_id}
        )
        if isinstance(data, dict) and ("orderId" in data or data.get("code") == -2011):
            return True  # -2011 = ordem ja nao existe (executada/cancelada)
        print(f"[EXEC] AVISO: falha ao cancelar stop {order_id}: {data}")
        return False

    def _mover_stop_exchange(self, novo_stop):
        """Cancel-then-replace do stop na exchange (spot: o stop antigo trava o
        saldo, entao cancela primeiro). Se o novo falhar, tenta restaurar o stop
        no nivel ANTIGO — melhor stop desatualizado do que posicao sem stop."""
        if self.simulacao or not self.posicao:
            return None
        antigo = self.posicao.get("stop_order_id")
        stop_antigo = self.posicao.get("stop_atual")
        qty = self.posicao["tamanho_btc"]
        if antigo and not self._cancelar_ordem_exchange(antigo):
            return antigo  # nao conseguiu cancelar: protecao antiga segue viva
        novo_id = self._colocar_stop_exchange(qty, novo_stop)
        if novo_id is None and stop_antigo:
            print("[EXEC] Replace falhou — restaurando stop no nivel antigo")
            novo_id = self._colocar_stop_exchange(qty, stop_antigo)
        return novo_id

    # ── Abrir posição LONG ─────────────────────────────────────

    def abrir_long(self, preco_entrada, tamanho_btc, stop_loss, take_profit):
        if self.posicao:
            print("[EXEC] Ja existe posicao aberta. Aguardar fechamento.")
            return False

        # Validar risco antes de executar
        saldo = gestao_risco.get_saldo_usdt()
        if not self.simulacao:
            validacao = gestao_risco.validar_trade("COMPRA", preco_entrada, saldo)
            if not validacao["pode"]:
                print(f"[EXEC] Trade bloqueado pelo risco: {validacao['motivo']}")
                return False

        # Usar preço limite ligeiramente acima (garante execução)
        preco_limit = self._arredondar_preco(preco_entrada * 1.001)
        resp = self._enviar_ordem("BUY", tamanho_btc, preco_limit, "LIMIT")

        if resp.get("status") != "FILLED" and not self.simulacao:
            print(f"[EXEC] Ordem nao preenchida: {resp}")
            return False

        preco_exec = float(resp.get("price", preco_entrada))

        # P0-2: protecao primaria NA EXCHANGE (sobrevive a crash do bot)
        stop_order_id = self._colocar_stop_exchange(tamanho_btc, stop_loss)

        with self._lock:
            self.posicao = {
                "tipo": "LONG",
                "entrada": preco_exec,
                "tamanho_btc": tamanho_btc,
                "stop_inicial": stop_loss,
                "stop_atual": stop_loss,
                "target1": take_profit,
                "target2": preco_exec * 1.05,  # alvo 2: 5%
                "parcial_feita": False,
                "abertura": datetime.now().isoformat(),
                "order_id": resp.get("orderId"),
                "stop_order_id": stop_order_id,
            }

        # P0-3: posicao persistida — sobrevive a restart (reconciliada no boot)
        try:
            database.salvar_posicao_aberta(self.symbol, self.posicao)
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao persistir posicao: {e}")

        gestao_risco._estado_risco["posicoes_abertas"] += 1
        gestao_risco.persistir_estado()
        print(
            f"[EXEC] LONG aberto @ ${preco_exec:,.2f} | "
            f"Stop: ${stop_loss:,.2f} | Target: ${take_profit:,.2f}"
        )

        # Inicia monitoramento
        self._ativo = True
        self._monitor = threading.Thread(target=self._monitorar, daemon=True)
        self._monitor.start()
        return True

    # ── Fechar posição ─────────────────────────────────────────

    def _persistir_posicao(self):
        """P0-3: espelha o estado atual da posicao no DB (stop/parcial mudam)."""
        try:
            if self.posicao:
                database.salvar_posicao_aberta(self.symbol, self.posicao)
            else:
                database.remover_posicao_aberta(self.symbol)
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao persistir posicao: {e}")

    def fechar_posicao(self, preco, motivo, parcial=False):
        if not self.posicao:
            return

        # P0-2: liberar o saldo travado pelo stop na exchange antes do SELL
        stop_id = self.posicao.get("stop_order_id")
        if stop_id:
            self._cancelar_ordem_exchange(stop_id)
            self.posicao["stop_order_id"] = None

        qty = self.posicao["tamanho_btc"]
        if parcial:
            qty = qty / 2

        resp = self._enviar_ordem("SELL", qty, tipo="MARKET")

        # P1-8: nao tratar como fechada se a ordem real nao preencheu — senao o bot
        # acharia que saiu da posicao sem ter saido. Mantem a posicao para retry.
        if not self.simulacao and resp.get("status") != "FILLED":
            print(
                f"[EXEC] FALHA ao fechar posicao (ordem nao preenchida): {resp}. "
                f"Posicao MANTIDA — nova tentativa no proximo ciclo."
            )
            # P0-2: SELL falhou e o stop foi cancelado — RECOLOCA a protecao
            self.posicao["stop_order_id"] = self._colocar_stop_exchange(
                self.posicao["tamanho_btc"], self.posicao["stop_atual"]
            )
            self._persistir_posicao()
            return

        # Ordem preenchida: agora sim aplica a mutacao do fechamento parcial
        if parcial:
            self.posicao["parcial_feita"] = True
            self.posicao["tamanho_btc"] = qty  # restante
            # P0-2: recoloca stop para a metade restante (o antigo foi cancelado)
            self.posicao["stop_order_id"] = self._colocar_stop_exchange(
                qty, self.posicao["stop_atual"]
            )
            self._persistir_posicao()

        pnl_pct = (preco - self.posicao["entrada"]) / self.posicao["entrada"] * 100
        pnl_usdt = qty * (preco - self.posicao["entrada"])

        print(
            f"[EXEC] {'PARCIAL' if parcial else 'TOTAL'} FECHADO @ ${preco:,.2f} | "
            f"PnL: {'+' if pnl_usdt>=0 else ''}{pnl_usdt:.2f} USDT ({pnl_pct:+.2f}%) | "
            f"Motivo: {motivo}"
        )

        gestao_risco.registrar_resultado(pnl_usdt)
        database.salvar_sinal(
            "FECHAR_LONG" if pnl_usdt >= 0 else "STOP",
            preco,
            f"{motivo} | PnL: {pnl_pct:+.2f}%",
            symbol=self.symbol,
            source="executor",
            executado=True,
        )

        if not parcial:
            with self._lock:
                self.posicao = None
                self._ativo = False
            self._persistir_posicao()  # P0-3: remove do DB
            gestao_risco._estado_risco["posicoes_abertas"] -= 1
            gestao_risco.persistir_estado()

    # ── Monitor de trailing stop ───────────────────────────────

    def _monitorar(self):
        """Thread que verifica stop/target e ajusta trailing stop.

        A decisão por-tick fica em avaliar_tick_monitor() (pura/testável); aqui
        há apenas I/O (preço, fechamento, sleep) e atualização de estado sob lock.
        """
        preco_pico = self.posicao["entrada"]

        while self._ativo and self.posicao:
            try:
                preco = self.get_preco()
                if preco <= 0:
                    time.sleep(5)
                    continue

                # Snapshot consistente da posicao (M-2). Lock liberado antes de
                # chamar fechar_posicao para evitar reentrancia.
                with self._lock:
                    pos = self.posicao
                if pos is None:
                    break

                d = avaliar_tick_monitor(
                    pos["entrada"],
                    pos["stop_atual"],
                    pos["target1"],
                    pos["target2"],
                    pos["parcial_feita"],
                    preco,
                    preco_pico,
                )
                preco_pico = d["preco_pico"]

                # Fechamento terminal (stop loss ou take-profit final)
                if d["encerrar"]:
                    self.fechar_posicao(preco, d["fechar_total"])
                    break

                # Take-profit parcial (50%) + stop em breakeven
                if d["fechar_parcial"]:
                    self.fechar_posicao(preco, "Take Profit Parcial (50%)", parcial=True)
                    if self.posicao:
                        self.posicao["stop_order_id"] = self._mover_stop_exchange(
                            d["stop_breakeven"]
                        )
                        self.posicao["stop_atual"] = d["stop_breakeven"]
                        self._persistir_posicao()
                        print(
                            f"[EXEC] Stop movido para breakeven: ${self.posicao['stop_atual']:,.2f}"
                        )

                # Trailing stop (pode coexistir com o parcial no mesmo tick)
                if d["novo_stop_trailing"] is not None and self.posicao:
                    self.posicao["stop_order_id"] = self._mover_stop_exchange(
                        d["novo_stop_trailing"]
                    )
                    self.posicao["stop_atual"] = d["novo_stop_trailing"]
                    self._persistir_posicao()
                    print(
                        f"[EXEC] Trailing Stop: ${d['novo_stop_trailing']:,.2f} (pico: ${preco_pico:,.2f})"
                    )

            except Exception as e:
                print(f"[EXEC] Erro no monitor: {e}")

            time.sleep(10)  # verificar a cada 10 segundos

    # ── Status da posição ──────────────────────────────────────

    def status(self):
        if not self.posicao:
            return {"posicao": "Nenhuma posicao aberta"}
        preco = self.get_preco()
        pos = self.posicao
        pnl = (preco - pos["entrada"]) / pos["entrada"] * 100
        return {
            "tipo": pos["tipo"],
            "entrada": pos["entrada"],
            "preco_atual": preco,
            "pnl_%": round(pnl, 2),
            "pnl_usdt": round(pos["tamanho_btc"] * (preco - pos["entrada"]), 2),
            "stop_atual": pos["stop_atual"],
            "target1": pos["target1"],
            "target2": pos["target2"],
            "parcial_feita": pos["parcial_feita"],
            "tamanho_btc": pos["tamanho_btc"],
        }


if __name__ == "__main__":
    # Teste em modo simulação
    ex = Executor(simulacao=True)
    preco_atual = ex.get_preco()
    print(f"\nPreco atual: ${preco_atual:,.2f}")

    stop = preco_atual * 0.985
    target = preco_atual * 1.030
    qty = 0.001  # 0.001 BTC para teste

    print(f"Abrindo LONG simulado: {qty} BTC @ ${preco_atual:,.2f}")
    print(f"Stop: ${stop:,.2f} | Target: ${target:,.2f}")
    ex.abrir_long(preco_atual, qty, stop, target)

    time.sleep(2)
    print("\nStatus da posicao:")
    for k, v in ex.status().items():
        print(f"  {k}: {v}")
