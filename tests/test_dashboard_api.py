"""
Testes das rotas REST do dashboard.py via Flask test client.
Sem WebSocket real, sem banco real — database é mockado.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch database e socketio ANTES de importar dashboard
_db_mock = MagicMock()
_db_mock.healthcheck.return_value = True
_db_mock.backend_info.return_value = {"backend": "sqlite", "db_path": ":memory:"}
_db_mock.buscar_sinais.return_value = []
_db_mock.buscar_trades_recentes = MagicMock(return_value=[])

with patch.dict(
    "sys.modules",
    {
        "database": _db_mock,
        "flask_socketio": MagicMock(SocketIO=MagicMock(return_value=MagicMock())),
        "flask_cors": MagicMock(CORS=MagicMock()),
        "websocket": MagicMock(),
        "risco": MagicMock(status=MagicMock(return_value={"capital": 1000.0})),
    },
):
    import dashboard


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    dashboard.app.config["SECRET_KEY"] = "test-secret"
    with dashboard.app.test_client() as c:
        yield c


# ── Testes: /health ───────────────────────────────────────────────────────────


class TestHealth:
    def test_retorna_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_payload_json(self, client):
        r = client.get("/health")
        data = r.get_json()
        assert data["status"] == "ok"
        assert data["role"] == "dashboard"

    def test_inclui_database(self, client):
        r = client.get("/health")
        data = r.get_json()
        assert "database" in data


# ── Testes: /ready ────────────────────────────────────────────────────────────


class TestReady:
    def test_200_quando_db_ok(self, client):
        _db_mock.healthcheck.return_value = True
        r = client.get("/ready")
        assert r.status_code == 200

    def test_503_quando_db_fail(self, client):
        _db_mock.healthcheck.return_value = False
        r = client.get("/ready")
        assert r.status_code == 503
        _db_mock.healthcheck.return_value = True  # restaurar

    def test_payload_status(self, client):
        r = client.get("/ready")
        data = r.get_json()
        assert "status" in data


# ── Testes: /api/pares ────────────────────────────────────────────────────────


class TestApiPares:
    def test_retorna_lista(self, client):
        r = client.get("/api/pares")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)

    def test_cada_par_tem_symbol(self, client):
        r = client.get("/api/pares")
        data = r.get_json()
        for item in data:
            assert "symbol" in item

    def test_pares_ativos_presentes(self, client):
        r = client.get("/api/pares")
        data = r.get_json()
        symbols = {p["symbol"] for p in data}
        assert "BTCUSDT" in symbols


# ── Testes: /api/estado ───────────────────────────────────────────────────────


class TestApiEstado:
    def test_btcusdt_retorna_200(self, client):
        r = client.get("/api/estado/BTCUSDT")
        assert r.status_code == 200

    def test_par_invalido_retorna_404(self, client):
        r = client.get("/api/estado/XXXYYY")
        assert r.status_code == 404

    def test_payload_tem_preco(self, client):
        r = client.get("/api/estado/BTCUSDT")
        data = r.get_json()
        assert "preco" in data

    def test_estado_sem_symbol_usa_btcusdt(self, client):
        r = client.get("/api/estado")
        assert r.status_code == 200


# ── Testes: /api/score ────────────────────────────────────────────────────────


class TestApiScore:
    def test_retorna_200(self, client):
        r = client.get("/api/score/BTCUSDT")
        assert r.status_code == 200

    def test_payload_tem_score(self, client):
        r = client.get("/api/score/BTCUSDT")
        data = r.get_json()
        assert "score" in data
        assert "decisao" in data

    def test_symbol_uppercase(self, client):
        r = client.get("/api/score/btcusdt")
        assert r.status_code == 200


# ── Testes: /api/eventos ──────────────────────────────────────────────────────


class TestApiEventos:
    def test_retorna_lista(self, client):
        r = client.get("/api/eventos")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)

    def test_max_100_eventos(self, client):
        # Preencher com mais de 100 eventos
        with dashboard._lock:
            dashboard.eventos_sistema.clear()
            for i in range(150):
                dashboard.eventos_sistema.append({"id": i})
        r = client.get("/api/eventos")
        data = r.get_json()
        assert len(data) <= 100
        with dashboard._lock:
            dashboard.eventos_sistema.clear()


# ── Testes: /api/trades ───────────────────────────────────────────────────────


class TestApiTrades:
    def test_retorna_lista(self, client):
        r = client.get("/api/trades")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)


# ── Testes: /api/sinais ───────────────────────────────────────────────────────


class TestApiSinais:
    def test_retorna_lista(self, client):
        _db_mock.buscar_sinais.return_value = []
        r = client.get("/api/sinais")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)

    def test_delega_ao_database(self, client):
        sinais_fake = [{"tipo": "COMPRA", "preco": 64000.0, "motivo": "RSI 55"}]
        _db_mock.buscar_sinais.return_value = sinais_fake
        r = client.get("/api/sinais")
        data = r.get_json()
        assert len(data) == 1
        assert data[0]["tipo"] == "COMPRA"
        _db_mock.buscar_sinais.return_value = []


# ── Testes: /api/risco ────────────────────────────────────────────────────────


class TestApiRisco:
    def test_retorna_200(self, client):
        r = client.get("/api/risco")
        assert r.status_code == 200

    def test_payload_tem_campos_risco(self, client):
        r = client.get("/api/risco")
        data = r.get_json()
        assert "bloqueado" in data


# ── Testes: hardening de segurança web (P2) ──────────────────────────────────


class TestSegurancaWeb:
    def test_headers_de_seguranca_presentes(self, client):
        r = client.get("/api/risco")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        # sem hosts de CDN externos no script-src (libs vendorizadas)
        assert "cdn.socket.io" not in csp and "jsdelivr" not in csp
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_vendor_serve_libs_locais(self, client):
        r = client.get("/vendor/socket.io.min.js")
        assert r.status_code == 200
        assert b"Socket.IO" in r.data

    def test_rate_limit_dispara_429(self, client):
        # zera o estado do limiter e satura a janela para o IP de teste
        dashboard._rate_hits.clear()
        limite = dashboard._RATE_LIMITE
        codes = [client.get("/api/estado/BTCUSDT").status_code for _ in range(limite + 5)]
        assert 429 in codes, "rate limit nao disparou apos o limite"
        assert codes[0] == 200, "primeiras requisicoes deveriam passar"
        dashboard._rate_hits.clear()

    def test_token_bloqueia_quando_configurado(self, client):
        dashboard._rate_hits.clear()
        original = dashboard._DASHBOARD_TOKEN
        dashboard._DASHBOARD_TOKEN = "segredo-teste"
        try:
            assert client.get("/api/risco").status_code == 401
            assert client.get("/api/risco?token=segredo-teste").status_code == 200
        finally:
            dashboard._DASHBOARD_TOKEN = original
            dashboard._rate_hits.clear()
