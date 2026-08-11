"""
Coletor de histórico de funding rate (Binance USDT-M perpétuos)
===============================================================
Endpoint público `GET /fapi/v1/fundingRate` (sem API key). Pagina de 8h em 8h,
1000 registros por lote. Salva em SQLite (tabela `funding`), idempotente.

Uso:
  python research/coletar_funding.py                  # BTCUSDT + ETHUSDT
  python research/coletar_funding.py --par SOLUSDT
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from datetime import datetime, timezone

import requests

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "btc_data.db"
)
BASE = "https://fapi.binance.com"
PARES = ("BTCUSDT", "ETHUSDT")
INICIO_PADRAO = "2019-01-01"


def criar_tabela(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS funding (
            symbol       TEXT    NOT NULL,
            funding_time INTEGER NOT NULL,
            funding_rate REAL    NOT NULL,
            UNIQUE(symbol, funding_time)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_funding ON funding(symbol, funding_time)")
    conn.commit()


def baixar(symbol, inicio_ms):
    """Pagina do início até agora. Retorna lista de (time_ms, rate)."""
    todos = []
    atual = inicio_ms
    while True:
        try:
            r = requests.get(
                f"{BASE}/fapi/v1/fundingRate",
                params={"symbol": symbol, "startTime": atual, "limit": 1000},
                timeout=20,
            )
            r.raise_for_status()
            lote = r.json()
        except Exception as e:
            print(f"  ERRO no lote ({e}) — parando a coleta deste par")
            break
        if not isinstance(lote, list) or not lote:
            break
        for x in lote:
            todos.append((int(x["fundingTime"]), float(x["fundingRate"])))
        ultimo = int(lote[-1]["fundingTime"])
        if ultimo <= atual:
            break
        atual = ultimo + 1
        dt = datetime.fromtimestamp(ultimo / 1000, tz=timezone.utc)
        print(f"  [{symbol}] {len(todos):6d} registros | até {dt:%Y-%m-%d}")
        if len(lote) < 1000:
            break
        time.sleep(0.25)
    return todos


def salvar(conn, symbol, registros):
    conn.executemany(
        "INSERT OR IGNORE INTO funding (symbol, funding_time, funding_rate) VALUES (?,?,?)",
        [(symbol, t, r) for t, r in registros],
    )
    conn.commit()


def resumo(conn, pares):
    print("\n" + "=" * 64)
    print("  RESUMO — histórico de funding no banco")
    print("=" * 64)
    for sym in pares:
        row = conn.execute(
            "SELECT COUNT(*), MIN(funding_time), MAX(funding_time), AVG(funding_rate) "
            "FROM funding WHERE symbol=?",
            (sym,),
        ).fetchone()
        n, mn, mx, media = row
        if not n:
            print(f"  {sym}: vazio")
            continue
        d0 = datetime.fromtimestamp(mn / 1000, tz=timezone.utc)
        d1 = datetime.fromtimestamp(mx / 1000, tz=timezone.utc)
        anos = (mx - mn) / 1000 / 86400 / 365.25
        print(f"  {sym}: {n:6d} pagamentos | {d0:%Y-%m-%d} → {d1:%Y-%m-%d} ({anos:.1f} anos)")
        print(f"    funding médio por 8h: {media:+.6f}  (~{media*3*365*100:+.2f}% a.a. bruto)")


def main():
    ap = argparse.ArgumentParser(description="Coletor de funding rate histórico")
    ap.add_argument("--par", action="append", help="Par (repetível). Default: BTC+ETH")
    ap.add_argument("--inicio", default=INICIO_PADRAO)
    args = ap.parse_args()
    pares = tuple(p.upper() for p in args.par) if args.par else PARES

    inicio_ms = int(
        datetime.fromisoformat(args.inicio).replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    criar_tabela(conn)
    for sym in pares:
        print(f"\n[{sym}] coletando funding desde {args.inicio}...")
        regs = baixar(sym, inicio_ms)
        salvar(conn, sym, regs)
        print(f"  salvos/ignorados: {len(regs)} registros")
    resumo(conn, pares)
    conn.close()


if __name__ == "__main__":
    main()
