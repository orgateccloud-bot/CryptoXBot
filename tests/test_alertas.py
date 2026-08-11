"""
Testes da frente I-9 — canal de escalonamento que efetivamente entrega.

Contexto medido antes desta frente (nao e hipotese):

  - `telegram_bot._enviar` era guardado por `if not TELEGRAM_TOKEN`, que testa
    VAZIO. O `.env` do projeto traz 'your_telegram_bot_token_here', uma string
    TRUTHY: atravessava a guarda, o POST ia para um token invalido, e a funcao
    devolvia False sem imprimir nada. 10 call sites, 8 tipos de alerta, zero
    entregas em 4 meses, em silencio absoluto.
  - `bot_events` tinha 14 escritores (thread_crash CRITICAL, divergencia
    local-vs-exchange, fill sem persistencia) e ZERO `SELECT` no repositorio.
  - `/health` retornava 200 incondicional: provava apenas que a thread do
    http.server estava viva, mesmo com as threads de avaliacao mortas.
  - `health.increment_metric("ordens_total")` vivia DEPOIS do `return` de
    simulacao — em paper trading o contador ficava eternamente em zero.

Cada teste abaixo falha se qualquer um desses quatro comportamentos voltar.
Nenhum toca a rede: `requests.post` e sempre substituido.
"""

import sqlite3
import threading
import time

import pytest

import database
import health
import telegram_bot

# ══════════════════════════════════════════════════════════════════
#  1. Guarda de placeholder — o bug central de I-9
# ══════════════════════════════════════════════════════════════════


class TestTokenConfigurado:
    @pytest.mark.parametrize(
        "token",
        [
            "your_telegram_bot_token_here",  # exatamente o que ha no .env
            "YOUR_TELEGRAM_BOT_TOKEN_HERE",  # deteccao case-insensitive
            "xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "coloque_seu_token_aqui_por_favor",
            "changeme_changeme_changeme_xx",
            "placeholder_placeholder_token",
        ],
    )
    def test_placeholder_e_recusado(self, token, monkeypatch):
        """Todo placeholder conhecido e recusado, mesmo sendo string truthy."""
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", token)
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "123456")
        assert telegram_bot.token_configurado() is False

    def test_placeholder_e_truthy_prova_do_bug_antigo(self):
        """A guarda antiga (`if not TOKEN`) NAO barrava o placeholder do .env.

        Este teste existe para tornar a regressao impossivel de passar
        desapercebida: se alguem trocar token_configurado() de volta por um
        teste de vazio, o teste acima quebra e este explica por que.
        """
        assert bool("your_telegram_bot_token_here") is True

    def test_token_vazio_e_recusado(self, monkeypatch):
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "")
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "123456")
        assert telegram_bot.token_configurado() is False

    def test_chat_id_vazio_e_recusado(self, monkeypatch):
        """Token real sem chat_id nao entrega nada — tambem e 'nao configurado'."""
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "8112233445:AAF" + "z" * 32)
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "")
        assert telegram_bot.token_configurado() is False

    def test_token_curto_e_recusado(self, monkeypatch):
        """Token real da Telegram tem ~46 chars; <20 nao pode ser real."""
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "123:abc")
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "123456")
        assert telegram_bot.token_configurado() is False

    def test_token_com_formato_real_e_aceito(self, monkeypatch):
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "8112233445:AAFz" + "Q7x" * 12)
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "-1001234567890")
        assert telegram_bot.token_configurado() is True

    def test_enviar_com_placeholder_nao_toca_a_rede(self, monkeypatch):
        """Nao basta devolver False: nao pode nem tentar o POST."""
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "your_telegram_bot_token_here")
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "123456")
        monkeypatch.setattr(telegram_bot, "_registrar_falha_de_entrega", lambda d: None)

        def _proibido(*a, **k):  # pragma: no cover - deve ser inalcancavel
            raise AssertionError("POST enviado com token placeholder")

        monkeypatch.setattr(telegram_bot.requests, "post", _proibido)
        assert telegram_bot._enviar("oi") is False

    def test_enviar_com_placeholder_diz_o_motivo(self, monkeypatch, capsys):
        """O silencio era o problema — a falha tem de aparecer no stdout."""
        monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "your_telegram_bot_token_here")
        monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "123456")
        monkeypatch.setattr(telegram_bot, "_registrar_falha_de_entrega", lambda d: None)
        telegram_bot._enviar("oi")
        saida = capsys.readouterr().out
        assert "NAO ENVIADO" in saida
        assert "placeholder" in saida.lower()


# ══════════════════════════════════════════════════════════════════
#  2. Falha de entrega deixa de ser silenciosa
# ══════════════════════════════════════════════════════════════════


class _RespostaFake:
    def __init__(self, status_code, body="", payload=None):
        self.status_code = status_code
        self.text = body
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload


@pytest.fixture
def token_valido(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_TOKEN", "8112233445:AAFz" + "Q7x" * 12)
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "-1001234567890")


@pytest.fixture(autouse=True)
def _estado_envio_limpo():
    """Zera o debounce entre testes — e estado de modulo."""
    telegram_bot._estado_envio["ultima_falha_alertada"] = 0.0
    telegram_bot._estado_envio["em_falha"] = False
    yield
    telegram_bot._estado_envio["ultima_falha_alertada"] = 0.0
    telegram_bot._estado_envio["em_falha"] = False


class TestFalhaDeEntregaNaoESilenciosa:
    def test_http_401_registra_evento_critico(self, token_valido, monkeypatch):
        eventos = []
        monkeypatch.setattr(
            telegram_bot.requests,
            "post",
            lambda *a, **k: _RespostaFake(401, '{"description":"Unauthorized"}'),
        )
        monkeypatch.setattr(
            database,
            "salvar_bot_event",
            lambda t, m, **k: eventos.append((t, m, k.get("severity"))),
        )
        assert telegram_bot._enviar("oi") is False
        assert len(eventos) == 1
        assert eventos[0][0] == "alerta_nao_entregue"
        assert eventos[0][2] == "CRITICAL"

    def test_corpo_da_resposta_e_preservado(self, token_valido, monkeypatch, capsys):
        """A Telegram diz exatamente o que esta errado; era descartado."""
        monkeypatch.setattr(
            telegram_bot.requests,
            "post",
            lambda *a, **k: _RespostaFake(400, '{"description":"chat not found"}'),
        )
        monkeypatch.setattr(database, "salvar_bot_event", lambda *a, **k: None)
        ok, detalhe = telegram_bot._enviar("oi", devolver_detalhe=True)
        assert ok is False
        assert "400" in detalhe
        assert "chat not found" in detalhe
        assert "chat not found" in capsys.readouterr().out

    def test_erro_de_rede_e_capturado_com_tipo(self, token_valido, monkeypatch):
        def _explode(*a, **k):
            raise telegram_bot.requests.exceptions.ConnectTimeout("timeout")

        monkeypatch.setattr(telegram_bot.requests, "post", _explode)
        monkeypatch.setattr(database, "salvar_bot_event", lambda *a, **k: None)
        ok, detalhe = telegram_bot._enviar("oi", devolver_detalhe=True)
        assert ok is False
        assert "ConnectTimeout" in detalhe

    def test_debounce_evita_flood_de_eventos(self, token_valido, monkeypatch):
        """Os call sites disparam por ciclo; um evento por falha inundaria a tabela."""
        eventos = []
        monkeypatch.setattr(
            telegram_bot.requests, "post", lambda *a, **k: _RespostaFake(401, "nope")
        )
        monkeypatch.setattr(database, "salvar_bot_event", lambda t, m, **k: eventos.append(t))
        for _ in range(25):
            telegram_bot._enviar("oi")
        assert len(eventos) == 1, "debounce nao segurou o flood"

    def test_debounce_rearma_apos_recuperacao(self, token_valido, monkeypatch):
        """Falha → sucesso → falha tem de gerar DOIS eventos, nao um."""
        eventos = []
        estado = {"status": 401}
        monkeypatch.setattr(
            telegram_bot.requests,
            "post",
            lambda *a, **k: _RespostaFake(
                estado["status"],
                "x",
                {"result": {"message_id": 7}} if estado["status"] == 200 else None,
            ),
        )
        monkeypatch.setattr(database, "salvar_bot_event", lambda t, m, **k: eventos.append(t))

        telegram_bot._enviar("falha 1")
        assert len(eventos) == 1

        estado["status"] = 200
        assert telegram_bot._enviar("sucesso") is True
        assert telegram_bot._estado_envio["em_falha"] is False

        estado["status"] = 401
        telegram_bot._enviar("falha 2")
        assert len(eventos) == 2, "nao rearmou depois da recuperacao"

    def test_sucesso_devolve_message_id(self, token_valido, monkeypatch):
        """Criterio de saida do modo --teste: prova de entrega, nao 'True'."""
        monkeypatch.setattr(
            telegram_bot.requests,
            "post",
            lambda *a, **k: _RespostaFake(200, "", {"ok": True, "result": {"message_id": 4242}}),
        )
        ok, detalhe = telegram_bot._enviar("oi", devolver_detalhe=True)
        assert ok is True
        assert "4242" in detalhe

    def test_registrar_falha_nunca_levanta(self, monkeypatch):
        """Se o proprio DB estiver fora, o alerta nao pode derrubar o caller."""

        def _db_fora(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(database, "salvar_bot_event", _db_fora)
        telegram_bot._registrar_falha_de_entrega("qualquer")  # nao deve levantar

    def test_compatibilidade_dos_10_call_sites(self, token_valido, monkeypatch):
        """Sem devolver_detalhe, o retorno continua bool — as 8 funcoes de alerta
        publicas fazem `return _enviar(msg)` e nao esperam tupla."""
        monkeypatch.setattr(
            telegram_bot.requests,
            "post",
            lambda *a, **k: _RespostaFake(200, "", {"result": {"message_id": 1}}),
        )
        assert telegram_bot.alerta_stop(50000.0, -12.5) is True
        assert telegram_bot.alerta_circuit_breaker("teste") is True
        assert telegram_bot.alerta_trailing_stop(49000.0, 51000.0) is True


# ══════════════════════════════════════════════════════════════════
#  3. bot_events ganha leitor
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def db_eventos(tmp_path, monkeypatch):
    """Banco temporario com a tabela bot_events e 5 eventos conhecidos."""
    caminho = tmp_path / "eventos.db"
    conn = sqlite3.connect(caminho)
    conn.execute("""CREATE TABLE bot_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               timestamp TEXT, service TEXT, symbol TEXT,
               event_type TEXT, severity TEXT, message TEXT)""")
    linhas = [
        ("2026-08-07T10:00:00", "worker", "BTCUSDT", "thread_crash", "CRITICAL", "loop morreu"),
        ("2026-08-07T10:01:00", "worker", None, "boot", "INFO", "subiu"),
        ("2026-08-07T10:02:00", "worker", "BTCUSDT", "divergencia", "WARNING", "0.3%"),
        ("2026-08-07T10:03:00", "worker", "BTCUSDT", "bot_travado", "CRITICAL", "drawdown 12%"),
        ("2026-08-07T10:04:00", "worker", None, "alerta_nao_entregue", "CRITICAL", "HTTP 401"),
    ]
    conn.executemany(
        "INSERT INTO bot_events (timestamp, service, symbol, event_type, severity, message)"
        " VALUES (?,?,?,?,?,?)",
        linhas,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "_backend", lambda: "sqlite")
    monkeypatch.setattr(database, "conectar", lambda: sqlite3.connect(caminho))
    return caminho


class TestListarBotEvents:
    def test_le_todos_mais_recentes_primeiro(self, db_eventos):
        eventos = database.listar_bot_events()
        assert len(eventos) == 5
        assert [e["id"] for e in eventos] == [5, 4, 3, 2, 1]

    def test_filtra_por_severidade(self, db_eventos):
        criticos = database.listar_bot_events(severidade="CRITICAL")
        assert len(criticos) == 3
        assert {e["event_type"] for e in criticos} == {
            "thread_crash",
            "bot_travado",
            "alerta_nao_entregue",
        }

    def test_severidade_e_case_insensitive(self, db_eventos):
        assert len(database.listar_bot_events(severidade="critical")) == 3

    def test_filtra_por_tipo(self, db_eventos):
        assert len(database.listar_bot_events(tipo="thread_crash")) == 1

    def test_desde_id_permite_consumo_incremental(self, db_eventos):
        """Sem isso o vigia re-alertaria o historico inteiro a cada varredura."""
        novos = database.listar_bot_events(desde_id=3)
        assert [e["id"] for e in novos] == [5, 4]
        assert database.listar_bot_events(desde_id=5) == []

    def test_limite_e_respeitado(self, db_eventos):
        assert len(database.listar_bot_events(limite=2)) == 2

    def test_filtros_combinados(self, db_eventos):
        r = database.listar_bot_events(severidade="CRITICAL", desde_id=1)
        assert [e["id"] for e in r] == [5, 4]

    def test_injecao_por_severidade_nao_altera_a_query(self, db_eventos):
        """A f-string do SQL monta so nomes de coluna fixos; valores sao ligados."""
        r = database.listar_bot_events(severidade="CRITICAL' OR '1'='1")
        assert r == []

    def test_injecao_por_tipo_nao_altera_a_query(self, db_eventos):
        assert database.listar_bot_events(tipo="x'; DROP TABLE bot_events; --") == []
        assert len(database.listar_bot_events()) == 5, "tabela sobreviveu?"


class TestEscalarEventosCriticos:
    def test_escala_criticos_e_devolve_maior_id(self, db_eventos, token_valido, monkeypatch):
        enviados = []
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: enviados.append(m) or True)
        n, maior = telegram_bot.escalar_eventos_criticos()
        assert maior == 5
        # 3 CRITICAL, menos o alerta_nao_entregue (id 5) que e deliberadamente pulado
        assert n == 2
        assert any("thread_crash" in m for m in enviados)
        assert any("bot_travado" in m for m in enviados)

    def test_nao_escala_a_propria_falha_de_entrega(self, db_eventos, monkeypatch):
        """Avisar por Telegram que o Telegram falhou e loop inutil."""
        enviados = []
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: enviados.append(m) or True)
        telegram_bot.escalar_eventos_criticos()
        assert not any("alerta_nao_entregue" in m for m in enviados)

    def test_ignora_nao_criticos(self, db_eventos, monkeypatch):
        enviados = []
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: enviados.append(m) or True)
        telegram_bot.escalar_eventos_criticos()
        assert not any("divergencia" in m or "subiu" in m for m in enviados)

    def test_consumo_incremental_nao_repete(self, db_eventos, monkeypatch):
        enviados = []
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: enviados.append(m) or True)
        n1, ult = telegram_bot.escalar_eventos_criticos()
        assert n1 == 2
        n2, _ = telegram_bot.escalar_eventos_criticos(desde_id=ult)
        assert n2 == 0, "re-escalou o que ja tinha escalado"

    def test_escala_em_ordem_cronologica(self, db_eventos, monkeypatch):
        """listar_bot_events devolve DESC (mais novo primeiro); o escalonamento
        reordena, senao o operador le os incidentes de tras para frente."""
        enviados = []
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: enviados.append(m) or True)
        telegram_bot.escalar_eventos_criticos()
        assert "thread_crash" in enviados[0]
        assert "bot_travado" in enviados[1]

    def test_lote_cheio_nao_deixa_lacuna(self, monkeypatch):
        """Defeito real corrigido antes do commit de I-9.

        A leitura era DESC + LIMIT 20 e o cursor avancava para o MAIOR id do
        lote. Com 25 CRITICAL entre varreduras, os 20 mais NOVOS eram escalados,
        o cursor saltava para o topo, e os 5 mais antigos ficavam abaixo dele —
        perdidos para sempre, em silencio, na tabela de incidentes.

        Este teste simula a tabela de verdade (id 1..25) e exige que duas
        varreduras cubram TODOS os 25, sem lacuna e sem repeticao.
        """
        tabela = [
            {
                "id": i,
                "timestamp": f"2026-08-07T10:{i:02d}:00",
                "service": "worker",
                "symbol": None,
                "event_type": "thread_crash",
                "severity": "CRITICAL",
                "message": f"evento {i}",
            }
            for i in range(1, 26)
        ]

        def _listar(*, limite=100, severidade=None, tipo=None, desde_id=None, crescente=False):
            r = [e for e in tabela if desde_id is None or e["id"] > desde_id]
            r.sort(key=lambda e: e["id"], reverse=not crescente)
            return r[:limite]

        monkeypatch.setattr(database, "listar_bot_events", _listar)
        vistos = []
        monkeypatch.setattr(
            telegram_bot,
            "_enviar",
            lambda m: vistos.append(int(m.rsplit("evento ", 1)[1])) or True,
        )

        n1, cursor = telegram_bot.escalar_eventos_criticos()
        n2, cursor2 = telegram_bot.escalar_eventos_criticos(desde_id=cursor)
        n3, _ = telegram_bot.escalar_eventos_criticos(desde_id=cursor2)

        assert sorted(vistos) == list(range(1, 26)), f"lacuna ou repeticao: {sorted(vistos)}"
        assert len(vistos) == len(set(vistos)), "evento escalado duas vezes"
        assert (n1, n2, n3) == (20, 5, 0)

    def test_lote_cheio_avisa_que_ha_fila(self, monkeypatch, capsys):
        """Truncar em silencio faz 'cobri tudo' parecer verdade quando nao e."""
        tabela = [
            {
                "id": i,
                "timestamp": "2026-08-07T10:00:00",
                "service": "worker",
                "symbol": None,
                "event_type": "thread_crash",
                "severity": "CRITICAL",
                "message": f"evento {i}",
            }
            for i in range(1, 40)
        ]
        monkeypatch.setattr(
            database,
            "listar_bot_events",
            lambda *, limite=100, crescente=False, **k: sorted(
                tabela, key=lambda e: e["id"], reverse=not crescente
            )[:limite],
        )
        monkeypatch.setattr(telegram_bot, "_enviar", lambda m: True)
        telegram_bot.escalar_eventos_criticos()
        assert "Lote cheio" in capsys.readouterr().out

    def test_db_indisponivel_nao_levanta(self, monkeypatch):
        def _fora(**k):
            raise sqlite3.OperationalError("no such table")

        monkeypatch.setattr(database, "listar_bot_events", _fora)
        n, ult = telegram_bot.escalar_eventos_criticos(desde_id=10)
        assert (n, ult) == (0, 10), "perdeu a posicao do cursor ao falhar"


# ══════════════════════════════════════════════════════════════════
#  4. /health deixa de ser 200 incondicional
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _threads_esperadas_limpas():
    original = set(health._threads_esperadas)
    health._threads_esperadas.clear()
    yield
    health._threads_esperadas.clear()
    health._threads_esperadas.update(original)


class TestLivenessDeThreads:
    def test_sem_threads_declaradas_nao_acusa_nada(self):
        assert health._threads_mortas() == []

    def test_thread_viva_nao_e_acusada(self):
        parar = threading.Event()
        t = threading.Thread(target=parar.wait, name="loop-BTCUSDT", daemon=True)
        t.start()
        health.registrar_thread_essencial("loop-BTCUSDT")
        try:
            assert health._threads_mortas() == []
        finally:
            parar.set()
            t.join(timeout=5)

    def test_thread_morta_e_detectada(self):
        """A morte de uma loop_par era completamente silenciosa: processo vivo,
        health 200, e o par simplesmente parava de ser avaliado."""
        t = threading.Thread(target=lambda: None, name="loop-ETHUSDT", daemon=True)
        t.start()
        t.join(timeout=5)
        health.registrar_thread_essencial("loop-ETHUSDT")
        assert health._threads_mortas() == ["loop-ETHUSDT"]

    def test_thread_que_nunca_subiu_e_detectada(self):
        health.registrar_thread_essencial("loop-INEXISTENTE")
        assert health._threads_mortas() == ["loop-INEXISTENTE"]

    def test_varias_mortas_saem_ordenadas(self):
        for nome in ("loop-C", "loop-A", "loop-B"):
            health.registrar_thread_essencial(nome)
        assert health._threads_mortas() == ["loop-A", "loop-B", "loop-C"]

    def test_registro_e_idempotente(self):
        health.registrar_thread_essencial("loop-X")
        health.registrar_thread_essencial("loop-X")
        assert health._threads_mortas() == ["loop-X"]


class TestHealthEndpointRefleteLiveness:
    """Exercita HealthHandler.do_GET sem abrir socket: o handler e instanciado
    sem __init__ e os metodos de resposta sao capturados."""

    def _chamar(self, path, role="worker"):
        h = health.HealthHandler.__new__(health.HealthHandler)
        h.path = path
        h.role = role
        capturado = {"status": None, "body": b""}

        class _Wfile:
            @staticmethod
            def write(b):
                capturado["body"] += b

        h.send_response = lambda c: capturado.__setitem__("status", c)
        h.send_header = lambda *a: None
        h.end_headers = lambda: None
        h.wfile = _Wfile()
        h.do_GET()
        return capturado["status"], capturado["body"].decode()

    def test_health_200_quando_tudo_vivo(self):
        status, corpo = self._chamar("/health")
        assert status == 200
        assert '"ready":true' in corpo.replace(" ", "")

    def test_health_503_com_thread_morta(self):
        """Era 200 INCONDICIONAL: toda a verificacao vivia sob
        `if self.path == "/ready"`, entao /health provava apenas que a thread do
        http.server estava viva."""
        health.registrar_thread_essencial("loop-MORTA")
        status, corpo = self._chamar("/health")
        assert status == 503
        assert "threads_mortas" in corpo
        assert "loop-MORTA" in corpo

    def test_corpo_do_503_nao_diz_ready_true(self):
        """Regressao de 2026-07-31: HTTP 503 com {"ready": true} — um probe que
        le o JSON concluia o oposto do codigo HTTP."""
        health.registrar_thread_essencial("loop-MORTA")
        _, corpo = self._chamar("/health")
        assert '"ready":false' in corpo.replace(" ", "")

    def test_health_do_dashboard_ignora_threads_do_worker(self):
        """O role 'dashboard' nao tem loop_par; a checagem e so do worker."""
        health.registrar_thread_essencial("loop-MORTA")
        status, _ = self._chamar("/health", role="dashboard")
        assert status == 200


# ══════════════════════════════════════════════════════════════════
#  5. Bind do health server e o contador de ordens
# ══════════════════════════════════════════════════════════════════


class TestBindDoHealthServer:
    def test_default_e_local(self):
        """Era ("0.0.0.0", porta) hardcoded, sem auth e sem variavel para mudar,
        publicando pnl_dia/drawdown_dia_pct para qualquer dispositivo da LAN."""
        from config.runtime_settings import HEALTH_BIND

        assert HEALTH_BIND == "127.0.0.1"

    def test_bind_usa_a_constante_nao_um_literal(self):
        """Guarda contra reintroducao do 0.0.0.0 hardcoded no bind.

        Compara so as linhas de codigo: o comentario que documenta o bug antigo
        cita literalmente ("0.0.0.0", porta) e nao deve derrubar o teste.
        """
        import inspect

        codigo = [
            linha.split("#")[0]
            for linha in inspect.getsource(health.start_health_server).splitlines()
            if not linha.strip().startswith("#")
        ]
        fonte = "\n".join(codigo)
        assert "ThreadingHTTPServer((HEALTH_BIND" in fonte
        assert '"0.0.0.0"' not in fonte.split("if HEALTH_BIND")[0]


class TestOrdensTotalEmSimulacao:
    def test_contador_incrementa_em_paper_trading(self, monkeypatch):
        """O increment_metric vivia DEPOIS do `return` de simulacao: em paper
        trading o contador ficava eternamente em zero — e paper e o unico modo
        que roda. Um contador que nunca sai de zero nao e observabilidade, e
        decoracao.

        Teste de COMPORTAMENTO (nao de posicao no fonte): manda uma ordem
        simulada de verdade e le o contador.
        """
        from executor import Executor

        ex = Executor(simulacao=True, symbol="BTCUSDT")
        monkeypatch.setattr(ex, "get_preco", lambda: 50000.0)

        with health._metrics_lock:
            antes = health._metrics["ordens_total"]

        resp = ex._enviar_ordem("BUY", 0.001, preco=50000.0, tipo="LIMIT")
        assert "erro" not in resp

        with health._metrics_lock:
            depois = health._metrics["ordens_total"]
        assert depois == antes + 1, "ordem simulada nao contou em ordens_total"

    def test_ordem_rejeitada_por_qty_minima_nao_conta(self, monkeypatch):
        """A guarda de qty minima devolve antes de qualquer ordem existir —
        contar ali inflaria a metrica com nao-eventos."""
        from executor import Executor

        ex = Executor(simulacao=True, symbol="BTCUSDT")
        with health._metrics_lock:
            antes = health._metrics["ordens_total"]

        resp = ex._enviar_ordem("BUY", 0.0, preco=50000.0, tipo="LIMIT")
        assert "erro" in resp

        with health._metrics_lock:
            assert health._metrics["ordens_total"] == antes


# ══════════════════════════════════════════════════════════════════
#  6. O vigia — quem transforma bot_events em mensagem
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def vigia(monkeypatch):
    """Sobe o vigia real com intervalo curto e o REAPA no fim do teste.

    O reaping e obrigatorio, nao higiene: `iniciar_vigia_de_eventos` nao devolve
    a thread e o loop testa `_shutdown_event`, que e global. Sem join, a thread
    do teste anterior sobrevivia ao `clear()` do teste seguinte e escrevia na
    lista de asserts dele — foi exatamente o falso-negativo que este fixture
    elimina (o teste do cursor em zero recebeu o 137 do teste anterior).
    """
    import main

    monkeypatch.setattr(main, "_VIGIA_INTERVALO_S", 0.05)
    criadas: list[threading.Thread] = []

    def _subir(eventos_iniciais, escalar_fake):
        monkeypatch.setattr(main.database, "listar_bot_events", lambda **k: eventos_iniciais)
        monkeypatch.setattr(main.telegram_bot, "escalar_eventos_criticos", escalar_fake)
        antes = {t for t in threading.enumerate() if t.name == "vigia-eventos"}
        main._shutdown_event.clear()
        main.iniciar_vigia_de_eventos()
        criadas.extend(
            t for t in threading.enumerate() if t.name == "vigia-eventos" and t not in antes
        )
        return main

    def _esperar(condicao, limite=5.0):
        fim = time.time() + limite
        while not condicao() and time.time() < fim:
            time.sleep(0.01)

    _subir.esperar = _esperar
    try:
        yield _subir
    finally:
        main._shutdown_event.set()
        for t in criadas:
            t.join(timeout=3)
        assert not [t for t in criadas if t.is_alive()], "vigia nao respeitou o shutdown"
        main._shutdown_event.clear()


class TestVigiaDeEventos:
    """O leitor sozinho nao resolve nada: alguem tem de varrer periodicamente.
    Estes testes usam a thread real, com intervalo curto e shutdown controlado.
    """

    def test_primeira_volta_nao_escala_o_historico(self, vigia):
        """Sem semear o cursor, o primeiro boot dispararia uma mensagem por
        evento CRITICAL antigo — e ha 4 meses de historico."""
        chamadas = []

        def _escalar(desde_id=None):
            chamadas.append(desde_id)
            return 0, desde_id

        vigia([{"id": 137}], _escalar)
        vigia.esperar(lambda: chamadas, limite=3)
        assert chamadas, "vigia nunca varreu"
        assert chamadas[0] == 137, "cursor nao foi semeado com o maior id existente"

    def test_tabela_vazia_semeia_cursor_em_zero(self, vigia):
        chamadas = []

        def _escalar(desde_id=None):
            chamadas.append(desde_id)
            return 0, desde_id

        vigia([], _escalar)
        vigia.esperar(lambda: chamadas, limite=3)
        assert chamadas and chamadas[0] == 0

    def test_cursor_avanca_entre_varreduras(self, vigia):
        """Sem avancar, cada varredura re-alertaria o que ja alertou."""
        chamadas = []

        def _escalar(desde_id=None):
            chamadas.append(desde_id)
            return 1, (desde_id or 0) + 10

        vigia([{"id": 5}], _escalar)
        vigia.esperar(lambda: len(chamadas) >= 3)
        assert chamadas[:3] == [5, 15, 25]

    def test_excecao_no_escalonamento_nao_mata_o_vigia(self, vigia):
        """Um vigia que morre no primeiro erro e pior que nenhum: da a impressao
        de cobertura e deixa de existir em silencio."""
        contador = {"n": 0}

        def _escalar(desde_id=None):
            contador["n"] += 1
            if contador["n"] == 1:
                raise RuntimeError("banco fora do ar")
            return 0, desde_id

        vigia([{"id": 1}], _escalar)
        vigia.esperar(lambda: contador["n"] >= 3)
        assert contador["n"] >= 3, "vigia morreu na primeira excecao"

    def test_vigia_para_no_shutdown(self, vigia):
        """O assert de fim do fixture cobre isto para todos os testes acima; aqui
        fica explicito — uma thread que ignora o _shutdown_event trava o
        encerramento do processo."""
        vigia([{"id": 1}], lambda desde_id=None: (0, desde_id))
        vigia.esperar(lambda: True, limite=0.2)

    def test_intervalo_respeita_o_criterio_de_um_minuto(self):
        """Criterio de saida de I-9: <= 60s entre o evento e a mensagem."""
        import main

        assert main._VIGIA_INTERVALO_S <= 60
