"""
Testes do LoggerBot — foco no fix de compatibilidade com logging.Logger.

main.py importa `from logger import logger` e usa o MESMO objeto para logs
operacionais (.warning/.error/.critical) e para analytics (registrar_avaliacao).
Antes do fix, os métodos de logging não existiam no LoggerBot → AttributeError
nos caminhos de erro do WebSocket. Estes testes garantem que delegam e que o
analytics continua funcionando.
"""

from unittest.mock import MagicMock

import pytest

import logger as L
from logger import LoggerBot


@pytest.fixture
def lb(tmp_path):
    return LoggerBot(db_path=str(tmp_path / "t.db"), log_dir=str(tmp_path / "logs"))


# ── Compat logging.Logger ──────────────────────────────────────────────────


@pytest.mark.parametrize("metodo", ["info", "warning", "error", "critical", "debug"])
def test_metodos_de_logging_existem_e_nao_lancam(lb, metodo):
    # exatamente o padrão de uso de main.py: msg + extra=dict
    getattr(lb, metodo)("mensagem", extra={"symbol": "BTCUSDT", "attempt": 1})


@pytest.mark.parametrize("metodo", ["info", "warning", "error", "critical", "debug"])
def test_delega_para_stdlog(lb, metodo, monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(L, "_stdlog", fake)
    getattr(lb, metodo)("oi", extra={"x": 1})
    getattr(fake, metodo).assert_called_once_with("oi", extra={"x": 1})


def test_instancia_global_tem_ambas_as_interfaces():
    # o objeto exportado (main.py: from logger import logger) deve ter as duas faces
    assert callable(getattr(L.logger, "warning"))
    assert callable(getattr(L.logger, "registrar_avaliacao"))


# ── Analytics continua funcionando ─────────────────────────────────────────


def test_registrar_avaliacao_grava_linha(lb):
    import sqlite3

    resultado = {
        "preco": 100.0,
        "score": 72,
        "score_decisao": "OPERAR_CHEIO",
        "sinal": "COMPRA",
        "tamanho_fator": 1.0,
        "regime": "TENDENCIA_ALTA",
        "fear_greed": 55,
        "tend_4h": "ALTA",
        "rsi": 55.0,
        "ema20_1h": 100.0,
        "ema50_1h": 99.0,
        "vwap": 100.0,
        "atr": 1.0,
        "vol_rel": 1.2,
    }
    # não deve lançar mesmo com chaves faltando (usa .get internamente)
    lb.registrar_avaliacao(resultado, symbol="BTCUSDT")
    conn = sqlite3.connect(lb.db_path)
    n = conn.execute("SELECT COUNT(*) FROM log_avaliacoes WHERE symbol='BTCUSDT'").fetchone()[0]
    conn.close()
    assert n >= 1


def test_tabelas_criadas_no_init(lb):
    import sqlite3

    conn = sqlite3.connect(lb.db_path)
    tabelas = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert {"log_avaliacoes", "log_trades", "log_performance"} <= tabelas
