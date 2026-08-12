"""
Relatório do Gate — BinanceXBot
=================================
Fonte única de verdade para as métricas da Etapa 2 do GATE_GO_LIVE.md.
Lê trades FECHADOS da tabela `sinais` (pnl_usdt IS NOT NULL) e compara o
resultado contra buy-and-hold de BTCUSDT no mesmo período.

A FONTE segue a configuração do projeto (`DATABASE_BACKEND`/`DATABASE_URL`),
igual ao resto do bot — não um caminho fixo. Ver `_conectar`.

Uso:
  python relatorio_gate.py                    # o backend que estiver configurado
  python relatorio_gate.py --sqlite           # forca o SQLite local
  python relatorio_gate.py --db outro.db      # forca ESTE arquivo
  python relatorio_gate.py --postgres         # forca Postgres (exige DATABASE_URL)
  python relatorio_gate.py --capital 1000

Só stdlib, fora psycopg3 quando o backend for Postgres.
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join("data", "btc_data.db")

# O relatorio imprime "→", "≥" e acentos. No console padrao do Windows
# (cp1252) o primeiro "→" levantava UnicodeEncodeError e o comando morria com
# traceback DEPOIS de ja ter lido o banco — o operador via um stack trace onde
# deveria ver APROVADO/REPROVADO. Um gate que nao consegue imprimir o veredito
# nao decidiu nada.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - stream sem suporte
        pass
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


def _config():
    """(backend, database_url, db_path) como o resto do bot os enxerga.

    Passa por `config.runtime_settings` para herdar o `load_dotenv()` — a
    configuracao do operador vive no `.env`, e ler so `os.environ` perderia
    ela. Se o modulo nao importar (ambiente pelado), cai para as env vars: e
    melhor rodar com menos contexto do que nao rodar.
    """
    try:
        sys.path.insert(0, RAIZ)
        from config.runtime_settings import DATABASE_BACKEND, DATABASE_URL, DB_PATH

        return (DATABASE_BACKEND or "sqlite"), (DATABASE_URL or ""), (DB_PATH or DB_PATH_DEFAULT)
    except Exception:
        return (
            os.environ.get("DATABASE_BACKEND", "sqlite"),
            os.environ.get("DATABASE_URL", ""),
            os.environ.get("DB_PATH", DB_PATH_DEFAULT),
        )


def _mascarar(dsn: str) -> str:
    """postgresql://user:SENHA@host/db -> postgresql://user:***@host/db"""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", dsn)


def _conectar(args):
    """Resolve a FONTE dos trades e devolve (conexao, placeholder, descricao).

    I-13 — o defeito que isto corrige: `--db data/btc_data.db` era o default
    fixo, e o Postgres so entrava com `--postgres` explicito. Num deploy com
    `DATABASE_BACKEND=postgres`, rodar `python relatorio_gate.py` media o
    SQLite local — que nessa maquina e um arquivo de paper trading antigo — e
    imprimia um veredito sobre o banco ERRADO. A ferramenta que decide se o
    capital real pode ser ligado nao pode escolher a fonte por omissao.

    Precedencia (explicito ganha da configuracao, configuracao ganha do
    default): --db > --sqlite > --postgres > DATABASE_BACKEND.
    """
    backend_cfg, url_cfg, db_cfg = _config()

    if args.db:
        alvo = args.db
    elif args.sqlite:
        alvo = db_cfg
    elif args.postgres or backend_cfg.lower() in {"postgres", "postgresql", "supabase"}:
        url = url_cfg or os.environ.get("DATABASE_URL", "")
        if not url:
            # FAIL-CLOSED: nao cair para o SQLite em silencio. O backend
            # configurado e Postgres; sem DSN a resposta certa e "nao sei
            # medir", nao "medi outra coisa".
            sys.exit(
                "Backend Postgres configurado mas DATABASE_URL esta vazio.\n"
                "Defina DATABASE_URL, ou rode com --sqlite para medir o arquivo local."
            )
        try:
            # I-13: psycopg3. `psycopg2` NAO esta no requirements.txt — o
            # projeto inteiro usa psycopg3 (database.py:82), entao este
            # import falhava mesmo num ambiente corretamente instalado.
            import psycopg  # type: ignore
        except ImportError:
            sys.exit("pip install 'psycopg[binary]' para usar o backend Postgres")
        return psycopg.connect(url), "%s", f"postgres {_mascarar(url)}"
    else:
        alvo = db_cfg

    if not os.path.isabs(alvo):
        alvo = os.path.join(RAIZ, alvo)
    alvo = os.path.normpath(alvo)
    if not os.path.exists(alvo):
        sys.exit(f"Banco não encontrado: {alvo}")
    return sqlite3.connect(alvo), "?", f"sqlite {alvo}"


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


# Ancorado no diretorio DO SCRIPT, nao no cwd: relativo, um `cd` qualquer fazia
# o open() falhar e a Etapa 1 constar como REPROVADA por arquivo ausente. O
# fail-closed esta certo, o motivo estaria errado — e motivo errado num gate e
# o que faz o operador parar de acreditar nele.
GATE_DOC = os.path.join(RAIZ, "docs", "GATE_GO_LIVE.md")


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
    except Exception as exc:
        # Nao engolir: "sem benchmark" reprova o gate, e o operador precisa
        # saber se foi falta de dado ou erro de consulta.
        print(f"  [!!] Falha ao ler klines da fonte principal: {exc}")
    return None, None


def benchmark_btc(conn, ph, inicio, fim, klines_db=None):
    """Buy-and-hold de BTC no periodo, tentando a fonte principal e depois o
    SQLite local.

    `klines` NAO existe no Postgres: nao esta em `_inicializar_postgres`
    (database.py:293) nem em nenhuma migration — quem a cria e
    `backtesting/coletar_dados.py`, que escreve sempre em SQLite. Sem este
    fallback, apontar o relatorio para o Supabase faria o criterio
    buy-and-hold ficar PERMANENTEMENTE indisponivel, e como benchmark ausente
    reprova (main:339), o gate reprovaria para sempre por falta de fonte — nao
    por desempenho da estrategia. Seria fail-closed pelo motivo errado, que e
    tao ruim quanto aprovar errado: o operador para de conseguir distinguir os
    dois casos.

    Klines sao OHLC publico de BTC, nao estado do bot — le-las de outro
    arquivo nao mistura fonte de verdade nenhuma. Se tambem nao houver klines
    ali, devolve (None, None) e o gate reprova, agora com motivo correto.
    """
    p_ini, p_fim = preco_btc_periodo(conn, ph, inicio, fim)
    if p_ini and p_fim:
        return p_ini, p_fim, None

    if not klines_db:
        return None, None, None
    if not os.path.isabs(klines_db):
        klines_db = os.path.join(RAIZ, klines_db)
    if not os.path.exists(klines_db):
        return None, None, None

    alt = sqlite3.connect(klines_db)
    try:
        p_ini, p_fim = preco_btc_periodo(alt, "?", inicio, fim)
    finally:
        alt.close()
    if p_ini and p_fim:
        return p_ini, p_fim, klines_db
    return None, None, None


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Relatório do Gate de Go-Live")
    ap.add_argument("--db", default=None, help="forca um arquivo SQLite especifico")
    ap.add_argument("--sqlite", action="store_true", help="forca o SQLite configurado")
    ap.add_argument("--postgres", action="store_true", help="forca Postgres (exige DATABASE_URL)")
    ap.add_argument("--capital", type=float, default=1000.0, help="capital simulado inicial (USDT)")
    ap.add_argument(
        "--klines-db",
        default=None,
        help="SQLite de onde ler klines para o benchmark quando a fonte nao as tiver "
        "(padrao: o DB_PATH configurado)",
    )
    args = ap.parse_args(argv)

    # O fallback de klines segue a CONFIGURACAO, como tudo mais. Fixa-lo em
    # `data/btc_data.db` faria `--db outro.db` abrir o banco de producao pelas
    # costas — inofensivo por ser leitura, mas e exatamente o tipo de fonte
    # implicita que I-13 existe para eliminar.
    klines_db = args.klines_db or _config()[2]

    conn, ph, fonte = _conectar(args)
    trades = carregar_trades_fechados(conn, ph)

    print("=" * 78)
    print("RELATÓRIO DO GATE — trades fechados (sinais.pnl_usdt IS NOT NULL)")
    print("=" * 78)
    print(f"\nFonte: {fonte}")

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
    p_ini, p_fim, via = benchmark_btc(conn, ph, inicio, fim, klines_db)
    print("\nBenchmark buy-and-hold BTCUSDT no mesmo período:")
    bh = None
    if p_ini and p_fim:
        bh = (p_fim / p_ini - 1) * 100
        print(f"  BTC: {p_ini:.0f} → {p_fim:.0f}  ({bh:+.2f}%)   vs bot: {m['retorno_pct']:+.2f}%")
        if via:
            print(f"  (klines lidas de {via} — a fonte dos trades nao as tem)")
    else:
        print(
            "  Sem klines BTCUSDT/1h cobrindo o período, nem na fonte dos trades nem em "
            f"{klines_db}.\n  Rode: python backtesting/coletar_dados.py"
        )

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
