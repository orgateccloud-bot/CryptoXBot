# Relatorio de Planejamento: Substituicao de Docker/GCP por Supabase + Railway

Data: 2026-06-17  
Projeto: BinanceXBot  
Objetivo: mapear o estado atual, planejar a retirada gradual de Docker/GCP como runtime principal e propor uma arquitetura com Railway para execucao da aplicacao e Supabase para persistencia.

## 1. Sumario Executivo

O projeto hoje esta estruturado como um bot Python de trading para Binance, com duas linhas arquiteturais convivendo:

1. A linha operacional atual, centrada em `main.py`, `database.py`, `executor.py`, `risco.py`, `estrategias/otimizada.py` e `dashboard.py`.
2. Uma linha modular mais nova, em `core/`, `execution/`, `infra/` e `ai/`, com event loop async, DI, metricas, paper trading e abstracoes de banco.

A substituicao de Docker por Railway e de SQLite local por Supabase e viavel, mas nao deve ser feita como um "lift and shift" simples. O desenho recomendado e separar o sistema em dois servicos Railway:

| Servico | Plataforma | Responsabilidade | Comando inicial sugerido |
|---|---|---|---|
| `binancexbot-worker` | Railway | Bot principal, WebSocket, estrategia, risco, execucao e persistencia | `python main.py --intervalo 15` |
| `binancexbot-dashboard` | Railway | Dashboard Flask/SocketIO e APIs de leitura | `python dashboard.py` ou `gunicorn` apos ajuste |
| `binancexbot-db` | Supabase | Postgres gerenciado para trades, snapshots, CVD, sinais, auditoria e estado | N/A |

O ponto mais critico e trocar o banco local SQLite por Postgres/Supabase sem quebrar o fluxo de trading. O segundo ponto critico e adaptar dashboard e healthcheck para Railway, pois Railway injeta a variavel `PORT` e espera que a aplicacao web escute nela.

## 2. Novo Mapeamento do Projeto

### 2.1 Entrada e runtime

| Componente | Arquivo | Estado atual | Papel na migracao |
|---|---|---|---|
| Bot principal | `main.py` | Runtime real do bot. Usa threads, WebSocket Binance e SQLite. | Deve virar worker Railway. |
| Dashboard | `dashboard.py` | Flask + SocketIO, porta fixa `5000`. | Deve virar servico web Railway ouvindo `PORT`. |
| Runtime async novo | `core/event_loop.py` | Parcialmente scaffoldado; nao e o entrypoint do Docker atual. | Pode virar alvo futuro, nao primeira migracao. |
| Container | `Dockerfile`, `docker-compose*.yml` | Runtime atual/planejado para Docker/GCP. Ha inconsistencia com `target: production`. | Deve ser substituido por config Railway/Nixpacks ou mantido apenas como fallback local. |

### 2.2 Dados e persistencia

| Componente | Arquivo | Estado atual | Mudanca necessaria |
|---|---|---|---|
| SQLite sincrono | `database.py` | Usado por `main.py`, `dashboard.py`, estrategia e executor. | Criar adapter Postgres/Supabase mantendo API compativel. |
| SQLite async | `infra/database.py` | Abstracao nova com `aiosqlite`, parcialmente isolada. | Evoluir para `PostgresStrategy`, mas nao bloquear primeira fase. |
| Dados locais | `data/`, `*.db`, `*.pkl` | Banco, modelos e historicos locais. | Migrar tabelas para Supabase; modelos podem ir para storage/volume ou artifact versionado. |
| Logs | `logs/`, `logger.py`, `infra/logging.py` | Logs locais e estruturados. | Usar logs Railway como primario; manter tabela de auditoria no Supabase. |

### 2.3 Trading e IA

| Camada | Arquivos | Observacao |
|---|---|---|
| Estrategia | `estrategias/otimizada.py`, `score.py`, `indicadores.py`, `suporte.py` | Forte dependencia de chamadas REST Binance e banco para sinais. |
| Risco | `risco.py` | Usa saldo real Binance, circuit breaker e SQLite para historico. Precisa persistir estado diario no Supabase. |
| Execucao | `executor.py`, `execution/order_manager.py` | Executor legado e usado hoje; async novo ainda e camada paralela. |
| ML | `ensemble.py`, `ml_filtro.py`, `lstm_modelo.py`, `lgbm_modelo.py`, `ai/inference.py` | Modelos locais e retreinamento semanal. Precisa definir storage/versionamento dos artefatos. |
| Retreinamento | `main.py` | Thread semanal no worker. | Em Railway, pode continuar no worker inicialmente; depois pode virar cron job separado. |

### 2.4 Deploy e infraestrutura atual

| Item | Arquivo | Destino recomendado |
|---|---|---|
| GCP deploy | `.github/workflows/deploy.yml`, `cloudbuild.yaml`, `terraform/`, `deploy-prod.sh` | Depreciar apos Railway estabilizado. |
| Compose local/prod | `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.dev.yml` | Manter temporariamente como fallback local; remover do caminho de producao. |
| Prometheus/Grafana | `monitoring/` | Substituir por Railway metrics + logs; Prometheus pode ficar opcional. |
| Systemd/Nginx | `deploy/` | Legado para VPS; depreciar. |

## 3. Arquitetura Alvo

```text
                 +-------------------------+
                 |        Railway          |
                 |                         |
                 |  worker: main.py        |
Binance WS/REST <+  - CVD                  |
                 |  - estrategia           |
                 |  - risco                |
                 |  - executor             |
                 |                         |
                 |  web: dashboard.py      |
Usuario Web  <---+  - Flask/SocketIO       |
                 |  - APIs de leitura      |
                 +-----------+-------------+
                             |
                             | DATABASE_URL / Supabase pooler
                             v
                 +-------------------------+
                 |       Supabase          |
                 |  Postgres              |
                 |  - trades              |
                 |  - snapshots_mercado   |
                 |  - cvd_historico       |
                 |  - sinais              |
                 |  - risk_state          |
                 |  - bot_events          |
                 +-------------------------+
```

## 4. Plano de Substituicao em Fases

### Fase 0 - Congelamento e seguranca

| Acao | Por que |
|---|---|
| Manter `DRY_RUN=true` durante toda a primeira migracao. | Evita ordens reais enquanto infraestrutura muda. |
| Separar variaveis reais de Binance em secrets/variaveis Railway. | Remove dependencia de `config/settings.py` local. |
| Criar branch dedicada `migration/railway-supabase`. | Reduz risco no branch principal. |
| Exportar o SQLite atual antes da migracao. | Permite rollback e comparacao de dados. |

### Fase 1 - Preparar Supabase

Criar migrations SQL versionadas em `supabase/migrations/` para substituir as tabelas criadas por `database.py`.

Tabelas minimas:

| Tabela | Origem atual | Melhorias recomendadas |
|---|---|---|
| `trades` | `database.py` | Adicionar `symbol`, `trade_id`, indice por `timestamp`, `symbol`. |
| `snapshots_mercado` | `database.py` | Adicionar `symbol`; padronizar nomes sem `%`. |
| `cvd_historico` | `database.py` | Adicionar `symbol`, janela/timeframe e indice temporal. |
| `sinais` | `database.py` | Adicionar `symbol`, `score`, `source`, `executado_em`. |
| `risk_state` | novo | Persistir drawdown, bloqueio, posicoes e dia operacional. |
| `bot_events` | novo | Auditoria operacional, erros, alertas e circuit breaker. |

Decisao de conexao:

| Opcao Supabase | Uso recomendado neste projeto |
|---|---|
| Direct connection | Boa para um worker persistente se a rede suportar IPv6 ou houver add-on IPv4. |
| Shared pooler session mode | Alternativa segura para Railway caso a conectividade direta IPv6 cause problema. |
| Transaction pooler | Melhor para workloads serverless; evitar se o driver usar prepared statements sem ajuste. |

### Fase 2 - Criar camada de banco compativel

Objetivo: trocar implementacao, nao reescrever o bot inteiro.

Criar um modulo como `infra/postgres_database.py` ou evoluir `database.py` para escolher backend:

```python
# Por que: preserva chamadas existentes como database.salvar_trade(...)
# e permite migrar sem tocar em toda a estrategia de uma vez.
BACKEND = os.getenv("DATABASE_BACKEND", "sqlite")

if BACKEND == "postgres":
    # usar psycopg/psycopg_pool com DATABASE_URL
    ...
else:
    # manter SQLite local para desenvolvimento e rollback
    ...
```

Dependencias sugeridas:

| Biblioteca | Motivo |
|---|---|
| `psycopg[binary,pool]` | Driver moderno para Postgres com pool. |
| `SQLAlchemy` opcional | So vale se o projeto for padronizar models/migrations. |
| `supabase` opcional | Bom para Storage/Auth/API, mas para trading backend Postgres direto e mais previsivel. |

### Fase 3 - Adaptar Railway

Criar `railway.json` ou configurar pelo painel:

| Servico | Start command | Healthcheck | Observacao |
|---|---|---|---|
| Worker | `python main.py --intervalo 15` | Sem HTTP obrigatorio inicialmente ou endpoint leve separado | Nao deve expor dashboard. |
| Dashboard | `python dashboard.py` | `/health` | Deve ouvir `PORT`. |

Mudancas de codigo necessarias:

| Arquivo | Mudanca |
|---|---|
| `dashboard.py` | Adicionar `/health` retornando 200 e trocar `port=5000` por `int(os.getenv("PORT", "5000"))`. |
| `main.py` | Ler configuracoes de ambiente quando `config/settings.py` nao existir. |
| `requirements.txt` | Adicionar driver Postgres e, possivelmente, `gunicorn`/`eventlet` se for estabilizar SocketIO. |
| `.env.example` | Adicionar `DATABASE_BACKEND=postgres`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. |

### Fase 4 - Migrar dados

Ordem recomendada:

1. Congelar o bot em paper trading.
2. Exportar SQLite atual.
3. Criar schema no Supabase via migration.
4. Rodar script idempotente `scripts/migrate_sqlite_to_supabase.py`.
5. Validar contagens por tabela.
6. Rodar dashboard contra Supabase.
7. Rodar worker contra Supabase em `DRY_RUN=true`.

Validacoes minimas:

| Validacao | Criterio |
|---|---|
| Contagem de `trades` | Supabase >= SQLite exportado. |
| Ultimos sinais | Ordem temporal preservada. |
| Dashboard `/api/sinais` | Responde sem depender de arquivo `.db`. |
| Worker | Salva CVD/sinais em Supabase sem erro. |
| Latencia | Insercao simples abaixo de limite aceitavel para o bot. |

### Fase 5 - Desativar Docker/GCP do caminho principal

Depois de Railway + Supabase estabilizados:

| Item | Acao |
|---|---|
| `.github/workflows/deploy.yml` | Substituir por workflow de testes apenas ou deploy Railway via GitHub integration. |
| `cloudbuild.yaml` | Arquivar/depreciar. |
| `terraform/` | Marcar como legado GCP ou remover em PR separado. |
| `docker-compose.prod.yml` | Manter apenas como fallback local temporario. |
| `PRODUCTION_README.md` | Atualizar para Railway/Supabase. |

## 5. Melhorias Recomendadas

### 5.1 Seguranca

| Melhoria | Impacto |
|---|---|
| Remover dependencia de `config/settings.py` em producao. | Evita segredo local dentro do runtime. |
| Usar variaveis Railway sealed para `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`. | Reduz exposicao de segredos. |
| Criar papel Postgres especifico para o bot, sem permissoes administrativas. | Limita impacto se a credencial vazar. |
| Ativar RLS nas tabelas expostas ao dashboard publico, se houver acesso frontend direto. | Protege dados se futuramente usar API Supabase no cliente. |
| Persistir eventos de risco/circuit breaker em `bot_events`. | Melhora auditoria pos-incidente. |

### 5.2 Confiabilidade

| Melhoria | Impacto |
|---|---|
| Separar worker e dashboard. | Uma falha visual nao derruba o bot. |
| Healthcheck real em `/health`. | Deploy Railway so recebe trafego quando o web estiver pronto. |
| Persistir `risk_state`. | Reinicio nao zera drawdown ou bloqueio operacional. |
| Idempotencia em trades por `trade_id`. | Evita duplicidade apos reconnect WebSocket. |
| Retreinamento como servico/cron separado. | Evita CPU spike dentro do worker de trading. |

### 5.3 Performance

| Ponto | Estado atual | Melhoria |
|---|---|---|
| SQLite local | Rapido, mas preso ao disco local. | Postgres com indices por `symbol`, `timestamp`. |
| Dashboard chama backtest via rota | Pode bloquear request. | Mover backtest para job/worker e salvar resultado. |
| Chamadas REST Binance repetidas | Varias funcoes chamam API diretamente. | Cache curto por par/timeframe para dashboard. |
| ML local | Carrega e prediz em fluxo principal. | Preload de modelo e isolamento de inferencia em worker/thread pool. |

### 5.4 Observabilidade

| Melhoria | Impacto |
|---|---|
| Logs estruturados com `symbol`, `service`, `event_type`, `order_id`. | Facilita busca no Railway. |
| Tabela `bot_events`. | Historico consultavel no dashboard. |
| Endpoint `/health` com dependencias basicas. | Detecta DB indisponivel e status de runtime. |
| Endpoint `/ready` opcional. | Diferencia processo vivo de pronto para operar. |

## 6. Riscos e Mitigacoes

| Risco | Severidade | Mitigacao |
|---|---:|---|
| Latencia do Supabase afetar fluxo de tick/CVD | Alta | Buffer em memoria + batch insert para trades frequentes. |
| Worker e dashboard escrevendo/consultando mesmas tabelas sem indices | Media | Criar indices antes do go-live. |
| Railway reiniciar worker durante posicao aberta | Alta | Persistir posicao/estado e reconciliar com Binance no startup. |
| Dependencia de arquivo `config/settings.py` quebrar deploy | Alta | Fallback obrigatorio para env vars. |
| Healthcheck inexistente quebrar deploy web | Media | Adicionar `/health` e usar `PORT`. |
| Retreinamento consumir CPU em producao | Media | Isolar como job/cron apos primeira fase. |

## 7. Roadmap Tecnico Sugerido

| Prioridade | Entrega | Resultado |
|---|---|---|
| P0 | Adicionar `PORT` + `/health` no dashboard | Web pronto para Railway. |
| P0 | Criar schema Supabase equivalente ao SQLite | Base pronta para migracao. |
| P0 | Adapter Postgres mantendo API de `database.py` | Bot troca backend por env var. |
| P1 | Script de migracao SQLite -> Supabase | Dados historicos preservados. |
| P1 | Config Railway para worker/dashboard | Docker sai do caminho principal. |
| P1 | Persistir `risk_state` e posicoes abertas | Reinicio mais seguro. |
| P2 | Separar retreinamento em cron/job | Runtime do bot mais estavel. |
| P2 | Refatorar para arquitetura async nova | Menos threads e melhor controle de lifecycle. |

## 8. Fontes Consultadas

- Railway Docs - Start Command: https://docs.railway.com/deployments/start-command
- Railway Docs - Healthchecks: https://docs.railway.com/deployments/healthchecks
- Railway Docs - Variables: https://docs.railway.com/variables
- Supabase Docs - Connecting to Postgres: https://supabase.com/docs/guides/database/connecting-to-postgres
- Supabase Docs - Local development and migrations: https://supabase.com/docs/guides/local-development/overview
- Supabase Docs - pg_cron: https://supabase.com/docs/guides/database/extensions/pg_cron

## 9. Conclusao

A melhor substituicao nao e "Docker por Railway" em bloco unico. E uma separacao limpa:

1. Railway executa processos Python.
2. Supabase centraliza dados e estado.
3. Docker/GCP ficam como legado ou fallback ate a estabilizacao.
4. O bot continua em paper trading ate provar persistencia, healthcheck, reconnect e recuperacao de estado.

O caminho mais seguro e iniciar por compatibilidade: manter as assinaturas atuais de `database.py`, adicionar backend Postgres por variavel de ambiente, subir dashboard e worker como servicos separados no Railway e so depois limpar os artefatos Docker/GCP.
