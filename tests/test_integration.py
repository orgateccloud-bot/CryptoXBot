"""
Integration Tests — End-to-End
==============================
Testes de integração para fluxos completos do bot.
"""

import unittest
import asyncio
import numpy as np
from config.di_container import DIContainer
from execution.signal_executor import TradingSignal, SignalType


class TestEndToEnd(unittest.TestCase):
    """Testes end-to-end do sistema completo."""

    def setUp(self):
        """Setup container DI."""
        self.container = DIContainer()

    async def asyncSetUp(self):
        """Async setup."""
        # Inicializar componentes
        db = self.container.database()
        await db.initialize()

        order_manager = self.container.order_manager()
        await order_manager.initialize()

    def tearDown(self):
        """Cleanup."""
        # Cleanup async components
        async def cleanup():
            order_manager = self.container.order_manager()
            await order_manager.close()

        asyncio.run(cleanup())

    async def test_full_trading_flow(self):
        """Test fluxo completo: data → score → signal → order."""
        await self.asyncSetUp()

        # 1. Gerar dados de mercado simulados
        ticks = self._generate_mock_ticks()

        # 2. Calcular indicadores
        from indicadores import calculate_rsi, calculate_ema
        prices = np.array([t['price'] for t in ticks])
        rsi = calculate_rsi(prices, period=14)[-1]
        ema_short = calculate_ema(prices, period=9)[-1]
        ema_long = calculate_ema(prices, period=21)[-1]

        # 3. Calcular CVD
        from data.cvd_calculator import calculate_cvd
        cvd_result = calculate_cvd(ticks[-50:])  # últimos 50 ticks

        # 4. Calcular score
        import score
        score_result = score.calcular(
            regime_info={"regime_final": "BULLISH", "score_regime": 100},
            fear_info={"valor": 40},
            tend_4h="ALTA",
            ml_prob=0.8,
            preco=prices[-1],
            ema20=ema_short,
            ema50=ema_long,
            rsi=rsi,
            vwap_val=prices[-1] * 0.999,
            vol_rel=1.5,
            atr_atual=500,
            atr_media=450,
            historico_ticks=ticks[-50:]
        )

        # 5. Verificar score alto o suficiente
        self.assertGreater(score_result['score_total'], 60)

        # 6. Gerar sinal de trading
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            confidence=0.85,
            score_result=score_result,
            timestamp=int(asyncio.get_event_loop().time() * 1000),
            price=prices[-1],
            features={
                'rsi': rsi,
                'ema_short': ema_short,
                'ema_long': ema_long,
                'cvd_slope': cvd_result.divergence_score,
                'price_change': (prices[-1] - prices[-2]) / prices[-2],
                'volume_ratio': 1.2,
                'divergence_score': cvd_result.divergence_score
            }
        )

        # 7. Processar sinal via signal executor
        signal_executor = self.container.signal_executor()
        success = await signal_executor.process_signal(signal)

        # 8. Verificar que ordem foi colocada
        self.assertTrue(success)

        # 9. Verificar posição
        order_manager = self.container.order_manager()
        position = await order_manager.get_position("BTCUSDT")
        self.assertIsNotNone(position)
        self.assertGreater(position.quantity, 0)

    async def test_risk_management_integration(self):
        """Test integração de risk management."""
        await self.asyncSetUp()

        order_manager = self.container.order_manager()

        # Tentar múltiplas ordens grandes
        for i in range(5):
            order = await order_manager.place_order(
                symbol="BTCUSDT",
                side=order_manager.OrderSide.BUY,
                quantity=0.005  # Dentro do limite individual
            )
            if i < 2:  # Primeiras devem passar
                self.assertIsNotNone(order)
            else:  # Depois devem ser rejeitadas por position limit
                self.assertIsNone(order)

    async def test_ai_integration(self):
        """Test integração com AI inference."""
        from ai.inference import AIInference

        inference = AIInference()
        await inference.load_models()

        # Features realistas
        features = {
            'rsi': 65.0,
            'ema_short': 50100.0,
            'ema_long': 50000.0,
            'sma_volume': 1.8,
            'cvd_slope': 0.3,
            'price_change': 0.001,
            'volume_ratio': 1.3,
            'divergence_score': 0.1
        }

        result = await inference.predict_async(features)

        # Verificar estrutura da resposta
        self.assertIsInstance(result.final_signal, int)
        self.assertGreaterEqual(result.final_signal, -1)
        self.assertLessEqual(result.final_signal, 1)
        self.assertIsInstance(result.ensemble_confidence, float)

        await inference.close()

    def _generate_mock_ticks(self, n_ticks=100):
        """Gerar ticks simulados para teste."""
        ticks = []
        base_price = 50000
        timestamp = 1000000

        for i in range(n_ticks):
            # Simular movimento de preço
            price_change = np.random.normal(0, 50)  # Random walk
            price = base_price + price_change

            # Simular volume e direction
            quantity = np.random.uniform(0.1, 2.0)
            is_buyer_maker = np.random.choice([True, False], p=[0.4, 0.6])  # Mais buys

            ticks.append({
                'price': price,
                'quantity': quantity,
                'is_buyer_maker': is_buyer_maker,
                'timestamp': timestamp + i * 1000
            })

            base_price = price

        return ticks


class TestPerformance(unittest.TestCase):
    """Testes de performance e benchmarking."""

    def setUp(self):
        """Setup."""
        self.container = DIContainer()

    async def test_cvd_performance(self):
        """Test performance do CVD calculator."""
        from data.cvd_calculator import calculate_cvd

        # Gerar muitos ticks
        ticks = []
        for i in range(1000):
            ticks.append({
                'price': 50000 + np.random.normal(0, 100),
                'quantity': np.random.uniform(0.1, 5.0),
                'is_buyer_maker': np.random.choice([True, False]),
                'timestamp': 1000000 + i * 100
            })

        import time
        start_time = time.time()

        result = calculate_cvd(ticks)

        end_time = time.time()
        duration = end_time - start_time

        # Deve processar 1000 ticks em < 10ms
        self.assertLess(duration, 0.01)
        self.assertEqual(len(result.cvd_values), 1000)

    async def test_ai_inference_performance(self):
        """Test performance da AI inference."""
        from ai.inference import AIInference

        inference = AIInference(max_workers=4)
        await inference.load_models()

        features = {
            'rsi': 50.0, 'ema_short': 50000.0, 'ema_long': 50000.0,
            'sma_volume': 1.0, 'cvd_slope': 0.0,
            'price_change': 0.0, 'volume_ratio': 1.0, 'divergence_score': 0.0
        }

        import time
        start_time = time.time()

        # Múltiplas inferências simultâneas
        tasks = [inference.predict_async(features) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        end_time = time.time()
        duration = end_time - start_time

        # Deve processar 10 inferências em < 100ms
        self.assertLess(duration, 0.1)
        self.assertEqual(len(results), 10)

        await inference.close()

    async def test_order_placement_performance(self):
        """Test performance de order placement."""
        order_manager = self.container.order_manager()
        await order_manager.initialize()

        import time
        start_time = time.time()

        # Colocar 10 ordens
        tasks = []
        for i in range(10):
            task = order_manager.place_order(
                symbol="BTCUSDT",
                side=order_manager.OrderSide.BUY,
                quantity=0.0001
            )
            tasks.append(task)

        orders = await asyncio.gather(*tasks)

        end_time = time.time()
        duration = end_time - start_time

        # Deve processar 10 ordens em < 50ms
        self.assertLess(duration, 0.05)
        successful_orders = [o for o in orders if o is not None]
        self.assertGreater(len(successful_orders), 5)  # Pelo menos algumas devem passar

        await order_manager.close()


if __name__ == '__main__':
    unittest.main()