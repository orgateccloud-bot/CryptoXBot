# Relatório de módulos — CryptoXbot

> Gerado em 2026-08-07 a partir de auditoria multiagente:
> 15 lotes mapeados por um agente cada e **refutados adversarialmente** por um
> cético independente, depois consolidados num scorecard calibrado entre si.
> 31 agentes, 5,6M tokens. Toda afirmação tem evidência `arquivo:linha`.

## Veredicto

**Nota global: 2/10 — NÃO APTO A CAPITAL REAL.**

NÃO APTO A CAPITAL REAL, e a distância não é de engenharia — é de edge. O sistema roda 24/7, está de pé e a camada de infraestrutura corrigida nesta semana é real e verificável. Mas os quatro portões em série que autorizam dinheiro estão todos fechados ao mesmo tempo: (1) o edge está reprovado com critério pré-registrado em cinco hipóteses e na Etapa 1 do gate, e a régua que reprovou mede uma estratégia que não é a que roda; (2) o sinal de 2 dos 3 pares é a leitura do BTC — regime, ensemble, ML e suporte —, o que produz de forma determinística ordens com stop acima da entrada; (3) o gate de risco dimensiona contra um stop que não é o enviado, o limite de drawdown acumulado nunca é avaliado e não existe kill-switch em lugar nenhum do repositório; (4) o caminho de execução real nunca rodou uma vez e falha de forma composta no primeiro trade, por comissão em ativo-base não descontada seguida de `abrir_long` que retorna True com a posição desprotegida. Sobre isso tudo, a rede de captura também está inerte: 0 de 8 alertas de Telegram entregues, bot_events com 14 escritores e zero leitor, /metrics sem scraper, /health 200 incondicional. O ativo mais valioso do projeto continua sendo o rigor dos FAILs pré-registrados — e ele está corroendo: os vereditos não são reproduzíveis hoje (banco não versionado com janela rolante, hold-out definido como fração de tabela mutável, tabela `funding` inexistente até nos backups), e o único hold-out temporal sobrevivente já está contaminado por construção. A prioridade não é corrigir os 45 módulos: é (a) congelar o substrato de pesquisa por data fixa e hash para que o FAIL volte a ser demonstrável, (b) eliminar a contaminação BTC no sinal ou reduzir o bot a mono-par explicitamente, (c) fechar os três buracos do executor que transformam o primeiro trade real em posição nua, e (d) ligar um canal de alerta que entregue, antes de qualquer decisão sobre capital.

## Panorama

| | |
|---|---|
| Módulos auditados | 45 |
| LOC total | 16.517 |
| Módulos nota ≤ 3 (🔴) | 39 de 45 |
| LOC em módulos nota ≤ 3 | 11.831 (71% do código) |
| Módulos nota ≥ 7 (🟢) | 1 |

### Nota média por camada

| Camada | Média | Módulos |
|---|---:|---:|
| Superfícies (UI/health) | 2.3 | 3 |
| Sinal | 2.6 | 9 |
| Ferramentas e periferia | 2.9 | 7 |
| Execução | 3.0 | 2 |
| Configuração | 3.0 | 4 |
| Pesquisa e backtest | 3.3 | 11 |
| Dados e persistência | 3.3 | 7 |
| Risco | 4.0 | 2 |

As duas camadas que decidem se o dinheiro sobrevive — execução (3,0) e sinal
(2,6) — estão entre as piores. Risco tem a maior média (4,0) e mesmo assim
nenhum número que ele produz descreve a ordem que sai.

## Como as notas foram calibradas

A média aritmética das 45 notas é exatamente 3,0 e ela engana por três motivos. (1) ESCALA ANCORADA: calibrei para que a nota signifique a mesma coisa em qualquer camada — 7-8 = correto, coberto por teste que prova propriedade e não só valor, e com consumidor vivo (só backtesting/metricas.py chega lá, e mesmo ele tem o único caminho de produção inerte); 5-6 = bom código adotado pela metade (binance_conta, data/klines, validacao, trend_following, edge_lab); 3-4 = executa e faz algo, mas nenhum número que produz descreve o que promete (executor, main, risco, database, walk_forward); 2 = existe, é chamado e o produto líquido é enganoso (dashboard grava sinais fantasma, logger reporta zeros fabricados, telegram entrega 0 de 8, testar_api declara chave válida); 1 = a saída não mede nada (motor.py, otimizador, motor_vectorbt, motor_otimizado). Sob essa régua, 'nunca executou' e 'executa e mente' não podem receber nota média só porque o código é bonito — foi por isso que rebaixei trend_live de 4 para 2 e ollama_client de 3 para 2. (2) A MÉDIA PREMIA MASSA: 12 dos 45 módulos são de pesquisa/backtest e 7 são ferramentas periféricas; eles diluem o peso das quatro camadas que decidem se o dinheiro sobrevive (execução, risco, sinal, dados primitivos). Se ponderasse por LOC seria pior ainda — executor.py e main.py sozinhos são 18% do código e ambos são 3. (3) PRONTIDÃO É CONJUNÇÃO, NÃO SOMA. Prontidão = min sobre quatro portões em série, e o resultado é o mínimo, não a média: (a) EDGE — cinco hipóteses reprovadas com critério pré-registrado, Etapa 1 do gate reprovada em 4 de 5, e a régua que reprovou mede stop/target 2%/4% que não são os 1,5%/5% de produção; a régua está errada nas DUAS direções, então nem uma futura aprovação valeria. Portão fechado, e nenhuma engenharia a jusante o abre. (b) SINAL — regime idêntico entre pares em 2.278 de 2.278 ciclos, ml_xgb bit-idêntico, suporte de BTC aplicado a ETH: 38 dos 100 pontos de score de dois terços dos pares vêm de outro ativo, e o resultado registrado em log é COMPRA com stop acima da entrada, de forma determinística. (c) RISCO — o tamanho aprovado é calculado contra um stop sintético que não é o enviado, MAX_DRAWDOWN_TOTAL nunca é avaliado, kelly é fail-open no teto, e não existe kill-switch em nenhum arquivo do repositório. (d) EXECUÇÃO — o núcleo puro é correto e roda em paper todo dia, mas 10 métodos bifurcam por `if self.simulacao`: o grafo que receberia capital tem zero execuções e falha de forma composta no primeiro trade (comissão em BTC → stop rejeitado → abrir_long retorna True com posição nua). Por cima dos quatro, a camada de detecção também está fechada: 0 de 8 alertas de Telegram entregues, bot_events com 14 escritores e zero leitor, /metrics sem scraper. Ou seja, os quatro portões falham E a rede de captura falha. Por isso nota_global = 2 e não 3: o sistema não está a alguns bugs de operar, está a uma hipótese de edge de distância — e hoje ele nem consegue provar para si mesmo que não a tem, porque os cinco FAILs pré-registrados não são reproduzíveis (banco não versionado, hold-out por fração de tabela mutável, tabela `funding` inexistente). Nota 3 exigiria que o veredito negativo fosse auditável; nota 4 exigiria pelo menos um dos quatro portões aberto com evidência. A única razão de não ser 1 é que a camada de infraestrutura recém-corrigida (retry dos WS sem teto, conftest isolando o banco, _posicao_e_plausivel, health /ready coerente, preco_medio_fill) é real, verificável linha a linha e coberta — não é fachada.

## Os piores

- backtesting/otimizador.py (1) — look-ahead de 47,4h em 100% das barras + grid de 8000 sem OOS, e a saída disso governa o trading ao vivo via config/params_pares.py
- backtesting/motor.py (1) — sobrescreve a posição 12.749 vezes e mockou 9 de 11 componentes do score; alcançável por `main.py --backtest`
- backtesting/motor_vectorbt.py (1) — 391 LOC que nem importam (dependência ausente), com paridade quebrada em sizing e escala de retorno
- backtesting/motor_otimizado.py (1) — código morto confirmado com look-ahead num filtro que é gate booleano; aposentar para _legado/
- estrategias/otimizada.py (2) — o override de stop pelo suporte de BTC (207-210) produz stop acima da entrada em 100% das compras de ETH/SOL
- suporte.py (2) — causa raiz do stop 34x (SYMBOL fixo em 29 e detectar_suportes sem par em 104), com testes que codificam o hardcode como contrato
- telegram_bot.py (2) — único canal de escalonamento do sistema, com 10 call sites reais e 0 de 8 alertas entregues, falhando em silêncio absoluto
- testar_api.py (2) — a ferramenta que o guia manda rodar antes de operar declara 'chaves validadas' lendo a permissão da CONTA, não da CHAVE
- data/cvd_calculator.py (2) — componente de 7% do score matematicamente incapaz de sair de 50/51, com testes que mockam valores irreais

## Os melhores

- backtesting/metricas.py (7) — biblioteca pura, determinística, com testes de propriedade e não só de valor; a única do lote cujos pontos fortes sobreviveram integralmente à refutação
- validacao.py (6) — purge/embargo aritmeticamente corretos contra o rótulo real, sem I/O, sem estado, consumido pelo caminho de retreino
- binance_conta.py (5) — contrato de erro explícito (float, str|None), offset de relógio que não se auto-zera em falha, -1021 com re-sync; adotado pela metade
- data/klines.py (5) — lock único correto, chave normalizada, timestamp só em sucesso, REST_BASE_URL respeitado; consolidação real de 4 dos 7 fetchers
- backtesting/trend_following.py (5) — causalidade do Donchian provada por teste, contabilidade verificada numericamente, custo de spot honesto (0,10%/lado, único do diretório)
- research/edge_lab.py (5) — pré-registro datado, corte anti-vazamento conservador, e 18 testes que provam que o harness NÃO fabrica edge falso sob null autocorrelacionado

## Scorecard por módulo

### Execução

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `executor.py` | 1506 | **3** | infra | Comissão no ativo-base não é descontada: `tamanho_btc = executedQty` bruto (executor.py:791-793) dimensiona proteção (810) e saída (919-923); o stop toma -2010, `_colocar_stop_exchange` devolve None e `abrir_long` não checa (810 vs `return True` em 889) — posição real aberta e DESPROTEGIDA no primeiro trade. |
| 🔴 | `main.py` | 1422 | **3** | infra | Nenhuma validação de `0 < stop < preco < target` antes de abrir_long (main.py:1084-1167), enquanto o caminho trend valida (main.py:909-911). Combinado com suporte BTC-hardcoded, 2 de 3 pares emitem COMPRA com stop ~30x acima da entrada de forma DETERMINÍSTICA, não eventual. |

### Risco

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `risco.py` | 756 | **3** | infra | validar_trade dimensiona contra um stop SINTÉTICO de 1,5% (risco.py:637) que não é o stop que o executor instala — `risco_usdt` e `tamanho_btc` não descrevem o risco da ordem. MAX_DRAWDOWN_TOTAL só aparece no payload de display (35 vs 734) e não existe kill-switch em todo o repo. |
| 🟡 | `binance_conta.py` | 222 | **5** | infra | restricoes_chave() — a função que responde 'esta chave pode mandar ordem?' — não tem call site de produção (definida em 175, único chamador no __main__ da linha 221), e chave_configurada() (44,51-56) não reconhece como placeholder a única chave concreta do projeto, que o dashboard reconhece. |

### Sinal

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `suporte.py` | 428 | **2** | edge | SYMBOL='BTCUSDT' fixo (suporte.py:29) e detectar_suportes não aceita símbolo (104) — causa raiz do stop de $63.521 num trade de ETH, e o chamador não tem como contornar sem editar o arquivo. |
| 🔴 | `estrategias/otimizada.py` | 358 | **2** | edge | otimizada.py:207-210 sobrescreve o stop percentual pelo suporte_forte sempre que este for maior, e sup.detectar_suportes('1h') (193) sempre consulta BTCUSDT — com BTC ~64k e ETH ~2k a condição é verdadeira em 100% das compras de ETH/SOL: stop acima da entrada, ordem de proteção rejeitada, posição nua. |
| 🔴 | `lstm_modelo.py` | 302 | **2** | edge | O artefato em produção tem n_iter_=19 com max_iter=200 e n_iter_no_change=15 — a rede parou na ~4ª época útil. Um MLP de 517 entradas praticamente não treinado, cv_auc 0,5685, é promovido sem gate (lstm_modelo.py:211-225 grava incondicionalmente e de forma não atômica). |
| 🔴 | `ensemble.py` | 220 | **2** | infra | prever() não aceita symbol (ensemble.py:44) e o guard `hasattr(ens_mod,'symbol')` de main.py:1014 é permanentemente False — ETH e SOL recebem a probabilidade e o regime do BTCUSDT nos 20 pontos de maior peso do score, com o guard fazendo o bug parecer tratado em code review. |
| 🔴 | `estrategias/trend_live.py` | 123 | **2** | edge | Nenhum call site ativo: MODO_TREND=False (main.py:87) e o serviço roda `main.py --simulacao --intervalo 15` sem --modo-trend — nem o dry run de validação de EXECUÇÃO, única razão declarada do módulo, está rodando. |
| 🔴 | `ml_filtro.py` | 354 | **3** | edge | Skew treino/inferência em dist_vwap (2ª feature mais importante): ind.vwap é CUMULATIVO, o treino passa ~17.500 velas e prever() passa 100 (ml_filtro.py:72 vs 303). Medido na mesma barra: -0,1964 vs -0,0062, e o pickle devolve 0,4787 contra 0,2220 — 25,7 p.p. de erro vindo de janela, não de mercado. |
| 🔴 | `regime.py` | 265 | **3** | edge | SYMBOL='BTCUSDT' é constante de módulo (regime.py:23) e detectar() não aceita símbolo (151): medido em log_avaliacoes, o regime foi IDÊNTICO entre pares em 2.278 de 2.278 ciclos, divergente em zero. O componente macro de 18% de ETH e SOL é a leitura do BTC. |
| 🔴 | `fear_greed.py` | 186 | **3** | infra | `except Exception` sem log devolve valor=50 (fear_greed.py:126-137) e score.py:234 mapeia 50 para 100 — é o único componente cujo modo-falha PREMIA, e ele apaga os dois bloqueios de sentimento. O módulo não importa logging nem health: a falha é absolutamente muda. |
| 🟡 | `score.py` | 502 | **4** | edge | Os bloqueios absolutos não cobrem o que o projeto declara evitar: com F&G=23 (fear_greed.py:91 diz pode_operar=False) o log registra 16 sinais COMPRA, e com regime=TENDENCIA_BAIXA registra 20 — porque score.py:372-375 usa 20/80 e score.py:370 não lista BAIXA nem INDEFINIDO. |

### Dados e persistência

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `logger.py` | 537 | **2** | infra | dados_relatorio_diario filtra `timestamp LIKE 'AAAA-MM-DD%'` (logger.py:468-484) enquanto otimizada.py:222 grava 'dd/mm/aaaa': medido no banco vivo, 0 linhas casam contra 15 reais. O `or 0.0` de logger.py:496-502 converte None em zero e o alerta diário reporta um sistema saudável que não está sendo lido. |
| 🔴 | `data/cvd_calculator.py` | 104 | **2** | edge | divergence_score = tanh(slope/std) tem teto matemático de 0,0693 com window_size=50, contra o limiar de 0,1 exigido em score.py:147. Medido em 1000 amostras reais: cvd_trend=0 em 1000/1000 e _score_cvd=51 em 1000/1000 — o componente de 7% é uma constante disfarçada de sinal. |
| 🔴 | `backtesting/coletar_dados.py` | 193 | **3** | infra | A janela termina em datetime.now(), então a última vela baixada é a AINDA ABERTA, e INSERT OR IGNORE garante que nunca será corrigida: medi 3 velas parciais congeladas em BTCUSDT/1h (detectadas por quebra da cadeia abertura[i+1]==fechamento[i]) e TODAS estão dentro do hold-out. |
| 🔴 | `research/coletar_funding.py` | 128 | **3** | infra | criar_tabela() é chamado incondicionalmente antes de qualquer download (linha 117) e a tabela `funding` não existe em nenhuma das três cópias do banco — não há evidência de que este coletor tenha produzido um artefato sobrevivente. Erro de rede faz break e grava parcial com exit 0. |
| 🟡 | `database.py` | 1027 | **4** | infra | `row[0]` sobre linha entregue como dict pelo pool com row_factory=dict_row (database.py:581 e 990): salvar_sinal e historico_cv_auc_modelo levantam KeyError em TODA chamada no backend Postgres — o alvo de deploy documentado. Em executor.py:997 a chamada não tem try/except e roda depois de registrar_resultado. |
| 🟡 | `indicadores.py` | 235 | **4** | edge | Duas definições incompatíveis de VWAP no mesmo sistema: o vivo usa ind.vwap CUMULATIVO (otimizada.py:97, suporte.py:140, ml_filtro.py:72) e todo o backtest usa vwap_rolling(20) — os parâmetros de params_pares.py foram calibrados com uma e o bot opera com a outra. |
| 🟡 | `data/klines.py` | 91 | **5** | infra | O fallback de dado antigo (klines.py:91) não tem teto de idade nem flag: risco.verificar_volatilidade — o circuit breaker — mede a variação de uma hora atrás sem saber. Fail-open na função cujo propósito é ser fail-closed, e o módulo não importa logging: nenhuma falha deixa rastro. |

### Superfícies (UI/health)

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `dashboard.py` | 801 | **2** | infra | O processo de UI executa a estratégia e GRAVA no banco de produção: dashboard.py:318 chama otimizada.analisar a cada 30s por par e otimizada.py:276 persiste em `sinais`. Medido: ~96% das linhas de sinal de BTCUSDT têm a cadência de 30s do dashboard, e nas últimas 24h 100% das linhas de `trades` vieram dele sem trade_id. |
| 🔴 | `telegram_bot.py` | 166 | **2** | infra | A guarda testa vazio em vez de validade (telegram_bot.py:31) e o .env traz 'your_telegram_bot_token_here', que é truthy: o POST vai para uma URL inválida, recebe 404 e a linha 45 devolve False sem print, sem log e sem bot_event. 0 de 8 alertas entregues, incluindo 'posição sem registro no banco'. |
| 🔴 | `health.py` | 236 | **3** | infra | Nenhum dos três endpoints tem consumidor: o único match de '/health' em deploy/ é um echo em setup.sh:76, o unit só tem Restart=on-failure, e /metrics é servido em 0.0.0.0 sem auth (health.py:234) expondo pnl_dia e drawdown. /health é 200 incondicional (158-162). |

### Pesquisa e backtest

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `backtesting/motor.py` | 407 | **1** | edge | Não há guard `if posicao is None` antes da entrada: a posição aberta é sobrescrita silenciosamente 12.749 vezes em 17.512 barras, com o trade anterior apagado sem PnL. O motor irmão TEM o guard (motor_ensemble.py:445) — a divergência entre os dois é o próprio bug. |
| 🔴 | `backtesting/otimizador.py` | 391 | **1** | edge | Look-ahead médio de 47,4h em 100% das barras (idx_1h//4, linha 64) num grid de até 8000 combinações sem hold-out, walk-forward ou correção de multiplicidade — e a saída disso está hardcoded em config/params_pares.py, lida no caminho vivo por otimizada.py:70. |
| 🔴 | `backtesting/motor_vectorbt.py` | 391 | **1** | infra | Nunca executou: `import vectorbt` no topo (linha 64) com a dependência não instalada, sem venv no repo, teste de paridade sempre skipped e CI que nunca instala requirements-backtest. Se rodasse, a paridade quebra em duas dimensões (fator 0,5 virou booleano; retorno líquido vs bruto). |
| 🔴 | `backtesting/motor_otimizado.py` | 291 | **1** | manutencao | Código morto confirmado: grep em todo o repositório devolve uma única referência fora do arquivo (uma linha de doc). Carrega o mesmo look-ahead de ~47h num filtro que aqui é GATE booleano duro, invalidando o único propósito do módulo — medir a contribuição de cada filtro. |
| 🔴 | `research/carry_lab.py` | 253 | **2** | infra | Não executa: `python research/carry_lab.py` termina em sqlite3.OperationalError 'no such table: funding', e a tabela não existe no banco vivo nem nos dois backups POSTERIORES à medição documentada. O FAIL de carry existe apenas como prosa em METODOLOGIA_CARRY.md. |
| 🔴 | `backtesting/motor_ensemble.py` | 697 | **3** | edge | Servido ao vivo por GET /api/backtest com look-ahead de 44-46h no filtro MTF (idx//4, linha 350) e taxa de futuros 0,04% num bot SPOT (linha 37). Reproduzido: os +2,54% / PF 1,01 exibidos viram -42,67% / PF 0,75 / capital $573 ao corrigir as duas linhas. |
| 🟡 | `backtesting/walk_forward.py` | 625 | **4** | infra | Mede uma estratégia que não existe: STOP_PCT/TARGET_PCT hardcoded em 2,0%/4,0% (linhas 75-76) contra 1,5%/5,0% de produção, sem take-profit parcial, sem breakeven, sem trailing, com score de 9 componentes e features cujo dist_vwap diverge por ordem de grandeza do que roda ao vivo. |
| 🟡 | `backtesting/trend_following.py` | 594 | **5** | edge | O backtest sai por CLOSE abaixo do canal (linha 110) enquanto ao vivo o canal vira STOP na exchange, que dispara em pavio intrabar — são dois sistemas, e o mais penalizado por whipsaw é o que receberia capital. Agravante: --holdout é silenciosamente IGNORADO no ramo default da CLI (linha 590). |
| 🟡 | `research/edge_lab.py` | 517 | **5** | edge | O hold-out é uma FRAÇÃO de tabela mutável (HOLDOUT_FRAC sobre len(f), edge_lab.py:47,226), não um intervalo de datas: toda coleta move a fronteira. O hold-out virgem de hoje começa em 2025-07-21 e engole período que já foi pesquisado — o único hold-out sobrevivente está contaminado por construção. |
| 🟡 | `validacao.py` | 146 | **6** | edge | O número rotulado 'AUC honesto (purged CV)' vem de np.array_split sem restrição de anterioridade (validacao.py:42-43): em 4 de 5 folds o treino contém dados posteriores ao teste. E detectar_drift nunca disparou — com base congelada o histórico é bit-idêntico e o alerta iria para bot_events, que tem 14 writers e zero leitor. |
| 🟢 | `backtesting/metricas.py` | 143 | **7** | manutencao | deflated_sharpe_ratio devolve PSR puro quando sharpes_trials é None, com o mesmo tipo e faixa, e 4 de 5 callers gravam o resultado sob a chave 'dsr'. E cvar_historico — único consumidor de produção — nunca executa: sinais_executados devolve lista vazia (0 de 5.255 sinais têm pnl). |

### Configuração

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `config/settings.py` | 81 | **2** | infra | REST_BASE_URL='https://fapi.binance.com' e WS_BASE_URL='wss://fstream...' em nível de módulo (linhas 59-60) têm PRECEDÊNCIA sobre o default SPOT via runtime_settings.py:73-74 — remover duas linhas do .env migra executor, risco, klines e binance_conta para Futuros em bloco, sem um único log. |
| 🔴 | `config/params_pares.py` | 54 | **3** | edge | Os números vêm de grid search de 144 a 8000 combinações por par sem hold-out nem walk-forward (Sharpe 3,24 reportado contradiz a Etapa 1 reprovada), e o stop_pct por par — a principal razão de existir do arquivo — é ANULADO para ETH e SOL em otimizada.py:207-210. |
| 🔴 | `config/settings_template.py` | 26 | **3** | manutencao | Ensina a criar o config/settings.py que arma o fallback silencioso de mercado (runtime_settings.py:20,61-85) e propaga DB_PATH='data/btc_data.db' — o mesmo caminho do banco vivo que produziu o incidente de contaminação documentado em conftest.py. |
| 🟡 | `config/runtime_settings.py` | 165 | **4** | infra | DRY_RUN é uma trava fantasma: documentada em SETE artefatos versionados como a garantia de paper trading 'mesmo com chaves reais', e lida apenas pelo payload de /api/conexao (dashboard.py:648,709-711). A decisão real é main.py:1251 (`simulacao = not args.real`). |

### Ferramentas e periferia

| | Módulo | LOC | Nota | Trilha | Defeito principal |
|---|---|---:|---:|---|---|
| 🔴 | `ai/ollama_client.py` | 312 | **2** | manutencao | Zero importadores de produção — a busca no repo devolve apenas a própria docstring e quatro linhas de teste — enquanto docs/Modulos/ML e Sinais.md classifica o módulo como maturidade 'Alta'. Não existe sequer flag para ligá-lo. |
| 🔴 | `monitor_fluxo.py` | 149 | **2** | manutencao | 100% da saída persistida é write-only: cvd_historico não tem um único SELECT no repo, e `trades` só é lida por database.resumo_trades, que não tem chamador nenhum. Além disso run_forever sem `reconnect` — o padrão de reconexão existe no próprio repo (dashboard.py:492-497) e não foi copiado. |
| 🔴 | `testar_api.py` | 68 | **2** | infra | Responde à pergunta de go-live com o campo errado: imprime `permissions` de /api/v3/account, que é da CONTA, e conclui 'Chaves de API validadas com sucesso!' (linhas 51-53). A armadilha está documentada no próprio gate e a versão correta já existe pronta em binance_conta.restricoes_chave(). |
| 🔴 | `scripts/migrate_sqlite_to_supabase.py` | 411 | **3** | infra | A docstring declara idempotência que só risk_state tem: 4 tabelas fazem INSERT puro e `trades` só dedupe por trade_id, que o único produtor grava NULL. E o insert de `sinais` omite preco_saida/pnl_usdt/pnl_pct/barreira_tocada — migrar apaga o resultado de todo trade fechado. |
| 🔴 | `scripts/purgar_fixtures_producao.py` | 215 | **3** | infra | O critério `order_id.startswith('SIM-')` é INCONDICIONAL (linha 70), mas no modo operacional atual o próprio Executor gera 'SIM-<epoch>' para toda ordem legítima (executor.py:405): `--confirmar` apaga posições reais de paper. O executor gateia o mesmo teste com `not self.simulacao` e há teste exigindo o oposto. |
| 🟡 | `relatorio_gate.py` | 234 | **4** | infra | O ramo de veredito (linhas 225-230) imprime 'GATE: APROVADO — prosseguir à Etapa 3' e sai com código 0 sem nenhum interlock com o estado da Etapa 1, que o contrato pré-registrado exige (GATE_GO_LIVE.md:103) e que REPROVOU em 4 de 5 critérios. |
| 🟡 | `analise_mercado.py` | 185 | **4** | infra | Seis requests.get sem timeout no boot (linhas 18,32,58,75,79,85), executados ANTES do crash recovery de posição (main.py:1368 vs 949-960): um socket pendurado deixa posição persistida sem readoção e sem monitor, com /health já respondendo 200 e o NSSM sem motivo para reiniciar. |

## Justificativa módulo a módulo

### Execução

#### `executor.py` — 3/10 🔴

*1506 LOC · trilha infra*

Núcleo puro é correto e coberto (preco_medio_fill, snapping Decimal, avaliar_tick_monitor, _posicao_e_plausivel) e executa em paper todo dia. Mas 10 métodos bifurcam por `if self.simulacao`: o grafo que vai receber capital tem ZERO execuções, e nele a falha é composta e previsível já no dia 1. Sem kill-switch em todo o repo.

**Defeito principal:** Comissão no ativo-base não é descontada: `tamanho_btc = executedQty` bruto (executor.py:791-793) dimensiona proteção (810) e saída (919-923); o stop toma -2010, `_colocar_stop_exchange` devolve None e `abrir_long` não checa (810 vs `return True` em 889) — posição real aberta e DESPROTEGIDA no primeiro trade.

#### `main.py` — 3/10 🔴

*1422 LOC · trilha infra*

A camada de infraestrutura é real e verificável (retry dos WS sem teto, shutdown signal-safe, travas de boot com SystemExit, 71 testes verdes). O caminho do dinheiro — loop_par — não tem UM teste, tem corrida no teto de posições, scale-in que avança na falha e nunca no sucesso, e grava duas linhas em `sinais` por decisão.

**Defeito principal:** Nenhuma validação de `0 < stop < preco < target` antes de abrir_long (main.py:1084-1167), enquanto o caminho trend valida (main.py:909-911). Combinado com suporte BTC-hardcoded, 2 de 3 pares emitem COMPRA com stop ~30x acima da entrada de forma DETERMINÍSTICA, não eventual.

### Risco

#### `risco.py` — 3/10 🔴

*756 LOC · trilha infra*

Os gates 1, 2, 4 e 5 executam a cada ciclo e o vol targeting está fiado ponta a ponta. Mas dos 3 call sites, só 1 roda hoje; kelly é fail-open no teto; o saldo 0.0 vira 100 USDT fantasma no chamador; ~165 linhas de correlação são inalcançáveis. Nenhum número que ele produz descreve a ordem que sai.

**Defeito principal:** validar_trade dimensiona contra um stop SINTÉTICO de 1,5% (risco.py:637) que não é o stop que o executor instala — `risco_usdt` e `tamanho_btc` não descrevem o risco da ordem. MAX_DRAWDOWN_TOTAL só aparece no payload de display (35 vs 734) e não existe kill-switch em todo o repo.

#### `binance_conta.py` — 5/10 🟡

*222 LOC · trilha infra*

O melhor código do repo em contrato de erro: (float, str|None) explícito, nada levanta, offset de relógio preservado em falha de sync, -1021 com re-sync forçado, 302 linhas de teste. Adotado pela metade: o executor mantém um segundo relógio pior no caminho das ordens e o gate de go-live nunca pergunta a permissão da chave.

**Defeito principal:** restricoes_chave() — a função que responde 'esta chave pode mandar ordem?' — não tem call site de produção (definida em 175, único chamador no __main__ da linha 221), e chave_configurada() (44,51-56) não reconhece como placeholder a única chave concreta do projeto, que o dashboard reconhece.

### Sinal

#### `estrategias/otimizada.py` — 2/10 🔴

*358 LOC · trilha edge*

É a estratégia que roda em produção e tem duas suítes e2e, mas produz o defeito mais caro do sistema, chama a análise duas vezes por ciclo (gravando linha órfã em `sinais`), decide todos os pares com regime e ensemble do BTC, e dos 11 filtros anunciados apenas 2 gateiam — um deles vacuamente verdadeiro fora do BTC.

**Defeito principal:** otimizada.py:207-210 sobrescreve o stop percentual pelo suporte_forte sempre que este for maior, e sup.detectar_suportes('1h') (193) sempre consulta BTCUSDT — com BTC ~64k e ETH ~2k a condição é verdadeira em 100% das compras de ETH/SOL: stop acima da entrada, ordem de proteção rejeitada, posição nua.

#### `estrategias/trend_live.py` — 2/10 🔴

*123 LOC · trilha edge*

Melhor engenharia do lote de estratégias (anti-look-ahead explícito, paridade com o backtest por identidade de função, trava em duas camadas com SystemExit, 284 linhas de teste). Nota baixa mede aptidão, não elegância: o edge está REPROVADO com hold-out queimado e o código está dormente.

**Defeito principal:** Nenhum call site ativo: MODO_TREND=False (main.py:87) e o serviço roda `main.py --simulacao --intervalo 15` sem --modo-trend — nem o dry run de validação de EXECUÇÃO, única razão declarada do módulo, está rodando.

#### `suporte.py` — 2/10 🔴

*428 LOC · trilha edge*

60+ testes verdes sobre partes puras que não podem pegar nenhum dos dois bloqueantes: a fixture mocka `_klines(intervalo, limite)` codificando o hardcode como contrato, e um teste PINA a ausência de guarda de teto do ScaleIn. E o ScaleIn nunca passa da parcela 1, reaproveitando o tamanho do trade anterior nos seguintes.

**Defeito principal:** SYMBOL='BTCUSDT' fixo (suporte.py:29) e detectar_suportes não aceita símbolo (104) — causa raiz do stop de $63.521 num trade de ETH, e o chamador não tem como contornar sem editar o arquivo.

#### `lstm_modelo.py` — 2/10 🔴

*302 LOC · trilha edge*

Herda o skew de dist_vwap e o fossiliza no StandardScaler (47 das 517 entradas colapsam em ~-1,06 na inferência). BTCUSDT hardcoded em três pontos e aplicado aos 3 pares com 45% do peso do ensemble. Zero testes: nenhum arquivo tests/test_lstm*.

**Defeito principal:** O artefato em produção tem n_iter_=19 com max_iter=200 e n_iter_no_change=15 — a rede parou na ~4ª época útil. Um MLP de 517 entradas praticamente não treinado, cv_auc 0,5685, é promovido sem gate (lstm_modelo.py:211-225 grava incondicionalmente e de forma não atômica).

#### `ensemble.py` — 2/10 🔴

*220 LOC · trilha infra*

É o funil por onde todo o ML entra na decisão e perde justamente a dimensão symbol. pode_operar e confianca são decorativos (otimizada.py:168-172 não os consulta), a degradação para um modelo só não renormaliza nem alerta, e o FSRS documentado no CLAUDE.md não existe — o arquivo foi deletado.

**Defeito principal:** prever() não aceita symbol (ensemble.py:44) e o guard `hasattr(ens_mod,'symbol')` de main.py:1014 é permanentemente False — ETH e SOL recebem a probabilidade e o regime do BTCUSDT nos 20 pontos de maior peso do score, com o guard fazendo o bug parecer tratado em code review.

#### `regime.py` — 3/10 🔴

*265 LOC · trilha edge*

ADX correto do zero com suavização de Wilder e degradação sem exceção. Mas perdeu 2 das 3 proteções que alega: o hardcode e o gate VOLATILIDADE, que medi em 2 anos de klines e NUNCA disparou (0 de 22.555 janelas de BTCUSDT, máximo 2.49 contra limiar 2.5). Sobra o bloqueio LATERAL.

**Defeito principal:** SYMBOL='BTCUSDT' é constante de módulo (regime.py:23) e detectar() não aceita símbolo (151): medido em log_avaliacoes, o regime foi IDÊNTICO entre pares em 2.278 de 2.278 ciclos, divergente em zero. O componente macro de 18% de ETH e SOL é a leitura do BTC.

#### `fear_greed.py` — 3/10 🔴

*186 LOC · trilha infra*

reducao_alvo é wired e testado e2e — o único output que muda o comportamento do bot. O resto é fachada: pode_operar é computado, exibido e nunca gateia a compra (otimizada.py:169-172), há quatro tabelas de faixa divergentes para o mesmo índice, e nenhum teste executa uma linha do módulo.

**Defeito principal:** `except Exception` sem log devolve valor=50 (fear_greed.py:126-137) e score.py:234 mapeia 50 para 100 — é o único componente cujo modo-falha PREMIA, e ele apaga os dois bloqueios de sentimento. O módulo não importa logging nem health: a falha é absolutamente muda.

#### `ml_filtro.py` — 3/10 🔴

*354 LOC · trilha edge*

Executa em produção e tem virtudes reais (escrita atômica do pickle, retry com backoff, purged CV wired, 59 testes). Mas o vetor servido não é o vetor treinado, o rótulo usa o máximo dos FECHAMENTOS sem barreira inferior, a base está congelada em 2026-04-03 e o retreino semanal reproduz AUC bit-idêntico.

**Defeito principal:** Skew treino/inferência em dist_vwap (2ª feature mais importante): ind.vwap é CUMULATIVO, o treino passa ~17.500 velas e prever() passa 100 (ml_filtro.py:72 vs 303). Medido na mesma barra: -0,1964 vs -0,0062, e o pickle devolve 0,4787 contra 0,2220 — 25,7 p.p. de erro vindo de janela, não de mercado.

#### `score.py` — 4/10 🟡

*502 LOC · trilha edge*

Executa de verdade e move dinheiro (tamanho_fator multiplica a ordem em main.py:1107), pesos somam 100 com guard, e a cobertura unitária é a melhor do repo. Mas 15% do score é constante para 2 de 3 pares, _score_atr é 100 em 98,9-99,8% das janelas medidas, e log_avaliacoes não guarda score por componente — não há auditoria pós-morte.

**Defeito principal:** Os bloqueios absolutos não cobrem o que o projeto declara evitar: com F&G=23 (fear_greed.py:91 diz pode_operar=False) o log registra 16 sinais COMPRA, e com regime=TENDENCIA_BAIXA registra 20 — porque score.py:372-375 usa 20/80 e score.py:370 não lista BAIXA nem INDEFINIDO.

### Dados e persistência

#### `logger.py` — 2/10 🔴

*537 LOC · trilha infra*

Das nove operações analíticas, UMA tem call site de produção e funciona (registrar_avaliacao). log_trades e log_performance têm 0 linhas em 4 meses; a única leitura de produção está quebrada e mente na direção tranquilizadora. O fix operacional legítimo (_FormatterComExtra) não tem uma assertiva de teste.

**Defeito principal:** dados_relatorio_diario filtra `timestamp LIKE 'AAAA-MM-DD%'` (logger.py:468-484) enquanto otimizada.py:222 grava 'dd/mm/aaaa': medido no banco vivo, 0 linhas casam contra 15 reais. O `or 0.0` de logger.py:496-502 converte None em zero e o alerta diário reporta um sistema saudável que não está sendo lido.

#### `data/cvd_calculator.py` — 2/10 🔴

*104 LOC · trilha edge*

Aritmética do cumsum correta e convenção de sinal certa, com call site provado ponta a ponta — e é exatamente por isso que dói: o único módulo do lote com cadeia rastreável até o score entrega uma constante. Os testes de _score_cvd mockam calculate_cvd com valores que a implementação real jamais produz.

**Defeito principal:** divergence_score = tanh(slope/std) tem teto matemático de 0,0693 com window_size=50, contra o limiar de 0,1 exigido em score.py:147. Medido em 1000 amostras reais: cvd_trend=0 em 1000/1000 e _score_cvd=51 em 1000/1000 — o componente de 7% é uma constante disfarçada de sinal.

#### `research/coletar_funding.py` — 3/10 🔴

*128 LOC · trilha infra*

Schema idempotente correto (UNIQUE + INSERT OR IGNORE + índice), caminho derivado de __file__, guarda anti-loop. Mas é o fornecedor único de dados de um FAIL pré-registrado, tem zero testes, nenhuma validação de continuidade, e grava num .db gitignored em vez de artefato versionável.

**Defeito principal:** criar_tabela() é chamado incondicionalmente antes de qualquer download (linha 117) e a tabela `funding` não existe em nenhuma das três cópias do banco — não há evidência de que este coletor tenha produzido um artefato sobrevivente. Erro de rede faz break e grava parcial com exit 0.

#### `backtesting/coletar_dados.py` — 3/10 🔴

*193 LOC · trilha infra*

Entregou o substrato de todo o projeto com ZERO buracos (17.562 deltas de 1h, todos exatamente 3.600.000 ms). Mas o conjunto é a união cumulativa não-registrada das coletas de um .db com caminho RELATIVO, o contador conta tentativas em vez de rowcount, e não há um teste do mapeamento k[1..5].

**Defeito principal:** A janela termina em datetime.now(), então a última vela baixada é a AINDA ABERTA, e INSERT OR IGNORE garante que nunca será corrigida: medi 3 velas parciais congeladas em BTCUSDT/1h (detectadas por quebra da cadeia abertura[i+1]==fechamento[i]) e TODAS estão dentro do hold-out.

#### `database.py` — 4/10 🟡

*1027 LOC · trilha infra*

O caminho SQLite carrega a produção de fato (WAL + busy_timeout 30s sustentando dois processos, 41 testes herméticos, crash recovery com call site real). O preço são 22 ramos Postgres DOA sem cobertura, idempotência de trades que hoje protege 0% das escritas, e ~355 MB em três tabelas sem nenhum leitor.

**Defeito principal:** `row[0]` sobre linha entregue como dict pelo pool com row_factory=dict_row (database.py:581 e 990): salvar_sinal e historico_cv_auc_modelo levantam KeyError em TODA chamada no backend Postgres — o alvo de deploy documentado. Em executor.py:997 a chamada não tem try/except e roda depois de registrar_resultado.

#### `indicadores.py` — 4/10 🟡

*235 LOC · trilha edge*

Folha pura, determinística, sem I/O nem estado global, e o indicador que efetivamente gateia a compra está correto. Mas macd() é 100% NaN com teste que consagra o defeito, ema(x, len(x)) devolve SMA — e é exatamente a chamada das features 0 e 1 do XGBoost — e nenhum teste compara contra implementação de referência.

**Defeito principal:** Duas definições incompatíveis de VWAP no mesmo sistema: o vivo usa ind.vwap CUMULATIVO (otimizada.py:97, suporte.py:140, ml_filtro.py:72) e todo o backtest usa vwap_rolling(20) — os parâmetros de params_pares.py foram calibrados com uma e o bot opera com a outra.

#### `data/klines.py` — 5/10 🟡

*91 LOC · trilha infra*

Projetado, não acumulado: lock único correto, chave normalizada nas duas pontas, timestamp só em sucesso, REST_BASE_URL respeitado, 11 testes herméticos. Mas 'ponto único' é falso (ml_filtro, lstm_modelo e dashboard mantêm fetchers próprios) e a vela em formação é repassada sem sinalização a 4 dos 5 consumidores.

**Defeito principal:** O fallback de dado antigo (klines.py:91) não tem teto de idade nem flag: risco.verificar_volatilidade — o circuit breaker — mede a variação de uma hora atrás sem saber. Fail-open na função cujo propósito é ser fail-closed, e o módulo não importa logging: nenhuma falha deixa rastro.

### Superfícies (UI/health)

#### `dashboard.py` — 2/10 🔴

*801 LOC · trilha infra*

Bind local, CSP, rate limit e delegação a binance_conta são reais. Mas o painel não é uma janela para o worker: é uma segunda instância da estratégia que contamina o dataset, não expõe posição nem ordem (zero rotas mutantes em todo o repo), tem token inerte em produção e mostra painel de risco congelado no primeiro request.

**Defeito principal:** O processo de UI executa a estratégia e GRAVA no banco de produção: dashboard.py:318 chama otimizada.analisar a cada 30s por par e otimizada.py:276 persiste em `sinais`. Medido: ~96% das linhas de sinal de BTCUSDT têm a cadência de 30s do dashboard, e nas últimas 24h 100% das linhas de `trades` vieram dele sem trade_id.

#### `telegram_bot.py` — 2/10 🔴

*166 LOC · trilha infra*

Os 10 call sites são reais e estão no caminho de produção — o canal não é órfão, está morto. O detector de placeholder correto já existe no repo (binance_conta.py:44) e nunca foi aplicado aqui, e nove monkeypatches foram escritos para contornar o sintoma em vez de corrigir a causa.

**Defeito principal:** A guarda testa vazio em vez de validade (telegram_bot.py:31) e o .env traz 'your_telegram_bot_token_here', que é truthy: o POST vai para uma URL inválida, recebe 404 e a linha 45 devolve False sem print, sem log e sem bot_event. 0 de 8 alertas entregues, incluindo 'posição sem registro no banco'.

#### `health.py` — 3/10 🔴

*236 LOC · trilha infra*

Código limpo, thread-safe e com /ready genuinamente correto (a regressão do 503 está corrigida). Mas o watchdog de 120s do @aggTrade — a única lógica com valor de dinheiro — só dispara se um humano fizer curl. Observabilidade completa sem observador.

**Defeito principal:** Nenhum dos três endpoints tem consumidor: o único match de '/health' em deploy/ é um echo em setup.sh:76, o unit só tem Restart=on-failure, e /metrics é servido em 0.0.0.0 sem auth (health.py:234) expondo pnl_dia e drawdown. /health é 200 incondicional (158-162).

### Pesquisa e backtest

#### `backtesting/motor.py` — 1/10 🔴

*407 LOC · trilha edge*

Alcançável por `main.py --backtest`. Chama o score de produção, mas com 9 de 11 componentes constantes (piso imutável de 55,77 de 100, só ema e rsi variam) e CVD com sinal invertido que só sobe. Os -99,24% que ele imprime não medem estratégia nenhuma — medem o harness.

**Defeito principal:** Não há guard `if posicao is None` antes da entrada: a posição aberta é sobrescrita silenciosamente 12.749 vezes em 17.512 barras, com o trade anterior apagado sem PnL. O motor irmão TEM o guard (motor_ensemble.py:445) — a divergência entre os dois é o próprio bug.

#### `backtesting/otimizador.py` — 1/10 🔴

*391 LOC · trilha edge*

Seis vieses independentes, todos na direção favorável: look-ahead, bloqueios de regime/volatilidade anulados por recálculo do fator, taxa de futuros, F&G travado em 100, Sharpe bruto de taxa anualizado por sqrt(252) sobre retornos por trade, e posição aberta descartada no fim. Zero teste.

**Defeito principal:** Look-ahead médio de 47,4h em 100% das barras (idx_1h//4, linha 64) num grid de até 8000 combinações sem hold-out, walk-forward ou correção de multiplicidade — e a saída disso está hardcoded em config/params_pares.py, lida no caminho vivo por otimizada.py:70.

#### `backtesting/motor_vectorbt.py` — 1/10 🔴

*391 LOC · trilha infra*

56 linhas de docstring documentando decisões de engenharia para código inerte, que ainda reproduz fielmente o look-ahead do legado e apresenta essa fidelidade como virtude. Velocidade sem correção só amplifica o data snooping.

**Defeito principal:** Nunca executou: `import vectorbt` no topo (linha 64) com a dependência não instalada, sem venv no repo, teste de paridade sempre skipped e CI que nunca instala requirements-backtest. Se rodasse, a paridade quebra em duas dimensões (fator 0,5 virou booleano; retorno líquido vs bruto).

#### `backtesting/motor_otimizado.py` — 1/10 🔴

*291 LOC · trilha manutencao*

291 LOC sem importador, sem teste, sem CLI, com SYMBOL fixo e DB_PATH relativo. Acoplamento de entrada zero torna a aposentadoria barata: mover para _legado/ não quebra nada. A ideia útil (contagem de aprovação por filtro) vale ser reimplementada dentro do walk_forward.

**Defeito principal:** Código morto confirmado: grep em todo o repositório devolve uma única referência fora do arquivo (uma linha de doc). Carrega o mesmo look-ahead de ~47h num filtro que aqui é GATE booleano duro, invalidando o único propósito do módulo — medir a contribuição de cada filtro.

#### `research/carry_lab.py` — 2/10 🔴

*253 LOC · trilha infra*

Aritmética correta e testada (custo cobrado uma vez, capital 1,25x, variante primária com zero parâmetros — impossível de sobreajustar). Mas a regra de decisão (imprimir, com os 5 critérios) não tem um teste, --holdout não tem trava nenhuma, e o substrato desapareceu.

**Defeito principal:** Não executa: `python research/carry_lab.py` termina em sqlite3.OperationalError 'no such table: funding', e a tabela não existe no banco vivo nem nos dois backups POSTERIORES à medição documentada. O FAIL de carry existe apenas como prosa em METODOLOGIA_CARRY.md.

#### `backtesting/motor_ensemble.py` — 3/10 🔴

*697 LOC · trilha edge*

Os defeitos B1/B4/B5 estão DIAGNOSTICADOS POR ESCRITO no próprio repo (walk_forward.py:17-41) e foram corrigidos só lá — o motor que o usuário vê ficou intacto. Cobertura executada de rodar() e _score_backtest é literalmente zero (o único teste é sempre skipped). Não desce mais porque o gate já reprovou a montante.

**Defeito principal:** Servido ao vivo por GET /api/backtest com look-ahead de 44-46h no filtro MTF (idx//4, linha 350) e taxa de futuros 0,04% num bot SPOT (linha 37). Reproduzido: os +2,54% / PF 1,01 exibidos viram -42,67% / PF 0,75 / capital $573 ao corrigir as duas linhas.

#### `backtesting/walk_forward.py` — 4/10 🟡

*625 LOC · trilha infra*

É a régua que reprovou a Etapa 1, e é honesta nos vieses que ela mesma documenta: B1 corrigido com teste de propriedade, contiguidade que ABORTA, purge treino/teste correto, censura final, stop antes de target. O problema é o alvo da medição — e o input do fix B6 (fng_historico.json) nem existe no disco.

**Defeito principal:** Mede uma estratégia que não existe: STOP_PCT/TARGET_PCT hardcoded em 2,0%/4,0% (linhas 75-76) contra 1,5%/5,0% de produção, sem take-profit parcial, sem breakeven, sem trailing, com score de 9 componentes e features cujo dist_vwap diverge por ordem de grandeza do que roda ao vivo.

#### `backtesting/trend_following.py` — 5/10 🟡

*594 LOC · trilha edge*

Núcleo causal provado por 11 testes, contabilidade fixada numericamente, e o único motor do diretório com taxa de spot honesta. Mas ~400 das 594 LOC — incluindo toda a lógica de aprovação/reprovação pré-registrada — não têm um teste, e RISCO_FRAC nunca dimensionou uma ordem real.

**Defeito principal:** O backtest sai por CLOSE abaixo do canal (linha 110) enquanto ao vivo o canal vira STOP na exchange, que dispara em pavio intrabar — são dois sistemas, e o mais penalizado por whipsaw é o que receberia capital. Agravante: --holdout é silenciosamente IGNORADO no ramo default da CLI (linha 590).

#### `research/edge_lab.py` — 5/10 🟡

*517 LOC · trilha edge*

Pré-registro datado, corte anti-vazamento conservador (preparar_para_ic mascara as últimas H linhas em vez de espiar), permutação por rotação circular semeada, e 18 testes que provam calibração sob null autocorrelacionado. Mas o veredito não é reproduzível: o banco não é versionado e as duas funções que produziram as evidências decisivas não têm call site.

**Defeito principal:** O hold-out é uma FRAÇÃO de tabela mutável (HOLDOUT_FRAC sobre len(f), edge_lab.py:47,226), não um intervalo de datas: toda coleta move a fronteira. O hold-out virgem de hoje começa em 2025-07-21 e engole período que já foi pesquisado — o único hold-out sobrevivente está contaminado por construção.

#### `validacao.py` — 6/10 🟡

*146 LOC · trilha edge*

Funções puras, sem I/O, sem estado, com purge/embargo verificados contra o rótulo real do ml_filtro (sem off-by-one) e 18 testes. O melhor código do repositório em disciplina. O que falha é o rótulo do resultado e a ausência de qualquer cobertura de covariate shift — o buraco por onde passou o dist_vwap.

**Defeito principal:** O número rotulado 'AUC honesto (purged CV)' vem de np.array_split sem restrição de anterioridade (validacao.py:42-43): em 4 de 5 folds o treino contém dados posteriores ao teste. E detectar_drift nunca disparou — com base congelada o histórico é bit-idêntico e o alerta iria para bot_events, que tem 14 writers e zero leitor.

#### `backtesting/metricas.py` — 7/10 🟢

*143 LOC · trilha manutencao*

143 linhas, zero I/O, zero estado, guardas em todo ponto de divisão, E[max SR] com o termo SR_bar completo, ~40 testes incluindo propriedades (equivariância, monotonia em N). É o teto da escala neste repositório — e mesmo ele tem seu único caminho de produção inerte.

**Defeito principal:** deflated_sharpe_ratio devolve PSR puro quando sharpes_trials é None, com o mesmo tipo e faixa, e 4 de 5 callers gravam o resultado sob a chave 'dsr'. E cvar_historico — único consumidor de produção — nunca executa: sinais_executados devolve lista vazia (0 de 5.255 sinais têm pnl).

### Configuração

#### `config/settings.py` — 2/10 🔴

*81 LOC · trilha infra*

Não está commitado (gitignore:2, git log vazio) e o ramo de credencial hardcoded está inerte com ENV=production. Sobra um arquivo untracked, por máquina e sem review, que decide endpoint de mercado para 13 módulos, mais duas linhas de erro de GCP em todo boot dos dois serviços.

**Defeito principal:** REST_BASE_URL='https://fapi.binance.com' e WS_BASE_URL='wss://fstream...' em nível de módulo (linhas 59-60) têm PRECEDÊNCIA sobre o default SPOT via runtime_settings.py:73-74 — remover duas linhas do .env migra executor, risco, klines e binance_conta para Futuros em bloco, sem um único log.

#### `config/params_pares.py` — 3/10 🔴

*54 LOC · trilha edge*

Código trivial e correto, com fallback conservador e call sites reais; score_operar/score_cheio chegam intactos ao caminho de dinheiro. O problema é a procedência dos valores e a ausência de qualquer validação de invariante (stop<target, rsi_min<rsi_max, score_operar<=score_cheio).

**Defeito principal:** Os números vêm de grid search de 144 a 8000 combinações por par sem hold-out nem walk-forward (Sharpe 3,24 reportado contradiz a Etapa 1 reprovada), e o stop_pct por par — a principal razão de existir do arquivo — é ANULADO para ETH e SOL em otimizada.py:207-210.

#### `config/settings_template.py` — 3/10 🔴

*26 LOC · trilha manutencao*

Inerte: zero importadores, nenhum caminho de execução, endpoints já corrigidos para SPOT. Mas divergiu do .env.example em 10+ variáveis (não menciona DRY_RUN, ALLOW_REAL_TRADING, OCO_BRACKET, RECONCILIAR_BOOT_EXCHANGE) e seus placeholders não casam com o detector do próprio repo.

**Defeito principal:** Ensina a criar o config/settings.py que arma o fallback silencioso de mercado (runtime_settings.py:20,61-85) e propaga DB_PATH='data/btc_data.db' — o mesmo caminho do banco vivo que produziu o incidente de contaminação documentado em conftest.py.

#### `config/runtime_settings.py` — 4/10 🟡

*165 LOC · trilha infra*

Precedência env > local > default correta e o único módulo com suíte de endurecimento (CORS efetivamente ligado). Mas o fallback silencioso para config/settings.py pode reintroduzir endpoints de FUTUROS, as coerções engolem ValueError sem log no caminho de dinheiro, e SECRET_KEY endurecido é ramo morto neste deploy.

**Defeito principal:** DRY_RUN é uma trava fantasma: documentada em SETE artefatos versionados como a garantia de paper trading 'mesmo com chaves reais', e lida apenas pelo payload de /api/conexao (dashboard.py:648,709-711). A decisão real é main.py:1251 (`simulacao = not args.real`).

### Ferramentas e periferia

#### `ai/ollama_client.py` — 2/10 🔴

*312 LOC · trilha manutencao*

312 LOC de peso morto com dependência de serviço externo e dois dos quatro métodos públicos sem consumidor sequer hipotético (não há fonte de notícias no repo). Robustez de código que nunca executou não é crédito; o caminho para subir a nota é _legado/, não mais código.

**Defeito principal:** Zero importadores de produção — a busca no repo devolve apenas a própria docstring e quatro linhas de teste — enquanto docs/Modulos/ML e Sinais.md classifica o módulo como maturidade 'Alta'. Não existe sequer flag para ligá-lo.

#### `monitor_fluxo.py` — 2/10 🔴

*149 LOC · trilha manutencao*

Interpretação do campo `m` do aggTrade correta e persistência protegida por try/except. Mas é um processo cuja indisponibilidade não afeta nada porque nada consome sua saída, e que grava trades sem trade_id, anulando o índice único de deduplicação.

**Defeito principal:** 100% da saída persistida é write-only: cvd_historico não tem um único SELECT no repo, e `trades` só é lida por database.resumo_trades, que não tem chamador nenhum. Além disso run_forever sem `reconnect` — o padrão de reconexão existe no próprio repo (dashboard.py:492-497) e não foi copiado.

#### `testar_api.py` — 2/10 🔴

*68 LOC · trilha infra*

É a ferramenta que o guia de chave manda rodar antes de operar, e ela emite uma declaração falsa de sanidade: com chave read-only diz que está tudo certo. Ainda filtra saldo por free>0, então poeira de 0,00000075 USDT aparece como 'Saldos disponíveis'. Duplicata degradada de um módulo canônico.

**Defeito principal:** Responde à pergunta de go-live com o campo errado: imprime `permissions` de /api/v3/account, que é da CONTA, e conclui 'Chaves de API validadas com sucesso!' (linhas 51-53). A armadilha está documentada no próprio gate e a versão correta já existe pronta em binance_conta.restricoes_chave().

#### `scripts/purgar_fixtures_producao.py` — 3/10 🔴

*215 LOC · trilha infra*

Ergonomia destrutiva exemplar (dry-run padrão, backup, imprime as decisões de MANTER, reconcilia o contador). Mas o único mecanismo de reversão está quebrado: shutil.copy2 sem o -wal num banco em journal_mode=wal com o worker vivo copia um backup sem as transações recentes.

**Defeito principal:** O critério `order_id.startswith('SIM-')` é INCONDICIONAL (linha 70), mas no modo operacional atual o próprio Executor gera 'SIM-<epoch>' para toda ordem legítima (executor.py:405): `--confirmar` apaga posições reais de paper. O executor gateia o mesmo teste com `not self.simulacao` e há teste exigindo o oposto.

#### `scripts/migrate_sqlite_to_supabase.py` — 3/10 🔴

*411 LOC · trilha infra*

Quatro modos bem escolhidos e dry-run que valida a conexão de verdade. Mas o schema é reimplementado à mão fora de database.py e já dessincronizou, o commit final roda mesmo com erros coletados, e imprime 40-50 caracteres da DATABASE_URL — que numa URL Supabase alcança o início da senha.

**Defeito principal:** A docstring declara idempotência que só risk_state tem: 4 tabelas fazem INSERT puro e `trades` só dedupe por trade_id, que o único produtor grava NULL. E o insert de `sinais` omite preco_saida/pnl_usdt/pnl_pct/barreira_tocada — migrar apaga o resultado de todo trade fechado.

#### `relatorio_gate.py` — 4/10 🟡

*234 LOC · trilha infra*

Comportamento observado hoje é conservador (0 trades fechados → REPROVADO com exit 1; benchmark ausente força aprovado=False). Mas é a ferramenta declarada fonte única de verdade do gate, com PF fail-open, duração medida por delta entre trades em vez de uptime, e ZERO testes.

**Defeito principal:** O ramo de veredito (linhas 225-230) imprime 'GATE: APROVADO — prosseguir à Etapa 3' e sai com código 0 sem nenhum interlock com o estado da Etapa 1, que o contrato pré-registrado exige (GATE_GO_LIVE.md:103) e que REPROVOU em 4 de 5 critérios.

#### `analise_mercado.py` — 4/10 🟡

*185 LOC · trilha infra*

Call site real e provado, try/except deliberado contra crash-loop e teste hermético da classificação. Mas bate exclusivamente em fapi (mercado que o bot não opera), e toda a saída — funding, open interest, liquidez — é print descartado enquanto as colunas correspondentes de snapshots_mercado ficam zeradas.

**Defeito principal:** Seis requests.get sem timeout no boot (linhas 18,32,58,75,79,85), executados ANTES do crash recovery de posição (main.py:1368 vs 949-960): um socket pendurado deixa posição persistida sem readoção e sem monitor, com /health já respondendo 200 e o NSSM sem motivo para reiniciar.

---

## Plano de combate

<!-- PLANO_DE_COMBATE -->

*(seção preenchida pelo agente de planejamento; ver `docs/GATE_GO_LIVE.md`
para o gate pré-registrado e o estado das fases I-1..I-7 / E-1..E-6)*
