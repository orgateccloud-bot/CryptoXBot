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
    main._historico_ticks_btc.clear()
    main._obi_historico.clear()
    main.ws_state_depth["connected"] = False
    main.ws_state_depth["last_message_time"] = 0.0
    main.ws_state_depth["latency_ms"] = 0.0
    yield


def _processar(msg):
    with patch("main.database.salvar_trade"):
        asyncio.run(main.process_message(msg))


def _processar_depth(msg):
    asyncio.run(main.process_depth_message(msg))
    main.ws_state_depth["last_message_time"] = main.time.time()


def _msg_depth(bids, asks):
    """Payload real do Partial Book Depth Stream (@depthN@100ms)."""
    return json.dumps({"lastUpdateId": 1, "bids": bids, "asks": asks})


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


# ── P1-1: buffer de historico_ticks para o componente CVD do score ──────────


def test_process_message_alimenta_historico_ticks():
    _processar(_msg_aggtrade(1, 63000.0, 0.5, is_buyer_maker=False))
    ticks = main.obter_historico_ticks_btc()
    assert ticks == [{"preco": 63000.0, "is_buyer_maker": False, "quantidade": 0.5}]


def test_historico_ticks_preserva_ordem_e_direcao():
    _processar(_msg_aggtrade(1, 63000.0, 1.0, is_buyer_maker=False))
    _processar(_msg_aggtrade(2, 63010.0, 0.4, is_buyer_maker=True))
    ticks = main.obter_historico_ticks_btc()
    assert [t["quantidade"] for t in ticks] == [1.0, 0.4]
    assert [t["is_buyer_maker"] for t in ticks] == [False, True]


def test_historico_ticks_respeita_maxlen_200():
    for i in range(1, 251):
        _processar(_msg_aggtrade(i, 63000.0 + i, 0.01, is_buyer_maker=False))
    ticks = main.obter_historico_ticks_btc()
    assert len(ticks) == 200
    # mantem os 200 MAIS RECENTES (descarta os mais antigos primeiro)
    assert ticks[0]["preco"] == pytest.approx(63000.0 + 51)
    assert ticks[-1]["preco"] == pytest.approx(63000.0 + 250)


def test_obter_historico_ticks_btc_retorna_copia_independente():
    _processar(_msg_aggtrade(1, 63000.0, 0.5, is_buyer_maker=False))
    ticks = main.obter_historico_ticks_btc()
    ticks.append({"preco": 0.0, "is_buyer_maker": False, "quantidade": 0.0})
    assert len(main.obter_historico_ticks_btc()) == 1  # mutar a copia nao afeta o buffer


def test_historico_ticks_vazio_antes_de_qualquer_mensagem():
    assert main.obter_historico_ticks_btc() == []


# ── P1-1: @depth / OBI ────────────────────────────────────────────────────


def test_process_depth_message_calcula_obi():
    # bid=15, ask=5 -> (15-5)/20 = 0.5
    _processar_depth(_msg_depth([["100.0", "10"], ["99.0", "5"]], [["101.0", "5"]]))
    assert main.obter_obi_suavizado() == pytest.approx(0.5)


def test_process_depth_message_totalmente_vendedor():
    _processar_depth(_msg_depth([], [["101.0", "10"]]))
    assert main.obter_obi_suavizado() == pytest.approx(-1.0)


def test_process_depth_message_sem_liquidez_nenhuma_e_zero():
    _processar_depth(_msg_depth([], []))
    assert main.obter_obi_suavizado() == pytest.approx(0.0)


def test_obi_suaviza_entre_mensagens_opostas():
    _processar_depth(_msg_depth([["100.0", "10"]], []))  # obi=+1.0
    _processar_depth(_msg_depth([], [["101.0", "10"]]))  # obi=-1.0
    # media das duas: 0.0, nao um salto direto para -1.0
    assert main.obter_obi_suavizado() == pytest.approx(0.0)


def test_obi_respeita_janela_de_suavizacao():
    for _ in range(main.OBI_JANELA_SUAVIZACAO):
        _processar_depth(_msg_depth([["100.0", "1"]], []))  # obi=+1.0 x N
    assert main.obter_obi_suavizado() == pytest.approx(1.0)
    _processar_depth(_msg_depth([], [["101.0", "1"]]))  # 1 mensagem obi=-1.0
    # janela cheia: a mais antiga (+1.0) sai, entra -1.0 -> media desloca
    esperado = ((main.OBI_JANELA_SUAVIZACAO - 1) * 1.0 + (-1.0)) / main.OBI_JANELA_SUAVIZACAO
    assert main.obter_obi_suavizado() == pytest.approx(esperado)


def test_obi_none_antes_de_qualquer_mensagem():
    assert main.obter_obi_suavizado() is None


def test_obi_none_quando_stream_stale():
    # Regressao: uma conexao morta com o buffer ainda cheio de dados ANTIGOS
    # nao pode retornar um OBI plausivel-porem-obsoleto -- deve degradar
    # para None (neutro no score), igual ao caso "sem dado nenhum".
    _processar_depth(_msg_depth([["100.0", "10"]], []))
    assert main.obter_obi_suavizado() == pytest.approx(1.0)  # fresco: ok

    main.ws_state_depth["last_message_time"] = main.time.time() - (main.OBI_STALE_SEGUNDOS + 1)
    assert main.obter_obi_suavizado() is None  # stale: degrada para None


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
