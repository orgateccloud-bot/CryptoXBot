---
tags: [operacao, deploy, railway]
---

# 🚂 Deploy — Railway (worker + dashboard)

> Voltar: [[00 - Home]] · Relacionado: [[Deploy Supabase]] · [[Variaveis de Ambiente]]

O bot roda como **dois serviços** no mesmo projeto Railway, apontando para o mesmo
repositório e o mesmo banco [[Deploy Supabase|Supabase]].

| Serviço | Comando | Papel | Health |
|---|---|---|---|
| **worker** | `python main.py --simulacao --intervalo 15` | bot de trading + WebSocket CVD | `/health` (health.py, na `PORT` injetada) |
| **web** (dashboard) | `python dashboard.py` | painel Flask + APIs REST | `/health` (Flask) |

> `railway.toml` define o **worker** (nixpacks, restart on_failure, healthcheck `/health`).
> `Procfile` declara os dois process types (`worker`, `web`). Para 2 serviços, crie
> dois serviços no Railway a partir do mesmo repo e sobrescreva o **Start Command**
> de cada um (worker usa o de `railway.toml`; web usa `python dashboard.py`).

## Passo a passo
1. **Provisionar Supabase** primeiro → ver [[Deploy Supabase]] (precisa da `DATABASE_URL`).
2. Railway → **New Project → Deploy from GitHub repo** → selecione `CryptoXBot`.
3. **Serviço worker:** mantém o start command do `railway.toml`.
4. **Serviço web:** **New Service → mesmo repo**; Start Command = `python dashboard.py`; gere um domínio público (Settings → Networking).
5. Em **cada** serviço, configure as variáveis → ver [[Variaveis de Ambiente]].
   - Mínimo: `DATABASE_URL`, `DATABASE_BACKEND=postgres`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`.
   - `SERVICE_ROLE` é inferido de `RAILWAY_SERVICE_NAME` (ou defina `worker`/`dashboard`).
6. Deploy. Railway injeta `PORT` automaticamente (health server do worker ativa sozinho).

## Segurança de trading (importante)
- Padrão **seguro**: o worker sobe com `--simulacao` (paper). Mesmo com chaves reais,
  `DRY_RUN=true` mantém paper trading.
- Para operar **real**, é preciso, deliberadamente: remover `--simulacao` (ou passar
  `--real`), `DRY_RUN=false` **e** `ALLOW_REAL_TRADING=true`. Faça isso só após
  paper trading extenso (ver [[Planejamento de Melhorias]]).

## Build
Railway usa **nixpacks** (sem Docker). O Python é fixado por `.python-version`
(3.11) e as deps vêm de `requirements.txt`. Não há `Dockerfile` no fluxo ativo.

## ✅ Docker/GCP aposentados
Todo o caminho antigo (Dockerfile, `docker-compose*`, `cloudbuild.yaml`,
`terraform/` GCP, `deploy/` Oracle/systemd, `deploy.yml` GCP, scripts de deploy)
foi movido para `_legado/infra/` (ver `_legado/LEIA-ME.md`). O `ci.yml` não faz
mais build de imagem Docker. Config de deploy ativa = `railway.toml` + `Procfile`
+ `.python-version` + `supabase/migrations/`.
