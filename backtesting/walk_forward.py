"""
Walk-Forward Validation + Retreino Automatico — BotBinance
============================================================
Divide os dados em janelas de treino/teste sequenciais,
retreinando o ML a cada janela para simular uso real.

Janelas:
  [===TREINO===][=TESTE=]
       [===TREINO===][=TESTE=]
            [===TREINO===][=TESTE=]

Parametros:
  - Janela de treino: 500 candles (1h ~ 21 dias)
  - Janela de teste:  100 candles (1h ~ 4 dias)
  - Passo: 100 candles (avanca a janela)

Uso:
  python backtesting/walk_forward.py
  python backtesting/walk_forward.py --par ETHUSDT
  python backtesting/walk_forward.py --treino 720 --teste 168
"""

import os
import sqlite3
import statistics
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicadores as ind
from backtesting.metricas import (
    calmar_ratio,
    deflated_sharpe_ratio,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)
from backtesting.motor_ensemble import SLIPPAGE, TAXA, _adx
from ml_filtro import extrair_features

DB_PATH = "data/btc_data.db"

STOP_PCT = 0.020
TARGET_PCT = 0.040
ALVO_PCT = 0.015
JANELA_FUTURA = 8
ADX_TENDENCIA = 25
ATR_EXTREMO = 2.5


def carregar(symbol, intervalo):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT timestamp, abertura, maxima, minima, fechamento, volume
        FROM klines WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (symbol, intervalo),
    ).fetchall()
    conn.close()
    return rows


def walk_forward(
    symbol="BTCUSDT", intervalo="1h", janela_treino=500, janela_teste=100, capital_inicial=1000.0
):
    """
    Walk-forward validation com retreino do XGBoost a cada janela.
    """
    k1h = carregar(symbol, intervalo)
    k4h = carregar(symbol, "4h")

    if len(k1h) < janela_treino + janela_teste + 100:
        return {
            "erro": f"Dados insuficientes ({len(k1h)} candles). Precisa de {janela_treino + janela_teste + 100}."
        }

    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]

    f4h = [r[4] for r in k4h]
    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    # Pre-computar indicadores 1H
    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = ind.atr(m1h, n1h, f1h, 14)
    volr = ind.volume_relativo(v1h, 20)
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bbu, bbm, bbl)
    vwap = ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20)
    adx_vals = _adx(m1h, n1h, f1h, 14)

    def tend_4h_em(idx_1h):
        idx4 = min(idx_1h // 4, len(f4h) - 1)
        if idx4 >= len(ema20_4h):
            return "LATERAL"
        p = f4h[idx4]
        e20 = ema20_4h[idx4]
        e50 = ema50_4h[idx4]
        if p > e20 > e50:
            return "ALTA"
        if p < e20 < e50:
            return "BAIXA"
        return "LATERAL"

    # Walk-forward loop
    capital = capital_inicial
    todas_ops = []
    janelas = []
    posicao = None
    janela_num = 0

    inicio = max(55, 55)  # minimo para features
    fim_dados = len(f1h)

    current = inicio + janela_treino

    while current + janela_teste < fim_dados:
        janela_num += 1
        treino_inicio = current - janela_treino
        treino_fim = current
        teste_inicio = current
        teste_fim = min(current + janela_teste, fim_dados - JANELA_FUTURA)

        # --- Treinar XGBoost nesta janela ---
        from xgboost import XGBClassifier

        X_train, y_train = [], []
        for i in range(max(treino_inicio, 55), treino_fim - JANELA_FUTURA):
            feat = extrair_features(f1h, m1h, n1h, v1h, i)
            if feat is None:
                continue
            preco_futuro = max(f1h[i + 1 : i + JANELA_FUTURA + 1])
            label = 1 if (preco_futuro - f1h[i]) / f1h[i] >= ALVO_PCT else 0
            X_train.append(feat)
            y_train.append(label)

        modelo = None
        auc_janela = 0.0
        if len(X_train) > 50:
            X_tr = np.array(X_train)
            y_tr = np.array(y_train)
            ratio = (len(y_tr) - y_tr.sum()) / max(y_tr.sum(), 1)

            modelo = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                scale_pos_weight=ratio,
                eval_metric="logloss",
                verbosity=0,
            )
            modelo.fit(X_tr, y_tr)

            # AUC no treino (diagnostico)
            try:
                from sklearn.metrics import roc_auc_score

                y_prob_tr = modelo.predict_proba(X_tr)[:, 1]
                auc_janela = roc_auc_score(y_tr, y_prob_tr)
            except Exception:
                pass

        # --- Testar nesta janela ---
        ops_janela = 0
        ganhos_janela = 0

        for i in range(teste_inicio, teste_fim):
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
                    pnl_pct = (ps - posicao["entrada"]) / posicao["entrada"] * 100
                    todas_ops.append(
                        {
                            "resultado": pnl,
                            "resultado_pct": round(pnl_pct, 2),
                            "tipo_saida": "STOP",
                            "janela": janela_num,
                            "entrada_dt": posicao.get("dt", ""),
                            "saida_dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime(
                                "%d/%m/%Y %H:%M"
                            ),
                            "preco_entrada": posicao["entrada"],
                            "preco_saida": round(ps, 2),
                        }
                    )
                    ops_janela += 1
                    posicao = None
                elif mx >= posicao["target"]:
                    pt = posicao["target"] * (1 - SLIPPAGE)
                    pnl = (
                        posicao["usdt"] * ((pt - posicao["entrada"]) / posicao["entrada"])
                        - posicao["usdt"] * TAXA * 2
                    )
                    capital += pnl
                    pnl_pct = (pt - posicao["entrada"]) / posicao["entrada"] * 100
                    todas_ops.append(
                        {
                            "resultado": pnl,
                            "resultado_pct": round(pnl_pct, 2),
                            "tipo_saida": "TARGET",
                            "janela": janela_num,
                            "entrada_dt": posicao.get("dt", ""),
                            "saida_dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime(
                                "%d/%m/%Y %H:%M"
                            ),
                            "preco_entrada": posicao["entrada"],
                            "preco_saida": round(pt, 2),
                        }
                    )
                    ops_janela += 1
                    ganhos_janela += 1
                    posicao = None

            # Entrada
            if posicao is None:
                t4h = tend_4h_em(i)

                # ML prob
                ml_p = None
                if modelo:
                    feat = extrair_features(f1h, m1h, n1h, v1h, i)
                    if feat:
                        ml_p = float(modelo.predict_proba([feat])[0][1])

                # Score simplificado
                from backtesting.motor_ensemble import _score_backtest

                score, decisao, fator, _ = _score_backtest(
                    preco,
                    e20,
                    e50,
                    rsi_v,
                    atr_v,
                    atr_med,
                    vr,
                    bw_v,
                    sum(x for x in bw[max(0, i - 20) : i] if x)
                    / max(1, len([x for x in bw[max(0, i - 20) : i] if x])),
                    vwap_v,
                    t4h,
                    adx_v,
                    atr_ratio,
                    ml_p,
                )

                if fator > 0:
                    entrada = preco * (1 + SLIPPAGE)
                    usdt = min(capital * 0.02 / STOP_PCT, capital) * fator
                    posicao = {
                        "entrada": entrada,
                        "stop": entrada * (1 - STOP_PCT),
                        "target": entrada * (1 + TARGET_PCT),
                        "usdt": usdt,
                        "dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime("%d/%m/%Y %H:%M"),
                    }

        dt_ini = datetime.fromtimestamp(ts1h[treino_inicio] / 1000).strftime("%d/%m/%Y")
        dt_fim = datetime.fromtimestamp(ts1h[min(teste_fim, len(ts1h) - 1)] / 1000).strftime(
            "%d/%m/%Y"
        )

        janelas.append(
            {
                "janela": janela_num,
                "periodo": f"{dt_ini} - {dt_fim}",
                "treino_size": len(X_train),
                "auc_treino": round(auc_janela, 4),
                "trades": ops_janela,
                "ganhos": ganhos_janela,
                "capital": round(capital, 2),
            }
        )

        print(
            f"  Janela {janela_num:2d}: {dt_ini}-{dt_fim} | "
            f"Treino: {len(X_train)} amostras, AUC: {auc_janela:.4f} | "
            f"Trades: {ops_janela} | Capital: ${capital:,.2f}"
        )

        current += janela_teste

    # Metricas finais
    if not todas_ops:
        return {"erro": "Nenhuma operacao no walk-forward."}

    total = len(todas_ops)
    ganhos = [o for o in todas_ops if o["resultado"] > 0]
    perdas = [o for o in todas_ops if o["resultado"] <= 0]
    wrate = len(ganhos) / total * 100
    lucro = sum(o["resultado"] for o in ganhos)
    perda = abs(sum(o["resultado"] for o in perdas))
    pf = profit_factor(lucro, perda)
    retorno = (capital - capital_inicial) / capital_inicial * 100

    pico = capital_inicial
    cap = capital_inicial
    max_dd = 0
    for o in todas_ops:
        cap += o["resultado"]
        if cap > pico:
            pico = cap
        dd = (pico - cap) / pico * 100 if pico > 0 else 0
        if dd > max_dd:
            max_dd = dd

    rets = [o["resultado_pct"] for o in todas_ops]
    sharpe = sharpe_ratio(rets)

    # Periodo do backtest (para Calmar) — mesmo formato de datetime ja usado
    dt_ini = datetime.strptime(todas_ops[0]["entrada_dt"], "%d/%m/%Y %H:%M")
    dt_fim = datetime.strptime(todas_ops[-1]["saida_dt"], "%d/%m/%Y %H:%M")
    dias_periodo = max((dt_fim - dt_ini).total_seconds() / 86400, 0)

    mg = sum(o["resultado_pct"] for o in ganhos) / len(ganhos) if ganhos else 0
    mp = abs(sum(o["resultado_pct"] for o in perdas) / len(perdas)) if perdas else 0

    return {
        "symbol": symbol,
        "intervalo": intervalo,
        "janela_treino": janela_treino,
        "janela_teste": janela_teste,
        "total_janelas": janela_num,
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
        "media_ganho_%": round(mg, 2),
        "media_perda_%": round(mp, 2),
        "janelas": janelas,
        "operacoes": todas_ops,
    }


def imprimir_relatorio(r):
    if "erro" in r:
        print(f"ERRO: {r['erro']}")
        return

    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD VALIDATION — {r['symbol']} [{r['intervalo']}]")
    print(f"{'='*65}")
    print(
        f"  Janelas:  {r['total_janelas']} (treino: {r['janela_treino']} / teste: {r['janela_teste']})"
    )
    print(f"  Trades:   {r['total_trades']} ({r['trades_ganhos']}W / {r['trades_perdas']}L)")
    print()
    print(f"  Win Rate:      {r['win_rate_%']:6.1f}%")
    print(f"  Sharpe Ratio:  {r['sharpe_ratio']:6.2f}")
    print(f"  Max Drawdown:  {r['max_drawdown_%']:6.2f}%")
    print(f"  Sortino Ratio: {r.get('sortino_ratio', 0):6.2f}")
    print(f"  Calmar Ratio:  {r.get('calmar_ratio', 0):6.2f}")
    print(
        f"  DSR (PSR, sem correção de multiple-testing — 1 único backtest): {r.get('dsr', 0):6.4f}"
    )
    print(f"  Retorno Total: {r['retorno_total_%']:6.2f}%")
    print(f"  Capital:       ${r['capital_inicial']:,.2f} → ${r['capital_final']:,.2f}")
    print()

    # Tabela de janelas
    print(f"  {'Jan':>3} {'Periodo':>25} {'Treino':>6} {'AUC':>6} {'Trades':>6} {'Capital':>10}")
    print(f"  {'-'*60}")
    for j in r["janelas"]:
        print(
            f"  {j['janela']:3d} {j['periodo']:>25} {j['treino_size']:6d} "
            f"{j['auc_treino']:6.4f} {j['trades']:6d} ${j['capital']:>9,.2f}"
        )

    # AUC medio
    aucs = [j["auc_treino"] for j in r["janelas"] if j["auc_treino"] > 0]
    if aucs:
        print(
            f"\n  AUC medio: {statistics.mean(aucs):.4f} "
            f"(min: {min(aucs):.4f}, max: {max(aucs):.4f})"
        )

    # Veredicto
    print(f"\n{'-'*65}")
    problemas = []
    if r["sharpe_ratio"] < 0.5:
        problemas.append("Sharpe baixo")
    if r["max_drawdown_%"] > 20:
        problemas.append("Drawdown alto")
    if r["win_rate_%"] < 45:
        problemas.append("Win rate baixo")
    if aucs and statistics.mean(aucs) < 0.55:
        problemas.append("AUC ML fraco")

    if not problemas:
        print("  VEREDITO: Walk-forward APROVADO — Modelo generaliza bem")
    elif len(problemas) <= 1:
        print(f"  VEREDITO: Walk-forward MODERADO — Atencao: {', '.join(problemas)}")
    else:
        print(f"  VEREDITO: Walk-forward REPROVADO — Problemas: {', '.join(problemas)}")
    print(f"{'='*65}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Walk-Forward Validation")
    parser.add_argument("--par", default="BTCUSDT")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--treino", type=int, default=500)
    parser.add_argument("--teste", type=int, default=100)
    parser.add_argument("--capital", type=float, default=1000.0)
    args = parser.parse_args()

    print(f"\n[WALK-FORWARD] {args.par} — Validacao com retreino automatico...\n")
    r = walk_forward(args.par, args.intervalo, args.treino, args.teste, args.capital)
    if r:
        imprimir_relatorio(r)
