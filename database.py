"""
Banco de dados SQLite local para o BotBinance.
Salva: candles, trades do WebSocket, snapshots de mercado, sinais gerados.
"""

import sqlite3
import os
from datetime import datetime
from config.settings import DB_PATH


def conectar():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar():
    """Cria todas as tabelas se não existirem."""
    conn = conectar()
    c = conn.cursor()

    # Trades individuais capturados pelo WebSocket
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            preco       REAL    NOT NULL,
            volume_btc  REAL    NOT NULL,
            volume_usdt REAL    NOT NULL,
            direcao     TEXT    NOT NULL,  -- 'COMPRA' ou 'VENDA'
            eh_baleia   INTEGER DEFAULT 0  -- 1 se volume >= 5 BTC
        )
    """)

    # Snapshots periódicos do mercado (order book, funding, OI)
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

    # CVD acumulado por janelas de tempo
    c.execute("""
        CREATE TABLE IF NOT EXISTS cvd_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            cvd         REAL NOT NULL,
            compras_btc REAL NOT NULL,
            vendas_btc  REAL NOT NULL
        )
    """)

    # Sinais gerados pela estratégia
    c.execute("""
        CREATE TABLE IF NOT EXISTS sinais (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            tipo        TEXT    NOT NULL,  -- 'COMPRA' ou 'VENDA'
            preco       REAL    NOT NULL,
            motivo      TEXT,
            executado   INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Banco de dados inicializado em: {DB_PATH}")


def salvar_trade(preco, volume_btc, direcao, whale_threshold=5.0):
    conn = conectar()
    conn.execute("""
        INSERT INTO trades (timestamp, preco, volume_btc, volume_usdt, direcao, eh_baleia)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        preco,
        volume_btc,
        preco * volume_btc,
        direcao,
        1 if volume_btc >= whale_threshold else 0
    ))
    conn.commit()
    conn.close()


def salvar_snapshot(dados: dict):
    conn = conectar()
    conn.execute("""
        INSERT INTO snapshots_mercado (
            timestamp, preco, variacao_24h, volume_24h_btc,
            funding_rate, open_interest_btc,
            ema20_1h, ema50_1h, rsi_1h, tendencia,
            pressao_order_book, liquidez_compra_usdt, liquidez_venda_usdt
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
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
    ))
    conn.commit()
    conn.close()


def salvar_cvd(cvd, compras_btc, vendas_btc):
    conn = conectar()
    conn.execute("""
        INSERT INTO cvd_historico (timestamp, cvd, compras_btc, vendas_btc)
        VALUES (?, ?, ?, ?)
    """, (datetime.now().isoformat(), cvd, compras_btc, vendas_btc))
    conn.commit()
    conn.close()


def salvar_sinal(tipo, preco, motivo):
    conn = conectar()
    conn.execute("""
        INSERT INTO sinais (timestamp, tipo, preco, motivo)
        VALUES (?, ?, ?, ?)
    """, (datetime.now().isoformat(), tipo, preco, motivo))
    conn.commit()
    conn.close()
    print(f"[DB] Sinal salvo: {tipo} @ ${preco:,.2f} — {motivo}")


def ultimos_snapshots(n=10):
    conn = conectar()
    rows = conn.execute("""
        SELECT * FROM snapshots_mercado ORDER BY id DESC LIMIT ?
    """, (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resumo_trades(minutos=60):
    conn = conectar()
    rows = conn.execute("""
        SELECT direcao, COUNT(*) as qtd, SUM(volume_btc) as total_btc
        FROM trades
        WHERE timestamp >= datetime('now', ?)
        GROUP BY direcao
    """, (f"-{minutos} minutes",)).fetchall()
    conn.close()
    return {r["direcao"]: {"qtd": r["qtd"], "total_btc": r["total_btc"]} for r in rows}


if __name__ == "__main__":
    inicializar()
    print("[DB] Tabelas criadas com sucesso.")
    print("[DB] Estrutura:")
    conn = conectar()
    tabelas = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for t in tabelas:
        print(f"  - {t[0]}")
    conn.close()
