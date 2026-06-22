"""
Small HTTP health server for Railway worker deployments.

The dashboard exposes its own Flask health route. The trading worker uses this
module so Railway can probe /health without changing the trading loop itself.

Endpoints:
  /health  → sempre 200 (liveness)
  /ready   → 200/503 dependendo do DB (readiness)
  /metrics → contadores estruturados do worker (Prometheus-compatible text)
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config.runtime_settings import DATABASE_BACKEND, PORT


_server: ThreadingHTTPServer | None = None

# Contadores globais — atualizados pelas threads de trading
_metrics: dict[str, float] = {
    "sinais_total": 0,
    "ordens_total": 0,
    "ordens_erro": 0,
    "circuit_breaker_ativacoes": 0,
    "drawdown_bloqueios": 0,
    "ws_reconexoes": 0,
    "uptime_start": time.time(),
}
_metrics_lock = threading.Lock()


def increment_metric(name: str, value: float = 1.0) -> None:
    """Incrementa um contador de métricas de forma thread-safe."""
    with _metrics_lock:
        if name in _metrics:
            _metrics[name] += value


def _metrics_text() -> bytes:
    """Gera resposta Prometheus-compatible (text/plain)."""
    with _metrics_lock:
        snap = dict(_metrics)
    uptime = time.time() - snap.pop("uptime_start", time.time())
    lines = [
        "# HELP botbinance_uptime_seconds Segundos desde o inicio do worker",
        "# TYPE botbinance_uptime_seconds gauge",
        f"botbinance_uptime_seconds {uptime:.1f}",
    ]
    for key, val in snap.items():
        metric_name = f"botbinance_{key}"
        lines.append(f"# TYPE {metric_name} counter")
        lines.append(f"{metric_name} {val}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _payload(status: str, role: str, ready: bool = True, extra: dict[str, Any] | None = None) -> bytes:
    data = {
        "status": status,
        "role": role,
        "ready": ready,
        "database_backend": DATABASE_BACKEND,
    }
    if extra:
        data.update(extra)
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


class HealthHandler(BaseHTTPRequestHandler):
    role = "worker"

    def do_GET(self):  # noqa: N802 - stdlib handler naming
        if self.path == "/metrics":
            body = _metrics_text()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path not in {"/health", "/ready"}:
            self.send_response(404)
            self.end_headers()
            return

        db_ok = True
        error = None
        if self.path == "/ready":
            try:
                import database

                db_ok = database.healthcheck()
            except Exception as exc:  # pragma: no cover - defensive endpoint
                db_ok = False
                error = str(exc)

        status_code = 200 if db_ok else 503
        body = _payload("ok" if db_ok else "degraded", self.role, db_ok, {"error": error} if error else None)

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return


def start_health_server(role: str = "worker", port: int | None = None) -> None:
    """Start a daemon HTTP server if it is not already running."""
    global _server
    if _server is not None:
        return

    bind_port = port or PORT
    HealthHandler.role = role
    _server = ThreadingHTTPServer(("0.0.0.0", bind_port), HealthHandler)
    thread = threading.Thread(target=_server.serve_forever, daemon=True, name=f"health-{role}")
    thread.start()

