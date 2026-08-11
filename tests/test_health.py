"""
Testes herméticos do servidor de health (health.py).
Não abre sockets reais — testa a lógica de payload e roteamento diretamente.
"""

import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

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
        threads = [
            threading.Thread(target=lambda: health.increment_metric("sinais_total"))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with health._metrics_lock:
            assert health._metrics["sinais_total"] == 20


# ── Testes: set_gauge / set_regime_atual (P2-5) ────────────────────────────────


class TestSetGauge:
    def setup_method(self):
        with health._gauges_lock:
            health._gauges.clear()

    def test_define_valor(self):
        health.set_gauge("pnl_dia", 42.5)
        with health._gauges_lock:
            assert health._gauges["pnl_dia"] == 42.5

    def test_sobrescreve_valor_anterior(self):
        health.set_gauge("pnl_dia", 10.0)
        health.set_gauge("pnl_dia", -5.0)
        with health._gauges_lock:
            assert health._gauges["pnl_dia"] == -5.0

    def test_aparece_no_metrics_text_como_gauge(self):
        health.set_gauge("pnl_dia", 12.3)
        out = health._metrics_text().decode()
        assert "botbinance_pnl_dia 12.3" in out
        assert "# TYPE botbinance_pnl_dia gauge" in out

    def test_thread_safe(self):
        threads = [
            threading.Thread(target=lambda i=i: health.set_gauge("x", float(i))) for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with health._gauges_lock:
            assert health._gauges["x"] in {float(i) for i in range(20)}


class TestSetRegimeAtual:
    def setup_method(self):
        with health._gauges_lock:
            health._gauges.clear()

    def test_regime_conhecido_liga_so_o_ativo(self):
        health.set_regime_atual("LATERAL")
        with health._gauges_lock:
            assert health._gauges["regime_lateral"] == 1.0
            assert health._gauges["regime_tendencia_alta"] == 0.0
            assert health._gauges["regime_tendencia_baixa"] == 0.0
            assert health._gauges["regime_volatilidade"] == 0.0
            assert health._gauges["regime_indefinido"] == 0.0

    def test_regime_desconhecido_cai_em_indefinido(self):
        health.set_regime_atual("ALGO_QUE_NAO_EXISTE")
        with health._gauges_lock:
            assert health._gauges["regime_indefinido"] == 1.0
            assert health._gauges["regime_lateral"] == 0.0

    def test_regime_none_cai_em_indefinido(self):
        health.set_regime_atual(None)
        with health._gauges_lock:
            assert health._gauges["regime_indefinido"] == 1.0

    def test_troca_de_regime_desliga_o_anterior(self):
        health.set_regime_atual("TENDENCIA_ALTA")
        health.set_regime_atual("VOLATILIDADE")
        with health._gauges_lock:
            assert health._gauges["regime_tendencia_alta"] == 0.0
            assert health._gauges["regime_volatilidade"] == 1.0


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


# ── Testes: watchdog @depth (OBI, P1-1) ────────────────────────────────────


class TestReadyStreamDepth:
    """/ready reporta o stream @depth (OBI) como informativo -- nunca gate
    o status code, ao contrario do watchdog do stream @aggTrade."""

    def _handler_capturando_corpo(self, path: str):
        handler = health.HealthHandler.__new__(health.HealthHandler)
        handler.path = path
        handler.role = "worker"
        responses = []
        corpo = []

        handler.send_response = lambda code: responses.append(code)
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler.wfile = MagicMock()
        handler.wfile.write = lambda data: corpo.append(data)
        return handler, responses, corpo

    @pytest.fixture(autouse=True)
    def _resetar_refs(self):
        original_ws = health._ws_state_ref
        original_depth = health._ws_state_depth_ref
        yield
        health._ws_state_ref = original_ws
        health._ws_state_depth_ref = original_depth

    def test_registrar_ws_state_depth_seta_a_referencia(self):
        ref = {"connected": True, "last_message_time": 0.0}
        health.registrar_ws_state_depth(ref)
        assert health._ws_state_depth_ref is ref

    def test_obi_stream_ok_true_quando_depth_fresco(self):
        health.registrar_ws_state_depth({"connected": True, "last_message_time": time.time()})
        mock_db = MagicMock()
        mock_db.healthcheck.return_value = True
        handler, responses, corpo = self._handler_capturando_corpo("/ready")
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        payload = json.loads(corpo[0])
        assert payload["obi_stream_ok"] is True
        assert 200 in responses

    def test_obi_stream_stale_nao_derruba_o_ready(self):
        # depth stale (last_message_time bem antigo) NAO deve virar 503 --
        # OBI e suplementar (8% do peso), stale so degrada o componente do
        # score para neutro, nao o worker inteiro.
        health.registrar_ws_state_depth(
            {"connected": True, "last_message_time": time.time() - 1000}
        )
        mock_db = MagicMock()
        mock_db.healthcheck.return_value = True
        handler, responses, corpo = self._handler_capturando_corpo("/ready")
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        payload = json.loads(corpo[0])
        assert payload["obi_stream_ok"] is False
        assert "ws_depth_stale" in (payload.get("error") or "")
        assert 200 in responses  # NAO 503 -- so informativo

    def test_sem_registrar_ws_state_depth_nao_quebra(self):
        health._ws_state_depth_ref = None
        mock_db = MagicMock()
        mock_db.healthcheck.return_value = True
        handler, responses, _ = self._handler_capturando_corpo("/ready")
        with patch.dict("sys.modules", {"database": mock_db}):
            handler.do_GET()
        assert 200 in responses

    def test_depth_stale_nao_afeta_health_simples(self):
        # /health (liveness) nunca olha para nenhum ws_state -- so /ready faz.
        health.registrar_ws_state_depth(
            {"connected": False, "last_message_time": time.time() - 1000}
        )
        handler, responses, _ = self._handler_capturando_corpo("/health")
        handler.do_GET()
        assert 200 in responses
