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

import hmac
import hashlib
import time
import threading
import requests
from datetime import datetime
import database
import risco as gestao_risco
from config.runtime_settings import API_KEY, API_SECRET

BASE_URL = "https://api.binance.com"

# Precisão por par (Binance Spot/Futures)
_PRECISAO = {
    "BTCUSDT":  {"qty_step": 0.00001, "min_qty": 0.00001, "price_prec": 2},
    "ETHUSDT":  {"qty_step": 0.001,   "min_qty": 0.001,   "price_prec": 2},
    "SOLUSDT":  {"qty_step": 0.1,     "min_qty": 0.1,     "price_prec": 3},
}
_PRECISAO_DEFAULT = {"qty_step": 0.001, "min_qty": 0.001, "price_prec": 2}


class Executor:
    def __init__(self, simulacao=True, symbol="BTCUSDT"):
        self.simulacao  = simulacao
        self.symbol     = symbol.upper()
        self.posicao    = None
        self._monitor   = None
        self._ativo     = False
        self._lock      = threading.Lock()  # protege o estado da posicao (M-2)
        prec = _PRECISAO.get(self.symbol, _PRECISAO_DEFAULT)
        self._qty_step   = prec["qty_step"]
        self._min_qty    = prec["min_qty"]
        self._price_prec = prec["price_prec"]
        modo = "SIMULACAO (Paper Trading)" if simulacao else "REAL (Capital Real)"
        print(f"[EXEC] Executor iniciado — {self.symbol} | Modo: {modo}")

    def _arredondar_qty(self, qty):
        return round(int(qty / self._qty_step) * self._qty_step, 8)

    def _arredondar_preco(self, preco):
        return round(preco, self._price_prec)

    # ── Assinatura da API ──────────────────────────────────────

    def _assinar(self, params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _headers(self):
        return {"X-MBX-APIKEY": API_KEY}

    # ── Preço atual ────────────────────────────────────────────

    def get_preco(self):
        try:
            r = requests.get(f"{BASE_URL}/api/v3/ticker/price",
                             params={"symbol": self.symbol}, timeout=5)
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

        log = (f"[EXEC] {'[SIM]' if self.simulacao else ''} "
               f"ORDEM {tipo} {lado} {qty} {self.symbol}"
               f"{f' @ ${preco:,.2f}' if preco else ''}")
        print(log)

        if self.simulacao:
            preco_exec = preco or self.get_preco()
            return {
                "orderId":   f"SIM-{int(time.time())}",
                "status":    "FILLED",
                "executedQty": qty,
                "price":     preco_exec,
                "simulacao": True,
            }

        # Ordem real
        params = {
            "symbol":    self.symbol,
            "side":      lado,
            "type":      tipo,
            "quantity":  qty,
            "timestamp": int(time.time() * 1000),
        }
        if tipo == "LIMIT" and preco:
            params["price"]       = self._arredondar_preco(preco)
            params["timeInForce"] = "GTC"

        params["signature"] = self._assinar(params)
        try:
            r = requests.post(f"{BASE_URL}/api/v3/order",
                              params=params, headers=self._headers(), timeout=10)
            data = r.json()
        except Exception as e:
            # Falha de rede/timeout/JSON invalido — nao deixar propagar como sucesso (P1-8)
            return {"erro": f"falha de rede/resposta: {e}"}

        # Erros da Binance chegam como {"code": -xxxx, "msg": "..."} sem orderId
        if isinstance(data, dict) and "orderId" not in data:
            return {"erro": data.get("msg", "resposta sem orderId"), **data}
        return data

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

        with self._lock:
            self.posicao = {
                "tipo":         "LONG",
                "entrada":      preco_exec,
                "tamanho_btc":  tamanho_btc,
                "stop_inicial": stop_loss,
                "stop_atual":   stop_loss,
                "target1":      take_profit,
                "target2":      preco_exec * 1.05,   # alvo 2: 5%
                "parcial_feita":False,
                "abertura":     datetime.now().isoformat(),
                "order_id":     resp.get("orderId"),
            }

        gestao_risco._estado_risco["posicoes_abertas"] += 1
        gestao_risco.persistir_estado()
        print(f"[EXEC] LONG aberto @ ${preco_exec:,.2f} | "
              f"Stop: ${stop_loss:,.2f} | Target: ${take_profit:,.2f}")

        # Inicia monitoramento
        self._ativo = True
        self._monitor = threading.Thread(target=self._monitorar, daemon=True)
        self._monitor.start()
        return True

    # ── Fechar posição ─────────────────────────────────────────

    def fechar_posicao(self, preco, motivo, parcial=False):
        if not self.posicao:
            return

        qty = self.posicao["tamanho_btc"]
        if parcial:
            qty = qty / 2

        resp = self._enviar_ordem("SELL", qty, tipo="MARKET")

        # P1-8: nao tratar como fechada se a ordem real nao preencheu — senao o bot
        # acharia que saiu da posicao sem ter saido. Mantem a posicao para retry.
        if not self.simulacao and resp.get("status") != "FILLED":
            print(f"[EXEC] FALHA ao fechar posicao (ordem nao preenchida): {resp}. "
                  f"Posicao MANTIDA — nova tentativa no proximo ciclo.")
            return

        # Ordem preenchida: agora sim aplica a mutacao do fechamento parcial
        if parcial:
            self.posicao["parcial_feita"] = True
            self.posicao["tamanho_btc"]   = qty  # restante

        pnl_pct = (preco - self.posicao["entrada"]) / self.posicao["entrada"] * 100
        pnl_usdt = qty * (preco - self.posicao["entrada"])

        print(f"[EXEC] {'PARCIAL' if parcial else 'TOTAL'} FECHADO @ ${preco:,.2f} | "
              f"PnL: {'+' if pnl_usdt>=0 else ''}{pnl_usdt:.2f} USDT ({pnl_pct:+.2f}%) | "
              f"Motivo: {motivo}")

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
                self._ativo  = False
            gestao_risco._estado_risco["posicoes_abertas"] -= 1
            gestao_risco.persistir_estado()

    # ── Monitor de trailing stop ───────────────────────────────

    def _monitorar(self):
        """Thread que verifica stop/target e ajusta trailing stop."""
        TRAILING_ATIVACAO = 0.01   # ativa trailing após 1% de ganho
        TRAILING_DISTANCIA = 0.008 # stop segue 0.8% abaixo do pico

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
                ganho_pct = (preco - pos["entrada"]) / pos["entrada"]

                # 1. Stop Loss atingido
                if preco <= pos["stop_atual"]:
                    self.fechar_posicao(preco, "Stop Loss")
                    break

                # 2. Partial Take Profit (50% no target1)
                if not pos["parcial_feita"] and preco >= pos["target1"]:
                    self.fechar_posicao(preco, "Take Profit Parcial (50%)", parcial=True)
                    # Mover stop para breakeven
                    self.posicao["stop_atual"] = pos["entrada"] * 1.002
                    print(f"[EXEC] Stop movido para breakeven: ${self.posicao['stop_atual']:,.2f}")

                # 3. Target 2 (fechar tudo)
                if pos["parcial_feita"] and preco >= pos["target2"]:
                    self.fechar_posicao(preco, "Take Profit Final")
                    break

                # 4. Trailing Stop (ativa após 1% de ganho)
                if ganho_pct >= TRAILING_ATIVACAO:
                    if preco > preco_pico:
                        preco_pico = preco
                    novo_stop = preco_pico * (1 - TRAILING_DISTANCIA)
                    if novo_stop > pos["stop_atual"]:
                        self.posicao["stop_atual"] = novo_stop
                        print(f"[EXEC] Trailing Stop: ${novo_stop:,.2f} (pico: ${preco_pico:,.2f})")

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
            "tipo":          pos["tipo"],
            "entrada":       pos["entrada"],
            "preco_atual":   preco,
            "pnl_%":         round(pnl, 2),
            "pnl_usdt":      round(pos["tamanho_btc"] * (preco - pos["entrada"]), 2),
            "stop_atual":    pos["stop_atual"],
            "target1":       pos["target1"],
            "target2":       pos["target2"],
            "parcial_feita": pos["parcial_feita"],
            "tamanho_btc":   pos["tamanho_btc"],
        }


if __name__ == "__main__":
    # Teste em modo simulação
    ex = Executor(simulacao=True)
    preco_atual = ex.get_preco()
    print(f"\nPreco atual: ${preco_atual:,.2f}")

    stop   = preco_atual * 0.985
    target = preco_atual * 1.030
    qty    = 0.001  # 0.001 BTC para teste

    print(f"Abrindo LONG simulado: {qty} BTC @ ${preco_atual:,.2f}")
    print(f"Stop: ${stop:,.2f} | Target: ${target:,.2f}")
    ex.abrir_long(preco_atual, qty, stop, target)

    time.sleep(2)
    print("\nStatus da posicao:")
    for k, v in ex.status().items():
        print(f"  {k}: {v}")
