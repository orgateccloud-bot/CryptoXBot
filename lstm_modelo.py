"""
Modelo Sequencial (MLP) — BotBinance
=======================================
Simula o comportamento de um LSTM usando uma rede neural MLP
com features sequenciais achatadas (flatten de N velas).

Vantagem vs XGBoost:
  - XGBoost: ve 1 snapshot (features do momento atual)
  - MLP Seq: ve 24 velas (captura padroes de evolucao temporal)

Arquitetura (sklearn MLPClassifier):
  Input: 24 velas x 11 features = 264 features achatadas
  Hidden: 128 → 64 → 32 (3 camadas)
  Output: 1 (sigmoid → probabilidade)

Uso:
  python lstm_modelo.py --treinar          → treina e salva
  python lstm_modelo.py                    → previsao atual
  python lstm_modelo.py --treinar --epocas 200
"""

import argparse
import os
import pickle
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicadores as ind
from ml_filtro import extrair_features, verificar_drift_e_registrar

MODEL_PATH = "data/modelo_lstm.pkl"
DB_PATH = "data/btc_data.db"
SYMBOL = "BTCUSDT"
BASE_URL = "https://api.binance.com"
ALVO_PCT = 0.015  # 1.5% de alta
JANELA_FUTURA = 8  # velas a frente
SEQ_LEN = 24  # velas no passado
N_FEATURES = 11


def preparar_sequencias(intervalo="1h"):
    """Prepara sequencias achatadas (flatten) para MLP."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT fechamento, maxima, minima, volume
        FROM klines
        WHERE symbol='BTCUSDT' AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (intervalo,),
    ).fetchall()
    conn.close()

    if len(rows) < 200:
        print("[SEQ] Dados insuficientes.")
        return None, None

    fechamentos = [r[0] for r in rows]
    maximas = [r[1] for r in rows]
    minimas = [r[2] for r in rows]
    volumes = [r[3] for r in rows]

    # Extrair features para cada vela
    all_features = []
    all_indices = []
    for i in range(55, len(fechamentos)):
        feat = extrair_features(fechamentos, maximas, minimas, volumes, i)
        if feat:
            all_features.append(feat)
            all_indices.append(i)

    # Criar sequencias achatadas
    X, y = [], []
    for j in range(SEQ_LEN, len(all_features)):
        idx = all_indices[j]
        if idx + JANELA_FUTURA >= len(fechamentos):
            break

        # Achatar 24 velas x 11 features = 264 features
        seq_flat = []
        for k in range(j - SEQ_LEN, j):
            seq_flat.extend(all_features[k])

        # Adicionar deltas entre velas (captura tendencia temporal)
        for k in range(j - SEQ_LEN + 1, j):
            for f_idx in range(N_FEATURES):
                delta = all_features[k][f_idx] - all_features[k - 1][f_idx]
                seq_flat.append(delta)

        X.append(seq_flat)

        preco_entrada = fechamentos[idx]
        preco_futuro = max(fechamentos[idx + 1 : idx + JANELA_FUTURA + 1])
        label = 1 if (preco_futuro - preco_entrada) / preco_entrada >= ALVO_PCT else 0
        y.append(label)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def treinar(intervalo="1h", max_iter=200):
    """Treina o MLP sequencial e salva."""
    print(f"[SEQ] Preparando sequencias [{intervalo}]...")
    X, y = preparar_sequencias(intervalo)
    if X is None:
        return None

    from sklearn.metrics import classification_report, roc_auc_score
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from validacao import purged_cv_auc, split_holdout_purgado

    n = len(X)
    pos = int(y.sum())
    neg = len(y) - pos

    print(f"[SEQ] Dataset: {n} seq ({SEQ_LEN} velas x {N_FEATURES} feat + deltas)")
    print(f"[SEQ] Features totais: {X.shape[1]}")
    print(f"[SEQ] Positivos: {pos} ({pos/n*100:.1f}%)")

    def _build_mlp():
        # Pipeline: o StandardScaler é re-fitado por fold no purged CV (senão
        # normalizar com dados do teste vazaria estatísticas do futuro).
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "mlp",
                    MLPClassifier(
                        hidden_layer_sizes=(128, 64, 32),
                        activation="relu",
                        solver="adam",
                        max_iter=max_iter,
                        early_stopping=True,
                        validation_fraction=0.15,
                        n_iter_no_change=15,
                        learning_rate="adaptive",
                        learning_rate_init=0.001,
                        batch_size=64,
                        random_state=42,
                    ),
                ),
            ]
        )

    # P0-1: estimativa honesta via purged & embargoed CV (horizonte = JANELA_FUTURA).
    # (O CV não aplica sample_weight — AUC é ranking-based, pouco sensível a isso.)
    cv_aucs = purged_cv_auc(
        _build_mlp, X, y, n_splits=5, horizonte=JANELA_FUTURA, embargo=JANELA_FUTURA
    )
    if cv_aucs:
        cv_mean, cv_std = float(np.mean(cv_aucs)), float(np.std(cv_aucs))
        print(f"[SEQ] Purged CV AUC: {cv_mean:.4f} ± {cv_std:.4f}  ({len(cv_aucs)} folds)")
    else:
        cv_mean = cv_std = None

    # Holdout cronológico PURGADO (últimas JANELA_FUTURA seq de treino removidas).
    tr_idx, te_idx = split_holdout_purgado(n, test_frac=0.2, horizonte=JANELA_FUTURA)
    X_tr, X_te = X[tr_idx], X[te_idx]
    y_tr, y_te = y[tr_idx], y[te_idx]

    # Normalizar
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    # Criar amostra balanceada via class_weight (sample_weight)
    sample_weight = np.ones(len(y_tr))
    ratio = neg / max(pos, 1)
    sample_weight[y_tr == 1] = ratio

    modelo = MLPClassifier(
        hidden_layer_sizes=(128, 64, 32),
        activation="relu",
        solver="adam",
        max_iter=max_iter,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=15,
        learning_rate="adaptive",
        learning_rate_init=0.001,
        batch_size=64,
        random_state=42,
        verbose=True,
    )

    print(f"[SEQ] Treinando MLP (max {max_iter} iteracoes)...")
    modelo.fit(X_tr_s, y_tr, sample_weight=sample_weight)

    y_prob = modelo.predict_proba(X_te_s)[:, 1]
    y_pred = modelo.predict(X_te_s)
    auc = roc_auc_score(y_te, y_prob)

    print(f"\n[SEQ] RESULTADO DO TREINAMENTO:")
    print(f"      AUC holdout purgado: {auc:.4f}")
    if cv_mean is not None:
        print(f"      AUC purged CV (honesto): {cv_mean:.4f} ± {cv_std:.4f}")
    print(f"      Iteracoes: {modelo.n_iter_}")
    print(classification_report(y_te, y_pred, target_names=["Nao Sobe", "Sobe"]))

    # P1-4: guard-rail LEVE de drift (sem MLflow) -- reusa a mesma logica de
    # ml_filtro.py (MLP e um unico modelo global, SYMBOL="BTCUSDT" fixo,
    # nao por par).
    verificar_drift_e_registrar(SYMBOL, "MLP", float(auc), cv_mean, cv_std)

    os.makedirs("data", exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(
            {
                "modelo": modelo,
                "scaler": scaler,
                "intervalo": intervalo,
                "auc": float(auc),
                "cv_auc_mean": cv_mean,  # P0-1: estimativa honesta (purged CV)
                "cv_auc_std": cv_std,
                "seq_len": SEQ_LEN,
                "n_features": N_FEATURES,
            },
            f,
        )

    print(f"[SEQ] Modelo salvo em: {MODEL_PATH}")
    return modelo


def prever():
    """Retorna probabilidade de alta usando modelo sequencial."""
    if not os.path.exists(MODEL_PATH):
        return None, "Modelo sequencial nao treinado. Rode: python lstm_modelo.py --treinar"

    with open(MODEL_PATH, "rb") as f:
        artefato = pickle.load(f)

    modelo = artefato["modelo"]
    scaler = artefato["scaler"]
    intervalo = artefato.get("intervalo", "1h")

    import requests

    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": SYMBOL, "interval": intervalo, "limit": 100},
        timeout=8,
    )
    rows = r.json()
    fechamentos = [float(k[4]) for k in rows]
    maximas = [float(k[2]) for k in rows]
    minimas = [float(k[3]) for k in rows]
    volumes = [float(k[5]) for k in rows]

    all_features = []
    for i in range(55, len(fechamentos)):
        feat = extrair_features(fechamentos, maximas, minimas, volumes, i)
        if feat:
            all_features.append(feat)

    if len(all_features) < SEQ_LEN:
        return None, "Features insuficientes"

    # Ultimas SEQ_LEN features achatadas + deltas
    recentes = all_features[-SEQ_LEN:]
    seq_flat = []
    for feat in recentes:
        seq_flat.extend(feat)
    for k in range(1, len(recentes)):
        for f_idx in range(N_FEATURES):
            delta = recentes[k][f_idx] - recentes[k - 1][f_idx]
            seq_flat.append(delta)

    X = np.array([seq_flat], dtype=np.float32)
    X_s = scaler.transform(X)

    prob = float(modelo.predict_proba(X_s)[0][1])
    return round(prob, 4), "OK"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--treinar", action="store_true")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--epocas", type=int, default=200)
    args = parser.parse_args()

    if args.treinar:
        treinar(args.intervalo, args.epocas)
    else:
        if not os.path.exists(MODEL_PATH):
            print("[SEQ] Modelo nao encontrado. Treinando...")
            treinar(args.intervalo)

        prob, msg = prever()
        if prob is not None:
            cor = "\033[92m" if prob >= 0.55 else "\033[91m"
            reset = "\033[0m"
            print(f"\n[SEQ] Probabilidade de alta: {cor}{prob*100:.1f}%{reset}")
        else:
            print(f"[SEQ] Erro: {msg}")
