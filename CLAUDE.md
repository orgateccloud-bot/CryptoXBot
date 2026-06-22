# CLAUDE.md — BinanceXBot (HFT Trading Bot)

Bot de trading algorítmico de alta frequência para Binance. **Deploy único:
Railway (compute, via nixpacks) + Supabase (Postgres gerenciado).** Os artefatos
de Docker/GCP do caminho antigo foram aposentados em `_legado/infra/`.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core | Python 3.11+ |
| ML/IA | XGBoost (modelo principal) + sklearn MLP + FSRS, em ensemble |
| Compute | **Railway** (nixpacks; `.python-version` fixa 3.11) |
| Banco | **Supabase** (Postgres) em produção · SQLite local em dev |
| CI | GitHub Actions (`ci.yml`: lint + smoke test + pytest + segurança) |
| Monitoramento | `dashboard.py` (Flask), `/health`, Telegram Bot, logging estruturado |
| Secrets | Variáveis de ambiente do Railway (por serviço) |

## Estrutura

```
ai/                # Cliente Ollama (análise qualitativa opcional)
backtesting/       # Backtesting de estratégias
config/            # runtime_settings (env > local > default) + params por par
data/              # cvd_calculator (fonte) — artefatos (*.db/*.pkl) gitignored
estrategias/       # Estratégias de trading (otimizada, ema_rsi_cvd)
scripts/           # migrate_sqlite_to_supabase.py
supabase/          # migrations/ (schema Postgres)
templates/         # dashboard
tests/             # pytest (599 passed)
docs/              # vault Obsidian (relatórios, deploy, pontuações)
_legado/           # código/infra aposentados (ver _legado/LEIA-ME.md)
```

Raiz: `main.py` (orquestrador), `executor.py`, `risco.py`, `database.py`,
`logger.py`, `ensemble.py`/`ml_filtro.py`/`lstm_modelo.py`/`fsrs_trading.py`/
`score.py`/`regime.py`/`fear_greed.py`, `dashboard.py`, `health.py`,
`telegram_bot.py`, `monitor_fluxo.py`, `indicadores.py`, `suporte.py`.

## Deploy (Railway + Supabase)

- **Supabase:** aplicar `supabase/migrations/001_initial_schema.sql`; usar a
  connection string em `DATABASE_URL` + `DATABASE_BACKEND=postgres`.
- **Railway:** 2 serviços do mesmo repo — `worker` (`python main.py ...`, via
  `railway.toml`) e `web`/dashboard (`python dashboard.py`). Railway observa o
  repo e faz deploy no push (sem Docker/Actions de deploy).
- Passo a passo completo em `docs/Operacao/` (Deploy Supabase, Deploy Railway,
  Variáveis de Ambiente).

## Comandos

```bash
# Desenvolvimento local (SQLite por padrão — sem Docker)
cp .env.example .env
python main.py --simulacao          # paper trading

# Apontar dev para Supabase (opcional)
# DATABASE_BACKEND=postgres DATABASE_URL=postgresql://... python main.py --simulacao

# Testes
pytest tests/ -v                    # 599 passed, 7 skipped
# Testar o backend Postgres do logger contra um PG real:
# BXBOT_TEST_PG_URL=postgresql://... pytest tests/test_logger_postgres.py -v

# Migrar dados locais SQLite -> Supabase (idempotente)
python scripts/migrate_sqlite_to_supabase.py --dry-run
python scripts/migrate_sqlite_to_supabase.py --confirmar

# Monitoramento
python dashboard.py
python monitor_fluxo.py
```

## Variáveis de Ambiente Críticas

```
# Configurar no painel do Railway (por serviço) — NUNCA commitar
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://postgres.<ref>:<senha>@...pooler.supabase.com:6543/postgres
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SECRET_KEY=...        # obrigatório em produção (senão é gerado efêmero)
# Segurança de trading: DRY_RUN=true e ALLOW_REAL_TRADING=false por padrão
```

## Modelos ML

- `ml_filtro.py` — **XGBoost** (modelo principal de classificação de sinal; é o que o ensemble usa)
- `lstm_modelo.py` — rede **MLP do sklearn** (nome "LSTM" é histórico; não é LSTM real)
- `ensemble.py` — Ensemble ponderado (XGBoost + MLP) com ajuste por regime e FSRS
- `fsrs_trading.py` — Filtro adaptativo (padrões com bom histórico)
- `score.py` — Score unificado 0-100 (10 componentes ponderados)
- `lgbm_modelo.py` (LightGBM) foi aposentado em `_legado/` (era órfão).

## Segurança

- Secrets NUNCA em código ou `.env` commitado — usar variáveis do Railway
- `.env*` no `.gitignore`; `SECRET_KEY` endurecido em produção
- `.secrets.baseline` + `.bandit` no pre-commit
- Paper trading (`DRY_RUN=true`) antes de qualquer mudança em produção

## Convenções

- Python 3.11+, type hints obrigatórios
- Variáveis e logs em português
- NUNCA fazer push --force no branch main (Railway faz deploy no push)
- Testes obrigatórios: `pytest tests/ -v` antes de qualquer PR
- Aposentar (não deletar): mover para `_legado/` com plano de rollback (@Zeta)
