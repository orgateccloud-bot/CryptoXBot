"""

BotBinance — Orquestrador Principal v2
=========================================
Threads em execução simultânea:
  1. WebSocket Binance BTC  → CVD BTC em tempo real
  2. Loop de estratégia     → avalia sinal a cada N minutos (por par)
  3. Executor + Monitor     → gerencia posição aberta por par (trailing stop)

Pares ativos: BTCUSDT, ETHUSDT (parâmetros otimizados por par)

Uso:
  python main.py                    → modo completo (padrão)
  python main.py --simulacao        → paper trading (sem ordens reais)
  python main.py --intervalo 5      → avalia estratégia a cada 5 min
  python main.py --relatorio        → só imprime relatório e sai
  python main.py --estrategia       → avalia sinal uma vez e sai
  python main.py --backtest 1h      → roda backtest e sai
  python main.py --treinar-ml       → treina modelo ML e sai
  python main.py --par BTCUSDT      → operar apenas um par específico
  python main.py --modo-trend --simulacao
                                    → dry run do sistema Donchian 20/10
                                      (validação de EXECUÇÃO; estratégia
                                       reprovada — recusa rodar com --real)
"""

import argparse
import asyncio
import json
import logging
import random
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime

import websockets

import database
import fear_greed as fg
import health
import regime as reg
import risco as gestao_risco
import telegram_bot
from analise_mercado import relatorio_completo
from config.runtime_settings import (
    ALLOW_REAL_TRADING,
    ENABLE_HEALTH_SERVER,
    MIN_BTC_VOLUME,
    RECONCILIAR_BOOT_EXCHANGE,
    SYMBOL_WS,
    WHALE_BTC_VOLUME,
    WS_BASE_URL,
)
from estrategias.otimizada import _incoerencia_de_precos as incoerencia_de_precos
from estrategias.otimizada import analisar as analisar_otimizada
from estrategias.otimizada import imprimir as imprimir_otimizada
from estrategias.otimizada import registrar_sinal as registrar_sinal_otimizada
from executor import Executor
from health import start_health_server
from logger import logger

# Console Windows padrao e cp1252, e este arquivo imprime "→"/"─"/emoji: o
# primeiro deles matava execucao MANUAL com UnicodeEncodeError (sob NSSM nao
# ha console, por isso o servico 24/7 nunca sentiu). Mesma guarda dos scripts.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

# E-8: `from suporte import ScaleIn` removido — o scale-in saiu do caminho vivo
# (as parcelas 2 e 3 eram inalcancaveis pelo gate `not exec_par.posicao`, e o
# objeto nao resetado dimensionava o proximo trade sobre o tamanho do anterior).
# A classe segue em suporte.py, com testes, para quando existir uma maquina de
# estados que de fato avalie com posicao aberta.

# Retreinamento automático semanal (domingo 02h)
_RETREINAMENTO_HORA = 2  # hora do dia (02:00)
_RETREINAMENTO_DIA = 6  # 6 = domingo (weekday())

# P2-5: relatório diário via Telegram (18h) — telegram_bot.relatorio_diario()
# existia pronta mas nunca era chamada (achado da auditoria 2026-07-22).
_RELATORIO_HORA = 18

# Configurar logging estruturado para WebSocket
ws_logger = logging.getLogger("websocket")
ws_logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
ws_logger.addHandler(handler)

# Pares ativos (BTC sempre ativo, ETH com parâmetros otimizados)
PARES_ATIVOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# ── Modo trend (dry run de validacao de execucao) ──────────────
# Ligado por --modo-trend. Substitui a estrategia otimizada pelo sistema
# Donchian 20/10 (estrategias/trend_live.py). NAO e uma estrategia aprovada:
# reprovou no hold-out (research/METODOLOGIA_TREND.md). Roda so em simulacao —
# ver _validar_trend_so_em_simulacao(), que e um SystemExit, nao um aviso.
MODO_TREND = False
MODO_TREND_INTERVALO = "1d"
# Ultimo bucket de candle ja processado por par: garante NO MAXIMO uma decisao
# por candle fechado, como no backtest (que avalia 1x por barra). Sem isso, um
# --intervalo de 15min reavaliaria a MESMA barra diaria 96x e poderia reentrar
# no mesmo dia logo apos um stop — algo que o backtest nunca faz.
_trend_ultimo_bucket: dict[str, int] = {}
_SEGUNDOS_POR_INTERVALO = {"1d": 86400, "4h": 14400, "1h": 3600}


# P1: morte de thread NUNCA silenciosa — threads daemon (loop_par, monitor,
# websocket) que morrem por exceção não capturada ficam registradas em log e
# no banco (bot_events), para o operador perceber que o bot parou de operar.
def _thread_excepthook(args):
    nome = args.thread.name if args.thread else "?"
    msg = f"Thread '{nome}' morreu: {args.exc_type.__name__}: {args.exc_value}"
    print(f"\033[91m[THREAD-CRASH] {msg}\033[0m")
    try:
        database.salvar_bot_event("thread_crash", msg, service="worker", severity="CRITICAL")
    except Exception:
        pass  # nunca deixar o hook derrubar o processo


threading.excepthook = _thread_excepthook

# ── Estado global ──────────────────────────────────────────────
cvd_btc = 0.0
total_compras = 0.0
total_vendas = 0.0
preco_atual = 0.0
_lock = threading.Lock()

# P1-1: buffer dos ultimos N ticks brutos do BTCUSDT (formato consumido por
# score._score_cvd: preco/is_buyer_maker/quantidade) -- antes desta mudanca
# so existiam acumuladores escalares (cvd_btc/total_compras/total_vendas),
# entao o caminho ao vivo nunca tinha o historico bruto que _score_cvd
# precisa (sempre passava historico_ticks=None, componente CVD do score
# ficava travado em 50/neutro). maxlen=200 da margem folgada acima do
# periodo=50 default de _score_cvd sem custo de memoria relevante.
_historico_ticks_btc = deque(maxlen=200)

# Estado WebSocket
ws_state = {
    "last_trade_id": 0,  # Para evitar duplicatas
    "connected": False,
    "last_message_time": 0.0,
    "latency_ms": 0.0,
}

# ── OBI — Order Book Imbalance (P1-1) ───────────────────────────
# Partial Book Depth Stream (@depthN@100ms): cada mensagem ja traz os top-N
# niveis de bid/ask completos e independentes -- ao contrario do diff-depth
# stream (@depth puro), NAO exige snapshot REST + reconciliacao U/u/pu (o
# protocolo de sincronizacao da Binance para manter um order book completo
# e correto). Para OBI so precisamos do volume nos primeiros niveis, entao
# a partial-book stream evita toda essa classe de bug de dessincronizacao
# por construcao -- nenhuma logica de continuidade e necessaria aqui.
OBI_DEPTH_LEVELS = 20  # @depth20 -- top 20 niveis de cada lado
OBI_JANELA_SUAVIZACAO = 30  # media movel sobre as ultimas 30 mensagens
# (~3s a 100ms/msg) contra spoofing (ordens fantasma colocadas e canceladas
# em janelas curtas nao devem mover o OBI suavizado sozinhas).

ws_state_depth = {
    "connected": False,
    "last_message_time": 0.0,
    "latency_ms": 0.0,
}
_lock_obi = threading.Lock()  # lock DEDICADO, distinto de _lock (mesmo padrao
# de _lock_correlacao em risco.py) -- @depth e @aggTrade rodam em threads/
# conexoes WS independentes, sem motivo para serializar uma no estado da outra.
_obi_historico = deque(maxlen=OBI_JANELA_SUAVIZACAO)

# C-7: shutdown gracioso. O Event e signal-safe (setar dentro de um signal
# handler e seguro; raise nao e confiavel). _ws_loop guarda o loop asyncio do
# WebSocket para pedir seu encerramento de fora da thread dele.
_shutdown_event = threading.Event()
_ws_loop = None
_ws_loop_depth = None  # loop asyncio da conexao @depth (P1-1), independente de _ws_loop

# Estado por par (executor e scale-in independentes)
_estado_pares = {}  # symbol → {"executor": Executor, "scale_in": ScaleIn|None}


# ── Helpers ────────────────────────────────────────────────────


def formatar_valor(v):
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}K"


# ── WebSocket Binance Assíncrono com Retry ──────────────────


def _ws_encerrando(rotulo: str) -> bool:
    """True se o WebSocket esta parando porque PEDIMOS (shutdown), nao porque
    falhou. Existe para que um stop limpo nao seja logado como incidente.

    Sem esta distincao, TODO shutdown gracioso escrevia "Erro critico
    WebSocket" no stderr -- e a string que se usaria para caçar uma falha real
    (conexao zumbi, CVD congelado, o que o watchdog de /ready existe para pegar)
    aparecia em toda parada normal. Ruido que mascara sinal e pior que silencio.

    A ordem em _encerrar() garante que isto funcione: o Event e setado ANTES de
    parar os loops, entao quando as excecoes de loop fechado chegam aqui o
    shutdown ja esta marcado.
    """
    if not _shutdown_event.is_set():
        return False
    ws_logger.info(f"{rotulo} encerrado a pedido (shutdown gracioso)")
    return True


# ── Politica de retry dos WebSockets (compartilhada pelos dois) ──
# Incidente de 2026-07-31, e antes dele uma queda de 8h40 em julho: `attempt`
# era inicializado FORA do while e nunca resetado numa conexao bem-sucedida, e
# o loop era `while attempt < 10`. Ou seja, 10 falhas ao longo de TODA a vida do
# processo esgotavam o orcamento -- num servico 24/7, isso e certeza matematica.
# Pior, o ramo `except Exception` generico dormia 1s fixo, queimando as 10
# tentativas em ~8 segundos numa falha rapida e repetida.
#
# A politica correta para processo 24/7 e: NUNCA desistir, saturar o backoff num
# teto, e ESCALAR quando a falha persiste. O que se limita e a frequencia de
# tentativa, nao a quantidade.
WS_DELAY_TETO_S = 300.0
WS_FALHAS_PARA_ESCALAR = 5  # ~1 min de indisponibilidade com o backoff abaixo


def _ws_delay_backoff(falhas_seguidas: int, base: float = 1.0, jitter: float = 0.1) -> float:
    """Backoff exponencial com jitter, saturando em WS_DELAY_TETO_S.

    O expoente e limitado a 12 para 2**n nao estourar em processo de vida longa
    (2**12 * 1s ja passa do teto de 300s de qualquer forma).
    """
    d = min(base * (2 ** min(max(falhas_seguidas, 0), 12)), WS_DELAY_TETO_S)
    d += random.uniform(-jitter * d, jitter * d)
    return max(0.1, d)


_ws_escalado = {}


def _ws_escalar_se_persistente(rotulo: str, falhas_seguidas: int, erro: str) -> None:
    """Escala UMA vez por episodio quando a indisponibilidade persiste.

    Sem isto o worker fica cego em silencio: ele segue avaliando sinal e
    dimensionando ordem com CVD/tape congelados, e o unico vestigio e o 503 de
    /ready, que nao tem probe nenhum lendo.
    """
    if falhas_seguidas < WS_FALHAS_PARA_ESCALAR or _ws_escalado.get(rotulo):
        return
    _ws_escalado[rotulo] = True
    logger.critical(
        f"{rotulo} indisponivel de forma persistente",
        extra={"falhas_seguidas": falhas_seguidas, "error": erro},
    )
    try:
        database.salvar_bot_event(
            "ws_indisponivel",
            f"{rotulo}: {falhas_seguidas} falhas seguidas de conexao. Ultimo erro: {erro}. "
            f"O bot segue avaliando sinal com dado POSSIVELMENTE CONGELADO.",
            service="worker",
            severity="CRITICAL",
        )
    except Exception:
        pass
    try:
        # NÃO é circuit breaker: nada pausa. Até 2026-08-19 este site reusava
        # alerta_circuit_breaker e o operador acordava com "O bot foi pausado.
        # Revise manualmente" — falso nas duas metades.
        telegram_bot.alerta_ws_indisponivel(rotulo, falhas_seguidas, erro)
    except Exception:
        pass


def _ws_marcar_recuperado(rotulo: str, falhas_seguidas: int) -> None:
    """Rearma o alerta e registra a recuperacao (senao o proximo episodio passa
    silencioso, que e pior do que nao alertar)."""
    if not _ws_escalado.get(rotulo):
        return
    _ws_escalado[rotulo] = False
    ws_logger.info(f"{rotulo} reconectado apos {falhas_seguidas} falhas seguidas")
    try:
        database.salvar_bot_event(
            "ws_recuperado",
            f"{rotulo} reconectado apos {falhas_seguidas} falhas seguidas.",
            service="worker",
            severity="WARNING",
        )
    except Exception:
        pass
    try:
        # O tudo-limpo no Telegram: o vigia so encaminha CRITICAL, entao sem
        # este envio o operador recebe o alarme e nunca a recuperacao.
        telegram_bot.alerta_ws_recuperado(rotulo, falhas_seguidas)
    except Exception:
        pass


def _drenar_tasks_pendentes(loop):
    """Cancela e drena as tasks que sobraram, ANTES de fechar o loop.

    Sem isto, loop.close() deixa tasks suspensas (a keepalive() do pacote
    websockets, e a propria task do handler, parada num await) para o GC
    finalizar depois -- e o interpretador cospe "Task was destroyed but it is
    pending!" mais tracebacks de "Event loop is closed" / "no running event
    loop" em todo shutdown limpo.

    Cancelar de forma ordenada faz o CancelledError chegar no ponto de await de
    cada task, que entao termina como cancelada em vez de virar lixo do GC.
    Timeout curto e best-effort: shutdown nunca pode ficar preso.
    """
    try:
        pendentes = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if not pendentes:
            return
        for t in pendentes:
            t.cancel()
        loop.run_until_complete(asyncio.wait(pendentes, timeout=3))
    except Exception:
        pass  # best-effort: nada aqui pode impedir o processo de morrer


async def websocket_handler():
    """
    Handler assíncrono para WebSocket Binance com retry exponencial e state management.
    """
    # P0-1: mesmo mercado da execucao (spot por padrao, via WS_BASE_URL).
    url = f"{WS_BASE_URL}/ws/{SYMBOL_WS}@aggTrade"

    falhas = 0  # SEGUIDAS, zeradas a cada conexao bem-sucedida
    while not _shutdown_event.is_set():  # 24/7: nunca desiste, so espaca
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket_conn:
                ws_state["connected"] = True
                ws_state["last_message_time"] = time.time()
                ws_logger.info(
                    "WebSocket conectado", extra={"symbol": SYMBOL_WS, "falhas_previas": falhas}
                )
                # ESTE reset e a correcao central: antes o contador so subia, e
                # 10 falhas espalhadas por dias encerravam o WebSocket para
                # sempre sem que nada reconectasse.
                _ws_marcar_recuperado("WebSocket", falhas)
                falhas = 0

                async for message in websocket_conn:
                    try:
                        await process_message(message)
                        ws_state["last_message_time"] = time.time()
                    except Exception as e:
                        ws_logger.error(
                            "Erro processando mensagem",
                            extra={
                                "error": str(e),
                                "symbol": SYMBOL_WS,
                                "latency_ms": ws_state["latency_ms"],
                            },
                        )

        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.WebSocketException,
            asyncio.TimeoutError,
        ) as e:
            ws_state["connected"] = False
            # Fechar a conexao no shutdown levanta ConnectionClosed: e parada
            # pedida, nao desconexao. Sair aqui tambem evita o asyncio.sleep(delay)
            # abaixo rodar sobre um loop ja fechado.
            if _ws_encerrando("WebSocket"):
                return
            latency = (time.time() - ws_state["last_message_time"]) * 1000
            ws_state["latency_ms"] = latency
            health.increment_metric("ws_reconexoes")

            falhas += 1
            delay = _ws_delay_backoff(falhas)
            logger.warning(
                "WebSocket desconectado",
                extra={
                    "error": str(e),
                    "symbol": SYMBOL_WS,
                    "falhas_seguidas": falhas,
                    "latency_ms": latency,
                    "next_retry_in_s": round(delay, 1),
                },
            )
            _ws_escalar_se_persistente("WebSocket", falhas, str(e))
            await asyncio.sleep(delay)

        except Exception as e:
            # RuntimeError("Event loop is closed") chega aqui quando o loop e
            # parado debaixo de um await -> era isto que virava
            # "Erro critico WebSocket" em todo stop limpo.
            if _ws_encerrando("WebSocket"):
                return
            ws_state["connected"] = False
            health.increment_metric("ws_reconexoes")
            falhas += 1
            # MESMO backoff do ramo de rede. Antes eram 1,0s fixos aqui, o que
            # queimava o orcamento inteiro de tentativas em ~8 segundos quando a
            # falha era imediata e repetida -- foi assim que os dois WebSockets
            # morreram em 2026-07-31 logo apos o boot.
            delay = _ws_delay_backoff(falhas)
            logger.error(
                "Erro crítico WebSocket",
                extra={
                    "error": f"{type(e).__name__}: {e}",
                    "symbol": SYMBOL_WS,
                    "falhas_seguidas": falhas,
                    "next_retry_in_s": round(delay, 1),
                },
            )
            _ws_escalar_se_persistente("WebSocket", falhas, f"{type(e).__name__}: {e}")
            await asyncio.sleep(delay)

    # So se chega aqui por shutdown -- o loop nao tem mais saida por esgotamento.
    ws_logger.info("WebSocket finalizado (shutdown gracioso)")


async def process_message(message):
    """
    Processa mensagem WebSocket com state management para evitar duplicatas.
    """
    global cvd_btc, total_compras, total_vendas, preco_atual

    data = json.loads(message)
    # Stream @aggTrade usa "a" (aggregate trade id). O antigo "t" (trade id do
    # stream @trade) nao existe aqui — levantava KeyError silencioso a cada
    # mensagem, zerando o CVD. Bug exposto pelo watchdog /ready (ws_stale).
    trade_id = int(data["a"])
    price = float(data["p"])
    quantity = float(data["q"])
    is_buyer_maker = data["m"]

    # Evitar duplicatas: só processar se trade_id > last_trade_id
    if trade_id <= ws_state["last_trade_id"]:
        return  # Duplicata, ignorar

    ws_state["last_trade_id"] = trade_id

    with _lock:
        preco_atual = price
        _historico_ticks_btc.append(
            {"preco": price, "is_buyer_maker": is_buyer_maker, "quantidade": quantity}
        )
        if is_buyer_maker:
            cvd_btc -= quantity
            total_vendas += quantity
            direcao = "VENDA"
            cor = "\033[91m"
            seta = "v"
        else:
            cvd_btc += quantity
            total_compras += quantity
            direcao = "COMPRA"
            cor = "\033[92m"
            seta = "^"

    reset = "\033[0m"
    cinza = "\033[90m"

    # Salvar trades grandes
    if quantity >= 0.1:
        try:
            database.salvar_trade(
                price, quantity, direcao, WHALE_BTC_VOLUME, symbol="BTCUSDT", trade_id=trade_id
            )
        except Exception as e:
            logger.error("Erro salvando trade", extra={"error": str(e)})

    # Log trades significativos
    if quantity >= MIN_BTC_VOLUME:
        hora = datetime.now().strftime("%H:%M:%S")
        if quantity >= WHALE_BTC_VOLUME:
            print(f"\n\033[93m{'='*54}")
            print(f"  BALEIA! {direcao} {quantity:.3f} BTC ({formatar_valor(price*quantity)})")
            print(f"{'='*54}\033[0m")
        else:
            print(
                f"{cor}[{hora}] {seta} {direcao:6s}{reset}  "
                f"{cinza}${price:,.2f}  {quantity:.3f} BTC "
                f"({formatar_valor(price*quantity)}){reset}"
            )
        cvd_cor = "\033[92m" if cvd_btc >= 0 else "\033[91m"
        print(
            f"  {cinza}CVD: {cvd_cor}{cvd_btc:+.3f}{reset}  "
            f"{cinza}C:{total_compras:.2f} V:{total_vendas:.2f}{reset}"
        )


def obter_historico_ticks_btc():
    """Copia thread-safe do buffer de ticks brutos do BTCUSDT, no formato
    que score._score_cvd espera. Lista vazia antes do WS acumular dados."""
    with _lock:
        return list(_historico_ticks_btc)


# ── WebSocket @depth (Order Book Imbalance, P1-1) ────────────────


async def websocket_handler_depth():
    """
    Handler assincrono para o Partial Book Depth Stream (@depthN@100ms).
    Espelha websocket_handler() (mesmo retry exponencial com jitter) --
    conexao INDEPENDENTE do stream @aggTrade, isolamento de falha (uma cair
    nao derruba a outra).
    """
    url = f"{WS_BASE_URL}/ws/{SYMBOL_WS}@depth{OBI_DEPTH_LEVELS}@100ms"

    falhas = 0  # SEGUIDAS, zeradas a cada conexao bem-sucedida
    while not _shutdown_event.is_set():  # 24/7: nunca desiste, so espaca
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket_conn:
                ws_state_depth["connected"] = True
                ws_state_depth["last_message_time"] = time.time()
                ws_logger.info(
                    "WebSocket @depth conectado",
                    extra={"symbol": SYMBOL_WS, "falhas_previas": falhas},
                )
                _ws_marcar_recuperado("WebSocket @depth", falhas)
                falhas = 0

                async for message in websocket_conn:
                    try:
                        await process_depth_message(message)
                        ws_state_depth["last_message_time"] = time.time()
                    except Exception as e:
                        ws_logger.error(
                            "Erro processando mensagem @depth",
                            extra={
                                "error": str(e),
                                "symbol": SYMBOL_WS,
                                "latency_ms": ws_state_depth["latency_ms"],
                            },
                        )

        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.WebSocketException,
            asyncio.TimeoutError,
        ) as e:
            ws_state_depth["connected"] = False
            if _ws_encerrando("WebSocket @depth"):
                return
            latency = (time.time() - ws_state_depth["last_message_time"]) * 1000
            ws_state_depth["latency_ms"] = latency
            health.increment_metric("ws_reconexoes")

            falhas += 1
            delay = _ws_delay_backoff(falhas)
            logger.warning(
                "WebSocket @depth desconectado",
                extra={
                    "error": str(e),
                    "symbol": SYMBOL_WS,
                    "falhas_seguidas": falhas,
                    "latency_ms": latency,
                    "next_retry_in_s": round(delay, 1),
                },
            )
            _ws_escalar_se_persistente("WebSocket @depth", falhas, str(e))
            await asyncio.sleep(delay)

        except Exception as e:
            if _ws_encerrando("WebSocket @depth"):
                return
            ws_state_depth["connected"] = False
            health.increment_metric("ws_reconexoes")
            falhas += 1
            delay = _ws_delay_backoff(falhas)
            logger.error(
                "Erro crítico WebSocket @depth",
                extra={
                    "error": f"{type(e).__name__}: {e}",
                    "symbol": SYMBOL_WS,
                    "falhas_seguidas": falhas,
                    "next_retry_in_s": round(delay, 1),
                },
            )
            _ws_escalar_se_persistente("WebSocket @depth", falhas, f"{type(e).__name__}: {e}")
            await asyncio.sleep(delay)

    ws_logger.info("WebSocket @depth finalizado (shutdown gracioso)")


async def process_depth_message(message):
    """
    Processa uma mensagem do Partial Book Depth Stream e atualiza o
    Order Book Imbalance (OBI). Cada mensagem ja traz o top-N completo e
    independente (nao e um diff) -- sem estado de continuidade a manter.

    OBI = (volume_bid - volume_ask) / (volume_bid + volume_ask), no
    top-N configurado (OBI_DEPTH_LEVELS). +1 = livro 100% do lado comprador,
    -1 = 100% vendedor. Acumulado numa janela deslizante
    (OBI_JANELA_SUAVIZACAO mensagens) para suavizar contra spoofing (ordens
    fantasma colocadas/canceladas rapido nao devem mover o OBI sozinhas) --
    ver obter_obi_suavizado().
    """
    data = json.loads(message)
    bids = data.get("bids", [])
    asks = data.get("asks", [])

    volume_bid = sum(float(qty) for _, qty in bids)
    volume_ask = sum(float(qty) for _, qty in asks)
    total = volume_bid + volume_ask
    obi_bruto = (volume_bid - volume_ask) / total if total > 0 else 0.0
    obi_bruto = max(-1.0, min(1.0, obi_bruto))  # protecao contra ponto flutuante

    with _lock_obi:
        _obi_historico.append(obi_bruto)


OBI_STALE_SEGUNDOS = 120  # mesmo limiar de staleness usado pelo watchdog
# /ready do stream @aggTrade (health.py) -- consistencia entre os dois.


def obter_obi_suavizado():
    """Media movel do OBI sobre a janela de suavizacao (thread-safe).
    None se: (a) nenhuma mensagem @depth recebida ainda, OU (b) a conexao
    esta stale (>120s sem mensagem) -- sem este segundo guard, uma conexao
    morta com o buffer ainda cheio de dados ANTIGOS retornaria um OBI
    plausivel porem obsoleto, silenciosamente, em vez de degradar para o
    neutro (50) como os demais componentes 'sem dado' do score."""
    idade = time.time() - ws_state_depth["last_message_time"]
    if idade >= OBI_STALE_SEGUNDOS:
        return None
    with _lock_obi:
        if not _obi_historico:
            return None
        return sum(_obi_historico) / len(_obi_historico)


def iniciar_websocket_depth_async():
    """
    Inicia o loop assincrono do WebSocket @depth em uma thread separada.
    Mesmo padrao de iniciar_websocket_async, loop/thread INDEPENDENTES
    (guardados em _ws_loop_depth para o shutdown gracioso poder para-lo
    junto com o loop de _ws_loop).
    """

    def run_async():
        global _ws_loop_depth
        loop = asyncio.new_event_loop()
        _ws_loop_depth = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(websocket_handler_depth())
        except (asyncio.CancelledError, RuntimeError):
            pass  # loop parado durante shutdown — esperado
        finally:
            _drenar_tasks_pendentes(loop)  # antes do close, senao vira ruido no GC
            loop.close()

    thread = threading.Thread(target=run_async, daemon=True, name="websocket-depth-async")
    thread.start()


def iniciar_websocket_async():
    """
    Inicia o loop assíncrono do WebSocket em uma thread separada.

    C-7: cria o loop explicitamente e guarda a referencia em _ws_loop, para que
    o signal handler possa pedir seu encerramento (loop.stop) de fora da thread.
    """

    def run_async():
        global _ws_loop
        loop = asyncio.new_event_loop()
        _ws_loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(websocket_handler())
        except (asyncio.CancelledError, RuntimeError):
            pass  # loop parado durante shutdown — esperado
        finally:
            _drenar_tasks_pendentes(loop)  # antes do close, senao vira ruido no GC
            loop.close()

    thread = threading.Thread(target=run_async, daemon=True, name="websocket-async")
    thread.start()


# ── Encerramento gracioso (C-7) ───────────────────────────────


def _encerrar(signum, frame):
    """Signal handler: seta o Event (signal-safe) e pede ao loop asyncio para
    parar. Sem raise — levantar excecao dentro de handler e fragil. Modulo-level
    para ser testavel e reutilizavel."""
    _shutdown_event.set()
    if _ws_loop is not None:
        try:
            _ws_loop.call_soon_threadsafe(_ws_loop.stop)
        except Exception:
            pass
    if _ws_loop_depth is not None:
        try:
            _ws_loop_depth.call_soon_threadsafe(_ws_loop_depth.stop)
        except Exception:
            pass


def _registrar_signal_handlers():
    """Registra _encerrar em todos os sinais de parada disponiveis na
    plataforma. NSSM/Windows: SIGINT/SIGBREAK; systemd/Linux: SIGTERM."""
    for nome in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, nome, None)
        if sig is not None:
            try:
                signal.signal(sig, _encerrar)
            except (ValueError, OSError):
                pass  # sinal inexistente na plataforma ou fora da main thread


# ── Retreinamento Automático Semanal ──────────────────────────


def _retreinar_modelos(pares: list[str]):
    """Retreina XGBoost e MLP para todos os pares. Chamado automaticamente."""
    print(
        f"\n\033[94m[RETRAIN] Iniciando retreinamento semanal — "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}\033[0m"
    )
    try:
        from lstm_modelo import treinar as treinar_mlp
        from ml_filtro import treinar as treinar_xgb

        for par in pares:
            print(f"\033[94m[RETRAIN] XGBoost — {par}...\033[0m")
            try:
                treinar_xgb("1h", par)
                print(f"\033[92m[RETRAIN] XGBoost {par} OK\033[0m")
            except Exception as e:
                print(f"\033[91m[RETRAIN] XGBoost {par} ERRO: {e}\033[0m")

        print("\033[94m[RETRAIN] MLP Sequencial — BTCUSDT...\033[0m")
        try:
            treinar_mlp("1h")
            print("\033[92m[RETRAIN] MLP OK\033[0m")
        except Exception as e:
            print(f"\033[91m[RETRAIN] MLP ERRO: {e}\033[0m")

    except Exception as e:
        print(f"\033[91m[RETRAIN] Falha no retreinamento: {e}\033[0m")
    finally:
        print(f"\033[94m[RETRAIN] Concluído — {datetime.now().strftime('%H:%M:%S')}\033[0m")


def iniciar_retreinamento_automatico(pares: list[str]):
    """
    Thread que verifica todo domingo às 02h e retreina os modelos ML.
    Não bloqueia o loop principal.
    """

    def _loop_retrain():
        ultimo_retreinamento = None

        while True:
            agora = datetime.now()
            domingo_e_hora_certa = (
                agora.weekday() == _RETREINAMENTO_DIA
                and agora.hour == _RETREINAMENTO_HORA
                and agora.minute < 10  # janela de 10 min para não falhar se bot reiniciar
            )
            data_hoje = agora.date()
            ja_retreinou_hoje = ultimo_retreinamento == data_hoje

            if domingo_e_hora_certa and not ja_retreinou_hoje:
                _retreinar_modelos(pares)
                ultimo_retreinamento = data_hoje

            time.sleep(300)  # verifica a cada 5 minutos (baixo overhead)

    thread = threading.Thread(target=_loop_retrain, daemon=True, name="retrain-weekly")
    thread.start()
    print(
        f"\033[94m[RETRAIN] Retreinamento automático agendado — todo domingo às "
        f"{_RETREINAMENTO_HORA:02d}h\033[0m"
    )


_VIGIA_INTERVALO_S = 30  # criterio de I-9: <= 60s entre o evento e a mensagem


_mesa_pausado = threading.Event()


def _executar_comando_mesa(cmd: dict) -> tuple[str, str]:
    """MESA DE OPERAÇÕES (aba 7 do dashboard) — contrato v1: PAPEL SOMENTE.

    Fail-closed por construção: a lista de comandos é fechada, nada aqui
    altera DRY_RUN/ALLOW_REAL_TRADING nem envia ordem real, e fechar posição
    RECUSA executor não-simulado. Retorna (status, resultado)."""
    nome = cmd.get("comando", "")
    params = cmd.get("params") or {}

    if nome == "pausar_bot":
        _mesa_pausado.set()
        return "EXECUTADO", "avaliações suspensas (posições/proteções seguem monitoradas)"

    if nome == "retomar_bot":
        _mesa_pausado.clear()
        return "EXECUTADO", "avaliações retomadas"

    if nome == "fechar_posicao_paper":
        par = str(params.get("symbol", "")).upper()
        estado = _estado_pares.get(par)
        ex = estado.get("executor") if estado else None
        if ex is None:
            return "FALHOU", f"sem executor ativo para {par}"
        if not ex.simulacao:
            return "REJEITADO", "executor em modo REAL — a mesa só fecha posição de papel"
        if not ex.posicao:
            return "FALHOU", f"{par} sem posição aberta"
        preco = ex.get_preco()
        ex.fechar_posicao(preco, "mesa_operacoes")
        return "EXECUTADO", f"{par} fechado a mercado ({preco})"

    if nome == "retreinar_ml":
        threading.Thread(
            target=_retreinar_modelos, args=(PARES_ATIVOS,), daemon=True, name="mesa-retreino"
        ).start()
        return "EXECUTADO", "retreino disparado em thread (resultado em model_metricas)"

    if nome == "testar_telegram":
        try:
            import telegram_bot as tb

            ok, detalhe = tb._enviar(
                "🧾 MESA: teste de entrega solicitado pelo operador.", devolver_detalhe=True
            )
            return ("EXECUTADO", "entregue") if ok else ("FALHOU", f"não entregue: {detalhe}")
        except Exception as e:  # pragma: no cover - defensivo
            return "FALHOU", f"telegram indisponível: {e}"

    return "REJEITADO", f"comando desconhecido: {nome}"


def iniciar_mesa_comandos():
    """Consumidor da fila `comandos` (10s). Auditoria integral: cada comando
    processado vira bot_events/mesa_comando com origem e status."""

    def _loop():
        while not _shutdown_event.is_set():
            try:
                cmd = database.proximo_comando_pendente()
                if cmd:
                    status, resultado = _executar_comando_mesa(cmd)
                    database.marcar_comando(cmd["id"], status, resultado)
                    database.salvar_bot_event(
                        "mesa_comando",
                        f"{cmd['comando']} -> {status}: {resultado}",
                        service="worker",
                        symbol=(cmd.get("params") or {}).get("symbol"),
                        severity="INFO" if status == "EXECUTADO" else "WARNING",
                        data={"id": cmd["id"], "origem": cmd.get("origem")},
                    )
                    continue  # pode haver fila — processa sem esperar
            except Exception as e:
                print(f"\033[93m[MESA] erro no consumidor: {e}\033[0m")
            _shutdown_event.wait(10)

    threading.Thread(target=_loop, daemon=True, name="mesa-comandos").start()
    print("\033[94m[MESA] consumidor de comandos ativo (papel somente).\033[0m")


def iniciar_vigia_de_eventos():
    """Thread que da CONSUMO aos bot_events CRITICAL (I-9).

    A tabela tinha 14 escritores e ZERO leitor em todo o repositorio -- 4 linhas
    em 4 meses num destino que ninguem lia. thread_crash, divergencia
    local-vs-exchange, fill sem persistencia, reidratacao recusada e a nova trava
    permanente todos escreviam ali e morriam ali.
    """

    def _loop():
        ultimo_id = None
        # Na primeira volta, aprende o ID atual sem escalar o historico -- senao
        # o primeiro boot dispararia uma mensagem por evento CRITICAL antigo.
        try:
            recentes = database.listar_bot_events(limite=1, severidade="CRITICAL")
            ultimo_id = int(recentes[0]["id"]) if recentes else 0
        except Exception:
            ultimo_id = 0

        while not _shutdown_event.is_set():
            time.sleep(_VIGIA_INTERVALO_S)
            try:
                n, ultimo_id = telegram_bot.escalar_eventos_criticos(desde_id=ultimo_id)
                if n:
                    print(f"[VIGIA] {n} evento(s) CRITICAL escalado(s) por Telegram.")
            except Exception as e:
                print(f"[VIGIA] falha ao escalar eventos: {e}")

    threading.Thread(target=_loop, daemon=True, name="vigia-eventos").start()
    print(f"[VIGIA] Vigia de bot_events CRITICAL ativo (varredura a cada {_VIGIA_INTERVALO_S}s).")


def iniciar_relatorio_diario(pares: str | list[str]):
    """Thread que dispara o relatorio diario (Telegram) 1x por dia às
    _RELATORIO_HORA. Não bloqueia o loop principal. (P2-5: telegram_bot.
    relatorio_diario() existia pronta mas nunca era chamada em produção.)

    Agrega TODOS os pares — até 2026-08-18 recebia só pares[0], então um
    trade fechado em ETH/SOL não aparecia no "Trades: 0" das 18h, rotulado
    como se fosse global. Saldo via binance_conta.saldo() para distinguir
    conta zerada de leitura que falhou (o get_saldo_usdt devolve 0.0 para
    os dois casos — ambiguidade que a auditoria de 2026-07-26 matou em todo
    lugar, menos aqui)."""
    lista_pares = [pares] if isinstance(pares, str) else list(pares)

    def _loop_relatorio():
        ultimo_relatorio = None
        while True:
            agora = datetime.now()
            hora_certa = agora.hour == _RELATORIO_HORA and agora.minute < 10
            data_hoje = agora.date()
            ja_relatou_hoje = ultimo_relatorio == data_hoje

            if hora_certa and not ja_relatou_hoje:
                try:
                    import binance_conta

                    pnl_total, trades_total, ganhos_total = 0.0, 0, 0
                    for s in lista_pares:
                        d = logger.dados_relatorio_diario(s)
                        pnl_total += d["pnl_usdt"]
                        trades_total += d["trades_dia"]
                        ganhos_total += d.get("ganhos_dia", 0)
                    win_rate = ganhos_total / trades_total * 100 if trades_total > 0 else None
                    saldo_atual, saldo_erro = binance_conta.saldo("USDT")
                    telegram_bot.relatorio_diario(
                        pnl_total,
                        trades_total,
                        None if saldo_erro else saldo_atual,
                        win_rate,
                        saldo_erro=saldo_erro,
                    )
                except Exception as e:
                    print(f"\033[91m[RELATORIO] Falha ao enviar relatorio diario: {e}\033[0m")
                ultimo_relatorio = data_hoje

            time.sleep(300)  # verifica a cada 5 minutos (baixo overhead)

    thread = threading.Thread(target=_loop_relatorio, daemon=True, name="relatorio-diario")
    thread.start()
    print(
        f"\033[94m[RELATORIO] Relatorio diario agendado — todo dia às {_RELATORIO_HORA:02d}h\033[0m"
    )


# ── Loop de Estratégia por Par ────────────────────────────────


def _escalar_incoerencia(par, resultado):
    """Sinal invalido nao pode passar em silencio (E-7).

    `analisar()` ja rebaixou o sinal para AGUARDAR — mas rebaixar sem denunciar
    apenas troca uma ordem absurda por um nao-evento. Um sinal COMPRA com stop
    acima da entrada significa que algum insumo esta errado (par trocado, nivel
    de suporte de outro ativo, parametro corrompido), e isso e um defeito, nao
    uma condicao de mercado. Vai para bot_events CRITICAL, que desde I-9 tem
    leitor e escalonamento por Telegram em <= 30s.
    """
    detalhe = resultado.get("incoerencia", "?")
    original = resultado.get("sinal_original", "?")
    print(
        f"\033[91m[{par}][INCOERENCIA] Sinal {original} rebaixado para AGUARDAR: "
        f"{detalhe}\033[0m"
    )
    try:
        database.salvar_bot_event(
            "sinal_incoerente",
            f"{par}: sinal {original} descartado por incoerencia de precos — {detalhe}. "
            f"Entrada={resultado.get('preco')} stop={resultado.get('stop_loss')} "
            f"target={resultado.get('take_profit')} "
            f"suporte_forte={resultado.get('suporte_forte')}.",
            service="worker",
            symbol=par,
            severity="CRITICAL",
        )
    except Exception:
        pass


def _registrar_execucao(par, estrategia, preco_ref, preco_mercado, exec_par, t0, qty):
    """Telemetria de EXECUCAO de uma entrada — compartilhada pelas duas
    estrategias, para que os numeros sejam comparaveis entre elas.

    Tres precos, tres perguntas diferentes:

    - `preco_ref`: o preco em que a ESTRATEGIA decidiu. Na trend e o close do
      ultimo candle FECHADO (exatamente o que o backtest assume como entrada).
      Na otimizada e `resultado["preco"]` = `f1h[-1]`, ou seja a vela em
      FORMACAO vinda do cache de klines (TTL 30s) — pode estar velha.
    - `preco_mercado`: preco fresco lido no instante de mandar a ordem. Contra
      `preco_ref` isso mede o custo de a decisao ter sido tomada sobre dado
      velho — e ja rende numero real em paper trading, sem esperar modo real.
    - `preco_fill`: preco em que a posicao abriu de fato.

    Os gauges sao genericos (`exec_*`) porque as duas estrategias nunca rodam
    juntas: `exec_estrategia_trend` (1/0) diz qual delas produziu a amostra.
    """
    preco_fill = (exec_par.posicao or {}).get("entrada", preco_mercado)
    lat_ms = (time.time() - t0) * 1000
    d_mercado = (preco_mercado - preco_ref) / preco_ref * 100 if preco_ref else 0.0
    d_fill = (preco_fill - preco_ref) / preco_ref * 100 if preco_ref else 0.0

    print(
        f"\033[96m[{par}][EXEC-TELEMETRIA] ref ${preco_ref:,.2f} -> mercado "
        f"${preco_mercado:,.2f} ({d_mercado:+.3f}%) -> fill ${preco_fill:,.2f} "
        f"({d_fill:+.3f}%) em {lat_ms:.0f}ms\033[0m"
    )
    try:
        health.set_gauge("exec_desvio_ref_mercado_pct", d_mercado)
        health.set_gauge("exec_desvio_ref_fill_pct", d_fill)
        health.set_gauge("exec_latencia_sinal_fill_ms", lat_ms)
        health.set_gauge("exec_estrategia_trend", 1.0 if estrategia == "trend" else 0.0)
    except Exception:
        pass
    try:
        database.salvar_bot_event(
            "execucao_entrada",
            f"{par} [{estrategia}] ref={preco_ref:.2f} mercado={preco_mercado:.2f} "
            f"fill={preco_fill:.2f} desvio_mercado={d_mercado:+.4f}% "
            f"desvio_fill={d_fill:+.4f}% latencia={lat_ms:.0f}ms qty={qty}",
            service="worker",
            symbol=par,
            severity="INFO",
        )
    except Exception:
        pass


def _bucket_candle(intervalo: str) -> int:
    """Identidade do candle atualmente em formacao (buckets da Binance sao
    alinhados ao epoch UTC: 1d = meia-noite UTC, 4h/1h idem)."""
    return int(time.time()) // _SEGUNDOS_POR_INTERVALO.get(intervalo, 86400)


def _ciclo_trend(par, exec_par):
    """Um ciclo do sistema Donchian ao vivo (dry run de validacao de execucao).

    Decide no maximo uma vez por candle FECHADO, com os mesmos niveis do
    backtest. A telemetria (preco de referencia do sinal vs preco de fill,
    latencia) e o produto real deste experimento: e exatamente o que o
    backtest idealiza e nao consegue medir.
    """
    from estrategias.trend_live import sinal_trend

    bucket = _bucket_candle(MODO_TREND_INTERVALO)
    if _trend_ultimo_bucket.get(par) == bucket:
        return  # este candle ja foi avaliado
    reset = "\033[0m"
    t0 = time.time()  # inicio do ciclo: base da latencia sinal->fill

    tem_posicao = bool(exec_par.posicao)
    r = sinal_trend(par, tem_posicao, intervalo=MODO_TREND_INTERVALO)
    if r["preco_ref"] is None:
        return  # dados indisponiveis: nao marca o bucket, tenta de novo
    _trend_ultimo_bucket[par] = bucket

    print(
        f"\033[96m[{par}][TREND] {r['sinal']} — {r['motivo']} "
        f"(candle fechado @ ${r['preco_ref']:,.2f}){reset}"
    )

    if r["sinal"] == "COMPRA":
        _trend_abrir(par, exec_par, r, t0)
    elif r["sinal"] == "FECHAR":
        exec_par.fechar_posicao(exec_par.get_preco(), "Saida Donchian (canal M)")
    elif tem_posicao and r["canal_baixo"]:
        # Em posicao: o canal Donchian-M so sobe -> e o trailing do sistema.
        atual = (exec_par.posicao or {}).get("stop_atual", 0)
        if r["canal_baixo"] > atual:
            exec_par._aplicar_novo_stop(r["canal_baixo"], "Trailing Donchian-M: ${v:,.2f}")


def _trend_abrir(par, exec_par, r, t0=None):
    """Entrada do sistema trend: sizing por risco ate o canal Donchian-M,
    limitado pelo tamanho que a gestao de risco autoriza.

    t0 e o instante em que o CICLO comecou (antes de buscar klines), nao o
    instante de mandar a ordem: a latencia que interessa e sinal->fill inteira,
    incluindo fetch/indicadores/risco, nao so o round-trip da ordem.
    """
    if t0 is None:
        t0 = time.time()
    from backtesting.trend_following import RISCO_FRAC

    # Ultima linha de defesa, no proprio caminho do dinheiro: mesmo que alguem
    # contorne a trava de boot (import direto, chamada em teste, edicao futura
    # do argparse), uma estrategia reprovada nunca envia ordem real.
    if not exec_par.simulacao:
        print(
            f"\033[91m[{par}][TREND] RECUSADO: estrategia reprovada, so opera em simulacao.\033[0m"
        )
        return

    preco = exec_par.get_preco()
    stop = r["canal_baixo"]
    if preco <= 0 or not stop or stop >= preco:
        print(
            f"\033[91m[{par}][TREND] Entrada abortada: stop {stop} invalido vs preco {preco}\033[0m"
        )
        return

    saldo = gestao_risco.get_saldo_usdt()
    saldo = saldo if saldo > 0 else 100
    # E-8: o stop do trend e o canal Donchian-M, nao 1,5% — passa-lo faz
    # `risco_usdt` refletir o risco real deste sistema, que e o dado que este
    # experimento existe para produzir.
    validacao = gestao_risco.validar_trade("COMPRA", preco, saldo, stop=stop, symbol=par)
    if not validacao["pode"]:
        print(f"\033[91m[{par}][TREND][RISCO] Bloqueado: {validacao['motivo']}\033[0m")
        return

    # Sizing do backtest: arrisca RISCO_FRAC do capital ate o stop.
    risco_pct = (preco - stop) / preco
    tamanho_trend = (RISCO_FRAC * saldo / risco_pct) / preco
    # O menor dos dois: nunca excede o que a gestao de risco autorizou.
    tamanho = round(min(tamanho_trend, validacao["tamanho_btc"]), 6)
    if tamanho <= 0:
        return

    # E-8: registra o sinal ANTES de abrir, para ter um id que ligue entrada e
    # fechamento. Sem isto o caminho trend chamava abrir_long sem sinal_id,
    # executor.py gravava None, e marcar_sinal_executado /
    # atualizar_sinal_fechamento eram no-ops silenciosos — e o trend e o caminho
    # que esta rodando no worker agora, ou seja o unico produzindo track record.
    #
    # `source="trend_live"` nao e rotulo decorativo: relatorio_gate.py filtra a
    # Etapa 2 por source, entao gravar aqui NAO contamina a conta que decide
    # capital real com uma estrategia que foi REPROVADA no hold-out.
    sinal_id = None
    try:
        sinal_id = database.salvar_sinal(
            "COMPRA",
            preco,
            f"Donchian N: rompimento @ ${r['preco_ref']:,.2f} | stop canal M ${stop:,.2f} "
            f"| risco ${validacao.get('risco_usdt', 0)}",
            symbol=par,
            source="trend_live",
        )
    except Exception as exc:
        print(f"\033[93m[{par}][TREND] Falha ao registrar sinal: {exc}\033[0m")

    if exec_par.abrir_long(preco, tamanho, stop, float("inf"), sinal_id=sinal_id):
        # preco (fresco, lido acima) e o preco de mercado; r["preco_ref"] e o
        # close do candle fechado em que a estrategia decidiu.
        _registrar_execucao(par, "trend", r["preco_ref"], preco, exec_par, t0, tamanho)


def loop_par(par, intervalo_min, simulacao):
    """Loop independente para cada par operado."""
    reset = "\033[0m"

    rotulo = "TREND (Donchian, dry run)" if MODO_TREND else "Estrategia"
    print(f"\033[94m[BOT] {par} — {rotulo} iniciada (intervalo: {intervalo_min} min).\033[0m")
    executor = Executor(simulacao=simulacao, symbol=par, modo_trend=MODO_TREND)
    _estado_pares[par] = {"executor": executor, "scale_in": None}

    # P0-3: crash recovery — se havia posicao aberta persistida, readota e
    # religa o monitor (senao a posicao ficaria orfa na exchange sem gestao).
    # RECONCILIAR_BOOT_EXCHANGE=true (auditoria 2026-07-22) cruza tambem com o
    # estado real da Binance (saldo/ordens abertas/myTrades) antes de decidir
    # — default False preserva o comportamento legado abaixo (confia no DB).
    try:
        if RECONCILIAR_BOOT_EXCHANGE:
            resultado = executor.reconciliar_boot()
            print(
                f"\033[93m[RECOVERY] {par} — reconciliacao de boot: "
                f"{resultado['acao']} ({resultado['detalhe']})\033[0m"
            )
        else:
            persistidas = database.carregar_posicoes_abertas()
            pos_salva = persistidas.get(par)
            if pos_salva:
                executor.reidratar_posicao(pos_salva)
                print(
                    f"\033[93m[RECOVERY] {par} — posicao aberta recuperada do banco "
                    f"(entrada ${pos_salva.get('entrada', 0):,.2f}, "
                    f"stop ${pos_salva.get('stop_atual', 0):,.2f}). Monitor religado.\033[0m"
                )
                database.salvar_bot_event(
                    "posicao_recuperada",
                    f"Posicao {par} recuperada apos restart (entrada {pos_salva.get('entrada')})",
                    service="worker",
                    symbol=par,
                    severity="WARNING",
                )
    except Exception as e:
        print(f"\033[91m[RECOVERY] {par} — falha ao recuperar posicao: {e}\033[0m")

    # Ensemble ML por par
    ensemble_disponivel = False
    try:
        import ensemble as ens_mod

        ensemble_disponivel = True
        print(f"\033[94m[BOT] {par} — Ensemble ML carregado.\033[0m")
    except Exception:
        pass

    while True:
        time.sleep(intervalo_min * 60)
        try:
            # I-8: a trava permanente e consultada ANTES de qualquer avaliacao.
            # Redundante com o gate 0 de validar_trade de proposito: assim o bot
            # travado nao gasta chamada de API, nao grava sinal e nao produz
            # linha de log que pareca operacao normal.
            travado, motivo_trava = gestao_risco.esta_travado()
            if travado:
                print(f"\033[91m[{par}][TRAVADO] {motivo_trava} — avaliacao suspensa.\033[0m")
                continue

            # MESA: pausa pedida pelo operador — suspende avaliações novas;
            # monitor/proteções das posições abertas seguem intocados.
            if _mesa_pausado.is_set():
                print(f"\033[93m[{par}][MESA] pausado pelo operador — avaliacao suspensa.\033[0m")
                continue

            if MODO_TREND:
                # Caminho totalmente separado: o sistema Donchian nao usa score,
                # ensemble, CVD/OBI nem scale-in. Misturar os dois caminhos
                # invalidaria a comparacao com o backtest.
                _ciclo_trend(par, _estado_pares[par]["executor"])
                continue

            t_inicio_ciclo = time.time()  # P2-5: latencia de decisao (gauge)
            # CVD/OBI: BTC usa WebSocket, ETH e outros usam None (opcional)
            cvd_snap = None
            historico_ticks_snap = None
            obi_snap = None
            if par == "BTCUSDT":
                with _lock:
                    cvd_snap = cvd_btc
                historico_ticks_snap = obter_historico_ticks_btc()
                obi_snap = obter_obi_suavizado()

            # Ensemble ML
            #
            # E-7: `ens_mod.prever(symbol=par)`, direto. O codigo anterior era
            #
            #     ens_mod.prever(symbol=par) if hasattr(ens_mod, "symbol")
            #     else ens_mod.prever()
            #
            # e `ens_mod` e o MODULO ensemble, que nunca teve atributo `symbol`:
            # a condicao era permanentemente False, o ramo com symbol NUNCA
            # executava, e ETH/SOL recebiam a previsao do modelo de BTC. O
            # padrao e pior que a ausencia do symbol, porque em code review o
            # bug parece resolvido.
            ensemble_result = None
            ml_prob = None
            if ensemble_disponivel:
                try:
                    ensemble_result = ens_mod.prever(par)
                    ml_prob = ensemble_result.get("prob_ensemble")
                except Exception as exc_ens:
                    print(f"\033[93m[{par}] Ensemble indisponivel: {exc_ens}\033[0m")

            # E-7: UMA avaliacao por ciclo. Antes vinham analisar_otimizada(...)
            # e imprimir_otimizada(...), e a segunda re-executava a estrategia
            # inteira: dois calculos, duas linhas em `sinais` para o mesmo
            # instante, e um bloco impresso que podia divergir da ordem enviada
            # (klines fora do TTL de 30s, fear&greed e ensemble recalculados).
            resultado = analisar_otimizada(
                symbol=par,
                cvd_atual=cvd_snap,
                ml_prob=ml_prob,
                ensemble_result=ensemble_result,
                historico_ticks=historico_ticks_snap,
                obi=obi_snap,
            )
            imprimir_otimizada(resultado=resultado)

            # E-7: a ESCRITA em `sinais` e do worker, nao da estrategia. Fica
            # aqui, depois da invariante de coerencia ter podido rebaixar o
            # sinal para AGUARDAR — sinal invalido nao vira linha no banco que
            # a Etapa 2 do gate le.
            if resultado.get("incoerencia"):
                _escalar_incoerencia(par, resultado)
            else:
                try:
                    registrar_sinal_otimizada(resultado)
                except Exception as exc_reg:
                    print(f"\033[93m[{par}] Falha ao registrar sinal: {exc_reg}\033[0m")

            try:
                logger.registrar_avaliacao(resultado, symbol=par)
            except Exception:
                pass

            # P2-5: gauges de observabilidade -- leves (sem rede extra: regime/
            # ml_prob ja calculados acima; PnL/drawdown via risco.status_leve(),
            # sem chamada de saldo/volatilidade).
            try:
                health.set_regime_atual(resultado.get("regime"))
                if ml_prob is not None:
                    health.set_gauge("ml_prob", ml_prob)
                leve = gestao_risco.status_leve()
                health.set_gauge("pnl_dia", leve["pnl_dia"])
                health.set_gauge("drawdown_dia_pct", leve["drawdown_dia_%"])
                health.set_gauge("drawdown_total_pct", leve["drawdown_total_%"])
                health.set_gauge("latencia_decisao_ms", (time.time() - t_inicio_ciclo) * 1000)
            except Exception:
                pass

            sinal = resultado["sinal"]
            estado = _estado_pares[par]
            exec_par = estado["executor"]

            # Salvar snapshot (apenas BTC por ora, para não sobrecarregar a tabela)
            if par == "BTCUSDT":
                database.salvar_snapshot(
                    {
                        "symbol": par,
                        "preco": resultado["preco"],
                        "variacao_24h_%": 0,
                        "volume_24h_btc": 0,
                        "funding_rate_%": resultado["funding_%"],
                        "open_interest_btc": 0,
                        "ema20_1h": resultado["ema20_1h"],
                        "ema50_1h": resultado["ema50_1h"],
                        "rsi_1h": resultado["rsi"],
                        "tendencia": resultado["tend_4h"],
                        "pressao_dominante": "COMPRA" if (cvd_snap or 0) > 0 else "VENDA",
                        "liquidez_compra_usdt": 0,
                        "liquidez_venda_usdt": 0,
                    },
                    symbol=par,
                )

            # Executar sinal (apenas LONG: o Executor ainda nao suporta short —
            # ver C-3 em RELATORIO_MAPEAMENTO_MELHORIAS.md)
            if sinal == "COMPRA" and not exec_par.posicao:
                preco = resultado["preco"]
                stop = resultado["stop_loss"]
                target = resultado["take_profit"]
                saldo = gestao_risco.get_saldo_usdt()

                # P0-3: vol targeting — atr_relativo = atr_atual/atr_media, mesmo dado
                # que a estrategia ja usou para aprovar o sinal (sem chamada de rede extra).
                atr_relativo = (
                    resultado["atr"] / resultado["atr_media"]
                    if resultado.get("atr_media")
                    else None
                )
                validacao = gestao_risco.validar_trade(
                    sinal,
                    preco,
                    saldo if saldo > 0 else 100,
                    atr_relativo=atr_relativo,
                    regime=resultado.get("regime"),
                    # E-8: o stop REAL do par. Sem ele, validar_trade dimensionava
                    # e reportava risco com 1,5% fixo para os tres pares, quando
                    # ETH usa 2,0% e SOL 3,0% (config/params_pares.py).
                    stop=stop,
                    symbol=par,
                )
                if validacao["pode"]:
                    tamanho_base = validacao["tamanho_btc"]
                    fator = resultado.get("tamanho_fator", 1.0)
                    tamanho = round(tamanho_base * fator, 6)
                    score_val = resultado.get("score", 0)

                    # ── E-8: ScaleIn REMOVIDO do caminho vivo ─────────────
                    #
                    # As parcelas 2 e 3 nunca foram alcancaveis. Este bloco esta
                    # dentro de `if sinal == "COMPRA" and not exec_par.posicao`:
                    # a parcela 1 ABRE posicao, entao no ciclo seguinte a guarda
                    # `not exec_par.posicao` e falsa e o codigo das parcelas 2/3
                    # nao e executado. Nas 5.255 avaliacoes gravadas, zero
                    # parcelas 2 ou 3.
                    #
                    # Pior que inutil: nada resetava estado["scale_in"] no
                    # fechamento. Como `scale_in.completo` so vira True apos a
                    # parcela 3 — que nunca vinha — o proximo trade caia no ramo
                    # `elif scale_in.parcela_atual == 1` e dimensionava a entrada
                    # sobre o tamanho_total do trade ANTERIOR (suporte.py:296,305),
                    # nao sobre o que validar_trade acabara de autorizar. O
                    # resultado: 40% do tamanho na 1a entrada de cada ciclo de vida
                    # do objeto e um multiplo arbitrario do tamanho velho depois.
                    #
                    # Agora abre com o tamanho INTEGRAL autorizado. A classe
                    # ScaleIn continua existindo em suporte.py, testada, para o dia
                    # em que houver uma maquina de estados que de fato chame as
                    # parcelas 2 e 3 (exigiria avaliar com posicao aberta, o que o
                    # gate acima proibe hoje).
                    parcela = tamanho
                    estado["scale_in"] = None
                    print(
                        f"\n\033[93m[{par}][ENTRADA] {parcela:.6f} @ ${preco:,.2f} "
                        f"(Score:{score_val} | risco ${validacao.get('risco_usdt', 0)} | "
                        f"limitado por {validacao.get('limitado_por', '?')}){reset}"
                    )

                    # Telegram
                    try:
                        from telegram_bot import alerta_sinal

                        alerta_sinal(
                            sinal,
                            preco,
                            stop,
                            target,
                            resultado["filtros_ok"],
                            resultado["filtros_total"],
                            ml_prob,
                            par=par,
                        )
                    except Exception:
                        pass

                    # Telemetria de execucao: le um preco FRESCO imediatamente
                    # antes de mandar a ordem. `preco` (= resultado["preco"] =
                    # f1h[-1]) veio do cache de klines (TTL 30s), logo a
                    # diferenca entre os dois mede o quanto a decisao foi tomada
                    # sobre dado velho. Uma chamada de rede a mais por ENTRADA
                    # (raro: teto de 1 posicao aberta), nao por ciclo.
                    preco_mercado = exec_par.get_preco() or preco
                    # E-8: a ordem vai com o preco FRESCO, nao com o de kline em
                    # cache. `preco_mercado` ja era lido nesta linha e usado APENAS
                    # para telemetria, enquanto a ordem seguia com `preco`
                    # (= f1h[-1], TTL de 30s). Em paper isso falsificava o preco de
                    # entrada gravado, que e a base de todo pnl_usdt da Etapa 2; com
                    # dinheiro real, e a diferenca entre o limite calculado e o
                    # mercado onde a ordem de fato descansa.
                    #
                    # stop/target continuam derivados de `preco` (a decisao foi
                    # tomada sobre ele) — recalcula-los sobre o preco fresco mudaria
                    # os niveis que a estrategia aprovou. A invariante de E-7
                    # dentro de abrir_long valida a coerencia do trio de qualquer
                    # forma, agora contra o preco que sera efetivamente usado.
                    #
                    # Consequencia a tratar ANTES de chamar abrir_long: se o
                    # mercado andou mais que stop_pct (1,5%-3%) durante o TTL de
                    # 30s, `preco_mercado` pode ja estar fora do trio. Isso e
                    # SINAL VELHO, nao insumo quebrado — a invariante do executor
                    # o registraria como CRITICAL, gerando alarme falso justo no
                    # canal que I-9 acabou de fazer funcionar. Distinguir os dois
                    # casos e o ponto: aqui vira um descarte informativo.
                    desatualizado = incoerencia_de_precos(sinal, preco_mercado, stop, target)
                    if desatualizado:
                        deriva = (preco_mercado - preco) / preco * 100 if preco else 0.0
                        print(
                            f"\033[93m[{par}][SINAL VELHO] Entrada descartada: o mercado "
                            f"andou {deriva:+.2f}% (kline ${preco:,.2f} -> agora "
                            f"${preco_mercado:,.2f}) e saiu do trio aprovado "
                            f"(stop ${stop:,.2f} / target ${target:,.2f}). {desatualizado}\033[0m"
                        )
                        continue

                    if exec_par.abrir_long(
                        preco_mercado,
                        parcela,
                        stop,
                        target,
                        atr_relativo=atr_relativo,
                        sinal_id=resultado.get("sinal_id"),
                    ):
                        _registrar_execucao(
                            par,
                            "otimizada",
                            preco,
                            preco_mercado,
                            exec_par,
                            t_inicio_ciclo,
                            parcela,
                        )
                else:
                    print(f"\033[91m[{par}][RISCO] Trade bloqueado: {validacao['motivo']}\033[0m")

            elif sinal == "VENDA" and not exec_par.posicao:
                # Short ainda nao implementado no Executor (so existe abrir_long).
                # Antes este sinal consumia validacao + scale-in + alerta Telegram
                # e era descartado em silencio. Agora e ignorado explicitamente,
                # sem efeitos colaterais.
                print(
                    f"\033[90m[{par}][INFO] Sinal VENDA (short) ignorado: "
                    f"executor opera apenas LONG no momento.\033[0m"
                )

            # CVD snapshot periódico (BTC apenas)
            if par == "BTCUSDT":
                database.salvar_cvd(cvd_btc, total_compras, total_vendas, symbol=par)

        except Exception as e:
            print(f"\033[91m[ERRO {par}] {e}\033[0m")


# ── Ponto de entrada ───────────────────────────────────────────


def _decidir_modo(real: bool, dry_run: bool) -> bool:
    """Decide simulacao vs real. Devolve True para SIMULACAO. (I-8)

    Funcao separada e pura porque e a decisao mais consequente do boot e precisa
    ser testavel sem subir o bot.

    Antes desta frente, `DRY_RUN` era um gate FANTASMA: definido em
    runtime_settings, documentado em 6 arquivos como cinto de seguranca, e lido
    em lugar nenhum do caminho de execucao — os unicos leitores eram duas linhas
    do dashboard, para desenhar um rotulo. O operador podia manter DRY_RUN=true
    e ter ordens reais saindo.

    `--real` junto com `DRY_RUN=true` e contradicao explicita e ABORTA, em vez de
    ser resolvida em silencio para um dos lados: resolver para real trairia o
    operador que confia na variavel; resolver para paper esconderia que a
    configuracao esta incoerente.
    """
    if real and dry_run:
        print("[BOOT ERROR] --real com DRY_RUN=true e contradicao explicita.")
        print("  DRY_RUN=true significa 'nao envie ordem real'. --real significa o oposto.")
        print("  Resolva no .env (DRY_RUN=false) ou remova --real. Abortando por seguranca.")
        raise SystemExit(1)
    return (not real) or dry_run


def _validar_postura_da_chave(simulacao: bool) -> None:
    """I-8: verifica no boot o que a CHAVE pode fazer, nao o que a CONTA pode.

    `canTrade` de /api/v3/account e da CONTA e ficou True nesta conta enquanto a
    chave era read-only — essa confusao ja custou tempo numa investigacao. A
    fonte correta e /sapi/v1/account/apiRestrictions, exposta em
    binance_conta.restricoes_chave(), que existia testada e sem nenhum chamador
    de producao.

    Aborta o boot quando:
      - a chave pode SACAR (risco desproporcional para um bot de trading);
      - estamos em modo real e a chave NAO pode negociar spot (falharia na
        primeira ordem com -2015, depois de o sinal ja ter sido consumido).
    Em simulacao a ausencia de permissao de trade e apenas informada.
    """
    try:
        import binance_conta
    except Exception as e:  # pragma: no cover - defensivo
        print(f"[AVISO] Nao foi possivel verificar a postura da chave: {e}")
        return

    if not binance_conta.chave_configurada():
        if not simulacao:
            print("[BOOT ERROR] Modo real sem chave utilizavel (ausente ou placeholder).")
            raise SystemExit(1)
        return

    r = binance_conta.restricoes_chave()
    if not r.get("ok"):
        # Nao aborta em paper por indisponibilidade da API — mas em modo real,
        # operar sem saber a postura da chave e pior do que nao operar.
        msg = f"nao foi possivel ler as restricoes da chave: {r.get('erro')}"
        if not simulacao:
            print(f"[BOOT ERROR] Modo real exige verificacao da chave — {msg}")
            raise SystemExit(1)
        print(f"[AVISO] {msg}")
        return

    if r.get("pode_sacar"):
        print("[BOOT ERROR] A chave de API tem permissao de SAQUE habilitada.")
        print("  Um bot de trading nunca precisa sacar. Desabilite 'Enable Withdrawals'")
        print("  no painel da Binance antes de rodar. Abortando.")
        raise SystemExit(1)

    if not simulacao and not r.get("pode_negociar_spot"):
        print("[BOOT ERROR] Modo real pedido, mas a chave NAO pode negociar spot")
        print("  (enableSpotAndMarginTrading=false). Toda ordem voltaria -2015.")
        raise SystemExit(1)

    if not r.get("restrito_por_ip"):
        print("[AVISO] A chave nao tem restricao de IP (ipRestrict=false).")

    print(
        f"[SEGURANCA] Chave verificada: spot={r.get('pode_negociar_spot')} "
        f"saque={r.get('pode_sacar')} futures={r.get('pode_futures')} "
        f"ip_restrito={r.get('restrito_por_ip')}"
    )


def _validar_trend_so_em_simulacao(modo_trend: bool, simulacao: bool) -> None:
    """TRAVA DE SEGURANCA — nao e convencao, e SystemExit.

    O sistema Donchian REPROVOU no hold-out (research/METODOLOGIA_TREND.md:
    +5.70% a.a. contra um piso pre-registrado de 8%). Ele existe no caminho ao
    vivo apenas como experimento de validacao de EXECUCAO em paper trading.
    Rodar com capital real exigiria hipotese nova, dados novos, hold-out novo e
    o GATE_GO_LIVE.md — cuja Etapa 1 tambem esta reprovada. Por isso a
    combinacao --modo-trend + --real e recusada no boot, antes de qualquer
    ordem, em vez de depender de disciplina operacional.
    """
    if modo_trend and not simulacao:
        print("[BOOT ERROR] --modo-trend nao pode rodar com ordens reais.")
        print("  A estrategia trend-following esta REPROVADA no hold-out")
        print("  (research/METODOLOGIA_TREND.md) e no GATE_GO_LIVE.md Etapa 1.")
        print("  Ela so existe ao vivo como validacao de execucao em paper trading.")
        print("  Use: python main.py --modo-trend --simulacao")
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description="BotBinance v2")
    parser.add_argument("--intervalo", type=int, default=15)
    parser.add_argument(
        "--simulacao",
        action="store_true",
        default=True,
        help="Paper trading (padrao: ativado por seguranca)",
    )
    parser.add_argument(
        "--real", action="store_true", help="Ativar ordens reais (desativa simulacao)"
    )
    parser.add_argument("--relatorio", action="store_true")
    parser.add_argument("--estrategia", action="store_true")
    parser.add_argument("--backtest", type=str, metavar="INTERVALO")
    parser.add_argument("--treinar-ml", action="store_true")
    parser.add_argument(
        "--modo-trend",
        action="store_true",
        help=(
            "DRY RUN de validacao de execucao com o sistema Donchian 20/10 "
            "(estrategia REPROVADA no hold-out — recusa iniciar com --real)."
        ),
    )
    parser.add_argument(
        "--par",
        type=str,
        default=None,
        help="Operar apenas um par (ex: BTCUSDT). Padrao: todos os pares ativos.",
    )
    args = parser.parse_args()

    global MODO_TREND
    MODO_TREND = args.modo_trend

    from config.runtime_settings import DRY_RUN

    simulacao = _decidir_modo(real=args.real, dry_run=DRY_RUN)  # I-8
    # Trava ANTES do downgrade por ALLOW_REAL_TRADING: recusa a INTENCAO de
    # rodar trend com dinheiro real, nao so o efeito. Senao, quem hoje e
    # rebaixado para paper por falta da env passaria a operar trend com capital
    # real no dia em que ligasse a env, sem nenhum novo aviso.
    _validar_trend_so_em_simulacao(MODO_TREND, simulacao)
    # E-9: recusa a INTENCAO de operar real com parametros sem procedencia —
    # pelo mesmo motivo da trava de trend acima, e ANTES do downgrade por
    # ALLOW_REAL_TRADING. Quem hoje e rebaixado a paper por falta da env
    # passaria a operar com parametros inauditaveis no dia em que a ligasse,
    # sem nenhum aviso novo. Paper e backtest nao passam por aqui: e com
    # parametros nao auditados que se MEDE.
    if args.real and not ALLOW_REAL_TRADING:
        simulacao = True
        print(
            "[SEGURANCA] --real ignorado: defina ALLOW_REAL_TRADING=true para liberar ordens reais."
        )

    # Validação de boot: fail-fast em modo real sem credenciais
    if not simulacao:
        from config.runtime_settings import API_KEY, API_SECRET, APP_ENV

        erros_boot = []
        if not API_KEY:
            erros_boot.append("BINANCE_API_KEY nao definida")
        if not API_SECRET:
            erros_boot.append("BINANCE_API_SECRET nao definida")
        if APP_ENV != "production":
            erros_boot.append(
                f"ENV/APP_ENV deve ser 'production' em modo real (atual: '{APP_ENV}')"
            )
        if erros_boot:
            print("[BOOT ERROR] Modo real requer configuracao correta:")
            for e in erros_boot:
                print(f"  - {e}")
            print(
                "Abortando. Use --simulacao para paper trading ou configure as variaveis de "
                "ambiente."
            )
            raise SystemExit(1)

    # I-8: postura da CHAVE verificada no boot. binance_conta.restricoes_chave()
    # existia, estava testada, e o unico chamador era o __main__ do proprio
    # arquivo -- ou seja, o bot nunca perguntou "esta chave pode sacar?" antes de
    # operar. E `canTrade` de /api/v3/account NAO responde isso: e da CONTA, e
    # ficou True nesta conta enquanto a chave era read-only.
    _validar_postura_da_chave(simulacao)

    # Definir pares a operar
    if args.par:
        pares = [args.par.upper()]
    else:
        pares = PARES_ATIVOS

    # E-9: capital real exige parametros com procedencia auditavel. A checagem
    # olha `args.real` (a INTENCAO), nao `simulacao` — pelo mesmo motivo da
    # trava de trend acima: quem hoje e rebaixado a paper por falta de
    # ALLOW_REAL_TRADING passaria a operar com parametros inauditaveis no dia
    # em que ligasse a env, sem nenhum aviso novo. Paper e backtest NAO passam
    # por aqui: e justamente com parametros nao auditados que se mede.
    if args.real:
        from config import params_pares as _params_pares

        _params_pares.exigir_params_auditados(pares)

    # Modos de uso único
    if args.relatorio:
        relatorio_completo()
        return

    if args.estrategia:
        database.inicializar()
        for par in pares:
            imprimir_otimizada(symbol=par)
        return

    if args.backtest:
        from backtesting.motor import MedicaoComMocks, imprimir_relatorio, rodar_backtest

        database.inicializar()
        try:
            r = rodar_backtest(args.backtest, 1000.0)
        except MedicaoComMocks as exc:
            # I-12d: o harness pontua com 8 entradas fabricadas — o numero
            # descreve outra estrategia. Sai != 0 em vez de imprimir veredito.
            print(f"\n[MEDICAO BLOQUEADA] {exc}")
            raise SystemExit(2) from exc
        if r:
            imprimir_relatorio(r)
        return

    if args.treinar_ml:
        from ml_filtro import treinar

        for par in pares:
            treinar("1h", par)
        return

    # === Modo completo =========================================
    database.inicializar()
    if ENABLE_HEALTH_SERVER:
        import health as _health

        _health.registrar_ws_state(ws_state)  # P1: /ready enxerga WS zumbi
        _health.registrar_ws_state_depth(ws_state_depth)  # P1-1: idem p/ @depth (OBI)
        start_health_server(role="worker")
        print("[HEALTH] Servidor /health ativo.")
    if args.real and not ALLOW_REAL_TRADING:
        database.salvar_bot_event(
            "real_trading_blocked",
            "--real foi solicitado, mas ALLOW_REAL_TRADING nao esta habilitado.",
            service="worker",
            severity="WARNING",
        )

    print("\n" + "=" * 56)
    print("  BOTBINANCE v2 — INICIANDO")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"  Modo: {'SIMULACAO (Paper Trading)' if simulacao else 'REAL'}")
    # I-8: publica o modo EFETIVO num canal que outro processo consegue ler. O
    # rotulo do dashboard era calculado a partir do ambiente do PROPRIO processo
    # do dashboard (DRY_RUN/ALLOW_REAL_TRADING), estruturalmente incapaz de
    # refletir o args.real do worker — podia exibir SIMULACAO com ordens reais
    # saindo, e REAL com o worker em paper. Mente nas duas direcoes.
    try:
        health.set_gauge("modo_simulacao", 1.0 if simulacao else 0.0)
    except Exception:
        pass
    try:
        database.salvar_bot_event(
            "modo_efetivo",
            f"worker iniciado em modo {'SIMULACAO' if simulacao else 'REAL'} "
            f"(--real={args.real}, DRY_RUN={DRY_RUN}, ALLOW_REAL_TRADING={ALLOW_REAL_TRADING})",
            service="worker",
            severity="WARNING" if not simulacao else "INFO",
        )
    except Exception:
        pass
    print(f"  Pares: {', '.join(pares)}")
    print("=" * 56)
    print("  Modulos ativos:")
    print("  [OK] WebSocket BTC/USDT Spot (CVD + OBI em tempo real)")
    if MODO_TREND:
        print("  [!!] MODO TREND — Donchian 20/10 diario (substitui a estrategia)")
        print("       EXPERIMENTO DE VALIDACAO DE EXECUCAO, NAO ESTRATEGIA APROVADA.")
        print("       Reprovada no hold-out (research/METODOLOGIA_TREND.md).")
        print("       Score/ensemble/CVD/OBI/scale-in NAO participam da decisao.")
    else:
        print("  [OK] Estrategia Otimizada MTF+ATR+Volume+VWAP+ML (por par)")
    print("  [OK] Gestao de Risco (Kelly + Circuit Breaker)")
    print(f"  [OK] Executor {'Simulado' if simulacao else 'Real'} + Trailing Stop (por par)")
    print(f"  [OK] Banco de dados {database.backend_info()['backend'].upper()}")
    print("  [OK] Retreinamento automatico (domingo 02h)")
    print(f"\n  Avaliacao de sinal: a cada {args.intervalo} minutos")
    print("  Ctrl+C para encerrar")
    print("=" * 56 + "\n")

    # Achado da auditoria 2026-07-17: relatorio_completo() bate no mercado de
    # FUTUROS (fapi.binance.com, analise_mercado.py) -- um mercado que o bot
    # nem opera (opera SPOT). Sem try/except, uma falha ali (rate limit,
    # geo-block, instabilidade da API de Futures) derrubava o boot inteiro do
    # processo (main() nao tem nenhum try/except ao redor, chamado nu em
    # `if __name__ == "__main__": main()`) -- e como o NSSM reinicia o
    # servico automaticamente, uma falha persistente virava crash-loop,
    # mesmo com o Spot 100% saudavel. Mesmo padrao de protecao ja usado por
    # reg.imprimir()/fg.imprimir() logo abaixo.
    try:
        relatorio_completo()
    except Exception as e:
        print(f"[AVISO] Relatorio de mercado (Futures, informativo): {e}")

    # Regime e Fear & Greed na inicializacao
    try:
        reg.imprimir(pares[0] if pares else "BTCUSDT")
        fg.imprimir()
    except Exception as e:
        print(f"[AVISO] Regime/FearGreed: {e}")

    # Thread de retreinamento automático semanal
    iniciar_retreinamento_automatico(pares)

    # Thread de relatorio diario via Telegram (P2-5) — TODOS os pares
    iniciar_relatorio_diario(pares)

    # I-9: vigia que da consumo aos bot_events CRITICAL
    iniciar_vigia_de_eventos()
    iniciar_mesa_comandos()

    # Threads — uma por par
    for par in pares:
        nome = f"loop-{par}"
        threading.Thread(
            target=loop_par, args=(par, args.intervalo, simulacao), daemon=True, name=nome
        ).start()
        # I-9: declara a thread como essencial para o /health. A morte de uma
        # loop_par era completamente silenciosa: o processo seguia vivo, o health
        # server seguia respondendo 200, e o par simplesmente parava de ser
        # avaliado sem que nada denunciasse.
        try:
            health.registrar_thread_essencial(nome)
        except Exception:
            pass

    # C-7: encerramento limpo. NSSM (Windows) manda CTRL_C_EVENT (SIGINT) e
    # CTRL_BREAK_EVENT (SIGBREAK) no stop; systemd/Railway (Linux) manda SIGTERM.
    _registrar_signal_handlers()

    try:
        iniciar_websocket_async()
        iniciar_websocket_depth_async()
        # time.sleep (nao Event.wait) porque no Windows o sleep e interrompido
        # pelo evento de console (CTRL_C/BREAK do NSSM), permitindo ao handler
        # rodar e ao path gracioso executar. Event.wait bloqueia em C e atrasa
        # o sinal ate o Windows force-terminar (STATUS_CONTROL_C_EXIT).
        while not _shutdown_event.is_set():
            time.sleep(0.5)
        print("\n\033[93m[BOT] Sinal de encerramento recebido — finalizando.\033[0m")
    except KeyboardInterrupt:
        _shutdown_event.set()
        print("\n\033[93m[BOT] Encerrado pelo usuario.\033[0m")
    finally:
        try:
            with _lock:
                database.salvar_cvd(cvd_btc, total_compras, total_vendas, symbol="BTCUSDT")
            print(f"[BOT] CVD BTC final: {cvd_btc:+.3f} BTC")
        except Exception as e:
            print(f"[BOT] AVISO: falha ao salvar CVD final: {e}")
        try:
            database.fechar_pool()  # fecha o pool Postgres/Supabase (evita conexões orphan)
        except Exception:
            pass


if __name__ == "__main__":
    main()
