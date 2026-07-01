"""
Testes de pureza e determinismo do motor de backtesting.
Não abre banco, não faz requests — usa dados sintéticos em memória.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
