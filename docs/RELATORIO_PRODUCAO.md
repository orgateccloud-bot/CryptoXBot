# Relatório de Produção — Mapeamento, Scorecard e Plano Final

> **Data:** 2026-08-12 · **Sucede:** `RELATORIO_MODULOS.md` (auditoria de 07/2026, nota global 2/10)
> **Método:** notas re-derivadas dos critérios de saída fechados das 12 frentes + gates medidos
> (flake8/bandit/pytest/CI) + evidência viva do banco e dos serviços — **não** é uma nova auditoria
> linha-a-linha dos 45 módulos. Onde a nota herda um critério de frente, a evidência citada é o teste
> ou o comando que o provou. Onde nada foi re-verificado, está escrito.

---

## Veredicto

A pergunta "entrar em produção" tem duas respostas diferentes, e confundi-las é o erro que todo o
sistema de gates existe para impedir:

| Sentido de "produção" | Veredicto | Por quê |
|---|---|---|
| **Operação 24/7 em paper** (o bot como serviço confiável) | ✅ **JÁ ESTÁ EM PRODUÇÃO** | `BXBotWorker` + `BXBotDashboard` rodando via NSSM, portas 8080/5000 vivas, boot em SIMULAÇÃO registrado em `bot_events` (2026-08-12 00:34), crash-recovery e reconciliação de boot testados |
| **Capital real** | ❌ **PROIBIDO — e não por engenharia** | Etapa 1 do gate REPROVADA (4 de 5 critérios, retorno −21,25% vs +14,09% B&H); cinco hipóteses de edge reprovadas com critério pré-registrado; FAIL pré-registrado é final |

**Nota de engenharia: 3 → 7.** A auditoria de julho deu 2/10 global com três razões em série: sem
edge, réguas irreproduzíveis, e execução que falharia de forma composta na primeira ordem real. As
razões 2 e 3 foram **fechadas e provadas por teste** — substrato versionado com sha256, hold-out por
data, régua única com causalidade provada, cadeia de dia-1 fail-closed, travas que travam. A razão 1
— **não há edge** — permanece intacta, e é a única que decide capital.

**Nota de edge: 0, inalterada.** Nenhuma quantidade de engenharia move este número. O caminho está
pré-registrado (`research/METODOLOGIA_MICROESTRUTURA.md`, commitada antes de qualquer medição), o
laboratório (`micro_lab.py`) ainda não existe.

---

## Antes → Depois (medido)

| Métrica | 07/2026 (auditoria) | 12/08/2026 | Prova |
|---|---|---|---|
| flake8 | 2.170 achados, `\|\| true` | **0, bloqueante** | CI run 4/4 verde |
| Testes | ~920 | **1.696 passed, 14 skipped** | `pytest tests/ -q` |
| bandit (código ativo) | achados abertos | **0** | `-ll` limpo |
| CI | não bloqueava nada | **3 checks obrigatórios + branch protection** | push recusado sem checks |
| Módulos nota ≤ 3 | 31 de 45 (75% do LOC) | **1** entre os re-pontuados (`backtesting/motor.py`); 8 módulos não re-auditados | mapeamento abaixo |
| Alertas Telegram entregues | 0 de 8, falha silenciosa | falha **detectada e gritada** (`alerta_nao_entregue` CRITICAL) — entrega pendente de token real | `bot_events` 2026-08-11 |
| Trades de paper com PnL real | 0 de 5.255 | **1** (circuito fechado em 2026-08-09) | `sinais.pnl_usdt` |
| Vereditos reproduzíveis | 0 de 5 | snapshot sha256 + hold-out por DATA + `reproduzir.py` | `research/vereditos/` |
| Scripts destrutivos | 1, que apagava estado legítimo | 3, todos com dry-run, arquivamento verificado e guarda de produção | 26+13 testes |
| LOC versionado (sem `_legado/`) | 16.517 auditadas | 44.333 (código + testes; núcleo raiz 12.357) | `git ls-files` |

---

## Mapeamento por módulo

Legenda: nota **antiga → nova**. 🟢 ≥6 · 🟡 4–5 · 🔴 ≤3. "Evidência" = o que sustenta a mudança.

### Execução e risco

| | Módulo | LOC | Nota | Evidência da mudança |
|---|---|---:|---|---|
| 🟢 | `executor.py` | 2.160 | 3 → **7** | I-10a–h: comissão em ativo-base descontada, `abrir_long` fail-closed sem proteção, reconciliação periódica com a exchange, `fechar_posicao` atômica/idempotente, RLock + escopo de lock, reconciliação de boot. Não é 8 porque o critério testnet (50 ciclos) está escrito e **não rodado** |
| 🟢 | `main.py` | 1.744 | 3 → **6** | `--real` com `DRY_RUN=true` **aborta o boot** (main.py:1388); invariante stop<preço<target; watchdog; reconciliação. Segue monólito de 1.744 linhas com `→`/`─` no stdout (crasha em console cp1252 — padrão corrigido nos scripts, não aqui) |
| 🟢 | `risco.py` | 1.017 | 3 → **7** | `MAX_DRAWDOWN_TOTAL` **comparado e travando** (risco.py:269); Kelly sobre `pnl_usdt` real; `validar_trade` recebe o stop real; gate de CVaR por regime alcançável (P2-3, testes dirigidos) |
| 🟢 | `binance_conta.py` | 222 | 5 → **6** | `restricoes_chave()` ganhou consumidor real: `testar_api.py` reescrito reprova chave read-only (rodado ao vivo, exit 1) |

### Sinal

| | Módulo | LOC | Nota | Evidência |
|---|---|---:|---|---|
| 🟢 | `score.py` | 503 | 4 → **6** | OBI novo (peso 8) ortogonal ao CVD (rebaixado a 7); F&G fail-closed; limiares alinhados à fonte. O componente CVD segue matematicamente fraco — por isso 6, não 7 |
| 🟢 | `suporte.py` | 451 | 2 → **6** | symbol **obrigatório** (E-7a); ScaleIn fora do caminho vivo (E-8b). A causa-raiz do stop 34× acima da entrada não existe mais |
| 🟢 | `estrategias/otimizada.py` | — | 2 → **6** | dupla execução por par, leitura/escrita separadas (E-7c), thread `sinal_id` fim-a-fim (E-8d) |
| 🟢 | `regime.py` | 284 | 4 → **6** | symbol obrigatório; klines via `data/klines.py` |
| 🟢 | `ml_filtro.py` | 486 | 3 → **6** | `dist_vwap` = `vwap_rolling(20)` treino **e** inferência (skew de sinal invertido eliminado, ml_filtro.py:111); drift-check + registro de métricas; modelos retreinados |
| 🟡 | `lstm_modelo.py` | 383 | 2 → **5** | symbol propagado; métricas registradas. cv_auc ~0,55 continua ruído carregando peso no ensemble — **decisão do piso 0,55 pendente do operador** |
| 🟢 | `ensemble.py` | 250 | 3 → **6** | `prever(symbol, regime)` — os 3 pares deixaram de receber a probabilidade do BTC; FSRS aposentado |
| 🟢 | `fear_greed.py` | 186 | 2 → **6** | fail-closed (ausência **aborta**, I-12g); histórico versionado com manifest (~100 KB); timeouts |
| 🟡 | `estrategias/trend_live.py` | — | 3 → **5** | dry-run de validação de EXECUÇÃO com telemetria; a estratégia em si está REPROVADA por escrito e não promove |

### Dados e persistência

| | Módulo | LOC | Nota | Evidência |
|---|---|---:|---|---|
| 🟢 | `database.py` | 1.159 | 3 → **7** | `_primeiro_valor()` mata o `KeyError: 0` sob dict_row; pool com `open=True`; TIMESTAMPTZ; 3 funções de crash-recovery com teste; suíte Postgres real escrita (aguarda DSN) |
| 🟢 | `logger.py` | 594 | 3 → **6** | timestamps ISO + query por range (o relatório diário parou de mentir); TIMESTAMPTZ no PG; SQLite com WAL/busy_timeout |
| 🟢 | `data/klines.py` | — | 5 → **7** | fonte única dos 6 fetchers; robustez I-11d; testes de stale/TTL |
| 🟡 | `data/cvd_calculator.py` | — | 2 → **4** | KeyError corrigido, CVD calculado sobre `data["a"]` certo; mas o teto matemático \|score\| ≤ 0,069 < limiar 0,1 continua — está documentado como motivação da E-11, não como componente vivo de decisão |
| 🟢 | `indicadores.py` | 235 | 4 → **6** | `vwap_rolling` unificado entre backtest, treino e vivo — os parâmetros passaram a ser calibrados sobre o indicador que a produção calcula |
| 🟢 | `backtesting/coletar_dados.py` | — | 4 → **6** | determinístico (`--inicio/--fim`), vela aberta censurada (I-11d) |

### Superfícies

| | Módulo | LOC | Nota | Evidência |
|---|---|---:|---|---|
| 🟡 | `dashboard.py` | 951 | 2 → **5** | read-only em `trades` (E-8e); bind local + token; CORS fechado; rota `/api/backtest` desligada (I-12a). Segue 951 LOC com o padrão cp1252 no stdout |
| 🟡 | `telegram_bot.py` | 342 | 2 → **5** | placeholder **detectado por substring** e escalado como `alerta_nao_entregue` CRITICAL em `bot_events`; 7 alertas com call site (P2-5). A nota só sobe quando um token real provar entrega ponta-a-ponta — evidência viva de 2026-08-11 mostra que ainda não foi configurado |
| 🟢 | `health.py` | 280 | 3 → **6** | `HEALTH_BIND` configurável, default local (era 0.0.0.0 fixo); gauges + `/ready` watchdog |
| 🟢 | `relatorio_gate.py` | 493 | 4 → **7** | deixou de escolher a fonte por omissão (backend configurado > default); fail-closed sem DSN; PF sem perdas = indefinido (não `inf`); Etapa 1 consultada; **27 testes onde havia zero** |

### Pesquisa e backtest

| | Módulo | LOC | Nota | Evidência |
|---|---|---:|---|---|
| 🟢 | `backtesting/metricas.py` | — | 7 → **8** | DSR parou de mentir (I-12c); `cvar_historico` com consumidor real |
| 🟢 | `validacao.py` | 146 | 7 → **7** | purge/embargo intactos; `detectar_drift` **segue inerte até existir base viva de cv_auc** — pendência E-10, do tempo, não de código |
| 🟢 | `backtesting/walk_forward.py` | — | 4 → **6** | lê `params_pares` (I-12f); F&G ausente aborta (I-12g); política de saída real modelada (I-12h); produziu o FAIL oficial da Etapa 1 |
| 🟡 | `backtesting/motor_ensemble.py` | — | 1 → **5** | os 3 defeitos de régua propagados (idx4 fechado, taxa SPOT, Sharpe honesto); rota HTTP que o servia desligada |
| 🔴 | `backtesting/motor.py` | — | 1 → **3** | taxa SPOT e idx4 corrigidos, mas **8 componentes do score seguem mockados** — serve como teste de fumaça, não como régua. Candidato a `_legado/` |
| 🟢 | `backtesting/otimizador.py` | — | 2 → **6** | Sharpe do grid agora paga taxa (otimizador.py:81,168) |
| 🟢 | `research/edge_lab.py` | — | 4 → **6** | hold-out por **DATA fixa** (deriva de fronteira eliminada); trava de uso único com leitor; funções do veredito com entrypoint |
| 🟢 | `research/carry_lab.py` | — | 2 → **6** | funding coletado e versionado; o FAIL de carry voltou a ter substrato |
| 🟢 | `research/reproduzir.py` | novo | — → **7** | o critério de saída de I-11: um comando re-deriva os vereditos com diferença 0.0 |

### Ferramentas

| | Módulo | Nota | Evidência |
|---|---|---|---|
| 🟢 | `scripts/migrate_sqlite_to_supabase.py` | 3 → **7** | preserva as 4 colunas de resultado; savepoint por tabela; commit condicional; DSN mascarada; **guarda de destino populado** (o `ON CONFLICT` era inerte em 4 de 6 tabelas — ver memória do projeto); `--help`/`--confirmar` não crasham mais em cp1252 |
| 🟢 | `scripts/purgar_fixtures_producao.py` | 3 → **6** | parou de casar com toda posição legítima de paper (consulta o modo efetivo); preserva a Etapa 2 |
| 🟢 | `scripts/purgar_retencao.py` | novo → **7** | dump verificado (relê + sha256) **antes** do DELETE; restauração idempotente validada contra o schema; 26 testes |
| 🟢 | `testar_api.py` | 3 → **6** | responde a pergunta certa (`restricoes_chave`), reprova read-only ao vivo |
| 🟢 | `conftest.py` | — → **7** | guard anti-produção que já pegou 2 defeitos reais nesta rodada (fallback de klines e teste de purga) |
| — | `_legado/` (9 artefatos) | — | `monitor_fluxo`, `ollama_client`, `motor_otimizado`, `motor_vectorbt`, `settings_template` etc. — aposentados com LEIA-ME e rollback (@Zeta) |

**Não re-verificados nesta rodada:** `analise_mercado.py` (timeouts entraram em M-1), `binance_conta`
internals, `backtesting/trend_following.py`, `alinhamento.py`, `regua.py`, `config/params_pares.py`
(os Sharpes otimistas do comentário seguem lá — inofensivos, mas merecem limpeza), e os diretórios
vazios `core/`, `execution/`, `infra/`, `ai/` (só `__init__.py` — candidatos a remoção).

---

## Scorecard por dimensão

| Dimensão | 07/2026 | Hoje | O que falta para 🟢 |
|---|:---:|:---:|---|
| **Edge validado** | 🔴 | 🔴 | É o portão. Instrumentos prontos em 2026-08-12 (`micro_lab.py` fail-closed + `coletar_book.py` coletando desde já); primeira medição possível com ~13 dias de coleta contínua; um PASS que hoje não existe |
| Travas de capital (I-8) | 🔴 | 🟢 | — (DRY_RUN aborta contradição; drawdown total trava; 3 condições + `--real` + PROCEDÊNCIA) |
| Canal de escalonamento (I-9) | 🔴 | 🟡 | **Token Telegram real** — a detecção funciona (CRITICAL em `bot_events` ao vivo), a entrega segue morta |
| Execução na exchange (I-10) | 🔴 | 🟡 | Rodar `INTEGRACAO_CICLOS=50` contra testnet (precisa de chaves testnet do operador) |
| Sinal multi-par (E-7) | 🔴 | 🟢 | — |
| Contabilidade de paper (E-8) | 🔴 | 🟢 | — (circuito fechado; 1º trade com PnL real em 2026-08-09) |
| Substrato reproduzível (I-11) | 🔴 | 🟢 | — |
| Régua de medição (I-12) | 🔴 | 🟢 | — |
| Persistência/Postgres (I-13) | 🔴 | 🟢 | Critério de saída **CUMPRIDO em 2026-08-12**: 40/40 testes contra PostgreSQL 18 real (cluster descartável) — migrar 2× com contagem idêntica, pnl idêntico origem/destino, logger ciclo completo. Restam do operador: purga `--confirmar` e decisão sobre UNIQUE |
| ML honesto (E-10) | 🟡 | 🟡 | Base viva de cv_auc (tempo) + decisão do piso MLP 0,55 |
| Sentimento (E-9) | 🔴 | 🟢 | — |
| Qualidade/CI (M-1) | 🔴 | 🟢 | — |
| Segurança operacional | 🟡 | 🟡 | Revogar chave `...P9A2h` (viva no histórico git em `3c0fc70`); IP allowlist na `...6kpYs` |

---

## Plano final para entrar em produção

### O que "entrar em produção" pode significar aqui

O bot **já opera em produção paper**. O plano abaixo é o caminho até **capital real** — e ele tem um
caminho crítico que não é código: é pesquisa. As fases 0–2 são dias e são todas executáveis agora; a
fase 3 é meses e é a única que decide.

### Fase 0 — Higiene do operador (custo: ~1 hora de cliques, hoje)

| # | Ação | Comando/local | Por quê agora |
|---|---|---|---|
| 1 | **Configurar token Telegram real** | `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` + restart | O canal de escalonamento está gritando `alerta_nao_entregue` CRITICAL desde 2026-08-10. Custo zero, fecha I-9 |
| 2 | **Revogar a chave `...P9A2h`** | painel Binance | Está no histórico git público do repo (`3c0fc70`). Enquanto viver, é credencial vazada ativa |
| 3 | IP allowlist na chave ativa `...6kpYs` | painel Binance | Reduz o raio de qualquer vazamento futuro |
| 4 | Rodar as provas Postgres (12 testes) | `createdb bxbot_teste` → `DATABASE_URL_TESTE=... pytest tests/test_migrador_postgres_real.py tests/test_database_postgres.py -v` | Fecha o critério de saída de I-13. Isolado por schema — não toca produção nem se apontado errado |
| 5 | Purga de retenção | `python scripts/purgar_retencao.py --confirmar` e, com worker parado, `--vacuum` | 1,85 M linhas (63% dos 375 MB) arquivadas e removidas; dump verificado antes do DELETE |
| 6 | Decidir piso MLP 0,55 | — | cv_auc ~ruído carregando peso 20 no ensemble via ML |
| 7 | Decidir constraints UNIQUE no Postgres | migration nova | Única forma de idempotência real no migrador além da guarda; mudança de schema é sua |

### Fase 1 — Prova de execução (custo: ~1 semana, depende de chaves testnet)

1. Criar chaves na **Binance Spot Testnet** (não são as de produção).
2. `INTEGRACAO_CICLOS=50 pytest tests/integration/ -v` — o critério de saída de I-10 que está
   escrito e nunca rodou.
3. Drill de restart: derrubar o worker com posição paper aberta, subir com
   `RECONCILIAR_BOOT_EXCHANGE=true`, verificar reconstrução. Duas vezes.

**Critério de saída:** os 50 ciclos verdes + 2 drills sem posição órfã. Sem isso, "a execução
funciona" continua sendo uma opinião.

### Fase 2 — Nuvem (opcional, custo: ~1 dia; o NSSM local já cumpre o papel)

1. Rodar `supabase/migrations/002` e `003` no Supabase de produção (**antes** de qualquer migração de
   dados — sem elas o destino não tem as colunas de meta-labeling).
2. `--listar` → `--dry-run` → `--confirmar` do migrador, **destino limpo** (a guarda recusa destino
   populado; `--forcar` duplica e existe só para reinserção deliberada).
3. `--validar-pg` e conferir contra a origem.
4. Só então apontar um worker para `DATABASE_BACKEND=postgres` — e decidir se o worker de referência
   passa a ser o Railway ou continua o NSSM (hoje: NSSM, com o Railway como espelho).

### Fase 3 — Edge (o caminho crítico; custo: meses, honesto)

É a única fase que muda o veredito de capital, e as regras já estão escritas:

1. **Construir `research/micro_lab.py`** conforme `METODOLOGIA_MICROESTRUTURA.md` — que já está
   commitada, com hipótese, features congeladas, critério numérico de PASS/FAIL e hold-out por data.
   A matéria-prima (`trades` + `snapshots_mercado`) é exatamente o que a política de retenção manda
   arquivar antes de apagar.
2. Dado novo = período novo, coletado com o coletor determinístico. Hold-outs de trend e carry estão
   **queimados por escrito** e não voltam.
3. Custo real (0,10%/lado + slippage medido no paper de E-8) entra no critério desde o dia 0. A
   margem das hipóteses mortas foi negativa em 2,3 p.p. — o custo não é detalhe.
4. **FAIL é resultado válido e final.** Se a microestrutura reprovar, a próxima hipótese ganha nova
   METODOLOGIA_*.md antes de qualquer medição. Nenhuma variante reprovada é promovida.

### Fase 4 — O gate, na ordem em que ele está escrito

1. **Etapa 1 re-medida** com o comando congelado (B8) sobre a configuração nova — só existe Etapa 2
   depois de um PASS aqui. O relógio de paper que corre hoje valida **infraestrutura**, não
   estratégia: os trades atuais são da configuração reprovada e não contam para o gate.
2. **Etapa 2:** 90 dias corridos de paper da estratégia aprovada, ≥30 trades fechados, PF>1,3,
   PnL>0, DD≤15%, ≥ buy-and-hold — medidos por `relatorio_gate.py`, que agora imprime a fonte e
   consulta a Etapa 1 (fail-closed).
3. **Etapa 3:** capital piloto por 30 dias, com as pré-condições de conta verificadas
   (`testar_api.py` reprova chave errada) e as três chaves de ativação simultâneas:
   `DRY_RUN=false` + `ALLOW_REAL_TRADING=true` + `ENV=production` + flag `--real` + `PROCEDENCIA`
   auditada.

### Linha do tempo honesta

```
hoje ──────── Fase 0 (1 dia) ─── Fase 1 (1 sem) ─── Fase 2 (1 dia, opcional)
                                        │
                                        └── Fase 3: pesquisa de edge ── meses, SEM data garantida
                                                       │
                                              PASS na Etapa 1? ── não → próxima hipótese (volta)
                                                       │ sim
                                              Etapa 2: +90 dias corridos
                                                       │
                                              Etapa 3: +30 dias piloto
```

Cenário **otimista** (primeira hipótese passa, ~2 meses de pesquisa): capital piloto em
**fevereiro/2027**. Cenário realista: mais tarde, ou nunca — e "nunca" é o sistema funcionando,
não falhando. Um bot que não opera capital sem edge provado está se comportando exatamente como
projetado.

### O que NÃO muda, sob nenhuma pressão

- FAIL pré-registrado **nunca** é revogado, re-rodado ou renegociado.
- Hold-outs de trend e carry estão consumidos; dado novo = período novo.
- Nenhuma variante secundária vira primária; nenhum parâmetro é ajustado para um critério passar.
- Capital real exige as três chaves + `--real` + PROCEDÊNCIA — e a Etapa anterior aprovada.

---

## Riscos residuais conhecidos

1. **Telegram mudo até a Fase 0.1** — hoje um evento CRITICAL vira linha em `bot_events` e nada
   chega ao telefone. É o risco operacional nº 1 e custa 5 minutos.
2. **Máquina única** — o worker vive num PC Windows via NSSM. Queda de energia/disco é indisponível
   até intervenção manual. Mitigação natural é a Fase 2 (Railway como espelho ou primário).
3. **`main.py`/`dashboard.py` crasham em console cp1252** — padrão corrigido nos scripts, não no
   boot do worker (deliberado: mexer no boot 24/7 merece mudança própria). Sob NSSM não há console,
   então o risco é só em execução manual.
4. **`backtesting/motor.py`** segue com 8 componentes mockados — não é régua de nada. Verificado
   em 2026-08-12: tem consumidor vivo (`main.py:1588`, caminho `--backtest`) e suíte própria
   (`test_motores_aposentados.py`), então aposentá-lo é mudança de comportamento, não limpeza —
   fica para uma decisão deliberada, junto com o destino do `--backtest` da CLI.
5. **Etapa 2 "corre" com estratégia reprovada** — o relógio atual serve para provar infra
   (decisão registrada em `disciplina-pesquisa-vs-execucao`), e o relatório do gate exclui e
   reporta qualquer source secundária. O risco de auto-engano está mitigado por código, não por
   disciplina pessoal.
6. **Diretórios vazios** (`core/`, `execution/`, `infra/`, `ai/`) e os Sharpes otimistas no
   comentário de `params_pares.py` — dívida cosmética, não de risco.

---

*Gerado sobre: commit `8ae50fa`, suíte 1.696/14, flake8 0, CI 4/4, serviços BXBotWorker/BXBotDashboard RUNNING, banco vivo 375 MB (purga pendente).*
