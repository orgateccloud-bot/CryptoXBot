"""
Otimizador de Hiperparametros — BotBinance
============================================
Grid Search sobre parametros da estrategia usando backtest.

Parametros otimizados:
  - STOP_PCT / TARGET_PCT (risco/retorno)
  - RSI_MIN / RSI_MAX (faixa de entrada)
  - ATR_MIN_RATIO (filtro de volatilidade)
  - VOL_MIN_RATIO (filtro de volume)
  - SCORE_OPERAR / SCORE_CHEIO (limiares do score)

Uso:
  python backtesting/otimizador.py
  python backtesting/otimizador.py --par ETHUSDT
  python backtesting/otimizador.py --rapido          (grid menor, mais rapido)
"""

import itertools
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicadores as ind
from backtesting import metricas
from backtesting.motor_ensemble import SLIPPAGE, TAXA, _adx, _score_backtest, carregar
from ml_filtro import extrair_features

DB_PATH = "data/btc_data.db"


def _rodar_com_params(
    f1h,
    m1h,
    n1h,
    v1h,
    ts1h,
    f4h,
    m4h,
    n4h,
    ema20,
    ema50,
    rsi14,
    atr14,
    volr,
    bw,
    vwap,
    adx_vals,
    ema20_4h,
    ema50_4h,
    params,
    capital_inicial=1000.0,
):
    """Roda backtest com um conjunto de parametros (rapido, sem ML)."""
    stop_pct = params["stop_pct"]
    target_pct = params["target_pct"]
    rsi_min = params["rsi_min"]
    rsi_max = params["rsi_max"]
    score_op = params["score_operar"]
    score_ch = params["score_cheio"]

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
    ganhos = 0
    perdas = 0
    total = 0
    pico = capital_inicial
    max_dd = 0
    rets = []

    for i in range(55, len(f1h)):
        preco = f1h[i]
        e20 = ema20[i]
        e50 = ema50[i]
        rsi_v = rsi14[i]
        atr_v = atr14[i]
        vr = volr[i]
        bw_v = bw[i]
        vwap_v = vwap[i]
        adx_v = adx_vals[i]

        if any(x is None for x in [rsi_v, atr_v, vr, bw_v, vwap_v]):
            continue

        atr_med = sum(x for x in atr14[max(0, i - 20) : i] if x) / max(
            1, len([x for x in atr14[max(0, i - 20) : i] if x])
        )
        bw_med = sum(x for x in bw[max(0, i - 20) : i] if x) / max(
            1, len([x for x in bw[max(0, i - 20) : i] if x])
        )
        atr_ratio = atr_v / atr_med if atr_med > 0 else 1.0

        # Saida
        if posicao:
            mn = n1h[i]
            mx = m1h[i]

            if mn <= posicao["stop"]:
                ps = posicao["stop"] * (1 - SLIPPAGE)
                pnl = (
                    posicao["usdt"] * ((ps - posicao["entrada"]) / posicao["entrada"])
                    - posicao["usdt"] * TAXA * 2
                )
                capital += pnl
                rets.append((ps - posicao["entrada"]) / posicao["entrada"] * 100)
                if pnl > 0:
                    ganhos += 1
                else:
                    perdas += 1
                total += 1
                posicao = None
            elif mx >= posicao["target"]:
                pt = posicao["target"] * (1 - SLIPPAGE)
                pnl = (
                    posicao["usdt"] * ((pt - posicao["entrada"]) / posicao["entrada"])
                    - posicao["usdt"] * TAXA * 2
                )
                capital += pnl
                rets.append((pt - posicao["entrada"]) / posicao["entrada"] * 100)
                if pnl > 0:
                    ganhos += 1
                else:
                    perdas += 1
                total += 1
                posicao = None

            if capital > pico:
                pico = capital
            dd = (pico - capital) / pico * 100 if pico > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Entrada
        if posicao is None:
            t4h = tend_4h_em(i)
            score, decisao, fator, _ = _score_backtest(
                preco,
                e20,
                e50,
                rsi_v,
                atr_v,
                atr_med,
                vr,
                bw_v,
                bw_med,
                vwap_v,
                t4h,
                adx_v,
                atr_ratio,
                None,
            )

            # Usar limiares customizados
            if score >= score_ch:
                fator = 1.0
            elif score >= score_op:
                fator = 0.5
            else:
                fator = 0.0

            if fator > 0 and rsi_min <= rsi_v <= rsi_max:
                entrada = preco * (1 + SLIPPAGE)
                usdt = min(capital * 0.02 / stop_pct, capital) * fator
                posicao = {
                    "entrada": entrada,
                    "stop": entrada * (1 - stop_pct),
                    "target": entrada * (1 + target_pct),
                    "usdt": usdt,
                }

    if total == 0:
        return None

    wrate = ganhos / total * 100
    retorno = (capital - capital_inicial) / capital_inicial * 100
    sharpe = metricas.sharpe_ratio(rets)

    return {
        "params": params,
        "total": total,
        "win_rate": round(wrate, 1),
        "retorno": round(retorno, 2),
        "max_dd": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "capital_final": round(capital, 2),
        "retornos_pct": rets,
    }


def grid_search(symbol="BTCUSDT", intervalo="1h", rapido=False):
    """Executa grid search sobre parametros."""
    k1h = carregar(symbol, intervalo)
    k4h = carregar(symbol, "4h")

    if len(k1h) < 100 or len(k4h) < 55:
        print(f"Dados insuficientes para {symbol}. Rode coletar_dados.py primeiro.")
        return []

    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]

    f4h = [r[4] for r in k4h]
    m4h = [r[2] for r in k4h]
    n4h = [r[3] for r in k4h]

    # Pre-computar indicadores (uma vez so)
    print("  Computando indicadores...")
    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = ind.atr(m1h, n1h, f1h, 14)
    volr = ind.volume_relativo(v1h, 20)
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bbu, bbm, bbl)
    vwap = ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20)
    adx_vals = _adx(m1h, n1h, f1h, 14)
    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    # Grid de parametros
    if rapido:
        grid = {
            "stop_pct": [0.015, 0.020, 0.025],
            "target_pct": [0.030, 0.040, 0.050],
            "rsi_min": [40, 42],
            "rsi_max": [60, 65],
            "score_operar": [55, 60],
            "score_cheio": [70, 75],
        }
    else:
        grid = {
            "stop_pct": [0.010, 0.015, 0.020, 0.025, 0.030],
            "target_pct": [0.020, 0.030, 0.040, 0.050, 0.060],
            "rsi_min": [38, 40, 42, 45],
            "rsi_max": [58, 60, 62, 65, 68],
            "score_operar": [50, 55, 60, 65],
            "score_cheio": [65, 70, 75, 80],
        }

    # Gerar todas as combinacoes
    keys = list(grid.keys())
    values = list(grid.values())
    combinacoes = list(itertools.product(*values))
    total_comb = len(combinacoes)

    print(f"  Grid: {total_comb} combinacoes para testar")
    print(f"  Dados: {len(k1h)} candles {symbol}/{intervalo}")

    resultados = []
    for idx, combo in enumerate(combinacoes):
        params = dict(zip(keys, combo))

        # Filtrar combinacoes invalidas
        if params["stop_pct"] >= params["target_pct"]:
            continue
        if params["rsi_min"] >= params["rsi_max"]:
            continue
        if params["score_operar"] >= params["score_cheio"]:
            continue

        r = _rodar_com_params(
            f1h,
            m1h,
            n1h,
            v1h,
            ts1h,
            f4h,
            m4h,
            n4h,
            ema20,
            ema50,
            rsi14,
            atr14,
            volr,
            bw,
            vwap,
            adx_vals,
            ema20_4h,
            ema50_4h,
            params,
        )

        if r and r["total"] >= 5:
            resultados.append(r)

        if (idx + 1) % 100 == 0:
            print(f"    {idx+1}/{total_comb} testados... ({len(resultados)} validos)")

    print(f"  Concluido: {len(resultados)} resultados validos de {total_comb} combinacoes")
    return resultados


def imprimir_top(resultados, top_n=10, ordenar_por="sharpe"):
    """Imprime os melhores resultados."""
    if not resultados:
        print("  Nenhum resultado encontrado.")
        return

    # Ordenar (sharpe e retorno favorecem, max_dd penaliza)
    if ordenar_por == "sharpe":
        resultados.sort(key=lambda x: x["sharpe"], reverse=True)
    elif ordenar_por == "retorno":
        resultados.sort(key=lambda x: x["retorno"], reverse=True)
    elif ordenar_por == "score":
        # Score composto: sharpe * retorno / max_dd
        resultados.sort(
            key=lambda x: x["sharpe"] * max(x["retorno"], 0.01) / max(x["max_dd"], 0.01),
            reverse=True,
        )
    else:
        resultados.sort(key=lambda x: x["sharpe"], reverse=True)

    print(f"\n{'='*80}")
    print(f"  TOP {min(top_n, len(resultados))} PARAMETROS (ordenado por {ordenar_por})")
    print(f"{'='*80}")
    print(
        f"  {'#':>2} {'Stop':>5} {'Target':>6} {'RSI':>7} {'ScoreOp':>7} {'ScoreFull':>9} "
        f"{'Trades':>6} {'WR%':>5} {'Ret%':>7} {'DD%':>5} {'Sharpe':>6}"
    )
    print(f"  {'-'*78}")

    for i, r in enumerate(resultados[:top_n]):
        p = r["params"]
        print(
            f"  {i+1:2d} "
            f"{p['stop_pct']*100:4.1f}% "
            f"{p['target_pct']*100:5.1f}% "
            f"{p['rsi_min']}-{p['rsi_max']:2d} "
            f"  {p['score_operar']:5d} "
            f"    {p['score_cheio']:5d} "
            f"{r['total']:6d} "
            f"{r['win_rate']:5.1f} "
            f"{r['retorno']:7.2f} "
            f"{r['max_dd']:5.2f} "
            f"{r['sharpe']:6.2f}"
        )

    # Melhor parametro
    best = resultados[0]

    n_trials = len(resultados)
    sharpes_trials_pt = [r["sharpe"] / (252**0.5) for r in resultados]  # DESANUALIZAR
    dsr_best = metricas.deflated_sharpe_ratio(best["retornos_pct"], sharpes_trials_pt)

    print(f"\n  MELHOR CONFIGURACAO:")
    for k, v in best["params"].items():
        if "pct" in k:
            print(f"    {k}: {v*100:.1f}%")
        else:
            print(f"    {k}: {v}")
    print(
        f"    Resultado: {best['total']} trades, WR {best['win_rate']:.1f}%, "
        f"Ret {best['retorno']:.2f}%, DD {best['max_dd']:.2f}%, Sharpe {best['sharpe']:.2f}"
    )
    print(f"  N trials válidos: {n_trials}")
    print(f"  DSR (melhor resultado, corrigido p/ multiple-testing): {dsr_best:.4f}")
    print(f"{'='*80}")

    return resultados[:top_n]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Grid Search de Hiperparametros")
    parser.add_argument("--par", default="BTCUSDT")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--rapido", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--ordenar", default="sharpe", choices=["sharpe", "retorno", "score"])
    args = parser.parse_args()

    print(f"\n[GRID SEARCH] {args.par} — Otimizando hiperparametros...\n")
    resultados = grid_search(args.par, args.intervalo, args.rapido)
    if resultados:
        imprimir_top(resultados, args.top, args.ordenar)
