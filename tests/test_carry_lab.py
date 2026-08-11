"""
Testes — research/carry_lab.py (medição do carry delta-neutro)
==============================================================
A medição é aritmética simples, e é exatamente por isso que precisa de teste:
um erro de sinal (funding negativo somando como receita), de capital (esquecer
o 1.25×) ou de custo (não cobrar o round-trip) inflaria o resultado e poderia
aprovar uma estratégia ruim.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research")
)
import carry_lab as cl  # noqa: E402

MS_8H = 8 * 3600 * 1000
T0 = 1_600_000_000_000


def serie(rates, t0=T0):
    ts = np.array([t0 + i * MS_8H for i in range(len(rates))], dtype=np.int64)
    return ts, np.array(rates, dtype=float)


class TestVarianteA:
    def test_retorno_sobre_notional_e_soma_do_funding(self):
        rates = [0.0001] * 30
        ts, r = serie(rates)
        res = cl.variante_A(ts, r)
        assert res["ret_notional"] == pytest.approx(0.003, abs=1e-9)

    def test_custo_round_trip_cobrado_uma_vez(self):
        rates = [0.0001] * 30  # Σ = 0.003 = exatamente o custo
        ts, r = serie(rates)
        res = cl.variante_A(ts, r)
        # líquido no notional = 0.003 - 0.003 = 0 -> capital também 0
        assert res["ret_capital"] == pytest.approx(0.0, abs=1e-9)

    def test_capital_divide_por_1_25(self):
        # Σ funding = 0.00625; líquido = 0.00625-0.003 = 0.00325
        # sobre capital = 0.00325/1.25 = 0.0026
        ts, r = serie([0.000125] * 50)
        res = cl.variante_A(ts, r)
        assert res["ret_capital"] == pytest.approx(0.0026, abs=1e-9)

    def test_funding_negativo_reduz_retorno(self):
        ts_p, r_p = serie([0.0002] * 40)
        ts_n, r_n = serie([0.0002] * 20 + [-0.0002] * 20)
        assert cl.variante_A(ts_n, r_n)["ret_capital"] < cl.variante_A(ts_p, r_p)["ret_capital"]

    def test_funding_todo_negativo_da_retorno_negativo(self):
        ts, r = serie([-0.0001] * 60)
        assert cl.variante_A(ts, r)["ret_capital"] < 0

    def test_anualizacao_simples(self):
        # 3 pagamentos/dia × 365.25 dias = 1095.75 pagamentos ≈ 1 ano
        n = int(3 * 365.25)
        ts, r = serie([0.0001] * n)
        res = cl.variante_A(ts, r)
        assert res["anos"] == pytest.approx(1.0, abs=0.01)
        # anualizado ≈ ret_capital / 1 ano
        assert res["anualizado"] == pytest.approx(res["ret_capital"], rel=0.02)

    def test_fracao_pagamentos_negativos(self):
        ts, r = serie([0.0001] * 75 + [-0.0001] * 25)
        assert cl.variante_A(ts, r)["frac_pagamentos_neg"] == pytest.approx(0.25)

    def test_serie_curta_retorna_vazio(self):
        ts, r = serie([0.0001])
        assert cl.variante_A(ts, r)["n"] == 0

    def test_pior_mes_identificado(self):
        # 2 meses: 1º com custo + funding baixo, 2º com funding alto
        rates = [0.00001] * 90 + [0.0005] * 90
        ts, r = serie(rates)
        res = cl.variante_A(ts, r)
        # o pior mês deve ser negativo (custo de 0.30% no início)
        assert res["pior_mes"] < 0


class TestVarianteB:
    def test_evita_periodo_negativo(self):
        """B deve superar A quando há um longo período de funding negativo,
        porque sai da estrutura (é a razão de existir do filtro)."""
        rates = [0.0003] * 200 + [-0.0003] * 200 + [0.0003] * 200
        ts, r = serie(rates)
        a = cl.variante_A(ts, r)["ret_capital"]
        b = cl.variante_B(ts, r)["ret_capital"]
        assert b > a

    def test_cobra_custo_por_alternancia(self):
        rates = [0.0003] * 100 + [-0.0003] * 100
        ts, r = serie(rates)
        res = cl.variante_B(ts, r)
        assert res["alternancias"] >= 1

    def test_filtro_e_causal(self):
        """O filtro só olha o passado: mudar o ÚLTIMO funding não pode alterar
        nenhuma decisão anterior (só o retorno do próprio último período)."""
        base = [0.0002] * 100
        ts, r1 = serie(base + [0.0002])
        _, r2 = serie(base + [-0.5])  # último valor absurdo
        b1 = cl.variante_B(ts, r1)
        b2 = cl.variante_B(ts, r2)
        assert b1["alternancias"] == b2["alternancias"]
