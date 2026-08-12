"""
Testes — relatorio_gate.py, a ferramenta que decide sobre capital real (I-13)
=============================================================================
Este arquivo imprime "GATE: APROVADO — prosseguir à Etapa 3 (capital piloto)"
ou "REPROVADO", e é o que o operador consulta antes de ligar dinheiro. Até
agora não tinha UM teste.

O defeito que motivou a suíte (I-13): a fonte dos trades era `data/btc_data.db`
fixo, e o Postgres só entrava com `--postgres` explícito. Num deploy com
`DATABASE_BACKEND=postgres`, `python relatorio_gate.py` media o SQLite local —
paper trading antigo — e emitia veredito sobre o banco errado, sem dizer que
tinha feito isso.

A propriedade central testada aqui é: **o gate nunca escolhe a fonte por
omissão, e nunca degrada em silêncio para uma fonte diferente da configurada.**
Quando não sabe medir, ele diz que não sabe e reprova.
"""

import os
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import relatorio_gate as rg

DSN = "postgresql://bot:s3nh4-secreta@db.supabase.co:5432/postgres"


def _args(**kw):
    """Namespace no formato que _conectar espera."""
    base = {"db": None, "sqlite": False, "postgres": False, "klines_db": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _sqlite_com_sinais(caminho, trades=(), klines=()):
    con = sqlite3.connect(caminho)
    con.execute(
        "CREATE TABLE sinais (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,"
        " tipo TEXT, preco REAL, motivo TEXT, executado INTEGER, symbol TEXT,"
        " score REAL, source TEXT, executado_em TEXT, preco_saida REAL,"
        " pnl_usdt REAL, pnl_pct REAL, barreira_tocada TEXT)"
    )
    con.execute(
        "CREATE TABLE klines (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,"
        " intervalo TEXT, timestamp INTEGER, abertura REAL, maxima REAL,"
        " minima REAL, fechamento REAL, volume REAL)"
    )
    for ts, pnl, source in trades:
        con.execute(
            "INSERT INTO sinais (timestamp, tipo, preco, executado, symbol,"
            " source, preco_saida, pnl_usdt, pnl_pct, barreira_tocada)"
            " VALUES (?, 'COMPRA', 60000, 1, 'BTCUSDT', ?, 60100, ?, 0.17, 'ALVO')",
            (ts, source, pnl),
        )
    for ms, fech in klines:
        con.execute(
            "INSERT INTO klines (symbol, intervalo, timestamp, fechamento)"
            " VALUES ('BTCUSDT', '1h', ?, ?)",
            (ms, fech),
        )
    con.commit()
    con.close()
    return str(caminho)


@pytest.fixture
def cfg(monkeypatch):
    """Controla o que _config() enxerga, sem tocar no .env real."""
    import config.runtime_settings as rs

    def _set(backend="sqlite", url="", db_path="/nao/existe.db"):
        monkeypatch.setattr(rs, "DATABASE_BACKEND", backend, raising=False)
        monkeypatch.setattr(rs, "DATABASE_URL", url, raising=False)
        monkeypatch.setattr(rs, "DB_PATH", db_path, raising=False)
        # _config() cai para os.environ se o import falhar; zerar evita que um
        # ambiente com DATABASE_URL de verdade vaze para dentro do teste.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_BACKEND", raising=False)

    return _set


@pytest.fixture
def psycopg_falso(monkeypatch):
    """Registra um psycopg falso e devolve a lista de DSNs conectados."""
    conectados = []

    modulo = types.ModuleType("psycopg")
    modulo.connect = lambda url, *a, **k: (conectados.append(url), object())[1]
    monkeypatch.setitem(sys.modules, "psycopg", modulo)
    return conectados


# ── o defeito de I-13: a fonte era escolhida por omissao ───────


class TestResolucaoDeBackend:
    def test_backend_postgres_configurado_vai_para_o_postgres(self, cfg, psycopg_falso, tmp_path):
        """Sem nenhuma flag. Antes ia para o SQLite local e media o banco errado."""
        local = _sqlite_com_sinais(tmp_path / "local.db")
        cfg(backend="postgres", url=DSN, db_path=local)

        _, ph, fonte = rg._conectar(_args())
        assert psycopg_falso == [DSN], "nao conectou no Postgres configurado"
        assert ph == "%s"
        assert fonte.startswith("postgres")

    def test_backend_sqlite_configurado_usa_o_db_path(self, cfg, tmp_path):
        local = _sqlite_com_sinais(tmp_path / "local.db")
        cfg(backend="sqlite", db_path=local)

        conn, ph, fonte = rg._conectar(_args())
        conn.close()
        assert ph == "?"
        assert os.path.normpath(local) in fonte

    @pytest.mark.parametrize("alias", ["postgres", "postgresql", "supabase", "POSTGRES"])
    def test_aliases_de_backend_reconhecidos(self, cfg, psycopg_falso, alias):
        """`database._backend()` aceita os tres aliases; o gate tem que aceitar
        os mesmos, ou 'supabase' no .env viraria SQLite em silencio."""
        cfg(backend=alias, url=DSN)
        rg._conectar(_args())
        assert psycopg_falso == [DSN]

    def test_postgres_sem_dsn_ABORTA_em_vez_de_cair_para_sqlite(self, cfg, tmp_path):
        """A propriedade central: backend Postgres configurado e DSN vazio nao
        pode virar 'mede o arquivo local'. Sem DSN a resposta certa e 'nao sei
        medir' — um veredito sobre a fonte errada e pior que veredito nenhum."""
        local = _sqlite_com_sinais(tmp_path / "local.db")
        cfg(backend="postgres", url="", db_path=local)

        with pytest.raises(SystemExit) as e:
            rg._conectar(_args())
        assert "DATABASE_URL" in str(e.value)

    def test_flag_sqlite_sobrepoe_o_backend_configurado(self, cfg, tmp_path):
        local = _sqlite_com_sinais(tmp_path / "local.db")
        cfg(backend="postgres", url=DSN, db_path=local)

        conn, ph, _ = rg._conectar(_args(sqlite=True))
        conn.close()
        assert ph == "?", "--sqlite explicito nao venceu a configuracao"

    def test_flag_db_sobrepoe_tudo(self, cfg, tmp_path):
        outro = _sqlite_com_sinais(tmp_path / "outro.db")
        cfg(backend="postgres", url=DSN, db_path=str(tmp_path / "config.db"))

        conn, ph, fonte = rg._conectar(_args(db=outro))
        conn.close()
        assert ph == "?"
        assert "outro.db" in fonte

    def test_flag_postgres_sem_dsn_aborta(self, cfg):
        cfg(backend="sqlite", url="")
        with pytest.raises(SystemExit):
            rg._conectar(_args(postgres=True))

    def test_banco_inexistente_aborta(self, cfg):
        cfg(backend="sqlite", db_path="/caminho/que/nao/existe.db")
        with pytest.raises(SystemExit) as e:
            rg._conectar(_args())
        assert "não encontrado" in str(e.value)


class TestCredencialNaoVaza:
    def test_descricao_da_fonte_mascara_a_senha(self, cfg, psycopg_falso):
        cfg(backend="postgres", url=DSN)
        _, _, fonte = rg._conectar(_args())
        assert "s3nh4-secreta" not in fonte
        assert "db.supabase.co" in fonte, "mascarar demais esconde QUAL banco foi medido"

    def test_mascarar_preserva_host_e_banco(self):
        saida = rg._mascarar(DSN)
        assert saida == "postgresql://bot:***@db.supabase.co:5432/postgres"


# ── Etapa 1: pre-requisito ancorado no script, nao no cwd ──────


class TestEtapa1Prerequisito:
    def test_le_o_documento_de_qualquer_diretorio(self, monkeypatch, tmp_path, capsys):
        """GATE_DOC era relativo ao cwd: um `cd` qualquer fazia o open() falhar
        e a Etapa 1 constar REPROVADA por arquivo ausente. Fail-closed certo,
        motivo errado — e motivo errado num gate destroi a confianca nele."""
        monkeypatch.chdir(tmp_path)
        rg._etapa1_aprovada()
        assert "nao encontrado" not in capsys.readouterr().out

    def test_documento_ausente_reprova(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rg, "GATE_DOC", str(tmp_path / "sumiu.md"))
        assert rg._etapa1_aprovada() is False

    def test_etapa1_hoje_esta_reprovada(self):
        """Guarda de regressao: enquanto GATE_GO_LIVE.md disser ESTRATEGIA
        REPROVADA, este relatorio nao pode dizer 'prossiga'."""
        assert rg._etapa1_aprovada() is False


# ── benchmark: klines nao existem no Postgres ──────────────────


class TestBenchmarkBuyAndHold:
    def _periodo(self):
        ini = datetime(2026, 5, 1, tzinfo=timezone.utc)
        return ini, ini + timedelta(days=30)

    def _klines(self, ini, fim):
        return [(int(ini.timestamp() * 1000), 60000.0), (int(fim.timestamp() * 1000), 66000.0)]

    def test_usa_a_fonte_principal_quando_ela_tem_klines(self, tmp_path):
        ini, fim = self._periodo()
        principal = _sqlite_com_sinais(tmp_path / "p.db", klines=self._klines(ini, fim))
        conn = sqlite3.connect(principal)
        p_ini, p_fim, via = rg.benchmark_btc(conn, "?", ini, fim, None)
        conn.close()
        assert (p_ini, p_fim) == (60000.0, 66000.0)
        assert via is None, "usou fallback tendo dado na fonte principal"

    def test_cai_para_o_sqlite_local_quando_a_fonte_nao_tem_klines(self, tmp_path):
        """`klines` nao existe no schema Postgres (nao esta em
        _inicializar_postgres nem em migration nenhuma). Sem este fallback, o
        criterio buy-and-hold ficaria permanentemente indisponivel no Supabase
        e o gate reprovaria para sempre por falta de fonte, nao por
        desempenho."""
        ini, fim = self._periodo()
        vazio = _sqlite_com_sinais(tmp_path / "sem_klines.db")
        sqlite3.connect(vazio).execute("DROP TABLE klines").connection.commit()
        com_klines = _sqlite_com_sinais(tmp_path / "k.db", klines=self._klines(ini, fim))

        conn = sqlite3.connect(vazio)
        p_ini, p_fim, via = rg.benchmark_btc(conn, "?", ini, fim, com_klines)
        conn.close()
        assert (p_ini, p_fim) == (60000.0, 66000.0)
        assert via == com_klines, "nao reportou de onde leu o benchmark"

    def test_sem_klines_em_lugar_nenhum_devolve_none(self, tmp_path):
        ini, fim = self._periodo()
        vazio = _sqlite_com_sinais(tmp_path / "v.db")
        conn = sqlite3.connect(vazio)
        assert rg.benchmark_btc(conn, "?", ini, fim, str(tmp_path / "inexistente.db")) == (
            None,
            None,
            None,
        )
        conn.close()

    def test_klines_fora_do_periodo_nao_servem(self, tmp_path):
        """O fallback nao pode pegar preco de outro periodo: isso produziria um
        benchmark numericamente plausivel e completamente errado."""
        ini, fim = self._periodo()
        antigas = [(int((ini - timedelta(days=400)).timestamp() * 1000), 30000.0)]
        db = _sqlite_com_sinais(tmp_path / "velho.db", klines=antigas)
        conn = sqlite3.connect(db)
        p_ini, _, _ = rg.benchmark_btc(conn, "?", ini, fim, None)
        conn.close()
        assert p_ini is None


# ── comportamento que nunca teve teste ─────────────────────────


class TestFiltroDaEstrategiaPrimaria:
    def test_source_secundaria_nao_entra_na_conta(self, tmp_path, capsys):
        """O trend esta REPROVADO no hold-out. Se o PnL dele entrasse aqui,
        poderia APROVAR o gate por acidente."""
        db = _sqlite_com_sinais(
            tmp_path / "s.db",
            trades=[
                ("2026-05-01T10:00:00", 10.0, "estrategia_otimizada"),
                ("2026-05-02T10:00:00", 999.0, "trend"),
            ],
        )
        conn = sqlite3.connect(db)
        trades = rg.carregar_trades_fechados(conn, "?")
        conn.close()
        assert [t["pnl_usdt"] for t in trades] == [10.0]
        assert "EXCLUIDOS 1 trades de source='trend'" in capsys.readouterr().out

    def test_source_nula_conta_como_primaria(self, tmp_path):
        """~5.255 linhas antigas tem source NULL e vieram todas da primaria."""
        db = _sqlite_com_sinais(tmp_path / "s.db", trades=[("2026-05-01T10:00:00", 7.0, None)])
        conn = sqlite3.connect(db)
        trades = rg.carregar_trades_fechados(conn, "?")
        conn.close()
        assert len(trades) == 1


class TestMetricas:
    def test_sem_perdas_o_profit_factor_e_indefinido_nao_infinito(self):
        m = rg.metricas([{"pnl_usdt": 5.0}, {"pnl_usdt": 3.0}], 1000.0)
        assert m["pf"] is None, "inf > 1.3 aprovaria uma amostra pequena e sortuda"

    def test_drawdown_sobre_equity_acumulada(self):
        trades = [{"pnl_usdt": 100.0}, {"pnl_usdt": -220.0}, {"pnl_usdt": 50.0}]
        m = rg.metricas(trades, 1000.0)
        assert m["mdd_pct"] == pytest.approx(20.0)


class TestSaidaDoProcesso:
    def _rodar(self, db, klines_db=None):
        argv = ["--db", db]
        if klines_db:
            argv += ["--klines-db", klines_db]
        with pytest.raises(SystemExit) as e:
            rg.main(argv)
        return e.value.code

    def test_zero_trades_sai_1(self, tmp_path):
        assert self._rodar(_sqlite_com_sinais(tmp_path / "vazio.db")) == 1

    def test_gate_reprovado_sai_1(self, tmp_path):
        db = _sqlite_com_sinais(
            tmp_path / "s.db", trades=[("2026-05-01T10:00:00", 1.0, "estrategia_otimizada")]
        )
        assert self._rodar(db) == 1

    def test_imprime_a_fonte_medida(self, tmp_path, capsys):
        """Sem isto, o veredito nao diz sobre QUAL banco foi emitido — que era
        exatamente como o defeito de I-13 passava despercebido."""
        db = _sqlite_com_sinais(
            tmp_path / "s.db", trades=[("2026-05-01T10:00:00", 1.0, "estrategia_otimizada")]
        )
        with pytest.raises(SystemExit):
            rg.main(["--db", db])
        assert "Fonte: sqlite" in capsys.readouterr().out
