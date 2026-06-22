"""
Teste de integração do backend Postgres do LoggerBot.

Pula por padrão. Para rodar, exporte BXBOT_TEST_PG_URL apontando para um
Postgres de teste, ex.:
    BXBOT_TEST_PG_URL=postgresql://postgres:senha@127.0.0.1:5432/bxbot

Executa o ciclo completo num SUBPROCESSO isolado (com DATABASE_BACKEND=postgres)
para não contaminar o estado de import da suíte principal (que roda em SQLite).
"""

import os
import subprocess
import sys
import textwrap

import pytest

PG_URL = os.getenv("BXBOT_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="defina BXBOT_TEST_PG_URL para testar o backend Postgres do logger"
)

# Script executado no subprocesso: exercita todas as operações do LoggerBot
# contra o Postgres e confere a contagem de linhas. Sai 0 em sucesso.
_SCRIPT = textwrap.dedent("""
    import sys
    import logger as L
    lb = L.LoggerBot()
    assert lb._pg is True, "deveria estar em modo postgres"
    assert lb._ph == "%s"
    sym = "TESTPGLOGGER"
    lb.registrar_avaliacao(
        {"preco": 101.5, "score": 68, "score_decisao": "OPERAR_REDUZIDO",
         "sinal": "COMPRA", "tend_4h": "ALTA", "rsi": 57.0,
         "score_result": {"bloqueios": ["x"]}}, sym)
    tid = lb.registrar_trade_entrada(sym, "LONG", 101.5, 0.5, 50.0, 99.0, 107.0, 68, 0.61)
    assert tid is not None, "RETURNING id falhou"
    lb.registrar_trade_saida(tid, 106.0, "TP", 22.5, 4.5, 72.5, "tp")
    lb.atualizar_performance_diaria(sym)
    lb.atualizar_performance_diaria(sym)  # 2x -> ON CONFLICT DO UPDATE
    ut = lb.ultimos_trades(5, sym)
    assert len(ut) >= 1 and ut[0]["pnl_usdt"] == 22.5, ut
    # readback direto
    import psycopg
    c = psycopg.connect(L.DATABASE_URL, connect_timeout=8, autocommit=True)
    n = c.execute("select count(*) from log_trades where symbol=%s", (sym,)).fetchone()[0]
    assert n >= 1, n
    # limpeza dos dados de teste
    for t in ("log_avaliacoes", "log_trades"):
        c.execute(f"delete from {t} where symbol=%s", (sym,))
    c.close()
    print("PG_LOGGER_OK")
    """)


def test_logger_postgres_ciclo_completo():
    env = dict(os.environ)
    env["DATABASE_BACKEND"] = "postgres"
    env["DATABASE_URL"] = PG_URL
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr[-800:]!r}"
    assert "PG_LOGGER_OK" in proc.stdout
