"""
Fear & Greed Index — BotBinance
=================================
Fonte: alternative.me/crypto/fear-and-greed-index/
API publica, sem autenticacao necessaria.

Escala:
  0-24   Medo Extremo    → mercado em panico, possivel fundo
  25-44  Medo            → cautela, evitar entradas longas
  45-55  Neutro          → sem vantagem clara
  56-74  Ganancia        → momento favoravel para longs
  75-100 Ganancia Extrema → risco de reversao, cautela

Regras de operacao:
  - Medo Extremo (< 25):     NAO operar (panico generalizado)
  - Medo (25-44):            Cautela, exigir mais confirmacoes
  - Neutro (45-55):          Operar normalmente
  - Ganancia (56-74):        Operar com alvo reduzido (mercado aquecido)
  - Ganancia Extrema (> 74): NAO abrir novas posicoes (topo provavel)
"""

import threading
from datetime import datetime

import requests

API_URL = "https://api.alternative.me/fng/?limit=3&format=json"
CACHE_TTL_S = 900  # 15 minutos

# Limites de operacao
MEDO_EXTREMO_MAX = 24  # abaixo disso: nao operar
GANANCIA_MAX = 74  # acima disso: nao abrir novas posicoes
IDEAL_MIN = 35  # zona ideal para entrar
IDEAL_MAX = 70  # zona ideal para entrar

_cache: dict = {"valor": None, "classificacao": None, "timestamp": None, "historico": []}
_cache_lock = threading.Lock()


def obter():
    """
    Retorna o Fear & Greed Index atual.
    Usa cache de 15 minutos (thread-safe) para nao sobrecarregar a API.

    Retorna dict com:
      valor:          0-100
      classificacao:  texto (ex: "Medo Extremo")
      classificacao_pt: traducao
      pode_operar:    bool
      reducao_alvo:   bool (true = reduzir take profit em 50%)
      motivo:         str
      historico:      list dos ultimos 3 dias
    """
    global _cache

    with _cache_lock:
        if _cache["timestamp"]:
            diff = (datetime.now() - _cache["timestamp"]).total_seconds()
            if diff < CACHE_TTL_S:
                return _cache

    try:
        r = requests.get(API_URL, timeout=8)
        r.raise_for_status()
        data = r.json()["data"]

        historico = []
        for item in data:
            historico.append(
                {
                    "valor": int(item["value"]),
                    "classificacao": item["value_classification"],
                    "data": datetime.fromtimestamp(int(item["timestamp"])).strftime("%d/%m"),
                }
            )

        atual = historico[0]
        valor = atual["valor"]

        # Traducao
        trad = {
            "Extreme Fear": "Medo Extremo",
            "Fear": "Medo",
            "Neutral": "Neutro",
            "Greed": "Ganancia",
            "Extreme Greed": "Ganancia Extrema",
        }
        classif_pt = trad.get(atual["classificacao"], atual["classificacao"])

        # Regras de operacao
        if valor <= MEDO_EXTREMO_MAX:
            pode_operar = False
            reducao_alvo = False
            motivo = f"Fear & Greed {valor} — Medo Extremo: mercado em panico, nao operar"
        elif valor > GANANCIA_MAX:
            pode_operar = False
            reducao_alvo = False
            motivo = f"Fear & Greed {valor} — Ganancia Extrema: risco de reversao iminente"
        elif IDEAL_MIN <= valor <= IDEAL_MAX:
            pode_operar = True
            reducao_alvo = False
            motivo = f"Fear & Greed {valor} — {classif_pt}: zona ideal para operar"
        elif valor < IDEAL_MIN:
            pode_operar = True
            reducao_alvo = True
            motivo = f"Fear & Greed {valor} — {classif_pt}: operar com cautela (alvo reduzido)"
        else:
            pode_operar = True
            reducao_alvo = True
            motivo = f"Fear & Greed {valor} — {classif_pt}: mercado aquecido (alvo reduzido)"

        novo = {
            "valor": valor,
            "classificacao": atual["classificacao"],
            "classificacao_pt": classif_pt,
            "pode_operar": pode_operar,
            "reducao_alvo": reducao_alvo,
            "motivo": motivo,
            "historico": historico,
            "timestamp": datetime.now(),
        }
        with _cache_lock:
            _cache = novo
        return novo

    except Exception as e:
        # Em caso de falha, retorna neutro (nao bloqueia o bot)
        return {
            "valor": 50,
            "classificacao": "Neutral",
            "classificacao_pt": "Neutro (offline)",
            "pode_operar": True,
            "reducao_alvo": False,
            "motivo": f"Fear & Greed indisponivel ({e}) — assumindo Neutro",
            "historico": [],
            "timestamp": datetime.now(),
        }


def imprimir():
    fg = obter()

    # Barra visual
    v = fg["valor"]
    barra_len = 30
    pos = int(v / 100 * barra_len)
    barra = "[" + "#" * pos + "-" * (barra_len - pos) + "]"

    if v <= 24:
        cor = "\033[91m"  # vermelho
    elif v <= 44:
        cor = "\033[93m"  # amarelo
    elif v <= 55:
        cor = "\033[37m"  # branco
    elif v <= 74:
        cor = "\033[92m"  # verde
    else:
        cor = "\033[95m"  # magenta

    reset = "\033[0m"
    verde = "\033[92m"
    vermelho = "\033[91m"

    print("\n" + "=" * 50)
    print("  FEAR & GREED INDEX")
    print("=" * 50)
    print(f"  Valor:    {cor}{v:3d}/100  {barra}{reset}")
    print(f"  Status:   {cor}{fg['classificacao_pt']}{reset}")
    print()

    if fg["historico"]:
        print("  Historico (ultimos dias):")
        for h in fg["historico"]:
            print(f"    {h['data']}: {h['valor']:3d} — {h['classificacao']}")

    print()
    print(f"  Pode Operar:  {verde+'SIM'+reset if fg['pode_operar'] else vermelho+'NAO'+reset}")
    if fg["reducao_alvo"]:
        print(f"  {cor}ATENCAO: Reduzir alvo em 50% (mercado em zona de risco){reset}")
    print(f"  {fg['motivo']}")
    print("=" * 50)
    return fg


if __name__ == "__main__":
    imprimir()
