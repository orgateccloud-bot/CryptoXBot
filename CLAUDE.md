# CLAUDE.md — BinanceXBot / CryptoXbot (HFT Trading Bot)

Bot de trading algorítmico swing/intraday (sinais em 1h/4h) para **Binance Spot**.
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
| ML/IA | XGBoost (modelo principal) + sklearn MLP, em ensemble |
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
data/              # cvd_calculator + klines (fetch consolidado, cache TTL) — artefatos (*.db/*.pkl) gitignored
data/snapshots/    # substrato de pesquisa CONGELADO + manifest sha256 — VERSIONADO (I-11)
deploy/            # systemd units, Caddyfile, setup.sh
estrategias/       # Estratégias de trading (otimizada)
scripts/           # migrate_sqlite_to_supabase.py + restart-servico.ps1 +
                   #   purgar_fixtures_producao.py + normalizar_timestamps.py (BR->ISO, E-8)
static/vendor/     # socket.io + chart.js vendorizados (sem CDN externo)
supabase/          # migrations/ (schema Postgres)
templates/         # dashboard.html (SPA — tema claro/escuro)
tests/             # pytest (1520 passed, 7 skipped) — isolado de producao via conftest.py da raiz
docs/              # vault Obsidian (relatórios, deploy, pontuações)
```

Raiz: `main.py` (orquestrador), `executor.py`, `risco.py`, `binance_conta.py`
(fonte única de saldo/permissões), `database.py`,
`logger.py`, `ensemble.py`/`ml_filtro.py`/`lstm_modelo.py`/
`score.py`/`regime.py`/`fear_greed.py`, `dashboard.py`, `health.py`,
`telegram_bot.py`, `monitor_fluxo.py`, `indicadores.py`, `suporte.py`.

## Deploy

### Windows (PC 24/7) via NSSM — deploy atual

Dois serviços do Windows (rodam como LocalSystem, auto-restart em crash,
iniciam no boot). Registrados via `nssm install` em **PowerShell Administrador**:

- `BXBotWorker` → `python main.py --simulacao --intervalo 15` (env `PORT=8080`, `ENABLE_HEALTH_SERVER=true`)
- `BXBotDashboard` → `python dashboard.py` (porta 5000)

```powershell
# Restart com PROVA (auto-eleva via UAC, confirma pelo PID, cai no Stop-Process
# se o nssm restart não pegar). Use este em vez do nssm na mão:
powershell -ExecutionPolicy Bypass -File scripts\restart-servico.ps1
powershell -ExecutionPolicy Bypass -File scripts\restart-servico.ps1 -Servico BXBotDashboard

Get-Service BXBot*
```

**Duas armadilhas que fazem um restart "bem-sucedido" não reiniciar nada:**

1. Os serviços rodam como LocalSystem → controlá-los exige **elevação**. Sem
   ela, `nssm restart` diz "Acesso negado" (barulhento, ok), mas
   `Stop-Process -Force` **retorna sem erro e não mata o processo**. Quem
   confia nesse "sucesso" segue com o código velho em memória.
2. Estar no grupo Administradores **não basta**: com UAC ligado um shell comum
   recebe token filtrado (o grupo aparece em `whoami /groups` como *"usado
   apenas para negar"*). A janela parece administrativa e não é.

A única prova de restart é o **PID mudar** — Python lê os `.py` no start, então
sem PID novo nenhum deploy entrou em vigor. `scripts/restart-servico.ps1` existe
exatamente para não repetir esse erro.

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
# Se o banco já existia antes de 2026-07-13, rodar também (idempotente):
#   supabase/migrations/002_meta_labeling_columns.sql
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

# Dry run de validacao de EXECUCAO (estrategia trend, REPROVADA no hold-out)
python main.py --modo-trend --simulacao   # recusa iniciar com --real (SystemExit)

# Testes
pytest tests/ -q                    # 1520 passed, 7 skipped em ~2min40s
# A suite e ISOLADA do estado de producao pelo conftest.py da RAIZ: banco em
# tmp + guard que FALHA o teste se alguem abrir data/btc_data.db. Leia o
# cabecalho de conftest.py antes de mexer nele — ate 2026-07-31 `pytest tests/`
# gravava posicao de fixture NO BANCO VIVO, que o boot readotava e "fechava"
# com PnL de +62.658%, tres vezes. Nao remova o conftest para destravar teste.
#   (O teste de retreino levava >10min porque treinava sobre os dados REAIS de
#    producao; com o banco isolado roda em ~6s, que e o que o nome dele diz.)
# BXBOT_TEST_PG_URL=postgresql://... pytest tests/test_logger_postgres.py -v

# Migração de dados SQLite -> Supabase (idempotente)
python scripts/migrate_sqlite_to_supabase.py --dry-run   # depois --confirmar

# Pesquisa reproduzivel (I-11) — o substrato e congelado, nao a tabela viva
python -m research.snapshot --verificar     # sha256 do snapshot bate com o manifest?
python -m research.reproduzir --comparar    # re-deriva o veredito, exige diferenca 0.0
# Coleta para PESQUISA exige janela fixa (--dias e movel e nao e reproduzivel):
python backtesting/coletar_dados.py --todos --inicio 2024-04-03 --fim 2026-04-03

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
- **Entrada maker-first (P0-2)**: `LIMIT_MAKER` (post-only) no melhor bid — sempre
  fee de maker, nunca cruza o spread; re-quota se não preencher (`MAKER_*` no
  `.env`); fills parciais acumulados (posição usa o qty realmente executado).
  `MAKER_FIRST=false` volta ao LIMIT cruzando.
- **Stop loss NA EXCHANGE**: `STOP_LOSS_LIMIT` colocado na Binance após o fill
  (sobrevive a crash do bot); trailing/breakeven via cancel-then-replace com
  restauração do nível antigo se o novo falhar.
- **Bracket OCO nativo (P2-1, opt-in `OCO_BRACKET=false` por padrão)**: quando
  ligado, a proteção pós-entrada vira um par atômico stop+alvo final (target2)
  via `POST /api/v3/orderList/oco` (one-cancels-the-other) — o **alvo** passa a
  viver na exchange (sobrevive a crash), não só no monitor local. O take-profit
  parcial (50% no target1) segue no monitor (um OCO tem 1 qty / 2 pernas): no
  parcial o OCO é cancelado, vende-se 50% e recoloca-se um OCO para o runner.
  `OCO_TRAILING_DELTA_BIPS>0` delega o trailing ao servidor (`belowTrailingDelta`,
  em bips: 80 = 0.8%) e suprime o cancel-then-replace local. Abstração única
  (`_abrir/_liberar/_mover_protecao`): com OCO desligado, é o `STOP_LOSS_LIMIT`
  puro de sempre. Fallback p/ stop puro se o OCO falhar (nunca sem proteção).
- **Crash recovery**: posição persistida no DB (`database.salvar_posicao_aberta`,
  reusa `risk_state` com prefixo `posicao:`); no boot, `loop_par` recupera e
  religa o monitor. Sem posição órfã pós-restart.
- **API robusta**: `recvWindow=5000` + sync de relógio (offset serverTime,
  corrige -1021) + retry/backoff (429/-1003/5xx) + `newClientOrderId` idempotente
  + consulta pós-timeout (elimina ordem fantasma).
- Gestão de risco (`risco.py`): Kelly fracionado (25%), circuit breaker 5%/dia e
  15% total, 1 posição por vez.

## Modo trend (dry run) — validação de execução, NÃO estratégia aprovada

`--modo-trend` substitui a estratégia otimizada pelo sistema Donchian 20/10
diário (`estrategias/trend_live.py`). **A estratégia REPROVOU no hold-out**
(+5.70% a.a. vs piso pré-registrado de 8% — `research/METODOLOGIA_TREND.md`, e
o `GATE_GO_LIVE.md` Etapa 1 também está reprovada). Ela existe no caminho ao
vivo só para medir o que o backtest idealiza: latência sinal→fill, desvio entre
o preço de referência da decisão e o fill real, e estabilidade em 24/7.

- **Trava:** `--modo-trend` + `--real` → `SystemExit(1)` no boot (recusa a
  *intenção*, antes do downgrade por `ALLOW_REAL_TRADING`); 2ª camada em
  `main._trend_abrir()` recusa qualquer executor não-simulado.
- **Paridade:** usa a *mesma* `trend_following.donchian_niveis` do backtest, e
  **descarta a vela em formação** que a Binance devolve como último candle
  (usá-la seria look-ahead). Uma decisão por candle fechado
  (`_trend_ultimo_bucket`) — senão `--intervalo 15` reavaliaria a mesma barra
  diária ~96× e reentraria no mesmo dia da saída.
- **`Executor(modo_trend=True)`** desliga o `target2 = entrada*1.05` hardcoded
  e o trailing percentual: ambos cortariam a cauda direita, que é todo o edge
  do trend-following. O stop é trilhado pelo canal Donchian-M.
- **Divergência declarada:** o backtest sai no close abaixo do canal; ao vivo o
  stop na exchange dispara intrabar → saídas ao vivo ≤ backtest em timing.
  Medir esse gap é objetivo do experimento.
- Telemetria: a mesma das duas estratégias — ver seção abaixo.

## Telemetria de execução (as duas estratégias)

`main._registrar_execucao()` é compartilhada por trend e otimizada, para que os
números sejam comparáveis. Mede **três** preços, não dois:

| Preço | Origem | O que a diferença revela |
|---|---|---|
| `ref` | preço em que a estratégia decidiu — trend: close do candle fechado; otimizada: `f1h[-1]`, a vela **em formação** vinda do cache (TTL 30s) | — |
| `mercado` | `get_preco()` fresco no instante de mandar a ordem | custo de decidir sobre **dado velho** (já rende número real em paper) |
| `fill` | `posicao["entrada"]` | custo total vs. o que o backtest assume |

Gauges em `/metrics` (genéricos porque as duas estratégias nunca rodam juntas;
`exec_estrategia_trend` = 1/0 diz qual gerou a amostra):
`exec_desvio_ref_mercado_pct`, `exec_desvio_ref_fill_pct`,
`exec_latencia_sinal_fill_ms`, `exec_estrategia_trend`,
`exec_desvio_saida_ref_fill_pct`. Eventos: `execucao_entrada` / `execucao_saida`
em `bot_events`.

A latência conta do **início do ciclo** (antes do fetch de klines), não do envio
da ordem: o que interessa é sinal→fill inteiro (fetch + indicadores + ML + risco
+ ordem).

**PnL sai do FILL, não da referência** (corrigido 2026-07-26, registrado em
`docs/GATE_GO_LIVE.md`). `fechar_posicao` calculava PnL e gravava
`sinais.preco_saida` sobre `preco` — a referência que o monitor observou — e não
sobre o preço executado. Os dois diferem **mesmo em simulação** (SELL MARKET
chega em `_enviar_ordem` sem preço → ramo simulado lê preço fresco), então o
viés existia no registro de paper trading, não só em modo real. Como `pnl_usdt`
é a coluna que `relatorio_gate.py` usa para profit factor na Etapa 2, o registro
era otimista pelo slippage de saída.

A **decisão** de fechar segue em `avaliar_tick_monitor` sobre `preco`: decide-se
no que se vê, contabiliza-se no que se executa.

`executor.preco_medio_fill(resp, fallback)` é a fonte única do preço executado,
usada nas **duas** pernas. Não lê `resp["price"]` cegamente — numa MARKET a
Binance devolve `price: "0.00000000"`, então a ordem de preferência é
`cummulativeQuoteQty/executedQty` → média ponderada de `fills[]` → `price` se
> 0 → fallback. Sem isso a correção valeria só em simulação e cairia no fallback
em modo real. Na entrada isso também corrigiu o uso do preço-*limite* em vez do
fill médio (subestimava lucro num LIMIT que cruza e preenche melhor).

**Trades fechados antes de 2026-07-26 têm `pnl_usdt` otimista** — a série antiga
não é comparável com a nova.

## Modelos ML

- `ml_filtro.py` — **XGBoost** (modelo principal). Pickle salvo atomicamente
  (`tmp` + `os.replace` — crash no retreino não corrompe o modelo). **Um modelo
  POR PAR** (`data/modelo_xgb_{par}.pkl`), com o par gravado dentro do artefato e
  conferido no `prever()` (E-7).
- `lstm_modelo.py` — **MLP do sklearn** (nome "LSTM" é histórico; não é LSTM real).
  **Modelo único, treinado em BTCUSDT.** Desde E-7, `prever(symbol)` RECUSA pares
  sem modelo próprio em vez de alimentar o modelo de BTC com features de ETH/SOL —
  seria transferência de domínio nunca validada com 45% do peso do ensemble. Na
  prática ETH e SOL operam no ramo "Apenas XGBoost".
- `ensemble.py` — Ensemble ponderado XGBoost 55% + MLP 45%, ajuste por regime.
  `prever(symbol, regime_atual)` — `symbol` é **obrigatório** (E-7): até 2026-08-08
  o ensemble não recebia par e ETH/SOL usavam a previsão do modelo de BTC, apesar
  dos modelos por par já existirem.
  FSRS aposentado em 2026-07-21 (nunca ativava no caminho ao vivo — branch morto).
- `score.py` — Score unificado 0-100 (11 componentes; ≥60 opera reduzido, ≥70 cheio).
  CVD (7%) e OBI (8%) — ver seção CVD/OBI/WebSocket abaixo — só têm dado real
  para BTCUSDT; demais pares operam com esses dois componentes neutros (50).
- Retreino automático domingo 02h. `lgbm_modelo.py` (LightGBM) aposentado (órfão).
- **Guard-rail de drift (P1-4, sem MLflow)**: cada retreino registra AUC/`cv_auc_mean`
  em `model_metricas` (`database.salvar_metricas_modelo`) e compara contra o
  histórico recente do mesmo symbol/modelo_tipo (`validacao.detectar_drift` —
  piso absoluto de AUC ou queda de 2 desvios-padrão vs a média histórica).
  Detectar drift só **alerta** (`bot_events`, severity=WARNING) — nunca
  bloqueia o retreino automático, decisão explícita do usuário. Lógica
  compartilhada via `ml_filtro.verificar_drift_e_registrar()`, reusada por
  `lstm_modelo.py`.

## CVD / OBI / WebSocket

- CVD: WS `@aggTrade` do mercado spot (BTCUSDT). O parser usa `data["a"]`
  (aggregate trade id) — **NÃO `data["t"]`** (bug histórico que zerava o CVD;
  ver `tests/test_process_message.py`). `main.py` mantém tanto os
  acumuladores escalares (`cvd_btc`/`total_compras`/`total_vendas`) quanto um
  buffer rolante dos últimos 200 ticks brutos (`obter_historico_ticks_btc()`),
  repassado a `score._score_cvd()` via `historico_ticks` — sem esse buffer o
  componente CVD do score ficava sempre neutro em produção (P1-1).
- OBI (Order Book Imbalance, P1-1): 2ª conexão WS independente, Partial Book
  Depth Stream `@depth20@100ms` — cada mensagem já traz o top-20 completo
  (não é diff), então **não** precisa do protocolo de sincronização
  snapshot+`U`/`u`/`pu` do diff-depth stream da Binance. `main.obter_obi_suavizado()`
  retorna a média móvel de 30 mensagens (~3s), `None` se stale (>120s) ou sem
  dado — degrada o componente `_score_obi()` para neutro (50) em vez de
  reportar um valor obsoleto.
- Watchdog: `/ready` (worker) acusa `degraded` (503) se o WS `@aggTrade` não
  recebe mensagem há >120s (`health.registrar_ws_state`) — pega conexão
  zumbi/CVD congelado. O WS `@depth` tem watchdog próprio
  (`health.registrar_ws_state_depth`) mas **só informativo** no payload
  (`obi_stream_ok`) — não derruba `/ready`, já que OBI é um componente
  suplementar do score (8%), não crítico como o preço/CVD.
- Shutdown gracioso: `main.py` trata SIGTERM/SIGINT/SIGBREAK, para ambos os
  loops assíncronos (`_ws_loop`/`_ws_loop_depth`). No Linux/systemd o path
  gracioso roda completo; no Windows/NSSM o processo termina prontamente
  (CVD/OBI re-acumulam do stream no restart).
- **Shutdown não é incidente** (corrigido 2026-07-26). Todo stop limpo escrevia
  no stderr `Erro crítico WebSocket`, `Task was destroyed but it is pending!`,
  `Event loop is closed` e `no running event loop` — nada disso era falha, mas
  `Erro crítico WebSocket` é justamente a string que se usaria para caçar uma
  falha real (conexão zumbi/CVD congelado, o que o watchdog de `/ready` pega).
  Aparecendo em toda parada, deixava de ser sinal. Três correções:
  `_ws_encerrando()` discrimina parada pedida de falha (loga INFO e retorna, em
  vez de logar erro e dormir num loop já fechado); o `logger.critical("Máximo
  de tentativas atingido")` só dispara quando as tentativas realmente
  esgotaram, não quando o `while` sai por `_shutdown_event`; e
  `_drenar_tasks_pendentes()` cancela/drena as tasks (a `keepalive()` do
  `websockets`, a do próprio handler) **antes** do `loop.close()`, senão o GC
  as finaliza depois e o interpretador reclama. Medido com WS reais: 14
  ocorrências de ruído → **0**. Cada teste em `tests/test_ws_shutdown.py` tem
  par — shutdown não loga, e falha fora de shutdown **continua** logando; o
  risco da correção era engolir incidente junto com o ruído.

## Leitura de conta / saldo

`binance_conta.py` é a **fonte única** de saldo e de permissões (auditoria
2026-07-26). Antes havia duas implementações: `risco.py` fazia
`except Exception: pass; return 0.0` — chave revogada, drift, geo-block e rate
limit viravam todos "saldo zero", indistinguível de conta vazia, e quem consome
esse número é o **sizing de posição**. `dashboard.py` tinha uma cópia melhor,
que separava `autenticado`/`erro`/`saldo`: quem só exibia era informado, quem
decidia era cego.

- `saldo(ativo) -> (valor, erro)`: `(0.0, None)` = conta zerada;
  `(0.0, "...")` = não foi possível saber. `risco.get_saldo_*` mantém o contrato
  histórico (float, 0.0 em falha) mas agora **escala** a falha —
  `bot_events/saldo_indisponivel` + log, com debounce de 15 min (roda por ciclo
  por par; sem debounce viraria flood).
- **Relógio**: toda chamada assinada usa `binance_conta.timestamp_ms()`, que
  compensa o drift com TTL de 5 min e re-sincroniza no erro `-1021`. Antes só
  `executor.py` compensava; `risco.py` assinava com `time.time()` cru.
- `restricoes_chave()` lê `/sapi/v1/account/apiRestrictions`. **`canTrade` de
  `/api/v3/account` é da CONTA, não da CHAVE** — pode vir `True` com a chave
  read-only. Para saber se o bot consegue mandar ordem, o campo é
  `enableSpotAndMarginTrading`.
- Diagnóstico rápido: `python binance_conta.py`.

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
