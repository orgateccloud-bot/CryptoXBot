"""
Signal Executor — Integração Score + Execution
===============================================
Executa sinais de trading baseado no score unificado.
Integra com OrderManager para placement automático de ordens.

Features:
- Decision making baseado em score
- Position sizing dinâmico
- Risk management integrado
- Circuit breakers

Uso:
  from execution.signal_executor import SignalExecutor
  executor = SignalExecutor(order_manager, score_calculator)
  await executor.process_signal(signal_data)
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from execution.order_manager import OrderManager, OrderSide, OrderType
from infra.logging import get_logger

logger = get_logger(__name__)


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass
class TradingSignal:
    """Sinal de trading gerado pelo score system."""
    signal_type: SignalType
    symbol: str
    confidence: float
    score_result: Dict[str, Any]
    timestamp: int
    price: float
    features: Dict[str, float]


class SignalExecutor:
    """Executor de sinais integrado com score e order management."""

    def __init__(self, order_manager: OrderManager, score_calculator):
        self.order_manager = order_manager
        self.score_calculator = score_calculator

        # Configurações
        self.max_orders_per_minute = 10
        self.min_signal_interval = 1.0  # segundos entre sinais
        self.base_position_size = 0.001  # BTC base

        # Estado
        self.last_signal_time = 0
        self.orders_this_minute = 0
        self.last_minute_reset = 0  # será inicializado no primeiro uso

        # Circuit breakers
        self.circuit_breaker_active = False
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3

        logger.info("SignalExecutor initialized")

    async def process_signal(self, signal: TradingSignal) -> bool:
        """
        Processa um sinal de trading.

        Args:
            signal: TradingSignal com dados do score

        Returns:
            True se ordem foi colocada, False caso contrário
        """
        # Verificar circuit breakers
        if self.circuit_breaker_active:
            logger.warning("Circuit breaker active - ignoring signal")
            return False

        # Rate limiting
        current_time = asyncio.get_event_loop().time()
        if current_time - self.last_signal_time < self.min_signal_interval:
            logger.debug("Signal rate limited")
            return False

        # Reset counter por minuto
        if self.last_minute_reset == 0 or current_time - self.last_minute_reset >= 60:
            self.orders_this_minute = 0
            self.last_minute_reset = current_time

        if self.orders_this_minute >= self.max_orders_per_minute:
            logger.warning("Max orders per minute reached")
            return False

        # Decidir ação baseada no score
        score_result = signal.score_result
        decisao = score_result.get("decisao", "AGUARDAR")
        tamanho_fator = score_result.get("tamanho_fator", 0.0)

        if decisao == "AGUARDAR":
            logger.debug(f"Signal ignored: score {score_result['score_total']} too low")
            return False

        # Calcular tamanho da posição
        position_size = self._calculate_position_size(signal, tamanho_fator)

        # Verificar posição atual
        current_position = await self.order_manager.get_position(signal.symbol)
        current_qty = current_position.quantity if current_position else 0

        # Decidir tipo de ordem
        order_side, order_qty = self._decide_order_action(
            signal.signal_type, position_size, current_qty
        )

        if order_qty == 0:
            logger.debug("No order needed - position already correct")
            return False

        # Colocar ordem
        try:
            order = await self.order_manager.place_order(
                symbol=signal.symbol,
                side=order_side,
                quantity=abs(order_qty),
                order_type=OrderType.MARKET  # HFT usa market orders
            )

            if order:
                self.last_signal_time = current_time
                self.orders_this_minute += 1
                logger.info(f"Order placed: {order.order_id} {order_side.value} {order_qty:.6f} {signal.symbol}")
                return True
            else:
                logger.warning("Order rejected by risk manager")
                return False

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return False

    def _calculate_position_size(self, signal: TradingSignal, tamanho_fator: float) -> float:
        """Calcula tamanho da posição baseado no score e confiança."""
        base_size = self.base_position_size

        # Ajustar por confiança do sinal
        confidence_multiplier = signal.confidence

        # Ajustar por tamanho_fator do score
        score_multiplier = tamanho_fator

        # Ajustar por volatilidade (se disponível)
        volatility_adjustment = 1.0
        if 'atr' in signal.features:
            atr_ratio = signal.features['atr'] / signal.price
            if atr_ratio > 0.01:  # alta volatilidade
                volatility_adjustment = 0.5

        position_size = base_size * confidence_multiplier * score_multiplier * volatility_adjustment
        return max(position_size, 0.0001)  # mínimo 0.0001 BTC

    def _decide_order_action(self, signal_type: SignalType, target_size: float, current_qty: float) -> tuple:
        """
        Decide ação da ordem baseada no sinal e posição atual.

        Returns:
            (OrderSide, quantity) - quantity positiva para BUY, negativa para SELL
        """
        if signal_type == SignalType.BUY:
            if current_qty >= target_size:
                return OrderSide.BUY, 0  # já tem posição suficiente
            else:
                return OrderSide.BUY, target_size - current_qty

        elif signal_type == SignalType.SELL:
            if current_qty <= -target_size:
                return OrderSide.SELL, 0  # já tem posição short suficiente
            else:
                return OrderSide.SELL, -(target_size + current_qty)  # vender para fechar long ou abrir short

        elif signal_type == SignalType.CLOSE:
            if current_qty > 0:
                return OrderSide.SELL, -current_qty  # fechar long
            elif current_qty < 0:
                return OrderSide.BUY, -current_qty   # fechar short
            else:
                return OrderSide.BUY, 0  # nada para fechar

        else:  # HOLD
            return OrderSide.BUY, 0

    async def update_pnl_tracking(self, current_price: float, symbol: str):
        """Atualiza tracking de PnL para circuit breakers."""
        position = await self.order_manager.get_position(symbol)
        if not position:
            return

        # Calcular PnL realizado (simplificado)
        # Em produção, isso viria de fills reais
        pass

    async def check_circuit_breakers(self):
        """Verifica e ativa circuit breakers se necessário."""
        # Implementar lógica de circuit breaker baseada em perdas consecutivas
        # drawdown, etc.
        pass

    async def emergency_stop(self):
        """Stop de emergência."""
        self.circuit_breaker_active = True
        await self.order_manager.emergency_stop()
        logger.warning("SignalExecutor emergency stop activated")