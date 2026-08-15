"""
Testes — VOLT lab (vol-targeting spot)
=======================================
Propriedades: teto de exposição 1,0 (spot puro, jamais alavanca),
causalidade da vol realizada (prefixo-invariante), custo de turnover
cobrado, régua dos ≥2/3 ativos, trava de uso único do hold-out.
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import volt_lab as vl  # noqa: E402

DIA_MS = 86_400_000


def _closes_sinteticos(n=400, seed=11):
    rng = np.random.default_rng(seed)
    # regime de vol variável: metade calmo, metade turbulento
    vol = np.where(np.arange(n)[:, None] < n // 2, 0.015, 0.06)
    rets = rng.normal(0.0004, 1.0, size=(n, 3)) * vol
    closes = 100.0 * np.cumprod(1.0 + rets, axis=0)
    ts0 = vl.HOLDOUT_INICIO_MS - (n - 100) * DIA_MS
    ts = ts0 + np.arange(n) * DIA_MS
    return ts, closes


class TestExposicao:
    def test_teto_de_exposicao_e_um(self):
        _, closes = _closes_sinteticos()
        _, _, exps = vl.simular_vt(closes[:, 0], w=10, sigma_alvo=0.60)
        assert float(np.max(exps)) <= 1.0 + 1e-12, "spot puro jamais alavanca"

    def test_vol_alta_derruba_exposicao(self):
        _, closes = _closes_sinteticos()
        _, _, exps = vl.simular_vt(closes[:, 0], w=10, sigma_alvo=0.40)
        n = len(exps)
        calmo = float(np.mean(exps[: n // 3]))
        turbulento = float(np.mean(exps[-n // 3 :]))
        assert turbulento < calmo, "no regime turbulento a exposição tem que cair"

    def test_exposicao_invariante_por_prefixo(self):
        """A exposição do dia t não muda quando o futuro é acrescentado."""
        _, closes = _closes_sinteticos()
        _, _, cheio = vl.simular_vt(closes[:, 1], w=20, sigma_alvo=0.40)
        _, _, prefixo = vl.simular_vt(closes[:300, 1], w=20, sigma_alvo=0.40)
        assert np.allclose(prefixo, cheio[: len(prefixo)])


class TestCustosERegua:
    def test_turnover_custa(self):
        _, closes = _closes_sinteticos()
        com, _, _ = vl.simular_vt(closes[:, 0], w=10, sigma_alvo=0.40, custo=0.001)
        sem, _, _ = vl.simular_vt(closes[:, 0], w=10, sigma_alvo=0.40, custo=0.0)
        assert float(np.sum(com)) < float(np.sum(sem))

    def test_janelas_vt_e_bh_sao_identicas(self):
        _, closes = _closes_sinteticos()
        rets_vt, rets_bh, exps = vl.simular_vt(closes[:, 2], w=10, sigma_alvo=0.40)
        assert len(rets_vt) == len(rets_bh) == len(exps)

    def _pesquisa(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vl, "DIR_VEREDITOS", str(tmp_path))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(vl, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(vl.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        return vl.rodar_pesquisa()

    def test_familia_tem_exatamente_6_trials(self, monkeypatch, tmp_path):
        r = self._pesquisa(monkeypatch, tmp_path)
        assert len(r["combos"]) == 6
        with open(tmp_path / "volt_trials_count.json", encoding="utf-8") as f:
            assert json.load(f)["total"] == 6

    def test_sobreviver_exige_dois_de_tres_ativos(self, monkeypatch, tmp_path):
        r = self._pesquisa(monkeypatch, tmp_path)
        for c in r["combos"]:
            assert c["sobrevive"] == (c["ativos_aprovados"] >= vl.MIN_ATIVOS)

    def test_pesquisa_nunca_avalia_dias_do_holdout(self, monkeypatch, tmp_path):
        r = self._pesquisa(monkeypatch, tmp_path)
        assert r["janela_pesquisa"][1] < vl.HOLDOUT_INICIO_MS


class TestTravaHoldout:
    def test_recusa_sem_confirmacao(self):
        with pytest.raises(SystemExit, match="USO ÚNICO"):
            vl.avaliar_holdout(confirmo_uso_unico=False)

    def test_pesquisa_fail_nao_queima_o_holdout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vl, "DIR_VEREDITOS", str(tmp_path))
        (tmp_path / "volt_pesquisa.json").write_text(
            json.dumps({"escolhida_para_holdout": None}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="FAIL"):
            vl.avaliar_holdout(confirmo_uso_unico=True)
        assert not (tmp_path / "volt_holdout.json").exists()

    def test_segunda_chamada_recusada_e_registro_gravado(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vl, "DIR_VEREDITOS", str(tmp_path))
        doc = tmp_path / "METODOLOGIA_VOLT.md"
        doc.write_text("# doc\n", encoding="utf-8")
        monkeypatch.setattr(vl, "DOC_METODOLOGIA", str(doc))
        ts, closes = _closes_sinteticos()
        monkeypatch.setattr(vl, "carregar_diarias", lambda rotulo=None: (ts, closes))
        monkeypatch.setattr(vl.snapshot, "ler_manifest", lambda r=None: {"rotulo": "teste"})
        (tmp_path / "volt_pesquisa.json").write_text(
            json.dumps(
                {
                    "snapshot": "teste",
                    "escolhida_para_holdout": {"w": 10, "sigma_alvo": 0.40},
                }
            ),
            encoding="utf-8",
        )
        r = vl.avaliar_holdout(confirmo_uso_unico=True)
        assert r["veredito_holdout"] in {"PASS", "FAIL"}
        assert "hold-out CONSUMIDO" in doc.read_text(encoding="utf-8")
        with pytest.raises(SystemExit, match="JÁ CONSUMIDO"):
            vl.avaliar_holdout(confirmo_uso_unico=True)

    def test_holdout_recusa_snapshot_divergente(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vl, "DIR_VEREDITOS", str(tmp_path))
        monkeypatch.setattr(vl.snapshot, "ler_manifest", lambda r=None: {"rotulo": "OUTRO"})
        (tmp_path / "volt_pesquisa.json").write_text(
            json.dumps(
                {
                    "snapshot": "teste",
                    "escolhida_para_holdout": {"w": 10, "sigma_alvo": 0.40},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="MESMO substrato"):
            vl.avaliar_holdout(confirmo_uso_unico=True)
        assert not (tmp_path / "volt_holdout.json").exists()


class TestRegressoesDoCetico:
    def test_fatia_holdout_comeca_na_janela_pre_registrada(self):
        """Off-by-one pego pelo cético: o 1º retorno AVALIADO do hold-out tem
        que ser 22/07 → 23/07, nunca um dia da janela de pesquisa."""
        ts, closes = _closes_sinteticos()
        w = 20
        fat = vl._fatia_holdout(ts, w)
        ini = int(np.searchsorted(ts, vl.HOLDOUT_INICIO_MS))
        assert fat.start == ini - w
        # o 1º retorno avaliado em simular_vt é o do dia (start + w) → (start + w + 1)
        primeiro_dia_avaliado = ts[fat.start + w]
        assert primeiro_dia_avaliado >= vl.HOLDOUT_INICIO_MS
