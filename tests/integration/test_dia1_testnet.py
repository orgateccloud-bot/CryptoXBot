"""
Integracao — cadeia de dia-1 contra o Binance Spot Testnet (I-10i)
===================================================================
O criterio de saida do I-10 pede 8 cenarios verdes. Eles se dividem em dois
grupos por uma razao tecnica, nao por conveniencia:

CENARIOS QUE SO A EXCHANGE REAL PODE PROVAR — ficam aqui:
  1. fill com comissao no ativo-base => o stop e ACEITO
     (o unico jeito de provar e a Binance de fato debitando a taxa em BTC e o
     STOP_LOSS_LIMIT passando pelo filtro de saldo)
  4. stop executado fora do bot => detectado na reconciliacao
  8. crash e restart => `reconciliar_boot` converge com o estado da exchange
  + metrica agregada: 0 posicoes sem stop apos N ciclos

CENARIOS DE INJECAO DE FALHA — ficam em tests/test_executor_dia1.py:
  2. stop rejeitado => abrir_long devolve False, sem posicao orfa
  3. 503 na entrada => nenhuma ordem duplicada
  5. SELL rejeitado => monitor sobrevive e retenta
  6. excecao em salvar_sinal => PnL contabilizado exatamente 1x
  7. cancelamento que falha => ids NAO sao zerados

Nao da para pedir ao testnet que devolva 503 no momento certo, nem que rejeite
um SELL sob demanda. Forcar isso exigiria um proxy que injeta falhas — mais
infraestrutura para provar o que o teste hermetico ja prova de forma
deterministica e sem flakiness. O que o testnet acrescenta e o que o mock NAO
consegue: o comportamento real dos filtros, da comissao e do ciclo de vida da
ordem.

Cada teste LIMPA o que criou (cancela ordens abertas e zera a posicao) no
teardown, inclusive se falhar no meio.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from conftest import motivo_do_skip  # noqa: E402  (conftest local do diretorio)

_MOTIVO = motivo_do_skip()
if _MOTIVO:
    pytest.skip(_MOTIVO, allow_module_level=True)

import executor as E  # noqa: E402
from executor import Executor  # noqa: E402

PAR = os.getenv("INTEGRACAO_PAR", "BTCUSDT")
# Notional pequeno mas acima do MIN_NOTIONAL do testnet (10 USDT).
NOTIONAL_USDT = float(os.getenv("INTEGRACAO_NOTIONAL", "15"))


class _RiscoInerte:
    """Neutraliza os gates de risco: o que esta sob teste e a interacao com a
    exchange, nao a politica de risco (que tem suite propria)."""

    def __init__(self):
        self.resultados = []
        self.travas = []

    def get_saldo_usdt(self):
        return 10_000.0

    def validar_trade(self, *a, **k):
        return {"pode": True, "motivo": "ok"}

    def registrar_resultado(self, pnl):
        self.resultados.append(pnl)

    def travar(self, motivo, **k):
        self.travas.append(motivo)

    def persistir_estado(self):
        pass

    def incrementar_posicoes_abertas(self):
        pass

    def decrementar_posicoes_abertas(self):
        pass


class _BancoEmMemoria:
    """Banco falso: a suite de integracao NAO pode tocar o banco real (o
    conftest da raiz ja barra o de producao, e um temporario so adicionaria
    ruido ao que se quer medir aqui)."""

    def __init__(self):
        self.posicoes = {}
        self.eventos = []
        self.sinais = []

    def salvar_posicao_aberta(self, symbol, pos):
        self.posicoes[symbol] = dict(pos)

    def remover_posicao_aberta(self, symbol):
        self.posicoes.pop(symbol, None)

    def carregar_posicoes_abertas(self):
        return dict(self.posicoes)

    def salvar_bot_event(self, tipo, msg, **k):
        self.eventos.append((tipo, msg))

    def salvar_sinal(self, *a, **k):
        self.sinais.append((a, k))
        return len(self.sinais)

    def marcar_sinal_executado(self, *a, **k):
        pass

    def atualizar_sinal_fechamento(self, *a, **k):
        pass


@pytest.fixture
def ex(credenciais_testnet, monkeypatch):
    """Executor apontado ao TESTNET, com risco e banco neutralizados."""
    monkeypatch.setattr(E, "BASE_URL", credenciais_testnet["url"])
    monkeypatch.setattr(E, "API_KEY", credenciais_testnet["chave"])
    monkeypatch.setattr(E, "API_SECRET", credenciais_testnet["segredo"])
    monkeypatch.setattr(E, "gestao_risco", _RiscoInerte())
    monkeypatch.setattr(E, "database", _BancoEmMemoria())
    # cache de precisao e por processo e pode ter sido preenchido com dados de
    # producao por outro teste — limpar garante que o exchangeInfo do testnet
    # (que tem filtros proprios) seja o que vale aqui.
    E._precisao_cache.clear()

    executor = Executor(simulacao=False, symbol=PAR)
    executor._monitorar = lambda: None  # o laco e dirigido manualmente nos testes
    yield executor

    # ── Teardown: nao deixar ordem nem posicao para tras ──────────────
    try:
        if executor.posicao:
            executor._liberar_protecao()
            qty = executor.posicao.get("tamanho_btc")
            if qty:
                executor._enviar_ordem("SELL", qty, tipo="MARKET")
            executor.posicao = None
        abertas = executor._request_assinado("GET", "/api/v3/openOrders", {"symbol": PAR})
        for o in abertas if isinstance(abertas, list) else []:
            executor._cancelar_ordem_exchange(o.get("orderId"))
    except Exception as e:  # teardown nunca derruba o teste
        print(f"[INTEGRACAO] AVISO no teardown: {e}")
    E._precisao_cache.clear()


def _qty_para(executor, notional_usdt):
    preco = executor.get_preco()
    assert preco > 0, "testnet nao devolveu preco"
    return executor._arredondar_qty(notional_usdt / preco), preco


# ══════════════════════════════════════════════════════════════
# Cenario 1 — comissao em ativo-base e o stop e ACEITO
# ══════════════════════════════════════════════════════════════


def test_fill_com_comissao_em_base_o_stop_e_aceito(ex):
    """O elo que abria a corrente inteira.

    Sem descontar a comissao, o STOP_LOSS_LIMIT e enviado por MAIS do que
    existe na conta e a Binance devolve -2010 — posicao real, desprotegida.
    Aqui o teste nao mede a aritmetica (isso e hermetico): mede se a exchange
    ACEITA o stop dimensionado pelo executor.
    """
    qty, preco = _qty_para(ex, NOTIONAL_USDT)
    stop = ex._arredondar_preco(preco * 0.97)
    alvo = ex._arredondar_preco(preco * 1.05)

    assert ex.abrir_long(preco, qty, stop, alvo) is True, "abrir_long recusou (ver log acima)"
    assert ex.posicao is not None
    # A prova: existe protecao viva na exchange, com id.
    assert (
        ex.posicao["stop_order_id"] or ex.posicao["oco_list_id"]
    ), "posicao aberta SEM protecao — o fail-closed deveria ter impedido isto"
    # E o tamanho protegido nao excede o saldo livre do ativo base.
    conta = ex._request_assinado("GET", "/api/v3/account", {})
    saldo = 0.0
    for b in conta.get("balances", []):
        if b.get("asset") == ex._base_asset:
            saldo = float(b.get("free", 0)) + float(b.get("locked", 0))
            break
    assert (
        saldo >= ex.posicao["tamanho_btc"] - 1e-12
    ), f"posicao ({ex.posicao['tamanho_btc']}) maior que o saldo real ({saldo})"


# ══════════════════════════════════════════════════════════════
# Cenario 4 — stop executado fora do bot
# ══════════════════════════════════════════════════════════════


def test_stop_executado_fora_do_bot_e_detectado(ex):
    """Simula o stop preenchendo sem o bot saber.

    Nao da para forcar o preco do testnet a cair, entao o teste faz o que o
    mercado faria: executa a venda por FORA do fluxo de fechamento e confirma
    que `_reconciliar_protecao_viva` percebe. E exatamente o buraco do I-10g —
    antes, o monitor so olhava o preco e nunca perguntava a exchange.
    """
    qty, preco = _qty_para(ex, NOTIONAL_USDT)
    stop = ex._arredondar_preco(preco * 0.97)
    assert ex.abrir_long(preco, qty, stop, ex._arredondar_preco(preco * 1.05)) is True

    # Cancela a protecao e vende por fora — do ponto de vista do bot, e como se
    # o stop tivesse disparado na exchange.
    ex._liberar_protecao()
    resp = ex._enviar_ordem("SELL", ex.posicao["tamanho_btc"], tipo="MARKET")
    assert resp.get("status") == "FILLED", f"venda externa nao preencheu: {resp}"

    # O bot ainda acha que esta comprado. A reconciliacao tem de convergir.
    ex.posicao["stop_order_id"] = resp.get("orderId")
    ex._reconciliar_protecao_viva()
    assert ex.posicao is None, "reconciliacao NAO detectou o fechamento na exchange"
    assert E.gestao_risco.resultados, "PnL do fechamento externo nao foi contabilizado"


# ══════════════════════════════════════════════════════════════
# Cenario 8 — crash e restart
# ══════════════════════════════════════════════════════════════


def test_restart_reconcilia_com_o_estado_da_exchange(ex, credenciais_testnet, monkeypatch):
    """Abre posicao, joga fora o estado em memoria (equivale a um crash) e
    confirma que um executor NOVO reconstroi a verdade a partir da exchange."""
    qty, preco = _qty_para(ex, NOTIONAL_USDT)
    stop = ex._arredondar_preco(preco * 0.97)
    assert ex.abrir_long(preco, qty, stop, ex._arredondar_preco(preco * 1.05)) is True
    salva = dict(ex.posicao)

    # "Crash": novo executor, sem estado em memoria, mas com o DB tendo a
    # posicao (o `_BancoEmMemoria` do fixture ja a guardou).
    novo = Executor(simulacao=False, symbol=PAR)
    novo._monitorar = lambda: None
    novo._reconciliar_boot_exchange = True
    novo.reconciliar_boot()

    assert novo.posicao is not None, "reconciliacao de boot perdeu uma posicao REAL"
    assert novo.posicao["tamanho_btc"] == pytest.approx(salva["tamanho_btc"], rel=1e-9)
    # devolve o estado ao fixture para o teardown limpar
    ex.posicao = novo.posicao


# ══════════════════════════════════════════════════════════════
# Metrica agregada — 0 posicoes sem stop
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize("ciclos", [int(os.getenv("INTEGRACAO_CICLOS", "10"))])
def test_nenhuma_posicao_sem_stop_apos_ciclos(ex, ciclos):
    """Abre e fecha N vezes seguidas verificando o invariante que resume o
    I-10: nunca existe posicao registrada sem protecao viva na exchange.

    `INTEGRACAO_CICLOS` controla a intensidade (default 10; o criterio de saida
    do I-10 pede 50 — rode com `INTEGRACAO_CICLOS=50` para o gate oficial, leva
    alguns minutos).
    """
    descobertas = []
    for i in range(ciclos):
        qty, preco = _qty_para(ex, NOTIONAL_USDT)
        stop = ex._arredondar_preco(preco * 0.97)
        aberto = ex.abrir_long(preco, qty, stop, ex._arredondar_preco(preco * 1.05))
        if aberto:
            if not (ex.posicao["stop_order_id"] or ex.posicao["oco_list_id"]):
                descobertas.append(i)
            ex.fechar_posicao(ex.get_preco(), "Fechamento Manual")
        # se `aberto` for False, o fail-closed agiu: nao ha posicao a checar
        assert ex.posicao is None, f"ciclo {i}: posicao sobrou aberta"
        time.sleep(0.2)  # respeita o rate limit do testnet
    assert descobertas == [], f"posicoes SEM stop nos ciclos {descobertas}"
