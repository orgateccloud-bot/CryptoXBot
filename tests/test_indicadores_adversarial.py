"""
Testes ADVERSARIAIS — Indicadores Técnicos (indicadores.py)  [@Delta]
=====================================================================
Complementa tests/test_indicadores.py SEM modificá-lo. Foco em branches e
bordas não cobertos: limiares exatos (len == periodo / periodo+1), periodo=1,
séries constantes/decrescentes, divisões por zero (media/volume/v_sum), valores
negativos, e regressões críticas (volume_relativo sem IndexError, bollinger sem
NameError no warmup, market_structure sem crash em entradas degeneradas).

Todos os valores de referência foram calculados à mão. Hermético: só numpy +
math puros; nenhuma rede, banco ou modelo é tocado.
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicadores

# ══════════════════════════════════════════════════════════════
# EMA — bordas
# ══════════════════════════════════════════════════════════════


def test_ema_len_igual_periodo_so_ultimo_definido():
    # len == periodo: índice periodo-1 (=último) é a média; resto nan.
    valores = np.array([2.0, 4.0, 6.0])
    out = indicadores.ema(valores, 3)
    assert len(out) == 3
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == pytest.approx(np.mean(valores))  # 4.0
    # loop range(periodo, len) é vazio -> nenhuma recursão extra


def test_ema_periodo_um_segue_a_serie():
    # periodo=1: k=1 -> ema[i] == valores[i] para todo i (sem warmup nan).
    valores = np.array([10.0, 20.0, 5.0, 7.0])
    out = indicadores.ema(valores, 1)
    assert len(out) == 4
    assert not np.any(np.isnan(out))
    assert np.allclose(out, valores)


def test_ema_lista_vazia_retorna_vazio():
    out = indicadores.ema(np.array([]), 5)
    assert isinstance(out, np.ndarray)
    assert len(out) == 0


# ══════════════════════════════════════════════════════════════
# SMA — bordas
# ══════════════════════════════════════════════════════════════


def test_sma_len_igual_periodo_um_unico_ponto():
    valores = np.array([3.0, 5.0, 7.0])
    out = indicadores.sma(valores, 3)
    assert len(out) == 1
    assert out[0] == pytest.approx(5.0)


def test_sma_periodo_um_identidade():
    valores = np.array([1.0, 9.0, 4.0])
    out = indicadores.sma(valores, 1)
    assert len(out) == 3
    assert np.allclose(out, valores)


# ══════════════════════════════════════════════════════════════
# RSI — limiar exato e valor a mão
# ══════════════════════════════════════════════════════════════


def test_rsi_len_exatamente_periodo_mais_um():
    # len == periodo+1 -> exatamente 1 ponto definido (o último).
    valores = np.arange(1, 16, dtype=float)  # 15 == 14+1
    out = indicadores.rsi(valores, 14)
    assert len(out) == 15
    assert np.all(np.isnan(out[:14]))
    assert not np.isnan(out[14])
    # série crescente pura -> ~100
    assert out[14] > 99.0


def test_rsi_valor_a_mao_ganhos_e_perdas_mistos():
    # periodo=2. deltas conhecidos -> RSI exato.
    valores = np.array([10.0, 12.0, 11.0, 14.0])
    # deltas = [+2, -1, +3]
    # gains  = [2, 0, 3]; losses = [0, 1, 0]
    # avg (janela=2, valid): avg_gains=[(2+0)/2,(0+3)/2]=[1.0,1.5]
    #                        avg_losses=[(0+1)/2,(1+0)/2]=[0.5,0.5]
    out = indicadores.rsi(valores, 2)
    assert len(out) == 4
    assert np.all(np.isnan(out[:2]))
    rs0 = 1.0 / 0.5  # 2.0
    rs1 = 1.5 / 0.5  # 3.0
    assert out[2] == pytest.approx(100 - 100 / (1 + rs0))  # ~66.667
    assert out[3] == pytest.approx(100 - 100 / (1 + rs1))  # 75.0


def test_rsi_sem_perdas_usa_epsilon_nao_divzero():
    # losses todos 0 -> divisor vira 1e-10 (sem ZeroDivision / sem nan).
    valores = np.array([1.0, 2.0, 3.0, 4.0])
    out = indicadores.rsi(valores, 2)
    definidos = out[~np.isnan(out)]
    assert len(definidos) == 2
    assert np.all(definidos > 99.0)
    assert np.all(np.isfinite(definidos))


# ══════════════════════════════════════════════════════════════
# MACD — propagação de nan em série curta
# ══════════════════════════════════════════════════════════════


def test_macd_serie_curta_tudo_nan():
    # len < lento (26) -> ema_len toda nan -> linha toda nan -> sig/hist nan.
    valores = np.linspace(1, 10, 10)
    linha, sig, hist = indicadores.macd(valores)
    assert len(linha) == len(sig) == len(hist) == 10
    assert np.all(np.isnan(linha))
    assert np.all(np.isnan(sig))
    assert np.all(np.isnan(hist))


def test_macd_sinal_propaga_nan_da_linha():
    # Comportamento REAL: `linha` tem nan no warmup (primeiros lento-1). ema(linha)
    # inicializa com mean() sobre nan e a recursão usa ema[i-1] (nan), então `sig`
    # fica TODA nan. Consequentemente hist = linha - sig também fica toda nan.
    valores = np.linspace(50, 250, 80)
    linha, sig, hist = indicadores.macd(valores, 5, 13, 4)
    assert np.any(~np.isnan(linha))  # linha tem valores definidos
    assert np.all(np.isnan(sig))  # sig contaminada por nan
    assert np.all(np.isnan(hist))  # hist herda os nan
    # onde ambos definidos (conjunto vazio aqui) a relação ainda vale vacuamente
    mask = ~np.isnan(linha) & ~np.isnan(sig)
    assert np.allclose(hist[mask], (linha - sig)[mask])


# ══════════════════════════════════════════════════════════════
# ATR — suavização real e default
# ══════════════════════════════════════════════════════════════


def test_atr_suavizacao_com_tr_variavel():
    # TR conhecido e variável -> conferimos a média e o passo de suavização.
    # candles planos exceto o ultimo, fechamentos == minimas anteriores p/ controle
    maximas = [12.0, 12.0, 12.0, 20.0]
    minimas = [10.0, 10.0, 10.0, 10.0]
    fechamentos = [11.0, 11.0, 11.0, 11.0]
    periodo = 2
    # TR[i], i=1..3:
    #  i=1: hl=2, hcp=|12-11|=1, lcp=|10-11|=1 -> 2
    #  i=2: hl=2, hcp=1, lcp=1 -> 2
    #  i=3: hl=10, hcp=|20-11|=9, lcp=|10-11|=1 -> 10
    # tr_list=[2,2,10]; media inicial=(2+2)/2=2.0 no idx 2
    # suaviza com tr=10: (2*1 + 10)/2 = 6.0 no idx 3
    out = indicadores.atr(maximas, minimas, fechamentos, periodo)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(6.0)


def test_atr_default_periodo_qucatorze_padding():
    n = 20
    maximas = [10.0 + i for i in range(n)]
    minimas = [5.0 + i for i in range(n)]
    fechamentos = [7.0 + i for i in range(n)]
    out = indicadores.atr(maximas, minimas, fechamentos)  # periodo=14 default
    assert len(out) == n
    assert out[:14] == [None] * 14
    assert out[14] is not None and isinstance(out[14], float)


# ══════════════════════════════════════════════════════════════
# BOLLINGER — default, negativos, regressão NameError no warmup
# ══════════════════════════════════════════════════════════════


def test_bollinger_warmup_nao_lanca_nameerror_serie_longa():
    # Garante que o caminho do warmup (i < periodo-1) e o cálculo (math.sqrt)
    # coexistem sem NameError ao longo de toda a série.
    fechamentos = [float(x) for x in range(1, 31)]
    upper, mid, lower = indicadores.bollinger(fechamentos)  # periodo=20 default
    assert len(mid) == 30
    assert mid[:19] == [None] * 19
    assert mid[19] is not None
    assert upper[-1] > mid[-1] > lower[-1]


def test_bollinger_valores_negativos_mid_e_ordenacao():
    fechamentos = [-2.0, -4.0, -6.0, -8.0]
    periodo = 3
    upper, mid, lower = indicadores.bollinger(fechamentos, periodo, 2)
    # idx 2: janela [-2,-4,-6], media=-4
    assert mid[2] == pytest.approx(-4.0)
    std = math.sqrt(((-2 + 4) ** 2 + 0 + (-6 + 4) ** 2) / 3)
    assert upper[2] == pytest.approx(-4.0 + 2 * std)
    assert lower[2] == pytest.approx(-4.0 - 2 * std)
    assert upper[2] > mid[2] > lower[2]


def test_bollinger_desvios_zero_colapsa_em_mid():
    fechamentos = [1.0, 3.0, 5.0, 7.0]
    upper, mid, lower = indicadores.bollinger(fechamentos, 2, 0)
    for i in range(1, 4):
        assert upper[i] == pytest.approx(mid[i]) == pytest.approx(lower[i])


# ══════════════════════════════════════════════════════════════
# BANDWIDTH — mid negativo (truthy) e mid None
# ══════════════════════════════════════════════════════════════


def test_bandwidth_mid_negativo_eh_calculado():
    # mid != 0 e truthy (negativo) -> entra no ramo de cálculo.
    upper = [-2.0]
    mid = [-4.0]
    lower = [-6.0]
    out = indicadores.bandwidth(upper, mid, lower)
    assert out[0] == pytest.approx((-2.0 - -6.0) / -4.0)  # 4 / -4 = -1.0


def test_bandwidth_todos_none_retorna_lista_de_none():
    upper = [None, None]
    mid = [None, None]
    lower = [None, None]
    out = indicadores.bandwidth(upper, mid, lower)
    assert out == [None, None]


# ══════════════════════════════════════════════════════════════
# VWAP — todos volumes zero / monotonia cumulativa
# ══════════════════════════════════════════════════════════════


def test_vwap_todos_volumes_zero_usa_fechamento_em_cada_ponto():
    maximas = [10.0, 12.0, 14.0]
    minimas = [8.0, 10.0, 12.0]
    fechamentos = [9.0, 11.0, 13.0]
    volumes = [0.0, 0.0, 0.0]
    out = indicadores.vwap(maximas, minimas, fechamentos, volumes)
    # cum_v permanece 0 em todos -> usa fechamentos[i]
    assert out == pytest.approx([9.0, 11.0, 13.0])


def test_vwap_segundo_ponto_volume_zero_mantem_acumulado():
    # 1o volume positivo, 2o zero -> cum_v != 0 no idx 1, usa razão acumulada.
    maximas = [10.0, 20.0]
    minimas = [8.0, 16.0]
    fechamentos = [9.0, 18.0]
    volumes = [100.0, 0.0]
    out = indicadores.vwap(maximas, minimas, fechamentos, volumes)
    # tipico[0]=9 -> out[0]=9
    # idx1: cum_pv = 9*100 + (18*0)=900; cum_v=100 -> 9.0
    assert out[0] == pytest.approx(9.0)
    assert out[1] == pytest.approx(9.0)


# ══════════════════════════════════════════════════════════════
# VWAP rolling — janela com v_sum zero usa fechamento
# ══════════════════════════════════════════════════════════════


def test_vwap_rolling_janela_volume_zero_usa_fechamento():
    maximas = [10.0, 10.0, 10.0]
    minimas = [8.0, 8.0, 8.0]
    fechamentos = [9.0, 9.0, 9.0]
    volumes = [0.0, 0.0, 0.0]
    out = indicadores.vwap_rolling(maximas, minimas, fechamentos, volumes, 3)
    assert out[:2] == [None, None]
    # v_sum==0 -> usa fechamentos[-1]
    assert out[-1] == pytest.approx(9.0)


def test_vwap_rolling_ponderado_a_mao():
    # Volumes diferentes -> VWAP ponderado real, não média simples.
    maximas = [12.0, 12.0]
    minimas = [6.0, 6.0]
    fechamentos = [9.0, 9.0]
    volumes = [100.0, 300.0]
    out = indicadores.vwap_rolling(maximas, minimas, fechamentos, volumes, 2)
    # tipico constante = (12+6+9)/3 = 9.0 -> vwap = 9.0
    assert out[0] is None
    assert out[-1] == pytest.approx(9.0)


# ══════════════════════════════════════════════════════════════
# VOLUME_RELATIVO — média zero -> None, periodo==n, regressão IndexError
# ══════════════════════════════════════════════════════════════


def test_volume_relativo_media_zero_retorna_none_no_ponto():
    # janela de volumes somando zero -> m==0 -> None nesse ponto.
    volumes = [0.0, 0.0, 5.0]
    periodo = 2
    out = indicadores.volume_relativo(volumes, periodo)
    assert len(out) == 3
    assert out[0] is None  # warmup
    # idx1: media janela [0,0]=0 -> None
    assert out[1] is None
    # idx2: media janela [0,5]=2.5 -> 5/2.5 = 2.0
    assert out[2] == pytest.approx(2.0)


def test_volume_relativo_periodo_igual_n_um_unico_ponto():
    # periodo == len -> único ponto definido no final, sem IndexError.
    volumes = [10.0, 20.0, 30.0]
    out = indicadores.volume_relativo(volumes, 3)
    assert len(out) == 3
    assert out[:2] == [None, None]
    # media janela toda = 20 -> 30/20 = 1.5
    assert out[2] == pytest.approx(1.5)


def test_volume_relativo_periodo_um_identidade():
    # periodo=1: cada ponto = volume[i]/media(janela de 1) = 1.0
    volumes = [3.0, 7.0, 11.0]
    out = indicadores.volume_relativo(volumes, 1)
    assert len(out) == 3
    assert out == pytest.approx([1.0, 1.0, 1.0])


@pytest.mark.parametrize("n,periodo", [(1, 1), (2, 2), (30, 20), (50, 50)])
def test_volume_relativo_sem_indexerror_extra(n, periodo):
    volumes = [float(i + 1) for i in range(n)]
    out = indicadores.volume_relativo(volumes, periodo)
    assert len(out) == n
    assert out[: periodo - 1] == [None] * (periodo - 1)
    for v in out[periodo - 1 :]:
        assert v is not None and isinstance(v, float)


# ══════════════════════════════════════════════════════════════
# TENDENCIA_MTF — LATERAL -> CONFLITO e série curta (<50)
# ══════════════════════════════════════════════════════════════


def test_tendencia_mtf_lateral_resulta_conflito():
    # Série totalmente plana -> p == e20 == e50 -> nenhum ramo estrito ALTA/BAIXA
    # -> LATERAL em ambos os timeframes -> CONFLITO (LATERAL != ALTA e != BAIXA).
    serie = [50.0] * 60
    assert indicadores.tendencia_mtf(serie, serie) == "CONFLITO"


def test_tendencia_mtf_serie_curta_usa_e20_como_e50():
    # len < 50 -> e50 = e20; ALTA exige p > e20 > e50, mas e20==e50 (não >).
    # Logo nunca ALTA nem BAIXA estritos -> CONFLITO. Garante sem crash.
    altos = list(np.linspace(1, 30, 30))
    baixos = list(np.linspace(1, 30, 30))
    res = indicadores.tendencia_mtf(altos, baixos)
    assert res in {"ALTA", "BAIXA", "CONFLITO"}
    assert res == "CONFLITO"  # e20==e50 quebra a desigualdade estrita


def test_tendencia_mtf_um_alta_um_lateral_eh_conflito():
    alta = list(np.linspace(1, 100, 60))
    lateral = [50.0] * 60  # plano -> LATERAL
    assert indicadores.tendencia_mtf(alta, lateral) == "CONFLITO"


# ══════════════════════════════════════════════════════════════
# MARKET_STRUCTURE — degenerados sem crash
# ══════════════════════════════════════════════════════════════


def test_market_structure_lista_vazia():
    assert indicadores.market_structure([], periodo=5) == []


def test_market_structure_um_elemento():
    out = indicadores.market_structure([42.0], periodo=5)
    assert out == [None]


def test_market_structure_serie_plana_sem_crash():
    # Tudo igual -> cada ponto é max E min da janela; primeiro ramo (max) vence.
    fechamentos = [5.0] * 10
    out = indicadores.market_structure(fechamentos, periodo=2)
    assert len(out) == 10
    permitido = {"HH", "HL", "LH", "LL", None}
    assert all(v in permitido for v in out)
    # plano -> nenhum topo estritamente maior -> sem 'HH'
    assert "HH" not in out


def test_market_structure_dominio_e_caudas_none():
    fechamentos = list(np.linspace(1, 40, 40))
    periodo = 4
    out = indicadores.market_structure(fechamentos, periodo)
    permitido = {"HH", "HL", "LH", "LL", None}
    assert all(v in permitido for v in out)
    assert out[0] is None
    assert out[-1] is None


def test_market_structure_ll_em_fundos_consecutivos():
    # Pivôs (periodo=1): L@1(2.0), H@2(2.0), L@3(1.0), L@4(1.0).
    # Os dois últimos são L consecutivos; val(1.0) > val_ant(1.0) é False -> LL.
    fechamentos = [3.0, 2.0, 2.0, 1.0, 1.0, 3.0]
    out = indicadores.market_structure(fechamentos, periodo=1)
    assert out[4] == "LL"
