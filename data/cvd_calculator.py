"""
CVD Calculator — Vetorizado
============================
Cálculo de Cumulative Volume Delta (CVD) com numpy.
Processa arrays de ticks para máxima performance.

Features:
- Vetorização completa com numpy.
- Cálculo incremental de CVD.
- Estruturas de saída padronizadas para MCP.

Uso:
  from data.cvd_calculator import calculate_cvd
  cvd_result = calculate_cvd(ticks_array)
"""

from typing import Any, Dict, List

import numpy as np
from pydantic import BaseModel


class TickData(BaseModel):
    """Modelo Pydantic para tick."""

    price: float
    quantity: float
    is_buyer_maker: bool
    timestamp: int


class CVDResult(BaseModel):
    """Modelo Pydantic para resultado CVD."""

    cvd_values: List[float]
    timestamps: List[int]
    divergence_score: float  # -1 a 1: negativo = bearish divergence
    trend_strength: float  # 0-1: força da tendência CVD


def calculate_cvd(ticks: List[Dict[str, Any]], window_size: int = 100) -> CVDResult:
    """
    Calcula CVD vetorizado sobre array de ticks.

    Args:
        ticks: Lista de dicts com 'price', 'quantity', 'is_buyer_maker', 'timestamp'
        window_size: Tamanho da janela para análise de tendência

    Returns:
        CVDResult com valores vetorizados e métricas
    """
    if not ticks:
        return CVDResult(cvd_values=[], timestamps=[], divergence_score=0.0, trend_strength=0.0)

    # Converter para arrays numpy
    quantities = np.array([t["quantity"] for t in ticks])
    is_buyer = np.array([t["is_buyer_maker"] for t in ticks])
    timestamps = np.array([t["timestamp"] for t in ticks])

    # CVD vetorizado: +quantity se not is_buyer (compra), - se is_buyer (venda)
    cvd_deltas = np.where(is_buyer, -quantities, quantities)
    cvd_values = np.cumsum(cvd_deltas).tolist()

    # Análise de tendência na janela recente
    if len(cvd_values) >= window_size:
        recent_cvd = np.array(cvd_values[-window_size:])
        x = np.arange(window_size)

        # Regressão linear para slope
        slope, _ = np.polyfit(x, recent_cvd, 1)
        trend_strength = (
            min(abs(slope) / np.std(recent_cvd), 1.0) if np.std(recent_cvd) > 0 else 0.0
        )

        # Divergence score: normalizar slope
        divergence_score = np.tanh(slope / (np.std(recent_cvd) + 1e-10))
    else:
        trend_strength = 0.0
        divergence_score = 0.0

    return CVDResult(
        cvd_values=cvd_values,
        timestamps=timestamps.tolist(),
        divergence_score=divergence_score,
        trend_strength=trend_strength,
    )


def calculate_cvd_from_prices(
    prices: np.ndarray, volumes: np.ndarray, directions: np.ndarray
) -> np.ndarray:
    """
    Calcula CVD diretamente de arrays numpy (para backtest otimizado).

    Args:
        prices: Array de preços
        volumes: Array de volumes
        directions: Array de direções (1=compra, -1=venda)

    Returns:
        Array de CVD cumulativo
    """
    cvd_deltas = volumes * directions
    return np.cumsum(cvd_deltas)
