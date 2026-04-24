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

import sys
import time
import threading
import argparse
import asyncio
import websockets
import json
import logging
from datetime import datetime
import random

import database
import risco as gestao_risco
import regime as reg
import fear_greed as fg
from suporte import ScaleIn
from analise_mercado import relatorio_completo
from estrategias.otimizada import analisar as analisar_otimizada, imprimir as imprimir_otimizada
from executor import Executor
from config.settings import SYMBOL_WS, MIN_BTC_VOLUME, WHALE_BTC_VOLUME
from logger import logger

# Retreinamento automático semanal (domingo 02h)
_RETREINAMENTO_HORA  = 2    # hora do dia (02:00)
_RETREINAMENTO_DIA   = 6    # 6 = domingo (weekday())

# Configurar logging estruturado para WebSocket
ws_logger = logging.getLogger("websocket")
ws_logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s - Extra: %(extra)s"
)
handler.setFormatter(formatter)
ws_logger.addHandler(handler)

# Pares ativos (BTC sempre ativo, ETH com parâmetros otimizados)
PARES_ATIVOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# ── Estado global ──────────────────────────────────────────────
cvd_btc       = 0.0
total_compras = 0.0
total_vendas  = 0.0
preco_atual   = 0.0
_lock = threading.Lock()

# Estado WebSocket
ws_state = {
    "last_trade_id": 0,  # Para evitar duplicatas
    "connected": False,
    "last_message_time": 0.0,
    "latency_ms": 0.0,
}

# Estado por par (executor e scale-in independentes)
_estado_pares = {}   # symbol → {"executor": Executor, "scale_in": ScaleIn|None}


# ── Helpers ────────────────────────────────────────────────────

def formatar_valor(v):
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}K"


# ── WebSocket Binance Assíncrono com Retry ──────────────────

async def websocket_handler():
    """
    Handler assíncrono para WebSocket Binance com retry exponencial e state management.
    """
    url = f"wss://fstream.binance.com/ws/{SYMBOL_WS}@aggTrade"
    max_retries = 10
    base_delay = 1.0  # segundos
    max_delay = 300.0  # 5 minutos
    jitter_factor = 0.1

    attempt = 0
    while attempt < max_retries:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket_conn:
                ws_state["connected"] = True
                ws_state["last_message_time"] = time.time()
                ws_logger.info("WebSocket conectado", extra={"symbol": SYMBOL_WS, "attempt": attempt})

                async for message in websocket_conn:
                    try:
                        await process_message(message)
                        ws_state["last_message_time"] = time.time()
                    except Exception as e:
                        ws_logger.error("Erro processando mensagem", extra={
                            "error": str(e),
                            "symbol": SYMBOL_WS,
                            "latency_ms": ws_state["latency_ms"]
                        })

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.WebSocketException,
                asyncio.TimeoutError) as e:
            ws_state["connected"] = False
            latency = (time.time() - ws_state["last_message_time"]) * 1000
            ws_state["latency_ms"] = latency

            # Backoff exponencial com jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(-jitter_factor * delay, jitter_factor * delay)
            delay += jitter
            delay = max(0.1, delay)  # mínimo 100ms

            logger.warning("WebSocket desconectado", extra={
                "error": str(e),
                "symbol": SYMBOL_WS,
                "attempt": attempt,
                "latency_ms": latency,
                "next_retry_in_s": delay
            })

            await asyncio.sleep(delay)
            attempt += 1

        except Exception as e:
            logger.error("Erro crítico WebSocket", extra={
                "error": str(e),
                "symbol": SYMBOL_WS,
                "attempt": attempt
            })
            attempt += 1
            await asyncio.sleep(1.0)

    logger.critical("Máximo de tentativas atingido", extra={"symbol": SYMBOL_WS, "max_retries": max_retries})


async def process_message(message):
    """
    Processa mensagem WebSocket com state management para evitar duplicatas.
    """
    global cvd_btc, total_compras, total_vendas, preco_atual

    data = json.loads(message)
    trade_id = int(data["t"])  # ID único do trade
    price = float(data["p"])
    quantity = float(data["q"])
    is_buyer_maker = data["m"]

    # Evitar duplicatas: só processar se trade_id > last_trade_id
    if trade_id <= ws_state["last_trade_id"]:
        return  # Duplicata, ignorar

    ws_state["last_trade_id"] = trade_id

    with _lock:
        preco_atual = price
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
            database.salvar_trade(price, quantity, direcao, WHALE_BTC_VOLUME)
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
            print(f"{cor}[{hora}] {seta} {direcao:6s}{reset}  "
                  f"{cinza}${price:,.2f}  {quantity:.3f} BTC ({formatar_valor(price*quantity)}){reset}")
        cvd_cor = "\033[92m" if cvd_btc >= 0 else "\033[91m"
        print(f"  {cinza}CVD: {cvd_cor}{cvd_btc:+.3f}{reset}  "
              f"{cinza}C:{total_compras:.2f} V:{total_vendas:.2f}{reset}")


def iniciar_websocket_async():
    """
    Inicia o loop assíncrono do WebSocket em uma thread separada.
    """
    def run_async():
        asyncio.run(websocket_handler())

    thread = threading.Thread(target=run_async, daemon=True, name="websocket-async")
    thread.start()


# ── Retreinamento Automático Semanal ──────────────────────────

def _retreinar_modelos(pares: list[str]):
    """Retreina XGBoost e MLP para todos os pares. Chamado automaticamente."""
    print(f"\n\033[94m[RETRAIN] Iniciando retreinamento semanal — {datetime.now().strftime('%d/%m/%Y %H:%M')}\033[0m")
    try:
        from ml_filtro import treinar as treinar_xgb
        from lstm_modelo import treinar as treinar_mlp

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
                agora.weekday() == _RETREINAMENTO_DIA and
                agora.hour == _RETREINAMENTO_HORA and
                agora.minute < 10  # janela de 10 min para não falhar se bot reiniciar
            )
            data_hoje = agora.date()
            ja_retreinou_hoje = (ultimo_retreinamento == data_hoje)

            if domingo_e_hora_certa and not ja_retreinou_hoje:
                _retreinar_modelos(pares)
                ultimo_retreinamento = data_hoje

            time.sleep(300)  # verifica a cada 5 minutos (baixo overhead)

    thread = threading.Thread(target=_loop_retrain, daemon=True, name="retrain-weekly")
    thread.start()
    print(f"\033[94m[RETRAIN] Retreinamento automático agendado — todo domingo às {_RETREINAMENTO_HORA:02d}h\033[0m")


# ── Loop de Estratégia por Par ────────────────────────────────

def loop_par(par, intervalo_min, simulacao):
    """Loop independente para cada par operado."""
    global _estado_pares
    reset = "\033[0m"

    print(f"\033[94m[BOT] {par} — Estrategia iniciada (intervalo: {intervalo_min} min).\033[0m")
    executor = Executor(simulacao=simulacao, symbol=par)
    _estado_pares[par] = {"executor": executor, "scale_in": None}

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
            # CVD: BTC usa WebSocket, ETH e outros usam None (opcional)
            cvd_snap = None
            if par == "BTCUSDT":
                with _lock:
                    cvd_snap = cvd_btc

            # Ensemble ML
            ensemble_result = None
            ml_prob = None
            if ensemble_disponivel:
                try:
                    ensemble_result = ens_mod.prever(symbol=par) if hasattr(ens_mod, 'symbol') else ens_mod.prever()
                    ml_prob = ensemble_result.get("prob_ensemble")
                except Exception:
                    pass

            resultado = analisar_otimizada(symbol=par, cvd_atual=cvd_snap, ml_prob=ml_prob, ensemble_result=ensemble_result)
            imprimir_otimizada(symbol=par, cvd_atual=cvd_snap, ml_prob=ml_prob, ensemble_result=ensemble_result)

            try:
                logger.registrar_avaliacao(resultado, symbol=par)
            except Exception:
                pass

            sinal = resultado["sinal"]
            estado = _estado_pares[par]
            exec_par = estado["executor"]

            # Salvar snapshot (apenas BTC por ora, para não sobrecarregar a tabela)
            if par == "BTCUSDT":
                database.salvar_snapshot({
                    "preco":             resultado["preco"],
                    "variacao_24h_%":    0,
                    "volume_24h_btc":    0,
                    "funding_rate_%":    resultado["funding_%"],
                    "open_interest_btc": 0,
                    "ema20_1h":          resultado["ema20_1h"],
                    "ema50_1h":          resultado["ema50_1h"],
                    "rsi_1h":            resultado["rsi"],
                    "tendencia":         resultado["tend_4h"],
                    "pressao_dominante": "COMPRA" if (cvd_snap or 0) > 0 else "VENDA",
                    "liquidez_compra_usdt": 0,
                    "liquidez_venda_usdt":  0,
                })

            # Executar sinal
            if sinal in ("COMPRA", "VENDA") and not exec_par.posicao:
                preco  = resultado["preco"]
                stop   = resultado["stop_loss"]
                target = resultado["take_profit"]
                saldo  = gestao_risco.get_saldo_usdt()

                validacao = gestao_risco.validar_trade(sinal, preco, saldo if saldo > 0 else 100)
                if validacao["pode"]:
                    tamanho_base = validacao["tamanho_btc"]
                    fator        = resultado.get("tamanho_fator", 1.0)
                    tamanho      = round(tamanho_base * fator, 6)
                    score_val    = resultado.get("score", 0)

                    # Scale-In
                    sup_forte  = resultado.get("suporte_forte", 0)
                    scale_in   = estado["scale_in"]
                    if scale_in is None or scale_in.completo:
                        scale_in = ScaleIn(tamanho, sup_forte)
                        estado["scale_in"] = scale_in
                        parcela = scale_in.entrada_parcela1(preco)
                        print(f"\n\033[93m[{par}][SCALE-IN] Parcela 1/3: {parcela:.6f} @ ${preco:,.2f} "
                              f"(Score:{score_val}){reset}")
                    elif scale_in.parcela_atual == 1:
                        parcela = scale_in.entrada_parcela2(preco)
                        print(f"\n\033[93m[{par}][SCALE-IN] Parcela 2/3: {parcela:.6f} @ ${preco:,.2f} "
                              f"(PM: ${scale_in.preco_medio:,.2f}){reset}")
                    elif scale_in.parcela_atual == 2:
                        parcela = scale_in.entrada_parcela3(preco)
                        print(f"\n\033[93m[{par}][SCALE-IN] Parcela 3/3: {parcela:.6f} @ ${preco:,.2f} "
                              f"(PM: ${scale_in.preco_medio:,.2f}) COMPLETO{reset}")
                    else:
                        parcela = tamanho

                    # Telegram
                    try:
                        from telegram_bot import alerta_sinal
                        alerta_sinal(sinal, preco, stop, target,
                                     resultado["filtros_ok"], resultado["filtros_total"], ml_prob,
                                     par=par)
                    except Exception:
                        pass

                    if sinal == "COMPRA":
                        exec_par.abrir_long(preco, parcela, stop, target)
                else:
                    print(f"\033[91m[{par}][RISCO] Trade bloqueado: {validacao['motivo']}\033[0m")

            # CVD snapshot periódico (BTC apenas)
            if par == "BTCUSDT":
                database.salvar_cvd(cvd_btc, total_compras, total_vendas)

        except Exception as e:
            print(f"\033[91m[ERRO {par}] {e}\033[0m")


# ── Ponto de entrada ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BotBinance v2")
    parser.add_argument("--intervalo",  type=int, default=15)
    parser.add_argument("--simulacao",  action="store_true", default=True,
                        help="Paper trading (padrao: ativado por seguranca)")
    parser.add_argument("--real",       action="store_true",
                        help="Ativar ordens reais (desativa simulacao)")
    parser.add_argument("--relatorio",  action="store_true")
    parser.add_argument("--estrategia", action="store_true")
    parser.add_argument("--backtest",   type=str, metavar="INTERVALO")
    parser.add_argument("--treinar-ml", action="store_true")
    parser.add_argument("--par",        type=str, default=None,
                        help="Operar apenas um par (ex: BTCUSDT). Padrao: todos os pares ativos.")
    args = parser.parse_args()

    simulacao = not args.real

    # Definir pares a operar
    if args.par:
        pares = [args.par.upper()]
    else:
        pares = PARES_ATIVOS

    # Modos de uso único
    if args.relatorio:
        relatorio_completo(); return

    if args.estrategia:
        database.inicializar()
        for par in pares:
            imprimir_otimizada(symbol=par)
        return

    if args.backtest:
        from backtesting.motor import rodar_backtest, imprimir_relatorio
        database.inicializar()
        r = rodar_backtest(args.backtest, 1000.0)
        if r: imprimir_relatorio(r)
        return

    if args.treinar_ml:
        from ml_filtro import treinar
        for par in pares:
            treinar("1h", par)
        return

    # === Modo completo =========================================
    database.inicializar()

    print("\n" + "="*56)
    print("  BOTBINANCE v2 — INICIANDO")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"  Modo: {'SIMULACAO (Paper Trading)' if simulacao else 'REAL'}")
    print(f"  Pares: {', '.join(pares)}")
    print("="*56)
    print("  Modulos ativos:")
    print("  [OK] WebSocket BTC/USDT Futures (CVD em tempo real)")
    print(f"  [OK] Estrategia Otimizada MTF+ATR+Volume+VWAP+ML (por par)")
    print(f"  [OK] Gestao de Risco (Kelly + Circuit Breaker)")
    print(f"  [OK] Executor {'Simulado' if simulacao else 'Real'} + Trailing Stop (por par)")
    print(f"  [OK] Banco de dados SQLite")
    print(f"  [OK] Retreinamento automatico (domingo 02h)")
    print(f"\n  Avaliacao de sinal: a cada {args.intervalo} minutos")
    print("  Ctrl+C para encerrar")
    print("="*56 + "\n")

    relatorio_completo()

    # Regime e Fear & Greed na inicializacao
    try:
        reg.imprimir()
        fg.imprimir()
    except Exception as e:
        print(f"[AVISO] Regime/FearGreed: {e}")

    # Thread de retreinamento automático semanal
    iniciar_retreinamento_automatico(pares)

    # Threads — uma por par
    for par in pares:
        threading.Thread(target=loop_par,
                         args=(par, args.intervalo, simulacao),
                         daemon=True, name=f"loop-{par}").start()

    try:
        iniciar_websocket_async()
    except KeyboardInterrupt:
        print("\n\033[93m[BOT] Encerrado pelo usuario.\033[0m")
        with _lock:
            database.salvar_cvd(cvd_btc, total_compras, total_vendas)
        print(f"[BOT] CVD BTC final: {cvd_btc:+.3f} BTC")


if __name__ == "__main__":
    main()
