"""
Database facade for BotBinance.

SQLite remains the default for local Windows development. Set
DATABASE_BACKEND=postgres and DATABASE_URL to use Supabase/Postgres in Railway.
The public functions intentionally keep the old API used by main.py,
dashboard.py, executor.py, and strategies.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from config.runtime_settings import (
    DATABASE_BACKEND,
    DATABASE_URL,
    DB_PATH,
    DB_POOL_MAX,
    DB_POOL_MIN,
    SYMBOL,
)

_POSTGRES_ALIASES = {"postgres", "postgresql", "supabase"}
_pg_pool = None


def _backend() -> str:
    backend = (DATABASE_BACKEND or "sqlite").lower()
    if backend in _POSTGRES_ALIASES:
        return "postgres"
    return "sqlite"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _symbol(symbol: str | None = None) -> str:
    return (symbol or SYMBOL or "BTCUSDT").upper()


def _pg_dsn() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_BACKEND=postgres exige DATABASE_URL configurado.")
    if DATABASE_URL.startswith("postgres://"):
        return "postgresql://" + DATABASE_URL[len("postgres://") :]
    return DATABASE_URL


def _pg_json(value: Any):
    from psycopg.types.json import Json

    return Json(value)


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise RuntimeError(
                "Backend Postgres selecionado, mas psycopg nao esta instalado. "
                "Instale requirements.txt atualizado."
            ) from exc

        _pg_pool = ConnectionPool(
            conninfo=_pg_dsn(),
            min_size=DB_POOL_MIN,
            max_size=DB_POOL_MAX,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
    return _pg_pool


@contextmanager
def _pg_connection():
    with _get_pg_pool().connection() as conn:
        yield conn


def conectar():
    """Return a raw connection for compatibility with legacy callers."""
    if _backend() == "postgres":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg nao instalado para backend Postgres.") from exc
        return psycopg.connect(_pg_dsn(), row_factory=dict_row, prepare_threshold=None)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _sqlite_add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _sqlite_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _inicializar_sqlite() -> None:
    conn = conectar()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            preco       REAL    NOT NULL,
            volume_btc  REAL    NOT NULL,
            volume_usdt REAL    NOT NULL,
            direcao     TEXT    NOT NULL,
            eh_baleia   INTEGER DEFAULT 0
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots_mercado (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp             TEXT    NOT NULL,
            preco                 REAL,
            variacao_24h          REAL,
            volume_24h_btc        REAL,
            funding_rate          REAL,
            open_interest_btc     REAL,
            ema20_1h              REAL,
            ema50_1h              REAL,
            rsi_1h                REAL,
            tendencia             TEXT,
            pressao_order_book    TEXT,
            liquidez_compra_usdt  REAL,
            liquidez_venda_usdt   REAL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS cvd_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            cvd         REAL NOT NULL,
            compras_btc REAL NOT NULL,
            vendas_btc  REAL NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sinais (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            tipo        TEXT    NOT NULL,
            preco       REAL    NOT NULL,
            motivo      TEXT,
            executado   INTEGER DEFAULT 0
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            name       TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            data       TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            service    TEXT,
            symbol     TEXT,
            event_type TEXT NOT NULL,
            severity   TEXT DEFAULT 'INFO',
            message    TEXT NOT NULL,
            data       TEXT
        )
        """
    )

    _sqlite_add_column(conn, "trades", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "trades", "trade_id", "INTEGER")
    _sqlite_add_column(conn, "snapshots_mercado", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "cvd_historico", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "sinais", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "sinais", "score", "REAL")
    _sqlite_add_column(conn, "sinais", "source", "TEXT")
    _sqlite_add_column(conn, "sinais", "executado_em", "TEXT")

    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id) WHERE trade_id IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sinais_symbol_timestamp ON sinais(symbol, timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cvd_symbol_timestamp ON cvd_historico(symbol, timestamp)")

    conn.commit()
    conn.close()


def _inicializar_postgres() -> None:
    with _pg_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id          BIGSERIAL PRIMARY KEY,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
                preco       DOUBLE PRECISION NOT NULL,
                volume_btc  DOUBLE PRECISION NOT NULL,
                volume_usdt DOUBLE PRECISION NOT NULL,
                direcao     TEXT NOT NULL,
                eh_baleia   BOOLEAN DEFAULT false,
                trade_id    BIGINT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots_mercado (
                id                    BIGSERIAL PRIMARY KEY,
                timestamp             TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol                TEXT NOT NULL DEFAULT 'BTCUSDT',
                preco                 DOUBLE PRECISION,
                variacao_24h          DOUBLE PRECISION,
                volume_24h_btc        DOUBLE PRECISION,
                funding_rate          DOUBLE PRECISION,
                open_interest_btc     DOUBLE PRECISION,
                ema20_1h              DOUBLE PRECISION,
                ema50_1h              DOUBLE PRECISION,
                rsi_1h                DOUBLE PRECISION,
                tendencia             TEXT,
                pressao_order_book    TEXT,
                liquidez_compra_usdt  DOUBLE PRECISION,
                liquidez_venda_usdt   DOUBLE PRECISION,
                raw_payload           JSONB
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cvd_historico (
                id          BIGSERIAL PRIMARY KEY,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
                cvd         DOUBLE PRECISION NOT NULL,
                compras_btc DOUBLE PRECISION NOT NULL,
                vendas_btc  DOUBLE PRECISION NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sinais (
                id          BIGSERIAL PRIMARY KEY,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
                tipo        TEXT NOT NULL,
                preco       DOUBLE PRECISION NOT NULL,
                motivo      TEXT,
                score       DOUBLE PRECISION,
                source      TEXT,
                executado   BOOLEAN DEFAULT false,
                executado_em TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_state (
                name       TEXT PRIMARY KEY,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                data       JSONB NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_events (
                id         BIGSERIAL PRIMARY KEY,
                timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
                service    TEXT,
                symbol     TEXT,
                event_type TEXT NOT NULL,
                severity   TEXT DEFAULT 'INFO',
                message    TEXT NOT NULL,
                data       JSONB
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id) WHERE trade_id IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sinais_symbol_timestamp ON sinais(symbol, timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cvd_symbol_timestamp ON cvd_historico(symbol, timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON bot_events(timestamp DESC)")
        conn.commit()


def inicializar() -> None:
    """Create all required tables for the selected backend."""
    if _backend() == "postgres":
        _inicializar_postgres()
        print("[DB] Banco Postgres/Supabase inicializado.")
        return

    _inicializar_sqlite()
    print(f"[DB] Banco SQLite inicializado em: {DB_PATH}")


def healthcheck() -> bool:
    try:
        if _backend() == "postgres":
            with _pg_connection() as conn:
                conn.execute("SELECT 1")
            return True

        conn = conectar()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def backend_info() -> dict[str, Any]:
    return {
        "backend": _backend(),
        "db_path": DB_PATH if _backend() == "sqlite" else None,
        "database_url_configured": bool(DATABASE_URL),
    }


def salvar_trade(
    preco: float,
    volume_btc: float,
    direcao: str,
    whale_threshold: float = 5.0,
    symbol: str | None = None,
    trade_id: int | None = None,
) -> None:
    sym = _symbol(symbol)
    volume_usdt = preco * volume_btc
    eh_baleia = volume_btc >= whale_threshold

    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO trades
                (timestamp, symbol, preco, volume_btc, volume_usdt, direcao, eh_baleia, trade_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trade_id) WHERE trade_id IS NOT NULL DO NOTHING
                """,
                (_utcnow(), sym, preco, volume_btc, volume_usdt, direcao, eh_baleia, trade_id),
            )
            conn.commit()
        return

    conn = conectar()
    conn.execute(
        """
        INSERT OR IGNORE INTO trades
        (timestamp, symbol, preco, volume_btc, volume_usdt, direcao, eh_baleia, trade_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.now().isoformat(), sym, preco, volume_btc, volume_usdt, direcao, 1 if eh_baleia else 0, trade_id),
    )
    conn.commit()
    conn.close()


def salvar_snapshot(dados: dict[str, Any], symbol: str | None = None) -> None:
    sym = _symbol(symbol or dados.get("symbol"))
    values = (
        sym,
        dados.get("preco"),
        dados.get("variacao_24h_%"),
        dados.get("volume_24h_btc"),
        dados.get("funding_rate_%"),
        dados.get("open_interest_btc"),
        dados.get("ema20_1h"),
        dados.get("ema50_1h"),
        dados.get("rsi_1h"),
        dados.get("tendencia"),
        dados.get("pressao_dominante"),
        dados.get("liquidez_compra_usdt"),
        dados.get("liquidez_venda_usdt"),
    )

    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO snapshots_mercado (
                    timestamp, symbol, preco, variacao_24h, volume_24h_btc,
                    funding_rate, open_interest_btc, ema20_1h, ema50_1h,
                    rsi_1h, tendencia, pressao_order_book,
                    liquidez_compra_usdt, liquidez_venda_usdt, raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (_utcnow(), *values, _pg_json(dados)),
            )
            conn.commit()
        return

    conn = conectar()
    conn.execute(
        """
        INSERT INTO snapshots_mercado (
            timestamp, symbol, preco, variacao_24h, volume_24h_btc,
            funding_rate, open_interest_btc, ema20_1h, ema50_1h, rsi_1h,
            tendencia, pressao_order_book, liquidez_compra_usdt, liquidez_venda_usdt
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (datetime.now().isoformat(), *values),
    )
    conn.commit()
    conn.close()


def salvar_cvd(cvd: float, compras_btc: float, vendas_btc: float, symbol: str | None = None) -> None:
    sym = _symbol(symbol)
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (_utcnow(), sym, cvd, compras_btc, vendas_btc),
            )
            conn.commit()
        return

    conn = conectar()
    conn.execute(
        """
        INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)
        VALUES (?, ?, ?, ?, ?)
        """,
        (datetime.now().isoformat(), sym, cvd, compras_btc, vendas_btc),
    )
    conn.commit()
    conn.close()


def salvar_sinal(
    tipo: str,
    preco: float,
    motivo: str,
    symbol: str | None = None,
    score: float | None = None,
    source: str | None = None,
    executado: bool = False,
) -> None:
    sym = _symbol(symbol)
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO sinais
                (timestamp, symbol, tipo, preco, motivo, score, source, executado, executado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (_utcnow(), sym, tipo, preco, motivo, score, source, executado, _utcnow() if executado else None),
            )
            conn.commit()
    else:
        conn = conectar()
        conn.execute(
            """
            INSERT INTO sinais
            (timestamp, symbol, tipo, preco, motivo, score, source, executado, executado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                sym,
                tipo,
                preco,
                motivo,
                score,
                source,
                1 if executado else 0,
                datetime.now().isoformat() if executado else None,
            ),
        )
        conn.commit()
        conn.close()

    print(f"[DB] Sinal salvo: {sym} {tipo} @ ${preco:,.2f} - {motivo}")


def ultimos_snapshots(n: int = 10, symbol: str | None = None) -> list[dict[str, Any]]:
    sym = symbol.upper() if symbol else None
    if _backend() == "postgres":
        sql = "SELECT * FROM snapshots_mercado"
        params: list[Any] = []
        if sym:
            sql += " WHERE symbol = %s"
            params.append(sym)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(n)
        with _pg_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    conn = conectar()
    if sym:
        rows = conn.execute(
            "SELECT * FROM snapshots_mercado WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (sym, n),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM snapshots_mercado ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def buscar_sinais(limit: int = 30, symbol: str | None = None) -> list[dict[str, Any]]:
    sym = symbol.upper() if symbol else None
    if _backend() == "postgres":
        sql = "SELECT * FROM sinais"
        params: list[Any] = []
        if sym:
            sql += " WHERE symbol = %s"
            params.append(sym)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)
        with _pg_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    conn = conectar()
    if sym:
        rows = conn.execute("SELECT * FROM sinais WHERE symbol = ? ORDER BY id DESC LIMIT ?", (sym, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sinais ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sinais_executados(limit: int = 1000) -> list[dict[str, Any]]:
    if _backend() == "postgres":
        with _pg_connection() as conn:
            rows = conn.execute(
                "SELECT tipo, symbol, preco, score FROM sinais WHERE executado = true ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    conn = conectar()
    rows = conn.execute(
        "SELECT tipo, symbol, preco, score FROM sinais WHERE executado = 1 ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resumo_trades(minutos: int = 60, symbol: str | None = None) -> dict[str, dict[str, float]]:
    sym = symbol.upper() if symbol else None
    if _backend() == "postgres":
        sql = """
            SELECT direcao, COUNT(*) AS qtd, COALESCE(SUM(volume_btc), 0) AS total_btc
            FROM trades
            WHERE timestamp >= now() - (%s || ' minutes')::interval
        """
        params: list[Any] = [minutos]
        if sym:
            sql += " AND symbol = %s"
            params.append(sym)
        sql += " GROUP BY direcao"
        with _pg_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return {r["direcao"]: {"qtd": r["qtd"], "total_btc": float(r["total_btc"] or 0)} for r in rows}

    conn = conectar()
    if sym:
        rows = conn.execute(
            """
            SELECT direcao, COUNT(*) as qtd, SUM(volume_btc) as total_btc
            FROM trades
            WHERE timestamp >= datetime('now', ?) AND symbol = ?
            GROUP BY direcao
            """,
            (f"-{minutos} minutes", sym),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT direcao, COUNT(*) as qtd, SUM(volume_btc) as total_btc
            FROM trades
            WHERE timestamp >= datetime('now', ?)
            GROUP BY direcao
            """,
            (f"-{minutos} minutes",),
        ).fetchall()
    conn.close()
    return {r["direcao"]: {"qtd": r["qtd"], "total_btc": r["total_btc"] or 0} for r in rows}


def salvar_risk_state(state: dict[str, Any], name: str = "default") -> None:
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO risk_state (name, updated_at, data)
                VALUES (%s, %s, %s)
                ON CONFLICT (name)
                DO UPDATE SET updated_at = EXCLUDED.updated_at, data = EXCLUDED.data
                """,
                (name, _utcnow(), _pg_json(state)),
            )
            conn.commit()
        return

    payload = json.dumps(state, default=str)
    conn = conectar()
    conn.execute(
        """
        INSERT INTO risk_state (name, updated_at, data)
        VALUES (?, ?, ?)
        ON CONFLICT(name)
        DO UPDATE SET updated_at = excluded.updated_at, data = excluded.data
        """,
        (name, datetime.now().isoformat(), payload),
    )
    conn.commit()
    conn.close()


def carregar_risk_state(name: str = "default") -> dict[str, Any] | None:
    if _backend() == "postgres":
        with _pg_connection() as conn:
            row = conn.execute("SELECT data FROM risk_state WHERE name = %s", (name,)).fetchone()
            if not row:
                return None
            data = row["data"]
            return data if isinstance(data, dict) else json.loads(data)

    conn = conectar()
    row = conn.execute("SELECT data FROM risk_state WHERE name = ?", (name,)).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["data"])


def salvar_bot_event(
    event_type: str,
    message: str,
    *,
    service: str | None = None,
    symbol: str | None = None,
    severity: str = "INFO",
    data: dict[str, Any] | None = None,
) -> None:
    sym = symbol.upper() if symbol else None
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO bot_events (timestamp, service, symbol, event_type, severity, message, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (_utcnow(), service, sym, event_type, severity, message, _pg_json(data or {})),
            )
            conn.commit()
        return

    payload = json.dumps(data or {}, default=str)
    conn = conectar()
    conn.execute(
        """
        INSERT INTO bot_events (timestamp, service, symbol, event_type, severity, message, data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.now().isoformat(), service, sym, event_type, severity, message, payload),
    )
    conn.commit()
    conn.close()


def fechar_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.close()
        _pg_pool = None


if __name__ == "__main__":
    inicializar()
    print("[DB] Backend:", backend_info())
    if _backend() == "postgres":
        with conectar() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
            ).fetchall()
            for row in rows:
                print(f"  - {row['table_name']}")
    else:
        conn = conectar()
        tabelas = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for t in tabelas:
            print(f"  - {t[0]}")
        conn.close()
