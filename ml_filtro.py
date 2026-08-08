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

import argparse
import os
import pickle
import sqlite3
import sys

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicadores as ind

DB_PATH = "data/btc_data.db"
BASE_URL = "https://api.binance.com"
ALVO_PCT = 0.015  # 1.5% de alta nas próximas 8 velas = label 1
JANELA = 8  # velas à frente para medir resultado


def _model_path(symbol="BTCUSDT"):
    return f"data/modelo_xgb_{symbol.lower()}.pkl"


MODEL_PATH = _model_path()  # retrocompat
# E-7: SYMBOL de modulo removido — prever() e treinar() recebem symbol; ficava
# aqui sem nenhum leitor desde que _model_path(symbol) passou a existir.


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


# ── Guard-rail de drift (P1-4, sem MLflow) ─────────────────────


def verificar_drift_e_registrar(symbol, modelo_tipo, auc, cv_mean, cv_std):
    """Compara o AUC honesto (purged CV) do retreino atual contra o
    historico recente do MESMO symbol/modelo_tipo (validacao.detectar_drift)
    e ALERTA (bot_events) se houver drift -- nunca bloqueia o retreino
    automatico de domingo, decisao explicita do usuario (ver
    PLANO_MODERNIZACAO.md P1-4). Sempre registra a metrica do retreino,
    mesmo quando nao ha drift. Excecoes sao engolidas (falha ao
    checar/registrar nao pode derrubar o retreino nem o salvamento do
    modelo). Reusado por lstm_modelo.py (mesma logica p/ o MLP)."""
    try:
        import database
        from validacao import detectar_drift

        historico = database.historico_cv_auc_modelo(symbol, modelo_tipo)
        houve_drift, motivo_drift = detectar_drift(cv_mean, historico)
        if houve_drift:
            print(f"[ML] AVISO DE DRIFT: {motivo_drift}")
            database.salvar_bot_event(
                "model_drift",
                motivo_drift,
                service="worker",
                symbol=symbol,
                severity="WARNING",
                data={"modelo_tipo": modelo_tipo, "cv_auc_mean": cv_mean, "auc": auc},
            )
        database.salvar_metricas_modelo(symbol, modelo_tipo, auc, cv_mean, cv_std)
    except Exception as e:
        print(f"[ML] AVISO: falha ao checar/registrar drift: {e}")


# ── Treinar modelo ────────────────────────────────────────────


def treinar(intervalo="1h", symbol="BTCUSDT"):
    print(f"[ML] Preparando dataset [{symbol}/{intervalo}]...")
    X, y = preparar_dataset(intervalo, symbol)
    if X is None:
        return

    from sklearn.metrics import classification_report, roc_auc_score
    from xgboost import XGBClassifier

    from validacao import purged_cv_auc, split_holdout_purgado

    print(f"[ML] Dataset: {len(X)} amostras | Positivos: {y.sum()} ({y.mean()*100:.1f}%)")

    # Corrige desbalanceamento: 85% negativo / 15% positivo
    ratio = (len(y) - y.sum()) / max(y.sum(), 1)

    def _build_xgb():
        return XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            scale_pos_weight=ratio,  # penaliza erros na classe minoritária
            verbosity=0,
        )

    # P0-1: métrica HONESTA — purged & embargoed CV (horizonte = JANELA de rótulo).
    # Um K-fold/holdout ingênuo vaza o futuro pelos labels que olham JANELA velas
    # à frente, inflando o AUC. Aqui o purge/embargo eliminam esse vazamento.
    cv_aucs = purged_cv_auc(_build_xgb, X, y, n_splits=5, horizonte=JANELA, embargo=JANELA)
    if cv_aucs:
        cv_mean, cv_std = float(np.mean(cv_aucs)), float(np.std(cv_aucs))
        print(f"[ML] Purged CV AUC: {cv_mean:.4f} ± {cv_std:.4f}  ({len(cv_aucs)} folds)")
    else:
        cv_mean = cv_std = None
        print("[ML] Purged CV: dados insuficientes p/ 5 folds — usando só holdout")

    # Holdout cronológico PURGADO (últimas JANELA amostras de treino removidas
    # para não vazar no teste). Modelo final treina no treino purgado.
    tr_idx, te_idx = split_holdout_purgado(len(X), test_frac=0.2, horizonte=JANELA)
    X_tr, y_tr = X[tr_idx], y[tr_idx]
    X_te, y_te = X[te_idx], y[te_idx]

    modelo = _build_xgb()
    modelo.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

    y_pred = modelo.predict(X_te)
    y_prob = modelo.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, y_prob)

    print(f"\n[ML] RESULTADO DO TREINAMENTO:")
    print(f"     AUC holdout purgado:  {auc:.4f}  (meta: > 0.60)")
    if cv_mean is not None:
        print(f"     AUC purged CV (honesto): {cv_mean:.4f} ± {cv_std:.4f}")
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

    verificar_drift_e_registrar(symbol, "XGBOOST", auc, cv_mean, cv_std)

    os.makedirs("data", exist_ok=True)
    path = _model_path(symbol)
    # Escrita atomica (P1): grava em .tmp e troca com os.replace — crash no meio
    # do retreino nunca corrompe o modelo que o bot carrega no proximo boot.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(
            {
                "modelo": modelo,
                "intervalo": intervalo,
                "auc": auc,  # holdout purgado
                "cv_auc_mean": cv_mean,  # P0-1: estimativa honesta (purged CV)
                "cv_auc_std": cv_std,
                "symbol": symbol,
            },
            f,
        )
    os.replace(tmp_path, path)
    print(f"\n[ML] Modelo salvo em: {path}")
    return modelo


# ── Prever probabilidade ──────────────────────────────────────


def prever(symbol="BTCUSDT"):
    """Retorna probabilidade de alta nas próximas 8 velas, PARA O SYMBOL DADO.

    Esta funcao sempre aceitou `symbol` e sempre o usou de fato (caminho do
    artefato + klines do par). O defeito de E-7 nao estava aqui: era
    `ensemble.prever()` que nao tinha symbol para passar, entao ETHUSDT e
    SOLUSDT recebiam a previsao do modelo de BTCUSDT apesar de ja existirem
    data/modelo_xgb_ethusdt.pkl e _solusdt.pkl treinados.
    """
    symbol = (symbol or "BTCUSDT").upper()
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

    # E-7: conferir a PROCEDENCIA do artefato. O treino ja gravava 'symbol'
    # dentro do pickle e ninguem conferia — um arquivo renomeado, copiado entre
    # maquinas ou restaurado de um backup errado produziria previsao do ativo
    # errado com aparencia perfeitamente normal no log.
    #
    # Artefato SEM a chave 'symbol' (treinado antes de ela existir) nao e
    # recusado: nesse caso a evidencia de procedencia e o proprio caminho, que ja
    # e derivado do par (_model_path). O unico caso sem par no nome e o
    # retrocompat data/modelo_xgb.pkl, aceito so para BTCUSDT — que e o que ele
    # de fato e. O que se recusa e a DIVERGENCIA declarada: o artefato diz um par
    # e pediram outro.
    treinado_em = artefato.get("symbol")
    if treinado_em is not None and str(treinado_em).upper() != symbol:
        return (
            None,
            f"Artefato {path} foi treinado em {str(treinado_em).upper()}, nao em {symbol} — "
            f"recusando para nao prever o ativo errado.",
        )
    if treinado_em is None and path == "data/modelo_xgb.pkl" and symbol != "BTCUSDT":
        return (
            None,
            f"Artefato legado data/modelo_xgb.pkl e de BTCUSDT; recusando para {symbol}.",
        )

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
