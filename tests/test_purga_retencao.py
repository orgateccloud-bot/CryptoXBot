"""
Testes — retenção de 90 dias com arquivamento (frente I-13)
============================================================
`scripts/purgar_retencao.py` é o segundo script destrutivo do repositório. O
primeiro (`purgar_fixtures_producao.py`) já mostrou o custo de errar: o
critério `order_id LIKE 'SIM-%'` casava com TODA posição legítima de paper, e
`--confirmar` teria apagado o estado inteiro que a Etapa 2 do gate depende.

Aqui o alvo são 1,8 milhão de linhas de tape bruto da Binance — dado que não
se recompra. A propriedade que estes testes protegem não é "apagou certo", é
**não apaga nada que não esteja arquivado e verificado**:

  - o dump é relido do disco e conferido ANTES do DELETE;
  - se a releitura não bater, o DELETE não roda (test_dump_truncado_*);
  - o que foi arquivado volta idêntico (test_restaurar_*);
  - Postgres compartilhado exige flag extra (test_postgres_*).

Tudo hermético: SQLite em tmp_path, nenhuma rede, nenhum banco de produção.
"""

import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import purgar_retencao as pr

ANTIGO = (datetime.now() - timedelta(days=200)).isoformat()
RECENTE = (datetime.now() - timedelta(days=3)).isoformat()

# quantas linhas de cada lado do corte cada tabela recebe
SEMENTE = {
    "trades": (7, 4),
    "snapshots_mercado": (5, 2),
    "cvd_historico": (3, 6),
}


def _criar_schema(db: str) -> None:
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            symbol TEXT, preco REAL, volume_btc REAL, volume_usdt REAL,
            direcao TEXT, eh_baleia INTEGER DEFAULT 0, trade_id INTEGER
        );
        CREATE UNIQUE INDEX idx_trades_trade_id ON trades(trade_id)
            WHERE trade_id IS NOT NULL;
        CREATE TABLE snapshots_mercado (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            symbol TEXT, preco REAL, rsi_1h REAL, tendencia TEXT
        );
        CREATE TABLE cvd_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            symbol TEXT, cvd REAL NOT NULL, compras_btc REAL NOT NULL,
            vendas_btc REAL NOT NULL
        );
        """)
    con.commit()
    con.close()


def _semear(db: str) -> None:
    con = sqlite3.connect(db)
    seq = 0
    for i in range(SEMENTE["trades"][0]):
        seq += 1
        con.execute(
            "INSERT INTO trades (timestamp, symbol, preco, volume_btc, volume_usdt,"
            " direcao, eh_baleia, trade_id) VALUES (?,?,?,?,?,?,?,?)",
            (ANTIGO, "BTCUSDT", 60000.0 + i, 0.5, 30000.0, "COMPRA", 0, seq),
        )
    for i in range(SEMENTE["trades"][1]):
        seq += 1
        con.execute(
            "INSERT INTO trades (timestamp, symbol, preco, volume_btc, volume_usdt,"
            " direcao, eh_baleia, trade_id) VALUES (?,?,?,?,?,?,?,?)",
            (RECENTE, "BTCUSDT", 70000.0 + i, 0.5, 35000.0, "VENDA", 1, seq),
        )
    for ts, n in (
        (ANTIGO, SEMENTE["snapshots_mercado"][0]),
        (RECENTE, SEMENTE["snapshots_mercado"][1]),
    ):
        for i in range(n):
            con.execute(
                "INSERT INTO snapshots_mercado (timestamp, symbol, preco, rsi_1h, tendencia)"
                " VALUES (?,?,?,?,?)",
                (ts, "BTCUSDT", 60000.0 + i, 55.5, "ALTA"),
            )
    for ts, n in ((ANTIGO, SEMENTE["cvd_historico"][0]), (RECENTE, SEMENTE["cvd_historico"][1])):
        for i in range(n):
            con.execute(
                "INSERT INTO cvd_historico (timestamp, symbol, cvd, compras_btc, vendas_btc)"
                " VALUES (?,?,?,?,?)",
                (ts, "BTCUSDT", 1.5 + i, 10.0, 8.5),
            )
    con.commit()
    con.close()


def _contar(db: str) -> dict[str, int]:
    con = sqlite3.connect(db)
    r = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SEMENTE}
    con.close()
    return r


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """SQLite semeado + runtime apontado para ele. Devolve (caminho, dir_dumps)."""
    db = str(tmp_path / "teste.db")
    _criar_schema(db)
    _semear(db)

    import config.runtime_settings as rs

    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    monkeypatch.setattr(rs, "DATABASE_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(rs, "DATABASE_URL", "", raising=False)
    return db, str(tmp_path / "dumps")


def _rodar(banco, *args) -> int:
    db, dumps = banco
    return pr.main(["--saida", dumps, *args])


# ── a escada: listar → dry-run → confirmar ─────────────────────


class TestModosNaoDestrutivos:
    def test_listar_nao_apaga_nem_escreve(self, banco):
        db, dumps = banco
        antes = _contar(db)
        assert _rodar(banco, "--listar") == 0
        assert _contar(db) == antes
        assert not os.path.exists(dumps), "--listar criou arquivo"

    def test_padrao_sem_flag_e_listar(self, banco):
        db, dumps = banco
        antes = _contar(db)
        assert _rodar(banco) == 0
        assert _contar(db) == antes
        assert not os.path.exists(dumps)

    def test_dry_run_nao_apaga_nem_escreve(self, banco):
        db, dumps = banco
        antes = _contar(db)
        assert _rodar(banco, "--dry-run") == 0
        assert _contar(db) == antes
        assert not os.path.exists(dumps), "--dry-run gravou dump"


# ── o caminho destrutivo ───────────────────────────────────────


class TestPurga:
    def test_apaga_so_o_que_passou_de_90_dias(self, banco):
        db, _ = banco
        assert _rodar(banco, "--confirmar") == 0
        restante = _contar(db)
        assert restante["trades"] == SEMENTE["trades"][1]
        assert restante["snapshots_mercado"] == SEMENTE["snapshots_mercado"][1]

    def test_o_que_sobrou_e_exatamente_o_recente(self, banco):
        db, _ = banco
        _rodar(banco, "--confirmar")
        con = sqlite3.connect(db)
        tss = [r[0] for r in con.execute("SELECT timestamp FROM trades")]
        con.close()
        assert tss and all(t == RECENTE for t in tss)

    def test_cvd_historico_vai_inteira(self, banco):
        """Política do operador: cvd_historico não tem leitor nenhum, sai toda
        — inclusive as linhas recentes."""
        db, _ = banco
        _rodar(banco, "--confirmar")
        assert _contar(db)["cvd_historico"] == 0

    def test_dump_tem_uma_linha_por_registro_apagado(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = [f for f in os.listdir(dumps) if f.startswith("trades-") and f.endswith(".gz")]
        assert len(arq) == 1
        with gzip.open(os.path.join(dumps, arq[0]), "rt", encoding="utf-8") as gz:
            linhas = [json.loads(x) for x in gz if x.strip()]
        assert len(linhas) == SEMENTE["trades"][0]
        assert all(x["timestamp"] == ANTIGO for x in linhas)
        assert linhas[0]["preco"] == 60000.0

    def test_manifesto_confere_com_o_arquivo(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = next(f for f in os.listdir(dumps) if f.startswith("trades-") and f.endswith(".gz"))
        caminho = os.path.join(dumps, arq)
        with open(caminho + ".manifest.json", encoding="utf-8") as fh:
            man = json.load(fh)
        sha, n = pr._sha256_e_linhas(caminho)
        assert man["sha256"] == sha
        assert man["linhas"] == n == SEMENTE["trades"][0]
        assert man["tabela"] == "trades"

    def test_rodar_duas_vezes_nao_apaga_o_recente(self, banco):
        db, _ = banco
        _rodar(banco, "--confirmar")
        depois_1 = _contar(db)
        _rodar(banco, "--confirmar")
        assert _contar(db) == depois_1

    def test_dias_override_muda_o_corte(self, banco):
        """--dias 1 leva tudo, inclusive o que tem 3 dias."""
        db, _ = banco
        _rodar(banco, "--confirmar", "--dias", "1")
        assert _contar(db) == {t: 0 for t in SEMENTE}

    def test_dias_negativo_e_recusado(self, banco):
        db, _ = banco
        antes = _contar(db)
        assert _rodar(banco, "--confirmar", "--dias", "-5") == 2
        assert _contar(db) == antes

    def test_vacuum_roda_mesmo_sem_nada_a_purgar(self, banco, monkeypatch, capsys):
        """Fluxo em duas etapas real: purga com worker vivo, VACUUM depois com
        ele parado. O early-return de 'Nada a purgar' tornava a segunda visita
        um no-op silencioso — encontrado em produção em 2026-08-12."""
        db, _ = banco
        _rodar(banco, "--confirmar")
        capsys.readouterr()

        monkeypatch.setattr(pr, "_worker_ativo", lambda conn, backend: False)
        tamanho_antes = os.path.getsize(db)
        assert _rodar(banco, "--confirmar", "--vacuum") == 0
        saida = capsys.readouterr().out
        assert "VACUUM" in saida, "nao chegou ao vacuum na segunda visita"
        assert "Nada a purgar — seguindo direto" in saida
        assert os.path.getsize(db) <= tamanho_antes


# ── a garantia que justifica o script existir ──────────────────


class TestDumpVerificadoAntesDoDelete:
    def test_dump_truncado_aborta_sem_apagar_nada(self, banco, monkeypatch):
        """Se a releitura do .gz não bater com o que foi escrito, o DELETE não
        pode rodar. Simula disco cheio / processo morto no meio da escrita."""
        db, _ = banco
        antes = _contar(db)

        real = pr._sha256_e_linhas
        monkeypatch.setattr(pr, "_sha256_e_linhas", lambda c: (real(c)[0], 0))

        with pytest.raises(RuntimeError, match="inconsistente"):
            _rodar(banco, "--confirmar")
        assert _contar(db) == antes, "apagou apesar do dump inconsistente"

    def test_falha_no_meio_preserva_as_tabelas_seguintes(self, banco, monkeypatch):
        """`trades` é a primeira do dicionário: se ela abortar, as outras duas
        continuam intactas em vez de irem junto."""
        db, _ = banco
        antes = _contar(db)

        def explode(conn, tabela, corte, backend, dir_dumps, carimbo):
            raise RuntimeError("dump de trades inconsistente: disco cheio")

        monkeypatch.setattr(pr, "_arquivar", explode)
        with pytest.raises(RuntimeError):
            _rodar(banco, "--confirmar")
        assert _contar(db) == antes


# ── reversibilidade (protocolo @Zeta) ──────────────────────────


class TestRestauracao:
    def _dump_de(self, dumps: str, tabela: str) -> str:
        nome = next(
            f for f in os.listdir(dumps) if f.startswith(f"{tabela}-") and f.endswith(".gz")
        )
        return os.path.join(dumps, nome)

    def test_restaurar_devolve_as_linhas_identicas(self, banco):
        db, dumps = banco
        con = sqlite3.connect(db)
        original = con.execute("SELECT * FROM trades ORDER BY id").fetchall()
        con.close()

        _rodar(banco, "--confirmar")
        assert _contar(db)["trades"] == SEMENTE["trades"][1]

        assert _rodar(banco, "--restaurar", self._dump_de(dumps, "trades"), "--confirmar") == 0

        con = sqlite3.connect(db)
        depois = con.execute("SELECT * FROM trades ORDER BY id").fetchall()
        con.close()
        assert depois == original, "a restauracao nao devolveu as linhas originais"

    def test_restaurar_e_idempotente(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = self._dump_de(dumps, "trades")
        _rodar(banco, "--restaurar", arq, "--confirmar")
        uma_vez = _contar(db)
        _rodar(banco, "--restaurar", arq, "--confirmar")
        assert _contar(db) == uma_vez

    def test_coluna_forjada_no_dump_e_recusada(self, banco):
        """As chaves do JSON viram nomes de coluna no INSERT. Um .jsonl.gz
        forjado com uma chave que fecha o parenteses emendaria SQL arbitrario
        — o dump vem do disco, nao deste script. Conferir contra o schema
        real fecha isso antes de montar a query."""
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = self._dump_de(dumps, "trades")

        veneno = "id) VALUES (999); DROP TABLE trades; --"
        with gzip.open(arq, "wt", encoding="utf-8") as gz:
            gz.write(json.dumps({"id": 1, "timestamp": ANTIGO, veneno: "x"}) + "\n")
        sha, n = pr._sha256_e_linhas(arq)
        with open(arq + ".manifest.json", encoding="utf-8") as fh:
            man = json.load(fh)
        man.update(sha256=sha, linhas=n)
        with open(arq + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(man, fh)

        with pytest.raises(RuntimeError, match="colunas que nao existem"):
            _rodar(banco, "--restaurar", arq, "--confirmar")

        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0
        con.close()

    def test_restaurar_sem_confirmar_nao_insere(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        antes = _contar(db)
        assert _rodar(banco, "--restaurar", self._dump_de(dumps, "trades")) == 0
        assert _contar(db) == antes

    def test_sha256_diferente_recusa_a_restauracao(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = self._dump_de(dumps, "trades")
        with open(arq + ".manifest.json", encoding="utf-8") as fh:
            man = json.load(fh)
        man["sha256"] = "0" * 64
        with open(arq + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(man, fh)

        antes = _contar(db)
        assert _rodar(banco, "--restaurar", arq, "--confirmar") == 2
        assert _contar(db) == antes

    def test_manifesto_ausente_recusa(self, banco):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = self._dump_de(dumps, "trades")
        os.remove(arq + ".manifest.json")
        assert _rodar(banco, "--restaurar", arq, "--confirmar") == 2

    def test_arquivo_inexistente_recusa(self, banco):
        assert _rodar(banco, "--restaurar", "nao/existe.jsonl.gz", "--confirmar") == 2


# ── guarda do banco compartilhado ──────────────────────────────


class TestGuardaPostgres:
    def test_confirmar_sem_flag_recusa_e_nao_conecta(self, banco, monkeypatch):
        db, _ = banco
        monkeypatch.setattr(pr, "_backend", lambda: "postgres")

        def nunca(*a, **k):
            raise AssertionError("tentou conectar no Postgres sem --producao-postgres")

        monkeypatch.setattr(pr, "_conectar", nunca)
        assert _rodar(banco, "--confirmar") == 2
        assert _contar(db) == _contar(db)

    def test_restaurar_sem_flag_recusa(self, banco, monkeypatch):
        db, dumps = banco
        _rodar(banco, "--confirmar")
        arq = next(
            os.path.join(dumps, f)
            for f in os.listdir(dumps)
            if f.startswith("trades-") and f.endswith(".gz")
        )
        monkeypatch.setattr(pr, "_backend", lambda: "postgres")
        monkeypatch.setattr(
            pr, "_conectar", lambda b: (_ for _ in ()).throw(AssertionError("conectou"))
        )
        assert _rodar(banco, "--restaurar", arq, "--confirmar") == 2


# ── detalhes que já morderam antes ─────────────────────────────


class TestCorteRespeitaOBackend:
    def test_sqlite_recebe_iso_ingenuo(self):
        """SQLite guarda `datetime.now().isoformat()` — hora local, sem fuso.
        Comparar com um datetime UTC-aware deslocaria o corte em horas."""
        corte = pr._corte(90, "sqlite")
        assert isinstance(corte, str)
        assert datetime.fromisoformat(corte).tzinfo is None

    def test_postgres_recebe_datetime_com_fuso(self):
        corte = pr._corte(90, "postgres")
        assert isinstance(corte, datetime)
        assert corte.tzinfo is not None

    def test_politica_none_corta_no_agora(self):
        antes = datetime.now()
        corte = datetime.fromisoformat(pr._corte(None, "sqlite"))
        assert antes <= corte <= datetime.now()


class TestLoteNaoPerdeLinhas:
    def test_delete_em_lotes_remove_tudo(self, banco, monkeypatch):
        """O DELETE é fatiado para não segurar o lock do SQLite. Um lote menor
        que o total é o caso em que um `while` mal fechado para cedo."""
        db, _ = banco
        monkeypatch.setattr(pr, "LOTE", 2)
        _rodar(banco, "--confirmar")
        assert _contar(db)["trades"] == SEMENTE["trades"][1]
