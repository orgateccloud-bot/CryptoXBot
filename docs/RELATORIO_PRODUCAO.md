# Relatório de Produção — Mapeamento, Scorecard e Plano Final

> **Data:** 2026-08-13 (v2 — engenharia CONCLUÍDA) · **Sucede:** `RELATORIO_MODULOS.md` (07/2026, nota 2/10)
> **v2 (13/08):** entre a v1 e esta versão, TODAS as frentes de engenharia fecharam: I-10 provada
> em testnet real (50 ciclos), I-9 entregando no Telegram, I-13 completa contra PG real, 004
> aplicada, purga+vacuum executados (378→162 MB), local-only decidido com backup diário, chave
> endurecida e verificada, coleta E-11 como serviço. O documento abaixo reflete esse estado.
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
| **Operação 24/7 em paper** (o bot como serviço confiável) | ✅ **EM PRODUÇÃO, COMPLETA** | 3 serviços NSSM (worker, dashboard, coletor E-11) + backup diário verificado + Telegram entregando + chave endurecida (spot ✓ · saque ✗ · IP fixo) + cadeia de execução provada em livro real (50 ciclos testnet) |
| **Capital real ("atividade real")** | ❌ **PROIBIDO — exclusivamente por falta de edge** | Etapa 1 REPROVADA (retorno −21,25% vs +14,09% B&H); cinco hipóteses mortas com critério pré-registrado; FAIL é final. A engenharia deixou de ser desculpa E deixou de ser obstáculo: o único portão que resta é o que deve decidir |

**Nota de engenharia: 3 → 8 (v2).** Na v1 faltavam os critérios de testnet e entrega de alerta;
ambos fecharam medidos em 13/08. A auditoria de julho deu 2/10 global com três razões em série: sem
edge, réguas irreproduzíveis, e execução que falharia de forma composta na primeira ordem real. As
razões 2 e 3 foram **fechadas e provadas por teste** — substrato versionado com sha256, hold-out por
data, régua única com causalidade provada, cadeia de dia-1 fail-closed, travas que travam. A razão 1
— **não há edge** — permanece intacta, e é a única que decide capital.

**Nota de edge: 0, inalterada — mas o relógio da hipótese nova está correndo.** Metodologia
pré-registrada, `micro_lab.py` construído fail-closed, e a série do livro coletando como serviço
desde 12/08 18:07 (1.545 min × 3 pares, 99,9% de cobertura). Primeira medição de pesquisa: ~25/08.

---

## Antes → Depois (medido)

| Métrica | 07/2026 (auditoria) | 13/08/2026 | Prova |
|---|---|---|---|
| flake8 | 2.170 achados, `\|\| true` | **0, bloqueante** | CI run 4/4 verde |
| Testes | ~920 | **1.740 passed, 14 skipped** | `pytest tests/ -q` |
| bandit (código ativo) | achados abertos | **0** | `-ll` limpo |
| CI | não bloqueava nada | **3 checks obrigatórios + branch protection** | push recusado sem checks |
| Módulos nota ≤ 3 | 31 de 45 (75% do LOC) | **1** entre os re-pontuados (`backtesting/motor.py`); 8 módulos não re-auditados | mapeamento abaixo |
| Alertas Telegram entregues | 0 de 8, falha silenciosa | **ENTREGANDO** — teste ponta-a-ponta confirmado no celular do operador em 13/08 | foto do chat |
| Execução real na exchange | 0 execuções do grafo de capital | **50 ciclos em livro real** sem posição desprotegida (testnet, 44 min, ~200 ordens) | `tests/integration/` 4/4 |
| Chave de API | vazada no git + sem restrição | endurecida: spot ✓ · saque ✗ · IP fixo · 2 chaves antigas revogadas | `testar_api.py` ao vivo |
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
| 🟢 | `executor.py` | 2.160 | 3 → **7** | I-10a–h: comissão em ativo-base descontada, `abrir_long` fail-closed sem proteção, reconciliação periódica com a exchange, `fechar_posicao` atômica/idempotente, RLock + escopo de lock, reconciliação de boot. **v2: 3 → 8** — o critério testnet RODOU em 13/08: 50 ciclos em livro real sem posição desprotegida, 4/4 |
| 🟢 | `main.py` | 1.744 | 3 → **6** | `--real` com `DRY_RUN=true` **aborta o boot** (main.py:1388); invariante stop<preço<target; watchdog; reconciliação. v2: guarda cp1252 aplicada também aqui (13/08). Segue monólito — dívida de forma, não de risco |
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
| 🟢 | `testar_api.py` | 3 → **6** | v2: lia nomes de campo inexistentes (bool(None)=False) — chave com SAQUE ganharia [OK]; corrigido em 13/08 com teste de contrato dos dois lados, verificado contra a chave real |
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
| Canal de escalonamento (I-9) | 🔴 | 🟢 | **FECHADA em 2026-08-13**: token real + chat_id configurados, entrega ponta-a-ponta provada (mensagem no celular do operador), worker/dashboard reiniciados com o token carregado |
| Execução na exchange (I-10) | 🔴 | 🟢 | **FECHADA em 2026-08-13**: 4/4 contra o testnet real — 50 ciclos sem posição desprotegida (44 min, ~200 ordens reais), comissão em base → stop aceito, stop externo detectado, restart reconcilia com a exchange (1 retry por seca de liquidez do livro de teste; `abrir_long` desistiu fail-closed como projetado) |
| Sinal multi-par (E-7) | 🔴 | 🟢 | — |
| Contabilidade de paper (E-8) | 🔴 | 🟢 | — (circuito fechado; 1º trade com PnL real em 2026-08-09) |
| Substrato reproduzível (I-11) | 🔴 | 🟢 | — |
| Régua de medição (I-12) | 🔴 | 🟢 | — |
| Persistência/Postgres (I-13) | 🔴 | 🟢 | Critério de saída **CUMPRIDO em 2026-08-12**: 40/40 testes contra PostgreSQL 18 real (cluster descartável) — migrar 2× com contagem idêntica, pnl idêntico origem/destino, logger ciclo completo. Restam do operador: purga `--confirmar` e decisão sobre UNIQUE |
| ML honesto (E-10) | 🟡 | 🟡 | Base viva de cv_auc (tempo) + decisão do piso MLP 0,55 |
| Sentimento (E-9) | 🔴 | 🟢 | — |
| Qualidade/CI (M-1) | 🔴 | 🟢 | — |
| Segurança operacional | 🟡 | 🟢 | **FECHADA em 2026-08-13**: `...P9A2h` (vazada no git) e `...6kpYs` revogadas; chave nova `...A1vz7` endurecida e verificada ao vivo — spot ✓, saque ✗, IP restrito a `170.254.73.32` ✓. De brinde, o `testar_api.py` ganhou o contrato de nomes que o impedia de denunciar chave com saque |

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
| 6 | Decidir piso MLP 0,55 | — | **Evidência medida (2026-08-12, `model_metricas`)**: MLP/BTCUSDT cv_auc 0,5777 ±0,018 (antes 0,5685) — fraco, mas ACIMA do piso 0,55 cogitado; XGBoost 0,585–0,626. Com o piso em 0,55, o MLP de hoje passa. A decisão virou: aceitar 0,55, subir o piso, ou reduzir o peso |
| 7 | ~~Decidir constraints UNIQUE~~ | ✅ **004 APLICADA no código em 2026-08-12** | UNIQUE de chave natural nas 4 tabelas (zero duplicatas medidas; `trades` fora por 11.178 colisões legítimas); ON CONFLICT nos escritores; guarda do migrador **dinâmica** (consulta `pg_indexes` do destino). 40 testes contra PG 18 real. Resta rodar a 004 no Supabase junto com 002+003 (Fase 2) |

### Fase 1 — Prova de execução (custo: ~1 semana, depende de chaves testnet)

1. Criar chaves na **Binance Spot Testnet** (não são as de produção).
2. `INTEGRACAO_CICLOS=50 pytest tests/integration/ -v` — o critério de saída de I-10 que está
   escrito e nunca rodou.
3. Drill de restart: derrubar o worker com posição paper aberta, subir com
   `RECONCILIAR_BOOT_EXCHANGE=true`, verificar reconstrução. Duas vezes.

**Critério de saída:** os 50 ciclos verdes + 2 drills sem posição órfã. Sem isso, "a execução
funciona" continua sendo uma opinião.

### Fase 2 — Nuvem · **DECIDIDO em 2026-08-13: NÃO — local-only**

O operador confirmou local-only: NSSM + SQLite são a produção. O caminho
Postgres fica como **opção dormente testada** (40 testes contra PG 18 real,
migrations 001–004 prontas, migrador idempotente). A mitigação de máquina
única deixou de ser "nuvem" e virou **backup diário verificado**
(`scripts/backup_local.py`, Task Scheduler 03:30, quick_check antes de
comprimir, rotação de 7). Os passos abaixo ficam registrados apenas para o
caso de a decisão ser revertida um dia:

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

---

## O caminho finalizado para a ATIVIDADE REAL (v2 — 13/08)

Com a engenharia concluída, "o que falta para operar dinheiro de verdade" tem uma resposta exata,
curta e sem nenhum item de código:

| # | Portão | Estado | Quando |
|---|---|---|---|
| 1 | `micro_lab` — pesquisa (porção de CV) | coleta a 99,9%, medição possível com ~300 barras | **~25/08** |
| 2 | Hold-out da microestrutura (uso único) | trancado por data | abre **01/12/2026** |
| 3 | Etapa 1 re-medida (comando congelado B8) | aguarda um PASS da pesquisa | 1 dia após o PASS |
| 4 | Etapa 2 — 90 dias de paper da estratégia APROVADA | relógio só conta pós-Etapa 1 | +90 dias |
| 5 | Etapa 3 — capital piloto 30 dias | pré-condições de CONTA ✅ **já satisfeitas** (chave spot ✓ · saque ✗ · IP fixo, verificada) | +30 dias |

A ignição da atividade real continua exigindo, simultaneamente: `DRY_RUN=false` +
`ALLOW_REAL_TRADING=true` + `ENV=production` + flag `--real` + `PROCEDENCIA` auditada — e cada
etapa anterior aprovada por escrito. Nada disso se negocia, nada disso se adianta.

**O que mudou com a v2:** antes, "falta engenharia" e "falta edge" se misturavam. Agora a lista
acima é pura: se um edge passar, não existe mais nenhum trabalho técnico entre o PASS e o piloto —
só os relógios do gate. E se nenhum edge passar, o sistema seguirá operando paper indefinidamente,
que é o comportamento projetado para essa realidade.

### O que NÃO muda, sob nenhuma pressão

- FAIL pré-registrado **nunca** é revogado, re-rodado ou renegociado.
- Hold-outs de trend e carry estão consumidos; dado novo = período novo.
- Nenhuma variante secundária vira primária; nenhum parâmetro é ajustado para um critério passar.
- Capital real exige as três chaves + `--real` + PROCEDÊNCIA — e a Etapa anterior aprovada.

---

## Riscos residuais conhecidos

1. ~~**Telegram mudo**~~ — **fechado em 2026-08-13**: entrega ponta-a-ponta provada e serviços
   reiniciados com o token. A primeira entrega orgânica esperada é o relatório diário das 18h.
2. **Máquina única** — o worker vive num PC Windows via NSSM. Queda de energia/disco é indisponível
   até intervenção manual. **Mitigado em 2026-08-13** com backup diário verificado
   (`backup_local.py`: snapshot consistente + quick_check + rotação; Task Scheduler 03:30).
   Downtime continua possível; perda de dados não — e apontar `--destino` para outro disco
   físico fecha o resto.
3. ~~**`main.py`/`dashboard.py` crasham em console cp1252**~~ — **fechado em 2026-08-12**: mesma
   guarda dos scripts, aplicada após o bloco de imports dos dois; entra em vigor no próximo
   restart dos serviços.
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

---

## Diário — 2026-08-15 (v2.2): o dia em que o funil ficou visível

Um dia de duas frentes — a tela e a pesquisa — fechadas no mesmo princípio:
**o que existe num arquivo de veredito está impresso onde o operador vê.**

### Dashboard: tema RAZÃO no ar (redesign total)

- Pedido do operador ("remodele tudo, quero ver os ganhos, quero o bot
  trabalhando"). Júri de 4 direções × 3 juízes escolheu **RAZÃO — o
  livro-razão impresso** (ledger 128 · brutalist 114 · aurum 111 ·
  missioncontrol 108): a única cor da tela pertence ao DADO, a marca é
  tinta pura, SIMULAÇÃO/REPROVADA são carimbos de borracha.
- Estrutura nova em 5 blocos: RESULTADO (curva de equity `/api/equity`,
  manchete de PnL), O BOT AGORA (pipeline + diário de bordo + batimento que
  denuncia feed parado >90s), MERCADO, CAMINHO AO REAL (`/api/gates`),
  EXPEDIENTE (`/api/sistema` — serviços NSSM reais via `sc query`).
- **Funil de hipóteses** impresso abaixo da régua: 7 famílias com carimbo,
  MOMO/VOLT lidos dos vereditos REAIS de `research/vereditos/` — um futuro
  SOBREVIVE aparece em verde sem tocar no dashboard.
- Deploy completo: PR #4 mergeado, CI 4/4 verde, serviço reiniciado com
  prova de PID, vars CSS agora semânticas (`--ink/--gain/--loss`).

### Pesquisa: 3 frentes novas, 2 FAILs honestos, hold-outs intactos

- **Pré-registro ANTES de qualquer número** (METODOLOGIA_MOMO / _VOLT /
  _CARRY_V2), família congelada, custos spot, mesma data de hold-out da
  casa (2025-07-22), trava de uso único com pin de snapshot.
- **Revisão adversarial antes da medição** (2 céticos independentes) pegou
  e corrigiu com regressão: off-by-one na fatia de hold-out do VOLT,
  `max_drawdown` sem o pico inicial (DD é régua no VOLT), hold-out sem pin
  de substrato. Emendas datadas nos pré-registros.
- **MOMO (rotação BTC/ETH/SOL): FAIL** — melhor combo +48,9% a.a. /
  Sharpe 0,94 contra Sharpe 1,47 do buy-and-hold de BTC na mesma janela; a
  rotação nunca paga o custo de trocar. 0 sobreviventes em 8 trials.
- **VOLT (vol-targeting spot, teto 1,0): FAIL** — corta drawdown (até −51%
  no SOL) mas nenhum combo atinge ΔSharpe ≥ +0,20 em ≥2 de 3 ativos.
  0 sobreviventes em 6 trials.
- **CARRY v2: AGENDADA** — janela futura 15/08→15/11, medição só em 16/11
  (única revisita legítima de família com hold-out consumido).
- Hold-outs **não tocados** (a trava recusa consumir sem sobrevivente);
  trials (8+6) contados para o deflator DSR.

### Segurança (achado colateral do teste novo)

O teste hermético de `/api/gates` acusou ignição ARMADA: a cópia de `.env`
do worktree estava com as 3 flags ligadas (sobra do trabalho de testnet).
**Produção estava segura** (`DRY_RUN=true`). Cópia desarmada; o endpoint
denuncia exatamente esse estado por design — a lição "edições de credencial
falham em silêncio; sempre verificar por leitura independente" segue valendo.

### Estado ao fim do dia

| Indicador | Valor |
|---|---|
| Suíte | **1.791 passed, 14 skipped** (2min55s) |
| CI da main | verde (lint + testes + security) sobre `48128f3` |
| Funil | 5 FAIL · 1 EM COLETA · 1 AGENDADA |
| Coleta E-11 | **66/300 barras** (~24–26/dia → medição ~24–25/08) |
| Paper PnL visível | +$0,17 (1 trade, SOLUSDT, circuito fechado 09/08) |
| Capital real | segue **PROIBIDO pelo gate** — nada mudou, e é isso que o protege |

Próximos eventos, todos autônomos: vigia dispara a primeira medição da
microestrutura ao cruzar 300 barras (aviso no Telegram, com o lembrete de
que é retrato da pesquisa, não veredito); hold-out 01/12; CARRY v2 16/11.

*Gerado sobre: commit `48128f3`, suíte 1.791/14, CI 4/4, serviços
BXBotWorker/BXBotDashboard/BXBotBook RUNNING.*

---

## Diário — 2026-08-16 (v2.3): o terminal que a demo do Google não conseguiu ser

O dia começou com um zip de fora e terminou com o dashboard reestruturado —
pelo caminho, uma aula sobre a diferença entre parecer e ser.

### A análise do "projeto do Google" (cryptoxbot.zip)

- Export de app do **Google AI Studio**: dashboard-demo TypeScript/Express
  ("CryptoX Terminal Pro v3.0") gerado por LLM, semeado com docs reais do
  repo (estados heterogêneos 22/07→13/08) para parecer o projeto.
- **Sem caminho de ordem real** (verificado linha a linha: zero HMAC, zero
  `/api/v3/order`, `BINANCE_API_SECRET` jamais lido) — mas com telemetria
  **fabricada em escala**: track record hardcoded (win rate 69%, Sharpe
  2,45, +$4.175/30 dias — servido por um processo de 60s de vida), backtest
  por `Math.random()`, order book sintético alimentando o prompt do LLM
  como "dados em tempo real", "XGBoost/LSTM" = `prob ± 0.02`.
- **"Zero-Loss Guardian"**: promessa matematicamente falsa + fail-open (sem
  chave Gemini, um rule-engine raso APROVA ordens assinando como IA) + LLM
  dimensionando posição sem teto + endpoints sem auth em 0.0.0.0.
- Método: 4 leitores independentes + execução em sandbox (sem chaves,
  derrubado ao fim). Relatório completo entregue ao operador.

### O que o zip rendeu de bom (implementado com régua honesta)

- **`/api/analytics` + faixa ANÁLISE DO PERÍODO**: PnL diário × capital
  acumulado, win rate móvel com referência nos pisos PRÉ-REGISTRADOS
  (PF>1,3 da Etapa 2; equilíbrio da barreira 2:1 ~33%), PnL por hora e por
  par, ribbon com presets 7D/14D/30D. Sem dados = "—"; PF sem perdas =
  NULO (o zip devolvia sentinela 9,99).
- **Diário de produção impresso no dashboard**: `/api/diario` lê a última
  entrada DESTE arquivo e a renderiza como coluna editorial — sem cópia
  que possa divergir.

### Terminal RAZÃO (D-1..D-6) — a reestruturação

- **6 seções** (Cockpit · Análise · Caminho ao Real · Quant Lab ·
  Telemetria · Expediente) com hash routing deep-linkável, atalhos 1-6 e
  init preguiçoso de gráficos; JS extraído para `static/razao/app.js`
  (mesma origem, zero CDN).
- **Quant Lab (`/api/quant`)**: o que a demo fingia, aqui é banco — 18
  retreinos reais com AUC (XGB BTC 0,609-0,625; MLP 0,577-0,580), zero
  alertas de drift, os 14 combos dos labs com números por trial, gauges
  `exec_*` do worker (None honesto até a primeira execução) e o arquivo de
  medições (backtest por HTTP segue DESLIGADO — I-12).
- **Fita de pregão medida**: só o @aggTrade real de BTC, tx/min e vol/min
  de janela móvel de 60s, pausa com fila, limitação declarada no rótulo.
- Fora por contrato: botão de ordem (E-8e), LLM guardian, fallback que
  inventa número, CDN, gerador sintético.

### Estado ao fim do dia

| Indicador | Valor |
|---|---|
| Suíte | **1.801 passed, 14 skipped** |
| CI da main | verde sobre `b9bcb93` (deploy verificado por PID) |
| Coleta E-11 | **84/300 barras** (16:52) — medição ~24-25/08 no prazo |
| Funil | 5 FAIL · 1 EM COLETA · 1 AGENDADA (16/11) |
| Paper PnL | +$0,17 (1 trade) — impresso com carimbo SIMULAÇÃO |
| Capital real | **PROIBIDO pelo gate** — inalterado |

A frase do dia, para constar: a demo inventava 30 dias de lucro; o terminal
imprime 18 retreinos, 5 FAILs e um trade de +$0,17 — e é por isso que só um
dos dois serve para decidir alguma coisa.

*Gerado sobre: commit `b9bcb93`, suíte 1.801/14, CI 4/4, serviços
BXBotWorker/BXBotDashboard/BXBotBook RUNNING, dashboard PID 16180.*

---

## Diário — 2026-08-17/18 (v2.4): a mesa, seu batismo, e o canal que voltou dos mortos

O dashboard ganhou a primeira ESCRITA da sua história — e o primeiro comando
real dela encontrou um incidente que ninguém sabia que existia.

### MESA DE OPERAÇÕES (aba 7, commit `225e756`)

- **Arquitetura**: o dashboard grava um COMANDO auditado na tabela nova
  `comandos`; o WORKER consome (poller 10s), executa e responde
  EXECUTADO/FALHOU/REJEITADO + trilha em `bot_events/mesa_comando`. Nada
  escreve em tabela de medição — o espírito do E-8e permanece.
- **Contrato v1, papel somente POR CONSTRUÇÃO**: lista fechada (pausar,
  retomar, fechar posição paper, retreinar ML, testar Telegram); fechar
  posição RECUSA executor não-simulado; **prova estática por AST** de que o
  código da mesa não conhece DRY_RUN/ALLOW_REAL/abrir_long; fail-closed —
  sem `DASHBOARD_TOKEN` no `.env`, a mesa não existe (403).
- UI com confirmação dupla e histórico vivo; token em sessionStorage
  autentica a página inteira (gate global de Bearer ativo).
- 17 testes novos; suíte **1.817 passed, 14 skipped**.

### A saga do token (a lição de sempre, 3ª ocorrência)

Duas tentativas de configurar `DASHBOARD_TOKEN` "salvas" pelo operador —
e o `.env` de produção intocado desde 13/08 19:17 (a edição ia para outro
lugar). Resolvido eliminando o editor da equação: comando que gera, grava
por caminho absoluto e copia para o clipboard. Verificação independente
depois: linha presente, gate respondendo 401 sem Bearer.

### O batismo que pagou a mesa: Telegram morto desde 13/08

O comando #1 (`testar_telegram`) respondeu FALHOU com HTTP 404 — e o
diagnóstico revelou: o token do bot no `.env` estava **truncado** (35
chars, sem o prefixo `dígitos:`) desde a edição de 13/08 19:17. Ou seja,
**os relatórios das 18h de 14-16/08 nunca chegaram, em silêncio** — falha
de entrega só aparece quando algo tenta enviar e alguém olha a resposta.
Conserto: token completo do BotFather (46 chars) + restart do worker
DEPOIS da gravação (token vive na memória do processo — a ordem importa).
Prova: `getMe` ok, envio direto entregue, e o comando #3 da mesa fechando
o circuito completo dashboard → banco → worker → celular: **EXECUTADO,
"entregue"**.

### Bateria completa (comandos #4-#7)

| # | Comando | Resposta do worker |
|---|---|---|
| 4 | pausar_bot | EXECUTADO — avaliações suspensas; proteções seguem |
| 5 | fechar_posicao_paper BTC | **FALHOU — "sem posição aberta"** (o "não" honesto) |
| 6 | retreinar_ml | EXECUTADO — retreino real disparado (→ model_metricas) |
| 7 | retomar_bot | EXECUTADO — avaliações retomadas |

7 comandos na vida da mesa: 4 EXECUTADO, 3 FALHOU com motivo verdadeiro,
0 REJEITADO. Auditoria íntegra em bot_events.

### Estado

| Indicador | Valor |
|---|---|
| Suíte | **1.817 passed, 14 skipped** |
| Coleta E-11 | **125/300 barras** (18/08 09:47) — medição ~24-25/08 |
| Telegram | **restaurado** — relatório das 18h volta a chegar |
| Funil | 5 FAIL · 1 EM COLETA · 1 AGENDADA (16/11) |
| Capital real | **PROIBIDO pelo gate** — a mesa não tem, por construção, como mudar isso |

O terminal fecha seu ciclo: vê, entende e AGE — no papel, com trilha. A
porta do capital real continua onde sempre esteve: atrás do primeiro
SOBREVIVE do funil.

*Gerado sobre: commit `225e756`, suíte 1.817/14, serviços worker/dashboard/
book RUNNING, mesa armada com 7 comandos auditados.*

## Diário — 2026-08-18 (v2.5): o dia em que a tela parou de mentir

Seis commits, um tema só: a diferença entre o que o sistema SABE e o que a
tela AFIRMA. Três mentiras de superfície morreram hoje — uma delas no ar
desde o deploy do terminal.

### O screenshot que valia mil textContent (`4319065`)

"Mostra a mesa completa" expôs o constrangedor: o corpo do terminal estava
**invisível em produção desde b9bcb93**. `ativarAba` setava `display=''`
na seção ativa, devolvendo a decisão à regra CSS `.aba-secao{display:none}`.
Toda verificação anterior lia `textContent` e APIs — cegas ao display
COMPUTADO. O primeiro screenshot real da UI pegou a página em branco em
10 segundos. Conserto de uma linha; a lição vale mais: **prova de UI é
`getComputedStyle` + pixel, não texto no DOM.**

### 401 nunca mais vira dado (`65eb901` + `87ded7a`)

O operador abriu o modal "Conexão — Binance" numa aba sem token e leu:
timeout nos dois REST, chave "Não configurada", DRY RUN "Desativado" EM
VERDE. Tudo falso — era o corpo `{"erro"}` do 401 pintado como diagnóstico.
A varredura ultracode (22 call sites) achou **mais 8 renderizadores
mentindo**: `/api/risco` imprimia "OPERANDO/OK" sem leitura, `/api/lucro`
"+$0,00", o Quant Lab atestava "nenhum alerta de drift", e dois caches
envenenados adiavam a mentira. Conserto arquitetural, não pontual: o
wrapper de fetch **rejeita a promise** em 401 (e não-2xx de GET — o 429 do
próprio rate limiter também não é dado). Nenhum renderizador, atual ou
futuro, volta a receber erro como medição; um chip único explica a trava e
distingue **sem token** de **token recusado**. A revisão adversarial ainda
pegou: a mesa ficava presa em "desarmada" após armar com histórico vazio
(200-vazio agora limpa), e placeholders do template afirmando "+$0.00"/"0"
antes de qualquer leitura (agora "—").

### O relatório que voltou dos mortos — e mentiu com boas maneiras (`770566a`)

18h em ponto: o primeiro relatório automático pós-ressurreição chegou ao
celular. Vitória — com três vícios: "Win Rate: 0.0%" com 0 trades (0/0
impresso como derrota total), "Saldo Atual: $0.00" (fonte que devolve 0.0
tanto para conta zerada quanto para leitura falha), e a marca fantasma
"BotBinance". E um furo de escopo: o relatório era só de `pares[0]` — um
trade de ETH/SOL não contaria no "Trades: 0" rotulado como global. Agora:
win rate `None` → "—", saldo via `binance_conta.saldo()` com "sem leitura"
em erro, CryptoXbot com "Paper trading" declarado, e agregação dos 3 pares.

### Hermeticidade: as 46 falhas que o CI nunca veria (`08b78e2`)

Rodar a suíte com `DASHBOARD_TOKEN` no `.env` derrubava TODOS os testes de
API por 401 — e o CI, sem `.env`, nunca saberia. O conftest da raiz agora
fixa `DASHBOARD_TOKEN=""` antes de qualquer import. Suíte: **1.819 passed,
14 skipped**.

### Identidade (`9e22994`)

Logo oficial do operador (robô cobre sobre o X, ₿ azul) no masthead e como
favicon — que o terminal nunca teve. Os bytes vieram de onde ninguém
procura: extraídos em base64 do transcript da própria sessão; fundo removido
por flood-fill a partir das bordas. Exceção deliberada e comentada à
doutrina "marca é tinta pura" — a cor de DADO segue exclusiva do verde/
vermelho.

### A mesa nas mãos de quem manda

O operador armou a mesa no próprio navegador e operou sem intermediário:
#8/#9 `retomar_bot`, #10 `testar_telegram` → **"entregue"**, #11
`retreinar_ml` → ciclo completo medido em `model_metricas` (XGB BTC 0.624 ·
ETH 0.609 · SOL 0.602 · MLP 0.577, 19:33–20:57). AUCs idênticos aos da
manhã — treino determinístico sobre a mesma janela diária — e nenhum
alerta de drift. 11 comandos na vida da mesa, todos com resposta auditada.

### Estado

| Indicador | Valor |
|---|---|
| Suíte | **1.819 passed, 14 skipped** |
| Deploys de hoje | 6 commits, CI verde em todos |
| Telegram | restaurado E honesto (próximo teste real: 19/08 18h) |
| Mesa | 11 comandos auditados; operador autônomo |
| Coleta E-11 | em curso — medição ~24-25/08 |
| Capital real | **PROIBIDO pelo gate** — inalterado |

A régua RAZÃO agora vale para o canal inteiro: banco, API, transporte e
tela. Se um número aparece, foi medido; se não foi medido, aparece "—" com
o motivo. O funil segue sendo o único caminho ao real.

*Gerado sobre: commit `9e22994`, suíte 1.819/14, serviços worker (PID
30348) / dashboard (PID 27120) RUNNING, mesa com 11 comandos auditados.*

## Diário — 2026-08-19 (v2.6): o alarme na medida certa e a régua virada para dentro

O dia começou com a madrugada prestando contas e terminou com o sistema
auditando a si mesmo — 3 commits, todos com CI verde, e dois consertos
grandes nascendo como sessões paralelas.

### A madrugada que se curou sozinha — e o alarme que mentiu sobre ela

Rede instável entre 23:47 e 07:44: os dois WebSockets caíram às 04:43
(CRITICAL após 5 falhas) e **reconectaram sozinhos às 04:57** (9
tentativas); 9 avisos de saldo ilegível (timeouts + drift `-1021`
re-sincronizado). Zero ação manual — o retry/backoff e o watchdog pagaram
o que prometiam. Mas o print do operador pegou a mentira: o Telegram das
04:43 dizia *"O bot foi pausado. Revise manualmente antes de reativar"* —
falso nas duas metades (nada pausou; nada havia a reativar) — enquanto o
bot_event do mesmo minuto dizia a verdade ("segue avaliando"). E o
tudo-limpo das 04:57 nunca chegou: o vigia só encaminha CRITICAL.

### O conserto (`dd96e3a`): rodapé por chamador, jamais genérico

`alerta_circuit_breaker` tinha o rodapé CRAVADO no template, reusado por 5
chamadores com 5 estados diferentes. Agora o rodapé é **parâmetro
obrigatório** — rodapé genérico é como essa classe de mentira nasce — e
cada situação diz a sua verdade: trava permanente exige destravar manual
(o único caso em que "manual" é verdade), bloqueio diário reseta sozinho,
volatilidade reavalia a cada ciclo, proteção-não-entrou distingue "sem
risco, bot segue" de "POSIÇÃO DESCOBERTA — intervenção URGENTE". WS caído
deixou de fingir ser circuit breaker: mensagem própria ("O bot NÃO
pausou... reconexão automática") e **aviso de recuperação** no Telegram.
Prova estática de que o rodapé genérico não pode voltar.

### Rotação de token — a lição das 3 ocorrências, enfim medida

"Gere o token": rotação completa com cada elo provado — token novo no
`.env` e, ANTES do restart, **HTTP 401 para o token novo** (o velho vivia
na memória do processo — a lição virou número, não anedota); restart PID
27120→24400; prova tripla (novo 200 · inválido 401 · ausente 401). O
percalço do meio também virou dado: a colagem sem Tab não gravou, e foi o
**log do servidor** que diagnosticou (rajada 401 às 18:15) e confirmou a
vitória (poll da mesa em 200 na cadência de 10s às 18:40:56).

### 18h: o primeiro relatório honesto ENTREGUE

Chegou no formato novo — CryptoXbot, 3 pares agregados, win rate "—"
quando não há trades, saldo rotulado. O ciclo completo print-do-operador →
conserto → entrega automática fechou em 24h.

### P2-4 fechado com número (`44b7a40`)

A "verificação pendente" pedia query no Supabase — obsoleta desde o
local-only. Medido na produção real: schema pós-P1-3 ok, 5.355 sinais,
**1 rotulado** vs piso de 200-500. Deferido sem atalho: no ritmo do paper,
o piso está a anos; só re-medir quando o funil mudar de estado. **Com isso
o backlog de engenharia do plano de modernização zerou.**

### A régua virada para dentro (`61220ba`)

Scorecard de todos os módulos: 10 auditores paralelos, 234 leituras de
código real, 4 lentes. **Engenharia em paper: ≈7.7/10** (resolução ±1) —
régua explicitamente distinta da de capital real, onde o Portão 1 (edge)
segue FAIL e nada mudou. Os quatro vermelhos: freio de drawdown "total"
que **não acumula entre dias** (o cenário motivador do I-8 não trava),
lstm_modelo com rótulo pré-E-10 e zero testes, CVD provadamente inerte
(honesto: pré-registrado), fear_greed fail-open fabricando 50. O
instrumento ainda pegou um **teste vermelho mascarado por ordem de
execução** (mock da função aposentada + debounce global de outro arquivo)
— consertado no mesmo commit. Os dois piores achados viraram **sessões de
trabalho paralelas**, iniciadas pelo operador no mesmo dia.

### Estado

| Indicador | Valor |
|---|---|
| Commits do dia | 3 (`dd96e3a` alertas · `44b7a40` P2-4 · `61220ba` scorecard) — CI verde em todos |
| Suíte | completa verde (exit 0) pós-conserto do teste mascarado |
| Scorecard engenharia | ≈7.7/10 · capital real: inalterado (gate FAIL) |
| Coleta E-11 | **147/300 barras** — medição ~25-26/08 |
| Telegram | honesto e entregue (18h de hoje foi a prova viva) |
| Em obra paralela | freio de drawdown acumulado · lstm rótulo E-10 |
| Capital real | **PROIBIDO pelo gate** — inalterado |

A doutrina completou o circuito: banco → API → transporte → tela → alerta
→ e agora a régua apontada para o próprio código, com os achados virando
obra no mesmo dia. O funil segue sendo o único caminho ao real.

*Gerado sobre: commit `61220ba`, CI 3/3 verde, serviços RUNNING (worker
24656 · dashboard 24400 · book 4524), duas sessões paralelas em curso.*
