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
# Rodada 1 (4h, primário — FALHOU): BTC/ETH/SOL.
# Rodada 2 (1d, primário — cesta congelada em METODOLOGIA_TREND.md antes da coleta):
ATIVOS_4H = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
ATIVOS_1D = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "LINKUSDT")


# ══════════════════════════════════════════════════════════════
# Núcleo (puro)
# ══════════════════════════════════════════════════════════════


def _ano_de(ts, i) -> int | None:
    """Ano-calendário (UTC) do candle i, se timestamps foram fornecidos."""
    if ts is None or i >= len(ts):
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(ts[i]) / 1000, tz=timezone.utc).year


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
    ts: np.ndarray | None = None,
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
                    "ano": _ano_de(ts, i),
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
            "ano": _ano_de(ts, len(c) - 1),
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
    """Closes + timestamps (ts usado para o relatório de PnL por ano)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT timestamp, fechamento FROM klines WHERE symbol=? AND intervalo=? ORDER BY timestamp ASC",
        (symbol, intervalo),
    ).fetchall()
    conn.close()
    ts = np.array([r[0] for r in rows], dtype=np.int64)
    c = np.array([r[1] for r in rows], dtype=float)
    return ts, c


def porcao_pesquisa(arr):
    return arr[: int(len(arr) * (1 - HOLDOUT_FRAC))]


def rodar_pesquisa(intervalo="4h", holdout=False) -> dict:
    """holdout=False -> porção de pesquisa (primeiros 65%). holdout=True ->
    últimos 35% (USO ÚNICO, só via avaliar_holdout_trend)."""
    ativos = ATIVOS_1D if intervalo == "1d" else ATIVOS_4H
    por_ativo = {}
    trades_pool = []
    pnl_por_ano: dict[int, float] = {}
    pnl_por_ativo: dict[str, float] = {}
    for sym in ativos:
        ts, c = carregar_closes(sym, intervalo)
        if holdout:
            corte = int(len(c) * (1 - HOLDOUT_FRAC))
            ts, c = ts[corte:], c[corte:]
        else:
            ts, c = porcao_pesquisa(ts), porcao_pesquisa(c)
        r = simular_trend(c, ts=ts)
        por_ativo[sym] = r
        pnl_por_ativo[sym] = sum(t["pnl"] for t in r.get("trades", []))
        for t in r.get("trades", []):
            trades_pool.append(t["ret_capital_pct"])
            ano = t.get("ano")
            if ano:
                pnl_por_ano[ano] = pnl_por_ano.get(ano, 0.0) + t["pnl"]
    return {"por_ativo": por_ativo, "pool": _metricas_pool(trades_pool, por_ativo),
            "pnl_por_ano": pnl_por_ano, "pnl_por_ativo": pnl_por_ativo,
            "intervalo": intervalo, "holdout": holdout}


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


def analise_por_regime(intervalo="1d", holdout=False) -> dict:
    """Rodada 3, critério B: retorno da estratégia vs buy-and-hold POR
    ANO-CALENDÁRIO, com o regime classificado pelo B&H equal-weighted da cesta
    (não pela performance da estratégia). Testa a proposta de valor real do
    trend-following: capturar parte do bull, evitar a maior parte do bear.

    Retorno da estratégia no ano = PnL do ano / capital inicial (base fixa de
    1000 por ativo, medida consistente entre ativos e anos)."""
    ativos = ATIVOS_1D if intervalo == "1d" else ATIVOS_4H
    strat_por_ano: dict[int, list[float]] = {}
    bh_por_ano: dict[int, list[float]] = {}

    for sym in ativos:
        ts, c = carregar_closes(sym, intervalo)
        if holdout:
            corte = int(len(c) * (1 - HOLDOUT_FRAC))
            ts, c = ts[corte:], c[corte:]
        else:
            ts, c = porcao_pesquisa(ts), porcao_pesquisa(c)
        r = simular_trend(c, ts=ts)
        pnl_ano: dict[int, float] = {}
        for t in r.get("trades", []):
            a = t.get("ano")
            if a:
                pnl_ano[a] = pnl_ano.get(a, 0.0) + t["pnl"]
        anos = np.array([_ano_de(ts, i) for i in range(len(ts))])
        for a in sorted({int(x) for x in anos if x}):
            mask = anos == a
            if mask.sum() < 20:  # ano com poucos candles (borda) — ignora
                continue
            cc = c[mask]
            bh_por_ano.setdefault(a, []).append((cc[-1] / cc[0] - 1) * 100)
            strat_por_ano.setdefault(a, []).append(pnl_ano.get(a, 0.0) / 1000 * 100)

    linhas = []
    for a in sorted(bh_por_ano):
        bh = float(np.mean(bh_por_ano[a]))
        st = float(np.mean(strat_por_ano.get(a, [0.0])))
        regime = "BEAR" if bh < -20 else ("BULL" if bh > 20 else "LATERAL")
        captura = (st / bh) if (regime == "BULL" and bh > 0) else None
        linhas.append({"ano": a, "regime": regime, "bh_pct": bh, "strat_pct": st,
                       "captura": captura})
    return {"linhas": linhas, "holdout": holdout, "intervalo": intervalo}


def imprimir_regime(reg):
    linhas = reg["linhas"]
    print("\n" + "=" * 72)
    print("  RODADA 3 — CRITÉRIO B: estratégia vs buy-and-hold POR REGIME")
    print("  (regime classificado pelo B&H equal-weighted da cesta)")
    print("=" * 72)
    print(f"\n  {'ano':>5} {'regime':<8} {'B&H %':>10} {'estrat %':>10} {'captura':>9}")
    for L in linhas:
        cap = f"{L['captura']:.0%}" if L["captura"] is not None else "—"
        print(f"  {L['ano']:>5} {L['regime']:<8} {L['bh_pct']:>+10.1f} "
              f"{L['strat_pct']:>+10.1f} {cap:>9}")

    bears = [L for L in linhas if L["regime"] == "BEAR"]
    bulls = [L for L in linhas if L["regime"] == "BULL"]
    print("\n" + "-" * 72)
    print("  Critérios da Rodada 3 (pré-registrados em METODOLOGIA_TREND.md):")

    # 1. proteção no bear
    if bears:
        ok_bear = all(L["strat_pct"] > L["bh_pct"] for L in bears)
        det = ", ".join(f"{L['ano']}: {L['strat_pct']:+.1f}% vs {L['bh_pct']:+.1f}%" for L in bears)
        print(f"    [{'x' if ok_bear else ' '}] proteção em TODO bear         ({det})")
    else:
        ok_bear = False
        print("    [ ] proteção no bear           (NENHUM ano BEAR na janela — não testável)")

    # 2. participação no bull
    caps = [L["captura"] for L in bulls if L["captura"] is not None]
    if caps:
        cap_med = float(np.mean(caps))
        ok_bull = cap_med >= 0.30
        print(f"    [{'x' if ok_bull else ' '}] captura média no bull >= 30%  "
              f"({cap_med:.2%} em {len(caps)} anos bull; por ano: "
              f"{', '.join(f'{c:.1%}' for c in caps)})")
    else:
        ok_bull = False
        cap_med = 0.0
        print("    [ ] captura no bull            (nenhum ano BULL)")

    # 3. sem ano catastrófico
    pior = min((L["strat_pct"] for L in linhas), default=0.0)
    ok_cat = pior > -25.0
    print(f"    [{'x' if ok_cat else ' '}] nenhum ano < -25%             (pior ano: {pior:+.1f}%)")

    if ok_bear and ok_bull and ok_cat:
        print("\n  >> CRITÉRIO B PASSOU — prosseguir ao hold-out (USO ÚNICO).")
    else:
        print("\n  >> CRITÉRIO B NÃO PASSOU — FAIL definitivo (sem Rodada 4, pré-registrado).")
    print("=" * 72)
    return {"ok_bear": ok_bear, "ok_bull": ok_bull, "ok_cat": ok_cat,
            "captura_media": cap_med, "pior_ano": pior}


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

    # Anti-fragilidade (pré-registrado na Rodada 2): concentração de lucro
    ano_txt = conc_ano = conc_ativo = None
    if res.get("pnl_por_ano"):
        anos = {a: v for a, v in sorted(res["pnl_por_ano"].items())}
        total_pos = sum(v for v in anos.values() if v > 0)
        print("\n  PnL por ano-calendário (anti-fragilidade):")
        ano_txt = "  ".join(f"{a}:{v:+.0f}" for a, v in anos.items())
        print(f"    {ano_txt}")
        if total_pos > 0:
            conc_ano = max((v for v in anos.values()), default=0) / total_pos
            print(f"    maior ano = {conc_ano:.0%} de todo o lucro bruto positivo")
    if res.get("pnl_por_ativo"):
        pa = res["pnl_por_ativo"]
        total_pos_a = sum(v for v in pa.values() if v > 0)
        print("  PnL por ativo:")
        print("    " + "  ".join(f"{s.replace('USDT',''):>5}:{v:+.0f}" for s, v in pa.items()))
        if total_pos_a > 0:
            conc_ativo = max(pa.values()) / total_pos_a
            print(f"    maior ativo = {conc_ativo:.0%} de todo o lucro bruto positivo")

    # regra de decisão pré-registrada
    print("\n" + "-" * 72)
    rodada2 = res.get("intervalo") == "1d"
    piso_trades = 100 if rodada2 else 30
    c1 = p["expectancy_net_pct"] > 0
    c2 = p["profit_factor"] > 1.3
    c3 = p["payoff_ratio"] > 1.5
    ratio_estrat = p["ret_medio_pct"] / p["dd_medio_pct"] if p["dd_medio_pct"] > 0 else 0
    ratio_bh = p["bh_ret_medio_pct"] / p["bh_dd_medio_pct"] if p["bh_dd_medio_pct"] > 0 else 0
    c4 = (p["ret_medio_pct"] >= p["bh_ret_medio_pct"]) or (ratio_estrat >= ratio_bh)
    c5 = p["n_trades"] >= piso_trades
    rodada = "RODADA 2 (1d, PRIMÁRIA)" if rodada2 else "RODADA 1 (4h)"
    print(f"  Regra de decisão — {rodada}, pooled:")
    print(f"    [{'x' if c1 else ' '}] expectância líquida > 0        ({p['expectancy_net_pct']:+.3f}%)")
    print(f"    [{'x' if c2 else ' '}] profit factor > 1.3            ({p['profit_factor']:.2f})")
    print(f"    [{'x' if c3 else ' '}] payoff ratio > 1.5             ({p['payoff_ratio']:.2f})")
    print(f"    [{'x' if c4 else ' '}] >= buy-and-hold risk-adjusted  (estrat {ratio_estrat:.2f} vs B&H {ratio_bh:.2f})")
    print(f"    [{'x' if c5 else ' '}] >= {piso_trades} trades pooled           ({p['n_trades']})")
    frag = []
    if conc_ano is not None and conc_ano > 0.70:
        frag.append(f"um único ano = {conc_ano:.0%} do lucro")
    if conc_ativo is not None and conc_ativo > 0.70:
        frag.append(f"um único ativo = {conc_ativo:.0%} do lucro")
    if rodada2:
        print(f"    [{'!' if frag else 'x'}] robustez de regime            "
              f"({'FRÁGIL: ' + '; '.join(frag) if frag else 'lucro distribuído'})")
    if all([c1, c2, c3, c4, c5]) and not frag:
        print("\n  >> PASSOU — prosseguir ao hold-out (USO ÚNICO).")
    elif all([c1, c2, c3, c4, c5]) and frag:
        print("\n  >> Critérios numéricos OK mas FRÁGIL (concentração) — não aprovar (pré-registrado).")
    else:
        print("\n  >> Não passou. Ver METODOLOGIA_TREND.md p/ próximos passos (sem ajuste de parâmetro).")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(description="Trend-following canônico — pesquisa")
    ap.add_argument("--intervalo", default="4h")
    ap.add_argument("--regime", action="store_true",
                    help="Rodada 3 critério B: análise por regime (ano-calendário)")
    args = ap.parse_args()
    if args.regime:
        imprimir_regime(analise_por_regime(args.intervalo))
    else:
        imprimir(rodar_pesquisa(args.intervalo))


if __name__ == "__main__":
    main()
