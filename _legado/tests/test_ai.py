"""
Unit Tests — AI Layer
=====================
Testes unitários para inference assíncrona.
"""

import unittest
import asyncio
import numpy as np
from unittest.mock import AsyncMock, MagicMock
from ai.inference import AIInference, PredictionResult, EnsembleResult


class TestAIInference(unittest.TestCase):
    """Testes para AI Inference."""

    def setUp(self):
        """Setup."""
        self.inference = AIInference(max_workers=2)

    def tearDown(self):
        """Cleanup."""
        asyncio.run(self.inference.close())

    def test_initialization(self):
        """Test inicialização."""
        self.assertIsNone(self.inference.xgb_model)
        self.assertIsNone(self.inference.mlp_model)
        self.assertEqual(len(self.inference.feature_names), 8)

    async def test_load_models(self):
        """Test carregamento de modelos."""
        await self.inference.load_models()
        # Em produção, verificaríamos se modelos foram carregados
        # Aqui apenas verificamos que não houve erro

    async def test_predict_async(self):
        """Test predição assíncrona."""
        await self.inference.load_models()

        features = {
            'rsi': 65.0,
            'ema_short': 50050.0,
            'ema_long': 50000.0,
            'sma_volume': 1.5,
            'cvd_slope': 0.3,
            'price_change': 0.001,
            'volume_ratio': 1.2,
            'divergence_score': 0.1
        }

        result = await self.inference.predict_async(features)

        self.assertIsInstance(result, EnsembleResult)
        self.assertIsInstance(result.final_signal, int)
        self.assertGreaterEqual(result.ensemble_confidence, 0.0)
        self.assertLessEqual(result.ensemble_confidence, 1.0)
        self.assertIn('xgb', result.model_contributions)
        self.assertIn('mlp', result.model_contributions)

    async def test_predict_regime_aware(self):
        """Test weighting regime-aware."""
        await self.inference.load_models()

        # Test bullish regime
        features_bullish = {
            'rsi': 70.0, 'ema_short': 50100.0, 'ema_long': 50000.0,
            'sma_volume': 2.0, 'cvd_slope': 0.8,  # bullish CVD
            'price_change': 0.002, 'volume_ratio': 1.5, 'divergence_score': 0.2
        }

        result_bullish = await self.inference.predict_async(features_bullish)

        # Test bearish regime
        features_bearish = {
            'rsi': 30.0, 'ema_short': 49900.0, 'ema_long': 50000.0,
            'sma_volume': 1.0, 'cvd_slope': -0.8,  # bearish CVD
            'price_change': -0.002, 'volume_ratio': 0.8, 'divergence_score': -0.2
        }

        result_bearish = await self.inference.predict_async(features_bearish)

        # Verificar que weightings são diferentes
        self.assertNotEqual(
            result_bullish.model_contributions['xgb'],
            result_bearish.model_contributions['xgb']
        )

    async def test_thread_pool_isolation(self):
        """Test isolamento de CPU-bound operations."""
        await self.inference.load_models()

        features = {
            'rsi': 50.0, 'ema_short': 50000.0, 'ema_long': 50000.0,
            'sma_volume': 1.0, 'cvd_slope': 0.0,
            'price_change': 0.0, 'volume_ratio': 1.0, 'divergence_score': 0.0
        }

        # Executar múltiplas predições simultâneas
        tasks = [
            self.inference.predict_async(features)
            for _ in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # Verificar que todas completaram
        self.assertEqual(len(results), 5)
        for result in results:
            self.assertIsInstance(result, EnsembleResult)

    def test_feature_vector_extraction(self):
        """Test extração correta de feature vector."""
        features = {
            'rsi': 65.0,
            'ema_short': 50050.0,
            'ema_long': 50000.0,
            'sma_volume': 1.5,
            'cvd_slope': 0.3,
            'price_change': 0.001,
            'volume_ratio': 1.2,
            'divergence_score': 0.1
        }

        # Simular extração (privado, mas testável)
        feature_vector = np.array([features[name] for name in self.inference.feature_names])

        self.assertEqual(len(feature_vector), 8)
        self.assertEqual(feature_vector[0], 65.0)  # rsi
        self.assertEqual(feature_vector[4], 0.3)   # cvd_slope


class TestPredictionResult(unittest.TestCase):
    """Testes para modelos Pydantic."""

    def test_prediction_result_creation(self):
        """Test criação de PredictionResult."""
        result = PredictionResult(
            signal=1,
            confidence=0.85,
            features_used=['rsi', 'ema'],
            model_version='v2.2',
            timestamp=1234567890
        )

        self.assertEqual(result.signal, 1)
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(result.model_version, 'v2.2')

    def test_ensemble_result_creation(self):
        """Test criação de EnsembleResult."""
        result = EnsembleResult(
            final_signal=-1,
            ensemble_confidence=0.75,
            model_contributions={'xgb': 0.6, 'mlp': 0.4},
            timestamp=1234567890
        )

        self.assertEqual(result.final_signal, -1)
        self.assertEqual(result.ensemble_confidence, 0.75)
        self.assertEqual(result.model_contributions['xgb'], 0.6)


if __name__ == '__main__':
    unittest.main()