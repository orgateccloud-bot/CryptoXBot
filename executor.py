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
import math
import threading
import time
import uuid
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import requests

import database
import health
import risco as gestao_risco
import telegram_bot
from config.runtime_settings import (
    API_KEY,
    API_SECRET,
    MAKER_FIRST,
    MAKER_MAX_REQUOTES,
    MAKER_TIMEOUT_S,
    OCO_BRACKET,
    OCO_TRAILING_DELTA_BIPS,
    RECONCILIAR_BOOT_EXCHANGE,
    REST_BASE_URL,
)

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

# P1-3: mapeia o `motivo` EXATO ja usado por avaliar_tick_monitor() (linha
# 117 do docstring: "Stop Loss" | "Take Profit Final") para a barreira
# efetivamente tocada -- classificacao correta em vez de inferir pelo sinal
# do PnL (bug anterior: um trailing-stop que fecha em lucro era rotulado
# igual a um target real; um stop atingido por slippage a lucro minimo
# seria mal-classificado do mesmo jeito). Motivo fora deste mapa -> MANUAL.
_BARREIRA_POR_MOTIVO = {
    "Stop Loss": "STOP",
    "Take Profit Final": "TARGET",
    "Take Profit Parcial (50%)": "TARGET_PARCIAL",
}


def _classificar_barreira(motivo: str) -> str:
    return _BARREIRA_POR_MOTIVO.get(motivo, "MANUAL")


def _decimais(valor_str: str) -> int:
    """Nº de casas decimais de um passo tipo '0.01' -> 2, '1' -> 0."""
    d = Decimal(valor_str).normalize()
    exp = d.as_tuple().exponent
    return -exp if exp < 0 else 0


# I-10c: quotes conhecidas, da mais longa para a mais curta — "BTCUSDT" tem de
# casar com USDT (base BTC), nao com USD (que daria base "BTCT").
_QUOTES_CONHECIDAS = ("USDT", "FDUSD", "TUSD", "USDC", "BUSD", "BTC", "ETH", "BNB", "EUR", "TRY")


def _base_asset_do_symbol(symbol: str) -> str:
    """Fallback para o baseAsset quando o exchangeInfo nao respondeu."""
    symbol = symbol.upper()
    for quote in sorted(_QUOTES_CONHECIDAS, key=len, reverse=True):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


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
        info = r.json()["symbols"][0]
        filtros = {f["filterType"]: f for f in info["filters"]}
        step = filtros["LOT_SIZE"]["stepSize"]
        tick = filtros["PRICE_FILTER"]["tickSize"]
        prec = {
            "qty_step": float(step),
            "min_qty": float(filtros["LOT_SIZE"]["minQty"]),
            "price_prec": _decimais(tick),
            "tick_size": Decimal(tick).normalize().__str__(),
            "step_size": Decimal(step).normalize().__str__(),
            # I-10c: o ativo em que a comissao de um BUY e debitada quando a
            # conta nao paga taxa em BNB.
            "base_asset": info.get("baseAsset") or _base_asset_do_symbol(symbol),
        }
    except Exception as e:
        print(f"[EXEC] exchangeInfo indisponivel p/ {symbol} ({e}) — usando fallback")
        prec = dict(_PRECISAO.get(symbol, _PRECISAO_DEFAULT))
        prec.setdefault("step_size", Decimal(str(prec["qty_step"])).normalize().__str__())
        prec.setdefault("base_asset", _base_asset_do_symbol(symbol))
    _precisao_cache[symbol] = prec
    return prec


# Estados terminais de uma ordem na Binance (nao muda mais sozinha)
_STATUS_TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}

# P2-1: sentinela devolvida por _mover_protecao quando o stop NAO deve ser mexido
# aqui (trailing server-side de um OCO — a exchange ja trilha via belowTrailingDelta,
# mexer local brigaria com o servidor). Diferente de None (que significa "sem stop").
_PROTECAO_NAO_MOVIDA = object()

TRAILING_ATIVACAO = 0.01  # ativa trailing após 1% de ganho
TRAILING_DISTANCIA = 0.008  # stop segue 0.8% abaixo do pico

# I-10g: de quantos em quantos ciclos do monitor perguntar a exchange se a
# protecao ainda existe. O laco dorme 10s, entao 6 ciclos = ~1 minuto — barato
# em peso de rate-limit (1 GET /api/v3/order por minuto por posicao aberta) e
# suficiente para o criterio de saida do I-10 ("stop executado fora do bot =>
# detectado em <= 1 ciclo" de decisao da estrategia, que roda em minutos).
CICLOS_ENTRE_RECONCILIACOES = 6

# I-10f: quantos fechamentos consecutivos podem falhar antes de escalar. 6
# ciclos de 10s = ~1 minuto tentando sair sem conseguir — passou disso, nao e
# mais um soluco de rede.
MAX_FECHAMENTOS_FALHOS = 6


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


def _incoerencia_long(preco_entrada, stop_loss, take_profit):
    """Devolve str descrevendo a incoerencia de uma entrada LONG, ou None.

    Invariante: 0 < stop_loss < preco_entrada < take_profit.

    Por que esta funcao e uma REPLICA de estrategias/otimizada._incoerencia_de_precos
    em vez de um import: `executor` nao pode depender de `estrategias`, senao a
    camada de execucao passa a arrastar regime/suporte/ensemble/xgboost inteiros
    para dentro de si (e o grafo de import fica ciclico via main.py). A escolha
    deliberada e duplicar a regra e provar por TESTE que as duas nunca divergem —
    tests/test_coerencia_precos.py compara as duas implementacoes em 1.000 casos
    aleatorios, entao uma alteracao em so uma delas quebra a suite.

    Fica aqui, no nivel do executor, porque este e o unico ponto que nenhum
    chamador consegue contornar.
    """
    if preco_entrada is None or preco_entrada <= 0:
        return f"preco de entrada invalido: {preco_entrada}"
    if stop_loss is None or take_profit is None:
        return f"stop/target ausentes (stop={stop_loss}, target={take_profit})"
    if stop_loss <= 0 or take_profit <= 0:
        return f"stop/target nao positivos (stop={stop_loss}, target={take_profit})"
    if not stop_loss < preco_entrada:
        return f"LONG com stop {stop_loss} >= entrada {preco_entrada}"
    if not preco_entrada < take_profit:
        return f"LONG com target {take_profit} <= entrada {preco_entrada}"
    return None


def preco_medio_fill(resp, fallback):
    """Preco medio REALMENTE executado de uma resposta de ordem da Binance.

    Nao da para simplesmente ler `resp["price"]`: numa ordem MARKET a Binance
    devolve `price: "0.00000000"` (o campo e o preco *pedido*, e MARKET nao pede
    preco). Num LIMIT que cruza o book, `price` e o limite, mas o fill sai nos
    precos do book — normalmente MELHOR que o limite. Nos dois casos a fonte de
    verdade e `cummulativeQuoteQty / executedQty`: quanto de quote girou dividido
    por quanto de base executou = preco medio ponderado, ja incluindo fills
    fracionados em niveis diferentes.

    Ordem de preferencia:
      1. cummulativeQuoteQty / executedQty  (autoritativo; MARKET e LIMIT reais)
      2. media ponderada de `fills[]`       (fallback se a exchange omitir cqq)
      3. `price`, se > 0                    (simulacao e o preco_medio ja
                                             calculado por _entrar_maker)
      4. `fallback`                         (preco de referencia do chamador)
    """
    if not isinstance(resp, dict):
        return float(fallback)

    def _f(valor):
        try:
            return float(valor or 0)
        except (TypeError, ValueError):
            return 0.0

    exec_qty = _f(resp.get("executedQty"))
    cqq = _f(resp.get("cummulativeQuoteQty"))
    if exec_qty > 0 and cqq > 0:
        return cqq / exec_qty

    fills = resp.get("fills")
    if isinstance(fills, list) and fills:
        qty_total = sum(_f(f.get("qty")) for f in fills)
        custo = sum(_f(f.get("qty")) * _f(f.get("price")) for f in fills)
        if qty_total > 0 and custo > 0:
            return custo / qty_total

    preco = _f(resp.get("price"))
    return preco if preco > 0 else float(fallback)


class Executor:
    def __init__(self, simulacao=True, symbol="BTCUSDT", modo_trend=False):
        self.simulacao = simulacao
        self.symbol = symbol.upper()
        # modo_trend (dry run de validacao de execucao, ver estrategias/
        # trend_live.py): o sistema Donchian NAO tem alvo -- todo o edge esta na
        # cauda direita (poucos trades enormes). O `target2 = entrada*1.05`
        # hardcoded de abrir_long e o trailing por TRAILING_ATIVACAO/DISTANCIA
        # cortariam exatamente essa cauda, transformando o sistema testado em
        # outro sistema. Neste modo os alvos viram +inf e o trailing embutido e
        # desligado: quem move o stop e o canal Donchian-M, via _aplicar_novo_stop.
        self.modo_trend = modo_trend
        self.posicao = None
        self._monitor = None
        self._ativo = False
        # E-8: id da linha de ENTRADA em log_trades, para o fechamento poder
        # completa-la. Nao vai dentro de self.posicao de proposito: aquele dict e
        # persistido/reidratado, e um id de log local nao sobrevive a um restart
        # de forma confiavel — carregar um id invalido seria pior que None.
        self._log_trade_id = None
        # RLock (nao Lock): fechar_posicao/_aplicar_novo_stop sao chamados de
        # dentro de _monitorar, que tambem pode readquirir o lock -- Lock comum
        # causaria deadlock em chamada aninhada pela mesma thread.
        self._lock = threading.RLock()  # protege o estado da posicao (M-2)
        prec = _carregar_precisao(self.symbol)  # exchangeInfo real (fonte de verdade)
        self._qty_step = prec["qty_step"]
        self._min_qty = prec["min_qty"]
        self._price_prec = prec["price_prec"]
        # Decimal p/ snapping EXATO ao step/tick — float pode gerar 0.5219999...
        # e derrubar qty/preco valido no filtro da Binance.
        self._step_dec = Decimal(prec.get("step_size", str(prec["qty_step"])))
        self._tick_dec = Decimal(prec.get("tick_size", "0.01"))
        # I-10c: em que ativo a comissao de um BUY cai quando nao ha BNB
        self._base_asset = prec.get("base_asset") or _base_asset_do_symbol(self.symbol)
        self._offset_ms = 0
        # P0-2: parametros da execucao maker-first (instancia p/ serem ajustaveis em teste)
        self._maker_first = MAKER_FIRST
        self._maker_timeout_s = MAKER_TIMEOUT_S
        self._maker_max_requotes = MAKER_MAX_REQUOTES
        self._maker_poll_s = 2.0
        # P2-1: bracket OCO nativo (stop+alvo atomicos na exchange) + trailing
        # server-side opcional. Instancia p/ serem ajustaveis em teste.
        self._oco_bracket = OCO_BRACKET
        self._oco_trailing_bips = OCO_TRAILING_DELTA_BIPS
        # Auditoria 2026-07-22: cruza o boot recovery com o estado real da
        # Binance antes de religar o monitor. Instancia p/ ser ajustavel em teste.
        self._reconciliar_boot_exchange = RECONCILIAR_BOOT_EXCHANGE
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

        I-10a: em caso de falha o dict devolve `timeout_rede=True` quando o
        estado da ordem na exchange e DESCONHECIDO — e nao apenas quando houve
        timeout de socket. Esse flag e o que gateia a recuperacao de ordem
        fantasma (`_consultar_ordem`).

        Antes, o flag era `"falha de rede" in ultima_falha`, que so casava com
        a excecao de socket. Um 503 esgotando as 3 tentativas devolvia
        `timeout_rede=False` e a recuperacao NAO rodava — mas a propria Binance
        documenta 5xx como estado desconhecido: a ordem pode ter sido aceita e
        so a resposta ter se perdido. O mesmo vale para 429/418 (a requisicao
        pode ter chegado antes do rate-limit ser aplicado).

        Um erro de NEGOCIO (-2010 saldo insuficiente, -1013 filtro) e outra
        coisa: a exchange respondeu e recusou. Esses caem no `return data` e
        nunca chegam aqui.
        """
        ultima_falha = "sem tentativa"
        estado_desconhecido = False
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
                estado_desconhecido = True  # socket caiu: a ordem pode existir
                time.sleep(2**i)
                continue
            codigo = data.get("code") if isinstance(data, dict) else None
            # Rate limit / banimento temporario / erro interno: retry com backoff
            if r.status_code in (429, 418, 500, 502, 503) or codigo in (-1003, -1000):
                ultima_falha = f"HTTP {r.status_code} code={codigo}: {data.get('msg', '')}"
                # I-10a: 5xx e 429 tambem deixam o estado indefinido — a ordem
                # pode ter sido aceita antes de a resposta se perder.
                estado_desconhecido = True
                time.sleep(2**i)
                continue
            # -1021 (timestamp fora do recvWindow): ressincroniza e tenta de novo
            if codigo == -1021:
                self._sincronizar_relogio()
                ultima_falha = f"-1021 timestamp: {data.get('msg', '')}"
                # -1021 e recusa deterministica (a exchange validou e rejeitou
                # o timestamp): a ordem NAO entrou. Nao marca desconhecido.
                continue
            return data
        return {"erro": ultima_falha, "timeout_rede": estado_desconhecido}

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

    def _melhor_bid(self):
        """P0-2: melhor bid do book (topo da fila de compra). Uma ordem BUY
        colocada AQUI descansa como maker (nao cruza o ask)."""
        try:
            r = requests.get(
                f"{BASE_URL}/api/v3/ticker/bookTicker", params={"symbol": self.symbol}, timeout=5
            )
            return float(r.json()["bidPrice"])
        except Exception:
            return 0.0

    def _status_ordem(self, order_id):
        """Consulta o estado de uma ordem por orderId. None se falhar."""
        data = self._request_assinado(
            "GET", "/api/v3/order", {"symbol": self.symbol, "orderId": order_id}, tentativas=2
        )
        return data if isinstance(data, dict) and "status" in data else None

    def _enviar_ordem(self, lado, qty, preco=None, tipo="LIMIT"):
        """
        lado:  BUY ou SELL
        tipo:  LIMIT | MARKET | LIMIT_MAKER
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

        # I-9: a metrica conta ordens nos DOIS modos. Ela estava logo abaixo do
        # `return` do ramo de simulacao, ou seja permanentemente 0 no unico modo
        # que roda -- um contador que nunca sai de zero nao e observabilidade, e
        # decoracao. Agora `ordens_total` mede atividade real do bot em paper, o
        # que a torna util como sinal de vida.
        health.increment_metric("ordens_total")

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
        elif tipo == "LIMIT_MAKER" and preco:
            # post-only: a Binance REJEITA (-2010) se a ordem cruzaria o book.
            # LIMIT_MAKER nao aceita timeInForce.
            params["price"] = self._arredondar_preco(preco)

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
            health.increment_metric("ordens_erro")
            return {"erro": data.get("erro") or data.get("msg", "resposta sem orderId"), **data}
        return data

    # ── Stop loss NA EXCHANGE (P0-2) ───────────────────────────
    # O monitor local (loop de 10s) vira redundancia: a protecao primaria e uma
    # ordem STOP_LOSS_LIMIT viva na Binance — sobrevive a crash/travamento do bot.

    def _colocar_stop_exchange(self, qty, stop_price):
        """Coloca STOP_LOSS_LIMIT SELL na exchange. Retorna orderId ou None.

        I-10b: em estado desconhecido (5xx/429/socket), CONSULTA a exchange
        pelo clientOrderId antes de desistir. Sem isso, um 503 na resposta
        devolvia None enquanto a ordem existia — o bot perdia o id de um stop
        VIVO, e o proximo `_liberar_protecao` nao teria o que cancelar.
        """
        if self.simulacao:
            return None
        qty = self._arredondar_qty(qty)
        stop = self._arredondar_preco(stop_price)
        client_id = f"bxstop-{uuid.uuid4().hex[:16]}"
        # price um pouco abaixo do stopPrice para garantir fill do limit
        params = {
            "symbol": self.symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "quantity": qty,
            "stopPrice": stop,
            "price": self._arredondar_preco(stop * 0.997),
            "timeInForce": "GTC",
            "newClientOrderId": client_id,
        }
        data = self._request_assinado("POST", "/api/v3/order", params)
        if isinstance(data, dict) and "orderId" in data:
            print(f"[EXEC] Stop na exchange @ ${stop:,.2f} (orderId={data['orderId']})")
            return data["orderId"]
        if isinstance(data, dict) and data.get("timeout_rede"):
            recuperada = self._consultar_ordem(client_id)
            if recuperada:
                print(
                    f"[EXEC] Stop RECUPERADO apos estado desconhecido "
                    f"(orderId={recuperada['orderId']}, clientOrderId={client_id})"
                )
                return recuperada["orderId"]
        print(f"[EXEC] AVISO: falha ao colocar stop na exchange: {data}")
        health.increment_metric("ordens_erro")
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

    # ── Bracket OCO nativo (P2-1) ──────────────────────────────
    # Um OCO (one-cancels-the-other) amarra stop + alvo numa unica lista atomica
    # na exchange: quando uma perna preenche, a outra e cancelada automaticamente.
    # Ganho vs. o STOP_LOSS_LIMIT puro: o ALVO tambem passa a viver na exchange
    # (sobrevive a crash do bot). Endpoint novo POST /api/v3/orderList/oco (o
    # legado /order/oco esta deprecado). Para fechar um LONG a lista e SELL:
    #   perna ACIMA do mercado = take-profit (LIMIT_MAKER @ alvo)
    #   perna ABAIXO do mercado = stop-loss  (STOP_LOSS_LIMIT @ stop, opcional
    #                                          trailing server-side via trailingDelta)

    def _colocar_oco_exchange(self, qty, stop_price, target_price, trailing_bips=0):
        """Coloca um OCO SELL (stop + alvo) na exchange. Retorna
        {"oco_list_id", "stop_order_id", "target_order_id"} ou None se falhar.

        trailing_bips>0 anexa belowTrailingDelta (trailing server-side, em bips)
        a perna de stop — a exchange trilha o stop sozinha."""
        if self.simulacao:
            return None
        qty = self._arredondar_qty(qty)
        if qty < self._min_qty:
            print(f"[EXEC] OCO nao colocado: qty {qty} < min {self._min_qty}")
            return None
        stop = self._arredondar_preco(stop_price)
        alvo = self._arredondar_preco(target_price)
        list_client_id = f"bxoco-{uuid.uuid4().hex[:13]}"
        params = {
            "symbol": self.symbol,
            "side": "SELL",
            "quantity": qty,
            # Perna de take-profit (acima do mercado)
            "aboveType": "LIMIT_MAKER",
            "abovePrice": alvo,
            "aboveClientOrderId": f"bxtp-{uuid.uuid4().hex[:14]}",
            # Perna de stop-loss (abaixo do mercado)
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": stop,
            # price um pouco abaixo do stopPrice p/ garantir fill do limit
            "belowPrice": self._arredondar_preco(stop * 0.997),
            "belowTimeInForce": "GTC",
            "belowClientOrderId": f"bxsl-{uuid.uuid4().hex[:14]}",
            "listClientOrderId": list_client_id,
        }
        if trailing_bips and trailing_bips > 0:
            params["belowTrailingDelta"] = int(trailing_bips)
        data = self._request_assinado("POST", "/api/v3/orderList/oco", params)
        if isinstance(data, dict) and data.get("orderListId") is not None:
            handle = self._extrair_pernas_oco(data)
            print(
                f"[EXEC] OCO na exchange: stop @ ${stop:,.2f} / alvo @ ${alvo:,.2f}"
                f" (listId={handle['oco_list_id']}"
                f"{f', trailing {trailing_bips}bips' if trailing_bips else ''})"
            )
            return handle
        # I-10b: estado desconhecido — a lista pode existir. Perder o listId de
        # um OCO vivo e pior que nao te-lo criado: as duas pernas ficam
        # travando saldo sem ninguem para cancela-las.
        if isinstance(data, dict) and data.get("timeout_rede"):
            recuperada = self._consultar_oco(list_client_id)
            if recuperada:
                handle = self._extrair_pernas_oco(recuperada)
                print(
                    f"[EXEC] OCO RECUPERADO apos estado desconhecido "
                    f"(listId={handle['oco_list_id']}, listClientOrderId={list_client_id})"
                )
                return handle
        print(f"[EXEC] AVISO: falha ao colocar OCO na exchange: {data}")
        health.increment_metric("ordens_erro")
        return None

    @staticmethod
    def comissao_em_ativo_base(resp: dict, ativo_base: str) -> float:
        """I-10c: soma a comissao cobrada NO ATIVO COMPRADO (ex.: BTC num BUY
        de BTCUSDT). Funcao pura — testavel sem rede.

        Numa conta SPOT sem desconto de BNB a Binance debita a taxa do proprio
        ativo recebido: um BUY de 0,01 BTC com 0,1% de taxa credita 0,00999 BTC.
        Dimensionar o stop pelos `executedQty` brutos (0,01) pede para vender
        mais do que existe na conta — a exchange devolve -2010 e a posicao fica
        SEM PROTECAO. Com BNB ligado a comissao vem em BNB e esta funcao
        devolve 0,0, que e o certo: o saldo do ativo base nao foi tocado.

        A resposta de MARKET/LIMIT traz `fills`, uma lista de
        {price, qty, commission, commissionAsset} — uma entrada por nivel de
        book consumido.
        """
        if not isinstance(resp, dict):
            return 0.0
        total = 0.0
        for f in resp.get("fills") or []:
            if not isinstance(f, dict):
                continue
            if str(f.get("commissionAsset", "")).upper() != ativo_base.upper():
                continue  # taxa em BNB/USDT nao reduz o ativo comprado
            try:
                total += float(f.get("commission") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _consultar_oco(self, list_client_order_id):
        """I-10b: par de `_consultar_ordem` para listas OCO. Confirma na
        exchange se a lista existe depois de um estado desconhecido."""
        data = self._request_assinado(
            "GET",
            "/api/v3/orderList",
            {"origClientOrderId": list_client_order_id},
            timeout=10,
            tentativas=2,
        )
        if isinstance(data, dict) and data.get("orderListId") is not None:
            return data
        return None

    @staticmethod
    def _extrair_pernas_oco(data: dict) -> dict:
        """Mapeia a resposta do orderList para os orderIds das pernas stop/alvo.
        A resposta traz `orders` (lista) e `orderReports` (com type por perna)."""
        stop_id = target_id = None
        relatorios = {o.get("orderId"): o for o in data.get("orderReports", [])}
        for o in data.get("orders", []):
            oid = o.get("orderId")
            tipo = (relatorios.get(oid, {}) or {}).get("type", "")
            if tipo == "LIMIT_MAKER":
                target_id = oid
            elif tipo in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                stop_id = oid
        return {
            "oco_list_id": data.get("orderListId"),
            "stop_order_id": stop_id,
            "target_order_id": target_id,
        }

    def _cancelar_oco_exchange(self, oco_list_id):
        """Cancela a lista OCO inteira (ambas as pernas). True se cancelou (ou ja
        nao existia)."""
        if self.simulacao or oco_list_id is None:
            return True
        data = self._request_assinado(
            "DELETE", "/api/v3/orderList", {"symbol": self.symbol, "orderListId": oco_list_id}
        )
        if isinstance(data, dict) and ("orderListId" in data or data.get("code") == -2011):
            return True  # -2011 = ja nao existe (perna executou / lista cancelada)
        print(f"[EXEC] AVISO: falha ao cancelar OCO {oco_list_id}: {data}")
        return False

    # ── Protecao pos-entrada: OCO (P2-1) ou stop puro (P0-2) ───
    # Abstracao unica usada por abrir_long/fechar_posicao/_monitorar: quando
    # _oco_bracket esta ligado, a protecao e um bracket atomico stop+alvo; senao
    # cai no STOP_LOSS_LIMIT puro (comportamento P0-2, byte a byte).

    def _abrir_protecao(self, qty, stop_price, target_price):
        """Coloca a protecao pos-entrada e devolve {"stop_order_id","oco_list_id"}.
        Em simulacao ambos sao None. Se o OCO falhar, cai no stop puro — nunca
        deixa a posicao sem protecao."""
        # modo_trend nao tem alvo finito (target=+inf): um OCO exige um preco
        # limite valido na perna de alvo, entao a protecao e sempre o stop puro.
        if (
            not self.simulacao
            and self._oco_bracket
            and target_price
            and math.isfinite(target_price)
        ):
            oco = self._colocar_oco_exchange(qty, stop_price, target_price, self._oco_trailing_bips)
            if oco:
                return {"stop_order_id": oco["stop_order_id"], "oco_list_id": oco["oco_list_id"]}
            print("[EXEC] OCO falhou — caindo no stop puro (protecao garantida)")
        return {"stop_order_id": self._colocar_stop_exchange(qty, stop_price), "oco_list_id": None}

    def _desfazer_entrada_desprotegida(self, qty, preco_entrada, stop_pretendido):
        """I-10d: vende a mercado uma entrada que ficou sem protecao.

        Chamada quando `_abrir_protecao` nao conseguiu colocar nem OCO nem stop
        puro. `self.posicao` ainda NAO foi montada neste ponto, entao esta
        funcao nao pode usar `fechar_posicao` — ela opera sobre a quantidade
        crua e registra o resultado direto.

        Devolve True se a posicao foi realmente desfeita. False significa que o
        capital esta exposto sem protecao e sem saida: o chamador tem de travar
        o bot.
        """
        registrado = False
        try:
            database.salvar_bot_event(
                "protecao_nao_colocada",
                f"{self.symbol}: entrada de {qty} @ ${preco_entrada:,.2f} ficou sem "
                f"stop (pretendido ${stop_pretendido:,.2f}). Desfazendo a mercado.",
                service="executor",
                symbol=self.symbol,
                severity="CRITICAL",
            )
            registrado = True
        except Exception:
            pass

        resp = self._enviar_ordem("SELL", qty, tipo="MARKET")
        ok = isinstance(resp, dict) and resp.get("status") == "FILLED"
        if ok:
            preco_saida = preco_medio_fill(resp, preco_entrada)
            prejuizo = (preco_saida - preco_entrada) * qty
            print(
                f"[EXEC] Entrada desprotegida DESFEITA @ ${preco_saida:,.2f} "
                f"(custo: {prejuizo:+.2f} USDT + taxas)"
            )
            # O custo de desfazer e um resultado real: entra na contabilidade
            # de risco como qualquer outro trade, senao o drawdown do dia
            # subestima o que aconteceu.
            try:
                gestao_risco.registrar_resultado(prejuizo)
                gestao_risco.persistir_estado()
            except Exception as e:
                print(f"[EXEC] AVISO: falha ao registrar o custo do desfazimento: {e}")
        else:
            print(f"[EXEC] CRITICO: SELL de desfazimento NAO preencheu: {resp}")

        try:
            telegram_bot.alerta_circuit_breaker(
                f"{self.symbol}: protecao nao entrou. "
                + (
                    "Entrada desfeita a mercado."
                    if ok
                    else "SELL de desfazimento FALHOU — POSICAO REAL DESCOBERTA."
                )
            )
        except Exception:
            pass
        if not registrado:
            print("[EXEC] AVISO: bot_event de protecao_nao_colocada nao foi gravado")
        return ok

    def _liberar_protecao(self):
        """Cancela a protecao atual (lista OCO ou stop puro). Devolve True se a
        exchange confirmou o cancelamento. Idempotente / no-op em simulacao.

        I-10e: o retorno de `_cancelar_*_exchange` era DESCARTADO e os ids eram
        zerados de qualquer jeito. Quando o cancelamento falhava, o bot
        esquecia o id de uma ordem que continuava VIVA na exchange — travando
        saldo que o SELL seguinte precisaria (-2010), e sem ninguem para
        cancela-la depois, porque a referencia tinha sido apagada.

        Agora os ids so sao zerados quando a exchange confirma, e a mudanca e
        persistida (antes nao chamava `_persistir_posicao`, entao um restart
        reidratava ids ja cancelados).
        """
        if self.simulacao or not self.posicao:
            return True
        oco_id = self.posicao.get("oco_list_id")
        if oco_id is not None:
            ok = self._cancelar_oco_exchange(oco_id)
        else:
            stop_id = self.posicao.get("stop_order_id")
            ok = self._cancelar_ordem_exchange(stop_id) if stop_id else True
        if not ok:
            print(
                f"[EXEC] AVISO: protecao de {self.symbol} NAO cancelada — ids "
                f"PRESERVADOS (oco={oco_id}, stop={self.posicao.get('stop_order_id')}). "
                f"A ordem segue viva na exchange."
            )
            health.increment_metric("ordens_erro")
            return False
        with self._lock:
            if self.posicao:
                self.posicao["oco_list_id"] = None
                self.posicao["stop_order_id"] = None
        self._persistir_posicao()
        return True

    def _mover_protecao(self, novo_stop):
        """Move a protecao para novo_stop (trailing/breakeven). Atualiza
        oco_list_id no estado e devolve o novo stop_order_id.

        - OCO com trailing server-side (trailingDelta): devolve _PROTECAO_NAO_MOVIDA
          — a exchange ja trilha o stop, mexer aqui brigaria com o servidor.
        - OCO sem trailing server-side: cancel-then-replace do OCO no novo stop
          (mesmo alvo); restaura o OCO antigo se o novo falhar.
        - stop puro: delega a _mover_stop_exchange (comportamento P0-2 preservado)."""
        if self.simulacao or not self.posicao:
            return None
        oco_antigo = self.posicao.get("oco_list_id")
        if oco_antigo is None:
            return self._mover_stop_exchange(novo_stop)
        if self._oco_trailing_bips > 0:
            return _PROTECAO_NAO_MOVIDA  # servidor trilha sozinho
        qty = self.posicao["tamanho_btc"]
        alvo = self.posicao.get("target2")
        stop_antigo = self.posicao.get("stop_atual")
        if not self._cancelar_oco_exchange(oco_antigo):
            return _PROTECAO_NAO_MOVIDA  # nao cancelou: OCO antigo segue vivo
        with self._lock:
            if self.posicao:
                self.posicao["oco_list_id"] = None
        prot = self._abrir_protecao(qty, novo_stop, alvo)
        if prot["stop_order_id"] is None and prot["oco_list_id"] is None and stop_antigo:
            print("[EXEC] Replace do OCO falhou — restaurando protecao no nivel antigo")
            prot = self._abrir_protecao(qty, stop_antigo, alvo)
        with self._lock:
            if self.posicao:
                self.posicao["oco_list_id"] = prot["oco_list_id"]
        return prot["stop_order_id"]

    # ── Entrada maker-first / post-only (P0-2) ─────────────────
    # Em vez de cruzar o spread (LIMIT acima do ask = taker), coloca LIMIT_MAKER
    # no melhor bid: descansa no book, sempre paga fee de MAKER e nao sofre
    # slippage de cruzamento. Se nao preencher, cancela e re-quota no bid novo.

    def _aguardar_fill(self, order_id, resp_inicial):
        """Poll do status até terminal (FILLED/CANCELED/...) ou timeout.
        Checa ao menos uma vez antes de olhar o deadline."""
        status = resp_inicial
        deadline = time.time() + self._maker_timeout_s
        while True:
            st = self._status_ordem(order_id)
            if st:
                status = st
            if status.get("status") in _STATUS_TERMINAL:
                return status
            if time.time() >= deadline:
                return status
            time.sleep(self._maker_poll_s)

    def _entrar_maker(self, qty_alvo):
        """Entrada post-only com re-quote. Preenchimentos parciais são ACUMULADOS
        (a posição usa o qty realmente executado, não o pedido).

        Retorna {"status":"FILLED","executedQty","price","maker":True} ou
        {"erro": ..., "executedQty": <parcial>}.
        """
        restante = self._arredondar_qty(qty_alvo)
        preenchido = 0.0
        custo = 0.0

        for tentativa in range(self._maker_max_requotes + 1):
            if restante < self._min_qty:
                break
            bid = self._melhor_bid()
            if bid <= 0:
                return {"erro": "book indisponivel (bid=0)", "executedQty": preenchido}

            resp = self._enviar_ordem("BUY", restante, bid, "LIMIT_MAKER")
            if "orderId" not in resp:
                # -2010 = cruzaria o book (bid subiu entre ler e enviar) -> re-quota
                print(f"[EXEC] LIMIT_MAKER recusado ({tentativa+1}): {resp.get('erro')}")
                continue

            order_id = resp["orderId"]
            status = self._aguardar_fill(order_id, resp)
            st = status.get("status")

            if st != "FILLED":
                self._cancelar_ordem_exchange(order_id)  # libera o restante

            exec_qty = float(status.get("executedQty", 0) or 0)
            if exec_qty > 0:
                cqq = float(status.get("cummulativeQuoteQty", 0) or 0)
                custo += cqq if cqq > 0 else exec_qty * bid
                preenchido += exec_qty
                restante = self._arredondar_qty(qty_alvo - preenchido)

            if st == "FILLED" or restante < self._min_qty:
                break
            print(
                f"[EXEC] Maker sem fill em {self._maker_timeout_s}s — "
                f"re-quote {tentativa+1}/{self._maker_max_requotes}"
            )

        if preenchido >= self._min_qty:
            preco_medio = custo / preenchido
            print(f"[EXEC] Entrada MAKER: {preenchido} @ ${preco_medio:,.2f} (fee de maker)")
            return {
                "status": "FILLED",
                "executedQty": preenchido,
                "price": preco_medio,
                "maker": True,
            }
        return {"erro": "maker-first nao preencheu apos re-quotes", "executedQty": preenchido}

    # ── Abrir posição LONG ─────────────────────────────────────

    def abrir_long(
        self,
        preco_entrada,
        tamanho_btc,
        stop_loss,
        take_profit,
        *,
        atr_relativo=None,
        sinal_id=None,
    ):
        if self.posicao:
            print("[EXEC] Ja existe posicao aberta. Aguardar fechamento.")
            return False

        # ── E-7: invariante de coerencia, fail-closed ─────────────
        # A mesma checagem existe na origem do sinal (estrategias/otimizada.py) e
        # no caminho trend. Esta aqui e a que importa: e o ULTIMO ponto antes de
        # a ordem existir, e nenhum chamador — presente ou futuro — consegue
        # contorna-la. O bloco de producao com entrada ~$1.858 e stop $63.521,65
        # atravessou a estrategia inteira porque nada neste nivel dizia nao.
        #
        # Com stop >= entrada, o desfecho e um destes dois, nenhum aceitavel:
        #   (a) a Binance rejeita o STOP_LOSS_LIMIT (stopPrice acima do mercado) e
        #       a posicao real fica DESPROTEGIDA; ou
        #   (b) o monitor local (_monitorar) a liquida no primeiro tick, pagando
        #       spread + duas taxas por nada.
        incoerencia = _incoerencia_long(preco_entrada, stop_loss, take_profit)
        if incoerencia:
            print(f"\033[91m[EXEC] ORDEM RECUSADA — precos incoerentes: {incoerencia}\033[0m")
            try:
                database.salvar_bot_event(
                    "ordem_recusada_incoerente",
                    f"{self.symbol}: abrir_long recusado — {incoerencia}. "
                    f"entrada={preco_entrada} stop={stop_loss} target={take_profit} "
                    f"qty={tamanho_btc} sinal_id={sinal_id}.",
                    service="worker",
                    symbol=self.symbol,
                    severity="CRITICAL",
                )
            except Exception:
                pass
            health.increment_metric("ordens_erro")
            return False

        # Validar risco antes de executar
        saldo = gestao_risco.get_saldo_usdt()
        if not self.simulacao:
            validacao = gestao_risco.validar_trade(
                "COMPRA",
                preco_entrada,
                saldo,
                atr_relativo=atr_relativo,
                # E-8: o stop real ja esta aqui como argumento; nao passa-lo
                # fazia esta revalidacao — a do caminho de DINHEIRO REAL — usar
                # 1,5% fixo e reportar risco errado justamente onde importa.
                stop=stop_loss,
                symbol=self.symbol,
            )
            if not validacao["pode"]:
                print(f"[EXEC] Trade bloqueado pelo risco: {validacao['motivo']}")
                return False

        # P0-2: entrada MAKER-FIRST (post-only) — nunca cruza o spread, sempre
        # paga fee de maker. Fallback p/ LIMIT cruzando se MAKER_FIRST=false.
        if not self.simulacao and self._maker_first:
            resp = self._entrar_maker(tamanho_btc)
        else:
            # Limite ligeiramente acima (cruza o book = taker, garante execução)
            preco_limit = self._arredondar_preco(preco_entrada * 1.001)
            resp = self._enviar_ordem("BUY", tamanho_btc, preco_limit, "LIMIT")

        if resp.get("status") != "FILLED" and not self.simulacao:
            print(f"[EXEC] Ordem nao preenchida: {resp}")
            return False

        # Mesma regra da saida: o preco medio REALMENTE executado. Num LIMIT que
        # cruza o book, `resp["price"]` e o limite — o fill sai nos precos do
        # book, normalmente MELHOR que o limite, entao ler o limite subestimava o
        # lucro. (_entrar_maker ja devolve o medio ponderado que calculou; o
        # helper o preserva via o ramo `price`.)
        preco_exec = preco_medio_fill(resp, preco_entrada)

        # Maker pode preencher PARCIAL: posição e stop devem usar o qty REAL
        # executado, senão o stop cobriria mais do que se comprou.
        if not self.simulacao:
            exec_qty = float(resp.get("executedQty", 0) or 0)
            if exec_qty > 0:
                # I-10c: descontar a comissao cobrada NO ATIVO COMPRADO. Sem
                # BNB, um BUY de 0,01 BTC com 0,1% credita 0,00999 BTC na
                # conta; dimensionar o stop por 0,01 pede para vender mais do
                # que existe e a exchange devolve -2010 — posicao real, SEM
                # protecao. `_arredondar_qty` faz floor no step do par
                # (ROUND_DOWN, :334) — arredondar para cima recriaria o mesmo
                # problema por outro caminho.
                comissao = self.comissao_em_ativo_base(resp, self._base_asset)
                tamanho_btc = self._arredondar_qty(max(exec_qty - comissao, 0.0))
                if comissao > 0:
                    print(
                        f"[EXEC] Comissao em {self._base_asset}: {comissao:.8f} "
                        f"— posicao dimensionada por {tamanho_btc} (bruto {exec_qty})"
                    )

        if self.modo_trend:
            # Sem alvo: a saida e o rompimento do canal Donchian-M (sinal), nao
            # um nivel de lucro fixo. +inf faz avaliar_tick_monitor nunca
            # disparar parcial nem alvo final.
            take_profit = float("inf")
            target2 = float("inf")
        else:
            target2 = preco_exec * 1.05  # alvo 2: 5%

        # P0-2/P2-1: protecao primaria NA EXCHANGE (sobrevive a crash do bot).
        # Com OCO_BRACKET, stop + alvo final (target2) viram um par atomico; o
        # take-profit parcial (target1) segue no monitor local. Sem OCO, e o
        # STOP_LOSS_LIMIT puro. O alvo do bracket e target2 (o runner), nao
        # target1: o parcial de 50% e cancel-vende-recoloca, orquestrado pelo
        # monitor — um OCO nao expressa "50% aqui + 50% ali".
        prot = self._abrir_protecao(tamanho_btc, stop_loss, target2)

        # I-10d: FAIL-CLOSED. Antes, o retorno de _abrir_protecao so alimentava
        # o dict e a funcao seguia ate `return True` — qualquer falha (-2010
        # saldo, -1013 filtro, rate-limit, rede) produzia uma posicao REAL
        # anunciada como sucesso, com stop_order_id=None. Nada no loop
        # perguntava a exchange, entao a posicao ficava desprotegida ate
        # intervencao manual.
        #
        # Agora: desfaz a compra a mercado. Ficar comprado sem stop e o pior
        # dos estados possiveis; sair imediatamente custa o spread + a taxa e
        # devolve o sistema a um estado conhecido.
        if not self.simulacao and prot["stop_order_id"] is None and prot["oco_list_id"] is None:
            print(
                f"[EXEC] CRITICO: protecao NAO entrou para {self.symbol} "
                f"(qty={tamanho_btc}, stop=${stop_loss:,.2f}). Desfazendo a entrada."
            )
            health.increment_metric("ordens_erro")
            desfeita = self._desfazer_entrada_desprotegida(tamanho_btc, preco_exec, stop_loss)
            if not desfeita:
                # Nao conseguiu nem sair: o unico caminho seguro e travar o bot
                # e gritar. Melhor um bot parado com uma posicao conhecida do
                # que um bot operando com uma posicao que ele acha protegida.
                # `travar` (I-8) e permanente — nao se revoga na virada do dia
                # nem no restart; so `destravar()` com a frase de confirmacao.
                gestao_risco.travar(
                    f"{self.symbol}: posicao aberta SEM protecao e SEM saida automatica "
                    f"(qty={tamanho_btc} @ ${preco_exec:,.2f}) — POSICAO REAL DESCOBERTA, "
                    f"exige intervencao manual na Binance"
                )
            return False

        with self._lock:
            self.posicao = {
                "tipo": "LONG",
                "entrada": preco_exec,
                "tamanho_btc": tamanho_btc,
                "tamanho_btc_original": tamanho_btc,  # P1-3: preservado p/ pnl_pct
                # do trade inteiro no fechamento final (tamanho_btc muda em
                # fechamentos parciais, este campo nunca e mutado depois)
                "stop_inicial": stop_loss,
                "stop_atual": stop_loss,
                "target1": take_profit,
                "target2": target2,
                "parcial_feita": False,
                "abertura": datetime.now().isoformat(),
                "order_id": resp.get("orderId"),
                "stop_order_id": prot["stop_order_id"],
                "oco_list_id": prot["oco_list_id"],  # P2-1: None se OCO desligado
                "sinal_id": sinal_id,  # P1-3: liga esta posicao ao sinal que a originou
                "pnl_usdt_parcial_acumulado": 0.0,  # P1-3: soma de fechamentos parciais
            }

        # P0-3: posicao persistida — sobrevive a restart (reconciliada no boot).
        # Retry curto: salvar_posicao_aberta e upsert por symbol, idempotente.
        # Isso reduz a janela de "DB lento/flaky" -- NAO fecha o gap de crash
        # duro do processo antes de qualquer tentativa (esse e o papel da
        # reconciliacao de boot); se falhar mesmo apos retries, escala visivelmente
        # em vez de so logar (bot_event CRITICAL + alerta Telegram).
        persistido = False
        ultimo_erro = None
        for tentativa in range(3):
            try:
                database.salvar_posicao_aberta(self.symbol, self.posicao)
                persistido = True
                break
            except Exception as e:
                ultimo_erro = e
                if tentativa < 2:
                    time.sleep(0.5 * (2**tentativa))
        if not persistido:
            print(f"[EXEC] AVISO: falha ao persistir posicao apos retries: {ultimo_erro}")
            try:
                database.salvar_bot_event(
                    "posicao_sem_persistencia",
                    f"Fill de {self.symbol} sem registro no DB apos retries: {ultimo_erro}",
                    service="executor",
                    symbol=self.symbol,
                    severity="CRITICAL",
                )
            except Exception:
                pass
            try:
                telegram_bot.alerta_persistencia_falhou(self.symbol, "LONG", preco_exec, tamanho_btc)
            except Exception:
                pass

        # P1-3: liga a entrada de fato executada ao sinal que a originou --
        # so agora (ordem preenchida), nao na hora do sinal ser gerado.
        try:
            database.marcar_sinal_executado(sinal_id, symbol=self.symbol)
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao marcar sinal executado: {e}")

        # E-8: `log_trades` ganha escritor.
        #
        # logger.registrar_trade_entrada/saida existiam prontas e NUNCA foram
        # chamadas em producao — a tabela tinha 0 linhas depois de 4 meses. E
        # logger.dados_relatorio_diario le o PnL do dia DELA, entao o alerta das
        # 18h somava um conjunto vazio e reportava PnL 0,00 mesmo num dia com
        # trades. Duas tabelas guardam o resultado por razoes diferentes:
        # `sinais` liga decisao->resultado (meta-labeling, Etapa 2 do gate) e
        # `log_trades` e o diario operacional por trade. A ausencia de escritor
        # tornava a segunda um bloco de leitura sobre o vazio.
        self._log_trade_id = None
        try:
            from logger import logger as _log_estruturado

            self._log_trade_id = _log_estruturado.registrar_trade_entrada(
                self.symbol,
                "LONG",
                preco_exec,
                tamanho_btc,
                preco_exec * tamanho_btc,
                stop_loss,
                take_profit,
                None,  # score: a estrategia o conhece, o executor nao
                None,  # ml_prob: idem
            )
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao registrar entrada em log_trades: {e}")

        gestao_risco.incrementar_posicoes_abertas()
        gestao_risco.persistir_estado()
        print(
            f"[EXEC] LONG aberto @ ${preco_exec:,.2f} | "
            f"Stop: ${stop_loss:,.2f} | Target: ${take_profit:,.2f}"
        )
        try:
            telegram_bot.alerta_trade_aberto("LONG", preco_exec, tamanho_btc, stop_loss, take_profit)
        except Exception:
            pass

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

        # I-10h: reentrancia. `fechar_posicao` e chamada do monitor (a cada 10s)
        # e tambem de main.py/reconciliacao. Se a primeira chamada levantar
        # depois de `registrar_resultado` mas antes de zerar `self.posicao`, a
        # proxima entra de novo e contabiliza o MESMO PnL — indefinidamente, a
        # cada ciclo. O snapshot sozinho nao protege disso: ele evita ler um
        # dict mutado, nao evita a segunda entrada.
        with self._lock:
            if not self.posicao:
                return
            if self.posicao.get("em_fechamento"):
                print(f"[EXEC] fechar_posicao de {self.symbol} ja em andamento — ignorando")
                return
            self.posicao["em_fechamento"] = True
            # Snapshot unico sob lock (M-2): todos os calculos de PnL/qty abaixo
            # usam `pos`, nunca releem self.posicao diretamente -- evita reler
            # um dict que outra chamada possa ter mutado/zerado no meio.
            pos = dict(self.posicao)

        try:
            self._fechar_posicao_interno(pos, preco, motivo, parcial)
        finally:
            # So limpa se a posicao ainda existe: no caminho de sucesso ela ja
            # foi para None e recriar a chave ressuscitaria um dict morto.
            with self._lock:
                if self.posicao:
                    self.posicao["em_fechamento"] = False

    def _fechar_posicao_interno(self, pos, preco, motivo, parcial):
        # P0-2/P2-1: liberar o saldo travado pela protecao (OCO ou stop puro)
        # na exchange antes do SELL.
        #
        # I-10e: se o cancelamento NAO confirmar, abortar aqui. A protecao
        # segue viva travando o saldo, entao o SELL cairia em -2010 — e o ramo
        # de falha abaixo chamaria `_abrir_protecao`, criando uma SEGUNDA
        # protecao sobre o mesmo saldo. Melhor sair e tentar no proximo ciclo.
        if not self._liberar_protecao():
            print(
                f"[EXEC] Fechamento de {self.symbol} ADIADO: protecao nao pode ser "
                f"cancelada. Nova tentativa no proximo ciclo."
            )
            try:
                database.salvar_bot_event(
                    "fechamento_adiado",
                    f"{self.symbol}: cancelamento da protecao falhou; SELL nao enviado "
                    f"para nao duplicar protecao. Motivo do fechamento: {motivo}",
                    service="executor",
                    symbol=self.symbol,
                    severity="WARNING",
                )
            except Exception:
                pass
            return

        qty = pos["tamanho_btc"]
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
            # P0-2/P2-1: SELL falhou e a protecao foi cancelada — RECOLOCA
            prot = self._abrir_protecao(pos["tamanho_btc"], pos["stop_atual"], pos.get("target2"))
            with self._lock:
                if self.posicao:
                    self.posicao["stop_order_id"] = prot["stop_order_id"]
                    self.posicao["oco_list_id"] = prot["oco_list_id"]
            self._persistir_posicao()
            return

        # Ordem preenchida: agora sim aplica a mutacao do fechamento parcial
        if parcial:
            with self._lock:
                if self.posicao:
                    self.posicao["parcial_feita"] = True
                    self.posicao["tamanho_btc"] = qty  # restante
            # P0-2/P2-1: recoloca a protecao para a metade restante (a antiga foi
            # cancelada). Com OCO, o novo bracket cobre o runner (stop + target2).
            prot = self._abrir_protecao(qty, pos["stop_atual"], pos.get("target2"))
            with self._lock:
                if self.posicao:
                    self.posicao["stop_order_id"] = prot["stop_order_id"]
                    self.posicao["oco_list_id"] = prot["oco_list_id"]
            self._persistir_posicao()

        # `preco` e a REFERENCIA que o monitor observou ao decidir fechar;
        # `preco_saida` e o preco medio em que o SELL realmente preencheu. Eles
        # nao sao iguais nem em simulacao: um SELL MARKET entra em _enviar_ordem
        # sem preco, e o ramo simulado cai em `preco or self.get_preco()` -> le
        # um preco FRESCO. A diferenca e slippage de latencia real (o monitor le,
        # o tempo passa, o preco anda).
        #
        # O PnL e a contabilidade usam o FILL, nunca a referencia: era otimista
        # por exatamente o desvio abaixo, e e a mesma coluna que
        # relatorio_gate.py usa para profit factor na Etapa 2 do gate.
        # A DECISAO de fechar segue sendo tomada sobre `preco` (em
        # avaliar_tick_monitor) -- decide-se no que se ve, contabiliza-se no que
        # se executa.
        preco_saida = preco_medio_fill(resp, preco)
        desvio_saida = (preco_saida - preco) / preco * 100 if preco else 0.0
        try:
            health.set_gauge("exec_desvio_saida_ref_fill_pct", desvio_saida)
            if abs(desvio_saida) > 0.0001:
                database.salvar_bot_event(
                    "execucao_saida",
                    f"{self.symbol} {'parcial' if parcial else 'total'}: "
                    f"ref={preco:.2f} fill={preco_saida:.2f} "
                    f"desvio={desvio_saida:+.4f}% motivo={motivo}",
                    service="executor",
                    symbol=self.symbol,
                    severity="INFO",
                )
        except Exception:
            pass

        pnl_pct = (preco_saida - pos["entrada"]) / pos["entrada"] * 100
        pnl_usdt = qty * (preco_saida - pos["entrada"])

        print(
            f"[EXEC] {'PARCIAL' if parcial else 'TOTAL'} FECHADO @ ${preco_saida:,.2f} "
            f"(ref ${preco:,.2f}, {desvio_saida:+.3f}%) | "
            f"PnL: {'+' if pnl_usdt>=0 else ''}{pnl_usdt:.2f} USDT ({pnl_pct:+.2f}%) | "
            f"Motivo: {motivo}"
        )

        # I-10h: `salvar_sinal` vem ANTES de `registrar_resultado`, e o que pode
        # falhar (I/O de banco) nao derruba mais a funcao.
        #
        # Era o contrario: `registrar_resultado` (que muta o estado de risco em
        # memoria) rodava primeiro e `salvar_sinal` logo depois, sem try. Uma
        # excecao no banco propagava ate o `except` do monitor, `self.posicao`
        # nunca era zerada (isso so acontece bem abaixo), e 10 segundos depois o
        # mesmo tick refazia tudo — somando o MESMO PnL ao drawdown do dia a
        # cada ciclo, para sempre. O registro de risco agora e a ULTIMA coisa a
        # acontecer nesta secao, e o unico passo que nao pode falhar.
        try:
            database.salvar_sinal(
                "FECHAR_LONG" if pnl_usdt >= 0 else "STOP",
                preco_saida,  # o preco EXECUTADO, nao a referencia do monitor
                f"{motivo} | PnL: {pnl_pct:+.2f}%",
                symbol=self.symbol,
                source="executor",
                executado=True,
            )
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao salvar sinal de fechamento: {e}")
        gestao_risco.registrar_resultado(pnl_usdt)

        if parcial:
            # P1-3: acumula o PnL desta perna parcial -- o fechamento FINAL
            # soma isso ao PnL da propria perna final para o resultado do
            # trade inteiro bater certo em atualizar_sinal_fechamento.
            with self._lock:
                if self.posicao:
                    self.posicao["pnl_usdt_parcial_acumulado"] = (
                        self.posicao.get("pnl_usdt_parcial_acumulado", 0.0) + pnl_usdt
                    )
        else:
            # P1-3: fechamento FINAL -- liga o resultado COMPLETO do trade
            # (parciais anteriores + esta perna) de volta ao sinal de
            # entrada, classificando a barreira pelo MOTIVO exato (nao pelo
            # sinal do PnL, que conflava um trailing-stop em lucro com um
            # target real atingido).
            pnl_usdt_total = pos.get("pnl_usdt_parcial_acumulado", 0.0) + pnl_usdt
            tamanho_original = pos.get("tamanho_btc_original") or qty
            pnl_pct_total = (
                pnl_usdt_total / (tamanho_original * pos["entrada"]) * 100
                if tamanho_original > 0
                else pnl_pct
            )
            try:
                database.atualizar_sinal_fechamento(
                    pos.get("sinal_id"),
                    preco_saida,  # preco_saida da coluna = fill, nao referencia
                    round(pnl_usdt_total, 2),
                    round(pnl_pct_total, 4),
                    _classificar_barreira(motivo),
                )
            except Exception as e:
                print(f"[EXEC] AVISO: falha ao atualizar sinal de fechamento: {e}")

            # E-8: fecha a linha em `log_trades` — a tabela que
            # logger.dados_relatorio_diario le para somar o PnL do dia. Sem este
            # par entrada/saida, o alerta das 18h somava conjunto vazio e
            # reportava PnL 0,00 num dia com trades.
            log_id = getattr(self, "_log_trade_id", None)
            if log_id is not None:
                try:
                    from logger import logger as _log_estruturado

                    _log_estruturado.registrar_trade_saida(
                        log_id,
                        preco_saida,
                        _classificar_barreira(motivo),
                        round(pnl_usdt_total, 2),
                        round(pnl_pct_total, 4),
                        gestao_risco.get_saldo_usdt(),
                        motivo,
                    )
                except Exception as e:
                    print(f"[EXEC] AVISO: falha ao registrar saida em log_trades: {e}")
                finally:
                    self._log_trade_id = None
            else:
                # Sem id de entrada: a posicao foi reidratada de um restart ou a
                # insercao de entrada falhou. Avisa em vez de sumir — igual ao
                # tratamento de sinal_id=None em database.py.
                print(
                    f"[EXEC] AVISO: trade fechado sem linha de entrada em log_trades "
                    f"(PnL ${pnl_usdt_total:.2f}); o relatorio diario nao vai conta-lo."
                )

            with self._lock:
                self.posicao = None
                self._ativo = False
            self._persistir_posicao()  # P0-3: remove do DB
            gestao_risco.decrementar_posicoes_abertas()
            gestao_risco.persistir_estado()

            # P2-5: 1 alerta por trade concluido -- alerta_stop para a barreira
            # de stop especifica, alerta_trade_fechado para as demais (target/
            # manual). Nao dispara na perna parcial (a posicao ainda segue aberta).
            try:
                if motivo == "Stop Loss":
                    telegram_bot.alerta_stop(preco, pnl_usdt_total)
                else:
                    telegram_bot.alerta_trade_fechado(
                        pos["tipo"], pos["entrada"], preco, pnl_usdt_total, pnl_pct_total, motivo
                    )
            except Exception:
                pass

    # ── Ajuste de stop (trailing / breakeven) ──────────────────

    def _aplicar_novo_stop(self, novo_stop, msg_template):
        """Move a protecao para novo_stop e, se o move de fato ocorreu, atualiza
        stop_atual/persiste/loga. Se o trailing e server-side (OCO com
        trailingDelta), _mover_protecao devolve a sentinela — a exchange ja
        trilha o stop, entao aqui NAO se toca em stop_atual (o floor local segue
        sendo um backstop seguro) nem se persiste."""
        if not self.posicao:
            return
        res = self._mover_protecao(novo_stop)
        if res is _PROTECAO_NAO_MOVIDA:
            return  # trailing server-side: exchange dona do stop
        with self._lock:
            if self.posicao:
                self.posicao["stop_order_id"] = res
                self.posicao["stop_atual"] = novo_stop
        self._persistir_posicao()
        print(f"[EXEC] {msg_template.format(v=novo_stop)}")

    # ── Monitor de trailing stop ───────────────────────────────

    def _monitorar(self):
        """Thread que verifica stop/target e ajusta trailing stop.

        A decisão por-tick fica em avaliar_tick_monitor() (pura/testável); aqui
        há apenas I/O (preço, fechamento, sleep) e atualização de estado sob lock.

        I-10f: esta thread e a unica coisa entre a posicao e o mercado quando o
        stop server-side nao basta (parcial, trailing local). Ela nao pode
        morrer em silencio, e por isso:

        - `preco_pico` e inicializado DENTRO do laco protegido. Estava fora do
          try: se `self.posicao` fosse None no instante do start (corrida com um
          fechamento imediato), a thread levantava AttributeError e morria antes
          do primeiro tick — sem log, sem alerta.
        - o `break` apos `fechar_posicao` deixou de ser incondicional. O
          fechamento tem caminhos que MANTEM a posicao de proposito (SELL nao
          preencheu, cancelamento da protecao falhou); sair do laco ali deixava
          a posicao viva e sem monitor.
        """
        preco_pico = None
        ciclos = 0
        fechamentos_falhos = 0

        while self._ativo and self.posicao:
            try:
                if preco_pico is None:
                    with self._lock:
                        if not self.posicao:
                            break
                        preco_pico = self.posicao["entrada"]

                ciclos += 1
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
                    # modo_trend: quem trilha o stop e o canal Donchian-M (via
                    # _aplicar_novo_stop, no ciclo diario). O trailing por
                    # percentual fixo daqui competiria com ele e sairia cedo,
                    # cortando a cauda direita que e o edge do sistema.
                    **(
                        {"trailing_ativacao": float("inf")}
                        if self.modo_trend
                        else {}
                    ),
                )
                preco_pico = d["preco_pico"]

                # Fechamento terminal (stop loss ou take-profit final)
                if d["encerrar"]:
                    self.fechar_posicao(preco, d["fechar_total"])
                    # I-10f: so encerra o laco se a posicao REALMENTE fechou.
                    # `fechar_posicao` mantem a posicao de proposito quando o
                    # SELL nao preenche ou a protecao nao pode ser cancelada;
                    # o `break` incondicional matava o monitor e deixava a
                    # posicao viva sem trailing, sem parcial e sem retry.
                    if not self.posicao:
                        break
                    fechamentos_falhos += 1
                    print(
                        f"[EXEC] Fechamento de {self.symbol} nao concluiu "
                        f"(tentativa {fechamentos_falhos}) — monitor SEGUE ativo."
                    )
                    # Retentar e melhor que morrer, mas retentar em silencio
                    # para sempre e so outra forma de falhar sem ninguem ver.
                    # Escala UMA vez ao cruzar o limite (latched) e segue
                    # tentando: se a exchange voltar, a posicao ainda fecha.
                    if fechamentos_falhos == MAX_FECHAMENTOS_FALHOS:
                        self._escalar_fechamento_travado(fechamentos_falhos, d["fechar_total"])
                    time.sleep(10)
                    continue
                fechamentos_falhos = 0

                # Take-profit parcial (50%) + stop em breakeven
                if d["fechar_parcial"]:
                    self.fechar_posicao(preco, "Take Profit Parcial (50%)", parcial=True)
                    if self.posicao:
                        self._aplicar_novo_stop(
                            d["stop_breakeven"], "Stop movido para breakeven: ${v:,.2f}"
                        )

                # Trailing stop (pode coexistir com o parcial no mesmo tick)
                if d["novo_stop_trailing"] is not None and self.posicao:
                    self._aplicar_novo_stop(
                        d["novo_stop_trailing"],
                        f"Trailing Stop: ${{v:,.2f}} (pico: ${preco_pico:,.2f})",
                    )
                    try:
                        telegram_bot.alerta_trailing_stop(d["novo_stop_trailing"], preco_pico)
                    except Exception:
                        pass

                # I-10g: perguntar a exchange, nao so olhar o preco.
                if ciclos % CICLOS_ENTRE_RECONCILIACOES == 0:
                    self._reconciliar_protecao_viva()

            except Exception as e:
                print(f"[EXEC] Erro no monitor: {e}")

            time.sleep(10)  # verificar a cada 10 segundos

    def _escalar_fechamento_travado(self, tentativas, motivo):
        """I-10f: N fechamentos seguidos falharam. Grita uma vez e trava o bot.

        A posicao segue aberta e o monitor segue tentando — travar impede
        ENTRADAS novas, nao a saida desta. O que nao pode acontecer e o bot
        abrir a proxima posicao enquanto esta esta presa.
        """
        msg = (
            f"{self.symbol}: {tentativas} tentativas de fechamento ({motivo}) falharam "
            f"seguidas. Posicao segue ABERTA; o monitor continua retentando."
        )
        print(f"[EXEC] CRITICO: {msg}")
        try:
            database.salvar_bot_event(
                "fechamento_travado",
                msg,
                service="executor",
                symbol=self.symbol,
                severity="CRITICAL",
            )
        except Exception:
            pass
        try:
            gestao_risco.travar(msg)
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao travar o bot: {e}")

    def _reconciliar_protecao_viva(self):
        """I-10g: confirma na exchange que a protecao da posicao ainda existe.

        Todo o resto do monitor decide olhando o PRECO. Isso e cego para os
        dois estados que mais importam:

          1. o stop EXECUTOU na exchange (gap noturno, ou o servidor trilhou
             sozinho via trailingDelta) e o bot segue achando que esta
             comprado — continua movendo stop de uma posicao que nao existe,
             e o PnL nunca e contabilizado;
          2. o stop foi CANCELADO por fora (na interface da Binance, ou por um
             cancel-all) e a posicao esta descoberta sem ninguem saber.

        `_status_ordem` existia e tinha 2 chamadores, um deles atras de flag
        desligada. Aqui ela vira o unico caminho que fecha o circuito.
        """
        if self.simulacao or not self.posicao:
            return
        stop_id = self.posicao.get("stop_order_id")
        if not stop_id:
            return  # sem id para consultar (OCO com trailing server-side, etc.)
        status = self._status_ordem(stop_id)
        if not status:
            return  # consulta falhou: nao concluir nada de uma resposta ausente
        estado = status.get("status")
        if estado == "FILLED":
            preco_stop = float(status.get("price") or self.posicao.get("stop_atual") or 0) or None
            print(
                f"[EXEC] Stop de {self.symbol} EXECUTOU na exchange (orderId={stop_id}) "
                f"— reconciliando o fechamento localmente."
            )
            try:
                database.salvar_bot_event(
                    "stop_executado_fora_do_bot",
                    f"{self.symbol}: stop {stop_id} preencheu na exchange; o bot detectou "
                    f"na reconciliacao periodica, nao no proprio fluxo de fechamento.",
                    service="executor",
                    symbol=self.symbol,
                    severity="WARNING",
                )
            except Exception:
                pass
            # A protecao ja nao existe: zerar os ids ANTES de fechar evita que
            # `_liberar_protecao` tente cancelar uma ordem preenchida.
            with self._lock:
                if self.posicao:
                    self.posicao["stop_order_id"] = None
                    self.posicao["oco_list_id"] = None
            self._fechar_por_reconciliacao(preco_stop or self.get_preco(), "Stop Loss")
        elif estado in ("CANCELED", "EXPIRED", "REJECTED"):
            print(
                f"[EXEC] AVISO: protecao de {self.symbol} sumiu da exchange "
                f"(orderId={stop_id}, status={estado}) — recolocando."
            )
            health.increment_metric("ordens_erro")
            prot = self._abrir_protecao(
                self.posicao["tamanho_btc"],
                self.posicao["stop_atual"],
                self.posicao.get("target2"),
            )
            with self._lock:
                if self.posicao:
                    self.posicao["stop_order_id"] = prot["stop_order_id"]
                    self.posicao["oco_list_id"] = prot["oco_list_id"]
            self._persistir_posicao()
            if prot["stop_order_id"] is None and prot["oco_list_id"] is None:
                gestao_risco.travar(
                    f"{self.symbol}: protecao sumiu da exchange e nao pode ser recolocada "
                    f"— posicao real descoberta"
                )

    def _fechar_por_reconciliacao(self, preco, motivo):
        """Fecha o registro LOCAL de uma posicao que a exchange ja encerrou.

        Caminho separado de `fechar_posicao` de proposito: aquela envia um SELL
        a mercado, e aqui a exchange ja vendeu — o SELL seria uma venda a
        descoberto de um saldo que nao existe mais. Este metodo so contabiliza:
        PnL, sinal, risco e limpeza de estado.
        """
        with self._lock:
            pos = dict(self.posicao) if self.posicao else None
            if pos is None or pos.get("em_fechamento"):
                return
            self.posicao["em_fechamento"] = True
        try:
            qty = pos["tamanho_btc"]
            pnl_usdt = (preco - pos["entrada"]) * qty
            pnl_pct = (preco - pos["entrada"]) / pos["entrada"] * 100 if pos["entrada"] else 0.0
            print(
                f"[EXEC] RECONCILIADO: {self.symbol} fechado na exchange @ ${preco:,.2f} "
                f"| PnL: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%) | Motivo: {motivo}"
            )
            pnl_total = pos.get("pnl_usdt_parcial_acumulado", 0.0) + pnl_usdt
            try:
                database.atualizar_sinal_fechamento(
                    pos.get("sinal_id"),
                    preco,
                    round(pnl_total, 2),
                    round(pnl_pct, 4),
                    _classificar_barreira(motivo),
                )
            except Exception as e:
                print(f"[EXEC] AVISO: falha ao atualizar sinal na reconciliacao: {e}")
            try:
                database.salvar_sinal(
                    "STOP",
                    preco,
                    f"{motivo} (reconciliado) | PnL: {pnl_pct:+.2f}%",
                    symbol=self.symbol,
                    source="executor",
                    executado=True,
                )
            except Exception as e:
                print(f"[EXEC] AVISO: falha ao salvar sinal na reconciliacao: {e}")
            gestao_risco.registrar_resultado(pnl_usdt)
            with self._lock:
                self.posicao = None
                self._ativo = False
            self._persistir_posicao()
            gestao_risco.decrementar_posicoes_abertas()
            gestao_risco.persistir_estado()
            try:
                telegram_bot.alerta_stop(preco, pnl_total)
            except Exception:
                pass
        finally:
            with self._lock:
                if self.posicao:
                    self.posicao["em_fechamento"] = False

    # ── Reconciliacao de boot (auditoria 2026-07-22) ───────────
    # O boot recovery legado (main.py:loop_par) so lia database.carregar_
    # posicoes_abertas() e religava o monitor cegamente -- nunca cruzava com
    # o estado real da Binance. Um crash entre o fill e o registro local (a
    # janela fechada acima em abrir_long/_persistir_posicao) deixava uma
    # posicao orfa (real + protegida na exchange) invisivel ao reiniciar; o
    # inverso (DB com posicao ja fechada fora do bot) tambem nao era detectado.
    # RECONCILIAR_BOOT_EXCHANGE=true liga essas checagens (default False).

    # Divergencia maxima tolerada entre a entrada persistida e o preco de
    # mercado no boot. 50% e folgado de proposito: cobre gap real e posicao
    # velha legitima, mas nao cobre ordem de magnitude.
    DIVERGENCIA_MAX_REIDRATACAO = 0.50

    def _posicao_e_plausivel(self, pos_salva):
        """Sanidade antes de readotar uma posicao do banco. Devolve (ok, motivo).

        Existe por um incidente real (2026-07-31): a suite de testes gravava
        posicao de fixture no banco de PRODUCAO -- entrada 100.0, order_id
        "SIM-1", abertura "x" -- e o boot a readotava sem validar nada. Com BTC
        a ~64.000 o monitor via preco >> target1 e "fechava" no primeiro tick,
        gravando PnL de +62.658% e +64.429% em `sinais`. Aconteceu tres vezes.
        Com capital real teria enviado um SELL de BTC que a estrategia nunca
        comprou.

        O conftest.py da raiz fecha a ORIGEM; isto e a segunda camada, para o
        banco que chega sujo por outro caminho (restore, edicao manual,
        migracao).
        """
        if not isinstance(pos_salva, dict):
            return False, "registro nao e dict"
        entrada = pos_salva.get("entrada")
        if not isinstance(entrada, (int, float)) or entrada <= 0:
            return False, f"entrada invalida: {entrada!r}"

        # Marcas de fixture: order_id sintetico do ramo de simulacao e campos
        # que o codigo de producao nunca produz.
        order_id = str(pos_salva.get("order_id") or "")
        if not self.simulacao and order_id.startswith("SIM-"):
            return False, f"order_id de simulacao ({order_id}) num boot REAL"
        abertura = str(pos_salva.get("abertura") or "")
        if abertura and "T" not in abertura:
            return False, f"abertura nao e timestamp ISO: {abertura!r}"

        preco = self.get_preco()
        if preco <= 0:
            # Sem preco nao da para julgar. Nao bloqueia: recusar posicao real
            # por API fora e pior do que aceitar uma suja aqui.
            return True, "preco indisponivel — plausibilidade nao verificada"
        divergencia = abs(preco - entrada) / preco
        if divergencia > self.DIVERGENCIA_MAX_REIDRATACAO:
            return False, (
                f"entrada {entrada:,.2f} diverge {divergencia * 100:.0f}% do mercado "
                f"{preco:,.2f} (limite {self.DIVERGENCIA_MAX_REIDRATACAO * 100:.0f}%)"
            )
        return True, "ok"

    def reidratar_posicao(self, pos_salva):
        """Restaura uma posicao (persistida ou reconstruida) e religa o
        monitor. Usado tanto pelo caminho legado (RECONCILIAR_BOOT_EXCHANGE=
        false) quanto pelos casos 'concordam'/'orfa recuperada' de
        reconciliar_boot().

        Retorna True se readotou. RECUSA posicao implausivel em vez de religar o
        monitor sobre ela -- ver _posicao_e_plausivel.
        """
        ok, motivo = self._posicao_e_plausivel(pos_salva)
        if not ok:
            print(
                f"[EXEC] RECUSADA a reidratacao de posicao em {self.symbol}: {motivo}. "
                f"Monitor NAO religado; registro mantido no banco para inspecao."
            )
            try:
                database.salvar_bot_event(
                    "reidratacao_recusada",
                    f"{self.symbol}: {motivo}. Posicao persistida NAO foi readotada.",
                    service="executor",
                    symbol=self.symbol,
                    severity="CRITICAL",
                )
            except Exception:
                pass
            return False

        with self._lock:
            self.posicao = pos_salva
            self._ativo = True
        self._monitor = threading.Thread(target=self._monitorar, daemon=True)
        self._monitor.start()
        return True

    def _saldo_ativo_base(self):
        """Saldo livre+travado do ativo-base (ex.: BTC em BTCUSDT). So usado
        pela reconciliacao de boot para decidir se uma posicao salva no DB
        ainda existe de fato na exchange. 0.0 em simulacao/falha."""
        if self.simulacao:
            return 0.0
        ativo = self.symbol[:-4] if self.symbol.endswith("USDT") else self.symbol
        data = self._request_assinado("GET", "/api/v3/account", {}, tentativas=2)
        if not isinstance(data, dict):
            return 0.0
        for b in data.get("balances", []):
            if b.get("asset") == ativo:
                try:
                    return float(b.get("free", 0)) + float(b.get("locked", 0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _protecao_aberta_na_exchange(self):
        """Consulta openOrders + openOrderList reais e devolve
        (tem_protecao, qty, stop_price, target_price) para self.symbol. Uma
        OCO tem prioridade (stop+alvo); sem OCO, procura um STOP_LOSS_LIMIT
        solto. (False, None, None, None) se nao achar nada ou a consulta falhar."""
        ordens = self._request_assinado(
            "GET", "/api/v3/openOrders", {"symbol": self.symbol}, tentativas=2
        )
        ocos = self._request_assinado("GET", "/api/v3/openOrderList", {}, tentativas=2)
        ordens = ordens if isinstance(ordens, list) else []
        ocos = ocos if isinstance(ocos, list) else []

        for oco in ocos:
            pernas = [p for p in (oco.get("orders") or []) if p.get("symbol") == self.symbol]
            if not pernas:
                continue
            stop_price = target_price = qty = None
            for p in pernas:
                detalhe = self._status_ordem(p.get("orderId"))
                if not detalhe:
                    continue
                tipo = detalhe.get("type", "")
                try:
                    if tipo in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                        stop_price = float(detalhe.get("stopPrice") or 0) or stop_price
                        qty = float(detalhe.get("origQty") or 0) or qty
                    elif tipo == "LIMIT_MAKER":
                        target_price = float(detalhe.get("price") or 0) or target_price
                        qty = float(detalhe.get("origQty") or 0) or qty
                except (TypeError, ValueError):
                    continue
            if qty:
                return True, qty, stop_price, target_price

        for o in ordens:
            if o.get("type") not in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                continue
            try:
                qty = float(o.get("origQty") or 0)
                stop_price = float(o.get("stopPrice") or 0) or None
            except (TypeError, ValueError):
                continue
            if qty:
                return True, qty, stop_price, None

        return False, None, None, None

    def _ultimo_preco_entrada_mytrades(self):
        """Preco do BUY fill mais recente de self.symbol via myTrades. So
        usado para reconstruir 'entrada' de uma posicao orfa (protecao real
        na exchange, sem registro local). None se falhar ou nao achar BUY."""
        trades = self._request_assinado(
            "GET", "/api/v3/myTrades", {"symbol": self.symbol, "limit": 50}, tentativas=2
        )
        if not isinstance(trades, list):
            return None
        compras = [t for t in trades if t.get("isBuyer")]
        if not compras:
            return None
        ultima = max(compras, key=lambda t: t.get("time", 0))
        try:
            return float(ultima["price"])
        except (KeyError, TypeError, ValueError):
            return None

    def reconciliar_boot(self):
        """Cruza o estado persistido no DB com o estado real da Binance antes
        de decidir como religar o monitor apos um restart. So faz sentido com
        credenciais reais (em simulacao, cai no comportamento legado -- nao ha
        conta/ordens reais para cruzar).

        Retorna dict {"acao": str, "detalhe": str}. acao em:
          "religou"        -- DB e exchange concordam (ou simulacao)
          "orfa_recuperada"-- DB vazio mas exchange tem protecao real;
                              reconstruida via myTrades + ordem aberta
          "removida"        -- DB tinha posicao, exchange nao tem nada
                              (saldo ~0); removida do DB, monitor NAO religado
          "inconsistente"   -- estado ambiguo (orfa nao-reconstruivel, ou DB
                              com posicao + saldo real mas sem protecao na
                              exchange); religa do DB (conservador) e alerta
                              para investigacao manual, NUNCA decide sozinho
                              vender ou recriar protecao aqui
          "nada"            -- nem DB nem exchange tem posicao
        """
        pos_salva = None
        try:
            pos_salva = database.carregar_posicoes_abertas().get(self.symbol)
        except Exception as e:
            print(f"[EXEC] AVISO: falha ao ler posicoes persistidas: {e}")

        if self.simulacao:
            if pos_salva:
                self.reidratar_posicao(pos_salva)
                return {"acao": "religou", "detalhe": "simulacao: confia no DB"}
            return {"acao": "nada", "detalhe": "simulacao: sem posicao salva"}

        try:
            tem_protecao, qty_ex, stop_ex, target_ex = self._protecao_aberta_na_exchange()
            saldo_ativo = self._saldo_ativo_base()
        except Exception as e:
            print(f"[EXEC] AVISO: reconciliacao de boot indisponivel ({e}) — usando so o DB")
            if pos_salva:
                self.reidratar_posicao(pos_salva)
                return {"acao": "religou", "detalhe": f"reconciliacao indisponivel: {e}"}
            return {"acao": "nada", "detalhe": f"reconciliacao indisponivel: {e}"}

        if pos_salva and tem_protecao:
            self.reidratar_posicao(pos_salva)
            return {"acao": "religou", "detalhe": "DB e exchange concordam"}

        if not pos_salva and tem_protecao:
            entrada = self._ultimo_preco_entrada_mytrades()
            if entrada is None or not qty_ex or stop_ex is None:
                try:
                    database.salvar_bot_event(
                        "reconciliacao_boot_orfa_nao_reconstruida",
                        f"{self.symbol}: protecao real na exchange sem posicao no DB, mas "
                        f"nao foi possivel reconstruir com seguranca (entrada={entrada}, "
                        f"qty={qty_ex}, stop={stop_ex}) — intervencao manual necessaria.",
                        service="executor",
                        symbol=self.symbol,
                        severity="CRITICAL",
                    )
                except Exception:
                    pass
                return {"acao": "inconsistente", "detalhe": "orfa nao reconstruida com seguranca"}

            posicao_reconstruida = {
                "tipo": "LONG",
                "entrada": entrada,
                "tamanho_btc": qty_ex,
                "tamanho_btc_original": qty_ex,
                "stop_inicial": stop_ex,
                "stop_atual": stop_ex,
                "target1": target_ex or (entrada * 1.03),
                "target2": target_ex or (entrada * 1.05),
                # Conservador: nao tenta adivinhar se um parcial de 50% ja
                # ocorreu antes do crash -- assume que sim (parcial_feita=True)
                # para nao vender 50% de uma posicao que a exchange ja reduziu.
                "parcial_feita": True,
                "abertura": datetime.now().isoformat(),
                "order_id": None,
                # ids resolvidos de novo na proxima _liberar/_mover_protecao
                "stop_order_id": None,
                "oco_list_id": None,
                # E-8: posicao ORFA reconstruida da exchange nao tem vinculo com
                # nenhuma linha de `sinais` — o crash aconteceu justamente entre
                # o fill e a persistencia. Fica None de proposito: inventar um
                # vinculo (ex: pegar o ultimo sinal COMPRA do par) atribuiria o
                # PnL deste trade a um sinal que talvez nao o tenha originado,
                # contaminando o track record com um par entrada/saida falso —
                # pior que a lacuna. O fechamento deste trade agora AVISA que o
                # PnL nao entrou no track record (database.py), em vez de sumir.
                "sinal_id": None,
                "pnl_usdt_parcial_acumulado": 0.0,
            }
            self.reidratar_posicao(posicao_reconstruida)
            self._persistir_posicao()
            try:
                database.salvar_bot_event(
                    "reconciliacao_boot_orfa_recuperada",
                    f"{self.symbol}: posicao orfa reconstruida (entrada=${entrada:,.2f}, "
                    f"qty={qty_ex}, stop=${stop_ex:,.2f}) e monitor religado",
                    service="executor",
                    symbol=self.symbol,
                    severity="CRITICAL",
                )
            except Exception:
                pass
            return {"acao": "orfa_recuperada", "detalhe": f"reconstruida: entrada={entrada}"}

        if pos_salva and not tem_protecao:
            if saldo_ativo < self._min_qty:
                try:
                    database.remover_posicao_aberta(self.symbol)
                except Exception:
                    pass
                try:
                    database.salvar_bot_event(
                        "reconciliacao_boot_fechada_fora",
                        f"{self.symbol}: DB tinha posicao mas exchange nao tem protecao "
                        f"nem saldo do ativo — fechada fora do bot, removida do DB sem "
                        f"religar monitor",
                        service="executor",
                        symbol=self.symbol,
                        severity="CRITICAL",
                    )
                except Exception:
                    pass
                return {"acao": "removida", "detalhe": "fechada fora do bot"}
            # Saldo existe mas sem protecao na exchange: estado inconsistente.
            # Mais seguro religar do DB (comportamento legado) e alertar do
            # que decidir sozinho vender ou recriar protecao aqui.
            self.reidratar_posicao(pos_salva)
            try:
                database.salvar_bot_event(
                    "reconciliacao_boot_sem_protecao",
                    f"{self.symbol}: posicao no DB, saldo do ativo existe, mas SEM "
                    f"protecao aberta na exchange — religado do DB, verifique manualmente",
                    service="executor",
                    symbol=self.symbol,
                    severity="WARNING",
                )
            except Exception:
                pass
            return {"acao": "inconsistente", "detalhe": "religado do DB, sem protecao exchange"}

        return {"acao": "nada", "detalhe": "nem DB nem exchange tem posicao"}

    # ── Status da posição ──────────────────────────────────────

    def status(self):
        # Snapshot unico sob lock (M-2): evita o TOCTOU de checar
        # self.posicao e so depois desreferencia-lo -- se _monitorar fechar a
        # posicao exatamente entre as duas leituras, pos vira None de forma
        # consistente em vez de estourar TypeError no meio do dict abaixo.
        with self._lock:
            pos = dict(self.posicao) if self.posicao else None
        if pos is None:
            return {"posicao": "Nenhuma posicao aberta"}
        preco = self.get_preco()
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
