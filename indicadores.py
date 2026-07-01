"""
Biblioteca central de indicadores técnicos — Vetorizada
========================================================
Todos calculados com numpy para máxima performance.
Evita loops Python nativos em grandes janelas de dados.

Uso:
  import numpy as np
  prices = np.array([100, 101, 102, ...])
  ema_vals = ema(prices, 20)
"""

import math
from typing import Tuple

import numpy as np

# ── Tendência ─────────────────────────────────────────────────


def ema(valores: np.ndarray, periodo: int) -> np.ndarray:
    """EMA vetorizada com numpy."""
    if len(valores) < periodo:
        return np.full(len(valores), np.nan)

    k = 2 / (periodo + 1)
    ema_values = np.empty(len(valores))
    ema_values[: periodo - 1] = np.nan
    ema_values[periodo - 1] = np.mean(valores[:periodo])  # Inicialização

    for i in range(periodo, len(valores)):
        ema_values[i] = valores[i] * k + ema_values[i - 1] * (1 - k)

    return ema_values


def sma(valores: np.ndarray, periodo: int) -> np.ndarray:
    """SMA vetorizada com numpy."""
    if len(valores) < periodo:
        return np.full(len(valores), np.nan)

    weights = np.ones(periodo) / periodo
    return np.convolve(valores, weights, mode="valid")


def rsi(valores: np.ndarray, periodo: int = 14) -> np.ndarray:
    """RSI vetorizado."""
    if len(valores) < periodo + 1:
        return np.full(len(valores), np.nan)

    deltas = np.diff(valores)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gains = np.convolve(gains, np.ones(periodo) / periodo, mode="valid")
    avg_losses = np.convolve(losses, np.ones(periodo) / periodo, mode="valid")

    rs = avg_gains / np.where(avg_losses == 0, 1e-10, avg_losses)
    rsi_values = 100 - (100 / (1 + rs))

    # Padding para manter tamanho
    padding = np.full(periodo, np.nan)
    return np.concatenate([padding, rsi_values])


def macd(
    valores: np.ndarray, rapido: int = 12, lento: int = 26, sinal: int = 9
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD vetorizado."""
    ema_rap = ema(valores, rapido)
    ema_len = ema(valores, lento)
    linha = ema_rap - ema_len
    sig = ema(linha, sinal)
    hist = linha - sig
    return linha, sig, hist


# ── Volatilidade / Volume / Utilitários ───────────────────────
# NOTA: versões numpy duplicadas de atr/bollinger/vwap/volume_relativo/bandwidth
# foram removidas — eram redefinidas com o mesmo nome logo abaixo (código morto,
# sombreado por Python). As implementações ATIVAS, baseadas em listas (com None
# de padding, formato esperado pelos callers via `[-1] or 0`), seguem abaixo.


def atr(maximas, minimas, fechamentos, periodo=14):
    """Average True Range — mede volatilidade da vela."""
    tr_list = []
    for i in range(1, len(fechamentos)):
        hl = maximas[i] - minimas[i]
        hcp = abs(maximas[i] - fechamentos[i - 1])
        lcp = abs(minimas[i] - fechamentos[i - 1])
        tr_list.append(max(hl, hcp, lcp))
    # ATR = média móvel exponencial do TR
    atr_vals = [None] * periodo
    media = sum(tr_list[:periodo]) / periodo
    atr_vals.append(media)
    for tr in tr_list[periodo:]:
        media = (media * (periodo - 1) + tr) / periodo
        atr_vals.append(media)
    return atr_vals


def bollinger(fechamentos, periodo=20, desvios=2):
    """Bandas de Bollinger — identifica squeeze e expansão."""
    upper, mid, lower = [], [], []
    for i in range(len(fechamentos)):
        if i < periodo - 1:
            upper.append(None)
            mid.append(None)
            lower.append(None)
            continue
        janela = fechamentos[i - periodo + 1 : i + 1]
        m = sum(janela) / periodo
        std = math.sqrt(sum((x - m) ** 2 for x in janela) / periodo)
        upper.append(m + desvios * std)
        mid.append(m)
        lower.append(m - desvios * std)
    return upper, mid, lower


def bandwidth(upper, mid, lower):
    """Largura das Bandas de Bollinger — baixo = squeeze (pré-breakout)."""
    result = []
    for i in range(len(mid)):
        if mid[i] and mid[i] != 0:
            result.append((upper[i] - lower[i]) / mid[i])
        else:
            result.append(None)
    return result


# ── Volume ────────────────────────────────────────────────────


def vwap(maximas, minimas, fechamentos, volumes):
    """VWAP cumulativo — para uso em tempo real (sessão corrente)."""
    result = []
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(len(fechamentos)):
        tipico = (maximas[i] + minimas[i] + fechamentos[i]) / 3
        cum_pv += tipico * volumes[i]
        cum_v += volumes[i]
        result.append(cum_pv / cum_v if cum_v else fechamentos[i])
    return result


def vwap_rolling(maximas, minimas, fechamentos, volumes, periodo=20):
    """VWAP por janela deslizante — ideal para backtesting multi-dia."""
    result = [None] * (periodo - 1)
    for i in range(periodo - 1, len(fechamentos)):
        pv_sum = 0.0
        v_sum = 0.0
        for j in range(i - periodo + 1, i + 1):
            tipico = (maximas[j] + minimas[j] + fechamentos[j]) / 3
            pv_sum += tipico * volumes[j]
            v_sum += volumes[j]
        result.append(pv_sum / v_sum if v_sum else fechamentos[i])
    return result


def volume_media(volumes, periodo=20):
    return sma(volumes, periodo)


def volume_relativo(volumes, periodo=20):
    """Volume relativo = volume atual / média(volume). > 1.5 = relevante.

    Retorna lista do tamanho de `volumes`, com None nos primeiros `periodo-1`
    pontos (ainda sem média). `media` (sma, mode='valid') tem tamanho
    len-periodo+1; o valor alinhado ao índice i (i >= periodo-1) é
    media[i-(periodo-1)]. (Correção do IndexError que quebrava a estratégia.)
    """
    media = volume_media(volumes, periodo)
    result = [None] * (periodo - 1)
    for i in range(periodo - 1, len(volumes)):
        m = media[i - (periodo - 1)]
        result.append(volumes[i] / m if m else None)
    return result


# ── Multi-Timeframe ───────────────────────────────────────────


def tendencia_mtf(fechamentos_altos, fechamentos_baixos):
    """
    Alinha dois timeframes.
    Retorna: 'ALTA', 'BAIXA' ou 'CONFLITO'
    """

    def _tend(f):
        e20 = ema(f[-20:], 20)[-1]
        e50 = ema(f[-50:], 50)[-1] if len(f) >= 50 else e20
        p = f[-1]
        if p > e20 > e50:
            return "ALTA"
        if p < e20 < e50:
            return "BAIXA"
        return "LATERAL"

    t_alto = _tend(fechamentos_altos)
    t_baixo = _tend(fechamentos_baixos)

    if t_alto == t_baixo == "ALTA":
        return "ALTA"
    if t_alto == t_baixo == "BAIXA":
        return "BAIXA"
    return "CONFLITO"


# ── Estrutura de Mercado ──────────────────────────────────────


def market_structure(fechamentos, periodo=5):
    """
    Identifica HH/HL (alta) ou LH/LL (baixa).
    Retorna lista de: 'HH', 'HL', 'LH', 'LL', None
    """
    result = [None] * len(fechamentos)
    pivots = []
    for i in range(periodo, len(fechamentos) - periodo):
        janela = fechamentos[i - periodo : i + periodo + 1]
        if fechamentos[i] == max(janela):
            pivots.append((i, "H", fechamentos[i]))
        elif fechamentos[i] == min(janela):
            pivots.append((i, "L", fechamentos[i]))

    for j in range(1, len(pivots)):
        idx, tipo, val = pivots[j]
        _, tipo_ant, val_ant = pivots[j - 1]
        if tipo == "H" and tipo_ant == "H":
            result[idx] = "HH" if val > val_ant else "LH"
        elif tipo == "L" and tipo_ant == "L":
            result[idx] = "HL" if val > val_ant else "LL"
    return result
