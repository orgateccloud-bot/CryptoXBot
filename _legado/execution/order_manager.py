"""
Execution Layer — Order Manager Assíncrono
===========================================
Gerenciamento de ordens assíncrono para HFT.
Integra com Binance API, risk management e position tracking.

Features:
- Ordem placement/cancellation assíncrono
- Risk management integrado (stop-loss, position sizing)
- Position tracking em tempo real
- Integration com score system para decision making
- Circuit breakers e fail-safes

Uso:
  from execution.order_manager import OrderManager
  manager = OrderManager(api_key, api_secret)
  await manager.place_order(side='BUY', quantity=0.001, price=50000)
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import time
import os

# Simulação da Binance API (substituir por binance-python quando integrar)
from infra.logging import get_logger
from infra.paper_trading import get_paper_trading_manager

logger = get_logger(__name__)


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """Representa uma ordem de trading."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float]
    stop_price: Optional[float]
    status: OrderStatus
    timestamp: int
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0


@dataclass
class Position:
    """Posição atual."""
    symbol: str
    quantity: float
    avg_price: float
    unrealized_pnl: float
    timestamp: int


class RiskManager:
    """Gerenciamento de risco integrado."""

    def __init__(self, max_position_size: float = 0.01, max_drawdown: float = 0.02):
        self.max_position_size = max_position_size  # BTC máximo
        self.max_drawdown = max_drawdown
        self.initial_balance = 1000.0  # USD
        self.current_balance = self.initial_balance
        self.open_positions: Dict[str, Position] = {}

    def check_order_risk(self, symbol: str, quantity: float, price: float) -> bool:
        """Verifica se a ordem respeita limites de risco."""
        # Verificar tamanho máximo da posição
        current_pos = self.open_positions.get(symbol, Position(symbol, 0, 0, 0, 0))
        new_quantity = current_pos.quantity + quantity

        if abs(new_quantity) > self.max_position_size:
            logger.warning(f"Order rejected: position size {new_quantity} exceeds max {self.max_position_size}")
            return False

        # Verificar drawdown
        if self.current_balance < self.initial_balance * (1 - self.max_drawdown):
            logger.warning("Order rejected: max drawdown reached")
            return False

        return True

    def update_position(self, order: Order):
        """Atualiza posição após fill."""
        if order.status != OrderStatus.FILLED:
            return

        symbol = order.symbol
        current_pos = self.open_positions.get(symbol, Position(symbol, 0, 0, 0, 0))

        if order.side == OrderSide.BUY:
            new_quantity = current_pos.quantity + order.filled_quantity
            new_avg_price = (
                (current_pos.quantity * current_pos.avg_price) +
                (order.filled_quantity * order.avg_fill_price)
            ) / new_quantity if new_quantity != 0 else 0
        else:  # SELL
            new_quantity = current_pos.quantity - order.filled_quantity
            new_avg_price = current_pos.avg_price  # manter avg price para cálculo de PnL

        # Calcular unrealized PnL (simplificado)
        current_price = order.avg_fill_price  # usar fill price como proxy
        unrealized_pnl = (current_price - new_avg_price) * new_quantity

        self.open_positions[symbol] = Position(
            symbol=symbol,
            quantity=new_quantity,
            avg_price=new_avg_price,
            unrealized_pnl=unrealized_pnl,
            timestamp=int(time.time() * 1000)
        )

        logger.info(f"Position updated: {symbol} qty={new_quantity:.6f} avg_price={new_avg_price:.2f}")


class OrderManager:
    """Gerenciador de ordens assíncrono."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        # Feature Flag: Paper Trading Mode
        self.dry_run = os.getenv('DRY_RUN', 'false').lower() in ('true', '1', 'yes', 'on')
        self.paper_trading_manager = None

        # Componentes
        self.risk_manager = RiskManager()
        self.pending_orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []

        # Locks para thread safety
        self.order_lock = asyncio.Lock()

        # Simulação de API client (substituir por real)
        self.api_client = None  # Binance API client aqui

        logger.info(f"OrderManager initialized (DRY_RUN={self.dry_run})")

    async def initialize(self):
        """Inicializa conexões e carrega estado."""
        if self.dry_run:
            self.paper_trading_manager = await get_paper_trading_manager()
            logger.warning("🚨 PAPER TRADING MODE ACTIVE - No real orders will be placed!")
        else:
            # Aqui inicializar API client real
            logger.info("OrderManager initialized with LIVE trading")
        logger.info("OrderManager initialized successfully")

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Optional[Order]:
        """
        Coloca uma ordem assincronamente.

        Args:
            symbol: Par de trading (ex: 'BTCUSDT')
            side: BUY ou SELL
            quantity: Quantidade em base currency
            order_type: Tipo da ordem
            price: Preço limite (para LIMIT orders)
            stop_price: Preço de stop (para STOP_LOSS)

        Returns:
            Order object se sucesso, None se rejeitada
        """
        async with self.order_lock:
            # Verificar risco
            if price and not self.risk_manager.check_order_risk(symbol, quantity if side == OrderSide.BUY else -quantity, price):
                return None

            # Criar ordem
            order_id = f"order_{int(time.time() * 1000)}_{len(self.pending_orders)}"
            order = Order(
                order_id=order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                status=OrderStatus.PENDING,
                timestamp=int(time.time() * 1000)
            )

            self.pending_orders[order_id] = order

            # Processamento baseado no modo
            if self.dry_run:
                asyncio.create_task(self._process_paper_order(order))
            else:
                asyncio.create_task(self._process_order(order))

            logger.info(f"Order placed: {order_id} {side.value} {quantity} {symbol}")
            return order

    async def cancel_order(self, order_id: str) -> bool:
        """Cancela uma ordem pendente."""
        async with self.order_lock:
            if order_id not in self.pending_orders:
                return False

            order = self.pending_orders[order_id]
            order.status = OrderStatus.CANCELLED
            del self.pending_orders[order_id]
            self.order_history.append(order)

            logger.info(f"Order cancelled: {order_id}")
            return True

    async def get_position(self, symbol: str) -> Optional[Position]:
        """Retorna posição atual para um símbolo."""
        return self.risk_manager.open_positions.get(symbol)

    async def get_pending_orders(self) -> List[Order]:
        """Retorna ordens pendentes."""
        async with self.order_lock:
            return list(self.pending_orders.values())

    async def _process_paper_order(self, order: Order):
        """Processa ordem em modo paper trading (simulação segura)."""
        try:
            # Simular latência de rede
            await asyncio.sleep(0.01)  # 10ms simulado

            # Registrar ordem virtual no banco
            await self.paper_trading_manager.log_virtual_order(
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                price=order.price or 50000.0,  # usar preço fornecido ou simulado
                order_type=order.order_type.value,
                order_book_snapshot={}  # TODO: integrar com order book real
            )

            # Simular fill imediato (paper trading)
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.avg_fill_price = order.price or 50000.0

            # Atualizar posição (mesmo em paper trading para tracking)
            self.risk_manager.update_position(order)

            # Mover para histórico
            async with self.order_lock:
                if order.order_id in self.pending_orders:
                    del self.pending_orders[order.order_id]
                self.order_history.append(order)

            logger.info(f"Paper order processed: {order.order_id} status={order.status.value} [DRY RUN]")

        except Exception as e:
            logger.error(f"Error processing paper order {order.order_id}: {e}")
            order.status = OrderStatus.REJECTED

    async def get_virtual_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retorna posição virtual (apenas em modo dry-run)."""
        if not self.dry_run or not self.paper_trading_manager:
            return None
        return await self.paper_trading_manager.get_virtual_position(symbol)

    async def get_virtual_pnl(self, symbol: str, current_price: float) -> Optional[Dict[str, float]]:
        """Retorna PnL virtual (apenas em modo dry-run)."""
        if not self.dry_run or not self.paper_trading_manager:
            return None
        return await self.paper_trading_manager.get_virtual_pnl(symbol, current_price)

    async def get_virtual_orders(self, symbol: Optional[str] = None, limit: int = 100) -> list:
        """Retorna histórico de ordens virtuais (apenas em modo dry-run)."""
        if not self.dry_run or not self.paper_trading_manager:
            return []
        return await self.paper_trading_manager.get_virtual_orders(symbol, limit)

    async def emergency_stop(self):
        """Stop de emergência - cancela todas as ordens."""
        async with self.order_lock:
            orders_to_cancel = list(self.pending_orders.keys())

        for order_id in orders_to_cancel:
            await self.cancel_order(order_id)

        logger.warning("Emergency stop activated - all orders cancelled")

    async def close(self):
        """Fecha conexões."""
        await self.emergency_stop()
        if self.paper_trading_manager:
            await self.paper_trading_manager.close()
        logger.info("OrderManager closed")