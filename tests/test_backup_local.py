"""
Testes — backup local (decisão local-only de 2026-08-13)
=========================================================
Com o Supabase fora do caminho, este script é a ÚNICA proteção contra a morte
do disco. As propriedades que importam:

  - a cópia é consistente mesmo com escritor concorrente (API de backup);
  - cópia que não passa no quick_check NÃO vira backup — remove e reporta;
  - a rotação só apaga o que o próprio script criou (padrão de nome exato);
  - .env nunca entra no backup.
"""

import gzip
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import backup_local as bl


def _db_com_dados(caminho, linhas=50):
    con = sqlite3.connect(caminho)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"linha-{i}",) for i in range(linhas)])
    con.commit()
    return con


def _preparar(tmp_path, monkeypatch):
    raiz = tmp_path / "repo"
    (raiz / "data").mkdir(parents=True)
    (raiz / "_legado" / "dumps").mkdir(parents=True)
    (raiz / "_legado" / "dumps" / "trades-ate-x.jsonl.gz").write_bytes(b"conteudo")
    (raiz / ".env").write_text("SEGREDO=nao-copiar", encoding="utf-8")
    monkeypatch.setattr(bl, "RAIZ", str(raiz))
    monkeypatch.setattr(bl, "DIR_DUMPS", str(raiz / "_legado" / "dumps"))
    return raiz


class TestBackup:
    def test_copia_verifica_e_comprime(self, tmp_path, monkeypatch):
        raiz = _preparar(tmp_path, monkeypatch)
        _db_com_dados(str(raiz / "data" / "btc_data.db")).close()
        _db_com_dados(str(raiz / "data" / "book_btc.db"), 10).close()
        destino = str(tmp_path / "bkp")

        r = bl.executar_backup(destino, manter=7)
        assert not r["erros"]
        nomes = {i["item"] for i in r["itens"]}
        assert nomes == {"data/btc_data.db", "data/book_btc.db", "_legado/dumps"}
        # e o conteudo sobrevive a ida e volta
        gz = next(i["arquivo"] for i in r["itens"] if i["item"] == "data/btc_data.db")
        restaurado = str(tmp_path / "restaurado.db")
        with gzip.open(gz, "rb") as f, open(restaurado, "wb") as out:
            out.write(f.read())
        con = sqlite3.connect(restaurado)
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
        con.close()

    def test_snapshot_com_escritor_vivo_e_consistente(self, tmp_path, monkeypatch):
        """A razao de usar a API de backup: transacao aberta no meio da copia
        nao pode produzir pagina rasgada nem dado meio-commitado."""
        raiz = _preparar(tmp_path, monkeypatch)
        escritor = _db_com_dados(str(raiz / "data" / "btc_data.db"))
        _db_com_dados(str(raiz / "data" / "book_btc.db"), 5).close()
        escritor.execute("BEGIN")
        escritor.execute("INSERT INTO t (v) VALUES ('nao-commitada')")

        r = bl.executar_backup(str(tmp_path / "bkp"), manter=7)
        escritor.rollback()
        escritor.close()
        assert not r["erros"]
        gz = next(i["arquivo"] for i in r["itens"] if i["item"] == "data/btc_data.db")
        alvo = str(tmp_path / "check.db")
        with gzip.open(gz, "rb") as f, open(alvo, "wb") as out:
            out.write(f.read())
        con = sqlite3.connect(alvo)
        vs = [v for (v,) in con.execute("SELECT v FROM t")]
        con.close()
        assert "nao-commitada" not in vs, "backup capturou transacao nao-commitada"
        assert len(vs) == 50

    def test_env_nunca_entra(self, tmp_path, monkeypatch):
        raiz = _preparar(tmp_path, monkeypatch)
        _db_com_dados(str(raiz / "data" / "btc_data.db")).close()
        _db_com_dados(str(raiz / "data" / "book_btc.db"), 5).close()
        destino = str(tmp_path / "bkp")
        r = bl.executar_backup(destino, manter=7)
        arquivos = [f for _, _, fs in os.walk(r["pasta"]) for f in fs]
        assert ".env" not in arquivos
        conteudos = b""
        for raiz_, _, fs in os.walk(r["pasta"]):
            for f in fs:
                if f.endswith(".gz"):
                    continue
                conteudos += open(os.path.join(raiz_, f), "rb").read()
        assert b"SEGREDO" not in conteudos

    def test_banco_ausente_e_reportado_nao_engolido(self, tmp_path, monkeypatch):
        raiz = _preparar(tmp_path, monkeypatch)
        _db_com_dados(str(raiz / "data" / "btc_data.db")).close()
        # book_btc.db nao existe
        r = bl.executar_backup(str(tmp_path / "bkp"), manter=7)
        assert any("book_btc" in e for e in r["erros"])


class TestRotacao:
    def test_mantem_os_n_mais_recentes(self, tmp_path):
        destino = tmp_path / "bkp"
        destino.mkdir()
        for i in range(10):
            (destino / f"backup-2026081{i}T000000").mkdir()
        removidos = bl._rotacionar(str(destino), manter=3)
        assert len(removidos) == 7
        restantes = sorted(os.listdir(destino))
        assert restantes == [
            "backup-20260817T000000",
            "backup-20260818T000000",
            "backup-20260819T000000",
        ]

    def test_nao_toca_no_que_nao_criou(self, tmp_path):
        """A guarda que separa 'rotacao' de 'apagar a pasta do usuario'."""
        destino = tmp_path / "bkp"
        destino.mkdir()
        (destino / "backup-20260801T000000").mkdir()
        (destino / "backup-20260802T000000").mkdir()
        (destino / "minhas-fotos").mkdir()
        (destino / "backup-manual").mkdir()
        (destino / "backup-20260803T000000-editado").mkdir()
        removidos = bl._rotacionar(str(destino), manter=1)
        assert removidos == ["backup-20260801T000000"]
        assert (destino / "minhas-fotos").exists()
        assert (destino / "backup-manual").exists()
        assert (destino / "backup-20260803T000000-editado").exists()

    def test_manter_zero_nao_apaga_nada(self, tmp_path):
        destino = tmp_path / "bkp"
        destino.mkdir()
        (destino / "backup-20260801T000000").mkdir()
        assert bl._rotacionar(str(destino), manter=0) == []


class TestQuickCheckReprovaCopiaCorrompida:
    def test_copia_corrompida_nao_vira_backup(self, tmp_path, monkeypatch):
        raiz = _preparar(tmp_path, monkeypatch)
        _db_com_dados(str(raiz / "data" / "btc_data.db")).close()
        _db_com_dados(str(raiz / "data" / "book_btc.db"), 5).close()
        monkeypatch.setattr(bl, "_quick_check", lambda c: False)

        r = bl.executar_backup(str(tmp_path / "bkp"), manter=7)
        assert len(r["erros"]) == 2
        assert all("quick_check FALHOU" in e for e in r["erros"])
        sobras = [f for _, _, fs in os.walk(r["pasta"]) for f in fs if f.endswith(".db")]
        assert not sobras, "deixou copia nao-verificada para tras"
