"""
Unit Tests — Data Layer
=======================
Testes unitários para CVD calculator e indicadores.
"""

import unittest
import numpy as np
from data.cvd_calculator import calculate_cvd, calculate_cvd_from_prices, CVDResult


class TestCVDCalculator(unittest.TestCase):
    """Testes para CVD Calculator."""

    def setUp(self):
        """Setup de dados de teste."""
        self.ticks_buy = [
            {'price': 50000, 'quantity': 1.0, 'is_buyer_maker': False, 'timestamp': 1000},
            {'price': 50001, 'quantity': 2.0, 'is_buyer_maker': False, 'timestamp': 1001},
        ]
        self.ticks_sell = [
            {'price': 50000, 'quantity': 1.0, 'is_buyer_maker': True, 'timestamp': 1000},
            {'price': 49999, 'quantity': 1.5, 'is_buyer_maker': True, 'timestamp': 1001},
        ]
        self.ticks_mixed = self.ticks_buy + self.ticks_sell

    def test_calculate_cvd_buy_only(self):
        """Test CVD com apenas compras."""
        result = calculate_cvd(self.ticks_buy)
        self.assertIsInstance(result, CVDResult)
        self.assertEqual(result.cvd_values, [1.0, 3.0])
        self.assertEqual(len(result.timestamps), 2)

    def test_calculate_cvd_sell_only(self):
        """Test CVD com apenas vendas."""
        result = calculate_cvd(self.ticks_sell)
        self.assertEqual(result.cvd_values, [-1.0, -2.5])

    def test_calculate_cvd_mixed(self):
        """Test CVD com compras e vendas."""
        result = calculate_cvd(self.ticks_mixed)
        expected_cvd = [1.0, 3.0, 2.0, 0.5]  # buy +2, sell -1.5
        self.assertEqual(result.cvd_values, expected_cvd)

    def test_calculate_cvd_empty(self):
        """Test CVD com lista vazia."""
        result = calculate_cvd([])
        self.assertEqual(result.cvd_values, [])
        self.assertEqual(result.divergence_score, 0.0)

    def test_calculate_cvd_divergence(self):
        """Test cálculo de divergence score."""
        # Preço subindo, CVD caindo (bearish divergence)
        ticks_divergence = [
            {'price': 50000, 'quantity': 2.0, 'is_buyer_maker': False, 'timestamp': 1000},  # buy
            {'price': 50010, 'quantity': 3.0, 'is_buyer_maker': True, 'timestamp': 1001},   # sell
            {'price': 50020, 'quantity': 1.0, 'is_buyer_maker': True, 'timestamp': 1002},   # sell
        ]
        result = calculate_cvd(ticks_divergence, window_size=3)
        self.assertLess(result.divergence_score, 0)  # bearish

    def test_calculate_cvd_from_arrays(self):
        """Test função vetorizada direta."""
        prices = np.array([50000, 50001, 50002])
        volumes = np.array([1.0, 0.5, 2.0])
        directions = np.array([1, -1, 1])  # buy, sell, buy

        cvd = calculate_cvd_from_prices(prices, volumes, directions)
        expected = np.array([1.0, 0.5, 2.5])
        np.testing.assert_array_equal(cvd, expected)


class TestIndicadores(unittest.TestCase):
    """Testes para indicadores técnicos."""

    def setUp(self):
        """Setup de dados de teste."""
        self.prices = np.array([50000, 50100, 49900, 50200, 49800, 50300])

    def test_ema_calculation(self):
        """Test EMA calculation."""
        # Importar função de indicadores
        from indicadores import ema

        ema_values = ema(self.prices, periodo=3)
        self.assertEqual(len(ema_values), len(self.prices))

        # EMA deve ser smoother que preço
        price_volatility = np.std(np.diff(self.prices))
        ema_volatility = np.std(np.diff(ema_values[~np.isnan(ema_values)]))
        self.assertLess(ema_volatility, price_volatility)

    def test_rsi_calculation(self):
        """Test RSI calculation."""
        from indicadores import rsi

        rsi_values = rsi(self.prices, periodo=3)
        self.assertEqual(len(rsi_values), len(self.prices))

        # RSI deve estar entre 0-100
        valid_rsi = rsi_values[~np.isnan(rsi_values)]
        for rsi_val in valid_rsi:
            self.assertGreaterEqual(rsi_val, 0)
            self.assertLessEqual(rsi_val, 100)

    def test_sma_calculation(self):
        """Test SMA calculation."""
        from indicadores import sma

        sma_values = sma(self.prices, periodo=3)
        # SMA com convolve retorna array menor
        self.assertEqual(len(sma_values), len(self.prices) - 3 + 1)

        # SMA período 3 dos primeiros valores
        expected_first = np.mean(self.prices[:3])
        self.assertAlmostEqual(sma_values[0], expected_first, places=2)


if __name__ == '__main__':
    unittest.main()