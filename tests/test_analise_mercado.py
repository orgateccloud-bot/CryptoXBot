"""
Testes herméticos de analise_mercado.py — lógica de classificação sem rede.
Todas as chamadas a requests são mockadas.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analise_mercado

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_resp(payload: dict | list):
    m = MagicMock()
    m.json.return_value = payload
    return m


# ── Testes: get_preco_atual ───────────────────────────────────────────────────


class TestGetPrecoAtual:
    def _payload(
        self,
        preco="64000.0",
        var="1.5",
        maxi="65000.0",
        mini="63000.0",
        vol="500.0",
        quoteVol="32000000000.0",
    ):
        return {
            "lastPrice": preco,
            "priceChangePercent": var,
            "highPrice": maxi,
            "lowPrice": mini,
            "volume": vol,
            "quoteVolume": quoteVol,
        }

    def test_retorna_preco_float(self):
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._payload())):
            r = analise_mercado.get_preco_atual()
        assert r["preco"] == pytest.approx(64000.0)

    def test_retorna_variacao(self):
        with patch(
            "analise_mercado.requests.get", return_value=_mock_resp(self._payload(var="2.3"))
        ):
            r = analise_mercado.get_preco_atual()
        assert r["variacao_24h_%"] == pytest.approx(2.3)

    def test_todos_campos_presentes(self):
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._payload())):
            r = analise_mercado.get_preco_atual()
        for campo in (
            "preco",
            "variacao_24h_%",
            "maximo_24h",
            "minimo_24h",
            "volume_24h_btc",
            "volume_24h_usdt",
        ):
            assert campo in r, f"Campo '{campo}' ausente"


# ── Testes: get_order_book ────────────────────────────────────────────────────


class TestGetOrderBook:
    def _ob_payload(self):
        return {
            "bids": [["63000.0", "2.5"], ["62900.0", "1.0"]],
            "asks": [["63100.0", "3.0"], ["63200.0", "0.5"]],
        }

    def test_pressao_compra_quando_bids_maior(self):
        payload = {
            "bids": [["63000.0", "10.0"]],  # 630000 USDT
            "asks": [["63100.0", "1.0"]],  # 63100 USDT
        }
        with patch("analise_mercado.requests.get", return_value=_mock_resp(payload)):
            r = analise_mercado.get_order_book()
        assert r["pressao_dominante"] == "COMPRA"

    def test_pressao_venda_quando_asks_maior(self):
        payload = {
            "bids": [["63000.0", "1.0"]],  # 63000 USDT
            "asks": [["63100.0", "10.0"]],  # 631000 USDT
        }
        with patch("analise_mercado.requests.get", return_value=_mock_resp(payload)):
            r = analise_mercado.get_order_book()
        assert r["pressao_dominante"] == "VENDA"

    def test_campos_retornados(self):
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._ob_payload())):
            r = analise_mercado.get_order_book()
        for campo in (
            "maior_suporte_preco",
            "maior_resistencia_preco",
            "liquidez_compra_usdt",
            "liquidez_venda_usdt",
            "pressao_dominante",
        ):
            assert campo in r


# ── Testes: get_funding_rate ──────────────────────────────────────────────────


class TestGetFundingRate:
    def test_positivo_otimismo(self):
        payload = {"lastFundingRate": "0.00015"}  # 0.015% > 0.01% → otimismo
        with patch("analise_mercado.requests.get", return_value=_mock_resp(payload)):
            r = analise_mercado.get_funding_rate()
        assert "OTIMISMO" in r["sentimento"]
        assert r["funding_rate_%"] == pytest.approx(0.015)

    def test_negativo_pessimismo(self):
        payload = {"lastFundingRate": "-0.00015"}
        with patch("analise_mercado.requests.get", return_value=_mock_resp(payload)):
            r = analise_mercado.get_funding_rate()
        assert "PESSIMISMO" in r["sentimento"]

    def test_neutro(self):
        payload = {"lastFundingRate": "0.00005"}  # 0.005% < 0.01% → neutro
        with patch("analise_mercado.requests.get", return_value=_mock_resp(payload)):
            r = analise_mercado.get_funding_rate()
        assert r["sentimento"] == "NEUTRO"


# ── Testes: get_medias_moveis ─────────────────────────────────────────────────


class TestGetMediasMoveis:
    def _klines(self, preco_base=64000.0, n=60):
        """Gera n klines com preço crescente (tendência ALTA)."""
        return [
            [
                0,
                str(preco_base + i * 10),
                str(preco_base + i * 10 + 100),
                str(preco_base + i * 10 - 50),
                str(preco_base + i * 10),
                "500",
            ]
            for i in range(n)
        ]

    def test_retorna_ema20_ema50(self):
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._klines())):
            r = analise_mercado.get_medias_moveis()
        assert "ema20_1h" in r
        assert "ema50_1h" in r
        assert isinstance(r["ema20_1h"], float)

    def test_tendencia_alta_detectada(self):
        """Com preços crescentes, EMA20 > EMA50 → tendência ALTA."""
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._klines())):
            r = analise_mercado.get_medias_moveis()
        assert "ALTA" in r["tendencia"]

    def test_tendencia_baixa_detectada(self):
        """Com preços decrescentes, EMA20 < EMA50 → tendência BAIXA."""
        klines = [
            [
                0,
                str(65000.0 - i * 10),
                str(65000.0 - i * 10 + 50),
                str(65000.0 - i * 10 - 100),
                str(65000.0 - i * 10),
                "500",
            ]
            for i in range(60)
        ]
        with patch("analise_mercado.requests.get", return_value=_mock_resp(klines)):
            r = analise_mercado.get_medias_moveis()
        assert "BAIXA" in r["tendencia"] or "LATERAL" in r["tendencia"]

    def test_retorna_rsi(self):
        with patch("analise_mercado.requests.get", return_value=_mock_resp(self._klines())):
            r = analise_mercado.get_medias_moveis()
        assert "rsi_1h" in r
        assert 0 <= r["rsi_1h"] <= 100
