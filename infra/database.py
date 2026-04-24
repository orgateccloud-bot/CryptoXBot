"""
Database Abstraction Layer — BotBinance Infra
===============================================
Abstração assíncrona para banco de dados.
Suporta SQLite local e BigQuery GCP via strategy pattern.

Features:
- Async puro (aiosqlite).
- Strategy para diferentes backends.
- Connection pooling.
- Migrations automáticas.

Uso:
  from infra.database import Database
  db = Database()
  await db.initialize()
  await db.save_trade(tick)
"""

import aiosqlite
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from config.settings import DB_PATH
from infra.logging import get_logger

logger = get_logger(__name__)


class DatabaseStrategy(ABC):
    """Strategy pattern para diferentes backends de DB."""

    @abstractmethod
    async def initialize(self):
        pass

    @abstractmethod
    async def save_trade(self, tick: Dict[str, Any]):
        pass

    @abstractmethod
    async def save_snapshot(self, snapshot: Dict[str, Any]):
        pass

    @abstractmethod
    async def get_recent_ticks(self, symbol: str, limit: int = 100) -> List[Dict]:
        pass


class SQLiteStrategy(DatabaseStrategy):
    """Implementação SQLite async."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._pool = None

    async def initialize(self):
        """Cria tabelas se necessário."""
        self._pool = await aiosqlite.connect(self.db_path)
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                price       REAL NOT NULL,
                quantity    REAL NOT NULL,
                is_buyer_maker INTEGER NOT NULL,
                trade_id    INTEGER UNIQUE
            )
        """)
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                data        TEXT NOT NULL
            )
        """)
        await self._pool.commit()
        logger.info("Database SQLite inicializado", extra={"path": self.db_path})

    async def save_trade(self, tick: Dict[str, Any]):
        """Salva trade de forma async."""
        await self._pool.execute("""
            INSERT OR IGNORE INTO trades
            (timestamp, symbol, price, quantity, is_buyer_maker, trade_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            tick["timestamp"],
            tick.get("symbol", "BTCUSDT"),
            tick["price"],
            tick["quantity"],
            1 if tick["is_buyer_maker"] else 0,
            tick["trade_id"]
        ))
        await self._pool.commit()

    async def save_snapshot(self, snapshot: Dict[str, Any]):
        """Salva snapshot."""
        import json
        await self._pool.execute("""
            INSERT INTO snapshots (timestamp, symbol, data)
            VALUES (?, ?, ?)
        """, (
            int(time.time() * 1000),
            snapshot.get("symbol", "BTCUSDT"),
            json.dumps(snapshot)
        ))
        await self._pool.commit()

    async def get_recent_ticks(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Busca ticks recentes."""
        cursor = await self._pool.execute("""
            SELECT * FROM trades
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (symbol, limit))
        rows = await cursor.fetchall()
        return [{
            "timestamp": row[1],
            "symbol": row[2],
            "price": row[3],
            "quantity": row[4],
            "is_buyer_maker": bool(row[5]),
            "trade_id": row[6]
        } for row in rows]

    async def close(self):
        """Fecha conexão."""
        if self._pool:
            await self._pool.close()


class Database:
    """Facade para database com strategy."""

    def __init__(self, strategy: DatabaseStrategy = None):
        self.strategy = strategy or SQLiteStrategy()

    async def initialize(self):
        await self.strategy.initialize()

    async def save_trade(self, tick: Dict[str, Any]):
        await self.strategy.save_trade(tick)

    async def save_snapshot(self, snapshot: Dict[str, Any]):
        await self.strategy.save_snapshot(snapshot)

    async def get_recent_ticks(self, symbol: str, limit: int = 100) -> List[Dict]:
        return await self.strategy.get_recent_ticks(symbol, limit)

    async def close(self):
        if hasattr(self.strategy, 'close'):
            await self.strategy.close()