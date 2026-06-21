"""
Runtime settings for local, Railway, and Supabase deployments.

Environment variables take precedence. A local ignored config/settings.py can
still exist for Windows development, but production no longer depends on it.
"""

from __future__ import annotations

import os
from typing import Any

try:  # Local-only, ignored by git. Do not require it in production.
    from config import settings as _local_settings  # type: ignore
except Exception:  # pragma: no cover - depends on developer machine
    _local_settings = None


def _local(name: str, default: Any = None) -> Any:
    return getattr(_local_settings, name, default) if _local_settings else default


def _env(name: str, default: Any = None) -> Any:
    value = os.getenv(name)
    return default if value in (None, "") else value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


API_KEY = _env("BINANCE_API_KEY", _local("API_KEY", ""))
API_SECRET = _env("BINANCE_API_SECRET", _local("API_SECRET", ""))

SYMBOL = _env("SYMBOL", _local("SYMBOL", "BTCUSDT")).upper()
SYMBOL_WS = _env("SYMBOL_WS", _local("SYMBOL_WS", SYMBOL.lower())).lower()

MIN_BTC_VOLUME = _env_float("MIN_BTC_VOLUME", float(_local("MIN_BTC_VOLUME", 0.5)))
WHALE_BTC_VOLUME = _env_float("WHALE_BTC_VOLUME", float(_local("WHALE_BTC_VOLUME", 5.0)))

REST_BASE_URL = _env("REST_BASE_URL", _local("REST_BASE_URL", "https://fapi.binance.com"))
WS_BASE_URL = _env("WS_BASE_URL", _local("WS_BASE_URL", "wss://fstream.binance.com"))

TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN", _env("TELEGRAM_BOT_TOKEN", _local("TELEGRAM_TOKEN", "")))
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", _local("TELEGRAM_CHAT_ID", ""))

LOG_LEVEL = _env("LOG_LEVEL", _local("LOG_LEVEL", "INFO"))

DB_PATH = _env("DB_PATH", _local("DB_PATH", "data/btc_data.db"))
DATABASE_URL = _env("DATABASE_URL", "")
DATABASE_BACKEND = _env(
    "DATABASE_BACKEND",
    "postgres" if DATABASE_URL else _local("DATABASE_BACKEND", "sqlite"),
).lower()
DB_POOL_MIN = _env_int("DB_POOL_MIN", 1)
DB_POOL_MAX = _env_int("DB_POOL_MAX", 5)

APP_ENV = _env("ENV", _env("APP_ENV", "development"))
SERVICE_ROLE = _env("SERVICE_ROLE", _env("RAILWAY_SERVICE_NAME", "dashboard")).lower()
PORT = _env_int("PORT", 5000)
SECRET_KEY = _env("SECRET_KEY", "botbinance-local-dev")
# Segurança: nunca usar a chave-padrão pública em produção. Sem SECRET_KEY no
# ambiente, gera uma efêmera aleatória (sessões/CSRF reiniciam a cada deploy,
# mas a chave nunca é a conhecida do repositório).
if APP_ENV == "production" and SECRET_KEY == "botbinance-local-dev":
    import secrets as _secrets
    import logging as _logging

    SECRET_KEY = _secrets.token_hex(32)
    _logging.getLogger("botbinance").warning(
        "SECRET_KEY ausente em producao — usando chave aleatoria efemera; "
        "defina SECRET_KEY no ambiente para sessoes persistentes."
    )
CORS_ORIGINS = _env("CORS_ORIGINS", "*")

DRY_RUN = _env_bool("DRY_RUN", True)
ALLOW_REAL_TRADING = _env_bool("ALLOW_REAL_TRADING", False)
ENABLE_HEALTH_SERVER = _env_bool("ENABLE_HEALTH_SERVER", bool(os.getenv("PORT")))

