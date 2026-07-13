"""
Validação de ML — Purged & Embargoed Cross-Validation (P0-1) +
Guard-rail de drift no retreino automático (P1-4)
=============================================================
Baseado em López de Prado, *Advances in Financial Machine Learning* (cap. 7).

Problema que resolve: os labels do bot olham `JANELA` velas à frente (ex.:
"preço sobe ≥1,5% nas próximas 8 velas"). Num split/K-fold ingênuo, amostras de
TREINO cujo horizonte de rótulo se sobrepõe ao período de TESTE vazam informação
do futuro → AUC de backtest otimista que não se replica ao vivo.

- **Purge:** remove do treino as amostras cujo label-window [i, i+horizonte]
  invade o bloco de teste.
- **Embargo:** remove `embargo` amostras logo após o teste (correlação serial).

Funções puras (só índices) — testáveis sem treinar modelo.
"""

from __future__ import annotations

import statistics

import numpy as np


def purged_kfold_indices(n_amostras, n_splits=5, horizonte=1, embargo=0):
    """
    Gera (train_idx, test_idx) para cada fold de um K-fold PURGADO e EMBARGADO.

    O teste de cada fold é um bloco CONTÍGUO no tempo (sem shuffle). Do treino
    saem: (a) o próprio bloco de teste; (b) purge — amostras nas `horizonte`
    barras ANTES do teste (cujo rótulo entra no teste); (c) embargo — `embargo`
    barras logo APÓS o teste. Assim nenhuma informação do teste vaza no treino.

    horizonte: barras à frente que o label observa (ex.: JANELA=8).
    embargo:   barras removidas após o teste (recomendado >= horizonte).
    """
    if n_splits < 2:
        raise ValueError("n_splits deve ser >= 2")
    if n_amostras < n_splits:
        raise ValueError("n_amostras < n_splits")
    idx = np.arange(n_amostras)
    for test_idx in np.array_split(idx, n_splits):
        if len(test_idx) == 0:
            continue
        t0, t1 = int(test_idx[0]), int(test_idx[-1])
        keep = np.ones(n_amostras, dtype=bool)
        keep[t0 : t1 + 1] = False  # remove o próprio teste
        keep[max(0, t0 - horizonte) : t0] = False  # purge: janelas que entram no teste
        keep[t1 + 1 : min(n_amostras, t1 + 1 + embargo)] = False  # embargo pós-teste
        yield idx[keep], test_idx


def split_holdout_purgado(n_amostras, test_frac=0.2, horizonte=1):
    """
    Holdout cronológico: últimos `test_frac` como teste, resto como treino, com
    PURGE das últimas `horizonte` amostras de treino (que vazariam no teste).
    Retorna (train_idx, test_idx) como arrays de índices.
    """
    if not 0.0 < test_frac < 1.0:
        raise ValueError("test_frac em (0, 1)")
    n_te = max(1, int(round(n_amostras * test_frac)))
    corte = n_amostras - n_te
    train_idx = np.arange(0, max(0, corte - horizonte))
    test_idx = np.arange(corte, n_amostras)
    return train_idx, test_idx


def purged_cv_auc(build_model, X, y, n_splits=5, horizonte=1, embargo=None):
    """
    AUC-ROC por fold usando PurgedKFold. `build_model` é uma fábrica sem args que
    retorna um classificador novo (com `.fit` e `.predict_proba`). Folds sem as
    duas classes (ou treino minúsculo) são pulados. Retorna lista de AUCs (pode
    vir vazia se os dados forem insuficientes).
    """
    from sklearn.metrics import roc_auc_score

    if embargo is None:
        embargo = horizonte
    X = np.asarray(X)
    y = np.asarray(y)
    aucs = []
    for tr, te in purged_kfold_indices(len(X), n_splits, horizonte, embargo):
        if len(tr) < 10 or len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        modelo = build_model()
        modelo.fit(X[tr], y[tr])
        prob = modelo.predict_proba(X[te])[:, 1]
        aucs.append(float(roc_auc_score(y[te], prob)))
    return aucs


def detectar_drift(
    cv_auc_novo,
    historico_cv_auc,
    min_historico=3,
    desvios=2.0,
    piso_absoluto=0.52,
    std_minimo=0.01,
):
    """
    Guard-rail LEVE de drift para o retreino automático de domingo (P1-4) --
    sem MLflow: só compara o AUC honesto (purged CV) do retreino atual contra
    o histórico recente de retreinos do MESMO modelo/symbol
    (database.historico_cv_auc_modelo). Alerta (não bloqueia) via
    database.salvar_bot_event nos chamadores (ml_filtro.py/lstm_modelo.py).

    Retorna (houve_drift: bool, motivo: str | None).

    - cv_auc_novo is None (purged CV sem dados suficientes p/ 5 folds nesse
      retreino) -> nada a comparar, (False, None).
    - cv_auc_novo abaixo de `piso_absoluto` -> drift, INDEPENDENTE de
      histórico (modelo perto de aleatório é sempre um alarme, mesmo no
      1º retreino já registrado).
    - histórico com menos de `min_historico` entradas -> sem base estatística
      para o teste RELATIVO, (False, None) (o teste absoluto acima já rodou).
    - cv_auc_novo abaixo de (média do histórico - `desvios` * desvio-padrão do
      histórico) -> drift. `std_minimo` evita falso-positivo quando o
      histórico por acaso teve variância ~0 (poucos retreinos idênticos).
    """
    if cv_auc_novo is None:
        return False, None

    if cv_auc_novo < piso_absoluto:
        return (
            True,
            f"AUC (purged CV) {cv_auc_novo:.4f} abaixo do piso absoluto "
            f"{piso_absoluto:.2f} (proximo de aleatorio)",
        )

    historico = [v for v in historico_cv_auc if v is not None]
    if len(historico) < min_historico:
        return False, None

    media = statistics.mean(historico)
    desvio = max(statistics.stdev(historico) if len(historico) >= 2 else 0.0, std_minimo)
    limiar = media - desvios * desvio
    if cv_auc_novo < limiar:
        return (
            True,
            f"AUC (purged CV) {cv_auc_novo:.4f} abaixo do limiar de drift "
            f"{limiar:.4f} (media historica {media:.4f} +/- {desvio:.4f}, "
            f"{len(historico)} retreinos)",
        )

    return False, None
