# CLAUDE.md — BinanceXBot / CryptoXbot (HFT Trading Bot)

Bot de trading algorítmico de alta frequência para **Binance Spot**.
Execução via `/api/v3/order` (spot); indicadores de **funding rate / open
interest** são lidos de Futures (`fapi.binance.com`) apenas como sentimento.
**Deploy: serviço 24/7 (Windows NSSM no PC · systemd na VPS) + Supabase.**
Railway e Docker foram aposentados e removidos do repo (recuperáveis via
histórico git) — zero acoplamento no código.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core | Python 3.11+ |
| Mercado | **Binance Spot** (execução) · Futures só p/ funding/OI (sentimento) |
| ML/IA | XGBoost (modelo principal) + sklearn MLP + FSRS, em ensemble |
| Compute | **NSSM** (Windows, serviço 24/7 no PC) · ou **systemd** na VPS (`deploy/`) |
| Banco | **Supabase** (Postgres) em produção · SQLite (WAL) local em dev |
| CI | GitHub Actions (`ci.yml`: lint + smoke test + pytest + segurança) |
| Monitoramento | `dashboard.py` (Flask + SocketIO), `/health`, `/ready`, `/metrics`, Telegram |
| Secrets | `.env` no servidor (nunca commitado) · `.env.example` documenta as vars |
| HTTPS | **Caddy** (`deploy/Caddyfile`) — TLS automático via Let's Encrypt |

## Estrutura

```
ai/                # Cliente Ollama (análise qualitativa opcional)
backtesting/       # Backtesting de estratégias
config/            # runtime_settings (env > local > default) + params por par
data/              # cvd_calculator (fonte) — artefatos (*.db/*.pkl) gitignored
deploy/            # systemd units, Caddyfile, setup.sh
estrategias/       # Estratégias de trading (otimizada)
scripts/           # migrate_sqlite_to_supabase.py
static/vendor/     # socket.io + chart.js vendorizados (sem CDN externo)
supabase/          # migrations/ (schema Postgres)
templates/         # dashboard.html (SPA — tema claro/escuro)
tests/             # pytest (721 passed, 7 skipped)
docs/              # vault Obsidian (relatórios, deploy, pontuações)
```

Raiz: `main.py` (orquestrador), `executor.py`, `risco.py`, `database.py`,
`logger.py`, `ensemble.py`/`ml_filtro.py`/`lstm_modelo.py`/`fsrs_trading.py`/
`score.py`/`regime.py`/`fear_greed.py`, `dashboard.py`, `health.py`,
`telegram_bot.py`, `monitor_fluxo.py`, `indicadores.py`, `suporte.py`.

## Deploy

### Windows (PC 24/7) via NSSM — deploy atual

Dois serviços do Windows (rodam como LocalSystem, auto-restart em crash,
iniciam no boot). Registrados via `nssm install` em **PowerShell Administrador**:

- `BXBotWorker` → `python main.py --simulacao --intervalo 15` (env `PORT=8080`, `ENABLE_HEALTH_SERVER=true`)
- `BXBotDashboard` → `python dashboard.py` (porta 5000)

```powershell
# Controle (SEMPRE em janela "Administrador:" — LocalSystem exige elevação;
# sem elevação, nssm/Stop-Process falham SILENCIOSAMENTE com access-denied)
Get-Service BXBot*
nssm restart BXBotWorker
# Se o worker não morrer no stop: Stop-Process -Id <pid da :8080> -Force
# (NSSM AppExit=Restart o ressuscita com o código atual)
```

Logs: `logs/worker-*.log`, `logs/dashboard-*.log`.

### VPS Ubuntu via systemd

```bash
cd /opt/binancexbot
bash deploy/setup.sh               # instala Python, venv, deps, registra serviços
nano .env                          # preencher credenciais
sudo systemctl start bxbot-worker bxbot-dashboard
journalctl -u bxbot-worker -f
```

### Banco (Supabase)

```bash
# Aplicar schema uma única vez: Supabase → SQL Editor → supabase/migrations/001_initial_schema.sql
# Depois no .env: DATABASE_BACKEND=postgres + DATABASE_URL=postgresql://...pooler.supabase.com:6543/postgres
```

### HTTPS para o dashboard (Caddy)

```bash
sudo apt install caddy
# Editar deploy/Caddyfile: substituir SEU-DOMINIO.COM
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

## Comandos

```bash
# Desenvolvimento local (SQLite por padrão, carrega .env via python-dotenv)
cp .env.example .env
python main.py --simulacao          # paper trading
python main.py --real --intervalo 15  # ordens reais (exige gates abaixo)

# IMPORTANTE ao rodar direto (sem serviço): passe PORT=8080 ao worker, senão o
# health server dele colide com o dashboard na 5000.

# Testes
pytest tests/ -v                    # 721 passed, 7 skipped
# BXBOT_TEST_PG_URL=postgresql://... pytest tests/test_logger_postgres.py -v

# Migração de dados SQLite -> Supabase (idempotente)
python scripts/migrate_sqlite_to_supabase.py --dry-run   # depois --confirmar

# Monitoramento
python dashboard.py
python monitor_fluxo.py
```

## Variáveis de Ambiente Críticas

```bash
# Preencher em .env no servidor — NUNCA commitar
BINANCE_API_KEY=...
BINANCE_API_SECRET=...

# Endpoints (padrão SPOT — mercado unificado sinal+execução)
REST_BASE_URL=https://api.binance.com
WS_BASE_URL=wss://stream.binance.com:9443

DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://postgres.<ref>:<senha>@...pooler.supabase.com:6543/postgres
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SECRET_KEY=...          # obrigatório em produção (gera efêmero se ausente/placeholder)
ENV=production
CORS_ORIGINS=https://seu-dominio.com

# Worker (injetado pelo serviço — não setar manualmente)
SERVICE_ROLE=worker
PORT=8080
ENABLE_HEALTH_SERVER=true

# Dashboard
SERVICE_ROLE=dashboard
PORT=5000
DASHBOARD_BIND=127.0.0.1     # padrão local; 0.0.0.0 só atrás de Caddy + token
DASHBOARD_TOKEN=             # se setado, exige Bearer/?token= em /api/*
DASHBOARD_RATE_LIMIT=120     # req/60s por IP em /api/*

# Segurança: padrão paper trading
DRY_RUN=true
ALLOW_REAL_TRADING=false
```

## Trading real (Rota B) — gates

Ordens reais exigem **os três** no `.env`: `DRY_RUN=false` +
`ALLOW_REAL_TRADING=true` + `ENV=production`. Além disso, a flag `--real` no
comando (o serviço tem `--simulacao` fixo como trava extra; trocar por `--real`
+ restart). `main.py` faz fail-fast no boot se faltar API_KEY/SECRET ou ENV.

Proteções de execução (`executor.py`, todas só em modo real):
- **Stop loss NA EXCHANGE**: `STOP_LOSS_LIMIT` colocado na Binance após o fill
  (sobrevive a crash do bot); trailing/breakeven via cancel-then-replace com
  restauração do nível antigo se o novo falhar.
- **Crash recovery**: posição persistida no DB (`database.salvar_posicao_aberta`,
  reusa `risk_state` com prefixo `posicao:`); no boot, `loop_par` recupera e
  religa o monitor. Sem posição órfã pós-restart.
- **API robusta**: `recvWindow=5000` + sync de relógio (offset serverTime,
  corrige -1021) + retry/backoff (429/-1003/5xx) + `newClientOrderId` idempotente
  + consulta pós-timeout (elimina ordem fantasma).
- Gestão de risco (`risco.py`): Kelly fracionado (25%), circuit breaker 5%/dia e
  15% total, 1 posição por vez.

## Modelos ML

- `ml_filtro.py` — **XGBoost** (modelo principal). Pickle salvo atomicamente
  (`tmp` + `os.replace` — crash no retreino não corrompe o modelo).
- `lstm_modelo.py` — **MLP do sklearn** (nome "LSTM" é histórico; não é LSTM real)
- `ensemble.py` — Ensemble ponderado XGBoost 55% + MLP 45%, ajuste por regime + FSRS
- `fsrs_trading.py` — Filtro adaptativo (padrões com bom histórico)
- `score.py` — Score unificado 0-100 (10 componentes; ≥60 opera reduzido, ≥70 cheio)
- Retreino automático domingo 02h. `lgbm_modelo.py` (LightGBM) aposentado (órfão).

## CVD / WebSocket

- Fonte: WS `@aggTrade` do mercado spot. O parser usa `data["a"]` (aggregate
  trade id) — **NÃO `data["t"]`** (bug histórico que zerava o CVD; ver
  `tests/test_process_message.py`).
- Watchdog: `/ready` (worker) acusa `degraded` se o WS não recebe mensagem há
  >120s (`health.registrar_ws_state`) — pega conexão zumbi/CVD congelado.
- Shutdown gracioso: `main.py` trata SIGTERM/SIGINT/SIGBREAK. No Linux/systemd
  o path gracioso roda completo; no Windows/NSSM o processo termina prontamente
  (CVD re-acumula do stream no restart).

## Segurança

- Secrets NUNCA em código ou `.env` commitado — `.env*` no `.gitignore`.
  `SECRET_KEY` endurecido em produção; `.secrets.baseline` + `.bandit` no pre-commit.
- Paper trading (`DRY_RUN=true`) é o padrão.
- **Dashboard**: bind `127.0.0.1` por padrão; `DASHBOARD_TOKEN` exigido em
  `/api/*` quando setado; rate limit por IP. CSP + X-Frame-Options=DENY +
  nosniff via `after_request`. XSS: helper `esc()` escapa campos-string da API
  antes de `innerHTML`. Libs (socket.io/chart.js) servidas de `static/vendor/`
  (sem CDN externo). Expor na rede só atrás de Caddy (HTTPS) + token.
- SQLite em WAL + `busy_timeout` (robustez a crash e concorrência de threads).

## Convenções

- Python 3.11+, type hints obrigatórios
- Variáveis e logs em português
- Testes obrigatórios: `pytest tests/ -v` antes de qualquer PR
- Aposentar código: preferir mover para pasta de arquivo com plano de rollback;
  remoção definitiva só quando o histórico git for rollback suficiente (@Zeta)
```
