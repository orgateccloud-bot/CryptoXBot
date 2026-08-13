"""
Testes das rotas REST do dashboard.py via Flask test client.
Sem WebSocket real, sem banco real — database é mockado.
"""

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


# ── Testes: _checar_exposicao_rede (auditoria 2026-07-17) ─────────────────────


class TestChecarExposicaoRede:
    def test_bind_local_nao_faz_nada(self, capsys):
        dashboard._checar_exposicao_rede("127.0.0.1", "", "production")
        assert capsys.readouterr().out == ""

    def test_bind_exposto_com_token_nao_faz_nada(self, capsys):
        dashboard._checar_exposicao_rede("0.0.0.0", "algum-token", "production")
        assert capsys.readouterr().out == ""

    def test_bind_exposto_sem_token_em_producao_aborta(self, capsys):
        with pytest.raises(SystemExit) as exc:
            dashboard._checar_exposicao_rede("0.0.0.0", "", "production")
        assert exc.value.code == 1
        assert "Abortando" in capsys.readouterr().out

    def test_bind_exposto_sem_token_fora_de_producao_so_avisa(self, capsys):
        dashboard._checar_exposicao_rede("0.0.0.0", "", "development")
        assert "AVISO" in capsys.readouterr().out


# ── Testes: /api/bot_events (I-9) ─────────────────────────────────────────────


class TestApiBotEvents:
    """A tabela bot_events tinha 14 escritores no worker e ZERO leitor em todo o
    repositorio. /api/eventos, que parecia ser esse leitor, le um deque em
    memoria do PROPRIO processo do dashboard — nunca viu um incidente do bot.

    A rota importa `database` em tempo de chamada, portanto resolve o modulo
    REAL (o mock de sys.modules deste arquivo vale so no import). Por isso o
    monkeypatch abaixo e no modulo real.
    """

    _EVENTOS = [
        {
            "id": 9,
            "timestamp": "2026-08-07T12:00:00",
            "service": "worker",
            "symbol": "BTCUSDT",
            "event_type": "thread_crash",
            "severity": "CRITICAL",
            "message": "loop morreu",
        }
    ]

    def test_devolve_eventos_da_tabela(self, client, monkeypatch):
        import database as db_real

        monkeypatch.setattr(db_real, "listar_bot_events", lambda **k: self._EVENTOS)
        r = client.get("/api/bot_events")
        assert r.status_code == 200
        data = r.get_json()
        assert data["total"] == 1
        assert data["eventos"][0]["event_type"] == "thread_crash"

    def test_repassa_filtros_da_query_string(self, client, monkeypatch):
        import database as db_real

        capturado = {}

        def _fake(**k):
            capturado.update(k)
            return []

        monkeypatch.setattr(db_real, "listar_bot_events", _fake)
        client.get("/api/bot_events?severidade=CRITICAL&tipo=bot_travado&limite=7")
        assert capturado == {"limite": 7, "severidade": "CRITICAL", "tipo": "bot_travado"}

    def test_limite_e_saturado_nos_extremos(self, client, monkeypatch):
        import database as db_real

        vistos = []
        monkeypatch.setattr(
            db_real, "listar_bot_events", lambda **k: vistos.append(k["limite"]) or []
        )
        client.get("/api/bot_events?limite=99999")
        client.get("/api/bot_events?limite=0")
        client.get("/api/bot_events?limite=abc")
        assert vistos == [500, 1, 100]

    def test_db_indisponivel_devolve_503_e_nao_500(self, client, monkeypatch):
        import database as db_real

        def _fora(**k):
            raise RuntimeError("no such table: bot_events")

        monkeypatch.setattr(db_real, "listar_bot_events", _fora)
        r = client.get("/api/bot_events")
        assert r.status_code == 503
        assert "no such table" in r.get_json()["erro"]

    def test_api_eventos_antiga_continua_intacta(self, client):
        """A rota legada nao pode mudar de contrato: e o que o front consome."""
        r = client.get("/api/eventos")
        assert r.status_code == 200
        assert isinstance(r.get_json(), list)


# ── /api/lucro e /api/atividade (tema Nebula, 2026-08-13) ─────────


def _db_lucro(tmp_path, com_dados=True, com_log=True):
    """SQLite real em arquivo: cada request abre/fecha a propria conexao."""
    import json as _json
    import sqlite3

    caminho = str(tmp_path / "dash.db")
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE risk_state (name TEXT PRIMARY KEY, updated_at TEXT, data TEXT)")
    con.execute(
        "CREATE TABLE sinais (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,"
        " symbol TEXT, source TEXT, pnl_usdt REAL, barreira_tocada TEXT)"
    )
    if com_log:
        con.execute(
            "CREATE TABLE log_avaliacoes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " timestamp TEXT, symbol TEXT, preco REAL, score INTEGER, decisao TEXT,"
            " regime TEXT, ml_ensemble REAL, bloqueios TEXT)"
        )
    if com_dados:
        con.execute(
            "INSERT INTO risk_state VALUES ('default','x',?)",
            (_json.dumps({"pnl_dia": 4.2}),),
        )
        linhas = [
            ("2026-08-10T10:00:00", "BTCUSDT", "estrategia_otimizada", 10.0, "TARGET"),
            ("2026-08-11T10:00:00", "BTCUSDT", None, -4.0, "STOP"),
            ("2026-08-12T10:00:00", "BTCUSDT", "trend", 999.0, "TARGET"),  # excluida
        ]
        con.executemany(
            "INSERT INTO sinais (timestamp, symbol, source, pnl_usdt, barreira_tocada)"
            " VALUES (?,?,?,?,?)",
            linhas,
        )
        if com_log:
            con.execute(
                "INSERT INTO log_avaliacoes (timestamp, symbol, preco, score, decisao,"
                " regime, ml_ensemble, bloqueios) VALUES"
                " ('2026-08-13 20:19:44','BTCUSDT',63472.0,39,'AGUARDAR',"
                "'TENDENCIA_BAIXA',0.165,'')"
            )
    con.commit()
    con.close()

    def conectar():
        c = sqlite3.connect(caminho)
        c.row_factory = sqlite3.Row
        return c

    return conectar


class TestApiLucro:
    def test_agrega_so_a_estrategia_primaria(self, client, tmp_path):
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/lucro").get_json()
        assert d["modo"] == "PAPER"
        assert d["pnl_dia"] == pytest.approx(4.2)
        # 999 do trend NAO entra: mesma regua do relatorio_gate
        assert d["pnl_total"] == pytest.approx(6.0)
        assert d["trades_fechados"] == 2
        assert d["win_rate"] == pytest.approx(50.0)
        assert d["ultimo"]["pnl_usdt"] == pytest.approx(-4.0)

    def test_banco_vazio_da_zeros_nao_500(self, client, tmp_path):
        _db_mock.conectar = _db_lucro(tmp_path, com_dados=False)
        r = client.get("/api/lucro")
        assert r.status_code == 200
        d = r.get_json()
        assert d["pnl_total"] == 0.0 and d["trades_fechados"] == 0
        assert d["win_rate"] is None and d["ultimo"] is None


class TestApiAtividade:
    def test_devolve_o_pensamento_do_bot(self, client, tmp_path):
        _db_mock.conectar = _db_lucro(tmp_path)
        rows = client.get("/api/atividade").get_json()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["decisao"] == "AGUARDAR"
        assert rows[0]["score"] == 39

    def test_sem_tabela_do_logger_devolve_lista_vazia(self, client, tmp_path):
        """Banco recem-criado sem o logger: feed vazio, nunca 500."""
        _db_mock.conectar = _db_lucro(tmp_path, com_log=False)
        r = client.get("/api/atividade")
        assert r.status_code == 200
        assert r.get_json() == []
