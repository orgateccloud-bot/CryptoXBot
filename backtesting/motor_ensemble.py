"""
Backtesting com Score Unificado + Ensemble ML
===============================================
Motor que replica a logica real da estrategia otimizada no backtest:
  - Score ponderado 0-100
  - Regime de mercado (ADX + EMA alignment)
  - ML Ensemble simulado (treina/avalia walk-forward)
  - Suportes para stop inteligente

Uso:
  python backtesting/motor_ensemble.py
  python backtesting/motor_ensemble.py --intervalo 1h --capital 1000
  python backtesting/motor_ensemble.py --par ETHUSDT
"""

import sqlite3
import argparse
import statistics
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicadores as ind
from ml_filtro import extrair_features
from config.params_pares import get_params

DB_PATH = "data/btc_data.db"

TAXA        = 0.0004
SLIPPAGE    = 0.0005
ATR_MIN_RATIO = 0.5
VOL_MIN_RATIO = 1.1

# ADX para regime
ADX_TENDENCIA = 25
ATR_EXTREMO   = 2.5


def _adx(maximas, minimas, fechamentos, periodo=14):
    """ADX simplificado para backtest."""
    n = len(fechamentos)
    if n < periodo + 2:
        return [0.0] * n

    tr_list, dm_plus, dm_minus = [], [], []
    for i in range(1, n):
        h, l, pc = maximas[i], minimas[i], fechamentos[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
        up   = maximas[i] - maximas[i - 1]
        down = minimas[i - 1] - minimas[i]
        dm_plus.append(up   if up   > down and up   > 0 else 0)
        dm_minus.append(down if down > up   and down > 0 else 0)

    def smooth(data, p):
        result = [sum(data[:p])]
        for i in range(p, len(data)):
            result.append(result[-1] - result[-1] / p + data[i])
        return result

    atr_s = smooth(tr_list, periodo)
    dmp_s = smooth(dm_plus, periodo)
    dmm_s = smooth(dm_minus, periodo)

    di_plus  = [100 * dmp_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]
    di_minus = [100 * dmm_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]

    dx = []
    for i in range(len(di_plus)):
        soma = di_plus[i] + di_minus[i]
        dx.append(100 * abs(di_plus[i] - di_minus[i]) / soma if soma > 0 else 0)

    adx = []
    if len(dx) >= periodo:
        adx.append(sum(dx[:periodo]) / periodo)
        for i in range(periodo, len(dx)):
            adx.append((adx[-1] * (periodo - 1) + dx[i]) / periodo)

    pad = [0.0] * (n - len(adx))
    return pad + adx


def _score_backtest(preco, ema20, ema50, rsi_v, atr_v, atr_med, vol_rel, bw_v, bw_med,
                    vwap_v, tend_4h, adx_v, atr_ratio, ml_prob=None,
                    rsi_min=42, rsi_max=60, score_operar=60, score_cheio=70):
    """Calcula score simplificado para backtest (sem API calls)."""
    scores = {}

    # Regime (25%)
    if atr_ratio > ATR_EXTREMO:
        scores["regime"] = 0
    elif adx_v >= ADX_TENDENCIA and preco > ema20 > ema50:
        scores["regime"] = min(100, 60 + adx_v)
    elif adx_v >= ADX_TENDENCIA and preco < ema20 < ema50:
        scores["regime"] = 10
    else:
        scores["regime"] = 20

    # MTF (20%)
    scores["mtf"] = 100 if tend_4h == "ALTA" else 50 if tend_4h == "LATERAL" else 0

    # ML (15%)
    if ml_prob is not None:
        scores["ml"] = min(100, max(0, int(ml_prob * 100)))
    else:
        scores["ml"] = 50

    # EMA (10%)
    if preco > ema20 > ema50:
        dist = (preco - ema50) / ema50 * 100
        scores["ema"] = min(100, 70 + dist * 10)
    elif preco > ema20:
        scores["ema"] = 50
    elif preco > ema50:
        scores["ema"] = 30
    else:
        scores["ema"] = 0

    # Fear & Greed (10%) - sem API no backtest, assume neutro
    scores["fear_greed"] = 100

    # RSI (8%)
    if rsi_v is None:
        scores["rsi"] = 50
    elif rsi_min <= rsi_v <= rsi_max:
        rsi_ideal = (rsi_min + rsi_max) / 2
        dist_centro = abs(rsi_v - rsi_ideal) / ((rsi_max - rsi_min) / 2)
        scores["rsi"] = int(100 - dist_centro * 30)
    elif rsi_v < rsi_min:
        scores["rsi"] = max(0, int((rsi_v - 30) / (rsi_min - 30) * 60))
    else:
        scores["rsi"] = max(0, int((80 - rsi_v) / (80 - rsi_max) * 60))

    # VWAP (5%)
    if vwap_v > 0:
        dist_pct = (preco - vwap_v) / vwap_v * 100
        scores["vwap"] = min(100, 70 + dist_pct * 5) if dist_pct > 0 else max(0, 40 + dist_pct * 8)
    else:
        scores["vwap"] = 50

    # Volume (4%)
    if vol_rel >= 2.0:    scores["volume"] = 100
    elif vol_rel >= 1.3:  scores["volume"] = 80
    elif vol_rel >= 1.0:  scores["volume"] = 60
    elif vol_rel >= 0.7:  scores["volume"] = 40
    else:                 scores["volume"] = 20

    # ATR (3%)
    if atr_med > 0:
        ratio = atr_v / atr_med
        if ratio > 2.5:           scores["atr"] = 0
        elif 0.7 <= ratio <= 1.8: scores["atr"] = 100
        elif ratio < 0.7:         scores["atr"] = 30
        else:                     scores["atr"] = 60
    else:
        scores["atr"] = 50

    # Calcular score ponderado
    pesos = {"regime": 25, "mtf": 20, "ml": 15, "ema": 10, "fear_greed": 10,
             "rsi": 8, "vwap": 5, "volume": 4, "atr": 3}
    total = sum(scores[k] * pesos[k] / 100 for k in pesos)

    # Bloqueios
    bloqueado = False
    if scores["regime"] <= 20 and adx_v < ADX_TENDENCIA:
        bloqueado = True
    if atr_ratio > ATR_EXTREMO:
        bloqueado = True

    if bloqueado:
        decisao = "AGUARDAR"
        fator = 0.0
    elif total >= score_cheio:
        decisao = "OPERAR_CHEIO"
        fator = 1.0
    elif total >= score_operar:
        decisao = "OPERAR_REDUZIDO"
        fator = 0.5
    else:
        decisao = "AGUARDAR"
        fator = 0.0

    return round(total), decisao, fator, scores


def carregar(symbol, intervalo):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, abertura, maxima, minima, fechamento, volume
        FROM klines WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """, (symbol, intervalo)).fetchall()
    conn.close()
    return rows


def rodar(symbol="BTCUSDT", intervalo_entrada="1h", intervalo_mtf="4h",
          capital_inicial=1000.0, usar_ml=True):
    """Roda backtest com score unificado + ML ensemble, usando params otimizados por par."""
    # Carregar parâmetros otimizados para o par
    p = get_params(symbol)
    STOP_PCT    = p["stop_pct"]
    TARGET_PCT  = p["target_pct"]
    RSI_MIN     = p["rsi_min"]
    RSI_MAX     = p["rsi_max"]
    SCORE_OP    = p["score_operar"]
    SCORE_CH    = p["score_cheio"]

    k1h = carregar(symbol, intervalo_entrada)
    k4h = carregar(symbol, intervalo_mtf)

    if len(k1h) < 100 or len(k4h) < 55:
        return {"erro": f"Dados insuficientes para {symbol}. Rode coletar_dados.py primeiro."}

    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]

    f4h = [r[4] for r in k4h]
    m4h = [r[2] for r in k4h]
    n4h = [r[3] for r in k4h]

    # Pre-computar indicadores 1H
    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = ind.atr(m1h, n1h, f1h, 14)
    volr  = ind.volume_relativo(v1h, 20)
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    bw    = ind.bandwidth(bbu, bbm, bbl)
    vwap  = ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20)
    adx_vals = _adx(m1h, n1h, f1h, 14)

    # Pre-computar 4H
    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    # Treinar ML no primeiro 60% dos dados (walk-forward simples)
    ml_probs = [None] * len(f1h)
    if usar_ml:
        try:
            split_idx = int(len(f1h) * 0.6)
            from xgboost import XGBClassifier
            import numpy as np

            # Preparar features do treinamento
            X_train, y_train = [], []
            for i in range(55, split_idx - 8):
                feat = extrair_features(f1h, m1h, n1h, v1h, i)
                if feat is None:
                    continue
                preco_futuro = max(f1h[i+1:i+9])
                label = 1 if (preco_futuro - f1h[i]) / f1h[i] >= 0.015 else 0
                X_train.append(feat)
                y_train.append(label)

            if len(X_train) > 100:
                X_tr = np.array(X_train)
                y_tr = np.array(y_train)
                ratio = (len(y_tr) - y_tr.sum()) / max(y_tr.sum(), 1)

                modelo = XGBClassifier(
                    n_estimators=200, max_depth=4, learning_rate=0.03,
                    subsample=0.8, colsample_bytree=0.8,
                    scale_pos_weight=ratio, eval_metric="logloss", verbosity=0)
                modelo.fit(X_tr, y_tr)

                # Prever no restante (out-of-sample)
                for i in range(split_idx, len(f1h)):
                    feat = extrair_features(f1h, m1h, n1h, v1h, i)
                    if feat:
                        ml_probs[i] = float(modelo.predict_proba([feat])[0][1])

                print(f"  [ML] Modelo treinado com {len(X_train)} amostras, "
                      f"prevendo {sum(1 for p in ml_probs if p is not None)} candles")
        except Exception as e:
            print(f"  [ML] Erro no treinamento: {e}")

    def tend_4h_em(idx_1h):
        idx4 = min(idx_1h // 4, len(f4h) - 1)
        p = f4h[idx4]
        e20 = ema20_4h[idx4]
        e50 = ema50_4h[idx4]
        if p > e20 > e50:  return "ALTA"
        if p < e20 < e50:  return "BAIXA"
        return "LATERAL"

    capital   = capital_inicial
    posicao   = None
    operacoes = []
    scores_hist = []
    start_idx = int(len(f1h) * 0.6) if usar_ml else 55

    for i in range(max(55, start_idx), len(k1h)):
        preco  = f1h[i]
        e20    = ema20[i]
        e50    = ema50[i]
        rsi_v  = rsi14[i]
        atr_v  = atr14[i]
        vr     = volr[i]
        bw_v   = bw[i]
        vwap_v = vwap[i]
        adx_v  = adx_vals[i]

        if any(x is None for x in [rsi_v, atr_v, vr, bw_v, vwap_v]):
            continue

        atr_med = sum(x for x in atr14[max(0,i-20):i] if x) / max(1, len([x for x in atr14[max(0,i-20):i] if x]))
        bw_med  = sum(x for x in bw[max(0,i-20):i] if x) / max(1, len([x for x in bw[max(0,i-20):i] if x]))
        atr_ratio = atr_v / atr_med if atr_med > 0 else 1.0

        # Verificar saida
        if posicao:
            mn = n1h[i]
            mx = m1h[i]

            if mn <= posicao["stop"]:
                ps  = posicao["stop"] * (1 - SLIPPAGE)
                pnl = posicao["usdt"] * ((ps - posicao["entrada"]) / posicao["entrada"]) - posicao["usdt"] * TAXA * 2
                capital += pnl
                operacoes.append({
                    "resultado": pnl,
                    "resultado_pct": round((ps - posicao["entrada"]) / posicao["entrada"] * 100, 2),
                    "tipo_saida": "STOP",
                    "entrada_dt": posicao.get("dt", ""),
                    "saida_dt": datetime.fromtimestamp(ts1h[i]/1000).strftime("%d/%m/%Y %H:%M"),
                    "preco_entrada": posicao["entrada"],
                    "preco_saida": round(ps, 2),
                    "score": posicao.get("score", 0),
                    "ml_prob": posicao.get("ml_prob"),
                })
                posicao = None
            elif mx >= posicao["target"]:
                pt  = posicao["target"] * (1 - SLIPPAGE)
                pnl = posicao["usdt"] * ((pt - posicao["entrada"]) / posicao["entrada"]) - posicao["usdt"] * TAXA * 2
                capital += pnl
                operacoes.append({
                    "resultado": pnl,
                    "resultado_pct": round((pt - posicao["entrada"]) / posicao["entrada"] * 100, 2),
                    "tipo_saida": "TARGET",
                    "entrada_dt": posicao.get("dt", ""),
                    "saida_dt": datetime.fromtimestamp(ts1h[i]/1000).strftime("%d/%m/%Y %H:%M"),
                    "preco_entrada": posicao["entrada"],
                    "preco_saida": round(pt, 2),
                    "score": posicao.get("score", 0),
                    "ml_prob": posicao.get("ml_prob"),
                })
                posicao = None

        # Verificar entrada
        if posicao is None:
            t4h = tend_4h_em(i)
            ml_p = ml_probs[i]

            score, decisao, fator, scores_det = _score_backtest(
                preco, e20, e50, rsi_v, atr_v, atr_med, vr, bw_v, bw_med,
                vwap_v, t4h, adx_v, atr_ratio, ml_p,
                rsi_min=RSI_MIN, rsi_max=RSI_MAX,
                score_operar=SCORE_OP, score_cheio=SCORE_CH)

            scores_hist.append(score)

            if decisao in ("OPERAR_CHEIO", "OPERAR_REDUZIDO"):
                entrada = preco * (1 + SLIPPAGE)
                usdt    = min(capital * 0.02 / STOP_PCT, capital) * fator
                posicao = {
                    "entrada": entrada,
                    "stop":    entrada * (1 - STOP_PCT),
                    "target":  entrada * (1 + TARGET_PCT),
                    "usdt":    usdt,
                    "dt":      datetime.fromtimestamp(ts1h[i]/1000).strftime("%d/%m/%Y %H:%M"),
                    "score":   score,
                    "ml_prob": ml_p,
                    "fator":   fator,
                }

    # Metricas
    if not operacoes:
        return {"erro": "Nenhuma operacao encontrada com score unificado."}

    total   = len(operacoes)
    ganhos  = [o for o in operacoes if o["resultado"] > 0]
    perdas  = [o for o in operacoes if o["resultado"] <= 0]
    wrate   = len(ganhos) / total * 100
    lucro   = sum(o["resultado"] for o in ganhos)
    perda   = abs(sum(o["resultado"] for o in perdas))
    pf      = lucro / perda if perda else 999.0
    retorno = (capital - capital_inicial) / capital_inicial * 100

    # Max Drawdown
    pico = capital_inicial
    cap  = capital_inicial
    max_dd = 0
    for o in operacoes:
        cap += o["resultado"]
        if cap > pico: pico = cap
        dd = (pico - cap) / pico * 100
        if dd > max_dd: max_dd = dd

    # Sharpe
    rets = [o["resultado_pct"] for o in operacoes]
    sharpe = (statistics.mean(rets) / statistics.stdev(rets) * (252**0.5)) if len(rets) > 1 and statistics.stdev(rets) > 0 else 0

    mg = sum(o["resultado_pct"] for o in ganhos) / len(ganhos) if ganhos else 0
    mp = abs(sum(o["resultado_pct"] for o in perdas) / len(perdas)) if perdas else 0
    exp = (wrate/100 * mg) - ((1-wrate/100) * mp)

    # Score medio das entradas
    scores_entradas = [o.get("score", 0) for o in operacoes]
    score_medio = statistics.mean(scores_entradas) if scores_entradas else 0

    # Win rate por faixa de score
    wr_por_score = {}
    for o in operacoes:
        s = o.get("score", 0)
        faixa = f"{(s // 10) * 10}-{(s // 10) * 10 + 9}"
        if faixa not in wr_por_score:
            wr_por_score[faixa] = {"ganhos": 0, "total": 0}
        wr_por_score[faixa]["total"] += 1
        if o["resultado"] > 0:
            wr_por_score[faixa]["ganhos"] += 1

    return {
        "symbol":          symbol,
        "intervalo":       f"{intervalo_entrada}+{intervalo_mtf}(MTF+Score+ML)",
        "params": {
            "stop_pct":     STOP_PCT,
            "target_pct":   TARGET_PCT,
            "rsi_min":      RSI_MIN,
            "rsi_max":      RSI_MAX,
            "score_operar": SCORE_OP,
            "score_cheio":  SCORE_CH,
        },
        "total_trades":    total,
        "win_rate_%":      round(wrate, 1),
        "trades_ganhos":   len(ganhos),
        "trades_perdas":   len(perdas),
        "profit_factor":   round(pf, 2),
        "retorno_total_%": round(retorno, 2),
        "capital_inicial": capital_inicial,
        "capital_final":   round(capital, 2),
        "max_drawdown_%":  round(max_dd, 2),
        "sharpe_ratio":    round(sharpe, 2),
        "expectancia_%":   round(exp, 2),
        "media_ganho_%":   round(mg, 2),
        "media_perda_%":   round(mp, 2),
        "score_medio":     round(score_medio, 1),
        "wr_por_score":    wr_por_score,
        "ml_usado":        usar_ml,
        "operacoes":       operacoes,
    }


def imprimir_relatorio(r):
    if "erro" in r:
        print(f"ERRO: {r['erro']}")
        return

    def nota(valor, ref_bom, ref_otimo, maior_melhor=True):
        if maior_melhor:
            if valor >= ref_otimo: return "[OTIMO]"
            if valor >= ref_bom:   return "[BOM]  "
            return "[RUIM] "
        else:
            if valor <= ref_otimo: return "[OTIMO]"
            if valor <= ref_bom:   return "[BOM]  "
            return "[RUIM] "

    p = r.get("params", {})
    print("\n" + "="*62)
    print(f"  BACKTEST ENSEMBLE — {r.get('symbol', 'BTCUSDT')}  [{r['intervalo']}]")
    print("="*62)
    if p:
        print(f"  Params: Stop {p['stop_pct']*100:.1f}% / Target {p['target_pct']*100:.1f}% / "
              f"RSI {p['rsi_min']}-{p['rsi_max']} / Score {p['score_operar']}/{p['score_cheio']}")
    print(f"  Total de operacoes:  {r['total_trades']}")
    print(f"  Ganhos / Perdas:     {r['trades_ganhos']} / {r['trades_perdas']}")
    print(f"  ML Ensemble:         {'SIM' if r.get('ml_usado') else 'NAO'}")
    print(f"  Score medio entrada: {r.get('score_medio', 0):.1f}/100")
    print()
    print(f"  Win Rate:      {r['win_rate_%']:6.1f}%   {nota(r['win_rate_%'], 52, 60)}")
    print(f"  Profit Factor: {r['profit_factor']:6.2f}    {nota(r['profit_factor'], 1.3, 1.8)}")
    print(f"  Sharpe Ratio:  {r['sharpe_ratio']:6.2f}    {nota(r['sharpe_ratio'], 1.0, 1.5)}")
    print(f"  Expectancia:   {r['expectancia_%']:6.2f}%   {nota(r['expectancia_%'], 0.1, 0.5)}")
    print(f"  Max Drawdown:  {r['max_drawdown_%']:6.2f}%   {nota(r['max_drawdown_%'], 15, 8, maior_melhor=False)}")
    print(f"  Retorno Total: {r['retorno_total_%']:6.2f}%")
    print()
    print(f"  Capital Inicial: ${r['capital_inicial']:,.2f}")
    print(f"  Capital Final:   ${r['capital_final']:,.2f}")
    print(f"  Lucro Liquido:   ${r['capital_final']-r['capital_inicial']:,.2f}")
    print()
    print(f"  Media Ganho/Trade: +{r['media_ganho_%']:.2f}%")
    print(f"  Media Perda/Trade: -{r['media_perda_%']:.2f}%")

    # Win Rate por faixa de score
    if r.get("wr_por_score"):
        print("\n  WIN RATE POR FAIXA DE SCORE:")
        for faixa in sorted(r["wr_por_score"].keys()):
            dados = r["wr_por_score"][faixa]
            wr = dados["ganhos"] / dados["total"] * 100 if dados["total"] > 0 else 0
            bar = "#" * int(wr / 5)
            print(f"    Score {faixa:5s}: {wr:5.1f}% ({dados['ganhos']}/{dados['total']}) {bar}")

    # Veredicto
    print("\n" + "-"*62)
    score = 0
    if r['win_rate_%']       >= 52:  score += 1
    if r['profit_factor']    >= 1.3: score += 1
    if r['sharpe_ratio']     >= 1.0: score += 1
    if r['max_drawdown_%']   <= 15:  score += 1
    if r['expectancia_%']    > 0:    score += 1

    if score == 5:
        veredito = "ESTRATEGIA EXCELENTE"
    elif score >= 3:
        veredito = "ESTRATEGIA PROMISSORA"
    else:
        veredito = "ESTRATEGIA FRACA — Otimizar parametros"

    print(f"  CRITERIOS APROVADOS: {score}/5")
    print(f"  VEREDITO: {veredito}")
    print("="*62)

    # Ultimas 10 operacoes
    print(f"\n  ULTIMAS 10 OPERACOES:")
    print(f"  {'Data Entrada':16s} {'Entrada':>10s} {'Saida':>10s} {'Result':>8s} {'Score':>5s} {'ML%':>5s} {'Tipo'}")
    print("  " + "-"*68)
    for o in r["operacoes"][-10:]:
        sinal = "+" if o["resultado"] > 0 else ""
        ml_str = f"{o.get('ml_prob',0)*100:4.0f}%" if o.get("ml_prob") else "  N/A"
        print(f"  {o['entrada_dt']:16s} "
              f"${o['preco_entrada']:>9,.0f} "
              f"${o['preco_saida']:>9,.0f} "
              f"{sinal}{o['resultado_pct']:>6.2f}% "
              f"{o.get('score',0):>4}  "
              f"{ml_str}  "
              f"{o['tipo_saida']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest com Score + Ensemble ML")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--capital", default=1000.0, type=float)
    parser.add_argument("--par", default="BTCUSDT")
    parser.add_argument("--sem-ml", action="store_true", help="Rodar sem ML")
    args = parser.parse_args()

    print(f"\n[BACKTEST] {args.par} — Estrategia Score+Ensemble...\n")
    r = rodar(args.par, args.intervalo, "4h", args.capital, usar_ml=not args.sem_ml)
    if r:
        imprimir_relatorio(r)
