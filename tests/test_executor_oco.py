"""
Testes do bracket OCO nativo (P2-1) — executor._colocar_oco_exchange,
_cancelar_oco_exchange, e a abstracao de protecao (_abrir/_liberar/_mover_protecao)
que roteia entre OCO e stop puro.

Herméticos: sem rede. Mockamos `_request_assinado` para simular as respostas da
Binance (POST /api/v3/orderList/oco e DELETE /api/v3/orderList) e inspecionar os
parametros enviados. O caminho de stop puro (OCO desligado) e coberto pelas suites
existentes (test_executor*.py) — aqui o foco e o comportamento COM OCO ligado.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as executor_mod
from executor import _PROTECAO_NAO_MOVIDA, Executor


@pytest.fixture
def gestao_risco_mock(monkeypatch):
    """Mock minimo das funcoes de risco usadas pelo Executor (espelha o de
    test_executor.py — o suficiente p/ os testes de integracao com OCO)."""

    class _RiscoFake:
        def __init__(self):
            self._estado_risco = {"posicoes_abertas": 0}
            self.resultados = []

        def get_saldo_usdt(self):
            return 1000.0

        def validar_trade(self, sinal, preco, capital, atr_relativo=None):
            return {"pode": True, "motivo": "ok"}

        def registrar_resultado(self, pnl_usdt):
            self.resultados.append(pnl_usdt)

        def persistir_estado(self):
            pass

        def incrementar_posicoes_abertas(self):
            self._estado_risco["posicoes_abertas"] += 1

        def decrementar_posicoes_abertas(self):
            self._estado_risco["posicoes_abertas"] = max(
                0, self._estado_risco["posicoes_abertas"] - 1
            )

    fake = _RiscoFake()
    monkeypatch.setattr(executor_mod, "gestao_risco", fake)
    return fake


@pytest.fixture
def database_mock(monkeypatch):
    """Mock minimo de database (salvar_sinal/marcar/atualizar/persistir posicao)."""

    class _DBFake:
        def salvar_sinal(self, *a, **k):
            return 1

        def marcar_sinal_executado(self, *a, **k):
            pass

        def atualizar_sinal_fechamento(self, *a, **k):
            pass

        def salvar_posicao_aberta(self, *a, **k):
            pass

        def remover_posicao_aberta(self, *a, **k):
            pass

    fake = _DBFake()
    monkeypatch.setattr(executor_mod, "database", fake)
    return fake


@pytest.fixture
def ex(monkeypatch):
    """Executor REAL (simulacao=False) com OCO ligado, sem tocar rede."""
    executor_mod._precisao_cache["BTCUSDT"] = {
        "qty_step": 0.00001,
        "min_qty": 0.00001,
        "price_prec": 2,
        "tick_size": "0.01",
        "step_size": "0.00001",
    }
    monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
    e = Executor(simulacao=False, symbol="BTCUSDT")
    e._oco_bracket = True
    e._oco_trailing_bips = 0
    yield e
    executor_mod._precisao_cache.clear()


def _resp_oco_ok(list_id=999, stop_id=222, target_id=111):
    """Resposta canonica de um POST /orderList/oco bem-sucedido."""
    return {
        "orderListId": list_id,
        "listClientOrderId": "bxoco-abc",
        "orders": [
            {"symbol": "BTCUSDT", "orderId": target_id, "clientOrderId": "bxtp-x"},
            {"symbol": "BTCUSDT", "orderId": stop_id, "clientOrderId": "bxsl-y"},
        ],
        "orderReports": [
            {"orderId": target_id, "type": "LIMIT_MAKER", "status": "NEW"},
            {"orderId": stop_id, "type": "STOP_LOSS_LIMIT", "status": "NEW"},
        ],
    }


# ══════════════════════════════════════════════════════════════
# _colocar_oco_exchange
# ══════════════════════════════════════════════════════════════


def test_colocar_oco_monta_pernas_e_parseia_ids(ex, monkeypatch):
    capturado = {}

    def _req(metodo, path, params, **k):
        capturado["metodo"] = metodo
        capturado["path"] = path
        capturado["params"] = params
        return _resp_oco_ok()

    monkeypatch.setattr(ex, "_request_assinado", _req)
    handle = ex._colocar_oco_exchange(0.01, 49000.0, 52000.0)

    assert handle == {"oco_list_id": 999, "stop_order_id": 222, "target_order_id": 111}
    assert capturado["metodo"] == "POST"
    assert capturado["path"] == "/api/v3/orderList/oco"
    p = capturado["params"]
    assert p["symbol"] == "BTCUSDT"
    assert p["side"] == "SELL"
    assert p["quantity"] == pytest.approx(0.01)
    # perna de take-profit ACIMA do mercado
    assert p["aboveType"] == "LIMIT_MAKER"
    assert p["abovePrice"] == pytest.approx(52000.0)
    # perna de stop ABAIXO do mercado, limit price = stop*0.997 snapped
    assert p["belowType"] == "STOP_LOSS_LIMIT"
    assert p["belowStopPrice"] == pytest.approx(49000.0)
    assert p["belowPrice"] == pytest.approx(round(49000.0 * 0.997, 2))
    assert p["belowTimeInForce"] == "GTC"
    # sem trailing por padrao
    assert "belowTrailingDelta" not in p


def test_colocar_oco_com_trailing_bips_anexa_belowTrailingDelta(ex, monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        ex,
        "_request_assinado",
        lambda m, path, params, **k: capturado.update(params) or _resp_oco_ok(),
    )
    ex._colocar_oco_exchange(0.01, 49000.0, 52000.0, trailing_bips=80)
    assert capturado["belowTrailingDelta"] == 80


def test_colocar_oco_qty_abaixo_min_retorna_none(ex, monkeypatch):
    chamou = {"flag": False}
    monkeypatch.setattr(
        ex, "_request_assinado", lambda *a, **k: chamou.update(flag=True) or _resp_oco_ok()
    )
    assert ex._colocar_oco_exchange(0.000001, 49000.0, 52000.0) is None
    assert chamou["flag"] is False  # nem chega a enviar


def test_colocar_oco_simulacao_retorna_none(monkeypatch):
    executor_mod._precisao_cache["BTCUSDT"] = {
        "qty_step": 0.00001,
        "min_qty": 0.00001,
        "price_prec": 2,
        "tick_size": "0.01",
        "step_size": "0.00001",
    }
    e = Executor(simulacao=True, symbol="BTCUSDT")
    e._oco_bracket = True
    assert e._colocar_oco_exchange(0.01, 49000.0, 52000.0) is None
    executor_mod._precisao_cache.clear()


def test_colocar_oco_erro_binance_retorna_none(ex, monkeypatch):
    monkeypatch.setattr(
        ex, "_request_assinado", lambda *a, **k: {"code": -1013, "msg": "Filter failure"}
    )
    assert ex._colocar_oco_exchange(0.01, 49000.0, 52000.0) is None


def test_extrair_pernas_por_tipo():
    handle = Executor._extrair_pernas_oco(_resp_oco_ok(list_id=7, stop_id=50, target_id=40))
    assert handle == {"oco_list_id": 7, "stop_order_id": 50, "target_order_id": 40}


# ══════════════════════════════════════════════════════════════
# _cancelar_oco_exchange
# ══════════════════════════════════════════════════════════════


def test_cancelar_oco_sucesso(ex, monkeypatch):
    capturado = {}

    def _req(metodo, path, params, **k):
        capturado.update(metodo=metodo, path=path, params=params)
        return {"orderListId": 999, "listOrderStatus": "ALL_DONE"}

    monkeypatch.setattr(ex, "_request_assinado", _req)
    assert ex._cancelar_oco_exchange(999) is True
    assert capturado["metodo"] == "DELETE"
    assert capturado["path"] == "/api/v3/orderList"
    assert capturado["params"]["orderListId"] == 999


def test_cancelar_oco_ja_inexistente_2011_e_sucesso(ex, monkeypatch):
    monkeypatch.setattr(ex, "_request_assinado", lambda *a, **k: {"code": -2011, "msg": "Unknown"})
    assert ex._cancelar_oco_exchange(999) is True


def test_cancelar_oco_falha_retorna_false(ex, monkeypatch):
    monkeypatch.setattr(ex, "_request_assinado", lambda *a, **k: {"erro": "rede"})
    assert ex._cancelar_oco_exchange(999) is False


def test_cancelar_oco_none_e_simulacao_sao_noop_true(ex):
    assert ex._cancelar_oco_exchange(None) is True
    ex.simulacao = True
    assert ex._cancelar_oco_exchange(999) is True


# ══════════════════════════════════════════════════════════════
# _abrir_protecao — roteia entre OCO e stop puro
# ══════════════════════════════════════════════════════════════


def test_abrir_protecao_oco_ligado_usa_oco(ex, monkeypatch):
    monkeypatch.setattr(
        ex,
        "_colocar_oco_exchange",
        lambda q, s, t, tb: {"oco_list_id": 5, "stop_order_id": 6, "target_order_id": 7},
    )
    prot = ex._abrir_protecao(0.01, 49000.0, 52000.0)
    assert prot == {"stop_order_id": 6, "oco_list_id": 5}


def test_abrir_protecao_oco_falha_cai_no_stop_puro(ex, monkeypatch):
    monkeypatch.setattr(ex, "_colocar_oco_exchange", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_colocar_stop_exchange", lambda q, s: 321)
    prot = ex._abrir_protecao(0.01, 49000.0, 52000.0)
    assert prot == {"stop_order_id": 321, "oco_list_id": None}


def test_abrir_protecao_oco_desligado_usa_stop_puro(ex, monkeypatch):
    ex._oco_bracket = False
    chamou_oco = {"flag": False}
    monkeypatch.setattr(
        ex,
        "_colocar_oco_exchange",
        lambda *a, **k: chamou_oco.update(flag=True) or {"oco_list_id": 1},
    )
    monkeypatch.setattr(ex, "_colocar_stop_exchange", lambda q, s: 99)
    prot = ex._abrir_protecao(0.01, 49000.0, 52000.0)
    assert prot == {"stop_order_id": 99, "oco_list_id": None}
    assert chamou_oco["flag"] is False


def test_abrir_protecao_sem_target_usa_stop_puro(ex, monkeypatch):
    # sem alvo nao da p/ montar OCO (2 pernas) -> stop puro
    monkeypatch.setattr(ex, "_colocar_stop_exchange", lambda q, s: 77)
    prot = ex._abrir_protecao(0.01, 49000.0, None)
    assert prot == {"stop_order_id": 77, "oco_list_id": None}


# ══════════════════════════════════════════════════════════════
# _liberar_protecao
# ══════════════════════════════════════════════════════════════


def test_liberar_protecao_cancela_oco_quando_presente(ex, monkeypatch):
    ex.posicao = {"oco_list_id": 999, "stop_order_id": 222, "tamanho_btc": 0.01}
    canc = {"oco": None, "stop": None}
    monkeypatch.setattr(ex, "_cancelar_oco_exchange", lambda lid: canc.update(oco=lid) or True)
    monkeypatch.setattr(ex, "_cancelar_ordem_exchange", lambda oid: canc.update(stop=oid) or True)
    ex._liberar_protecao()
    assert canc["oco"] == 999
    assert canc["stop"] is None  # NAO cancela o stop isolado quando ha OCO
    assert ex.posicao["oco_list_id"] is None
    assert ex.posicao["stop_order_id"] is None


def test_liberar_protecao_cancela_stop_puro_quando_sem_oco(ex, monkeypatch):
    ex.posicao = {"oco_list_id": None, "stop_order_id": 222, "tamanho_btc": 0.01}
    canc = {"oco": False, "stop": None}
    monkeypatch.setattr(ex, "_cancelar_oco_exchange", lambda lid: canc.update(oco=True) or True)
    monkeypatch.setattr(ex, "_cancelar_ordem_exchange", lambda oid: canc.update(stop=oid) or True)
    ex._liberar_protecao()
    assert canc["oco"] is False  # sem OCO, nao chama cancelar_oco
    assert canc["stop"] == 222
    assert ex.posicao["stop_order_id"] is None


# ══════════════════════════════════════════════════════════════
# _mover_protecao — trailing / breakeven
# ══════════════════════════════════════════════════════════════


def test_mover_protecao_sem_oco_delega_stop_exchange(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": None,
        "stop_order_id": 5,
        "stop_atual": 49000.0,
        "tamanho_btc": 0.01,
    }
    monkeypatch.setattr(ex, "_mover_stop_exchange", lambda ns: 888)
    assert ex._mover_protecao(49500.0) == 888


def test_mover_protecao_oco_sem_trailing_faz_cancel_replace(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": 999,
        "stop_order_id": 222,
        "stop_atual": 49000.0,
        "target2": 52000.0,
        "tamanho_btc": 0.01,
    }
    ex._oco_trailing_bips = 0
    cancelados = []
    monkeypatch.setattr(ex, "_cancelar_oco_exchange", lambda lid: cancelados.append(lid) or True)
    monkeypatch.setattr(
        ex,
        "_colocar_oco_exchange",
        lambda q, s, t, tb: {"oco_list_id": 1000, "stop_order_id": 333, "target_order_id": 334},
    )
    res = ex._mover_protecao(49500.0)
    assert cancelados == [999]  # cancelou o OCO antigo
    assert res == 333  # novo stop id
    assert ex.posicao["oco_list_id"] == 1000  # novo list id gravado


def test_mover_protecao_oco_com_trailing_server_side_nao_mexe(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": 999,
        "stop_order_id": 222,
        "stop_atual": 49000.0,
        "target2": 52000.0,
        "tamanho_btc": 0.01,
    }
    ex._oco_trailing_bips = 80  # trailing server-side ligado
    chamou = {"cancel": False}
    monkeypatch.setattr(
        ex, "_cancelar_oco_exchange", lambda lid: chamou.update(cancel=True) or True
    )
    res = ex._mover_protecao(49500.0)
    assert res is _PROTECAO_NAO_MOVIDA
    assert chamou["cancel"] is False  # servidor trilha -> nao toca o OCO
    assert ex.posicao["oco_list_id"] == 999  # inalterado


def test_mover_protecao_cancel_falha_mantem_oco_antigo(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": 999,
        "stop_order_id": 222,
        "stop_atual": 49000.0,
        "target2": 52000.0,
        "tamanho_btc": 0.01,
    }
    monkeypatch.setattr(ex, "_cancelar_oco_exchange", lambda lid: False)
    recolocou = {"flag": False}
    monkeypatch.setattr(
        ex, "_colocar_oco_exchange", lambda *a, **k: recolocou.update(flag=True) or {}
    )
    res = ex._mover_protecao(49500.0)
    assert res is _PROTECAO_NAO_MOVIDA
    assert recolocou["flag"] is False  # nao recoloca se nao cancelou
    assert ex.posicao["oco_list_id"] == 999  # antigo preservado


def test_mover_protecao_replace_falha_restaura_nivel_antigo(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": 999,
        "stop_order_id": 222,
        "stop_atual": 49000.0,
        "target2": 52000.0,
        "tamanho_btc": 0.01,
    }
    monkeypatch.setattr(ex, "_cancelar_oco_exchange", lambda lid: True)
    tentativas = []

    def _abrir(q, stop, target):
        tentativas.append(stop)
        if len(tentativas) == 1:
            return {"stop_order_id": None, "oco_list_id": None}  # 1a tentativa falha
        return {"stop_order_id": 444, "oco_list_id": 1001}  # restauracao no nivel antigo

    monkeypatch.setattr(ex, "_abrir_protecao", _abrir)
    res = ex._mover_protecao(49500.0)
    assert tentativas == [49500.0, 49000.0]  # tenta novo, depois restaura antigo
    assert res == 444
    assert ex.posicao["oco_list_id"] == 1001


# ══════════════════════════════════════════════════════════════
# Integracao — abrir_long / fechar_posicao com OCO
# ══════════════════════════════════════════════════════════════


def test_abrir_long_com_oco_persiste_list_id_e_alvo_e_target2(
    ex, monkeypatch, gestao_risco_mock, database_mock
):
    ex._maker_first = False
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0
    monkeypatch.setattr(
        ex, "_enviar_ordem", lambda *a, **k: {"status": "FILLED", "price": 50000.0, "orderId": "R1"}
    )
    capt = {}
    monkeypatch.setattr(
        ex,
        "_abrir_protecao",
        lambda q, s, t: capt.update(qty=q, stop=s, target=t)
        or {"stop_order_id": 222, "oco_list_id": 999},
    )
    ok = ex.abrir_long(50000.0, 0.01, 49000.0, 51000.0)
    assert ok is True
    # o alvo do bracket e target2 (runner = entrada*1.05), nao target1
    assert capt["target"] == pytest.approx(50000.0 * 1.05)
    assert capt["stop"] == 49000.0
    assert ex.posicao["oco_list_id"] == 999
    assert ex.posicao["stop_order_id"] == 222


def test_fechar_posicao_libera_oco_antes_do_sell(ex, monkeypatch, gestao_risco_mock, database_mock):
    ex._monitorar = lambda: None
    ex.posicao = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "tamanho_btc": 0.01,
        "tamanho_btc_original": 0.01,
        "stop_atual": 49000.0,
        "target2": 52500.0,
        "parcial_feita": False,
        "oco_list_id": 999,
        "stop_order_id": 222,
        "sinal_id": None,
        "pnl_usdt_parcial_acumulado": 0.0,
    }
    ex._ativo = True
    ordem = {"liberou_oco": False, "vendeu": False}

    def _liberar():
        ordem["liberou_oco"] = True
        ex.posicao["oco_list_id"] = None
        ex.posicao["stop_order_id"] = None

    monkeypatch.setattr(ex, "_liberar_protecao", _liberar)

    def _enviar(*a, **k):
        # a protecao ja deve ter sido liberada antes do SELL
        assert ordem["liberou_oco"] is True
        ordem["vendeu"] = True
        return {"status": "FILLED", "price": 52500.0, "orderId": "S1"}

    monkeypatch.setattr(ex, "_enviar_ordem", _enviar)
    ex.fechar_posicao(52500.0, "Take Profit Final")
    assert ordem["vendeu"] is True
    assert ex.posicao is None


def test_aplicar_novo_stop_server_trailing_nao_mexe_stop_atual(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": 999,
        "stop_atual": 49000.0,
        "stop_order_id": 222,
        "tamanho_btc": 0.01,
    }
    monkeypatch.setattr(ex, "_mover_protecao", lambda ns: _PROTECAO_NAO_MOVIDA)
    persistiu = {"flag": False}
    monkeypatch.setattr(ex, "_persistir_posicao", lambda: persistiu.update(flag=True))
    ex._aplicar_novo_stop(49500.0, "Trailing Stop: ${v:,.2f}")
    assert ex.posicao["stop_atual"] == 49000.0  # inalterado (servidor e dono)
    assert persistiu["flag"] is False


def test_aplicar_novo_stop_move_normal_atualiza_stop_atual(ex, monkeypatch):
    ex.posicao = {
        "oco_list_id": None,
        "stop_atual": 49000.0,
        "stop_order_id": 222,
        "tamanho_btc": 0.01,
    }
    monkeypatch.setattr(ex, "_mover_protecao", lambda ns: 555)
    monkeypatch.setattr(ex, "_persistir_posicao", lambda: None)
    ex._aplicar_novo_stop(49500.0, "Trailing Stop: ${v:,.2f}")
    assert ex.posicao["stop_atual"] == 49500.0
    assert ex.posicao["stop_order_id"] == 555
