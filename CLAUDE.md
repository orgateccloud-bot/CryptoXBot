# CLAUDE.md — BinanceXBot (HFT Trading Bot)

Bot de trading algorítmico de alta frequência para Binance Futures.
**Deploy: VPS Ubuntu (systemd) + Supabase (Postgres gerenciado).**
Railway foi aposentado em `_legado/infra/railway.toml` — zero acoplamento no código.
Docker foi aposentado em `_legado/infra/lote3-docker/` — zero acoplamento no código.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core | Python 3.11+ |
| ML/IA | XGBoost (modelo principal) + sklearn MLP + FSRS, em ensemble |
| Compute | **systemd** direto na VPS (ver `deploy/`) |
| Banco | **Supabase** (Postgres) em produção · SQLite local em dev |
| CI | GitHub Actions (`ci.yml`: lint + smoke test + pytest + segurança) |
| Monitoramento | `dashboard.py` (Flask + SocketIO), `/health`, `/metrics`, Telegram Bot |
| Secrets | `.env` no servidor (nunca commitado) · `.env.example` documenta todas as vars |
| HTTPS | **Caddy** (`deploy/Caddyfile`) — TLS automático via Let's Encrypt |

## Estrutura

```
ai/                # Cliente Ollama (análise qualitativa opcional)
backtesting/       # Backtesting de estratégias
config/            # runtime_settings (env > local > default) + params por par
data/              # cvd_calculator (fonte) — artefatos (*.db/*.pkl) gitignored
deploy/            # Artefatos de deploy: systemd units, Caddyfile, setup.sh
estrategias/       # Estratégias de trading (otimizada)
scripts/           # migrate_sqlite_to_supabase.py
supabase/          # migrations/ (schema Postgres)
templates/         # dashboard HTML
tests/             # pytest (710 passed, 7 skipped)
docs/              # vault Obsidian (relatórios, deploy, pontuações)
_legado/           # código/infra aposentados (ver _legado/LEIA-ME.md)
```

Raiz: `main.py` (orquestrador), `executor.py`, `risco.py`, `database.py`,
`logger.py`, `ensemble.py`/`ml_filtro.py`/`lstm_modelo.py`/`fsrs_trading.py`/
`score.py`/`regime.py`/`fear_greed.py`, `dashboard.py`, `health.py`,
`telegram_bot.py`, `monitor_fluxo.py`, `indicadores.py`, `suporte.py`.

## Deploy (VPS + Supabase)

### systemd direto

```bash
cd /opt/binancexbot
bash deploy/setup.sh               # instala Python, venv, deps, registra serviços
nano .env                          # preencher credenciais
sudo systemctl start bxbot-worker bxbot-dashboard
journalctl -u bxbot-worker -f
```

### Banco (Supabase)

```bash
# Aplicar schema uma única vez:
# Supabase → SQL Editor → colar supabase/migrations/001_initial_schema.sql
# Depois setar no .env:
# DATABASE_BACKEND=postgres
# DATABASE_URL=postgresql://postgres.[ref]:[password]@...pooler.supabase.com:6543/postgres
```

### HTTPS para o dashboard (Caddy)

```bash
sudo apt install caddy
# Editar deploy/Caddyfile: substituir SEU-DOMINIO.COM
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy        # TLS automático via Let's Encrypt
```

## Comandos

```bash
# Desenvolvimento local (SQLite por padrão)
cp .env.example .env
python main.py --simulacao          # paper trading

# Apontar dev para Supabase (opcional)
# DATABASE_BACKEND=postgres DATABASE_URL=postgresql://... python main.py --simulacao

# Testes
pytest tests/ -v                    # 710 passed, 7 skipped
# Testar backend Postgres do logger:
# BXBOT_TEST_PG_URL=postgresql://... pytest tests/test_logger_postgres.py -v

# Migrar dados locais SQLite -> Supabase (idempotente)
python scripts/migrate_sqlite_to_supabase.py --dry-run
python scripts/migrate_sqlite_to_supabase.py --confirmar

# Monitoramento
python dashboard.py
python monitor_fluxo.py
```

## Variáveis de Ambiente Críticas

```bash
# Preencher em .env no servidor — NUNCA commitar
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://postgres.<ref>:<senha>@...pooler.supabase.com:6543/postgres
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SECRET_KEY=...          # obrigatório em produção (gerado efêmero se ausente)
ENV=production
CORS_ORIGINS=https://seu-dominio.com

# Worker (injetado pelo systemd — não setar manualmente)
SERVICE_ROLE=worker
PORT=8080
ENABLE_HEALTH_SERVER=true

# Dashboard
SERVICE_ROLE=dashboard
PORT=5000

# Segurança: padrão paper trading
DRY_RUN=true
ALLOW_REAL_TRADING=false
```

## Modelos ML

- `ml_filtro.py` — **XGBoost** (modelo principal de classificação de sinal)
- `lstm_modelo.py` — rede **MLP do sklearn** (nome "LSTM" é histórico; não é LSTM real)
- `ensemble.py` — Ensemble ponderado (XGBoost + MLP) com ajuste por regime e FSRS
- `fsrs_trading.py` — Filtro adaptativo (padrões com bom histórico)
- `score.py` — Score unificado 0-100 (10 componentes ponderados)
- `lgbm_modelo.py` (LightGBM) foi aposentado em `_legado/` (era órfão).

## Segurança

- Secrets NUNCA em código ou `.env` commitado — `.env` vive só no servidor
- `.env*` no `.gitignore`; `SECRET_KEY` endurecido em produção
- `.secrets.baseline` + `.bandit` no pre-commit
- Paper trading (`DRY_RUN=true`) antes de qualquer mudança em produção

## Convenções

- Python 3.11+, type hints obrigatórios
- Variáveis e logs em português
- Testes obrigatórios: `pytest tests/ -v` antes de qualquer PR
- Aposentar (não deletar): mover para `_legado/` com plano de rollback (@Zeta)
