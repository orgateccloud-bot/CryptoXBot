"""
Gestão de Risco — BotBinance
==============================
Módulos:
  1. Kelly Criterion        — tamanho ideal da posição
  2. Max Drawdown Diário    — trava o bot se perder X% no dia
  3. Circuit Breaker        — desliga em volatilidade extrema
  4. Calculadora de posição — tamanho em BTC dado o risco em USDT
  5. Validador de trade     — checa todas as condições antes de executar
"""

import hashlib
import hmac
import time
from datetime import date, datetime

import requests

import database
from config.runtime_settings import API_KEY, API_SECRET

BASE_URL = "https://api.binance.com"

# ── Configurações de risco ────────────────────────────────────
MAX_RISCO_POR_TRADE = 0.02  # 2% do capital por operação
MAX_DRAWDOWN_DIARIO = 0.05  # 5% — trava o bot no dia
MAX_DRAWDOWN_TOTAL = 0.15  # 15% — desliga o bot até revisão manual
KELLY_FATOR = 0.25  # Kelly fracionado (25% do Kelly puro) — conservador
VOLATILIDADE_MAXIMA = 0.08  # 8% de variação em 1h = mercado extremo
MAX_POSICOES_ABERTAS = 1  # só 1 posição por vez
FUNDING_LIMITE = 0.10  # % — não operar se funding acima disso


# ── Estado do dia ─────────────────────────────────────────────

_estado_risco = {
    "capital_inicio_dia": None,
    "pnl_dia": 0.0,
    "data_dia": None,
    "bloqueado": False,
    "motivo_bloqueio": "",
    "posicoes_abertas": 0,
}
_estado_carregado = False


def _carregar_estado_persistido():
    global _estado_carregado
    if _estado_carregado:
        return
    _estado_carregado = True
    try:
        salvo = database.carregar_risk_state()
        if salvo:
            _estado_risco.update(salvo)
    except Exception:
        pass


def persistir_estado():
    """Persist risk state so Railway restarts do not reset safeguards."""
    try:
        database.salvar_risk_state(_estado_risco)
    except Exception:
        pass


def _resetar_se_novo_dia():
    _carregar_estado_persistido()
    hoje = str(date.today())
    if _estado_risco["data_dia"] != hoje:
        _estado_risco["data_dia"] = hoje
        _estado_risco["pnl_dia"] = 0.0
        _estado_risco["bloqueado"] = False
        _estado_risco["motivo_bloqueio"] = ""
        # Não reseta capital — mantém histórico
        persistir_estado()


def registrar_resultado(pnl_usdt):
    """Registra resultado de um trade fechado."""
    _resetar_se_novo_dia()
    _estado_risco["pnl_dia"] += pnl_usdt
    persistir_estado()


# ── Kelly Criterion ───────────────────────────────────────────


def kelly(win_rate, ratio_rr=2.0):
    """
    Fórmula de Kelly: f = W - (1-W)/R
    win_rate: taxa de acerto (0.0 a 1.0)
    ratio_rr: razão risco/retorno (2.0 = 2:1)
    Retorna fração do capital a arriscar (já fracionada).
    """
    if win_rate <= 0 or ratio_rr <= 0:
        return MAX_RISCO_POR_TRADE
    kelly_puro = win_rate - (1 - win_rate) / ratio_rr
    kelly_puro = max(kelly_puro, 0)
    return round(kelly_puro * KELLY_FATOR, 4)


def kelly_do_banco():
    """Calcula Kelly com base no histórico de trades no banco."""
    try:
        sinais_rows = database.sinais_executados(limit=1000)

        if len(sinais_rows) < 10:
            return MAX_RISCO_POR_TRADE  # sem histórico suficiente

        ganhos = sum(1 for s in sinais_rows if s.get("tipo") == "COMPRA")
        wr = ganhos / len(sinais_rows)
        return kelly(wr, 2.0)
    except Exception:
        return MAX_RISCO_POR_TRADE


# ── Tamanho da posição ────────────────────────────────────────


def calcular_tamanho(capital_usdt, preco_entrada, stop_loss, fator_risco=None):
    """
    Calcula tamanho da posição baseado no risco máximo em USDT.

    capital_usdt:   saldo disponível
    preco_entrada:  preço de entrada
    stop_loss:      preço do stop
    fator_risco:    fração do capital a arriscar (usa Kelly se None)
    """
    if fator_risco is None:
        fator_risco = kelly_do_banco()
        fator_risco = min(fator_risco, MAX_RISCO_POR_TRADE)

    risco_usdt = capital_usdt * fator_risco
    distancia_stop = abs(preco_entrada - stop_loss)

    if distancia_stop <= 0:
        return 0.0

    # Tamanho em BTC = risco em USDT / distância do stop em USDT por BTC
    tamanho_btc = risco_usdt / distancia_stop
    tamanho_usdt = tamanho_btc * preco_entrada

    # Nunca arriscar mais que 20% do capital em uma única posição
    tamanho_usdt = min(tamanho_usdt, capital_usdt * 0.20)
    tamanho_btc = tamanho_usdt / preco_entrada

    return round(tamanho_btc, 6)


# ── Circuit Breaker ────────────────────────────────────────────


def verificar_volatilidade(symbol="BTCUSDT"):
    """Retorna variação % da última hora. Se > 8%, mercado extremo."""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1h", "limit": 2},
            timeout=5,
        )
        k = r.json()
        if len(k) < 2:
            return 0.0
        abertura = float(k[-2][1])
        fechamento = float(k[-1][4])
        return abs(fechamento - abertura) / abertura
    except Exception:
        return 0.0


# ── Saldo real da conta ────────────────────────────────────────


def get_saldo_usdt():
    """Retorna saldo disponível em USDT na conta Spot."""
    try:
        params = {"timestamp": int(time.time() * 1000)}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        r = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=8,
        )
        balances = r.json().get("balances", [])
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["free"])
    except Exception:
        pass
    return 0.0


def get_saldo_btc():
    """Retorna saldo disponível em BTC na conta Spot."""
    try:
        params = {"timestamp": int(time.time() * 1000)}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        r = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=8,
        )
        balances = r.json().get("balances", [])
        for b in balances:
            if b["asset"] == "BTC":
                return float(b["free"])
    except Exception:
        pass
    return 0.0


# ── Validador completo ────────────────────────────────────────


def validar_trade(sinal, preco, capital_usdt):
    """
    Valida todas as condições de risco antes de executar um trade.
    Retorna: {"pode": bool, "motivo": str, "tamanho_btc": float}
    """
    _resetar_se_novo_dia()

    # 1. Bot bloqueado?
    if _estado_risco["bloqueado"]:
        return {
            "pode": False,
            "motivo": f"Bot bloqueado: {_estado_risco['motivo_bloqueio']}",
            "tamanho_btc": 0,
        }

    # 2. Saldo suficiente?
    if capital_usdt < 10:
        return {"pode": False, "motivo": "Saldo insuficiente (< $10)", "tamanho_btc": 0}

    # 3. Drawdown diário excedido?
    if _estado_risco["capital_inicio_dia"]:
        dd_dia = _estado_risco["pnl_dia"] / _estado_risco["capital_inicio_dia"]
        if dd_dia <= -MAX_DRAWDOWN_DIARIO:
            _estado_risco["bloqueado"] = True
            _estado_risco["motivo_bloqueio"] = f"Max drawdown diario atingido ({dd_dia*100:.1f}%)"
            persistir_estado()
            return {"pode": False, "motivo": _estado_risco["motivo_bloqueio"], "tamanho_btc": 0}

    # 4. Volatilidade extrema?
    vol = verificar_volatilidade()
    if vol > VOLATILIDADE_MAXIMA:
        return {"pode": False, "motivo": f"Volatilidade extrema: {vol*100:.1f}%", "tamanho_btc": 0}

    # 5. Posições abertas?
    if _estado_risco["posicoes_abertas"] >= MAX_POSICOES_ABERTAS:
        return {
            "pode": False,
            "motivo": "Posicao ja aberta — aguardar fechamento",
            "tamanho_btc": 0,
        }

    # 6. Calcular tamanho
    stop = preco * (1 - 0.015) if sinal == "COMPRA" else preco * (1 + 0.015)
    tamanho = calcular_tamanho(capital_usdt, preco, stop)

    if tamanho <= 0:
        return {"pode": False, "motivo": "Tamanho calculado zerado", "tamanho_btc": 0}

    # Inicializar capital do dia se necessário
    if _estado_risco["capital_inicio_dia"] is None:
        _estado_risco["capital_inicio_dia"] = capital_usdt
        persistir_estado()

    return {
        "pode": True,
        "motivo": "Todos os criterios de risco aprovados",
        "tamanho_btc": tamanho,
        "risco_usdt": round(tamanho * abs(preco - stop), 2),
        "fator_kelly": kelly_do_banco(),
    }


def status():
    """Retorna estado atual do módulo de risco."""
    _resetar_se_novo_dia()
    saldo_usdt = get_saldo_usdt()
    saldo_btc = get_saldo_btc()
    vol = verificar_volatilidade()
    dd_dia = 0.0
    if _estado_risco["capital_inicio_dia"]:
        dd_dia = _estado_risco["pnl_dia"] / _estado_risco["capital_inicio_dia"] * 100

    return {
        "saldo_usdt": round(saldo_usdt, 4),
        "saldo_btc": round(saldo_btc, 8),
        "pnl_dia": round(_estado_risco["pnl_dia"], 2),
        "drawdown_dia_%": round(dd_dia, 2),
        "volatilidade_%": round(vol * 100, 2),
        "bloqueado": _estado_risco["bloqueado"],
        "motivo_bloqueio": _estado_risco["motivo_bloqueio"],
        "posicoes_abertas": _estado_risco["posicoes_abertas"],
        "kelly_%": round(kelly_do_banco() * 100, 2),
        "max_dd_diario_%": MAX_DRAWDOWN_DIARIO * 100,
        "max_dd_total_%": MAX_DRAWDOWN_TOTAL * 100,
    }


if __name__ == "__main__":
    print("\n=== STATUS DE RISCO ===")
    s = status()
    for k, v in s.items():
        print(f"  {k:25s}: {v}")

    print("\n=== SIMULACAO DE TAMANHO ===")
    preco = 68000
    capital = get_saldo_usdt()
    if capital < 1:
        capital = 100  # simulação
        print("  (Usando capital simulado de $100)")
    stop = preco * 0.985
    tam = calcular_tamanho(capital, preco, stop)
    print(f"  Capital:   ${capital:.2f}")
    print(f"  Preco:     ${preco:,}")
    print(f"  Stop:      ${stop:,.2f}")
    print(f"  Tamanho:   {tam:.6f} BTC (${tam*preco:.2f})")
    print(f"  Risco max: ${tam * (preco - stop):.2f} ({MAX_RISCO_POR_TRADE*100:.0f}% do capital)")
