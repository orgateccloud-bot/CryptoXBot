"""
Score Unificado 0-100 — BotBinance
=====================================
Combina todos os filtros e sinais em um unico score ponderado.

Pesos por componente:
  Regime de Mercado  20%  — filtro mais importante (macro)
  CVD Divergence     15%  — fluxo de agressao vs price action (novo componente pesado)
  Multi-Timeframe    15%  — confirmacao em 4H
  ML XGBoost         12%  — modelo preditivo
  EMA Alinhadas      10%  — tendencia de curto prazo
  Fear & Greed       10%  — sentimento de mercado
  RSI                 8%  — momentum
  VWAP                5%  — preco vs media ponderada por volume
  Volume              3%  — forca do movimento
  ATR                 2%  — volatilidade adequada

Regras:
  score >= 70  → OPERAR com tamanho cheio
  score 60-69  → OPERAR com tamanho reduzido (50%)
  score < 60   → AGUARDAR

Bloqueios absolutos (score forcado para 0):
  - Regime VOLATILIDADE ou LATERAL
  - Fear & Greed < 20 (Medo Extremo)
  - Fear & Greed > 80 (Ganancia Extrema)
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.cvd_calculator import calculate_cvd

# Limiar de operacao
SCORE_OPERAR = 60  # score minimo para operar
SCORE_CHEIO = 70  # score para tamanho cheio
SCORE_REDUZIDO = 60  # score para tamanho 50%

# Pesos (somam 100)
PESOS = {
    "regime": 18,  # -2 (continua filtro macro principal)
    "cvd": 15,  # = (fluxo institucional, peso mantido)
    "mtf": 12,  # -3 (confirmação multi-TF)
    "ml": 20,  # +8 (ensemble XGBoost+MLP validado, peso elevado)
    "ema": 8,  # -2 (tendência curto prazo)
    "fear_greed": 8,  # -2 (sentimento macro)
    "rsi": 8,  # = (momentum)
    "vwap": 5,  # = (preço vs média ponderada)
    "volume": 4,  # +1 (força do movimento)
    "atr": 2,  # = (volatilidade adequada)
}


def _score_regime(regime_info):
    """
    0-100 para o regime de mercado (filtro macro principal).

    Reaproveita o score de força de tendência (ADX + concentração de votos) já
    calculado em regime.detectar(). Direção-neutro: tendência clara (ALTA ou
    BAIXA) pontua alto porque o ambiente é operável; a direção é decidida pelos
    filtros EMA long/short na estratégia. LATERAL/VOLATILIDADE pontuam baixo e
    ainda são bloqueios absolutos em calcular().

    Args:
        regime_info: dict de regime.detectar() com 'regime_final' e 'score'.
    """
    if not regime_info:
        return 50  # neutro sem dados

    regime = regime_info.get("regime_final", "INDEFINIDO")
    forca = regime_info.get("score", 50)  # 0-100 (força da tendência)

    if regime in ("TENDENCIA_ALTA", "TENDENCIA_BAIXA"):
        return max(60, min(100, int(forca)))  # trend claro: usa a força (>=60)
    if regime == "LATERAL":
        return 30
    if regime == "VOLATILIDADE":
        return 10
    return 40  # INDEFINIDO — conservador


def _score_cvd(preco_atual, historico_ticks, periodo=50):
    """
    0-100 para divergência CVD vs Price Action (usando CVD calculator vetorizado).

    Args:
        preco_atual: preço atual
        historico_ticks: lista de dicts [{"preco": float, "is_buyer_maker": bool, "quantidade": float}]
        periodo: período para calcular slopes (default 50 ticks)

    Retorna score baseado em divergência:
        - Preço subindo + CVD subindo: 100
        - Preço subindo + CVD caindo: 0 (divergência bearish)
        - Preço caindo + CVD subindo: 50 (divergência bullish)
        - Preço caindo + CVD caindo: 20
    """
    if not historico_ticks or len(historico_ticks) < periodo:
        return 50  # neutro sem dados suficientes

    # Usar CVD calculator vetorizado
    cvd_result = calculate_cvd(historico_ticks[-periodo:], window_size=periodo)

    # Calcular slope do preço
    precos = np.array([t["preco"] for t in historico_ticks[-periodo:]])
    x = np.arange(len(precos))
    preco_slope = np.polyfit(x, precos, 1)[0]

    # Usar métricas do CVD calculator
    cvd_slope = cvd_result.divergence_score  # já normalizado -1 a 1
    trend_strength = cvd_result.trend_strength  # 0-1

    # Normalizar slopes
    preco_trend = 1 if preco_slope > 0.001 else -1 if preco_slope < -0.001 else 0
    cvd_trend = 1 if cvd_slope > 0.1 else -1 if cvd_slope < -0.1 else 0

    # Score baseado em divergência e força da tendência
    base_score = 50  # neutro
    if preco_trend == 1 and cvd_trend == 1:
        base_score = 100  # harmonia bullish
    elif preco_trend == 1 and cvd_trend == -1:
        base_score = 0  # divergência bearish forte
    elif preco_trend == -1 and cvd_trend == 1:
        base_score = 50  # divergência bullish moderada
    elif preco_trend == -1 and cvd_trend == -1:
        base_score = 20  # harmonia bearish

    # Bonus por força da tendência CVD
    strength_bonus = int(trend_strength * 20)  # até +20pts se tendência forte
    final_score = min(100, base_score + strength_bonus)

    return final_score


def _score_mtf(tend_4h, tend_1h_dir=""):
    """0-100 para multi-timeframe."""
    if tend_4h == "ALTA":
        return 100
    elif tend_4h == "LATERAL":
        return 50
    else:
        return 0


def _score_ml(ml_prob, regime_atual="INDEFINIDO"):
    """0-100 para probabilidade ML, ajustada por regime."""
    if ml_prob is None:
        return 50  # sem modelo = neutro
    # Prob 0.5 = 50pts, prob 1.0 = 100pts
    base_score = min(100, max(0, int(ml_prob * 100)))

    # Penalizar em mercados laterais (reduz score em 20pts se prob > 0.6)
    if regime_atual == "LATERAL" and ml_prob > 0.60:
        base_score = max(0, base_score - 20)

    return base_score


def _score_ema(preco, ema20, ema50):
    """0-100 para alinhamento de EMAs."""
    if preco > ema20 > ema50:
        # Bonus por distancia: mais afastado = tendencia mais forte
        dist = (preco - ema50) / ema50 * 100
        return min(100, 70 + dist * 10)
    elif preco > ema20:
        return 50
    elif preco > ema50:
        return 30
    else:
        return 0


def _score_fear_greed(valor):
    """0-100 baseado no Fear & Greed Index."""
    if valor <= 20:
        return 0  # bloqueio absoluto
    elif valor > 80:
        return 0  # bloqueio absoluto (ganancia extrema)
    elif 35 <= valor <= 65:
        return 100  # zona ideal
    elif 25 <= valor < 35:
        return 60  # medo moderado — pode operar com cautela
    elif 65 < valor <= 75:
        return 70  # ganancia moderada
    else:
        return 40  # zona de transicao


def _score_rsi(rsi, rsi_min=42, rsi_max=62):
    """0-100 para RSI."""
    if rsi is None:
        return 50
    rsi_ideal = (rsi_min + rsi_max) / 2  # 52
    if rsi_min <= rsi <= rsi_max:
        # Maximo no centro, cai nas bordas
        dist_centro = abs(rsi - rsi_ideal) / ((rsi_max - rsi_min) / 2)
        return int(100 - dist_centro * 30)
    elif rsi < rsi_min:
        return max(0, int((rsi - 30) / (rsi_min - 30) * 60))
    else:
        return max(0, int((80 - rsi) / (80 - rsi_max) * 60))


def _score_vwap(preco, vwap_val):
    """0-100 para posicao vs VWAP."""
    if vwap_val <= 0:
        return 50
    dist_pct = (preco - vwap_val) / vwap_val * 100
    if dist_pct > 0:
        # Acima do VWAP: bom para long
        return min(100, 70 + dist_pct * 5)
    else:
        # Abaixo do VWAP: ruim para long
        return max(0, 40 + dist_pct * 8)


def _score_volume(vol_rel):
    """0-100 para volume relativo."""
    if vol_rel >= 2.0:
        return 100
    elif vol_rel >= 1.3:
        return 80
    elif vol_rel >= 1.0:
        return 60
    elif vol_rel >= 0.7:
        return 40
    else:
        return 20


def _score_atr(atr_atual, atr_media):
    """0-100 para ATR (volatilidade adequada)."""
    if atr_media <= 0:
        return 50
    ratio = atr_atual / atr_media
    if ratio > 2.5:
        return 0  # volatilidade extrema
    elif 0.7 <= ratio <= 1.8:
        return 100  # volatilidade ideal
    elif ratio < 0.7:
        return 30  # mercado parado
    else:
        return 60  # volatilidade elevada mas aceitavel


def calcular(
    regime_info,
    fear_info,
    tend_4h,
    ml_prob,
    preco,
    ema20,
    ema50,
    rsi,
    vwap_val,
    vol_rel,
    atr_atual,
    atr_media,
    historico_ticks=None,
    rsi_min=42,
    rsi_max=62,
    score_operar=60,
    score_cheio=70,
):
    """
    Calcula o score unificado 0-100.

    Retorna dict com:
      score_total:   0-100 (score final ponderado)
      scores:        dict com score de cada componente
      pesos:         dict com pesos aplicados
      decisao:       OPERAR_CHEIO | OPERAR_REDUZIDO | AGUARDAR
      tamanho_fator: 1.0 | 0.5 | 0.0
      motivo:        str explicando a decisao
      detalhes:      lista de componentes ordenados por contribuicao
    """
    scores_raw = {
        "regime": _score_regime(regime_info),
        "cvd": _score_cvd(preco, historico_ticks or []),
        "mtf": _score_mtf(tend_4h),
        "ml": _score_ml(ml_prob, regime_info.get("regime_final", "INDEFINIDO")),
        "ema": _score_ema(preco, ema20, ema50),
        "fear_greed": _score_fear_greed(fear_info.get("valor", 50)),
        "rsi": _score_rsi(rsi, rsi_min, rsi_max),
        "vwap": _score_vwap(preco, vwap_val),
        "volume": _score_volume(vol_rel),
        "atr": _score_atr(atr_atual, atr_media),
    }

    # Score ponderado
    score_total = 0
    contribuicoes = []
    for comp, peso in PESOS.items():
        raw = scores_raw[comp]
        contrib = raw * peso / 100
        score_total += contrib
        contribuicoes.append(
            {
                "componente": comp,
                "score_raw": raw,
                "peso": peso,
                "contribuicao": round(contrib, 1),
            }
        )

    score_total = round(score_total)

    # Bloqueios absolutos (forcam score para 0)
    bloqueios = []
    if regime_info.get("regime_final") in ("VOLATILIDADE", "LATERAL"):
        bloqueios.append(f"Regime {regime_info['regime_final']}")
    if fear_info.get("valor", 50) <= 20:
        bloqueios.append(f"Fear&Greed {fear_info['valor']} (Medo Extremo)")
    if fear_info.get("valor", 50) > 80:
        bloqueios.append(f"Fear&Greed {fear_info['valor']} (Ganancia Extrema)")

    if bloqueios:
        score_total = max(score_total, 1)  # mantém score real mas bloqueia operacao
        decisao = "AGUARDAR"
        tamanho_fator = 0.0
        motivo = "BLOQUEIO: " + " | ".join(bloqueios)
    elif score_total >= score_cheio:
        decisao = "OPERAR_CHEIO"
        tamanho_fator = 1.0
        motivo = f"Score {score_total}/100 — Condicoes excelentes para operar"
    elif score_total >= score_operar:
        decisao = "OPERAR_REDUZIDO"
        tamanho_fator = 0.5
        motivo = f"Score {score_total}/100 — Operar com 50% do tamanho (cautela)"
    else:
        decisao = "AGUARDAR"
        tamanho_fator = 0.0
        motivo = f"Score {score_total}/100 — Abaixo do minimo ({score_operar})"

    # Ordenar por maior contribuicao
    contribuicoes.sort(key=lambda x: x["contribuicao"], reverse=True)

    return {
        "score_total": score_total,
        "scores": scores_raw,
        "pesos": PESOS,
        "decisao": decisao,
        "tamanho_fator": tamanho_fator,
        "motivo": motivo,
        "bloqueios": bloqueios,
        "detalhes": contribuicoes,
    }


def imprimir_score(score_result):
    """Imprime o score de forma visual."""
    s = score_result["score_total"]
    decisao = score_result["decisao"]

    # Cor do score
    if s >= SCORE_CHEIO:
        cor = "\033[92m"  # verde
    elif s >= SCORE_REDUZIDO:
        cor = "\033[93m"  # amarelo
    else:
        cor = "\033[91m"  # vermelho
    reset = "\033[0m"
    verde = "\033[92m"
    vermelho = "\033[91m"
    cinza = "\033[90m"

    # Barra de progresso
    barra_len = 40
    pos = int(s / 100 * barra_len)
    # Marcadores de limiar
    barra = list("-" * barra_len)
    for i in range(pos):
        barra[i] = "#"
    # Marcadores visuais
    barra[int(SCORE_REDUZIDO / 100 * barra_len)] = "|"
    barra[int(SCORE_CHEIO / 100 * barra_len)] = "|"
    barra_str = "".join(barra)

    print("\n" + "=" * 58)
    print("  SCORE UNIFICADO")
    print("=" * 58)
    print(f"  {cor}{s:3d}/100  [{barra_str}]{reset}")
    print(f"         {cinza}{'':>{int(SCORE_REDUZIDO/100*barra_len)+2}}60  75{reset}")
    print()

    # Tabela de componentes
    print(f"  {'Componente':<14} {'Score':>6}  {'Peso':>5}  {'Contrib':>7}")
    print(f"  {'-'*40}")
    for d in score_result["detalhes"]:
        raw = round(d["score_raw"]) if isinstance(d["score_raw"], float) else d["score_raw"]
        cor_c = verde if raw >= 70 else vermelho if raw < 40 else "\033[93m"
        print(
            f"  {d['componente']:<14} {cor_c}{raw:>5}{reset}  "
            f"{d['peso']:>4}%  {d['contribuicao']:>6.1f}pt"
        )

    print(f"  {'-'*40}")
    print(f"  {'TOTAL':<14} {cor}{s:>5}{reset}  {'100':>5}%  {s:>6.1f}pt")

    print()

    cor_d = (
        verde
        if decisao == "OPERAR_CHEIO"
        else "\033[93m" if decisao == "OPERAR_REDUZIDO" else vermelho
    )

    print(f"  Decisao: {cor_d}{decisao}{reset}")
    if score_result["bloqueios"]:
        for b in score_result["bloqueios"]:
            print(f"  {vermelho}BLOQUEIO: {b}{reset}")
    print(f"  {score_result['motivo']}")
    if decisao == "OPERAR_REDUZIDO":
        amarelo = "\033[93m"
        print(f"  {amarelo}Tamanho da posicao: 50% do calculado pelo Kelly{reset}")
    print("=" * 58)


if __name__ == "__main__":
    import fear_greed as fg
    import regime as reg

    print("Calculando Score Unificado...")
    regime_info = reg.detectar()
    fear_info = fg.obter()

    # Valores simulados para demo
    resultado = calcular(
        regime_info=regime_info,
        fear_info=fear_info,
        tend_4h="LATERAL",
        ml_prob=0.55,
        preco=66000,
        ema20=66500,
        ema50=67000,
        rsi=52,
        vwap_val=66800,
        vol_rel=0.8,
        atr_atual=500,
        atr_media=510,
    )
    imprimir_score(resultado)
