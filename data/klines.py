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

import threading
import time

import requests

from config.runtime_settings import REST_BASE_URL

BASE_URL = REST_BASE_URL

# TTL curto (nao os minutos entre ticks de main.py/loop_par) -- so precisa
# ser maior que a janela em que as 3 threads de par chamam o mesmo
# symbol/intervalo quase simultaneamente (ex: no boot, ou quando os
# intervalos de sleep de cada thread coincidem). Um TTL longo deixaria a
# vela em formacao (o ultimo candle, ainda "vivo") desatualizada por mais
# tempo que o necessario.
TTL_PADRAO_SEGUNDOS = 30

_cache: dict[tuple[str, str, int], dict] = (
    {}
)  # (symbol, intervalo, limit) -> {"dados", "timestamp"}
_lock = threading.Lock()


def _fetch_rest(symbol: str, intervalo: str, limit: int) -> dict | None:
    try:
        r = requests.get(
            f"{BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": intervalo, "limit": limit},
            timeout=8,
        )
        k = r.json()
        return {
            "abertura": [float(x[1]) for x in k],
            "maxima": [float(x[2]) for x in k],
            "minima": [float(x[3]) for x in k],
            "fechamento": [float(x[4]) for x in k],
            "volume": [float(x[5]) for x in k],
        }
    except Exception:
        return None


def obter_klines(
    symbol: str = "BTCUSDT",
    intervalo: str = "1h",
    limit: int = 100,
    ttl_segundos: int = TTL_PADRAO_SEGUNDOS,
) -> dict | None:
    """Retorna dict {abertura, maxima, minima, fechamento, volume} (listas
    paralelas, mesma ordem cronológica da API), cacheado por
    (symbol, intervalo, limit) com TTL curto. Lock único, toda a lógica
    (checagem de TTL + fetch se necessário + gravação) dentro de UM 'with'
    -- mesmo padrão de risco._cache_correlacao: serializa fetches
    concorrentes em vez de arriscar corrida/fetches redundantes.

    Falha de rede: mantém o dado antigo em cache (mesmo expirado) se
    existir, em vez de descartar por causa de uma falha transitória.
    Sem cache anterior e fetch falhou -> None."""
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

        return entry["dados"] if entry is not None else None
