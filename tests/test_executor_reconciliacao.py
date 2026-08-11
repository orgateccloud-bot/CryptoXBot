"""
Testes da reconciliação de boot (auditoria 2026-07-22) — Executor.reconciliar_boot,
reidratar_posicao, e os helpers _saldo_ativo_base/_protecao_aberta_na_exchange/
_ultimo_preco_entrada_mytrades.

Herméticos: sem rede real. `_request_assinado` é mockado por path (dict de
respostas canônicas da Binance) para cada teste. `database`/`gestao_risco` são
mockados no mesmo padrão de test_executor.py/test_executor_oco.py.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as executor_mod
from executor import Executor


@pytest.fixture(autouse=True)
def _precisao_hermetica():
    executor_mod._precisao_cache["BTCUSDT"] = {
        "qty_step": 0.00001,
        "min_qty": 0.00001,
        "price_prec": 2,
        "tick_size": "0.01",
        "step_size": "0.00001",
    }
    yield
    executor_mod._precisao_cache.clear()


@pytest.fixture
def database_mock(monkeypatch):
    class _DBFake:
        def __init__(self):
            self.posicoes = {}
            self.removidas = []
            self.eventos = []

        def carregar_posicoes_abertas(self):
            return dict(self.posicoes)

        def remover_posicao_aberta(self, symbol):
            self.removidas.append(symbol)
            self.posicoes.pop(symbol, None)

        def salvar_posicao_aberta(self, symbol, posicao):
            self.posicoes[symbol] = posicao

        def salvar_bot_event(self, event_type, message, **kwargs):
            self.eventos.append((event_type, message, kwargs))

    fake = _DBFake()
    monkeypatch.setattr(executor_mod, "database", fake)
    return fake


@pytest.fixture
def ex(monkeypatch, database_mock):
    monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
    e = Executor(simulacao=False, symbol="BTCUSDT")
    yield e
    # Nunca deixar um _monitor real (daemon) rodando entre testes — ele chama
    # get_preco() (rede) em loop; encerramos via _ativo=False.
    e._ativo = False


def _mock_request(monkeypatch, ex_obj, respostas):
    """respostas: dict {path: resposta} ou {path: callable(params) -> resposta}.
    GET /api/v3/order (usado por _status_ordem) precisa de dispatch por orderId,
    entao aceita tambem a chave especial "order_by_id": {orderId: resposta}."""

    def _req(metodo, path, params, timeout=10, tentativas=3):
        if path == "/api/v3/order" and "order_by_id" in respostas:
            return respostas["order_by_id"].get(params.get("orderId"), {})
        if path in respostas:
            r = respostas[path]
            return r(params) if callable(r) else r
        return {"erro": f"path nao mockado: {path}"}

    monkeypatch.setattr(ex_obj, "_request_assinado", _req)


# ══════════════════════════════════════════════════════════════
# _saldo_ativo_base
# ══════════════════════════════════════════════════════════════


def test_saldo_ativo_base_simulacao_retorna_zero_sem_rede(monkeypatch):
    ex_sim = Executor(simulacao=True, symbol="BTCUSDT")
    chamado = {"flag": False}
    monkeypatch.setattr(
        ex_sim, "_request_assinado", lambda *a, **k: chamado.update(flag=True) or {}
    )
    assert ex_sim._saldo_ativo_base() == 0.0
    assert chamado["flag"] is False


def test_saldo_ativo_base_soma_free_e_locked(ex, monkeypatch):
    _mock_request(
        monkeypatch,
        ex,
        {
            "/api/v3/account": {
                "balances": [
                    {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                    {"asset": "USDT", "free": "100", "locked": "0"},
                ]
            }
        },
    )
    assert ex._saldo_ativo_base() == pytest.approx(0.6)


def test_saldo_ativo_base_ativo_ausente_retorna_zero(ex, monkeypatch):
    _mock_request(monkeypatch, ex, {"/api/v3/account": {"balances": []}})
    assert ex._saldo_ativo_base() == 0.0


def test_saldo_ativo_base_resposta_invalida_retorna_zero(ex, monkeypatch):
    _mock_request(monkeypatch, ex, {"/api/v3/account": {"erro": "falhou"}})
    assert ex._saldo_ativo_base() == 0.0


# ══════════════════════════════════════════════════════════════
# _protecao_aberta_na_exchange
# ══════════════════════════════════════════════════════════════


def test_protecao_aberta_via_oco(ex, monkeypatch):
    _mock_request(
        monkeypatch,
        ex,
        {
            "/api/v3/openOrders": [],
            "/api/v3/openOrderList": [
                {
                    "orderListId": 1,
                    "symbol": "BTCUSDT",
                    "orders": [
                        {"symbol": "BTCUSDT", "orderId": 10},
                        {"symbol": "BTCUSDT", "orderId": 11},
                    ],
                }
            ],
            "order_by_id": {
                10: {
                    "status": "NEW",
                    "type": "STOP_LOSS_LIMIT",
                    "stopPrice": "49000.0",
                    "origQty": "0.01",
                },
                11: {"status": "NEW", "type": "LIMIT_MAKER", "price": "52000.0", "origQty": "0.01"},
            },
        },
    )
    tem, qty, stop, target = ex._protecao_aberta_na_exchange()
    assert tem is True
    assert qty == pytest.approx(0.01)
    assert stop == pytest.approx(49000.0)
    assert target == pytest.approx(52000.0)


def test_protecao_aberta_via_stop_puro(ex, monkeypatch):
    _mock_request(
        monkeypatch,
        ex,
        {
            "/api/v3/openOrders": [
                {
                    "symbol": "BTCUSDT",
                    "type": "STOP_LOSS_LIMIT",
                    "stopPrice": "48000.0",
                    "origQty": "0.02",
                }
            ],
            "/api/v3/openOrderList": [],
        },
    )
    tem, qty, stop, target = ex._protecao_aberta_na_exchange()
    assert tem is True
    assert qty == pytest.approx(0.02)
    assert stop == pytest.approx(48000.0)
    assert target is None


def test_protecao_aberta_ignora_oco_de_outro_symbol(ex, monkeypatch):
    _mock_request(
        monkeypatch,
        ex,
        {
            "/api/v3/openOrders": [],
            "/api/v3/openOrderList": [
                {
                    "orderListId": 1,
                    "symbol": "ETHUSDT",
                    "orders": [{"symbol": "ETHUSDT", "orderId": 99}],
                }
            ],
        },
    )
    tem, qty, stop, target = ex._protecao_aberta_na_exchange()
    assert tem is False


def test_protecao_aberta_nenhuma_retorna_false(ex, monkeypatch):
    _mock_request(monkeypatch, ex, {"/api/v3/openOrders": [], "/api/v3/openOrderList": []})
    tem, qty, stop, target = ex._protecao_aberta_na_exchange()
    assert (tem, qty, stop, target) == (False, None, None, None)


# ══════════════════════════════════════════════════════════════
# _ultimo_preco_entrada_mytrades
# ══════════════════════════════════════════════════════════════


def test_ultimo_preco_entrada_pega_compra_mais_recente(ex, monkeypatch):
    _mock_request(
        monkeypatch,
        ex,
        {
            "/api/v3/myTrades": [
                {"isBuyer": True, "price": "50000.0", "time": 1000},
                {"isBuyer": False, "price": "51000.0", "time": 2000},
                {"isBuyer": True, "price": "50500.0", "time": 1500},
            ]
        },
    )
    assert ex._ultimo_preco_entrada_mytrades() == pytest.approx(50500.0)


def test_ultimo_preco_entrada_sem_compras_retorna_none(ex, monkeypatch):
    _mock_request(
        monkeypatch, ex, {"/api/v3/myTrades": [{"isBuyer": False, "price": "1.0", "time": 1}]}
    )
    assert ex._ultimo_preco_entrada_mytrades() is None


def test_ultimo_preco_entrada_resposta_invalida_retorna_none(ex, monkeypatch):
    _mock_request(monkeypatch, ex, {"/api/v3/myTrades": {"erro": "falhou"}})
    assert ex._ultimo_preco_entrada_mytrades() is None


# ══════════════════════════════════════════════════════════════
# reidratar_posicao
# ══════════════════════════════════════════════════════════════


def test_reidratar_posicao_liga_monitor(ex, monkeypatch):
    monkeypatch.setattr(ex, "get_preco", lambda: 0.0)  # monitor dorme sem decidir nada
    pos = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52000.0,
        "parcial_feita": False,
        "tamanho_btc": 0.01,
    }
    ex.reidratar_posicao(pos)
    assert ex.posicao == pos
    assert ex._ativo is True
    assert ex._monitor.is_alive()


# ══════════════════════════════════════════════════════════════
# reconciliar_boot — simulacao
# ══════════════════════════════════════════════════════════════


def test_reconciliar_boot_simulacao_com_posicao_religa(monkeypatch, database_mock):
    ex_sim = Executor(simulacao=True, symbol="BTCUSDT")
    monkeypatch.setattr(ex_sim, "get_preco", lambda: 0.0)
    pos = {
        "tipo": "LONG",
        "entrada": 1.0,
        "stop_atual": 1.0,
        "target1": 1.0,
        "target2": 1.0,
        "parcial_feita": True,
        "tamanho_btc": 0.01,
    }
    database_mock.posicoes["BTCUSDT"] = pos
    resultado = ex_sim.reconciliar_boot()
    assert resultado["acao"] == "religou"
    assert ex_sim.posicao == pos
    ex_sim._ativo = False


def test_reconciliar_boot_simulacao_sem_posicao_nao_faz_nada(monkeypatch, database_mock):
    ex_sim = Executor(simulacao=True, symbol="BTCUSDT")
    resultado = ex_sim.reconciliar_boot()
    assert resultado["acao"] == "nada"
    assert ex_sim.posicao is None


# ══════════════════════════════════════════════════════════════
# reconciliar_boot — modo real, os 4 casos
# ══════════════════════════════════════════════════════════════


def test_reconciliar_boot_db_e_exchange_concordam(ex, monkeypatch, database_mock):
    monkeypatch.setattr(ex, "get_preco", lambda: 0.0)
    pos = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52000.0,
        "parcial_feita": False,
        "tamanho_btc": 0.01,
    }
    database_mock.posicoes["BTCUSDT"] = pos
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (True, 0.01, 49000.0, 52000.0))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.01)

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "religou"
    assert ex.posicao == pos
    assert ex._monitor.is_alive()


def test_reconciliar_boot_orfa_recuperada(ex, monkeypatch, database_mock):
    monkeypatch.setattr(ex, "get_preco", lambda: 0.0)
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (True, 0.02, 48000.0, 52500.0))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.02)
    monkeypatch.setattr(ex, "_ultimo_preco_entrada_mytrades", lambda: 50000.0)

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "orfa_recuperada"
    assert ex.posicao["entrada"] == 50000.0
    assert ex.posicao["tamanho_btc"] == pytest.approx(0.02)
    assert ex.posicao["stop_atual"] == pytest.approx(48000.0)
    assert ex.posicao["target2"] == pytest.approx(52500.0)
    assert ex._monitor.is_alive()
    assert "BTCUSDT" in database_mock.posicoes  # persistida
    assert any(e[0] == "reconciliacao_boot_orfa_recuperada" for e in database_mock.eventos)


def test_reconciliar_boot_orfa_nao_reconstruida_sem_entrada(ex, monkeypatch, database_mock):
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (True, 0.02, 48000.0, None))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.02)
    monkeypatch.setattr(ex, "_ultimo_preco_entrada_mytrades", lambda: None)  # myTrades falhou

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "inconsistente"
    assert ex.posicao is None  # NAO religa monitor sem entrada confiavel
    assert any(e[0] == "reconciliacao_boot_orfa_nao_reconstruida" for e in database_mock.eventos)


def test_reconciliar_boot_fechada_fora_do_bot_remove_do_db(ex, monkeypatch, database_mock):
    pos = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52000.0,
        "parcial_feita": False,
        "tamanho_btc": 0.01,
    }
    database_mock.posicoes["BTCUSDT"] = pos
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (False, None, None, None))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.0)  # saldo zerado -- ja vendeu fora

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "removida"
    assert ex.posicao is None
    assert "BTCUSDT" in database_mock.removidas
    assert any(e[0] == "reconciliacao_boot_fechada_fora" for e in database_mock.eventos)


def test_reconciliar_boot_saldo_existe_sem_protecao_religa_e_alerta(ex, monkeypatch, database_mock):
    monkeypatch.setattr(ex, "get_preco", lambda: 0.0)
    pos = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52000.0,
        "parcial_feita": False,
        "tamanho_btc": 0.01,
    }
    database_mock.posicoes["BTCUSDT"] = pos
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (False, None, None, None))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.01)  # saldo ainda existe

    resultado = ex.reconciliar_boot()

    # Conservador: religa do DB em vez de decidir sozinho vender/recriar protecao
    assert resultado["acao"] == "inconsistente"
    assert ex.posicao == pos
    assert ex._monitor.is_alive()
    assert any(e[0] == "reconciliacao_boot_sem_protecao" for e in database_mock.eventos)


def test_reconciliar_boot_nada_quando_ambos_vazios(ex, monkeypatch, database_mock):
    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", lambda: (False, None, None, None))
    monkeypatch.setattr(ex, "_saldo_ativo_base", lambda: 0.0)

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "nada"
    assert ex.posicao is None


def test_reconciliar_boot_falha_de_rede_cai_no_db(ex, monkeypatch, database_mock):
    """Reconciliacao indisponivel (rede fora) NUNCA derruba loop_par -- cai no
    comportamento legado (confia no DB) quando ha posicao salva."""
    monkeypatch.setattr(ex, "get_preco", lambda: 0.0)
    pos = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52000.0,
        "parcial_feita": False,
        "tamanho_btc": 0.01,
    }
    database_mock.posicoes["BTCUSDT"] = pos

    def _explode():
        raise ConnectionError("rede indisponivel")

    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", _explode)

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "religou"
    assert "indisponivel" in resultado["detalhe"]
    assert ex.posicao == pos


def test_reconciliar_boot_falha_de_rede_sem_posicao_nao_falha(ex, monkeypatch, database_mock):
    def _explode():
        raise ConnectionError("rede indisponivel")

    monkeypatch.setattr(ex, "_protecao_aberta_na_exchange", _explode)

    resultado = ex.reconciliar_boot()

    assert resultado["acao"] == "nada"
    assert ex.posicao is None
