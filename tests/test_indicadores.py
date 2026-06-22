"""
Testes — Indicadores Técnicos (indicadores.py)
===============================================
Suíte hermética (sem rede, sem banco, sem modelos) para a biblioteca de
indicadores técnicos vetorizados do BinanceXBot.

Cobre:
  - ema/sma/rsi/macd     — formato, padding nan, valores plausíveis
  - atr                  — lista len==N, None nos primeiros `periodo`, depois floats
  - bollinger            — (upper, mid, lower) len==N, None no warmup, mid==média,
                           upper>mid>lower, regressão do `import math` (sem NameError)
  - bandwidth            — (u-l)/m, None quando mid None/0
  - vwap / vwap_rolling  — cumulativo / janela deslizante
  - volume_media         — sma (ndarray menor)
  - volume_relativo      — REGRESSÃO IndexError: len==N, None no warmup,
                           [-1]==volumes[-1]/media_da_ultima_janela; vários tamanhos
  - tendencia_mtf        — ALTA / BAIXA / CONFLITO
  - market_structure     — HH / HL / LH / LL / None

Nenhuma dependência externa é tocada: o módulo só usa numpy e math puros.
Valores de referência foram calculados à mão para entradas pequenas.
"""

import os
import sys
import math

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicadores

# ══════════════════════════════════════════════════════════════
# EMA
# ══════════════════════════════════════════════════════════════


def test_ema_formato_e_padding_nan():
    valores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = indicadores.ema(valores, 3)
    # mesmo comprimento da entrada
    assert isinstance(out, np.ndarray)
    assert len(out) == len(valores)
    # padding nan nos primeiros periodo-1 itens
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    # inicialização no índice periodo-1 == média da primeira janela
    assert out[2] == pytest.approx(np.mean(valores[:3]))  # mean(1,2,3)=2.0
    assert not np.isnan(out[2])


def test_ema_recursao_valores_a_mao():
    valores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = indicadores.ema(valores, 3)
    k = 2 / (3 + 1)  # 0.5
    # out[2] = mean(1,2,3) = 2.0
    esperado_3 = 4.0 * k + 2.0 * (1 - k)  # 3.0
    esperado_4 = 5.0 * k + esperado_3 * (1 - k)  # 4.0
    assert out[3] == pytest.approx(esperado_3)
    assert out[4] == pytest.approx(esperado_4)


def test_ema_curto_demais_retorna_tudo_nan():
    valores = np.array([1.0, 2.0])
    out = indicadores.ema(valores, 5)
    assert len(out) == 2
    assert np.all(np.isnan(out))


# ══════════════════════════════════════════════════════════════
# SMA
# ══════════════════════════════════════════════════════════════


def test_sma_valores_e_comprimento():
    valores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = indicadores.sma(valores, 3)
    # mode='valid' -> comprimento len - periodo + 1
    assert len(out) == len(valores) - 3 + 1  # 3
    assert out[0] == pytest.approx((1 + 2 + 3) / 3)  # 2.0
    assert out[1] == pytest.approx((2 + 3 + 4) / 3)  # 3.0
    assert out[2] == pytest.approx((3 + 4 + 5) / 3)  # 4.0


def test_sma_curto_demais_retorna_nan():
    valores = np.array([1.0, 2.0])
    out = indicadores.sma(valores, 5)
    assert len(out) == 2
    assert np.all(np.isnan(out))


# ══════════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════════


def test_rsi_formato_e_padding():
    valores = np.arange(1, 21, dtype=float)  # 20 valores
    out = indicadores.rsi(valores, 14)
    assert isinstance(out, np.ndarray)
    assert len(out) == len(valores)
    # padding nan nos primeiros `periodo` itens
    assert np.all(np.isnan(out[:14]))
    assert not np.isnan(out[14])


def test_rsi_alta_continua_proximo_de_100():
    # série estritamente crescente -> ganhos puros -> RSI ~ 100
    valores = np.arange(1, 21, dtype=float)
    out = indicadores.rsi(valores, 14)
    ultimos = out[~np.isnan(out)]
    assert np.all(ultimos > 99.0)
    assert np.all(ultimos <= 100.0)


def test_rsi_queda_continua_proximo_de_zero():
    # série estritamente decrescente -> perdas puras -> RSI ~ 0
    valores = np.arange(20, 0, -1, dtype=float)
    out = indicadores.rsi(valores, 14)
    ultimos = out[~np.isnan(out)]
    assert np.all(ultimos < 1.0)
    assert np.all(ultimos >= 0.0)


def test_rsi_curto_demais_retorna_nan():
    valores = np.arange(1, 5, dtype=float)  # 4 < 14+1
    out = indicadores.rsi(valores, 14)
    assert len(out) == 4
    assert np.all(np.isnan(out))


# ══════════════════════════════════════════════════════════════
# MACD
# ══════════════════════════════════════════════════════════════


def test_macd_formato_tripla():
    valores = np.linspace(100, 200, 60)
    linha, sig, hist = indicadores.macd(valores, rapido=12, lento=26, sinal=9)
    for arr in (linha, sig, hist):
        assert isinstance(arr, np.ndarray)
        assert len(arr) == len(valores)
    # hist == linha - sig onde ambos definidos
    mask = ~np.isnan(linha) & ~np.isnan(sig)
    assert np.allclose(hist[mask], linha[mask] - sig[mask])


def test_macd_linha_e_diferenca_de_emas():
    valores = np.linspace(100, 200, 60)
    ema_rap = indicadores.ema(valores, 12)
    ema_len = indicadores.ema(valores, 26)
    linha, _, _ = indicadores.macd(valores)
    mask = ~np.isnan(ema_rap) & ~np.isnan(ema_len)
    assert np.allclose(linha[mask], (ema_rap - ema_len)[mask])


# ══════════════════════════════════════════════════════════════
# ATR
# ══════════════════════════════════════════════════════════════


def test_atr_comprimento_e_padding_none():
    n = 10
    periodo = 3
    maximas = [10.0 + i for i in range(n)]
    minimas = [5.0 + i for i in range(n)]
    fechamentos = [7.0 + i for i in range(n)]
    out = indicadores.atr(maximas, minimas, fechamentos, periodo)
    assert isinstance(out, list)
    assert len(out) == len(fechamentos)
    # None nos primeiros `periodo` itens
    assert out[:periodo] == [None] * periodo
    # depois floats
    for v in out[periodo:]:
        assert v is not None
        assert isinstance(v, float)


def test_atr_primeiro_valor_a_mao():
    # Construímos TR conhecido. maximas-minimas = 2 sempre; fechamentos planos.
    maximas = [11.0, 11.0, 11.0, 11.0, 11.0]
    minimas = [9.0, 9.0, 9.0, 9.0, 9.0]
    fechamentos = [10.0, 10.0, 10.0, 10.0, 10.0]
    periodo = 2
    # TR[i] (i de 1..4): hl=2, hcp=|11-10|=1, lcp=|9-10|=1 -> max=2
    # tr_list = [2,2,2,2]; media inicial = (2+2)/2 = 2.0 no índice `periodo`
    out = indicadores.atr(maximas, minimas, fechamentos, periodo)
    assert out[:periodo] == [None, None]
    assert out[periodo] == pytest.approx(2.0)
    # suavização permanece em 2.0 (TR constante)
    assert out[-1] == pytest.approx(2.0)


# ══════════════════════════════════════════════════════════════
# BOLLINGER  (regressão: import math -> sem NameError)
# ══════════════════════════════════════════════════════════════


def test_bollinger_comprimento_e_warmup_none():
    fechamentos = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    periodo = 3
    upper, mid, lower = indicadores.bollinger(fechamentos, periodo, 2)
    for banda in (upper, mid, lower):
        assert isinstance(banda, list)
        assert len(banda) == len(fechamentos)
    # None nos primeiros periodo-1
    assert upper[: periodo - 1] == [None] * (periodo - 1)
    assert mid[: periodo - 1] == [None] * (periodo - 1)
    assert lower[: periodo - 1] == [None] * (periodo - 1)


def test_bollinger_nao_lanca_nameerror_math():
    # Regressão direta: bollinger usa math.sqrt; se o import sumir, NameError.
    fechamentos = [1.0, 2.0, 3.0, 4.0, 5.0]
    # não deve levantar exceção alguma
    upper, mid, lower = indicadores.bollinger(fechamentos, 3, 2)
    assert mid[-1] is not None


def test_bollinger_mid_eh_media_e_ordenacao():
    fechamentos = [2.0, 4.0, 6.0, 8.0, 10.0]
    periodo = 3
    desvios = 2
    upper, mid, lower = indicadores.bollinger(fechamentos, periodo, desvios)
    # No índice 2: janela [2,4,6], media=4
    assert mid[2] == pytest.approx(4.0)
    std = math.sqrt(((2 - 4) ** 2 + (4 - 4) ** 2 + (6 - 4) ** 2) / 3)
    assert upper[2] == pytest.approx(4.0 + desvios * std)
    assert lower[2] == pytest.approx(4.0 - desvios * std)
    # ordenação upper > mid > lower (janela com variância > 0)
    for i in range(periodo - 1, len(fechamentos)):
        assert upper[i] > mid[i] > lower[i]


def test_bollinger_janela_constante_bandas_colapsam():
    fechamentos = [5.0, 5.0, 5.0, 5.0]
    upper, mid, lower = indicadores.bollinger(fechamentos, 3, 2)
    # std=0 -> upper==mid==lower
    assert upper[-1] == pytest.approx(mid[-1]) == pytest.approx(lower[-1]) == 5.0


# ══════════════════════════════════════════════════════════════
# BANDWIDTH
# ══════════════════════════════════════════════════════════════


def test_bandwidth_formula_e_none_no_warmup():
    upper = [None, None, 12.0, 14.0]
    mid = [None, None, 10.0, 11.0]
    lower = [None, None, 8.0, 9.0]
    out = indicadores.bandwidth(upper, mid, lower)
    assert len(out) == len(mid)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == pytest.approx((12.0 - 8.0) / 10.0)  # 0.4
    assert out[3] == pytest.approx((14.0 - 9.0) / 11.0)


def test_bandwidth_mid_zero_retorna_none():
    upper = [5.0]
    mid = [0.0]
    lower = [1.0]
    out = indicadores.bandwidth(upper, mid, lower)
    assert out == [None]


# ══════════════════════════════════════════════════════════════
# VWAP cumulativo
# ══════════════════════════════════════════════════════════════


def test_vwap_cumulativo_comprimento_e_valores():
    maximas = [10.0, 12.0]
    minimas = [8.0, 10.0]
    fechamentos = [9.0, 11.0]
    volumes = [100.0, 300.0]
    out = indicadores.vwap(maximas, minimas, fechamentos, volumes)
    assert len(out) == len(fechamentos)
    # tipico[0] = (10+8+9)/3 = 9.0 ; vwap[0] = 9.0
    assert out[0] == pytest.approx(9.0)
    # tipico[1] = (12+10+11)/3 = 11.0
    # cum_pv = 9*100 + 11*300 = 900 + 3300 = 4200 ; cum_v = 400 ; 4200/400=10.5
    assert out[1] == pytest.approx(10.5)


def test_vwap_volume_zero_usa_fechamento():
    maximas = [10.0]
    minimas = [8.0]
    fechamentos = [9.0]
    volumes = [0.0]
    out = indicadores.vwap(maximas, minimas, fechamentos, volumes)
    # cum_v == 0 -> usa fechamentos[i]
    assert out[0] == pytest.approx(9.0)


# ══════════════════════════════════════════════════════════════
# VWAP rolling
# ══════════════════════════════════════════════════════════════


def test_vwap_rolling_warmup_e_comprimento():
    n = 6
    periodo = 3
    maximas = [10.0 + i for i in range(n)]
    minimas = [8.0 + i for i in range(n)]
    fechamentos = [9.0 + i for i in range(n)]
    volumes = [100.0] * n
    out = indicadores.vwap_rolling(maximas, minimas, fechamentos, volumes, periodo)
    assert len(out) == n
    assert out[: periodo - 1] == [None] * (periodo - 1)
    for v in out[periodo - 1 :]:
        assert v is not None


def test_vwap_rolling_ultima_janela_a_mao():
    # janela final = últimos 3 candles. Volumes iguais -> média simples dos típicos.
    maximas = [10.0, 10.0, 10.0]
    minimas = [8.0, 8.0, 8.0]
    fechamentos = [9.0, 9.0, 9.0]
    volumes = [50.0, 50.0, 50.0]
    out = indicadores.vwap_rolling(maximas, minimas, fechamentos, volumes, 3)
    # tipico constante = 9.0 -> vwap = 9.0
    assert out[-1] == pytest.approx(9.0)


# ══════════════════════════════════════════════════════════════
# VOLUME_MEDIA
# ══════════════════════════════════════════════════════════════


def test_volume_media_eh_sma():
    volumes = np.array([100.0, 200.0, 300.0, 400.0])
    out = indicadores.volume_media(volumes, 2)
    esperado = indicadores.sma(volumes, 2)
    assert isinstance(out, np.ndarray)
    # ndarray menor (mode='valid'): len - periodo + 1
    assert len(out) == len(volumes) - 2 + 1
    assert np.allclose(out, esperado)


# ══════════════════════════════════════════════════════════════
# VOLUME_RELATIVO  (REGRESSÃO IndexError)
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize("n,periodo", [(5, 3), (10, 5), (20, 20), (25, 14), (6, 1)])
def test_volume_relativo_sem_indexerror_varios_tamanhos(n, periodo):
    volumes = [float(i + 1) for i in range(n)]
    # não deve lançar IndexError
    out = indicadores.volume_relativo(volumes, periodo)
    assert isinstance(out, list)
    assert len(out) == len(volumes)
    # None nos primeiros periodo-1
    assert out[: periodo - 1] == [None] * (periodo - 1)
    # restante: floats (ou None se média 0, que não ocorre aqui)
    for v in out[periodo - 1 :]:
        assert v is not None
        assert isinstance(v, float)


def test_volume_relativo_ultimo_valor_a_mao():
    volumes = [10.0, 20.0, 30.0, 40.0, 50.0]
    periodo = 3
    out = indicadores.volume_relativo(volumes, periodo)
    # média da última janela [30,40,50] = 40.0 ; rel = 50/40 = 1.25
    assert out[-1] == pytest.approx(50.0 / 40.0)
    # índice periodo-1 (=2): janela [10,20,30]=20 ; rel = 30/20 = 1.5
    assert out[2] == pytest.approx(30.0 / 20.0)


def test_volume_relativo_alinhamento_completo():
    # Confere todos os pontos alinhados ao SMA mode='valid'.
    volumes = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    periodo = 2
    out = indicadores.volume_relativo(volumes, periodo)
    media = indicadores.volume_media(volumes, periodo)  # len = 5
    for i in range(periodo - 1, len(volumes)):
        m = media[i - (periodo - 1)]
        assert out[i] == pytest.approx(volumes[i] / m)


# ══════════════════════════════════════════════════════════════
# TENDENCIA_MTF
# ══════════════════════════════════════════════════════════════


def test_tendencia_mtf_alta():
    # Série crescente em ambos os timeframes -> preço > e20 > e50 -> ALTA
    altos = list(np.linspace(1, 100, 60))
    baixos = list(np.linspace(1, 100, 60))
    assert indicadores.tendencia_mtf(altos, baixos) == "ALTA"


def test_tendencia_mtf_baixa():
    altos = list(np.linspace(100, 1, 60))
    baixos = list(np.linspace(100, 1, 60))
    assert indicadores.tendencia_mtf(altos, baixos) == "BAIXA"


def test_tendencia_mtf_conflito():
    altos = list(np.linspace(1, 100, 60))  # ALTA
    baixos = list(np.linspace(100, 1, 60))  # BAIXA
    assert indicadores.tendencia_mtf(altos, baixos) == "CONFLITO"


# ══════════════════════════════════════════════════════════════
# MARKET_STRUCTURE
# ══════════════════════════════════════════════════════════════


def test_market_structure_comprimento_e_dominio():
    fechamentos = list(np.linspace(1, 50, 50))
    periodo = 5
    out = indicadores.market_structure(fechamentos, periodo)
    assert isinstance(out, list)
    assert len(out) == len(fechamentos)
    permitido = {"HH", "HL", "LH", "LL", None}
    assert all(v in permitido for v in out)
    # warmup/cauda nunca rotulados (range vai de periodo a len-periodo)
    assert out[0] is None
    assert out[-1] is None


def test_market_structure_detecta_hh():
    # HH: dois topos consecutivos na lista de pivôs, o 2º estritamente maior.
    # Série com plateaus que produzem pivôs H sucessivos (periodo=2, janela=5).
    # Valor verificado: HH no índice 5.
    fechamentos = [1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 1.0, 1.0]
    out = indicadores.market_structure(fechamentos, periodo=2)
    assert len(out) == len(fechamentos)
    assert out[5] == "HH"


def test_market_structure_detecta_hl():
    # HL: dois fundos consecutivos, o 2º estritamente maior (higher low).
    # Valor verificado: HL no índice 5.
    fechamentos = [1.0, 1.0, 1.0, 2.0, 3.0, 2.0, 4.0, 2.0]
    out = indicadores.market_structure(fechamentos, periodo=2)
    assert out[5] == "HL"


def test_market_structure_detecta_lh():
    # LH: dois topos consecutivos, 2º NÃO maior (val == val_ant via plateau).
    # Valor verificado: LH no índice 2.
    fechamentos = [1.0, 1.0, 1.0, 1.0]
    out = indicadores.market_structure(fechamentos, periodo=1)
    assert out[2] == "LH"


def test_market_structure_detecta_ll():
    # LL: dois fundos consecutivos, o 2º estritamente menor (lower low).
    # Valor verificado: LL no índice 2.
    fechamentos = [2.0, 1.0, 1.0, 2.0]
    out = indicadores.market_structure(fechamentos, periodo=1)
    assert out[2] == "LL"


def test_market_structure_curto_sem_pivos():
    # Entrada menor que 2*periodo -> sem range -> tudo None
    fechamentos = [1.0, 2.0, 3.0]
    out = indicadores.market_structure(fechamentos, periodo=5)
    assert out == [None, None, None]
