"""
Snapshot imutavel do substrato de pesquisa (I-11).
===================================================

## Por que isto existe

Os cinco FAILs pre-registrados sao hoje o ativo mais valioso do repositorio: sao
a unica coisa que impede o capital de entrar. E, ate agora, eram PROSA — nenhum
deles podia ser re-derivado, porque os labs liam a tabela `klines` VIVA, que
muda.

Nao e hipotese. Medido em 2026-08-08:

    BTCUSDT/1h ..... 17.563 velas, primeira em 1711994400000
    ETHUSDT/1h ..... 17.520 velas, primeira em 1712149200000
    SOLUSDT/1h ..... 17.520 velas, primeira em 1712149200000

As 43 velas extras do BTC estao no INICIO da serie (154.800.000 ms = 43 horas
antes), ou seja a tabela cresceu para TRAS — mudanca nao-append. E o hold-out de
edge_lab.py e `int(N * (1 - 0.35))`: com N=17.520 a fronteira cai no indice
11.388; com N=17.563, no 11.415. A fronteira andou 27 velas sozinha, sem ninguem
decidir nada.

Um FAIL que ninguem consegue reproduzir cede na primeira duvida — e a duvida
sempre chega quando alguem esta com pressa de ligar o capital.

## O que este modulo NAO consegue fazer

Ele NAO reconstroi os vereditos originais bit-a-bit. O substrato ja se moveu
antes de existir um snapshot; a serie exata sobre a qual os FAILs de julho foram
medidos nao existe mais em lugar nenhum. O que ele faz e congelar o substrato de
HOJE e tornar todo veredito futuro re-derivavel. Re-rodar a pesquisa sobre este
snapshot produz numeros NOVOS — que passam a ser permanentemente reproduziveis.

Registrar essa limitacao e parte do trabalho: um snapshot que se apresenta como
"os dados originais" seria pior que nenhum.

## Uso

    python -m research.snapshot --criar            # congela o estado atual
    python -m research.snapshot --verificar        # sha256 confere?
    python -m research.snapshot --listar

    from research.snapshot import carregar_serie
    ts, m, n, f, v = carregar_serie("BTCUSDT", "1h")   # le o snapshot, nao o DB
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Caminho derivado de __file__, nao do cwd: rodar de outro diretorio nao pode
# criar silenciosamente um snapshot vazio (o mesmo defeito que coletar_dados.py
# tinha com DB_PATH relativo).
DIR_SNAPSHOTS = os.path.join(RAIZ, "data", "snapshots")
DB_PATH = os.path.join(RAIZ, "data", "btc_data.db")

# Series que a pesquisa de fato usa. edge_lab le 1h para os tres pares
# (carregar_klines + ic_por_asset); os demais intervalos existem na tabela mas
# nenhum lab os consome, e snapshotar o que nao se usa so aumenta o repositorio.
SERIES = (
    ("BTCUSDT", "1h"),
    ("ETHUSDT", "1h"),
    ("SOLUSDT", "1h"),
)

COLUNAS = ("timestamp", "abertura", "maxima", "minima", "fechamento", "volume")
NOME_MANIFEST = "manifest.json"


def _sha256(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as fp:
        for bloco in iter(lambda: fp.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _ler_do_banco(conn, symbol: str, intervalo: str):
    return conn.execute(
        "SELECT timestamp, abertura, maxima, minima, fechamento, volume FROM klines "
        "WHERE symbol=? AND intervalo=? ORDER BY timestamp ASC",
        (symbol, intervalo),
    ).fetchall()


def criar(rotulo: str | None = None, db_path: str | None = None) -> str:
    """Congela as series em data/snapshots/<rotulo>/ e escreve o manifest.

    Devolve o diretorio criado. Recusa sobrescrever um snapshot existente: um
    snapshot mutavel nao e um snapshot.
    """
    rotulo = rotulo or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    destino = os.path.join(DIR_SNAPSHOTS, rotulo)
    if os.path.exists(destino):
        raise FileExistsError(
            f"Snapshot '{rotulo}' ja existe em {destino}. Snapshots sao IMUTAVEIS — "
            f"para um novo substrato, use outro rotulo (--rotulo)."
        )
    os.makedirs(destino)

    conn = sqlite3.connect(db_path or DB_PATH)
    entradas = []
    try:
        for symbol, intervalo in SERIES:
            linhas = _ler_do_banco(conn, symbol, intervalo)
            if not linhas:
                raise ValueError(f"Serie vazia no banco: {symbol}/{intervalo}")
            nome = f"{symbol}_{intervalo}.csv"
            caminho = os.path.join(destino, nome)
            # newline="" + LF explicito: o sha256 tem de bater em Windows e
            # Linux. Sem isso o Python traduz \n para \r\n no Windows e o hash
            # do mesmo dado difere entre maquinas — o que quebraria exatamente o
            # criterio de saida ("re-deriva em duas maquinas diferentes").
            with open(caminho, "w", newline="", encoding="utf-8") as fp:
                w = csv.writer(fp, lineterminator="\n")
                w.writerow(COLUNAS)
                w.writerows(linhas)
            entradas.append(
                {
                    "symbol": symbol,
                    "intervalo": intervalo,
                    "arquivo": nome,
                    "n": len(linhas),
                    "primeiro_ts": int(linhas[0][0]),
                    "ultimo_ts": int(linhas[-1][0]),
                    "primeiro_iso": _iso(int(linhas[0][0])),
                    "ultimo_iso": _iso(int(linhas[-1][0])),
                    "sha256": _sha256(caminho),
                }
            )
    finally:
        conn.close()

    manifest = {
        "rotulo": rotulo,
        "criado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origem": "data/btc_data.db (tabela klines)",
        "aviso": (
            "Este snapshot congela o substrato de pesquisa a partir desta data. Ele NAO "
            "reconstroi os vereditos anteriores: a serie sobre a qual os FAILs de julho "
            "foram medidos nao existe mais (a tabela klines mudou de forma nao-append — "
            "BTCUSDT/1h passou de 17.520 para 17.563 velas, as 43 extras no INICIO). "
            "Vereditos re-derivados sobre este snapshot sao numeros NOVOS, e permanentes."
        ),
        "series": entradas,
    }
    with open(os.path.join(destino, NOME_MANIFEST), "w", encoding="utf-8", newline="") as fp:
        json.dump(manifest, fp, indent=2, ensure_ascii=False)
        fp.write("\n")
    return destino


def caminho_do_snapshot(rotulo: str | None = None) -> str:
    """Diretorio do snapshot pedido, ou o mais recente por ordem de rotulo."""
    if rotulo:
        d = os.path.join(DIR_SNAPSHOTS, rotulo)
        if not os.path.isdir(d):
            raise FileNotFoundError(f"Snapshot inexistente: {d}")
        return d
    if not os.path.isdir(DIR_SNAPSHOTS):
        raise FileNotFoundError(
            f"Nenhum snapshot em {DIR_SNAPSHOTS}. Rode: python -m research.snapshot --criar"
        )
    candidatos = sorted(
        d
        for d in os.listdir(DIR_SNAPSHOTS)
        if os.path.isfile(os.path.join(DIR_SNAPSHOTS, d, NOME_MANIFEST))
    )
    if not candidatos:
        raise FileNotFoundError(
            f"Nenhum snapshot valido em {DIR_SNAPSHOTS}. Rode: python -m research.snapshot --criar"
        )
    return os.path.join(DIR_SNAPSHOTS, candidatos[-1])


def ler_manifest(rotulo: str | None = None) -> dict:
    with open(os.path.join(caminho_do_snapshot(rotulo), NOME_MANIFEST), encoding="utf-8") as fp:
        return json.load(fp)


def verificar(rotulo: str | None = None) -> list[dict]:
    """Recalcula o sha256 de cada serie e compara com o manifest.

    Devolve a lista de divergencias (vazia = integro). Nao levanta: quem chama
    decide o que fazer — `reproduzir.py` aborta, o CLI imprime.
    """
    base = caminho_do_snapshot(rotulo)
    manifest = ler_manifest(rotulo)
    problemas = []
    for s in manifest["series"]:
        caminho = os.path.join(base, s["arquivo"])
        if not os.path.exists(caminho):
            problemas.append({"arquivo": s["arquivo"], "erro": "ausente"})
            continue
        atual = _sha256(caminho)
        if atual != s["sha256"]:
            problemas.append(
                {
                    "arquivo": s["arquivo"],
                    "erro": "sha256 divergente",
                    "esperado": s["sha256"],
                    "encontrado": atual,
                }
            )
    return problemas


def carregar_serie(symbol: str, intervalo: str = "1h", rotulo: str | None = None):
    """(ts, maxima, minima, fechamento, volume) — mesma tupla de
    edge_lab.carregar_klines, para ser substituto direto.

    VERIFICA o sha256 antes de devolver. Ler um snapshot corrompido e pior que
    ler a tabela viva: a tabela viva ao menos nao finge ser imutavel.
    """
    import numpy as np

    base = caminho_do_snapshot(rotulo)
    manifest = ler_manifest(rotulo)
    entrada = next(
        (s for s in manifest["series"] if s["symbol"] == symbol and s["intervalo"] == intervalo),
        None,
    )
    if entrada is None:
        raise KeyError(f"{symbol}/{intervalo} nao esta no snapshot {manifest['rotulo']}")

    caminho = os.path.join(base, entrada["arquivo"])
    atual = _sha256(caminho)
    if atual != entrada["sha256"]:
        raise ValueError(
            f"Snapshot CORROMPIDO: {entrada['arquivo']} tem sha256 {atual[:16]}..., "
            f"o manifest diz {entrada['sha256'][:16]}.... Nenhum veredito derivado "
            f"deste arquivo e valido."
        )

    ts, m, n, f, v = [], [], [], [], []
    with open(caminho, encoding="utf-8", newline="") as fp:
        for linha in csv.DictReader(fp):
            ts.append(int(linha["timestamp"]))
            m.append(float(linha["maxima"]))
            n.append(float(linha["minima"]))
            f.append(float(linha["fechamento"]))
            v.append(float(linha["volume"]))
    if len(ts) != entrada["n"]:
        raise ValueError(
            f"Snapshot inconsistente: {entrada['arquivo']} tem {len(ts)} linhas, "
            f"o manifest diz {entrada['n']}."
        )
    return (
        np.array(ts),
        np.array(m, dtype=float),
        np.array(n, dtype=float),
        np.array(f, dtype=float),
        np.array(v, dtype=float),
    )


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Snapshot imutavel do substrato (I-11)")
    ap.add_argument("--criar", action="store_true")
    ap.add_argument("--verificar", action="store_true")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--rotulo", default=None, help="default: data UTC de hoje / mais recente")
    args = ap.parse_args()

    if args.criar:
        destino = criar(args.rotulo)
        m = ler_manifest(os.path.basename(destino))
        print(f"Snapshot criado: {destino}")
        for s in m["series"]:
            print(
                f"  {s['symbol']}/{s['intervalo']}: {s['n']} velas "
                f"({s['primeiro_iso']} -> {s['ultimo_iso']}) sha256={s['sha256'][:16]}..."
            )
        return 0

    if args.verificar:
        problemas = verificar(args.rotulo)
        if not problemas:
            m = ler_manifest(args.rotulo)
            print(f"Snapshot '{m['rotulo']}' INTEGRO ({len(m['series'])} series).")
            return 0
        print("Snapshot COMPROMETIDO:")
        for p in problemas:
            print(f"  {p}")
        return 1

    if args.listar:
        if not os.path.isdir(DIR_SNAPSHOTS):
            print(f"Nenhum snapshot em {DIR_SNAPSHOTS}")
            return 0
        for d in sorted(os.listdir(DIR_SNAPSHOTS)):
            mf = os.path.join(DIR_SNAPSHOTS, d, NOME_MANIFEST)
            if os.path.isfile(mf):
                with open(mf, encoding="utf-8") as fp:
                    m = json.load(fp)
                total = sum(s["n"] for s in m["series"])
                print(f"  {d}  ({len(m['series'])} series, {total} velas, {m['criado_em']})")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
