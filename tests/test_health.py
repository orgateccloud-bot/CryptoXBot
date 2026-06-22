"""
Testes herméticos do servidor de health (health.py).
Não abre sockets reais — testa a lógica de payload e roteamento diretamente.
"""

import json
import sys
import os
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import health


# ── Testes: _payload ──────────────────────────────────────────────────────────

class TestPayloadHelper:
    def test_estrutura_basica(self):
        raw = health._payload("ok", "worker", ready=True)
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["role"] == "worker"
        assert data["ready"] is True

    def test_extra_fields(self):
        raw = health._payload("degraded", "worker", ready=False, extra={"error": "timeout"})
        data = json.loads(raw)
        assert data["error"] == "timeout"
        assert data["ready"] is False

    def test_retorna_bytes(self):
        raw = health._payload("ok", "worker")
        assert isinstance(raw, bytes)

    def test_inclui_database_backend(self):
        raw = health._payload("ok", "worker")
        data = json.loads(raw)
        assert "database_backend" in data


# ── Testes: _metrics_text ─────────────────────────────────────────────────────

class TestMetricsText:
    def test_retorna_bytes(self):
        out = health._metrics_text()
        assert isinstance(out, bytes)

    def test_contem_uptime(self):
        out = health._metrics_text().decode()
        assert "uptime_seconds" in out

    def test_formato_prometheus(self):
        out = health._metrics_text().decode()
        # Cada métrica deve ter linha # TYPE
        assert "# TYPE" in out

    def test_contadores_presentes(self):
        out = health._metrics_text().decode()
        assert "botbinance_sinais_total" in out
        assert "botbinance_ordens_total" in out


# ── Testes: increment_metric ──────────────────────────────────────────────────

class TestIncrementMetric:
    def setup_method(self):
        # Reset dos contadores antes de cada teste
        with health._metrics_lock:
            health._metrics["sinais_total"] = 0
            health._metrics["ordens_total"] = 0
            health._metrics["ordens_erro"] = 0

    def test_incrementa_contador(self):
        health.increment_metric("sinais_total")
        with health._metrics_lock:
            assert health._metrics["sinais_total"] == 1

    def test_incrementa_por_valor(self):
        health.increment_metric("ordens_total", 5)
        with health._metrics_lock:
            assert health._metrics["ordens_total"] == 5

    def test_nome_invalido_nao_quebra(self):
        health.increment_metric("nao_existe_xyz")  # não deve lançar exceção

    def test_thread_safe(self):
        with health._metrics_lock:
            health._metrics["sinais_total"] = 0
        threads = [threading.Thread(target=lambda: health.increment_metric("sinais_total")) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with health._metrics_lock:
            assert health._metrics["sinais_total"] == 20


# ── Testes: HealthHandler (roteamento direto) ─────────────────────────────────

class TestHealthHandlerRouting:
    """Testa o roteamento via path sem abrir sockets reais."""

    def _handler(self, path: str):
        handler = health.HealthHandler.__new__(health.HealthHandler)
        handler.path = path
        handler.role = "worker"
        responses = []

        def fake_send_response(code):
            responses.append(code)

        def fake_send_header(k, v):
            pass

        def fake_end_headers():
            pass

        def fake_write(data):
            pass

        handler.send_response = fake_send_response
        handler.send_header = fake_send_header
        handler.end_headers = fake_end_headers
        handler.wfile = MagicMock()
        handler.wfile.write = fake_write
        return handler, responses

    def test_health_retorna_200(self):
        handler, responses = self._handler("/health")
        handler.do_GET()
        assert 200 in responses

    def test_unknown_path_retorna_404(self):
        handler, responses = self._handler("/nao-existe")
        handler.do_GET()
        assert 404 in responses

    def test_metrics_retorna_200(self):
        handler, responses = self._handler("/metrics")
        handler.do_GET()
        assert 200 in responses

    def test_ready_db_ok(self):
        handler, responses = self._handler("/ready")
        mock_db = MagicMock()
        mock_db.healthcheck.return_value = True
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        assert 200 in responses

    def test_ready_db_fail(self):
        handler, responses = self._handler("/ready")
        mock_db = MagicMock()
        mock_db.healthcheck.return_value = False
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        assert 503 in responses

    def test_ready_db_exception(self):
        handler, responses = self._handler("/ready")
        mock_db = MagicMock()
        mock_db.healthcheck.side_effect = RuntimeError("conn failed")
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        assert 503 in responses
