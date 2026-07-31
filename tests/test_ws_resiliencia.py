"""
Testes — o WebSocket não pode desistir num processo 24/7
=========================================================
Incidente de 2026-07-31, e antes dele uma queda de 8h40 em julho.

`attempt` era inicializado FORA do while e nunca resetado numa conexão
bem-sucedida, com `while attempt < max_retries` (10). Num serviço 24/7 isso é
certeza matemática: dez falhas espalhadas por dias encerram o WebSocket **para
sempre**, e nada reconecta. Pior, o ramo `except Exception` genérico dormia 1,0s
fixo — uma falha imediata e repetida queimava o orçamento inteiro em ~8s.

O que se mediu no dia: os dois WebSockets com "Máximo de tentativas atingido"
minutos após o boot, `/ready` em 503 com `ws_stale=1785532757s`, e o worker
seguindo Running, avaliando sinal com CVD e tape congelados. O log não ajudava
porque o `extra=` era descartado pelo `lastResort` do stdlib.

O que estes testes travam:
  1. O loop NUNCA sai por esgotamento — só por shutdown.
  2. `falhas` zera na conexão bem-sucedida (a correção central).
  3. O ramo genérico usa o MESMO backoff do ramo de rede.
  4. Falha persistente ESCALA uma vez, e rearma depois de recuperar.
  5. `/ready` não pode dizer `"ready": true` num 503.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch):
    main._shutdown_event.clear()
    monkeypatch.setattr(main, "_ws_escalado", {})
    yield
    main._shutdown_event.clear()


async def _sem_espera(*a, **k):
    return None


# ── 1. Backoff ────────────────────────────────────────────────


class TestBackoff:
    def test_cresce_e_satura_no_teto(self):
        d = [main._ws_delay_backoff(n, jitter=0) for n in range(0, 14)]
        assert d[0] == pytest.approx(1.0)
        assert d[3] == pytest.approx(8.0)
        assert all(b >= a for a, b in zip(d, d[1:])), "tem que ser monotônico"
        assert d[-1] == pytest.approx(main.WS_DELAY_TETO_S)

    def test_nao_estoura_com_contador_enorme(self):
        """Processo de vida longa: 2**n sem limite viraria OverflowError."""
        assert main._ws_delay_backoff(10_000, jitter=0) == pytest.approx(main.WS_DELAY_TETO_S)

    def test_jitter_fica_dentro_da_faixa(self):
        vals = [main._ws_delay_backoff(5) for _ in range(200)]
        base = main._ws_delay_backoff(5, jitter=0)
        assert all(0.9 * base * 0.99 <= v <= 1.1 * base * 1.01 for v in vals)

    def test_nunca_devolve_zero_ou_negativo(self):
        assert all(main._ws_delay_backoff(n) >= 0.1 for n in (-5, 0, 1, 99))


# ── 2. Escalada e rearme ──────────────────────────────────────


class TestEscalada:
    def _captura(self, monkeypatch):
        ev = []
        monkeypatch.setattr(main.database, "salvar_bot_event",
                            lambda t, m, **k: ev.append((t, k.get("severity"))))
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)
        monkeypatch.setattr(main.logger, "critical", lambda *a, **k: None)
        return ev

    def test_nao_escala_antes_do_limiar(self, monkeypatch):
        ev = self._captura(monkeypatch)
        for n in range(1, main.WS_FALHAS_PARA_ESCALAR):
            main._ws_escalar_se_persistente("WS", n, "erro")
        assert ev == []

    def test_escala_uma_unica_vez_por_episodio(self, monkeypatch):
        ev = self._captura(monkeypatch)
        for n in range(main.WS_FALHAS_PARA_ESCALAR, main.WS_FALHAS_PARA_ESCALAR + 40):
            main._ws_escalar_se_persistente("WS", n, "erro")
        assert len(ev) == 1 and ev[0] == ("ws_indisponivel", "CRITICAL")

    def test_recuperacao_rearma_para_o_proximo_episodio(self, monkeypatch):
        """Sem rearme, a SEGUNDA queda passaria silenciosa — pior que a primeira,
        porque aí ninguém mais está olhando."""
        ev = self._captura(monkeypatch)
        main._ws_escalar_se_persistente("WS", main.WS_FALHAS_PARA_ESCALAR, "e1")
        main._ws_marcar_recuperado("WS", main.WS_FALHAS_PARA_ESCALAR)
        main._ws_escalar_se_persistente("WS", main.WS_FALHAS_PARA_ESCALAR, "e2")
        tipos = [t for t, _ in ev]
        assert tipos == ["ws_indisponivel", "ws_recuperado", "ws_indisponivel"]

    def test_os_dois_websockets_escalam_independentemente(self, monkeypatch):
        ev = self._captura(monkeypatch)
        main._ws_escalar_se_persistente("WebSocket", main.WS_FALHAS_PARA_ESCALAR, "e")
        main._ws_escalar_se_persistente("WebSocket @depth", main.WS_FALHAS_PARA_ESCALAR, "e")
        assert len(ev) == 2

    def test_falha_do_canal_de_alerta_nao_derruba_o_ws(self, monkeypatch):
        def explode(*a, **k):
            raise RuntimeError("telegram fora")

        monkeypatch.setattr(main.database, "salvar_bot_event", explode)
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", explode)
        monkeypatch.setattr(main.logger, "critical", lambda *a, **k: None)
        main._ws_escalar_se_persistente("WS", main.WS_FALHAS_PARA_ESCALAR, "e")  # não levanta


# ── 3. O loop não desiste ─────────────────────────────────────


class TestNuncaDesiste:
    def _rodar(self, handler, conectar, monkeypatch):
        monkeypatch.setattr(main.websockets, "connect", conectar)
        monkeypatch.setattr(main.asyncio, "sleep", _sem_espera)
        monkeypatch.setattr(main.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler())
        finally:
            loop.close()

    def test_sobrevive_a_muito_mais_de_dez_falhas(self, monkeypatch):
        """O teto antigo era 10 e valia para a vida INTEIRA do processo."""
        n = {"v": 0}

        def conectar(*a, **k):
            n["v"] += 1
            if n["v"] >= 60:
                main._shutdown_event.set()  # única saída válida do loop
            raise main.websockets.exceptions.WebSocketException("recusada")

        self._rodar(main.websocket_handler, conectar, monkeypatch)
        assert n["v"] >= 60, f"desistiu apos {n['v']} tentativas"

    def test_ramo_generico_tambem_nao_desiste(self, monkeypatch):
        """O ConnectionResetError do incidente caía AQUI, com sleep de 1s fixo."""
        n = {"v": 0}

        def conectar(*a, **k):
            n["v"] += 1
            if n["v"] >= 60:
                main._shutdown_event.set()
            raise ConnectionResetError("[WinError 64] nome da rede indisponivel")

        self._rodar(main.websocket_handler, conectar, monkeypatch)
        assert n["v"] >= 60

    def test_depth_tambem_nao_desiste(self, monkeypatch):
        n = {"v": 0}

        def conectar(*a, **k):
            n["v"] += 1
            if n["v"] >= 40:
                main._shutdown_event.set()
            raise ConnectionResetError("reset")

        self._rodar(main.websocket_handler_depth, conectar, monkeypatch)
        assert n["v"] >= 40

    def test_shutdown_continua_encerrando_o_loop(self, monkeypatch):
        """Não desistir por falha não pode virar 'não parar nunca'."""
        main._shutdown_event.set()
        n = {"v": 0}

        def conectar(*a, **k):
            n["v"] += 1
            raise ConnectionResetError("x")

        self._rodar(main.websocket_handler, conectar, monkeypatch)
        assert n["v"] == 0, "não deveria nem tentar com shutdown pedido"


# ── 4. Reset do contador na reconexão ─────────────────────────


class TestResetNaReconexao:
    def test_conexao_bem_sucedida_zera_o_contador(self, monkeypatch):
        """A correção central. Antes, falhas intercaladas com sucessos somavam
        até 10 e encerravam o WebSocket para sempre."""
        recuperacoes = []
        monkeypatch.setattr(main, "_ws_marcar_recuperado",
                            lambda rot, f: recuperacoes.append(f))
        monkeypatch.setattr(main.asyncio, "sleep", _sem_espera)
        monkeypatch.setattr(main.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)
        monkeypatch.setattr(main, "process_message", _sem_espera)

        ciclo = {"v": 0}

        class ConexaoFake:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                # entrega uma mensagem e derruba, forcando novo ciclo
                raise StopAsyncIteration

        def conectar(*a, **k):
            ciclo["v"] += 1
            # 3 falhas, 1 sucesso, 3 falhas, 1 sucesso, encerra
            if ciclo["v"] in (4, 8):
                return ConexaoFake()
            if ciclo["v"] >= 9:
                main._shutdown_event.set()
                return ConexaoFake()
            raise ConnectionResetError("falha")

        loop = asyncio.new_event_loop()
        monkeypatch.setattr(main.websockets, "connect", conectar)
        try:
            loop.run_until_complete(main.websocket_handler())
        finally:
            loop.close()

        # Nas duas reconexões o contador acumulado tinha que ser 3, não 3 e 6.
        assert recuperacoes[:2] == [3, 3], f"contador não zerou: {recuperacoes}"


# ── 5. /ready não pode mentir ─────────────────────────────────


class TestReadyCoerente:
    def test_payload_ready_acompanha_o_status(self):
        """Passava-se db_ok no parâmetro `ready`, então um 503 por WebSocket
        morto trazia "ready": true. Medido: HTTP 503 com {"ready": true,
        "error": " ws_stale=1785532757s"}."""
        import json

        import health

        degradado = json.loads(health._payload("degraded", "worker", False, {"error": " ws_stale=9s"}))
        assert degradado["ready"] is False
        assert degradado["status"] == "degraded"

        ok = json.loads(health._payload("ok", "worker", True, None))
        assert ok["ready"] is True and ok["status"] == "ok"

    def test_handler_passa_pronto_e_nao_db_ok(self):
        """Trava a correção no ponto de chamada, não só no _payload."""
        import inspect

        import health

        src = inspect.getsource(health.HealthHandler)
        assert "_payload(\"ok\" if pronto else \"degraded\", self.role, pronto" in src, (
            "o 3o argumento posicional de _payload tem que ser `pronto`, não `db_ok`"
        )
