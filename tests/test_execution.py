"""
Unit Tests — Execution Layer
============================
Testes unitários para order management e signal execution.
"""

import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from execution.order_manager import OrderManager, OrderSide, OrderType, OrderStatus, RiskManager
from execution.signal_executor import SignalExecutor, TradingSignal, SignalType


class TestOrderManager(unittest.TestCase):
    """Testes para Order Manager."""

    def setUp(self):
        """Setup."""
        self.order_manager = OrderManager(
            api_key="test_key",
            api_secret="test_secret",
            testnet=True
        )

    def tearDown(self):
        """Cleanup."""
        asyncio.run(self.order_manager.close())

    async def asyncSetUp(self):
        """Async setup."""
        await self.order_manager.initialize()

    def test_initialization(self):
        """Test inicialização."""
        self.assertIsNotNone(self.order_manager.risk_manager)
        self.assertEqual(len(self.order_manager.pending_orders), 0)

    async def test_place_market_order(self):
        """Test placement de ordem market."""
        await self.asyncSetUp()

        order = await self.order_manager.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001,
            order_type=OrderType.MARKET
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.side, OrderSide.BUY)
        self.assertEqual(order.quantity, 0.001)
        self.assertEqual(order.status, OrderStatus.PENDING)

        # Aguardar processamento
        await asyncio.sleep(0.05)

        # Verificar se foi processada
        self.assertEqual(len(self.order_manager.pending_orders), 0)
        self.assertEqual(len(self.order_manager.order_history), 1)

    async def test_risk_management_rejection(self):
        """Test rejeição por risk management."""
        await self.asyncSetUp()

        # Tentar ordem muito grande
        order = await self.order_manager.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=1.0,  # Maior que max_position_size
            order_type=OrderType.MARKET
        )

        self.assertIsNone(order)  # Deve ser rejeitada

    async def test_cancel_order(self):
        """Test cancelamento de ordem."""
        await self.asyncSetUp()

        # Colocar ordem
        order = await self.order_manager.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001
        )
        self.assertIsNotNone(order)

        # Cancelar
        success = await self.order_manager.cancel_order(order.order_id)
        self.assertTrue(success)

        # Verificar que foi cancelada
        self.assertNotIn(order.order_id, self.order_manager.pending_orders)

    async def test_position_tracking(self):
        """Test tracking de posição."""
        await self.asyncSetUp()

        # Ordem de compra
        order_buy = await self.order_manager.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001
        )
        await asyncio.sleep(0.05)  # Aguardar processamento

        position = await self.order_manager.get_position("BTCUSDT")
        self.assertIsNotNone(position)
        self.assertEqual(position.quantity, 0.001)
        self.assertEqual(position.symbol, "BTCUSDT")

        # Ordem de venda (fechar posição)
        order_sell = await self.order_manager.place_order(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            quantity=0.001
        )
        await asyncio.sleep(0.05)

        position_after = await self.order_manager.get_position("BTCUSDT")
        self.assertEqual(position_after.quantity, 0.0)


class TestRiskManager(unittest.TestCase):
    """Testes para Risk Manager."""

    def setUp(self):
        """Setup."""
        self.risk_manager = RiskManager(max_position_size=0.01, max_drawdown=0.02)

    def test_position_size_check(self):
        """Test verificação de tamanho de posição."""
        # Ordem dentro do limite
        allowed = self.risk_manager.check_order_risk("BTCUSDT", 0.005, 50000)
        self.assertTrue(allowed)

        # Ordem acima do limite
        not_allowed = self.risk_manager.check_order_risk("BTCUSDT", 0.02, 50000)
        self.assertFalse(not_allowed)

    def test_drawdown_check(self):
        """Test verificação de drawdown."""
        # Simular perda pequena (dentro do limite)
        self.risk_manager.current_balance = 980  # Drawdown de 2%

        # Deve permitir
        allowed = self.risk_manager.check_order_risk("BTCUSDT", 0.001, 50000)
        self.assertTrue(allowed)

        # Simular perda grande (acima do limite)
        self.risk_manager.current_balance = 960  # Drawdown de 4%
        not_allowed = self.risk_manager.check_order_risk("BTCUSDT", 0.001, 50000)
        self.assertFalse(not_allowed)

    def test_position_update(self):
        """Test atualização de posição."""
        # Compra inicial
        mock_order_buy = MagicMock()
        mock_order_buy.status = OrderStatus.FILLED
        mock_order_buy.side = OrderSide.BUY
        mock_order_buy.filled_quantity = 0.001
        mock_order_buy.avg_fill_price = 50000
        mock_order_buy.symbol = "BTCUSDT"

        self.risk_manager.update_position(mock_order_buy)

        position = self.risk_manager.open_positions["BTCUSDT"]
        self.assertEqual(position.quantity, 0.001)
        self.assertEqual(position.avg_price, 50000)

        # Venda adicional
        mock_order_sell = MagicMock()
        mock_order_sell.status = OrderStatus.FILLED
        mock_order_sell.side = OrderSide.SELL
        mock_order_sell.filled_quantity = 0.0005
        mock_order_sell.avg_fill_price = 51000
        mock_order_sell.symbol = "BTCUSDT"

        self.risk_manager.update_position(mock_order_sell)

        position_after = self.risk_manager.open_positions["BTCUSDT"]
        self.assertEqual(position_after.quantity, 0.0005)


class TestSignalExecutor(unittest.TestCase):
    """Testes para Signal Executor."""

    def setUp(self):
        """Setup."""
        self.order_manager = AsyncMock()
        self.score_calculator = MagicMock()
        self.signal_executor = SignalExecutor(self.order_manager, self.score_calculator)

    def tearDown(self):
        """Cleanup."""
        asyncio.run(self.signal_executor.emergency_stop())

    async def test_process_buy_signal(self):
        """Test processamento de sinal de compra."""
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            confidence=0.8,
            score_result={
                "decisao": "OPERAR_CHEIO",
                "tamanho_fator": 1.0,
                "score_total": 85
            },
            timestamp=1234567890,
            price=50000.0,
            features={"rsi": 30.0, "atr": 500.0}
        )

        # Mock order placement
        self.order_manager.place_order.return_value = MagicMock(order_id="test_order")

        success = await self.signal_executor.process_signal(signal)

        self.assertTrue(success)
        self.order_manager.place_order.assert_called_once()

    async def test_ignore_low_score_signal(self):
        """Test ignorar sinal com score baixo."""
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            confidence=0.5,
            score_result={
                "decisao": "AGUARDAR",
                "tamanho_fator": 0.0,
                "score_total": 45
            },
            timestamp=1234567890,
            price=50000.0,
            features={}
        )

        success = await self.signal_executor.process_signal(signal)

        self.assertFalse(success)
        self.order_manager.place_order.assert_not_called()

    async def test_position_sizing(self):
        """Test cálculo de position sizing."""
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            confidence=0.8,
            score_result={"decisao": "OPERAR_CHEIO", "tamanho_fator": 1.0},
            timestamp=1234567890,
            price=50000.0,
            features={"rsi": 30.0, "atr": 500.0}
        )

        size = self.signal_executor._calculate_position_size(signal, 1.0)
        expected_min = 0.0001
        expected_max = 0.001 * 0.8 * 1.0  # base_size * confidence * tamanho_fator

        self.assertGreaterEqual(size, expected_min)
        self.assertLessEqual(size, expected_max)

    async def test_rate_limiting(self):
        """Test rate limiting de sinais."""
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            confidence=0.8,
            score_result={"decisao": "OPERAR_CHEIO", "tamanho_fator": 1.0},
            timestamp=1234567890,
            price=50000.0,
            features={}
        )

        # Primeiro sinal
        self.order_manager.place_order.return_value = MagicMock()
        success1 = await self.signal_executor.process_signal(signal)
        self.assertTrue(success1)

        # Segundo sinal imediato (deve ser limitado)
        success2 = await self.signal_executor.process_signal(signal)
        self.assertFalse(success2)  # Rate limited


if __name__ == '__main__':
    unittest.main()