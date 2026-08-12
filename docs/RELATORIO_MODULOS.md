# Relatório de módulos e plano de combate — CryptoXbot

> 2026-08-07 · auditoria multiagente: 15 lotes mapeados por um agente
> cada e **refutados adversarialmente** por um cético independente, consolidados num
> scorecard calibrado entre si. Toda afirmação tem evidência `arquivo:linha`.

## Veredicto

**Nota global 2/10 — não apto a capital real.**

NÃO APTO A OPERAR COM CAPITAL REAL — e a distância não é de polimento, é de fundamento. O sistema roda 24/7 de forma estável em paper, com ilhas de engenharia genuinamente boa (validacao.py, backtesting/metricas.py, binance_conta.py, data/klines.py, o retry dos WebSockets, o conftest anti-produção). Nada disso muda o veredito, por três razões em série. PRIMEIRA: não há edge. Cinco hipóteses reprovadas com critério pré-registrado, Etapa 1 do gate reprovada em 4 de 5 critérios com retorno de −21,25%, e os hold-outs que restariam estão queimados ou contaminados por deriva de fronteira (43,7% do hold-out atual do edge_lab já foi porção de pesquisa). SEGUNDA: as réguas que deveriam sustentar esse FAIL não são reproduzíveis nem íntegras — a tabela de klines é rolante e não versionada, o input do fix B6 do walk_forward está ausente da árvore, o motor servido no dashboard tem look-ahead e taxa de futures, e os parâmetros vivos saíram de um grid de 8.000 combinações ordenado por um Sharpe cego a custo. Um FAIL que ninguém consegue re-derivar cede na primeira dúvida. TERCEIRA: se alguém ignorar os dois primeiros pontos e ligar ALLOW_REAL_TRADING, a primeira ordem real falha de forma composta e previsível — comissão em ativo-base não descontada faz o stop na exchange ser rejeitado, abrir_long não verifica esse retorno e devolve True com posição real desprotegida, nada no loop pergunta à exchange se o stop executou, não existe kill-switch em lugar nenhum do repositório, e o único canal de escalonamento entrega 0 de 8 alertas por causa de um placeholder truthy. Os freios que existiriam também não freiam: MAX_DRAWDOWN_TOTAL nunca é comparado, o bloqueio diário se auto-revoga à meia-noite, Kelly é fail-open com win-rate 0 e o gate de CVaR é inalcançável porque 0 dos 5.255 sinais tem PnL. Some-se que o próprio DRY_RUN, documentado no CLAUDE.md como uma das três condições para ordem real, não é lido por nenhum caminho de execução — quem o ligar para frear continuará enviando ordens, com o painel exibindo SIMULAÇÃO. Ordem mínima de trabalho antes de qualquer conversa sobre capital: (1) tornar os cinco FAILs reproduzíveis — versionar o substrato de dados, fixar hold-out por DATA e não por fração, commitar o funding, dar entrypoint às funções que produziram os vereditos; (2) fechar a cadeia de dia-1 do executor (comissão, verificação de proteção, consulta de stop na exchange, kill-switch) e cobrir com integração contra testnet; (3) ligar o Telegram e provar entrega ponta a ponta, mais um leitor real para bot_events; (4) corrigir o BTC-only estrutural (suporte, regime, ml_filtro/ensemble) que hoje faz 2 dos 3 pares operarem com sinal de outro ativo e stop 34x acima da entrada. Enquanto os itens 1 e 2 não estiverem feitos, a decisão correta é manter paper — e o mais valioso do repositório hoje não é nenhum módulo, é o conjunto de FAILs pré-registrados que ainda impede o capital de entrar.

## Panorama

| | |
|---|---|
| Módulos auditados | 45 |
| LOC total | 16.517 |
| Módulos com nota ≤ 3 | **31** de 45 |
| LOC em módulos nota ≤ 3 | 12.400 — 75% do código |
| Módulos com nota ≥ 7 | 2 |

| Camada | Nota média | Módulos |
|---|---:|---:|
| Superfícies | 2.3 | 3 |
| Configuração | 2.5 | 4 |
| Sinal | 2.8 | 9 |
| Execução | 3.0 | 2 |
| Ferramentas e periferia | 3.1 | 7 |
| Pesquisa e backtest | 3.2 | 12 |
| Dados e persistência | 3.5 | 6 |
| Risco | 4.0 | 2 |

## Precisão do instrumento (leia antes de citar uma nota)

A auditoria foi executada **duas vezes** — a primeira teve o agente de
planejamento interrompido e foi reexecutada. Isso rendeu, sem querer, uma
medida da reprodutibilidade do próprio scorecard:

- **28 de 45 módulos (62%) receberam nota idêntica** nas duas execuções
- 17 divergiram: 16 por ±1 e 1 por ±2
- Desvio médio sobre os 45: **0,40 ponto**
- **Nota global idêntica (2) nas duas**, e a contagem de módulos nota ≤ 3 praticamente igual (32 e 31)

A conclusão prática: **a resolução deste instrumento é ±1 ponto por módulo.**
Uma diferença de 3 para 4 entre dois módulos não é significativa; uma de 2 para 7
é. Os agregados — nota global, ranking de camadas, quais módulos estão no vermelho
— são estáveis. Este documento usa a segunda execução, que é a que gerou o plano.

<details><summary>As 17 divergências</summary>

| Módulo | Run 1 | Run 2 | Δ |
|---|---:|---:|---:|
| `backtesting/motor_ensemble.py` | 3 | 1 | −2 |
| `backtesting/coletar_dados.py` | 3 | 4 | +1 |
| `backtesting/motor_vectorbt.py` | 1 | 2 | +1 |
| `backtesting/otimizador.py` | 1 | 2 | +1 |
| `backtesting/trend_following.py` | 5 | 4 | −1 |
| `config/params_pares.py` | 3 | 2 | −1 |
| `config/settings.py` | 2 | 1 | −1 |
| `database.py` | 4 | 3 | −1 |
| `ensemble.py` | 2 | 3 | +1 |
| `estrategias/trend_live.py` | 2 | 3 | +1 |
| `fear_greed.py` | 3 | 2 | −1 |
| `logger.py` | 2 | 3 | +1 |
| `monitor_fluxo.py` | 2 | 3 | +1 |
| `regime.py` | 3 | 4 | +1 |
| `research/edge_lab.py` | 5 | 4 | −1 |
| `testar_api.py` | 2 | 3 | +1 |
| `validacao.py` | 6 | 7 | +1 |

</details>

## Como as notas foram calibradas

CALIBRAÇÃO ENTRE MÓDULOS. A escala mede aptidão para operar com DINHEIRO REAL, não elegância nem cobertura. Âncoras usadas para tornar um 7 comparável entre camadas: 7-8 = correto, coberto por teste que falharia se quebrasse, e com consumidor real (validacao.py, backtesting/metricas.py); 5-6 = executa em produção, contrato de erro explícito, defeitos conhecidos e contidos (binance_conta.py, data/klines.py); 4 = executa e é útil, mas tem um defeito estrutural que invalida parte da sua saída ou um modo de falha silencioso não coberto (score.py, walk_forward.py, indicadores.py, analise_mercado.py); 3 = o caminho que roda funciona, o caminho que vai receber capital tem defeito de dia-1 e zero execuções (executor.py, main.py, risco.py, database.py); 2 = executa e produz número/decisão errada, ou é código completo que nunca executou (dashboard.py, otimizada.py, suporte.py, telegram_bot.py, ollama_client.py); 1 = ativamente enganoso — serve um resultado falsamente positivo, ou é morto com defeito que invalida seu próprio propósito (motor_ensemble.py, motor.py, motor_otimizado.py, config/settings.py). Duas regras de desempate aplicadas uniformemente: (a) 'existe e ninguém chama' derruba a nota independentemente da qualidade do código — foi o que separou ollama_client.py (2) de binance_conta.py (5), apesar de ambos serem bem escritos; (b) código atrás de `if simulacao` / flag default-off não conta como executado — foi o que impediu executor.py de passar de 3 mesmo com ~150 testes.

POR QUE A MÉDIA ARITMÉTICA ENGANA. A média dos 45 dá ~2,9, o que sugeriria 'sistema mediano com pontos a melhorar'. Prontidão para operar não é média, é CONJUNÇÃO em série: o capital só sobrevive se TODAS as camadas segurarem simultaneamente, e a probabilidade composta é dominada pelo pior elo de cada camada, não pela contribuição média. Ponderei em quatro portões multiplicativos, do mais eliminatório para o menos:

PORTÃO 1 — EDGE (multiplicador ~0). Cinco hipóteses reprovadas com critério pré-registrado, Etapa 1 do gate REPROVADA em 4 de 5 critérios (retorno −21,25%), e os dois hold-outs que restariam estão comprometidos: o de trend/carry queimado, e o temporal do edge_lab contaminado em 43,7% por deriva da fronteira (fração de tabela mutável). Sem edge, a qualidade de execução só determina a velocidade da perda. Nenhuma nota alta em qualquer outra camada compensa isto — por isso a nota global não pode passar de 2, ainda que o executor fosse 9.

PORTÃO 2 — INTEGRIDADE DA MEDIÇÃO (o que deveria estar segurando o portão 1). As três réguas que decidem se há edge estão comprometidas de formas independentes: otimizador.py ordena por Sharpe bruto de taxa e sizing, sobre 8.000 trials sem out-of-sample, e é a ORIGEM dos parâmetros vivos; walk_forward declara corrigido um bloqueio de F&G que o código não implementa e depende de um input ausente da árvore; motor_ensemble serve um resultado positivo por HTTP com look-ahead, taxa de futures e Sharpe inflado. Agravante estrutural: os vereditos não são reproduzíveis porque a tabela klines é rolante e não versionada, e já mudou de intervalo entre a medição oficial e hoje. Uma medição irreprodutível não protege — ela cede na primeira pressão.

PORTÃO 3 — RISCO E EXECUÇÃO (o que limitaria a perda se o portão 1 fosse ignorado). risco.py: MAX_DRAWDOWN_TOTAL nunca é comparado, não há kill-switch, o único bloqueio se auto-revoga à meia-noite, Kelly é fail-open e o gate de CVaR é inalcançável (0 de 5.255 sinais fechados). executor.py: comissão em ativo-base não descontada + abrir_long que não verifica se a proteção foi colocada = posição real desprotegida no primeiro trade, sem nada perguntando à exchange e sem canal de alerta (0 de 8 alertas entregues, comprovado). Estes dois compõem: o freio não freia E a proteção não é verificada.

PORTÃO 4 — OBSERVABILIDADE (o que permitiria descobrir os três acima). bot_events tem 14 writers e zero SELECT; /metrics não tem scraper; /ready não tem probe; o relatório diário reporta zeros fabricados; Telegram entrega nada. Não há caminho por onde uma falha dos portões 1-3 chegue a um humano.

CONCLUSÃO DA PONDERAÇÃO: nota_global 2 = 'o sistema roda 24/7 de forma estável em paper e tem ilhas de engenharia genuinamente boa (validacao, metricas, binance_conta, klines, os fixes de WS e o conftest), mas nenhuma configuração atual está apta a receber capital'. Não é 1 porque a infraestrutura de execução é estável e existem travas reais que hoje impedem o pior (--simulacao no unit file, ALLOW_REAL_TRADING=False, chave read-only, SystemExit do modo trend). Não é 3 porque três dos quatro portões estão abertos ao mesmo tempo e o quarto impede a descoberta disso. Registro dois vieses da própria auditoria: (i) o inventário de partida estava errado ao listar trend_following.py e trend_live.py como sem teste — ambos têm suíte; (ii) várias fichas superestimaram magnitudes numéricas (o look-ahead de ~48h vale para BTCUSDT, mas é de ~4,5h para ETH/SOL, que são 2 dos 3 conjuntos de parâmetros vivos), e os defeitos estruturais foram mantidos onde as magnitudes caíram.

## Os piores

- config/settings.py (1) — untracked, sem teste, credencial em texto plano cuja key está commitada em dashboard.py:665, e fallback com precedência que migraria o bot inteiro para Futures em silêncio
- backtesting/motor_ensemble.py (1) — serve por HTTP 24/7 um resultado falsamente positivo (+2,54%) com look-ahead, taxa de futures e Sharpe inflado; corrigir só o look-ahead o leva a −31%
- backtesting/motor.py (1) — 9 de 11 componentes do score são constantes mockadas; o harness não executa a estratégia que diz medir
- backtesting/motor_otimizado.py (1) — código morto confirmado cujo único propósito (medir contribuição por filtro) é destruído por look-ahead no filtro que é gate obrigatório
- dashboard.py (2) — console de operação que escreve no banco que o bot lê, trava 2 de 3 pares em AGUARDAR para sempre e não tem nenhuma leitura de posição nem kill-switch
- estrategias/otimizada.py (2) — importa suporte BTC-only e produz stop 34x acima da entrada em ETH/SOL, sem validação em nenhuma das 4 camadas a jusante
- suporte.py (2) — SYMBOL hardcoded sem escapatória na API (causa raiz do stop absurdo) e ScaleIn que nunca passa da parcela 1 e reaproveita estado entre trades
- telegram_bot.py (2) — único canal de escalonamento do sistema, 10 call sites reais, 0 de 8 alertas entregues e falha 100% silenciosa
- backtesting/otimizador.py (2) — origem dos parâmetros vivos: grid de 8.000 sem out-of-sample, ordenado por Sharpe cego a custo e enviesado para amostras de 5 trades
- config/params_pares.py (2) — a chave principal (stop_pct) não dimensiona posição em par nenhum, e os Sharpes reportados contradizem a Etapa 1 reprovada
- data/cvd_calculator.py (2) — o componente CVD do score é matematicamente incapaz de sair de 50/51 para qualquer fluxo de ordens existente
- fear_greed.py (2) — fail-open que pontua o MÁXIMO, desliga os dois bloqueios de sentimento e é absolutamente mudo; zero testes reais
- lstm_modelo.py (2) — cv_auc 0,568 (ruído) carregando 45% do ensemble, BTC-only, promovido sem gate e sem um único arquivo de teste
- ai/ollama_client.py (2) — 312 LOC sem nenhum importador de produção, documentadas como '🟢 Alta'
- backtesting/motor_vectorbt.py (2) — 391 LOC que nunca importaram neste ambiente, com quebra de paridade de sizing que o teste é estruturalmente incapaz de detectar
- research/carry_lab.py (2) — não executa (tabela funding inexistente em todas as cópias do banco); o FAIL de carry não tem substrato sobrevivente

## Os melhores

- backtesting/metricas.py (7) — aritmética correta, guardas em todo denominador, ~40 testes incluindo propriedades (equivariância, monotonia); único senão é que o consumidor de produção está curto-circuitado
- validacao.py (7) — purge/embargo aritmeticamente corretos contra o rótulo real, 18 testes de borda, zero I/O e zero estado; falha só na calibragem do detector de drift
- binance_conta.py (5) — contrato de erro explícito, offset de relógio que não se auto-zera, tratamento de -1021, 302 linhas de teste com casos negativos reais
- data/klines.py (5) — lock único correto, chave normalizada, TTL, fallback stale testado; consolidou 6 fetchers duplicados de verdade
- backtesting/walk_forward.py (4) — única régua com causalidade MTF corrigida e provada por teste de propriedade, contiguidade abortando com SystemExit e censura final; produziu o FAIL que hoje trava o capital
- research/edge_lab.py (4) — fronteira anti-vazamento provada (alterar só o hold-out não move nenhum IC), permutação por rotação circular semeada, testes de calibração sob null autocorrelacionado

## Scorecard por módulo

### Execução

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `executor.py` | 1506 | **3** | infra | Cadeia de dia-1 sem rede de captura: comissão em ativo-base não descontada (executor.py:791-793) → _colocar_stop_exchange recebe -2010 → retorna None (473-474) → abrir_long NÃO checa (810 vs 889) → retorna True com posição real DESPROTEGIDA; nada consulta a exchange no loop (_status_ordem só em 677 e 1279), o `break` de 1126 mata o monitor no 1º SELL rejeitado e não há kill-switch no repo. |
| 🔴 | `main.py` | 1422 | **3** | infra | Nenhuma invariante stop<preco<target antes de abrir_long (main.py:1084-1167), enquanto o caminho trend valida em 909-911; combinado ao scale-in que avança na FALHA e dimensiona parcelas 2/3 sobre o tamanho_total de um trade anterior (suporte.py:296,305), o sizing efetivo é aleatório em 3 de 4 entradas. |

### Risco

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `risco.py` | 756 | **3** | infra | MAX_DRAWDOWN_TOTAL (risco.py:35) nunca é comparado — só aparece no payload de display (:734) — e não há kill-switch: o único bloqueio (drawdown diário) se auto-revoga na virada do dia (:117-123). O bot pode perder 5% por dia indefinidamente e religar sozinho. |
| 🟡 | `binance_conta.py` | 222 | **5** | infra | restricoes_chave() — a única função do repo que responde 'a chave pode mandar ordem / pode sacar?' — não é chamada em produção (só no __main__), apesar de existir gancho de boot pronto em main.py:1263-1283; e chave_configurada() NÃO detecta o par commitado em config/settings.py:44-45. |

### Sinal

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `suporte.py` | 428 | **2** | edge | SYMBOL='BTCUSDT' (suporte.py:29) sem escapatória na API (detectar_suportes só aceita intervalo, :104) é a causa raiz do stop 34x acima da entrada em ETH/SOL; e ScaleIn nunca passa da parcela 1 (main.py:1084 exige `not exec_par.posicao`) e nada reseta o objeto no fechamento. |
| 🔴 | `estrategias/otimizada.py` | 358 | **2** | edge | otimizada.py:193 chama sup.detectar_suportes('1h') sem symbol (a função nem aceita) e :207-210 sobrescreve o stop pelo suporte de BTC — reproduz o bloco de produção ETHUSDT com stop $63.521 sobre entrada ~$1.858; nenhuma das 4 camadas a jusante valida stop<preco. |
| 🔴 | `lstm_modelo.py` | 302 | **2** | edge | Dentro de UMA chamada de prever(), a janela do VWAP cumulativo varia de 77 a 100 velas entre os 24 níveis (lstm_modelo.py:256-273): 47 das 517 entradas medem um artefato de denominador, e o StandardScaler salvo (mean_[6]=0.2057) fossiliza uma distribuição que a produção nunca visita. |
| 🔴 | `fear_greed.py` | 186 | **2** | infra | Caminho de exceção (fear_greed.py:126-137) é alcançável por um blip de rede (sem retry, sem backoff, sem cache de falha), devolve valor=50 → score 100/100, desliga os dois bloqueios de sentimento e é absolutamente mudo (o arquivo não importa logging, logger nem health). Zero testes tocam o módulo de verdade. |
| 🔴 | `ml_filtro.py` | 354 | **3** | edge | Train/serve skew em dist_vwap (2ª feature por importância): ind.vwap é cumulativo desde o índice 0 e o treino passa ~17.500 velas contra 100 na inferência (ml_filtro.py:72 vs :303) — mesma barra, +0.4916 no treino e +0.0041 em produção, com sinal invertido em parte da série. |
| 🔴 | `ensemble.py` | 220 | **3** | infra | prever() não aceita symbol (:44) e o guard `hasattr(ens_mod,'symbol')` de main.py:1014 é permanentemente False — os 3 pares recebem a probabilidade e o regime do BTCUSDT; pode_operar/confiança são calculados e nunca gateiam compra, e o chamador é fail-open (otimizada.py:116-120 entrega 10 pontos de graça). |
| 🔴 | `estrategias/trend_live.py` | 123 | **3** | edge | O call site está atrás de MODO_TREND=False (main.py:87), que só liga com --modo-trend — flag ausente do ExecStart do BXBotWorker; nem o dry run de validação de EXECUÇÃO, única justificativa declarada do módulo, está coletando telemetria. |
| 🟡 | `score.py` | 502 | **4** | edge | Os bloqueios absolutos usam limiares divergentes da fonte do dado (score.py:372-375 <=20/>80 contra fear_greed.py:31-32 <=24/>74) e o fallback offline do F&G pontua 100, único componente cujo modo-falha PREMIA e ainda desliga os dois vetos de sentimento. |
| 🟡 | `regime.py` | 265 | **4** | edge | SYMBOL='BTCUSDT' é constante de módulo (regime.py:23) e detectar() não aceita symbol (:151): o componente de maior peso macro (18%) de ETH e SOL é uma leitura do Bitcoin — e com limite=60 velas a 'EMA50' carrega 67% de seed SMA, divergindo do backtest que calibrou os parâmetros. |

### Dados e persistência

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `data/cvd_calculator.py` | 104 | **2** | edge | Com window_size=50 (o valor de produção), \|tanh(slope/std)\| <= 1/std(arange(50)) = 0.0692 < limiar 0.1 de score.py:147 — cvd_trend é SEMPRE 0 e _score_cvd só pode devolver 50 ou 51 para qualquer fluxo de ordens que exista; o bônus documentado de '+20 pts' tem teto real de +1. |
| 🔴 | `database.py` | 1027 | **3** | infra | salvar_sinal (database.py:581) e historico_cv_auc_modelo (:990) indexam por inteiro linhas que o pool entrega como dict (row_factory=dict_row, :95) → KeyError em toda chamada sob Postgres; e as 3 funções de crash recovery (:860,:865,:879) não têm nenhum teste direto. |
| 🔴 | `logger.py` | 537 | **3** | infra | O relatório diário das 18h lê por LIKE 'YYYY-MM-DD%' um timestamp que otimizada.py:222 grava como 'dd/mm/aaaa' — a query real devolve (0,None,None) e logger.py:496-502 converte None em 0.0, produzindo um alerta que mente na direção tranquilizadora todo dia. |
| 🟡 | `indicadores.py` | 235 | **4** | edge | Backtest e vivo usam DEFINIÇÕES diferentes de VWAP (vwap_rolling(20) em otimizador/walk_forward contra ind.vwap cumulativo em otimizada.py:97) e ema(f[-20:],20) degenera em SMA — os parâmetros de params_pares foram calibrados sobre indicadores que produção não calcula. |
| 🟡 | `backtesting/coletar_dados.py` | 193 | **4** | infra | Janela rolante ancorada em datetime.now() sem --inicio/--fim (:60-61) é a causa-raiz da irreprodutibilidade de TODOS os vereditos de pesquisa do repo; e a última vela baixada é a vela ainda aberta, congelada para sempre pelo INSERT OR IGNORE (2 linhas medidas, uma dentro do hold-out atual). |
| 🟡 | `data/klines.py` | 91 | **5** | infra | Fallback stale sem teto de idade e sem flag (:91), somado à ausência de raise_for_status (:46-60, então 429/418 vira ValueError silencioso) e a zero logging no módulo inteiro — o consumidor não tem como distinguir dado fresco de dado de uma hora atrás. |

### Superfícies

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `dashboard.py` | 801 | **2** | infra | cvd=0.0 fixo para ETH/SOL (dashboard.py:317 → otimizada.py:136,186) reprova COMPRA e VENDA ao mesmo tempo, travando 2/3 dos pares em AGUARDAR para sempre; e dashboard.py:457 grava em `trades` sem trade_id, burlando a dedupe parcial de database.py:439/449. |
| 🔴 | `telegram_bot.py` | 166 | **2** | infra | A guarda de :31 testa vazio em vez de validade e o .env traz 'your_telegram_bot_token_here' (truthy): o POST vai para uma URL inválida, a Telegram responde 404 e :45 devolve False sem print, log ou bot_event — inclusive para 'URGENTE — POSIÇÃO SEM REGISTRO NO BANCO'. O detector correto já existe em binance_conta.py:43-44 e não foi aplicado. |
| 🔴 | `health.py` | 236 | **3** | infra | Bind 0.0.0.0 fixo, sem auth e sem variável para mudar (health.py:234), publicando pnl_dia, drawdown_dia_pct, ml_prob e desvios de execução para qualquer dispositivo da LAN — política oposta à que o dashboard já adotou. |

### Pesquisa e backtest

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `backtesting/motor_ensemble.py` | 697 | **1** | edge | Três defeitos de régua já diagnosticados por escrito no arquivo vizinho (walk_forward.py:17-41) e nunca propagados: idx4=i//4 lê candle 4h ainda aberto em 100% das barras, TAXA=0.0004 é tarifa de futures num bot SPOT, e Sharpe é anualizado a 252 sobre retorno bruto por trade — e é esse Sharpe que aprova um dos 5 critérios do veredito. |
| 🔴 | `backtesting/motor.py` | 407 | **1** | edge | 9 dos 11 componentes do score são constantes: 8 mocks hardcoded anotados `# mock` (motor.py:197-204) mais o CVD, que é inerte por construção matemática — nenhum bloqueio absoluto de produção pode disparar e o harness vira um comprador quase permanente. |
| 🔴 | `backtesting/motor_otimizado.py` | 291 | **1** | manutencao | O único propósito do módulo (medir a contribuição de cada filtro) é destruído pelo próprio módulo: MTF é gate booleano OBRIGATÓRIO (:189,:208) alimentado por idx//4 que lê 47,44h no futuro em 100% das barras — e como os 7 filtros entram em AND, o vazamento contamina a contagem de todos. Aposentar para _legado/. |
| 🔴 | `backtesting/otimizador.py` | 391 | **2** | edge | O Sharpe que ORDENA o grid é calculado sobre retorno BRUTO — sem fee e sem o fator de sizing (:117,:131) — e anualizado por sqrt(252) sobre retornos POR TRADE, com piso de apenas 5 trades: a seleção é cega a custo e ativamente enviesada para amostras minúsculas. Some-se o look-ahead do idx//4 e o F&G fixo em 100. |
| 🔴 | `backtesting/motor_vectorbt.py` | 391 | **2** | infra | Quebra de paridade não detectada: o legado dimensiona por `* fator` (0.5 no score intermediário) e o VBT usa fator só como booleano com size escalar constante (:304-306, :333) — todo trade de meia posição dobra; e o teste só compara CONTAGEM de trades com tolerância 0.5x-2.0x, que é invariante a esse bug. |
| 🔴 | `research/carry_lab.py` | 253 | **2** | infra | `python research/carry_lab.py` termina em sqlite3.OperationalError: no such table: funding, e a tabela não existe no banco vivo nem nos dois backups de 2026-07-31 (posteriores à medição documentada) — o FAIL de carry, o mais detalhado dos cinco, não tem substrato sobrevivente; e --holdout não tem trava nenhuma contra um doc que o declara queimado. |
| 🔴 | `research/coletar_funding.py` | 128 | **3** | infra | Zero testes num fornecedor único de hipótese pré-registrada, e erro de rede vira break silencioso que grava o parcial e imprime o resumo como coleta completa, com exit 0 (:57-59, :118-123) — sem checkpoint incremental, uma interrupção descarta 100% do que já foi baixado. |
| 🟡 | `backtesting/walk_forward.py` | 625 | **4** | infra | O fix B6, declarado corrigido em GATE_GO_LIVE.md:231, NÃO reproduz o bloqueio absoluto de F&G: em produção score.py:372-381 força AGUARDAR, aqui F&G é só um componente de peso 10 (motor_ensemble.py:216-220 não o lista nos bloqueios) — e o input do fix (data/fng_historico.json) está ausente da árvore, degradando em silêncio para score 100. |
| 🟡 | `backtesting/trend_following.py` | 594 | **4** | edge | Backtest sai por CLOSE abaixo do canal (:110) enquanto o caminho vivo é stopado intrabar pelo monitor local — toda estatística é de um sistema que nunca é stopado por pavio, numa estratégia cuja margem contra o piso já é negativa (2,3 p.p.); e a metade que decide PASSOU/FAIL não tem um único teste. |
| 🟡 | `research/edge_lab.py` | 517 | **4** | edge | HOLDOUT_FRAC=0.35 sobre uma tabela mutável (:224-227): o hold-out de hoje começa em 2025-07-21 e engole ~2.688 velas (43,7%) que eram porção de PESQUISA na rodada de 2026-07-24 — o único hold-out temporal sobrevivente já está contaminado por construção, e as 2 funções que produziram o veredito econômico não têm call site. |
| 🟢 | `validacao.py` | 146 | **7** | edge | detectar_drift é calibração fraca e provadamente inerte neste deploy: com histórico constante stdev=0 → banda mínima ±0.02 → nunca dispara (:136-138); piso_absoluto=0.52 está abaixo da utilidade real, só alarma para baixo, e o alerta vai para bot_events, tabela com 14 writers e zero SELECT. |
| 🟢 | `backtesting/metricas.py` | 143 | **7** | manutencao | deflated_sharpe_ratio devolve PSR puro quando sharpes_trials é None e 4 dos 5 callers gravam o resultado sob a chave 'dsr' — o nome mente para callers futuros; e o único consumidor de produção (cvar_historico via risco.py:192) é inalcançável pelo early-return de risco.py:189, então o módulo tem ZERO execuções no caminho vivo. |

### Configuração

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `config/settings.py` | 81 | **1** | infra | Via o fallback de runtime_settings.py:73-74 tem PRECEDÊNCIA sobre o default SPOT: REST_BASE_URL='fapi.binance.com' (:59-60) migraria executor, risco, klines, dashboard e binance_conta para Futures em bloco e em silêncio; e carrega um par de credenciais em texto plano cuja key está literalmente commitada em dashboard.py:665. |
| 🔴 | `config/params_pares.py` | 54 | **2** | edge | stop_pct nunca dimensiona posição em par nenhum — risco.py:637 recalcula um stop fixo de 1,5% para chamar calcular_tamanho, então SOL (3,0%) opera com o DOBRO do tamanho correto — e para ETH/SOL o valor ainda é sobrescrito pelo suporte de BTC em otimizada.py:207-210. |
| 🔴 | `config/settings_template.py` | 26 | **3** | manutencao | É o passo documentado que arma o vetor: 'COPIE este arquivo para settings.py' cria exatamente o arquivo cujo fallback sobrescreve credencial e endpoint de mercado — e ainda ensina DB_PATH='data/btc_data.db' (:26), o mesmo caminho do banco vivo cuja colisão já causou 3 contaminações documentadas em conftest.py:4-41. |
| 🟡 | `config/runtime_settings.py` | 165 | **4** | infra | DRY_RUN (:128) é trava fantasma: os únicos leitores são dashboard.py:648,709-711 (payload de display) e a decisão real é `simulacao = not args.real` (main.py:1251) — enquanto CLAUDE.md:180 promete 'DRY_RUN=false + ALLOW_REAL_TRADING=true + ENV=production'. Um operador em modo real que ligar DRY_RUN para frear continua enviando ordens, com o painel dizendo SIMULAÇÃO. |

### Ferramentas e periferia

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `ai/ollama_client.py` | 312 | **2** | manutencao | O ponto forte que sustentaria a nota não existe: os testes de fallback NÃO são herméticos — instanciam o cliente real e o primeiro passo de _gerar é esta_disponivel(), que faz requests.get em localhost:11434 sem mock (:54 via :69). Código defensivo bem escrito que nunca produziu um caractere em produção. |
| 🔴 | `scripts/migrate_sqlite_to_supabase.py` | 411 | **3** | infra | _insert_sinais omite preco_saida, pnl_usdt, pnl_pct e barreira_tocada (:204-227), que existem nos DOIS schemas: migrar para o Supabase apaga o resultado de todos os trades fechados e faz relatorio_gate.py reportar zero — destrói a matéria-prima da Etapa 2 e de risco.kelly_do_banco(). |
| 🔴 | `scripts/purgar_fixtures_producao.py` | 215 | **3** | infra | O critério nº1 (`order_id.startswith('SIM-')`, :70) casa com TODA posição legítima de paper, porque executor.py:405 gera esse formato em simulação — o modo em que o BXBotWorker roda 24/7. O Executor tem a guarda `if not self.simulacao` (:1190); o script não tem. `--confirmar` hoje apaga todo o estado de paper de risk_state. |
| 🔴 | `monitor_fluxo.py` | 149 | **3** | infra | run_forever sem o parâmetro `reconnect` (:149): a primeira desconexão encerra o processo, enquanto dashboard.py:494-497 já tem o padrão correto um arquivo ao lado; somado a on_message sem try/except (:51-59), qualquer frame não-aggTrade derruba o monitor de vez. |
| 🔴 | `testar_api.py` | 68 | **3** | infra | Responde à pergunta de go-live com o dado errado e declara sucesso: imprime `permissions` de /api/v3/account (que é da CONTA) e conclui 'Chaves de API validadas com sucesso!' (:51-53) — a armadilha está documentada em GATE_GO_LIVE.md:196-199 e a fonte correta já existe em binance_conta.restricoes_chave(). Uma chave read-only recebe luz verde. |
| 🟡 | `relatorio_gate.py` | 234 | **4** | infra | Viés de seleção fail-open na medição: a query só enxerga trades cuja linha de ENTRADA recebeu pnl_usdt, e isso depende de sinal_id — posições reconstruídas pela reconciliação nascem com sinal_id=None (executor.py:1406) e atualizar_sinal_fechamento vira no-op (database.py:649-650). Trades órfãos de crash somem do numerador E do denominador. |
| 🟡 | `analise_mercado.py` | 185 | **4** | infra | Nenhuma das 6 requisições passa timeout (:18,:32,:58,:75,:79,:85): um socket pendurado trava relatorio_completo() para sempre, e como start_health_server (main.py:1325) roda ANTES, o worker fica vivo, /health responde 200 e o loop de trading nunca começa — apagão silencioso que o NSSM não detecta. |

---

# Plano de combate

O sistema roda 24/7 de forma estavel em paper e tem ilhas de engenharia genuinamente boa (validacao.py, backtesting/metricas.py, binance_conta.py, data/klines.py, o retry dos WebSockets, o conftest anti-producao). Nada disso muda o veredito, e a distancia ate capital real nao e de polimento: e de fundamento. Quatro portoes estao abertos ao mesmo tempo e o quarto impede a descoberta dos outros tres. (1) Nao ha edge: cinco hipoteses reprovadas com criterio pre-registrado, Etapa 1 do gate reprovada em 4 de 5 criterios com retorno -21,25%, e os hold-outs remanescentes estao queimados ou contaminados. (2) As reguas que sustentam esses FAILs nao sao reproduziveis: a tabela klines e rolante e ja mudou de forma nao-append, o input do fix B6 do walk_forward esta ausente da arvore, o motor servido no dashboard tem look-ahead e taxa de futures, e os parametros vivos sairam de um grid de 8.000 combinacoes ordenado por um Sharpe cego a custo. (3) Se alguem ignorar os dois primeiros pontos, a primeira ordem real falha de forma composta e previsivel — comissao em ativo-base nao descontada faz o stop ser rejeitado, abrir_long nao verifica esse retorno e devolve True com posicao real desprotegida, nada no loop pergunta a exchange, nao existe kill-switch em lugar nenhum do repositorio e o unico canal de escalonamento entrega 0 de 8 alertas. (4) O proprio DRY_RUN, documentado no CLAUDE.md como uma das tres condicoes para ordem real, nao e lido por nenhum caminho de execucao: quem o ligar para FREAR continua enviando ordens, com o painel exibindo SIMULACAO. Uma observacao que muda a ordem de ataque: como o capital nao esta dentro, o risco financeiro imediato nao vem do executor — vem dos caminhos pelos quais o capital ENTRA por engano ou por evidencia falsa. Por isso as travas e a verdade da medicao vem antes da cadeia de dia-1 do executor, ainda que o executor seja o lugar onde a perda de fato aconteceria.

## Sequência

A logica de ordenacao e uma so: o que reduz mais probabilidade de perda por hora de trabalho vem primeiro — e como o capital ainda esta FORA, a perda mais provavel hoje nao e um trade ruim, e uma DECISAO ruim de ligar o capital. Por isso: (1) I-8 e I-9 primeiro, em horas. Sao as travas e o canal de escalonamento. Enquanto DRY_RUN nao freia, nao ha kill-switch, MAX_DRAWDOWN_TOTAL nunca e comparado, o bloqueio diario se auto-revoga a meia-noite e a chave nunca e interrogada sobre permissao de saque, qualquer outro trabalho e feito sobre um sistema que pode comecar a gastar dinheiro por acidente. Junto com elas vao os quick wins de I-12 (desligar a rota /api/backtest) e de M-1 (relatorio_gate fail-closed, testar_api), porque sao os artefatos que dizem 'pode ir' quando nao pode. (2) E-7 e E-8 em seguida, em dias. Nao porque o sinal contaminado perde dinheiro hoje (nao perde, e paper), mas porque toda hora de paper rodada sobre um sinal BTC-only, com sizing aleatorio em 3 de 4 entradas e com zero trades gravando pnl_usdt, e uma hora de relogio da Etapa 2 desperdicada. Comecar os 90 dias antes disso e garantir 90 dias de nada. (3) I-11 em paralelo, tambem em dias, por outra equipe ou outro worktree: e barata e protege o ativo mais valioso do repositorio, que sao os cinco FAILs pre-registrados. Um FAIL irreproduzivel nao aguenta a primeira duvida, e a duvida sempre chega quando alguem esta com pressa de ligar o capital. (4) So entao I-10, o executor, em semanas. Ele e o lugar onde a perda de fato acontece, mas exige testnet, exige o canal de alerta de I-9 funcionando e exige a contabilidade de E-8 para que os cenarios sejam verificaveis. Fazer o executor antes das travas seria blindar o cofre deixando a porta da frente aberta. (5) I-12, I-13 e E-9 depois, porque dependem do substrato versionado de I-11 e porque sao pre-requisito de qualquer numero que va justificar capital. (6) E-10 e E-11 por ultimo, em semanas e meses. O ML honesto e a hipotese de edge nova sao o unico caminho para o sistema um dia valer o capital, e sao tambem o unico trabalho que nao adianta apressar: um edge apressado e indistinguivel de ruido. (7) M-1 corre em segundo plano o tempo todo, com o CI entrando junto de I-8 para que nada do que for corrigido regrida em silencio. Uma nota de honestidade sobre a ordem: se a decisao fosse ligar capital amanha, a ordem correta seria a inversa — E-11 primeiro, porque sem edge nada mais importa. A ordem acima e a ordem certa para quem NAO vai ligar capital amanha e quer que o sistema esteja pronto e verificavel quando (e se) um edge aparecer.

## Quick wins (horas, alto valor)

- Desligar (ou marcar como MEDICAO INVALIDA) a rota GET /api/backtest/<symbol> em dashboard.py:721-723 — e o unico artefato do repositorio que serve um resultado falsamente POSITIVO por HTTP 24/7, com veredito 'ESTRATEGIA PROMISSORA' replicado em JS. ~30 minutos.
- Corrigir a guarda do Telegram: telegram_bot.py:31 testa vazio em vez de validade e o token e 'your_telegram_bot_token_here' (truthy). Reusar binance_conta.py:44 (_PLACEHOLDERS) e logar a falha em vez de `return r.status_code == 200` mudo (:45). ~1 hora, destrava os 8 tipos de alerta.
- Invariante `0 < stop < preco < target` antes de abrir_long em main.py:1084-1167, copiando a validacao que o caminho trend ja tem em main.py:909-911. Tres linhas que matam o cenario do stop 34x acima da entrada.
- Kill-switch de arquivo lido como gate 0 em risco.validar_trade e no topo de loop_par. ~15 linhas, e da ao operador um botao de panico que hoje simplesmente nao existe (grep por kill_switch no repo: 0 ocorrencias).
- health.py:234: bind 0.0.0.0 fixo, sem auth e sem variavel para mudar, publicando pnl_dia, drawdown_dia_pct e ml_prob para qualquer dispositivo da LAN. Introduzir HEALTH_BIND com default 127.0.0.1. ~30 minutos.
- Passar `preco_mercado` em vez de `preco` em main.py:1160 — o preco fresco ja e lido na linha 1159 e hoje so alimenta telemetria, enquanto a ordem sai com kline em cache de ate 30s.
- relatorio_gate.py:225-230: fail-closed contra a Etapa 1. Enquanto a Etapa 1 estiver reprovada, imprimir REPROVADO e sair com codigo != 0, em vez de poder imprimir 'GATE: APROVADO — prosseguir a Etapa 3'.
- Chamar binance_conta.restricoes_chave() no gancho de boot que ja existe em main.py:1263-1283: a funcao esta escrita, testada e nunca e chamada em producao. Aborta o boot se a chave puder SACAR ou se nao puder negociar spot em modo real.
- `git grep -n nk6ge30Z` e remover a chave de 64 chars commitada em dashboard.py:665 (identica ao default de config/settings.py:44), e rotaciona-la na Binance. Substituir a deteccao de placeholder do dashboard por binance_conta.chave_configurada().
- Adicionar timeout nas 6 requisicoes de analise_mercado.py (:18,:32,:58,:75,:79,:85): um socket pendurado trava o boot para sempre com /health respondendo 200, e o NSSM nao detecta.
- Guarda `if not self.simulacao` no criterio SIM- de scripts/purgar_fixtures_producao.py:70, espelhando executor.py:1190 — hoje `--confirmar` apaga todo o estado de paper legitimo.
- Tornar `suporte.detectar_suportes` aceitar symbol (suporte.py:104) e passa-lo em estrategias/otimizada.py:193. Uma assinatura e um argumento: corta a causa raiz do stop absurdo em ETH/SOL antes mesmo da refatoracao completa de E-7.

## Frentes

### I-8 — Travas de entrada de capital: kill-switch real, DRY_RUN que freia, credencial fora do codigo

*Trilha infra · esforço: horas · depende de: nada — comeca hoje, em paralelo com tudo*

**Objetivo.** Tornar mecanicamente impossivel que uma ordem real saia por acidente, e dar ao operador um botao de panico que hoje nao existe em lugar nenhum do repositorio. Esta e a frente de maior risco financeiro por unidade de esforco: e o unico caminho pelo qual o dinheiro entra HOJE, e custa horas.

**Módulos:** `main.py`, `risco.py`, `config/runtime_settings.py`, `config/settings.py`, `config/settings_template.py`, `binance_conta.py`, `dashboard.py`, `executor.py`

**Ações:**

- Fazer DRY_RUN participar da decisao real: main.py:1251 hoje e `simulacao = not args.real` e DRY_RUN (config/runtime_settings.py:128) nao e lido por NENHUM caminho de execucao — os unicos leitores sao dashboard.py:648,709-711. Trocar por `simulacao = (not args.real) or DRY_RUN` e adicionar fail-fast no boot quando args.real and DRY_RUN.
- Publicar o modo EFETIVO: o worker grava executor.simulacao em health/bot_events e dashboard.py:711 passa a exibir esse valor em vez de calcular `"SIMULACAO" if (DRY_RUN or not ALLOW_REAL_TRADING)`. Hoje o painel pode exibir SIMULACAO enquanto ordens reais saem.
- Criar kill-switch persistente: arquivo/flag lido como gate 0 de risco.validar_trade (antes de risco.py:600) e no topo de loop_par (main.py:1084). Uma vez armado, NAO pode ser limpo por _resetar_se_novo_dia (risco.py:114-123, que hoje faz `_estado_risco["bloqueado"] = False` na virada do dia) — so por acao humana explicita.
- Comparar MAX_DRAWDOWN_TOTAL de verdade: risco.py:35 define 0.15 com o comentario 'desliga o bot ate revisao manual' e a constante so aparece no payload de display em risco.py:734. Implementar o bloqueio acumulado e liga-lo ao mesmo estado permanente do kill-switch.
- Chamar binance_conta.restricoes_chave() no boot, no gancho ja existente em main.py:1263-1283. A funcao existe (binance_conta.py:175), esta testada, e o unico chamador e o __main__ do proprio arquivo. Abortar o boot se pode_sacar=True; abortar se --real e nao pode_negociar_spot.
- Eliminar o fallback de credencial e de endpoint: config/runtime_settings.py:61-62 usa config/settings.py como default de API_KEY/API_SECRET e :73-74 o usa como fallback de REST_BASE_URL com PRECEDENCIA sobre o default SPOT — e config/settings.py:59-60 aponta para fapi.binance.com. Remover o import de config.settings (runtime_settings.py:19-26), mover config/settings_template.py para _legado/ com LEIA-ME (@Zeta) e apagar config/settings.py da maquina.
- Rotacionar/revogar na Binance a chave de 64 chars que esta commitada em dashboard.py:665 (identica ao default de config/settings.py:44) e remove-la do fonte; a deteccao de placeholder do dashboard passa a usar binance_conta.chave_configurada().

**Critério de saída.** `pytest tests/test_modo_real.py -v` com 5 testes verdes: (a) DRY_RUN=true + --real => simulacao=True/SystemExit; (b) kill-switch armado reprova validar_trade nos 3 pares e continua reprovando apos virada de dia simulada; (c) drawdown acumulado > 15% bloqueia e nao se auto-revoga; (d) boot com chave que pode sacar aborta com exit != 0; (e) sem .env, `python -c "import config.runtime_settings as r; print(r.REST_BASE_URL)"` imprime https://api.binance.com. Mais: `git grep -n nk6ge30Z` retorna 0 ocorrencias.

**Risco de não fazer.** Um operador que quiser FREAR ligando DRY_RUN=true continua enviando ordens reais, com o painel dizendo SIMULACAO — e nao existe kill-switch, nem teto de drawdown acumulado, nem verificacao de permissao de saque da chave. O bot pode perder 5% por dia indefinidamente e religar sozinho a cada meia-noite (risco.py:117-123).

### I-9 — Canal de escalonamento que efetivamente entrega

*Trilha infra · esforço: horas · depende de: nada*

**Objetivo.** Fazer com que uma falha dos demais portoes chegue a um humano. Hoje 0 de 8 tipos de alerta sao entregues, bot_events tem 14 writers e ZERO SELECT, /metrics nao tem scraper e /health responde 200 para worker morto — nao existe caminho por onde uma falha vire conhecimento.

**Módulos:** `telegram_bot.py`, `health.py`, `database.py`, `dashboard.py`, `monitoring/prometheus.yml`

**Ações:**

- Corrigir a guarda de configuracao: telegram_bot.py:31 testa vazio (`if not TELEGRAM_TOKEN`) e o .env traz 'your_telegram_bot_token_here', que e truthy. Reusar binance_conta.py:44 (_PLACEHOLDERS), que ja resolve exatamente este problema para as chaves da Binance.
- Falha de entrega deixa de ser silenciosa: telegram_bot.py:45 (`return r.status_code == 200`) passa a logar o status e o corpo e a gravar bot_event de severidade CRITICAL. Adicionar `python telegram_bot.py --teste` que prova a entrega imprimindo o message_id devolvido pela API.
- Dar um LEITOR a bot_events: rota /api/eventos no dashboard (filtro por severidade) mais um worker que varre eventos CRITICAL nao entregues e escala por Telegram. Sem isso, os alertas de drift (ml_filtro.py:158-166), de persistencia falhada e de protecao ausente continuam write-only.
- Fechar o /metrics: health.py:234 faz bind em 0.0.0.0 fixo, sem auth e sem variavel para mudar, publicando pnl_dia, drawdown_dia_pct, ml_prob e desvios de execucao para qualquer dispositivo da LAN. Introduzir HEALTH_BIND com default 127.0.0.1 e token, alinhando com a politica que o dashboard ja adotou.
- Tirar as metricas de dentro do ramo real-only: health.increment_metric('ordens_total') esta em executor.py:413, abaixo do `return` do ramo de simulacao (executor.py:404) — a metrica e permanentemente 0 no unico modo que roda. Idem ordens_erro (:443, :473, :558).
- /health deixa de ser 200 incondicional (health.py:153-158 concentra toda a verificacao sob `if self.path == '/ready'`): passa a checar liveness das threads de loop_par e do monitor, e o NSSM/superviser passa a usar esse endpoint.

**Critério de saída.** `python telegram_bot.py --teste` sai com codigo 0 e imprime o message_id retornado pela API. Injetar um bot_event CRITICAL e medir <= 60s ate a mensagem chegar. `curl http://<ip-da-lan>:8080/metrics` recusa conexao e `curl http://127.0.0.1:8080/metrics` responde com ordens_total > 0 apos 1 ciclo de paper. Matar a thread de loop_par e ver /health responder 503.

**Risco de não fazer.** O alerta 'URGENTE — POSICAO SEM REGISTRO NO BANCO' (executor.py:863) nao chega, e nem sequer registra que nao chegou. Com capital real, a organizacao acredita estar coberta por um canal que nao existe — pior do que nao ter canal.

### E-7 — Sinal por par: acabar com o BTC-only e com o stop incoerente

*Trilha edge · esforço: dias · depende de: I-8 (para que os testes rodem sem risco de escapar para modo real)*

**Objetivo.** Fazer com que ETHUSDT e SOLUSDT sejam decididos por dados de ETH e SOL, e garantir que nenhum sinal com stop acima da entrada chegue ao executor. Hoje 2 dos 3 pares operam com o sinal de outro ativo e recebem um stop derivado do suporte do BTC — o bloco de producao com entrada ~$1.858 e stop $63.521,65 e reprodutivel por construcao.

**Módulos:** `suporte.py`, `regime.py`, `ensemble.py`, `estrategias/otimizada.py`, `main.py`, `executor.py`, `ml_filtro.py`, `lstm_modelo.py`, `score.py`

**Ações:**

- suporte.py: eliminar SYMBOL de modulo (suporte.py:29, usado em :44) e tornar `symbol` parametro obrigatorio de detectar_suportes (hoje `def detectar_suportes(intervalo="1h")`, suporte.py:104). Atualizar o chamador estrategias/otimizada.py:193, que hoje chama sem symbol e alimenta o override de stop em otimizada.py:207-210.
- regime.py: eliminar SYMBOL='BTCUSDT' (regime.py:23) e _klines sem simbolo (:32-35); `detectar(symbol)` (:151). Atualizar otimizada.py:112 e ensemble.py:70-72 — o componente de 18% do score de ETH e SOL e hoje uma leitura do Bitcoin.
- ensemble.py: `prever(symbol, regime_atual)` (hoje `def prever(regime_atual="INDEFINIDO")`, ensemble.py:44), repassando symbol a xgb_prever (:87) e lstm_prever (:95). Remover o guard morto `hasattr(ens_mod, 'symbol')` de main.py:1013-1015, que e permanentemente False e faz o bug parecer resolvido em code review.
- Invariante dura antes de abrir_long: o caminho de producao (main.py:1084-1167) nao valida nada, enquanto o caminho trend valida em main.py:909-911. Adicionar `0 < stop < preco < target`, com AGUARDAR + bot_event + alerta quando violado — e replicar a mesma guarda DENTRO de executor.abrir_long (executor.py:743-889), fail-closed, para que nenhum chamador futuro possa contorna-la.
- Eliminar a dupla execucao: main.py:1021 chama analisar_otimizada() e main.py:1029 chama imprimir_otimizada(), que re-executa analisar() em otimizada.py:296 — dois calculos, duas linhas em `sinais` (otimizada.py:276) e um bloco impresso que pode divergir da ordem enviada. imprimir() passa a receber o dict ja calculado.
- Separar leitura de escrita: `analisar()` fica puro e o registro no banco vai para uma funcao propria chamada so pelo worker. Hoje dashboard.py:318 chama analisar() a cada 30s por par e grava sinais fantasma na mesma tabela `sinais` que alimenta a Etapa 2 do gate.

**Critério de saída.** Teste de propriedade novo: para fixtures de klines DIFERENTES entre os 3 pares, assert que ml_prob, regime_final e suporte_forte diferem entre BTCUSDT/ETHUSDT/SOLUSDT (hoje sao bit-identicos). Teste randomizado de 1.000 casos COMPRA: 100% satisfazem 0 < stop < preco < target, e violacoes forcadas resultam em AGUARDAR e nunca em abrir_long. Apos 24h de paper: `grep -c 'Stop Loss' logs | ` nenhum bloco com stop > entrada, e `SELECT timestamp,symbol,COUNT(*) FROM sinais GROUP BY 1,2 HAVING COUNT(*)>1` retorna 0 linhas.

**Risco de não fazer.** Com capital real, toda entrada de ETH/SOL ou (a) tem o STOP_LOSS_LIMIT rejeitado pela Binance por stopPrice acima do mercado, deixando a posicao real DESPROTEGIDA, ou (b) e liquidada no primeiro tick pelo monitor local (executor.py:162-166), pagando spread + 2x taxa. E enquanto isso o track record de paper que vai justificar o go-live esta sendo gerado por um sinal que nao olha para o ativo.

### E-8 — Contabilidade de paper confiavel: a materia-prima da Etapa 2

*Trilha edge · esforço: dias · depende de: E-7*

**Objetivo.** Garantir que os 90 dias de paper da Etapa 2 midam alguma coisa. Hoje o sizing efetivo e aleatorio em 3 de 4 entradas, o preco de entrada e falsificado, e ZERO dos 5.255 sinais tem executado=1 ou pnl_usdt — o pipeline de meta-labeling nunca gravou uma linha.

**Módulos:** `main.py`, `suporte.py`, `risco.py`, `executor.py`, `database.py`, `dashboard.py`, `estrategias/otimizada.py`, `logger.py`

**Ações:**

- Resolver o ScaleIn: main.py:1084 gateia com `not exec_par.posicao`, entao a parcela 1 (main.py:1116) torna as parcelas 2 e 3 inalcancaveis no caminho feliz; nada reseta estado['scale_in'] no fechamento, e o proximo trade cai em main.py:1121 dimensionando sobre o tamanho_total do trade ANTERIOR (suporte.py:296,305). Recomendacao: remover o ScaleIn do caminho vivo (parcelas 2/3 nunca executaram) e abrir com o tamanho integral que validar_trade autorizou.
- risco.validar_trade passa a receber e usar o stop REAL: risco.py:637 recalcula `stop = preco * (1 - 0.015)` so para dimensionar, entao SOL (stop_pct 3,0% em config/params_pares.py:37) opera com o DOBRO do tamanho correto e ETH com 1,33x. O campo 'risco_usdt' devolvido em risco.py:694 e ficcao.
- Usar o preco fresco: main.py:1159 le `preco_mercado = exec_par.get_preco()` e a linha seguinte (main.py:1160) passa o preco de kline em cache para abrir_long. Passar preco_mercado — a infraestrutura ja esta ali e so alimenta telemetria.
- Fechar o circuito de meta-labeling: marcar_sinal_executado (database.py:610) e atualizar_sinal_fechamento (database.py:635) precisam gravar de fato; o caminho trend precisa passar sinal_id (main.py:928 chama abrir_long posicionalmente, sem o kwarg, e executor.py:829 grava None); posicoes reconstruidas nascem com sinal_id=None (executor.py:1406) e viram no-op silencioso em database.py:649-650.
- Dashboard read-only sobre o banco de decisao: dashboard.py:318 para de gravar sinais e dashboard.py:457 para de gravar em `trades` sem trade_id (burlando a dedupe parcial de database.py:439/449 — 2.460.820 linhas com trade_id NULL contra 386.124 com id).
- Consertar o relatorio diario: logger.py:474/484 filtra `timestamp LIKE 'YYYY-MM-DD%'` sobre um campo que estrategias/otimizada.py:222 grava como '%d/%m/%Y' — a query devolve (0,None,None) e logger.py:496-502 converte None em 0.0, produzindo todo dia um alerta que mente na direcao tranquilizadora. Padronizar ISO em todo o repo e reescrever a query por range.
- Dar escritor a log_trades ou aposenta-la: registrar_trade_entrada (logger.py:271) e registrar_trade_saida (logger.py:309) nao tem NENHUM chamador de producao e a tabela tem 0 linhas — todo o bloco de leitura construido em cima dela e vazio por consequencia.

**Critério de saída.** Apos 72h de paper: `SELECT COUNT(*) FROM sinais WHERE executado=1` > 0 e igual ao numero de posicoes abertas no periodo; `SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NOT NULL` igual ao numero de trades fechados; `SELECT COUNT(*) FROM trades WHERE trade_id IS NULL AND timestamp > <inicio>` = 0; relatorio_gate.py reporta n>0 e um PnL que bate com a soma manual dos fechamentos no log; teste que prova risco_usdt == (preco-stop)*qty com erro < 1% para os 3 pares; relatorio diario das 18h reporta numeros diferentes de zero em um dia com atividade.

**Risco de não fazer.** Rodar 90 dias de Etapa 2 e chegar ao fim com um relatorio que reporta zero (ou pior, um numero errado). Sao 90 dias de calendario perdidos, e um profit factor calculado sobre um subconjunto auto-selecionado de trades pode APROVAR capital real por acidente.

### I-10 — Cadeia de dia-1 do executor, provada em testnet

*Trilha infra · esforço: semanas · depende de: I-8, I-9, E-8*

**Objetivo.** Fechar a sequencia de falhas compostas que hoje transforma a primeira ordem real em posicao desprotegida e permanente. Nenhum item aqui jamais executou: 10 metodos bifurcam por `if self.simulacao` e o grafo que recebera capital tem ZERO execucoes reais.

**Módulos:** `executor.py`, `risco.py`, `database.py`, `config/runtime_settings.py`, `tests/integration/`

**Ações:**

- Descontar a comissao em ativo-base: executor.py:791-793 atribui `tamanho_btc = executedQty` bruto; em SPOT sem BNB a Binance debita a comissao em BTC, entao a proteção e a saida sao dimensionadas acima do saldo livre. Ler `fills`/commissionAsset da resposta (grep 'commission' nao retorna nada no codigo de producao) ou dimensionar pela leitura de saldo livre.
- abrir_long fail-closed: executor.py:810 chama _abrir_protecao e o retorno so alimenta o dict (executor.py:827-828); nao existe nenhum `if prot[...] is None` ate o `return True` da linha 889. Qualquer falha (-2010, -1013, rate-limit, rede) produz posicao real anunciada como sucesso com stop_order_id=None. Implementar: desfazer a posicao (SELL market) ou escalar CRITICAL + armar o kill-switch — nunca retornar True.
- Recuperacao de ordem fantasma cobrir HTTP 5xx/429: _request_assinado so marca timeout_rede quando 'falha de rede' esta na ultima_falha (executor.py:341), enquanto o ramo de 429/418/5xx (executor.py:330-333) nunca dispara o flag que gateia a recuperacao (executor.py:433).
- Dar newClientOrderId + recuperacao pos-timeout tambem a _colocar_stop_exchange (executor.py:466-468) e _colocar_oco_exchange (executor.py:543-548), que hoje chamam _request_assinado direto e devolvem None mesmo quando a ordem existe na exchange — o bot perde o orderId de uma ordem viva.
- _liberar_protecao passa a checar o retorno de _cancelar_oco_exchange/_cancelar_ordem_exchange antes de zerar os ids (executor.py:621-631, que hoje os descarta) e a persistir a mudanca (nao chama _persistir_posicao, que existe em :892-901).
- Monitor sobrevivente: executor.py:1124-1126 faz `break` incondicional apos fechar_posicao, inclusive quando o early-return de :927-939 MANTEVE a posicao. Retentar em vez de sair; adicionar watchdog de thread (nao ha `is_alive` em lugar nenhum); mover `preco_pico = self.posicao['entrada']` (executor.py:1087) para dentro do try.
- Perguntar a exchange: _status_ordem (executor.py:379) so tem 2 call sites (:677 e :1279, este ultimo atras de flag desligada). Adicionar reconciliacao periodica no loop _monitorar (executor.py:1081-1150) e ligar RECONCILIAR_BOOT_EXCHANGE (config/runtime_settings.py:165, hoje False e ausente do .env).
- fechar_posicao atomica e idempotente: flag 'em_fechamento' sob lock (hoje o lock e solto apos o snapshot, executor.py:910-911) e registrar_resultado (executor.py:996) so DEPOIS de salvar_sinal (executor.py:997) — hoje uma excecao ali faz o PnL ser contabilizado de novo a cada 10s, indefinidamente.

**Critério de saída.** Nova suite tests/integration contra Binance Spot Testnet, 8 cenarios verdes: fill com comissao em BTC => stop aceito; stop rejeitado => abrir_long retorna False e nao ha posicao orfa; 503 na entrada => nenhuma ordem duplicada (verificado por consulta ao clientOrderId); stop executado fora do bot => detectado em <= 1 ciclo; SELL rejeitado => monitor sobrevive e retenta; excecao em salvar_sinal => PnL contabilizado exatamente 1x; cancelamento que falha => ids NAO sao zerados; crash e restart => reconciliar_boot converge com o estado da exchange. Metrica agregada: 0 posicoes sem stop apos 50 ciclos de fuzz.

**Risco de não fazer.** A primeira ordem real falha de forma composta e previsivel: comissao nao descontada => stop rejeitado => abrir_long devolve True com posicao desprotegida => nada no loop pergunta a exchange => o monitor morre no primeiro SELL rejeitado => sem kill-switch e sem alerta. Perda ilimitada ate intervencao manual.

### I-11 — Substrato de dados versionado e vereditos reproduziveis

*Trilha infra · esforço: dias · depende de: nada; roda em paralelo com E-7/E-8*

**Objetivo.** Transformar os 5 FAILs pre-registrados — hoje o ativo mais valioso do repositorio, e o unico que impede o capital de entrar — de prosa em resultado re-derivavel. Um FAIL que ninguem consegue reproduzir cede na primeira duvida.

**Módulos:** `backtesting/coletar_dados.py`, `research/edge_lab.py`, `research/carry_lab.py`, `research/coletar_funding.py`, `data/klines.py`, `docs/GATE_GO_LIVE.md`, `research/METODOLOGIA.md`

**Ações:**

- coletar_dados.py: exigir --inicio/--fim (hoje a janela e ancorada em datetime.now(), coletar_dados.py:60-61, e por isso NENHUM veredito e reproduzivel); descartar a vela em formacao (a ultima baixada e parcial e o INSERT OR IGNORE de :104 a congela para sempre); DB_PATH derivado de __file__ (hoje relativo ao cwd, :21, criando silenciosamente um banco vazio quando rodado de outro diretorio); exit != 0 no `break` de erro de lote (:90-93).
- Congelar um snapshot imutavel: exportar as series usadas pela pesquisa para data/snapshots/ com manifest.json contendo symbol, intervalo, primeira e ultima vela, contagem e sha256 — versionado no git. Os labs passam a ler o snapshot, nao a tabela viva. Evidencia de que isso e necessario: a tabela klines ja mudou de 17.520 para 17.563 linhas de forma NAO-append entre a medicao oficial e hoje.
- Hold-out por DATA fixa, nao por fracao: edge_lab.py:224-227 (HOLDOUT_FRAC=0.35 sobre len) e carry_lab.py:65-67 fazem a fronteira se mover a cada coleta — 43,7% do hold-out atual do edge_lab ja foi porcao de PESQUISA na rodada de 2026-07-24. Fixar por data no codigo e registrar em METODOLOGIA.
- Dar entrypoint as funcoes que produziram o veredito: teste_permutacao_auc_xgb (edge_lab.py:326) e avaliar_economia (edge_lab.py:361) nao tem NENHUM call site — o argparse de main() (edge_lab.py:496-499) nao as expoe. Adicionar --perm-auc e --economia.
- Ressuscitar o substrato do carry: `python research/carry_lab.py` termina em `sqlite3.OperationalError: no such table: funding` e a tabela nao existe no banco vivo nem nos dois backups de 2026-07-31. Rodar coletar_funding.py (com checkpoint incremental e exit != 0 no break de rede, coletar_funding.py:57-59) e versionar o snapshot resultante.
- Trava de uso unico com LEITOR: edge_lab.py:411-416 e so um kwarg booleano e o registro em METODOLOGIA (edge_lab.py:429-433) esta dentro de try/except:pass e nao tem nenhum leitor; carry_lab.py:246-247 nao tem trava nenhuma. A trava passa a LER o registro e recusar a segunda execucao.
- data/klines.py: adicionar raise_for_status (:46-60, hoje 429/418 vira ValueError silencioso), teto de idade + flag `stale` no fallback (:91) e logging (o modulo nao importa logging). risco.verificar_volatilidade — o circuit breaker — nao pode receber dado stale.

**Critério de saída.** `python -m research.reproduzir --manifest manifests/<data>.json` re-deriva os 5 FAILs com diferenca 0.0 em todos os IC e em todos os vereditos, em duas maquinas diferentes. `sha256sum` do snapshot bate com o manifest. `python research/carry_lab.py` executa sem OperationalError. Segunda chamada de --holdout aborta com erro citando o registro anterior.

**Risco de não fazer.** O unico ativo que hoje protege o capital e um conjunto de FAILs que ninguem consegue re-derivar. Na primeira pressao ('sera que o mercado mudou?'), o FAIL e descartado e o capital entra numa estrategia que mediu -21,25%.

### I-12 — Regua unica de medicao

*Trilha infra · esforço: semanas · depende de: I-11 (precisa do snapshot versionado para medir contra algo estavel)*

**Objetivo.** Fazer com que backtest, walk-forward, otimizador e producao midam a MESMA funcao de decisao. Hoje sao reguas diferentes, e a que o dashboard serve por HTTP 24/7 e falsamente positiva.

**Módulos:** `backtesting/motor_ensemble.py`, `backtesting/walk_forward.py`, `backtesting/otimizador.py`, `backtesting/motor.py`, `backtesting/motor_otimizado.py`, `backtesting/motor_vectorbt.py`, `backtesting/metricas.py`, `score.py`, `dashboard.py`, `templates/backtest.html`

**Ações:**

- QUICK WIN IMEDIATO: desligar a rota GET /api/backtest/<symbol> (dashboard.py:721-723) ou substituir o payload por um aviso de medicao invalida. E o unico artefato do repo que serve um numero falsamente POSITIVO (+2,54%, Sharpe 1,04, veredito 'ESTRATEGIA PROMISSORA') por HTTP, e o veredito esta replicado em JS no frontend (templates/backtest.html:251-265).
- Uma so funcao de score: eliminar _score_backtest (backtesting/motor_ensemble.py:202-212, pesos regime 25 / mtf 20 / ml 15, sem cvd e sem obi) e fazer as reguas importarem score.calcular (score.py:50-64, pesos regime 18 / mtf 12 / ml 20 / cvd 7 / obi 8), com os bloqueios absolutos de score.py:370-375.
- Corrigir o mapeamento MTF em todos os motores: `idx4 = i // 4` (motor_ensemble.py:350, otimizador.py:64, motor_otimizado.py:83, motor_vectorbt.py:229) le candle 4h ainda aberto. A correcao ja existe em walk_forward.py:120 (_mapear_idx4_fechado) e nunca foi propagada.
- TAXA de SPOT em todo lugar: motor_ensemble.py:37 usa 0.0004 (tarifa de futures) num bot que executa /api/v3/order; trend_following.py:36 ja usa 0.001 com o comentario correto. E o Sharpe passa a ser calculado sobre retorno LIQUIDO e com periodos_por_ano derivado da frequencia real de trades (metricas.py:15 usa 252 por default sobre retornos por trade).
- deflated_sharpe_ratio deixa de mentir: metricas.py:134-143 devolve PSR puro quando sharpes_trials e None, e 4 dos 5 callers gravam sob a chave 'dsr'. Tornar o argumento obrigatorio ou separar em duas funcoes.
- walk_forward: ler config/params_pares (hoje STOP_PCT/TARGET_PCT sao constantes de modulo, walk_forward.py:75-76, e ETH/SOL sao medidos com os limiares do BTC); modelar a politica de saida real (parcial 50% + breakeven + trailing, executor.py:167-184); tratar F&G como BLOQUEIO ABSOLUTO e nao como componente de peso 10; abortar se data/fng_historico.json estiver ausente ou nao cobrir o periodo (hoje degrada em silencio para score 100).
- otimizador: OOS obrigatorio, ordenacao por Sharpe liquido de taxa e ponderado pelo fator de sizing (hoje otimizador.py:112-117 enfileira retorno BRUTO), piso de trades >= 50 (hoje 5, otimizador.py:298) e DSR com n_trials real.
- Aposentar para _legado/ com LEIA-ME (@Zeta): backtesting/motor_otimizado.py (morto confirmado), backtesting/motor.py (9 de 11 componentes do score sao mocks) e backtesting/motor_vectorbt.py (nunca executou; quebra de paridade de sizing que o teste e estruturalmente incapaz de detectar).

**Critério de saída.** `pytest tests/test_paridade_regua.py`: para 500 barras aleatorias, o score da regua == score.calcular bit-a-bit, incluindo os bloqueios absolutos. Teste de causalidade: `assert ts_fechamento_4h(idx4) <= ts_1h[i]` em 100% das barras, para os 3 pares. `python backtesting/walk_forward.py --par ETHUSDT` grava no JSON rsi_min=38/rsi_max=62/score_operar=50 (os valores de config/params_pares.py:29-32). GET /api/backtest devolve 404 ou um resultado com look-ahead=0 provado por teste.

**Risco de não fazer.** O numero que autoriza (ou nao) o capital vem de uma regua que mede outra estrategia. Corrigir apenas o look-ahead do motor servido no dashboard leva o resultado de +2,54% para -31%; corrigindo tambem a taxa, para -42,67%. Sao ~45 pontos percentuais fabricados por duas linhas.

### E-9 — Reconstruir (ou aposentar) os parametros vivos

*Trilha edge · esforço: semanas · depende de: I-11, I-12*

**Objetivo.** config/params_pares.py governa o trading ao vivo e veio de um grid de ate 8.000 combinacoes sem out-of-sample, ordenado por um Sharpe cego a custo e enviesado para amostras de 5 trades, com look-ahead de MTF e F&G fixo em 100 — e o ultimo commit do arquivo (5991003, 2026-06-22) e ANTERIOR a versao atual do otimizador. A procedencia e inauditavel.

**Módulos:** `config/params_pares.py`, `backtesting/otimizador.py`, `risco.py`, `estrategias/otimizada.py`

**Ações:**

- Marcar os parametros atuais como NAO CONFIAVEIS no proprio arquivo e proibir por escrito o uso dos Sharpes reportados (3,24 / 1,79 / 2,98 em config/params_pares.py:10-24) como evidencia em qualquer documento de decisao — eles contradizem a Etapa 1, que reprovou em 4 de 5 criterios com retorno -21,25%.
- Fazer stop_pct efetivamente dimensionar posicao (dependencia direta de E-8: risco.py:637 recalcula 1,5% fixo, entao SOL opera com o dobro do tamanho correto).
- Re-derivar os parametros com o pipeline de I-12 sobre o snapshot de I-11: OOS obrigatorio, custo de SPOT, F&G real, MTF causal, DSR deflacionado pelo n_trials verdadeiro.
- Registrar no cabecalho do arquivo: commit da regua, hash do snapshot, janela, n_trials e DSR. Sem esses cinco campos, o arquivo nao entra em producao.

**Critério de saída.** Novo config/params_pares.py com os 5 campos de procedencia preenchidos e DSR deflacionado >= 0,95 no hold-out; OU — resultado esperado e perfeitamente aceitavel — um documento declarando que NENHUM conjunto de parametros passou, e o bot permanece em paper. Em qualquer dos casos, `pytest tests/test_params_procedencia.py` falha se algum par nao tiver os 5 campos.

**Risco de não fazer.** Capital real atras de parametros selecionados como o maximo de 8.000 sorteios sobre uma serie unica com vazamento — o vencedor esperado tem Sharpe alto por construcao, e nada disso sobrevive fora da amostra.

### E-10 — ML honesto ou desligado

*Trilha edge · esforço: semanas · depende de: E-7 (symbol no ensemble), I-11 (base de treino viva)*

**Objetivo.** O componente ML pesa 20 pontos do score (o maior isolado) e hoje: recebe em producao uma feature de escala ~30x menor que a do treino, e treinado sobre uma base congelada em 2026-04-03, tem guard-rail de drift provadamente inerte (cv_auc bit-identico em 3 retreinos) e carrega 45% de peso num MLP com cv_auc 0,5685 (ruido) promovido sem gate.

**Módulos:** `ml_filtro.py`, `lstm_modelo.py`, `ensemble.py`, `validacao.py`, `indicadores.py`, `estrategias/otimizada.py`, `main.py`

**Ações:**

- Fechar o train/serve skew de dist_vwap: indicadores.py:135-145 e VWAP CUMULATIVO; o treino passa a serie inteira (ml_filtro.py:126 -> :72) e a inferencia passa 100 velas (ml_filtro.py:303). Medido na mesma barra: +0,4916 no treino contra +0,0041 em producao, com sinal invertido em parte da serie. Usar vwap_rolling(20) nos dois lados (que e o que o backtest ja usa) ou fixar a mesma janela.
- Teste de paridade treino-vs-inferencia como criterio permanente: para 200 barras, |feature_treino - feature_inferencia| < 1e-6 nas 11 features. Este teste teria pego o defeito no dia em que foi introduzido.
- Gate de promocao de modelo: lstm_modelo.py:212 salva o pickle incondicionalmente e a escrita nao e atomica (contraste com ml_filtro.py:259-272, que faz tmp + os.replace, no mesmo repo). Nao promover abaixo de um piso de cv_auc; escrever atomicamente. Se o MLP nao passar o piso, aposentar para _legado/ e redistribuir o peso.
- ml_filtro.prever validar que artefato['symbol'] bate com o pedido (hoje le so 'modelo' e 'intervalo', ml_filtro.py:295-296, e o fallback silencioso de :285 pode servir um pickle de 4 meses atras sem cv_auc registrado).
- Rotulo com barreira inferior: ml_filtro.py:130-132 marca 1 se o MAXIMO dos 8 fechamentos seguintes bater +1,5%, sem olhar o caminho — um trade que cai 3% (estourando o stop) e rotulado POSITIVO. O modelo esta sendo otimizado para uma pergunta que a mesa nao faz.
- Fail-open do ensemble deixa de premiar: otimizada.py:116-120 substitui a falha por prob 0.5 + pode_operar=True, entregando 10 pontos de graca; main.py:1046-1049 nunca rebaixa o gauge ml_prob. Modelo indisponivel tem que reduzir exposicao.
- Detector de drift com banda que possa disparar (validacao.py:136-138: com historico constante a banda minima e +-0,02 e o limiar nunca e cruzado) — depende de I-11 para que a base de treino deixe de ser congelada.

**Critério de saída.** `pytest tests/test_ml_paridade.py -v` verde nas 11 features. Teste que prova que ensemble indisponivel resulta em decisao AGUARDAR ou fator reduzido (nunca em pontuacao neutra). Apos dois retreinos com a base atualizada, `SELECT cv_auc_mean FROM model_metricas ORDER BY id DESC LIMIT 2` devolve valores DIFERENTES (hoje sao bit-identicos em 3 retreinos). Existe tests/test_lstm_modelo.py (hoje nao existe nenhum arquivo de teste do modulo).

**Risco de não fazer.** 20 pontos do score — o maior componente — vem de um modelo que opera fora da variedade em que foi validado, e o AUC reportado (0,6086/0,6173) nao descreve o comportamento ao vivo. Qualquer decisao de capital que se apoie nesse numero se apoia em nada.

### I-13 — Postgres/Supabase sem perda de dado

*Trilha infra · esforço: dias · depende de: E-8 (nao migrar antes de o meta-labeling gravar) *

**Objetivo.** O backend de producao documentado esta deterministicamente quebrado e o migrador destroi a materia-prima da Etapa 2. Enquanto isso nao for resolvido, a migracao pendente (migrations 002+003) nao pode ser executada.

**Módulos:** `database.py`, `logger.py`, `scripts/migrate_sqlite_to_supabase.py`, `relatorio_gate.py`, `supabase/migrations/`

**Ações:**

- Corrigir o acesso por indice sobre dict_row: database.py:581 (salvar_sinal, `sinal_id = row[0]`) e database.py:990 (historico_cv_auc_modelo, `[row[0] for row in rows]`) — o pool entrega dict (row_factory=dict_row, database.py:95). Sob Postgres, toda chamada levanta KeyError: 0. Note que sinais_executados (:745) e buscar_sinais (:714) ja usam dict(row) corretamente.
- Migrador: copiar preco_saida, pnl_usdt, pnl_pct e barreira_tocada em _insert_sinais (scripts/migrate_sqlite_to_supabase.py:204-227) — as quatro colunas existem nos DOIS schemas (database.py:248-251 e :328-331) e sem elas a migracao apaga o resultado de todos os trades fechados.
- Migrador: ON CONFLICT nas 4 tabelas que hoje fazem INSERT puro (snapshots_mercado :171, cvd_historico :196, sinais :219, bot_events :268) — um segundo --confirmar duplica `sinais` inteira e dobra o n e o PnL lidos pelo gate.
- Migrador: savepoint por tabela (hoje o except por tabela continua o laco, :332-338, e o commit final roda incondicionalmente, :340-342 — em psycopg3 o primeiro erro aborta a transacao e tudo depois falha em cascata); _sqlite_rows deixa de engolir excecao devolvendo [] (:117-118, que hoje faz 'database is locked' virar '0 linhas (vazio ou inexistente)' e sucesso); parar de imprimir 40-50 chars da DATABASE_URL (:312, :363).
- logger.py: DDL com TIMESTAMPTZ no Postgres (hoje logger.py:142-218 usa `timestamp TEXT` nos dois backends, perpetuando a comparacao de data por string) e _connect com timeout/busy_timeout/synchronous como database.conectar() faz (logger.py:129 abre o mesmo arquivo de 346 MB cru).
- relatorio_gate.py: usar psycopg3 (hoje importa psycopg2, :39, que nao esta no requirements) e respeitar DATABASE_URL/DATABASE_BACKEND (hoje conecta em data/btc_data.db, :23,43-45, ignorando a configuracao).
- Retencao/purga das tabelas sem leitor antes de pagar armazenamento: `trades`, `snapshots_mercado` e `cvd_historico` somam 361 MB, nenhuma tem SELECT no caminho de producao (resumo_trades :761 e ultimos_snapshots :676 nao tem call site; cvd_historico nao tem sequer um SELECT).

**Estado (2026-08-11).**

| Ação | Estado |
|---|---|
| dict_row: `salvar_sinal`, `historico_cv_auc_modelo` | ✅ `_primeiro_valor()` resolve nos dois backends |
| migrador copia `preco_saida`/`pnl_usdt`/`pnl_pct`/`barreira_tocada` | ✅ |
| migrador com `ON CONFLICT` nas 4 tabelas | ✅ |
| migrador com savepoint por tabela + commit condicional | ✅ `ec5dee4`, `tests/test_migrador_savepoint.py` (7 testes) |
| migrador para de imprimir a `DATABASE_URL` crua | ✅ `_destino_seguro()`; teste varre a fonte por `DATABASE_URL[:` |
| `logger.py`: `TIMESTAMPTZ` no PG + SQLite com WAL/busy_timeout | ✅ `85f46b1` |
| `relatorio_gate.py`: psycopg3 + respeitar `DATABASE_URL` | ✅ + 27 testes (o arquivo não tinha nenhum) |
| retenção/purga das 3 tabelas sem leitor | ✅ `scripts/purgar_retencao.py` + 26 testes — **ferramenta pronta, purga NÃO executada** |

**`relatorio_gate.py` — a fonte deixa de ser escolhida por omissão.** A parte do psycopg3 já estava feita; o que faltava era a fonte. O relatório abria `data/btc_data.db` fixo e só ia ao Postgres com `--postgres` explícito: num deploy com `DATABASE_BACKEND=postgres`, `python relatorio_gate.py` media o SQLite local — nessa máquina, paper trading antigo — e emitia veredito sobre o banco errado sem dizer que tinha feito isso. Precedência agora: `--db` > `--sqlite` > `--postgres` > `DATABASE_BACKEND`, e a fonte medida é impressa no cabeçalho (DSN mascarada). Backend Postgres configurado com `DATABASE_URL` vazio **aborta** em vez de cair para o arquivo local: sem DSN a resposta certa é "não sei medir", não "medi outra coisa".

Três defeitos vizinhos que apareceram ao mexer: (a) `klines` não existe no schema Postgres — não está em `_inicializar_postgres` nem em migration nenhuma, quem a cria é `backtesting/coletar_dados.py`, sempre em SQLite. Sem tratar, o critério buy-and-hold ficaria permanentemente indisponível no Supabase e o gate reprovaria para sempre por falta de fonte, não por desempenho — fail-closed pelo motivo errado. Há fallback para o SQLite configurado, e ele diz de onde leu. (b) `GATE_DOC` era relativo ao cwd: rodar de outro diretório fazia a Etapa 1 constar REPROVADA por arquivo ausente. (c) o relatório morria com `UnicodeEncodeError` no primeiro `→` no console cp1252 do Windows — depois de já ter lido o banco, entregando traceback onde deveria estar o veredito.

**Retenção — política e mecânica.** Decisão do operador: **90 dias em `trades` e `snapshots_mercado`, arquivando antes de apagar; `cvd_historico` sai inteira** (é a única sem leitor *nem* uso futuro previsto — as outras duas são matéria-prima de E-11). Medido no banco vivo: 1.843.150 de 2.938.237 linhas de `trades`, 7.077 de 9.833 snapshots e as 4.623 de CVD — 1,85 milhão de linhas, ~63% do arquivo.

A ordem do script é rígida, e é ela que o torna reversível (protocolo @Zeta): grava o dump em `_legado/dumps/<tabela>-ate-<carimbo>.jsonl.gz`, **relê o .gz do disco** contando linhas e calculando sha256, e só então roda o DELETE. Dump truncado por disco cheio ou processo morto falha na releitura e o DELETE não acontece — há teste dirigido para isso. `--restaurar` confere o sha256 contra o manifesto, valida cada coluna do JSON contra o schema real (um `.jsonl.gz` forjado poderia emendar SQL pelo nome da coluna) e é idempotente. DELETE em lotes de 20 mil, para não segurar o lock do SQLite enquanto o worker escreve.

Duas correções de fato ao levantamento acima: `cvd_historico` **é escrita** (`main.py:1361` por ciclo, `:1733` no shutdown) — o que ela não tem é leitor, e por isso volta a crescer ~35 linhas/dia depois da purga; e o espaço em disco só é devolvido com `--vacuum`, que precisa de lock exclusivo e portanto do worker parado.

**Critério de saída.** `DATABASE_URL=<testdb> pytest tests/test_database_postgres.py -v` verde, cobrindo salvar_sinal, historico_cv_auc_modelo e as 3 funcoes de crash recovery (database.py:860,865,879 — hoje sem nenhum teste direto). Migrar duas vezes seguidas e obter `SELECT COUNT(*)` identico nas 6 tabelas. `SELECT COUNT(*) FROM sinais WHERE pnl_usdt IS NOT NULL` identico na origem e no destino.

**Risco de não fazer.** No dia em que DATABASE_URL for configurado, o bot nao grava UM sinal sequer; e a chamada de executor.py:997 nao tem try/except e vem DEPOIS de registrar_resultado (:996) e ANTES de limpar a posicao (:1039-1043), deixando posicao fantasma na memoria e no risk_state. E a migracao apaga o historico que a Etapa 2 precisa.

### E-11 — Hipotese de edge nova, pre-registrada

*Trilha edge · esforço: meses · depende de: I-11, I-12, E-8*

**Objetivo.** E o portao 1, o unico com multiplicador ~0: cinco hipoteses ja reprovadas com criterio pre-registrado, Etapa 1 reprovada em 4 de 5 criterios. Sem edge, toda a qualidade de execucao so determina a velocidade da perda. Esta frente e a mais longa e a menos urgente em termos de risco de perda IMEDIATA — precisamente porque as travas mantem o capital fora.

**Módulos:** `research/`, `backtesting/walk_forward.py`, `docs/GATE_GO_LIVE.md`, `research/METODOLOGIA_<nova>.md`

**Ações:**

- Escrever METODOLOGIA_<nome>.md com hipotese, familia de features congelada, criterio de PASS/FAIL numerico e hold-out definido por DATA — e commitar ANTES da primeira medicao, registrando o hash do commit.
- Hold-out NOVO: nao reusar o de trend nem o de carry (queimados por escrito) nem o temporal do edge_lab (43,7% ja foi porcao de pesquisa). Dado novo significa periodo novo ou universo novo, coletado com o coletor corrigido de I-11.
- Custo de execucao real entra no criterio desde o dia 0: 0,10%/lado de SPOT taker mais slippage medido no proprio track record de paper de E-8. A margem contra o piso, nas hipoteses ja testadas, foi negativa por 2,3 p.p. — o custo nao e detalhe.
- Candidatos que o codigo atual sugere e que ainda nao foram testados honestamente: meta-labeling sobre o track record de paper (viavel so depois de E-8 gravar), e microestrutura de verdade (o componente CVD atual e matematicamente incapaz de sair de 50/51: com window_size=50, |tanh(slope/std)| <= 0,0692 contra o limiar 0,1 de score.py:147).
- Trava de uso unico com leitor (I-11) aplicada ao novo hold-out, e um unico comando de reproducao registrado no METODOLOGIA.

**Critério de saída.** Documento de metodologia commitado com hash ANTES da medicao; veredito re-derivavel pelo comando de I-11 com diferenca 0.0; trava de hold-out recusando a segunda execucao. O criterio de PASS e o que estiver escrito nesse documento — e um FAIL e um resultado valido e final, nunca renegociavel.

**Risco de não fazer.** Continuar operando (mesmo em paper) uma estrategia com edge reprovado e chamar isso de progresso. Com capital real, e perda esperada positiva contra o operador.

### M-1 — Aposentadoria, ferramentas que mentem e CI

*Trilha manutencao · esforço: dias · depende de: nada; a parte de CI deveria vir junto com I-8*

**Objetivo.** Remover as ferramentas que dao luz verde errada e o codigo morto que infla a percepcao de completude, e fechar o CI para que nenhuma das correcoes acima regrida em silencio.

**Módulos:** `testar_api.py`, `relatorio_gate.py`, `scripts/purgar_fixtures_producao.py`, `monitor_fluxo.py`, `ai/ollama_client.py`, `backtesting/motor_otimizado.py`, `config/settings_template.py`, `analise_mercado.py`, `.github/workflows/`

**Ações:**

- testar_api.py: imprime `permissions` de /api/v3/account (que e da CONTA) e conclui 'Chaves de API validadas com sucesso!' (:51-53) — uma chave READ-ONLY recebe luz verde. A armadilha esta documentada em docs/GATE_GO_LIVE.md:196-199 e a fonte correta ja existe em binance_conta.restricoes_chave(). Trocar ou apagar o arquivo.
- relatorio_gate.py fail-closed contra a Etapa 1: hoje pode imprimir 'GATE: APROVADO — prosseguir a Etapa 3' e sair 0 sem consultar o estado da Etapa 1, que esta REPROVADA. Mais: PF vira float('inf') quando nao ha perdedores (:128) e passa em `pf > 1.3`; --capital e float livre de argv (:164) e alimenta MDD e retorno; duracao e a distancia entre o primeiro e o ultimo trade (:181-182), nao uptime continuo.
- scripts/purgar_fixtures_producao.py: o criterio no1 (`order_id.startswith('SIM-')`, :70) casa com TODA posicao legitima de paper, porque executor.py:405 gera esse formato em simulacao — o modo em que o BXBotWorker roda 24/7. Adicionar a mesma guarda que o Executor ja tem (`if not self.simulacao`, executor.py:1190). Hoje `--confirmar` apaga todo o estado de paper.
- analise_mercado.py: adicionar timeout nas 6 requisicoes (:18,:32,:58,:75,:79,:85). Um socket pendurado trava relatorio_completo() para sempre, e como start_health_server (main.py:1325) roda ANTES, o worker fica vivo, /health responde 200 e o loop de trading nunca comeca — apagao que o NSSM nao detecta.
- Aposentar para _legado/ com LEIA-ME e plano de rollback (@Zeta): backtesting/motor_otimizado.py (291 LOC, codigo morto confirmado), ai/ollama_client.py (312 LOC, zero importador de producao, documentado como 'Alta'), config/settings_template.py (arma o vetor de config/settings.py), e monitor_fluxo.py se cvd_historico continuar sem leitor (alternativa: run_forever com reconnect=True, :149, seguindo o padrao ja correto em dashboard.py:494-497).
- CI: black/isort/flake8 falham desde o primeiro run — deixar verdes e torna-los bloqueantes; ativar branch protection na main (que dispara deploy).

**Critério de saída.** `black --check . && isort --check . && flake8 && pytest tests/ -v` exit 0 no CI. `gh api repos/<owner>/<repo>/branches/main/protection` retorna uma regra ativa. relatorio_gate.py executado contra o banco atual imprime REPROVADO e sai com codigo != 0. `grep -rn 'import ai\.\|from ai' --include=*.py .` retorna 0 fora de _legado/ e tests/. purgar_fixtures_producao.py --confirmar em um banco de paper mantem as posicoes SIM- legitimas (teste automatizado).

**Estado em 2026-08-11 (commits `b559a52` + `344f0bb`, mais os anteriores da frente).**

| Critério | Estado |
|---|---|
| `testar_api.py` reprova chave read-only | ✅ rodado ao vivo: exit 1, "a chave NAO pode enviar ordens spot (read-only)" |
| `relatorio_gate.py` REPROVADO + exit != 0 | ✅ rodado ao vivo contra o banco atual |
| purga preserva posições `SIM-` de paper | ✅ `tests/test_purga_preserva_paper.py` (9 testes) |
| timeouts em `analise_mercado.py` | ✅ 5 chamadas, `TIMEOUT_HTTP=10`, teste que conta `requests.get` vs `timeout=` |
| zero importador de `ai/` fora de `_legado`/`tests` | ✅ módulo movido para `_legado/ollama_client.py` |
| `monitor_fluxo.py` aposentado | ✅ `cvd_historico` não tem nenhum SELECT no repo |
| `black --check .` | ✅ passa e **bloqueia** |
| `isort --check .` | ✅ passa e **bloqueia** |
| `pytest tests/` | ✅ 1626 passed, 5 skipped |
| `flake8` exit 0 | ✅ **0 achados** (eram 2.170) — `\|\| true` removido, o passo **bloqueia** |
| `pip-audit` exit 0 | ✅ PYSEC-2026-3447 resolvido com `setuptools>=83.0.0` nos 3 jobs |
| branch protection na `main` | ✅ ativa (ver abaixo) |

**Pipeline inteiro verde no runner** — run [31542342765](https://github.com/orgateccloud-bot/CryptoXBot/actions/runs/31542342765): Lint ✓ 52s, Tests ✓ 2m10s, Security ✓ 30s.

**Branch protection** (`PUT /repos/orgateccloud-bot/CryptoXBot/branches/main/protection`):

| Regra | Valor | Por quê |
|---|---|---|
| `required_status_checks.contexts` | Lint, Tests 3.11, Security | os 3 jobs precisam passar |
| `required_status_checks.strict` | `true` | o branch precisa estar atualizado com a `main` antes do merge |
| `allow_force_pushes` | `false` | trava por configuração a regra que era só convenção ("NUNCA push --force na main") |
| `allow_deletions` | `false` | a `main` dispara deploy; apagá-la é irreversível |
| `enforce_admins` | **`false`** | ver ressalva |

Ressalva sobre `enforce_admins: false`: o dono do repositório **continua conseguindo dar push direto na `main` sem CI verde**. Isso é deliberado — é o fluxo em uso hoje (não há PRs, é um operador só) e ligar `enforce_admins` transformaria cada correção urgente em produção num PR que espera 3 minutos de CI. O que a proteção garante hoje, incondicionalmente, é: nada de force-push, nada de deletar a branch, e qualquer PR de terceiro precisa dos 3 checks. Para tornar o gate absoluto: `gh api -X PUT repos/orgateccloud-bot/CryptoXBot/branches/main/protection/enforce_admins`.

Sobre os 2.170: **1.966 estavam em `.claude/worktrees/`**, que são cópias do próprio repositório em estados antigos, criadas por sessões paralelas — o CI lintava o passado do projeto. Excluído esse diretório (mais `_legado/`, `.venv/`, `scratch_vbt/` e `config/settings.py`, que é gitignored e não existe no CI), restaram 139 no código vivo, todos zerados:

| Código | n | Como foi resolvido |
|---|---:|---|
| E501 linha > 100 | 54 | literais quebrados em concatenação implícita; SQL quebrado em duas linhas (whitespace-insensitive); um ternário aninhado extraído para variável |
| E402 import fora do topo | 19 | `per-file-ignores` no `.flake8` — o import vem **depois** de `sys.path.insert(...)`, que é o que o torna resolvível; subir quebra o módulo |
| F401 import não usado | 17 | removidos |
| F541 f-string sem placeholder | 14 | prefixo `f` retirado |
| E741 variável `l` | 12 | `lo` (lows) nos builders, `lim` nos lambdas de `_klines` |
| E231 falta espaço após vírgula | 10 | corrigido |
| F841 local nunca lido | 8 | removidos |
| E221/E225/E302/E305/F824 | 5 | 3 eram de `config/settings.py` (fora do CI); `pnl_usdt>=0` espaçado; `global _estado_pares` inerte removido |

Dois defeitos reais apareceram durante a limpeza, ambos por `str.replace` sem `count`: `mr`/`nr` foram apagados de `avaliar_economia` em `research/edge_lab.py` (o F841 apontava só a outra ocorrência) e um comentário de `database.py` foi partido no meio. Os dois foram pegos por `F821 undefined name` e por `compileall` antes de qualquer commit.

Branch protection continua aberto porque ativar exige permissão de admin em `orgateccloud-bot/CryptoXBot` e porque a regra **bloquearia o push direto na `main`**, que é o fluxo em uso hoje — é decisão do operador, não mudança a aplicar em silêncio.

**Risco de não fazer.** As ferramentas que o operador usa para decidir sobre capital dao respostas erradas na direcao tranquilizadora, e o unico script destrutivo do repo apaga o estado de paper que a Etapa 2 esta acumulando.

## Critérios de saída globais

- TRAVA DE CAPITAL: nenhum caminho de codigo emite ordem real sem a conjuncao (--real) AND (ALLOW_REAL_TRADING=true) AND (DRY_RUN=false) AND (kill-switch inativo) AND (binance_conta.restricoes_chave() com pode_negociar_spot=True e pode_sacar=False). Provado por 5 testes em tests/test_modo_real.py, cada um falhando se a respectiva condicao for removida.
- VERDADE DO PAINEL: o rotulo de modo exibido em dashboard.py vem do estado efetivo do Executor (executor.simulacao publicado pelo worker), nao de DRY_RUN. Teste: worker em modo real + DRY_RUN=true faz o painel exibir REAL.
- SINAL POR PAR: 30 dias corridos de paper com ZERO blocos de log contendo stop >= preco de entrada, ZERO sinais duplicados no mesmo timestamp (SELECT symbol,timestamp,COUNT(*) FROM sinais GROUP BY 1,2 HAVING COUNT(*)>1 retorna 0 linhas) e ml_prob/regime/suporte_forte comprovadamente distintos entre BTCUSDT, ETHUSDT e SOLUSDT em >95% dos ciclos.
- TRACK RECORD UTILIZAVEL: 100% dos trades fechados no periodo tem sinal_id ligado, executado=1 e pnl_usdt NOT NULL (hoje: 0 de 5.255). relatorio_gate.py passa a reportar n>0 lendo o mesmo banco que o worker escreve.
- REPRODUTIBILIDADE: um unico comando re-deriva os 5 FAILs pre-registrados sobre um snapshot de dados com sha256 versionado, com diferenca 0.0 em todos os IC e vereditos, em duas maquinas diferentes.
- EXECUCAO PROVADA: suite de integracao contra Binance Spot Testnet com 0 posicoes sem stop apos 50 ciclos de fuzz, 0 ordens duplicadas sob 5xx/429, 0 PnL contabilizado duas vezes e 0 threads de monitor mortas com posicao viva.
- OBSERVABILIDADE: alerta CRITICAL chega ao operador em <= 60s ponta a ponta (bot_event gravado -> leitor -> Telegram entregue com message_id no retorno), provado por um teste de fumaca que roda no CI diariamente.
- HIGIENE: black --check . && isort --check . && flake8 && pytest tests/ -v tudo exit 0 no CI, com branch protection ativa na main.
- GATE: Etapa 2 so comeca quando os 8 criterios acima estiverem verdes, medindo a estrategia que efetivamente roda, com duracao contando UPTIME continuo (nao distancia entre o primeiro e o ultimo trade). Etapa 3 (capital piloto) permanece bloqueada enquanto nao existir edge aprovado em hold-out NOVO, definido por data, pre-registrado antes da primeira medicao.

## O que NÃO fazer

- NAO ligar --real / ALLOW_REAL_TRADING confiando em DRY_RUN como freio. DRY_RUN (config/runtime_settings.py:128) nao e lido por nenhum caminho de execucao — os unicos leitores sao dashboard.py:648,709-711. A decisao real e `simulacao = not args.real` (main.py:1251). Pior: o painel exibira SIMULACAO enquanto ordens reais saem.
- NAO usar o numero de GET /api/backtest (+2,54%, PF 1,01, Sharpe 1,04, veredito 'ESTRATEGIA PROMISSORA') para absolutamente nada. Corrigindo so o look-ahead do MTF vai a -31%; corrigindo tambem a taxa de futures para SPOT, a -42,67%. O veredito ainda esta replicado em JavaScript em templates/backtest.html:251-265, entao corrigir o Python nao limpa a tela.
- NAO citar os Sharpes de config/params_pares.py (3,24 / 1,79 / 2,98) como evidencia de nada. Grid de ate 8.000 combinacoes sem out-of-sample, ordenado por Sharpe calculado sobre retorno BRUTO de taxa e de sizing, anualizado a 252 sobre retornos POR TRADE, com piso de 5 trades. E o ultimo commit do arquivo e anterior a versao atual do otimizador — a procedencia e inauditavel.
- NAO rodar `python scripts/purgar_fixtures_producao.py --confirmar` no deploy atual. O criterio no1 (`order_id.startswith('SIM-')`, :70) casa com TODA posicao legitima de paper, porque executor.py:405 gera esse formato em simulacao — o modo em que o BXBotWorker roda 24/7. O Executor tem a guarda `if not self.simulacao` (:1190); o script nao tem.
- NAO rodar `scripts/migrate_sqlite_to_supabase.py --confirmar` antes de I-13. _insert_sinais omite preco_saida, pnl_usdt, pnl_pct e barreira_tocada (:204-227), apagando o resultado de todos os trades fechados; e um segundo run duplica a tabela `sinais` inteira, dobrando n e PnL lidos pelo gate.
- NAO seguir a instrucao de config/settings_template.py:2 ('COPIE este arquivo para settings.py'). Isso cria exatamente o arquivo cujo fallback (config/runtime_settings.py:61-62 e :73-74) tem PRECEDENCIA sobre o default SPOT e migraria executor, risco, klines, dashboard e binance_conta para fapi.binance.com em bloco e em silencio, alem de injetar a credencial commitada.
- NAO reusar hold-out consumido, nem re-registrar criterio, nem promover variante secundaria. Os hold-outs de trend e de carry estao QUEIMADOS por escrito; o temporal do edge_lab ja esta 43,7% contaminado por deriva de fronteira. Um FAIL pre-registrado nunca e revogado.
- NAO interpretar um dry run bonito do modo trend como aprovacao de edge. A estrategia reprovou no hold-out (+5,70% a.a. contra piso de 8%) e o hold-out foi consumido. O dry run so pode produzir telemetria de EXECUCAO, jamais evidencia de edge — e hoje nem isso, porque MODO_TREND=False (main.py:87) e a flag nao esta no ExecStart do BXBotWorker.
- NAO tentar 'consertar a estrategia' contra backtesting/motor.py. Nove dos onze componentes do score sao constantes mockadas (motor.py:197-204 mais o CVD, inerte por construcao matematica): o harness nao executa a estrategia que diz medir, e otimizar contra ele piora o sistema.
- NAO confiar em `python testar_api.py` para dizer que a chave pode operar. Ele imprime `permissions` de /api/v3/account, que e da CONTA, e conclui 'Chaves de API validadas com sucesso!' (:51-53). Uma chave read-only recebe luz verde. A resposta correta esta em binance_conta.restricoes_chave() e essa funcao nunca e chamada em producao.
- NAO ligar OCO_BRACKET nem RECONCILIAR_BOOT_EXCHANGE direto em producao. Sao ~350 LOC atras de flags default-off, ausentes do .env, que NUNCA executaram — o primeiro exercicio real delas nao pode ser com dinheiro dentro.
- NAO tratar 'a metrica existe' como 'a metrica e lida'. bot_events tem 14 writers e ZERO SELECT em todo o repositorio; /metrics nao tem scraper (monitoring/prometheus.yml aponta para host/porta inexistentes e a intersecao com dashboard.json e zero); /ready nao tem probe; log_trades tem 0 linhas apos 4 meses. Este projeto tem historico de codigo implementado, cabeado, testado e inerte.
- NAO medir ETHUSDT ou SOLUSDT com backtesting/walk_forward.py no estado atual: ele nunca le config/params_pares e aplica silenciosamente os limiares do BTC (rsi 42/60, score 60/70) a pares cujos valores reais sao 38/62 e 50/65.
- NAO 'consertar' o capital fantasma aumentando o fallback de 100 USDT (main.py:1100). O defeito e que risco.get_saldo_usdt devolve 0.0 tanto para conta vazia quanto para FALHA de API; a correcao e usar binance_conta.saldo(), que ja expressa a distincao e ja esta testado.
- NAO comecar a contar os 90 dias da Etapa 2 antes de E-7 e E-8. Rodar o relogio sobre um sinal contaminado por BTC, com sizing aleatorio em 3 de 4 entradas e com zero trades gravando pnl_usdt significa chegar ao fim de 90 dias sem nada medido.
- NAO fazer push --force na main (dispara deploy) e nao commitar .env, credenciais ou data/*.db.
