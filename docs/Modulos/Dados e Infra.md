---
tags: [modulo, dados, infra, observabilidade]
---

# 🗄️ Dados, Infra e Observabilidade

> Voltar: [[00 - Home]] · Operação: [[Deploy Supabase]] · [[Deploy VPS]]

---

## `database.py` — Fachada SQLite ↔ Supabase 🟡 Média
- **Propósito:** mesma API para SQLite (dev) e Postgres/Supabase (prod).
- **Seleção de backend:** usa Postgres se `DATABASE_URL` setado **e** `DATABASE_BACKEND ∈ {postgres, postgresql, supabase}`; senão SQLite (`data/btc_data.db`). Pool via `psycopg_pool`.
- **Tabelas (6):** `trades`, `snapshots_mercado`, `cvd_historico`, `sinais`, `risk_state`, `bot_events`.
- **Riscos:** timestamps divergem (SQLite `isoformat` TEXT vs Postgres `TIMESTAMPTZ`); `INSERT OR IGNORE` (SQLite) vs `ON CONFLICT` (PG) não 100% idênticos.
- ✅ Corrigido: `fechar_pool()` é chamado no shutdown gracioso (SIGTERM/SIGINT/SIGBREAK + finally em `main.py`) — não vaza mais conexões no restart do serviço (NSSM/systemd).
- ✅ **Crash recovery de posição**: `salvar_posicao_aberta` persiste a posição
  aberta (reusa `risk_state` com prefixo `posicao:`); recuperada no boot por
  `loop_par`.

## `logger.py` — Logging analítico 🟢 Alta
- **Propósito:** tabelas `log_avaliacoes`, `log_trades`, `log_performance` (análise detalhada).
- ✅ **Multi-backend (validado em Postgres real):** segue a config de `database.py` (SQLite local / Postgres-Supabase em prod) — DDL/placeholders/ON CONFLICT/RETURNING por backend, `connect_timeout` + init resiliente. **Fim do split-brain.**
- ✅ Também: delega `.warning/.error/.critical` (fix do AttributeError no WS); whitelist em `exportar_csv`. Testes: `test_logger` (13) + `test_logger_postgres` (integração skippable).

## `health.py` — Health endpoint 🟢 Alta
- `/health` (sempre 200), `/ready` (testa `database.healthcheck()` + **watchdog do
  WS**: `degraded` se o `@aggTrade` fica >120s sem mensagem, via
  `registrar_ws_state`) e `/metrics` (Prometheus). Usados pelo serviço
  (NSSM/systemd) e pelo monitoramento.

## `telegram_bot.py` — Alertas 🟢 Alta
- Alertas de sinal/trade/circuit breaker/relatório diário. Token via env, sem vazamento. Read-only (não grava em DB).

## `dashboard.py` — UI Flask + SocketIO 🟢 Alta
- APIs REST (`/api/estado`, `/api/trades`, `/api/sinais`, `/api/score`, `/api/risco`...) + WebSocket. Lê do `database`. Roda como serviço **dashboard** separado (`BXBotDashboard`/`bxbot-dashboard`, porta 5000).
- ✅ **Endurecido**: bind `127.0.0.1` por padrão (`DASHBOARD_BIND`); `DASHBOARD_TOKEN`
  exigido em `/api/*` quando setado; **rate limit** por IP (`DASHBOARD_RATE_LIMIT`);
  CSP + X-Frame-Options=DENY + nosniff via `after_request`; helper `esc()`
  anti-XSS antes de `innerHTML`; libs (socket.io/chart.js) servidas de
  `static/vendor/` (sem CDN externo). Expor na rede só atrás de Caddy + token.

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

## Deploy / CI 🟢
- **Serviço 24/7 + Supabase (Postgres).** Alvos: **Windows NSSM** (worker :8080 +
  dashboard :5000) e **VPS systemd** (`deploy/` units + `setup.sh` + `Caddyfile`).
  Instalação nativa (`pip install -r requirements.txt`, Python 3.11 via
  `.python-version`), sem Docker/nixpacks. Ver [[Deploy VPS]].
- `.github/workflows/ci.yml` 🟢 — lint + smoke test de imports + pytest + security (sem build Docker).
- ✅ **Docker/Railway/GCP aposentados e removidos do repo** (Dockerfile,
  docker-compose*, `railway.toml`, `Procfile`, nixpacks, cloudbuild, terraform
  GCP, deploy.yml GCP). O histórico git preserva.

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| health / telegram / monitor_fluxo / config | 🟢 Alta |
| supabase migrations | 🟢 Alta |
| logger.py (multi-backend, validado em Postgres) | 🟢 Alta |
| dashboard.py (endurecido) | 🟢 Alta |
| database.py | 🟡 Média |
| migrador SQLite→Supabase | 🟡 Média |
