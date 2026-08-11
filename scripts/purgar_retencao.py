"""
Retenção de dados — arquiva e purga as três tabelas de alto volume (I-13).
==========================================================================
`data/btc_data.db` tem 375 MB. Três tabelas respondem por quase tudo isso e
**nenhuma delas tem SELECT no caminho de produção**:

    trades              2.938.174 linhas   tape bruto da Binance (@aggTrade)
    snapshots_mercado       9.832 linhas   contexto de cada decisão
    cvd_historico           4.622 linhas   CVD periódico — escrito, nunca lido

Política decidida pelo operador em 2026-08-11: **90 dias, arquivando antes de
apagar**. `trades` e `snapshots_mercado` são matéria-prima da pesquisa de
microestrutura (frente E-11) — apagar sem arquivo destruiria dado
irrecuperável. `cvd_historico` sai inteira: não tem leitor nenhum, e o
componente CVD do score foi provado matematicamente inerte.

Nota sobre `cvd_historico`: a ESCRITA continua viva (`main.py:1361`, por ciclo,
e `main.py:1733`, no shutdown). A tabela volta a crescer ~35 linhas/dia depois
da purga. Desligar o escritor é outra decisão, fora do escopo deste script.

O QUE TORNA ISTO REVERSÍVEL (protocolo @Zeta)
---------------------------------------------
Nada é apagado antes de existir um arquivo verificado. A ordem é rígida:

    1. dump em `_legado/dumps/<tabela>-ate-<carimbo>.jsonl.gz`
    2. RELÊ o .gz do disco, conta as linhas e calcula o sha256
    3. só se a releitura bater com o que foi escrito é que o DELETE roda

Um dump truncado (disco cheio, processo morto no meio) falha no passo 2 e o
DELETE nunca acontece. O manifesto `.manifest.json` ao lado guarda o sha256, e
`--restaurar` recusa um arquivo cujo hash não confira.

USO — a escada é `--listar` → `--dry-run` → `--confirmar`:

    python scripts/purgar_retencao.py                    # inventário, só lê
    python scripts/purgar_retencao.py --dry-run          # o que faria
    python scripts/purgar_retencao.py --confirmar        # arquiva e apaga
    python scripts/purgar_retencao.py --restaurar _legado/dumps/trades-....jsonl.gz

Postgres/Supabase é banco COMPARTILHADO: qualquer escrita ali exige
`--producao-postgres` além de `--confirmar`.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# None = purga tudo (não há nada a reter). Ver o cabeçalho para o porquê de
# cada política.
RETENCAO_DIAS: dict[str, int | None] = {
    "trades": 90,
    "snapshots_mercado": 90,
    "cvd_historico": None,
}

DIR_DUMPS_PADRAO = os.path.join(RAIZ, "_legado", "dumps")

# DELETE em lotes: 1,8 milhão de linhas numa transação só seguraria o lock de
# escrita do SQLite por minutos, e o worker escreve 24/7. Em lotes ele pega as
# janelas entre as transações.
LOTE = 20_000

# Se a linha mais recente de `trades` for mais nova que isto, tem worker
# escrevendo agora. Só importa para o VACUUM, que precisa de lock exclusivo.
JANELA_WORKER_MIN = 10


# ── conexão ────────────────────────────────────────────────────


def _backend() -> str:
    from config.runtime_settings import DATABASE_BACKEND

    alias = {"postgres", "postgresql", "supabase"}
    return "postgres" if (DATABASE_BACKEND or "sqlite").lower() in alias else "sqlite"


def _destino_seguro() -> str:
    """Host/banco do Postgres, sem usuário nem senha — o mesmo cuidado que o
    migrador aplica: nenhum print deste script pode ecoar a DSN crua."""
    import database
    from config.runtime_settings import DATABASE_URL

    return database._mascarar_dsn(DATABASE_URL or "")


def _conectar(backend: str):
    if backend == "postgres":
        import psycopg
        from psycopg.rows import dict_row

        import database

        return psycopg.connect(database._pg_dsn(), row_factory=dict_row, connect_timeout=10)

    import sqlite3

    from config.runtime_settings import DB_PATH

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _ph(backend: str) -> str:
    return "%s" if backend == "postgres" else "?"


def _corte(dias: int | None, backend: str):
    """O tipo do corte tem que casar com o da coluna, ou a comparação vira
    ruído: SQLite guarda `datetime.now().isoformat()` (ingênuo, hora local) e
    Postgres guarda TIMESTAMPTZ em UTC. Comparar string ISO funciona porque
    ISO-8601 ordena lexicograficamente — mas só se o fuso for o mesmo."""
    if backend == "postgres":
        agora = datetime.now(timezone.utc)
        return agora if dias is None else agora - timedelta(days=dias)
    agora = datetime.now()
    return (agora if dias is None else agora - timedelta(days=dias)).isoformat()


def _rotulo_corte(corte) -> str:
    return corte if isinstance(corte, str) else corte.isoformat()


# ── leitura ────────────────────────────────────────────────────


def _valor(row, idx: int = 0):
    """Primeira coluna, nos dois backends (dict_row no PG, sqlite3.Row aqui)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return list(row.values())[idx]
    return row[idx]


def _contar(conn, tabela: str, corte, backend: str) -> tuple[int, int]:
    """(total, a purgar)."""
    ph = _ph(backend)
    q_total = f"SELECT COUNT(*) FROM {tabela}"  # nosec B608
    q_antigos = f"SELECT COUNT(*) FROM {tabela} WHERE timestamp < {ph}"  # nosec B608
    total = _valor(conn.execute(q_total).fetchone())
    antigos = _valor(conn.execute(q_antigos, (corte,)).fetchone())
    return int(total or 0), int(antigos or 0)


def _faixa(conn, tabela: str) -> tuple[Any, Any]:
    q = f"SELECT MIN(timestamp), MAX(timestamp) FROM {tabela}"  # nosec B608
    row = conn.execute(q).fetchone()
    if row is None:
        return None, None
    if isinstance(row, dict):
        vals = list(row.values())
        return vals[0], vals[1]
    return row[0], row[1]


def _colunas(conn, tabela: str, backend: str) -> set[str]:
    """Nomes de coluna que a tabela REALMENTE tem, perguntando ao banco."""
    if backend == "postgres":
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (tabela,),
        )
        return {_valor(r) for r in cur}
    q = f"PRAGMA table_info({tabela})"  # nosec B608
    return {r[1] for r in conn.execute(q)}


def _worker_ativo(conn, backend: str) -> bool:
    """Tem alguém escrevendo agora? Olha o dado, não o serviço — assim vale
    tanto para o NSSM no Windows quanto para o worker no Railway."""
    try:
        _, maximo = _faixa(conn, "trades")
        if maximo is None:
            return False
        ultimo = maximo if isinstance(maximo, datetime) else datetime.fromisoformat(str(maximo))
        agora = datetime.now(ultimo.tzinfo) if ultimo.tzinfo else datetime.now()
        return (agora - ultimo) < timedelta(minutes=JANELA_WORKER_MIN)
    except Exception:
        return True  # na dúvida, assume ativo: o custo é só pedir uma flag


# ── arquivamento ───────────────────────────────────────────────


def _json_default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    return str(o)


def _linhas(conn, tabela: str, corte, backend: str) -> Iterable[dict]:
    """Itera as linhas a arquivar SEM carregar tudo em memória. No Postgres
    isso exige cursor nomeado (server-side); o cursor padrão do psycopg puxa o
    resultado inteiro, e 1,8 milhão de linhas não cabe."""
    ph = _ph(backend)
    sql = f"SELECT * FROM {tabela} WHERE timestamp < {ph} ORDER BY id"  # nosec B608
    if backend == "postgres":
        with conn.cursor(name=f"purga_{tabela}") as cur:
            cur.itersize = 10_000
            cur.execute(sql, (corte,))
            yield from cur
        return
    cur = conn.execute(sql, (corte,))
    for row in cur:
        yield dict(row)


def _sha256_e_linhas(caminho: str) -> tuple[str, int]:
    """Relê o .gz do disco: hash do arquivo comprimido e contagem de linhas
    descomprimidas. É este passo que autoriza o DELETE."""
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    n = 0
    with gzip.open(caminho, "rt", encoding="utf-8") as gz:
        for _ in gz:
            n += 1
    return h.hexdigest(), n


def _arquivar(conn, tabela: str, corte, backend: str, dir_dumps: str, carimbo: str) -> dict:
    """Grava o dump, relê e verifica. Levanta se a releitura não bater."""
    os.makedirs(dir_dumps, exist_ok=True)
    caminho = os.path.join(dir_dumps, f"{tabela}-ate-{carimbo}.jsonl.gz")

    escritas = 0
    with gzip.open(caminho, "wt", encoding="utf-8", newline="\n") as gz:
        for row in _linhas(conn, tabela, corte, backend):
            gz.write(json.dumps(dict(row), ensure_ascii=False, default=_json_default))
            gz.write("\n")
            escritas += 1

    sha, relidas = _sha256_e_linhas(caminho)
    if relidas != escritas:
        raise RuntimeError(
            f"dump de {tabela} inconsistente: {escritas} escritas, {relidas} relidas "
            f"({caminho}) — DELETE abortado"
        )

    manifesto = {
        "tabela": tabela,
        "backend": backend,
        "corte": _rotulo_corte(corte),
        "linhas": escritas,
        "sha256": sha,
        "bytes": os.path.getsize(caminho),
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "arquivo": os.path.basename(caminho),
    }
    with open(caminho + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifesto, fh, ensure_ascii=False, indent=2)
    return manifesto


# ── remoção ────────────────────────────────────────────────────


def _deletar(conn, tabela: str, corte, backend: str) -> int:
    ph = _ph(backend)
    lote = f"SELECT id FROM {tabela} WHERE timestamp < {ph} ORDER BY id LIMIT {ph}"  # nosec B608
    sql = f"DELETE FROM {tabela} WHERE id IN ({lote})"  # nosec B608
    removidas = 0
    while True:
        cur = conn.execute(sql, (corte, LOTE))
        n = cur.rowcount or 0
        conn.commit()
        removidas += n
        if n < LOTE:
            return removidas
        print(f"      ... {removidas:>9} removidas", flush=True)


# ── comandos ───────────────────────────────────────────────────


def _inventario(conn, backend: str, dias_override: int | None) -> list[dict]:
    linhas = []
    for tabela, dias_padrao in RETENCAO_DIAS.items():
        dias = dias_padrao if dias_override is None else dias_override
        corte = _corte(dias, backend)
        total, antigos = _contar(conn, tabela, corte, backend)
        minimo, maximo = _faixa(conn, tabela)
        linhas.append(
            {
                "tabela": tabela,
                "dias": dias,
                "corte": corte,
                "total": total,
                "purgar": antigos,
                "manter": total - antigos,
                "min": minimo,
                "max": maximo,
            }
        )
    return linhas


def _imprimir_inventario(inv: list[dict]) -> None:
    print(f"  {'tabela':<20} {'politica':>10} {'total':>10} {'purgar':>10} {'manter':>10}")
    print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
    for r in inv:
        pol = "tudo" if r["dias"] is None else f"{r['dias']}d"
        print(
            f"  {r['tabela']:<20} {pol:>10} {r['total']:>10,} "
            f"{r['purgar']:>10,} {r['manter']:>10,}"
        )
        if r["total"]:
            print(f"  {'':<20} faixa: {str(r['min'])[:19]} .. {str(r['max'])[:19]}")
    print(f"\n  Total a purgar: {sum(r['purgar'] for r in inv):,} linhas")


def cmd_purgar(args) -> int:
    backend = _backend()
    if backend == "postgres":
        print(f"  Backend: postgres — {_destino_seguro()}")
        if args.confirmar and not args.producao_postgres:
            print(
                "\n  [RECUSADO] Postgres/Supabase e banco COMPARTILHADO. Para escrever ali,"
                "\n  passe --producao-postgres junto de --confirmar.\n"
            )
            return 2
    else:
        from config.runtime_settings import DB_PATH

        if not os.path.exists(DB_PATH):
            print(f"  [ERRO] banco nao encontrado: {DB_PATH}")
            return 2
        tam = os.path.getsize(DB_PATH) / 1e6
        print(f"  Backend: sqlite — {DB_PATH} ({tam:,.1f} MB)")

    if args.confirmar:
        modo = "APLICANDO (arquiva e apaga)"
    elif args.dry_run:
        modo = "DRY-RUN (nada e escrito nem apagado)"
    else:
        modo = "LISTAR (somente leitura)"
    print(f"  Modo:    {modo}\n")

    conn = _conectar(backend)
    try:
        inv = _inventario(conn, backend, args.dias)
        _imprimir_inventario(inv)

        if not args.confirmar:
            if not args.dry_run:
                print("\n  Proximo passo: --dry-run e depois --confirmar.\n")
            else:
                print(f"\n  Arquivaria em: {args.saida}")
                print("  DRY-RUN: nada foi escrito nem apagado. Use --confirmar.\n")
            return 0

        if not sum(r["purgar"] for r in inv):
            print("\n  Nada a purgar.\n")
            return 0

        carimbo = datetime.now().strftime("%Y%m%dT%H%M%S")
        print(f"\n  Arquivando em {args.saida} (carimbo {carimbo})\n")

        total_removidas = 0
        for r in inv:
            if not r["purgar"]:
                print(f"    {r['tabela']:<20} nada a purgar")
                continue
            print(f"    {r['tabela']:<20} arquivando {r['purgar']:,} linhas...", flush=True)
            man = _arquivar(conn, r["tabela"], r["corte"], backend, args.saida, carimbo)
            print(
                f"    {'':<20} -> {man['arquivo']} "
                f"({man['linhas']:,} linhas, {man['bytes'] / 1e6:,.1f} MB)"
            )
            print(f"    {'':<20} sha256 {man['sha256'][:16]}... verificado")

            removidas = _deletar(conn, r["tabela"], r["corte"], backend)
            total_removidas += removidas
            if removidas != man["linhas"]:
                print(
                    f"    {'':<20} [AVISO] arquivadas {man['linhas']:,} mas removidas "
                    f"{removidas:,} — alguem inseriu linha antiga durante a purga"
                )
            else:
                print(f"    {'':<20} removidas {removidas:,} linhas")

        print(f"\n  Total removido: {total_removidas:,} linhas")
        print("\n  Depois:")
        _imprimir_inventario(_inventario(conn, backend, args.dias))

        if args.vacuum:
            if backend == "postgres":
                print("\n  [IGNORADO] --vacuum e so para SQLite; o Postgres tem autovacuum.")
            elif _worker_ativo(conn, backend) and not args.forcar_vacuum:
                print(
                    "\n  [PULADO] VACUUM precisa de lock EXCLUSIVO e ha escrita nos ultimos"
                    f"\n  {JANELA_WORKER_MIN} min. Pare o worker e rode de novo, ou"
                    " --forcar-vacuum.\n"
                )
            else:
                from config.runtime_settings import DB_PATH

                antes = os.path.getsize(DB_PATH) / 1e6
                print(f"\n  VACUUM em {antes:,.1f} MB (pode demorar)...", flush=True)
                # VACUUM nao roda dentro de transacao; o sqlite3 do Python abre
                # uma implicitamente para DML. isolation_level=None desliga isso.
                conn.isolation_level = None
                conn.execute("VACUUM")
                depois = os.path.getsize(DB_PATH) / 1e6
                print(f"  {antes:,.1f} MB -> {depois:,.1f} MB (-{antes - depois:,.1f} MB)")
        elif backend == "sqlite":
            print("\n  Espaco em disco so e devolvido com --vacuum (worker parado).")
        print()
        return 0
    finally:
        conn.close()


def cmd_restaurar(args) -> int:
    caminho = args.restaurar
    if not os.path.exists(caminho):
        print(f"  [ERRO] arquivo nao encontrado: {caminho}")
        return 2

    man_path = caminho + ".manifest.json"
    if not os.path.exists(man_path):
        print(f"  [ERRO] manifesto ausente: {man_path}")
        return 2
    with open(man_path, encoding="utf-8") as fh:
        man = json.load(fh)

    tabela = man["tabela"]
    if tabela not in RETENCAO_DIAS:
        print(f"  [ERRO] tabela desconhecida no manifesto: {tabela}")
        return 2

    sha, linhas = _sha256_e_linhas(caminho)
    if sha != man["sha256"]:
        print(f"  [ERRO] sha256 nao confere — arquivo corrompido ou trocado.\n    {caminho}")
        return 2
    print(f"  Arquivo: {caminho}")
    print(f"  Tabela : {tabela}   linhas: {linhas:,}   sha256 OK")

    backend = _backend()
    if backend == "postgres" and not args.producao_postgres:
        print("\n  [RECUSADO] restaurar no Postgres exige --producao-postgres.\n")
        return 2
    if not args.confirmar:
        print("\n  DRY-RUN: nada inserido. Use --confirmar.\n")
        return 0

    conn = _conectar(backend)
    try:
        inseridas = _reinserir(conn, tabela, caminho, backend)
        conn.commit()
        print(f"\n  Reinseridas: {inseridas:,} linhas em {tabela}")
        print("  (linhas ja presentes foram ignoradas — a restauracao e idempotente)\n")
        return 0
    finally:
        conn.close()


def _reinserir(conn, tabela: str, caminho: str, backend: str) -> int:
    """Reinsere preservando o `id`. Conflito de chave e ignorado: rodar
    --restaurar duas vezes tem que dar o mesmo resultado que rodar uma."""
    import database

    ph = _ph(backend)
    # As chaves do JSON viram nomes de coluna no INSERT. Num dump gerado por
    # este script isso e inofensivo — mas o arquivo vem do disco, e nada impede
    # um .jsonl.gz forjado com uma chave que fecha o parenteses e emenda outro
    # comando. Conferir contra o schema real fecha isso e, de quebra, da erro
    # legivel quando o dump e antigo e a tabela ganhou/perdeu coluna.
    permitidas = _colunas(conn, tabela, backend)
    inseridas = 0
    with gzip.open(caminho, "rt", encoding="utf-8") as gz:
        for linha in gz:
            linha = linha.strip()
            if not linha:
                continue
            row = json.loads(linha)
            cols = list(row.keys())
            desconhecidas = [c for c in cols if c not in permitidas]
            if desconhecidas:
                raise RuntimeError(
                    f"dump traz colunas que nao existem em {tabela}: {desconhecidas} — "
                    f"schema mudou, ou o arquivo nao e confiavel"
                )
            vals = [
                (
                    database._pg_json(row[c])
                    if backend == "postgres" and isinstance(row[c], (dict, list))
                    else row[c]
                )
                for c in cols
            ]
            marcadores = ", ".join([ph] * len(cols))
            campos = ", ".join(cols)
            if backend == "postgres":
                verbo, ignorar = "INSERT INTO", "ON CONFLICT DO NOTHING"
            else:
                verbo, ignorar = "INSERT OR IGNORE INTO", ""
            sql = f"{verbo} {tabela} ({campos}) VALUES ({marcadores}) {ignorar}"  # nosec B608
            cur = conn.execute(sql, vals)
            inseridas += cur.rowcount if (cur.rowcount or 0) > 0 else 0
    return inseridas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--listar", action="store_true", help="inventario (padrao, somente leitura)")
    ap.add_argument("--dry-run", action="store_true", help="mostra o que faria, sem escrever")
    ap.add_argument("--confirmar", action="store_true", help="arquiva e apaga de fato")
    ap.add_argument("--restaurar", metavar="ARQ", help="reinsere um dump .jsonl.gz")
    ap.add_argument(
        "--dias", type=int, default=None, help="sobrepoe a retencao de TODAS as tabelas"
    )
    ap.add_argument("--saida", default=DIR_DUMPS_PADRAO, help="diretorio dos dumps")
    ap.add_argument("--vacuum", action="store_true", help="recompacta o SQLite apos a purga")
    ap.add_argument(
        "--forcar-vacuum", action="store_true", help="VACUUM mesmo com worker escrevendo"
    )
    ap.add_argument(
        "--producao-postgres",
        action="store_true",
        help="autoriza escrita no Postgres/Supabase compartilhado",
    )
    args = ap.parse_args(argv)

    if args.dias is not None and args.dias < 0:
        print("  [ERRO] --dias nao pode ser negativo.")
        return 2

    print()
    if args.restaurar:
        return cmd_restaurar(args)
    return cmd_purgar(args)


if __name__ == "__main__":
    raise SystemExit(main())
