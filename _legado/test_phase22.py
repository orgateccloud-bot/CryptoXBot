"""
Teste rápido para Phase 2.2: Data e AI layers
"""
import asyncio
import numpy as np
from data.cvd_calculator import calculate_cvd, calculate_cvd_from_prices
from ai.inference import AIInference

async def test_cvd():
    """Teste CVD calculator."""
    print("Testing CVD Calculator...")

    # Dados de teste
    ticks = [
        {'price': 50000, 'quantity': 1.0, 'is_buyer_maker': False, 'timestamp': 1000},  # buy
        {'price': 50001, 'quantity': 0.5, 'is_buyer_maker': True, 'timestamp': 1001},   # sell
        {'price': 50002, 'quantity': 2.0, 'is_buyer_maker': False, 'timestamp': 1002},  # buy
    ]

    result = calculate_cvd(ticks)
    print(f"CVD values: {result.cvd_values}")
    print(f"Divergence score: {result.divergence_score:.3f}")
    print(f"Trend strength: {result.trend_strength:.3f}")

    # Teste vetorizado direto
    prices = np.array([50000, 50001, 50002])
    volumes = np.array([1.0, 0.5, 2.0])
    directions = np.array([1, -1, 1])  # 1=buy, -1=sell

    cvd_direct = calculate_cvd_from_prices(prices, volumes, directions)
    print(f"Direct CVD: {cvd_direct}")

async def test_inference():
    """Teste AI inference."""
    print("\nTesting AI Inference...")

    inference = AIInference()
    await inference.load_models()

    # Features de teste
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

    result = await inference.predict_async(features)
    print(f"Final signal: {result.final_signal}")
    print(f"Ensemble confidence: {result.ensemble_confidence:.3f}")
    print(f"Model contributions: {result.model_contributions}")

    await inference.close()

async def main():
    await test_cvd()
    await test_inference()
    print("\nPhase 2.2 validation complete!")

if __name__ == "__main__":
    asyncio.run(main())