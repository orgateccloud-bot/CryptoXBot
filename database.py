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
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

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


def _primeiro_valor(row):
    """I-13: primeira coluna de uma linha, nos DOIS backends.

    O pool do Postgres usa `row_factory=dict_row` (:95) e o SQLite entrega
    `sqlite3.Row` (ou tupla). `row[0]` funciona num e levanta `KeyError: 0` no
    outro — e era assim que `salvar_sinal` e `historico_cv_auc_modelo`
    quebravam determinamente sob Postgres.

    Escrever `row["id"]` resolveria só metade: os dois call sites selecionam
    colunas com nomes diferentes (`id` e `cv_auc_mean`), e um SELECT de coluna
    unica nao deveria obrigar o chamador a repetir o nome dela. Esta funcao
    devolve o unico valor, seja qual for a forma da linha.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def _pg_dsn() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_BACKEND=postgres exige DATABASE_URL configurado.")
    if DATABASE_URL.startswith("postgres://"):
        return "postgresql://" + DATABASE_URL[len("postgres://") :]
    return DATABASE_URL


def _mascarar_dsn(dsn: str) -> str:
    """A-9: oculta a senha do DSN para nunca vazar em log/traceback.
    postgresql://user:SENHA@host:port/db -> postgresql://user:***@host:port/db"""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", dsn)


def _conectar_pg_seguro(fabrica):
    """Executa uma fabrica de conexao Postgres mascarando o DSN em qualquer
    erro — psycopg costuma ecoar o conninfo (com senha) no traceback."""
    try:
        return fabrica()
    except Exception as exc:
        msg = _mascarar_dsn(str(exc))
        raise RuntimeError(f"Falha ao conectar no Postgres: {msg}") from None


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

        _pg_pool = _conectar_pg_seguro(
            lambda: ConnectionPool(
                conninfo=_pg_dsn(),
                min_size=DB_POOL_MIN,
                max_size=DB_POOL_MAX,
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                # psycopg_pool >= 3.2 nao abre o pool implicitamente; sem isto,
                # .connection() da PoolTimeout.
                open=True,
            )
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
        return _conectar_pg_seguro(
            lambda: psycopg.connect(_pg_dsn(), row_factory=dict_row, prepare_threshold=None)
        )

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # busy_timeout: threads concorrentes esperam em vez de "database is locked".
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL: escrita nao bloqueia leitura + robustez contra corrupcao em crash.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.DatabaseError:
        pass  # pragma pode falhar em DB somente-leitura; segue com defaults
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            preco       REAL    NOT NULL,
            volume_btc  REAL    NOT NULL,
            volume_usdt REAL    NOT NULL,
            direcao     TEXT    NOT NULL,
            eh_baleia   INTEGER DEFAULT 0
        )
        """)

    c.execute("""
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
        """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cvd_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            cvd         REAL NOT NULL,
            compras_btc REAL NOT NULL,
            vendas_btc  REAL NOT NULL
        )
        """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sinais (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            tipo        TEXT    NOT NULL,
            preco       REAL    NOT NULL,
            motivo      TEXT,
            executado   INTEGER DEFAULT 0
        )
        """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_state (
            name       TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            data       TEXT NOT NULL
        )
        """)

    c.execute("""
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
        """)

    # P1-4: historico de AUC por retreino (guard-rail de drift, sem MLflow —
    # ver validacao.detectar_drift) -- um lineage por symbol+modelo_tipo,
    # populado a cada retreino semanal (ml_filtro.py/lstm_modelo.py).
    c.execute("""
        CREATE TABLE IF NOT EXISTS model_metricas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            modelo_tipo TEXT NOT NULL,
            auc         REAL,
            cv_auc_mean REAL,
            cv_auc_std  REAL
        )
        """)

    # MESA DE OPERACOES (2026-08-16): canal de COMANDO dashboard -> worker.
    # Nao e tabela de medicao (E-8e intacto): o dashboard grava a INTENCAO
    # auditada, o worker consome e responde. Contrato v1: papel somente.
    c.execute("""
        CREATE TABLE IF NOT EXISTS comandos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_em     TEXT NOT NULL,
            comando       TEXT NOT NULL,
            params        TEXT,
            origem        TEXT,
            status        TEXT NOT NULL DEFAULT 'PENDENTE',
            resultado     TEXT,
            processado_em TEXT
        )
        """)

    _sqlite_add_column(conn, "trades", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "trades", "trade_id", "INTEGER")
    _sqlite_add_column(conn, "snapshots_mercado", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "cvd_historico", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "sinais", "symbol", "TEXT DEFAULT 'BTCUSDT'")
    _sqlite_add_column(conn, "sinais", "score", "REAL")
    _sqlite_add_column(conn, "sinais", "source", "TEXT")
    _sqlite_add_column(conn, "sinais", "executado_em", "TEXT")
    # P1-3: meta-labeling — liga a linha de ENTRADA ao resultado do trade
    # (preenchido por executor.fechar_posicao no fechamento final), dando
    # a `sinais_executados()`/kelly_do_banco() e a um futuro dataset de
    # meta-labeling um PnL real e a barreira efetivamente tocada.
    _sqlite_add_column(conn, "sinais", "preco_saida", "REAL")
    _sqlite_add_column(conn, "sinais", "pnl_usdt", "REAL")
    _sqlite_add_column(conn, "sinais", "pnl_pct", "REAL")
    _sqlite_add_column(conn, "sinais", "barreira_tocada", "TEXT")

    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp)")
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id "
        "ON trades(trade_id) WHERE trade_id IS NOT NULL"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_sinais_symbol_timestamp ON sinais(symbol, timestamp)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_cvd_symbol_timestamp ON cvd_historico(symbol, timestamp)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_metricas_symbol_tipo "
        "ON model_metricas(symbol, modelo_tipo, timestamp)"
    )

    conn.commit()
    conn.close()


def _inicializar_postgres() -> None:
    with _pg_connection() as conn:
        conn.execute("""
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
            """)
        conn.execute("""
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
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cvd_historico (
                id          BIGSERIAL PRIMARY KEY,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
                cvd         DOUBLE PRECISION NOT NULL,
                compras_btc DOUBLE PRECISION NOT NULL,
                vendas_btc  DOUBLE PRECISION NOT NULL
            )
            """)
        conn.execute("""
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
                executado_em TIMESTAMPTZ,
                preco_saida     DOUBLE PRECISION,
                pnl_usdt        DOUBLE PRECISION,
                pnl_pct         DOUBLE PRECISION,
                barreira_tocada TEXT
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_state (
                name       TEXT PRIMARY KEY,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                data       JSONB NOT NULL
            )
            """)
        conn.execute("""
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
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_metricas (
                id          BIGSERIAL PRIMARY KEY,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
                symbol      TEXT NOT NULL,
                modelo_tipo TEXT NOT NULL,
                auc         DOUBLE PRECISION,
                cv_auc_mean DOUBLE PRECISION,
                cv_auc_std  DOUBLE PRECISION
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comandos (
                id            BIGSERIAL PRIMARY KEY,
                criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
                comando       TEXT NOT NULL,
                params        TEXT,
                origem        TEXT,
                status        TEXT NOT NULL DEFAULT 'PENDENTE',
                resultado     TEXT,
                processado_em TIMESTAMPTZ
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp "
            "ON trades(symbol, timestamp DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id "
            "ON trades(trade_id) WHERE trade_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sinais_symbol_timestamp "
            "ON sinais(symbol, timestamp DESC)"
        )
        # 004: UNIQUE que torna o ON CONFLICT do migrador REAL. Chaves
        # naturais medidas com ZERO duplicatas em 4,5 meses de dados
        # (2026-08-12). `trades` fica de fora: 11.178 colisoes legitimas na
        # chave composta — a dedupe dela continua no indice parcial acima.
        # Banco criado do zero nasce igual ao migrado pela 004.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_symbol_ts "
            "ON snapshots_mercado (symbol, timestamp)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cvd_symbol_ts "
            "ON cvd_historico (symbol, timestamp)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sinais_symbol_ts "
            "ON sinais (symbol, timestamp)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_ts_tipo "
            "ON bot_events (timestamp, event_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cvd_symbol_timestamp "
            "ON cvd_historico(symbol, timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON bot_events(timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_metricas_symbol_tipo "
            "ON model_metricas(symbol, modelo_tipo, timestamp DESC)"
        )
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
        (
            datetime.now().isoformat(),
            sym,
            preco,
            volume_btc,
            volume_usdt,
            direcao,
            1 if eh_baleia else 0,
            trade_id,
        ),
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
                ON CONFLICT DO NOTHING
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


def salvar_cvd(
    cvd: float, compras_btc: float, vendas_btc: float, symbol: str | None = None
) -> None:
    sym = _symbol(symbol)
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
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
) -> int | None:
    """Retorna o id da linha inserida (P1-3: meta-labeling) -- permite ao
    chamador (estrategias/otimizada.py) guardar essa referencia e, se a
    entrada de fato executar, repassa-la a executor.abrir_long para ligar
    entrada e fechamento do mesmo trade. None se a insercao falhar."""
    sym = _symbol(symbol)
    if _backend() == "postgres":
        with _pg_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO sinais
                (timestamp, symbol, tipo, preco, motivo, score, source, executado, executado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    _utcnow(),
                    sym,
                    tipo,
                    preco,
                    motivo,
                    score,
                    source,
                    executado,
                    _utcnow() if executado else None,
                ),
            ).fetchone()
            conn.commit()
            # I-13: o pool usa `row_factory=dict_row` (:95), entao `row` e um
            # DICT e `row[0]` levantava `KeyError: 0`. Nao era um caso de borda:
            # `salvar_sinal` esta no caminho de TODA decisao e de todo
            # fechamento — no dia em que DATABASE_URL fosse configurado, o bot
            # nao gravaria UM sinal sequer.
            #
            # `buscar_sinais` (:724) e `sinais_executados` (:748) ja faziam
            # `dict(row)` corretamente; o defeito estava so onde alguem indexou
            # por POSICAO, que e o idioma do sqlite3.Row e nao o do dict_row.
            sinal_id = _primeiro_valor(row)
    else:
        conn = conectar()
        cursor = conn.execute(
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
        sinal_id = cursor.lastrowid
        conn.close()

    print(f"[DB] Sinal salvo: {sym} {tipo} @ ${preco:,.2f} - {motivo}")
    return sinal_id


def marcar_sinal_executado(sinal_id: int | None, *, symbol: str | None = None) -> None:
    """P1-3: marca a linha de ENTRADA (criada por salvar_sinal) como
    realmente executada -- chamado por executor.abrir_long quando a ordem
    de fato preenche.

    E-8: sinal_id=None deixou de ser no-op SILENCIOSO. Medido em 2026-08-08:
    5.255 sinais na tabela, ZERO com executado=1 — o circuito de meta-labeling
    nunca gravou uma linha em 4 meses, e nada no log dizia isso. Havia tres
    fontes de None (caminho trend sem sinal_id, posicao reconstruida no boot,
    falha na insercao original) e as tres desapareciam aqui.

    Um trade sem vinculo nao e recuperavel depois: a Etapa 2 precisa de
    entrada+saida do MESMO trade, e sem o id nao ha como pareá-los. Por isso o
    aviso e obrigatorio — perder o vinculo e um defeito de dado, nao um detalhe.
    """
    if sinal_id is None:
        print(
            f"[DB] AVISO: marcar_sinal_executado(None)"
            f"{f' [{symbol}]' if symbol else ''} — trade aberto SEM vinculo ao "
            f"sinal. O par entrada/saida nao entrara no track record da Etapa 2."
        )
        return
    agora = _utcnow() if _backend() == "postgres" else datetime.now().isoformat()
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                "UPDATE sinais SET executado = true, executado_em = %s WHERE id = %s",
                (agora, sinal_id),
            )
            conn.commit()
    else:
        conn = conectar()
        conn.execute(
            "UPDATE sinais SET executado = 1, executado_em = ? WHERE id = ?",
            (agora, sinal_id),
        )
        conn.commit()
        conn.close()


def atualizar_sinal_fechamento(
    sinal_id: int | None,
    preco_saida: float,
    pnl_usdt: float,
    pnl_pct: float,
    barreira_tocada: str,
) -> None:
    """P1-3: liga o resultado do trade (fechamento FINAL, nao parcial) de
    volta a linha de ENTRADA -- da a sinais_executados()/kelly_do_banco() e
    a um futuro dataset de meta-labeling o PnL real e a barreira
    efetivamente tocada (STOP/TARGET/TARGET_PARCIAL/MANUAL), em vez de
    inferir "ganho/perda" pelo sinal do PnL.

    E-8: sinal_id=None deixou de ser no-op SILENCIOSO — mesma razao de
    marcar_sinal_executado. Este e o ponto mais caro de perder: aqui o PnL REAL
    do trade existe, foi calculado, e ia para o lixo sem uma linha de log. Zero
    das 5.255 linhas de `sinais` tinha pnl_usdt preenchido depois de 4 meses.
    """
    if sinal_id is None:
        print(
            f"[DB] AVISO: atualizar_sinal_fechamento(None) — PnL de "
            f"${pnl_usdt:.2f} ({pnl_pct:+.2f}%, {barreira_tocada}) NAO foi "
            f"vinculado a nenhum sinal e nao entra no track record."
        )
        return
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                UPDATE sinais
                SET preco_saida = %s, pnl_usdt = %s, pnl_pct = %s, barreira_tocada = %s
                WHERE id = %s
                """,
                (preco_saida, pnl_usdt, pnl_pct, barreira_tocada, sinal_id),
            )
            conn.commit()
    else:
        conn = conectar()
        conn.execute(
            """
            UPDATE sinais
            SET preco_saida = ?, pnl_usdt = ?, pnl_pct = ?, barreira_tocada = ?
            WHERE id = ?
            """,
            (preco_saida, pnl_usdt, pnl_pct, barreira_tocada, sinal_id),
        )
        conn.commit()
        conn.close()


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
        rows = conn.execute(
            "SELECT * FROM snapshots_mercado ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
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
        rows = conn.execute(
            "SELECT * FROM sinais WHERE symbol = ? ORDER BY id DESC LIMIT ?", (sym, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sinais ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sinais_executados(limit: int = 1000) -> list[dict[str, Any]]:
    """Entradas EXECUTADAS com resultado JA CONHECIDO (trade fechado) --
    P1-3: filtra por pnl_usdt IS NOT NULL, nao so executado=true, porque
    uma posicao ainda ABERTA tem executado=true (marcado no abrir_long) mas
    pnl_usdt continua NULL ate o fechamento final (atualizar_sinal_fechamento).
    Sem esse filtro, risco.kelly_do_banco() contaria posicoes em aberto como
    se ja tivessem um resultado."""
    if _backend() == "postgres":
        with _pg_connection() as conn:
            rows = conn.execute(
                """
                SELECT tipo, symbol, preco, score, pnl_usdt, pnl_pct, barreira_tocada
                FROM sinais
                WHERE executado = true AND pnl_usdt IS NOT NULL
                ORDER BY id DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    conn = conectar()
    rows = conn.execute(
        """
        SELECT tipo, symbol, preco, score, pnl_usdt, pnl_pct, barreira_tocada
        FROM sinais
        WHERE executado = 1 AND pnl_usdt IS NOT NULL
        ORDER BY id DESC LIMIT ?
        """,
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
            return {
                r["direcao"]: {"qtd": r["qtd"], "total_btc": float(r["total_btc"] or 0)}
                for r in rows
            }

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


# ── Posições abertas (P0-3: crash recovery) ──────────────────────────────────
# Reutiliza a tabela risk_state (name PK + data JSON) com prefixo "posicao:" —
# zero migração de schema, funciona igual em SQLite e Postgres.

_PREFIXO_POSICAO = "posicao:"


def salvar_posicao_aberta(symbol: str, posicao: dict[str, Any]) -> None:
    """Persiste (upsert) a posição aberta do par — sobrevive a restart."""
    salvar_risk_state(posicao, name=f"{_PREFIXO_POSICAO}{symbol.upper()}")


def remover_posicao_aberta(symbol: str) -> None:
    """Remove a posição persistida do par (posição fechada)."""
    name = f"{_PREFIXO_POSICAO}{symbol.upper()}"
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute("DELETE FROM risk_state WHERE name = %s", (name,))
            conn.commit()
        return
    conn = conectar()
    conn.execute("DELETE FROM risk_state WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def carregar_posicoes_abertas() -> dict[str, dict[str, Any]]:
    """Retorna {symbol: posicao} de todas as posições persistidas (boot recovery)."""
    padrao = f"{_PREFIXO_POSICAO}%"
    resultado: dict[str, dict[str, Any]] = {}
    if _backend() == "postgres":
        with _pg_connection() as conn:
            rows = conn.execute(
                "SELECT name, data FROM risk_state WHERE name LIKE %s", (padrao,)
            ).fetchall()
        for row in rows:
            data = row["data"]
            resultado[row["name"][len(_PREFIXO_POSICAO) :]] = (
                data if isinstance(data, dict) else json.loads(data)
            )
        return resultado
    conn = conectar()
    rows = conn.execute("SELECT name, data FROM risk_state WHERE name LIKE ?", (padrao,)).fetchall()
    conn.close()
    for row in rows:
        resultado[row["name"][len(_PREFIXO_POSICAO) :]] = json.loads(row["data"])
    return resultado


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
                INSERT INTO bot_events
                    (timestamp, service, symbol, event_type, severity, message, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
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


def criar_comando(comando: str, params: dict[str, Any] | None, origem: str) -> int:
    """MESA: grava a intenção do operador. Quem executa é o worker."""
    payload = json.dumps(params or {}, default=str)
    if _backend() == "postgres":
        with _pg_connection() as conn:
            cur = conn.execute(
                "INSERT INTO comandos (criado_em, comando, params, origem)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (_utcnow(), comando, payload, origem),
            )
            novo_id = int(cur.fetchone()["id"])  # dict_row: sempre por nome
            conn.commit()
        return novo_id
    conn = conectar()
    cur = conn.execute(
        "INSERT INTO comandos (criado_em, comando, params, origem) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), comando, payload, origem),
    )
    novo_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return novo_id


def proximo_comando_pendente() -> dict[str, Any] | None:
    """Consumidor único (worker): o comando PENDENTE mais antigo, ou None."""
    if _backend() == "postgres":
        with _pg_connection() as conn:
            cur = conn.execute(
                "SELECT id, comando, params, origem FROM comandos"
                " WHERE status = 'PENDENTE' ORDER BY id ASC LIMIT 1"
            )
            row = cur.fetchone()
        if not row:
            return None
        try:
            params = json.loads(row["params"] or "{}")
        except ValueError:
            params = {}
        return {
            "id": int(row["id"]),
            "comando": row["comando"],
            "params": params,
            "origem": row["origem"],
        }
    conn = conectar()
    row = conn.execute(
        "SELECT id, comando, params, origem FROM comandos"
        " WHERE status = 'PENDENTE' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        params = json.loads(row["params"] or "{}")
    except ValueError:
        params = {}
    return {
        "id": int(row["id"]),
        "comando": row["comando"],
        "params": params,
        "origem": row["origem"],
    }


def marcar_comando(comando_id: int, status: str, resultado: str) -> None:
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                "UPDATE comandos SET status = %s, resultado = %s, processado_em = %s"
                " WHERE id = %s",
                (status, resultado, _utcnow(), comando_id),
            )
            conn.commit()
        return
    conn = conectar()
    conn.execute(
        "UPDATE comandos SET status = ?, resultado = ?, processado_em = ? WHERE id = ?",
        (status, resultado, datetime.now().isoformat(), comando_id),
    )
    conn.commit()
    conn.close()


def listar_comandos(limite: int = 20) -> list[dict[str, Any]]:
    limite = max(1, min(int(limite), 200))
    if _backend() == "postgres":
        with _pg_connection() as conn:
            cur = conn.execute(
                "SELECT id, criado_em, comando, params, origem, status, resultado,"
                " processado_em FROM comandos ORDER BY id DESC LIMIT %s",
                (limite,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": int(r["id"]),
                "criado_em": str(r["criado_em"]),
                "comando": r["comando"],
                "params": r["params"],
                "origem": r["origem"],
                "status": r["status"],
                "resultado": r["resultado"],
                "processado_em": str(r["processado_em"]) if r["processado_em"] else None,
            }
            for r in rows
        ]
    conn = conectar()
    rows = conn.execute(
        "SELECT id, criado_em, comando, params, origem, status, resultado, processado_em"
        " FROM comandos ORDER BY id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_bot_events(
    *,
    limite: int = 100,
    severidade: str | None = None,
    tipo: str | None = None,
    desde_id: int | None = None,
    crescente: bool = False,
) -> list[dict[str, Any]]:
    """LEITOR de bot_events — a tabela nao tinha nenhum (I-9).

    Eram 14 call sites de escrita, incluindo thread_crash CRITICAL, divergencia
    local-vs-exchange e fill sem persistencia, e ZERO `SELECT` em todo o
    repositorio: 4 linhas em 4 meses, num destino que ninguem lia. Escrever
    incidente em tabela sem leitor e o mesmo que nao registrar.

    desde_id permite consumo incremental (o vigia de main.py escala so o que
    ainda nao viu, sem re-alertar o historico a cada varredura).

    `crescente` NAO e cosmetico, e o que torna o consumo incremental correto:

      - DESC + LIMIT n devolve os n MAIS NOVOS acima do cursor. Quem depois
        avanca o cursor para o maior id visto PULA para sempre tudo que ficou
        entre o cursor antigo e o lote — perda silenciosa, justo na tabela de
        incidentes. Serve para HISTORICO (dashboard), nao para consumo.
      - ASC + LIMIT n devolve os n mais ANTIGOS ainda nao vistos; avancar o
        cursor para o maior deles nao deixa lacuna, e o lote seguinte continua
        de onde este parou.
    """
    onde, params = [], []
    if severidade:
        onde.append("severity = ?")
        params.append(severidade.upper())
    if tipo:
        onde.append("event_type = ?")
        params.append(tipo)
    if desde_id is not None:
        onde.append("id > ?")
        params.append(desde_id)
    clausula = (" WHERE " + " AND ".join(onde)) if onde else ""
    cols = "id, timestamp, service, symbol, event_type, severity, message"
    # `cols` e os fragmentos de `onde` sao literais deste modulo; TODO valor
    # vindo do chamador (severidade, tipo, desde_id, limite) vai em `params`,
    # por placeholder ligado. A isencao abaixo vale so para esse padrao — ha
    # dois testes dedicados (test_alertas.py::test_injecao_por_*) que provam
    # que severidade/tipo maliciosos nao alteram a query.
    ordem = "ASC" if crescente else "DESC"
    sql = f"SELECT {cols} FROM bot_events{clausula} ORDER BY id {ordem} LIMIT ?"  # nosec B608
    params.append(int(limite))

    if _backend() == "postgres":
        with _pg_connection() as conn:
            cur = conn.execute(sql.replace("?", "%s"), tuple(params))
            return [dict(r) for r in cur.fetchall()]

    conn = conectar()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def salvar_metricas_modelo(
    symbol: str,
    modelo_tipo: str,
    auc: float | None,
    cv_auc_mean: float | None,
    cv_auc_std: float | None,
) -> None:
    """P1-4: registra o resultado de UM retreino (auc holdout + purged CV) --
    historico usado por validacao.detectar_drift() para alertar (nao
    bloquear) quando um retreino semanal produz um modelo pior que o
    histórico recente. Chamado por ml_filtro.py/lstm_modelo.py apos cada
    treinar(), independente de ter detectado drift ou nao."""
    sym = symbol.upper()
    if _backend() == "postgres":
        with _pg_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_metricas
                    (timestamp, symbol, modelo_tipo, auc, cv_auc_mean, cv_auc_std)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (_utcnow(), sym, modelo_tipo, auc, cv_auc_mean, cv_auc_std),
            )
            conn.commit()
        return

    conn = conectar()
    conn.execute(
        """
        INSERT INTO model_metricas (timestamp, symbol, modelo_tipo, auc, cv_auc_mean, cv_auc_std)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (datetime.now().isoformat(), sym, modelo_tipo, auc, cv_auc_mean, cv_auc_std),
    )
    conn.commit()
    conn.close()


def historico_cv_auc_modelo(symbol: str, modelo_tipo: str, limit: int = 20) -> list[float]:
    """P1-4: lista de cv_auc_mean dos ULTIMOS retreinos (mais recente
    primeiro), para validacao.detectar_drift() comparar contra o retreino
    atual. Exclui entradas com cv_auc_mean NULL (CV sem dados suficientes
    naquele retreino, ver ml_filtro.py/lstm_modelo.py)."""
    sym = symbol.upper()
    if _backend() == "postgres":
        with _pg_connection() as conn:
            rows = conn.execute(
                """
                SELECT cv_auc_mean FROM model_metricas
                WHERE symbol = %s AND modelo_tipo = %s AND cv_auc_mean IS NOT NULL
                ORDER BY id DESC LIMIT %s
                """,
                (sym, modelo_tipo, limit),
            ).fetchall()
            # I-13: mesmo defeito de `salvar_sinal` — dict_row nao aceita
            # indice por posicao. Aqui a consequencia era mais silenciosa: o
            # guard-rail de drift (`verificar_drift_e_registrar`) engole
            # excecoes de proposito, entao sob Postgres o historico voltaria
            # sempre vazio e o detector nunca dispararia — sem nenhum erro
            # visivel.
            return [_primeiro_valor(row) for row in rows]

    conn = conectar()
    rows = conn.execute(
        """
        SELECT cv_auc_mean FROM model_metricas
        WHERE symbol = ? AND modelo_tipo = ? AND cv_auc_mean IS NOT NULL
        ORDER BY id DESC LIMIT ?
        """,
        (sym, modelo_tipo, limit),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


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
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            ).fetchall()
            for row in rows:
                print(f"  - {row['table_name']}")
    else:
        conn = conectar()
        tabelas = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for t in tabelas:
            print(f"  - {t[0]}")
        conn.close()
