"""
Testes herméticos do database.py em modo SQLite (sem Postgres, sem rede).
Usa um banco temporário isolado criado a cada teste via monkeypatch.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Garantir SQLite antes de importar o módulo
os.environ["DATABASE_BACKEND"] = "sqlite"

import database  # noqa: E402 — importar após setar env

# ── Fixture: banco temporário ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def sqlite_tmp(tmp_path, monkeypatch):
    """Redireciona DB_PATH para um arquivo temporário isolado por teste."""
    db_file = str(tmp_path / "test_bot.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    # Garantir que o backend permaneça SQLite mesmo que o env mude
    monkeypatch.setattr(database, "DATABASE_BACKEND", "sqlite")
    database.inicializar()
    yield db_file


def _raw(db_path: str):
    return sqlite3.connect(db_path)


# ── Testes: inicializar ───────────────────────────────────────────────────────


class TestInicializar:
    def test_cria_tabela_sinais(self, sqlite_tmp):
        conn = _raw(sqlite_tmp)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "sinais" in tables

    def test_cria_tabela_snapshots(self, sqlite_tmp):
        conn = _raw(sqlite_tmp)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "snapshots_mercado" in tables

    def test_cria_tabela_trades(self, sqlite_tmp):
        conn = _raw(sqlite_tmp)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "trades" in tables

    def test_cria_tabela_risk_state(self, sqlite_tmp):
        conn = _raw(sqlite_tmp)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "risk_state" in tables

    def test_idempotente(self, sqlite_tmp):
        database.inicializar()  # segunda chamada não deve falhar
        conn = _raw(sqlite_tmp)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        assert "sinais" in tables


# ── Testes: salvar_sinal ──────────────────────────────────────────────────────


class TestSalvarSinal:
    def test_salva_compra(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 65000.0, "RSI 55", symbol="BTCUSDT")
        conn = _raw(sqlite_tmp)
        rows = conn.execute("SELECT tipo, preco FROM sinais").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "COMPRA"
        assert rows[0][1] == pytest.approx(65000.0)

    def test_salva_multiplos(self, sqlite_tmp):
        for i in range(4):
            database.salvar_sinal("AGUARDAR", float(60000 + i * 500), f"motivo {i}")
        conn = _raw(sqlite_tmp)
        count = conn.execute("SELECT COUNT(*) FROM sinais").fetchone()[0]
        conn.close()
        assert count == 4

    def test_salva_com_score(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 64000.0, "score alto", score=82.5)
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT score FROM sinais").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == pytest.approx(82.5)

    def test_salva_executado(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 64000.0, "executado", executado=True)
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT executado FROM sinais").fetchone()
        conn.close()
        assert row[0] == 1

    def test_retorna_id_da_linha_inserida(self, sqlite_tmp):
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "primeiro")
        assert isinstance(sinal_id, int)
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT tipo FROM sinais WHERE id = ?", (sinal_id,)).fetchone()
        conn.close()
        assert row[0] == "COMPRA"

    def test_ids_sequenciais_e_distintos(self, sqlite_tmp):
        id1 = database.salvar_sinal("COMPRA", 64000.0, "um")
        id2 = database.salvar_sinal("VENDA", 65000.0, "dois")
        assert id1 != id2
        assert id2 > id1

    def test_colunas_de_resultado_nulas_por_padrao(self, sqlite_tmp):
        # P1-3: preco_saida/pnl_usdt/pnl_pct/barreira_tocada so sao
        # preenchidas por atualizar_sinal_fechamento, no fechamento do trade
        # -- na criacao (entrada) devem vir NULL.
        database.salvar_sinal("COMPRA", 64000.0, "entrada")
        conn = _raw(sqlite_tmp)
        row = conn.execute(
            "SELECT preco_saida, pnl_usdt, pnl_pct, barreira_tocada FROM sinais"
        ).fetchone()
        conn.close()
        assert row == (None, None, None, None)


# ── Testes: marcar_sinal_executado / atualizar_sinal_fechamento (P1-3) ────────


class TestMarcarSinalExecutado:
    def test_marca_executado_e_grava_timestamp(self, sqlite_tmp):
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.marcar_sinal_executado(sinal_id)
        conn = _raw(sqlite_tmp)
        row = conn.execute(
            "SELECT executado, executado_em FROM sinais WHERE id = ?", (sinal_id,)
        ).fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] is not None

    def test_sinal_id_none_e_no_op(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 64000.0, "outro")  # linha nao tocada
        database.marcar_sinal_executado(None)  # nao deve lancar nem afetar nada
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT executado FROM sinais").fetchone()
        conn.close()
        assert row[0] == 0

    def test_so_afeta_a_linha_referenciada(self, sqlite_tmp):
        id1 = database.salvar_sinal("COMPRA", 64000.0, "um")
        id2 = database.salvar_sinal("COMPRA", 65000.0, "dois")
        database.marcar_sinal_executado(id1)
        conn = _raw(sqlite_tmp)
        rows = {r[0]: r[1] for r in conn.execute("SELECT id, executado FROM sinais").fetchall()}
        conn.close()
        assert rows[id1] == 1
        assert rows[id2] == 0


class TestAtualizarSinalFechamento:
    def test_grava_resultado_do_trade(self, sqlite_tmp):
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.atualizar_sinal_fechamento(sinal_id, 65000.0, 100.0, 1.56, "TARGET")
        conn = _raw(sqlite_tmp)
        row = conn.execute(
            "SELECT preco_saida, pnl_usdt, pnl_pct, barreira_tocada FROM sinais WHERE id = ?",
            (sinal_id,),
        ).fetchone()
        conn.close()
        assert row == (65000.0, 100.0, 1.56, "TARGET")

    def test_sinal_id_none_e_no_op(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.atualizar_sinal_fechamento(None, 65000.0, 100.0, 1.56, "TARGET")  # nao lanca
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT preco_saida FROM sinais").fetchone()
        conn.close()
        assert row[0] is None

    def test_nao_afeta_executado(self, sqlite_tmp):
        # atualizar_sinal_fechamento so grava o resultado -- executado/
        # executado_em sao responsabilidade exclusiva de marcar_sinal_executado.
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.marcar_sinal_executado(sinal_id)
        database.atualizar_sinal_fechamento(sinal_id, 65000.0, 100.0, 1.56, "TARGET")
        conn = _raw(sqlite_tmp)
        row = conn.execute("SELECT executado FROM sinais WHERE id = ?", (sinal_id,)).fetchone()
        conn.close()
        assert row[0] == 1  # preservado, nao resetado pela segunda chamada


# ── Testes: sinais_executados (P1-3) ──────────────────────────────────────────


class TestSinaisExecutados:
    def test_so_retorna_trades_realmente_fechados(self, sqlite_tmp):
        # entrada executada mas AINDA ABERTA (pnl_usdt continua NULL) --
        # nao pode aparecer aqui, senao kelly_do_banco() a contaria como
        # se ja tivesse um resultado conhecido.
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.marcar_sinal_executado(sinal_id)
        assert database.sinais_executados() == []

    def test_retorna_apos_fechamento(self, sqlite_tmp):
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.marcar_sinal_executado(sinal_id)
        database.atualizar_sinal_fechamento(sinal_id, 65000.0, 100.0, 1.56, "TARGET")
        rows = database.sinais_executados()
        assert len(rows) == 1
        assert rows[0]["pnl_usdt"] == pytest.approx(100.0)
        assert rows[0]["barreira_tocada"] == "TARGET"

    def test_nao_executado_nunca_aparece_mesmo_fechado(self, sqlite_tmp):
        # atualizar_sinal_fechamento sem marcar_sinal_executado antes (nao
        # deveria acontecer no fluxo real, mas o guard e por executado E
        # pnl_usdt, entao confirma que ambos sao exigidos).
        sinal_id = database.salvar_sinal("COMPRA", 64000.0, "entrada")
        database.atualizar_sinal_fechamento(sinal_id, 65000.0, 100.0, 1.56, "TARGET")
        assert database.sinais_executados() == []


# ── Testes: salvar_snapshot ───────────────────────────────────────────────────


class TestSalvarSnapshot:
    def test_salva_snapshot_basico(self, sqlite_tmp):
        dados = {"preco": 64500.0, "symbol": "BTCUSDT", "rsi_1h": 55.0}
        database.salvar_snapshot(dados)
        conn = _raw(sqlite_tmp)
        rows = conn.execute("SELECT preco FROM snapshots_mercado").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(64500.0)

    def test_salva_snapshot_campos_opcionais(self, sqlite_tmp):
        dados = {
            "preco": 64000.0,
            "variacao_24h_%": 1.5,
            "ema20_1h": 63500.0,
            "ema50_1h": 63000.0,
        }
        database.salvar_snapshot(dados, symbol="ETHUSDT")
        conn = _raw(sqlite_tmp)
        rows = conn.execute("SELECT preco, symbol FROM snapshots_mercado").fetchall()
        conn.close()
        assert len(rows) == 1


# ── Testes: buscar_sinais ─────────────────────────────────────────────────────


class TestBuscarSinais:
    def test_retorna_lista_vazia(self, sqlite_tmp):
        sinais = database.buscar_sinais(limit=10)
        assert isinstance(sinais, list)
        assert len(sinais) == 0

    def test_retorna_sinais_salvos(self, sqlite_tmp):
        database.salvar_sinal("COMPRA", 64000.0, "teste")
        sinais = database.buscar_sinais(limit=10)
        assert len(sinais) == 1
        assert sinais[0]["tipo"] == "COMPRA"

    def test_limite_respeitado(self, sqlite_tmp):
        for i in range(5):
            database.salvar_sinal("COMPRA", float(60000 + i), f"motivo {i}")
        sinais = database.buscar_sinais(limit=3)
        assert len(sinais) <= 3


# ── Testes: healthcheck ───────────────────────────────────────────────────────


class TestHealthcheck:
    def test_retorna_true_com_banco_ok(self, sqlite_tmp):
        assert database.healthcheck() is True

    def test_retorna_false_quando_sqlite_falha(self, monkeypatch):
        # Simula falha forçando sqlite3.connect a levantar exceção
        import sqlite3 as _sqlite3

        original_connect = _sqlite3.connect

        def _connect_fail(*args, **kwargs):
            raise _sqlite3.OperationalError("fake failure")

        monkeypatch.setattr(_sqlite3, "connect", _connect_fail)
        monkeypatch.setattr(database, "DATABASE_BACKEND", "sqlite")
        result = database.healthcheck()
        # Restaurar antes da próxima asserção
        monkeypatch.setattr(_sqlite3, "connect", original_connect)
        assert result is False


# ── Testes: salvar_risk_state / carregar_risk_state ───────────────────────────


class TestRiskState:
    def test_round_trip(self, sqlite_tmp):
        state = {"capital": 1000.0, "drawdown": 0.05, "operacoes_hoje": 3}
        database.salvar_risk_state(state, name="default")
        loaded = database.carregar_risk_state("default")
        assert loaded is not None
        assert loaded["capital"] == pytest.approx(1000.0)
        assert loaded["operacoes_hoje"] == 3

    def test_inexistente_retorna_none(self, sqlite_tmp):
        result = database.carregar_risk_state("nao_existe_xyz")
        assert result is None

    def test_atualizar_state(self, sqlite_tmp):
        database.salvar_risk_state({"capital": 500.0}, name="slot1")
        database.salvar_risk_state({"capital": 750.0}, name="slot1")
        loaded = database.carregar_risk_state("slot1")
        assert loaded["capital"] == pytest.approx(750.0)


# ── A-9: mascaramento de senha do DSN em logs/erros ──────────────────────────


def test_mascarar_dsn_oculta_senha():
    import database as _db

    dsn = "postgresql://postgres.abc:MinhaSenha123@aws-0-sa.pooler.supabase.com:6543/postgres"
    m = _db._mascarar_dsn(dsn)
    assert "MinhaSenha123" not in m
    assert "***" in m
    assert "aws-0-sa.pooler.supabase.com" in m  # host preservado (útil p/ debug)


def test_conectar_pg_seguro_mascara_erro():
    import database as _db

    class _Boom(Exception):
        pass

    def _fabrica():
        raise _Boom("falha em postgresql://u:segredo@host/db")

    try:
        _db._conectar_pg_seguro(_fabrica)
        assert False, "deveria ter levantado"
    except RuntimeError as e:
        assert "segredo" not in str(e)
        assert "***" in str(e)
