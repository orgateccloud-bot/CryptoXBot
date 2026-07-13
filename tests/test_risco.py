"""
Testes — Gestão de Risco (risco.py)
====================================
Suíte hermética (sem rede, sem banco) para o módulo de risco do BinanceXBot.

Cobre:
  - kelly()              — Kelly fracionado, clamp >= 0, guarda de entradas inválidas
  - calcular_tamanho()   — risco em USDT, teto de 20%, proteção divisão por zero
  - validar_trade()      — todos os branches de aprovação/rejeição
  - _resetar_se_novo_dia — troca de data e zera pnl_dia
  - registrar_resultado  — acumula pnl_dia e persiste
  - get_saldo_usdt       — saldo via requests; fallback 0.0 em erro

Todas as dependências externas (database.*, requests, verificar_volatilidade)
são mockadas via monkeypatch. O estado global risco._estado_risco é restaurado
após cada teste por uma fixture autouse.
"""

import os
import sys
import threading
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import risco

# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolar_estado_risco(monkeypatch):
    """Isola o estado global e neutraliza persistência/carregamento de banco.

    - Salva e restaura risco._estado_risco e _estado_carregado.
    - Mocka database.salvar_risk_state / carregar_risk_state para no-ops,
      garantindo testes sem banco e sem efeitos colaterais entre testes.
    """
    estado_original = dict(risco._estado_risco)
    carregado_original = risco._estado_carregado

    # Marca como já carregado para que _carregar_estado_persistido() não toque
    # no banco; mesmo assim mockamos database para hermeticidade total.
    risco._estado_carregado = True

    monkeypatch.setattr(risco.database, "salvar_risk_state", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(risco.database, "carregar_risk_state", lambda *a, **k: None, raising=True)

    yield

    risco._estado_risco.clear()
    risco._estado_risco.update(estado_original)
    risco._estado_carregado = carregado_original


def _set_estado(**kwargs):
    """Helper para ajustar campos do estado global de risco."""
    risco._estado_risco.update(kwargs)


# ══════════════════════════════════════════════════════════════
# 1. kelly()
# ══════════════════════════════════════════════════════════════


class TestKelly:

    def test_valor_conhecido_fracionado(self):
        # win_rate=0.6, R=2.0 -> kelly_puro = 0.6 - 0.4/2 = 0.4
        # fracionado: 0.4 * 0.25 = 0.10
        assert risco.kelly(0.6, 2.0) == pytest.approx(0.10)

    def test_aplica_kelly_fator(self):
        # win_rate=0.7, R=2.0 -> puro = 0.7 - 0.3/2 = 0.55
        # 0.55 * 0.25 = 0.1375
        assert risco.kelly(0.7, 2.0) == pytest.approx(0.1375)

    def test_win_rate_baixo_clamp_zero(self):
        # win_rate=0.2, R=2.0 -> puro = 0.2 - 0.8/2 = -0.2 -> clamp 0 -> 0.0
        assert risco.kelly(0.2, 2.0) == 0.0

    def test_win_rate_exatamente_no_limiar(self):
        # win_rate tal que puro == 0: W - (1-W)/R = 0, R=2 -> W = 1/3
        assert risco.kelly(1 / 3, 2.0) == 0.0

    def test_win_rate_zero_retorna_max_risco(self):
        # Guarda: win_rate <= 0 -> MAX_RISCO_POR_TRADE (não passa pela fórmula)
        assert risco.kelly(0.0, 2.0) == risco.MAX_RISCO_POR_TRADE

    def test_ratio_rr_zero_retorna_max_risco(self):
        assert risco.kelly(0.6, 0.0) == risco.MAX_RISCO_POR_TRADE

    def test_ratio_rr_negativo_retorna_max_risco(self):
        assert risco.kelly(0.6, -1.0) == risco.MAX_RISCO_POR_TRADE

    def test_default_ratio_rr(self):
        # default ratio_rr=2.0; mesmo resultado de chamada explícita
        assert risco.kelly(0.6) == pytest.approx(risco.kelly(0.6, 2.0))

    def test_resultado_arredondado_4_casas(self):
        # win_rate=0.55, R=3.0 -> puro = 0.55 - 0.45/3 = 0.4
        # 0.4 * 0.25 = 0.1 -> round(_,4)
        valor = risco.kelly(0.55, 3.0)
        assert valor == round(valor, 4)


# ══════════════════════════════════════════════════════════════
# 2. calcular_tamanho()
# ══════════════════════════════════════════════════════════════


class TestCalcularTamanho:

    def test_tamanho_basico_com_fator_explicito(self):
        # capital=1000, fator=0.02 -> risco_usdt = 20
        # preco=100, stop=98 -> distancia=2 -> tamanho_btc=10, tamanho_usdt=1000
        # teto 20% do capital = 200 -> tamanho_usdt=200 -> tamanho_btc=2.0
        assert risco.calcular_tamanho(1000, 100, 98, fator_risco=0.02) == 2.0

    def test_respeita_teto_20pct(self):
        # Sem teto, posicao seria gigante; deve ser limitada a 20% do capital
        # capital=1000, fator=0.02 -> risco=20; preco=100, stop=99.9 -> dist=0.1
        # tamanho_btc=200, tamanho_usdt=20000 -> teto 200 -> tamanho_btc=2.0
        tam = risco.calcular_tamanho(1000, 100, 99.9, fator_risco=0.02)
        tamanho_usdt = tam * 100
        assert tamanho_usdt == pytest.approx(1000 * 0.20)

    def test_tamanho_abaixo_do_teto(self):
        # capital=1000, fator=0.01 -> risco=10; preco=100, stop=90 -> dist=10
        # tamanho_btc=1.0, tamanho_usdt=100 (< teto 200) -> mantém 1.0
        assert risco.calcular_tamanho(1000, 100, 90, fator_risco=0.01) == 1.0

    def test_protecao_divisao_por_zero_stop_igual_preco(self):
        # distancia_stop == 0 -> retorna 0.0, não lança
        assert risco.calcular_tamanho(1000, 100, 100, fator_risco=0.02) == 0.0

    def test_stop_acima_do_preco_usa_valor_absoluto(self):
        # distancia usa abs(); stop=102, preco=100 -> dist=2 (igual a stop=98)
        venda = risco.calcular_tamanho(1000, 100, 102, fator_risco=0.02)
        compra = risco.calcular_tamanho(1000, 100, 98, fator_risco=0.02)
        assert venda == compra

    def test_fator_none_usa_kelly_do_banco(self, monkeypatch):
        # Com fator None, usa kelly_do_banco() limitado a MAX_RISCO_POR_TRADE.
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.50)
        # fator efetivo = min(0.50, 0.02) = 0.02
        # capital=1000 -> risco=20; preco=100, stop=90 -> dist=10
        # tamanho_btc=2.0, tamanho_usdt=200 (= teto) -> 2.0
        assert risco.calcular_tamanho(1000, 100, 90) == 2.0

    def test_resultado_arredondado_6_casas(self):
        tam = risco.calcular_tamanho(137, 100, 97, fator_risco=0.02)
        assert tam == round(tam, 6)


# ══════════════════════════════════════════════════════════════
# 3. _resetar_se_novo_dia()
# ══════════════════════════════════════════════════════════════


class TestResetarSeNovoDia:

    def test_novo_dia_zera_pnl_e_atualiza_data(self):
        _set_estado(data_dia="2000-01-01", pnl_dia=123.4, bloqueado=True, motivo_bloqueio="algo")
        risco._resetar_se_novo_dia()
        assert risco._estado_risco["data_dia"] == str(date.today())
        assert risco._estado_risco["pnl_dia"] == 0.0
        assert risco._estado_risco["bloqueado"] is False
        assert risco._estado_risco["motivo_bloqueio"] == ""

    def test_mesmo_dia_nao_zera_pnl(self):
        _set_estado(
            data_dia=str(date.today()), pnl_dia=77.0, bloqueado=True, motivo_bloqueio="trava"
        )
        risco._resetar_se_novo_dia()
        # Mesmo dia: nada é resetado
        assert risco._estado_risco["pnl_dia"] == 77.0
        assert risco._estado_risco["bloqueado"] is True

    def test_nao_reseta_capital_inicio_dia(self):
        _set_estado(data_dia="1999-12-31", pnl_dia=10.0, capital_inicio_dia=500.0)
        risco._resetar_se_novo_dia()
        # capital é preservado entre dias
        assert risco._estado_risco["capital_inicio_dia"] == 500.0


# ══════════════════════════════════════════════════════════════
# 4. registrar_resultado()
# ══════════════════════════════════════════════════════════════


class TestRegistrarResultado:

    def test_acumula_pnl_dia(self):
        _set_estado(data_dia=str(date.today()), pnl_dia=0.0)
        risco.registrar_resultado(15.0)
        risco.registrar_resultado(-5.0)
        assert risco._estado_risco["pnl_dia"] == pytest.approx(10.0)

    def test_chama_persistir_estado(self, monkeypatch):
        chamado = {"n": 0}
        monkeypatch.setattr(
            risco, "persistir_estado", lambda: chamado.__setitem__("n", chamado["n"] + 1)
        )
        _set_estado(data_dia=str(date.today()), pnl_dia=0.0)
        risco.registrar_resultado(20.0)
        assert chamado["n"] >= 1

    def test_novo_dia_zera_antes_de_registrar(self):
        # Se for novo dia, pnl é zerado e só então soma o resultado atual.
        _set_estado(data_dia="2000-01-01", pnl_dia=999.0)
        risco.registrar_resultado(8.0)
        assert risco._estado_risco["pnl_dia"] == pytest.approx(8.0)


# ══════════════════════════════════════════════════════════════
# 4b. incrementar/decrementar_posicoes_abertas()
# ══════════════════════════════════════════════════════════════


class TestContadorPosicoesAbertas:

    def test_incrementa(self):
        _set_estado(posicoes_abertas=0)
        risco.incrementar_posicoes_abertas()
        assert risco._estado_risco["posicoes_abertas"] == 1

    def test_decrementa(self):
        _set_estado(posicoes_abertas=1)
        risco.decrementar_posicoes_abertas()
        assert risco._estado_risco["posicoes_abertas"] == 0

    def test_decrementa_nao_fica_negativo(self):
        # defesa contra decremento espurio (ex: fechar_posicao chamado sem
        # abrir_long correspondente) -- nunca deve ficar negativo.
        _set_estado(posicoes_abertas=0)
        risco.decrementar_posicoes_abertas()
        assert risco._estado_risco["posicoes_abertas"] == 0

    def test_incrementos_concorrentes_nao_perdem_contagem(self):
        # regressao para a race condition original: 50 threads incrementando
        # ao mesmo tempo devem produzir exatamente 50, nao menos (o que
        # aconteceria com um += sem lock sob contencao real).
        _set_estado(posicoes_abertas=0)
        threads = [threading.Thread(target=risco.incrementar_posicoes_abertas) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert risco._estado_risco["posicoes_abertas"] == 50


# ══════════════════════════════════════════════════════════════
# 5. validar_trade()
# ══════════════════════════════════════════════════════════════


class TestValidarTrade:

    @pytest.fixture(autouse=True)
    def _estado_dia_atual(self, monkeypatch):
        # Estado base: dia atual, não bloqueado, sem posições, volatilidade calma.
        _set_estado(
            data_dia=str(date.today()),
            pnl_dia=0.0,
            bloqueado=False,
            motivo_bloqueio="",
            posicoes_abertas=0,
            capital_inicio_dia=None,
        )
        # Volatilidade calma por padrão (sem rede)
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.0)
        # kelly_do_banco usado em calcular_tamanho e no retorno feliz
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.02)

    def test_bloqueado_retorna_pode_false(self):
        _set_estado(bloqueado=True, motivo_bloqueio="manual")
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "manual" in r["motivo"]
        assert r["tamanho_btc"] == 0

    def test_saldo_insuficiente(self):
        r = risco.validar_trade("COMPRA", 68000, 9.99)
        assert r["pode"] is False
        assert "< $10" in r["motivo"]

    def test_saldo_no_limite_10_passa_da_checagem(self):
        # capital == 10 não dispara "< 10"; deve seguir e aprovar (caminho feliz)
        r = risco.validar_trade("COMPRA", 68000, 10)
        assert r["pode"] is True

    def test_drawdown_diario_bloqueia(self):
        # pnl_dia / capital_inicio_dia <= -MAX_DRAWDOWN_DIARIO (-0.05)
        _set_estado(capital_inicio_dia=1000.0, pnl_dia=-60.0)  # -6%
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "drawdown" in r["motivo"].lower()
        # efeito colateral: estado fica bloqueado
        assert risco._estado_risco["bloqueado"] is True

    def test_drawdown_no_limiar_exato_bloqueia(self):
        # dd == -0.05 exatamente -> <= -0.05 é verdadeiro -> bloqueia
        _set_estado(capital_inicio_dia=1000.0, pnl_dia=-50.0)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False

    def test_drawdown_abaixo_do_limiar_nao_bloqueia(self):
        # dd = -0.04 (> -0.05) -> não bloqueia por drawdown
        _set_estado(capital_inicio_dia=1000.0, pnl_dia=-40.0)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is True

    def test_volatilidade_extrema_bloqueia(self, monkeypatch):
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.09)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "olatil" in r["motivo"]  # "Volatilidade"

    def test_volatilidade_no_limite_nao_bloqueia(self, monkeypatch):
        # vol == 0.08 não é > 0.08 -> passa
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.08)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is True

    def test_posicao_ja_aberta_bloqueia(self):
        _set_estado(posicoes_abertas=1)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "Posicao" in r["motivo"] or "posic" in r["motivo"].lower()

    def test_caminho_feliz_compra(self):
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is True
        assert r["tamanho_btc"] > 0
        assert "risco_usdt" in r
        assert "fator_kelly" in r
        # capital_inicio_dia foi inicializado
        assert risco._estado_risco["capital_inicio_dia"] == 1000

    def test_caminho_feliz_venda(self):
        r = risco.validar_trade("VENDA", 68000, 1000)
        assert r["pode"] is True
        assert r["tamanho_btc"] > 0

    def test_tamanho_zerado_reprovado(self, monkeypatch):
        # Força calcular_tamanho a retornar 0 -> "Tamanho calculado zerado"
        monkeypatch.setattr(risco, "calcular_tamanho", lambda *a, **k: 0.0)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "zerado" in r["motivo"].lower()

    def test_nao_acessa_rede_nem_banco(self, monkeypatch):
        # Garante hermeticidade: se requests for usado, falha o teste.
        def _boom(*a, **k):
            raise AssertionError("Acesso de rede não deveria ocorrer")

        monkeypatch.setattr(risco.requests, "get", _boom)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is True


# ══════════════════════════════════════════════════════════════
# 6. get_saldo_usdt()
# ══════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestGetSaldoUsdt:

    def test_retorna_saldo_usdt(self, monkeypatch):
        payload = {
            "balances": [
                {"asset": "BTC", "free": "0.5"},
                {"asset": "USDT", "free": "1234.56"},
            ]
        }
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(payload))
        assert risco.get_saldo_usdt() == pytest.approx(1234.56)

    def test_sem_usdt_nas_balances_retorna_zero(self, monkeypatch):
        payload = {"balances": [{"asset": "BTC", "free": "0.5"}]}
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(payload))
        # Loop não encontra USDT -> cai no return 0.0 final
        assert risco.get_saldo_usdt() == 0.0

    def test_erro_de_rede_retorna_zero(self, monkeypatch):
        def _boom(*a, **k):
            raise ConnectionError("sem rede")

        monkeypatch.setattr(risco.requests, "get", _boom)
        assert risco.get_saldo_usdt() == 0.0

    def test_resposta_sem_balances_retorna_zero(self, monkeypatch):
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp({}))
        assert risco.get_saldo_usdt() == 0.0


# ══════════════════════════════════════════════════════════════
# 7. get_saldo_btc()
# ══════════════════════════════════════════════════════════════


class TestGetSaldoBtc:

    def test_retorna_saldo_btc(self, monkeypatch):
        payload = {
            "balances": [
                {"asset": "USDT", "free": "1000.0"},
                {"asset": "BTC", "free": "0.12345678"},
            ]
        }
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(payload))
        assert risco.get_saldo_btc() == pytest.approx(0.12345678)

    def test_sem_btc_nas_balances_retorna_zero(self, monkeypatch):
        payload = {"balances": [{"asset": "USDT", "free": "500.0"}]}
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(payload))
        assert risco.get_saldo_btc() == 0.0

    def test_erro_de_rede_retorna_zero(self, monkeypatch):
        def _boom(*a, **k):
            raise ConnectionError("sem rede")

        monkeypatch.setattr(risco.requests, "get", _boom)
        assert risco.get_saldo_btc() == 0.0

    def test_resposta_sem_balances_retorna_zero(self, monkeypatch):
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp({}))
        assert risco.get_saldo_btc() == 0.0


# ══════════════════════════════════════════════════════════════
# 8. verificar_volatilidade()
# ══════════════════════════════════════════════════════════════


class TestVerificarVolatilidade:
    # P1-5: verificar_volatilidade delega para data.klines.obter_klines
    # (via risco.obter_klines) -- mockar isso diretamente, não mais
    # risco.requests.get (que não é chamado por este caminho desde a
    # migração: o fetch real agora vive em data/klines.py).

    def test_calcula_variacao_percentual(self, monkeypatch):
        # abertura = dados["abertura"][-2] = 100, fechamento = dados["fechamento"][-1] = 108
        # |108 - 100| / 100 = 0.08
        dados = {"abertura": [100.0, 104.0], "fechamento": [105.0, 108.0]}
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: dados)
        assert risco.verificar_volatilidade() == pytest.approx(0.08)

    def test_usa_valor_absoluto_em_queda(self, monkeypatch):
        # fechamento < abertura -> abs garante valor positivo
        dados = {"abertura": [100.0, 104.0], "fechamento": [105.0, 90.0]}
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: dados)
        assert risco.verificar_volatilidade() == pytest.approx(0.10)

    def test_klines_insuficientes_retorna_zero(self, monkeypatch):
        # len(fechamento) < 2 -> 0.0
        dados = {"abertura": [100.0], "fechamento": [105.0]}
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: dados)
        assert risco.verificar_volatilidade() == 0.0

    def test_klines_vazio_retorna_zero(self, monkeypatch):
        dados = {"abertura": [], "fechamento": []}
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: dados)
        assert risco.verificar_volatilidade() == 0.0

    def test_sem_dados_retorna_zero(self, monkeypatch):
        # obter_klines() retorna None (falha total, sem cache anterior)
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: None)
        assert risco.verificar_volatilidade() == 0.0

    def test_erro_de_rede_retorna_zero(self, monkeypatch):
        def _boom(*a, **k):
            raise ConnectionError("sem rede")

        monkeypatch.setattr(risco, "obter_klines", _boom)
        assert risco.verificar_volatilidade() == 0.0

    def test_abertura_zero_retorna_zero_sem_lancar(self, monkeypatch):
        # divisão por abertura=0 lançaria ZeroDivisionError -- o except
        # geral cobre esse caso tal como cobria o payload malformado antes.
        dados = {"abertura": [0.0, 0.0], "fechamento": [0.0, 0.0]}
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: dados)
        assert risco.verificar_volatilidade() == 0.0

    def test_repassa_symbol_intervalo_e_limite_corretos(self, monkeypatch):
        capturado = {}

        def _fake(symbol, intervalo, limit):
            capturado["args"] = (symbol, intervalo, limit)
            return {"abertura": [100.0, 100.0], "fechamento": [100.0, 100.0]}

        monkeypatch.setattr(risco, "obter_klines", _fake)
        risco.verificar_volatilidade("ETHUSDT")
        assert capturado["args"] == ("ETHUSDT", "1h", 2)


# ══════════════════════════════════════════════════════════════
# 9. kelly_do_banco()
# ══════════════════════════════════════════════════════════════


class TestKellyDoBanco:

    def test_sem_historico_suficiente_retorna_max(self, monkeypatch):
        # menos de 10 rows -> MAX_RISCO_POR_TRADE
        monkeypatch.setattr(
            risco.database, "sinais_executados", lambda *a, **k: [{"tipo": "COMPRA"}] * 5
        )
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_zero_rows_retorna_max(self, monkeypatch):
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: [])
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_calcula_kelly_a_partir_do_winrate(self, monkeypatch):
        # P1-3: win-rate real via pnl_usdt (trades fechados) -- 12 rows, 6
        # com pnl_usdt>0 -> wr = 0.5 -> kelly(0.5, 2.0)
        rows = [{"tipo": "COMPRA", "pnl_usdt": 50.0}] * 6 + [
            {"tipo": "COMPRA", "pnl_usdt": -30.0}
        ] * 6
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        assert risco.kelly_do_banco() == pytest.approx(risco.kelly(0.5, 2.0))

    def test_todos_ganhos_winrate_um(self, monkeypatch):
        rows = [{"tipo": "COMPRA", "pnl_usdt": 25.0}] * 20
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        # wr = 1.0 -> kelly(1.0, 2.0) = (1 - 0)*0.25 = 0.25
        assert risco.kelly_do_banco() == pytest.approx(risco.kelly(1.0, 2.0))

    def test_excecao_no_banco_retorna_max(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(risco.database, "sinais_executados", _boom)
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_rows_sem_chave_pnl_usdt_contam_como_perda(self, monkeypatch):
        # s.get("pnl_usdt") ausente -> None -> (None or 0) = 0 -> nao conta como ganho
        rows = [{"tipo": "COMPRA"}] * 10
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        # wr = 0 -> kelly(0.0, ...) -> guard win_rate<=0 -> MAX_RISCO_POR_TRADE
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_pnl_usdt_zero_nao_conta_como_ganho(self, monkeypatch):
        # empate exato (pnl_usdt==0, ex: fees zeraram um trade neutro) nao
        # deve contar como vitoria -- so pnl_usdt>0 conta.
        rows = [{"pnl_usdt": 0.0}] * 10
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_pnl_usdt_negativo_nao_conta_como_ganho(self, monkeypatch):
        rows = [{"pnl_usdt": 50.0}] * 3 + [{"pnl_usdt": -10.0}] * 7
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        # wr = 3/10 = 0.3 -> kelly(0.3, 2.0)
        assert risco.kelly_do_banco() == pytest.approx(risco.kelly(0.3, 2.0))


# ══════════════════════════════════════════════════════════════
# 10. persistir_estado() / _carregar_estado_persistido()
# ══════════════════════════════════════════════════════════════


class TestPersistencia:

    def test_persistir_chama_database(self, monkeypatch):
        capturado = {}
        monkeypatch.setattr(
            risco.database, "salvar_risk_state", lambda estado: capturado.update(estado)
        )
        _set_estado(pnl_dia=42.0)
        risco.persistir_estado()
        assert capturado["pnl_dia"] == 42.0

    def test_persistir_engole_excecao(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db indisponivel")

        monkeypatch.setattr(risco.database, "salvar_risk_state", _boom)
        # Não deve propagar
        risco.persistir_estado()

    def test_carregar_atualiza_estado_quando_salvo(self, monkeypatch):
        # Força recarregar do "banco"
        risco._estado_carregado = False
        monkeypatch.setattr(risco.database, "carregar_risk_state", lambda: {"pnl_dia": 314.0})
        risco._carregar_estado_persistido()
        assert risco._estado_risco["pnl_dia"] == 314.0
        assert risco._estado_carregado is True

    def test_carregar_idempotente_quando_ja_carregado(self, monkeypatch):
        chamado = {"n": 0}
        risco._estado_carregado = True
        monkeypatch.setattr(
            risco.database,
            "carregar_risk_state",
            lambda: chamado.__setitem__("n", chamado["n"] + 1) or {},
        )
        risco._carregar_estado_persistido()
        assert chamado["n"] == 0  # short-circuit, não toca o banco

    def test_carregar_engole_excecao(self, monkeypatch):
        risco._estado_carregado = False

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(risco.database, "carregar_risk_state", _boom)
        risco._carregar_estado_persistido()  # não propaga
        assert risco._estado_carregado is True

    def test_carregar_salvo_none_nao_altera_estado(self, monkeypatch):
        risco._estado_carregado = False
        _set_estado(pnl_dia=55.0)
        monkeypatch.setattr(risco.database, "carregar_risk_state", lambda: None)
        risco._carregar_estado_persistido()
        # salvo é falsy -> update não ocorre
        assert risco._estado_risco["pnl_dia"] == 55.0


# ══════════════════════════════════════════════════════════════
# 11. status()
# ══════════════════════════════════════════════════════════════


class TestStatus:

    @pytest.fixture(autouse=True)
    def _mockar_externos(self, monkeypatch):
        monkeypatch.setattr(risco, "get_saldo_usdt", lambda: 1000.0)
        monkeypatch.setattr(risco, "get_saldo_btc", lambda: 0.05)
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.03)
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.02)

    def test_status_estrutura_basica(self):
        _set_estado(
            data_dia=str(date.today()),
            pnl_dia=0.0,
            capital_inicio_dia=None,
            bloqueado=False,
            motivo_bloqueio="",
            posicoes_abertas=0,
        )
        s = risco.status()
        assert s["saldo_usdt"] == 1000.0
        assert s["saldo_btc"] == 0.05
        assert s["volatilidade_%"] == pytest.approx(3.0)
        assert s["kelly_%"] == pytest.approx(2.0)
        assert s["max_dd_diario_%"] == risco.MAX_DRAWDOWN_DIARIO * 100
        assert s["max_dd_total_%"] == risco.MAX_DRAWDOWN_TOTAL * 100

    def test_status_drawdown_zero_sem_capital_inicio(self):
        # capital_inicio_dia None/falsy -> dd_dia permanece 0.0
        _set_estado(data_dia=str(date.today()), pnl_dia=-100.0, capital_inicio_dia=None)
        s = risco.status()
        assert s["drawdown_dia_%"] == 0.0

    def test_status_calcula_drawdown_com_capital(self):
        _set_estado(data_dia=str(date.today()), pnl_dia=-50.0, capital_inicio_dia=1000.0)
        s = risco.status()
        # -50/1000 * 100 = -5.0
        assert s["drawdown_dia_%"] == pytest.approx(-5.0)

    def test_status_reflete_bloqueio(self):
        _set_estado(
            data_dia=str(date.today()),
            bloqueado=True,
            motivo_bloqueio="trava X",
            posicoes_abertas=1,
            capital_inicio_dia=None,
            pnl_dia=0.0,
        )
        s = risco.status()
        assert s["bloqueado"] is True
        assert s["motivo_bloqueio"] == "trava X"
        assert s["posicoes_abertas"] == 1


# ══════════════════════════════════════════════════════════════
# 12. validar_trade() — branches/asserções adicionais
# ══════════════════════════════════════════════════════════════


class TestValidarTradeExtra:

    @pytest.fixture(autouse=True)
    def _estado_dia_atual(self, monkeypatch):
        _set_estado(
            data_dia=str(date.today()),
            pnl_dia=0.0,
            bloqueado=False,
            motivo_bloqueio="",
            posicoes_abertas=0,
            capital_inicio_dia=None,
        )
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.0)
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.02)

    def test_capital_inicio_dia_zero_pula_checagem_drawdown(self):
        # capital_inicio_dia == 0 é falsy -> branch de drawdown não executa,
        # mesmo com pnl muito negativo (não bloqueia por drawdown).
        _set_estado(capital_inicio_dia=0, pnl_dia=-9999.0)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        # Não bloqueia por drawdown; segue e aprova
        assert r["pode"] is True
        assert risco._estado_risco["bloqueado"] is False

    def test_risco_usdt_calculado_corretamente(self):
        # stop COMPRA = preco * (1 - 0.015) = 68000 * 0.985 = 66980
        # risco_usdt = round(tamanho_btc * |preco - stop|, 2)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        stop = 68000 * (1 - 0.015)
        esperado = round(r["tamanho_btc"] * abs(68000 - stop), 2)
        assert r["risco_usdt"] == esperado

    def test_fator_kelly_reflete_kelly_do_banco(self):
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["fator_kelly"] == 0.02

    def test_ordem_bloqueado_antes_de_saldo(self):
        # Bloqueado tem prioridade sobre saldo insuficiente.
        _set_estado(bloqueado=True, motivo_bloqueio="trava")
        r = risco.validar_trade("COMPRA", 68000, 5.0)  # saldo < 10 também
        assert r["pode"] is False
        assert "bloqueado" in r["motivo"].lower()
        assert "$10" not in r["motivo"]

    def test_saldo_negativo_reprovado(self):
        r = risco.validar_trade("COMPRA", 68000, -50.0)
        assert r["pode"] is False
        assert "< $10" in r["motivo"]

    def test_posicoes_acima_do_max_bloqueia(self):
        _set_estado(posicoes_abertas=5)  # >= MAX_POSICOES_ABERTAS
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        assert "aguardar" in r["motivo"].lower()

    def test_capital_inicio_dia_inicializado_uma_vez(self):
        _set_estado(capital_inicio_dia=777.0, pnl_dia=0.0)
        r = risco.validar_trade("COMPRA", 68000, 1000)
        # Já inicializado -> não sobrescreve com capital_usdt
        assert r["pode"] is True
        assert risco._estado_risco["capital_inicio_dia"] == 777.0

    def test_drawdown_seta_motivo_bloqueio_persistente(self):
        _set_estado(capital_inicio_dia=1000.0, pnl_dia=-200.0)  # -20%
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is False
        # estado de bloqueio espelha o motivo retornado
        assert risco._estado_risco["motivo_bloqueio"] == r["motivo"]
        assert "drawdown" in r["motivo"].lower()

    def test_segunda_chamada_apos_drawdown_retorna_bloqueado(self):
        # 1ª chamada dispara drawdown e seta bloqueado; 2ª cai no branch 1.
        _set_estado(capital_inicio_dia=1000.0, pnl_dia=-100.0)
        risco.validar_trade("COMPRA", 68000, 1000)
        r2 = risco.validar_trade("COMPRA", 68000, 1000)
        assert r2["pode"] is False
        assert "bloqueado" in r2["motivo"].lower()


# ══════════════════════════════════════════════════════════════
# 13. fator_volatilidade()  — vol targeting (P0-3)
# ══════════════════════════════════════════════════════════════


class TestFatorVolatilidade:

    def test_neutro_quando_none(self):
        assert risco.fator_volatilidade(None) == 1.0

    def test_neutro_quando_zero(self):
        assert risco.fator_volatilidade(0.0) == 1.0

    def test_neutro_quando_negativo(self):
        assert risco.fator_volatilidade(-1.5) == 1.0

    def test_vol_alta_reduz_multiplicador(self):
        # atr_relativo=2 -> mult = 1/2 = 0.5
        assert risco.fator_volatilidade(2.0) == 0.5

    def test_vol_baixa_aumenta_multiplicador_ate_o_teto(self):
        # atr_relativo=0.5 -> mult = 1/0.5 = 2.0 -> clamp 1.5 (teto exato)
        assert risco.fator_volatilidade(0.5) == 1.5

    def test_vol_muito_baixa_clampada_no_teto_nao_no_bruto(self):
        # atr_relativo=0.1 -> mult bruto = 10.0 -> clampado a 1.5, nao 10.0
        assert risco.fator_volatilidade(0.1) == 1.5

    def test_vol_muito_alta_clampada_no_piso_nao_no_bruto(self):
        # atr_relativo=10 -> mult bruto = 0.1 -> clampado a 0.5, nao 0.1
        assert risco.fator_volatilidade(10.0) == 0.5

    def test_resultado_arredondado_4_casas(self):
        valor = risco.fator_volatilidade(1.3)
        assert valor == round(valor, 4)


# ══════════════════════════════════════════════════════════════
# 14. calcular_tamanho() — vol targeting (P0-3)
# ══════════════════════════════════════════════════════════════


class TestCalcularTamanhoVolTargeting:

    def test_atr_relativo_alto_reduz_tamanho_vs_sem_ele(self, monkeypatch):
        # Kelly abaixo do teto (0.01 < 0.02) para o multiplicador ter efeito direto.
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.01)
        sem = risco.calcular_tamanho(1000, 100, 90)
        com = risco.calcular_tamanho(1000, 100, 90, atr_relativo=2.0)  # mult=0.5
        assert com < sem
        assert com == pytest.approx(sem * 0.5)

    def test_atr_relativo_baixo_aumenta_tamanho_quando_kelly_abaixo_do_teto(self, monkeypatch):
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.01)
        sem = risco.calcular_tamanho(1000, 100, 90)
        com = risco.calcular_tamanho(1000, 100, 90, atr_relativo=0.5)  # mult=1.5
        assert com > sem
        assert com == pytest.approx(sem * 1.5)

    def test_atr_relativo_baixo_nunca_excede_max_risco_com_kelly_no_teto(self, monkeypatch):
        # TESTE MAIS IMPORTANTE DA TAREFA: Kelly bruto muito acima do teto
        # (0.50) + vol muito baixa (atr_relativo=0.1, mult clampado a 1.5) —
        # a fracao de capital efetivamente arriscada nao pode ultrapassar
        # MAX_RISCO_POR_TRADE mesmo com o multiplicador no teto de 1.5x.
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.50)
        capital, preco, stop = 1000, 100, 90
        tam = risco.calcular_tamanho(capital, preco, stop, atr_relativo=0.1)
        fracao_efetiva = (tam * abs(preco - stop)) / capital
        assert fracao_efetiva <= risco.MAX_RISCO_POR_TRADE + 1e-9
        assert fracao_efetiva == pytest.approx(risco.MAX_RISCO_POR_TRADE)

    def test_fator_risco_explicito_ignora_atr_relativo(self):
        # fator_risco explicito e um bypass intencional do chamador; vol
        # targeting so se aplica ao caminho derivado do Kelly (fator_risco=None).
        # Isso vale tanto para o calculo de risco quanto para o teto de 20%.
        sem = risco.calcular_tamanho(1000, 100, 90, fator_risco=0.02)
        com = risco.calcular_tamanho(1000, 100, 90, fator_risco=0.02, atr_relativo=2.0)
        assert com == sem

    def test_backward_compat_sem_atr_relativo_identico_ao_anterior(self, monkeypatch):
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.50)
        assert risco.calcular_tamanho(1000, 100, 90) == 2.0  # mesmo resultado de antes do P0-3

    def test_teto_de_20pct_tambem_e_modulado_pela_volatilidade(self, monkeypatch):
        # Kelly no teto (0.50 -> capado a 0.02) + stop apertado (1.5%, o mesmo
        # usado por validar_trade em producao): o sizing por risco sempre
        # excede o teto notional, entao e o teto quem domina o tamanho final
        # — e ele precisa responder a volatilidade, senao o vol targeting e
        # um no-op em producao (achado da revisao adversarial do P0-3).
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.50)
        capital, preco = 1000, 68000
        stop = preco * (1 - 0.015)
        neutro = risco.calcular_tamanho(capital, preco, stop)
        vol_alta = risco.calcular_tamanho(capital, preco, stop, atr_relativo=2.0)  # mult=0.5
        vol_baixa = risco.calcular_tamanho(capital, preco, stop, atr_relativo=0.5)  # mult=1.5
        # tolerancia absoluta: calcular_tamanho arredonda em 6 casas, mais
        # apertado que o rel=1e-6 default do pytest.approx nessa magnitude.
        assert neutro == pytest.approx(capital * 0.20 / preco, abs=1e-6)
        assert vol_alta == pytest.approx(neutro * 0.5, abs=1e-6)
        assert vol_baixa == pytest.approx(neutro * 1.5, abs=1e-6)

    def test_teto_fica_fixo_em_20pct_quando_fator_risco_explicito(self):
        # Mesmo com stop apertado e atr_relativo extremo, um fator_risco
        # explicito bypassa o teto vol-targetado (mesma semantica do bypass
        # do calculo de risco).
        capital, preco = 1000, 68000
        stop = preco * (1 - 0.015)
        tam = risco.calcular_tamanho(capital, preco, stop, fator_risco=0.50, atr_relativo=0.1)
        assert tam == pytest.approx(capital * 0.20 / preco, abs=1e-6)


# ══════════════════════════════════════════════════════════════
# 15. validar_trade() — vol targeting (P0-3)
# ══════════════════════════════════════════════════════════════


class TestValidarTradeVolTargeting:

    @pytest.fixture(autouse=True)
    def _estado_dia_atual(self, monkeypatch):
        _set_estado(
            data_dia=str(date.today()),
            pnl_dia=0.0,
            bloqueado=False,
            motivo_bloqueio="",
            posicoes_abertas=0,
            capital_inicio_dia=None,
        )
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.0)
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.02)

    def test_fator_volatilidade_aparece_no_retorno(self):
        r = risco.validar_trade("COMPRA", 68000, 1000, atr_relativo=2.0)
        assert r["pode"] is True
        assert r["fator_volatilidade"] == 0.5

    def test_atr_relativo_repassado_reduz_tamanho(self):
        # Com o teto de 20% tambem modulado por volatilidade (ver
        # test_teto_de_20pct_tambem_e_modulado_pela_volatilidade em
        # TestCalcularTamanhoVolTargeting), o efeito aparece mesmo com o
        # kelly_do_banco=0.02 padrao da fixture da classe (que satura o
        # sizing por risco no teto de 20% independente de atr_relativo).
        sem = risco.validar_trade("COMPRA", 68000, 1000)
        com = risco.validar_trade("COMPRA", 68000, 1000, atr_relativo=2.0)
        assert com["tamanho_btc"] < sem["tamanho_btc"]

    def test_fator_volatilidade_reflete_o_multiplicador_efetivo_aplicado(self):
        # Achado da revisao adversarial do P0-3: o campo "fator_volatilidade"
        # nao pode divergir do efeito real no tamanho. Com kelly_do_banco=0.02
        # (fixture, no teto) e stop de 1.5%, o sizing por risco satura no teto
        # notional em ambos os casos — mas o teto em si escala por mult_vol,
        # entao a razao real entre os tamanhos bate exatamente com o campo.
        sem = risco.validar_trade("COMPRA", 68000, 1000)
        com = risco.validar_trade("COMPRA", 68000, 1000, atr_relativo=2.0)
        assert com["fator_volatilidade"] == 0.5
        assert com["tamanho_btc"] == pytest.approx(
            sem["tamanho_btc"] * com["fator_volatilidade"], abs=1e-6
        )

    def test_backward_compat_sem_atr_relativo_fator_volatilidade_neutro(self):
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["fator_volatilidade"] == 1.0
        assert r["tamanho_btc"] > 0


# ══════════════════════════════════════════════════════════════
# 16. correlacao_pearson() — risco de portfolio (P1-2)
# ══════════════════════════════════════════════════════════════


class TestCorrelacaoPearson:
    def test_correlacao_perfeita_positiva(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [2.0, 4.0, 6.0, 8.0, 10.0]
        assert risco.correlacao_pearson(a, b) == pytest.approx(1.0)

    def test_correlacao_perfeita_negativa(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert risco.correlacao_pearson(a, b) == pytest.approx(-1.0)

    def test_sem_correlacao(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [1.0, -1.0, 1.0, -1.0, 1.0]
        assert risco.correlacao_pearson(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_tamanhos_diferentes_levanta_valueerror(self):
        with pytest.raises(ValueError):
            risco.correlacao_pearson([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_menos_de_2_pontos_retorna_zero(self):
        assert risco.correlacao_pearson([1.0], [2.0]) == 0.0
        assert risco.correlacao_pearson([], []) == 0.0

    def test_serie_constante_retorna_zero(self):
        # std==0 em qualquer serie -> correlacao indefinida, guard retorna 0.0
        assert risco.correlacao_pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) == 0.0
        assert risco.correlacao_pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) == 0.0

    def test_resultado_clampado_em_menos1_e_1(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [2.0, 4.0, 6.0, 8.0, 10.0]
        c = risco.correlacao_pearson(a, b)
        assert -1.0 <= c <= 1.0


# ══════════════════════════════════════════════════════════════
# 17. _precos_para_retornos()
# ══════════════════════════════════════════════════════════════


class TestPrecosParaRetornos:
    def test_retornos_calculados_corretamente(self):
        precos = [100.0, 110.0, 99.0]
        retornos = risco._precos_para_retornos(precos)
        assert retornos == pytest.approx([0.10, -0.10])

    def test_pula_pontos_com_preco_anterior_nao_positivo(self):
        precos = [100.0, 0.0, 50.0, -10.0, 60.0]
        # i=1: anterior=100 (ok) -> retorno (0-100)/100=-1.0
        # i=2: anterior=0.0 -> pulado
        # i=3: anterior=50.0 (ok) -> retorno (-10-50)/50=-1.2
        # i=4: anterior=-10.0 -> pulado
        assert risco._precos_para_retornos(precos) == pytest.approx([-1.0, -1.2])

    def test_lista_com_1_preco_retorna_vazio(self):
        assert risco._precos_para_retornos([100.0]) == []

    def test_lista_vazia_retorna_vazio(self):
        assert risco._precos_para_retornos([]) == []


# ══════════════════════════════════════════════════════════════
# 18. matriz_correlacao()
# ══════════════════════════════════════════════════════════════


class TestMatrizCorrelacao:
    def test_dict_vazio_retorna_vazio(self):
        assert risco.matriz_correlacao({}) == {}

    def test_diagonal_e_1(self):
        m = risco.matriz_correlacao(
            {"BTCUSDT": [1.0, 2.0, 3.0, 4.0], "ETHUSDT": [2.0, 4.0, 6.0, 8.0]}
        )
        assert m["BTCUSDT"]["BTCUSDT"] == 1.0
        assert m["ETHUSDT"]["ETHUSDT"] == 1.0

    def test_matriz_simetrica(self):
        m = risco.matriz_correlacao(
            {"BTCUSDT": [1.0, 2.0, 3.0, 4.0], "ETHUSDT": [4.0, 3.0, 2.0, 1.0]}
        )
        assert m["BTCUSDT"]["ETHUSDT"] == m["ETHUSDT"]["BTCUSDT"]

    def test_series_de_tamanhos_diferentes_nao_quebra(self):
        # BTCUSDT tem 6 precos, ETHUSDT so 4 -- trunca para o comum (4),
        # alinhando pelo FIM (os mais recentes).
        m = risco.matriz_correlacao(
            {
                "BTCUSDT": [10.0, 20.0, 1.0, 2.0, 3.0, 4.0],
                "ETHUSDT": [2.0, 4.0, 6.0, 8.0],
            }
        )
        assert m["BTCUSDT"]["ETHUSDT"] == pytest.approx(1.0)

    def test_precos_com_valores_nao_positivos_nao_quebra(self):
        # Achado real: precos_por_symbol com mesmo comprimento pode ainda
        # gerar listas de RETORNOS de tamanhos diferentes (via o skip de
        # _precos_para_retornos), o que crashava correlacao_pearson antes
        # do truncamento adicional sobre os retornos.
        m = risco.matriz_correlacao(
            {
                "BTCUSDT": [1.0, 2.0, 3.0, 4.0, 5.0],
                "ETHUSDT": [2.0, 4.0, 6.0, 8.0, 10.0],
                "SOLUSDT": [1.0, -1.0, 1.0, -1.0, 1.0],
            }
        )
        assert m["BTCUSDT"]["ETHUSDT"] == pytest.approx(1.0)
        assert -1.0 <= m["BTCUSDT"]["SOLUSDT"] <= 1.0

    def test_retornos_todos_vazios_nao_quebra(self):
        # todo preco apos o primeiro e <=0 -> lista de retornos vazia
        m = risco.matriz_correlacao({"A": [1.0, -1.0, -1.0], "B": [1.0, 2.0, 3.0]})
        assert m["A"]["B"] == 0.0
        assert m["A"]["A"] == 1.0


# ══════════════════════════════════════════════════════════════
# 19. _corr_lookup()
# ══════════════════════════════════════════════════════════════


class TestCorrLookup:
    def test_mesmo_symbol_sempre_1(self):
        # mesmo que a matriz diga outra coisa, a==b e hardcoded para 1.0
        matriz = {"BTCUSDT": {"BTCUSDT": 0.3}}
        assert risco._corr_lookup("BTCUSDT", "BTCUSDT", matriz) == 1.0

    def test_par_presente_usa_matriz(self):
        matriz = {"BTCUSDT": {"ETHUSDT": 0.75}}
        assert risco._corr_lookup("BTCUSDT", "ETHUSDT", matriz) == 0.75

    def test_par_ausente_usa_default_conservador(self):
        assert risco._corr_lookup("BTCUSDT", "SOLUSDT", {}) == risco.CORRELACAO_DEFAULT_AUSENTE
        assert risco.CORRELACAO_DEFAULT_AUSENTE == 1.0


# ══════════════════════════════════════════════════════════════
# 20. exposicao_agregada_efetiva()
# ══════════════════════════════════════════════════════════════


class TestExposicaoAgregadaEfetiva:
    def test_posicao_solo_invariante_de_inercia(self):
        # sem nenhuma posicao aberta, a exposicao efetiva da candidata e
        # exatamente o proprio notional dela -- nunca amplificada nem
        # reduzida por uma matriz de correlacao vazia/irrelevante.
        assert risco.exposicao_agregada_efetiva([], "BTCUSDT", 500.0, {}) == pytest.approx(500.0)

    def test_correlacao_1_e_totalmente_aditiva(self):
        matriz = {"BTCUSDT": {"ETHUSDT": 1.0}, "ETHUSDT": {"BTCUSDT": 1.0}}
        r = risco.exposicao_agregada_efetiva(
            [{"symbol": "BTCUSDT", "notional_usdt": 300.0}], "ETHUSDT", 200.0, matriz
        )
        assert r == pytest.approx(500.0)

    def test_correlacao_0_da_beneficio_de_diversificacao(self):
        matriz = {"BTCUSDT": {"ETHUSDT": 0.0}, "ETHUSDT": {"BTCUSDT": 0.0}}
        r = risco.exposicao_agregada_efetiva(
            [{"symbol": "BTCUSDT", "notional_usdt": 300.0}], "ETHUSDT", 200.0, matriz
        )
        assert r == pytest.approx((300.0**2 + 200.0**2) ** 0.5)
        assert r < 500.0  # sempre menor que a soma simples quando corr<1

    def test_correlacao_negativa_reduz_exposicao_abaixo_do_pitagoras(self):
        matriz = {"BTCUSDT": {"ETHUSDT": -0.5}, "ETHUSDT": {"BTCUSDT": -0.5}}
        r = risco.exposicao_agregada_efetiva(
            [{"symbol": "BTCUSDT", "notional_usdt": 300.0}], "ETHUSDT", 200.0, matriz
        )
        esperado = (300.0**2 + 200.0**2 + 2 * 300.0 * 200.0 * -0.5) ** 0.5
        assert r == pytest.approx(esperado)
        assert r < (300.0**2 + 200.0**2) ** 0.5

    def test_tres_posicoes_soma_dupla_completa(self):
        matriz = {
            "BTCUSDT": {"ETHUSDT": 0.8, "SOLUSDT": 0.6},
            "ETHUSDT": {"BTCUSDT": 0.8, "SOLUSDT": 0.5},
            "SOLUSDT": {"BTCUSDT": 0.6, "ETHUSDT": 0.5},
        }
        abertas = [
            {"symbol": "BTCUSDT", "notional_usdt": 100.0},
            {"symbol": "ETHUSDT", "notional_usdt": 150.0},
        ]
        r = risco.exposicao_agregada_efetiva(abertas, "SOLUSDT", 80.0, matriz)
        n = {"BTCUSDT": 100.0, "ETHUSDT": 150.0, "SOLUSDT": 80.0}
        soma_manual = sum(n[a] * n[b] * (1.0 if a == b else matriz[a][b]) for a in n for b in n)
        assert r == pytest.approx(soma_manual**0.5)


# ══════════════════════════════════════════════════════════════
# 21. obter_matriz_correlacao_cache()
# ══════════════════════════════════════════════════════════════


class TestObterMatrizCorrelacaoCache:
    @pytest.fixture(autouse=True)
    def _limpar_cache(self):
        original = dict(risco._cache_correlacao)
        yield
        risco._cache_correlacao.clear()
        risco._cache_correlacao.update(original)

    def _klines_fake(self, precos):
        # P1-5: _obter_precos_klines agora delega para risco.obter_klines
        # (data/klines.py), que devolve um dict, não a resposta HTTP crua.
        return {"fechamento": list(precos)}

    def test_busca_e_cacheia_matriz(self, monkeypatch):
        risco._cache_correlacao["matriz"] = {}
        risco._cache_correlacao["timestamp"] = 0.0
        monkeypatch.setattr(
            risco, "obter_klines", lambda *a, **k: self._klines_fake([1.0, 2.0, 3.0, 4.0])
        )
        m = risco.obter_matriz_correlacao_cache(ttl_segundos=900)
        assert m  # populada
        assert risco._cache_correlacao["matriz"] == m
        assert risco._cache_correlacao["timestamp"] > 0.0

    def test_usa_cache_dentro_do_ttl_sem_nova_chamada_de_rede(self, monkeypatch):
        risco._cache_correlacao["matriz"] = {"BTCUSDT": {"BTCUSDT": 1.0}}
        risco._cache_correlacao["timestamp"] = risco.time.time()
        chamou = []
        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: chamou.append(1))
        m = risco.obter_matriz_correlacao_cache(ttl_segundos=900)
        assert m == {"BTCUSDT": {"BTCUSDT": 1.0}}
        assert chamou == []  # nao bateu rede -- veio do cache

    def test_ttl_expirado_busca_de_novo(self, monkeypatch):
        risco._cache_correlacao["matriz"] = {"BTCUSDT": {"BTCUSDT": 1.0}}
        risco._cache_correlacao["timestamp"] = risco.time.time() - 1000  # bem antigo
        monkeypatch.setattr(
            risco, "obter_klines", lambda *a, **k: self._klines_fake([1.0, 2.0, 3.0, 4.0])
        )
        m = risco.obter_matriz_correlacao_cache(ttl_segundos=900)
        assert "BTCUSDT" in m
        # matriz nova populada com o(s) symbol(s) buscados, timestamp atualizado
        assert risco._cache_correlacao["timestamp"] > risco.time.time() - 5

    def test_falha_total_de_rede_preserva_cache_antigo(self, monkeypatch):
        antiga = {
            "BTCUSDT": {"ETHUSDT": 0.9, "BTCUSDT": 1.0},
            "ETHUSDT": {"BTCUSDT": 0.9, "ETHUSDT": 1.0},
        }
        risco._cache_correlacao["matriz"] = antiga
        risco._cache_correlacao["timestamp"] = risco.time.time() - 1000  # expirado

        monkeypatch.setattr(risco, "obter_klines", lambda *a, **k: None)
        m = risco.obter_matriz_correlacao_cache(ttl_segundos=900)
        assert m == antiga  # nao sobrescreveu com {} por causa da falha


# ══════════════════════════════════════════════════════════════
# 22. validar_trade() — risco de portfolio (P1-2)
# ══════════════════════════════════════════════════════════════


class TestValidarTradePortfolio:
    @pytest.fixture(autouse=True)
    def _estado_dia_atual(self, monkeypatch):
        _set_estado(
            data_dia=str(date.today()),
            pnl_dia=0.0,
            bloqueado=False,
            motivo_bloqueio="",
            posicoes_abertas=0,
            capital_inicio_dia=None,
        )
        monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.0)
        monkeypatch.setattr(risco, "kelly_do_banco", lambda: 0.02)

    def test_sem_posicoes_abertas_detalhe_nunca_bate_rede_nem_bloqueia(self, monkeypatch):
        # comportamento default (hoje sempre, ja que nenhum call site real
        # popula posicoes_abertas_detalhe): inerte, sem chamada de rede.
        chamou = []
        monkeypatch.setattr(
            risco, "obter_matriz_correlacao_cache", lambda *a, **k: chamou.append(1)
        )
        r = risco.validar_trade("COMPRA", 68000, 1000)
        assert r["pode"] is True
        assert chamou == []
        assert r["exposicao_agregada_efetiva"] == pytest.approx(r["tamanho_btc"] * 68000, abs=0.01)

    def test_lista_vazia_explicita_tambem_e_inerte(self, monkeypatch):
        chamou = []
        monkeypatch.setattr(
            risco, "obter_matriz_correlacao_cache", lambda *a, **k: chamou.append(1)
        )
        r = risco.validar_trade("COMPRA", 68000, 1000, posicoes_abertas_detalhe=[])
        assert r["pode"] is True
        assert chamou == []

    def test_bloqueia_quando_exposicao_agregada_excede_teto(self, monkeypatch):
        monkeypatch.setattr(risco, "obter_matriz_correlacao_cache", lambda *a, **k: {})
        # posicao aberta enorme (correlacao default=1.0 -> aditivo) forca a
        # exposicao agregada a estourar TETO_EXPOSICAO_AGREGADA_PCT*capital.
        outras = [{"symbol": "ETHUSDT", "notional_usdt": 100000.0}]
        r = risco.validar_trade(
            "COMPRA", 68000, 1000, posicoes_abertas_detalhe=outras, symbol="BTCUSDT"
        )
        assert r["pode"] is False
        assert "agregada" in r["motivo"].lower()
        assert "exposicao_agregada_efetiva" in r

    def test_aprova_quando_exposicao_agregada_dentro_do_teto(self, monkeypatch):
        monkeypatch.setattr(
            risco,
            "obter_matriz_correlacao_cache",
            lambda *a, **k: {"BTCUSDT": {"ETHUSDT": 0.0}, "ETHUSDT": {"BTCUSDT": 0.0}},
        )
        outras = [{"symbol": "ETHUSDT", "notional_usdt": 50.0}]
        r = risco.validar_trade(
            "COMPRA", 68000, 1000, posicoes_abertas_detalhe=outras, symbol="BTCUSDT"
        )
        assert r["pode"] is True
        assert "exposicao_agregada_efetiva" in r
