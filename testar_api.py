"""
Testa se as chaves de API da Binance estão funcionando.
Exibe saldo e permissões da conta. Não realiza nenhuma ordem.

Uso: python testar_api.py
"""

import hashlib
import hmac
import time

import requests

from config.runtime_settings import API_KEY, API_SECRET

BASE_URL = "https://api.binance.com"  # Spot API


def assinar(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()


def get_saldo():
    if not API_KEY or not API_SECRET:
        print("[ERRO] API_KEY ou API_SECRET nao preenchidos em config/settings.py")
        return

    params = {"timestamp": int(time.time() * 1000)}
    params["signature"] = assinar(params)
    headers = {"X-MBX-APIKEY": API_KEY}

    try:
        r = requests.get(f"{BASE_URL}/api/v3/account", params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

        print("\n" + "=" * 50)
        print("  CONEXAO COM API BINANCE SPOT: OK")
        print("=" * 50)

        ativos = [b for b in data.get("balances", []) if float(b["free"]) > 0]

        if ativos:
            print("\n  Saldos disponiveis:")
            for b in ativos:
                print(f"  {b['asset']:8s}  livre: {float(b['free']):,.6f}")
        else:
            print("\n  Nenhum saldo disponivel na conta Spot.")

        perms = data.get("permissions", [])
        print(f"\n  Permissoes: {', '.join(perms)}")
        print("  Chaves de API validadas com sucesso!")
        print("=" * 50)

    except requests.exceptions.HTTPError as e:
        print(f"\n[ERRO HTTP] {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 401:
            print("  CAUSA: Chave invalida ou IP bloqueado.")
            print("  SOLUCAO: Verifique o IP na pagina de API da Binance.")
        elif e.response.status_code == 403:
            print("  CAUSA: IP nao autorizado.")
    except Exception as e:
        print(f"[ERRO] {e}")


if __name__ == "__main__":
    get_saldo()
