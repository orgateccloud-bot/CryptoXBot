"""
Testes — shutdown gracioso dos WebSockets sem ruído de log
===========================================================
Todo stop limpo do worker escrevia no stderr:

    Task was destroyed but it is pending!
    RuntimeError: Event loop is closed
    Erro critico WebSocket
    RuntimeError: no running event loop

Nada disso era falha: `_encerrar` para o loop debaixo de um `await`, e o
interpretador reclama na finalização. Mas a string "Erro critico WebSocket" é
justamente a que se usaria para caçar uma falha REAL (conexão zumbi, CVD
congelado — o que o watchdog de `/ready` existe para pegar). Aparecendo em toda
parada normal, ela deixa de ser sinal.

O risco de "consertar" isso é engolir falha de verdade junto com o ruído. Por
isso cada teste aqui tem par: shutdown **não** loga crítico, e falha fora de
shutdown **continua** logando.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


async def _sem_espera(*a, **k):
    """Substitui asyncio.sleep: sem isso o backoff exponencial (até 300s por
    tentativa, 10 tentativas) faria estes testes levarem horas."""
    return None


@pytest.fixture(autouse=True)
def _shutdown_limpo():
    """A flag é global do módulo: garante estado conhecido e restaura depois,
    senão um teste vaza shutdown para o próximo (e para o resto da suíte)."""
    estava = main._shutdown_event.is_set()
    main._shutdown_event.clear()
    yield
    if estava:
        main._shutdown_event.set()
    else:
        main._shutdown_event.clear()


@pytest.fixture
def logs(monkeypatch):
    """Captura o que cada logger recebeu, por nível."""
    cap = {"error": [], "critical": [], "warning": [], "info": []}
    for nivel in ("error", "critical", "warning"):
        monkeypatch.setattr(main.logger, nivel, lambda msg, _n=nivel, **kw: cap[_n].append(msg))
    monkeypatch.setattr(main.ws_logger, "info", lambda msg, **kw: cap["info"].append(msg))
    monkeypatch.setattr(main.ws_logger, "error", lambda msg, **kw: cap["error"].append(msg))
    return cap


# ── _ws_encerrando: o discriminador ────────────────────────────


class TestWsEncerrando:
    def test_falso_fora_de_shutdown(self, logs):
        assert main._ws_encerrando("WebSocket") is False
        assert logs["info"] == []  # não loga nada se não é shutdown

    def test_verdadeiro_e_loga_info_em_shutdown(self, logs):
        main._shutdown_event.set()
        assert main._ws_encerrando("WebSocket") is True
        assert len(logs["info"]) == 1
        assert "shutdown" in logs["info"][0].lower()
        assert logs["error"] == [] and logs["critical"] == []

    def test_rotulo_aparece_no_log(self, logs):
        main._shutdown_event.set()
        main._ws_encerrando("WebSocket @depth")
        assert "@depth" in logs["info"][0]


# ── Fim do loop de retry: shutdown != tentativas esgotadas ─────


class TestFimDoLoopNaoEFalsoAlarme:
    """O loop só sai por SHUTDOWN.

    Quando estes testes foram escritos, a condição era
    `while attempt < max_retries and not _shutdown_event.is_set()` e o ponto era
    que o CRITICAL de "Máximo de tentativas" não podia disparar na saída por
    shutdown. Em 2026-07-31 descobriu-se que a OUTRA saída era o defeito:
    `max_retries` valia para a vida inteira do processo e os dois WebSockets
    morreram em definitivo. Hoje o esgotamento não existe mais — o que se
    limita é a frequência de tentativa.
    """

    def _rodar(self, handler):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler())
        finally:
            loop.close()

    def test_shutdown_nao_loga_maximo_de_tentativas(self, logs, monkeypatch):
        main._shutdown_event.set()  # loop nem entra
        self._rodar(main.websocket_handler)
        assert logs["critical"] == [], f"falso alarme: {logs['critical']}"
        assert any("shutdown" in m.lower() for m in logs["info"])

    def test_shutdown_nao_loga_maximo_de_tentativas_depth(self, logs):
        main._shutdown_event.set()
        self._rodar(main.websocket_handler_depth)
        assert logs["critical"] == []

    def test_falha_de_rede_repetida_loga_warning_e_NAO_desiste(self, logs, monkeypatch):
        """Contrato invertido de propósito: antes este teste exigia
        `critical("Máximo de tentativas")` após 10 falhas. Esse era o defeito."""
        n = {"v": 0}

        # `def`, não `async def`: websockets.connect() é usado como
        # `async with connect(...)`, ou seja devolve um context manager, não uma
        # coroutine. Um mock `async def` faria o `async with` estourar
        # AttributeError('__aenter__') -- uma Exception qualquer, que cai no ramo
        # GENÉRICO e não no de rede. O teste passaria medindo a coisa errada.
        def conectar_falhando(*a, **k):
            n["v"] += 1
            if n["v"] >= 25:  # bem acima do antigo teto de 10
                main._shutdown_event.set()  # única saída válida hoje
            raise main.websockets.exceptions.WebSocketException("conexao recusada")

        monkeypatch.setattr(main.websockets, "connect", conectar_falhando)
        monkeypatch.setattr(main.asyncio, "sleep", _sem_espera)  # sem backoff real
        monkeypatch.setattr(main.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)
        self._rodar(main.websocket_handler)

        assert n["v"] >= 25, "desistiu antes do shutdown"
        # 24 e não 25: na tentativa que dispara o shutdown, _ws_encerrando()
        # retorna ANTES de logar — falha coincidente com parada pedida é
        # classificada como shutdown, não como incidente. É o comportamento
        # correto, e a razão de o ruído de stderr ter sumido.
        assert len(logs["warning"]) >= n["v"] - 1  # uma por tentativa, sem teto
        assert logs["error"] == []  # falha de rede != erro crítico


# ── Erro crítico: só quando é crítico de verdade ───────────────


class TestErroCriticoSoQuandoEhCritico:
    """O par que importa: a MESMA exceção logada ou não, conforme houve pedido
    de parada. Se o discriminador falhar num sentido volta o ruído; no outro,
    engole falha real."""

    def _rodar(self, handler, conectar, monkeypatch):
        monkeypatch.setattr(main.websockets, "connect", conectar)
        monkeypatch.setattr(main.asyncio, "sleep", _sem_espera)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler())
        finally:
            loop.close()

    def test_loop_fechado_em_shutdown_nao_e_erro_critico(self, logs, monkeypatch):
        """O caso exato do ruído: RuntimeError('Event loop is closed') levantado
        porque PEDIMOS a parada."""

        def conectar(*a, **k):
            main._shutdown_event.set()  # shutdown chega junto, como no real
            raise RuntimeError("Event loop is closed")

        self._rodar(main.websocket_handler, conectar, monkeypatch)

        assert logs["error"] == [], f"logou erro num shutdown: {logs['error']}"
        assert logs["critical"] == []
        assert any("shutdown" in m.lower() for m in logs["info"])

    def test_erro_inesperado_fora_de_shutdown_ainda_loga(self, logs, monkeypatch):
        """Sem shutdown, RuntimeError IDÊNTICO é incidente e tem que logar.

        Este é o ramo em que caiu o ConnectionResetError de 2026-07-31 — e onde
        o sleep fixo de 1s queimava as 10 tentativas em ~8 segundos. Hoje o
        ramo usa o mesmo backoff do de rede e não desiste.
        """
        n = {"v": 0}
        monkeypatch.setattr(main.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(main.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: None)

        def conectar(*a, **k):
            n["v"] += 1
            if n["v"] >= 20:
                main._shutdown_event.set()
            raise RuntimeError("Event loop is closed")

        self._rodar(main.websocket_handler, conectar, monkeypatch)

        assert n["v"] >= 20, "desistiu antes do shutdown"
        # n-1: a tentativa que dispara o shutdown sai por _ws_encerrando sem logar.
        assert len(logs["error"]) >= n["v"] - 1, "erro real tem que logar por tentativa"
        assert all("rítico" in m for m in logs["error"])

    def test_desconexao_em_shutdown_nao_vira_warning(self, logs, monkeypatch):
        """Fechar a conexão no stop levanta ConnectionClosed -- é parada pedida,
        não desconexão a reportar."""

        def conectar(*a, **k):
            main._shutdown_event.set()
            raise main.websockets.exceptions.WebSocketException("closed")

        self._rodar(main.websocket_handler, conectar, monkeypatch)

        assert logs["warning"] == [], f"logou desconexao num shutdown: {logs['warning']}"
        assert logs["critical"] == []

    def test_depth_tem_o_mesmo_comportamento(self, logs, monkeypatch):
        """Os dois handlers são espelhos; o @depth não pode ficar de fora."""

        def conectar(*a, **k):
            main._shutdown_event.set()
            raise RuntimeError("Event loop is closed")

        self._rodar(main.websocket_handler_depth, conectar, monkeypatch)

        assert logs["error"] == [] and logs["critical"] == []
        assert any("@depth" in m for m in logs["info"])


# ── Drenagem: sem "Task was destroyed but it is pending!" ──────


class TestDrenarTasksPendentes:
    def test_cancela_e_drena_task_suspensa(self):
        """Uma task parada num await longo tem que terminar CANCELADA, não
        virar lixo do GC (que é o que produz o aviso do interpretador)."""
        loop = asyncio.new_event_loop()

        async def dorme_muito():
            await asyncio.sleep(3600)

        t = loop.create_task(dorme_muito())
        loop.call_later(0.05, loop.stop)
        try:
            loop.run_forever()
        except RuntimeError:
            pass

        assert not t.done()  # suspensa: exatamente o cenário do ruído
        main._drenar_tasks_pendentes(loop)
        assert t.done() and t.cancelled()
        loop.close()

    def test_no_op_sem_tasks_pendentes(self):
        loop = asyncio.new_event_loop()
        main._drenar_tasks_pendentes(loop)  # não levanta
        loop.close()

    def test_loop_ja_fechado_nao_levanta(self):
        """Chamado no `finally`, pode receber um loop em qualquer estado --
        shutdown não pode falhar por causa da limpeza."""
        loop = asyncio.new_event_loop()
        loop.close()
        main._drenar_tasks_pendentes(loop)  # engole e segue

    def test_task_que_ignora_cancel_nao_trava_o_shutdown(self):
        """Timeout curto: uma task que resiste ao cancel não pode prender o
        processo para sempre."""
        loop = asyncio.new_event_loop()

        async def teimosa():
            while True:
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass  # ignora o cancelamento de propósito

        t = loop.create_task(teimosa())
        loop.call_later(0.05, loop.stop)
        try:
            loop.run_forever()
        except RuntimeError:
            pass

        main._drenar_tasks_pendentes(loop)  # não pode pendurar (timeout=3)
        t.cancel()
        loop.close()

    def test_chamado_antes_do_close_nos_dois_loops(self):
        """Trava a ordem: drenar ANTES de fechar. Invertido, o close deixaria as
        tasks para o GC e o ruído voltaria."""
        import inspect

        for fn in (main.iniciar_websocket_async, main.iniciar_websocket_depth_async):
            src = inspect.getsource(fn)
            assert "_drenar_tasks_pendentes" in src, f"{fn.__name__} nao drena"
            assert src.index("_drenar_tasks_pendentes") < src.index("loop.close()")
