"""
Backtesting da Estratégia Otimizada (MTF + ATR + Volume + VWAP + Bollinger)
Compara com a estratégia base para medir o ganho de cada filtro.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicadores as ind
from backtesting.metricas import (
    calmar_ratio,
    deflated_sharpe_ratio,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)

DB_PATH = "data/btc_data.db"
SYMBOL = "BTCUSDT"

STOP_PCT = 0.015
TARGET_PCT = 0.050
TAXA = 0.0004
SLIPPAGE = 0.0005

# Parâmetros otimizados
RSI_MIN = 42
RSI_MAX = 60
ATR_MIN_RATIO = 0.5  # ATR atual >= 50% da media
VOL_MIN_RATIO = 1.1  # volume >= 110% da media
BW_MIN_RATIO = 0.5  # Bollinger nao em squeeze puro


def carregar(intervalo):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT timestamp, abertura, maxima, minima, fechamento, volume
        FROM klines WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (SYMBOL, intervalo),
    ).fetchall()
    conn.close()
    return rows


def rodar(intervalo_entrada="1h", intervalo_mtf="4h", capital_inicial=1000.0):
    k1h = carregar(intervalo_entrada)
    k4h = carregar(intervalo_mtf)

    if len(k1h) < 60 or len(k4h) < 55:
        print("Dados insuficientes.")
        return None

    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]

    f4h = [r[4] for r in k4h]

    # Pré-computar indicadores 1H
    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = ind.atr(m1h, n1h, f1h, 14)
    volr = ind.volume_relativo(v1h, 20)
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bbu, bbm, bbl)
    vwap = ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20)

    # Pré-computar EMA 4H (alinhamento de índice aproximado: 1 candle 4h ≈ 4 candles 1h)
    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    def tend_4h_em(idx_1h):
        idx4 = min(idx_1h // 4, len(f4h) - 1)
        p = f4h[idx4]
        e20 = ema20_4h[idx4]
        e50 = ema50_4h[idx4]
        if p > e20 > e50:
            return "ALTA"
        if p < e20 < e50:
            return "BAIXA"
        return "LATERAL"

    capital = capital_inicial
    posicao = None
    operacoes = []
    filtros_contagem = {
        "ema": 0,
        "rsi": 0,
        "atr": 0,
        "volume": 0,
        "vwap": 0,
        "mtf": 0,
        "bollinger": 0,
        "total": 0,
    }

    for i in range(55, len(k1h)):
        preco = f1h[i]
        mx = m1h[i]
        mn = n1h[i]
        e20 = ema20[i]
        e50 = ema50[i]
        rsi_v = rsi14[i]
        atr_v = atr14[i]
        vr = volr[i]
        bw_v = bw[i]
        vwap_v = vwap[i]

        if any(x is None for x in [rsi_v, atr_v, vr, bw_v, vwap_v]):
            continue

        atr_med = sum(x for x in atr14[max(0, i - 20) : i] if x) / max(
            1, len([x for x in atr14[max(0, i - 20) : i] if x])
        )
        bw_med = sum(x for x in bw[max(0, i - 20) : i] if x) / max(
            1, len([x for x in bw[max(0, i - 20) : i] if x])
        )

        # Verificar saída
        if posicao:
            saiu = False
            if mn <= posicao["stop"]:
                ps = posicao["stop"] * (1 - SLIPPAGE)
                pnl = (
                    posicao["usdt"] * ((ps - posicao["entrada"]) / posicao["entrada"])
                    - posicao["usdt"] * TAXA * 2
                )
                capital += pnl
                operacoes.append(
                    {
                        "resultado": pnl,
                        "resultado_pct": round(
                            (ps - posicao["entrada"]) / posicao["entrada"] * 100, 2
                        ),
                        "tipo_saida": "STOP",
                        "saida_dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime(
                            "%d/%m/%Y %H:%M"
                        ),
                        "entrada_dt": posicao.get("dt", ""),
                        "preco_entrada": posicao["entrada"],
                        "preco_saida": round(ps, 2),
                    }
                )
                posicao = None
                saiu = True
            elif mx >= posicao["target"]:
                pt = posicao["target"] * (1 - SLIPPAGE)
                pnl = (
                    posicao["usdt"] * ((pt - posicao["entrada"]) / posicao["entrada"])
                    - posicao["usdt"] * TAXA * 2
                )
                capital += pnl
                operacoes.append(
                    {
                        "resultado": pnl,
                        "resultado_pct": round(
                            (pt - posicao["entrada"]) / posicao["entrada"] * 100, 2
                        ),
                        "tipo_saida": "TARGET",
                        "saida_dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime(
                            "%d/%m/%Y %H:%M"
                        ),
                        "entrada_dt": posicao.get("dt", ""),
                        "preco_entrada": posicao["entrada"],
                        "preco_saida": round(pt, 2),
                    }
                )
                posicao = None
                saiu = True

        # Verificar entrada com todos os filtros
        if posicao is None:
            t4h = tend_4h_em(i)
            ok_ema = preco > e20 > e50
            ok_rsi = RSI_MIN <= rsi_v <= RSI_MAX
            ok_atr = atr_v >= atr_med * ATR_MIN_RATIO
            ok_vol = vr >= VOL_MIN_RATIO
            ok_vwap = preco > vwap_v
            ok_mtf = t4h in ("ALTA", "LATERAL")
            ok_bb = bw_v >= bw_med * BW_MIN_RATIO

            # Contar filtros individualmente para análise
            if ok_ema:
                filtros_contagem["ema"] += 1
            if ok_rsi:
                filtros_contagem["rsi"] += 1
            if ok_atr:
                filtros_contagem["atr"] += 1
            if ok_vol:
                filtros_contagem["volume"] += 1
            if ok_vwap:
                filtros_contagem["vwap"] += 1
            if ok_mtf:
                filtros_contagem["mtf"] += 1
            if ok_bb:
                filtros_contagem["bollinger"] += 1

            if all([ok_ema, ok_rsi, ok_atr, ok_vol, ok_vwap, ok_mtf, ok_bb]):
                filtros_contagem["total"] += 1
                entrada = preco * (1 + SLIPPAGE)
                usdt = min(capital * 0.02 / STOP_PCT, capital)
                posicao = {
                    "entrada": entrada,
                    "stop": entrada * (1 - STOP_PCT),
                    "target": entrada * (1 + TARGET_PCT),
                    "usdt": usdt,
                    "dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime("%d/%m/%Y %H:%M"),
                }

    # Métricas
    if not operacoes:
        return {"erro": "Nenhuma operacao encontrada com os filtros aplicados."}

    total = len(operacoes)
    ganhos = [o for o in operacoes if o["resultado"] > 0]
    perdas = [o for o in operacoes if o["resultado"] <= 0]
    wrate = len(ganhos) / total * 100
    lucro = sum(o["resultado"] for o in ganhos)
    perda = abs(sum(o["resultado"] for o in perdas))
    pf = profit_factor(lucro, perda)
    retorno = (capital - capital_inicial) / capital_inicial * 100

    # Max Drawdown
    pico = capital_inicial
    cap = capital_inicial
    max_dd = 0
    for o in operacoes:
        cap += o["resultado"]
        if cap > pico:
            pico = cap
        dd = (pico - cap) / pico * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe
    rets = [o["resultado_pct"] for o in operacoes]
    sharpe = sharpe_ratio(rets)

    # Periodo do backtest (para Calmar) — mesmo formato de datetime ja usado
    dt_ini = datetime.strptime(operacoes[0]["entrada_dt"], "%d/%m/%Y %H:%M")
    dt_fim = datetime.strptime(operacoes[-1]["saida_dt"], "%d/%m/%Y %H:%M")
    dias_periodo = max((dt_fim - dt_ini).total_seconds() / 86400, 0)

    mg = sum(o["resultado_pct"] for o in ganhos) / len(ganhos) if ganhos else 0
    mp = abs(sum(o["resultado_pct"] for o in perdas) / len(perdas)) if perdas else 0
    exp = (wrate / 100 * mg) - ((1 - wrate / 100) * mp)

    return {
        "intervalo": f"{intervalo_entrada}+{intervalo_mtf}(MTF)",
        "total_trades": total,
        "win_rate_%": round(wrate, 1),
        "trades_ganhos": len(ganhos),
        "trades_perdas": len(perdas),
        "profit_factor": round(pf, 2),
        "retorno_total_%": round(retorno, 2),
        "capital_inicial": capital_inicial,
        "capital_final": round(capital, 2),
        "max_drawdown_%": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino_ratio(rets), 2),
        "calmar_ratio": round(calmar_ratio(retorno, max(max_dd, 0.0), dias_periodo), 2),
        "dsr": round(deflated_sharpe_ratio(rets, None), 4),
        "expectancia_%": round(exp, 2),
        "media_ganho_%": round(mg, 2),
        "media_perda_%": round(mp, 2),
        "filtros_disparados": filtros_contagem,
        "operacoes": operacoes,
    }


if __name__ == "__main__":
    from backtesting.motor import imprimir_relatorio

    print("\n[BACKTEST] Rodando estrategia OTIMIZADA (MTF+ATR+Vol+VWAP+BB)...\n")
    r = rodar("1h", "4h", 1000.0)
    if r:
        imprimir_relatorio(r)
        if "filtros_disparados" in r:
            print("\n  ANALISE DE FILTROS (quantas vezes cada um foi aprovado):")
            for nome, qtd in r["filtros_disparados"].items():
                print(f"    {nome:12s}: {qtd:4d} vezes")
