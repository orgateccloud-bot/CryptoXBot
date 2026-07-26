"""
Leitura da conta Spot da Binance — fonte ÚNICA de saldo
========================================================
Nasceu de uma investigação em 2026-07-26: o dashboard mostrava `SALDO_USDT
$0,00` e não havia como saber, olhando o número, se a conta estava vazia ou se
a API tinha falhado. `risco.get_saldo_usdt()` fazia:

    except Exception:
        pass
    return 0.0

ou seja, chave revogada, clock drift, geo-block e rate limit viravam todos "zero"
— indistinguíveis de saldo genuinamente zerado. Pior: quem consome esse número é
o **sizing de posição**. Ficar cego ali é diferente de ficar cego num display.

Ironicamente `dashboard.py` tinha uma segunda implementação, duplicada e melhor,
que separava `autenticado` / `erro` / `saldo`. Quem só exibia era informado; quem
decidia era cego. Este módulo unifica as duas na versão informada.

**Relógio:** `executor.py` sincroniza com `serverTime` (`_offset_ms`) desde o
P0-4, mas `risco.py` assinava com `time.time()` cru. Toda requisição assinada
carrega um `timestamp`, e a Binance rejeita com -1021 fora da janela — então o
offset precisa valer para TODAS as chamadas assinadas, não só as de ordem. Aqui
ele é de módulo, com TTL, e re-sincroniza sozinho.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time

import requests

from config.runtime_settings import API_KEY, API_SECRET, REST_BASE_URL

BASE_URL = REST_BASE_URL
RECV_WINDOW_MS = 5000
OFFSET_TTL_S = 300  # re-sincroniza o relógio a cada 5 min
TIMEOUT_PADRAO = 8

# Placeholders conhecidos do .env.example — chave "presente" mas inútil.
_PLACEHOLDERS = ("your_", "_here", "xxx", "placeholder", "changeme", "sua_chave")

_lock = threading.Lock()
_offset_ms = 0
_offset_em = 0.0


def chave_configurada() -> bool:
    """True se há chave que ao menos parece real (não placeholder do example)."""
    if not API_KEY or not API_SECRET or len(API_KEY) < 20:
        return False
    baixa = API_KEY.lower()
    return not any(p in baixa for p in _PLACEHOLDERS)


def sincronizar_relogio(forcar: bool = False) -> int:
    """Atualiza (com TTL) o offset serverTime-local. Devolve o offset em ms.

    Falha de sync NÃO zera o offset conhecido: um offset velho de 5 min é melhor
    estimativa que assumir zero drift.
    """
    global _offset_ms, _offset_em
    with _lock:
        fresco = (time.time() - _offset_em) < OFFSET_TTL_S
        if fresco and not forcar:
            return _offset_ms
    try:
        r = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
        server_ms = int(r.json()["serverTime"])
        novo = server_ms - int(time.time() * 1000)
        with _lock:
            _offset_ms = novo
            _offset_em = time.time()
        return novo
    except Exception:
        with _lock:
            return _offset_ms


def timestamp_ms() -> int:
    """Timestamp já compensado pelo drift, para requisições assinadas."""
    return int(time.time() * 1000) + sincronizar_relogio()


def _assinar(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()


def ler_conta(timeout: int = TIMEOUT_PADRAO) -> dict:
    """GET /api/v3/account. NUNCA levanta — devolve o resultado descrito.

    Retorna:
      ok           bool  — a leitura foi bem-sucedida
      autenticado  bool  — a Binance aceitou a assinatura (HTTP 200)
      erro         str|None — motivo legível quando ok=False
      saldos       dict  — {ativo: free} apenas dos ativos com saldo > 0
      permissoes   list  — campo `permissions` da conta
      pode_operar  bool  — `canTrade` da CONTA (atenção: NÃO é permissão da
                           CHAVE; a da chave está em /sapi/v1/account/
                           apiRestrictions -> enableSpotAndMarginTrading)
    """
    vazio = {
        "ok": False,
        "autenticado": False,
        "erro": None,
        "saldos": {},
        "permissoes": [],
        "pode_operar": False,
    }
    if not chave_configurada():
        return {**vazio, "erro": "chave de API ausente ou placeholder"}

    try:
        params = {"timestamp": timestamp_ms(), "recvWindow": RECV_WINDOW_MS}
        params["signature"] = _assinar(params)
        r = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=timeout,
        )
    except Exception as e:
        return {**vazio, "erro": f"{type(e).__name__}: {str(e)[:120]}"}

    try:
        corpo = r.json()
    except Exception:
        return {**vazio, "erro": f"HTTP {r.status_code} com corpo não-JSON"}

    if r.status_code != 200:
        cod = corpo.get("code")
        msg = corpo.get("msg", f"HTTP {r.status_code}")
        # -1021 = timestamp fora da janela: re-sincroniza para a próxima chamada
        # já sair certa, em vez de repetir o erro em loop.
        if cod == -1021:
            sincronizar_relogio(forcar=True)
        return {**vazio, "erro": f"[{cod}] {msg}" if cod else str(msg)}

    saldos = {}
    for b in corpo.get("balances", []):
        try:
            livre = float(b.get("free", 0) or 0)
        except (TypeError, ValueError):
            continue
        if livre > 0:
            saldos[b.get("asset")] = livre

    return {
        "ok": True,
        "autenticado": True,
        "erro": None,
        "saldos": saldos,
        "permissoes": corpo.get("permissions", []),
        "pode_operar": bool(corpo.get("canTrade")),
    }


def saldo(ativo: str, timeout: int = TIMEOUT_PADRAO) -> tuple[float, str | None]:
    """Saldo livre de um ativo. Devolve (valor, erro).

    O `erro` é o ponto do módulo: `(0.0, None)` significa "a conta tem zero";
    `(0.0, "...")` significa "não foi possível saber". Quem chama decide o que
    fazer com a diferença — antes ela nem existia.
    """
    r = ler_conta(timeout=timeout)
    if not r["ok"]:
        return 0.0, r["erro"]
    return float(r["saldos"].get(ativo.upper(), 0.0)), None


def restricoes_chave(timeout: int = TIMEOUT_PADRAO) -> dict:
    """GET /sapi/v1/account/apiRestrictions — permissões da CHAVE.

    Distinção que já enganou uma investigação: `canTrade` de /api/v3/account é
    da CONTA e pode ser True enquanto a chave é read-only. Para saber se o bot
    consegue mandar ordem, o que vale é `enableSpotAndMarginTrading` daqui.
    """
    if not chave_configurada():
        return {"ok": False, "erro": "chave de API ausente ou placeholder"}
    try:
        params = {"timestamp": timestamp_ms(), "recvWindow": RECV_WINDOW_MS}
        params["signature"] = _assinar(params)
        r = requests.get(
            f"{BASE_URL}/sapi/v1/account/apiRestrictions",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=timeout,
        )
        corpo = r.json()
    except Exception as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {str(e)[:120]}"}

    if r.status_code != 200:
        return {"ok": False, "erro": f"[{corpo.get('code')}] {corpo.get('msg')}"}
    return {
        "ok": True,
        "erro": None,
        "pode_negociar_spot": bool(corpo.get("enableSpotAndMarginTrading")),
        "pode_sacar": bool(corpo.get("enableWithdrawals")),
        "pode_futures": bool(corpo.get("enableFutures")),
        "restrito_por_ip": bool(corpo.get("ipRestrict")),
        "somente_leitura": bool(corpo.get("enableReading"))
        and not bool(corpo.get("enableSpotAndMarginTrading")),
    }


if __name__ == "__main__":
    print(f"\n  chave configurada: {chave_configurada()}")
    print(f"  offset de relogio: {sincronizar_relogio(forcar=True)} ms")
    c = ler_conta()
    print(f"\n  ok={c['ok']} autenticado={c['autenticado']} erro={c['erro']}")
    if c["ok"]:
        print(f"  permissoes={c['permissoes']}  canTrade(conta)={c['pode_operar']}")
        print("  saldos != 0:")
        for a, v in sorted(c["saldos"].items(), key=lambda x: -x[1]):
            print(f"    {a:8} {v}")
    p = restricoes_chave()
    print(f"\n  restricoes da CHAVE: {p}\n")
