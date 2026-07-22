---
tags: [modulo, dados, infra, observabilidade]
atualizado: 2026-07-22
---

# 🗄️ Dados, Infra e Observabilidade

> Voltar: [[00 - Home]] · Operação: [[Deploy Supabase]] · [[Deploy VPS]]

---

## `database.py` — Fachada SQLite ↔ Supabase 🟡 Média
- **Propósito:** mesma API para SQLite (dev) e Postgres/Supabase (prod).
- **Seleção de backend:** usa Postgres se `DATABASE_URL` setado **e** `DATABASE_BACKEND ∈ {postgres, postgresql, supabase}`; senão SQLite (`data/btc_data.db`). Pool via `psycopg_pool`.
- **Tabelas (7):** `trades`, `snapshots_mercado`, `cvd_historico`, `sinais`
  (com colunas de meta-labeling — `preco_saida`/`pnl_usdt`/`pnl_pct`/
  `barreira_tocada`, P1-3), `risk_state`, `bot_events`, `model_metricas`
  (P1-4, guard-rail de drift).
- **Riscos:** timestamps divergem (SQLite `isoformat` TEXT vs Postgres `TIMESTAMPTZ`); `INSERT OR IGNORE` (SQLite) vs `ON CONFLICT` (PG) não 100% idênticos.
- ✅ Corrigido: `fechar_pool()` é chamado no shutdown gracioso (SIGTERM/SIGINT/SIGBREAK + finally em `main.py`) — não vaza mais conexões no restart do serviço (NSSM/systemd).
- ✅ **Crash recovery de posição**: `salvar_posicao_aberta` persiste a posição
  aberta (reusa `risk_state` com prefixo `posicao:`); recuperada no boot por
  `loop_par` (ou reconciliada contra o estado real da Binance — ver
  `reconciliar_boot()` em [[Core e Execucao]]).
- ⚠️ **Ação pendente do usuário**: `sinais_executados()`/meta-labeling
  precisam confirmar que a migration `002_meta_labeling_columns.sql` foi
  aplicada no Supabase de produção — ver `PLANO_MODERNIZACAO.md` (P2-4).

## `data/klines.py` — Cache TTL compartilhado 🟢 Alta (P1-5)
- **Propósito:** ponto único de fetch de klines para `regime.py`,
  `suporte.py`, `estrategias/otimizada.py`, `risco.py` — consolidou 6 cópias
  duplicadas do mesmo fetch em 4 módulos.
- **Força:** cache TTL thread-safe; elimina divergência de dados entre
  módulos que antes buscavam klines de forma independente.

## `logger.py` — Logging analítico 🟢 Alta
- **Propósito:** tabelas `log_avaliacoes`, `log_trades`, `log_performance` (análise detalhada).
- ✅ **Multi-backend (validado em Postgres real):** segue a config de `database.py` (SQLite local / Postgres-Supabase em prod) — DDL/placeholders/ON CONFLICT/RETURNING por backend, `connect_timeout` + init resiliente. **Fim do split-brain.**
- ✅ Também: delega `.warning/.error/.critical` (fix do AttributeError no WS); whitelist em `exportar_csv`. Testes: `test_logger` (13) + `test_logger_postgres` (integração skippable).
- ✅ **P2-5:** `dados_relatorio_diario()` extrai os dados brutos (sem imprimir)
  de `relatorio_diario()`, reaproveitado pelo alerta Telegram agendado
  (`main.iniciar_relatorio_diario`) sem duplicar a query.

## `health.py` — Health endpoint 🟢 Alta
- `/health` (sempre 200), `/ready` (testa `database.healthcheck()` + **watchdog do
  WS**: `degraded` se o `@aggTrade` fica >120s sem mensagem, via
  `registrar_ws_state`) e `/metrics` (Prometheus, texto nativo — sem lib
  `prometheus_client`, removida deliberadamente). Usados pelo serviço
  (NSSM/systemd) e pelo monitoramento.
- ✅ **P2-5 (2026-07-22):** os 6 contadores declarados (`ordens_total`,
  `ordens_erro`, `circuit_breaker_ativacoes`, `drawdown_bloqueios`,
  `ws_reconexoes`, `sinais_total`) agora são **de fato incrementados** em
  produção — antes só existiam declarados, sempre retornavam 0. Novos
  `set_gauge()`/`set_regime_atual()` (PnL do dia, drawdown, `ml_prob`,
  latência de decisão, regime como one-hot).

## `telegram_bot.py` — Alertas 🟢 Alta
- 8 tipos de alerta: sinal, trade aberto/fechado, stop, trailing stop,
  circuit breaker, relatório diário, persistência falhou (novo, P2-5). Token
  via env, sem vazamento. Read-only (não grava em DB).
- ✅ **P2-5 (2026-07-22):** antes só `alerta_sinal` era chamada em produção
  (as outras 6 existiam prontas, órfãs). Agora todas conectadas:
  `alerta_trade_aberto/fechado`/`alerta_stop`/`alerta_trailing_stop` em
  `executor.py`; `alerta_circuit_breaker` em `risco.py`; `relatorio_diario`
  agendado (18h) via `main.iniciar_relatorio_diario`.

## `dashboard.py` — UI Flask + SocketIO 🟢 Alta
- APIs REST (`/api/estado`, `/api/trades`, `/api/sinais`, `/api/score`, `/api/risco`...) + WebSocket. Lê do `database`. Roda como serviço **dashboard** separado (`BXBotDashboard`/`bxbot-dashboard`, porta 5000).
- ✅ **Endurecido**: bind `127.0.0.1` por padrão (`DASHBOARD_BIND`); `DASHBOARD_TOKEN`
  exigido em `/api/*` quando setado; **rate limit** por IP (`DASHBOARD_RATE_LIMIT`);
  CSP + X-Frame-Options=DENY + nosniff via `after_request`; helper `esc()`
  anti-XSS antes de `innerHTML`; libs (socket.io/chart.js) servidas de
  `static/vendor/` (sem CDN externo). Expor na rede só atrás de Caddy + token.
- ✅ **CORS endurecido em produção (2026-07-18)**: `CORS_ORIGINS='*'` (default
  ou herdado de `.env` antigo) em produção agora **nega cross-origin por
  padrão** (`CORS_SAME_ORIGIN_ONLY`, `config/runtime_settings.py`) —
  `origins=[]` no Flask-CORS, `cors_allowed_origins=None` no Socket.IO.
  `DASHBOARD_BIND != 127.0.0.1` sem `DASHBOARD_TOKEN` em produção agora
  **aborta o boot** (fail-fast, mesmo padrão do `--real` sem credenciais).
- ✅ `Flask-Cors` atualizado 5.0.1 → 6.0.0 (CVE-2024-6866, 2026-07-21).

## `monitor_fluxo.py` — CVD standalone 🟢 Alta
- WebSocket aggTrade + CVD acumulado + snapshot a cada 5 min. Thread-safe (GIL). Utilitário independente do `main`.

## `config/` 🟢 Alta
- `runtime_settings.py` — env > local `settings.py` (gitignored) > default.
  Sem segredos hardcoded. Novas flags opt-in (default `False`, mesma
  cautela de sempre — validar em paper antes de ativar em produção real):
  `OCO_BRACKET`/`OCO_TRAILING_DELTA_BIPS` (P2-1), `RECONCILIAR_BOOT_EXCHANGE`
  (auditoria 2026-07-22), `CORS_SAME_ORIGIN_ONLY` (derivada, não é env var).
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
- ⚠️ `requirements-backtest.txt` (novo, P2-2a) — dependência **opcional** de
  pesquisa (`vectorbt`), NUNCA instalar no ambiente de produção nem no
  Python global da máquina de dev: esta máquina não tem venv dedicado ao
  projeto (Python compartilhado com outras ferramentas) — `pip install
  vectorbt` sem pin explícito de numpy/scipy/scikit-learn já causou um
  incidente real (upgrade em cascata quebrando outras ferramentas,
  corrigido na mesma sessão). Sempre instalar num `.venv/` local ao projeto.

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| health (métricas/gauges conectados) / telegram (7 alertas conectados) / monitor_fluxo / config | 🟢 Alta |
| data/klines.py | 🟢 Alta |
| supabase migrations | 🟢 Alta |
| logger.py (multi-backend, validado em Postgres) | 🟢 Alta |
| dashboard.py (endurecido + CORS) | 🟢 Alta |
| database.py | 🟡 Média |
| migrador SQLite→Supabase | 🟡 Média |
