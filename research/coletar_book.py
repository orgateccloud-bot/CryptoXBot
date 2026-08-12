"""
Coletor de livro (@bookTicker) — matéria-prima da hipótese de microestrutura
=============================================================================
`METODOLOGIA_MICROESTRUTURA.md` congela cinco features. Duas delas — `ofi_book`
e `spread_rel` — precisam do melhor bid/ask ao longo do tempo, e **ninguém
persiste isso hoje**: o consumidor @depth do worker calcula OBI em memória e
descarta. Cada dia sem este coletor rodando é um dia de PESQUISA perdido para
sempre — a janela fecha em 2026-11-30.

Desenho, e por quê:

- **Processo separado do worker.** O bot 24/7 não ganha nenhuma linha de código
  novo por causa de pesquisa. Se este coletor morrer, o trading não sente.
- **Banco separado** (`data/book_btc.db`). O arquivo principal tem 375 MB e um
  escritor 24/7; disputar o lock dele por dado de pesquisa seria auto-sabotagem.
- **Agregado por minuto, não tick a tick.** O @bookTicker do BTC emite dezenas
  de mensagens por segundo; persistir cru são ~10 GB/mês. As features precisam
  do OFI SOMADO na janela e do spread médio — agregáveis sem perda para o que a
  metodologia mede. 3 pares × 1440 min/dia ≈ 4.300 linhas/dia.
- **OFI de Cont-Kukanov-Stoikov** no melhor nível: a "soma assinada das
  variações de tamanho no melhor bid/ask" da metodologia é exatamente e_n
  somado na janela (ver `ofi_incremento`).
- **Idempotente por minuto**: `INSERT OR REPLACE` com chave (symbol, minuto).
  Reiniciar o coletor perde no máximo o minuto corrente.

USO:
    python research/coletar_book.py              # coleta para sempre
    python research/coletar_book.py --status     # cobertura por par/dia

Para rodar 24/7 no Windows (mesmo padrão dos outros serviços):
    nssm install BXBotBook "<python>" "<repo>\\research\\coletar_book.py"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DB_BOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "book_btc.db"
)
# data-stream.binance.vision e o endpoint PUBLICO so de market-data da
# Binance: mesmo payload, sem auth, e nao compete com os limites da conexao
# de trading do worker. O stream.binance.com fica de fallback (algumas redes
# corporativas/sandboxes cortam um e nao o outro — medido neste host).
_STREAMS = "/".join(f"{p.lower()}@bookTicker" for p in PARES)
WS_URLS = [
    f"wss://data-stream.binance.vision/stream?streams={_STREAMS}",
    f"wss://stream.binance.com:9443/stream?streams={_STREAMS}",
]
MINUTO_MS = 60_000


# ── agregação pura (testável sem rede) ─────────────────────────


def ofi_incremento(
    bid_p: float,
    bid_q: float,
    ask_p: float,
    ask_q: float,
    prev_bid_p: float,
    prev_bid_q: float,
    prev_ask_p: float,
    prev_ask_q: float,
) -> float:
    """e_n de Cont-Kukanov-Stoikov no melhor nível.

    Lado bid: preço subiu -> chegou demanda (+q nova); preço caiu -> o nível
    foi consumido/cancelado (-q antiga); preço igual -> a variação de tamanho
    É o fluxo. Espelhado no ask com sinal trocado. A soma disso na janela é a
    "soma assinada das variações de tamanho no melhor bid/ask" congelada na
    metodologia.
    """
    if bid_p > prev_bid_p:
        e = bid_q
    elif bid_p < prev_bid_p:
        e = -prev_bid_q
    else:
        e = bid_q - prev_bid_q

    if ask_p < prev_ask_p:
        e -= ask_q
    elif ask_p > prev_ask_p:
        e += prev_ask_q
    else:
        e -= ask_q - prev_ask_q
    return e


@dataclass
class AgregadorMinuto:
    """Acumula updates de um par dentro de um minuto e fecha a linha."""

    symbol: str
    minuto_ms: int = 0
    ofi: float = 0.0
    soma_spread_rel: float = 0.0
    n_updates: int = 0
    _prev: tuple | None = field(default=None, repr=False)
    _ultimo: tuple | None = field(default=None, repr=False)

    def atualizar(self, ts_ms: int, bid_p, bid_q, ask_p, ask_q) -> dict | None:
        """Processa um update. Devolve a linha fechada quando o minuto vira."""
        minuto = (ts_ms // MINUTO_MS) * MINUTO_MS
        fechada = None
        if self.minuto_ms and minuto != self.minuto_ms:
            fechada = self.fechar()
        if not self.minuto_ms or minuto != self.minuto_ms:
            self.minuto_ms = minuto
            self.ofi = 0.0
            self.soma_spread_rel = 0.0
            self.n_updates = 0

        if self._prev is not None:
            self.ofi += ofi_incremento(bid_p, bid_q, ask_p, ask_q, *self._prev)
        mid = (bid_p + ask_p) / 2.0
        if mid > 0:
            self.soma_spread_rel += (ask_p - bid_p) / mid
        self.n_updates += 1
        self._prev = (bid_p, bid_q, ask_p, ask_q)
        self._ultimo = (bid_p, bid_q, ask_p, ask_q)
        return fechada

    def fechar(self) -> dict | None:
        """Linha do minuto corrente, ou None se nada foi acumulado."""
        if not self.n_updates or self._ultimo is None:
            return None
        bid_p, bid_q, ask_p, ask_q = self._ultimo
        mid = (bid_p + ask_p) / 2.0
        return {
            "symbol": self.symbol,
            "minuto_ms": self.minuto_ms,
            "ofi": self.ofi,
            "spread_rel_medio": self.soma_spread_rel / self.n_updates,
            "mid_fim": mid,
            "bid_qty_fim": bid_q,
            "ask_qty_fim": ask_q,
            "n_updates": self.n_updates,
        }


# ── persistência ───────────────────────────────────────────────


def conectar(caminho: str = DB_BOOK) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_minuto (
            symbol           TEXT    NOT NULL,
            minuto_ms        INTEGER NOT NULL,
            ofi              REAL    NOT NULL,
            spread_rel_medio REAL    NOT NULL,
            mid_fim          REAL    NOT NULL,
            bid_qty_fim      REAL    NOT NULL,
            ask_qty_fim      REAL    NOT NULL,
            n_updates        INTEGER NOT NULL,
            PRIMARY KEY (symbol, minuto_ms)
        )
        """)
    return conn


def salvar_minuto(conn: sqlite3.Connection, linha: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO book_minuto
        (symbol, minuto_ms, ofi, spread_rel_medio, mid_fim, bid_qty_fim,
         ask_qty_fim, n_updates)
        VALUES (:symbol, :minuto_ms, :ofi, :spread_rel_medio, :mid_fim,
                :bid_qty_fim, :ask_qty_fim, :n_updates)
        """,
        linha,
    )
    conn.commit()


# ── loop de coleta ─────────────────────────────────────────────


async def coletar(parar: asyncio.Event | None = None) -> None:
    import websockets

    conn = conectar()
    aggs = {p: AgregadorMinuto(p) for p in PARES}
    falhas = 0
    print(f"[BOOK] coletando {', '.join(PARES)} -> {DB_BOOK}", flush=True)
    while not (parar and parar.is_set()):
        url = WS_URLS[falhas % len(WS_URLS)]
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                print(f"[BOOK] conectado a {url.split('/')[2]}", flush=True)
                falhas = 0
                async for msg in ws:
                    d = json.loads(msg).get("data", {})
                    sym = d.get("s")
                    if sym not in aggs:
                        continue
                    # bookTicker nao traz event time em todos os payloads do
                    # stream combinado; o relogio local por MINUTO e suficiente
                    # para a agregacao (erro de fronteira <=1s em janela de 60s).
                    ts_ms = int(time.time() * 1000)
                    fechada = aggs[sym].atualizar(
                        ts_ms, float(d["b"]), float(d["B"]), float(d["a"]), float(d["A"])
                    )
                    if fechada:
                        salvar_minuto(conn, fechada)
                    if parar and parar.is_set():
                        break
        except asyncio.CancelledError:
            break
        except Exception as e:
            falhas += 1
            espera = min(60, 2**falhas)
            print(f"[BOOK] reconectando em {espera}s ({type(e).__name__}: {e})", flush=True)
            await asyncio.sleep(espera)
    # flush do minuto corrente ao sair — perder <=59s e aceitavel, perder de
    # graca nao
    for agg in aggs.values():
        linha = agg.fechar()
        if linha:
            salvar_minuto(conn, linha)
    conn.close()


def cmd_status() -> int:
    if not os.path.exists(DB_BOOK):
        print(f"\n  Sem coleta ainda: {DB_BOOK} nao existe.")
        print("  Inicie com: python research/coletar_book.py\n")
        return 1
    conn = conectar()
    print(f"\n  {DB_BOOK}\n")
    print(f"  {'par':<10} {'minutos':>9} {'primeiro':<17} {'ultimo':<17} {'cobertura'}")
    for sym in PARES:
        row = conn.execute(
            "SELECT COUNT(*), MIN(minuto_ms), MAX(minuto_ms) FROM book_minuto WHERE symbol=?",
            (sym,),
        ).fetchone()
        n, mn, mx = row
        if not n:
            print(f"  {sym:<10} {0:>9}")
            continue
        de = time.strftime("%Y-%m-%d %H:%M", time.localtime(mn / 1000))
        ate = time.strftime("%Y-%m-%d %H:%M", time.localtime(mx / 1000))
        janela = (mx - mn) // MINUTO_MS + 1
        print(f"  {sym:<10} {n:>9} {de:<17} {ate:<17} {100 * n / janela:.1f}%")
    conn.close()
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--status", action="store_true", help="cobertura por par")
    args = ap.parse_args(argv)
    if args.status:
        return cmd_status()
    try:
        asyncio.run(coletar())
    except KeyboardInterrupt:
        print("\n[BOOK] encerrado pelo usuario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
