# CLAUDE.md — BinanceXBot (HFT Trading Bot)

Bot de trading algorítmico de alta frequência para Binance, com deploy automatizado
na Google Cloud Platform via GitHub Actions + Terraform.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core | Python 3.11+ |
| ML/IA | LightGBM, LSTM, ensemble models |
| Infraestrutura | Docker, GCP (Compute Engine + Artifact Registry) |
| IaC | Terraform |
| CI/CD | GitHub Actions |
| Monitoramento | Cloud Logging, dashboard.py, Telegram Bot |
| Secrets | GCP Secret Manager |

## Estrutura

```
ai/                # Modelos de ML (LightGBM, LSTM)
backtesting/       # Backtesting de estratégias
config/            # Configurações
core/              # Core do bot (ordens, posições, risco)
estrategias/       # Estratégias de trading
execution/         # Execução de ordens na Binance
infra/             # Infraestrutura (logs, DB)
monitoring/        # Monitoramento e alertas
scripts/           # Utilitários de deploy e manutenção
terraform/         # IaC para GCP
tests/             # pytest
```

## GCP Setup

- Projeto: `rich-streamer-v4p9v`
- Service Account: `github-actions-deployer@rich-streamer-v4p9v.iam.gserviceaccount.com`
- Região: definida no Terraform
- Secrets: `binance-api-key`, `binance-api-secret` no GCP Secret Manager

## Comandos

```bash
# Desenvolvimento local
cp .env.example .env
docker-compose up -d
python main.py

# Produção
docker-compose -f docker-compose.prod.yml up -d

# Testes
pytest tests/ -v
python test_paper_trading.py      # paper trading sem dinheiro real

# Deploy GCP (via GitHub Actions push para main)
# Manual: ./deploy.sh ou ./deploy-prod.sh

# Monitoramento
python dashboard.py
python monitor_fluxo.py
```

## Variáveis de Ambiente Críticas

```
# .env.prod — NUNCA commitar
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATABASE_URL=postgresql://...
```

## Modelos ML

- `lgbm_modelo.py` — LightGBM para classificação de sinal
- `lstm_modelo.py` — LSTM para previsão de série temporal
- `ensemble.py` — Ensemble dos modelos
- `ml_filtro.py` — Filtro de qualidade de sinal
- `score.py` — Score de confiança da operação

## Segurança

- Secrets NUNCA em código ou `.env` commitado
- Usar GCP Secret Manager em produção
- `.env.prod` no `.gitignore`
- Paper trading antes de qualquer mudança em produção

## Convenções

- Python 3.11+, type hints obrigatórios
- Variáveis e logs em português
- NUNCA fazer push --force no branch main (CI/CD dispara deploy)
- Testes obrigatórios: `pytest tests/ -v` antes de qualquer PR
- Checar `chaos_test_report.json` após testes de resiliência
