"""
Testes — compatibilidade Postgres e migrador (frente I-13)
===========================================================
O backend de produção documentado estava **deterministicamente quebrado** e o
migrador destruía a matéria-prima da Etapa 2 do gate. Dois grupos de defeito:

1. **`dict_row` indexado por posição.** O pool usa
   `row_factory=dict_row` (database.py:95), mas `salvar_sinal` e
   `historico_cv_auc_modelo` faziam `row[0]` — idioma do `sqlite3.Row`. Sob
   Postgres isso é `KeyError: 0`. `salvar_sinal` está no caminho de TODA
   decisão: no dia em que `DATABASE_URL` fosse configurado, o bot não gravaria
   um sinal sequer.

2. **Migrador que apaga resultado.** `_insert_sinais` copiava 9 colunas e
   descartava `preco_saida`, `pnl_usdt`, `pnl_pct` e `barreira_tocada` — as
   quatro que a Etapa 2 lê. E sem `ON CONFLICT`, um segundo `--confirmar`
   duplicava a tabela inteira, dobrando o n e o PnL do gate.

Estes testes são **herméticos**: provam a classe do defeito sem exigir um
Postgres. A suíte contra um banco real fica em `TestContraPostgresReal`,
pulada sem `DATABASE_URL` — o critério de saída do I-13 exige rodá-la.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from scripts import migrate_sqlite_to_supabase as mig  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 1. dict_row indexado por posição
# ══════════════════════════════════════════════════════════════


class TestPrimeiroValor:
    """`_primeiro_valor` é o que faz o mesmo código servir aos dois backends."""

    def test_dict_row_do_postgres(self):
        assert database._primeiro_valor({"id": 42}) == 42

    def test_dict_com_nome_de_coluna_diferente(self):
        # Os dois call sites selecionam colunas de nomes distintos (`id` e
        # `cv_auc_mean`). Um `row["id"]` resolveria só metade.
        assert database._primeiro_valor({"cv_auc_mean": 0.61}) == 0.61

    def test_tupla_do_sqlite(self):
        assert database._primeiro_valor((7, "x")) == 7

    def test_sqlite_row_real(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT 99 AS id").fetchone()
        assert database._primeiro_valor(row) == 99
        conn.close()

    def test_none_devolve_none(self):
        assert database._primeiro_valor(None) is None

    def test_dict_vazio_devolve_none(self):
        # RETURNING que nao devolveu coluna nenhuma — nao pode estourar
        assert database._primeiro_valor({}) is None

    def test_row_indexado_por_posicao_QUEBRA_em_dict(self):
        """Contraprova: o código antigo realmente falhava.

        Sem isto, os testes acima passariam mesmo que o defeito nunca tivesse
        existido — e não haveria registro de POR QUE a função existe.
        """
        row_pg = {"id": 42}
        with pytest.raises(KeyError):
            _ = row_pg[0]  # era exatamente isto em salvar_sinal


class TestSemAcessoPorPosicaoNoCaminhoPostgres:
    def test_nenhum_row_indice_zero_em_ramo_postgres(self):
        """Varre os ramos `_pg_connection()` atrás de `row[0]`.

        Trava de regressão: qualquer novo SELECT no caminho Postgres que
        indexe por posição volta a quebrar sob `dict_row`, e o erro só
        apareceria em produção.
        """
        import inspect
        import re

        fonte = inspect.getsource(database)
        ofensores = []
        dentro_pg = False
        indent_pg = 0
        for n, linha in enumerate(fonte.splitlines(), 1):
            # `with _pg_connection()`, nao a DEFINICAO da funcao: casar
            # `_pg_connection()` solto ligava o scanner no `def` (indentacao 0)
            # e ele nunca mais desligava, invadindo o modulo inteiro.
            if "with _pg_connection()" in linha:
                dentro_pg = True
                indent_pg = len(linha) - len(linha.lstrip())
                continue
            if not dentro_pg or not linha.strip():
                continue
            # O bloco Postgres termina quando a indentacao VOLTA — o ramo
            # SQLite costuma vir logo depois, no nivel do corpo da funcao, sem
            # um `else:` (o ramo PG sai por `return`). Detectar so `else:`/`def`
            # fazia o scanner invadir o codigo SQLite, onde `row[0]` e correto:
            # `sqlite3.Row` aceita indice por posicao, `dict_row` nao.
            if len(linha) - len(linha.lstrip()) < indent_pg:
                dentro_pg = False
                continue
            if linha.lstrip().startswith("#"):
                continue
            if re.search(r"\brow\[\d\]|\br\[\d\]", linha):
                ofensores.append(f"{n}: {linha.strip()}")
        assert not ofensores, "acesso por posicao sob dict_row:\n" + "\n".join(ofensores)


# ══════════════════════════════════════════════════════════════
# 2. Migrador
# ══════════════════════════════════════════════════════════════


class _PgFalso:
    """Captura os SQLs e parâmetros que o migrador enviaria."""

    def __init__(self):
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((" ".join(sql.split()), params))


class TestMigradorPreservaOResultado:
    def _linha_sinal(self):
        return {
            "timestamp": "2026-08-01T10:00:00",
            "symbol": "BTCUSDT",
            "tipo": "COMPRA",
            "preco": 60000.0,
            "motivo": "teste",
            "score": 72,
            "source": "executor",
            "executado": 1,
            "executado_em": "2026-08-01T10:00:05",
            "preco_saida": 61000.0,
            "pnl_usdt": 12.34,
            "pnl_pct": 1.67,
            "barreira_tocada": "TARGET",
        }

    def test_as_quatro_colunas_do_meta_labeling_sao_copiadas(self):
        # Sem elas a migracao preserva a DECISAO e apaga o RESULTADO — o
        # relatorio do gate leria `pnl_usdt IS NOT NULL` e acharia zero.
        pg = _PgFalso()
        mig._insert_sinais(pg, [self._linha_sinal()], dry=False)
        sql, params = pg.chamadas[0]
        for coluna in ("preco_saida", "pnl_usdt", "pnl_pct", "barreira_tocada"):
            assert coluna in sql, f"{coluna} nao esta no INSERT"
        assert 61000.0 in params
        assert 12.34 in params
        assert 1.67 in params
        assert "TARGET" in params

    def test_numero_de_placeholders_bate_com_os_parametros(self):
        # Um %s a menos ou a mais e erro de execucao no destino, no meio de uma
        # migracao de centenas de milhares de linhas.
        pg = _PgFalso()
        mig._insert_sinais(pg, [self._linha_sinal()], dry=False)
        sql, params = pg.chamadas[0]
        assert sql.count("%s") == len(params)

    def test_sinal_sem_resultado_migra_com_none(self):
        # Trade ainda aberto: as quatro colunas vem vazias, e isso e valido.
        pg = _PgFalso()
        linha = self._linha_sinal()
        for c in ("preco_saida", "pnl_usdt", "pnl_pct", "barreira_tocada"):
            del linha[c]
        mig._insert_sinais(pg, [linha], dry=False)
        _sql, params = pg.chamadas[0]
        assert params[-4:] == (None, None, None, None)

    def test_dry_run_nao_executa_nada(self):
        pg = _PgFalso()
        n = mig._insert_sinais(pg, [self._linha_sinal()], dry=True)
        assert n == 1
        assert pg.chamadas == []


class TestMigradorIdempotente:
    @pytest.mark.parametrize(
        "fn,linha",
        [
            (mig._insert_sinais, {"timestamp": "2026-01-01T00:00:00"}),
            (mig._insert_snapshots, {"timestamp": "2026-01-01T00:00:00"}),
            (mig._insert_cvd, {"timestamp": "2026-01-01T00:00:00"}),
            (mig._insert_bot_events, {"timestamp": "2026-01-01T00:00:00"}),
        ],
    )
    def test_todo_insert_tem_on_conflict(self, fn, linha):
        # Sem ON CONFLICT, um segundo `--confirmar` duplica a tabela inteira —
        # e em `sinais` isso DOBRA o n e o PnL que o gate le.
        pg = _PgFalso()
        fn(pg, [linha], dry=False)
        sql, _ = pg.chamadas[0]
        assert "ON CONFLICT" in sql, f"{fn.__name__} faz INSERT puro"


class TestLeituraSqliteNaoEngoleErro:
    def test_tabela_inexistente_e_tolerada(self, tmp_path):
        db = tmp_path / "x.db"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        assert mig._sqlite_rows(conn, "nao_existe") == []
        assert mig._sqlite_count(conn, "nao_existe") == -1
        conn.close()

    def test_erro_real_PROPAGA(self, tmp_path):
        """`database is locked` não pode virar "0 linhas, sucesso".

        Era o pior modo de falha do migrador: com o worker rodando 24/7 sobre o
        mesmo arquivo, um lock faria a migração reportar êxito tendo copiado
        nada.
        """
        db = tmp_path / "y.db"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.commit()
        conn.close()  # conexao fechada: qualquer query levanta ProgrammingError
        with pytest.raises(Exception) as exc:
            mig._sqlite_rows(conn, "t")
        assert not isinstance(exc.value, AssertionError)

    def test_dado_real_e_lido(self, tmp_path):
        db = tmp_path / "z.db"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'x')")
        conn.commit()
        assert mig._sqlite_rows(conn, "t") == [{"a": 1, "b": "x"}]
        assert mig._sqlite_count(conn, "t") == 1
        conn.close()


class TestNaoVazarCredencial:
    def test_destino_seguro_omite_usuario_e_senha(self, monkeypatch):
        monkeypatch.setattr(
            mig, "DATABASE_URL", "postgresql://usuario:senha_secreta@db.exemplo.com:5432/postgres"
        )
        saida = mig._destino_seguro()
        assert "senha_secreta" not in saida
        assert "usuario" not in saida
        assert "db.exemplo.com" in saida

    def test_url_vazia_nao_quebra(self, monkeypatch):
        monkeypatch.setattr(mig, "DATABASE_URL", "")
        assert "nao definida" in mig._destino_seguro()

    def test_url_ilegivel_nao_quebra(self, monkeypatch):
        monkeypatch.setattr(mig, "DATABASE_URL", "%%%nao-e-url%%%")
        mig._destino_seguro()  # nao deve levantar


class TestRelatorioGateUsaPsycopg3:
    def test_nao_importa_psycopg2(self):
        # `psycopg2` nao esta no requirements.txt; o projeto inteiro usa
        # psycopg3 (database.py:82). O import falhava mesmo num ambiente
        # corretamente instalado.
        import inspect

        import relatorio_gate

        fonte = inspect.getsource(relatorio_gate)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert "import psycopg2" not in codigo
        assert "psycopg2.connect" not in codigo


# ══════════════════════════════════════════════════════════════
# 3. Contra um Postgres REAL (critério de saída do I-13)
# ══════════════════════════════════════════════════════════════

_URL_TESTE = os.getenv("DATABASE_URL_TESTE", "").strip()


@pytest.mark.skipif(
    not _URL_TESTE,
    reason="exporte DATABASE_URL_TESTE=postgresql://... para um banco DESCARTAVEL",
)
class TestContraPostgresReal:
    """Só roda com `DATABASE_URL_TESTE` apontando para um banco descartável.

    Variável PRÓPRIA, nunca `DATABASE_URL`: apontar estes testes para o banco
    de produção por acidente os faria escrever nele.
    """

    def test_salvar_sinal_devolve_id(self, monkeypatch):
        monkeypatch.setattr(database, "DATABASE_URL", _URL_TESTE)
        database.inicializar()
        sinal_id = database.salvar_sinal("COMPRA", 60000.0, "teste I-13", symbol="BTCUSDT")
        assert isinstance(sinal_id, int) and sinal_id > 0

    def test_historico_cv_auc_devolve_floats(self, monkeypatch):
        monkeypatch.setattr(database, "DATABASE_URL", _URL_TESTE)
        database.inicializar()
        database.salvar_metricas_modelo("BTCUSDT", "XGBOOST", 0.61, 0.60, 0.02)
        hist = database.historico_cv_auc_modelo("BTCUSDT", "XGBOOST")
        assert hist and all(isinstance(v, float) for v in hist)

    def test_crash_recovery_ida_e_volta(self, monkeypatch):
        monkeypatch.setattr(database, "DATABASE_URL", _URL_TESTE)
        database.inicializar()
        pos = {
            "tipo": "LONG",
            "entrada": 60000.0,
            "tamanho_btc": 0.01,
            "stop_atual": 59000.0,
            "target1": 63000.0,
        }
        database.salvar_posicao_aberta("BTCUSDT", pos)
        assert "BTCUSDT" in database.carregar_posicoes_abertas()
        database.remover_posicao_aberta("BTCUSDT")
        assert "BTCUSDT" not in database.carregar_posicoes_abertas()
