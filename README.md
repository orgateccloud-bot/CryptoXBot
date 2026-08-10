# CryptoXbot (BinanceXBot)

> **Status: estratégia REPROVADA no gate de go-live** (Etapa 1, walk-forward
> de 2 anos, 2026-07-24: retorno −21% vs +14% do buy-and-hold — reprovou em
> 4 de 5 critérios pré-registrados; o veredito não depende da taxa). Ver
> [`docs/GATE_GO_LIVE.md`](docs/GATE_GO_LIVE.md). Capital real **proibido**;
> próximo passo é redesenho estrutural da estratégia, não ajuste de parâmetros.

Bot de trading algorítmico **swing/intraday** (sinais em 1h/4h) para
**Binance Spot**, com filtro de sinal por ensemble de ML (XGBoost + MLP) e
gestão de risco (Kelly Criterion + Circuit Breaker). Dados de Futures usados
apenas como sentimento (funding/open interest). Paper trading por padrão.

[![CI](https://github.com/orgateccloud-bot/CryptoXBot/actions/workflows/ci.yml/badge.svg)](https://github.com/orgateccloud-bot/CryptoXBot/actions/workflows/ci.yml)

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core | Python 3.11+ |
| ML/IA | XGBoost (modelo principal) + sklearn MLP, em ensemble |
| Compute | systemd direto na VPS (ver `deploy/`) |
| Banco | **Supabase** (Postgres) em produção · SQLite local em dev |
| CI | GitHub Actions (`ci.yml`: lint + smoke test + pytest + segurança) |
| Monitoramento | `dashboard.py` (Flask + SocketIO), `/health`, `/metrics`, Telegram Bot |
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
tests/             # pytest (1537 passed, 8 skipped)
docs/              # vault Obsidian (relatórios, deploy, pontuações)
```

Raiz: `main.py` (orquestrador), `executor.py`, `risco.py`, `database.py`,
`logger.py`, `ensemble.py`/`ml_filtro.py`/`lstm_modelo.py`/
`score.py`/`regime.py`/`fear_greed.py`, `dashboard.py`, `health.py`,
`telegram_bot.py`, `monitor_fluxo.py`, `indicadores.py`, `suporte.py`,
`relatorio_gate.py` (métricas do gate de go-live).

## Início rápido (dev local)

```bash
git clone https://github.com/orgateccloud-bot/CryptoXBot
cd CryptoXBot
python -m venv .venv && .venv\Scripts\activate   # Windows (ou source .venv/bin/activate no Linux/Mac)
pip install -r requirements.txt

cp .env.example .env
python main.py --simulacao          # paper trading, SQLite local
```

Dashboard (em outro terminal):

```bash
python dashboard.py                 # http://localhost:5000
```

Testes:

```bash
pytest tests/ -v                    # 1537 passed, 8 skipped
```

## Deploy (VPS + Supabase)

```bash
cd /opt/binancexbot
bash deploy/setup.sh               # instala Python, venv, deps, registra serviços
nano .env                          # preencher credenciais
sudo systemctl start bxbot-worker bxbot-dashboard
journalctl -u bxbot-worker -f
```

Banco (Supabase): aplique `supabase/migrations/001_initial_schema.sql` no SQL
Editor e configure `DATABASE_BACKEND=postgres` + `DATABASE_URL=...` no `.env`.

HTTPS: `sudo cp deploy/Caddyfile /etc/caddy/Caddyfile` (edite o domínio antes)
+ `sudo systemctl reload caddy`.

Guia completo, variáveis de ambiente e modelos ML: ver [CLAUDE.md](CLAUDE.md).

## Segurança

- Paper trading (`DRY_RUN=true`, `ALLOW_REAL_TRADING=false`) por padrão
- Secrets nunca em código ou `.env` commitado — `.env` vive só no servidor
- Boot fail-fast valida credenciais antes de liberar ordens reais (`main.py`)

## Documentação

- [`CLAUDE.md`](CLAUDE.md) — referência completa (stack, comandos, variáveis, modelos ML)
- [`PLANO_MODERNIZACAO.md`](PLANO_MODERNIZACAO.md) — roadmap de modernização (P0-P3)
- [`docs/`](docs/) — vault Obsidian (arquitetura, operação, pontuações)
