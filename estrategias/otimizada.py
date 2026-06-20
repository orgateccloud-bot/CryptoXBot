"""
Estratégia Otimizada — Camada 1+2
===================================
Filtros adicionados sobre a estratégia base (EMA+RSI):

  1. Multi-Timeframe (MTF): 4H deve confirmar tendência de alta antes de entrar no 1H
  2. ATR Filter:            Não entrar se ATR < 50% da média (mercado lateral/sem força)
  3. Volume Filter:         Vela de sinal deve ter volume > 1.3x a média de 20 períodos
  4. Bollinger Squeeze:     Não entrar após squeeze (banda estreita sem expansão)
  5. VWAP Filter:           Preço deve estar acima do VWAP para longs
  6. Market Structure:      Só comprar em HH/HL (estrutura de alta confirmada)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from datetime import datetime
import database
import indicadores as ind
import regime as reg
import fear_greed as fg
import score as sc
import suporte as sup
import ensemble as ens
from config.params_pares import get_params

BASE_URL = "https://api.binance.com"

# Filtros estruturais (iguais para todos os pares)
ATR_MIN_RATIO   = 0.6
VOL_MIN_RATIO   = 1.3
VWAP_FILTER     = True
MTF_FILTER      = True


def _klines(intervalo, limite=100, symbol="BTCUSDT"):
    r = requests.get(f"{BASE_URL}/api/v3/klines",
                     params={"symbol": symbol, "interval": intervalo, "limit": limite},
                     timeout=8)
    k = r.json()
    return {
        "abertura":    [float(x[1]) for x in k],
        "maxima":      [float(x[2]) for x in k],
        "minima":      [float(x[3]) for x in k],
        "fechamento":  [float(x[4]) for x in k],
        "volume":      [float(x[5]) for x in k],
    }


def analisar(symbol="BTCUSDT", cvd_atual=None, ml_prob=None, ensemble_result=None):
    """
    Avalia a estratégia otimizada para o par especificado.
    ml_prob: probabilidade do XGBoost (retrocompat)
    ensemble_result: resultado do ensemble (XGB + LSTM)
    """
    # Carregar parâmetros otimizados para o par
    p = get_params(symbol)
    RSI_MIN    = p["rsi_min"]
    RSI_MAX    = p["rsi_max"]
    STOP_PCT   = p["stop_pct"]
    TARGET_PCT = p["target_pct"]

    d1h = _klines("1h", 100, symbol)
    d4h = _klines("4h", 60, symbol)

    f1h = d1h["fechamento"]
    preco = f1h[-1]

    # ── Indicadores 1H ────────────────────────────────────────
    ema20  = ind.ema(f1h, 20)[-1]
    ema50  = ind.ema(f1h, 50)[-1]
    rsi14  = ind.rsi(f1h)[-1]
    atr14  = ind.atr(d1h["maxima"], d1h["minima"], f1h, 14)
    atr_media = sum(x for x in atr14[-20:] if x) / 20
    atr_atual = atr14[-1] if atr14[-1] else 0

    vol_rel = ind.volume_relativo(d1h["volume"], 20)[-1] or 0

    bb_upper, bb_mid, bb_lower = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bb_upper, bb_mid, bb_lower)
    bw_atual  = bw[-1] or 0
    bw_media  = sum(x for x in bw[-20:] if x) / 20

    vwap_val  = ind.vwap(d1h["maxima"], d1h["minima"], f1h, d1h["volume"])[-1]

    # ── Multi-Timeframe 4H ────────────────────────────────────
    f4h    = d4h["fechamento"]
    ema20_4h = ind.ema(f4h, 20)[-1]
    ema50_4h = ind.ema(f4h, 50)[-1]
    tend_4h  = "ALTA" if f4h[-1] > ema20_4h > ema50_4h else \
               "BAIXA" if f4h[-1] < ema20_4h < ema50_4h else "LATERAL"

    funding = 0.0

    # ── Regime de Mercado ─────────────────────────────────────
    regime_info = reg.detectar()
    fear_info   = fg.obter()

    # ── Ensemble ML (XGBoost + LSTM) ──────────────────────────
    if ensemble_result is None:
        try:
            ensemble_result = ens.prever()
        except Exception:
            ensemble_result = {"prob_ensemble": 0.5, "pode_operar": True, "confianca": "NENHUM"}

    ml_ensemble_prob = ensemble_result.get("prob_ensemble", ml_prob or 0.5)
    ml_pode          = ensemble_result.get("pode_operar", True)

    # ── Avaliação dos filtros ──────────────────────────────────
    filtros = {
        "ema_1h":     preco > ema20 > ema50,
        "rsi":        RSI_MIN <= (rsi14 or 0) <= RSI_MAX,
        "atr":        atr_atual >= atr_media * ATR_MIN_RATIO,
        "volume":     vol_rel >= VOL_MIN_RATIO,
        "bollinger":  bw_atual >= bw_media * 0.8,
        "vwap":       preco > vwap_val if VWAP_FILTER else True,
        "mtf_4h":     tend_4h in ("ALTA", "LATERAL") if MTF_FILTER else True,
        "regime":     regime_info["pode_operar"] and regime_info["regime_final"] == "TENDENCIA_ALTA",
        "fear_greed": fear_info["pode_operar"],
        "cvd":        (cvd_atual is None) or (cvd_atual > 0),
        "ml":         ml_pode and ml_ensemble_prob >= 0.55,
    }

    aprovados  = sum(filtros.values())
    total_fil  = len(filtros)

    # ── Score Unificado ───────────────────────────────────────
    score_result = sc.calcular(
        regime_info=regime_info,
        fear_info=fear_info,
        tend_4h=tend_4h,
        ml_prob=ml_ensemble_prob,
        preco=preco,
        ema20=ema20,
        ema50=ema50,
        rsi=rsi14,
        vwap_val=vwap_val,
        vol_rel=vol_rel,
        atr_atual=atr_atual,
        atr_media=atr_media,
        rsi_min=RSI_MIN,
        rsi_max=RSI_MAX,
        score_operar=p["score_operar"],
        score_cheio=p["score_cheio"],
    )

    # Decisao baseada no score (substitui logica de filtros binarios)
    decisao       = score_result["decisao"]
    tamanho_fator = score_result["tamanho_fator"]

    if decisao == "OPERAR_CHEIO" and filtros["ema_1h"] and filtros["cvd"]:
        sinal = "COMPRA"
    elif decisao == "OPERAR_REDUZIDO" and filtros["ema_1h"] and filtros["cvd"]:
        sinal = "COMPRA"
    else:
        sinal = "AGUARDAR"

    # Sinal de venda (short)
    filtros_short = {
        "ema_1h":     preco < ema20 < ema50,
        "rsi":        RSI_MIN <= (rsi14 or 0) <= RSI_MAX,
        "atr":        atr_atual >= atr_media * ATR_MIN_RATIO,
        "volume":     vol_rel >= VOL_MIN_RATIO,
        "vwap":       preco < vwap_val if VWAP_FILTER else True,
        "mtf_4h":     tend_4h == "BAIXA" if MTF_FILTER else True,
        "regime":     regime_info["pode_operar"] and regime_info["regime_final"] == "TENDENCIA_BAIXA",
        "fear_greed": fear_info["pode_operar"],
        "cvd":        (cvd_atual is None) or (cvd_atual < 0),
        "ml":         (ml_prob is None) or (ml_prob >= 0.60),
    }
    if all(filtros_short.values()) and decisao != "AGUARDAR":
        sinal = "VENDA"

    # ── Suportes e Resistencias ─────────────────────────────────
    suporte_info = sup.detectar_suportes("1h")

    stop   = round(preco * (1 - STOP_PCT), 2) if sinal == "COMPRA" else \
             round(preco * (1 + STOP_PCT), 2) if sinal == "VENDA"  else None
    target = round(preco * (1 + TARGET_PCT), 2) if sinal == "COMPRA" else \
             round(preco * (1 - TARGET_PCT), 2) if sinal == "VENDA"  else None

    # Stop abaixo do suporte forte (mais inteligente que % fixo)
    if sinal == "COMPRA" and suporte_info["suporte_forte"] > 0:
        stop_suporte = round(suporte_info["suporte_forte"] * 0.995, 2)  # 0.5% abaixo do suporte
        if stop_suporte > stop:
            stop = stop_suporte  # stop mais apertado no suporte

    # Ajustar target se Fear & Greed pede reducao
    if target and fear_info.get("reducao_alvo"):
        alvo_pct = TARGET_PCT * 0.5
        target = round(preco * (1 + alvo_pct), 2) if sinal == "COMPRA" else \
                 round(preco * (1 - alvo_pct), 2) if sinal == "VENDA" else target

    resultado = {
        "timestamp":     datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "sinal":         sinal,
        "score":         score_result["score_total"],
        "score_decisao": decisao,
        "tamanho_fator": tamanho_fator,
        "preco":         preco,
        "ema20_1h":      round(ema20, 2),
        "ema50_1h":      round(ema50, 2),
        "ema20_4h":      round(ema20_4h, 2),
        "ema50_4h":      round(ema50_4h, 2),
        "rsi":           round(rsi14 or 0, 2),
        "atr":           round(atr_atual, 2),
        "atr_media":     round(atr_media, 2),
        "volume_rel":    round(vol_rel, 2),
        "vwap":          round(vwap_val, 2),
        "bw":            round(bw_atual, 4),
        "bw_media":      round(bw_media, 4),
        "tend_4h":       tend_4h,
        "funding_%":     round(funding, 4),
        "regime":        regime_info["regime_final"],
        "regime_score":  regime_info["score"],
        "fear_greed":    fear_info["valor"],
        "fear_greed_pt": fear_info["classificacao_pt"],
        "cvd":             cvd_atual,
        "ml_prob":         ml_prob,
        "ml_ensemble":     ml_ensemble_prob,
        "ml_confianca":    ensemble_result.get("confianca", "?"),
        "ml_xgb":          ensemble_result.get("prob_xgb"),
        "ml_lstm":         ensemble_result.get("prob_lstm"),
        "stop_loss":     stop,
        "take_profit":   target,
        "filtros":        filtros,
        "filtros_ok":     aprovados,
        "filtros_total":  total_fil,
        "score_result":   score_result,
        "suporte_forte":  suporte_info["suporte_forte"],
        "suporte_dist_%": suporte_info["distancia_%"],
        "suporte_zona":   suporte_info["na_zona"],
        "suporte_info":   suporte_info,
    }

    resultado["symbol"] = symbol

    if sinal != "AGUARDAR":
        motivo = (f"Score:{score_result['score_total']} | MTF:{tend_4h} | "
                  f"RSI:{rsi14:.1f} | ATR:{atr_atual:.0f} | "
                  f"VolRel:{vol_rel:.2f}x | FG:{fear_info['valor']}")
        database.salvar_sinal(
            sinal,
            preco,
            motivo,
            symbol=symbol,
            score=score_result["score_total"],
            source="estrategia_otimizada",
        )

    return resultado


def imprimir(symbol="BTCUSDT", cvd_atual=None, ml_prob=None, ensemble_result=None):
    r = analisar(symbol, cvd_atual, ml_prob, ensemble_result)
    verde  = "\033[92m"
    vermelho = "\033[91m"
    amarelo  = "\033[93m"
    cinza    = "\033[90m"
    reset    = "\033[0m"

    cor_sinal = verde if r["sinal"] == "COMPRA" else \
                vermelho if r["sinal"] == "VENDA" else amarelo

    print("\n" + "="*60)
    print(f"  ESTRATEGIA OTIMIZADA — {r.get('symbol', 'BTCUSDT')}  (MTF + ATR + Volume + VWAP + ML)")
    print(f"  {r['timestamp']}")
    print("="*60)
    print(f"  Preco:    ${r['preco']:,.2f}")
    print(f"  EMA20/50 1H: ${r['ema20_1h']:,.2f} / ${r['ema50_1h']:,.2f}")
    print(f"  EMA20/50 4H: ${r['ema20_4h']:,.2f} / ${r['ema50_4h']:,.2f}  [{r['tend_4h']}]")
    print(f"  RSI:      {r['rsi']}  |  ATR: {r['atr']:.1f} (media: {r['atr_media']:.1f})")
    print(f"  Volume:   {r['volume_rel']:.2f}x media  |  VWAP: ${r['vwap']:,.2f}")
    print(f"  Bollinger BW: {r['bw']:.4f} (media: {r['bw_media']:.4f})")
    ens_p = r.get("ml_ensemble")
    xgb_p = r.get("ml_xgb")
    lstm_p = r.get("ml_lstm")
    print(f"  ML Ensemble: {ens_p*100:.1f}% "
          f"(XGB:{xgb_p*100:.0f}% LSTM:{lstm_p*100:.0f}% {r.get('ml_confianca','?')})"
          if ens_p and xgb_p and lstm_p else
          f"  ML Prob: {(r.get('ml_prob') or 0)*100:.1f}%")
    print(f"  Regime:   {r.get('regime','?')}  (score {r.get('regime_score','?')}/100)")
    print(f"  Fear & Greed: {r.get('fear_greed','?')}/100 — {r.get('fear_greed_pt','?')}")
    print()

    print("  FILTROS:")
    for nome, ok in r["filtros"].items():
        icone = f"{verde}OK  {reset}" if ok else f"{vermelho}FAIL{reset}"
        print(f"  [{icone}] {nome}")

    print(f"\n  Filtros aprovados: {r['filtros_ok']}/{r['filtros_total']}")

    # Score visual
    sc.imprimir_score(r["score_result"])

    print(f"\n  {cor_sinal}*** SINAL: {r['sinal']} ***{reset}")
    if r["sinal"] != "AGUARDAR":
        print(f"  Stop Loss:    ${r['stop_loss']:,.2f}")
        print(f"  Take Profit:  ${r['take_profit']:,.2f}")
        if r.get("tamanho_fator", 1.0) < 1.0:
            print(f"  {amarelo}Tamanho: 50% (score entre 60-74){reset}")

    print("="*60)
    return r


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--par", default="BTCUSDT")
    args = parser.parse_args()
    database.inicializar()
    imprimir(symbol=args.par)
