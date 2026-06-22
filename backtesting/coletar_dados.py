"""
Coleta histórico de klines da Binance e salva no banco SQLite.
Suporta múltiplos pares (BTC, ETH, SOL) e até 2 anos de dados.

Uso:
  python backtesting/coletar_dados.py                    → coleta BTC (padrão)
  python backtesting/coletar_dados.py --par ETHUSDT      → coleta ETH
  python backtesting/coletar_dados.py --todos             → coleta BTC + ETH + SOL
  python backtesting/coletar_dados.py --dias 730          → 2 anos de dados
"""

import requests
import sqlite3
import os
import sys
import time
import argparse
from datetime import datetime, timedelta

DB_PATH = "data/btc_data.db"
BASE_URL = "https://api.binance.com"

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

TIMEFRAMES = {
    "1h": {"intervalo": "1h", "dias": 730},  # 2 anos
    "4h": {"intervalo": "4h", "dias": 730},  # 2 anos
    "15m": {"intervalo": "15m", "dias": 90},  # 3 meses
    "1d": {"intervalo": "1d", "dias": 730},  # 2 anos
}


def conectar():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def criar_tabela(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT    NOT NULL,
            intervalo  TEXT    NOT NULL,
            timestamp  INTEGER NOT NULL,
            abertura   REAL,
            maxima     REAL,
            minima     REAL,
            fechamento REAL,
            volume     REAL,
            UNIQUE(symbol, intervalo, timestamp)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_klines ON klines(symbol, intervalo, timestamp)")
    conn.commit()


def baixar_klines(symbol, intervalo, dias):
    """Baixa klines em lotes de 1000 (limite da API Binance)."""
    fim = int(datetime.now().timestamp() * 1000)
    inicio = int((datetime.now() - timedelta(days=dias)).timestamp() * 1000)

    todos = []
    atual = inicio
    total_lotes = 0

    while atual < fim:
        params = {
            "symbol": symbol,
            "interval": intervalo,
            "startTime": atual,
            "endTime": fim,
            "limit": 1000,
        }
        try:
            r = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=10)
            r.raise_for_status()
            lote = r.json()
            if not lote:
                break
            todos.extend(lote)
            atual = lote[-1][0] + 1
            total_lotes += 1
            print(
                f"  [{symbol}/{intervalo}] Lote {total_lotes}: {len(lote)} candles "
                f"| Total: {len(todos)} "
                f"| Até: {datetime.fromtimestamp(lote[-1][0]/1000).strftime('%d/%m/%Y')}"
            )
            time.sleep(0.2)
        except Exception as e:
            print(f"  ERRO no lote: {e}")
            time.sleep(2)
            break

    return todos


def salvar_klines(conn, symbol, intervalo, klines):
    inseridos = 0
    for k in klines:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO klines
                (symbol, intervalo, timestamp, abertura, maxima, minima, fechamento, volume)
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    symbol,
                    intervalo,
                    int(k[0]),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                ),
            )
            inseridos += 1
        except Exception:
            pass
    conn.commit()
    return inseridos


def coletar_par(conn, symbol, timeframes_override=None):
    """Coleta todos os timeframes para um par."""
    tfs = timeframes_override or TIMEFRAMES
    for nome, cfg in tfs.items():
        print(f"\n[{symbol}/{nome}] Baixando {cfg['dias']} dias...")
        klines = baixar_klines(symbol, cfg["intervalo"], cfg["dias"])
        inseridos = salvar_klines(conn, symbol, nome, klines)
        print(f"  Salvo: {inseridos} candles no banco")


def resumo(conn, symbols):
    print("\n" + "=" * 60)
    print("  RESUMO DO BANCO")
    print("=" * 60)
    for symbol in symbols:
        print(f"\n  {symbol}:")
        for nome in TIMEFRAMES:
            count = conn.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            primeiro = conn.execute(
                "SELECT MIN(timestamp) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            ultimo = conn.execute(
                "SELECT MAX(timestamp) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            if primeiro and ultimo:
                dt_ini = datetime.fromtimestamp(primeiro / 1000).strftime("%d/%m/%Y")
                dt_fim = datetime.fromtimestamp(ultimo / 1000).strftime("%d/%m/%Y")
                print(f"    [{nome:4s}] {count:5d} candles  |  {dt_ini} ate {dt_fim}")
            elif count > 0:
                print(f"    [{nome:4s}] {count:5d} candles")


def main():
    parser = argparse.ArgumentParser(description="Coletor de dados historicos Binance")
    parser.add_argument("--par", default="BTCUSDT", help="Par para coletar (ex: BTCUSDT, ETHUSDT)")
    parser.add_argument("--todos", action="store_true", help="Coletar BTC + ETH + SOL")
    parser.add_argument("--dias", type=int, default=0, help="Override dias de historico")
    args = parser.parse_args()

    conn = conectar()
    criar_tabela(conn)

    # Override dias se especificado
    tfs = None
    if args.dias > 0:
        tfs = {k: {"intervalo": v["intervalo"], "dias": args.dias} for k, v in TIMEFRAMES.items()}

    if args.todos:
        symbols = PARES
    else:
        symbols = [args.par.upper()]

    print("\n" + "=" * 60)
    print(f"  COLETANDO HISTORICO — {', '.join(symbols)} (Binance)")
    print("=" * 60)

    for symbol in symbols:
        coletar_par(conn, symbol, tfs)

    resumo(conn, symbols)
    conn.close()
    print("\n  Dados prontos para backtesting e ML.")


if __name__ == "__main__":
    main()
