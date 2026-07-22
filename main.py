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
from estrategias.otimizada import analisar as analisar_otimizada
from estrategias.otimizada import imprimir as imprimir_otimizada
from executor import Executor
from health import start_health_server
from logger import logger
from suporte import ScaleIn

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


async def websocket_handler():
    """
    Handler assíncrono para WebSocket Binance com retry exponencial e state management.
    """
    # P0-1: mesmo mercado da execucao (spot por padrao, via WS_BASE_URL).
    url = f"{WS_BASE_URL}/ws/{SYMBOL_WS}@aggTrade"
    max_retries = 10
    base_delay = 1.0  # segundos
    max_delay = 300.0  # 5 minutos
    jitter_factor = 0.1

    attempt = 0
    while attempt < max_retries and not _shutdown_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket_conn:
                ws_state["connected"] = True
                ws_state["last_message_time"] = time.time()
                ws_logger.info(
                    "WebSocket conectado", extra={"symbol": SYMBOL_WS, "attempt": attempt}
                )

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
            latency = (time.time() - ws_state["last_message_time"]) * 1000
            ws_state["latency_ms"] = latency
            health.increment_metric("ws_reconexoes")

            # Backoff exponencial com jitter
            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(-jitter_factor * delay, jitter_factor * delay)
            delay += jitter
            delay = max(0.1, delay)  # mínimo 100ms

            logger.warning(
                "WebSocket desconectado",
                extra={
                    "error": str(e),
                    "symbol": SYMBOL_WS,
                    "attempt": attempt,
                    "latency_ms": latency,
                    "next_retry_in_s": delay,
                },
            )

            await asyncio.sleep(delay)
            attempt += 1

        except Exception as e:
            logger.error(
                "Erro crítico WebSocket",
                extra={"error": str(e), "symbol": SYMBOL_WS, "attempt": attempt},
            )
            attempt += 1
            await asyncio.sleep(1.0)

    logger.critical(
        "Máximo de tentativas atingido", extra={"symbol": SYMBOL_WS, "max_retries": max_retries}
    )


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
                f"{cinza}${price:,.2f}  {quantity:.3f} BTC ({formatar_valor(price*quantity)}){reset}"
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
    max_retries = 10
    base_delay = 1.0
    max_delay = 300.0
    jitter_factor = 0.1

    attempt = 0
    while attempt < max_retries and not _shutdown_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket_conn:
                ws_state_depth["connected"] = True
                ws_state_depth["last_message_time"] = time.time()
                ws_logger.info(
                    "WebSocket @depth conectado", extra={"symbol": SYMBOL_WS, "attempt": attempt}
                )

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
            latency = (time.time() - ws_state_depth["last_message_time"]) * 1000
            ws_state_depth["latency_ms"] = latency
            health.increment_metric("ws_reconexoes")

            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(-jitter_factor * delay, jitter_factor * delay)
            delay += jitter
            delay = max(0.1, delay)

            logger.warning(
                "WebSocket @depth desconectado",
                extra={
                    "error": str(e),
                    "symbol": SYMBOL_WS,
                    "attempt": attempt,
                    "latency_ms": latency,
                    "next_retry_in_s": delay,
                },
            )

            await asyncio.sleep(delay)
            attempt += 1

        except Exception as e:
            logger.error(
                "Erro crítico WebSocket @depth",
                extra={"error": str(e), "symbol": SYMBOL_WS, "attempt": attempt},
            )
            attempt += 1
            await asyncio.sleep(1.0)

    logger.critical(
        "Máximo de tentativas atingido (@depth)",
        extra={"symbol": SYMBOL_WS, "max_retries": max_retries},
    )


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
        f"\n\033[94m[RETRAIN] Iniciando retreinamento semanal — {datetime.now().strftime('%d/%m/%Y %H:%M')}\033[0m"
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

        print(f"\033[94m[RETRAIN] MLP Sequencial — BTCUSDT...\033[0m")
        try:
            treinar_mlp("1h")
            print(f"\033[92m[RETRAIN] MLP OK\033[0m")
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
        f"\033[94m[RETRAIN] Retreinamento automático agendado — todo domingo às {_RETREINAMENTO_HORA:02d}h\033[0m"
    )


def iniciar_relatorio_diario(symbol: str):
    """Thread que dispara o relatorio diario (Telegram) 1x por dia às
    _RELATORIO_HORA. Não bloqueia o loop principal. (P2-5: telegram_bot.
    relatorio_diario() existia pronta mas nunca era chamada em produção.)"""

    def _loop_relatorio():
        ultimo_relatorio = None
        while True:
            agora = datetime.now()
            hora_certa = agora.hour == _RELATORIO_HORA and agora.minute < 10
            data_hoje = agora.date()
            ja_relatou_hoje = ultimo_relatorio == data_hoje

            if hora_certa and not ja_relatou_hoje:
                try:
                    d = logger.dados_relatorio_diario(symbol)
                    saldo_atual = gestao_risco.get_saldo_usdt()
                    telegram_bot.relatorio_diario(
                        d["pnl_usdt"], d["trades_dia"], saldo_atual, d["win_rate"]
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


def loop_par(par, intervalo_min, simulacao):
    """Loop independente para cada par operado."""
    global _estado_pares
    reset = "\033[0m"

    print(f"\033[94m[BOT] {par} — Estrategia iniciada (intervalo: {intervalo_min} min).\033[0m")
    executor = Executor(simulacao=simulacao, symbol=par)
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
            ensemble_result = None
            ml_prob = None
            if ensemble_disponivel:
                try:
                    ensemble_result = (
                        ens_mod.prever(symbol=par)
                        if hasattr(ens_mod, "symbol")
                        else ens_mod.prever()
                    )
                    ml_prob = ensemble_result.get("prob_ensemble")
                except Exception:
                    pass

            resultado = analisar_otimizada(
                symbol=par,
                cvd_atual=cvd_snap,
                ml_prob=ml_prob,
                ensemble_result=ensemble_result,
                historico_ticks=historico_ticks_snap,
                obi=obi_snap,
            )
            imprimir_otimizada(
                symbol=par,
                cvd_atual=cvd_snap,
                ml_prob=ml_prob,
                ensemble_result=ensemble_result,
                historico_ticks=historico_ticks_snap,
                obi=obi_snap,
            )

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
                )
                if validacao["pode"]:
                    tamanho_base = validacao["tamanho_btc"]
                    fator = resultado.get("tamanho_fator", 1.0)
                    tamanho = round(tamanho_base * fator, 6)
                    score_val = resultado.get("score", 0)

                    # Scale-In
                    sup_forte = resultado.get("suporte_forte", 0)
                    scale_in = estado["scale_in"]
                    if scale_in is None or scale_in.completo:
                        scale_in = ScaleIn(tamanho, sup_forte)
                        estado["scale_in"] = scale_in
                        parcela = scale_in.entrada_parcela1(preco)
                        print(
                            f"\n\033[93m[{par}][SCALE-IN] Parcela 1/3: {parcela:.6f} @ ${preco:,.2f} "
                            f"(Score:{score_val}){reset}"
                        )
                    elif scale_in.parcela_atual == 1:
                        parcela = scale_in.entrada_parcela2(preco)
                        print(
                            f"\n\033[93m[{par}][SCALE-IN] Parcela 2/3: {parcela:.6f} @ ${preco:,.2f} "
                            f"(PM: ${scale_in.preco_medio:,.2f}){reset}"
                        )
                    elif scale_in.parcela_atual == 2:
                        parcela = scale_in.entrada_parcela3(preco)
                        print(
                            f"\n\033[93m[{par}][SCALE-IN] Parcela 3/3: {parcela:.6f} @ ${preco:,.2f} "
                            f"(PM: ${scale_in.preco_medio:,.2f}) COMPLETO{reset}"
                        )
                    else:
                        parcela = tamanho

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

                    exec_par.abrir_long(
                        preco,
                        parcela,
                        stop,
                        target,
                        atr_relativo=atr_relativo,
                        sinal_id=resultado.get("sinal_id"),
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
        "--par",
        type=str,
        default=None,
        help="Operar apenas um par (ex: BTCUSDT). Padrao: todos os pares ativos.",
    )
    args = parser.parse_args()

    simulacao = not args.real
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
                "Abortando. Use --simulacao para paper trading ou configure as variaveis de ambiente."
            )
            raise SystemExit(1)

    # Definir pares a operar
    if args.par:
        pares = [args.par.upper()]
    else:
        pares = PARES_ATIVOS

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
        from backtesting.motor import imprimir_relatorio, rodar_backtest

        database.inicializar()
        r = rodar_backtest(args.backtest, 1000.0)
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
    print(f"  Pares: {', '.join(pares)}")
    print("=" * 56)
    print("  Modulos ativos:")
    print("  [OK] WebSocket BTC/USDT Spot (CVD + OBI em tempo real)")
    print(f"  [OK] Estrategia Otimizada MTF+ATR+Volume+VWAP+ML (por par)")
    print(f"  [OK] Gestao de Risco (Kelly + Circuit Breaker)")
    print(f"  [OK] Executor {'Simulado' if simulacao else 'Real'} + Trailing Stop (por par)")
    print(f"  [OK] Banco de dados {database.backend_info()['backend'].upper()}")
    print(f"  [OK] Retreinamento automatico (domingo 02h)")
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
        reg.imprimir()
        fg.imprimir()
    except Exception as e:
        print(f"[AVISO] Regime/FearGreed: {e}")

    # Thread de retreinamento automático semanal
    iniciar_retreinamento_automatico(pares)

    # Thread de relatorio diario via Telegram (P2-5)
    iniciar_relatorio_diario(pares[0])

    # Threads — uma por par
    for par in pares:
        threading.Thread(
            target=loop_par, args=(par, args.intervalo, simulacao), daemon=True, name=f"loop-{par}"
        ).start()

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
