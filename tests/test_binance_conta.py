"""
Testes — binance_conta (fonte única de saldo) e a escalada em risco.py
=======================================================================
O defeito original: `risco.get_saldo_usdt()` fazia `except Exception: pass;
return 0.0`. Chave revogada, clock drift, geo-block e rate limit viravam todos
"saldo zero" — indistinguível de conta genuinamente vazia. E quem consome esse
número é o **sizing de posição**.

O que estes testes travam:
  1. `(0.0, None)` != `(0.0, "erro")`  — a distinção que não existia.
  2. O offset de relógio vale para TODA chamada assinada, não só as de ordem.
  3. Falha escala uma vez por episódio, não a cada ciclo (senão vira flood).
  4. `canTrade` da CONTA != permissão da CHAVE — a confusão que já enganou uma
     investigação real.

Tudo hermético: `requests` mockado, nenhuma chamada de rede.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import binance_conta as bc  # noqa: E402
import risco  # noqa: E402


class RespostaFake:
    def __init__(self, status, corpo):
        self.status_code = status
        self._corpo = corpo

    def json(self):
        if isinstance(self._corpo, Exception):
            raise self._corpo
        return self._corpo


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch):
    """Offset e estado de falha são globais de módulo: sem reset, um teste
    contamina o próximo."""
    monkeypatch.setattr(bc, "_offset_ms", 0)
    monkeypatch.setattr(bc, "_offset_em", 0.0)
    monkeypatch.setattr(bc, "API_KEY", "k" * 64)
    monkeypatch.setattr(bc, "API_SECRET", "s" * 64)
    risco._estado_saldo.update({"em_falha": False, "ultimo_alerta": 0.0, "ultimo_erro": ""})
    yield
    risco._estado_saldo.update({"em_falha": False, "ultimo_alerta": 0.0, "ultimo_erro": ""})


def _mock_get(monkeypatch, por_url):
    """Roteia requests.get por trecho de URL. Registra as chamadas."""
    chamadas = []

    def fake(url, **kw):
        chamadas.append({"url": url, "params": kw.get("params", {})})
        for trecho, resp in por_url.items():
            if trecho in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"URL nao mockada: {url}")

    monkeypatch.setattr(bc.requests, "get", fake)
    return chamadas


CONTA_OK = {
    "canTrade": True,
    "permissions": ["TRD_GRP_066"],
    "balances": [
        {"asset": "BTC", "free": "0.02071317", "locked": "0.00000000"},
        {"asset": "USDT", "free": "0.00000075", "locked": "0.00000000"},
        {"asset": "ETH", "free": "0.00000000", "locked": "0.00000000"},
    ],
}


# ── 1. A distinção que não existia ─────────────────────────────


class TestZeroNaoEhErro:
    def test_saldo_dust_devolve_valor_e_erro_none(self, monkeypatch):
        """O caso real da investigação: USDT = 0.00000075. É zero de verdade,
        não falha — e o chamador precisa saber disso."""
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": RespostaFake(200, CONTA_OK)})
        valor, erro = bc.saldo("USDT")
        assert valor == pytest.approx(7.5e-07)
        assert erro is None

    def test_ativo_ausente_e_zero_sem_erro(self, monkeypatch):
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": RespostaFake(200, CONTA_OK)})
        assert bc.saldo("DOGE") == (0.0, None)

    def test_falha_de_rede_devolve_zero_COM_erro(self, monkeypatch):
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": ConnectionError("timeout")})
        valor, erro = bc.saldo("USDT")
        assert valor == 0.0
        assert erro and "ConnectionError" in erro

    def test_chave_revogada_devolve_erro_da_binance(self, monkeypatch):
        _mock_get(monkeypatch, {
            "/api/v3/time": RespostaFake(200, {"serverTime": 1}),
            "/api/v3/account": RespostaFake(401, {"code": -2015, "msg": "Invalid API-key"}),
        })
        valor, erro = bc.saldo("USDT")
        assert valor == 0.0
        assert "-2015" in erro and "Invalid API-key" in erro

    def test_chave_placeholder_nao_chega_a_chamar_a_api(self, monkeypatch):
        monkeypatch.setattr(bc, "API_KEY", "your_api_key_here")
        chamadas = _mock_get(monkeypatch, {})
        valor, erro = bc.saldo("USDT")
        assert valor == 0.0 and "placeholder" in erro
        assert chamadas == []  # nem tentou

    def test_corpo_nao_json_nao_estoura(self, monkeypatch):
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": RespostaFake(502, ValueError("nao e json"))})
        valor, erro = bc.saldo("USDT")
        assert valor == 0.0 and "502" in erro

    def test_saldo_corrompido_e_ignorado_sem_derrubar_o_resto(self, monkeypatch):
        corpo = {"balances": [{"asset": "BTC", "free": "abc"},
                              {"asset": "USDT", "free": "5.0"}]}
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": RespostaFake(200, corpo)})
        r = bc.ler_conta()
        assert r["ok"] and r["saldos"] == {"USDT": 5.0}


# ── 2. Offset de relógio nas chamadas assinadas ────────────────


class TestOffsetDeRelogio:
    def test_timestamp_compensa_o_drift(self, monkeypatch):
        agora_ms = 1_700_000_000_000
        monkeypatch.setattr(bc.time, "time", lambda: agora_ms / 1000)
        # servidor 2500ms à frente do relógio local
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": agora_ms + 2500})})
        assert bc.sincronizar_relogio(forcar=True) == 2500
        assert bc.timestamp_ms() == agora_ms + 2500

    def test_offset_e_aplicado_na_chamada_de_conta(self, monkeypatch):
        """A regressão que motivou o módulo: risco.py assinava com time.time()
        cru enquanto executor.py compensava."""
        agora_ms = 1_700_000_000_000
        monkeypatch.setattr(bc.time, "time", lambda: agora_ms / 1000)
        ch = _mock_get(monkeypatch, {
            "/api/v3/time": RespostaFake(200, {"serverTime": agora_ms - 3000}),
            "/api/v3/account": RespostaFake(200, CONTA_OK),
        })
        bc.ler_conta()
        conta = [c for c in ch if "account" in c["url"]][0]
        assert conta["params"]["timestamp"] == agora_ms - 3000

    def test_ttl_evita_resync_a_cada_chamada(self, monkeypatch):
        _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                "/api/v3/account": RespostaFake(200, CONTA_OK)})
        bc.sincronizar_relogio(forcar=True)
        ch = _mock_get(monkeypatch, {"/api/v3/time": RespostaFake(200, {"serverTime": 1}),
                                     "/api/v3/account": RespostaFake(200, CONTA_OK)})
        for _ in range(5):
            bc.ler_conta()
        assert len([c for c in ch if "/time" in c["url"]]) == 0  # TTL segurou

    def test_falha_de_sync_preserva_offset_conhecido(self, monkeypatch):
        """Offset velho é melhor estimativa que assumir zero drift."""
        monkeypatch.setattr(bc, "_offset_ms", 1234)
        monkeypatch.setattr(bc, "_offset_em", 0.0)  # vencido
        _mock_get(monkeypatch, {"/api/v3/time": ConnectionError("sem rede")})
        assert bc.sincronizar_relogio() == 1234

    def test_erro_1021_forca_resync_para_a_proxima(self, monkeypatch):
        """Timestamp fora da janela: re-sincroniza em vez de repetir o erro."""
        ch = _mock_get(monkeypatch, {
            "/api/v3/time": RespostaFake(200, {"serverTime": 1}),
            "/api/v3/account": RespostaFake(400, {"code": -1021, "msg": "Timestamp out of window"}),
        })
        bc.ler_conta()
        assert len([c for c in ch if "/time" in c["url"]]) == 2  # inicial + forçado


# ── 3. Permissão da CHAVE != canTrade da CONTA ─────────────────


class TestRestricoesDaChave:
    def test_chave_read_only_apesar_de_canTrade_da_conta(self, monkeypatch):
        """O caso real: /api/v3/account diz canTrade=True, mas a CHAVE não pode
        negociar. Confundir os dois leva a achar que o bot está liberado."""
        _mock_get(monkeypatch, {
            "/api/v3/time": RespostaFake(200, {"serverTime": 1}),
            "/api/v3/account": RespostaFake(200, CONTA_OK),
            "apiRestrictions": RespostaFake(200, {
                "enableReading": True,
                "enableSpotAndMarginTrading": False,
                "enableWithdrawals": False,
                "enableFutures": False,
                "ipRestrict": False,
            }),
        })
        assert bc.ler_conta()["pode_operar"] is True  # da CONTA
        p = bc.restricoes_chave()
        assert p["ok"] and p["pode_negociar_spot"] is False  # da CHAVE
        assert p["somente_leitura"] is True
        assert p["pode_sacar"] is False

    def test_chave_de_trading_completa(self, monkeypatch):
        _mock_get(monkeypatch, {
            "/api/v3/time": RespostaFake(200, {"serverTime": 1}),
            "apiRestrictions": RespostaFake(200, {
                "enableReading": True, "enableSpotAndMarginTrading": True,
                "enableWithdrawals": False, "ipRestrict": True,
            }),
        })
        p = bc.restricoes_chave()
        assert p["pode_negociar_spot"] and p["restrito_por_ip"]
        assert p["somente_leitura"] is False


# ── 4. Escalada em risco.py, com debounce ──────────────────────


class TestEscaladaEmRisco:
    def test_erro_vira_bot_event_uma_vez_por_episodio(self, monkeypatch):
        """get_saldo_* roda por ciclo por par: sem debounce, uma API fora do ar
        enche bot_events."""
        eventos = []
        monkeypatch.setattr(risco.database, "salvar_bot_event",
                            lambda t, m, **k: eventos.append((t, m)))
        monkeypatch.setattr(risco.binance_conta, "saldo",
                            lambda ativo, **k: (0.0, "[-2015] Invalid API-key"))

        for _ in range(10):
            assert risco.get_saldo_usdt() == 0.0

        assert len(eventos) == 1, f"flood: {len(eventos)} eventos"
        assert eventos[0][0] == "saldo_indisponivel"
        assert "-2015" in eventos[0][1]

    def test_zero_legitimo_nao_gera_evento(self, monkeypatch):
        """Conta vazia é estado normal, não incidente."""
        eventos = []
        monkeypatch.setattr(risco.database, "salvar_bot_event",
                            lambda t, m, **k: eventos.append(t))
        monkeypatch.setattr(risco.binance_conta, "saldo", lambda ativo, **k: (0.0, None))
        assert risco.get_saldo_usdt() == 0.0
        assert eventos == []

    def test_recuperacao_rearma_o_alerta(self, monkeypatch):
        """Depois de voltar, uma nova falha tem que alertar de novo -- senão o
        segundo incidente passa silencioso."""
        eventos = []
        monkeypatch.setattr(risco.database, "salvar_bot_event",
                            lambda t, m, **k: eventos.append(t))
        estado = {"erro": "[-2015] revogada"}
        monkeypatch.setattr(risco.binance_conta, "saldo",
                            lambda ativo, **k: (0.0, estado["erro"]) if estado["erro"] else (9.0, None))

        risco.get_saldo_usdt()          # falha -> 1 evento
        estado["erro"] = None
        assert risco.get_saldo_usdt() == 9.0  # recupera
        estado["erro"] = "[-1021] drift"
        risco.get_saldo_usdt()          # falha de novo -> 2o evento

        assert len(eventos) == 2

    def test_valor_bom_passa_intacto(self, monkeypatch):
        monkeypatch.setattr(risco.binance_conta, "saldo", lambda ativo, **k: (1234.56, None))
        assert risco.get_saldo_usdt() == pytest.approx(1234.56)
        assert risco.get_saldo_btc() == pytest.approx(1234.56)

    def test_evento_falhando_nao_derruba_o_sizing(self, monkeypatch):
        """Telemetria nunca pode quebrar o caminho de decisão."""
        def explode(*a, **k):
            raise RuntimeError("db fora")

        monkeypatch.setattr(risco.database, "salvar_bot_event", explode)
        monkeypatch.setattr(risco.binance_conta, "saldo", lambda ativo, **k: (0.0, "erro"))
        assert risco.get_saldo_usdt() == 0.0  # não levanta


# ── 5. Sem duplicação: dashboard usa a mesma fonte ─────────────


def test_dashboard_nao_reimplementa_leitura_de_conta():
    """A duplicação era o problema estrutural: a cópia do dashboard era melhor
    que a do risco. Se alguém reintroduzir uma assinatura manual ali, as duas
    voltam a divergir."""
    import inspect

    import dashboard

    src = inspect.getsource(dashboard.api_conexao)
    assert "binance_conta" in src
    assert "/api/v3/account" not in src, "dashboard voltou a assinar na mão"
