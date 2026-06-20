"""
Estratégia 1: EMA + RSI + CVD
==============================
Lógica de entrada:
  COMPRA  quando: preço > EMA20 > EMA50
                  AND RSI entre 40-65 (não sobrecomprado, mas com momentum)
                  AND CVD positivo (pressão compradora confirmada)
                  AND Funding Rate < 0.05% (mercado não excessivamente alavancado em longs)

  VENDA   quando: preço < EMA20 < EMA50
                  AND RSI entre 35-60
                  AND CVD negativo (pressão vendedora confirmada)
                  AND Funding Rate > -0.05%

Gestão de risco:
  Stop Loss:  2.0% abaixo da entrada (COMPRA) / acima (VENDA)
  Take Profit: 4.0% (ratio 2:1)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from datetime import datetime
import database


BASE_URL = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

# === Parâmetros da estratégia ===
RSI_MIN_COMPRA = 40
RSI_MAX_COMPRA = 65
RSI_MIN_VENDA  = 35
RSI_MAX_VENDA  = 60
FUNDING_MAX_LONG  = 0.05   # % — não entrar comprado se funding muito positivo
FUNDING_MAX_SHORT = -0.05  # % — não entrar vendido se funding muito negativo
STOP_LOSS_PCT    = 0.020   # 2.0%
TAKE_PROFIT_PCT  = 0.040   # 4.0%


def _klines_fechamento(intervalo="1h", limite=60):
    r = requests.get(f"{BASE_URL}/fapi/v1/klines", params={
        "symbol": SYMBOL, "interval": intervalo, "limit": limite
    })
    return [float(k[4]) for k in r.json()]


def calcular_ema(valores, periodo):
    k = 2 / (periodo + 1)
    ema = valores[0]
    for v in valores[1:]:
        ema = v * k + ema * (1 - k)
    return round(ema, 2)


def calcular_rsi(valores, periodo=14):
    ganhos, perdas = [], []
    for i in range(1, len(valores)):
        diff = valores[i] - valores[i - 1]
        ganhos.append(max(diff, 0))
        perdas.append(max(-diff, 0))
    mg = sum(ganhos[-periodo:]) / periodo
    mp = sum(perdas[-periodo:]) / periodo
    if mp == 0:
        return 100.0
    return round(100 - (100 / (1 + mg / mp)), 2)


def get_funding_rate():
    r = requests.get(f"{BASE_URL}/fapi/v1/premiumIndex", params={"symbol": SYMBOL})
    return float(r.json()["lastFundingRate"]) * 100


def get_preco():
    r = requests.get(f"{BASE_URL}/fapi/v1/ticker/price", params={"symbol": SYMBOL})
    return float(r.json()["price"])


def analisar(cvd_atual=None):
    """
    Avalia a estratégia e retorna um sinal: 'COMPRA', 'VENDA' ou 'AGUARDAR'.

    Args:
        cvd_atual: CVD acumulado na sessão atual do monitor_fluxo.py
                   (None = não usar CVD no filtro)

    Returns:
        dict com sinal, preço, stop, target, motivos
    """
    fechamentos = _klines_fechamento()
    preco = get_preco()
    ema20 = calcular_ema(fechamentos[-20:], 20)
    ema50 = calcular_ema(fechamentos, 50)
    rsi   = calcular_rsi(fechamentos)
    funding = get_funding_rate()

    motivos = []
    sinal = "AGUARDAR"

    # --- Verificar condições de COMPRA ---
    cond_tendencia_alta = preco > ema20 > ema50
    cond_rsi_compra     = RSI_MIN_COMPRA <= rsi <= RSI_MAX_COMPRA
    cond_funding_compra = funding < FUNDING_MAX_LONG
    cond_cvd_compra     = (cvd_atual is None) or (cvd_atual > 0)

    if cond_tendencia_alta and cond_rsi_compra and cond_funding_compra and cond_cvd_compra:
        sinal = "COMPRA"
        motivos = [
            f"Preço (${preco:,.2f}) > EMA20 ({ema20}) > EMA50 ({ema50})",
            f"RSI {rsi} em zona ideal ({RSI_MIN_COMPRA}-{RSI_MAX_COMPRA})",
            f"Funding {funding:.4f}% < {FUNDING_MAX_LONG}% (não sobrecarregado)",
            f"CVD {'positivo' if cvd_atual and cvd_atual > 0 else 'não avaliado'}",
        ]

    # --- Verificar condições de VENDA ---
    cond_tendencia_baixa = preco < ema20 < ema50
    cond_rsi_venda       = RSI_MIN_VENDA <= rsi <= RSI_MAX_VENDA
    cond_funding_venda   = funding > FUNDING_MAX_SHORT
    cond_cvd_venda       = (cvd_atual is None) or (cvd_atual < 0)

    if cond_tendencia_baixa and cond_rsi_venda and cond_funding_venda and cond_cvd_venda:
        sinal = "VENDA"
        motivos = [
            f"Preço (${preco:,.2f}) < EMA20 ({ema20}) < EMA50 ({ema50})",
            f"RSI {rsi} em zona ideal ({RSI_MIN_VENDA}-{RSI_MAX_VENDA})",
            f"Funding {funding:.4f}% > {FUNDING_MAX_SHORT}% (não sobrecarregado)",
            f"CVD {'negativo' if cvd_atual and cvd_atual < 0 else 'não avaliado'}",
        ]

    # Calcular stops e alvos
    if sinal == "COMPRA":
        stop   = round(preco * (1 - STOP_LOSS_PCT), 2)
        target = round(preco * (1 + TAKE_PROFIT_PCT), 2)
    elif sinal == "VENDA":
        stop   = round(preco * (1 + STOP_LOSS_PCT), 2)
        target = round(preco * (1 - TAKE_PROFIT_PCT), 2)
    else:
        stop = target = None

    resultado = {
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "sinal": sinal,
        "preco": preco,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "funding_%": funding,
        "cvd": cvd_atual,
        "stop_loss": stop,
        "take_profit": target,
        "risco_retorno": "2:1",
        "motivos": motivos,
    }

    # Salvar sinal no banco (somente se acionado)
    if sinal != "AGUARDAR":
        database.salvar_sinal(sinal, preco, " | ".join(motivos), symbol="BTCUSDT", source="ema_rsi_cvd")

    return resultado


def imprimir_analise(cvd_atual=None):
    r = analisar(cvd_atual)

    cor_sinal = {
        "COMPRA":  "\033[92m",
        "VENDA":   "\033[91m",
        "AGUARDAR":"\033[93m",
    }
    cor = cor_sinal.get(r["sinal"], "")
    reset = "\033[0m"
    cinza = "\033[90m"

    print("\n" + "=" * 56)
    print(f"  ESTRATÉGIA: EMA + RSI + CVD")
    print(f"  {r['timestamp']}")
    print("=" * 56)
    print(f"  Preço:    ${r['preco']:,.2f}")
    print(f"  EMA 20:   ${r['ema20']:,.2f}")
    print(f"  EMA 50:   ${r['ema50']:,.2f}")
    print(f"  RSI:      {r['rsi']}")
    print(f"  Funding:  {r['funding_%']:.4f}%")
    if r["cvd"] is not None:
        print(f"  CVD:      {r['cvd']:+.3f} BTC")
    print(f"\n  {cor}*** SINAL: {r['sinal']} ***{reset}")

    if r["sinal"] != "AGUARDAR":
        print(f"\n  Stop Loss:   ${r['stop_loss']:,.2f}  (-{STOP_LOSS_PCT*100:.1f}%)")
        print(f"  Take Profit: ${r['take_profit']:,.2f}  (+{TAKE_PROFIT_PCT*100:.1f}%)")
        print(f"  Risco/Retorno: {r['risco_retorno']}")
        print(f"\n  Motivos:")
        for m in r["motivos"]:
            print(f"  {cinza}• {m}{reset}")
    else:
        print(f"\n  {cinza}Condições não atingidas. Monitorando...{reset}")

    print("=" * 56)
    return r


if __name__ == "__main__":
    database.inicializar()
    imprimir_analise(cvd_atual=None)
