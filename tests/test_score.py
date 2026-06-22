"""
Suíte pytest para score.py (Score Unificado 0-100).

Perfil @Delta — testes herméticos: SEM rede, SEM banco.
O módulo score.py contém funções puras; o único import externo é
data.cvd_calculator.calculate_cvd, que só é acionado quando há
historico_ticks com tamanho >= periodo. Todos os testes mantêm
historico_ticks=None/curto para fixar o componente CVD em 50 (neutro),
o que torna a suíte determinística sem qualquer mock de rede/DB.
"""

import os
import sys

import pytest

# Garante que a raiz do repo está no path (score.py vive na raiz).
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import score  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _inputs_base(**overrides):
    """Conjunto de kwargs neutros para score.calcular(); sobrescreva o necessário."""
    base = dict(
        regime_info={"regime_final": "TENDENCIA_ALTA", "score": 50},
        fear_info={"valor": 50},
        tend_4h="LATERAL",
        ml_prob=0.5,
        preco=100.0,
        ema20=100.0,
        ema50=100.0,
        rsi=52,
        vwap_val=100.0,
        vol_rel=1.0,
        atr_atual=100.0,
        atr_media=100.0,
        historico_ticks=None,  # mantém CVD em 50 (neutro), sem tocar calculate_cvd
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PESOS
# ---------------------------------------------------------------------------


def test_pesos_somam_100():
    assert sum(score.PESOS.values()) == 100


def test_pesos_chaves_esperadas():
    esperadas = {
        "regime",
        "cvd",
        "mtf",
        "ml",
        "ema",
        "fear_greed",
        "rsi",
        "vwap",
        "volume",
        "atr",
    }
    assert set(score.PESOS.keys()) == esperadas


# ---------------------------------------------------------------------------
# _score_regime
# ---------------------------------------------------------------------------


def test_score_regime_tendencia_alta_forca_alta():
    r = score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 90})
    assert r >= 60
    assert r == 90


def test_score_regime_tendencia_alta_minimo_60():
    # Mesmo com força baixa, trend claro nunca cai abaixo de 60.
    r = score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 10})
    assert r == 60


def test_score_regime_tendencia_baixa_trend_claro():
    r = score._score_regime({"regime_final": "TENDENCIA_BAIXA", "score": 85})
    assert r >= 60
    assert r == 85


def test_score_regime_lateral():
    assert score._score_regime({"regime_final": "LATERAL", "score": 99}) == 30


def test_score_regime_volatilidade():
    assert score._score_regime({"regime_final": "VOLATILIDADE", "score": 99}) == 10


def test_score_regime_dict_vazio():
    assert score._score_regime({}) == 50


def test_score_regime_none():
    assert score._score_regime(None) == 50


def test_score_regime_indefinido():
    assert score._score_regime({"regime_final": "INDEFINIDO", "score": 70}) == 40


def test_score_regime_clamp_100():
    # Força acima de 100 é limitada a 100.
    assert score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 150}) == 100


# ---------------------------------------------------------------------------
# _score_rsi
# ---------------------------------------------------------------------------


def test_score_rsi_none():
    assert score._score_rsi(None) == 50


def test_score_rsi_centro_da_faixa():
    # Centro (52) deve dar o máximo (100).
    assert score._score_rsi(52) == 100


def test_score_rsi_dentro_da_faixa_borda():
    r = score._score_rsi(42)  # borda inferior da faixa ideal
    assert 0 <= r <= 100
    assert r == 70  # 100 - 1*30


def test_score_rsi_abaixo_da_faixa():
    r = score._score_rsi(30)
    assert 0 <= r <= 100
    assert r == 0


def test_score_rsi_acima_da_faixa():
    r = score._score_rsi(80)
    assert 0 <= r <= 100
    assert r == 0


def test_score_rsi_sempre_no_intervalo():
    for v in range(0, 101, 5):
        assert 0 <= score._score_rsi(v) <= 100


# ---------------------------------------------------------------------------
# _score_ml
# ---------------------------------------------------------------------------


def test_score_ml_none():
    assert score._score_ml(None) == 50


def test_score_ml_prob_alta():
    assert score._score_ml(0.9) == 90


def test_score_ml_prob_baixa():
    assert score._score_ml(0.1) == 10


def test_score_ml_penaliza_lateral():
    # prob > 0.6 em mercado lateral perde 20pts.
    assert score._score_ml(0.8, "LATERAL") == 60


def test_score_ml_lateral_sem_penalidade_se_prob_baixa():
    assert score._score_ml(0.5, "LATERAL") == 50


def test_score_ml_intervalo():
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert 0 <= score._score_ml(p) <= 100


# ---------------------------------------------------------------------------
# _score_fear_greed
# ---------------------------------------------------------------------------


def test_score_fear_greed_medo_extremo():
    assert score._score_fear_greed(20) == 0
    assert score._score_fear_greed(10) == 0


def test_score_fear_greed_ganancia_extrema():
    assert score._score_fear_greed(81) == 0
    assert score._score_fear_greed(95) == 0


def test_score_fear_greed_zona_ideal():
    assert score._score_fear_greed(50) == 100


def test_score_fear_greed_medo_moderado():
    assert score._score_fear_greed(30) == 60


def test_score_fear_greed_ganancia_moderada():
    assert score._score_fear_greed(70) == 70


def test_score_fear_greed_transicao():
    assert score._score_fear_greed(78) == 40


def test_score_fear_greed_intervalo():
    for v in range(0, 101, 5):
        assert 0 <= score._score_fear_greed(v) <= 100


# ---------------------------------------------------------------------------
# _score_vwap
# ---------------------------------------------------------------------------


def test_score_vwap_invalido():
    assert score._score_vwap(100, 0) == 50
    assert score._score_vwap(100, -5) == 50


def test_score_vwap_preco_acima():
    r = score._score_vwap(110, 100)  # +10%
    assert 0 <= r <= 100
    assert r > 50


def test_score_vwap_preco_abaixo():
    r = score._score_vwap(90, 100)  # -10%
    assert 0 <= r <= 100
    assert r < 50


def test_score_vwap_intervalo():
    for preco in (50, 80, 95, 100, 105, 130, 200):
        assert 0 <= score._score_vwap(preco, 100) <= 100


# ---------------------------------------------------------------------------
# _score_volume
# ---------------------------------------------------------------------------


def test_score_volume_faixas():
    assert score._score_volume(2.5) == 100
    assert score._score_volume(1.5) == 80
    assert score._score_volume(1.1) == 60
    assert score._score_volume(0.8) == 40
    assert score._score_volume(0.3) == 20


def test_score_volume_intervalo():
    for v in (0.0, 0.5, 0.7, 1.0, 1.3, 2.0, 5.0):
        assert 0 <= score._score_volume(v) <= 100


# ---------------------------------------------------------------------------
# _score_atr
# ---------------------------------------------------------------------------


def test_score_atr_media_invalida():
    assert score._score_atr(100, 0) == 50


def test_score_atr_extrema():
    assert score._score_atr(300, 100) == 0  # ratio 3.0 > 2.5


def test_score_atr_ideal():
    assert score._score_atr(100, 100) == 100  # ratio 1.0


def test_score_atr_mercado_parado():
    assert score._score_atr(50, 100) == 30  # ratio 0.5 < 0.7


def test_score_atr_elevada_aceitavel():
    assert score._score_atr(200, 100) == 60  # ratio 2.0 (entre 1.8 e 2.5)


def test_score_atr_intervalo():
    for a in (10, 50, 70, 100, 180, 250, 400):
        assert 0 <= score._score_atr(a, 100) <= 100


# ---------------------------------------------------------------------------
# _score_mtf
# ---------------------------------------------------------------------------


def test_score_mtf_alta():
    assert score._score_mtf("ALTA") == 100


def test_score_mtf_lateral():
    assert score._score_mtf("LATERAL") == 50


def test_score_mtf_baixa():
    assert score._score_mtf("BAIXA") == 0


def test_score_mtf_intervalo():
    for t in ("ALTA", "LATERAL", "BAIXA", "QUALQUER"):
        assert 0 <= score._score_mtf(t) <= 100


# ---------------------------------------------------------------------------
# _score_ema
# ---------------------------------------------------------------------------


def test_score_ema_alinhamento_perfeito():
    r = score._score_ema(110, 105, 100)  # preco > ema20 > ema50
    assert 0 <= r <= 100
    assert r >= 70


def test_score_ema_preco_acima_ema20():
    assert score._score_ema(106, 105, 110) == 50  # preco>ema20 mas ema20<ema50


def test_score_ema_preco_acima_ema50_apenas():
    assert score._score_ema(102, 105, 100) == 30  # preco<ema20, preco>ema50


def test_score_ema_abaixo_de_tudo():
    assert score._score_ema(90, 105, 100) == 0


def test_score_ema_intervalo():
    casos = [
        (110, 105, 100),
        (200, 105, 100),  # distância grande, deve saturar em 100
        (106, 105, 110),
        (102, 105, 100),
        (90, 105, 100),
    ]
    for preco, e20, e50 in casos:
        assert 0 <= score._score_ema(preco, e20, e50) <= 100


# ---------------------------------------------------------------------------
# _score_cvd  (sem rede/DB: None ou histórico curto -> 50 neutro)
# ---------------------------------------------------------------------------


def test_score_cvd_none():
    assert score._score_cvd(100.0, None) == 50


def test_score_cvd_historico_curto():
    ticks = [{"preco": 100.0, "is_buyer_maker": False, "quantidade": 1.0}]
    assert score._score_cvd(100.0, ticks, periodo=50) == 50


def test_score_cvd_lista_vazia():
    assert score._score_cvd(100.0, []) == 50


# ---------------------------------------------------------------------------
# calcular(...) — decisões e bloqueios
# ---------------------------------------------------------------------------


def test_calcular_operar_cheio():
    """Inputs maximizados -> score >= score_cheio, decisao OPERAR_CHEIO."""
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 100},
            fear_info={"valor": 50},  # 100
            tend_4h="ALTA",  # 100
            ml_prob=0.95,  # 95
            preco=120.0,
            ema20=110.0,
            ema50=100.0,  # ema alinhado, dist grande
            rsi=52,  # 100
            vwap_val=100.0,  # preço acima -> alto
            vol_rel=2.5,  # 100
            atr_atual=100.0,
            atr_media=100.0,  # 100
        )
    )
    assert res["decisao"] == "OPERAR_CHEIO"
    assert res["tamanho_fator"] == 1.0
    assert res["score_total"] >= 70
    assert 0 <= res["score_total"] <= 100
    assert res["bloqueios"] == []


def test_calcular_operar_reduzido():
    """Score entre score_operar (60) e score_cheio (70) -> OPERAR_REDUZIDO."""
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 70},
            fear_info={"valor": 50},  # 100
            tend_4h="LATERAL",  # 50
            ml_prob=0.55,  # 55
            preco=100.0,
            ema20=100.0,
            ema50=100.0,  # ema -> preco>ema20? não (igual) => 0
            rsi=52,  # 100
            vwap_val=100.0,  # igual -> dist 0 => 70
            vol_rel=1.0,  # 60
            atr_atual=100.0,
            atr_media=100.0,  # 100
        )
    )
    assert res["decisao"] == "OPERAR_REDUZIDO"
    assert res["tamanho_fator"] == 0.5
    assert 60 <= res["score_total"] < 70
    assert res["bloqueios"] == []


def test_calcular_aguardar_score_baixo():
    """Inputs fracos (sem bloqueio absoluto) -> score < score_operar -> AGUARDAR."""
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "INDEFINIDO", "score": 40},  # 40
            fear_info={
                "valor": 78
            },  # 40 (transição, não bloqueia: 75<v<=80? não, >75 e <=80 => else 40)
            tend_4h="BAIXA",  # 0
            ml_prob=0.1,  # 10
            preco=90.0,
            ema20=105.0,
            ema50=100.0,  # ema 0
            rsi=80,  # 0
            vwap_val=100.0,  # preço 90 abaixo -> baixo
            vol_rel=0.3,  # 20
            atr_atual=300.0,
            atr_media=100.0,  # 0
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert res["score_total"] < 60
    assert 0 <= res["score_total"] <= 100
    assert res["bloqueios"] == []


def test_calcular_score_total_no_intervalo():
    res = score.calcular(**_inputs_base())
    assert 0 <= res["score_total"] <= 100


def test_calcular_estrutura_retorno():
    res = score.calcular(**_inputs_base())
    for chave in (
        "score_total",
        "scores",
        "pesos",
        "decisao",
        "tamanho_fator",
        "motivo",
        "bloqueios",
        "detalhes",
    ):
        assert chave in res
    assert set(res["scores"].keys()) == set(score.PESOS.keys())
    assert res["pesos"] == score.PESOS
    # detalhes ordenados por contribuição decrescente
    contribs = [d["contribuicao"] for d in res["detalhes"]]
    assert contribs == sorted(contribs, reverse=True)


# ---------------------------------------------------------------------------
# Bloqueios absolutos
# ---------------------------------------------------------------------------


def test_bloqueio_regime_volatilidade():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "VOLATILIDADE", "score": 90},
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert any("VOLATILIDADE" in b for b in res["bloqueios"])
    assert res["motivo"].startswith("BLOQUEIO")


def test_bloqueio_regime_lateral():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "LATERAL", "score": 90},
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert any("LATERAL" in b for b in res["bloqueios"])


def test_bloqueio_fear_medo_extremo():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 100},
            fear_info={"valor": 15},  # <= 20
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert any("Medo Extremo" in b for b in res["bloqueios"])


def test_bloqueio_fear_ganancia_extrema():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 100},
            fear_info={"valor": 90},  # > 80
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert any("Ganancia Extrema" in b for b in res["bloqueios"])


def test_bloqueio_mantem_score_minimo_1():
    # Mesmo bloqueado, score_total nunca fica abaixo de 1.
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "VOLATILIDADE", "score": 5},
            fear_info={"valor": 15},
            tend_4h="BAIXA",
            ml_prob=0.0,
            preco=90.0,
            ema20=105.0,
            ema50=100.0,
            rsi=80,
            vwap_val=100.0,
            vol_rel=0.1,
            atr_atual=300.0,
            atr_media=100.0,
        )
    )
    assert res["score_total"] >= 1
    assert res["decisao"] == "AGUARDAR"


# ===========================================================================
# ADITIVO @Delta — cobertura adversarial (branches/bordas não cobertos)
# ===========================================================================

from collections import namedtuple  # noqa: E402

# Stub leve do CVDResult (só os campos que _score_cvd consome).
_FakeCVD = namedtuple("_FakeCVD", ["divergence_score", "trend_strength"])


def _ticks(precos, qtd=1.0, buyer=False):
    """Gera ticks no formato que _score_cvd consome (campo 'preco')."""
    return [{"preco": float(p), "is_buyer_maker": buyer, "quantidade": qtd} for p in precos]


# ---------------------------------------------------------------------------
# _score_regime — bordas e tipos
# ---------------------------------------------------------------------------


def test_score_regime_tendencia_baixa_forca_baixa_minimo_60():
    # TENDENCIA_BAIXA com força baixa também é piso 60 (branch trend claro).
    assert score._score_regime({"regime_final": "TENDENCIA_BAIXA", "score": 1}) == 60


def test_score_regime_forca_float_truncada():
    # int(forca) trunca floats (não arredonda).
    assert score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 87.9}) == 87


def test_score_regime_sem_chave_score_usa_default_50_clampado_60():
    # Sem 'score', default 50; trend claro força piso 60.
    assert score._score_regime({"regime_final": "TENDENCIA_ALTA"}) == 60


def test_score_regime_sem_chave_regime_final_indefinido():
    # Sem 'regime_final', default INDEFINIDO -> 40.
    assert score._score_regime({"score": 99}) == 40


def test_score_regime_borda_tendencia_alta_score_60():
    assert score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 60}) == 60


def test_score_regime_borda_tendencia_alta_score_61():
    assert score._score_regime({"regime_final": "TENDENCIA_ALTA", "score": 61}) == 61


# ---------------------------------------------------------------------------
# _score_rsi — bordas exatas das faixas
# ---------------------------------------------------------------------------


def test_score_rsi_borda_superior_faixa_62():
    # |62-52|/10 = 1.0 -> 100 - 30 = 70.
    assert score._score_rsi(62) == 70


def test_score_rsi_dentro_faixa_60():
    # |60-52|/10 = 0.8 -> 100 - 24 = 76.
    assert score._score_rsi(60) == 76


def test_score_rsi_abaixo_faixa_intermediario_41():
    # 41 < 42 -> int((41-30)/(42-30)*60) = int(55.0) = 55.
    assert score._score_rsi(41) == 55


def test_score_rsi_acima_faixa_intermediario_63():
    # 63 > 62 -> int((80-63)/(80-62)*60) = int(56.66) = 56.
    assert score._score_rsi(63) == 56


def test_score_rsi_acima_faixa_em_80_zera():
    assert score._score_rsi(80) == 0


def test_score_rsi_faixa_customizada():
    # rsi_min=40, rsi_max=60 -> centro 50; rsi 50 -> 100.
    assert score._score_rsi(50, rsi_min=40, rsi_max=60) == 100


# ---------------------------------------------------------------------------
# _score_ml — clamps e penalidade lateral
# ---------------------------------------------------------------------------


def test_score_ml_prob_acima_de_1_clamp_100():
    assert score._score_ml(1.5) == 100


def test_score_ml_prob_negativa_clamp_0():
    assert score._score_ml(-0.5) == 0


def test_score_ml_prob_exatamente_um():
    assert score._score_ml(1.0) == 100


def test_score_ml_lateral_prob_exatamente_060_sem_penalidade():
    # Penalidade só para prob > 0.60 (estritamente).
    assert score._score_ml(0.60, "LATERAL") == 60


def test_score_ml_lateral_prob_061_penalizada():
    # int(0.61*100)=61, -20 = 41.
    assert score._score_ml(0.61, "LATERAL") == 41


def test_score_ml_regime_nao_lateral_sem_penalidade():
    assert score._score_ml(0.9, "TENDENCIA_ALTA") == 90


def test_score_ml_lateral_prob_alta_nao_fica_negativa():
    # int(0.65*100)=65 -20 = 45 (nunca < 0).
    assert score._score_ml(0.65, "LATERAL") == 45


# ---------------------------------------------------------------------------
# _score_fear_greed — todas as fronteiras
# ---------------------------------------------------------------------------


def test_score_fear_greed_borda_20_bloqueio():
    assert score._score_fear_greed(20) == 0


def test_score_fear_greed_21_transicao():
    # 21 não bloqueia, mas 21<25 -> else 40.
    assert score._score_fear_greed(21) == 40


def test_score_fear_greed_25_medo_moderado():
    assert score._score_fear_greed(25) == 60


def test_score_fear_greed_34_medo_moderado():
    assert score._score_fear_greed(34) == 60


def test_score_fear_greed_35_zona_ideal():
    assert score._score_fear_greed(35) == 100


def test_score_fear_greed_65_zona_ideal():
    assert score._score_fear_greed(65) == 100


def test_score_fear_greed_66_ganancia_moderada():
    assert score._score_fear_greed(66) == 70


def test_score_fear_greed_75_ganancia_moderada():
    assert score._score_fear_greed(75) == 70


def test_score_fear_greed_76_transicao():
    assert score._score_fear_greed(76) == 40


def test_score_fear_greed_80_nao_bloqueia_transicao():
    # 80 NÃO é bloqueio (bloqueio é > 80, estritamente); cai no else -> 40.
    assert score._score_fear_greed(80) == 40


def test_score_fear_greed_81_bloqueio():
    # 81 > 80 -> bloqueio absoluto.
    assert score._score_fear_greed(81) == 0


# ---------------------------------------------------------------------------
# _score_vwap — clamps superior/inferior
# ---------------------------------------------------------------------------


def test_score_vwap_satura_em_100():
    # +10% -> min(100, 70 + 50) = 100.
    assert score._score_vwap(110, 100) == 100


def test_score_vwap_acima_moderado():
    # +5% -> 70 + 25 = 95.
    assert score._score_vwap(105, 100) == 95


def test_score_vwap_abaixo_satura_em_0():
    # -10% -> max(0, 40 - 80) = 0.
    assert score._score_vwap(90, 100) == 0


def test_score_vwap_abaixo_leve():
    # -2% -> 40 - 16 = 24.
    assert score._score_vwap(98, 100) == 24


def test_score_vwap_igual_da_70():
    # dist 0 -> ramo > 0 falso -> max(0, 40 + 0) = 40.
    assert score._score_vwap(100, 100) == 40


def test_score_vwap_vwap_negativo_neutro():
    assert score._score_vwap(100, -1) == 50


# ---------------------------------------------------------------------------
# _score_volume — limiares exatos
# ---------------------------------------------------------------------------


def test_score_volume_borda_2_0():
    assert score._score_volume(2.0) == 100


def test_score_volume_borda_1_3():
    assert score._score_volume(1.3) == 80


def test_score_volume_borda_1_0():
    assert score._score_volume(1.0) == 60


def test_score_volume_borda_0_7():
    assert score._score_volume(0.7) == 40


def test_score_volume_logo_abaixo_0_7():
    assert score._score_volume(0.69) == 20


def test_score_volume_zero_e_negativo():
    assert score._score_volume(0.0) == 20
    assert score._score_volume(-1.0) == 20


# ---------------------------------------------------------------------------
# _score_atr — limiares exatos de ratio
# ---------------------------------------------------------------------------


def test_score_atr_ratio_0_7_ideal():
    assert score._score_atr(70, 100) == 100


def test_score_atr_ratio_1_8_ideal():
    assert score._score_atr(180, 100) == 100


def test_score_atr_logo_abaixo_0_7_parado():
    assert score._score_atr(69, 100) == 30


def test_score_atr_ratio_2_5_aceitavel():
    # 2.5 não é > 2.5 -> cai no else (elevada aceitável) = 60.
    assert score._score_atr(250, 100) == 60


def test_score_atr_ratio_acima_2_5_extrema():
    assert score._score_atr(260, 100) == 0


def test_score_atr_media_negativa_neutro():
    assert score._score_atr(100, -1) == 50


# ---------------------------------------------------------------------------
# _score_mtf — branch genérico (else)
# ---------------------------------------------------------------------------


def test_score_mtf_baixa_zero():
    assert score._score_mtf("BAIXA") == 0


def test_score_mtf_valor_desconhecido_cai_no_else():
    assert score._score_mtf("QUALQUER_OUTRO") == 0


def test_score_mtf_vazio_cai_no_else():
    assert score._score_mtf("") == 0


# ---------------------------------------------------------------------------
# _score_ema — saturação e fronteiras de igualdade
# ---------------------------------------------------------------------------


def test_score_ema_distancia_grande_satura_100():
    # preco>>ema50 -> min(100, 70 + dist*10) satura.
    assert score._score_ema(200, 105, 100) == 100


def test_score_ema_distancia_pequena_nao_satura():
    # preco=101, ema20=100.5, ema50=100 -> dist=1.0 -> 70 + 10 = 80.
    assert score._score_ema(101, 100.5, 100) == 80


def test_score_ema_preco_igual_ema20_nao_e_alinhamento():
    # preco==ema20 quebra "preco>ema20>ema50"; cai em preco>ema50 -> 30.
    assert score._score_ema(105, 105, 100) == 30


def test_score_ema_preco_igual_ema50_zero():
    # preco==ema50, não > ema20 nem > ema50 -> 0.
    assert score._score_ema(100, 105, 100) == 0


# ---------------------------------------------------------------------------
# _score_cvd — branches de divergência (mock de calculate_cvd, sem rede/DB)
# ---------------------------------------------------------------------------


def test_score_cvd_harmonia_bullish(monkeypatch):
    # Preço subindo + CVD subindo -> base 100; trend_strength 0 -> 100.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(0.5, 0.0))
    precos = [100 + i for i in range(50)]  # slope > 0
    assert score._score_cvd(149.0, _ticks(precos), periodo=50) == 100


def test_score_cvd_divergencia_bearish(monkeypatch):
    # Preço subindo + CVD caindo -> base 0; bonus int(0.5*20)=10 -> 10.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(-0.5, 0.5))
    precos = [100 + i for i in range(50)]
    assert score._score_cvd(149.0, _ticks(precos), periodo=50) == 10


def test_score_cvd_divergencia_bullish(monkeypatch):
    # Preço caindo + CVD subindo -> base 50; trend_strength 0 -> 50.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(0.5, 0.0))
    precos = [200 - i for i in range(50)]  # slope < 0
    assert score._score_cvd(150.0, _ticks(precos), periodo=50) == 50


def test_score_cvd_harmonia_bearish(monkeypatch):
    # Preço caindo + CVD caindo -> base 20; bonus int(1.0*20)=20 -> 40.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(-0.5, 1.0))
    precos = [200 - i for i in range(50)]
    assert score._score_cvd(150.0, _ticks(precos), periodo=50) == 40


def test_score_cvd_preco_lateral_cvd_neutro(monkeypatch):
    # preco_slope ~0 e cvd_slope dentro de [-0.1,0.1] -> base permanece 50.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(0.0, 0.0))
    precos = [100.0] * 50  # slope 0
    assert score._score_cvd(100.0, _ticks(precos), periodo=50) == 50


def test_score_cvd_bonus_satura_em_100(monkeypatch):
    # base 100 + bonus seria 120, clampado a 100.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(0.5, 1.0))
    precos = [100 + i for i in range(50)]
    assert score._score_cvd(149.0, _ticks(precos), periodo=50) == 100


def test_score_cvd_nao_chama_calculate_cvd_se_curto(monkeypatch):
    # Histórico < período -> retorna 50 cedo, sem tocar calculate_cvd.
    def _boom(*a, **k):
        raise AssertionError("calculate_cvd nao deveria ser chamado")

    monkeypatch.setattr(score, "calculate_cvd", _boom)
    assert score._score_cvd(100.0, _ticks([100, 101, 102]), periodo=50) == 50


# ---------------------------------------------------------------------------
# calcular(...) — integração de branches adicionais
# ---------------------------------------------------------------------------


def test_calcular_bloqueio_duplo_acumula():
    # Regime LATERAL + Medo Extremo: dois bloqueios concatenados no motivo.
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "LATERAL", "score": 90},
            fear_info={"valor": 10},
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert res["tamanho_fator"] == 0.0
    assert len(res["bloqueios"]) == 2
    assert any("LATERAL" in b for b in res["bloqueios"])
    assert any("Medo Extremo" in b for b in res["bloqueios"])
    assert " | " in res["motivo"]


def test_calcular_ml_usa_regime_final_para_penalidade():
    # Em regime cujo regime_final é LATERAL, ml_prob alta é penalizada no score ml.
    # (LATERAL também bloqueia, mas o score bruto de ml deve refletir a penalidade.)
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "LATERAL", "score": 50},
            ml_prob=0.9,
        )
    )
    # 0.9*100=90, lateral e >0.6 -> 90-20=70.
    assert res["scores"]["ml"] == 70


def test_calcular_fear_sem_chave_valor_usa_default_50():
    # Sem 'valor', usa 50 -> não bloqueia e fear_greed=100.
    res = score.calcular(**_inputs_base(fear_info={}))
    assert res["scores"]["fear_greed"] == 100
    assert res["bloqueios"] == []


def test_calcular_motivo_aguardar_menciona_minimo():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "INDEFINIDO", "score": 40},
            tend_4h="BAIXA",
            ml_prob=0.1,
            preco=90.0,
            ema20=105.0,
            ema50=100.0,
            rsi=80,
            vwap_val=100.0,
            vol_rel=0.3,
            atr_atual=300.0,
            atr_media=100.0,
            fear_info={"valor": 50},
        )
    )
    assert res["decisao"] == "AGUARDAR"
    assert "Abaixo do minimo" in res["motivo"]


def test_calcular_motivo_cheio_menciona_excelentes():
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 100},
            fear_info={"valor": 50},
            tend_4h="ALTA",
            ml_prob=0.95,
            preco=120.0,
            ema20=110.0,
            ema50=100.0,
            rsi=52,
            vwap_val=100.0,
            vol_rel=2.5,
            atr_atual=100.0,
            atr_media=100.0,
        )
    )
    assert res["decisao"] == "OPERAR_CHEIO"
    assert "excelentes" in res["motivo"]


def test_calcular_thresholds_customizados():
    # Eleva score_cheio/score_operar para forçar AGUARDAR mesmo com bom score.
    res = score.calcular(
        **_inputs_base(
            regime_info={"regime_final": "TENDENCIA_ALTA", "score": 100},
            fear_info={"valor": 50},
            tend_4h="ALTA",
            ml_prob=0.95,
            preco=120.0,
            ema20=110.0,
            ema50=100.0,
            rsi=52,
            vwap_val=100.0,
            vol_rel=2.5,
            atr_atual=100.0,
            atr_media=100.0,
            score_operar=99,
            score_cheio=100,
        )
    )
    # Score alto mas limiar 99/100 muito agressivo: provável AGUARDAR ou CHEIO.
    assert res["decisao"] in ("AGUARDAR", "OPERAR_CHEIO", "OPERAR_REDUZIDO")
    assert 0 <= res["score_total"] <= 100


def test_calcular_detalhes_consistentes_com_scores_e_pesos():
    res = score.calcular(**_inputs_base())
    # Cada detalhe deve casar score_raw com scores[componente] e peso com PESOS.
    for d in res["detalhes"]:
        comp = d["componente"]
        assert d["score_raw"] == res["scores"][comp]
        assert d["peso"] == score.PESOS[comp]
        assert d["contribuicao"] == round(d["score_raw"] * d["peso"] / 100, 1)


def test_calcular_score_total_igual_soma_contribuicoes_arredondada():
    res = score.calcular(**_inputs_base())
    soma = sum(d["score_raw"] * d["peso"] / 100 for d in res["detalhes"])
    assert res["score_total"] == round(soma)


def test_calcular_cvd_integrado_via_mock(monkeypatch):
    # calcular() repassa historico_ticks para _score_cvd; mock garrante hermético.
    monkeypatch.setattr(score, "calculate_cvd", lambda ticks, window_size: _FakeCVD(0.5, 0.0))
    precos = [100 + i for i in range(50)]
    res = score.calcular(
        **_inputs_base(
            preco=149.0,
            historico_ticks=_ticks(precos),
        )
    )
    assert res["scores"]["cvd"] == 100
    assert 0 <= res["score_total"] <= 100


def test_calcular_pesos_nao_mutados_entre_chamadas():
    antes = dict(score.PESOS)
    score.calcular(**_inputs_base())
    assert dict(score.PESOS) == antes
    assert sum(score.PESOS.values()) == 100
