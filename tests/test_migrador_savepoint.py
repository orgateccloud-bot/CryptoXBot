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


class FakePg:
    def __init__(self, falhar_em=()):
        self.falhar_em = set(falhar_em)
        self.eventos = []
        self.linhas = []
        self.abortada = False

    def transaction(self):
        return _Savepoint(self)

    def execute(self, sql, params=None):
        # depois de um erro sem savepoint, um Postgres real recusa TUDO.
        if self.abortada:
            raise RuntimeError("current transaction is aborted")
        for t in self.falhar_em:
            if f"INTO {t}" in sql:
                self.abortada = True
                raise RuntimeError(f"erro proposital em {t}")
        self.linhas.append(sql)

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

    def _run(falhar_em=(), dry=False):
        pg = FakePg(falhar_em)
        monkeypatch.setattr(mig, "DATABASE_URL", "postgresql://u:senha@host/banco")
        monkeypatch.setattr(mig, "_pg_conn", lambda: pg)
        monkeypatch.setattr(mig, "_sqlite_conn", lambda: type("C", (), {"close": lambda s: None})())
        monkeypatch.setattr(mig, "_sqlite_rows", lambda c, t: [{"timestamp": "2026-08-11"}])
        codigo = 0
        try:
            mig.cmd_migrate(dry=dry)
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
