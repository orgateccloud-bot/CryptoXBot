---
tags: [operacao, deploy, nssm, systemd, vps]
atualizado: 2026-07-22
---

# 🖥️ Deploy — Serviço 24/7 (Windows NSSM · VPS systemd)

> Voltar: [[00 - Home]] · Relacionado: [[Deploy Supabase]] · [[Variaveis de Ambiente]]

O bot roda como **dois serviços permanentes** (worker + dashboard) apontando para
o mesmo repositório e o mesmo banco [[Deploy Supabase|Supabase]]. Há dois alvos de
deploy suportados, ambos com auto-restart em crash e início no boot:

- **Windows (PC 24/7) via NSSM** — deploy atual do dev.
- **VPS Ubuntu via systemd** — `deploy/` traz os units + `setup.sh`.

| Serviço | Comando | Papel | Porta |
|---|---|---|---|
| **worker** (`BXBotWorker` / `bxbot-worker`) | `python main.py --simulacao --intervalo 15` | bot de trading + WebSocket CVD (spot) | `8080` (`/health`) |
| **dashboard** (`BXBotDashboard` / `bxbot-dashboard`) | `python dashboard.py` | painel Flask + APIs REST | `5000` |

---

## A) Windows (PC 24/7) via NSSM — deploy atual

Dois serviços do Windows registrados via **NSSM** (rodam como LocalSystem,
auto-restart em crash, iniciam no boot). Registrar em **PowerShell Administrador**:

- `BXBotWorker` → `python main.py --simulacao --intervalo 15`
  (env `PORT=8080`, `ENABLE_HEALTH_SERVER=true`, `SERVICE_ROLE=worker`)
- `BXBotDashboard` → `python dashboard.py` (porta 5000, `SERVICE_ROLE=dashboard`)

```powershell
# Controle — SEMPRE em janela "Administrador:" (LocalSystem exige elevação;
# sem elevação, nssm/Stop-Process falham SILENCIOSAMENTE com access-denied).
Get-Service BXBot*
nssm restart BXBotWorker
# Se o worker não morrer no stop: Stop-Process -Id <pid da :8080> -Force
# (NSSM AppExit=Restart o ressuscita com o código atual).
```

Logs: `logs/worker-*.log`, `logs/dashboard-*.log`.

## B) VPS Ubuntu via systemd

Os units estão versionados em `deploy/`
(`bxbot-worker.service`, `bxbot-dashboard.service`) e o `deploy/setup.sh`
instala Python 3.11, venv, dependências, Caddy e registra os serviços.

```bash
git clone <repo> /opt/binancexbot
cd /opt/binancexbot
bash deploy/setup.sh               # instala deps + registra serviços systemd + Caddy
nano .env                          # preencher credenciais (ver [[Variaveis de Ambiente]])
sudo systemctl start bxbot-worker bxbot-dashboard
journalctl -u bxbot-worker -f      # acompanhar logs do worker
```

O `bxbot-worker.service` já injeta `SERVICE_ROLE=worker`, `PORT=8080` e
`ENABLE_HEALTH_SERVER=true`; o `bxbot-dashboard.service` injeta
`SERVICE_ROLE=dashboard` e `PORT=5000`. `Restart=on-failure` garante o
auto-restart. Configure as variáveis em `/opt/binancexbot/.env`
(ver [[Variaveis de Ambiente]]).

## HTTPS para o dashboard (Caddy)

O dashboard faz bind em `127.0.0.1` por padrão. Para expor na rede, coloque-o
atrás do **Caddy** (`deploy/Caddyfile`, TLS automático via Let's Encrypt) **e**
exija `DASHBOARD_TOKEN`:

```bash
sudo apt install caddy
# Editar deploy/Caddyfile: substituir SEU-DOMINIO.COM.
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

## Pré-requisito: banco Supabase
Provisione o Supabase **antes** (precisa da `DATABASE_URL`) → ver
[[Deploy Supabase]]. Mínimo no `.env` de cada alvo: `DATABASE_URL`,
`DATABASE_BACKEND=postgres`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`.

## Segurança de trading (importante)
- Padrão **seguro**: o worker sobe com `--simulacao` (paper). Mesmo com chaves
  reais, `DRY_RUN=true` mantém paper trading.
- Para operar **real**, é preciso, deliberadamente: trocar `--simulacao` por
  `--real` **e** setar `DRY_RUN=false` + `ALLOW_REAL_TRADING=true` +
  `ENV=production`. `main.py` faz fail-fast no boot se faltar API_KEY/SECRET ou
  `ENV`. Faça isso só após paper trading extenso (ver `PLANO_MODERNIZACAO.md`,
  raiz do repo).
- `ENV=production` também ativa, automaticamente: `CORS_SAME_ORIGIN_ONLY`
  (nega cross-origin no dashboard por padrão) e o fail-fast do dashboard se
  exposto (`DASHBOARD_BIND != 127.0.0.1`) sem `DASHBOARD_TOKEN` — ver
  [[Variaveis de Ambiente]].
- Antes de ativar `OCO_BRACKET`/`RECONCILIAR_BOOT_EXCHANGE` em produção
  real: validar em paper primeiro, incluindo **drills de restart** (matar o
  processo com uma posição aberta e confirmar que o boot recovery se
  comporta como esperado) — mesma cautela de qualquer mudança no caminho de
  dinheiro real.

## Build / dependências
Sem Docker, sem nixpacks: instalação nativa via `pip install -r requirements.txt`
sobre Python 3.11 (`.python-version`). O `deploy/setup.sh` cuida disso na VPS; no
Windows, use o venv/interpretador do PC.

## ✅ Docker / Railway / GCP aposentados e removidos do repo
Todo o caminho antigo (Dockerfile, `docker-compose*`, `railway.toml`, `Procfile`,
nixpacks, `cloudbuild.yaml`, `terraform/` GCP, `deploy.yml` GCP) foi **removido do
repositório** (o histórico git preserva). O `ci.yml` não faz build de imagem
Docker. Config de deploy ativa = `deploy/` (systemd units + `setup.sh` +
`Caddyfile`) + NSSM (Windows) + `.python-version` + `supabase/migrations/`.
