"""
Testes da entrada MAKER-FIRST / post-only (P0-2) — executor._entrar_maker.

Herméticos: sem rede. Mockamos `_melhor_bid`, `_enviar_ordem`, `_status_ordem`
e `_cancelar_ordem_exchange` para simular o ciclo de vida real da ordem
LIMIT_MAKER (descansa no book → preenche / não preenche / é recusada).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as executor_mod
from executor import Executor


@pytest.fixture(autouse=True)
def _telegram_hermetico(monkeypatch):
    """abrir_long agora chama telegram_bot.alerta_trade_aberto -- mockar
    _enviar evita rede real (ver mesma fixture em tests/test_executor.py)."""
    monkeypatch.setattr(executor_mod.telegram_bot, "_enviar", lambda *a, **k: True)


@pytest.fixture
def ex(monkeypatch):
    """Executor REAL (simulacao=False) sem tocar rede: precisão pré-cacheada e
    sync de relógio neutralizado. Timeouts minúsculos p/ o teste ser rápido."""
    executor_mod._precisao_cache["BTCUSDT"] = {
        "qty_step": 0.00001,
        "min_qty": 0.00001,
        "price_prec": 2,
        "tick_size": "0.01",
        "step_size": "0.00001",
    }
    monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
    e = Executor(simulacao=False, symbol="BTCUSDT")
    e._maker_timeout_s = 0.01
    e._maker_poll_s = 0.0
    e._maker_max_requotes = 2
    e._cancelados = []
    monkeypatch.setattr(
        e, "_cancelar_ordem_exchange", lambda oid: e._cancelados.append(oid) or True
    )
    monkeypatch.setattr(e, "_melhor_bid", lambda: 60000.0)
    yield e
    executor_mod._precisao_cache.clear()


def test_fill_total_na_primeira_cotacao(ex, monkeypatch):
    monkeypatch.setattr(
        ex, "_enviar_ordem", lambda *a, **k: {"orderId": 1, "status": "NEW", "executedQty": 0}
    )
    monkeypatch.setattr(
        ex,
        "_status_ordem",
        lambda oid: {"status": "FILLED", "executedQty": 0.01, "cummulativeQuoteQty": 600.0},
    )
    r = ex._entrar_maker(0.01)
    assert r["status"] == "FILLED"
    assert r["executedQty"] == pytest.approx(0.01)
    assert r["price"] == pytest.approx(60000.0)  # 600 / 0.01
    assert r["maker"] is True
    assert ex._cancelados == []  # nada a cancelar quando preenche


def test_nao_preenche_cancela_e_requota_ate_preencher(ex, monkeypatch):
    enviadas = []

    def _enviar(lado, qty, preco, tipo):
        enviadas.append((qty, preco, tipo))
        return {"orderId": len(enviadas), "status": "NEW", "executedQty": 0}

    # 1a cotacao: fica NEW (nao preenche) → cancela e re-quota; 2a: FILLED
    respostas = [
        {"status": "NEW", "executedQty": 0},
        {"status": "FILLED", "executedQty": 0.01, "cummulativeQuoteQty": 600.0},
    ]
    monkeypatch.setattr(ex, "_enviar_ordem", _enviar)
    monkeypatch.setattr(ex, "_status_ordem", lambda oid: respostas[oid - 1])

    r = ex._entrar_maker(0.01)
    assert r["status"] == "FILLED"
    assert len(enviadas) == 2  # houve re-quote
    assert ex._cancelados == [1]  # a 1a ordem foi cancelada
    assert all(t == "LIMIT_MAKER" for _q, _p, t in enviadas)


def test_limit_maker_recusado_2010_requota(ex, monkeypatch):
    tentativas = []

    def _enviar(lado, qty, preco, tipo):
        tentativas.append(preco)
        if len(tentativas) == 1:
            # Binance recusa: cruzaria o book (post-only)
            return {"erro": "Order would immediately match and take.", "code": -2010}
        return {"orderId": 9, "status": "NEW", "executedQty": 0}

    monkeypatch.setattr(ex, "_enviar_ordem", _enviar)
    monkeypatch.setattr(
        ex,
        "_status_ordem",
        lambda oid: {"status": "FILLED", "executedQty": 0.01, "cummulativeQuoteQty": 600.0},
    )
    r = ex._entrar_maker(0.01)
    assert r["status"] == "FILLED"
    assert len(tentativas) == 2  # recusa nao aborta: re-quota


def test_fill_parcial_acumula_entre_requotes(ex, monkeypatch):
    def _enviar(lado, qty, preco, tipo):
        _enviar.pedidos.append(qty)
        return {"orderId": len(_enviar.pedidos), "status": "NEW", "executedQty": 0}

    _enviar.pedidos = []
    # 1a: preenche metade e expira; 2a: preenche o resto
    respostas = [
        {"status": "EXPIRED", "executedQty": 0.004, "cummulativeQuoteQty": 240.0},
        {"status": "FILLED", "executedQty": 0.006, "cummulativeQuoteQty": 360.0},
    ]
    monkeypatch.setattr(ex, "_enviar_ordem", _enviar)
    monkeypatch.setattr(ex, "_status_ordem", lambda oid: respostas[oid - 1])

    r = ex._entrar_maker(0.01)
    assert r["status"] == "FILLED"
    assert r["executedQty"] == pytest.approx(0.01)  # 0.004 + 0.006
    assert r["price"] == pytest.approx(60000.0)  # (240+360) / 0.01
    # a 2a cotacao pediu só o RESTANTE
    assert _enviar.pedidos[1] == pytest.approx(0.006)


def test_nunca_preenche_retorna_erro_e_nao_abre_posicao(ex, monkeypatch):
    monkeypatch.setattr(
        ex, "_enviar_ordem", lambda *a, **k: {"orderId": 1, "status": "NEW", "executedQty": 0}
    )
    monkeypatch.setattr(ex, "_status_ordem", lambda oid: {"status": "NEW", "executedQty": 0})
    r = ex._entrar_maker(0.01)
    assert "erro" in r
    assert r["executedQty"] == 0.0
    # cancelou cada cotacao que ficou pendurada (1 inicial + 2 re-quotes)
    assert len(ex._cancelados) == 3


def test_book_indisponivel_aborta_sem_enviar_ordem(ex, monkeypatch):
    monkeypatch.setattr(ex, "_melhor_bid", lambda: 0.0)
    chamou = []
    monkeypatch.setattr(ex, "_enviar_ordem", lambda *a, **k: chamou.append(1))
    r = ex._entrar_maker(0.01)
    assert "erro" in r and "bid=0" in r["erro"]
    assert chamou == []  # nao arrisca ordem sem preco de referencia


def test_abrir_long_real_usa_maker_e_adota_qty_executado(ex, monkeypatch):
    # maker preenche PARCIAL (0.006 de 0.01) -> posicao e stop usam 0.006
    monkeypatch.setattr(
        ex,
        "_entrar_maker",
        lambda q: {
            "status": "FILLED",
            "executedQty": 0.006,
            "price": 60000.0,
            "maker": True,
        },
    )
    stops = []
    monkeypatch.setattr(ex, "_colocar_stop_exchange", lambda q, p: stops.append(q) or 777)
    monkeypatch.setattr(executor_mod.gestao_risco, "get_saldo_usdt", lambda: 10000.0)
    monkeypatch.setattr(
        executor_mod.gestao_risco, "validar_trade", lambda *a, **k: {"pode": True, "motivo": "ok"}
    )
    monkeypatch.setattr(executor_mod.gestao_risco, "persistir_estado", lambda: None)
    monkeypatch.setattr(executor_mod.gestao_risco, "_estado_risco", {"posicoes_abertas": 0})
    monkeypatch.setattr(executor_mod.database, "salvar_posicao_aberta", lambda *a, **k: None)
    ex._monitorar = lambda: None

    ok = ex.abrir_long(60000.0, 0.01, 59000.0, 61000.0)
    assert ok is True
    # posicao reflete o EXECUTADO (0.006), nao o pedido (0.01)
    assert ex.posicao["tamanho_btc"] == pytest.approx(0.006)
    assert ex.posicao["entrada"] == pytest.approx(60000.0)
    # o stop na exchange cobre exatamente o executado
    assert stops == [pytest.approx(0.006)]


def test_simulacao_nao_usa_maker(monkeypatch):
    # Em simulação o caminho maker não é acionado (fill imediato como antes).
    executor_mod._precisao_cache["BTCUSDT"] = {
        "qty_step": 0.00001,
        "min_qty": 0.00001,
        "price_prec": 2,
        "tick_size": "0.01",
        "step_size": "0.00001",
    }
    e = Executor(simulacao=True, symbol="BTCUSDT")
    chamou = []
    monkeypatch.setattr(e, "_entrar_maker", lambda q: chamou.append(1))
    monkeypatch.setattr(e, "get_preco", lambda: 60000.0)
    monkeypatch.setattr(executor_mod.gestao_risco, "get_saldo_usdt", lambda: 1000.0)
    monkeypatch.setattr(executor_mod.database, "salvar_posicao_aberta", lambda *a, **k: None)
    monkeypatch.setattr(executor_mod.gestao_risco, "persistir_estado", lambda: None)
    e._monitorar = lambda: None
    ok = e.abrir_long(60000.0, 0.01, 59000.0, 61000.0)
    assert ok is True
    assert chamou == []  # maker NAO acionado em simulacao
    executor_mod._precisao_cache.clear()
