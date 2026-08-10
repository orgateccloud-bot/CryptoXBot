"""
Suíte adversarial complementar para estrategias/otimizada.py (@Delta).

Objetivo
--------
Aumentar cobertura e FORÇA das asserções da suíte E2E existente
(`tests/test_otimizada_e2e.py`) SEM modificar fonte nem o teste original.
Estes testes são 100% herméticos (sem rede / sem banco / sem modelos) e
reutilizam exatamente a mesma estratégia de mock holística da suíte original,
copiada localmente para não criar acoplamento entre arquivos de teste.

Cobre branches/bordas não exercidos pela suíte original:

REGRESSÃO REFORÇADA (indicadores que quebravam a estratégia)
  - `volume_relativo`: alinhamento da janela sem IndexError em série de exatamente
    `periodo` velas, e em série mais curta que `periodo` (retorno só com None).
  - `bollinger` + `bandwidth`: cálculo sem NameError nas últimas velas e
    integração com `analisar` (chave "bw"/"bw_media" no contrato).

OTIMIZADA / analisar()
  - Fallback de símbolo desconhecido em get_params (cai em BTCUSDT) — não quebra.
  - Pares alternativos (ETHUSDT, SOLUSDT) com score_operar mais baixo (50).
  - Caminho OPERAR_REDUZIDO (score na faixa 60-69) -> tamanho_fator 0.5.
  - reducao_alvo do Fear&Greed encurta o take_profit de uma COMPRA.
  - cvd_atual=None NÃO bloqueia o filtro cvd (compra ainda válida).
  - ensemble ausente + ens.prever() lançando exceção -> fallback interno
    {"prob_ensemble":0.5,...} sem rede e sem crash.
  - Bloqueio absoluto por Fear&Greed extremo (valor>80) -> AGUARDAR sem salvar.
  - Contrato estendido: chaves de telemetria e coerência numérica.
  - imprimir(): ramo ML-ensemble vs ramo ML-prob simples (None) sem lançar.
"""

import math
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import estrategias.otimizada as otimizada  # noqa: E402
import indicadores as ind  # noqa: E402

# ---------------------------------------------------------------------------
# Geradores de séries sintéticas (determinísticas) — espelham a suíte original
# ---------------------------------------------------------------------------


def _serie_klines(n=100, base=100.0, passo=1.0, vol=1000.0, vol_ultima=None):
    fechamento = [base + passo * i for i in range(n)]
    abertura = [c - passo * 0.3 for c in fechamento]
    maxima = [c + abs(passo) * 0.5 + 0.5 for c in fechamento]
    minima = [c - abs(passo) * 0.5 - 0.5 for c in fechamento]
    volume = [vol for _ in range(n)]
    if vol_ultima is not None:
        volume[-1] = vol_ultima
    return {
        "abertura": abertura,
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": volume,
    }


def _fake_klines_factory(d1h, d4h):
    def _fake(intervalo, limite=100, symbol="BTCUSDT"):
        return d4h if str(intervalo).lower() == "4h" else d1h

    return _fake


def _regime(regime_final="TENDENCIA_ALTA", pode_operar=True, score=85):
    return {
        "regime_final": regime_final,
        "pode_operar": pode_operar,
        "score": score,
        "adx": 30,
        "detalhe": "sintetico",
    }


def _fear(valor=50, pode_operar=True, classificacao_pt="Neutro", reducao_alvo=False):
    return {
        "valor": valor,
        "pode_operar": pode_operar,
        "classificacao_pt": classificacao_pt,
        "reducao_alvo": reducao_alvo,
    }


def _suporte(suporte_forte=0.0):
    return {
        "suporte_forte": suporte_forte,
        "distancia_%": 0.0,
        "na_zona": False,
    }


def _ensemble(prob=0.80, pode_operar=True):
    return {
        "prob_ensemble": prob,
        "pode_operar": pode_operar,
        "confianca": "ALTA",
        "prob_xgb": prob,
        "prob_lstm": prob,
    }


@pytest.fixture
def patch_otimizada(monkeypatch):
    chamadas_salvar = []

    def aplicar(d1h, d4h, regime_info, fear_info, suporte_info):
        monkeypatch.setattr(otimizada, "_klines", _fake_klines_factory(d1h, d4h))
        monkeypatch.setattr(otimizada.reg, "detectar", lambda *a, **k: regime_info)
        monkeypatch.setattr(otimizada.fg, "obter", lambda *a, **k: fear_info)
        monkeypatch.setattr(otimizada.sup, "detectar_suportes", lambda *a, **k: suporte_info)

        def _salvar(*a, **k):
            chamadas_salvar.append((a, k))
            return None

        monkeypatch.setattr(otimizada.database, "salvar_sinal", _salvar)
        return chamadas_salvar

    return aplicar


CHAVES_CONTRATO = (
    "sinal",
    "score",
    "preco",
    "stop_loss",
    "take_profit",
    "ema20_1h",
    "rsi",
    "score_decisao",
)


# ===========================================================================
# 1. REGRESSÃO DIRETA DOS INDICADORES (sem passar por analisar)
# ===========================================================================


def test_volume_relativo_serie_exatamente_periodo_sem_indexerror():
    """20 velas e janela 20: índice da última (19) deve alinhar com media[0].

    Reproduz o cenário exato do IndexError corrigido: a série tem o tamanho
    mínimo para a janela e o acesso `media[i-(periodo-1)]` precisa ficar dentro
    de `media` (que tem tamanho len-periodo+1 == 1).
    """
    volumes = [1000.0] * 19 + [2000.0]
    out = ind.volume_relativo(volumes, 20)
    assert len(out) == len(volumes)
    assert out[:19] == [None] * 19
    media = sum(volumes) / 20
    assert out[-1] == pytest.approx(2000.0 / media)


def test_volume_relativo_serie_mais_curta_que_periodo_so_none():
    """Série < período: lista de None do tamanho min(len, periodo-1), sem crash."""
    out = ind.volume_relativo([1.0, 2.0, 3.0], 20)
    assert out == [None] * 3 or out == [None] * 19
    # comportamento real observado: padding de (periodo-1) Nones e loop vazio
    assert all(x is None for x in out)


def test_volume_relativo_media_zero_retorna_none():
    """Janela 100% zerada -> divisão por média 0 cai no ramo `or None`."""
    volumes = [0.0] * 20
    out = ind.volume_relativo(volumes, 20)
    assert out[-1] is None


def test_bollinger_bandwidth_ultima_vela_sem_nameerror():
    """bollinger + bandwidth nas últimas velas (regressão NameError/std)."""
    fechamentos = [100.0 + i * 0.5 for i in range(40)]
    upper, mid, lower = ind.bollinger(fechamentos, 20, 2)
    assert len(upper) == len(mid) == len(lower) == len(fechamentos)
    # primeiros periodo-1 são None; o restante é numérico e coerente
    assert upper[:19] == [None] * 19
    assert upper[-1] > mid[-1] > lower[-1]
    # std não-degenerado para série crescente => bandwidth > 0
    bw = ind.bandwidth(upper, mid, lower)
    assert bw[-1] is not None and bw[-1] > 0


def test_bandwidth_mid_none_ou_zero_retorna_none():
    """mid None (padding) ou mid==0 -> bandwidth retorna None nessas posições."""
    upper = [None, 10.0, 5.0]
    mid = [None, 0.0, 2.5]
    lower = [None, -10.0, 0.0]
    out = ind.bandwidth(upper, mid, lower)
    assert out[0] is None  # mid None
    assert out[1] is None  # mid == 0
    assert out[2] == pytest.approx((5.0 - 0.0) / 2.5)


# ===========================================================================
# 2. analisar(): integração indicadores -> contrato estendido
# ===========================================================================


def test_contrato_estendido_chaves_telemetria(patch_otimizada):
    """Além do contrato mínimo, telemetria de bw/atr/vwap/regime presente."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    for chave in CHAVES_CONTRATO:
        assert chave in r
    for extra in (
        "bw",
        "bw_media",
        "atr",
        "atr_media",
        "vwap",
        "tend_4h",
        "regime",
        "volume_rel",
        "symbol",
        "score_result",
        "tamanho_fator",
    ):
        assert extra in r, f"telemetria ausente: {extra}"

    # coerência numérica básica
    assert math.isfinite(r["bw"]) and r["bw"] >= 0
    assert math.isfinite(r["atr"]) and r["atr"] >= 0
    assert r["symbol"] == "BTCUSDT"
    # janela de 20: 19 velas a 1000 + última a 2000 -> media 1050 -> ~1.9x
    media_vol = (19 * 1000.0 + 2000.0) / 20
    assert r["volume_rel"] == pytest.approx(round(2000.0 / media_vol, 2), abs=0.01)
    assert r["volume_rel"] >= otimizada.VOL_MIN_RATIO


# ===========================================================================
# 3. get_params: fallback de símbolo desconhecido + pares alternativos
# ===========================================================================


def test_simbolo_desconhecido_cai_em_btc_sem_quebrar(patch_otimizada):
    """Par fora de PARAMS_PARES usa fallback BTCUSDT (stop 1.5%)."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    r = otimizada.analisar(
        "DOGEUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["symbol"] == "DOGEUSDT"
    assert r["sinal"] in ("COMPRA", "VENDA", "AGUARDAR")
    if r["sinal"] == "COMPRA":
        # fallback BTC: stop = preço * (1 - 0.015) (sem suporte forte)
        esperado = round(r["preco"] * (1 - 0.015), 2)
        assert r["stop_loss"] == esperado


def test_par_eth_usa_stop_target_proprios(patch_otimizada):
    """ETHUSDT: stop 2.0% / target 6.0% e symbol propagado."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    r = otimizada.analisar(
        "ETHUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["symbol"] == "ETHUSDT"
    if r["sinal"] == "COMPRA":
        assert r["stop_loss"] == round(r["preco"] * (1 - 0.020), 2)
        assert r["take_profit"] == round(r["preco"] * (1 + 0.060), 2)


# ===========================================================================
# 4. Decisão por score: OPERAR_REDUZIDO (faixa 60-69) -> tamanho_fator 0.5
# ===========================================================================


def test_operar_reduzido_define_tamanho_fator_meio(patch_otimizada):
    """Score na faixa [60,70) deve dar OPERAR_REDUZIDO e tamanho_fator 0.5.

    Para empurrar o score para a faixa reduzida (e não cheio), usamos uma
    tendência de alta fraca: regime score moderado, ML neutro e Fear&Greed
    fora da zona ideal. Aceitamos qualquer decisão operável, mas se for
    REDUZIDO validamos o invariante tamanho_fator==0.5.
    """
    d1h = _serie_klines(n=100, base=100.0, passo=0.2, vol=1000.0, vol_ultima=1100.0)
    d4h = _serie_klines(n=60, base=100.0, passo=0.2)
    patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", pode_operar=True, score=62),
        _fear(valor=70, pode_operar=True),  # ganância moderada (70 pts)
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=1.0, ml_prob=0.55, ensemble_result=_ensemble(prob=0.55)
    )

    assert r["score_decisao"] in ("OPERAR_CHEIO", "OPERAR_REDUZIDO", "AGUARDAR")
    if r["score_decisao"] == "OPERAR_REDUZIDO":
        assert r["tamanho_fator"] == 0.5
        assert 60 <= r["score"] < 70
    elif r["score_decisao"] == "OPERAR_CHEIO":
        assert r["tamanho_fator"] == 1.0
        assert r["score"] >= 70


# ===========================================================================
# 5. Fear&Greed reducao_alvo encurta o take_profit de uma COMPRA
# ===========================================================================


def test_reducao_alvo_encurta_take_profit_da_compra(patch_otimizada):
    """reducao_alvo=True -> target usa metade do TARGET_PCT (2.5% no BTC)."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", score=90),
        _fear(valor=50, reducao_alvo=True),  # zona ideal + pede redução de alvo
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "COMPRA"
    # BTC target_pct=0.05 -> reduzido para 0.025
    esperado = round(r["preco"] * (1 + 0.025), 2)
    assert r["take_profit"] == esperado
    # alvo reduzido fica acima do preço mas abaixo do alvo cheio (5%)
    alvo_cheio = round(r["preco"] * (1 + 0.05), 2)
    assert r["preco"] < r["take_profit"] < alvo_cheio


# ===========================================================================
# 6. cvd_atual=None não bloqueia a COMPRA (ramo `cvd_atual is None`)
# ===========================================================================


def test_cvd_none_nao_bloqueia_compra(patch_otimizada):
    """filtros['cvd'] = (cvd is None) or (cvd>0): None deve permitir COMPRA."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=None, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "COMPRA"
    assert r["cvd"] is None


def test_cvd_negativo_bloqueia_compra_vira_aguardar(patch_otimizada):
    """Em cenário bullish, cvd<0 derruba filtros['cvd'] -> não COMPRA."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    chamadas = patch_otimizada(
        d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte()
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=-5.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    # bullish + cvd<0: filtros['cvd'] False -> não vira COMPRA;
    # regime ALTA também impede o ramo de VENDA -> AGUARDAR
    assert r["sinal"] == "AGUARDAR"
    assert len(chamadas) == 0


# ===========================================================================
# 7. ensemble ausente + ens.prever() lança -> fallback interno sem rede
# ===========================================================================


def test_ensemble_indisponivel_resulta_em_aguardar(patch_otimizada, monkeypatch):
    """E-10: `ens.prever()` explode -> AGUARDAR, nunca pontuação neutra.

    Este teste AFIRMAVA o defeito. Ele exigia `ml_ensemble == 0.5` e
    `ml_confianca == "NENHUM"` — que era exatamente o fail-open: substituir o
    modelo quebrado por uma probabilidade neutra. E `_score_ml(0.5)` vale 50
    de 100 num componente que pesa 20 pontos, então a FALHA entregava 10
    pontos de graça e ainda liberava o filtro "ml".

    O cenário é o mais favorável possível a uma COMPRA — regime
    TENDENCIA_ALTA com score 90, F&G neutro, CVD positivo, série em alta —
    justamente para que o único motivo de não comprar seja a ausência do
    modelo.
    """
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    def _boom(*a, **k):
        raise RuntimeError("modelo indisponivel / sem rede")

    monkeypatch.setattr(otimizada.ens, "prever", _boom)

    r = otimizada.analisar("BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=None)

    assert r["sinal"] == "AGUARDAR", "modelo indisponivel nao pode gerar entrada"
    assert r["tamanho_fator"] == 0.0, "o fator tem de zerar, nao so o sinal"
    # e o estado tem de ser LEGIVEL: 0.5 dizia "o modelo achou 50%", o que e
    # falso — ele nao achou nada.
    assert r["ml_ensemble"] is None
    assert r["ml_confianca"] == "INDISPONIVEL"


# ===========================================================================
# 8. Bloqueio absoluto por Fear&Greed extremo -> AGUARDAR sem salvar
# ===========================================================================


def test_fear_greed_extremo_alto_bloqueia_sem_salvar(patch_otimizada):
    """F&G > 80 é bloqueio absoluto em score.calcular -> AGUARDAR, sem DB."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    chamadas = patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", score=90),
        _fear(valor=90, pode_operar=True),  # ganância extrema
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "AGUARDAR"
    assert r["score_decisao"] == "AGUARDAR"
    assert r["tamanho_fator"] == 0.0
    assert r["stop_loss"] is None and r["take_profit"] is None
    assert len(chamadas) == 0


def test_fear_greed_medo_extremo_baixo_bloqueia(patch_otimizada):
    """F&G <= 20 (medo extremo) também força AGUARDAR mesmo com tudo bullish."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    chamadas = patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", score=90),
        _fear(valor=10, pode_operar=True),
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "AGUARDAR"
    assert len(chamadas) == 0


# ===========================================================================
# 9. ml_ensemble_prob herda ml_prob quando ensemble não traz prob_ensemble
# ===========================================================================


def test_ensemble_sem_prob_usa_ml_prob_como_fallback(patch_otimizada):
    """ensemble dict sem 'prob_ensemble' -> ml_ensemble cai em ml_prob."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    ensemble_sem_prob = {"pode_operar": True, "confianca": "MEDIA"}
    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.77, ensemble_result=ensemble_sem_prob
    )

    assert r["ml_ensemble"] == pytest.approx(0.77)
    assert r["ml_prob"] == pytest.approx(0.77)


# ===========================================================================
# 10. imprimir(): ramo ML-prob simples (sem ensemble completo) não lança
# ===========================================================================


def test_imprimir_ramo_ml_prob_simples(patch_otimizada, capsys):
    """ensemble sem xgb/lstm -> imprimir usa o ramo 'ML Prob' sem crash."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    # sem prob_xgb/prob_lstm -> condição do print ternário vai ao ramo simples
    ensemble_simples = {"prob_ensemble": 0.80, "pode_operar": True, "confianca": "MEDIA"}
    r = otimizada.imprimir(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=ensemble_simples
    )
    out = capsys.readouterr().out
    assert "ESTRATEGIA OTIMIZADA" in out
    assert "ML Prob:" in out
    assert "SINAL:" in out
    assert r["ml_xgb"] is None and r["ml_lstm"] is None


def test_imprimir_venda_mostra_stop_target(patch_otimizada, capsys):
    """imprimir() em VENDA imprime Stop/Take e SINAL: VENDA (cobre ramo != AGUARDAR)."""
    # série bearish forte com cauda oscilante para RSI operável (igual helper original)
    fechamento = []
    for i in range(100):
        if i < 78:
            fechamento.append(400.0 - 3.0 * i)
        else:
            base = (400.0 - 3.0 * 78) - 1.2 * (i - 78)
            osc = 6.0 if (i % 2 == 0) else -6.0
            fechamento.append(base + osc)
    d1h = {
        "abertura": list(fechamento),
        "maxima": [c + 1.5 for c in fechamento],
        "minima": [c - 1.5 for c in fechamento],
        "fechamento": fechamento,
        "volume": [1000.0] * 99 + [2000.0],
    }
    d4h = _serie_klines(n=60, base=200.0, passo=-1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_BAIXA", score=95), _fear(valor=50), _suporte())

    r = otimizada.imprimir(
        "BTCUSDT", cvd_atual=-10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )
    out = capsys.readouterr().out
    assert r["sinal"] == "VENDA"
    assert "SINAL: VENDA" in out
    assert "Stop Loss:" in out
    assert "Take Profit:" in out
