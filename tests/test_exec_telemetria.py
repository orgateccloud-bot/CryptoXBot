"""
Testes — telemetria de EXECUÇÃO (entrada e saída), compartilhada pelas duas
estratégias
============================================================================
O propósito da instrumentação é responder "a execução come o edge?". Ela só
serve se os três preços forem realmente distintos e medidos no lugar certo:

  ref     — preço em que a ESTRATÉGIA decidiu (na otimizada, `f1h[-1]`, que vem
            do cache de klines com TTL de 30s: pode estar velho)
  mercado — preço fresco lido no instante de mandar a ordem
  fill    — preço em que a ordem preencheu

Um bug aqui não quebra o bot — produz um número errado que parece certo, que é
pior. Daí estes testes serem sobre a aritmética e o ponto de medição.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class ExecFake:
    """Executor mínimo: só o que a telemetria de entrada lê."""

    simulacao = True

    def __init__(self, entrada):
        self.posicao = {"entrada": entrada} if entrada is not None else None


@pytest.fixture
def captura(monkeypatch):
    """Captura gauges e bot_events sem tocar em health/database de verdade."""
    g, ev = {}, []
    monkeypatch.setattr(main.health, "set_gauge", lambda n, v: g.__setitem__(n, v))
    monkeypatch.setattr(
        main.database,
        "salvar_bot_event",
        lambda tipo, msg, **kw: ev.append((tipo, msg, kw)),
    )
    return g, ev


# ── Entrada ────────────────────────────────────────────────────


class TestTelemetriaEntrada:
    def test_separa_desvio_de_decisao_e_desvio_total(self, captura):
        """ref=100, mercado=101 (decisão sobre dado 1% velho), fill=102."""
        g, _ = captura
        main._registrar_execucao(
            "BTCUSDT", "otimizada", 100.0, 101.0, ExecFake(102.0), main.time.time(), 0.01
        )
        assert g["exec_desvio_ref_mercado_pct"] == pytest.approx(1.0)
        assert g["exec_desvio_ref_fill_pct"] == pytest.approx(2.0)

    def test_desvio_negativo_quando_preco_cai(self, captura):
        g, _ = captura
        main._registrar_execucao(
            "BTCUSDT", "trend", 100.0, 99.0, ExecFake(98.5), main.time.time(), 0.01
        )
        assert g["exec_desvio_ref_mercado_pct"] == pytest.approx(-1.0)
        assert g["exec_desvio_ref_fill_pct"] == pytest.approx(-1.5)

    def test_discriminador_de_estrategia(self, captura):
        """Os gauges são genéricos; este é o único jeito de saber quem gerou."""
        g, _ = captura
        main._registrar_execucao(
            "BTCUSDT", "trend", 100.0, 100.0, ExecFake(100.0), main.time.time(), 0.01
        )
        assert g["exec_estrategia_trend"] == 1.0
        main._registrar_execucao(
            "BTCUSDT", "otimizada", 100.0, 100.0, ExecFake(100.0), main.time.time(), 0.01
        )
        assert g["exec_estrategia_trend"] == 0.0

    def test_latencia_conta_do_inicio_do_ciclo(self, captura):
        """A latência que interessa é sinal→fill inteira (fetch + indicadores +
        ML + risco + ordem), não só o round-trip da ordem."""
        g, _ = captura
        t0 = main.time.time() - 2.5  # ciclo começou 2.5s atrás
        main._registrar_execucao("BTCUSDT", "trend", 100.0, 100.0, ExecFake(100.0), t0, 0.01)
        assert g["exec_latencia_sinal_fill_ms"] >= 2500

    def test_registra_bot_event_com_estrategia_e_precos(self, captura):
        _, ev = captura
        main._registrar_execucao(
            "ETHUSDT", "otimizada", 100.0, 101.0, ExecFake(102.0), main.time.time(), 0.5
        )
        assert len(ev) == 1
        tipo, msg, kw = ev[0]
        assert tipo == "execucao_entrada"
        assert "[otimizada]" in msg and "ETHUSDT" in msg
        assert kw["symbol"] == "ETHUSDT"

    def test_sem_posicao_usa_mercado_como_fill(self, captura):
        """Se a posição sumiu entre o fill e a leitura, degrada em vez de
        estourar TypeError no meio da telemetria."""
        g, _ = captura
        main._registrar_execucao(
            "BTCUSDT", "trend", 100.0, 101.0, ExecFake(None), main.time.time(), 0.01
        )
        assert g["exec_desvio_ref_fill_pct"] == pytest.approx(1.0)

    def test_falha_de_health_nao_derruba_a_entrada(self, monkeypatch):
        """Telemetria NUNCA pode quebrar o caminho da ordem."""

        def explode(*a, **k):
            raise RuntimeError("gauge morreu")

        monkeypatch.setattr(main.health, "set_gauge", explode)
        monkeypatch.setattr(main.database, "salvar_bot_event", explode)
        main._registrar_execucao(
            "BTCUSDT", "trend", 100.0, 101.0, ExecFake(102.0), main.time.time(), 0.01
        )


# ── Saída ──────────────────────────────────────────────────────


class TestTelemetriaSaida:
    def _exec_sim(self, monkeypatch, preco_fill):
        import executor as ex

        e = ex.Executor(simulacao=True, symbol="BTCUSDT")
        e.posicao = {
            "tipo": "LONG",
            "entrada": 100.0,
            "tamanho_btc": 0.01,
            "tamanho_btc_original": 0.01,
            "stop_inicial": 90.0,
            "stop_atual": 90.0,
            "target1": 105.0,
            "target2": 105.0,
            "parcial_feita": False,
            "abertura": "2026-01-01T00:00:00",
            "order_id": 1,
            "stop_order_id": None,
            "oco_list_id": None,
            "sinal_id": None,
            "pnl_usdt_parcial_acumulado": 0.0,
        }
        # SELL MARKET "preenche" no preco_fill informado (fresco), nao no `preco`
        monkeypatch.setattr(
            e,
            "_enviar_ordem",
            lambda *a, **k: {"status": "FILLED", "price": preco_fill, "executedQty": 0.01},
        )
        for nome in (
            "salvar_sinal",
            "atualizar_sinal_fechamento",
            "remover_posicao_aberta",
            "salvar_posicao_aberta",
        ):
            if hasattr(ex.database, nome):
                monkeypatch.setattr(ex.database, nome, lambda *a, **k: None)
        monkeypatch.setattr(ex.gestao_risco, "registrar_resultado", lambda *a, **k: None)
        monkeypatch.setattr(ex.gestao_risco, "decrementar_posicoes_abertas", lambda *a, **k: None)
        monkeypatch.setattr(ex.gestao_risco, "persistir_estado", lambda *a, **k: None)
        return ex, e

    def test_mede_desvio_entre_referencia_e_fill(self, monkeypatch):
        """Monitor decidiu fechar vendo 110; o SELL preencheu em 109.45."""
        g, ev = {}, []
        ex, e = self._exec_sim(monkeypatch, preco_fill=109.45)
        monkeypatch.setattr(ex.health, "set_gauge", lambda n, v: g.__setitem__(n, v))
        monkeypatch.setattr(
            ex.database,
            "salvar_bot_event",
            lambda tipo, msg, **kw: ev.append((tipo, msg)),
        )
        e.fechar_posicao(110.0, "Take Profit Final")

        assert g["exec_desvio_saida_ref_fill_pct"] == pytest.approx(-0.5)
        assert any(t == "execucao_saida" for t, _ in ev)

    def test_sem_desvio_nao_polui_bot_events(self, monkeypatch):
        """Fill exatamente na referência não gera evento — senão o log viraria
        ruído com uma linha por fechamento."""
        g, ev = {}, []
        ex, e = self._exec_sim(monkeypatch, preco_fill=110.0)
        monkeypatch.setattr(ex.health, "set_gauge", lambda n, v: g.__setitem__(n, v))
        monkeypatch.setattr(
            ex.database,
            "salvar_bot_event",
            lambda tipo, msg, **kw: ev.append((tipo, msg)),
        )
        e.fechar_posicao(110.0, "Take Profit Final")

        assert g["exec_desvio_saida_ref_fill_pct"] == pytest.approx(0.0)
        assert not any(t == "execucao_saida" for t, _ in ev)

    def test_pnl_usa_o_fill_nao_a_referencia(self, monkeypatch):
        """O PnL contabiliza o preço EXECUTADO. Antes usava `preco` (o que o
        monitor observou), o que inflava o resultado por exatamente o slippage
        de saída — na mesma coluna que a Etapa 2 do gate usa para profit factor.
        """
        registrado = {}
        ex, e = self._exec_sim(monkeypatch, preco_fill=109.45)
        monkeypatch.setattr(ex.health, "set_gauge", lambda n, v: None)
        monkeypatch.setattr(ex.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(
            ex.gestao_risco,
            "registrar_resultado",
            lambda pnl: registrado.__setitem__("pnl", pnl),
        )
        e.fechar_posicao(110.0, "Take Profit Final")

        # 0.01 * (109.45 - 100) = 0.0945 com o fill; era 0.10 com a referência.
        assert registrado["pnl"] == pytest.approx(0.0945)

    def test_banco_grava_o_preco_executado(self, monkeypatch):
        """`preco_saida` no banco tem que ser o fill — senão o registro diz que
        saiu num preço em que não saiu."""
        gravado = {}
        ex, e = self._exec_sim(monkeypatch, preco_fill=109.45)
        monkeypatch.setattr(ex.health, "set_gauge", lambda n, v: None)
        monkeypatch.setattr(ex.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(
            ex.database,
            "salvar_sinal",
            lambda tipo, preco, motivo, **kw: gravado.__setitem__("sinal", (tipo, preco)),
        )
        monkeypatch.setattr(
            ex.database,
            "atualizar_sinal_fechamento",
            lambda sid, preco, pnl, pct, barreira: gravado.__setitem__("fechamento", (preco, pnl)),
        )
        e.fechar_posicao(110.0, "Take Profit Final")

        assert gravado["sinal"][1] == pytest.approx(109.45)
        assert gravado["fechamento"][0] == pytest.approx(109.45)
        assert gravado["fechamento"][1] == pytest.approx(0.09, abs=0.005)

    def test_stop_com_slippage_contra_registra_prejuizo_maior(self, monkeypatch):
        """O caso que mais importa: stop dispara em 90 mas preenche em 89.2. O
        registro tem que doer o valor real, não o do gatilho."""
        registrado = {}
        ex, e = self._exec_sim(monkeypatch, preco_fill=89.2)
        monkeypatch.setattr(ex.health, "set_gauge", lambda n, v: None)
        monkeypatch.setattr(ex.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(
            ex.gestao_risco,
            "registrar_resultado",
            lambda pnl: registrado.__setitem__("pnl", pnl),
        )
        e.fechar_posicao(90.0, "Stop Loss")

        # 0.01 * (89.2 - 100) = -0.108; com a referência seria -0.10 (otimista)
        assert registrado["pnl"] == pytest.approx(-0.108)


class TestPrecoMedioFill:
    """A resposta da Binance não entrega o preço de fill num campo só.

    Numa MARKET, `price` vem "0.00000000" (o campo é o preço *pedido*, e MARKET
    não pede preço). Ler `price` cegamente faria a correção do PnL valer só em
    simulação — em modo real cairia no fallback e nada mudaria.
    """

    def test_market_real_ignora_price_zerado(self):
        from executor import preco_medio_fill

        resp = {
            "status": "FILLED",
            "price": "0.00000000",
            "executedQty": "0.01000000",
            "cummulativeQuoteQty": "1094.50000000",
        }
        assert preco_medio_fill(resp, 110.0) == pytest.approx(109450.0)

    def test_cqq_tem_prioridade_sobre_price(self):
        """LIMIT que cruza: `price` é o limite, o fill sai melhor."""
        from executor import preco_medio_fill

        resp = {"price": "100.0", "executedQty": "2.0", "cummulativeQuoteQty": "198.0"}
        assert preco_medio_fill(resp, 100.0) == pytest.approx(99.0)

    def test_media_ponderada_de_fills(self):
        from executor import preco_medio_fill

        resp = {
            "price": "0.00000000",
            "fills": [{"qty": "1.0", "price": "100.0"}, {"qty": "3.0", "price": "104.0"}],
        }
        assert preco_medio_fill(resp, 50.0) == pytest.approx(103.0)

    def test_simulacao_usa_price(self):
        from executor import preco_medio_fill

        assert preco_medio_fill({"price": 64500.0, "executedQty": 0.01}, 1.0) == 64500.0

    def test_maker_medio_preservado(self):
        """_entrar_maker já calcula custo/preenchido e devolve em `price`; o
        helper não pode sobrescrever isso."""
        from executor import preco_medio_fill

        resp = {"status": "FILLED", "executedQty": 0.5, "price": 101.25, "maker": True}
        assert preco_medio_fill(resp, 100.0) == pytest.approx(101.25)

    def test_fallback_quando_nao_ha_nada_utilizavel(self):
        from executor import preco_medio_fill

        assert preco_medio_fill({"status": "FILLED"}, 123.0) == 123.0
        assert preco_medio_fill({"price": "0.00000000"}, 123.0) == 123.0
        assert preco_medio_fill(None, 123.0) == 123.0

    def test_campos_corrompidos_nao_estouram(self):
        from executor import preco_medio_fill

        assert preco_medio_fill({"price": "abc", "executedQty": None}, 77.0) == 77.0
