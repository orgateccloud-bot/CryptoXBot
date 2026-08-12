"""
Critério de saída da frente I-13 — contra um Postgres REAL
===========================================================
`docs/RELATORIO_MODULOS.md` define três provas que só um Postgres de verdade
consegue dar:

  1. `salvar_sinal`, `historico_cv_auc_modelo` e as 3 funções de crash
     recovery funcionam sob `dict_row` (em `test_database_postgres.py`).
  2. **Migrar duas vezes seguidas dá `SELECT COUNT(*)` idêntico nas 6
     tabelas.**
  3. **`SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NOT NULL` idêntico na
     origem e no destino.**

2 e 3 nunca tiveram teste. Este arquivo é essas duas provas.

POR QUE ELAS IMPORTAM MAIS DO QUE PARECEM
------------------------------------------
Historia em duas medicoes, ambas contra Postgres real:

2026-08-12 (manha): o `ON CONFLICT DO NOTHING` de snapshots/cvd/sinais/
bot_events NAO FAZIA NADA — unica chave unica era o id BIGSERIAL, que nunca
conflita. `test_sem_a_guarda_a_reinsercao_DUPLICA` demonstrou a duplicacao
acontecendo. Em `trades` o UNIQUE e parcial (trade_id NULL fica fora).

2026-08-12 (tarde, migration 004 aplicada no codigo): os UNIQUE de chave
natural existem, criados por `inicializar()`; o ON CONFLICT das quatro passou
a segurar reinsercao de verdade — `test_com_004_a_reinsercao_so_duplica_trades_null`
prova a verdade nova, e `trades` continua sendo o motivo de a guarda existir.

COMO RODAR
----------
Precisa de um Postgres DESCARTÁVEL. Crie um banco só para isto e exporte:

    export DATABASE_URL_TESTE=postgresql://user:senha@host:5432/bxbot_teste
    pytest tests/test_migrador_postgres_real.py -v

ISOLAMENTO
----------
Tudo roda dentro de um SCHEMA temporário (`bxbot_teste_<pid>`), criado e
derrubado pela fixture. Nem o migrador nem `database.py` qualificam nome de
tabela, então o `search_path` da DSN redireciona todo INSERT/DDL para lá —
mesmo que `DATABASE_URL_TESTE` aponte, por acidente, para o banco de
produção. Há ainda uma recusa explícita se ele for igual a `DATABASE_URL`.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from scripts import migrate_sqlite_to_supabase as mig  # noqa: E402

_URL_TESTE = os.getenv("DATABASE_URL_TESTE", "").strip()

# Marca por CLASSE, nao no modulo: `TestDsnComSchema` e funcao pura e tem que
# rodar sempre. Um arquivo inteiro pulado por falta de DSN e um arquivo que
# ninguem descobre estar quebrado.
precisa_pg = pytest.mark.skipif(
    not _URL_TESTE,
    reason="exporte DATABASE_URL_TESTE=postgresql://... para um banco DESCARTAVEL",
)

SCHEMA = f"bxbot_teste_{os.getpid()}"

# Linhas semeadas no SQLite de origem, por tabela. `sinais` leva 3, das quais
# 2 com pnl_usdt — é a contagem que o critério 3 compara.
SINAIS_COM_PNL = 2


def _dsn_no_schema(dsn: str, schema: str) -> str:
    """Prende a conexão a um schema via `options` do libpq.

    `search_path` resolve todo nome de tabela não qualificado — e nem o
    migrador nem database.py qualificam. É o que torna impossível este teste
    escrever numa tabela de produção mesmo apontado para o banco dela.
    """
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options=-csearch_path%3D{schema}"


def _semear_sqlite(caminho: str) -> None:
    con = sqlite3.connect(caminho)
    con.executescript("""
        CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            symbol TEXT, preco REAL, volume_btc REAL, volume_usdt REAL,
            direcao TEXT, eh_baleia INTEGER, trade_id INTEGER);
        CREATE TABLE snapshots_mercado (id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, preco REAL, rsi_1h REAL, tendencia TEXT);
        CREATE TABLE cvd_historico (id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, cvd REAL, compras_btc REAL, vendas_btc REAL);
        CREATE TABLE sinais (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            tipo TEXT, preco REAL, motivo TEXT, executado INTEGER, symbol TEXT,
            score REAL, source TEXT, executado_em TEXT, preco_saida REAL,
            pnl_usdt REAL, pnl_pct REAL, barreira_tocada TEXT);
        CREATE TABLE risk_state (name TEXT PRIMARY KEY, updated_at TEXT, data TEXT);
        CREATE TABLE bot_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            service TEXT, symbol TEXT, event_type TEXT, severity TEXT,
            message TEXT, data TEXT);
        """)
    # trades: uma COM trade_id (entra no indice parcial) e duas SEM — a
    # proporcao real e 16%/84%, e e o que faz o ON CONFLICT nao bastar.
    con.execute(
        "INSERT INTO trades (timestamp, symbol, preco, volume_btc, volume_usdt,"
        " direcao, eh_baleia, trade_id) VALUES"
        " ('2026-05-01T10:00:00','BTCUSDT',60000,0.5,30000,'COMPRA',0,111)"
    )
    for i in (1, 2):
        con.execute(
            "INSERT INTO trades (timestamp, symbol, preco, volume_btc, volume_usdt,"
            " direcao, eh_baleia, trade_id) VALUES"
            f" ('2026-05-0{i}T11:00:00','BTCUSDT',6000{i},0.5,30000,'VENDA',0,NULL)"
        )
    con.execute(
        "INSERT INTO snapshots_mercado (timestamp, symbol, preco, rsi_1h, tendencia)"
        " VALUES ('2026-05-01T10:00:00','BTCUSDT',60000,55.5,'ALTA')"
    )
    con.execute(
        "INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)"
        " VALUES ('2026-05-01T10:00:00','BTCUSDT',1.5,10,8.5)"
    )
    # 3 sinais, 2 fechados com resultado
    con.execute(
        "INSERT INTO sinais (timestamp, tipo, preco, motivo, executado, symbol,"
        " source, preco_saida, pnl_usdt, pnl_pct, barreira_tocada) VALUES"
        " ('2026-05-01T10:00:00','COMPRA',60000,'t',1,'BTCUSDT','estrategia_otimizada',"
        "61000,12.34,1.67,'TARGET')"
    )
    con.execute(
        "INSERT INTO sinais (timestamp, tipo, preco, motivo, executado, symbol,"
        " source, preco_saida, pnl_usdt, pnl_pct, barreira_tocada) VALUES"
        " ('2026-05-02T10:00:00','COMPRA',61000,'t',1,'BTCUSDT','estrategia_otimizada',"
        "60500,-5.10,-0.82,'STOP')"
    )
    con.execute(
        "INSERT INTO sinais (timestamp, tipo, preco, motivo, executado, symbol, source)"
        " VALUES ('2026-05-03T10:00:00','COMPRA',62000,'aberto',1,'BTCUSDT',"
        "'estrategia_otimizada')"
    )
    con.execute(
        "INSERT INTO risk_state (name, updated_at, data)"
        " VALUES ('default','2026-05-01T10:00:00','{\"pnl_dia\": 7.24}')"
    )
    con.execute(
        "INSERT INTO bot_events (timestamp, service, symbol, event_type, severity,"
        " message, data) VALUES ('2026-05-01T10:00:00','worker','BTCUSDT',"
        "'modo_efetivo','INFO','SIMULACAO','{}')"
    )
    con.commit()
    con.close()


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """SQLite semeado + schema Postgres descartável. Derruba tudo no fim."""
    if _URL_TESTE == os.getenv("DATABASE_URL", "").strip() and _URL_TESTE:
        pytest.fail(
            "DATABASE_URL_TESTE e igual a DATABASE_URL — aponte para um banco "
            "DESCARTAVEL, nunca para o de producao."
        )

    import psycopg

    dsn_schema = _dsn_no_schema(_URL_TESTE, SCHEMA)

    admin = psycopg.connect(_URL_TESTE, autocommit=True)
    admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    admin.execute(f'CREATE SCHEMA "{SCHEMA}"')

    sqlite_path = str(tmp_path / "origem.db")
    _semear_sqlite(sqlite_path)

    monkeypatch.setattr(mig, "DB_PATH", sqlite_path)
    monkeypatch.setattr(mig, "DATABASE_URL", dsn_schema)
    monkeypatch.setattr(database, "DATABASE_URL", dsn_schema)
    monkeypatch.setattr(database, "DATABASE_BACKEND", "postgres")
    database.fechar_pool()  # o pool cacheia a DSN antiga
    database.inicializar()  # cria as 6 tabelas DENTRO do schema

    yield {"sqlite": sqlite_path, "dsn": dsn_schema}

    database.fechar_pool()
    admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    admin.close()


def _contagens(dsn) -> dict[str, int]:
    import psycopg

    con = psycopg.connect(dsn, autocommit=True)
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in mig.TABELAS}
    finally:
        con.close()


def _migrar(forcar=False) -> int:
    try:
        mig.cmd_migrate(dry=False, forcar=forcar)
    except SystemExit as e:
        return e.code or 0
    return 0


# ── critério de saída, parte 2 ─────────────────────────────────


@precisa_pg
class TestMigrarDuasVezes:
    def test_contagem_identica_nas_6_tabelas(self, ambiente):
        """O critério, literal: migrar duas vezes e obter COUNT(*) idêntico."""
        assert _migrar() == 0
        depois_1 = _contagens(ambiente["dsn"])
        assert sum(depois_1.values()) > 0, "a primeira migracao nao inseriu nada"

        codigo = _migrar()
        depois_2 = _contagens(ambiente["dsn"])

        assert depois_2 == depois_1, f"a segunda migracao mudou o destino: {depois_1} -> {depois_2}"
        assert codigo == 1, "a segunda migracao deveria RECUSAR, nao seguir em silencio"

    def test_primeira_migracao_copia_tudo(self, ambiente):
        _migrar()
        c = _contagens(ambiente["dsn"])
        assert c["trades"] == 3
        assert c["snapshots_mercado"] == 1
        assert c["cvd_historico"] == 1
        assert c["sinais"] == 3
        assert c["risk_state"] == 1
        assert c["bot_events"] == 1

    def test_com_004_a_reinsercao_so_duplica_trades_null(self, ambiente):
        """Antes da 004, --forcar duplicava sinais/snapshots/cvd/bot_events
        inteiras (provado nesta suite em 2026-08-12, contra PG real). Com os
        UNIQUE criados, o ON CONFLICT segura as quatro — o que resta duplicar
        sao as linhas de `trades` com trade_id NULL, fora do indice parcial.
        E exatamente por isso que trades continua bloqueando a guarda."""
        _migrar()
        antes = _contagens(ambiente["dsn"])
        _migrar(forcar=True)
        depois = _contagens(ambiente["dsn"])

        assert depois["sinais"] == antes["sinais"], "ON CONFLICT de sinais nao segurou"
        assert depois["snapshots_mercado"] == antes["snapshots_mercado"]
        assert depois["cvd_historico"] == antes["cvd_historico"]
        assert depois["bot_events"] == antes["bot_events"]
        # trades: as 2 linhas com trade_id NULL reinsertam; a com trade_id nao
        assert depois["trades"] == antes["trades"] + 2
        assert depois["risk_state"] == antes["risk_state"]


# ── critério de saída, parte 3 ─────────────────────────────────


@precisa_pg
class TestResultadoDosTradesSobrevive:
    def _pnl_nao_nulo(self, dsn) -> int:
        import psycopg

        con = psycopg.connect(dsn, autocommit=True)
        try:
            return con.execute("SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NOT NULL").fetchone()[
                0
            ]
        finally:
            con.close()

    def test_contagem_de_pnl_identica_origem_e_destino(self, ambiente):
        """O critério, literal. Antes do I-13 o migrador copiava 9 colunas e
        descartava as 4 do meta-labeling: o destino teria 0 aqui, depois de
        meses de paper trading."""
        _migrar()
        origem = sqlite3.connect(ambiente["sqlite"])
        n_origem = origem.execute(
            "SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NOT NULL"
        ).fetchone()[0]
        origem.close()

        assert n_origem == SINAIS_COM_PNL
        assert self._pnl_nao_nulo(ambiente["dsn"]) == n_origem

    def test_os_valores_chegam_intactos_nao_so_a_contagem(self, ambiente):
        """Contagem igual com valores errados passaria no critério e mentiria
        para o relatório do gate."""
        import psycopg

        _migrar()
        con = psycopg.connect(ambiente["dsn"], autocommit=True)
        try:
            row = con.execute(
                "SELECT preco_saida, pnl_usdt, pnl_pct, barreira_tocada FROM sinais"
                " WHERE barreira_tocada = 'TARGET'"
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "o sinal com TARGET nao chegou"
        assert float(row[0]) == 61000.0
        assert float(row[1]) == pytest.approx(12.34)
        assert float(row[2]) == pytest.approx(1.67)

    def test_sinal_sem_resultado_chega_com_null(self, ambiente):
        """O terceiro sinal está aberto: `pnl_usdt` tem que ficar NULL, não
        virar 0 — 0 entraria na conta do gate como trade fechado sem lucro."""
        import psycopg

        _migrar()
        con = psycopg.connect(ambiente["dsn"], autocommit=True)
        try:
            n = con.execute("SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NULL").fetchone()[0]
        finally:
            con.close()
        assert n == 1


# ── o que o schema Postgres promete ────────────────────────────


@precisa_pg
class TestSchemaReal:
    def test_timestamp_e_timestamptz_nas_tabelas_migradas(self, ambiente):
        """I-13 trocou TEXT por TIMESTAMPTZ no Postgres. Se o migrador
        gravasse string, a comparação de data voltaria a ser por texto."""
        import psycopg

        _migrar()
        con = psycopg.connect(ambiente["dsn"], autocommit=True)
        try:
            tipos = dict(
                con.execute(
                    "SELECT table_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = %s AND column_name = 'timestamp'",
                    (SCHEMA,),
                ).fetchall()
            )
        finally:
            con.close()
        for tabela in ("trades", "snapshots_mercado", "cvd_historico", "sinais"):
            assert tipos.get(tabela) == "timestamp with time zone", f"{tabela}: {tipos.get(tabela)}"

    def test_004_cria_unique_nas_quatro_tabelas(self, ambiente):
        """Pos-004: banco criado do zero por inicializar() nasce com os
        indices que o migrador espera — DDL e guarda andam juntos."""
        import psycopg

        con = psycopg.connect(ambiente["dsn"], autocommit=True)
        try:
            linhas = con.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s",
                (SCHEMA,),
            ).fetchall()
        finally:
            con.close()
        presentes = {r[0] for r in linhas}
        for tabela, indice in mig.IDEMPOTENTES_COM_UNIQUE.items():
            assert indice in presentes, f"{tabela}: indice {indice} nao foi criado"


# ── isolamento: a rede de seguranca deste arquivo ──────────────


@precisa_pg
class TestIsolamentoPorSchema:
    def test_tudo_foi_criado_dentro_do_schema_temporario(self, ambiente):
        import psycopg

        _migrar()
        con = psycopg.connect(_URL_TESTE, autocommit=True)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s",
                (SCHEMA,),
            ).fetchone()[0]
        finally:
            con.close()
        assert n >= len(mig.TABELAS), "as tabelas nao foram para o schema de teste"


class TestDsnComSchema:
    """Funcao pura — roda sempre, mesmo sem DSN de teste."""

    def test_prende_o_search_path_ao_schema(self):
        dsn = _dsn_no_schema("postgresql://u:p@h/db", "meu_schema")
        assert dsn.endswith("?options=-csearch_path%3Dmeu_schema")

    def test_nao_estraga_dsn_que_ja_tem_query(self):
        """`?sslmode=require` e comum em Supabase; um segundo `?` invalidaria
        a URI e o isolamento cairia silenciosamente no schema `public`."""
        dsn = _dsn_no_schema("postgresql://u:p@h/db?sslmode=require", "s")
        assert dsn.count("?") == 1
        assert "sslmode=require" in dsn and "search_path%3Ds" in dsn
