"""
Testes — travas de entrada de capital (frente I-8)
==================================================
Estas são as travas que decidem se dinheiro real pode sair. Cada teste aqui
corresponde a um defeito que a auditoria de 2026-08-06 mediu no código:

1. **`DRY_RUN` era gate fantasma.** Definido em `runtime_settings`, documentado
   em 6 arquivos como cinto de segurança, e lido em lugar nenhum do caminho de
   execução — os únicos leitores eram duas linhas do `dashboard.py`, para
   desenhar um rótulo. O operador podia manter `DRY_RUN=true` e ter ordens reais
   saindo.

2. **Não havia kill-switch em nenhum arquivo do repositório.** O único bloqueio
   (`bloqueado`, drawdown diário) se auto-revogava na virada do dia
   (`_resetar_se_novo_dia`), então cinco dias de −4,9% (−22% de equity) não
   disparavam nada e o bot religava sozinho toda meia-noite.

3. **`MAX_DRAWDOWN_TOTAL` nunca era comparado com nada.** A constante existia com
   o comentário "desliga o bot até revisão manual" e aparecia em exatamente um
   lugar: o payload de display de `status()`.

4. **A postura da CHAVE nunca era verificada.** `restricoes_chave()` existia,
   testada, e o único chamador era o `__main__` do próprio arquivo. E `canTrade`
   de `/api/v3/account` não responde isso — é da CONTA, e ficou `True` nesta
   conta enquanto a chave era read-only.

5. **O fallback de endpoint reintroduzia FUTUROS.** `config/settings.py` tinha
   precedência sobre o default SPOT, mascarado por duas linhas do `.env`.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import risco  # noqa: E402


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch):
    """O estado de risco é global de módulo e persistido — sem reset, um teste
    trava o bot para todos os seguintes."""
    orig = dict(risco._estado_risco)
    monkeypatch.setattr(risco, "persistir_estado", lambda: None)
    monkeypatch.setattr(risco, "_carregar_estado_persistido", lambda: None)
    monkeypatch.setattr(risco.database, "salvar_bot_event", lambda *a, **k: None)
    monkeypatch.setattr(risco.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)
    risco._estado_risco.update({
        "travado": False, "motivo_travamento": "", "travado_em": None,
        "bloqueado": False, "motivo_bloqueio": "", "pnl_dia": 0.0,
        "capital_inicio_dia": 1000.0, "data_dia": str(date.today()),
        "posicoes_abertas": 0, "circuit_breaker_ativo": False,
    })
    yield
    risco._estado_risco.clear()
    risco._estado_risco.update(orig)


# ── (a) DRY_RUN participa da decisão ──────────────────────────


class TestDryRunFreia:
    def test_real_com_dry_run_aborta_o_boot(self, monkeypatch):
        """A contradição explícita tem que matar o boot, não ser resolvida em
        silêncio para um dos lados."""
        chamou = {"v": False}
        monkeypatch.setattr(main, "_validar_trend_so_em_simulacao",
                            lambda *a: chamou.__setitem__("v", True))
        with pytest.raises(SystemExit) as ex:
            main._decidir_modo(real=True, dry_run=True)
        assert ex.value.code == 1
        assert chamou["v"] is False, "abortou antes de qualquer outra validacao"

    def test_dry_run_sozinho_forca_simulacao(self):
        assert main._decidir_modo(real=False, dry_run=True) is True

    def test_real_sem_dry_run_permite_real(self):
        assert main._decidir_modo(real=True, dry_run=False) is False

    def test_default_e_simulacao(self):
        assert main._decidir_modo(real=False, dry_run=False) is True


# ── (b) kill-switch sobrevive à virada do dia ─────────────────


class TestKillSwitchPermanente:
    def test_travado_reprova_validar_trade(self):
        risco.travar("teste")
        r = risco.validar_trade("COMPRA", 64000.0, 1000.0)
        assert r["pode"] is False
        assert "TRAVADO" in r["motivo"]
        assert r["tamanho_btc"] == 0

    def test_gate_0_vem_antes_de_tudo(self):
        """Travado + saldo insuficiente: o motivo tem que ser a trava, provando
        que ela é avaliada antes do gate 1 e do 2."""
        risco.travar("teste")
        r = risco.validar_trade("COMPRA", 64000.0, 1.0)  # capital < 10
        assert "TRAVADO" in r["motivo"], f"gate 0 nao veio primeiro: {r['motivo']}"

    def test_SOBREVIVE_a_virada_do_dia(self):
        """O defeito central: `bloqueado` era limpo por _resetar_se_novo_dia, o
        que permitia perder 5% por dia indefinidamente."""
        risco.travar("perda acumulada")
        risco._estado_risco["data_dia"] = str(date.today() - timedelta(days=1))
        risco._resetar_se_novo_dia()
        assert risco._estado_risco["travado"] is True, "a trava se auto-revogou"
        assert risco.validar_trade("COMPRA", 64000.0, 1000.0)["pode"] is False

    def test_bloqueado_diario_CONTINUA_sendo_limpo(self):
        """Contrapeso: o reset diário do drawdown DIÁRIO é correto e não pode ter
        sido quebrado junto."""
        risco._estado_risco["bloqueado"] = True
        risco._estado_risco["motivo_bloqueio"] = "dd diario"
        risco._estado_risco["data_dia"] = str(date.today() - timedelta(days=1))
        risco._resetar_se_novo_dia()
        assert risco._estado_risco["bloqueado"] is False

    def test_destravar_exige_a_frase_exata(self):
        risco.travar("teste")
        assert risco.destravar("liberar") is False
        assert risco.destravar("") is False
        assert risco.esta_travado()[0] is True
        assert risco.destravar(risco.CONFIRMACAO_DESTRAVAR) is True
        assert risco.esta_travado()[0] is False

    def test_travar_e_idempotente(self, monkeypatch):
        ev = []
        monkeypatch.setattr(risco.database, "salvar_bot_event",
                            lambda t, m, **k: ev.append(t))
        risco.travar("primeiro")
        risco.travar("segundo")
        assert len(ev) == 1, "re-armar nao pode re-alertar"
        assert risco.esta_travado()[1] == "primeiro", "motivo original preservado"


# ── (c) drawdown acumulado bloqueia de verdade ────────────────


class TestDrawdownAcumulado:
    def test_perda_acumulada_acima_do_limite_trava(self):
        """5 dias de -4,9% = -22% de equity. Cada dia isolado passava pelo gate
        diário de 5%; o acumulado nunca era avaliado."""
        risco._estado_risco["capital_inicio_dia"] = 1000.0
        risco._estado_risco["pnl_dia"] = -(risco.MAX_DRAWDOWN_TOTAL * 1000.0) - 1
        risco._verificar_drawdown_acumulado()
        assert risco.esta_travado()[0] is True
        assert "drawdown acumulado" in risco.esta_travado()[1]

    def test_perda_abaixo_do_limite_nao_trava(self):
        risco._estado_risco["capital_inicio_dia"] = 1000.0
        risco._estado_risco["pnl_dia"] = -(risco.MAX_DRAWDOWN_TOTAL * 1000.0) + 10
        risco._verificar_drawdown_acumulado()
        assert risco.esta_travado()[0] is False

    def test_lucro_nunca_trava(self):
        risco._estado_risco["pnl_dia"] = 500.0
        risco._verificar_drawdown_acumulado()
        assert risco.esta_travado()[0] is False

    def test_sem_capital_conhecido_nao_trava_por_divisao(self):
        for cap in (None, 0, -1):
            risco._estado_risco["capital_inicio_dia"] = cap
            risco._estado_risco["pnl_dia"] = -9999.0
            risco._verificar_drawdown_acumulado()  # não levanta
        assert risco.esta_travado()[0] is False

    def test_registrar_resultado_avalia_o_acumulado(self):
        """O freio tem de estar no caminho por onde toda perda passa."""
        risco._estado_risco["capital_inicio_dia"] = 1000.0
        risco.registrar_resultado(-(risco.MAX_DRAWDOWN_TOTAL * 1000.0) - 1)
        assert risco.esta_travado()[0] is True

    def test_nao_se_auto_revoga_no_dia_seguinte(self):
        risco._estado_risco["capital_inicio_dia"] = 1000.0
        risco.registrar_resultado(-200.0)
        assert risco.esta_travado()[0] is True
        risco._estado_risco["data_dia"] = str(date.today() - timedelta(days=1))
        risco._resetar_se_novo_dia()
        assert risco.esta_travado()[0] is True


# ── (d) postura da chave no boot ──────────────────────────────


class TestPosturaDaChave:
    def _mock(self, monkeypatch, restr, configurada=True):
        import binance_conta
        monkeypatch.setattr(binance_conta, "chave_configurada", lambda: configurada)
        monkeypatch.setattr(binance_conta, "restricoes_chave", lambda **k: restr)

    def test_chave_com_saque_aborta_mesmo_em_paper(self, monkeypatch):
        """Chave que pode sacar é risco desproporcional para um bot — abortar
        vale mesmo em simulação, porque a chave é a mesma."""
        self._mock(monkeypatch, {"ok": True, "pode_sacar": True,
                                 "pode_negociar_spot": True, "restrito_por_ip": True})
        with pytest.raises(SystemExit):
            main._validar_postura_da_chave(simulacao=True)

    def test_real_com_chave_read_only_aborta(self, monkeypatch):
        """O caso desta conta: canTrade da CONTA era True e a chave não podia
        negociar. Toda ordem voltaria -2015, depois de o sinal ser consumido."""
        self._mock(monkeypatch, {"ok": True, "pode_sacar": False,
                                 "pode_negociar_spot": False, "restrito_por_ip": False})
        with pytest.raises(SystemExit):
            main._validar_postura_da_chave(simulacao=False)

    def test_paper_com_chave_read_only_NAO_aborta(self, monkeypatch):
        """É a configuração de hoje e tem de continuar funcionando."""
        self._mock(monkeypatch, {"ok": True, "pode_sacar": False,
                                 "pode_negociar_spot": False, "restrito_por_ip": False})
        main._validar_postura_da_chave(simulacao=True)

    def test_real_sem_conseguir_ler_a_chave_aborta(self, monkeypatch):
        """Operar sem saber a postura da chave é pior que não operar."""
        self._mock(monkeypatch, {"ok": False, "erro": "timeout"})
        with pytest.raises(SystemExit):
            main._validar_postura_da_chave(simulacao=False)

    def test_paper_sem_conseguir_ler_apenas_avisa(self, monkeypatch):
        self._mock(monkeypatch, {"ok": False, "erro": "timeout"})
        main._validar_postura_da_chave(simulacao=True)

    def test_real_sem_chave_utilizavel_aborta(self, monkeypatch):
        self._mock(monkeypatch, {"ok": True}, configurada=False)
        with pytest.raises(SystemExit):
            main._validar_postura_da_chave(simulacao=False)


# ── (e) endpoint sem fallback para Futures ────────────────────


class TestEndpointSemFallback:
    def test_default_e_spot_e_nao_futures(self):
        """config/settings.py tinha PRECEDÊNCIA sobre o default SPOT e apontava
        para fapi.binance.com — mascarado por duas linhas do .env."""
        import config.runtime_settings as rs

        assert "fapi" not in rs.REST_BASE_URL, f"caiu em Futures: {rs.REST_BASE_URL}"
        assert "fstream" not in rs.WS_BASE_URL, f"caiu em Futures: {rs.WS_BASE_URL}"

    def test_local_nao_le_mais_settings(self):
        """_local() foi mantida com a assinatura antiga (30 call sites) mas não
        consulta mais config/settings.py."""
        import config.runtime_settings as rs

        assert rs._local("API_KEY", "sentinela") == "sentinela"
        assert rs._local("REST_BASE_URL", "x") == "x"

    def test_runtime_settings_nao_importa_config_settings(self):
        import inspect

        import config.runtime_settings as rs

        src = inspect.getsource(rs)
        assert "from config import settings" not in src, "o fallback voltou"


# ── chave literal fora do fonte ───────────────────────────────


def test_nenhuma_chave_de_64_chars_versionada():
    """Havia uma chave de API literal de 64 caracteres em dashboard.py,
    versionada desde 3c0fc70. Este teste impede que qualquer segredo desse
    formato volte ao repositório — inclusive por 'praticidade' numa lista de
    placeholders.

    Escopo: apenas arquivos RASTREADOS pelo git. A primeira versão deste teste
    varria o disco e acusou `config/settings.py`, que é gitignored, não é lido
    por código nenhum desde a frente I-8, e é a configuração local do
    desenvolvedor — apagá-lo destruiria o ambiente dele. "Segredo no fonte"
    significa no repositório, não na máquina de quem desenvolve.
    """
    import re
    import subprocess

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        saida = subprocess.run(
            ["git", "-C", raiz, "ls-files", "-z", "--", "*.py"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except Exception as e:  # pragma: no cover - sem git no ambiente
        pytest.skip(f"git indisponivel para delimitar arquivos versionados: {e}")

    rastreados = [p for p in saida.split("\0") if p.endswith(".py")]
    assert rastreados, "git ls-files nao devolveu nenhum .py — escopo suspeito"

    padrao = re.compile(r"['\"][A-Za-z0-9]{64}['\"]")
    achados = []
    for rel in rastreados:
        if rel.startswith("_legado/"):
            continue
        p = os.path.join(raiz, rel)
        if not os.path.exists(p):
            continue
        for i, linha in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
            if padrao.search(linha) and "sha256" not in linha.lower():
                achados.append(f"{rel}:{i}")
    assert not achados, f"possivel segredo de 64 chars VERSIONADO: {achados}"
