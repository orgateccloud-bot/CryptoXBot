---
tags: [operacao, config, env]
---

# 🔑 Variáveis de Ambiente

> Voltar: [[00 - Home]] · Relacionado: [[Deploy Railway]] · [[Deploy Supabase]]

Referência completa (de `config/runtime_settings.py`). Template em `.env.example`.
**Nunca** commitar valores reais — no Railway, use o painel de variáveis por serviço.

## Obrigatórias (produção)
| Variável | Exemplo | Notas |
|---|---|---|
| `BINANCE_API_KEY` | `abc...` | chave Binance |
| `BINANCE_API_SECRET` | `xyz...` | secret Binance |
| `DATABASE_URL` | `postgresql://postgres.<ref>:<senha>@aws-0-<region>.pooler.supabase.com:6543/postgres` | connection string Supabase |
| `DATABASE_BACKEND` | `postgres` | ativa o backend Postgres |

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
| `ENV` / `APP_ENV` | `development` | use `production` no Railway |
| `SECRET_KEY` | `botbinance-local-dev...` | **trocar** em produção |
| `CORS_ORIGINS` | `*` | restringir no dashboard público |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | pool Postgres |
| `MIN_BTC_VOLUME` / `WHALE_BTC_VOLUME` | `0.5` / `5.0` | filtros de trade no WS |

## Opcionais (alertas Telegram)
| Variável | Notas |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token do bot |
| `TELEGRAM_CHAT_ID` | chat de destino |

## Injetadas pelo Railway (não definir manualmente)
| Variável | Notas |
|---|---|
| `PORT` | porta do health server / dashboard |
| `RAILWAY_SERVICE_NAME` | usado para inferir `SERVICE_ROLE` (`worker`/`dashboard`) |

## Só desenvolvimento local
- `DATABASE_BACKEND=sqlite` (default) + `DB_PATH=data/btc_data.db` → usa SQLite, sem `DATABASE_URL`.
