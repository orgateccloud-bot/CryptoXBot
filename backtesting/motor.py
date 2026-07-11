"""
Motor de Backtesting — BotBinance
===================================
Simula a estratégia EMA+RSI+CVD sobre dados históricos reais.

Regras simuladas:
  Entrada LONG:  EMA20 > EMA50, RSI entre 40-65, Funding neutro
  Saída:         Stop Loss 1.5% ou Take Profit 5.0%
  Tamanho:       Fixo por operação (configurável)
  Slippage:      0.05% por lado (simulação realista)
  Taxa:          0.04% por lado (Binance Futures Taker)

Uso:
  python backtesting/motor.py
  python backtesting/motor.py --intervalo 4h --capital 1000
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

import indicadores as ind
import score as score_mod
from backtesting.metricas import (
    calmar_ratio,
    deflated_sharpe_ratio,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)

DB_PATH = "data/btc_data.db"
SYMBOL = "BTCUSDT"

# Parâmetros da estratégia EMA
EMA_RAPIDA = 20
EMA_LENTA = 50


# Wrappers para compatibilidade com o motor
def calcular_ema(fechamentos, periodo):
    arr = np.array(fechamentos, dtype=float)
    result = ind.ema(arr, periodo)
    return [None] * (periodo - 1) + list(result[periodo - 1 :])


def calcular_rsi(fechamentos, periodo=14):
    return ind.rsi(fechamentos, periodo)


# Parâmetros de risco
STOP_PCT = 0.015  # 1.5%
TARGET_PCT = 0.050  # 5.0%
TAXA_BINANCE = 0.0004  # 0.04% por lado
SLIPPAGE = 0.0005  # 0.05% por lado


# ── Simulação de CVD ───────────────────────────────────────


def simular_historico_ticks(klines, periodo_ticks=50):
    """
    Simula historico_ticks baseado em klines para cálculo de CVD.
    Como não temos dados reais de buyer_maker, simulamos baseado na direção do candle.
    """
    ticks = []
    for k in klines[-periodo_ticks:]:
        abertura, maxima, minima, fechamento, volume = k[1], k[2], k[3], k[4], k[5]
        # Simular ticks: assumir 70% do volume como buyer se fechamento > abertura
        is_bullish = fechamento > abertura
        buyer_volume = volume * 0.7 if is_bullish else volume * 0.3
        seller_volume = volume - buyer_volume

        # Adicionar tick de abertura (neutro, mas vamos classificar)
        ticks.append(
            {"preco": abertura, "is_buyer_maker": not is_bullish, "quantidade": volume * 0.1}
        )
        # Tick principal
        ticks.append(
            {"preco": fechamento, "is_buyer_maker": not is_bullish, "quantidade": buyer_volume}
        )
        if seller_volume > 0:
            ticks.append(
                {"preco": fechamento, "is_buyer_maker": is_bullish, "quantidade": seller_volume}
            )

    return ticks[-periodo_ticks:]  # últimos 50 ticks simulados


# ── Carregar dados ────────────────────────────────────────────


def carregar_klines(intervalo):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT timestamp, abertura, maxima, minima, fechamento, volume
        FROM klines
        WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (SYMBOL, intervalo),
    ).fetchall()
    conn.close()
    return rows


# ── Motor principal ───────────────────────────────────────────


def rodar_backtest(intervalo="1h", capital_inicial=1000.0, risco_por_trade=0.02):
    """
    Risco por trade: 2% do capital (padrão conservador).
    """
    klines = carregar_klines(intervalo)
    if len(klines) < EMA_LENTA + 10:
        print(f"Dados insuficientes para [{intervalo}]. Rode coletar_dados.py primeiro.")
        return None

    fechamentos = [k[4] for k in klines]
    maximas = [k[2] for k in klines]
    minimas = [k[3] for k in klines]
    timestamps = [k[0] for k in klines]

    ema20 = calcular_ema(fechamentos, EMA_RAPIDA)
    ema50 = calcular_ema(fechamentos, EMA_LENTA)
    rsi = calcular_rsi(fechamentos)

    capital = capital_inicial
    operacoes = []
    posicao = None  # {entrada, stop, target, tamanho_btc, idx}

    for i in range(EMA_LENTA + 1, len(klines)):
        preco = fechamentos[i]
        e20 = ema20[i]
        e50 = ema50[i]
        rsi_val = rsi[i]
        maxima_i = maximas[i]
        minima_i = minimas[i]

        if rsi_val is None:
            continue

        # ── Verificar saída de posição aberta ──────────────────
        if posicao:
            saiu = False
            resultado = 0.0

            # Stop atingido (minima tocou o stop)
            if minima_i <= posicao["stop"]:
                preco_saida = posicao["stop"] * (1 - SLIPPAGE)
                pnl_pct = (preco_saida - posicao["entrada"]) / posicao["entrada"]
                resultado = posicao["tamanho_usdt"] * pnl_pct
                resultado -= posicao["tamanho_usdt"] * TAXA_BINANCE * 2
                tipo_saida = "STOP"
                saiu = True

            # Target atingido (maxima tocou o alvo)
            elif maxima_i >= posicao["target"]:
                preco_saida = posicao["target"] * (1 - SLIPPAGE)
                pnl_pct = (preco_saida - posicao["entrada"]) / posicao["entrada"]
                resultado = posicao["tamanho_usdt"] * pnl_pct
                resultado -= posicao["tamanho_usdt"] * TAXA_BINANCE * 2
                tipo_saida = "TARGET"
                saiu = True

            if saiu:
                capital += resultado
                op = {
                    "entrada_idx": posicao["idx"],
                    "saida_idx": i,
                    "entrada_dt": datetime.fromtimestamp(
                        timestamps[posicao["idx"]] / 1000
                    ).strftime("%d/%m/%Y %H:%M"),
                    "saida_dt": datetime.fromtimestamp(timestamps[i] / 1000).strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                    "preco_entrada": posicao["entrada"],
                    "preco_saida": preco_saida,
                    "resultado": round(resultado, 4),
                    "resultado_pct": round(pnl_pct * 100, 2),
                    "tipo_saida": tipo_saida,
                    "capital_apos": round(capital, 2),
                }
                operacoes.append(op)
                posicao = None

        # ── Calcular Score Unificado ──────────────────────────
        historico_ticks = simular_historico_ticks(klines[: i + 1])  # ticks até i

        # Simular dados para score (valores mock para componentes não calculados)
        regime_info = {"regime_final": "TENDENCIA_ALTA"}  # mock
        fear_info = {"valor": 50}  # mock
        tend_4h = "ALTA"  # mock
        ml_prob = 0.55  # mock
        vwap_val = preco  # mock
        vol_rel = 1.0  # mock
        atr_atual = 500  # mock
        atr_media = 510  # mock

        score_result = score_mod.calcular(
            regime_info=regime_info,
            fear_info=fear_info,
            tend_4h=tend_4h,
            ml_prob=ml_prob,
            preco=preco,
            ema20=e20,
            ema50=e50,
            rsi=rsi_val,
            vwap_val=vwap_val,
            vol_rel=vol_rel,
            atr_atual=atr_atual,
            atr_media=atr_media,
            historico_ticks=historico_ticks,
        )

        sinal_compra = score_result["decisao"] in ("OPERAR_CHEIO", "OPERAR_REDUZIDO")
        if sinal_compra:
            entrada_com_slip = preco * (1 + SLIPPAGE)
            tamanho_base_usdt = capital * risco_por_trade / STOP_PCT
            tamanho_usdt = tamanho_base_usdt * score_result["tamanho_fator"]
            tamanho_usdt = min(tamanho_usdt, capital)  # nunca mais que o capital
            posicao = {
                "entrada": entrada_com_slip,
                "stop": entrada_com_slip * (1 - STOP_PCT),
                "target": entrada_com_slip * (1 + TARGET_PCT),
                "tamanho_usdt": tamanho_usdt,
                "idx": i,
            }

    return calcular_metricas(operacoes, capital_inicial, capital, intervalo)


# ── Métricas ──────────────────────────────────────────────────


def calcular_metricas(operacoes, capital_inicial, capital_final, intervalo):
    if not operacoes:
        return {"erro": "Nenhuma operacao encontrada."}

    total = len(operacoes)
    ganhos = [o for o in operacoes if o["resultado"] > 0]
    perdas = [o for o in operacoes if o["resultado"] <= 0]

    win_rate = len(ganhos) / total * 100
    lucro_total = sum(o["resultado"] for o in ganhos)
    perda_total = abs(sum(o["resultado"] for o in perdas))
    pf = profit_factor(lucro_total, perda_total)
    retorno_total = (capital_final - capital_inicial) / capital_inicial * 100

    # Max Drawdown
    pico = capital_inicial
    max_dd = 0.0
    capital = capital_inicial
    for o in operacoes:
        capital += o["resultado"]
        if capital > pico:
            pico = capital
        dd = (pico - capital) / pico * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe Ratio (simplificado, risk-free = 0)
    retornos = [o["resultado_pct"] for o in operacoes]
    sharpe = sharpe_ratio(retornos)

    # Periodo do backtest (para Calmar) — mesmo formato de datetime ja usado
    dt_ini = datetime.strptime(operacoes[0]["entrada_dt"], "%d/%m/%Y %H:%M")
    dt_fim = datetime.strptime(operacoes[-1]["saida_dt"], "%d/%m/%Y %H:%M")
    dias_periodo = max((dt_fim - dt_ini).total_seconds() / 86400, 0)

    # Expectância (valor esperado por trade em %)
    media_ganho = sum(o["resultado_pct"] for o in ganhos) / len(ganhos) if ganhos else 0
    media_perda = abs(sum(o["resultado_pct"] for o in perdas) / len(perdas)) if perdas else 0
    expectancia = (win_rate / 100 * media_ganho) - ((1 - win_rate / 100) * media_perda)

    return {
        "intervalo": intervalo,
        "total_trades": total,
        "win_rate_%": round(win_rate, 1),
        "trades_ganhos": len(ganhos),
        "trades_perdas": len(perdas),
        "profit_factor": round(pf, 2),
        "retorno_total_%": round(retorno_total, 2),
        "capital_inicial": capital_inicial,
        "capital_final": round(capital_final, 2),
        "max_drawdown_%": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino_ratio(retornos), 2),
        "calmar_ratio": round(calmar_ratio(retorno_total, max(max_dd, 0.0), dias_periodo), 2),
        "dsr": round(deflated_sharpe_ratio(retornos, None), 4),
        "expectancia_%": round(expectancia, 2),
        "media_ganho_%": round(media_ganho, 2),
        "media_perda_%": round(media_perda, 2),
        "operacoes": operacoes,
    }


# ── Relatório ─────────────────────────────────────────────────


def imprimir_relatorio(r):
    if "erro" in r:
        print(f"ERRO: {r['erro']}")
        return

    def nota(valor, ref_bom, ref_otimo, maior_melhor=True):
        if maior_melhor:
            if valor >= ref_otimo:
                return "[OTIMO]"
            if valor >= ref_bom:
                return "[BOM]  "
            return "[RUIM] "
        else:
            if valor <= ref_otimo:
                return "[OTIMO]"
            if valor <= ref_bom:
                return "[BOM]  "
            return "[RUIM] "

    print("\n" + "=" * 58)
    print(f"  RESULTADO DO BACKTESTING  [{r['intervalo']}]")
    print("=" * 58)
    print(f"  Total de operacoes:  {r['total_trades']}")
    print(f"  Ganhos / Perdas:     {r['trades_ganhos']} / {r['trades_perdas']}")
    print()
    print(f"  Win Rate:      {r['win_rate_%']:6.1f}%   {nota(r['win_rate_%'], 52, 60)}")
    print(f"  Profit Factor: {r['profit_factor']:6.2f}    {nota(r['profit_factor'], 1.3, 1.8)}")
    print(f"  Sharpe Ratio:  {r['sharpe_ratio']:6.2f}    {nota(r['sharpe_ratio'], 1.0, 1.5)}")
    print(f"  Expectancia:   {r['expectancia_%']:6.2f}%   {nota(r['expectancia_%'], 0.1, 0.5)}")
    print(
        f"  Max Drawdown:  {r['max_drawdown_%']:6.2f}%   {nota(r['max_drawdown_%'], 15, 8, maior_melhor=False)}"
    )
    print(
        f"  Sortino Ratio: {r.get('sortino_ratio', 0):6.2f}    {nota(r.get('sortino_ratio', 0), 1.5, 2.0)}"
    )
    print(
        f"  Calmar Ratio:  {r.get('calmar_ratio', 0):6.2f}    {nota(r.get('calmar_ratio', 0), 1.0, 2.0)}"
    )
    print(
        f"  DSR (PSR, sem correção de multiple-testing — 1 único backtest): "
        f"{r.get('dsr', 0):6.4f}    {nota(r.get('dsr', 0), 0.90, 0.95)}"
    )
    print(f"  Retorno Total: {r['retorno_total_%']:6.2f}%")
    print()
    print(f"  Capital Inicial: ${r['capital_inicial']:,.2f}")
    print(f"  Capital Final:   ${r['capital_final']:,.2f}")
    print(f"  Lucro Liquido:   ${r['capital_final']-r['capital_inicial']:,.2f}")
    print()
    print(f"  Media Ganho/Trade: +{r['media_ganho_%']:.2f}%")
    print(f"  Media Perda/Trade: -{r['media_perda_%']:.2f}%")

    # Veredicto
    print("\n" + "-" * 58)
    score = 0
    if r["win_rate_%"] >= 52:
        score += 1
    if r["profit_factor"] >= 1.3:
        score += 1
    if r["sharpe_ratio"] >= 1.0:
        score += 1
    if r["max_drawdown_%"] <= 15:
        score += 1
    if r["expectancia_%"] > 0:
        score += 1

    if score == 5:
        veredito = "ESTRATEGIA EXCELENTE — Pode operar com capital real"
    elif score >= 3:
        veredito = "ESTRATEGIA PROMISSORA — Otimize antes de capital real"
    else:
        veredito = "ESTRATEGIA FRACA — Reformule antes de operar"

    print(f"  CRITERIOS APROVADOS: {score}/5")
    print(f"  VEREDITO: {veredito}")
    print("=" * 58)

    # Últimas 10 operações
    print("\n  ULTIMAS 10 OPERACOES:")
    print(f"  {'Data Entrada':16s} {'Entrada':>10s} {'Saida':>10s} {'Resultado':>10s} {'Tipo'}")
    print("  " + "-" * 56)
    for o in r["operacoes"][-10:]:
        sinal = "+" if o["resultado"] > 0 else ""
        print(
            f"  {o['entrada_dt']:16s} "
            f"${o['preco_entrada']:>9,.0f} "
            f"${o['preco_saida']:>9,.0f} "
            f"{sinal}{o['resultado_pct']:>6.2f}%   "
            f"{o['tipo_saida']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intervalo", default="1h", help="1h, 4h, 15m, 1d")
    parser.add_argument("--capital", default=1000.0, type=float, help="Capital inicial em USDT")
    parser.add_argument("--risco", default=0.02, type=float, help="Risco por trade (0.02 = 2%)")
    args = parser.parse_args()

    resultado = rodar_backtest(args.intervalo, args.capital, args.risco)
    if resultado:
        imprimir_relatorio(resultado)
