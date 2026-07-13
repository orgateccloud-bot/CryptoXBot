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

# P1: referencia viva ao ws_state do worker (injetada por main.py) — permite
# ao /ready detectar WebSocket zumbi sem importar o modulo main.
_ws_state_ref: dict[str, Any] | None = None

# P1-1: referencia viva ao ws_state_depth (stream @depth, OBI) — mesma ideia
# do ws_state acima, mas so INFORMATIVA no payload de /ready (nao gate a
# prontidao): OBI e um componente suplementar do score (8% do peso), stale
# so degrada esse componente para neutro (ver main.obter_obi_suavizado), nao
# justifica marcar o worker inteiro como not-ready/reiniciar o servico, ao
# contrario do @aggTrade (preco/CVD), que e critico para operar com dados
# corretos.
_ws_state_depth_ref: dict[str, Any] | None = None


def registrar_ws_state(ref: dict[str, Any]) -> None:
    global _ws_state_ref
    _ws_state_ref = ref


def registrar_ws_state_depth(ref: dict[str, Any]) -> None:
    global _ws_state_depth_ref
    _ws_state_depth_ref = ref


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


def _payload(
    status: str, role: str, ready: bool = True, extra: dict[str, Any] | None = None
) -> bytes:
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
        ws_ok = True
        ws_depth_ok = True
        error = None
        if self.path == "/ready":
            try:
                import database

                db_ok = database.healthcheck()
            except Exception as exc:  # pragma: no cover - defensive endpoint
                db_ok = False
                error = str(exc)

            # P1: /ready tambem exige WebSocket vivo (worker) — detecta conexao
            # zumbi/CVD congelado que o check de DB nunca enxergaria. A referencia
            # e injetada por main.py via registrar_ws_state() (import main aqui
            # criaria um modulo duplicado com ws_state vazio).
            if self.role == "worker" and _ws_state_ref is not None:
                try:
                    import time as _t

                    idade = _t.time() - float(_ws_state_ref.get("last_message_time") or 0)
                    ws_ok = bool(_ws_state_ref.get("connected")) and idade < 120
                    if not ws_ok:
                        error = (error or "") + f" ws_stale={idade:.0f}s"
                except Exception:  # pragma: no cover - defensive
                    pass

            # P1-1: stream @depth (OBI) — so INFORMATIVO (nao gate 'pronto').
            # OBI e um componente suplementar do score (8%); stale so degrada
            # esse componente para neutro (main.obter_obi_suavizado), nao
            # justifica marcar o worker inteiro not-ready.
            if self.role == "worker" and _ws_state_depth_ref is not None:
                try:
                    import time as _t

                    idade_depth = _t.time() - float(
                        _ws_state_depth_ref.get("last_message_time") or 0
                    )
                    ws_depth_ok = bool(_ws_state_depth_ref.get("connected")) and idade_depth < 120
                    if not ws_depth_ok:
                        error = (error or "") + f" ws_depth_stale={idade_depth:.0f}s"
                except Exception:  # pragma: no cover - defensive
                    pass

        pronto = db_ok and ws_ok
        status_code = 200 if pronto else 503
        extra = {"error": error} if error else None
        if self.path == "/ready" and self.role == "worker":
            extra = extra or {}
            extra["obi_stream_ok"] = ws_depth_ok
        body = _payload("ok" if pronto else "degraded", self.role, db_ok, extra)

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
