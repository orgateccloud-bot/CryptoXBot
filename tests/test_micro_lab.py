"""
Testes — coletor de livro e micro_lab (frente E-11)
====================================================
O micro_lab é o instrumento de uma metodologia pré-registrada. O que estes
testes protegem não é "o IC dá o número X" — é que o instrumento não tenha os
defeitos que mataram as medições anteriores:

  - a normalização do CVD que prendia o componente em |v| <= 0,069 (morto);
  - fronteira de hold-out que se move quando a série cresce;
  - medir com metade das features e chamar pelo nome da hipótese inteira;
  - hold-out rodado duas vezes.

Tudo hermético: SQLite sintético em tmp_path, zero rede, zero banco vivo.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import coletar_book as cb
from research import micro_lab as ml

# ── OFI de Cont-Kukanov-Stoikov ────────────────────────────────


class TestOfiIncremento:
    BASE = dict(prev_bid_p=100.0, prev_bid_q=5.0, prev_ask_p=101.0, prev_ask_q=4.0)

    def test_bid_sobe_soma_a_quantidade_nova(self):
        e = cb.ofi_incremento(100.5, 7.0, 101.0, 4.0, **self.BASE)
        assert e == pytest.approx(7.0)

    def test_bid_cai_subtrai_a_quantidade_antiga(self):
        e = cb.ofi_incremento(99.5, 2.0, 101.0, 4.0, **self.BASE)
        assert e == pytest.approx(-5.0)

    def test_ask_cai_e_pressao_vendedora(self):
        e = cb.ofi_incremento(100.0, 5.0, 100.5, 6.0, **self.BASE)
        assert e == pytest.approx(-6.0)

    def test_precos_iguais_usa_a_variacao_de_tamanho(self):
        e = cb.ofi_incremento(100.0, 8.0, 101.0, 3.0, **self.BASE)
        # bid: 8-5 = +3 ; ask: -(3-4) = +1
        assert e == pytest.approx(4.0)


class TestAgregadorMinuto:
    def test_fecha_a_linha_quando_o_minuto_vira(self):
        agg = cb.AgregadorMinuto("BTCUSDT")
        t0 = 1_700_000_040_000  # dentro de um minuto
        assert agg.atualizar(t0, 100.0, 5.0, 101.0, 4.0) is None
        assert agg.atualizar(t0 + 1_000, 100.0, 6.0, 101.0, 4.0) is None
        fechada = agg.atualizar(t0 + 60_000, 100.0, 6.0, 101.0, 4.0)
        assert fechada is not None
        assert fechada["n_updates"] == 2
        # 1º update não tem anterior; 2º: bid igual -> +1, ask igual -> 0
        assert fechada["ofi"] == pytest.approx(1.0)

    def test_spread_rel_e_media_do_minuto(self):
        agg = cb.AgregadorMinuto("X")
        t0 = 1_700_000_040_000
        agg.atualizar(t0, 100.0, 1.0, 102.0, 1.0)  # spread 2/101
        agg.atualizar(t0 + 1, 100.0, 1.0, 100.5, 1.0)  # spread 0.5/100.25
        linha = agg.fechar()
        esperado = ((2 / 101.0) + (0.5 / 100.25)) / 2
        assert linha["spread_rel_medio"] == pytest.approx(esperado)

    def test_minuto_vazio_nao_produz_linha(self):
        assert cb.AgregadorMinuto("X").fechar() is None


# ── a normalização que resolve o componente morto ──────────────


class TestCvdSlopeNorm:
    def test_nao_depende_do_tamanho_da_janela(self):
        """O defeito original: |v| <= 1/std(x), que cai com n. A versão correta
        é a correlação de Pearson com o tempo — invariante ao tamanho."""
        rng = np.random.default_rng(3)
        base = np.cumsum(rng.normal(0.3, 1.0, 400))
        v50 = ml.cvd_slope_norm(base[:50])
        v200 = ml.cvd_slope_norm(base[:200])
        # mesmos dados subjacentes, janelas 4x diferentes: mesma ORDEM de
        # grandeza (o defeito antigo forcaria v200 <= 0.035 SEMPRE)
        assert abs(v200) > 0.069, "a normalizacao nova nao pode ter o teto antigo"
        assert abs(v50) > 0.069

    def test_linear_perfeito_da_um(self):
        assert ml.cvd_slope_norm(np.arange(50.0)) == pytest.approx(1.0)
        assert ml.cvd_slope_norm(-np.arange(50.0)) == pytest.approx(-1.0)

    def test_limitado_a_um_em_modulo(self):
        rng = np.random.default_rng(9)
        for _ in range(20):
            v = ml.cvd_slope_norm(np.cumsum(rng.normal(0, 1, 80)))
            assert -1.0 <= v <= 1.0

    def test_serie_constante_da_zero(self):
        assert ml.cvd_slope_norm(np.full(50, 7.0)) == 0.0


class TestFeaturesPuras:
    def test_desequilibrio_agressor_centrado_em_zero(self):
        assert ml.desequilibrio_agressor(5.0, 10.0) == pytest.approx(0.0)
        assert ml.desequilibrio_agressor(10.0, 10.0) == pytest.approx(0.5)
        assert ml.desequilibrio_agressor(0.0, 0.0) == 0.0

    def test_intensidade_sem_base_da_zero(self):
        assert ml.intensidade_trades(10.0, 0.0) == 0.0
        assert ml.intensidade_trades(10.0, 5.0) == pytest.approx(2.0)


# ── partição por DATA fixa ─────────────────────────────────────


class TestParticao:
    def test_fronteira_e_2026_12_01_utc(self):
        antes = ml.HOLDOUT_INICIO_MS - 1
        depois = ml.HOLDOUT_INICIO_MS
        pesq, hold = ml.particionar(np.array([antes, depois]))
        assert pesq.tolist() == [True, False]
        assert hold.tolist() == [False, True]

    def test_fronteira_nao_depende_do_tamanho_da_serie(self):
        """O defeito de I-11b: fração do tamanho. Aqui, dobrar a série não
        move um único rótulo."""
        ts = np.arange(ml.HOLDOUT_INICIO_MS - 5 * 3_600_000, ml.HOLDOUT_INICIO_MS, 3_600_000)
        p1, _ = ml.particionar(ts)
        p2, _ = ml.particionar(np.concatenate([ts, ts + 10 * 3_600_000]))
        assert p2[: len(ts)].tolist() == p1.tolist()


# ── labels líquidos ────────────────────────────────────────────


class TestLabels:
    def test_custo_e_subtraido(self):
        px = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])
        labels, _ = ml.labels_liquidos(px, custo=0.002)
        assert labels[1][0] == pytest.approx(0.01 - 0.002)

    def test_sem_futuro_e_invalido(self):
        px = np.linspace(100, 110, 12)
        _, validos = ml.labels_liquidos(px, custo=0.0)
        assert not validos[8][-1]
        assert validos[8][0]


# ── fail-closed e barras ───────────────────────────────────────


def _db_tape(caminho, symbol, inicio, minutos, com_pnl=0):
    con = sqlite3.connect(caminho)
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,"
        " symbol TEXT, preco REAL, volume_btc REAL, direcao TEXT)"
    )
    con.execute(
        "CREATE TABLE sinais (id INTEGER PRIMARY KEY AUTOINCREMENT, preco REAL,"
        " preco_saida REAL, pnl_pct REAL, pnl_usdt REAL)"
    )
    for k in range(com_pnl):
        con.execute(
            "INSERT INTO sinais (preco, preco_saida, pnl_pct, pnl_usdt) VALUES (100,101,1,1)"
        )
    rng = np.random.default_rng(11)
    linhas = []
    for i in range(minutos):
        ts = (inicio + timedelta(minutes=i)).isoformat()
        preco = 60000 + float(rng.normal(0, 30)) + i
        for _ in range(3):
            linhas.append((ts, symbol, preco, 0.05, "COMPRA" if rng.random() < 0.55 else "VENDA"))
    con.executemany(
        "INSERT INTO trades (timestamp, symbol, preco, volume_btc, direcao) VALUES (?,?,?,?,?)",
        linhas,
    )
    con.commit()
    con.close()


def _db_book(caminho, symbol, inicio_ms, minutos):
    con = cb.conectar(caminho)
    rng = np.random.default_rng(12)
    for i in range(minutos):
        cb.salvar_minuto(
            con,
            {
                "symbol": symbol,
                "minuto_ms": inicio_ms + i * 60_000,
                "ofi": float(rng.normal(0, 3)),
                "spread_rel_medio": 1e-4,
                "mid_fim": 60000.0,
                "bid_qty_fim": 5.0,
                "ask_qty_fim": 5.0,
                "n_updates": 40,
            },
        )
    con.close()


class TestFailClosed:
    def test_sem_livro_aborta_sem_medir_nada(self, tmp_path, monkeypatch):
        """3 das 5 features sem fonte = outra hipótese. Aborta, e diz que a
        metodologia continua virgem."""
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path / "vereditos"))
        tape = str(tmp_path / "tape.db")
        inicio = datetime(2026, 8, 1, 0, 0, 0)
        _db_tape(tape, "BTCUSDT", inicio, minutos=60 * 24)
        with pytest.raises(SystemExit, match="Coleta insuficiente"):
            ml.rodar_pesquisa("BTCUSDT", tape_db=tape, book_db=str(tmp_path / "nao_existe.db"))
        assert not os.path.exists(tmp_path / "vereditos"), "gravou veredito sem medir"

    def test_com_livro_completo_a_pesquisa_roda(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path / "vereditos"))
        monkeypatch.setattr(ml, "MIN_BARRAS_PESQUISA", 24)
        tape, book = str(tmp_path / "tape.db"), str(tmp_path / "book.db")
        inicio = datetime(2026, 8, 1, 0, 0, 0)
        minutos = 60 * 40
        _db_tape(tape, "BTCUSDT", inicio, minutos, com_pnl=2)
        _db_book(book, "BTCUSDT", int(inicio.timestamp() * 1000), minutos)

        r = ml.rodar_pesquisa("BTCUSDT", tape_db=tape, book_db=book)
        assert r["fase"] == "PESQUISA"
        assert r["n_barras"] >= 24
        assert r["melhor_feature"] in ml.FEATURES
        assert 0.0 <= r["p_valor"] <= 1.0
        assert r["custo_roundtrip"] >= 0.002, "custo nao pode ficar abaixo das duas pernas de taxa"
        assert os.path.exists(tmp_path / "vereditos" / "microestrutura_pesquisa_BTCUSDT.json")

    def test_barras_sem_livro_sao_descartadas_e_contadas(self, tmp_path):
        tape = str(tmp_path / "tape.db")
        book = str(tmp_path / "book.db")
        inicio = datetime(2026, 8, 1, 0, 0, 0)
        _db_tape(tape, "BTCUSDT", inicio, minutos=60 * 6)
        # livro cobre so as 3 primeiras horas
        _db_book(book, "BTCUSDT", int(inicio.timestamp() * 1000), 60 * 3)
        barras = ml.construir_barras(
            ml.carregar_tape_minuto("BTCUSDT", tape), ml.carregar_book_minuto("BTCUSDT", book)
        )
        assert barras["descartadas_sem_book"] >= 2
        assert len(barras["ts"]) <= 3


class TestHoldoutUsoUnico:
    def _preparar(self, tmp_path, monkeypatch):
        vered = tmp_path / "vereditos"
        vered.mkdir()
        doc = tmp_path / "METODOLOGIA.md"
        doc.write_text("## Consumos do hold-out\n", encoding="utf-8")
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(vered))
        monkeypatch.setattr(ml, "DOC_METODOLOGIA", str(doc))
        return vered, doc

    def test_sem_confirmacao_recusa(self, tmp_path, monkeypatch):
        self._preparar(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="USO ÚNICO"):
            ml.avaliar_holdout("BTCUSDT", confirmo_uso_unico=False)

    def test_segunda_execucao_recusa(self, tmp_path, monkeypatch):
        vered, _ = self._preparar(tmp_path, monkeypatch)
        (vered / "microestrutura_holdout_BTCUSDT.json").write_text(
            json.dumps({"consumido_em": "2026-12-15T10:00:00"}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="JÁ FOI CONSUMIDO"):
            ml.avaliar_holdout("BTCUSDT", confirmo_uso_unico=True)

    def test_sem_pesquisa_registrada_recusa(self, tmp_path, monkeypatch):
        self._preparar(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="pesquisa registrada"):
            ml.avaliar_holdout("BTCUSDT", confirmo_uso_unico=True)

    def test_consumo_e_registrado_no_lock_e_no_doc(self, tmp_path, monkeypatch):
        _, doc = self._preparar(tmp_path, monkeypatch)
        resultado = {
            "consumido_em": "2026-12-15T10:00:00",
            "veredito": "FAIL",
        }
        ml.registrar_consumo_holdout("BTCUSDT", resultado)
        assert ml.holdout_ja_consumido("BTCUSDT") == "2026-12-15T10:00:00"
        assert "FAIL" in doc.read_text(encoding="utf-8")


class TestCarteira:
    def test_sinal_perfeito_da_sharpe_positivo(self):
        rng = np.random.default_rng(5)
        ret = rng.normal(0.001, 0.01, 500)
        feature = ret + rng.normal(0, 0.001, 500)  # quase o proprio retorno
        assert ml.sharpe_carteira(feature, ret, custo=0.0) > 1.0

    def test_custo_reduz_o_sharpe(self):
        rng = np.random.default_rng(6)
        ret = rng.normal(0.001, 0.01, 500)
        feature = rng.normal(0, 1, 500)  # ruido: troca de lado toda hora
        bruto = ml.sharpe_carteira(feature, ret, custo=0.0)
        liquido = ml.sharpe_carteira(feature, ret, custo=0.002)
        assert liquido < bruto

    def test_serie_curta_da_zero(self):
        assert ml.sharpe_carteira(np.ones(10), np.ones(10), 0.0) == 0.0
