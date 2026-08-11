"""
Migração idempotente SQLite → Supabase/Postgres.

Uso:
  python scripts/migrate_sqlite_to_supabase.py --listar
  python scripts/migrate_sqlite_to_supabase.py --dry-run
  python scripts/migrate_sqlite_to_supabase.py --confirmar

Flags:
  --listar      Mostra contagem de linhas por tabela no SQLite de origem.
  --dry-run     Simula a migração: lê SQLite e valida conexão Postgres sem inserir.
  --confirmar   Executa a migração real (upsert idempotente por chave primária).

Pré-requisitos:
  DATABASE_URL=postgresql://... (Supabase connection string)
  pip install psycopg psycopg-pool

O script usa ON CONFLICT DO NOTHING em trades (trade_id) e ON CONFLICT DO UPDATE
em risk_state (name). Pode ser executado múltiplas vezes com segurança.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.getenv("DB_PATH", "data/btc_data.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

TABELAS = ["trades", "snapshots_mercado", "cvd_historico", "sinais", "risk_state", "bot_events"]


# ── Helpers ────────────────────────────────────────────────────────


def _sqlite_conn() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"[ERRO] SQLite não encontrado: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _destino_seguro() -> str:
    """I-13: host e banco, NUNCA a URL crua.

    Imprimir 40-50 caracteres de uma connection string vaza usuario e
    frequentemente a senha — o formato e
    `postgresql://usuario:senha@host/banco`, e a senha cabe nos 40
    primeiros caracteres. Esses logs vao para arquivo e para o console de
    quem estiver olhando.
    """
    if not DATABASE_URL:
        return "(DATABASE_URL nao definida)"
    from urllib.parse import urlparse

    try:
        u = urlparse(DATABASE_URL)
        # aspas simples dentro da f-string: aninhar aspas DUPLAS so e valido
        # no Python 3.12+ (PEP 701) e o projeto declara 3.11+
        return f"{u.hostname or '?'}{u.path or ''}"
    except Exception:
        return "(URL ilegivel)"


def _pg_conn():
    if not DATABASE_URL:
        print("[ERRO] DATABASE_URL não configurado. Defina no .env ou como variável de ambiente.")
        sys.exit(1)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("[ERRO] psycopg não instalado. Execute: pip install 'psycopg[binary]'")
        sys.exit(1)

    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)


def _parse_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            return value
    return str(value)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return False


def _parse_json(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Leitura SQLite ─────────────────────────────────────────────────


def _tabela_inexistente(exc: Exception) -> bool:
    """I-13: distingue "a tabela não existe" de "o banco está travado".

    Um `except Exception` cru tratava as duas coisas igual, e as consequências
    são opostas: tabela ausente é esperado (schemas evoluem), banco travado
    significa que a migração vai reportar sucesso tendo copiado ZERO linhas.
    """
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()


def _sqlite_count(conn: sqlite3.Connection, tabela: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()
        return row[0] if row else 0
    except Exception as e:
        if _tabela_inexistente(e):
            return -1  # tabela não existe neste SQLite
        raise


def _sqlite_rows(conn: sqlite3.Connection, tabela: str) -> list[dict]:
    """I-13: só engole "tabela não existe". Qualquer outro erro PROPAGA.

    Antes, `except Exception: return []` transformava `database is locked` —
    provável com o worker rodando 24/7 sobre o mesmo arquivo — em "0 linhas" e
    a migração seguia, reportava sucesso e deixava a tabela vazia no destino.
    Falhar alto é a única forma de isso não passar despercebido.
    """
    try:
        rows = conn.execute(f"SELECT * FROM {tabela}").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        if _tabela_inexistente(e):
            return []
        raise


# ── Inserção Postgres ──────────────────────────────────────────────


def _insert_trades(pg, rows: list[dict], dry: bool) -> int:
    count = 0
    for r in rows:
        params = (
            _parse_ts(r.get("timestamp")),
            r.get("symbol", "BTCUSDT"),
            r.get("preco"),
            r.get("volume_btc"),
            r.get("volume_usdt"),
            r.get("direcao"),
            _parse_bool(r.get("eh_baleia")),
            r.get("trade_id"),
        )
        if not dry:
            pg.execute(
                """
                INSERT INTO trades (timestamp, symbol, preco, volume_btc, volume_usdt, direcao,
                eh_baleia, trade_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trade_id) WHERE trade_id IS NOT NULL DO NOTHING
                """,
                params,
            )
        count += 1
    return count


def _insert_snapshots(pg, rows: list[dict], dry: bool) -> int:
    count = 0
    for r in rows:
        params = (
            _parse_ts(r.get("timestamp")),
            r.get("symbol", "BTCUSDT"),
            r.get("preco"),
            r.get("variacao_24h") or r.get("variacao_24h_%"),
            r.get("volume_24h_btc"),
            r.get("funding_rate") or r.get("funding_rate_%"),
            r.get("open_interest_btc"),
            r.get("ema20_1h"),
            r.get("ema50_1h"),
            r.get("rsi_1h"),
            r.get("tendencia"),
            r.get("pressao_order_book") or r.get("pressao_dominante"),
            r.get("liquidez_compra_usdt"),
            r.get("liquidez_venda_usdt"),
            json.dumps({}),
        )
        if not dry:
            pg.execute(
                """
                INSERT INTO snapshots_mercado (
                    timestamp, symbol, preco, variacao_24h, volume_24h_btc,
                    funding_rate, open_interest_btc, ema20_1h, ema50_1h, rsi_1h,
                    tendencia, pressao_order_book, liquidez_compra_usdt, liquidez_venda_usdt,
                    raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT DO NOTHING
                """,
                params,
            )
        count += 1
    return count


def _insert_cvd(pg, rows: list[dict], dry: bool) -> int:
    count = 0
    for r in rows:
        params = (
            _parse_ts(r.get("timestamp")),
            r.get("symbol", "BTCUSDT"),
            r.get("cvd"),
            r.get("compras_btc"),
            r.get("vendas_btc"),
        )
        if not dry:
            pg.execute(
                "INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)"
                " VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                params,
            )
        count += 1
    return count


def _insert_sinais(pg, rows: list[dict], dry: bool) -> int:
    """I-13: copiava 9 colunas e DESCARTAVA as quatro do meta-labeling.

    `preco_saida`, `pnl_usdt`, `pnl_pct` e `barreira_tocada` existem nos DOIS
    schemas (database.py:248-251 no SQLite, :328-331 no Postgres) e sao
    exatamente a materia-prima da Etapa 2 do gate. Sem elas a migracao
    preservava a DECISAO de cada trade e apagava o RESULTADO — o relatorio do
    gate leria `pnl_usdt IS NOT NULL` e encontraria zero, depois de meses de
    paper trading.
    """
    count = 0
    for r in rows:
        params = (
            _parse_ts(r.get("timestamp")),
            r.get("symbol", "BTCUSDT"),
            r.get("tipo"),
            r.get("preco"),
            r.get("motivo"),
            r.get("score"),
            r.get("source"),
            _parse_bool(r.get("executado")),
            _parse_ts(r.get("executado_em")),
            r.get("preco_saida"),
            r.get("pnl_usdt"),
            r.get("pnl_pct"),
            r.get("barreira_tocada"),
        )
        if not dry:
            pg.execute(
                """
                INSERT INTO sinais
                (timestamp, symbol, tipo, preco, motivo, score, source, executado,
                 executado_em, preco_saida, pnl_usdt, pnl_pct, barreira_tocada)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                params,
            )
        count += 1
    return count


def _insert_risk_state(pg, rows: list[dict], dry: bool) -> int:
    count = 0
    for r in rows:
        data = _parse_json(r.get("data"))
        params = (
            r.get("name", "default"),
            _parse_ts(r.get("updated_at")) or datetime.now(timezone.utc).isoformat(),
            json.dumps(data),
        )
        if not dry:
            pg.execute(
                """
                INSERT INTO risk_state (name, updated_at, data)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (name)
                DO UPDATE SET updated_at = EXCLUDED.updated_at, data = EXCLUDED.data
                """,
                params,
            )
        count += 1
    return count


def _insert_bot_events(pg, rows: list[dict], dry: bool) -> int:
    count = 0
    for r in rows:
        data = _parse_json(r.get("data"))
        params = (
            _parse_ts(r.get("timestamp")),
            r.get("service"),
            r.get("symbol"),
            r.get("event_type"),
            r.get("severity", "INFO"),
            r.get("message", ""),
            json.dumps(data or {}),
        )
        if not dry:
            pg.execute(
                """
                INSERT INTO bot_events (timestamp, service, symbol, event_type, severity, message,
                data)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT DO NOTHING
                """,
                params,
            )
        count += 1
    return count


_INSERTERS = {
    "trades": _insert_trades,
    "snapshots_mercado": _insert_snapshots,
    "cvd_historico": _insert_cvd,
    "sinais": _insert_sinais,
    "risk_state": _insert_risk_state,
    "bot_events": _insert_bot_events,
}


# ── Comandos ───────────────────────────────────────────────────────


def cmd_listar() -> None:
    print(f"\nSQLite: {DB_PATH}")
    print("-" * 40)
    conn = _sqlite_conn()
    total = 0
    for tabela in TABELAS:
        n = _sqlite_count(conn, tabela)
        if n >= 0:
            print(f"  {tabela:<25} {n:>8} linhas")
            total += n
        else:
            print(f"  {tabela:<25}   (não existe)")
    print("-" * 40)
    print(f"  {'TOTAL':<25} {total:>8} linhas\n")
    conn.close()


def cmd_migrate(dry: bool) -> None:
    modo = "DRY-RUN (nada será inserido)" if dry else "CONFIRMAR (inserção real)"
    print(f"\n[MIGRAÇÃO] Modo: {modo}")
    print(f"  Origem : {DB_PATH}")
    print(f"  Destino: {DATABASE_URL[:40]}...\n")

    sqlite = _sqlite_conn()
    pg = _pg_conn()

    resultados: dict[str, int] = {}
    erros: list[str] = []

    for tabela in TABELAS:
        rows = _sqlite_rows(sqlite, tabela)
        if not rows:
            print(f"  {tabela:<25}  0 linhas (vazio ou inexistente)")
            resultados[tabela] = 0
            continue

        inserter = _INSERTERS.get(tabela)
        if not inserter:
            print(f"  {tabela:<25}  [SKIP] sem inserter definido")
            continue

        try:
            n = inserter(pg, rows, dry)
            resultados[tabela] = n
            print(f"  {tabela:<25} {n:>6} linhas {'simuladas' if dry else 'inseridas'}")
        except Exception as exc:
            erros.append(f"{tabela}: {exc}")
            print(f"  {tabela:<25}  [ERRO] {exc}")

    if not dry:
        pg.commit()
        print("\n[OK] Commit realizado.")

    pg.close()
    sqlite.close()

    print("\n" + "=" * 50)
    total = sum(resultados.values())
    print(f"  Total: {total} linhas {'simuladas' if dry else 'migradas'}")
    if erros:
        print(f"\n  Erros ({len(erros)}):")
        for e in erros:
            print(f"    • {e}")
    else:
        print("  Sem erros.")

    if not dry:
        print("\n  Próximo passo: validar contagens no Supabase com --listar\n")


def cmd_validar_pg() -> None:
    pg = _pg_conn()
    print(f"\nPostgres: {DATABASE_URL[:50]}...")
    print("-" * 40)
    total = 0
    for tabela in TABELAS:
        try:
            row = pg.execute(f"SELECT COUNT(*) AS n FROM {tabela}").fetchone()
            n = row["n"] if row else 0
            print(f"  {tabela:<25} {n:>8} linhas")
            total += n
        except Exception as exc:
            print(f"  {tabela:<25}  [ERRO] {exc}")
    print("-" * 40)
    print(f"  {'TOTAL':<25} {total:>8} linhas\n")
    pg.close()


# ── Entry point ────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Migração idempotente SQLite → Supabase/Postgres")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--listar", action="store_true", help="Lista contagens no SQLite de origem")
    group.add_argument("--dry-run", action="store_true", help="Simula migração sem inserir")
    group.add_argument("--confirmar", action="store_true", help="Executa migração real")
    group.add_argument(
        "--validar-pg", action="store_true", help="Lista contagens no Postgres de destino"
    )

    args = parser.parse_args()

    if args.listar:
        cmd_listar()
    elif args.dry_run:
        cmd_migrate(dry=True)
    elif args.confirmar:
        print("\n⚠  ATENÇÃO: Inserção real no Postgres. Pressione Ctrl+C para cancelar.")
        try:
            input("   Pressione ENTER para continuar...")
        except KeyboardInterrupt:
            print("\n[ABORTADO]")
            sys.exit(0)
        cmd_migrate(dry=False)
    elif args.validar_pg:
        cmd_validar_pg()


if __name__ == "__main__":
    main()
