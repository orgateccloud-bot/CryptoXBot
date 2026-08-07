"""
Runtime settings para desenvolvimento local e deploy em produção (Supabase + VPS).

Ordem de precedência: variáveis de ambiente > config/settings.py local (dev) > padrões.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

# Docker/systemd ja injetam .env como env vars do processo (env_file / Environ
# mentFile); load_dotenv() nao sobrescreve o que ja estiver setado no os.environ
# - so preenche a lacuna quando o processo roda direto (python main.py).
load_dotenv()

# I-8 (2026-08-07): o fallback para config/settings.py foi REMOVIDO.
#
# Ele era um import protegido por try/except, invisivel a qualquer varredura
# ingenua de dependencias, e tinha PRECEDENCIA sobre os defaults deste arquivo.
# Duas consequencias medidas:
#
#   1. API_KEY/API_SECRET caiam para valores hardcoded em config/settings.py
#      quando o .env nao os definia — credencial resolvida por arquivo local nao
#      versionado, sem nenhum aviso no boot.
#   2. REST_BASE_URL/WS_BASE_URL caiam para fapi.binance.com / fstream, ou seja
#      FUTUROS, sobrepondo o default SPOT que o P0-1 estabeleceu justamente para
#      eliminar a divergencia sinal-execucao. Isso ficava mascarado por duas
#      linhas do .env: bastava remove-las para o bot silenciosamente passar a ler
#      Futures enquanto executava Spot.
#
# Agora a precedencia e apenas: variavel de ambiente > default deste arquivo.
# Configuracao local se faz por .env (ver .env.example), que load_dotenv() ja le.
# config/settings.py continua existindo na maquina do desenvolvedor (gitignored)
# e simplesmente deixou de ser consultado.


def _local(name: str, default: Any = None) -> Any:  # noqa: ARG001 - assinatura preservada
    """Mantida por compatibilidade de chamada; NAO le mais config/settings.py.

    As ~30 chamadas `_env("X", _local("X", padrao))` continuam validas e agora
    resolvem direto para o padrao. Preservar a assinatura evita um diff de 30
    linhas num arquivo que 13 modulos importam.
    """
    return default


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

# Mercado unificado: SPOT (P0-1). O executor sempre operou spot (/api/v3);
# os dados (WS/klines) agora seguem o mesmo mercado para eliminar divergencia
# sinal-execucao. Futures (fapi/fstream) só via override explicito no .env.
REST_BASE_URL = _env("REST_BASE_URL", _local("REST_BASE_URL", "https://api.binance.com"))
WS_BASE_URL = _env("WS_BASE_URL", _local("WS_BASE_URL", "wss://stream.binance.com:9443"))

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
SERVICE_ROLE = _env("SERVICE_ROLE", "worker").lower()
PORT = _env_int("PORT", 5000)
SECRET_KEY = _env("SECRET_KEY", "botbinance-local-dev")
# Segurança: nunca usar a chave-padrão pública em produção. Sem SECRET_KEY no
# ambiente, gera uma efêmera aleatória (sessões/CSRF reiniciam a cada deploy,
# mas a chave nunca é a conhecida do repositório).
if APP_ENV == "production" and SECRET_KEY == "botbinance-local-dev":
    import logging as _logging
    import secrets as _secrets

    SECRET_KEY = _secrets.token_hex(32)
    _logging.getLogger("botbinance").warning(
        "SECRET_KEY ausente em producao — usando chave aleatoria efemera; "
        "defina SECRET_KEY no ambiente para sessoes persistentes."
    )
CORS_ORIGINS = _env("CORS_ORIGINS", "*")
# Auditoria 2026-07-17: CORS_ORIGINS='*' deixa qualquer pagina aberta na mesma
# maquina ler saldo/estrategia via JS (o dashboard nao tem CORS proprio). Em
# producao, '*' nunca e respeitado — vindo do default ou de um .env real
# copiado do template antigo (ambos indistinguiveis na pratica, e o template
# antigo tinha CORS_ORIGINS=* descomentado) — sempre negamos cross-origin por
# padrao (dashboard.py traduz isso para cada biblioteca: [] no Flask-CORS,
# None no Socket.IO). Acessar o dashboard diretamente continua funcionando
# normalmente, so a leitura cross-origin e bloqueada; para liberar de fato,
# defina CORS_ORIGINS com um dominio real (nao wildcard).
CORS_SAME_ORIGIN_ONLY = False
if APP_ENV == "production" and CORS_ORIGINS == "*":
    import logging as _log_cors

    CORS_SAME_ORIGIN_ONLY = True
    _log_cors.getLogger("botbinance").warning(
        "CORS_ORIGINS='*' em producao — bloqueando acesso cross-origin por "
        "padrao (o dashboard acessado diretamente continua funcionando "
        "normalmente). Defina CORS_ORIGINS=https://seu-dominio.com se precisar "
        "de acesso cross-origin de fato."
    )

DRY_RUN = _env_bool("DRY_RUN", True)
ALLOW_REAL_TRADING = _env_bool("ALLOW_REAL_TRADING", False)
ENABLE_HEALTH_SERVER = _env_bool("ENABLE_HEALTH_SERVER", bool(os.getenv("PORT")))

# P0-2: execução maker-first (post-only). Entradas viram LIMIT_MAKER no melhor
# bid — sempre paga fee de maker, nunca cruza o spread. Se não preencher em
# MAKER_TIMEOUT_S, cancela e re-quota no novo bid (até MAKER_MAX_REQUOTES).
MAKER_FIRST = _env_bool("MAKER_FIRST", True)
MAKER_TIMEOUT_S = _env_int("MAKER_TIMEOUT_S", 20)
MAKER_MAX_REQUOTES = _env_int("MAKER_MAX_REQUOTES", 3)

# P2-1: bracket OCO nativo (POST /api/v3/orderList/oco) como protecao pos-entrada.
# Quando ligado, o stop E o alvo final (target2) viram um par atomico
# one-cancels-the-other na Binance — o alvo passa a viver NA EXCHANGE (nao so no
# monitor local), sobrevivendo a crash do bot. O take-profit parcial (50% no
# target1) segue orquestrado pelo monitor local (um OCO tem 1 qty / 2 pernas,
# nao expressa parcial+runner) — no parcial o OCO e cancelado, vende-se 50% e um
# novo OCO e recolocado para o restante.
# OCO_TRAILING_DELTA_BIPS>0 anexa belowTrailingDelta a perna de stop (trailing
# SERVER-SIDE, em bips: 80 = 0.8%): a exchange trilha o stop sozinha e o
# cancel-then-replace do trailing local e suprimido para nao brigar com o servidor.
# Default OFF: mudanca no caminho de dinheiro real exige validacao em paper +
# sign-off explicito antes de ativar (mesma cautela de MAKER_FIRST / P1-2).
OCO_BRACKET = _env_bool("OCO_BRACKET", False)
OCO_TRAILING_DELTA_BIPS = _env_int("OCO_TRAILING_DELTA_BIPS", 0)

# Auditoria 2026-07-22: o boot recovery legado (loop_par em main.py) so le a
# posicao persistida no DB e nunca cruza com o estado real da Binance (saldo/
# ordens abertas) -- um crash entre o fill e o registro local deixa uma
# posicao orfa (real + protegida na exchange) invisivel ao reiniciar; o
# inverso (DB com posicao ja fechada fora do bot) tambem nao e detectado.
# RECONCILIAR_BOOT_EXCHANGE=true liga Executor.reconciliar_boot() (GET
# /api/v3/account + openOrders/openOrderList + myTrades) antes de decidir
# religar o monitor. Default OFF: mudanca no caminho de dinheiro real exige
# validacao em paper/drills de restart antes de ativar (mesma cautela de
# OCO_BRACKET/MAKER_FIRST) -- sem a flag, o comportamento e o legado (confia
# cegamente no DB).
RECONCILIAR_BOOT_EXCHANGE = _env_bool("RECONCILIAR_BOOT_EXCHANGE", False)
