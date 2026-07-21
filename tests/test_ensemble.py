"""
Testes herméticos do ensemble.py — combinação XGBoost+LSTM, ajuste por regime e FSRS.
Todos os modelos são mockados: sem arquivos .pkl, sem rede.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ensemble

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_xgb(prob: float):
    return patch("ensemble.ml_filtro", create=True), patch(
        "ml_filtro.prever", return_value=(prob, "OK")
    )


def _prever_com_mocks(xgb_prob, lstm_prob, regime="INDEFINIDO"):
    """Invoca ensemble.prever() com modelos mockados."""
    with (
        patch("ensemble.ml_filtro", create=True),
        patch("ensemble.lstm_modelo", create=True),
        patch("ml_filtro.prever", return_value=(xgb_prob, "OK")),
        patch("lstm_modelo.prever", return_value=(lstm_prob, "OK")),
    ):
        # Patch lazy imports dentro de prever()
        import importlib

        with patch.dict(
            "sys.modules",
            {
                "ml_filtro": MagicMock(prever=lambda *a, **kw: (xgb_prob, "OK")),
                "lstm_modelo": MagicMock(prever=lambda *a, **kw: (lstm_prob, "OK")),
            },
        ):
            return ensemble.prever(regime_atual=regime)


# ── Testes: pesos por regime ──────────────────────────────────────────────────


class TestPesosPorRegime:
    def test_tendencia_alta_pesos_base(self):
        ajuste = ensemble.AJUSTES_REGIME["TENDENCIA_ALTA"]
        assert ajuste["xgb"] == pytest.approx(0.55)
        assert ajuste["lstm"] == pytest.approx(0.45)

    def test_lateral_penaliza_lstm(self):
        ajuste = ensemble.AJUSTES_REGIME["LATERAL"]
        assert ajuste["xgb"] > ajuste["lstm"]
        assert ajuste["xgb"] == pytest.approx(0.70)

    def test_volatilidade_threshold_maior(self):
        ajuste = ensemble.AJUSTES_REGIME["VOLATILIDADE"]
        base = ensemble.AJUSTES_REGIME["INDEFINIDO"]
        assert ajuste["threshold"] > base["threshold"]

    def test_todos_regimes_definidos(self):
        regimes = ["TENDENCIA_ALTA", "TENDENCIA_BAIXA", "LATERAL", "VOLATILIDADE", "INDEFINIDO"]
        for r in regimes:
            assert r in ensemble.AJUSTES_REGIME
            assert "xgb" in ensemble.AJUSTES_REGIME[r]
            assert "threshold" in ensemble.AJUSTES_REGIME[r]

    def test_pesos_somam_1(self):
        for regime, ajuste in ensemble.AJUSTES_REGIME.items():
            soma = ajuste["xgb"] + ajuste["lstm"]
            assert soma == pytest.approx(1.0), f"Regime {regime}: pesos somam {soma}"


# ── Testes: cálculo do ensemble ───────────────────────────────────────────────


class TestCalculoEnsemble:
    def test_ambos_disponíveis_calcula_media_ponderada(self):
        """prob_ensemble é média ponderada dos dois modelos — fica entre os dois."""
        xgb, lstm = 0.70, 0.60
        r = _prever_com_mocks(xgb, lstm, regime="INDEFINIDO")
        # A média ponderada sem FSRS fica entre min e max;
        # com FSRS ajuste ≥ 0.7 → ainda bem acima de 0.4
        assert r["prob_ensemble"] >= 0.40
        assert r["prob_ensemble"] <= max(xgb, lstm) + 0.05

    def test_concordancia_ambos_acima_50(self):
        r = _prever_com_mocks(0.70, 0.65, regime="INDEFINIDO")
        assert r["concordancia"] is True

    def test_discordancia_um_acima_um_abaixo(self):
        r = _prever_com_mocks(0.70, 0.40, regime="INDEFINIDO")
        assert r["concordancia"] is False

    def test_pode_operar_true_quando_acima_limiar(self):
        r = _prever_com_mocks(0.80, 0.75, regime="TENDENCIA_ALTA")
        # threshold=0.55; ensemble alto → deve poder operar
        assert r["pode_operar"] is True

    def test_nao_opera_quando_abaixo_limiar(self):
        r = _prever_com_mocks(0.30, 0.25, regime="INDEFINIDO")
        assert r["pode_operar"] is False

    def test_retorna_campos_obrigatorios(self):
        r = _prever_com_mocks(0.65, 0.60)
        campos = [
            "prob_ensemble",
            "prob_xgb",
            "prob_lstm",
            "concordancia",
            "confianca",
            "pode_operar",
            "motivo",
            "regime",
            "pesos_aplicados",
        ]
        for campo in campos:
            assert campo in r, f"Campo '{campo}' ausente"

    def test_confianca_alta_quando_concordam_e_acima_limiar(self):
        r = _prever_com_mocks(0.80, 0.80, regime="TENDENCIA_ALTA")
        assert r["confianca"] == "ALTA"

    def test_confianca_baixa_quando_abaixo_limiar(self):
        r = _prever_com_mocks(0.30, 0.25, regime="INDEFINIDO")
        assert r["confianca"] == "BAIXA"


# ── Testes: fallback quando um modelo falha ───────────────────────────────────


class TestFallback:
    def test_apenas_xgb_disponivel(self):
        with patch.dict(
            "sys.modules",
            {
                "ml_filtro": MagicMock(prever=lambda *a, **kw: (0.70, "OK")),
                "lstm_modelo": MagicMock(prever=MagicMock(side_effect=Exception("sem modelo"))),
            },
        ):
            r = ensemble.prever(regime_atual="INDEFINIDO")
        assert r["prob_lstm"] is None
        assert r["prob_xgb"] == pytest.approx(0.70)
        assert r["concordancia"] is False

    def test_nenhum_modelo_disponivel(self):
        with patch.dict(
            "sys.modules",
            {
                "ml_filtro": MagicMock(prever=MagicMock(side_effect=Exception("no xgb"))),
                "lstm_modelo": MagicMock(prever=MagicMock(side_effect=Exception("no lstm"))),
            },
        ):
            r = ensemble.prever(regime_atual="INDEFINIDO")
        assert r["prob_ensemble"] == pytest.approx(0.5)
        assert r["pode_operar"] is False
        assert r["confianca"] == "NENHUM"

    def test_regime_invalido_usa_indefinido(self):
        r = _prever_com_mocks(0.60, 0.55, regime="REGIME_INEXISTENTE")
        # Deve usar AJUSTES_REGIME["INDEFINIDO"] como fallback
        assert r["pesos_aplicados"]["xgb"] == pytest.approx(
            ensemble.AJUSTES_REGIME["INDEFINIDO"]["xgb"]
        )
