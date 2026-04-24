"""
LightGBM — Modelo de Alta Velocidade para BotBinance
=======================================================
Complementa o XGBoost com um algoritmo de árvore diferente:
  - Leaf-wise splitting (vs level-wise do XGB) → mais preciso com menos dados
  - Treino 5-10x mais rápido que XGBoost em datasets grandes
  - Menor footprint de memória em produção (~20 MB vs 50 MB do XGB)

Features: as mesmas 11 do XGBoost (dist_ema20, dist_ema50, rsi, atr_rel,
          vol_rel, bw_rel, dist_vwap, var_1h, var_4h, var_24h, bb_pos)

Target: preço sobe >= 1.5% nas próximas 8 velas = label 1

Uso:
  python lgbm_modelo.py --treinar           → treina e salva modelo
  python lgbm_modelo.py --treinar --par ETH → treina para ETH
  python lgbm_modelo.py                     → previsão atual BTC
  python lgbm_modelo.py --par SOLUSDT       → previsão SOL
"""

import os
import sys
import sqlite3
import pickle
import argparse
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ml_filtro import extrair_features, DB_PATH, BASE_URL, ALVO_PCT, JANELA


def _model_path(symbol: str = "BTCUSDT") -> str:
    return f"data/modelo_lgb_{symbol.lower()}.pkl"


# ── Dataset ───────────────────────────────────────────────────

def preparar_dataset(intervalo: str = "1h", symbol: str = "BTCUSDT"):
    """Carrega dados do SQLite e prepara X, y (mesmas features do XGBoost)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT fechamento, maxima, minima, volume
        FROM klines
        WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """, (symbol, intervalo)).fetchall()
    conn.close()

    if len(rows) < 100:
        print(f"[LGB] Dados insuficientes para {symbol}. Rode: python backtesting/coletar_dados.py")
        return None, None

    fechamentos = [r[0] for r in rows]
    maximas     = [r[1] for r in rows]
    minimas     = [r[2] for r in rows]
    volumes     = [r[3] for r in rows]

    X, y = [], []
    for i in range(55, len(fechamentos) - JANELA):
        feat = extrair_features(fechamentos, maximas, minimas, volumes, i)
        if feat is None:
            continue
        preco_entrada = fechamentos[i]
        preco_futuro  = max(fechamentos[i + 1 : i + JANELA + 1])
        label = 1 if (preco_futuro - preco_entrada) / preco_entrada >= ALVO_PCT else 0
        X.append(feat)
        y.append(label)

    return np.array(X), np.array(y)


# ── Treinar ───────────────────────────────────────────────────

def treinar(intervalo: str = "1h", symbol: str = "BTCUSDT"):
    """Treina o modelo LightGBM e salva em data/modelo_lgb_{symbol}.pkl"""
    try:
        import lightgbm as lgb
    except ImportError:
        print("[LGB] lightgbm não instalado. Rode: pip install lightgbm")
        return None

    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, roc_auc_score

    print(f"[LGB] Preparando dataset [{symbol}/{intervalo}]...")
    X, y = preparar_dataset(intervalo, symbol)
    if X is None:
        return None

    print(f"[LGB] Dataset: {len(X)} amostras | Positivos: {y.sum()} ({y.mean()*100:.1f}%)")

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)

    # Corrige desbalanceamento
    n_neg = int((y == 0).sum())
    n_pos = int((y == 1).sum())
    ratio = n_neg / max(n_pos, 1)

    modelo = lgb.LGBMClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.025,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=ratio,
        min_child_samples=20,
        reg_alpha=0.1,
        reg_lambda=0.2,
        verbose=-1,
        n_jobs=-1,
    )

    modelo.fit(
        X_tr, y_tr,
        eval_set=[(X_te, y_te)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=-1)],
    )

    y_prob  = modelo.predict_proba(X_te)[:, 1]
    y_pred  = (y_prob >= 0.5).astype(int)
    auc     = roc_auc_score(y_te, y_prob)

    print(f"\n[LGB] RESULTADO DO TREINAMENTO:")
    print(f"     AUC-ROC:  {auc:.4f}  (meta: > 0.60)")
    print(classification_report(y_te, y_pred, target_names=["Nao Sobe", "Sobe"]))

    # Importância das features
    nomes = ["dist_ema20", "dist_ema50", "rsi", "atr_rel", "vol_rel",
             "bw_rel", "dist_vwap", "var_1h", "var_4h", "var_24h", "bb_pos"]
    imp = sorted(zip(nomes, modelo.feature_importances_), key=lambda x: -x[1])
    print("\n[LGB] Importancia das features:")
    for n, v in imp:
        bar = "#" * int(v / max(imp[0][1], 1) * 30)
        print(f"     {n:15s} {v:6.0f} {bar}")

    os.makedirs("data", exist_ok=True)
    path = _model_path(symbol)
    with open(path, "wb") as f:
        pickle.dump({
            "modelo":    modelo,
            "intervalo": intervalo,
            "auc":       auc,
            "symbol":    symbol,
            "n_amostras": len(X),
        }, f)
    print(f"\n[LGB] Modelo salvo em: {path}")
    return modelo


# ── Prever ────────────────────────────────────────────────────

def prever(symbol: str = "BTCUSDT"):
    """
    Retorna probabilidade de alta nas próximas 8 velas (1H).
    Tuple (float|None, str)  →  (prob, mensagem)
    """
    path = _model_path(symbol)
    if not os.path.exists(path):
        return None, f"Modelo LGB não treinado para {symbol}. Rode: python lgbm_modelo.py --treinar --par {symbol}"

    with open(path, "rb") as f:
        artefato = pickle.load(f)
    modelo    = artefato["modelo"]
    intervalo = artefato.get("intervalo", "1h")

    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": intervalo, "limit": 100},
        timeout=8,
    )
    rows        = r.json()
    fechamentos = [float(k[4]) for k in rows]
    maximas     = [float(k[2]) for k in rows]
    minimas     = [float(k[3]) for k in rows]
    volumes     = [float(k[5]) for k in rows]

    feat = extrair_features(fechamentos, maximas, minimas, volumes, len(fechamentos) - 1)
    if feat is None:
        return None, "Features insuficientes"

    prob = modelo.predict_proba([feat])[0][1]
    return round(float(prob), 4), "OK"


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LightGBM — Modelo BotBinance")
    parser.add_argument("--treinar",   action="store_true", help="Treina e salva o modelo")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--par",       default="BTCUSDT")
    args = parser.parse_args()

    symbol = args.par.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    if args.treinar:
        treinar(args.intervalo, symbol)
    else:
        path = _model_path(symbol)
        if not os.path.exists(path):
            print(f"[LGB] Modelo não encontrado. Treinando agora...")
            treinar(args.intervalo, symbol)

        prob, msg = prever(symbol)
        if prob is not None:
            cor   = "\033[92m" if prob >= 0.60 else "\033[91m"
            reset = "\033[0m"
            print(f"\n[LGB] {symbol} — Probabilidade de alta: {cor}{prob*100:.1f}%{reset}")
            print(f"     Filtro LGB: {'APROVADO' if prob >= 0.60 else 'REPROVADO'} (limiar: 60%)")
        else:
            print(f"[LGB] Erro: {msg}")
