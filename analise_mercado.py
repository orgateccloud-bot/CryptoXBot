"""
Análise de Mercado BTC/USDT - Binance Futures
Extrai: preço atual, order book, funding rate, open interest
Não requer chaves de API (endpoints públicos).
"""

from datetime import datetime

import requests

# M-1: toda requisicao tem teto. Sem timeout, um socket pendurado trava
# relatorio_completo() para sempre — e como start_health_server roda ANTES
# (main.py), o worker fica vivo, /health responde 200 e o loop de trading
# nunca comeca. E um apagao que o NSSM nao detecta: o processo nao morre.
TIMEOUT_HTTP = 10

BASE_URL = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"


def get_preco_atual():
    """Preço atual e variação 24h."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/ticker/24hr", params={"symbol": SYMBOL}, timeout=TIMEOUT_HTTP
    )
    d = r.json()
    return {
        "preco": float(d["lastPrice"]),
        "variacao_24h_%": float(d["priceChangePercent"]),
        "maximo_24h": float(d["highPrice"]),
        "minimo_24h": float(d["lowPrice"]),
        "volume_24h_btc": float(d["volume"]),
        "volume_24h_usdt": float(d["quoteVolume"]),
    }


def get_order_book(limit=20):
    """Maiores paredes de compra (suporte) e venda (resistência)."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/depth", params={"symbol": SYMBOL, "limit": limit}, timeout=TIMEOUT_HTTP
    )
    d = r.json()

    bids = [(float(p), float(q)) for p, q in d["bids"]]  # compras
    asks = [(float(p), float(q)) for p, q in d["asks"]]  # vendas

    maior_bid = max(bids, key=lambda x: x[1])
    maior_ask = max(asks, key=lambda x: x[1])

    total_bid_usdt = sum(p * q for p, q in bids)
    total_ask_usdt = sum(p * q for p, q in asks)
    pressao = "COMPRA" if total_bid_usdt > total_ask_usdt else "VENDA"

    return {
        "maior_suporte_preco": maior_bid[0],
        "maior_suporte_btc": maior_bid[1],
        "maior_resistencia_preco": maior_ask[0],
        "maior_resistencia_btc": maior_ask[1],
        "liquidez_compra_usdt": total_bid_usdt,
        "liquidez_venda_usdt": total_ask_usdt,
        "pressao_dominante": pressao,
    }


def get_funding_rate():
    """Taxa de financiamento atual (sentimento do mercado de futuros)."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/premiumIndex", params={"symbol": SYMBOL}, timeout=TIMEOUT_HTTP
    )
    d = r.json()
    taxa = float(d["lastFundingRate"]) * 100
    if taxa > 0.01:
        sentimento = "OTIMISMO EXCESSIVO (risco de short squeeze ou liquidação de longs)"
    elif taxa < -0.01:
        sentimento = "PESSIMISMO EXCESSIVO (risco de short squeeze)"
    else:
        sentimento = "NEUTRO"
    return {
        "funding_rate_%": round(taxa, 4),
        "sentimento": sentimento,
    }


def get_open_interest():
    """Contratos em aberto - indica nível de alavancagem no mercado."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/openInterest", params={"symbol": SYMBOL}, timeout=TIMEOUT_HTTP
    )
    d = r.json()
    return {
        "open_interest_btc": float(d["openInterest"]),
        "open_interest_usdt": float(d["openInterest"]) * float(get_preco_atual()["preco"]),
    }


def get_medias_moveis():
    """EMA 20, EMA 50 e RSI calculados a partir das klines de 1h."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": "1h", "limit": 60},
        timeout=TIMEOUT_HTTP,
    )
    klines = r.json()
    fechamentos = [float(k[4]) for k in klines]

    def ema(valores, periodo):
        k = 2 / (periodo + 1)
        ema_val = valores[0]
        for v in valores[1:]:
            ema_val = v * k + ema_val * (1 - k)
        return round(ema_val, 2)

    def rsi(valores, periodo=14):
        ganhos, perdas = [], []
        for i in range(1, len(valores)):
            diff = valores[i] - valores[i - 1]
            ganhos.append(max(diff, 0))
            perdas.append(max(-diff, 0))
        media_ganho = sum(ganhos[-periodo:]) / periodo
        media_perda = sum(perdas[-periodo:]) / periodo
        if media_perda == 0:
            return 100
        rs = media_ganho / media_perda
        return round(100 - (100 / (1 + rs)), 2)

    ema20 = ema(fechamentos, 20)
    ema50 = ema(fechamentos, 50)
    rsi_val = rsi(fechamentos)
    preco_atual = fechamentos[-1]

    if preco_atual > ema20 > ema50:
        tendencia = "ALTA (Bullish)"
    elif preco_atual < ema20 < ema50:
        tendencia = "BAIXA (Bearish)"
    else:
        tendencia = "LATERAL / INDEFINIDA"

    return {
        "ema20_1h": ema20,
        "ema50_1h": ema50,
        "rsi_1h": rsi_val,
        "tendencia": tendencia,
    }


def relatorio_completo():
    """Gera um relatório completo de análise de mercado."""
    print("\n" + "=" * 60)
    print("  ANÁLISE BTC/USDT - BINANCE FUTURES")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    print("\n[1] PREÇO E VOLUME 24H")
    preco = get_preco_atual()
    sinal = "+" if preco["variacao_24h_%"] >= 0 else ""
    print(f"  Preço Atual:   ${preco['preco']:,.2f}")
    print(f"  Variação 24h:  {sinal}{preco['variacao_24h_%']:.2f}%")
    print(f"  Máxima 24h:    ${preco['maximo_24h']:,.2f}")
    print(f"  Mínima 24h:    ${preco['minimo_24h']:,.2f}")
    print(
        f"  Volume 24h:    {preco['volume_24h_btc']:,.1f} BTC  "
        f"(${preco['volume_24h_usdt']/1e9:.2f}B)"
    )

    print("\n[2] LIVRO DE ORDENS (Order Book)")
    ob = get_order_book()
    print(
        f"  Maior Suporte:     ${ob['maior_suporte_preco']:,.2f}  ({ob['maior_suporte_btc']:.3f} "
        f"BTC)"
    )
    print(
        f"  Maior Resistência: ${ob['maior_resistencia_preco']:,.2f}  "
        f"({ob['maior_resistencia_btc']:.3f} BTC)"
    )
    print(f"  Liquidez Compra:   ${ob['liquidez_compra_usdt']:,.0f}")
    print(f"  Liquidez Venda:    ${ob['liquidez_venda_usdt']:,.0f}")
    print(f"  Pressão Dominante: *** {ob['pressao_dominante']} ***")

    print("\n[3] FUNDING RATE (Mercado de Futuros)")
    fr = get_funding_rate()
    print(f"  Taxa Atual:  {fr['funding_rate_%']}%")
    print(f"  Sentimento:  {fr['sentimento']}")

    print("\n[4] OPEN INTEREST (Contratos em Aberto)")
    oi = get_open_interest()
    print(
        f"  Open Interest: {oi['open_interest_btc']:,.1f} BTC  "
        f"(${oi['open_interest_usdt']/1e9:.2f}B)"
    )

    print("\n[5] INDICADORES TÉCNICOS (Gráfico 1H)")
    mm = get_medias_moveis()
    rsi = mm["rsi_1h"]
    faixa_rsi = "(SOBRECOMPRADO)" if rsi > 70 else "(SOBREVENDIDO)" if rsi < 30 else "(NEUTRO)"
    print(f"  EMA 20:    ${mm['ema20_1h']:,.2f}")
    print(f"  EMA 50:    ${mm['ema50_1h']:,.2f}")
    print(f"  RSI (14):  {rsi}  {faixa_rsi}")
    print(f"  Tendência: {mm['tendencia']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    relatorio_completo()
