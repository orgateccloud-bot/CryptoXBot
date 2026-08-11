"""
Testes — o componente CVD do score é matematicamente inerte (frente E-11)
==========================================================================
`score._score_cvd` pesa 7% do score unificado e **nunca sai de 50**. Não é
"fraco": é impossível de disparar, para qualquer dado de entrada.

A PROVA. `data.cvd_calculator.calculate_cvd` devolve

    divergence_score = tanh(slope / std(cvd))

com `slope` de `polyfit(x, cvd, 1)` sobre `x = 0..n-1`. Por Cauchy-Schwarz,
`|Cov(x,y)| <= std(x)·std(y)`, então

    |slope| = |Cov(x,y)| / Var(x) <= std(y) / std(x)
    |slope / std(y)| <= 1 / std(x)        com std(x) = sqrt((n²-1)/12)

Com o `periodo=50` que a produção usa (score.py:95), `std(x) = 14,43` e o teto
de `|divergence_score|` é **tanh(1/14,43) = 0,0692**. O limiar em score.py é
**0,1**. Logo `cvd_trend` é sempre 0, nenhum dos quatro ramos de divergência
casa, e `base_score` fica em 50.

Verificação empírica: 10.000 séries aleatórias e o caso perfeitamente linear
atingem no máximo 0,069185 — exatamente o limite teórico.

POR QUE UM TESTE, E NÃO UMA CORREÇÃO. Ajustar a normalização mudaria a
estratégia viva com base em nada — seria exatamente o "mexer até funcionar"
que os pré-registros desta sessão proíbem. A correção é uma HIPÓTESE, e está
pré-registrada em `research/METODOLOGIA_MICROESTRUTURA.md` para ser testada
com hold-out. Até lá, o que este arquivo garante é que ninguém acredite que o
componente funciona.
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score  # noqa: E402
from data.cvd_calculator import calculate_cvd  # noqa: E402

LIMIAR_SCORE = 0.1  # score.py:147 — `cvd_slope > 0.1` / `< -0.1`
PERIODO_PRODUCAO = 50


def _teto_teorico(n: int) -> float:
    """Maior |divergence_score| possível para uma janela de n pontos."""
    return math.tanh(1.0 / math.sqrt((n * n - 1) / 12))


def _ticks(quantidades, compras=None):
    compras = compras if compras is not None else [False] * len(quantidades)
    return [
        {"timestamp": i, "price": 100.0, "quantity": float(q), "is_buyer_maker": bool(b)}
        for i, (q, b) in enumerate(zip(quantidades, compras))
    ]


class TestLimiteMatematico:
    def test_periodo_de_producao_e_50(self):
        # Se mudar, o teto muda junto e o resto deste arquivo precisa ser
        # relido — n <= 34 volta a conseguir disparar.
        import inspect

        assinatura = inspect.signature(score._score_cvd)
        assert assinatura.parameters["periodo"].default == PERIODO_PRODUCAO

    def test_teto_teorico_fica_abaixo_do_limiar(self):
        teto = _teto_teorico(PERIODO_PRODUCAO)
        assert teto == pytest.approx(0.069185, abs=1e-6)
        assert teto < LIMIAR_SCORE

    def test_a_virada_e_em_n_35(self):
        # Documenta ONDE o componente deixou de funcionar: com janela de ate 34
        # pontos ele dispara; de 35 em diante, nunca.
        assert _teto_teorico(34) > LIMIAR_SCORE
        assert _teto_teorico(35) < LIMIAR_SCORE


class TestVerificacaoEmpirica:
    def test_series_aleatorias_nunca_cruzam_o_limiar(self):
        rng = np.random.default_rng(11)
        pior = 0.0
        for _ in range(2000):
            q = rng.uniform(0.01, 50, PERIODO_PRODUCAO)
            b = rng.random(PERIODO_PRODUCAO) < 0.5
            r = calculate_cvd(_ticks(q, b), window_size=PERIODO_PRODUCAO)
            pior = max(pior, abs(r.divergence_score))
        assert pior < LIMIAR_SCORE, f"max |div| = {pior}"

    def test_cvd_perfeitamente_linear_atinge_o_teto_e_para(self):
        # O melhor caso possível: todo tick é compra do mesmo tamanho, então o
        # CVD é uma reta e a correlação com x é 1. Nem assim passa.
        r = calculate_cvd(
            _ticks([1.0] * PERIODO_PRODUCAO), window_size=PERIODO_PRODUCAO
        )
        assert abs(r.divergence_score) == pytest.approx(
            _teto_teorico(PERIODO_PRODUCAO), abs=1e-9
        )
        assert abs(r.divergence_score) < LIMIAR_SCORE

    def test_salto_gigante_tambem_nao_passa(self):
        q = [0.001] * (PERIODO_PRODUCAO - 1) + [1e6]
        r = calculate_cvd(_ticks(q), window_size=PERIODO_PRODUCAO)
        assert abs(r.divergence_score) < LIMIAR_SCORE

    def test_janela_curta_CONSEGUE_disparar(self):
        """Contraprova: o limiar não é inalcançável por si só — é a JANELA.

        Sem isto, os testes acima seriam compatíveis com "0,1 é alto demais
        para qualquer configuração", e a causa real (n=50) ficaria escondida.
        """
        r = calculate_cvd(_ticks([1.0] * 20), window_size=20)
        assert abs(r.divergence_score) > LIMIAR_SCORE


class TestConsequenciaNoScore:
    def test_componente_cvd_fica_preso_perto_de_50(self):
        # 7% do score unificado que nao discrimina nada: entrega a mesma
        # contribuicao em todo cenario.
        rng = np.random.default_rng(3)
        vistos = set()
        for _ in range(200):
            q = rng.uniform(0.01, 30, PERIODO_PRODUCAO)
            b = rng.random(PERIODO_PRODUCAO) < 0.5
            precos = 100.0 + np.cumsum(rng.normal(0, 0.5, PERIODO_PRODUCAO))
            ticks = [
                {
                    "timestamp": i,
                    "preco": float(precos[i]),
                    "quantidade": float(q[i]),
                    "is_buyer_maker": bool(b[i]),
                }
                for i in range(PERIODO_PRODUCAO)
            ]
            vistos.add(round(score._score_cvd(float(precos[-1]), ticks), 4))
        # O ramo de divergencia nunca casa; sobra o neutro (mais o bonus de
        # trend_strength, que e pequeno).
        assert max(vistos) <= 51.0, f"valores observados: {sorted(vistos)}"
        assert min(vistos) >= 49.0, f"valores observados: {sorted(vistos)}"
