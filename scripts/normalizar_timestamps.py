"""
Normaliza timestamps em formato BR para ISO-8601 (E-8).
=========================================================

## O problema que este script conserta

`estrategias/otimizada.py` gravava `resultado["timestamp"]` como
`'%d/%m/%Y %H:%M:%S'` e `logger.registrar_avaliacao` o persistia literalmente em
`log_avaliacoes.timestamp`. As consultas do relatorio diario filtravam
`timestamp LIKE 'YYYY-MM-DD%'`, que NUNCA casa com `'08/08/2026 09:10:53'`.

Medido no banco de producao em 2026-08-08:

    total em log_avaliacoes ........ 7.625 linhas
    LIKE '2026-08-08%' ............. 0 linhas
    LIKE '08/08/2026%' ............. 111 linhas   <- as de hoje estavam aqui

`logger.dados_relatorio_diario` convertia o None resultante em 0.0, entao o
alerta diario reportava "0 avaliacoes, PnL 0,00" TODOS OS DIAS. Uma mentira na
direcao tranquilizadora, que e o pior tipo num sistema de risco: o operador ve
zero e conclui "nada aconteceu", quando o correto seria "nao consigo medir".

A origem foi corrigida (ISO na escrita) e as consultas passaram a filtrar por
range. Este script conserta o passado — sem ele, todo o historico anterior a
2026-08-08 fica invisivel para qualquer consulta por data.

## Uso

    python scripts/normalizar_timestamps.py                 # dry-run (default)
    python scripts/normalizar_timestamps.py --confirmar      # aplica
    python scripts/normalizar_timestamps.py --confirmar --sem-backup

Idempotente: linhas ja em ISO nao sao tocadas. Rodar duas vezes nao muda nada
na segunda.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.runtime_settings import DATABASE_BACKEND, DB_PATH  # noqa: E402

# (tabela, coluna) que recebem timestamp textual de decisao/trade.
ALVOS = (
    ("log_avaliacoes", "timestamp"),
    ("log_trades", "timestamp_entrada"),
    ("log_trades", "timestamp_saida"),
    ("sinais", "timestamp"),
)

FORMATOS_BR = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")
FORMATO_ISO = "%Y-%m-%d %H:%M:%S"

# Variantes ISO que o repositorio realmente produz — todas ACEITAS como corretas,
# nenhuma convertida. O dry-run deste script foi o que revelou a terceira:
# `sinais.timestamp` grava `datetime.now().isoformat()`, ou seja
# '2026-04-01T05:00:32.920379' (separador 'T' e microssegundos). Sem incluir esse
# formato, as 5.255 linhas de `sinais` apareciam como "irreconheciveis" — um
# falso alarme que teria mandado alguem caçar um problema que nao existe.
#
# As tres variantes conviverem nao quebra as consultas por RANGE, e isso vale ser
# explicito: comparadas como texto, ' ' (0x20) < 'T' (0x54) e o prefixo de data
# tem largura fixa, entao '2026-08-08T09:10:53.1' fica corretamente entre
# '2026-08-08 00:00:00' e '2026-08-09 00:00:00'. Era o LIKE que nao tolerava
# variacao, nao o range.
FORMATOS_ISO = (
    FORMATO_ISO,
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
)


def para_iso(valor: str) -> str | None:
    """Converte um timestamp BR para ISO. None se ja for ISO ou irreconhecivel.

    A deteccao e por PARSE, nao por regex de aparencia: '08/08/2026' e
    '2026-08-08' sao ambos reconheciveis, e o unico jeito seguro de decidir se
    algo precisa conversao e tentar interpretar. Devolve None (nao toca) tanto
    para o que ja esta certo quanto para o que nao se entende — nunca inventa.
    """
    if not valor or not isinstance(valor, str):
        return None
    texto = valor.strip()
    # Ja ISO? Nao mexer.
    for f in FORMATOS_ISO:
        try:
            datetime.strptime(texto, f)
            return None
        except ValueError:
            pass
    for f in FORMATOS_BR:
        try:
            return datetime.strptime(texto, f).strftime(FORMATO_ISO)
        except ValueError:
            continue
    return None


def _colunas(conn, tabela: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")}  # nosec B608
    except sqlite3.Error:
        return set()


def normalizar(caminho: str, confirmar: bool) -> int:
    conn = sqlite3.connect(caminho)
    total_convertidos = 0
    total_ignorados = 0
    try:
        for tabela, coluna in ALVOS:
            if coluna not in _colunas(conn, tabela):
                print(f"  {tabela}.{coluna}: tabela/coluna ausente — pulando")
                continue

            # rowid e estavel em SQLite e existe mesmo sem PK declarada.
            linhas = conn.execute(
                f"SELECT rowid, {coluna} FROM {tabela} WHERE {coluna} IS NOT NULL"  # nosec B608
            ).fetchall()
            pares = []
            nao_reconhecidos = 0
            for rowid, valor in linhas:
                iso = para_iso(valor)
                if iso is not None:
                    pares.append((iso, rowid))
                elif valor and not _parece_iso(valor):
                    nao_reconhecidos += 1

            print(
                f"  {tabela}.{coluna}: {len(linhas)} linhas, "
                f"{len(pares)} para converter, {nao_reconhecidos} irreconheciveis"
            )
            if nao_reconhecidos:
                # Nunca silenciar: um formato desconhecido continua invisivel para
                # as consultas por data, e o operador tem de saber disso.
                amostra = [
                    v for _, v in linhas[:200] if v and para_iso(v) is None and not _parece_iso(v)
                ][:3]
                print(f"    AVISO: formatos nao reconhecidos permanecem como estao. Ex: {amostra}")

            if pares and confirmar:
                conn.executemany(
                    f"UPDATE {tabela} SET {coluna}=? WHERE rowid=?",  # nosec B608
                    pares,
                )
            total_convertidos += len(pares)
            total_ignorados += nao_reconhecidos

        if confirmar:
            conn.commit()
    finally:
        conn.close()

    print()
    print(f"  convertidos: {total_convertidos} | irreconheciveis: {total_ignorados}")
    return total_convertidos


def _parece_iso(valor: str) -> bool:
    for f in FORMATOS_ISO:
        try:
            datetime.strptime(valor.strip(), f)
            return True
        except ValueError:
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Normaliza timestamps BR -> ISO (E-8)")
    ap.add_argument("--confirmar", action="store_true", help="aplica de fato (default: dry-run)")
    ap.add_argument("--sem-backup", action="store_true", help="nao copia o .db antes de alterar")
    ap.add_argument("--db", default=None, help=f"caminho do SQLite (default: {DB_PATH})")
    args = ap.parse_args()

    if DATABASE_BACKEND != "sqlite" and args.db is None:
        print(f"ERRO: backend e '{DATABASE_BACKEND}'. Este script so trata SQLite.")
        print("      Para Postgres/Supabase, rode o UPDATE equivalente no SQL Editor:")
        print("        UPDATE log_avaliacoes SET timestamp =")
        print("          to_char(to_timestamp(timestamp,'DD/MM/YYYY HH24:MI:SS'),")
        print("                  'YYYY-MM-DD HH24:MI:SS')")
        print("        WHERE timestamp ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}';")
        return 2

    caminho = args.db or DB_PATH
    if not os.path.exists(caminho):
        print(f"ERRO: banco nao encontrado: {caminho}")
        return 2

    print(f"Banco: {caminho}")
    print(f"Modo:  {'APLICAR' if args.confirmar else 'DRY-RUN (nada sera alterado)'}")
    print()

    if args.confirmar and not args.sem_backup:
        bkp = f"{caminho}.bkp-ts-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(caminho, bkp)
        print(f"  backup: {bkp}")
        print()

    normalizar(caminho, args.confirmar)

    if not args.confirmar:
        print()
        print("  Nada foi alterado. Para aplicar:")
        print("    python scripts/normalizar_timestamps.py --confirmar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
