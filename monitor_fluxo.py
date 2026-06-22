"""
Monitor de Fluxo em Tempo Real - BTC/USDT Binance Futures
- WebSocket de trades agregados
- Calcula CVD em tempo real
- Salva tudo no banco SQLite
- Salva snapshot CVD a cada 5 minutos

Uso: python monitor_fluxo.py
"""

import websocket
import json
import time
import threading
from datetime import datetime
from config.runtime_settings import MIN_BTC_VOLUME, SYMBOL_WS, WHALE_BTC_VOLUME
import database

# === Estado global ===
cvd = 0.0
total_compras = 0.0
total_vendas = 0.0
preco_atual = 0.0
inicio = time.time()
ultimo_snapshot_cvd = time.time()
INTERVALO_SNAPSHOT_CVD = 300  # 5 minutos


def formatar_valor(valor):
    if valor >= 1_000_000:
        return f"${valor/1_000_000:.2f}M"
    return f"${valor/1_000:.1f}K"


def snapshot_cvd_periodico():
    """Salva CVD no banco a cada 5 minutos em thread separada."""
    global ultimo_snapshot_cvd
    while True:
        time.sleep(INTERVALO_SNAPSHOT_CVD)
        try:
            database.salvar_cvd(cvd, total_compras, total_vendas)
            print(
                f"\n[DB] CVD salvo: {cvd:+.3f} BTC | Compras: {total_compras:.2f} | Vendas: {total_vendas:.2f}"
            )
        except Exception as e:
            print(f"[DB] Erro ao salvar CVD: {e}")


def on_message(ws, message):
    global cvd, total_compras, total_vendas, preco_atual

    data = json.loads(message)
    price = float(data["p"])
    quantity = float(data["q"])
    is_buyer_maker = data["m"]
    # m=True  → VENDA a mercado (agrediu bid)
    # m=False → COMPRA a mercado (agrediu ask)

    preco_atual = price

    if is_buyer_maker:
        cvd -= quantity
        total_vendas += quantity
        direcao = "VENDA"
        cor = "\033[91m"
        seta = "▼"
    else:
        cvd += quantity
        total_compras += quantity
        direcao = "COMPRA"
        cor = "\033[92m"
        seta = "▲"

    reset = "\033[0m"
    cinza = "\033[90m"

    # Salva no banco qualquer trade >= 0.1 BTC (silenciosamente)
    if quantity >= 0.1:
        try:
            database.salvar_trade(price, quantity, direcao, WHALE_BTC_VOLUME)
        except Exception:
            pass

    # Exibe no terminal apenas acima do filtro configurado
    if quantity >= MIN_BTC_VOLUME:
        hora = datetime.now().strftime("%H:%M:%S")

        if quantity >= WHALE_BTC_VOLUME:
            print(f"\n\033[93m{'='*56}")
            print(f"  BALEIA! {direcao} {quantity:.3f} BTC ({formatar_valor(price*quantity)})")
            print(f"{'='*56}\033[0m")
        else:
            print(
                f"{cor}[{hora}] {seta} {direcao:6s}{reset}  "
                f"{cinza}${price:,.2f}  "
                f"{quantity:.3f} BTC ({formatar_valor(price*quantity)}){reset}"
            )

        cvd_cor = "\033[92m" if cvd >= 0 else "\033[91m"
        print(
            f"  {cinza}CVD: {cvd_cor}{cvd:+.3f} BTC{reset}  "
            f"{cinza}C:{total_compras:.2f}  V:{total_vendas:.2f}{reset}"
        )


def on_error(ws, error):
    print(f"\033[91m[ERRO] {error}\033[0m")


def on_close(ws, code, msg):
    duracao = int(time.time() - inicio)
    print(f"\n\033[93m[ENCERRADO] Sessão: {duracao//60}min {duracao%60}s")
    print(f"  CVD Final: {cvd:+.3f} BTC\033[0m")
    # Salva CVD final
    try:
        database.salvar_cvd(cvd, total_compras, total_vendas)
    except Exception:
        pass


def on_open(ws):
    database.inicializar()
    # Inicia thread de snapshot periódico
    t = threading.Thread(target=snapshot_cvd_periodico, daemon=True)
    t.start()

    print("\033[92m" + "=" * 56)
    print(f"  MONITOR DE FLUXO — BTC/USDT FUTURES (BINANCE)")
    print(f"  Filtro terminal: >= {MIN_BTC_VOLUME} BTC")
    print(f"  Alerta baleia:   >= {WHALE_BTC_VOLUME} BTC")
    print(f"  Salvando no DB:  >= 0.1 BTC")
    print(f"  Snapshot CVD:    a cada 5 minutos")
    print("=" * 56 + "\033[0m")
    print("  CVD+ = pressão compradora  |  CVD- = pressão vendedora")
    print("-" * 56)


if __name__ == "__main__":
    url = f"wss://fstream.binance.com/ws/{SYMBOL_WS}@aggTrade"
    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)
