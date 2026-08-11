"""
Purga registros de FIXTURE DE TESTE que vazaram para o banco de produção.
=========================================================================
Incidente de 2026-07-31: a suíte de testes escrevia em `data/btc_data.db` porque
não existia `conftest.py`. `tests/test_executor_monitor.py` instancia um
`Executor` real e persiste posição de fixture. O banco vivo ficou com:

    risk_state['posicao:BTCUSDT'] = {"entrada": 100.0, "order_id": "SIM-1",
                                     "abertura": "x", "target1": 105.0, ...}

O boot do worker readotava isso sem validar. Com BTC a ~64.000 o monitor via
preço >> target1 e "fechava" no primeiro tick, gravando em `sinais` PnL de
+62.658% e +64.429%. Ocorreu três vezes (09/07, 19/07, 31/07). Com chave de
trading habilitada, teria disparado um SELL real de BTC não comprado.

A ORIGEM já está fechada por `conftest.py` (redireciona DB_PATH e falha alto se
alguém abrir o arquivo de produção) e por `Executor._posicao_e_plausivel`
(recusa readotar posição implausível). Este script limpa o que já vazou.

Critérios de identificação — conservadores de propósito. Só remove o que carrega
marca inequívoca de fixture:

  posições: order_id começando com "SIM-" (o ramo de simulação de _enviar_ordem
            gera "SIM-<epoch>"; a fixture usa "SIM-1"), OU `abertura` que não é
            timestamp ISO, OU entrada divergindo >90% do preço de mercado.
  sinais:   fechamentos cujo PnL no texto do motivo excede |1000%|. Nenhum trade
            real de spot com stop de 1,5% chega perto disso.

USO (o padrão é dry-run; nada muda sem --confirmar):
    python scripts/purgar_fixtures_producao.py
    python scripts/purgar_fixtures_producao.py --confirmar

Faz backup automático antes de qualquer escrita.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LIMITE_PNL_ABSURDO_PCT = 1000.0
DIVERGENCIA_MAX = 0.90


def _preco_mercado(symbol: str) -> float:
    try:
        import requests

        from config.runtime_settings import REST_BASE_URL

        r = requests.get(
            f"{REST_BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=8
        )
        return float(r.json()["price"])
    except Exception:
        return 0.0


def _worker_em_simulacao() -> bool:
    """M-1: o worker está rodando em paper AGORA?

    O bot publica o modo EFETIVO em `bot_events` (`modo_efetivo`,
    main.py:1631) justamente porque o ambiente DESTE processo não reflete o
    `--real` do worker. Na dúvida — sem evento, sem banco, erro de leitura —
    devolve True: tratar como paper preserva dados, tratar como real os apaga.
    """
    try:
        import database

        eventos = database.listar_bot_events(limite=1, tipo="modo_efetivo")
        if eventos:
            msg = str(eventos[0].get("message") or "").upper()
            em_sim = "SIMULACAO" in msg
            print(f"  Modo do worker (ultimo evento): {'SIMULACAO' if em_sim else 'REAL'}")
            return em_sim
        print("  [AVISO] nenhum evento 'modo_efetivo' registrado — assumindo SIMULACAO")
    except Exception as e:
        print(f"  [AVISO] nao consegui ler o modo efetivo ({e}) — assumindo SIMULACAO")
    return True


def _posicao_suspeita(nome: str, dados: dict, em_simulacao: bool | None = None) -> str | None:
    """Devolve o motivo se for fixture, ou None se parecer legítima.

    M-1: `order_id` começando com `SIM-` NÃO é fixture quando o bot roda em
    paper — `executor.py:502` gera exatamente esse formato em simulação, e é
    esse o modo em que o BXBotWorker roda 24/7 acumulando a Etapa 2 do gate.
    O critério casava com TODA posição legítima de paper: `--confirmar`
    apagava o estado inteiro que a Etapa 2 depende.

    `executor.py:1836` já tinha a guarda certa (`if not self.simulacao and
    order_id.startswith("SIM-")`) — ela só faltava aqui.
    """
    if em_simulacao is None:
        em_simulacao = _worker_em_simulacao()
    order_id = str(dados.get("order_id") or "")
    if order_id.startswith("SIM-") and not em_simulacao:
        return f'order_id sintetico "{order_id}"'
    abertura = str(dados.get("abertura") or "")
    if abertura and "T" not in abertura:
        return f'abertura nao e timestamp ISO: "{abertura}"'
    entrada = dados.get("entrada")
    if isinstance(entrada, (int, float)) and entrada > 0:
        symbol = nome.split(":", 1)[1] if ":" in nome else "BTCUSDT"
        preco = _preco_mercado(symbol)
        if preco > 0 and abs(preco - entrada) / preco > DIVERGENCIA_MAX:
            return f"entrada {entrada:,.2f} diverge >90% do mercado {preco:,.2f}"
    return None


def _pnl_do_motivo(motivo: str) -> float | None:
    """Extrai o PnL% do texto do motivo. Cuidado com o formato: o código grava
    com ponto decimal (f"{pnl_pct:+.2f}%" -> "+62658.27%"), mas texto vindo de
    outra origem pode usar padrão pt-BR. Tratar "62658.27" como se o ponto fosse
    separador de milhar dá 6.265.827 — e esse número é justamente o que o
    operador lê antes de autorizar uma remoção."""
    m = re.search(r"PnL:\s*([+-]?[\d.,]+)%", motivo or "")
    if not m:
        return None
    bruto = m.group(1)
    tem_ponto, tem_virgula = "." in bruto, "," in bruto
    if tem_ponto and tem_virgula:  # pt-BR: 1.234,56
        bruto = bruto.replace(".", "").replace(",", ".")
    elif tem_virgula:  # só vírgula: decimal pt-BR
        bruto = bruto.replace(",", ".")
    # só ponto (ou nenhum) já é float válido — não mexer
    try:
        return float(bruto)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--confirmar", action="store_true", help="aplica de fato (padrao: dry-run)")
    ap.add_argument("--db", default=None, help="caminho do banco (padrao: DB_PATH do runtime)")
    args = ap.parse_args()

    from config.runtime_settings import DB_PATH

    db = args.db or DB_PATH
    if not os.path.exists(db):
        print(f"[ERRO] banco nao encontrado: {db}")
        return 2

    modo = "APLICANDO" if args.confirmar else "DRY-RUN (nada sera alterado)"
    print(f"\n  Banco: {db}")
    print(f"  Modo:  {modo}\n")

    con = sqlite3.connect(db, timeout=30)
    cur = con.cursor()

    # ── posições persistidas ────────────────────────────────────
    alvos_pos = []
    for nome, upd, data in cur.execute(
        "SELECT name, updated_at, data FROM risk_state WHERE name LIKE 'posicao:%'"
    ):
        try:
            dados = json.loads(data)
        except Exception:
            alvos_pos.append((nome, upd, "JSON invalido", data[:120]))
            continue
        motivo = _posicao_suspeita(nome, dados)
        if motivo:
            alvos_pos.append((nome, upd, motivo, json.dumps(dados)[:160]))
        else:
            print(f"  [MANTIDA] {nome} (updated_at={upd}) — parece legitima")

    print(f"  Posicoes de fixture encontradas: {len(alvos_pos)}")
    for nome, upd, motivo, amostra in alvos_pos:
        print(f"    - {nome}  updated_at={upd}")
        print(f"        motivo: {motivo}")
        print(f"        dados : {amostra}")

    # ── sinais de fechamento com PnL absurdo ────────────────────
    alvos_sig = []
    for sid, ts, tipo, preco, motivo in cur.execute(
        "SELECT id, timestamp, tipo, preco, motivo FROM sinais "
        "WHERE tipo IN ('FECHAR_LONG','STOP') ORDER BY id"
    ):
        pnl = _pnl_do_motivo(motivo or "")
        if pnl is not None and abs(pnl) > LIMITE_PNL_ABSURDO_PCT:
            alvos_sig.append((sid, ts, tipo, preco, motivo, pnl))

    print(
        f"\n  Sinais de fechamento fabricados (|PnL| > {LIMITE_PNL_ABSURDO_PCT:.0f}%): "
        f"{len(alvos_sig)}"
    )
    for sid, ts, tipo, preco, motivo, pnl in alvos_sig:
        print(f"    - id={sid} {ts[:19]} {tipo} preco={preco} PnL={pnl:+,.2f}%")
        print(f"        motivo: {motivo}")

    # ── coerencia de posicoes_abertas ───────────────────────────
    row = cur.execute("SELECT data FROM risk_state WHERE name='default'").fetchone()
    estado = json.loads(row[0]) if row else {}
    total_pos = cur.execute(
        "SELECT COUNT(*) FROM risk_state WHERE name LIKE 'posicao:%'"
    ).fetchone()[0]
    esperado = total_pos - len(alvos_pos)
    atual = estado.get("posicoes_abertas")
    incoerente = atual != esperado
    print(
        f"\n  posicoes_abertas em risk_state['default']: {atual} | esperado apos purga: {esperado}"
    )
    if incoerente:
        print("    -> sera reconciliado")

    if not (alvos_pos or alvos_sig or incoerente):
        print("\n  Nada a fazer. Banco limpo.\n")
        con.close()
        return 0

    if not args.confirmar:
        print("\n  DRY-RUN: nada foi alterado. Rode de novo com --confirmar para aplicar.\n")
        con.close()
        return 0

    con.close()
    carimbo = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = f"{db}.bak-purga-{carimbo}"
    shutil.copy2(db, backup)
    print(f"\n  Backup: {backup}")

    con = sqlite3.connect(db, timeout=30)
    cur = con.cursor()
    for nome, *_ in alvos_pos:
        cur.execute("DELETE FROM risk_state WHERE name=?", (nome,))
    if alvos_sig:
        cur.executemany("DELETE FROM sinais WHERE id=?", [(s[0],) for s in alvos_sig])
    if incoerente:
        estado["posicoes_abertas"] = esperado
        cur.execute("UPDATE risk_state SET data=? WHERE name='default'", (json.dumps(estado),))
    con.commit()

    pos_rest = cur.execute(
        "SELECT COUNT(*) FROM risk_state WHERE name LIKE 'posicao:%'"
    ).fetchone()[0]
    fech_rest = cur.execute(
        "SELECT COUNT(*) FROM sinais WHERE tipo IN ('FECHAR_LONG','STOP')"
    ).fetchone()[0]
    con.close()

    print(f"  Removidas   : {len(alvos_pos)} posicoes, {len(alvos_sig)} sinais")
    print(f"  Restantes   : {pos_rest} posicoes persistidas, {fech_rest} fechamentos")
    print("\n  Reinicie o worker para garantir estado limpo em memoria:")
    print("    powershell -ExecutionPolicy Bypass -File scripts\\restart-servico.ps1\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
