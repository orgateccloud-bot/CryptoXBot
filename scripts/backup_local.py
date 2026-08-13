"""
Backup local — a mitigação do risco de máquina única (decisão local-only)
==========================================================================
Decisão do operador em 2026-08-13: o bot roda LOCAL (NSSM), sem Supabase.
Com isso, o único ponto de falha de dados vira este PC — e o que este script
protege é exatamente o que não se recompra:

    data/btc_data.db     estado vivo: sinais com PnL (Etapa 2), risk_state
    data/book_btc.db     série do livro (E-11) — cada minuto perdido é
                         pesquisa perdida; a janela fecha em 2026-11-30
    _legado/dumps/       arquivos da retenção (tape histórico, sha256)

O que fica DE FORA, de propósito:
    .env                 credenciais não se espalham por cópia
    data/*.pkl           modelos são retreináveis a partir do que está acima
    klines               recoletáveis pelo coletor determinístico (I-11d)

COMO COPIA: API de backup do SQLite (`Connection.backup`), que produz um
snapshot consistente MESMO com o worker escrevendo — copiar o arquivo cru de
um banco WAL vivo pode pegar página rasgada. Depois do backup, cada cópia
passa por `PRAGMA quick_check` e só então é comprimida; um backup que não
verifica não é backup, é esperança.

ROTAÇÃO: mantém os N mais recentes (padrão 7). Só apaga diretórios cujo nome
casa exatamente com `backup-AAAAMMDDTHHMMSS` DENTRO do destino — nunca toca
em nada que não tenha sido criado por ele.

USO:
    python scripts/backup_local.py                       # padrao: D:\\backups\\cryptoxbot
    python scripts/backup_local.py --destino E:\\bkp     # ideal: OUTRO disco fisico
    python scripts/backup_local.py --listar
    python scripts/backup_local.py --manter 14

O destino ideal é outro disco físico ou uma pasta sincronizada (OneDrive/
Drive) — backup no mesmo disco protege contra deleção acidental, não contra
morte do disco.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO_PADRAO = r"D:\backups\cryptoxbot"
BANCOS = ["data/btc_data.db", "data/book_btc.db"]
DIR_DUMPS = os.path.join(RAIZ, "_legado", "dumps")
PADRAO_BACKUP = re.compile(r"^backup-\d{8}T\d{6}$")
MANTER_PADRAO = 7


def _backup_sqlite(origem: str, destino: str) -> None:
    """Snapshot consistente via API de backup — seguro com escritor vivo."""
    src = sqlite3.connect(origem, timeout=60)
    src.execute("PRAGMA busy_timeout=60000")
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _quick_check(caminho: str) -> bool:
    conn = sqlite3.connect(caminho)
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def _gzip_e_remover(caminho: str) -> str:
    saida = caminho + ".gz"
    with open(caminho, "rb") as fh, gzip.open(saida, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(fh, gz, length=1 << 20)
    os.remove(caminho)
    return saida


def executar_backup(destino: str, manter: int) -> dict:
    carimbo = datetime.now().strftime("%Y%m%dT%H%M%S")
    pasta = os.path.join(destino, f"backup-{carimbo}")
    os.makedirs(pasta, exist_ok=True)
    resultado = {"pasta": pasta, "itens": [], "erros": []}

    for rel in BANCOS:
        origem = os.path.join(RAIZ, rel)
        if not os.path.exists(origem):
            resultado["erros"].append(f"{rel}: nao existe")
            continue
        alvo = os.path.join(pasta, os.path.basename(origem))
        _backup_sqlite(origem, alvo)
        if not _quick_check(alvo):
            # copia corrompida NAO vira backup: remove e grita
            os.remove(alvo)
            resultado["erros"].append(f"{rel}: quick_check FALHOU na copia")
            continue
        final = _gzip_e_remover(alvo)
        resultado["itens"].append(
            {"item": rel, "arquivo": final, "mb": os.path.getsize(final) / 1e6}
        )

    if os.path.isdir(DIR_DUMPS):
        alvo_dumps = os.path.join(pasta, "dumps")
        shutil.copytree(DIR_DUMPS, alvo_dumps, dirs_exist_ok=True)
        total = sum(
            os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(alvo_dumps) for f in fs
        )
        resultado["itens"].append(
            {"item": "_legado/dumps", "arquivo": alvo_dumps, "mb": total / 1e6}
        )

    resultado["removidos"] = _rotacionar(destino, manter)
    return resultado


def _rotacionar(destino: str, manter: int) -> list[str]:
    """Apaga só os mais antigos, e só o que TEM o nosso nome."""
    if manter < 1:
        return []
    pastas = sorted(
        d
        for d in os.listdir(destino)
        if PADRAO_BACKUP.match(d) and os.path.isdir(os.path.join(destino, d))
    )
    removidos = []
    for d in pastas[:-manter]:
        shutil.rmtree(os.path.join(destino, d))
        removidos.append(d)
    return removidos


def cmd_listar(destino: str) -> int:
    if not os.path.isdir(destino):
        print(f"\n  Sem backups ainda: {destino} nao existe.\n")
        return 1
    pastas = sorted(d for d in os.listdir(destino) if PADRAO_BACKUP.match(d))
    print(f"\n  {destino}  ({len(pastas)} backups)\n")
    for d in pastas:
        caminho = os.path.join(destino, d)
        total = sum(
            os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(caminho) for f in fs
        )
        print(f"  {d}   {total / 1e6:>8.1f} MB")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--destino", default=DESTINO_PADRAO)
    ap.add_argument("--manter", type=int, default=MANTER_PADRAO, help="quantos backups reter")
    ap.add_argument("--listar", action="store_true")
    args = ap.parse_args(argv)

    if args.listar:
        return cmd_listar(args.destino)

    print(f"\n  Backup -> {args.destino}  (retencao: {args.manter})")
    r = executar_backup(args.destino, args.manter)
    for item in r["itens"]:
        print(f"    ok  {item['item']:<20} {item['mb']:>8.1f} MB")
    for e in r["erros"]:
        print(f"    ERRO  {e}")
    if r["removidos"]:
        print(f"    rotacao: removidos {len(r['removidos'])} antigos")
    print(f"  Pasta: {r['pasta']}\n")
    return 1 if r["erros"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
