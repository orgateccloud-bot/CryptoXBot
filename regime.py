"""
Deteccao de Regime de Mercado — BotBinance
============================================
Classifica o mercado em 3 estados:

  TENDENCIA_ALTA   → EMA alinhadas, ADX forte, volume crescente
  TENDENCIA_BAIXA  → EMA invertidas, ADX forte, pressao vendedora
  LATERAL          → ADX fraco, preco oscilando em range definido
  VOLATILIDADE     → ATR > 2x media (eventos extremos, nao operar)

O regime e calculado em 3 timeframes (1H, 4H, 1D) e combinado
em um score. Somente opera se pelo menos 2 de 3 TFs concordam.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicadores as ind
from data.klines import obter_klines

SYMBOL = "BTCUSDT"

# Limiares
ADX_TENDENCIA = 25  # ADX > 25 = tendencia presente
ADX_FORTE = 40  # ADX > 40 = tendencia forte
ATR_EXTREMO = 2.5  # ATR > 2.5x media = volatilidade extrema
ATR_LATERAL = 0.5  # ATR < 0.5x media = mercado parado demais


def _klines(intervalo, limite=60):
    """P1-5: delega para data.klines (cache TTL + REST_BASE_URL), mesmo
    contrato de antes (None em falha, sem propagar exceção)."""
    return obter_klines(SYMBOL, intervalo, limite)


def _adx(maximas, minimas, fechamentos, periodo=14):
    """
    Average Directional Index (ADX) — mede a forca da tendencia.
    Retorna lista de valores ADX (0-100). > 25 = tendencia presente.
    """
    n = len(fechamentos)
    if n < periodo + 2:
        return [0.0] * n

    tr_list, dm_plus, dm_minus = [], [], []

    for i in range(1, n):
        h, l, pc = maximas[i], minimas[i], fechamentos[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)

        up = maximas[i] - maximas[i - 1]
        down = minimas[i - 1] - minimas[i]

        dm_plus.append(up if up > down and up > 0 else 0)
        dm_minus.append(down if down > up and down > 0 else 0)

    def smooth(data, p):
        result = [sum(data[:p])]
        for i in range(p, len(data)):
            result.append(result[-1] - result[-1] / p + data[i])
        return result

    atr_s = smooth(tr_list, periodo)
    dmp_s = smooth(dm_plus, periodo)
    dmm_s = smooth(dm_minus, periodo)

    di_plus = [100 * dmp_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]
    di_minus = [100 * dmm_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]

    dx = []
    for i in range(len(di_plus)):
        soma = di_plus[i] + di_minus[i]
        dx.append(100 * abs(di_plus[i] - di_minus[i]) / soma if soma > 0 else 0)

    # ADX = media suavizada do DX
    adx = []
    if len(dx) >= periodo:
        adx.append(sum(dx[:periodo]) / periodo)
        for i in range(periodo, len(dx)):
            adx.append((adx[-1] * (periodo - 1) + dx[i]) / periodo)

    # Padding para manter tamanho igual ao input
    pad = [0.0] * (n - len(adx))
    return pad + adx


def _classificar_tf(dados, label=""):
    """Classifica regime para um timeframe."""
    if not dados:
        return {"regime": "INDEFINIDO", "adx": 0, "atr_ratio": 0, "direcao": "NEUTRO"}

    f = dados["fechamento"]
    h = dados["maxima"]
    l = dados["minima"]
    v = dados["volume"]

    ema20 = ind.ema(f, 20)[-1]
    ema50 = ind.ema(f, 50)[-1]
    ema200 = ind.ema(f, min(200, len(f) - 1))[-1] if len(f) > 50 else ema50

    atr_vals = ind.atr(h, l, f, 14)
    atr_atual = atr_vals[-1] or 0
    atr_media = sum(x for x in atr_vals[-20:] if x) / max(1, len([x for x in atr_vals[-20:] if x]))
    atr_ratio = atr_atual / atr_media if atr_media > 0 else 1.0

    adx_vals = _adx(h, l, f, 14)
    adx = adx_vals[-1] if adx_vals else 0

    preco = f[-1]

    # Direcao das EMAs
    if preco > ema20 > ema50:
        direcao = "ALTA"
    elif preco < ema20 < ema50:
        direcao = "BAIXA"
    else:
        direcao = "NEUTRO"

    # Volume crescente?
    vol_media = sum(v[-20:]) / 20 if len(v) >= 20 else v[-1]
    vol_atual = v[-1]
    vol_crescente = vol_atual > vol_media

    # Classificacao do regime
    if atr_ratio > ATR_EXTREMO:
        regime = "VOLATILIDADE"
    elif adx >= ADX_TENDENCIA and direcao == "ALTA":
        regime = "TENDENCIA_ALTA"
    elif adx >= ADX_TENDENCIA and direcao == "BAIXA":
        regime = "TENDENCIA_BAIXA"
    else:
        regime = "LATERAL"

    return {
        "regime": regime,
        "adx": round(adx, 1),
        "atr_ratio": round(atr_ratio, 2),
        "direcao": direcao,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "vol_crescente": vol_crescente,
        "preco": preco,
        "label": label,
    }


def detectar():
    """
    Detecta o regime atual em 3 timeframes e retorna veredicto consolidado.

    Retorna dict com:
      regime_final: TENDENCIA_ALTA | TENDENCIA_BAIXA | LATERAL | VOLATILIDADE
      pode_operar:  bool
      motivo:       str
      score:        0-100
      detalhes_tf:  dict com analise por timeframe
    """
    d1h = _klines("1h", 60)
    d4h = _klines("4h", 60)
    d1d = _klines("1d", 60)

    tf1h = _classificar_tf(d1h, "1H")
    tf4h = _classificar_tf(d4h, "4H")
    tf1d = _classificar_tf(d1d, "1D")

    # 4H mantém peso duplo (timeframe de referência); 1H e 1D entram com peso
    # simples. Antes o 1D era calculado mas descartado do voto (M-1): isso dava
    # ao 4H controle quase total e ignorava o sinal de longo prazo.
    regimes = [tf1h["regime"], tf4h["regime"], tf4h["regime"], tf1d["regime"]]

    # Contar votos
    votos = {
        "TENDENCIA_ALTA": regimes.count("TENDENCIA_ALTA"),
        "TENDENCIA_BAIXA": regimes.count("TENDENCIA_BAIXA"),
        "LATERAL": regimes.count("LATERAL"),
        "VOLATILIDADE": regimes.count("VOLATILIDADE"),
    }

    # Volatilidade extrema tem prioridade absoluta
    if "VOLATILIDADE" in regimes:
        regime_final = "VOLATILIDADE"
        pode_operar = False
        motivo = f"Volatilidade extrema detectada (ATR {tf1h['atr_ratio']:.1f}x media)"

    elif votos["TENDENCIA_ALTA"] >= 2:
        regime_final = "TENDENCIA_ALTA"
        pode_operar = True
        motivo = "Tendencia de alta confirmada em multiplos timeframes"

    elif votos["TENDENCIA_BAIXA"] >= 2:
        regime_final = "TENDENCIA_BAIXA"
        pode_operar = True
        motivo = "Tendencia de baixa confirmada (operar short ou aguardar)"

    elif votos["LATERAL"] >= 2:
        regime_final = "LATERAL"
        pode_operar = False
        motivo = "Mercado lateral — baixo ADX, sem direcao clara"

    else:
        # Conflito entre TFs — ser conservador
        regime_final = "INDEFINIDO"
        pode_operar = False
        motivo = f"Conflito entre timeframes: 1H={tf1h['regime']} 4H={tf4h['regime']} 1D={tf1d['regime']}"

    # Score 0-100
    adx_medio = (tf1h["adx"] + tf4h["adx"]) / 2
    score_adx = min(adx_medio / ADX_FORTE * 50, 50)
    score_tf = (votos.get(regime_final, 0) / 3) * 50
    score = round(score_adx + score_tf)

    return {
        "regime_final": regime_final,
        "pode_operar": pode_operar,
        "motivo": motivo,
        "score": score,
        "votos": votos,
        "detalhes_tf": {
            "1h": tf1h,
            "4h": tf4h,
            "1d": tf1d,
        },
    }


def imprimir():
    r = detectar()

    cores = {
        "TENDENCIA_ALTA": "\033[92m",
        "TENDENCIA_BAIXA": "\033[91m",
        "LATERAL": "\033[93m",
        "VOLATILIDADE": "\033[91m",
        "INDEFINIDO": "\033[90m",
    }
    cor = cores.get(r["regime_final"], "\033[0m")
    reset = "\033[0m"
    verde = "\033[92m"
    vermelho = "\033[91m"

    print("\n" + "=" * 58)
    print("  DETECCAO DE REGIME DE MERCADO")
    print("=" * 58)

    for tf, d in r["detalhes_tf"].items():
        cor_tf = cores.get(d["regime"], "\033[0m")
        print(
            f"  [{tf.upper():3s}]  {cor_tf}{d['regime']:18s}{reset}  "
            f"ADX:{d['adx']:5.1f}  ATRx:{d['atr_ratio']:.2f}  Dir:{d['direcao']}"
        )

    print(f"\n  Regime Final: {cor}{r['regime_final']}{reset}")
    print(f"  Score:        {r['score']}/100")
    print(f"  Pode Operar:  {verde+'SIM'+reset if r['pode_operar'] else vermelho+'NAO'+reset}")
    print(f"  Motivo:       {r['motivo']}")
    print("=" * 58)
    return r


if __name__ == "__main__":
    imprimir()
