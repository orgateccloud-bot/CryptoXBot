"""
Logger Estruturado — BotBinance
=================================
Salva todas as decisoes do bot em formato CSV + SQLite para analise posterior.

Logs salvos:
  - Cada avaliacao de sinal (score, filtros, ML, decisao)
  - Cada trade (entrada, saida, PnL)
  - Performance acumulada (diaria/semanal)

Uso:
  from logger import LoggerBot
  log = LoggerBot()
  log.registrar_avaliacao(resultado_estrategia)
  log.registrar_trade(trade_info)
  log.relatorio_diario()
"""

import os
import csv
import sqlite3
import logging
from datetime import datetime

# Logger estruturado padrão (stdlib) usado para mensagens operacionais.
# main.py importa `logger` daqui e chama .warning/.error/.critical/.info —
# por isso LoggerBot delega esses métodos para este logger (ver abaixo).
_stdlog = logging.getLogger("botbinance")


class LoggerBot:

    def __init__(self, db_path="data/btc_data.db", log_dir="logs"):
        self.db_path = db_path
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._inicializar_tabelas()

    # ── Compat com logging.Logger ──────────────────────────────
    # main.py usa o mesmo objeto `logger` tanto para analytics
    # (registrar_avaliacao/registrar_trade) quanto para logs operacionais
    # (.warning/.error/.critical). Sem isto, esses últimos davam AttributeError
    # nos caminhos de erro do WebSocket, derrubando o handler.
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

    def _inicializar_tabelas(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS log_avaliacoes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
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

        c.execute("""
            CREATE TABLE IF NOT EXISTS log_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
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

        c.execute("""
            CREATE TABLE IF NOT EXISTS log_performance (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
        conn.close()

    def registrar_avaliacao(self, resultado, symbol="BTCUSDT"):
        """Registra uma avaliacao de estrategia no banco."""
        conn = sqlite3.connect(self.db_path)
        try:
            score_result = resultado.get("score_result", {})
            conn.execute("""
                INSERT INTO log_avaliacoes (
                    timestamp, symbol, preco, score, decisao, sinal, tamanho_fator,
                    regime, fear_greed, tend_4h, rsi, ema20, ema50, vwap, atr, vol_rel,
                    ml_xgb, ml_lstm, ml_ensemble, ml_confianca, cvd,
                    filtros_ok, filtros_total, bloqueios
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
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
            ))
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao registrar avaliacao: {e}")
        finally:
            conn.close()

    def registrar_trade_entrada(self, symbol, direcao, preco, tamanho_btc, tamanho_usdt,
                                 stop, target, score, ml_prob):
        """Registra entrada de trade."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO log_trades (
                    timestamp_entrada, symbol, direcao, preco_entrada,
                    tamanho_btc, tamanho_usdt, stop_loss, take_profit,
                    score_entrada, ml_prob
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol, direcao, preco, tamanho_btc, tamanho_usdt,
                stop, target, score, ml_prob,
            ))
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception as e:
            print(f"[LOG] Erro ao registrar trade entrada: {e}")
            return None
        finally:
            conn.close()

    def registrar_trade_saida(self, trade_id, preco_saida, tipo_saida, pnl_usdt,
                               pnl_pct, capital_apos, motivo=""):
        """Registra saida de trade."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                UPDATE log_trades SET
                    timestamp_saida=?, preco_saida=?, tipo_saida=?,
                    pnl_usdt=?, pnl_pct=?, capital_apos=?, motivo_saida=?
                WHERE id=?
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                preco_saida, tipo_saida, pnl_usdt, pnl_pct,
                capital_apos, motivo, trade_id,
            ))
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao registrar trade saida: {e}")
        finally:
            conn.close()

    def atualizar_performance_diaria(self, symbol="BTCUSDT"):
        """Calcula e salva performance do dia."""
        conn = sqlite3.connect(self.db_path)
        hoje = datetime.now().strftime("%Y-%m-%d")
        try:
            # Trades do dia
            trades = conn.execute("""
                SELECT pnl_usdt, pnl_pct FROM log_trades
                WHERE symbol=? AND timestamp_saida LIKE ?
                AND pnl_usdt IS NOT NULL
            """, (symbol, f"{hoje}%")).fetchall()

            # Avaliacoes do dia
            avals = conn.execute("""
                SELECT score, sinal FROM log_avaliacoes
                WHERE symbol=? AND timestamp LIKE ?
            """, (symbol, f"{hoje}%")).fetchall()

            total = len(trades)
            ganhos = sum(1 for t in trades if t[0] > 0)
            perdas = total - ganhos
            pnl_total = sum(t[0] for t in trades)
            pnl_pct = sum(t[1] for t in trades) if trades else 0

            sinais = sum(1 for a in avals if a[1] in ("COMPRA", "VENDA"))
            scores = [a[0] for a in avals if a[0] is not None]
            score_medio = sum(scores) / len(scores) if scores else 0

            conn.execute("""
                INSERT OR REPLACE INTO log_performance (
                    data, symbol, trades_total, trades_ganhos, trades_perdas,
                    pnl_dia_usdt, pnl_dia_pct, avaliacoes, sinais_gerados, score_medio
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (hoje, symbol, total, ganhos, perdas, pnl_total, pnl_pct,
                  len(avals), sinais, score_medio))
            conn.commit()
        except Exception as e:
            print(f"[LOG] Erro ao atualizar performance: {e}")
        finally:
            conn.close()

    def exportar_csv(self, tabela="log_avaliacoes", dias=30):
        """Exporta logs para CSV."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if tabela == "log_avaliacoes":
                rows = conn.execute("""
                    SELECT * FROM log_avaliacoes
                    ORDER BY timestamp DESC LIMIT ?
                """, (dias * 96,)).fetchall()  # ~96 avaliacoes/dia a cada 15min
            elif tabela == "log_trades":
                rows = conn.execute("""
                    SELECT * FROM log_trades
                    ORDER BY timestamp_entrada DESC LIMIT ?
                """, (dias * 10,)).fetchall()
            elif tabela == "log_performance":
                rows = conn.execute(
                    "SELECT * FROM log_performance ORDER BY data DESC LIMIT ?",
                    (dias,)).fetchall()
            else:
                # Whitelist de tabelas (M-6): evita interpolacao de nome de tabela
                # em SQL (injection) caso 'tabela' venha de origem nao confiavel.
                raise ValueError(
                    f"Tabela nao permitida para exportacao: {tabela!r}. "
                    f"Use: log_avaliacoes, log_trades ou log_performance."
                )

            if not rows:
                print(f"[LOG] Nenhum registro em {tabela}")
                return None

            filepath = os.path.join(self.log_dir, f"{tabela}_{datetime.now().strftime('%Y%m%d')}.csv")
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(tuple(row))

            print(f"[LOG] Exportado: {filepath} ({len(rows)} registros)")
            return filepath
        except Exception as e:
            print(f"[LOG] Erro ao exportar CSV: {e}")
            return None
        finally:
            conn.close()

    def relatorio_diario(self, symbol="BTCUSDT"):
        """Imprime relatorio do dia."""
        conn = sqlite3.connect(self.db_path)
        hoje = datetime.now().strftime("%Y-%m-%d")

        avals = conn.execute("""
            SELECT COUNT(*), AVG(score),
                   SUM(CASE WHEN sinal IN ('COMPRA','VENDA') THEN 1 ELSE 0 END)
            FROM log_avaliacoes WHERE symbol=? AND timestamp LIKE ?
        """, (symbol, f"{hoje}%")).fetchone()

        trades = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END),
                   SUM(pnl_usdt), SUM(pnl_pct)
            FROM log_trades WHERE symbol=? AND timestamp_saida LIKE ?
            AND pnl_usdt IS NOT NULL
        """, (symbol, f"{hoje}%")).fetchone()

        conn.close()

        print(f"\n  RELATORIO DIARIO — {hoje} ({symbol})")
        print(f"  {'='*45}")
        print(f"  Avaliacoes:     {avals[0] or 0}")
        print(f"  Score medio:    {avals[1] or 0:.1f}")
        print(f"  Sinais gerados: {avals[2] or 0}")
        print(f"  Trades feitos:  {trades[0] or 0}")
        if trades[0] and trades[0] > 0:
            wr = (trades[1] or 0) / trades[0] * 100
            print(f"  Win Rate:       {wr:.1f}%")
            print(f"  PnL dia:        ${trades[2] or 0:,.2f} ({trades[3] or 0:.2f}%)")
        print(f"  {'='*45}")

    def ultimos_trades(self, n=10, symbol="BTCUSDT"):
        """Retorna ultimos N trades."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM log_trades
            WHERE symbol=? AND pnl_usdt IS NOT NULL
            ORDER BY timestamp_saida DESC LIMIT ?
        """, (symbol, n)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# Instancia global
logger = LoggerBot()
