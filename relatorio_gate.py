"""
Relatório do Gate — BinanceXBot
=================================
Fonte única de verdade para as métricas da Etapa 2 do GATE_GO_LIVE.md.
Lê trades FECHADOS da tabela `sinais` (pnl_usdt IS NOT NULL) e compara o
resultado contra buy-and-hold de BTCUSDT no mesmo período.

Uso:
  python relatorio_gate.py                          # SQLite (data/btc_data.db)
  python relatorio_gate.py --db caminho/outro.db
  python relatorio_gate.py --capital 1000
  DATABASE_URL=postgresql://... python relatorio_gate.py --postgres

Sem dependências além da stdlib (psycopg3 opcional para --postgres).
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH_DEFAULT = "data/btc_data.db"
MIN_TRADES = 30
MIN_DIAS = 90
MIN_PROFIT_FACTOR = 1.3
MAX_DRAWDOWN_PCT = 15.0

# E-8: a Etapa 2 mede a estrategia PRIMARIA, e so ela.
#
# Ate 2026-08-08 esta consulta era `WHERE tipo='COMPRA' AND executado=1 AND
# pnl_usdt IS NOT NULL`, sem filtro de `source` — todas as linhas de `sinais`
# entravam. Isso era inofensivo enquanto so `estrategia_otimizada` escrevia ali.
# Deixou de ser no momento em que o caminho trend passou a registrar sinais (e
# ele PRECISA registrar: e o dry run de validacao de EXECUCAO que esta rodando
# no worker agora).
#
# O trend esta REPROVADO no hold-out, por escrito, e a proibicao pre-registrada
# e explicita: nao promover variante secundaria a primaria. Sem este filtro, o
# PnL de uma estrategia reprovada entraria na conta que decide se o capital real
# pode ser ligado — e poderia APROVAR por acidente. Filtrar aqui e mais seguro
# que confiar em quem escreve na tabela.
SOURCE_PRIMARIA = "estrategia_otimizada"


# ── Acesso a dados ────────────────────────────────────────────


def _conectar(args):
    if args.postgres:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            sys.exit("--postgres exige DATABASE_URL no ambiente")
        try:
            # I-13: psycopg3. `psycopg2` NAO esta no requirements.txt — o
            # projeto inteiro usa psycopg3 (database.py:82), entao este
            # import falhava mesmo num ambiente corretamente instalado.
            import psycopg  # type: ignore
        except ImportError:
            sys.exit("pip install 'psycopg[binary]' para usar --postgres")
        return psycopg.connect(url), "%s"
    if not os.path.exists(args.db):
        sys.exit(f"Banco não encontrado: {args.db}")
    return sqlite3.connect(args.db), "?"


def carregar_trades_fechados(conn, ph, source=SOURCE_PRIMARIA):
    """Trades fechados da estrategia PRIMARIA: COMPRA executada com PnL.

    `source=None` desativa o filtro (util para inspecao manual), mas o default e
    e deve continuar sendo a primaria — ver SOURCE_PRIMARIA acima.

    Linhas com `source` NULO contam como primarias: sao as ~5.255 gravadas antes
    de o campo passar a ser preenchido de forma disciplinada, e todas vieram de
    estrategia_otimizada. Excluir seria descartar historico legitimo.
    """
    # A unica interpolacao e o literal interno "true"/"1" (dialeto do
    # backend); nenhum input externo entra na string.
    executado_literal = "true" if ph == "%s" else "1"
    filtro_source = f" AND (source IS NULL OR source = {ph})" if source else ""
    sql = (  # nosec B608
        "SELECT timestamp, symbol, preco, preco_saida, pnl_usdt, pnl_pct, "  # nosec B608
        "barreira_tocada FROM sinais "
        f"WHERE tipo = 'COMPRA' AND executado = {executado_literal}"
        f" AND pnl_usdt IS NOT NULL{filtro_source} ORDER BY timestamp ASC"
    )
    cur = conn.cursor()
    cur.execute(sql, (source,) if source else ())
    linhas = cur.fetchall()

    # NUNCA excluir em silencio: um gate que descarta linhas sem dizer quantas
    # e indistinguivel de um gate que nao tem dados.
    if source:
        cur.execute(
            "SELECT source, COUNT(*) FROM sinais "  # nosec B608
            f"WHERE tipo = 'COMPRA' AND executado = {executado_literal}"
            " AND pnl_usdt IS NOT NULL AND source IS NOT NULL"
            f" AND source <> {ph} GROUP BY source",
            (source,),
        )
        for src, n in cur.fetchall():
            print(
                f"  [GATE] EXCLUIDOS {n} trades de source='{src}' — a Etapa 2 mede "
                f"apenas '{source}'. Estrategia secundaria/reprovada nao entra na "
                f"conta que decide capital real."
            )

    trades = []
    for ts, symbol, preco, preco_saida, pnl_usdt, pnl_pct, barreira in linhas:
        trades.append(
            {
                "ts": _parse_ts(ts),
                "symbol": symbol,
                "entrada": preco,
                "saida": preco_saida,
                "pnl_usdt": float(pnl_usdt),
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                "barreira": barreira or "?",
            }
        )
    return trades


GATE_DOC = "docs/GATE_GO_LIVE.md"


def _etapa1_aprovada() -> bool:
    """M-1: a Etapa 1 do gate passou?

    A Etapa 2 (que este relatório avalia) só pode começar se a Etapa 1 aprovar
    — está escrito em `GATE_GO_LIVE.md:157`. O relatório podia imprimir
    "APROVADO — prosseguir à Etapa 3" e sair 0 sem nunca olhar para isso.

    A fonte é o próprio documento do gate, que é onde o veredito é registrado.
    FAIL-CLOSED: documento ausente ou ilegível conta como REPROVADA. Uma
    ferramenta que decide sobre capital não pode dar luz verde por não ter
    conseguido ler o pré-requisito.
    """
    try:
        with open(GATE_DOC, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        print(f"  [!!] {GATE_DOC} nao encontrado — Etapa 1 tratada como REPROVADA.")
        return False
    # O documento declara o veredito no cabeçalho. Enquanto essa frase estiver
    # lá, a Etapa 1 está reprovada.
    return "ESTRATÉGIA REPROVADA" not in texto.upper().replace("ESTRATEGIA", "ESTRATÉGIA")


def _parse_ts(ts):
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):  # epoch ms ou s
        ts = ts / 1000 if ts > 1e12 else ts
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(ts)[:26], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(ts))
    except ValueError:
        sys.exit(f"Timestamp não reconhecido: {ts!r}")


def preco_btc_periodo(conn, ph, inicio, fim):
    """Fechamento de BTCUSDT mais próximo do início e do fim (tabela klines)."""
    cur = conn.cursor()
    try:
        cur.execute(
            # nosec B608 — {ph} e o placeholder do driver ("?"/"%s"), literal
            # interno; os valores vao todos parametrizados na tupla abaixo.
            f"SELECT fechamento FROM klines WHERE symbol={ph} AND intervalo={ph} "  # nosec B608
            f"AND timestamp >= {ph} ORDER BY timestamp ASC LIMIT 1",
            ("BTCUSDT", "1h", int(inicio.timestamp() * 1000)),
        )
        p_ini = cur.fetchone()
        cur.execute(
            f"SELECT fechamento FROM klines WHERE symbol={ph} AND intervalo={ph} "  # nosec B608
            f"AND timestamp <= {ph} ORDER BY timestamp DESC LIMIT 1",
            ("BTCUSDT", "1h", int(fim.timestamp() * 1000)),
        )
        p_fim = cur.fetchone()
        if p_ini and p_fim:
            return float(p_ini[0]), float(p_fim[0])
    except Exception:
        pass
    return None, None


# ── Métricas ──────────────────────────────────────────────────


def metricas(trades, capital_inicial):
    ganhos = [t["pnl_usdt"] for t in trades if t["pnl_usdt"] > 0]
    perdas = [t["pnl_usdt"] for t in trades if t["pnl_usdt"] < 0]
    pnl_total = sum(t["pnl_usdt"] for t in trades)
    bruto_g, bruto_p = sum(ganhos), abs(sum(perdas))
    # M-1: era `float("inf")` sem perdedores — e `inf > 1.3` PASSA. Um punhado
    # de trades sem nenhuma perda dava profit factor infinito e APROVAVA o
    # criterio, que e exatamente o cenario de amostra pequena e sortuda que o
    # gate existe para barrar. Sem perdedores o PF nao esta definido: None
    # reprova, em vez de aprovar.
    pf = bruto_g / bruto_p if bruto_p > 0 else None
    win_rate = len(ganhos) / len(trades) * 100 if trades else 0.0

    # Max drawdown sobre equity acumulada
    equity, pico, mdd = capital_inicial, capital_inicial, 0.0
    for t in trades:
        equity += t["pnl_usdt"]
        pico = max(pico, equity)
        mdd = max(mdd, (pico - equity) / pico * 100 if pico > 0 else 0.0)

    return {
        "n": len(trades),
        "win_rate": win_rate,
        "pf": pf,
        "pnl_total": pnl_total,
        "retorno_pct": pnl_total / capital_inicial * 100,
        "expectancy": pnl_total / len(trades) if trades else 0.0,
        "mdd_pct": mdd,
        "maior_perda": min(perdas) if perdas else 0.0,
        "maior_ganho": max(ganhos) if ganhos else 0.0,
    }


def _linha(criterio, valor, minimo, passou):
    status = "PASSOU" if passou else "REPROVOU"
    print(f"  {'[ok]' if passou else '[XX]'} {criterio:<42} {valor:<18} (mín: {minimo}) → {status}")
    return passou


# ── Relatório ─────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="Relatório do Gate de Go-Live")
    ap.add_argument("--db", default=DB_PATH_DEFAULT)
    ap.add_argument("--postgres", action="store_true")
    ap.add_argument("--capital", type=float, default=1000.0, help="capital simulado inicial (USDT)")
    args = ap.parse_args()

    conn, ph = _conectar(args)
    trades = carregar_trades_fechados(conn, ph)

    print("=" * 78)
    print("RELATÓRIO DO GATE — trades fechados (sinais.pnl_usdt IS NOT NULL)")
    print("=" * 78)

    if not trades:
        print("\n  ZERO trades fechados no banco.")
        print("  Conclusão honesta: não existe validação. O gate está na estaca zero.")
        print("  Próximo passo: Etapa 1 do GATE_GO_LIVE.md (backtest walk-forward).")
        conn.close()
        sys.exit(1)

    inicio, fim = trades[0]["ts"], trades[-1]["ts"]
    dias = max((fim - inicio).days, 0)
    m = metricas(trades, args.capital)

    print(f"\nPeríodo: {inicio:%Y-%m-%d} → {fim:%Y-%m-%d}  ({dias} dias)")
    print(f"Capital simulado inicial: {args.capital:.2f} USDT\n")
    print(f"  Trades fechados:  {m['n']}")
    print(f"  Win rate:         {m['win_rate']:.1f}%   (lembrete: win rate NÃO é o critério)")
    # M-1: `pf` e None quando nao ha perdedores — nao formatar como numero.
    # "indefinido" e a leitura honesta: com zero perdas o PF nao existe, e
    # antes isso virava `inf`, que passava no criterio.
    _pf = m["pf"]
    print(f"  Profit factor:    {'indefinido (sem perdas)' if _pf is None else f'{_pf:.2f}'}")
    print(f"  PnL total:        {m['pnl_total']:+.2f} USDT  ({m['retorno_pct']:+.2f}%)")
    print(f"  Expectancy:       {m['expectancy']:+.2f} USDT/trade")
    print(f"  Max drawdown:     {m['mdd_pct']:.2f}%")
    print(f"  Maior ganho/perda: {m['maior_ganho']:+.2f} / {m['maior_perda']:+.2f} USDT")

    por_barreira = {}
    for t in trades:
        por_barreira[t["barreira"]] = por_barreira.get(t["barreira"], 0) + 1
    print(
        "  Saídas por barreira: " + ", ".join(f"{k}={v}" for k, v in sorted(por_barreira.items()))
    )

    # Benchmark buy-and-hold
    p_ini, p_fim = preco_btc_periodo(conn, ph, inicio, fim)
    print("\nBenchmark buy-and-hold BTCUSDT no mesmo período:")
    bh = None
    if p_ini and p_fim:
        bh = (p_fim / p_ini - 1) * 100
        print(f"  BTC: {p_ini:.0f} → {p_fim:.0f}  ({bh:+.2f}%)   vs bot: {m['retorno_pct']:+.2f}%")
    else:
        print("  Sem klines BTCUSDT/1h no banco para o período — rode coletar_dados.py.")

    print("\n" + "=" * 78)
    print("AVALIAÇÃO CONTRA O GATE (Etapa 2 — GATE_GO_LIVE.md)")
    print("=" * 78)
    aprovado = True

    # M-1: FAIL-CLOSED contra a Etapa 1. Este relatorio avalia a Etapa 2, e
    # podia imprimir "GATE: APROVADO — prosseguir a Etapa 3" e sair 0 sem
    # nunca consultar a Etapa 1 — que esta REPROVADA em 4 de 5 criterios
    # (docs/GATE_GO_LIVE.md). Aprovar a Etapa 2 nao pula a Etapa 1: as etapas
    # sao sequenciais, e a ferramenta que o operador usa para decidir sobre
    # capital nao pode dizer "prossiga" enquanto a anterior esta reprovada.
    etapa1_ok = _etapa1_aprovada()
    aprovado &= _linha(
        "Etapa 1 do gate (pre-requisito)",
        "APROVADA" if etapa1_ok else "REPROVADA",
        "APROVADA",
        etapa1_ok,
    )

    aprovado &= _linha("Duração (dias)", f"{dias}", f"{MIN_DIAS}", dias >= MIN_DIAS)
    aprovado &= _linha("Trades fechados", f"{m['n']}", f"{MIN_TRADES}", m["n"] >= MIN_TRADES)
    aprovado &= _linha(
        "Profit factor",
        "indefinido (sem perdas)" if m["pf"] is None else f"{m['pf']:.2f}",
        f"{MIN_PROFIT_FACTOR}",
        m["pf"] is not None and m["pf"] > MIN_PROFIT_FACTOR,
    )
    aprovado &= _linha("PnL total > 0", f"{m['pnl_total']:+.2f}", "> 0", m["pnl_total"] > 0)
    aprovado &= _linha(
        "Max drawdown",
        f"{m['mdd_pct']:.2f}%",
        f"<= {MAX_DRAWDOWN_PCT}%",
        m["mdd_pct"] <= MAX_DRAWDOWN_PCT,
    )
    if bh is not None:
        aprovado &= _linha(
            "Retorno >= buy-and-hold BTC",
            f"{m['retorno_pct']:+.2f}%",
            f">= {bh:+.2f}%",
            m["retorno_pct"] >= bh,
        )
    else:
        print(
            "  [??] Benchmark indisponível — critério buy-and-hold NÃO avaliado (não conta como "
            "aprovado)."
        )
        aprovado = False

    print(
        "\n"
        + (
            "GATE: APROVADO — prosseguir à Etapa 3 (capital piloto)."
            if aprovado
            else "GATE: REPROVADO — capital real continua PROIBIDO."
        )
    )
    if m["n"] < MIN_TRADES:
        print(f"Atenção: com n={m['n']} qualquer métrica acima é estatisticamente frágil.")
    conn.close()
    sys.exit(0 if aprovado else 1)


if __name__ == "__main__":
    main()
