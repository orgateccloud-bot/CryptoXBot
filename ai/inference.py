"""
AI Inference Layer — Não-Bloqueante
====================================
Inferência ML assíncrona com ThreadPoolExecutor.
Previne bloqueio do asyncio durante predições.

Features:
- ThreadPoolExecutor para isolamento de CPU-bound.
- Modelos XGBoost/MLP carregados uma vez.
- Saídas Pydantic para MCP readiness.

Uso:
  from ai.inference import AIInference
  inference = AIInference()
  result = await inference.predict_async(features)
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
import numpy as np
import xgboost as xgb
from sklearn.neural_network import MLPClassifier
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class PredictionResult(BaseModel):
    """Modelo Pydantic para resultado de predição."""
    signal: int  # 1=buy, -1=sell, 0=hold
    confidence: float  # 0-1
    features_used: List[str]
    model_version: str
    timestamp: int


class EnsembleResult(BaseModel):
    """Resultado do ensemble."""
    final_signal: int
    ensemble_confidence: float
    model_contributions: Dict[str, float]  # peso de cada modelo
    timestamp: int


class AIInference:
    """Inferência ML não-bloqueante."""

    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ml-inference")
        self.xgb_model: Optional[xgb.XGBClassifier] = None
        self.mlp_model: Optional[MLPClassifier] = None
        self.feature_names = [
            'rsi', 'ema_short', 'ema_long', 'sma_volume', 'cvd_slope',
            'price_change', 'volume_ratio', 'divergence_score'
        ]
        self.model_version = "v2.2-regime-aware"

    async def load_models(self):
        """Carrega modelos em background."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self.executor, self._load_models_sync)

    def _load_models_sync(self):
        """Carregamento síncrono dos modelos."""
        try:
            # XGBoost model (placeholder - carregar de arquivo)
            self.xgb_model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                objective='multi:softprob',
                num_class=3  # buy, hold, sell
            )
            # Simular carregamento
            logger.info("XGBoost model loaded")

            # MLP model
            self.mlp_model = MLPClassifier(
                hidden_layer_sizes=(64, 32),
                activation='relu',
                solver='adam',
                max_iter=1000,
                random_state=42
            )
            # Simular carregamento
            logger.info("MLP model loaded")

        except Exception as e:
            logger.error(f"Failed to load models: {e}")
            raise

    async def predict_async(self, features: Dict[str, float]) -> EnsembleResult:
        """
        Predição assíncrona do ensemble.

        Args:
            features: Dict com features normalizadas

        Returns:
            EnsembleResult com sinal final
        """
        loop = asyncio.get_event_loop()

        # Executar predições em paralelo no ThreadPool
        xgb_future = loop.run_in_executor(self.executor, self._predict_xgb, features)
        mlp_future = loop.run_in_executor(self.executor, self._predict_mlp, features)

        xgb_result, mlp_result = await asyncio.gather(xgb_future, mlp_future)

        # Ensemble weighting (regime-aware)
        regime_factor = features.get('cvd_slope', 0)  # exemplo: usar CVD para weighting

        if regime_factor > 0.5:  # bullish regime
            xgb_weight = 0.7
            mlp_weight = 0.3
        elif regime_factor < -0.5:  # bearish regime
            xgb_weight = 0.6
            mlp_weight = 0.4
        else:  # neutral
            xgb_weight = 0.5
            mlp_weight = 0.5

        # Weighted average das probabilidades
        final_probs = (
            xgb_weight * np.array(xgb_result['probabilities']) +
            mlp_weight * np.array(mlp_result['probabilities'])
        )

        final_signal = int(np.argmax(final_probs) - 1)  # 0->-1, 1->0, 2->1
        ensemble_confidence = float(np.max(final_probs))

        return EnsembleResult(
            final_signal=final_signal,
            ensemble_confidence=ensemble_confidence,
            model_contributions={
                'xgb': xgb_weight,
                'mlp': mlp_weight
            },
            timestamp=int(asyncio.get_event_loop().time() * 1000)
        )

    def _predict_xgb(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Predição XGBoost síncrona."""
        if self.xgb_model is None:
            raise RuntimeError("XGBoost model not loaded")

        # Extrair features na ordem certa
        feature_vector = np.array([features[name] for name in self.feature_names]).reshape(1, -1)

        # Placeholder: simular predição
        probabilities = np.array([0.2, 0.6, 0.2])  # hold, buy, sell
        prediction = np.argmax(probabilities)

        return {
            'prediction': prediction,
            'probabilities': probabilities.tolist(),
            'confidence': float(np.max(probabilities))
        }

    def _predict_mlp(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Predição MLP síncrona."""
        if self.mlp_model is None:
            raise RuntimeError("MLP model not loaded")

        feature_vector = np.array([features[name] for name in self.feature_names]).reshape(1, -1)

        # Placeholder: simular predição
        probabilities = np.array([0.3, 0.4, 0.3])  # hold, buy, sell
        prediction = np.argmax(probabilities)

        return {
            'prediction': prediction,
            'probabilities': probabilities.tolist(),
            'confidence': float(np.max(probabilities))
        }

    async def close(self):
        """Fecha o executor."""
        self.executor.shutdown(wait=True)