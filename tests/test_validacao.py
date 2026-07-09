"""
Testes de validacao.py — Purged & Embargoed CV (P0-1).

Foco: PROVAR que não há vazamento (leak) — nenhuma amostra de treino cujo
label-window [i, i+horizonte] toque o bloco de teste, nem no embargo pós-teste.
Lógica de índices é pura/determinística — testável sem treinar modelo.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validacao import purged_cv_auc, purged_kfold_indices, split_holdout_purgado

# ── purged_kfold_indices ─────────────────────────────────────────────────────


def test_folds_cobrem_todos_os_indices_sem_sobreposicao():
    n, k = 100, 5
    vistos = []
    for _tr, te in purged_kfold_indices(n, n_splits=k, horizonte=8, embargo=8):
        vistos.append(te)
    todos = np.concatenate(vistos)
    # união dos testes = todos os índices, sem repetição (blocos contíguos)
    assert sorted(todos.tolist()) == list(range(n))


def test_train_e_test_sao_disjuntos():
    for tr, te in purged_kfold_indices(120, n_splits=4, horizonte=5, embargo=5):
        assert set(tr.tolist()).isdisjoint(set(te.tolist()))


def test_sem_leak_purge_e_embargo():
    # PROPRIEDADE CENTRAL: nenhum índice de treino cai em [t0-h, t1+embargo].
    h, emb = 8, 8
    for tr, te in purged_kfold_indices(200, n_splits=5, horizonte=h, embargo=emb):
        t0, t1 = int(te[0]), int(te[-1])
        zona_proibida = set(range(max(0, t0 - h), min(200, t1 + 1 + emb)))
        assert set(tr.tolist()).isdisjoint(
            zona_proibida
        ), f"leak no fold [{t0},{t1}]: treino invade a zona purgada/embargada"


def test_purge_remove_exatamente_horizonte_antes_do_teste():
    # Fold do meio: as `horizonte` amostras imediatamente antes do teste somem.
    folds = list(purged_kfold_indices(100, n_splits=5, horizonte=8, embargo=0))
    _tr, te = folds[2]  # bloco do meio (40..59)
    t0 = int(te[0])
    tr = _tr
    # índices [t0-8, t0-1] não podem estar no treino
    assert all(i not in tr for i in range(t0 - 8, t0))
    # mas t0-9 (fora do purge) permanece
    assert (t0 - 9) in tr


def test_embargo_remove_apos_o_teste():
    folds = list(purged_kfold_indices(100, n_splits=5, horizonte=0, embargo=6))
    _tr, te = folds[1]
    t1 = int(te[-1])
    assert all(i not in _tr for i in range(t1 + 1, t1 + 1 + 6))
    assert (t1 + 7) in _tr  # fora do embargo, volta ao treino


def test_n_splits_invalido_lanca():
    with pytest.raises(ValueError):
        list(purged_kfold_indices(100, n_splits=1, horizonte=8))


# ── split_holdout_purgado ────────────────────────────────────────────────────


def test_holdout_teste_e_o_ultimo_bloco():
    tr, te = split_holdout_purgado(100, test_frac=0.2, horizonte=8)
    assert te.tolist() == list(range(80, 100))  # últimos 20%


def test_holdout_purga_gap_de_horizonte_entre_treino_e_teste():
    tr, te = split_holdout_purgado(100, test_frac=0.2, horizonte=8)
    # treino termina em 71 (80 - 8 - 1); há um gap de 8 antes do teste (80)
    assert tr[-1] == 71
    assert te[0] == 80
    assert set(range(72, 80)).isdisjoint(set(tr.tolist()))


# ── purged_cv_auc ────────────────────────────────────────────────────────────


def test_cv_auc_em_dados_separaveis_bate_acima_de_meio():
    # dataset linearmente separável: modelo trivial deve ter AUC > 0.5
    rng = np.random.RandomState(0)
    n = 300
    X = rng.randn(n, 3)
    y = (X[:, 0] + 0.3 * rng.randn(n) > 0).astype(int)

    from sklearn.linear_model import LogisticRegression

    aucs = purged_cv_auc(lambda: LogisticRegression(max_iter=200), X, y, n_splits=5, horizonte=4)
    assert len(aucs) >= 3
    assert np.mean(aucs) > 0.7  # sinal real → AUC alto


def test_cv_auc_pula_folds_sem_as_duas_classes():
    # y só com uma classe em toda parte → nenhum fold válido → lista vazia
    X = np.random.RandomState(1).randn(100, 2)
    y = np.zeros(100, dtype=int)
    from sklearn.linear_model import LogisticRegression

    aucs = purged_cv_auc(lambda: LogisticRegression(), X, y, n_splits=5, horizonte=4)
    assert aucs == []
