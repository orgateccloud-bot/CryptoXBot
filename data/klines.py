"""
Klines — Fetch REST consolidado com cache (P1-5)
===================================================
Ponto único para buscar candles via REST da Binance, substituindo as 6
cópias independentes que existiam em regime.py/suporte.py/estrategias/
otimizada.py/risco.py (cada uma com contrato de erro e endpoint próprios
— duas delas hardcoded para https://api.binance.com, ignorando
REST_BASE_URL). Cache em memória (thread-safe, TTL curto) elimina o
fetch redundante quando múltiplas threads de par pedem os mesmos dados
(regime.py e suporte.py sempre pedem BTCUSDT, por exemplo, mas rodam uma
vez por par/thread).

Uso:
  from data.klines import obter_klines
  dados = obter_klines("BTCUSDT", "1h", 100)  # None em falha total
  dados["fechamento"][-1]  # último close
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from config.runtime_settings import REST_BASE_URL

BASE_URL = REST_BASE_URL

# I-11: o modulo nao importava logging. Toda falha de fetch e todo fallback para
# cache expirado aconteciam sem rastro nenhum — inclusive os que alimentavam o
# circuit breaker. Usa o mesmo logger nomeado do resto do projeto, entao herda o
# handler que preserva `extra=` (logger.py).
_log = logging.getLogger("botbinance")

# TTL curto (nao os minutos entre ticks de main.py/loop_par) -- so precisa
# ser maior que a janela em que as 3 threads de par chamam o mesmo
# symbol/intervalo quase simultaneamente (ex: no boot, ou quando os
# intervalos de sleep de cada thread coincidem). Um TTL longo deixaria a
# vela em formacao (o ultimo candle, ainda "vivo") desatualizada por mais
# tempo que o necessario.
TTL_PADRAO_SEGUNDOS = 30

# I-11: teto de idade do fallback para cache expirado. Cinco minutos absorve
# falha transitoria de rede sem alimentar decisao com dado velho — ver o bloco
# comentado no fim de obter_klines(). `idade_maxima_s=None` desliga o teto para
# quem quiser explicitamente o comportamento antigo (nenhum chamador quer).
IDADE_MAXIMA_PADRAO_S = 300

_cache: dict[tuple[str, str, int], dict] = (
    {}
)  # (symbol, intervalo, limit) -> {"dados", "timestamp"}
_lock = threading.Lock()

# E-7: intervalos aceitos pela API de klines da Binance. Fechado de proposito —
# ver _validar() abaixo.
INTERVALOS_VALIDOS = frozenset(
    {
        "1s", "1m", "3m", "5m", "15m", "30m",
        "1h", "2h", "4h", "6h", "8h", "12h",
        "1d", "3d", "1w", "1M",
    }
)


def _validar(symbol: str, intervalo: str) -> None:
    """Recusa symbol/intervalo malformados com ValueError, em vez de deixar a
    API devolver erro e o chamador receber None (E-7).

    Por que isto e uma guarda de dinheiro e nao higiene: `suporte.py` e
    `regime.py` tinham SYMBOL='BTCUSDT' de modulo e assinaturas
    `detectar_suportes(intervalo="1h")` / `detectar()`. Ao tornar `symbol` o
    primeiro parametro, toda chamada posicional antiga -- `detectar_suportes("1h")`
    -- passaria "1h" COMO SYMBOL. Sem esta validacao o resultado seria
    obter_klines("1h", "1h") -> HTTP 400 -> None -> o dict-vazio de fallback,
    e o par continuaria operando com suporte_forte=0: exatamente o modo de falha
    silenciosa que E-7 existe para eliminar, so trocado de ativo-errado por
    dado-ausente. Falhar alto aqui torna a migracao incompleta impossivel de
    passar desapercebida.
    """
    if not isinstance(symbol, str) or not symbol.isalnum() or not 6 <= len(symbol) <= 20:
        raise ValueError(
            f"symbol invalido: {symbol!r}. Esperado par da Binance como 'BTCUSDT'. "
            f"Se voce passou um intervalo aqui, a assinatura e obter_klines(symbol, intervalo)."
        )
    if intervalo not in INTERVALOS_VALIDOS:
        raise ValueError(
            f"intervalo invalido: {intervalo!r}. Validos: {sorted(INTERVALOS_VALIDOS)}"
        )


def _fetch_rest(symbol: str, intervalo: str, limit: int) -> dict | None:
    try:
        r = requests.get(
            f"{BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": intervalo, "limit": limit},
            timeout=8,
        )
        # I-11: raise_for_status. Sem ele, um 429 (rate limit) ou 418 (IP banido)
        # devolvia um JSON de ERRO — `{"code": -1121, "msg": "..."}` — que caía no
        # `float(x[1])` e virava TypeError/ValueError engolido pelo `except` de
        # baixo. O chamador recebia None, indistinguível de "sem rede", e o
        # diagnóstico do rate limit desaparecia. Rate limit tratado como falha de
        # rede leva a retry, que agrava o rate limit.
        r.raise_for_status()
        k = r.json()
        if not isinstance(k, list):
            _log.warning("klines: resposta inesperada p/ %s/%s: %.120s", symbol, intervalo, k)
            return None
        return {
            "abertura": [float(x[1]) for x in k],
            "maxima": [float(x[2]) for x in k],
            "minima": [float(x[3]) for x in k],
            "fechamento": [float(x[4]) for x in k],
            "volume": [float(x[5]) for x in k],
        }
    except Exception as exc:
        # I-11: o módulo não importava logging — toda falha de fetch era muda, e
        # o fallback para cache expirado (abaixo) acontecia sem nenhum rastro.
        _log.warning("klines: falha ao buscar %s/%s: %s", symbol, intervalo, exc)
        return None


def obter_klines(
    symbol: str = "BTCUSDT",
    intervalo: str = "1h",
    limit: int = 100,
    ttl_segundos: int = TTL_PADRAO_SEGUNDOS,
    idade_maxima_s: int | None = IDADE_MAXIMA_PADRAO_S,
) -> dict | None:
    """Retorna dict {abertura, maxima, minima, fechamento, volume} (listas
    paralelas, mesma ordem cronológica da API), cacheado por
    (symbol, intervalo, limit) com TTL curto. Lock único, toda a lógica
    (checagem de TTL + fetch se necessário + gravação) dentro de UM 'with'
    -- mesmo padrão de risco._cache_correlacao: serializa fetches
    concorrentes em vez de arriscar corrida/fetches redundantes.

    Falha de rede: mantém o dado antigo em cache (mesmo expirado) se
    existir, em vez de descartar por causa de uma falha transitória.
    Sem cache anterior e fetch falhou -> None.

    Levanta ValueError para symbol/intervalo malformados (E-7): erro de
    PROGRAMACAO nao pode se disfarcar de falha de rede, senao a diferenca entre
    "a Binance esta fora" e "estou lendo o ativo errado" desaparece do log."""
    _validar(symbol, intervalo)
    key = (symbol.upper(), intervalo, limit)
    with _lock:
        entry = _cache.get(key)
        agora = time.time()
        if entry is not None and (agora - entry["timestamp"]) < ttl_segundos:
            return entry["dados"]

        dados = _fetch_rest(symbol.upper(), intervalo, limit)
        if dados is not None:
            _cache[key] = {"dados": dados, "timestamp": agora}
            return dados

        # ── I-11: o fallback para cache expirado ganhou TETO DE IDADE ────────
        #
        # Antes, `return entry["dados"]` devolvia o cache por tempo indefinido:
        # com a Binance fora por horas, `risco.verificar_volatilidade()` — o
        # CIRCUIT BREAKER — recebia preços de horas atrás e concluía "mercado
        # calmo" sobre um mercado que podia estar em queda livre. Um circuit
        # breaker alimentado por dado velho é pior que nenhum: ele autoriza.
        #
        # Manter o cache alguns minutos continua certo (falha transitória não
        # deve derrubar o bot). Passar do teto, não: aí o correto é devolver None
        # e deixar cada chamador decidir, com a informação de que não há dado.
        if entry is None:
            return None
        idade = agora - entry["timestamp"]
        if idade_maxima_s is not None and idade > idade_maxima_s:
            _log.error(
                "klines: cache de %s/%s tem %.0fs (teto %ss) — devolvendo None em vez de "
                "dado velho. Consumidores de decisao (circuit breaker) nao podem receber stale.",
                symbol, intervalo, idade, idade_maxima_s,
            )
            return None
        _log.warning(
            "klines: servindo cache STALE de %s/%s (%.0fs) — fetch falhou.",
            symbol, intervalo, idade,
        )
        return entry["dados"]
