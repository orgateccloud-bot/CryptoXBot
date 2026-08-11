"""
Testes — integração dry run do trend-following (estrategias/trend_live.py)
===========================================================================
Duas coisas precisam ser verdade para este experimento significar alguma coisa:

1. **SEGURANÇA** — a estratégia REPROVOU no hold-out. Tem que ser impossível
   rodá-la com capital real, mesmo por acidente (trava de boot + trava no
   caminho do dinheiro).
2. **PARIDADE** — o sinal ao vivo tem que ser o MESMO que o backtest produziu.
   Se divergir, medir slippage contra o backtest não mede nada. Inclui a
   armadilha central: a Binance devolve a vela EM FORMAÇÃO como último candle,
   e usá-la seria look-ahead.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from backtesting import trend_following as tf  # noqa: E402
from estrategias import trend_live as tl  # noqa: E402


def _mock_klines(monkeypatch, closes):
    """Simula data.klines.obter_klines: o ÚLTIMO elemento é a vela viva."""
    chamadas = {}

    def fake(symbol, intervalo, limit):
        chamadas["args"] = (symbol, intervalo, limit)
        return {
            "abertura": list(closes),
            "maxima": list(closes),
            "minima": list(closes),
            "fechamento": list(closes),
            "volume": [1.0] * len(closes),
        }

    monkeypatch.setattr(tl, "obter_klines", fake)
    return chamadas


# ── 1. Anti-look-ahead: a vela em formação é descartada ────────


class TestVelaEmFormacao:
    def test_descarta_ultimo_candle(self, monkeypatch):
        # 40 closes estáveis + uma vela viva absurda no fim. Se ela entrasse no
        # cálculo, o preço de referência seria 999999 e dispararia COMPRA.
        closes = [100.0] * 40 + [999999.0]
        _mock_klines(monkeypatch, closes)
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)
        assert r["preco_ref"] == 100.0  # o close FECHADO, não a vela viva
        assert r["sinal"] == "AGUARDAR"

    def test_vela_viva_nao_contamina_o_canal(self, monkeypatch):
        # Vela viva com pico altíssimo não pode elevar o Donchian superior.
        closes = [100.0] * 40 + [50000.0]
        _mock_klines(monkeypatch, closes)
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)
        assert r["canal_alto"] == 100.0

    def test_dados_insuficientes_devolve_aguardar(self, monkeypatch):
        _mock_klines(monkeypatch, [100.0] * 5)
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)
        assert r["sinal"] == "AGUARDAR" and r["preco_ref"] is None

    def test_fetch_indisponivel_devolve_aguardar(self, monkeypatch):
        monkeypatch.setattr(tl, "obter_klines", lambda *a, **k: None)
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)
        assert r["sinal"] == "AGUARDAR" and r["preco_ref"] is None


# ── 2. Paridade com o backtest ─────────────────────────────────


class TestParidadeComBacktest:
    def test_mesma_funcao_de_niveis(self):
        """Não é 'uma reimplementação equivalente' — é a MESMA função."""
        assert tl.donchian_niveis is tf.donchian_niveis

    def test_niveis_batem_com_o_backtest(self, monkeypatch):
        rng = np.random.default_rng(7)
        fechados = list(100 + np.cumsum(rng.normal(0, 2, 60)))
        _mock_klines(monkeypatch, fechados + [123456.0])  # + vela viva
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)

        arr = np.asarray(fechados)
        up, _ = tf.donchian_niveis(arr, tf.N_ENTRADA)
        _, low = tf.donchian_niveis(arr, tf.M_SAIDA)
        i = len(arr) - 1
        assert r["canal_alto"] == pytest.approx(float(up[i]))
        assert r["canal_baixo"] == pytest.approx(float(low[i]))
        assert r["preco_ref"] == pytest.approx(float(arr[i]))

    def test_compra_no_mesmo_ponto_que_o_backtest_entraria(self, monkeypatch):
        # Série que o backtest reconhece como rompimento no último candle.
        fechados = [100.0] * 40 + [150.0]
        _mock_klines(monkeypatch, fechados + [151.0])
        r = tl.sinal_trend("BTCUSDT", tem_posicao=False)
        assert r["sinal"] == "COMPRA"

        # O backtest, sobre os MESMOS closes fechados, também abre um trade.
        res = tf.simular_trend(np.asarray(fechados), taxa=0.0, slippage=0.0)
        assert res["n_trades"] == 1

    def test_fechar_quando_rompe_o_canal_inferior(self, monkeypatch):
        # sobe e depois despenca abaixo do fundo dos M anteriores
        fechados = [100.0] * 25 + [150.0] * 12 + [90.0]
        _mock_klines(monkeypatch, fechados + [89.0])
        r = tl.sinal_trend("BTCUSDT", tem_posicao=True)
        assert r["sinal"] == "FECHAR"
        assert r["preco_ref"] < r["canal_baixo"]

    def test_em_posicao_sem_rompimento_aguarda(self, monkeypatch):
        fechados = [100.0] * 25 + [150.0] * 12
        _mock_klines(monkeypatch, fechados + [151.0])
        r = tl.sinal_trend("BTCUSDT", tem_posicao=True)
        assert r["sinal"] == "AGUARDAR"

    def test_nao_compra_estando_em_posicao(self, monkeypatch):
        """Rompimento de topo com posição aberta nunca vira 2ª entrada —
        mesma regra 'uma posição por vez' do backtest."""
        fechados = [100.0] * 40 + [150.0]
        _mock_klines(monkeypatch, fechados + [151.0])
        assert tl.sinal_trend("BTCUSDT", tem_posicao=True)["sinal"] != "COMPRA"


# ── 3. Trava de segurança (estratégia reprovada) ───────────────


class TestTravaDeSeguranca:
    def test_boot_recusa_trend_com_real(self):
        with pytest.raises(SystemExit):
            main._validar_trend_so_em_simulacao(modo_trend=True, simulacao=False)

    def test_boot_aceita_trend_em_simulacao(self):
        main._validar_trend_so_em_simulacao(modo_trend=True, simulacao=True)

    def test_boot_nao_interfere_no_modo_normal(self):
        main._validar_trend_so_em_simulacao(modo_trend=False, simulacao=False)

    def test_caminho_do_dinheiro_recusa_executor_real(self, monkeypatch):
        """Defesa em profundidade: mesmo contornando a trava de boot, nenhuma
        ordem real sai."""

        class ExecReal:
            simulacao = False
            posicao = None

            def get_preco(self):
                raise AssertionError("nao pode nem consultar preco")

            def abrir_long(self, *a, **k):
                raise AssertionError("ORDEM REAL COM ESTRATEGIA REPROVADA")

        main._trend_abrir(
            "BTCUSDT", ExecReal(), {"preco_ref": 100.0, "canal_baixo": 90.0}
        )  # não levanta = recusou antes de tocar em qualquer coisa


# ── 4. Uma decisão por candle fechado ──────────────────────────


class TestUmaDecisaoPorCandle:
    def test_bucket_muda_a_cada_intervalo(self, monkeypatch):
        t = {"v": 1_700_000_000.0}
        monkeypatch.setattr(main.time, "time", lambda: t["v"])
        b0 = main._bucket_candle("1d")
        t["v"] += 3600  # +1h
        assert main._bucket_candle("1d") == b0
        t["v"] += 86400  # +1 dia
        assert main._bucket_candle("1d") == b0 + 1

    def test_segundo_ciclo_no_mesmo_candle_e_no_op(self, monkeypatch):
        """Com --intervalo 15, o mesmo candle diário é visto ~96x. Só a
        primeira avaliação decide — senão o bot reentraria no mesmo dia em que
        saiu, algo que o backtest nunca faz."""
        chamadas = {"n": 0}

        def fake_sinal(symbol, tem_posicao, intervalo="1d"):
            chamadas["n"] += 1
            return {
                "sinal": "AGUARDAR",
                "preco_ref": 100.0,
                "canal_alto": 110.0,
                "canal_baixo": 90.0,
                "motivo": "x",
            }

        monkeypatch.setitem(
            sys.modules["estrategias.trend_live"].__dict__, "sinal_trend", fake_sinal
        )
        monkeypatch.setattr(main, "_trend_ultimo_bucket", {})

        class ExecFake:
            simulacao = True
            posicao = None

        main._ciclo_trend("BTCUSDT", ExecFake())
        main._ciclo_trend("BTCUSDT", ExecFake())
        assert chamadas["n"] == 1

    def test_dados_indisponiveis_nao_queimam_o_candle(self, monkeypatch):
        """Se o fetch falhar, o bucket NÃO pode ser marcado — senão uma falha
        de rede às 00h01 cancelaria a decisão do dia inteiro."""
        chamadas = {"n": 0}

        def fake_sinal(symbol, tem_posicao, intervalo="1d"):
            chamadas["n"] += 1
            return {
                "sinal": "AGUARDAR",
                "preco_ref": None,
                "canal_alto": None,
                "canal_baixo": None,
                "motivo": "indisponivel",
            }

        monkeypatch.setitem(
            sys.modules["estrategias.trend_live"].__dict__, "sinal_trend", fake_sinal
        )
        monkeypatch.setattr(main, "_trend_ultimo_bucket", {})

        class ExecFake:
            simulacao = True
            posicao = None

        main._ciclo_trend("BTCUSDT", ExecFake())
        main._ciclo_trend("BTCUSDT", ExecFake())
        assert chamadas["n"] == 2


# ── 5. Executor em modo_trend: sem alvo, sem trailing fixo ─────


class TestExecutorModoTrend:
    def test_modo_trend_desliga_alvos(self, monkeypatch):
        """O `target2 = entrada*1.05` hardcoded cortaria a cauda direita — que
        é literalmente todo o edge do trend-following."""
        import executor as ex

        e = ex.Executor(simulacao=True, symbol="BTCUSDT", modo_trend=True)
        monkeypatch.setattr(
            e, "_enviar_ordem", lambda *a, **k: {"status": "FILLED", "price": 100.0}
        )
        monkeypatch.setattr(e, "_monitorar", lambda: None)
        monkeypatch.setattr(ex.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(ex.database, "marcar_sinal_executado", lambda *a, **k: None)
        monkeypatch.setattr(ex.gestao_risco, "get_saldo_usdt", lambda: 1000.0)
        monkeypatch.setattr(ex.gestao_risco, "incrementar_posicoes_abertas", lambda: None)
        monkeypatch.setattr(ex.gestao_risco, "persistir_estado", lambda: None)

        assert e.abrir_long(100.0, 0.01, 90.0, float("inf")) is True
        assert e.posicao["target1"] == float("inf")
        assert e.posicao["target2"] == float("inf")

    def test_modo_padrao_mantem_alvo_de_5pct(self, monkeypatch):
        import executor as ex

        e = ex.Executor(simulacao=True, symbol="BTCUSDT")
        assert e.modo_trend is False
        monkeypatch.setattr(
            e, "_enviar_ordem", lambda *a, **k: {"status": "FILLED", "price": 100.0}
        )
        monkeypatch.setattr(e, "_monitorar", lambda: None)
        monkeypatch.setattr(ex.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(ex.database, "marcar_sinal_executado", lambda *a, **k: None)
        monkeypatch.setattr(ex.gestao_risco, "get_saldo_usdt", lambda: 1000.0)
        monkeypatch.setattr(ex.gestao_risco, "incrementar_posicoes_abertas", lambda: None)
        monkeypatch.setattr(ex.gestao_risco, "persistir_estado", lambda: None)

        e.abrir_long(100.0, 0.01, 90.0, 103.0)
        assert e.posicao["target1"] == 103.0
        assert e.posicao["target2"] == pytest.approx(105.0)

    def test_alvo_infinito_nunca_dispara_no_monitor(self):
        """avaliar_tick_monitor com alvos +inf: nem parcial nem alvo final."""
        from executor import avaliar_tick_monitor

        d = avaliar_tick_monitor(
            entrada=100.0,
            stop_atual=90.0,
            target1=float("inf"),
            target2=float("inf"),
            parcial_feita=False,
            preco=100_000.0,
            preco_pico=100_000.0,
            trailing_ativacao=float("inf"),
        )
        assert d["fechar_parcial"] is False
        assert d["fechar_total"] is None
        assert d["novo_stop_trailing"] is None  # trailing fixo desligado

    def test_stop_ainda_protege_em_modo_trend(self):
        from executor import avaliar_tick_monitor

        d = avaliar_tick_monitor(
            entrada=100.0,
            stop_atual=90.0,
            target1=float("inf"),
            target2=float("inf"),
            parcial_feita=False,
            preco=89.0,
            preco_pico=100.0,
            trailing_ativacao=float("inf"),
        )
        assert d["fechar_total"] == "Stop Loss" and d["encerrar"] is True
