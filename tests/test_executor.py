"""
Testes — Executor de Ordens (executor.Executor)
================================================
@Delta — QA Automation. Suíte hermética (SEM rede, SEM banco).

Todos os colaboradores externos são mockados:
  - executor.requests          (preço / envio de ordem real)
  - executor.gestao_risco.*    (saldo, registrar_resultado, persistir_estado, _estado_risco)
  - executor.database.salvar_sinal

Cobertura:
  - _arredondar_qty / _arredondar_preco (precisão por par)
  - _enviar_ordem em simulação (FILLED, SIM-...) e abaixo de min_qty (erro)
  - abrir_long sucesso (cria self.posicao LONG) e guard "já existe posição"
  - fechar_posicao parcial (mantém posição, metade do tamanho, parcial_feita=True)
  - fechar_posicao total (posicao=None, _ativo=False)
  - FIX P1-8: simulacao=False + ordem sem FILLED => mantém posição, sem registrar PnL
  - existência de self._lock (threading.Lock)
  - status() com e sem posição
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as executor_mod
from executor import Executor


@pytest.fixture(autouse=True)
def _precisao_hermetica():
    """Pre-popula o cache de precisao com os valores reais do spot, evitando a
    chamada de rede ao exchangeInfo no __init__ do Executor (mantem a suite
    hermetica). Valores = LOT_SIZE/PRICE_FILTER reais da Binance spot."""
    executor_mod._precisao_cache.update(
        {
            "BTCUSDT": {
                "qty_step": 0.00001,
                "min_qty": 0.00001,
                "price_prec": 2,
                "tick_size": "0.01",
                "step_size": "0.00001",
            },
            "ETHUSDT": {
                "qty_step": 0.0001,
                "min_qty": 0.0001,
                "price_prec": 2,
                "tick_size": "0.01",
                "step_size": "0.0001",
            },
            "SOLUSDT": {
                "qty_step": 0.001,
                "min_qty": 0.001,
                "price_prec": 2,
                "tick_size": "0.01",
                "step_size": "0.001",
            },
        }
    )
    yield
    executor_mod._precisao_cache.clear()


# ══════════════════════════════════════════════════════════════
# Fixtures: mocks dos colaboradores externos
# ══════════════════════════════════════════════════════════════


@pytest.fixture
def gestao_risco_mock(monkeypatch):
    """Mocka todas as funções de risco usadas pelo Executor."""

    class _RiscoFake:
        def __init__(self):
            self._estado_risco = {"posicoes_abertas": 0}
            self.resultados = []
            self.persistido = 0

        def get_saldo_usdt(self):
            return 1000.0

        def validar_trade(self, sinal, preco, capital):
            return {"pode": True, "motivo": "ok"}

        def registrar_resultado(self, pnl_usdt):
            self.resultados.append(pnl_usdt)

        def persistir_estado(self):
            self.persistido += 1

    fake = _RiscoFake()
    monkeypatch.setattr(executor_mod, "gestao_risco", fake)
    return fake


@pytest.fixture
def database_mock(monkeypatch):
    """Mocka database.salvar_sinal capturando as chamadas."""

    class _DBFake:
        def __init__(self):
            self.chamadas = []

        def salvar_sinal(self, *args, **kwargs):
            self.chamadas.append((args, kwargs))

    fake = _DBFake()
    monkeypatch.setattr(executor_mod, "database", fake)
    return fake


@pytest.fixture
def ex_sim(gestao_risco_mock, database_mock, monkeypatch):
    """Executor em SIMULAÇÃO, preço fixo, monitor desativado (sem thread real)."""
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    # Evita thread real de monitoramento (monkeypatch de instância).
    ex._monitorar = lambda: None
    # Preço fixo determinístico.
    ex.get_preco = lambda: 50000.0
    return ex


# ══════════════════════════════════════════════════════════════
# Arredondamento (precisão por par)
# ══════════════════════════════════════════════════════════════


def test_lock_existe():
    ex = Executor(simulacao=True)
    assert isinstance(ex._lock, type(threading.Lock()))


def test_precisao_btcusdt():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    assert ex._qty_step == 0.00001
    assert ex._min_qty == 0.00001
    assert ex._price_prec == 2


def test_arredondar_qty_btc():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    # step 0.00001 -> trunca para o múltiplo inferior
    assert ex._arredondar_qty(0.0012349) == 0.00123
    assert ex._arredondar_qty(0.000019) == 0.00001


def test_arredondar_preco_btc():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    # price_prec = 2
    assert ex._arredondar_preco(50000.12345) == 50000.12


def test_precisao_fallback_quando_exchangeinfo_falha(monkeypatch):
    # exchangeInfo indisponivel + simbolo fora do _PRECISAO -> _PRECISAO_DEFAULT
    executor_mod._precisao_cache.pop("DOGEUSDT", None)

    def _sem_rede(*a, **k):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(executor_mod.requests, "get", _sem_rede)
    ex = Executor(simulacao=True, symbol="DOGEUSDT")
    assert ex._qty_step == 0.001
    assert ex._min_qty == 0.001
    assert ex._price_prec == 2


# ══════════════════════════════════════════════════════════════
# _enviar_ordem em simulação
# ══════════════════════════════════════════════════════════════


def test_enviar_ordem_simulacao_filled(ex_sim):
    resp = ex_sim._enviar_ordem("BUY", 0.001, preco=50000.0, tipo="LIMIT")
    assert resp["status"] == "FILLED"
    assert str(resp["orderId"]).startswith("SIM-")
    assert resp["simulacao"] is True
    assert resp["price"] == 50000.0


def test_enviar_ordem_simulacao_usa_get_preco_quando_sem_preco(ex_sim):
    resp = ex_sim._enviar_ordem("SELL", 0.001, tipo="MARKET")
    assert resp["status"] == "FILLED"
    # sem preço informado, usa get_preco (mockado para 50000.0)
    assert resp["price"] == 50000.0


def test_enviar_ordem_abaixo_min_qty_retorna_erro(ex_sim):
    # 0.000001 < min_qty 0.00001 -> após arredondar vira 0.0
    resp = ex_sim._enviar_ordem("BUY", 0.000001, preco=50000.0)
    assert "erro" in resp
    assert "status" not in resp


# ══════════════════════════════════════════════════════════════
# abrir_long
# ══════════════════════════════════════════════════════════════


def test_abrir_long_sucesso(ex_sim, gestao_risco_mock):
    ok = ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ok is True
    assert ex_sim.posicao is not None
    pos = ex_sim.posicao
    assert pos["tipo"] == "LONG"
    assert pos["tamanho_btc"] == 0.001
    assert pos["stop_inicial"] == 49000.0
    assert pos["stop_atual"] == 49000.0
    assert pos["target1"] == 51000.0
    assert pos["parcial_feita"] is False
    # entrada == preço executado (em simulação, preco_limit = entrada*1.001 arredondado)
    assert pos["entrada"] == ex_sim._arredondar_preco(50000.0 * 1.001)
    # estado de risco incrementado e persistido
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 1
    assert gestao_risco_mock.persistido >= 1


def test_abrir_long_guard_posicao_existente(ex_sim):
    assert ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0) is True
    primeira = ex_sim.posicao
    # Segunda chamada com posição aberta -> False e não substitui posição
    assert ex_sim.abrir_long(48000.0, 0.002, 47000.0, 52000.0) is False
    assert ex_sim.posicao is primeira


# ══════════════════════════════════════════════════════════════
# fechar_posicao — parcial e total (simulação)
# ══════════════════════════════════════════════════════════════


def test_fechar_posicao_parcial(ex_sim):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    tamanho_antes = ex_sim.posicao["tamanho_btc"]

    ex_sim.fechar_posicao(51000.0, "Take Profit Parcial", parcial=True)

    assert ex_sim.posicao is not None
    assert ex_sim.posicao["parcial_feita"] is True
    assert ex_sim.posicao["tamanho_btc"] == pytest.approx(tamanho_antes / 2)
    assert ex_sim._ativo is True


def test_fechar_posicao_total(ex_sim, gestao_risco_mock, database_mock):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 1

    ex_sim.fechar_posicao(51000.0, "Take Profit Final", parcial=False)

    assert ex_sim.posicao is None
    assert ex_sim._ativo is False
    # PnL registrado e sinal salvo
    assert len(gestao_risco_mock.resultados) == 1
    assert len(database_mock.chamadas) == 1
    # contador de posições decrementado
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 0


def test_fechar_posicao_sem_posicao_e_noop(ex_sim, gestao_risco_mock, database_mock):
    # Sem posição aberta -> retorna None sem efeitos colaterais
    assert ex_sim.posicao is None
    assert ex_sim.fechar_posicao(50000.0, "x") is None
    assert gestao_risco_mock.resultados == []
    assert database_mock.chamadas == []


# ══════════════════════════════════════════════════════════════
# FIX DE SEGURANÇA P1-8 (CRÍTICO)
# ══════════════════════════════════════════════════════════════


def test_p1_8_fechar_real_sem_filled_mantem_posicao(gestao_risco_mock, database_mock, monkeypatch):
    """
    Com simulacao=False, se _enviar_ordem retornar algo SEM status FILLED,
    fechar_posicao deve MANTER a posição e NÃO registrar PnL.
    """
    ex = Executor(simulacao=False, symbol="BTCUSDT")
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0

    # Injeta posição manualmente (evita o fluxo real de abertura).
    ex.posicao = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "tamanho_btc": 0.001,
        "stop_inicial": 49000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52500.0,
        "parcial_feita": False,
        "abertura": "2026-06-20T00:00:00",
        "order_id": "REAL-1",
    }
    ex._ativo = True

    # Ordem real "falha": retorna erro, sem FILLED.
    ex._enviar_ordem = lambda *a, **k: {"erro": "x"}

    ret = ex.fechar_posicao(51000.0, "Take Profit Final", parcial=False)

    # Posição MANTIDA
    assert ex.posicao is not None
    assert ex._ativo is True
    assert ret is None
    # Nenhum PnL registrado e nenhum sinal salvo
    assert gestao_risco_mock.resultados == []
    assert database_mock.chamadas == []


def test_p1_8_simulacao_fecha_mesmo_sem_validacao_real(ex_sim, gestao_risco_mock):
    """Sanidade: em simulação a ordem SIM retorna FILLED, então fecha normalmente."""
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    ex_sim.fechar_posicao(51000.0, "Take Profit Final", parcial=False)
    assert ex_sim.posicao is None
    assert len(gestao_risco_mock.resultados) == 1


# ══════════════════════════════════════════════════════════════
# status()
# ══════════════════════════════════════════════════════════════


def test_status_sem_posicao(ex_sim):
    assert ex_sim.posicao is None
    st = ex_sim.status()
    assert st == {"posicao": "Nenhuma posicao aberta"}


def test_status_com_posicao(ex_sim):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    st = ex_sim.status()
    assert st["tipo"] == "LONG"
    assert "pnl_%" in st
    assert "pnl_usdt" in st
    assert st["preco_atual"] == 50000.0
    assert st["parcial_feita"] is False
    assert st["tamanho_btc"] == 0.001


# ══════════════════════════════════════════════════════════════
# ADVERSARIAL — Arredondamento: bordas exatas e outros pares
# ══════════════════════════════════════════════════════════════


def test_precisao_ethusdt():
    # Valores reais do spot (exchangeInfo): step/minQty 0.0001, tick 0.01.
    ex = Executor(simulacao=True, symbol="ETHUSDT")
    assert ex._qty_step == 0.0001
    assert ex._min_qty == 0.0001
    assert ex._price_prec == 2


def test_precisao_solusdt_spot():
    # SOLUSDT spot: tick 0.01 (2 casas) — NAO 3 como o hardcode antigo (futures).
    # Preco com 3 casas era rejeitado pelo PRICE_FILTER em modo real.
    ex = Executor(simulacao=True, symbol="SOLUSDT")
    assert ex._qty_step == 0.001
    assert ex._min_qty == 0.001
    assert ex._price_prec == 2


def test_symbol_normalizado_para_maiusculo():
    # symbol.upper() — minúsculo deve mapear para a precisão correta
    ex = Executor(simulacao=True, symbol="btcusdt")
    assert ex.symbol == "BTCUSDT"
    assert ex._qty_step == 0.00001


def test_arredondar_qty_sem_artefato_de_float():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    # Com Decimal o artefato de float sumiu: 0.00123 permanece 0.00123
    # (o codigo antigo com int(qty/step) truncava para 0.00122).
    assert ex._arredondar_qty(0.00123) == 0.00123
    # exatamente no min_qty NÃO é truncado para zero
    assert ex._arredondar_qty(0.00001) == 0.00001


def test_arredondar_qty_abaixo_do_step_vira_zero():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    # abaixo do step trunca para 0.0
    assert ex._arredondar_qty(0.000009) == 0.0


def test_arredondar_qty_solusdt_floor_ao_step():
    ex = Executor(simulacao=True, symbol="SOLUSDT")
    # step real 0.001 -> 1.2745 faz floor para 1.274
    assert ex._arredondar_qty(1.2745) == 1.274
    # 0.05 e multiplo valido do step 0.001 -> permanece
    assert ex._arredondar_qty(0.05) == 0.05


def test_arredondar_preco_solusdt_snap_ao_tick():
    ex = Executor(simulacao=True, symbol="SOLUSDT")
    # tick 0.01 -> 2 casas (nao 3): 123.45678 -> 123.46
    assert ex._arredondar_preco(123.45678) == 123.46


def test_arredondar_qty_zero_e_negativo():
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    assert ex._arredondar_qty(0.0) == 0.0
    # qty negativa: int() trunca em direção a zero -> 0
    assert ex._arredondar_qty(-0.000005) == 0.0


# ══════════════════════════════════════════════════════════════
# ADVERSARIAL — _enviar_ordem (simulação): fallbacks e bordas
# ══════════════════════════════════════════════════════════════


def test_enviar_ordem_preco_zero_usa_get_preco(ex_sim):
    # preco=0 é falsy -> cai no fallback get_preco() (mock 50000.0)
    resp = ex_sim._enviar_ordem("BUY", 0.001, preco=0, tipo="MARKET")
    assert resp["status"] == "FILLED"
    assert resp["price"] == 50000.0


def test_enviar_ordem_qty_exatamente_min_qty_passa(ex_sim):
    # qty == min_qty NÃO deve ser rejeitada (condição é qty < min_qty)
    resp = ex_sim._enviar_ordem("BUY", 0.00001, preco=50000.0)
    assert resp["status"] == "FILLED"
    assert resp["executedQty"] == 0.00001


def test_enviar_ordem_executedqty_arredondada(ex_sim):
    # executedQty reflete a qty já arredondada ao step
    resp = ex_sim._enviar_ordem("BUY", 0.0012349, preco=50000.0)
    assert resp["executedQty"] == 0.00123


def test_enviar_ordem_orderid_unico_prefixo_sim(ex_sim):
    resp = ex_sim._enviar_ordem("SELL", 0.001, preco=50000.0)
    assert resp["orderId"].startswith("SIM-")
    assert resp["simulacao"] is True


# ══════════════════════════════════════════════════════════════
# ADVERSARIAL — abrir_long em MODO REAL (simulacao=False)
# ══════════════════════════════════════════════════════════════


def _novo_executor_real(monkeypatch):
    # Hermético: sem sync de relógio (rede). maker_first=False para exercitar o
    # caminho LIMIT de fallback — o caminho maker-first (P0-2) tem suíte própria
    # em tests/test_executor_maker.py.
    monkeypatch.setattr(Executor, "_sincronizar_relogio", lambda self: None)
    ex = Executor(simulacao=False, symbol="BTCUSDT")
    ex._maker_first = False
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0
    return ex


def test_abrir_long_real_filled_abre_posicao(gestao_risco_mock, database_mock, monkeypatch):
    ex = _novo_executor_real(monkeypatch)
    # Ordem real preenchida com price específico
    ex._enviar_ordem = lambda *a, **k: {
        "status": "FILLED",
        "price": 50050.0,
        "orderId": "REAL-9",
    }
    ok = ex.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ok is True
    assert ex.posicao is not None
    # entrada == price do resp real
    assert ex.posicao["entrada"] == 50050.0
    # target2 = preco_exec * 1.05
    assert ex.posicao["target2"] == pytest.approx(50050.0 * 1.05)
    assert ex.posicao["order_id"] == "REAL-9"
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 1


def test_abrir_long_real_nao_filled_retorna_false(gestao_risco_mock, database_mock, monkeypatch):
    ex = _novo_executor_real(monkeypatch)
    ex._enviar_ordem = lambda *a, **k: {"erro": "rejeitada"}
    ok = ex.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ok is False
    assert ex.posicao is None
    # nenhuma posição contabilizada
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 0


def test_abrir_long_real_bloqueado_pelo_risco(gestao_risco_mock, database_mock, monkeypatch):
    ex = _novo_executor_real(monkeypatch)
    # validar_trade nega -> abrir_long retorna False ANTES de enviar ordem
    gestao_risco_mock.validar_trade = lambda *a, **k: {"pode": False, "motivo": "drawdown"}
    enviado = {"flag": False}

    def _spy(*a, **k):
        enviado["flag"] = True
        return {"status": "FILLED", "price": 50000.0, "orderId": "X"}

    ex._enviar_ordem = _spy
    ok = ex.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ok is False
    assert ex.posicao is None
    assert enviado["flag"] is False  # ordem nem chegou a ser enviada


def test_abrir_long_simulacao_nao_chama_validar_trade(ex_sim, gestao_risco_mock):
    # Em simulação validar_trade NÃO é checado (bloco só roda se not self.simulacao)
    chamou = {"flag": False}

    def _v(*a, **k):
        chamou["flag"] = True
        return {"pode": False, "motivo": "bloqueado"}

    gestao_risco_mock.validar_trade = _v
    ok = ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ok is True  # abre mesmo com validar_trade que negaria
    assert chamou["flag"] is False


def test_abrir_long_entrada_aplica_slippage_1_por_mil(ex_sim):
    # preco_limit = preco_entrada * 1.001 arredondado; em simulação entrada == esse preço
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    assert ex_sim.posicao["entrada"] == ex_sim._arredondar_preco(50000.0 * 1.001)
    # target2 baseado no preco_exec
    assert ex_sim.posicao["target2"] == pytest.approx(ex_sim.posicao["entrada"] * 1.05)


# ══════════════════════════════════════════════════════════════
# ADVERSARIAL — fechar_posicao: PnL, labels e parcial em modo real
# ══════════════════════════════════════════════════════════════


def test_fechar_total_pnl_positivo_label_fechar_long(ex_sim, gestao_risco_mock, database_mock):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    entrada = ex_sim.posicao["entrada"]
    tam = ex_sim.posicao["tamanho_btc"]
    ex_sim.fechar_posicao(60000.0, "Take Profit Final", parcial=False)
    # PnL positivo registrado
    pnl_esperado = tam * (60000.0 - entrada)
    assert gestao_risco_mock.resultados[0] == pytest.approx(pnl_esperado)
    assert pnl_esperado > 0
    # label do sinal salvo deve ser FECHAR_LONG (pnl >= 0)
    args, kwargs = database_mock.chamadas[0]
    assert args[0] == "FECHAR_LONG"


def test_fechar_total_pnl_negativo_label_stop(ex_sim, gestao_risco_mock, database_mock):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    # fecha abaixo da entrada -> PnL negativo -> label STOP
    ex_sim.fechar_posicao(40000.0, "Stop Loss", parcial=False)
    assert gestao_risco_mock.resultados[0] < 0
    args, kwargs = database_mock.chamadas[0]
    assert args[0] == "STOP"
    assert kwargs.get("executado") is True
    assert kwargs.get("source") == "executor"
    assert kwargs.get("symbol") == "BTCUSDT"


def test_fechar_parcial_pnl_calculado_sobre_metade(ex_sim, gestao_risco_mock):
    ex_sim.abrir_long(50000.0, 0.002, 49000.0, 51000.0)
    entrada = ex_sim.posicao["entrada"]
    tam_inicial = ex_sim.posicao["tamanho_btc"]
    ex_sim.fechar_posicao(55000.0, "Take Profit Parcial", parcial=True)
    # PnL parcial sobre METADE da quantidade
    pnl_esperado = (tam_inicial / 2) * (55000.0 - entrada)
    assert gestao_risco_mock.resultados[0] == pytest.approx(pnl_esperado)
    # posição mantida com metade do tamanho e parcial_feita True
    assert ex_sim.posicao["tamanho_btc"] == pytest.approx(tam_inicial / 2)
    assert ex_sim.posicao["parcial_feita"] is True


def test_fechar_parcial_nao_decrementa_posicoes_abertas(ex_sim, gestao_risco_mock):
    ex_sim.abrir_long(50000.0, 0.002, 49000.0, 51000.0)
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 1
    persist_antes = gestao_risco_mock.persistido
    ex_sim.fechar_posicao(55000.0, "Take Profit Parcial", parcial=True)
    # parcial não fecha a posição: contador permanece e não persiste de novo no fim
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 1
    assert gestao_risco_mock.persistido == persist_antes


def test_p1_8_fechar_parcial_real_sem_filled_mantem_tudo(
    gestao_risco_mock, database_mock, monkeypatch
):
    # Guard P1-8 também vale para fechamento PARCIAL em modo real
    ex = Executor(simulacao=False, symbol="BTCUSDT")
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0
    ex.posicao = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "tamanho_btc": 0.002,
        "stop_inicial": 49000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52500.0,
        "parcial_feita": False,
        "abertura": "2026-06-20T00:00:00",
        "order_id": "REAL-1",
    }
    ex._ativo = True
    ex._enviar_ordem = lambda *a, **k: {"erro": "x"}
    ret = ex.fechar_posicao(55000.0, "Take Profit Parcial", parcial=True)
    assert ret is None
    # tamanho NÃO foi reduzido e parcial_feita continua False
    assert ex.posicao["tamanho_btc"] == 0.002
    assert ex.posicao["parcial_feita"] is False
    assert gestao_risco_mock.resultados == []
    assert database_mock.chamadas == []


def test_fechar_total_real_filled_fecha(gestao_risco_mock, database_mock, monkeypatch):
    # Modo real com ordem FILLED deve fechar normalmente
    ex = Executor(simulacao=False, symbol="BTCUSDT")
    ex._monitorar = lambda: None
    ex.get_preco = lambda: 50000.0
    ex.posicao = {
        "tipo": "LONG",
        "entrada": 50000.0,
        "tamanho_btc": 0.001,
        "stop_inicial": 49000.0,
        "stop_atual": 49000.0,
        "target1": 51000.0,
        "target2": 52500.0,
        "parcial_feita": False,
        "abertura": "2026-06-20T00:00:00",
        "order_id": "REAL-1",
    }
    ex._ativo = True
    gestao_risco_mock._estado_risco["posicoes_abertas"] = 1
    ex._enviar_ordem = lambda *a, **k: {"status": "FILLED", "price": 51000.0, "orderId": "R2"}
    ex.fechar_posicao(51000.0, "Take Profit Final", parcial=False)
    assert ex.posicao is None
    assert ex._ativo is False
    assert len(gestao_risco_mock.resultados) == 1
    assert gestao_risco_mock._estado_risco["posicoes_abertas"] == 0


# ══════════════════════════════════════════════════════════════
# ADVERSARIAL — status(): valores exatos de PnL
# ══════════════════════════════════════════════════════════════


def test_status_pnl_valores_exatos(ex_sim):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    entrada = ex_sim.posicao["entrada"]
    # get_preco mockado para 50000.0
    st = ex_sim.status()
    pnl_pct = (50000.0 - entrada) / entrada * 100
    assert st["pnl_%"] == round(pnl_pct, 2)
    assert st["pnl_usdt"] == round(0.001 * (50000.0 - entrada), 2)
    assert st["entrada"] == entrada
    assert st["stop_atual"] == 49000.0
    assert st["target1"] == 51000.0


def test_status_inclui_todas_as_chaves(ex_sim):
    ex_sim.abrir_long(50000.0, 0.001, 49000.0, 51000.0)
    st = ex_sim.status()
    esperadas = {
        "tipo",
        "entrada",
        "preco_atual",
        "pnl_%",
        "pnl_usdt",
        "stop_atual",
        "target1",
        "target2",
        "parcial_feita",
        "tamanho_btc",
    }
    assert esperadas.issubset(st.keys())


def test_get_preco_excecao_retorna_zero(gestao_risco_mock, database_mock, monkeypatch):
    # get_preco real: se requests falhar, retorna 0.0 (sem propagar exceção)
    ex = Executor(simulacao=True, symbol="BTCUSDT")

    class _ReqFake:
        def get(self, *a, **k):
            raise RuntimeError("sem rede")

    monkeypatch.setattr(executor_mod, "requests", _ReqFake())
    assert ex.get_preco() == 0.0
