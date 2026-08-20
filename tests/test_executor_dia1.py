"""
Testes — cadeia de dia-1 do executor (frente I-10)
===================================================
Nenhum destes caminhos jamais executou com capital real: dez metodos do
executor bifurcam por `if self.simulacao` e o grafo que recebera dinheiro tem
ZERO execucoes. A frente I-10 fecha a sequencia de falhas COMPOSTAS que
transformava a primeira ordem real em posicao desprotegida e permanente:

    comissao nao descontada -> stop rejeitado (-2010) -> abrir_long devolve
    True com a posicao sem protecao -> nada pergunta a exchange -> o monitor
    morre no primeiro SELL rejeitado -> sem kill-switch e sem alerta.

Cada teste aqui corta um elo dessa corrente. Todos herméticos: a rede e
mockada em `_request_assinado`/`_enviar_ordem` e nenhum toca banco de
producao.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as E  # noqa: E402
from executor import Executor  # noqa: E402


class _RiscoFake:
    """Substitui o modulo `gestao_risco` inteiro (mesmo padrao de
    tests/test_executor.py). Trocar o modulo, e nao atributo por atributo,
    garante que nenhum gate real — saldo, drawdown, circuit breaker — decida o
    resultado destes testes."""

    def __init__(self):
        self.resultados = []
        self.travas = []
        self.posicoes_abertas = 0

    def get_saldo_usdt(self):
        return 1000.0

    def validar_trade(self, sinal, preco, capital, **kwargs):
        return {"pode": True, "motivo": "ok"}

    def registrar_resultado(self, pnl_usdt):
        self.resultados.append(pnl_usdt)

    def travar(self, motivo, **kwargs):
        self.travas.append(motivo)

    def persistir_estado(self):
        pass

    def incrementar_posicoes_abertas(self):
        self.posicoes_abertas += 1

    def confirmar_slot_posicao(self, symbol):
        self.posicoes_abertas += 1

    def decrementar_posicoes_abertas(self):
        self.posicoes_abertas = max(0, self.posicoes_abertas - 1)


@pytest.fixture
def risco_fake(monkeypatch):
    fake = _RiscoFake()
    monkeypatch.setattr(E, "gestao_risco", fake)
    return fake


@pytest.fixture
def ex_real(monkeypatch):
    """Executor em modo REAL, sem tocar a rede em nenhum ponto."""
    monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
    ex = Executor(simulacao=False, symbol="BTCUSDT")
    ex._maker_first = False
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0
    return ex


def _pos(ex, **extra):
    base = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "tamanho_btc": 0.01,
        "tamanho_btc_original": 0.01,
        "stop_inicial": 49000.0,
        "stop_atual": 49000.0,
        "target1": 52500.0,
        "target2": 52500.0,
        "parcial_feita": False,
        "abertura": "2026-08-09T12:00:00",
        "order_id": "E-1",
        "stop_order_id": "STOP-1",
        "oco_list_id": None,
        "sinal_id": None,
        "pnl_usdt_parcial_acumulado": 0.0,
    }
    base.update(extra)
    ex.posicao = base
    return base


# ══════════════════════════════════════════════════════════════
# I-10a — estado DESCONHECIDO nao e so timeout de socket
# ══════════════════════════════════════════════════════════════


class TestEstadoDesconhecido:
    """`timeout_rede` gateia a recuperacao de ordem fantasma. Marca-lo so no
    timeout de socket deixava 5xx/429 de fora — e a Binance documenta 5xx como
    estado INDEFINIDO: a ordem pode ter entrado e so a resposta ter sumido."""

    def _resposta(self, ex, monkeypatch, status_code, corpo):
        class _R:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                return corpo

        monkeypatch.setattr(E.time, "sleep", lambda s: None)
        monkeypatch.setattr(E.requests, "request", lambda *a, **k: _R())
        return ex._request_assinado("POST", "/api/v3/order", {"symbol": "BTCUSDT"})

    @pytest.mark.parametrize("codigo_http", [500, 502, 503, 429, 418])
    def test_5xx_e_429_marcam_estado_desconhecido(self, ex_real, monkeypatch, codigo_http):
        r = self._resposta(ex_real, monkeypatch, codigo_http, {"code": -1000, "msg": "erro"})
        assert r["timeout_rede"] is True, f"HTTP {codigo_http} deixou de gatear a recuperacao"

    def test_socket_caido_segue_marcando(self, ex_real, monkeypatch):
        monkeypatch.setattr(E.time, "sleep", lambda s: None)

        def _explode(*a, **k):
            raise OSError("conexao abortada")

        monkeypatch.setattr(E.requests, "request", _explode)
        r = ex_real._request_assinado("POST", "/api/v3/order", {"symbol": "BTCUSDT"})
        assert r["timeout_rede"] is True

    def test_recusa_deterministica_nao_marca(self, ex_real, monkeypatch):
        # -1021 (timestamp fora do recvWindow) e recusa: a exchange validou e
        # rejeitou. Marcar desconhecido aqui dispararia uma consulta inutil a
        # cada erro de relogio.
        chamadas = {"n": 0}

        class _R:
            status_code = 200

            def json(self):
                chamadas["n"] += 1
                return {"code": -1021, "msg": "Timestamp for this request..."}

        monkeypatch.setattr(E.time, "sleep", lambda s: None)
        monkeypatch.setattr(E.requests, "request", lambda *a, **k: _R())
        monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
        r = ex_real._request_assinado("POST", "/api/v3/order", {"symbol": "BTCUSDT"})
        assert r["timeout_rede"] is False
        assert chamadas["n"] == 3  # tentou as 3 vezes, ressincronizando

    def test_erro_de_negocio_nem_chega_no_flag(self, ex_real, monkeypatch):
        # -2010 (saldo insuficiente) volta como resposta normal: a exchange
        # respondeu e recusou. Nao ha nada a recuperar.
        r = self._resposta(ex_real, monkeypatch, 400, {"code": -2010, "msg": "insufficient"})
        assert r == {"code": -2010, "msg": "insufficient"}
        assert "timeout_rede" not in r


# ══════════════════════════════════════════════════════════════
# I-10b — a protecao tambem precisa de recuperacao pos-timeout
# ══════════════════════════════════════════════════════════════


class TestRecuperacaoDaProtecao:
    def test_stop_recuperado_por_client_order_id(self, ex_real, monkeypatch):
        # POST falha em estado desconhecido, mas a ordem EXISTE na exchange.
        # Sem a consulta, o bot perderia o id de um stop vivo.
        visto = {}

        def _req(metodo, path, params, **k):
            if metodo == "POST":
                visto["client_id"] = params["newClientOrderId"]
                return {"erro": "HTTP 503", "timeout_rede": True}
            visto["consultou"] = params.get("origClientOrderId")
            return {"orderId": "STOP-VIVO", "status": "NEW"}

        monkeypatch.setattr(ex_real, "_request_assinado", _req)
        assert ex_real._colocar_stop_exchange(0.01, 49000.0) == "STOP-VIVO"
        assert visto["consultou"] == visto["client_id"]

    def test_stop_nao_existe_devolve_none(self, ex_real, monkeypatch):
        def _req(metodo, path, params, **k):
            if metodo == "POST":
                return {"erro": "HTTP 503", "timeout_rede": True}
            return {"code": -2013, "msg": "Order does not exist."}

        monkeypatch.setattr(ex_real, "_request_assinado", _req)
        assert ex_real._colocar_stop_exchange(0.01, 49000.0) is None

    def test_recusa_deterministica_nao_consulta(self, ex_real, monkeypatch):
        # -1013 (filtro) e recusa: consultar seria um GET a toa em cada
        # rejeicao de precisao.
        consultas = {"n": 0}

        def _req(metodo, path, params, **k):
            if metodo == "POST":
                return {"code": -1013, "msg": "Filter failure: LOT_SIZE"}
            consultas["n"] += 1
            return {}

        monkeypatch.setattr(ex_real, "_request_assinado", _req)
        assert ex_real._colocar_stop_exchange(0.01, 49000.0) is None
        assert consultas["n"] == 0

    def test_oco_recuperado_por_list_client_order_id(self, ex_real, monkeypatch):
        visto = {}

        def _req(metodo, path, params, **k):
            if metodo == "POST":
                visto["list_id"] = params["listClientOrderId"]
                return {"erro": "HTTP 502", "timeout_rede": True}
            visto["consultou"] = params.get("origClientOrderId")
            return {
                "orderListId": 777,
                "orders": [{"orderId": "A"}, {"orderId": "B"}],
                "orderReports": [
                    {"orderId": "A", "type": "LIMIT_MAKER"},
                    {"orderId": "B", "type": "STOP_LOSS_LIMIT"},
                ],
            }

        monkeypatch.setattr(ex_real, "_request_assinado", _req)
        handle = ex_real._colocar_oco_exchange(0.01, 49000.0, 52500.0)
        assert handle["oco_list_id"] == 777
        assert handle["stop_order_id"] == "B"
        assert visto["consultou"] == visto["list_id"]


# ══════════════════════════════════════════════════════════════
# I-10c — comissao em ativo-base
# ══════════════════════════════════════════════════════════════


class TestComissaoEmAtivoBase:
    def test_soma_apenas_a_comissao_no_ativo_comprado(self):
        resp = {
            "fills": [
                {"qty": "0.005", "commission": "0.000005", "commissionAsset": "BTC"},
                {"qty": "0.005", "commission": "0.000005", "commissionAsset": "BTC"},
            ]
        }
        assert Executor.comissao_em_ativo_base(resp, "BTC") == pytest.approx(0.00001)

    def test_comissao_em_bnb_nao_reduz_o_ativo_base(self):
        # Com BNB ligado a taxa sai do saldo de BNB: os 0,01 BTC comprados
        # chegam inteiros e descontar seria dimensionar a MENOS.
        resp = {"fills": [{"qty": "0.01", "commission": "0.002", "commissionAsset": "BNB"}]}
        assert Executor.comissao_em_ativo_base(resp, "BTC") == 0.0

    def test_resposta_sem_fills_nao_quebra(self):
        assert Executor.comissao_em_ativo_base({}, "BTC") == 0.0
        assert Executor.comissao_em_ativo_base({"fills": None}, "BTC") == 0.0
        assert Executor.comissao_em_ativo_base(None, "BTC") == 0.0

    def test_valor_invalido_e_ignorado_sem_derrubar(self):
        resp = {
            "fills": [
                {"commission": "nao-e-numero", "commissionAsset": "BTC"},
                {"commission": "0.00002", "commissionAsset": "BTC"},
            ]
        }
        assert Executor.comissao_em_ativo_base(resp, "BTC") == pytest.approx(0.00002)

    def test_base_asset_derivado_do_symbol(self):
        assert E._base_asset_do_symbol("BTCUSDT") == "BTC"
        assert E._base_asset_do_symbol("ETHUSDT") == "ETH"
        assert E._base_asset_do_symbol("SOLUSDT") == "SOL"
        # a quote mais LONGA tem de vencer: USDT antes de USD
        assert E._base_asset_do_symbol("BTCUSDC") == "BTC"
        assert E._base_asset_do_symbol("ETHBTC") == "ETH"

    def test_posicao_dimensionada_liquida_da_comissao(self, ex_real, risco_fake, monkeypatch):
        # O elo 1 da corrente: sem isto o stop cobre mais BTC do que existe na
        # conta e a exchange devolve -2010.
        ex_real._enviar_ordem = lambda *a, **k: {
            "status": "FILLED",
            "price": 50000.0,
            "orderId": "E-9",
            "executedQty": "0.01000000",
            "fills": [{"commission": "0.00001000", "commissionAsset": "BTC"}],
        }
        qty_protegida = {}
        ex_real._abrir_protecao = lambda qty, stop, alvo: (
            qty_protegida.setdefault("qty", qty),
            {"stop_order_id": "S-1", "oco_list_id": None},
        )[1]
        monkeypatch.setattr(E.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "marcar_sinal_executado", lambda *a, **k: None)
        monkeypatch.setattr(
            E.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: None})()
        )

        assert ex_real.abrir_long(50000.0, 0.01, 49000.0, 52500.0) is True
        # 0,01 - 0,00001 = 0,00999, com floor no step do par
        assert qty_protegida["qty"] == pytest.approx(0.00999)
        assert ex_real.posicao["tamanho_btc"] == pytest.approx(0.00999)


# ══════════════════════════════════════════════════════════════
# I-10d — abrir_long fail-closed
# ══════════════════════════════════════════════════════════════


class TestProtecaoFailClosed:
    def _preparar(self, ex, monkeypatch):
        monkeypatch.setattr(E.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "marcar_sinal_executado", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "salvar_bot_event", lambda *a, **k: None)
        ex._abrir_protecao = lambda qty, stop, alvo: {
            "stop_order_id": None,
            "oco_list_id": None,
        }

    def test_sem_protecao_abrir_long_devolve_false(self, ex_real, risco_fake, monkeypatch):
        self._preparar(ex_real, monkeypatch)
        vendas = []

        def _ordem(lado, qty, tipo=None, preco=None, **k):
            if lado == "BUY":
                return {
                    "status": "FILLED",
                    "price": 50000.0,
                    "orderId": "E-1",
                    "executedQty": "0.01",
                }
            vendas.append((lado, qty))
            return {"status": "FILLED", "price": 49990.0, "orderId": "S-1"}

        ex_real._enviar_ordem = _ordem
        assert ex_real.abrir_long(50000.0, 0.01, 49000.0, 52500.0) is False
        # a posicao NAO pode ficar registrada
        assert ex_real.posicao is None
        # e a compra tem de ter sido desfeita
        assert vendas and vendas[0][0] == "SELL"

    def test_se_o_desfazimento_falhar_o_bot_trava(self, ex_real, risco_fake, monkeypatch):
        self._preparar(ex_real, monkeypatch)
        travas = risco_fake.travas

        def _ordem(lado, qty, tipo=None, preco=None, **k):
            if lado == "BUY":
                return {
                    "status": "FILLED",
                    "price": 50000.0,
                    "orderId": "E-1",
                    "executedQty": "0.01",
                }
            return {"erro": "-2010 insufficient balance"}  # SELL nao preenche

        ex_real._enviar_ordem = _ordem
        assert ex_real.abrir_long(50000.0, 0.01, 49000.0, 52500.0) is False
        assert travas, "posicao real descoberta sem saida NAO travou o bot"
        assert "SEM protecao" in travas[0]

    def test_desfazimento_bem_sucedido_nao_trava(self, ex_real, risco_fake, monkeypatch):
        self._preparar(ex_real, monkeypatch)
        travas = risco_fake.travas
        # `_enviar_ordem` e chamada posicionalmente com 4 args no caminho LIMIT
        # (lado, qty, preco, tipo) e com 3 no MARKET do desfazimento.
        ex_real._enviar_ordem = lambda lado, qty, preco=None, tipo=None, **k: {
            "status": "FILLED",
            "price": 50000.0 if lado == "BUY" else 49990.0,
            "orderId": "X",
            "executedQty": "0.01",
        }
        assert ex_real.abrir_long(50000.0, 0.01, 49000.0, 52500.0) is False
        assert travas == [], "desfazimento OK nao deveria travar o bot"

    def test_em_simulacao_a_protecao_ausente_e_normal(self, risco_fake, monkeypatch):
        # Em paper trading `_abrir_protecao` devolve None/None de proposito —
        # o fail-closed nao pode transformar isso em recusa.
        ex = Executor(simulacao=True, symbol="BTCUSDT")
        ex._monitorar = lambda: None
        ex.get_preco = lambda: 50000.0
        monkeypatch.setattr(E.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "marcar_sinal_executado", lambda *a, **k: None)
        monkeypatch.setattr(
            E.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: None})()
        )
        assert ex.abrir_long(50000.0, 0.01, 49000.0, 52500.0) is True
        assert ex.posicao is not None


# ══════════════════════════════════════════════════════════════
# I-10e — cancelamento que falha NAO pode apagar o id
# ══════════════════════════════════════════════════════════════


class TestLiberarProtecao:
    def test_cancelamento_ok_zera_ids_e_persiste(self, ex_real, monkeypatch):
        _pos(ex_real)
        persistiu = {"n": 0}
        monkeypatch.setattr(ex_real, "_cancelar_ordem_exchange", lambda oid: True)
        monkeypatch.setattr(
            ex_real, "_persistir_posicao", lambda: persistiu.__setitem__("n", persistiu["n"] + 1)
        )
        assert ex_real._liberar_protecao() is True
        assert ex_real.posicao["stop_order_id"] is None
        assert persistiu["n"] == 1

    def test_cancelamento_falho_preserva_os_ids(self, ex_real, monkeypatch):
        # A ordem segue VIVA na exchange. Zerar o id era perde-la de vista:
        # ninguem mais teria como cancela-la, e ela travaria o saldo do SELL.
        _pos(ex_real)
        monkeypatch.setattr(ex_real, "_cancelar_ordem_exchange", lambda oid: False)
        assert ex_real._liberar_protecao() is False
        assert ex_real.posicao["stop_order_id"] == "STOP-1"

    def test_oco_falho_preserva_o_list_id(self, ex_real, monkeypatch):
        _pos(ex_real, oco_list_id=555)
        monkeypatch.setattr(ex_real, "_cancelar_oco_exchange", lambda lid: False)
        assert ex_real._liberar_protecao() is False
        assert ex_real.posicao["oco_list_id"] == 555

    def test_fechamento_adia_quando_nao_consegue_liberar(self, ex_real, monkeypatch):
        # Sem isto, o SELL cairia em -2010 e o ramo de falha chamaria
        # `_abrir_protecao` — criando uma SEGUNDA protecao sobre o mesmo saldo.
        _pos(ex_real)
        monkeypatch.setattr(E.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(ex_real, "_cancelar_ordem_exchange", lambda oid: False)
        chamadas = {"sell": 0, "protecao": 0}

        def _ordem(*a, **k):
            chamadas["sell"] += 1
            return {"status": "FILLED"}

        ex_real._enviar_ordem = _ordem
        ex_real._abrir_protecao = lambda *a: chamadas.__setitem__(
            "protecao", chamadas["protecao"] + 1
        ) or {"stop_order_id": None, "oco_list_id": None}

        ex_real.fechar_posicao(49000.0, "Stop Loss")
        assert chamadas["sell"] == 0, "enviou SELL com a protecao ainda viva"
        assert chamadas["protecao"] == 0, "duplicou a protecao"
        assert ex_real.posicao is not None  # mantida para o proximo ciclo


# ══════════════════════════════════════════════════════════════
# I-10h — fechar_posicao atomica e idempotente
# ══════════════════════════════════════════════════════════════


class TestFechamentoIdempotente:
    def test_reentrancia_nao_contabiliza_duas_vezes(self, ex_real, risco_fake, monkeypatch):
        # O bug: `registrar_resultado` rodava ANTES de `salvar_sinal`, que nao
        # tinha try. Uma excecao no banco propagava, `self.posicao` nunca era
        # zerada, e 10s depois o mesmo tick refazia tudo — somando o MESMO PnL
        # ao drawdown do dia a cada ciclo, para sempre.
        _pos(ex_real)
        resultados = risco_fake.resultados
        monkeypatch.setattr(E.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "atualizar_sinal_fechamento", lambda *a, **k: None)
        monkeypatch.setattr(ex_real, "_liberar_protecao", lambda: True)
        monkeypatch.setattr(ex_real, "_persistir_posicao", lambda: None)

        def _salvar_sinal_quebrado(*a, **k):
            raise RuntimeError("banco fora do ar")

        monkeypatch.setattr(E.database, "salvar_sinal", _salvar_sinal_quebrado)
        ex_real._enviar_ordem = lambda *a, **k: {
            "status": "FILLED",
            "price": 49000.0,
            "orderId": "S-1",
        }

        ex_real.fechar_posicao(49000.0, "Stop Loss")
        # o banco caiu, mas o fechamento se completou e o PnL entrou UMA vez
        assert len(resultados) == 1
        assert ex_real.posicao is None

    def test_chamada_reentrante_e_ignorada(self, ex_real, risco_fake, monkeypatch):
        _pos(ex_real)
        monkeypatch.setattr(ex_real, "_liberar_protecao", lambda: True)
        vendas = {"n": 0}

        def _ordem(*a, **k):
            vendas["n"] += 1
            # reentra no meio do proprio fechamento (o que o monitor faria)
            ex_real.fechar_posicao(49000.0, "Stop Loss")
            return {"status": "FILLED", "price": 49000.0, "orderId": "S-1"}

        ex_real._enviar_ordem = _ordem
        monkeypatch.setattr(E.database, "salvar_sinal", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "atualizar_sinal_fechamento", lambda *a, **k: None)
        monkeypatch.setattr(ex_real, "_persistir_posicao", lambda: None)

        ex_real.fechar_posicao(49000.0, "Stop Loss")
        assert vendas["n"] == 1, "a chamada reentrante enviou um SEGUNDO SELL"


# ══════════════════════════════════════════════════════════════
# I-10g — perguntar a exchange
# ══════════════════════════════════════════════════════════════


class TestReconciliacaoPeriodica:
    def _silenciar(self, monkeypatch):
        monkeypatch.setattr(E.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "salvar_sinal", lambda *a, **k: None)
        monkeypatch.setattr(E.database, "atualizar_sinal_fechamento", lambda *a, **k: None)

    def test_stop_executado_fora_do_bot_e_contabilizado(self, ex_real, risco_fake, monkeypatch):
        # O bot achava que seguia comprado e continuava movendo o stop de uma
        # posicao que nao existia; o PnL nunca era registrado.
        _pos(ex_real)
        self._silenciar(monkeypatch)
        resultados = risco_fake.resultados
        monkeypatch.setattr(ex_real, "_persistir_posicao", lambda: None)
        monkeypatch.setattr(
            ex_real, "_status_ordem", lambda oid: {"status": "FILLED", "price": "49000.0"}
        )
        ex_real._reconciliar_protecao_viva()
        assert ex_real.posicao is None
        assert resultados == [pytest.approx((49000.0 - 50000.0) * 0.01)]

    def test_protecao_cancelada_por_fora_e_recolocada(self, ex_real, risco_fake, monkeypatch):
        _pos(ex_real)
        self._silenciar(monkeypatch)
        monkeypatch.setattr(ex_real, "_persistir_posicao", lambda: None)
        monkeypatch.setattr(ex_real, "_status_ordem", lambda oid: {"status": "CANCELED"})
        ex_real._abrir_protecao = lambda qty, stop, alvo: {
            "stop_order_id": "STOP-NOVO",
            "oco_list_id": None,
        }
        ex_real._reconciliar_protecao_viva()
        assert ex_real.posicao["stop_order_id"] == "STOP-NOVO"

    def test_recolocacao_falha_trava_o_bot(self, ex_real, risco_fake, monkeypatch):
        _pos(ex_real)
        self._silenciar(monkeypatch)
        travas = risco_fake.travas
        monkeypatch.setattr(ex_real, "_persistir_posicao", lambda: None)
        monkeypatch.setattr(ex_real, "_status_ordem", lambda oid: {"status": "CANCELED"})
        ex_real._abrir_protecao = lambda *a: {"stop_order_id": None, "oco_list_id": None}
        ex_real._reconciliar_protecao_viva()
        assert travas, "posicao descoberta sem recolocacao nao travou o bot"

    def test_ordem_ainda_viva_nao_faz_nada(self, ex_real, monkeypatch):
        _pos(ex_real)
        monkeypatch.setattr(ex_real, "_status_ordem", lambda oid: {"status": "NEW"})
        ex_real._reconciliar_protecao_viva()
        assert ex_real.posicao is not None
        assert ex_real.posicao["stop_order_id"] == "STOP-1"

    def test_consulta_que_falha_nao_conclui_nada(self, ex_real, monkeypatch):
        # Uma resposta ausente nao e prova de que a ordem sumiu. Concluir
        # "cancelada" aqui recolocaria protecao em cima de uma ordem viva.
        _pos(ex_real)
        monkeypatch.setattr(ex_real, "_status_ordem", lambda oid: None)
        chamou = {"protecao": False}
        ex_real._abrir_protecao = lambda *a: chamou.__setitem__("protecao", True)
        ex_real._reconciliar_protecao_viva()
        assert chamou["protecao"] is False
        assert ex_real.posicao is not None

    def test_em_simulacao_nao_consulta(self, monkeypatch):
        ex = Executor(simulacao=True, symbol="BTCUSDT")
        _pos(ex)
        chamou = {"n": 0}
        monkeypatch.setattr(ex, "_status_ordem", lambda oid: chamou.__setitem__("n", 1))
        ex._reconciliar_protecao_viva()
        assert chamou["n"] == 0
