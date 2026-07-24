"""
Testes — research/edge_lab.py (harness de descoberta de edge)
=============================================================
A razão de existir deste harness é NÃO fabricar edges falsos. Estes testes
provam as três propriedades que, se falharem, invalidam toda a pesquisa:

1. CALIBRAÇÃO SOB NULL — com feature e label INDEPENDENTES (incl.
   autocorrelacionados), a taxa de falso-positivo do teste de permutação fica
   ~no nível nominal. É o teste que justifica confiar num p-valor baixo.
2. PODER — um sinal genuinamente preditivo É detectado (p baixo, sinal certo).
3. CORREÇÃO DE MÚLTIPLAS HIPÓTESES — testar muitas features de ruído não
   produz um "vencedor" significativo (o max-statistic corrige o snooping).

+ correção das funções de label e do IC, e a trava de uso único do hold-out.

Herméticos: operam sobre arrays sintéticos; não tocam banco nem
extrair_features (as funções de estatística são puras).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))

import edge_lab as el  # noqa: E402


def _ar1(n, phi, rng, escala=1.0):
    """Série AR(1) (autocorrelacionada) de média 0."""
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, escala)
    return x


def _familia_de(X, fwd_por_h):
    """Monta labels/validos no formato do harness a partir de forward-returns
    já dados (sem NaN internos; máscara = tudo True menos as últimas H)."""
    labels, validos = {}, {}
    n = X.shape[0]
    for H, fwd in fwd_por_h.items():
        lab = np.full(n, np.nan)
        lab[: n - H] = fwd[: n - H]
        labels[H] = lab
        validos[H] = ~np.isnan(lab)
    return labels, validos


# ══════════════════════════════════════════════════════════════
# spearman_ic
# ══════════════════════════════════════════════════════════════


class TestSpearman:
    def test_monotonico_perfeito(self):
        a = np.arange(100.0)
        assert el.spearman_ic(a, a * 3 + 1) == pytest.approx(1.0, abs=1e-9)

    def test_anti_monotonico(self):
        a = np.arange(100.0)
        assert el.spearman_ic(a, -a) == pytest.approx(-1.0, abs=1e-9)

    def test_independente_proximo_de_zero(self):
        rng = np.random.default_rng(0)
        ics = [el.spearman_ic(rng.normal(size=500), rng.normal(size=500)) for _ in range(50)]
        assert abs(np.mean(ics)) < 0.02

    def test_degenerado_retorna_zero(self):
        assert el.spearman_ic(np.ones(50), np.arange(50.0)) == 0.0
        assert el.spearman_ic(np.array([1.0]), np.array([1.0])) == 0.0


# ══════════════════════════════════════════════════════════════
# Labels
# ══════════════════════════════════════════════════════════════


class TestLabelForwardReturn:
    def test_valores_e_mascara(self):
        f = np.array([100.0, 110.0, 121.0, 130.0])
        fwd, vmask = el.label_forward_return(f, H=1)
        assert fwd[0] == pytest.approx(0.10)
        assert fwd[1] == pytest.approx(0.10)
        assert not vmask[-1]  # último candle não tem futuro
        assert vmask[0] and vmask[1] and vmask[2]


class TestLabelTripleBarrier:
    def test_alvo_primeiro(self):
        # candle 1 sobe 5% (>4% alvo), sem tocar -2%: label 1
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 105.0, 100.0])
        n = np.array([100.0, 100.0, 100.0])
        y, v = el.label_triple_barrier(f, m, n, alvo_pct=0.04, stop_pct=0.02, H=1)
        assert y[0] == 1.0 and v[0]

    def test_stop_primeiro(self):
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 100.0, 100.0])
        n = np.array([100.0, 97.0, 100.0])  # cai 3% (< -2% stop)
        y, v = el.label_triple_barrier(f, m, n, alvo_pct=0.04, stop_pct=0.02, H=1)
        assert y[0] == 0.0 and v[0]

    def test_timeout_conta_zero(self):
        f = np.array([100.0, 100.5, 100.0])
        m = np.array([100.0, 101.0, 100.0])  # nunca toca +4%
        n = np.array([100.0, 99.5, 100.0])   # nunca toca -2%
        y, v = el.label_triple_barrier(f, m, n, alvo_pct=0.04, stop_pct=0.02, H=1)
        assert y[0] == 0.0

    def test_candle_ambiguo_stop_prioridade(self):
        # candle 1 toca AMBOS: mínima -3% e máxima +5%. Convenção: stop primeiro.
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 105.0, 100.0])
        n = np.array([100.0, 97.0, 100.0])
        y, v = el.label_triple_barrier(f, m, n, alvo_pct=0.04, stop_pct=0.02, H=1)
        assert y[0] == 0.0  # stop tem prioridade


# ══════════════════════════════════════════════════════════════
# Teste de permutação — CALIBRAÇÃO, PODER, MÚLTIPLAS HIPÓTESES
# ══════════════════════════════════════════════════════════════


class TestPermutacao:
    def test_poder_detecta_sinal_real(self):
        # 1 feature genuinamente preditiva de fwd_ret (H=8) + ruído.
        rng = np.random.default_rng(1)
        n = 2000
        fwd8 = rng.normal(size=n)
        feat_boa = (fwd8 - fwd8.mean()) / fwd8.std() + rng.normal(0, 0.5, size=n)
        # + 3 features de ruído puro
        X = np.column_stack([feat_boa] + [rng.normal(size=n) for _ in range(3)])
        labels, validos = _familia_de(X, {8: fwd8, 24: rng.normal(size=n)})
        r = el.teste_permutacao_max(X, labels, validos, n_perm=300, seed=7)
        assert r["p_valor"] < 0.05, f"não detectou sinal real (p={r['p_valor']})"
        assert r["melhor_feature_idx"] == 0  # a feature boa
        assert r["melhor_horizonte"] == 8
        assert r["melhor_ic"] > 0  # sinal correto

    def test_calibracao_sob_null_ruido_branco(self):
        # Feature e label INDEPENDENTES (ruído branco): taxa de rejeição ~nominal.
        rejeicoes = 0
        n_rep = 40
        for s in range(n_rep):
            rng = np.random.default_rng(100 + s)
            n = 1500
            X = np.column_stack([rng.normal(size=n) for _ in range(4)])
            labels, validos = _familia_de(X, {8: rng.normal(size=n), 24: rng.normal(size=n)})
            r = el.teste_permutacao_max(X, labels, validos, n_perm=200, seed=s)
            if r["p_valor"] < 0.05:
                rejeicoes += 1
        taxa = rejeicoes / n_rep
        # nominal 5%; com 40 reps tolera-se folga estatística até ~17.5%.
        assert taxa <= 0.175, f"falso-positivo alto sob null branco: {taxa:.2%}"

    def test_calibracao_sob_null_autocorrelacionado(self):
        # O teste crítico: feature E label autocorrelacionados mas INDEPENDENTES.
        # Um shuffle ingênuo dispararia falso-positivo; a rotação preserva a
        # autocorrelação no null e mantém a taxa controlada.
        rejeicoes = 0
        n_rep = 40
        for s in range(n_rep):
            rng = np.random.default_rng(500 + s)
            n = 1500
            X = np.column_stack([_ar1(n, 0.9, rng) for _ in range(4)])
            fwd8 = _ar1(n, 0.9, rng)   # autocorrelacionado, independente de X
            fwd24 = _ar1(n, 0.9, rng)
            labels, validos = _familia_de(X, {8: fwd8, 24: fwd24})
            r = el.teste_permutacao_max(X, labels, validos, n_perm=200, seed=s)
            if r["p_valor"] < 0.05:
                rejeicoes += 1
        taxa = rejeicoes / n_rep
        assert taxa <= 0.175, f"falso-positivo alto sob null autocorrelacionado: {taxa:.2%}"

    def test_muitas_features_de_ruido_nao_produzem_vencedor(self):
        # 20 features de ruído puro vs label independente: o max-statistic
        # NÃO deve declarar significância (correção de múltiplas hipóteses).
        rng = np.random.default_rng(9)
        n = 1800
        X = np.column_stack([rng.normal(size=n) for _ in range(20)])
        labels, validos = _familia_de(X, {8: rng.normal(size=n), 24: rng.normal(size=n)})
        r = el.teste_permutacao_max(X, labels, validos, n_perm=300, seed=3)
        assert r["p_valor"] >= 0.05, (
            f"declarou edge falso com 20 features de ruído (p={r['p_valor']}, "
            f"melhor |IC|={abs(r['melhor_ic']):.4f})"
        )


# ══════════════════════════════════════════════════════════════
# Hold-out — trava de uso único
# ══════════════════════════════════════════════════════════════


class TestRetornoBarreira:
    def test_alvo_retorna_mais_alvo(self):
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 105.0, 100.0])
        n = np.array([100.0, 100.0, 100.0])
        assert el.retorno_barreira(f, m, n, 0, 0.04, 0.02, 1) == pytest.approx(0.04)

    def test_stop_retorna_menos_stop(self):
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 100.0, 100.0])
        n = np.array([100.0, 97.0, 100.0])
        assert el.retorno_barreira(f, m, n, 0, 0.04, 0.02, 1) == pytest.approx(-0.02)

    def test_timeout_retorna_mercado(self):
        f = np.array([100.0, 101.0, 100.5])
        m = np.array([100.0, 101.2, 100.6])  # nunca +4%
        n = np.array([100.0, 100.8, 100.4])  # nunca -2%
        # H=2: sai a mercado no candle 2 -> (100.5-100)/100 = 0.005
        assert el.retorno_barreira(f, m, n, 0, 0.04, 0.02, 2) == pytest.approx(0.005)

    def test_stop_prioridade_no_ambiguo(self):
        f = np.array([100.0, 100.0, 100.0])
        m = np.array([100.0, 105.0, 100.0])
        n = np.array([100.0, 97.0, 100.0])
        assert el.retorno_barreira(f, m, n, 0, 0.04, 0.02, 1) == pytest.approx(-0.02)


class TestHoldoutLock:
    def test_recusa_sem_confirmacao(self):
        with pytest.raises(RuntimeError, match="USO ÚNICO"):
            el.avaliar_holdout("BTCUSDT", fi=0, H=8, confirmo_uso_unico=False)
