"""
Testes — savepoint por tabela e commit condicional no migrador (frente I-13)
=============================================================================
`scripts/migrate_sqlite_to_supabase.py` move a matéria-prima da Etapa 2 do
gate. Dois defeitos no mesmo laço faziam uma migração parcial ser reportada
como sucesso:

1. Sem SAVEPOINT. Em psycopg3 o primeiro erro deixa a transação em estado
   abortado; as 5 tabelas seguintes falhavam em cascata com "current
   transaction is aborted", e o laço imprimia [ERRO] em cada uma como se
   fossem 6 problemas independentes.
2. `pg.commit()` incondicional, seguido de `print("[OK] Commit realizado")`
   — mesmo com tabelas em erro.

Migração parcial é pior que nenhuma: o operador acredita que tem os dados, e
o `--confirmar` seguinte reinsere só o que faltou sem que ninguém saiba o que
ficou de fora.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import migrate_sqlite_to_supabase as mig


class _Savepoint:
    """Contexto que imita `psycopg.Connection.transaction()` aninhado:
    emite SAVEPOINT na entrada e ROLLBACK TO se o corpo levantar."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.eventos.append("SAVEPOINT")
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.eventos.append("ROLLBACK TO")
            # ROLLBACK TO SAVEPOINT *limpa* o estado abortado da transacao —
            # e exatamente por isso que ele resolve a cascata.
            self.conn.abortada = False
        else:
            self.conn.eventos.append("RELEASE")
        return False  # nunca engole a excecao


class _Resultado:
    """O que `Connection.execute` devolve no psycopg3: um cursor."""

    def __init__(self, linhas):
        self._linhas = linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchall(self):
        return list(self._linhas)


class FakePg:
    def __init__(self, falhar_em=(), destino_ocupado=None, indices=()):
        self.falhar_em = set(falhar_em)
        self.eventos = []
        self.linhas = []
        self.abortada = False
        # I-13: contagens do destino, para exercitar a guarda de reinsercao.
        # Vazio = destino limpo, que e o caso da migracao inicial.
        self.destino_ocupado = destino_ocupado or {}
        # 004: indices UNIQUE presentes no destino (pg_indexes). Vazio modela
        # um destino antigo, criado antes da 004.
        self.indices = set(indices)

    def transaction(self):
        return _Savepoint(self)

    def execute(self, sql, params=None):
        # depois de um erro sem savepoint, um Postgres real recusa TUDO.
        if self.abortada:
            raise RuntimeError("current transaction is aborted")

        # A guarda de destino populado consulta o catalogo e conta linhas. Um
        # fake que so entende INSERT nao modela o psycopg3, e mascararia esse
        # caminho inteiro.
        if "information_schema.tables" in sql:
            return _Resultado([{"table_name": t} for t in mig.TABELAS])
        if "pg_indexes" in sql:
            return _Resultado([{"indexname": i} for i in self.indices])
        if sql.lstrip().upper().startswith("SELECT COUNT(*)"):
            tabela = sql.split("FROM")[1].strip().split()[0]
            return _Resultado([{"n": self.destino_ocupado.get(tabela, 0)}])

        for t in self.falhar_em:
            if f"INTO {t}" in sql:
                self.abortada = True
                raise RuntimeError(f"erro proposital em {t}")
        self.linhas.append(sql)
        return _Resultado([])

    def commit(self):
        self.eventos.append("COMMIT")

    def rollback(self):
        self.eventos.append("ROLLBACK")
        self.abortada = False

    def close(self):
        pass


@pytest.fixture
def migracao(monkeypatch):
    """Roda cmd_migrate com SQLite e Postgres falsos."""

    def _run(falhar_em=(), dry=False, destino_ocupado=None, forcar=False, indices=()):
        pg = FakePg(falhar_em, destino_ocupado, indices)
        monkeypatch.setattr(mig, "DATABASE_URL", "postgresql://u:senha@host/banco")
        monkeypatch.setattr(mig, "_pg_conn", lambda: pg)
        monkeypatch.setattr(mig, "_sqlite_conn", lambda: type("C", (), {"close": lambda s: None})())
        monkeypatch.setattr(mig, "_sqlite_rows", lambda c, t: [{"timestamp": "2026-08-11"}])
        codigo = 0
        try:
            mig.cmd_migrate(dry=dry, forcar=forcar)
        except SystemExit as e:
            codigo = e.code
        return pg, codigo

    return _run


class TestSavepointPorTabela:
    def test_erro_numa_tabela_nao_derruba_as_seguintes(self, migracao):
        """O defeito: `trades` falha e as 5 seguintes morrem em cascata."""
        pg, _ = migracao(falhar_em=["trades"])
        # o savepoint desfaz so a tabela que falhou; as outras inserem
        assert "ROLLBACK TO" in pg.eventos
        assert not pg.abortada, "transacao ficou abortada — savepoint nao desfez"
        outras = [s for s in pg.linhas if "INTO trades" not in s]
        assert outras, "nenhuma tabela posterior a trades conseguiu inserir"

    def test_toda_tabela_roda_dentro_de_um_savepoint(self, migracao):
        pg, _ = migracao()
        assert pg.eventos.count("SAVEPOINT") == len(mig.TABELAS)


class TestCommitCondicional:
    def test_com_erro_nao_commita(self, migracao):
        pg, codigo = migracao(falhar_em=["sinais"])
        assert "COMMIT" not in pg.eventos, "commitou uma migracao parcial"
        assert "ROLLBACK" in pg.eventos
        assert codigo == 1, "saiu 0 com tabela em erro"

    def test_sem_erro_commita_e_sai_zero(self, migracao):
        pg, codigo = migracao()
        assert "COMMIT" in pg.eventos
        assert codigo == 0

    def test_dry_run_nunca_commita(self, migracao):
        pg, codigo = migracao(dry=True)
        assert "COMMIT" not in pg.eventos
        assert codigo == 0


class TestGuardaDeDestinoPopulado:
    """A idempotência que o `ON CONFLICT` NÃO dá.

    Medido no schema: o único UNIQUE fora das PKs é `idx_trades_trade_id`, e
    ele é parcial (`WHERE trade_id IS NOT NULL`). `snapshots_mercado`,
    `cvd_historico`, `sinais` e `bot_events` têm só `id BIGSERIAL PRIMARY
    KEY`, que gera valor novo a cada insert — o `ON CONFLICT DO NOTHING`
    dessas quatro nunca dispara. Um segundo `--confirmar` duplicaria `sinais`,
    dobrando o n e o PnL que decidem sobre capital real.
    """

    def test_destino_com_dados_recusa_e_nao_insere(self, migracao):
        pg, codigo = migracao(destino_ocupado={"sinais": 5339})
        assert codigo == 1
        assert not pg.linhas, "inseriu apesar de recusar"
        assert "COMMIT" not in pg.eventos

    def test_risk_state_sozinho_nao_bloqueia(self, migracao):
        """`risk_state` tem ON CONFLICT (name) DO UPDATE — reinserir e seguro,
        e bloquear por causa dela impediria toda migracao legitima."""
        pg, codigo = migracao(destino_ocupado={"risk_state": 1})
        assert codigo == 0
        assert pg.linhas

    def test_destino_vazio_migra_normalmente(self, migracao):
        pg, codigo = migracao()
        assert codigo == 0
        assert "COMMIT" in pg.eventos

    def test_forcar_passa_por_cima(self, migracao):
        pg, codigo = migracao(destino_ocupado={"sinais": 5339}, forcar=True)
        assert codigo == 0
        assert pg.linhas, "--forcar nao deixou inserir"

    def test_dry_run_nao_e_bloqueado(self, migracao):
        """Dry-run nao escreve, entao destino populado nao e impedimento — e
        justamente o comando que o operador usa para inspecionar antes."""
        pg, codigo = migracao(dry=True, destino_ocupado={"sinais": 5339})
        assert codigo == 0

    def test_so_risk_state_e_incondicionalmente_idempotente(self):
        """risk_state e a unica cuja idempotencia nao depende do destino (PK
        + DO UPDATE). As outras quatro sao CONDICIONAIS: valem apenas quando o
        indice da 004 existe no destino, verificado via pg_indexes."""
        assert mig.IDEMPOTENTES == {"risk_state"}
        assert set(mig.IDEMPOTENTES_COM_UNIQUE) == {
            "snapshots_mercado",
            "cvd_historico",
            "sinais",
            "bot_events",
        }
        assert "trades" not in mig.IDEMPOTENTES_COM_UNIQUE, (
            "trades NUNCA pode entrar: o UNIQUE dela e parcial e ~58% das "
            "linhas tem trade_id NULL"
        )

    def test_destino_COM_indices_da_004_nao_bloqueia(self, migracao):
        """A 004 aplicada no destino torna o ON CONFLICT real — reinsercao
        deixa de ser perigosa e a guarda deixa de atrapalhar."""
        pg, codigo = migracao(
            destino_ocupado={"sinais": 5339, "snapshots_mercado": 100},
            indices=set(mig.IDEMPOTENTES_COM_UNIQUE.values()),
        )
        assert codigo == 0, "bloqueou um destino que a 004 ja protege"
        assert "COMMIT" in pg.eventos

    def test_destino_antigo_SEM_indices_continua_bloqueado(self, migracao):
        """Destino criado antes da 004 nao tem os indices: reinserir la
        duplicaria como sempre. A verificacao e no catalogo, nao na lista."""
        pg, codigo = migracao(destino_ocupado={"sinais": 5339}, indices=set())
        assert codigo == 1
        assert not pg.linhas

    def test_indice_parcial_de_trades_nao_libera_trades(self, migracao):
        """trades populada bloqueia MESMO com todos os indices da 004: o
        indice dela e parcial e nao cobre trade_id NULL."""
        pg, codigo = migracao(
            destino_ocupado={"trades": 1000},
            indices=set(mig.IDEMPOTENTES_COM_UNIQUE.values()) | {"idx_trades_trade_id"},
        )
        assert codigo == 1


class TestCredencialNaoVazaNoLog:
    def test_destino_mostra_host_e_banco_sem_senha(self, monkeypatch):
        monkeypatch.setattr(mig, "DATABASE_URL", "postgresql://user:s3nh4@db.supabase.co/postgres")
        saida = mig._destino_seguro()
        assert "s3nh4" not in saida and "user" not in saida
        assert "db.supabase.co" in saida

    def test_nenhum_print_fatia_a_url_crua(self):
        """Os dois `DATABASE_URL[:40]`/`[:50]` cabiam a senha inteira."""
        import inspect

        fonte = inspect.getsource(mig)
        assert "DATABASE_URL[:" not in fonte
