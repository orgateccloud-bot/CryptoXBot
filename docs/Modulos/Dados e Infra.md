---
tags: [modulo, dados, infra, observabilidade]
---

# 🗄️ Dados, Infra e Observabilidade

> Voltar: [[00 - Home]] · Operação: [[Deploy Supabase]] · [[Deploy Railway]]

---

## `database.py` — Fachada SQLite ↔ Supabase 🟡 Média
- **Propósito:** mesma API para SQLite (dev) e Postgres/Supabase (prod).
- **Seleção de backend:** usa Postgres se `DATABASE_URL` setado **e** `DATABASE_BACKEND ∈ {postgres, postgresql, supabase}`; senão SQLite (`data/btc_data.db`). Pool via `psycopg_pool`.
- **Tabelas (6):** `trades`, `snapshots_mercado`, `cvd_historico`, `sinais`, `risk_state`, `bot_events`.
- **Riscos:** timestamps divergem (SQLite `isoformat` TEXT vs Postgres `TIMESTAMPTZ`); `INSERT OR IGNORE` (SQLite) vs `ON CONFLICT` (PG) não 100% idênticos.
- ✅ Corrigido nesta sessão: `fechar_pool()` agora é chamado no shutdown (SIGTERM/finally em `main.py`) — não vaza mais conexões no restart do Railway.

## `logger.py` — Logging analítico 🟡 Média
- **Propósito:** tabelas `log_avaliacoes`, `log_trades`, `log_performance` (análise detalhada).
- ✅ Corrigido nesta sessão: (1) `LoggerBot` delega `.warning/.error/.critical` (corrige AttributeError nos erros do WebSocket); (2) whitelist de tabelas em `exportar_csv` (SQL injection); cobertura `test_logger` (13).
- **Gap remanescente (P0):** ainda grava SEMPRE em SQLite — em produção Supabase cria um 2º banco desacoplado (split-brain). Port para Postgres é PR dedicado (ver [[Planejamento de Melhorias]]).

## `health.py` — Health endpoint 🟢 Alta
- `/health` (sempre 200) e `/ready` (testa `database.healthcheck()` → 200/503). Usado pelos probes do Railway.

## `telegram_bot.py` — Alertas 🟢 Alta
- Alertas de sinal/trade/circuit breaker/relatório diário. Token via env, sem vazamento. Read-only (não grava em DB).

## `dashboard.py` — UI Flask + SocketIO 🟡 Média
- APIs REST (`/api/estado`, `/api/trades`, `/api/sinais`, `/api/score`, `/api/risco`...) + WebSocket. Lê do `database`. Roda como serviço **web** separado no Railway.

## `monitor_fluxo.py` — CVD standalone 🟢 Alta
- WebSocket aggTrade + CVD acumulado + snapshot a cada 5 min. Thread-safe (GIL). Utilitário independente do `main`.

## `config/` 🟢 Alta
- `runtime_settings.py` — env > local `settings.py` (gitignored) > default. Sem segredos hardcoded.
- `params_pares.py` — hiperparâmetros por par (stop/target/RSI/score) de backtests.
- `settings_template.py` — template seguro para dev local.

## Banco gerenciado (Supabase)
- `supabase/migrations/001_initial_schema.sql` — schema idempotente (`CREATE IF NOT EXISTS`), tipos fortes (BIGSERIAL, TIMESTAMPTZ, JSONB, BOOLEAN), índices por `(symbol, timestamp DESC)`. **Bate** com o schema Postgres de `database.py`.
- `scripts/migrate_sqlite_to_supabase.py` — migrador com `--listar`, `--dry-run`, `--confirmar`, `--validar-pg`. Idempotente em `trades` (trade_id UNIQUE) e `risk_state` (PK name). Gaps: sem wrapper transacional; pool não fechado.

→ Passo a passo em [[Deploy Supabase]].

## Deploy / CI
- `Dockerfile` 🟢 — multi-stage, usuário não-root.
- `docker-compose.prod.yml` 🟡 — assume GCP; health check fraco; serviço `redis` órfão.
- `.github/workflows/ci.yml` 🟢 — lint + smoke test de imports + pytest + security + docker. ✅ smoke test adicionado nesta sessão.
- `.github/workflows/deploy.yml` 🟡 — ainda **GCP-centric** (desatualizado para Railway).

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| health / telegram / monitor_fluxo / config | 🟢 Alta |
| supabase migrations | 🟢 Alta |
| database.py | 🟡 Média |
| dashboard.py / migrador / docker-compose / deploy.yml | 🟡 Média |
| logger.py (SQLite-only) | 🔴 Baixa |
