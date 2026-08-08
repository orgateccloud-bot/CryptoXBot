"""
Coleta histórico de klines da Binance e salva no banco SQLite.
Suporta múltiplos pares (BTC, ETH, SOL) e até 2 anos de dados.

Uso:
  python backtesting/coletar_dados.py                    → coleta BTC (padrão)
  python backtesting/coletar_dados.py --par ETHUSDT      → coleta ETH
  python backtesting/coletar_dados.py --todos             → coleta BTC + ETH + SOL
  python backtesting/coletar_dados.py --dias 730          → 2 anos de dados
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# I-11: derivado de __file__, nao do cwd. Era "data/btc_data.db" relativo:
# rodar o coletor de qualquer diretorio que nao a raiz CRIAVA um SQLite vazio
# ali (sqlite3.connect cria o arquivo), a coleta "funcionava", e o banco real
# ficava intocado. Falha silenciosa que so aparece quando alguem estranha que
# a pesquisa nao viu os dados novos.
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "btc_data.db"
)
BASE_URL = "https://api.binance.com"

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class ColetaIncompleta(RuntimeError):
    """Coleta interrompida no meio. I-11: uma serie parcial que se apresenta
    como completa e pior que nenhuma serie — a pesquisa mede sobre ela sem
    saber, e o veredito sai errado sem nenhum sinal de que algo falhou."""


TIMEFRAMES = {
    "1h": {"intervalo": "1h", "dias": 730},  # 2 anos
    "4h": {"intervalo": "4h", "dias": 730},  # 2 anos
    "15m": {"intervalo": "15m", "dias": 90},  # 3 meses
    "1d": {"intervalo": "1d", "dias": 730},  # 2 anos
}


def conectar():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def criar_tabela(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT    NOT NULL,
            intervalo  TEXT    NOT NULL,
            timestamp  INTEGER NOT NULL,
            abertura   REAL,
            maxima     REAL,
            minima     REAL,
            fechamento REAL,
            volume     REAL,
            UNIQUE(symbol, intervalo, timestamp)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_klines ON klines(symbol, intervalo, timestamp)")
    conn.commit()


def _ms(data_iso: str, fim_do_dia: bool = False) -> int:
    """'YYYY-MM-DD' -> epoch ms em UTC. Fim do dia = 23:59:59.999."""
    d = datetime.strptime(data_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if fim_do_dia:
        d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(d.timestamp() * 1000)


def baixar_klines(symbol, intervalo, dias=None, inicio_iso=None, fim_iso=None):
    """Baixa klines em lotes de 1000 (limite da API Binance).

    I-11: aceita janela EXPLICITA (inicio_iso/fim_iso). A janela ancorada em
    datetime.now() era a razao pela qual NENHUM veredito era reproduzivel — dois
    dias diferentes coletando "730 dias" produzem series diferentes, e todos os
    labs liam a tabela resultante.

    `dias` continua aceito para uso operacional (atualizar o banco), mas nunca
    deve ser usado para produzir um numero que va a um documento: para isso, use
    a janela explicita e registre-a.
    """
    if inicio_iso and fim_iso:
        inicio = _ms(inicio_iso)
        fim = _ms(fim_iso, fim_do_dia=True)
    elif dias:
        fim = int(datetime.now(timezone.utc).timestamp() * 1000)
        inicio = int((datetime.now(timezone.utc) - timedelta(days=dias)).timestamp() * 1000)
    else:
        raise ValueError("baixar_klines exige --inicio/--fim ou dias")

    todos = []
    atual = inicio
    total_lotes = 0

    while atual < fim:
        params = {
            "symbol": symbol,
            "interval": intervalo,
            "startTime": atual,
            "endTime": fim,
            "limit": 1000,
        }
        try:
            r = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=10)
            r.raise_for_status()
            lote = r.json()
            if not lote:
                break
            todos.extend(lote)
            atual = lote[-1][0] + 1
            total_lotes += 1
            print(
                f"  [{symbol}/{intervalo}] Lote {total_lotes}: {len(lote)} candles "
                f"| Total: {len(todos)} "
                f"| Até: {datetime.fromtimestamp(lote[-1][0]/1000).strftime('%d/%m/%Y')}"
            )
            time.sleep(0.2)
        except Exception as e:
            # I-11: o `break` silencioso devolvia uma serie PARCIAL como se fosse
            # completa, e main() saia com codigo 0. Uma falha de rede no meio da
            # coleta virava "coleta concluida" com metade dos dados — e a
            # pesquisa media sobre isso sem saber.
            print(f"  ERRO no lote {total_lotes + 1}: {e}")
            raise ColetaIncompleta(
                f"{symbol}/{intervalo}: falha apos {total_lotes} lotes "
                f"({len(todos)} candles). A serie esta INCOMPLETA — nao use para "
                f"pesquisa. Causa: {e}"
            ) from e

    # I-11: descarta a vela EM FORMACAO.
    #
    # O ultimo candle devolvido pela Binance quando `fim` e proximo de agora esta
    # aberto: fechamento, maxima, minima e volume ainda vao mudar. E o
    # `INSERT OR IGNORE` de salvar_klines o CONGELA nesse estado parcial para
    # sempre — a proxima coleta encontra a chave ja existente e ignora a versao
    # correta. Havia uma vela permanentemente errada no fim de cada serie.
    #
    # Criterio: o candle esta fechado se `closeTime` (k[6]) ja passou. Usar
    # closeTime em vez de estimar pela duracao do intervalo evita erro em
    # intervalos que a API trata de forma especial (1M, 1w).
    agora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    fechados = [k for k in todos if int(k[6]) < agora_ms]
    if len(fechados) != len(todos):
        print(
            f"  [{symbol}/{intervalo}] Descartadas {len(todos) - len(fechados)} vela(s) "
            f"EM FORMACAO (nao fechadas ainda)."
        )
    return fechados


def salvar_klines(conn, symbol, intervalo, klines):
    inseridos = 0
    for k in klines:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO klines
                (symbol, intervalo, timestamp, abertura, maxima, minima, fechamento, volume)
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    symbol,
                    intervalo,
                    int(k[0]),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                ),
            )
            inseridos += 1
        except Exception:
            pass
    conn.commit()
    return inseridos


def coletar_par(conn, symbol, timeframes_override=None, inicio_iso=None, fim_iso=None):
    """Coleta todos os timeframes para um par."""
    tfs = timeframes_override or TIMEFRAMES
    for nome, cfg in tfs.items():
        if inicio_iso and fim_iso:
            print(f"\n[{symbol}/{nome}] Baixando {inicio_iso} -> {fim_iso} (janela FIXA)...")
        else:
            print(f"\n[{symbol}/{nome}] Baixando {cfg['dias']} dias (janela MOVEL)...")
        klines = baixar_klines(symbol, cfg["intervalo"], cfg.get("dias"), inicio_iso, fim_iso)
        inseridos = salvar_klines(conn, symbol, nome, klines)
        print(f"  Salvo: {inseridos} candles no banco")


def resumo(conn, symbols):
    print("\n" + "=" * 60)
    print("  RESUMO DO BANCO")
    print("=" * 60)
    for symbol in symbols:
        print(f"\n  {symbol}:")
        for nome in TIMEFRAMES:
            count = conn.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            primeiro = conn.execute(
                "SELECT MIN(timestamp) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            ultimo = conn.execute(
                "SELECT MAX(timestamp) FROM klines WHERE symbol=? AND intervalo=?", (symbol, nome)
            ).fetchone()[0]
            if primeiro and ultimo:
                dt_ini = datetime.fromtimestamp(primeiro / 1000).strftime("%d/%m/%Y")
                dt_fim = datetime.fromtimestamp(ultimo / 1000).strftime("%d/%m/%Y")
                print(f"    [{nome:4s}] {count:5d} candles  |  {dt_ini} ate {dt_fim}")
            elif count > 0:
                print(f"    [{nome:4s}] {count:5d} candles")


def main():
    parser = argparse.ArgumentParser(description="Coletor de dados historicos Binance")
    parser.add_argument("--par", default="BTCUSDT", help="Par para coletar (ex: BTCUSDT, ETHUSDT)")
    parser.add_argument("--todos", action="store_true", help="Coletar BTC + ETH + SOL")
    parser.add_argument("--dias", type=int, default=0, help="Override dias de historico")
    # I-11: janela EXPLICITA. Sem ela, "coletar 730 dias" produz uma serie
    # diferente a cada dia em que se roda o comando, e nenhum veredito derivado
    # da tabela e reproduzivel. Nao e obrigatoria porque o coletor tambem serve
    # para uso operacional (manter o banco atualizado) — mas o modo movel agora
    # se ANUNCIA, para ninguem confundir os dois usos.
    parser.add_argument("--inicio", default=None, help="YYYY-MM-DD (UTC) — janela fixa")
    parser.add_argument("--fim", default=None, help="YYYY-MM-DD (UTC) — janela fixa")
    args = parser.parse_args()

    if bool(args.inicio) != bool(args.fim):
        print("ERRO: --inicio e --fim andam juntos (janela fixa exige os dois).")
        return 2
    if args.inicio and args.dias:
        print("ERRO: --dias (janela movel) e --inicio/--fim (janela fixa) sao exclusivos.")
        return 2

    conn = conectar()
    criar_tabela(conn)

    # Override dias se especificado
    tfs = None
    if args.dias > 0:
        tfs = {k: {"intervalo": v["intervalo"], "dias": args.dias} for k, v in TIMEFRAMES.items()}

    if args.todos:
        symbols = PARES
    else:
        symbols = [args.par.upper()]

    print("\n" + "=" * 60)
    print(f"  COLETANDO HISTORICO — {', '.join(symbols)} (Binance)")
    if not args.inicio:
        print("  AVISO: janela MOVEL (ancorada em agora). O resultado NAO e")
        print("         reproduzivel. Para pesquisa, use --inicio/--fim.")
    print("=" * 60)

    try:
        for symbol in symbols:
            coletar_par(conn, symbol, tfs, args.inicio, args.fim)
    except ColetaIncompleta as exc:
        # I-11: exit != 0. Antes o `break` engolia o erro e o processo saia com 0,
        # entao qualquer script/CI encadeado continuava sobre dado parcial.
        print(f"\n  COLETA ABORTADA: {exc}")
        conn.close()
        return 1

    resumo(conn, symbols)
    conn.close()
    print("\n  Dados prontos para backtesting e ML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
