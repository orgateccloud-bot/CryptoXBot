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

    def test_calcula_variacao_percentual(self, monkeypatch):
        # abertura = k[-2][1] = 100, fechamento = k[-1][4] = 108
        # |108 - 100| / 100 = 0.08
        klines = [
            ["t", "100", "h", "l", "105", "v"],  # k[-2]: abertura=100
            ["t", "104", "h", "l", "108", "v"],  # k[-1]: fechamento=108
        ]
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(klines))
        assert risco.verificar_volatilidade() == pytest.approx(0.08)

    def test_usa_valor_absoluto_em_queda(self, monkeypatch):
        # fechamento < abertura -> abs garante valor positivo
        klines = [
            ["t", "100", "h", "l", "105", "v"],
            ["t", "104", "h", "l", "90", "v"],  # fechamento=90 -> |90-100|/100 = 0.10
        ]
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(klines))
        assert risco.verificar_volatilidade() == pytest.approx(0.10)

    def test_klines_insuficientes_retorna_zero(self, monkeypatch):
        # len(k) < 2 -> 0.0
        monkeypatch.setattr(
            risco.requests, "get", lambda *a, **k: _FakeResp([["t", "100", "h", "l", "105", "v"]])
        )
        assert risco.verificar_volatilidade() == 0.0

    def test_klines_vazio_retorna_zero(self, monkeypatch):
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp([]))
        assert risco.verificar_volatilidade() == 0.0

    def test_erro_de_rede_retorna_zero(self, monkeypatch):
        def _boom(*a, **k):
            raise ConnectionError("sem rede")

        monkeypatch.setattr(risco.requests, "get", _boom)
        assert risco.verificar_volatilidade() == 0.0

    def test_payload_malformado_retorna_zero(self, monkeypatch):
        # k[-2][1] não conversível -> float() lança -> except -> 0.0
        klines = [["t", "abc"], ["t", "def", "x", "y", "ghi"]]
        monkeypatch.setattr(risco.requests, "get", lambda *a, **k: _FakeResp(klines))
        assert risco.verificar_volatilidade() == 0.0


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
        # 12 rows, 6 COMPRA -> wr = 0.5 -> kelly(0.5, 2.0)
        rows = [{"tipo": "COMPRA"}] * 6 + [{"tipo": "VENDA"}] * 6
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        assert risco.kelly_do_banco() == pytest.approx(risco.kelly(0.5, 2.0))

    def test_todos_compra_winrate_um(self, monkeypatch):
        rows = [{"tipo": "COMPRA"}] * 20
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        # wr = 1.0 -> kelly(1.0, 2.0) = (1 - 0)*0.25 = 0.25
        assert risco.kelly_do_banco() == pytest.approx(risco.kelly(1.0, 2.0))

    def test_excecao_no_banco_retorna_max(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(risco.database, "sinais_executados", _boom)
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE

    def test_rows_sem_chave_tipo_contam_como_nao_compra(self, monkeypatch):
        # s.get("tipo") ausente -> None != "COMPRA" -> não conta
        rows = [{}] * 10
        monkeypatch.setattr(risco.database, "sinais_executados", lambda *a, **k: rows)
        # wr = 0 -> kelly(0.0, ...) -> guard win_rate<=0 -> MAX_RISCO_POR_TRADE
        assert risco.kelly_do_banco() == risco.MAX_RISCO_POR_TRADE


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
