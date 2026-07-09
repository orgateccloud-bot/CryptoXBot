---
tags: [operacao, config, env]
---

# 🔑 Variáveis de Ambiente

> Voltar: [[00 - Home]] · Relacionado: [[Deploy VPS]] · [[Deploy Supabase]]

Referência completa (de `config/runtime_settings.py`). Template em `.env.example`.
**Nunca** commitar valores reais — preencha o `.env` no host do serviço
(PC/VPS); `.env*` está no `.gitignore`.

## Obrigatórias (produção)
| Variável | Exemplo | Notas |
|---|---|---|
| `BINANCE_API_KEY` | `abc...` | chave Binance |
| `BINANCE_API_SECRET` | `xyz...` | secret Binance |
| `REST_BASE_URL` | `https://api.binance.com` | endpoint SPOT (execução) |
| `WS_BASE_URL` | `wss://stream.binance.com:9443` | WebSocket SPOT (`@aggTrade`) |
| `DATABASE_URL` | `postgresql://postgres.<ref>:<senha>@aws-0-<region>.pooler.supabase.com:6543/postgres` | connection string Supabase |
| `DATABASE_BACKEND` | `postgres` | ativa o backend Postgres |
| `SECRET_KEY` | `...` | obrigatório em produção (efêmero se ausente/placeholder) |

## Segurança de trading
| Variável | Default | Notas |
|---|---|---|
| `DRY_RUN` | `true` | `true` = paper trading mesmo com chaves reais |
| `ALLOW_REAL_TRADING` | `false` | precisa ser `true` p/ ordens reais (opt-in) |

## Recomendadas
| Variável | Default | Notas |
|---|---|---|
| `SYMBOL` | `BTCUSDT` | par principal |
| `LOG_LEVEL` | `INFO` | verbosidade |
| `ENV` / `APP_ENV` | `development` | use `production` em produção (gate de trading real) |
| `CORS_ORIGINS` | `*` | restringir no dashboard público |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | pool Postgres |
| `MIN_BTC_VOLUME` / `WHALE_BTC_VOLUME` | `0.5` / `5.0` | filtros de trade no WS |

## Dashboard
| Variável | Default | Notas |
|---|---|---|
| `DASHBOARD_BIND` | `127.0.0.1` | `0.0.0.0` só atrás de Caddy + token |
| `DASHBOARD_TOKEN` | *(vazio)* | se setado, exige Bearer/`?token=` em `/api/*` |
| `DASHBOARD_RATE_LIMIT` | `120` | req/60s por IP em `/api/*` |

## Opcionais (alertas Telegram)
| Variável | Notas |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token do bot |
| `TELEGRAM_CHAT_ID` | chat de destino |

## Injetadas pelo serviço (não definir manualmente)
Os units systemd (`deploy/*.service`) e o registro NSSM já injetam:
| Variável | Notas |
|---|---|
| `SERVICE_ROLE` | `worker` ou `dashboard` |
| `PORT` | `8080` (worker/health) · `5000` (dashboard) |
| `ENABLE_HEALTH_SERVER` | `true` no worker (sobe o `/health`) |

## Só desenvolvimento local
- `DATABASE_BACKEND=sqlite` (default) + `DB_PATH=data/btc_data.db` → usa SQLite, sem `DATABASE_URL`.
- Ao rodar o worker direto (sem serviço), passe `PORT=8080` — senão o health server colide com o dashboard na 5000.
