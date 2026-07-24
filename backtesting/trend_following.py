"""
Trend-following canônico (Donchian breakout, long-only) — redesenho estrutural
==============================================================================
Pré-registrado em research/METODOLOGIA_TREND.md. NÃO é a estratégia velha
(barreira 2:1, reprovada): aqui não há alvo fixo — o M-exit é o trailing stop
que deixa o ganho correr e corta a perda (skew positivo do trend-following).

Sistema (parâmetros canônicos NÃO ajustados — Turtle System 1):
  - Entrada: close rompe acima do maior close dos N candles anteriores.
  - Saída:   close cai abaixo do menor close dos M candles anteriores.
  - Long-only (restrição do executor), 1 posição por vez.
  - N=20 / M=10, timeframe 4h, sizing por risco 2% ao stop inicial.
  - Custos do gate: 0.10%/lado taxa (taker) + 0.05%/lado slippage.

Funções puras (arrays) no topo — testáveis sem banco.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtesting.metricas import profit_factor  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "btc_data.db")

N_ENTRADA = 20
M_SAIDA = 10
TAXA = 0.001       # por lado (spot taker)
SLIPPAGE = 0.0005  # por lado
RISCO_FRAC = 0.02
HOLDOUT_FRAC = 0.35
ATIVOS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


# ══════════════════════════════════════════════════════════════
# Núcleo (puro)
# ══════════════════════════════════════════════════════════════


def donchian_niveis(closes: np.ndarray, periodo: int) -> tuple[np.ndarray, np.ndarray]:
    """Canal de Donchian CAUSAL: níveis[i] = max/min dos `periodo` closes
    ANTERIORES a i (não inclui i). NaN para i < periodo."""
    c = np.asarray(closes, dtype=float)
    n = len(c)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(periodo, n):
        janela = c[i - periodo : i]
        upper[i] = janela.max()
        lower[i] = janela.min()
    return upper, lower


def simular_trend(
    closes: np.ndarray, N=N_ENTRADA, M=M_SAIDA,
    capital_inicial=1000.0, taxa=TAXA, slippage=SLIPPAGE, risco_frac=RISCO_FRAC,
) -> dict:
    """Backtest long-only Donchian. Decisão sempre no CLOSE do candle i, usando
    só dados <= i (causal). Retorna métricas + lista de trades (retorno líquido
    % por trade sobre o capital no momento)."""
    c = np.asarray(closes, dtype=float)
    if len(c) < max(N, M) + 2:
        return {"n_trades": 0, "trades": [], "retorno_total_pct": 0.0,
                "max_drawdown_pct": 0.0, "bh_retorno_pct": 0.0, "bh_max_dd_pct": 0.0,
                "sem_dados": True}
    up_entrada, _ = donchian_niveis(c, N)
    _, low_saida = donchian_niveis(c, M)

    capital = capital_inicial
    pos = None
    trades = []
    equity_curve = [capital_inicial]

    inicio = max(N, M)
    for i in range(inicio, len(c)):
        if pos is None:
            # entrada: rompeu o topo dos N anteriores
            if not np.isnan(up_entrada[i]) and c[i] > up_entrada[i]:
                entrada = c[i] * (1 + slippage)
                stop = low_saida[i]  # Donchian-M inferior no momento da entrada
                risco = (entrada - stop) / entrada
                if risco <= 0:
                    continue  # stop acima da entrada (não deveria) — pula
                notional = min(risco_frac * capital / risco, capital)
                pos = {"entrada": entrada, "notional": notional,
                       "cap_antes": capital, "risco": risco}
        else:
            # saída: rompeu o fundo dos M anteriores
            if not np.isnan(low_saida[i]) and c[i] < low_saida[i]:
                saida = c[i] * (1 - slippage)
                bruto = pos["notional"] * (saida - pos["entrada"]) / pos["entrada"]
                fee = pos["notional"] * taxa * 2
                pnl = bruto - fee
                capital += pnl
                trades.append({
                    "ret_bruto_pct": (saida - pos["entrada"]) / pos["entrada"] * 100,
                    "pnl": pnl,
                    "ret_capital_pct": pnl / pos["cap_antes"] * 100,
                })
                equity_curve.append(capital)
                pos = None

    # censura final: fecha a mercado no último close
    if pos is not None:
        saida = c[-1] * (1 - slippage)
        bruto = pos["notional"] * (saida - pos["entrada"]) / pos["entrada"]
        pnl = bruto - pos["notional"] * taxa * 2
        capital += pnl
        trades.append({
            "ret_bruto_pct": (saida - pos["entrada"]) / pos["entrada"] * 100,
            "pnl": pnl, "ret_capital_pct": pnl / pos["cap_antes"] * 100,
        })
        equity_curve.append(capital)

    return _metricas(trades, equity_curve, capital_inicial, capital, c)


def _metricas(trades, equity_curve, capital_inicial, capital_final, closes) -> dict:
    # buy-and-hold do período (independe de trades — sempre computado)
    bh_ret = (closes[-1] / closes[0] - 1) * 100
    pico_p = np.maximum.accumulate(closes)
    bh_dd = float(np.max((pico_p - closes) / pico_p * 100))
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "trades": [], "retorno_total_pct": 0.0,
                "max_drawdown_pct": 0.0, "bh_retorno_pct": bh_ret, "bh_max_dd_pct": bh_dd}
    ganhos = [t for t in trades if t["pnl"] > 0]
    perdas = [t for t in trades if t["pnl"] <= 0]
    lucro = sum(t["pnl"] for t in ganhos)
    perda = abs(sum(t["pnl"] for t in perdas))
    avg_ganho = np.mean([t["ret_capital_pct"] for t in ganhos]) if ganhos else 0.0
    avg_perda = abs(np.mean([t["ret_capital_pct"] for t in perdas])) if perdas else 0.0
    eq = np.array(equity_curve)
    pico = np.maximum.accumulate(eq)
    max_dd = float(np.max((pico - eq) / pico * 100)) if len(eq) else 0.0
    # buy-and-hold do mesmo período
    bh_ret = (closes[-1] / closes[0] - 1) * 100
    pico_p = np.maximum.accumulate(closes)
    bh_dd = float(np.max((pico_p - closes) / pico_p * 100))
    return {
        "n_trades": n,
        "win_rate": len(ganhos) / n * 100,
        "profit_factor": profit_factor(lucro, perda),
        "expectancy_net_pct": float(np.mean([t["ret_capital_pct"] for t in trades])),
        "payoff_ratio": (avg_ganho / avg_perda) if avg_perda > 0 else float("inf"),
        "avg_ganho_pct": float(avg_ganho),
        "avg_perda_pct": float(avg_perda),
        "retorno_total_pct": (capital_final - capital_inicial) / capital_inicial * 100,
        "max_drawdown_pct": max_dd,
        "bh_retorno_pct": bh_ret,
        "bh_max_dd_pct": bh_dd,
        "trades": trades,
    }


# ══════════════════════════════════════════════════════════════
# I/O + runner de pesquisa (só porção de pesquisa)
# ══════════════════════════════════════════════════════════════


def carregar_closes(symbol, intervalo="4h"):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT fechamento FROM klines WHERE symbol=? AND intervalo=? ORDER BY timestamp ASC",
        (symbol, intervalo),
    ).fetchall()
    conn.close()
    return np.array([r[0] for r in rows], dtype=float)


def porcao_pesquisa(closes):
    return closes[: int(len(closes) * (1 - HOLDOUT_FRAC))]


def rodar_pesquisa(intervalo="4h") -> dict:
    por_ativo = {}
    trades_pool = []
    for sym in ATIVOS:
        c = porcao_pesquisa(carregar_closes(sym, intervalo))
        r = simular_trend(c)
        por_ativo[sym] = r
        for t in r.get("trades", []):
            trades_pool.append(t["ret_capital_pct"])
    return {"por_ativo": por_ativo, "pool": _metricas_pool(trades_pool, por_ativo)}


def _metricas_pool(rets_pct, por_ativo) -> dict:
    n = len(rets_pct)
    if n == 0:
        return {"n_trades": 0}
    r = np.array(rets_pct)
    ganhos = r[r > 0]
    perdas = r[r <= 0]
    pf = ganhos.sum() / abs(perdas.sum()) if len(perdas) and perdas.sum() != 0 else float("inf")
    payoff = (ganhos.mean() / abs(perdas.mean())) if len(perdas) else float("inf")
    # média equal-weight de retorno e DD vs B&H
    ret_med = np.mean([por_ativo[s]["retorno_total_pct"] for s in por_ativo if por_ativo[s]["n_trades"]])
    dd_med = np.mean([por_ativo[s]["max_drawdown_pct"] for s in por_ativo if por_ativo[s]["n_trades"]])
    bh_ret_med = np.mean([por_ativo[s]["bh_retorno_pct"] for s in por_ativo])
    bh_dd_med = np.mean([por_ativo[s]["bh_max_dd_pct"] for s in por_ativo])
    return {
        "n_trades": n,
        "win_rate": float((r > 0).mean() * 100),
        "profit_factor": float(pf),
        "payoff_ratio": float(payoff),
        "expectancy_net_pct": float(r.mean()),
        "ret_medio_pct": float(ret_med),
        "dd_medio_pct": float(dd_med),
        "bh_ret_medio_pct": float(bh_ret_med),
        "bh_dd_medio_pct": float(bh_dd_med),
    }


def imprimir(res):
    print("=" * 72)
    print("  TREND-FOLLOWING (Donchian 20/10, long-only, 4h) — PESQUISA (hold-out intocado)")
    print("=" * 72)
    print(f"\n  {'ativo':<9} {'trades':>6} {'win%':>6} {'PF':>6} {'payoff':>7} "
          f"{'exp%/tr':>8} {'ret%':>8} {'DD%':>6} {'B&H ret%':>9} {'B&H DD%':>8}")
    for sym, r in res["por_ativo"].items():
        if not r["n_trades"]:
            print(f"  {sym:<9} {'0 trades':>6}")
            continue
        print(f"  {sym:<9} {r['n_trades']:>6} {r['win_rate']:>6.1f} {r['profit_factor']:>6.2f} "
              f"{r['payoff_ratio']:>7.2f} {r['expectancy_net_pct']:>+8.3f} {r['retorno_total_pct']:>+8.2f} "
              f"{r['max_drawdown_pct']:>6.1f} {r['bh_retorno_pct']:>+9.2f} {r['bh_max_dd_pct']:>8.1f}")

    p = res["pool"]
    print("\n  POOLED (BTC+ETH+SOL — decisão primária):")
    if not p["n_trades"]:
        print("    0 trades.")
        return
    print(f"    n={p['n_trades']} | win {p['win_rate']:.1f}% | PF {p['profit_factor']:.2f} | "
          f"payoff {p['payoff_ratio']:.2f} | exp {p['expectancy_net_pct']:+.3f}%/trade")
    print(f"    retorno médio {p['ret_medio_pct']:+.2f}% (DD {p['dd_medio_pct']:.1f}%) vs "
          f"B&H {p['bh_ret_medio_pct']:+.2f}% (DD {p['bh_dd_medio_pct']:.1f}%)")

    # regra de decisão pré-registrada
    print("\n" + "-" * 72)
    c1 = p["expectancy_net_pct"] > 0
    c2 = p["profit_factor"] > 1.3
    c3 = p["payoff_ratio"] > 1.5
    ratio_estrat = p["ret_medio_pct"] / p["dd_medio_pct"] if p["dd_medio_pct"] > 0 else 0
    ratio_bh = p["bh_ret_medio_pct"] / p["bh_dd_medio_pct"] if p["bh_dd_medio_pct"] > 0 else 0
    c4 = (p["ret_medio_pct"] >= p["bh_ret_medio_pct"]) or (ratio_estrat >= ratio_bh)
    c5 = p["n_trades"] >= 30
    print("  Regra de decisão (METODOLOGIA_TREND.md, pooled):")
    print(f"    [{'x' if c1 else ' '}] expectância líquida > 0        ({p['expectancy_net_pct']:+.3f}%)")
    print(f"    [{'x' if c2 else ' '}] profit factor > 1.3            ({p['profit_factor']:.2f})")
    print(f"    [{'x' if c3 else ' '}] payoff ratio > 1.5             ({p['payoff_ratio']:.2f})")
    print(f"    [{'x' if c4 else ' '}] >= buy-and-hold risk-adjusted  (estrat {ratio_estrat:.2f} vs B&H {ratio_bh:.2f})")
    print(f"    [{'x' if c5 else ' '}] >= 30 trades pooled            ({p['n_trades']})")
    if all([c1, c2, c3, c4, c5]):
        print("\n  >> HÁ EDGE CANDIDATO — prosseguir ao hold-out (uso único).")
    else:
        print("\n  >> Não passou. Ver METODOLOGIA_TREND.md p/ próximos passos (sem ajuste de parâmetro).")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(description="Trend-following canônico — pesquisa")
    ap.add_argument("--intervalo", default="4h")
    args = ap.parse_args()
    imprimir(rodar_pesquisa(args.intervalo))


if __name__ == "__main__":
    main()
