"""
Paper Trading Module — Simulação Segura de Trades
=================================================
Módulo para modo dry-run que registra intenções de trade sem executar ordens reais.
Garante isolamento total da API da Binance.

Features:
- Logging de ordens virtuais no SQLite
- Cálculo de PnL simulado
- Estimativa de taxas Binance (Taker/Maker)
- Timestamp preciso em milissegundos

Uso:
  from infra.paper_trading import PaperTradingManager
  manager = PaperTradingManager()
  await manager.log_virtual_order(side='BUY', price=50000.0, quantity=0.001)
"""

import asyncio
import aiosqlite
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import os
from infra.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VirtualOrder:
    """Representa uma ordem virtual registrada."""
    timestamp: int  # milissegundos
    symbol: str
    side: str  # 'BUY' ou 'SELL'
    quantity: float
    price: float
    order_type: str  # 'MARKET', 'LIMIT', etc.
    estimated_fee: float  # taxa estimada
    fee_type: str  # 'taker' ou 'maker'
    order_book_snapshot: Dict[str, Any]  # snapshot do order book no momento


class PaperTradingManager:
    """Gerenciador de paper trading com persistência SQLite."""

    def __init__(self, db_path: str = "data/paper_trading.db"):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None
        self._initialized = False

    async def initialize(self):
        """Inicializa banco de dados."""
        if self._initialized:
            return

        # Criar diretório se não existir
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.db = await aiosqlite.connect(self.db_path)
        await self._create_tables()
        self._initialized = True
        logger.info(f"Paper trading database initialized at {self.db_path}")

    async def _create_tables(self):
        """Cria tabelas necessárias."""
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS virtual_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                order_type TEXT NOT NULL,
                estimated_fee REAL NOT NULL,
                fee_type TEXT NOT NULL,
                order_book_snapshot TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS virtual_positions (
                symbol TEXT PRIMARY KEY,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                total_fees REAL NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self.db.commit()

    async def log_virtual_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
        order_book_snapshot: Optional[Dict[str, Any]] = None
    ) -> VirtualOrder:
        """
        Registra uma ordem virtual no banco.

        Args:
            symbol: Par de trading
            side: 'BUY' ou 'SELL'
            quantity: Quantidade
            price: Preço exato do order book
            order_type: Tipo da ordem
            order_book_snapshot: Snapshot do order book no momento do sinal

        Returns:
            VirtualOrder registrada
        """
        if not self._initialized:
            await self.initialize()

        timestamp = int(datetime.now().timestamp() * 1000)  # milissegundos

        # Estimar taxa (simplificado - em produção usar taxas reais da Binance)
        fee_rate = 0.001  # 0.1% para taker (pior caso)
        estimated_fee = quantity * price * fee_rate
        fee_type = "taker"  # assumir taker por segurança

        if order_book_snapshot is None:
            order_book_snapshot = {}

        virtual_order = VirtualOrder(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            estimated_fee=estimated_fee,
            fee_type=fee_type,
            order_book_snapshot=order_book_snapshot
        )

        # Inserir no banco
        await self.db.execute("""
            INSERT INTO virtual_orders
            (timestamp, symbol, side, quantity, price, order_type, estimated_fee, fee_type, order_book_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            virtual_order.timestamp,
            virtual_order.symbol,
            virtual_order.side,
            virtual_order.quantity,
            virtual_order.price,
            virtual_order.order_type,
            virtual_order.estimated_fee,
            virtual_order.fee_type,
            json.dumps(virtual_order.order_book_snapshot)
        ))

        await self.db.commit()

        # Atualizar posição virtual
        await self._update_virtual_position(virtual_order)

        logger.info(f"Virtual order logged: {side} {quantity} {symbol} @ {price} (fee: {estimated_fee:.8f})")
        return virtual_order

    async def _update_virtual_position(self, order: VirtualOrder):
        """Atualiza posição virtual após ordem."""
        # Buscar posição atual
        cursor = await self.db.execute(
            "SELECT quantity, avg_price, total_fees FROM virtual_positions WHERE symbol = ?",
            (order.symbol,)
        )
        row = await cursor.fetchone()

        if row:
            current_qty, current_avg_price, total_fees = row
        else:
            current_qty, current_avg_price, total_fees = 0.0, 0.0, 0.0

        # Calcular nova posição
        if order.side.upper() == "BUY":
            new_qty = current_qty + order.quantity
            if new_qty != 0:
                new_avg_price = (
                    (current_qty * current_avg_price) +
                    (order.quantity * order.price)
                ) / new_qty
            else:
                new_avg_price = 0.0
        else:  # SELL
            new_qty = current_qty - order.quantity
            new_avg_price = current_avg_price  # manter para cálculo de PnL

        new_total_fees = total_fees + order.estimated_fee

        # Atualizar ou inserir
        await self.db.execute("""
            INSERT OR REPLACE INTO virtual_positions
            (symbol, quantity, avg_price, total_fees, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (order.symbol, new_qty, new_avg_price, new_total_fees))

        await self.db.commit()

    async def get_virtual_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retorna posição virtual para um símbolo."""
        if not self._initialized:
            await self.initialize()

        cursor = await self.db.execute(
            "SELECT quantity, avg_price, total_fees FROM virtual_positions WHERE symbol = ?",
            (symbol,)
        )
        row = await cursor.fetchone()

        if row:
            qty, avg_price, total_fees = row
            return {
                "symbol": symbol,
                "quantity": qty,
                "avg_price": avg_price,
                "total_fees": total_fees
            }
        return None

    async def get_virtual_orders(
        self,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> list[VirtualOrder]:
        """Retorna histórico de ordens virtuais."""
        if not self._initialized:
            await self.initialize()

        query = """
            SELECT timestamp, symbol, side, quantity, price, order_type,
                   estimated_fee, fee_type, order_book_snapshot
            FROM virtual_orders
        """
        params = []

        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()

        orders = []
        for row in rows:
            timestamp, symbol, side, quantity, price, order_type, fee, fee_type, snapshot_json = row
            orders.append(VirtualOrder(
                timestamp=timestamp,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                estimated_fee=fee,
                fee_type=fee_type,
                order_book_snapshot=json.loads(snapshot_json)
            ))

        return orders

    async def get_virtual_pnl(self, symbol: str, current_price: float) -> Dict[str, float]:
        """Calcula PnL virtual baseado na posição atual e preço corrente."""
        position = await self.get_virtual_position(symbol)
        if not position or position["quantity"] == 0:
            return {"unrealized_pnl": 0.0, "total_fees": 0.0}

        qty = position["quantity"]
        avg_price = position["avg_price"]
        total_fees = position["total_fees"]

        # PnL não realizado
        unrealized_pnl = (current_price - avg_price) * qty

        return {
            "unrealized_pnl": unrealized_pnl,
            "total_fees": total_fees,
            "net_pnl": unrealized_pnl - total_fees
        }

    async def close(self):
        """Fecha conexão com banco."""
        if self.db:
            await self.db.close()
            self._initialized = False


# Instância global
_paper_trading_manager = PaperTradingManager()


async def get_paper_trading_manager() -> PaperTradingManager:
    """Retorna instância global do gerenciador."""
    if not _paper_trading_manager._initialized:
        await _paper_trading_manager.initialize()
    return _paper_trading_manager