---
tags: [operacao, config, env]
atualizado: 2026-07-22
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
| `CORS_ORIGINS` | `*` | **desde 2026-07-18**: em produção, `*` (default ou herdado de `.env` antigo) **sempre nega cross-origin** por padrão (`CORS_SAME_ORIGIN_ONLY`, derivada — não é env var própria); acesso direto ao dashboard continua funcionando normalmente. Defina um domínio real (não wildcard) só se precisar de acesso cross-origin de fato |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | pool Postgres |
| `MIN_BTC_VOLUME` / `WHALE_BTC_VOLUME` | `0.5` / `5.0` | filtros de trade no WS |

## Execução (P0-2 / P2-1) — opt-in, validar em paper antes de ativar
| Variável | Default | Notas |
|---|---|---|
| `MAKER_FIRST` | `true` | entrada `LIMIT_MAKER` no melhor bid (post-only); `false` volta ao LIMIT cruzando (taker) |
| `MAKER_TIMEOUT_S` | `20` | segundos aguardando fill antes de re-quotar |
| `MAKER_MAX_REQUOTES` | `3` | re-quotes antes de desistir da entrada |
| `OCO_BRACKET` | `false` | bracket OCO nativo (stop+alvo atômico na exchange) em vez do `STOP_LOSS_LIMIT` puro |
| `OCO_TRAILING_DELTA_BIPS` | `0` | >0 delega o trailing ao servidor (bips: `80`=0.8%); suprime o cancel-then-replace local |
| `RECONCILIAR_BOOT_EXCHANGE` | `false` | cruza o DB com o estado real da Binance (saldo/ordens/`myTrades`) antes de religar o monitor no boot — detecta posição órfã ou já fechada fora do bot |

## Dashboard
| Variável | Default | Notas |
|---|---|---|
| `DASHBOARD_BIND` | `127.0.0.1` | `0.0.0.0` só atrás de Caddy + token. **Desde 2026-07-18**: em produção, exposto (`!= 127.0.0.1`) sem `DASHBOARD_TOKEN` **aborta o boot** (fail-fast) |
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
