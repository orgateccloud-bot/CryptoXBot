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


# ── /api/equity, /api/gates e /api/sistema (redesign 2026-08-14) ──


class TestApiEquity:
    def test_acumula_em_ordem_e_exclui_trend(self, client, tmp_path):
        """A curva e a MESMA regua do gate: trend (999) fora, cum trade a trade."""
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/equity").get_json()
        assert d["modo"] == "PAPER"
        assert d["n"] == 2
        assert [p["pnl"] for p in d["pontos"]] == [pytest.approx(10.0), pytest.approx(-4.0)]
        assert [p["cum"] for p in d["pontos"]] == [pytest.approx(10.0), pytest.approx(6.0)]
        assert d["total"] == pytest.approx(6.0)

    def test_banco_vazio_curva_vazia_nao_500(self, client, tmp_path):
        _db_mock.conectar = _db_lucro(tmp_path, com_dados=False)
        r = client.get("/api/equity")
        assert r.status_code == 200
        d = r.get_json()
        assert d["pontos"] == [] and d["n"] == 0 and d["total"] == 0.0

    def test_limite_corta_o_inicio_mas_preserva_o_acumulado(self, client, tmp_path):
        """?limite=N devolve os N ultimos pontos com o cum GLOBAL correto —
        cortar a serie nao pode reiniciar a soma."""
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/equity?limite=10").get_json()
        # limite abaixo do minimo (10) e aceito; com 2 pontos devolve os 2
        assert d["n"] == 2
        assert d["pontos"][-1]["cum"] == pytest.approx(6.0)


class TestApiGates:
    """Hermetico por monkeypatch: a rota le DRY_RUN/ALLOW_REAL_TRADING do
    runtime_settings a cada request, e runtime_settings carrega o .env da
    MAQUINA — o teste nao pode depender do que o desenvolvedor tem la.
    (Descoberto na pratica: a copia de .env do worktree estava com as tres
    flags de ignicao armadas, sobra do trabalho de testnet, e o teste acusou
    ARMADA — o endpoint estava certo; o ambiente e que estava sujo.)"""

    def _flags(self, monkeypatch, dry_run, allow_real, env):
        import config.runtime_settings as rs

        monkeypatch.setattr(rs, "DRY_RUN", dry_run)
        monkeypatch.setattr(rs, "ALLOW_REAL_TRADING", allow_real)
        monkeypatch.setattr(dashboard, "APP_ENV", env)

    def test_regua_completa_e_honesta(self, client, monkeypatch):
        """O caminho ao real aparece INTEIRO e sem maquiagem: Etapa 1 segue
        REPROVADA (FAIL pre-registrado e final) e a ignicao fica bloqueada
        com o ambiente paper padrao."""
        self._flags(monkeypatch, dry_run=True, allow_real=False, env="production")
        d = client.get("/api/gates").get_json()
        ids = [e["id"] for e in d["etapas"]]
        assert ids == [
            "pesquisa_micro",
            "holdout",
            "etapa1",
            "etapa2",
            "etapa3",
            "ignicao",
        ]
        por_id = {e["id"]: e for e in d["etapas"]}
        assert por_id["etapa1"]["status"] == "REPROVADA"
        assert por_id["pesquisa_micro"]["status"] in {"EM_COLETA", "MEDIDA"}
        assert por_id["ignicao"]["status"] == "BLOQUEADA"
        assert por_id["ignicao"]["flags"]["dry_run_false"] is False

    def test_ignicao_armada_e_denunciada(self, client, monkeypatch):
        """Se as tres flags estiverem ligadas, a rota nao esconde: ARMADA.
        E exatamente este aviso que pega um .env armado por engano."""
        self._flags(monkeypatch, dry_run=False, allow_real=True, env="production")
        d = client.get("/api/gates").get_json()
        ignicao = next(e for e in d["etapas"] if e["id"] == "ignicao")
        assert ignicao["status"] == "ARMADA"
        assert all(ignicao["flags"].values())

    def test_holdout_trancado_com_data(self, client):
        d = client.get("/api/gates").get_json()
        holdout = next(e for e in d["etapas"] if e["id"] == "holdout")
        assert holdout["status"] == "TRANCADO"
        assert holdout["data"] == "2026-12-01"

    def test_funil_de_hipoteses_com_vereditos_reais(self, client):
        """O funil lista TODAS as famílias com status honesto: as 3
        históricas FAIL (pré-registro final), MOMO/VOLT lidos dos vereditos
        que os labs gravaram, e CARRY v2 agendada para 16/11."""
        d = client.get("/api/gates").get_json()
        frentes = {f["nome"]: f for f in d["frentes"]}
        assert len(frentes) == 7
        assert frentes["Trend Donchian diário"]["status"] == "FAIL"
        assert frentes["CARRY v2 — funding em janela nova"]["status"] == "AGENDADA"
        assert frentes["CARRY v2 — funding em janela nova"]["quando"] == "2026-11-16"
        # MOMO/VOLT: FAIL se o veredito estiver no repo (está, commitado);
        # NAO_MEDIDA apenas num checkout sem research/vereditos
        for nome in ("MOMO — rotação BTC/ETH/SOL", "VOLT — vol-targeting spot"):
            assert frentes[nome]["status"] in {"FAIL", "SOBREVIVE", "NAO_MEDIDA"}


class TestApiAnalytics:
    def test_agregacoes_com_regua_do_gate(self, client, tmp_path):
        """Diário, win-rate móvel, hora e par — trend (999) fora, como no gate."""
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/analytics").get_json()
        assert d["modo"] == "PAPER"
        assert [x["pnl"] for x in d["diario"]] == [pytest.approx(10.0), pytest.approx(-4.0)]
        assert d["diario"][-1]["cum"] == pytest.approx(6.0)
        assert [x["wr"] for x in d["win_movel"]] == [pytest.approx(100.0), pytest.approx(50.0)]
        assert d["por_hora"][10]["n"] == 2  # ambos às 10:00
        assert len(d["por_par"]) == 1 and d["por_par"][0]["symbol"] == "BTCUSDT"
        r = d["ribbon"]
        assert r["n_trades"] == 2 and r["win_rate"] == pytest.approx(50.0)
        assert r["profit_factor"] == pytest.approx(2.5)  # 10 / 4
        assert r["melhor_dia"]["pnl"] == pytest.approx(10.0)
        assert r["pior_dia"]["pnl"] == pytest.approx(-4.0)
        assert r["pct_dias_positivos"] == pytest.approx(50.0)

    def test_referencias_sao_os_pisos_pre_registrados(self, client, tmp_path):
        """A linha de referência vem do GATE (PF 1,3) e da barreira 2:1 —
        nunca um benchmark inventado (anti-exemplo: zip analisado 16/08)."""
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/analytics").get_json()
        assert d["referencias"]["pf_piso_etapa2"] == pytest.approx(1.3)
        assert d["referencias"]["wr_equilibrio_barreira"] == pytest.approx(33.3)

    def test_filtro_de_periodo_estreito_devolve_vazio_honesto(self, client, tmp_path):
        """Trades de 2026-08-10/11 não entram numa janela de 1 dia: estruturas
        vazias e profit_factor NULO — jamais número inventado ou sentinela."""
        _db_mock.conectar = _db_lucro(tmp_path)
        d = client.get("/api/analytics?dias=1").get_json()
        assert d["ribbon"]["n_trades"] == 0
        assert d["diario"] == [] and d["win_movel"] == []
        assert d["ribbon"]["profit_factor"] is None
        assert d["ribbon"]["win_rate"] is None

    def test_banco_vazio_sem_500(self, client, tmp_path):
        _db_mock.conectar = _db_lucro(tmp_path, com_dados=False)
        r = client.get("/api/analytics")
        assert r.status_code == 200
        assert r.get_json()["ribbon"]["n_trades"] == 0

    def test_so_ganhos_profit_factor_nulo_nao_sentinela(self, client, tmp_path):
        """Sem perdas, PF é matematicamente indefinido → None (o zip devolvia
        9,99 de sentinela — exatamente o que não fazemos)."""
        import sqlite3

        caminho = str(tmp_path / "dash2.db")
        con = sqlite3.connect(caminho)
        con.execute(
            "CREATE TABLE sinais (id INTEGER PRIMARY KEY, timestamp TEXT,"
            " symbol TEXT, source TEXT, pnl_usdt REAL, barreira_tocada TEXT)"
        )
        con.execute("CREATE TABLE risk_state (name TEXT PRIMARY KEY, updated_at TEXT, data TEXT)")
        con.execute(
            "INSERT INTO sinais (timestamp, symbol, source, pnl_usdt, barreira_tocada)"
            " VALUES ('2026-08-10T10:00:00', 'BTCUSDT', NULL, 5.0, 'TARGET')"
        )
        con.commit()
        con.close()

        def conectar():
            c = sqlite3.connect(caminho)
            c.row_factory = sqlite3.Row
            return c

        _db_mock.conectar = conectar
        d = client.get("/api/analytics").get_json()
        assert d["ribbon"]["profit_factor"] is None
        assert d["ribbon"]["win_rate"] == pytest.approx(100.0)


class TestApiMesa:
    def test_sem_token_configurado_mesa_nao_existe(self, client, monkeypatch):
        """Fail-closed: sem DASHBOARD_TOKEN no ambiente, 403 sempre."""
        monkeypatch.setattr(dashboard, "_DASHBOARD_TOKEN", "")
        r = client.post("/api/mesa/comando", json={"comando": "pausar_bot"})
        assert r.status_code == 403
        assert "DASHBOARD_TOKEN" in r.get_json()["erro"]

    def _com_token(self, client, monkeypatch, corpo):
        monkeypatch.setattr(dashboard, "_DASHBOARD_TOKEN", "tok-teste")
        return client.post(
            "/api/mesa/comando",
            json=corpo,
            headers={"Authorization": "Bearer tok-teste"},
        )

    def test_com_token_cria_comando_pendente(self, client, monkeypatch):
        _db_mock.criar_comando = MagicMock(return_value=7)
        r = self._com_token(client, monkeypatch, {"comando": "pausar_bot"})
        assert r.status_code == 201
        d = r.get_json()
        assert d["id"] == 7 and d["status"] == "PENDENTE"
        _db_mock.criar_comando.assert_called_once()
        args = _db_mock.criar_comando.call_args
        assert args.args[0] == "pausar_bot" and args.args[1] == {}

    def test_token_errado_401_pelo_gate_global(self, client, monkeypatch):
        monkeypatch.setattr(dashboard, "_DASHBOARD_TOKEN", "tok-teste")
        r = client.post(
            "/api/mesa/comando",
            json={"comando": "pausar_bot"},
            headers={"Authorization": "Bearer errado"},
        )
        assert r.status_code == 401

    def test_comando_fora_da_lista_400(self, client, monkeypatch):
        r = self._com_token(client, monkeypatch, {"comando": "ligar_modo_real"})
        assert r.status_code == 400

    def test_fechar_posicao_valida_symbol(self, client, monkeypatch):
        r = self._com_token(
            client,
            monkeypatch,
            {"comando": "fechar_posicao_paper", "params": {"symbol": "DOGEUSDT"}},
        )
        assert r.status_code == 400
        _db_mock.criar_comando = MagicMock(return_value=9)
        r2 = self._com_token(
            client,
            monkeypatch,
            {"comando": "fechar_posicao_paper", "params": {"symbol": "btcusdt"}},
        )
        assert r2.status_code == 201
        assert _db_mock.criar_comando.call_args.args[1] == {"symbol": "BTCUSDT"}

    def test_historico_e_leitura_simples(self, client):
        _db_mock.listar_comandos = MagicMock(
            return_value=[{"id": 1, "comando": "pausar_bot", "status": "EXECUTADO"}]
        )
        r = client.get("/api/mesa/comandos")
        assert r.status_code == 200
        assert r.get_json()[0]["status"] == "EXECUTADO"


class TestApiQuant:
    def _db_quant(self, tmp_path, com_tabelas=True):
        import sqlite3

        caminho = str(tmp_path / "quant.db")
        con = sqlite3.connect(caminho)
        if com_tabelas:
            con.execute(
                "CREATE TABLE model_metricas (id INTEGER PRIMARY KEY, timestamp TEXT,"
                " symbol TEXT, modelo_tipo TEXT, auc REAL, cv_auc_mean REAL, cv_auc_std REAL)"
            )
            con.execute(
                "CREATE TABLE bot_events (id INTEGER PRIMARY KEY, timestamp TEXT,"
                " service TEXT, symbol TEXT, event_type TEXT, severity TEXT,"
                " message TEXT, data TEXT)"
            )
            con.execute(
                "INSERT INTO model_metricas (timestamp, symbol, modelo_tipo, auc,"
                " cv_auc_mean, cv_auc_std) VALUES"
                " ('2026-08-10T02:00:00', 'BTCUSDT', 'xgboost', 0.61, 0.585, 0.02)"
            )
            con.execute(
                "INSERT INTO bot_events (timestamp, symbol, event_type, severity, message)"
                " VALUES ('2026-08-10T02:01:00', 'BTCUSDT', 'model_drift', 'WARNING',"
                " 'AUC caiu 2 sigma vs historico')"
            )
        con.commit()
        con.close()

        def conectar():
            c = sqlite3.connect(caminho)
            c.row_factory = sqlite3.Row
            return c

        return conectar

    def test_artefatos_reais_do_banco_e_dos_vereditos(self, client, tmp_path, monkeypatch):
        _db_mock.conectar = self._db_quant(tmp_path)
        # worker fora do ar: gauges viram None, jamais numero inventado
        monkeypatch.setattr(
            dashboard.requests,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(dashboard.requests.RequestException()),
        )
        d = client.get("/api/quant").get_json()
        assert d["retreinos"][0]["symbol"] == "BTCUSDT"
        assert d["retreinos"][0]["auc"] == pytest.approx(0.61)
        assert d["drift"][0]["mensagem"].startswith("AUC caiu")
        assert d["exec_gauges"] is None
        # labs lidos dos vereditos REAIS commitados no repo
        assert d["labs"]["momo"]["veredito"] in {"FAIL", "SOBREVIVE"}
        assert d["labs"]["momo"]["trials"] == 8
        assert d["labs"]["volt"]["trials"] == 6
        arquivos = [a["arquivo"] for a in d["arquivo"]]
        assert "momo_pesquisa.json" in arquivos and "volt_pesquisa.json" in arquivos

    def test_banco_sem_tabelas_degrada_sem_500(self, client, tmp_path, monkeypatch):
        _db_mock.conectar = self._db_quant(tmp_path, com_tabelas=False)
        monkeypatch.setattr(
            dashboard.requests,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(dashboard.requests.RequestException()),
        )
        r = client.get("/api/quant")
        assert r.status_code == 200
        d = r.get_json()
        assert d["retreinos"] == [] and d["drift"] == []


class TestApiDiario:
    def test_devolve_a_ultima_entrada_do_relatorio(self, client):
        """A fonte é o arquivo versionado do repo — a v2.2 (ou mais nova)
        tem que vir com título e corpo não-vazios."""
        d = client.get("/api/diario").get_json()
        assert d["titulo"] and d["titulo"].startswith("Diário —")
        assert d["corpo"] and "###" in d["corpo"]
        assert d["fonte"] == "docs/RELATORIO_PRODUCAO.md"

    def test_sem_arquivo_devolve_vazio_nao_500(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(dashboard, "_RELATORIO_PRODUCAO", str(tmp_path / "nao_existe.md"))
        r = client.get("/api/diario")
        assert r.status_code == 200
        d = r.get_json()
        assert d["titulo"] is None and d["corpo"] is None

    def test_arquivo_sem_secao_de_diario_devolve_vazio(self, client, monkeypatch, tmp_path):
        doc = tmp_path / "rel.md"
        doc.write_text("# Relatorio\n\n## Veredicto\ntexto\n", encoding="utf-8")
        monkeypatch.setattr(dashboard, "_RELATORIO_PRODUCAO", str(doc))
        d = client.get("/api/diario").get_json()
        assert d["titulo"] is None


class TestApiSistema:
    def test_servicos_e_relogios_sem_500(self, client):
        """Em qualquer ambiente (Windows com/sem servicos, CI Linux sem `sc`)
        a rota responde 200 com estados do conjunto conhecido."""
        r = client.get("/api/sistema")
        assert r.status_code == 200
        d = r.get_json()
        nomes = [s["nome"] for s in d["servicos"]]
        assert nomes == ["BXBotWorker", "BXBotDashboard", "BXBotBook"]
        estados_validos = {"RODANDO", "PARADO", "AUSENTE", "INDISPONIVEL", "DESCONHECIDO"}
        assert all(s["estado"] in estados_validos for s in d["servicos"])
        assert len(d["relogios"]) == 4

    def test_proximos_disparos_no_futuro(self, client):
        from datetime import datetime as _dt

        d = client.get("/api/sistema").get_json()
        agora = _dt.fromisoformat(d["agora"])
        for rel in d["relogios"]:
            assert _dt.fromisoformat(rel["proximo"]) > agora
