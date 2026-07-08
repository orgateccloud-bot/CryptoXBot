"""
Testes herméticos do parser de mensagens do WebSocket (main.process_message).

Regressão: o parser lia data["t"] (trade id do stream @trade), mas o bot assina
@aggTrade, cujo id é data["a"]. Isso levantava KeyError em TODA mensagem, zerando
o CVD silenciosamente. Estes testes travam o formato @aggTrade e a acumulação
de CVD/compras/vendas em ambas as direções.
"""

import asyncio
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def _msg_aggtrade(agg_id, price, qty, is_buyer_maker):
    """Payload real de um @aggTrade da Binance (spot e futures usam o mesmo)."""
    return json.dumps(
        {
            "e": "aggTrade",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "a": agg_id,
            "p": str(price),
            "q": str(qty),
            "f": 100,
            "l": 105,
            "T": 1700000000000,
            "m": is_buyer_maker,
            "M": True,
        }
    )


@pytest.fixture(autouse=True)
def _resetar_estado():
    # Zera o estado global antes de cada teste (evita contaminação entre casos).
    main.cvd_btc = 0.0
    main.total_compras = 0.0
    main.total_vendas = 0.0
    main.preco_atual = 0.0
    main.ws_state["last_trade_id"] = 0
    yield


def _processar(msg):
    with patch("main.database.salvar_trade"):
        asyncio.run(main.process_message(msg))


def test_aggtrade_compra_acumula_cvd_positivo():
    # is_buyer_maker=False => COMPRA agressiva => CVD sobe
    _processar(_msg_aggtrade(1, 63000.0, 0.5, is_buyer_maker=False))
    assert main.cvd_btc == pytest.approx(0.5)
    assert main.total_compras == pytest.approx(0.5)
    assert main.total_vendas == pytest.approx(0.0)
    assert main.preco_atual == pytest.approx(63000.0)


def test_aggtrade_venda_acumula_cvd_negativo():
    # is_buyer_maker=True => VENDA agressiva => CVD desce
    _processar(_msg_aggtrade(1, 63000.0, 0.3, is_buyer_maker=True))
    assert main.cvd_btc == pytest.approx(-0.3)
    assert main.total_vendas == pytest.approx(0.3)
    assert main.total_compras == pytest.approx(0.0)


def test_usa_campo_a_nao_t():
    # Garantia anti-regressão: a mensagem @aggTrade NÃO tem "t"; o parser não
    # pode depender dele. Se voltar a ler data["t"], isto levanta KeyError.
    _processar(_msg_aggtrade(42, 60000.0, 0.2, is_buyer_maker=False))
    assert main.ws_state["last_trade_id"] == 42


def test_dedup_ignora_trade_id_repetido():
    _processar(_msg_aggtrade(10, 63000.0, 0.5, is_buyer_maker=False))
    _processar(_msg_aggtrade(10, 63000.0, 0.5, is_buyer_maker=False))  # duplicata
    assert main.cvd_btc == pytest.approx(0.5)  # não contou duas vezes


def test_sequencia_mista_cvd_liquido():
    _processar(_msg_aggtrade(1, 63000.0, 1.0, is_buyer_maker=False))  # +1.0 compra
    _processar(_msg_aggtrade(2, 63010.0, 0.4, is_buyer_maker=True))  # -0.4 venda
    _processar(_msg_aggtrade(3, 63020.0, 0.1, is_buyer_maker=False))  # +0.1 compra
    assert main.cvd_btc == pytest.approx(0.7)
    assert main.total_compras == pytest.approx(1.1)
    assert main.total_vendas == pytest.approx(0.4)


# ── C-7: encerramento gracioso (signal handlers) ─────────────────────────────


def test_encerrar_seta_o_event_sem_raise():
    import signal as _sig

    main._shutdown_event.clear()
    # nao deve levantar excecao (raise dentro de handler e fragil)
    main._encerrar(_sig.SIGTERM, None)
    assert main._shutdown_event.is_set()
    main._shutdown_event.clear()


def test_registra_handlers_dos_sinais_de_parada():
    import signal as _sig

    # salva handlers originais para nao poluir o pytest (SIGINT etc.)
    nomes = [n for n in ("SIGTERM", "SIGINT", "SIGBREAK") if getattr(_sig, n, None)]
    originais = {n: _sig.getsignal(getattr(_sig, n)) for n in nomes}
    try:
        main._registrar_signal_handlers()
        for n in nomes:
            assert _sig.getsignal(getattr(_sig, n)) is main._encerrar, f"{n} nao registrado"
    finally:
        for n, h in originais.items():
            _sig.signal(getattr(_sig, n), h)
