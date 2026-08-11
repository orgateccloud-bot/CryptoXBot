"""
Testes — backtesting/trend_following.py (redesenho trend-following)
===================================================================
Cobrem as duas coisas que, se erradas, invalidam a pesquisa:
1. CAUSALIDADE do Donchian (níveis[i] usam só candles < i — sem look-ahead).
2. CONTABILIDADE do backtest (entrada/saída nos níveis certos, taxa dos dois
   lados, sizing por risco, censura final).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtesting import trend_following as tf  # noqa: E402


class TestDonchian:
    def test_niveis_causais(self):
        c = np.array([10.0, 11, 12, 13, 14, 9, 8])
        up, low = tf.donchian_niveis(c, 3)
        assert np.isnan(up[0]) and np.isnan(up[2])  # antes de ter 3 anteriores
        # i=3: janela = closes[0:3] = [10,11,12] -> up 12, low 10
        assert up[3] == 12.0 and low[3] == 10.0
        # i=5: janela = closes[2:5] = [12,13,14] -> up 14, low 12
        assert up[5] == 14.0 and low[5] == 12.0

    def test_nao_inclui_candle_atual(self):
        # se incluísse o próprio candle, up[i] >= c[i] sempre; provamos que não.
        c = np.array([10.0, 11, 12, 20, 13])
        up, _ = tf.donchian_niveis(c, 3)
        # i=3: janela [10,11,12], up=12 < c[3]=20 (rompimento detectável)
        assert up[3] == 12.0 and c[3] > up[3]


class TestSimularTrend:
    def test_entrada_no_rompimento_e_saida_no_fundo(self):
        # sobe rompendo, depois cai rompendo o fundo dos M anteriores.
        # N=3, M=2. closes desenhados p/ 1 trade completo.
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8], dtype=float)
        r = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=1.0)
        assert r["n_trades"] == 1
        t = r["trades"][0]
        # entrada em i=4 (c=12 > max(c[1:4])=10); saída no 1º i com c < min(c[i-2:i])
        # i=7: c=8 < min(c[5:7])=12 -> sai. bruto = (8-12)/12 = -33.3%
        assert t["ret_bruto_pct"] == pytest.approx((8 - 12) / 12 * 100, abs=1e-6)

    def test_taxa_aplicada_dos_dois_lados(self):
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8], dtype=float)
        r0 = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=1.0)
        r1 = tf.simular_trend(c, N=3, M=2, taxa=0.001, slippage=0.0, risco_frac=1.0)
        # com taxa, o PnL líquido é menor (paga fee nos 2 lados)
        assert r1["trades"][0]["pnl"] < r0["trades"][0]["pnl"]

    def test_slippage_piora_entrada_e_saida(self):
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8], dtype=float)
        r0 = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=1.0)
        r1 = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0005, risco_frac=1.0)
        assert r1["trades"][0]["pnl"] < r0["trades"][0]["pnl"]

    def test_sizing_por_risco(self):
        # risco_frac 2%: notional = 0.02*capital / risco. Verifica que o PnL
        # escala com o notional esperado, não com o capital inteiro.
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8], dtype=float)
        r = tf.simular_trend(
            c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=0.02, capital_inicial=1000.0
        )
        # entrada=12, stop=min(c[1:4])=10 -> risco=(12-10)/12=0.1667; notional=0.02*1000/0.1667=120
        # bruto = 120 * (8-12)/12 = -40
        assert r["trades"][0]["pnl"] == pytest.approx(-40.0, abs=1e-3)

    def test_uma_posicao_por_vez(self):
        # múltiplos rompimentos de topo enquanto em posição não abrem 2ª entrada
        c = np.array([10, 10, 10, 10, 12, 14, 16, 8, 8], dtype=float)
        r = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=1.0)
        assert r["n_trades"] == 1  # só 1 trade apesar de 3 topos consecutivos

    def test_censura_final_fecha_posicao_aberta(self):
        # entra e nunca sai (sem rompimento de fundo) -> fecha a mercado no fim
        c = np.array([10, 10, 10, 10, 12, 13, 14, 15, 16], dtype=float)
        r = tf.simular_trend(c, N=3, M=2, taxa=0.0, slippage=0.0, risco_frac=1.0)
        assert r["n_trades"] == 1
        # saída a mercado no último close (16), entrada em 12 -> ganho
        assert r["trades"][0]["pnl"] > 0

    def test_sem_trades_retorna_estrutura_vazia(self):
        c = np.array([10.0] * 20)  # sem rompimento nunca
        r = tf.simular_trend(c, N=3, M=2)
        assert r["n_trades"] == 0

    def test_trades_carregam_ts_saida(self):
        """A curva de CARTEIRA mescla trades de vários ativos por instante de
        saída — sem ts_saida a ordenação (e o DD de carteira) estaria errada."""
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8], dtype=float)
        ts = np.array([1_700_000_000_000 + i * 86400_000 for i in range(len(c))], dtype=np.int64)
        r = tf.simular_trend(c, N=3, M=2, ts=ts, taxa=0.0, slippage=0.0)
        assert r["n_trades"] == 1
        assert r["trades"][0]["ts_saida"] == int(ts[7])  # saiu no candle 7

    def test_payoff_e_metricas_presentes(self):
        c = np.array([10, 10, 10, 10, 12, 12, 12, 8, 8, 8, 10, 14, 14, 14, 9, 9], dtype=float)
        r = tf.simular_trend(c, N=3, M=2, taxa=0.001, slippage=0.0005)
        for chave in (
            "win_rate",
            "profit_factor",
            "payoff_ratio",
            "expectancy_net_pct",
            "max_drawdown_pct",
            "bh_retorno_pct",
        ):
            assert chave in r
