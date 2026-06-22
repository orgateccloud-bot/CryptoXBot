"""
Filtro de Machine Learning — BotBinance
=========================================
Modelo: XGBoost (Gradient Boosting)

Features (entrada):
  EMA20, EMA50, RSI, ATR, Volume Relativo,
  Bollinger BW, distância do VWAP,
  Funding Rate, variação 1h/4h/24h,
  CVD normalizado, posição relativa ao BB

Target (saída):
  1 = preço sobe >= 1.5% nas próximas 8 velas
  0 = não sobe (ou cai)

Uso:
  python ml_filtro.py --treinar   → treina e salva o modelo
  python ml_filtro.py             → avalia probabilidade atual
"""

import os
import sys
import sqlite3
import pickle
import argparse
import requests
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicadores as ind

DB_PATH = "data/btc_data.db"
BASE_URL = "https://api.binance.com"
ALVO_PCT = 0.015  # 1.5% de alta nas próximas 8 velas = label 1
JANELA = 8  # velas à frente para medir resultado


def _model_path(symbol="BTCUSDT"):
    return f"data/modelo_xgb_{symbol.lower()}.pkl"


MODEL_PATH = _model_path()  # retrocompat
SYMBOL = "BTCUSDT"


# ── Extração de features ──────────────────────────────────────


def extrair_features(fechamentos, maximas, minimas, volumes, i):
    """Extrai vetor de features para o índice i."""
    if i < 55:
        return None

    f = fechamentos[: i + 1]
    m = maximas[: i + 1]
    mn = minimas[: i + 1]
    v = volumes[: i + 1]

    preco = f[-1]

    ema20_v = ind.ema(f[-20:], 20)[-1]
    ema50_v = ind.ema(f[-50:], 50)[-1]
    rsi_v = ind.rsi(f, 14)[-1] or 50
    atr_v = ind.atr(m, mn, f, 14)
    atr_val = atr_v[-1] if atr_v[-1] else 1
    atr_med = sum(x for x in atr_v[-20:] if x) / max(len([x for x in atr_v[-20:] if x]), 1)
    vr = ind.volume_relativo(v, 20)[-1] or 1.0
    bb_u, bb_m, bb_l = ind.bollinger(f, 20, 2)
    bw_v = ind.bandwidth(bb_u, bb_m, bb_l)[-1] or 0
    bw_med = sum(x for x in ind.bandwidth(bb_u, bb_m, bb_l)[-20:] if x) / 20
    vwap_v = ind.vwap(m, mn, f, v)[-1]

    var_1 = (f[-1] - f[-2]) / f[-2] if len(f) >= 2 else 0
    var_4 = (f[-1] - f[-5]) / f[-5] if len(f) >= 5 else 0
    var_24 = (f[-1] - f[-25]) / f[-25] if len(f) >= 25 else 0

    # Posição dentro do Bollinger (0=inferior, 1=superior)
    bb_pos = (preco - bb_l[-1]) / max(bb_u[-1] - bb_l[-1], 1) if bb_l[-1] and bb_u[-1] else 0.5

    dist_ema20 = (preco - ema20_v) / ema20_v
    dist_ema50 = (preco - ema50_v) / ema50_v
    dist_vwap = (preco - vwap_v) / vwap_v

    return [
        dist_ema20,  # 0
        dist_ema50,  # 1
        rsi_v / 100,  # 2
        atr_val / atr_med,  # 3  ATR relativo
        min(vr, 5) / 5,  # 4  volume relativo normalizado
        bw_v / max(bw_med, 0.0001),  # 5  bollinger relativo
        dist_vwap,  # 6
        var_1,  # 7
        var_4,  # 8
        var_24,  # 9
        bb_pos,  # 10
    ]


# ── Preparar dataset ──────────────────────────────────────────


def preparar_dataset(intervalo="1h", symbol="BTCUSDT"):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT fechamento, maxima, minima, volume
        FROM klines
        WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (symbol, intervalo),
    ).fetchall()
    conn.close()

    if len(rows) < 100:
        print("Dados insuficientes. Rode: python backtesting/coletar_dados.py")
        return None, None

    fechamentos = [r[0] for r in rows]
    maximas = [r[1] for r in rows]
    minimas = [r[2] for r in rows]
    volumes = [r[3] for r in rows]

    X, y = [], []
    for i in range(55, len(fechamentos) - JANELA):
        feat = extrair_features(fechamentos, maximas, minimas, volumes, i)
        if feat is None:
            continue
        preco_entrada = fechamentos[i]
        preco_futuro = max(fechamentos[i + 1 : i + JANELA + 1])
        label = 1 if (preco_futuro - preco_entrada) / preco_entrada >= ALVO_PCT else 0
        X.append(feat)
        y.append(label)

    return np.array(X), np.array(y)


# ── Treinar modelo ────────────────────────────────────────────


def treinar(intervalo="1h", symbol="BTCUSDT"):
    print(f"[ML] Preparando dataset [{symbol}/{intervalo}]...")
    X, y = preparar_dataset(intervalo, symbol)
    if X is None:
        return

    from xgboost import XGBClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, roc_auc_score

    print(f"[ML] Dataset: {len(X)} amostras | Positivos: {y.sum()} ({y.mean()*100:.1f}%)")

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)

    # Corrige desbalanceamento: 85% negativo / 15% positivo
    ratio = (len(y) - y.sum()) / max(y.sum(), 1)

    modelo = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        scale_pos_weight=ratio,  # penaliza erros na classe minoritária
        verbosity=0,
    )
    modelo.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

    y_pred = modelo.predict(X_te)
    y_prob = modelo.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, y_prob)

    print(f"\n[ML] RESULTADO DO TREINAMENTO:")
    print(f"     AUC-ROC:  {auc:.4f}  (meta: > 0.60)")
    print(classification_report(y_te, y_pred, target_names=["Nao Sobe", "Sobe"]))

    # Importância das features
    nomes = [
        "dist_ema20",
        "dist_ema50",
        "rsi",
        "atr_rel",
        "vol_rel",
        "bw_rel",
        "dist_vwap",
        "var_1h",
        "var_4h",
        "var_24h",
        "bb_pos",
    ]
    imp = sorted(zip(nomes, modelo.feature_importances_), key=lambda x: -x[1])
    print("\n[ML] Importancia das features:")
    for n, v in imp:
        bar = "#" * int(v * 40)
        print(f"     {n:15s} {v:.4f} {bar}")

    os.makedirs("data", exist_ok=True)
    path = _model_path(symbol)
    with open(path, "wb") as f:
        pickle.dump({"modelo": modelo, "intervalo": intervalo, "auc": auc, "symbol": symbol}, f)
    print(f"\n[ML] Modelo salvo em: {path}")
    return modelo


# ── Prever probabilidade ──────────────────────────────────────


def prever(symbol="BTCUSDT"):
    """Retorna probabilidade de alta nas próximas 8 velas."""
    path = _model_path(symbol)
    if not os.path.exists(path):
        # Fallback para modelo antigo (retrocompat)
        if symbol == "BTCUSDT" and os.path.exists("data/modelo_xgb.pkl"):
            path = "data/modelo_xgb.pkl"
        else:
            return (
                None,
                f"Modelo nao treinado para {symbol}. Rode: python ml_filtro.py --treinar --par {symbol}",
            )

    with open(path, "rb") as f:
        artefato = pickle.load(f)
    modelo = artefato["modelo"]
    intervalo = artefato.get("intervalo", "1h")

    # Buscar dados recentes da API (retry 3x com backoff)
    for tentativa in range(3):
        try:
            r = requests.get(
                f"{BASE_URL}/api/v3/klines",
                params={"symbol": symbol, "interval": intervalo, "limit": 100},
                timeout=8,
            )
            r.raise_for_status()
            break
        except requests.RequestException:
            if tentativa == 2:
                return None, "Falha ao buscar klines da API apos 3 tentativas"
            import time as _time

            _time.sleep(2**tentativa)
    rows = r.json()
    fechamentos = [float(k[4]) for k in rows]
    maximas = [float(k[2]) for k in rows]
    minimas = [float(k[3]) for k in rows]
    volumes = [float(k[5]) for k in rows]

    feat = extrair_features(fechamentos, maximas, minimas, volumes, len(fechamentos) - 1)
    if feat is None:
        return None, "Features insuficientes"

    prob = modelo.predict_proba([feat])[0][1]
    return round(float(prob), 4), "OK"


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--treinar", action="store_true")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--par", default="BTCUSDT")
    args = parser.parse_args()

    symbol = args.par.upper()

    if args.treinar:
        treinar(args.intervalo, symbol)
    else:
        path = _model_path(symbol)
        if not os.path.exists(path):
            print(f"[ML] Modelo nao encontrado para {symbol}. Treinando agora...")
            treinar(args.intervalo, symbol)

        prob, msg = prever(symbol)
        if prob is not None:
            cor = "\033[92m" if prob >= 0.60 else "\033[91m"
            reset = "\033[0m"
            print(f"\n[ML] {symbol} — Probabilidade de alta: {cor}{prob*100:.1f}%{reset}")
            print(f"     Filtro ML: {'APROVADO' if prob >= 0.60 else 'REPROVADO'} (limiar: 60%)")
        else:
            print(f"[ML] Erro: {msg}")
