"""
Testes — MOMO lab (momentum cross-sectional)
=============================================
As propriedades que importam: causalidade (nenhuma decisão usa futuro),
custos cobrados de verdade, filtro TSMOM indo a caixa, pesquisa jamais
avaliando dias do hold-out, e a trava de uso único do hold-out.
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import momo_lab as ml  # noqa: E402

DIA_MS = 86_400_000


def _closes_sinteticos(n=400, seed=7):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.03, size=(n, 3))
    closes = 100.0 * np.cumprod(1.0 + rets, axis=0)
    # série diária cruzando o início do hold-out (2025-07-22)
    ts0 = ml.HOLDOUT_INICIO_MS - (n - 100) * DIA_MS
    ts = ts0 + np.arange(n) * DIA_MS
    return ts, closes


class TestCausalidade:
    def test_posicoes_invariantes_por_prefixo(self):
        """A posição do dia t não pode mudar quando o futuro é acrescentado —
        é A prova mecânica de que não há look-ahead."""
        _, closes = _closes_sinteticos()
        _, pos_cheio = ml.simular(closes, k=7, com_filtro=True)
        _, pos_prefixo = ml.simular(closes[:300], k=7, com_filtro=True)
        assert np.array_equal(pos_prefixo, pos_cheio[: len(pos_prefixo)])

    def test_sinal_do_dia_nao_ve_o_dia_seguinte(self):
        """Choque enorme no dia t+1 não altera a escolha feita em t."""
        _, closes = _closes_sinteticos()
        alterado = closes.copy()
        alterado[-1, 2] *= 10.0  # SOL explode no último dia
        _, pos_a = ml.simular(closes, k=7, com_filtro=False)
        _, pos_b = ml.simular(alterado, k=7, com_filtro=False)
        # a última DECISÃO (t = n-2) usa closes até n-2 — inalterada
        assert pos_a[-1] == pos_b[-1]

    def test_pesquisa_nunca_avalia_dias_do_holdout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(ml, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(ml.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        r = ml.rodar_pesquisa(salvar=False)
        assert r["janela_pesquisa"][1] < ml.HOLDOUT_INICIO_MS


class TestExecucao:
    def test_custos_reduzem_o_retorno(self):
        _, closes = _closes_sinteticos()
        com, _ = ml.simular(closes, k=3, com_filtro=False, custo=0.001)
        sem, _ = ml.simular(closes, k=3, com_filtro=False, custo=0.0)
        assert float(np.sum(com)) < float(np.sum(sem))

    def test_filtro_tsmom_vai_a_caixa_em_queda_geral(self):
        n = 60
        closes = np.empty((n, 3))
        for j in range(3):
            closes[:, j] = 100.0 * (0.99 ** np.arange(n))  # tudo caindo
        rets, pos = ml.simular(closes, k=7, com_filtro=True)
        assert np.all(pos == -1), "com tudo em queda, o filtro exige caixa"
        # em caixa não há retorno de mercado (só a perna de entrada, que não houve)
        assert np.allclose(rets, 0.0)

    def test_sem_filtro_sempre_posicionado(self):
        _, closes = _closes_sinteticos()
        _, pos = ml.simular(closes, k=7, com_filtro=False)
        assert np.all(pos >= 0)

    def test_troca_de_ativo_paga_duas_pernas(self):
        # dois dias apenas: entra (1 perna) e troca (2 pernas)
        closes = np.array(
            [
                [100.0, 100.0, 100.0],
                [110.0, 100.0, 100.0],  # BTC lidera
                [110.0, 120.0, 100.0],  # ETH assume a liderança
                [110.0, 120.0, 100.0],
            ]
        )
        rets, pos = ml.simular(closes, k=1, com_filtro=False, custo=0.001)
        assert pos[0] == 0 and pos[1] == 1
        # dia da troca: retorno bruto de ETH (0) menos 2 pernas
        assert rets[1] == pytest.approx(120.0 / 120.0 - 1.0 - 0.002, abs=1e-9)


class TestReguaEDeterminismo:
    def _pesquisa(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(ml, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(ml.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        return ml.rodar_pesquisa()

    def test_familia_tem_exatamente_8_trials(self, monkeypatch, tmp_path):
        r = self._pesquisa(monkeypatch, tmp_path)
        assert len(r["combos"]) == 8
        with open(tmp_path / "momo_trials_count.json", encoding="utf-8") as f:
            assert json.load(f)["total"] == 8

    def test_deterministico(self, monkeypatch, tmp_path):
        r1 = self._pesquisa(monkeypatch, tmp_path)
        r2 = self._pesquisa(monkeypatch, tmp_path)
        assert [c["sharpe"] for c in r1["combos"]] == [c["sharpe"] for c in r2["combos"]]

    def test_sobreviver_exige_as_tres_condicoes(self, monkeypatch, tmp_path):
        r = self._pesquisa(monkeypatch, tmp_path)
        for c in r["combos"]:
            esperado = (
                c["retorno_aa"] >= ml.PISO_RETORNO_AA
                and c["sharpe"] >= ml.PISO_SHARPE
                and c["sharpe"] >= c["sharpe_bh_btc"]
            )
            assert c["sobrevive"] == esperado


class TestTravaHoldout:
    def test_recusa_sem_confirmacao(self):
        with pytest.raises(SystemExit, match="USO ÚNICO"):
            ml.avaliar_holdout(confirmo_uso_unico=False)

    def test_recusa_sem_pesquisa_previa(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        with pytest.raises(SystemExit, match="pesquisa antes"):
            ml.avaliar_holdout(confirmo_uso_unico=True)

    def test_pesquisa_fail_nao_queima_o_holdout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        (tmp_path / "momo_pesquisa.json").write_text(
            json.dumps({"escolhida_para_holdout": None}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="FAIL"):
            ml.avaliar_holdout(confirmo_uso_unico=True)
        assert not (tmp_path / "momo_holdout.json").exists()

    def test_segunda_chamada_recusada_e_registro_gravado(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        doc = tmp_path / "METODOLOGIA_MOMO.md"
        doc.write_text("# doc\n", encoding="utf-8")
        monkeypatch.setattr(ml, "DOC_METODOLOGIA", str(doc))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(ml, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(ml.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        (tmp_path / "momo_pesquisa.json").write_text(
            json.dumps(
                {
                    "snapshot": "teste",
                    "escolhida_para_holdout": {"k": 7, "filtro_tsmom": True},
                }
            ),
            encoding="utf-8",
        )
        r = ml.avaliar_holdout(confirmo_uso_unico=True)
        assert r["veredito_holdout"] in {"PASS", "FAIL"}
        assert "hold-out CONSUMIDO" in doc.read_text(encoding="utf-8")
        with pytest.raises(SystemExit, match="JÁ CONSUMIDO"):
            ml.avaliar_holdout(confirmo_uso_unico=True)

    def test_holdout_recusa_snapshot_divergente(self, monkeypatch, tmp_path):
        """O hold-out de uso único tem que rodar no MESMO substrato da
        pesquisa (achado do cético, 2026-08-14)."""
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        monkeypatch.setattr(ml.snapshot, "ler_manifest", lambda r=None: {"rotulo": "OUTRO"})
        (tmp_path / "momo_pesquisa.json").write_text(
            json.dumps(
                {
                    "snapshot": "teste",
                    "escolhida_para_holdout": {"k": 7, "filtro_tsmom": True},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="MESMO substrato"):
            ml.avaliar_holdout(confirmo_uso_unico=True)
        assert not (tmp_path / "momo_holdout.json").exists()


class TestRegressoesDoCetico:
    def test_max_drawdown_conta_o_pico_inicial(self):
        """Queda no 1º dia avaliado é drawdown de verdade: [-10%, +20%] → 10%."""
        assert ml.max_drawdown(np.array([-0.10, 0.20])) == pytest.approx(0.10)

    def test_benchmark_paga_perna_de_entrada(self, monkeypatch, tmp_path):
        """B&H de BTC paga 0,10% de entrada — uniforme com o VOLT."""
        monkeypatch.setattr(ml, "DIR_VEREDITOS", str(tmp_path))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(ml, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(ml.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        r = ml.rodar_pesquisa(salvar=False)
        fat = ml._fatia_pesquisa(ts)
        closes_p = closes[fat]
        k = r["combos"][0]["k"]
        bh_cru = closes_p[k + 1 :, 0] / closes_p[k:-1, 0] - 1.0
        assert r["combos"][0]["sharpe_bh_btc"] < round(ml.sharpe(bh_cru), 3) + 1e-9
        assert r["combos"][0]["sharpe_bh_btc"] != round(ml.sharpe(bh_cru), 3)
