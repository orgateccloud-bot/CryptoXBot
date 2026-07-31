"""
Logger Estruturado — BotBinance
=================================
Salva todas as decisoes do bot para analise posterior.

Backend: segue a mesma configuracao do database.py — SQLite localmente,
Postgres/Supabase quando DATABASE_URL+DATABASE_BACKEND estao definidos. Assim os
logs analiticos NAO ficam num SQLite paralelo em producao (evita split-brain).

Logs salvos:
  - Cada avaliacao de sinal (score, filtros, ML, decisao)
  - Cada trade (entrada, saida, PnL)
  - Performance acumulada (diaria/semanal)

Uso:
  from logger import logger
  logger.registrar_avaliacao(resultado_estrategia)
  logger.warning("...")   # tambem delega para logging padrao (usado por main.py)
"""

import csv
import logging
import os
import sqlite3
from datetime import datetime

from config.runtime_settings import DATABASE_BACKEND, DATABASE_URL, DB_PATH

# Logger estruturado padrão (stdlib) usado para mensagens operacionais.
_stdlog = logging.getLogger("botbinance")


class _FormatterComExtra(logging.Formatter):
    """Anexa à mensagem os campos passados em `extra=`.

    Sem handler próprio, o stdlib cai no `lastResort`, que imprime SÓ a mensagem
    e DESCARTA o `extra`. Como todo o diagnóstico de WebSocket vive no extra
    (error, attempt, latency_ms, next_retry_in_s), o log ficava assim:

        Erro crítico WebSocket
        Máximo de tentativas atingido

    — sem o erro, sem a tentativa, sem nada. Foi o que impediu diagnosticar uma
    queda de 8h40 em julho e, de novo, a queda de 2026-07-31.
    """

    _PADRAO = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "message", "asctime", "taskName",
    }

    def format(self, record):
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in self._PADRAO and not k.startswith("_")
        }
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return base


def _configurar_stdlog() -> None:
    """Instala um handler que PRESERVA o extra. Idempotente."""
    if any(getattr(h, "_bxbot", False) for h in _stdlog.handlers):
        return
    h = logging.StreamHandler()
    h.setFormatter(_FormatterComExtra("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    h._bxbot = True  # marca para não duplicar em reimport/reload
    _stdlog.addHandler(h)
    _stdlog.setLevel(logging.INFO)
    _stdlog.propagate = False  # evita eco no root


_configurar_stdlog()

_TABELAS_EXPORT = {"log_avaliacoes", "log_trades", "log_performance"}


def _is_postgres() -> bool:
    return bool(DATABASE_URL) and DATABASE_BACKEND in ("postgres", "postgresql", "supabase")


class LoggerBot:

    def __init__(self, db_path=None, log_dir="logs"):
        self.db_path = db_path or DB_PATH
        self.log_dir = log_dir
        self._pg = _is_postgres()
        self._ph = "%s" if self._pg else "?"
        os.makedirs(log_dir, exist_ok=True)
        # Init tolerante: um problema transitório de DB no boot não deve travar/
        # quebrar o import do bot (as escritas já têm try/except próprio).
        try:
            self._inicializar_tabelas()
        except Exception as e:
            _stdlog.warning(
                "LoggerBot: falha ao inicializar tabelas (%s) — logging analitico degradado", e
            )

    # ── Compat com logging.Logger ──────────────────────────────
    # main.py usa o mesmo objeto `logger` para analytics (registrar_*) e para
    # logs operacionais (.warning/.error/.critical). Sem isto, esses últimos
    # davam AttributeError nos caminhos de erro do WebSocket.
    def info(self, msg, *args, **kwargs):
        _stdlog.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        _stdlog.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        _stdlog.error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        _stdlog.critical(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        _stdlog.debug(msg, *args, **kwargs)

    # ── Conexão backend-aware ──────────────────────────────────
    def _sql(self, sql: str) -> str:
        """Converte placeholders ? (SQLite) -> %s (psycopg) quando em Postgres."""
        return sql.replace("?", "%s") if self._pg else sql

    def _connect(self):
        if self._pg:
            import psycopg

            return psycopg.connect(DATABASE_URL, connect_timeout=10)
        return sqlite3.connect(self.db_path)

    def _connect_rows(self):
        """Conexão com linhas indexáveis por nome de coluna (para exports)."""
        if self._pg:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _inicializar_tabelas(self):
        id_col = "BIGSERIAL PRIMARY KEY" if self._pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn = self._connect()
        try:
            cur = conn.cursor()

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS log_avaliacoes (
                    id          {id_col},
                    timestamp   TEXT NOT NULL,
                    symbol      TEXT DEFAULT 'BTCUSDT',
                    preco       REAL,
                    score       INTEGER,
                    decisao     TEXT,
                    sinal       TEXT,
                    tamanho_fator REAL,
                    regime      TEXT,
                    fear_greed  INTEGER,
                    tend_4h     TEXT,
                    rsi         REAL,
                    ema20       REAL,
                    ema50       REAL,
                    vwap        REAL,
                    atr         REAL,
                    vol_rel     REAL,
                    ml_xgb      REAL,
                    ml_lstm     REAL,
                    ml_ensemble REAL,
                    ml_confianca TEXT,
                    cvd         REAL,
                    filtros_ok  INTEGER,
                    filtros_total INTEGER,
                    bloqueios   TEXT
                )
            """)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS log_trades (
                    id          {id_col},
                    timestamp_entrada TEXT NOT NULL,
                    timestamp_saida   TEXT,
                    symbol      TEXT DEFAULT 'BTCUSDT',
                    direcao     TEXT,
                    preco_entrada REAL,
                    preco_saida   REAL,
                    tamanho_btc   REAL,
                    tamanho_usdt  REAL,
                    stop_loss     REAL,
                    take_profit   REAL,
                    score_entrada INTEGER,
                    ml_prob       REAL,
                    tipo_saida    TEXT,
                    pnl_usdt      REAL,
                    pnl_pct       REAL,
                    capital_apos  REAL,
                    motivo_saida  TEXT
                )
            """)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS log_performance (
                    id          {id_col},
                    data        TEXT NOT NULL UNIQUE,
                    symbol      TEXT DEFAULT 'BTCUSDT',
                    trades_total    INTEGER DEFAULT 0,
                    trades_ganhos   INTEGER DEFAULT 0,
                    trades_perdas   INTEGER DEFAULT 0,
                    pnl_dia_usdt    REAL DEFAULT 0,
                    pnl_dia_pct     REAL DEFAULT 0,
                    capital_inicio  REAL,
                    capital_fim     REAL,
                    avaliacoes      INTEGER DEFAULT 0,
                    sinais_gerados  INTEGER DEFAULT 0,
                    score_medio     REAL,
                    max_drawdown_dia REAL DEFAULT 0
                )
            """)

            conn.commit()
        finally:
            conn.close()

    def registrar_avaliacao(self, resultado, symbol="BTCUSDT"):
        """Registra uma avaliacao de estrategia no banco."""
        conn = self._connect()
        try:
            score_result = resultado.get("score_result", {})
            conn.execute(
                self._sql("""
                INSERT INTO log_avaliacoes (
                    timestamp, symbol, preco, score, decisao, sinal, tamanho_fator,
                    regime, fear_greed, tend_4h, rsi, ema20, ema50, vwap, atr, vol_rel,
                    ml_xgb, ml_lstm, ml_ensemble, ml_confianca, cvd,
                    filtros_ok, filtros_total, bloqueios
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """),
                (
                    resultado.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    symbol,
                    resultado.get("preco"),
                    resultado.get("score"),
                    resultado.get("score_decisao"),
                    resultado.get("sinal"),
                    resultado.get("tamanho_fator"),
                    resultado.get("regime"),
                    resultado.get("fear_greed"),
                    resultado.get("tend_4h"),
                    resultado.get("rsi"),
                    resultado.get("ema20_1h"),
                    resultado.get("ema50_1h"),
                    resultado.get("vwap"),
                    resultado.get("atr"),
                    resultado.get("volume_rel"),
                    resultado.get("ml_xgb"),
                    resultado.get("ml_lstm"),
                    resultado.get("ml_ensemble"),
                    resultado.get("ml_confianca"),
                    resultado.get("cvd"),
                    resultado.get("filtros_ok"),
                    resultado.get("filtros_total"),
                    " | ".join(score_result.get("bloqueios", [])),
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao registrar avaliacao: {e}")
        finally:
            conn.close()

    def registrar_trade_entrada(
        self, symbol, direcao, preco, tamanho_btc, tamanho_usdt, stop, target, score, ml_prob
    ):
        """Registra entrada de trade. Retorna o id da linha criada."""
        conn = self._connect()
        try:
            params = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                direcao,
                preco,
                tamanho_btc,
                tamanho_usdt,
                stop,
                target,
                score,
                ml_prob,
            )
            cols = """
                INSERT INTO log_trades (
                    timestamp_entrada, symbol, direcao, preco_entrada,
                    tamanho_btc, tamanho_usdt, stop_loss, take_profit,
                    score_entrada, ml_prob
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """
            if self._pg:
                row = conn.execute(self._sql(cols) + " RETURNING id", params).fetchone()
                conn.commit()
                return row[0] if row else None
            cur = conn.execute(cols, params)
            conn.commit()
            return cur.lastrowid
        except Exception as e:
            print(f"[LOG] Erro ao registrar trade entrada: {e}")
            return None
        finally:
            conn.close()

    def registrar_trade_saida(
        self, trade_id, preco_saida, tipo_saida, pnl_usdt, pnl_pct, capital_apos, motivo=""
    ):
        """Registra saida de trade."""
        conn = self._connect()
        try:
            conn.execute(
                self._sql("""
                UPDATE log_trades SET
                    timestamp_saida=?, preco_saida=?, tipo_saida=?,
                    pnl_usdt=?, pnl_pct=?, capital_apos=?, motivo_saida=?
                WHERE id=?
            """),
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    preco_saida,
                    tipo_saida,
                    pnl_usdt,
                    pnl_pct,
                    capital_apos,
                    motivo,
                    trade_id,
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao registrar trade saida: {e}")
        finally:
            conn.close()

    def atualizar_performance_diaria(self, symbol="BTCUSDT"):
        """Calcula e salva performance do dia."""
        conn = self._connect()
        hoje = datetime.now().strftime("%Y-%m-%d")
        try:
            trades = conn.execute(
                self._sql("""
                SELECT pnl_usdt, pnl_pct FROM log_trades
                WHERE symbol=? AND timestamp_saida LIKE ?
                AND pnl_usdt IS NOT NULL
            """),
                (symbol, f"{hoje}%"),
            ).fetchall()

            avals = conn.execute(
                self._sql("""
                SELECT score, sinal FROM log_avaliacoes
                WHERE symbol=? AND timestamp LIKE ?
            """),
                (symbol, f"{hoje}%"),
            ).fetchall()

            total = len(trades)
            ganhos = sum(1 for t in trades if t[0] > 0)
            perdas = total - ganhos
            pnl_total = sum(t[0] for t in trades)
            pnl_pct = sum(t[1] for t in trades) if trades else 0

            sinais = sum(1 for a in avals if a[1] in ("COMPRA", "VENDA"))
            scores = [a[0] for a in avals if a[0] is not None]
            score_medio = sum(scores) / len(scores) if scores else 0

            valores = (
                hoje,
                symbol,
                total,
                ganhos,
                perdas,
                pnl_total,
                pnl_pct,
                len(avals),
                sinais,
                score_medio,
            )
            if self._pg:
                conn.execute(
                    """
                    INSERT INTO log_performance (
                        data, symbol, trades_total, trades_ganhos, trades_perdas,
                        pnl_dia_usdt, pnl_dia_pct, avaliacoes, sinais_gerados, score_medio
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (data) DO UPDATE SET
                        symbol=EXCLUDED.symbol, trades_total=EXCLUDED.trades_total,
                        trades_ganhos=EXCLUDED.trades_ganhos, trades_perdas=EXCLUDED.trades_perdas,
                        pnl_dia_usdt=EXCLUDED.pnl_dia_usdt, pnl_dia_pct=EXCLUDED.pnl_dia_pct,
                        avaliacoes=EXCLUDED.avaliacoes, sinais_gerados=EXCLUDED.sinais_gerados,
                        score_medio=EXCLUDED.score_medio
                """,
                    valores,
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO log_performance (
                        data, symbol, trades_total, trades_ganhos, trades_perdas,
                        pnl_dia_usdt, pnl_dia_pct, avaliacoes, sinais_gerados, score_medio
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                    valores,
                )
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao atualizar performance: {e}")
        finally:
            conn.close()

    def exportar_csv(self, tabela="log_avaliacoes", dias=30):
        """Exporta logs para CSV."""
        if tabela not in _TABELAS_EXPORT:
            # Whitelist de tabelas (M-6): evita interpolacao de nome de tabela
            # em SQL (injection) caso 'tabela' venha de origem nao confiavel.
            raise ValueError(
                f"Tabela nao permitida para exportacao: {tabela!r}. "
                f"Use: log_avaliacoes, log_trades ou log_performance."
            )
        conn = self._connect_rows()
        try:
            if tabela == "log_avaliacoes":
                rows = conn.execute(
                    self._sql("SELECT * FROM log_avaliacoes ORDER BY timestamp DESC LIMIT ?"),
                    (dias * 96,),
                ).fetchall()  # ~96 avaliacoes/dia a cada 15min
            elif tabela == "log_trades":
                rows = conn.execute(
                    self._sql("SELECT * FROM log_trades ORDER BY timestamp_entrada DESC LIMIT ?"),
                    (dias * 10,),
                ).fetchall()
            else:  # log_performance
                rows = conn.execute(
                    self._sql("SELECT * FROM log_performance ORDER BY data DESC LIMIT ?"), (dias,)
                ).fetchall()

            if not rows:
                print(f"[LOG] Nenhum registro em {tabela}")
                return None

            header = list(rows[0].keys())
            filepath = os.path.join(
                self.log_dir, f"{tabela}_{datetime.now().strftime('%Y%m%d')}.csv"
            )
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                for row in rows:
                    writer.writerow([row[k] for k in header])

            print(f"[LOG] Exportado: {filepath} ({len(rows)} registros)")
            return filepath
        except Exception as e:
            print(f"[LOG] Erro ao exportar CSV: {e}")
            return None
        finally:
            conn.close()

    def dados_relatorio_diario(self, symbol="BTCUSDT"):
        """Dados brutos do relatorio do dia (sem imprimir) -- usado tanto por
        relatorio_diario() (print) quanto pelo alerta Telegram agendado
        (P2-5, main.py) para nao duplicar a query."""
        conn = self._connect()
        hoje = datetime.now().strftime("%Y-%m-%d")
        try:
            avals = conn.execute(
                self._sql("""
                SELECT COUNT(*), AVG(score),
                       SUM(CASE WHEN sinal IN ('COMPRA','VENDA') THEN 1 ELSE 0 END)
                FROM log_avaliacoes WHERE symbol=? AND timestamp LIKE ?
            """),
                (symbol, f"{hoje}%"),
            ).fetchone()

            trades = conn.execute(
                self._sql("""
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END),
                       SUM(pnl_usdt), SUM(pnl_pct)
                FROM log_trades WHERE symbol=? AND timestamp_saida LIKE ?
                AND pnl_usdt IS NOT NULL
            """),
                (symbol, f"{hoje}%"),
            ).fetchone()
        finally:
            conn.close()

        trades_dia = trades[0] or 0
        win_rate = (trades[1] or 0) / trades_dia * 100 if trades_dia > 0 else 0.0
        return {
            "hoje": hoje,
            "avaliacoes": avals[0] or 0,
            "score_medio": avals[1] or 0.0,
            "sinais_gerados": avals[2] or 0,
            "trades_dia": trades_dia,
            "win_rate": win_rate,
            "pnl_usdt": trades[2] or 0.0,
            "pnl_pct": trades[3] or 0.0,
        }

    def relatorio_diario(self, symbol="BTCUSDT"):
        """Imprime relatorio do dia."""
        d = self.dados_relatorio_diario(symbol)
        print(f"\n  RELATORIO DIARIO — {d['hoje']} ({symbol})")
        print(f"  {'='*45}")
        print(f"  Avaliacoes:     {d['avaliacoes']}")
        print(f"  Score medio:    {d['score_medio']:.1f}")
        print(f"  Sinais gerados: {d['sinais_gerados']}")
        print(f"  Trades feitos:  {d['trades_dia']}")
        if d["trades_dia"] > 0:
            print(f"  Win Rate:       {d['win_rate']:.1f}%")
            print(f"  PnL dia:        ${d['pnl_usdt']:,.2f} ({d['pnl_pct']:.2f}%)")
        print(f"  {'='*45}")

    def ultimos_trades(self, n=10, symbol="BTCUSDT"):
        """Retorna ultimos N trades."""
        conn = self._connect_rows()
        try:
            rows = conn.execute(
                self._sql("""
                SELECT * FROM log_trades
                WHERE symbol=? AND pnl_usdt IS NOT NULL
                ORDER BY timestamp_saida DESC LIMIT ?
            """),
                (symbol, n),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]


# Instancia global
logger = LoggerBot()
