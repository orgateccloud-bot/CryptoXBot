"""
Testes de pureza e determinismo do motor de backtesting.
Não abre banco, não faz requests — usa dados sintéticos em memória.
"""

import math
import os
import statistics
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtesting.metricas as metricas
import backtesting.motor as motor

# ── Testes: calcular_metricas ─────────────────────────────────────────────────


class TestCalcularMetricas:
    def _op(self, resultado: float) -> dict:
        return {
            "entrada_idx": 0,
            "saida_idx": 10,
            "entrada_dt": "01/01/2025 00:00",
            "saida_dt": "01/01/2025 10:00",
            "preco_entrada": 60000.0,
            "preco_saida": 60000.0 + resultado * 1000,
            "resultado": resultado,
            "resultado_pct": resultado / 600,
            "tipo_saida": "TARGET" if resultado > 0 else "STOP",
            "capital_apos": 1000 + resultado,
        }

    def test_win_rate_100(self):
        ops = [self._op(50.0), self._op(30.0), self._op(20.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1100.0, "1h")
        assert r["win_rate_%"] == pytest.approx(100.0)

    def test_win_rate_50(self):
        ops = [self._op(50.0), self._op(-30.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1020.0, "1h")
        assert r["win_rate_%"] == pytest.approx(50.0)

    def test_win_rate_0(self):
        ops = [self._op(-20.0), self._op(-15.0)]
        r = motor.calcular_metricas(ops, 1000.0, 965.0, "1h")
        assert r["win_rate_%"] == pytest.approx(0.0)

    def test_retorno_total_positivo(self):
        ops = [self._op(50.0), self._op(30.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1080.0, "1h")
        assert r["retorno_total_%"] == pytest.approx(8.0)

    def test_retorno_total_negativo(self):
        ops = [self._op(-100.0)]
        r = motor.calcular_metricas(ops, 1000.0, 900.0, "1h")
        assert r["retorno_total_%"] == pytest.approx(-10.0)

    def test_profit_factor_sem_perdas(self):
        ops = [self._op(50.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1050.0, "1h")
        assert r["profit_factor"] == pytest.approx(999.0)

    def test_profit_factor_calculado(self):
        ops = [self._op(60.0), self._op(-30.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1030.0, "1h")
        assert r["profit_factor"] == pytest.approx(2.0)

    def test_sem_operacoes_retorna_erro(self):
        r = motor.calcular_metricas([], 1000.0, 1000.0, "1h")
        assert "erro" in r

    def test_total_trades(self):
        ops = [self._op(10.0), self._op(-5.0), self._op(8.0)]
        r = motor.calcular_metricas(ops, 1000.0, 1013.0, "1h")
        assert r["total_trades"] == 3

    def test_determinismo(self):
        """Mesma entrada → mesma saída (sem randomidade)."""
        ops = [self._op(50.0), self._op(-20.0), self._op(30.0)]
        r1 = motor.calcular_metricas(ops, 1000.0, 1060.0, "1h")
        r2 = motor.calcular_metricas(ops, 1000.0, 1060.0, "1h")
        assert r1["win_rate_%"] == r2["win_rate_%"]
        assert r1["profit_factor"] == r2["profit_factor"]
        assert r1["retorno_total_%"] == r2["retorno_total_%"]


# ── Testes: simular_historico_ticks ──────────────────────────────────────────


class TestSimularHistoricoTicks:
    def _kline(self, abertura, fechamento, volume=100.0):
        return (0, abertura, fechamento + 50, abertura - 30, fechamento, volume)

    def test_retorna_lista_nao_vazia(self):
        klines = [self._kline(60000.0, 60500.0) for _ in range(10)]
        ticks = motor.simular_historico_ticks(klines)
        assert len(ticks) > 0

    def test_limite_50_ticks(self):
        klines = [self._kline(60000.0, 60500.0) for _ in range(100)]
        ticks = motor.simular_historico_ticks(klines)
        assert len(ticks) <= 50

    def test_candle_bullish_mais_buyer(self):
        """Candle bullish: fechamento > abertura → mais volume buyer."""
        klines = [self._kline(60000.0, 61000.0, volume=100.0)]  # bullish
        ticks = motor.simular_historico_ticks(klines)
        buyer_vol = sum(t["quantidade"] for t in ticks if not t["is_buyer_maker"])
        seller_vol = sum(t["quantidade"] for t in ticks if t["is_buyer_maker"])
        assert buyer_vol > seller_vol

    def test_campos_tick(self):
        klines = [self._kline(60000.0, 60500.0)]
        ticks = motor.simular_historico_ticks(klines)
        for tick in ticks:
            assert "preco" in tick
            assert "is_buyer_maker" in tick
            assert "quantidade" in tick


# ── Testes: calcular_ema / calcular_rsi ──────────────────────────────────────


class TestIndicadoresMotor:
    def test_ema_comprimento(self):
        dados = list(range(100, 160))
        result = motor.calcular_ema(dados, 20)
        assert len(result) == len(dados)

    def test_ema_primeiros_nulos(self):
        dados = list(range(50))
        result = motor.calcular_ema(dados, 20)
        nulos = [x for x in result[:19] if x is None]
        assert len(nulos) == 19

    def test_rsi_comprimento(self):
        dados = [float(i) for i in range(100, 150)]
        result = motor.calcular_rsi(dados)
        assert len(result) == len(dados)

    def test_rsi_range_valido(self):
        import math

        # Sequência oscilante para garantir tanto ganhos quanto perdas
        dados = [60000.0 + (i % 5) * 100 - (i % 3) * 50 for i in range(100)]
        result = motor.calcular_rsi(dados)
        for v in result:
            if v is not None and not math.isnan(float(v)):
                assert 0 <= float(v) <= 100

    def test_constantes_ema(self):
        assert motor.EMA_RAPIDA == 20
        assert motor.EMA_LENTA == 50


# ── Testes: backtesting/metricas.py ──────────────────────────────────────────


class TestSharpeRatio:
    def test_poucos_dados_retorna_zero(self):
        assert metricas.sharpe_ratio([1.0]) == 0.0
        assert metricas.sharpe_ratio([]) == 0.0

    def test_stdev_zero_retorna_zero(self):
        assert metricas.sharpe_ratio([2.0, 2.0, 2.0]) == 0.0

    def test_valor_normal(self):
        retornos = [1.0, 2.0, -1.0, 3.0, 0.5]
        media = statistics.mean(retornos)
        std = statistics.stdev(retornos)
        esperado = (media / std) * (252**0.5)
        assert metricas.sharpe_ratio(retornos) == pytest.approx(esperado)

    def test_nao_anualizado_periodos_1(self):
        retornos = [1.0, 2.0, -1.0]
        media = statistics.mean(retornos)
        std = statistics.stdev(retornos)
        assert metricas.sharpe_ratio(retornos, periodos_por_ano=1) == pytest.approx(media / std)


class TestSortinoRatio:
    def test_sem_downside_retorna_sentinel(self):
        assert metricas.sortino_ratio([1.0, 2.0, 3.0]) == pytest.approx(999.0)

    def test_com_downside(self):
        retornos = [2.0, -1.0, 3.0, -2.0]
        media = statistics.mean(retornos)
        dd = (sum(min(0, r) ** 2 for r in retornos) / len(retornos)) ** 0.5
        esperado = (media / dd) * (252**0.5)
        assert metricas.sortino_ratio(retornos) == pytest.approx(esperado)

    def test_mar_diferente_zero(self):
        retornos = [1.0, 2.0, 0.5]
        mar = 1.5
        media = statistics.mean(retornos)
        dd = (sum(min(0, r - mar) ** 2 for r in retornos) / len(retornos)) ** 0.5
        esperado = ((media - mar) / dd) * (252**0.5)
        assert metricas.sortino_ratio(retornos, mar_pct=mar) == pytest.approx(esperado)

    def test_lista_vazia(self):
        assert metricas.sortino_ratio([]) == 0.0

    def test_downside_dev_zero_e_media_igual_mar_retorna_zero(self):
        # todos os retornos == mar_pct: downside_dev==0 mas media NAO excede
        # mar_pct -> deve cair no ramo "else 0.0" (nao no sentinel 999.0),
        # unico jeito de exercitar esse ramo sem violar downside_dev==0.
        assert metricas.sortino_ratio([2.0, 2.0, 2.0], mar_pct=2.0) == 0.0


class TestCalmarRatio:
    def test_max_dd_zero_sentinel(self):
        assert metricas.calmar_ratio(20.0, 0.0, 365) == pytest.approx(999.0)
        assert metricas.calmar_ratio(-5.0, 0.0, 365) == pytest.approx(0.0)

    def test_dias_periodo_pequeno_floor(self):
        # dias_periodo=0 deve virar max(0,1)=1 (nao ZeroDivisionError/dias=0
        # no expoente) -- valor exato calculado com o floor ja aplicado, para
        # que uma regressao no floor (ex: trocar por "dias_periodo or 1", ou
        # remove-lo) derrube o teste em vez de so precisar "nao lancar excecao".
        retorno_anualizado_esperado = ((1 + 5.0 / 100) ** (365 / 1) - 1) * 100
        esperado = retorno_anualizado_esperado / 10.0
        assert metricas.calmar_ratio(5.0, 10.0, 0) == pytest.approx(esperado)

    def test_retorno_negativo(self):
        # base=0.9, dias_periodo==dias_ano -> expoente 1, retorno_anualizado
        # bate com o proprio retorno_total_pct.
        assert metricas.calmar_ratio(-10.0, 15.0, 365) == pytest.approx(-10.0 / 15.0)

    def test_perda_total_ou_pior_retorna_sentinel_negativo(self):
        # retorno_total_pct <= -100% -> base<=0 -> potencia fracionaria
        # produziria numero complexo (TypeError em round()) sem o guard.
        assert metricas.calmar_ratio(-100.0, 20.0, 100) == pytest.approx(-999.0)
        assert metricas.calmar_ratio(-150.0, 20.0, 100) == pytest.approx(-999.0)


class TestProfitFactor:
    def test_sem_perdas(self):
        assert metricas.profit_factor(100.0, 0.0) == pytest.approx(999.0)

    def test_calculado(self):
        assert metricas.profit_factor(100.0, 50.0) == pytest.approx(2.0)


class TestProbabilisticSharpeRatio:
    def test_poucos_dados_retorna_neutro(self):
        assert metricas.probabilistic_sharpe_ratio([1.0]) == 0.5

    def test_formula_exata_skew_normal(self):
        retornos = [0.5, 1.2, -0.3, 0.8, 0.1, -0.5, 1.0]
        n = len(retornos)
        sr_hat = metricas.sharpe_ratio(retornos, periodos_por_ano=1)
        skew, kurt = metricas._skew_kurtosis_populacional(retornos)
        denom = (1 - skew * sr_hat + ((kurt - 1) / 4) * sr_hat**2) ** 0.5
        z = (sr_hat - 0.0) * ((n - 1) ** 0.5) / denom
        esperado = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        assert metricas.probabilistic_sharpe_ratio(retornos, 0.0) == pytest.approx(esperado)

    def test_benchmark_maior_reduz_psr(self):
        retornos = [1.0, 2.0, -0.5, 1.5]
        assert metricas.probabilistic_sharpe_ratio(
            retornos, 5.0
        ) < metricas.probabilistic_sharpe_ratio(retornos, 0.0)

    def test_denom_negativo_retorna_neutro(self, monkeypatch):
        # forca denom_sq = 1 - skew*sr_hat + ((kurt-1)/4)*sr_hat^2 <= 0 via
        # skew extremo -- o guard deve devolver 0.5 em vez de tentar sqrt
        # de numero negativo.
        monkeypatch.setattr(metricas, "_skew_kurtosis_populacional", lambda r: (1e6, 3.0))
        assert metricas.probabilistic_sharpe_ratio([0.1, 0.2, 0.15, 0.3], 0.0) == 0.5

    def test_skew_zero_kurtosis_normal_recai_formula_classica(self, monkeypatch):
        # skew=0, kurt=3 (raw, normal) -- a formula geral deve recair
        # exatamente na forma classica Phi((SR_hat-SR*)*sqrt(n-1) /
        # sqrt(1+0.5*SR_hat^2)), calculada aqui com o expoente 0.5 escrito a
        # mao (nao via _skew_kurtosis_populacional) para nao repetir a
        # mesma chamada que o codigo sob teste usa.
        retornos = [0.5, 1.2, -0.3, 0.8, 0.1, -0.5, 1.0]
        monkeypatch.setattr(metricas, "_skew_kurtosis_populacional", lambda r: (0.0, 3.0))
        n = len(retornos)
        sr_hat = metricas.sharpe_ratio(retornos, periodos_por_ano=1)
        z = (sr_hat - 0.0) * ((n - 1) ** 0.5) / ((1 + 0.5 * sr_hat**2) ** 0.5)
        esperado = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        assert metricas.probabilistic_sharpe_ratio(retornos, 0.0) == pytest.approx(esperado)


class TestSharpeEsperadoMax:
    def test_n_igual_1_retorna_zero(self):
        assert metricas.sharpe_esperado_max([1.0]) == 0.0
        assert metricas.sharpe_esperado_max([]) == 0.0

    def test_formula_exata_n5(self):
        sharpes = [0.5, 1.0, 0.2, 0.8, 1.3]
        n = len(sharpes)
        sr_bar = statistics.mean(sharpes)
        sigma = statistics.stdev(sharpes)
        gamma = metricas.GAMMA_EULER_MASCHERONI
        nd = statistics.NormalDist()
        esperado = sr_bar + sigma * (
            (1 - gamma) * nd.inv_cdf(1 - 1 / n) + gamma * nd.inv_cdf(1 - 1 / (n * math.e))
        )
        assert metricas.sharpe_esperado_max(sharpes) == pytest.approx(esperado)

    def test_desloca_exatamente_com_a_media_dos_trials(self):
        # Invariante independente da formula (nao reimplementa a expressao
        # do codigo-fonte): deslocar TODOS os sharpes de N trials por uma
        # constante c desloca o Sharpe esperado maximo pela MESMA constante
        # c (SR_bar desloca por c, sigma_N e os quantis normais nao mudam).
        # Uma implementacao que omitisse o termo SR_bar (bug real encontrado
        # em revisao adversarial desta task) seria invariante a esse
        # deslocamento -- este teste falha imediatamente nesse caso.
        base = [0.5, 1.0, 0.2, 0.8, 1.3]
        c = 0.7
        deslocado = [s + c for s in base]
        assert metricas.sharpe_esperado_max(deslocado) == pytest.approx(
            metricas.sharpe_esperado_max(base) + c
        )

    def test_sigma_zero_retorna_media_dos_trials(self):
        # todos os trials com o mesmo Sharpe -> sem variabilidade entre
        # trials, o "maximo esperado" e trivialmente esse proprio valor
        # (nao 0.0 -- so N<=1 e degenerado o suficiente para isso).
        assert metricas.sharpe_esperado_max([0.8, 0.8, 0.8]) == pytest.approx(0.8)

    def test_deflator_cresce_com_n(self):
        base = [0.5, 1.0, 0.2, 0.8]
        maior = base + [1.5, -0.3, 0.9, 1.1]
        assert metricas.sharpe_esperado_max(maior) >= metricas.sharpe_esperado_max(base) - 1e-9


class TestDeflatedSharpeRatio:
    def test_sem_trials_igual_psr_zero(self):
        retornos = [1.0, 2.0, -0.5, 1.5, 0.8]
        assert metricas.deflated_sharpe_ratio(retornos, None) == pytest.approx(
            metricas.probabilistic_sharpe_ratio(retornos, 0.0)
        )
        assert metricas.deflated_sharpe_ratio(retornos, [0.5]) == pytest.approx(
            metricas.probabilistic_sharpe_ratio(retornos, 0.0)
        )

    def test_invariante_dsr_menor_igual_psr0(self):
        # Cenario numerico concreto: sharpe_esperado_max = SR_bar + sigma_N *
        # [...]. Os 7 trials tem SR_bar=5.1/7~=0.729 (positivo) e o termo
        # sigma_N*[...] tambem e >=0 para N>=2 (ambos os quantis normais sao
        # positivos quando N>=2 -- Zinv(1-1/N) e Zinv(1-1/(N*e)) so ficam
        # negativos para N=1, caso ja tratado a parte). Logo SR*=
        # sharpe_esperado_max(sharpes_trials) >= SR_bar > 0, e DSR =
        # PSR(SR*>=0) <= PSR(0) sempre, por monotonicidade de PSR no benchmark.
        retornos = [1.0, 2.0, -0.5, 1.5, 0.8, -1.0, 2.2, 0.5, -0.3, 1.8]
        sharpes_trials = [0.9, 0.4, 1.3, 0.2, 0.7, 1.1, 0.5]
        psr0 = metricas.probabilistic_sharpe_ratio(retornos, 0.0)
        dsr = metricas.deflated_sharpe_ratio(retornos, sharpes_trials)
        assert dsr <= psr0 + 1e-9

    def test_wiring_correto(self):
        retornos = [1.0, 2.0, -0.5, 1.5, 0.8]
        sharpes_trials = [0.9, 0.4, 1.3, 0.2]
        sr_star = metricas.sharpe_esperado_max(sharpes_trials)
        assert metricas.deflated_sharpe_ratio(retornos, sharpes_trials) == pytest.approx(
            metricas.probabilistic_sharpe_ratio(retornos, sr_star)
        )
