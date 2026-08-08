# Metodologia de descoberta de edge — pré-registro

> **Natureza deste documento:** contrato de método, escrito ANTES de rodar o
> harness e de ver qualquer resultado. Serve ao mesmo propósito do
> `docs/GATE_GO_LIVE.md`: impedir que a pesquisa vire *overfitting por
> iteração* (testar variações contra os mesmos dados até algo "passar").
> Alterar as regras abaixo depois de ver os números anula o valor da
> pesquisa. Qualquer mudança exige justificativa datada aqui embaixo.
>
> Criado em: 2026-07-24 · Autor da decisão de direção: usuário (escolheu
> "harness de edge primeiro" após a reprovação da Etapa 1 do gate).

## Por que existimos

A Etapa 1 do gate reprovou a estratégia (retorno −21% vs +14% do
buy-and-hold). O diagnóstico forense (`research/` — ver commit) provou que a
**entrada não tem edge**: win rate 33.3%, PF 0.86 e retorno da estratégia são
estatisticamente **indistinguíveis de entrada aleatória** na mesma geometria
de barreiras 2:1 (baseline aleatório: 32.5% / PF 0.82). Num random walk, uma
barreira 2:1 tem expectância bruta ≈ 0 por construção (gambler's ruin); o
sinal de 11 componentes + XGBoost não move a agulha.

Consequência: **antes de construir qualquer estratégia nova, é preciso
MEDIR se algum sinal tem poder preditivo real out-of-sample.** Se nada tiver,
a conclusão honesta é "não há edge direcional tradeable neste conjunto de
sinais em BTC 1h" — e isso é um resultado válido, não um fracasso.

## O que será testado (família de hipóteses — congelada)

**Features (11):** exatamente as que `ml_filtro.extrair_features` já usa (o
sinal que o projeto de fato emprega), reproduzidas causalmente:
`dist_ema20`, `dist_ema50`, `rsi/100`, `atr_relativo`, `volume_relativo_norm`,
`bollinger_relativo`, `dist_vwap`, `var_1`, `var_4`, `var_24`, `bb_pos`.

**Labels:**
- `fwd_ret_H`: retorno futuro em H candles (contínuo) — para o *information
  coefficient*. **Estatística primária de edge.**
- `barreira_2_4_H`: triple-barrier alvo +4% / stop −2% em H candles (a
  barreira que a estratégia realmente usa; base rate ~33%).
- `barreira_sim_H`: triple-barrier simétrico ±2% em H candles (benchmark
  limpo, base rate ~50%).

**Horizontes:** H ∈ {8, 24} candles (8 = a `JANELA` do bot; 24 ≈ um dia).

**A família completa para a correção de múltiplas hipóteses é: 11 features ×
2 horizontes = 22 testes de IC.** Nenhum outro teste será adicionado depois
de ver resultados sem re-congelar esta lista e reiniciar.

## Estatística de edge

**Primária — Information Coefficient (IC):** correlação de Spearman (rank,
robusta a não-linearidade e outliers) entre `feature[i]` e `fwd_ret_H[i]`,
medida **apenas out-of-sample** via purged & embargoed CV
(`validacao.purged_kfold_indices`, purge = embargo = H, 5 folds). O IC de uma
feature é a média dos ICs OOS dos folds. Edge é bidirecional (IC positivo OU
negativo é explorável), então a estatística de magnitude é `|IC_OOS|`.

**Secundária — AUC do modelo combinado:** XGBoost nos 11 features via purged
CV (`validacao.purged_cv_auc`) sobre `barreira_sim_H` (base 50%, AUC
interpretável). Reportada como corroboração; não decide sozinha.

## Correção por múltiplas hipóteses (o coração do método)

Testar 22 estatísticas garante que a MELHOR pareça boa por acaso. Correção
por **teste de permutação com max-statistic**:

1. Observado: `obs_max = max` sobre as 22 combinações de `|IC_OOS|`.
2. Null: **rotação circular** do vetor de forward-returns por um offset
   aleatório grande (> H). Refinamento pré-registrado (2026-07-24, antes de
   qualquer resultado — ver registro de mudanças): a rotação **preserva
   perfeitamente** toda a autocorrelação da série (é a mesma série, só
   deslocada no tempo) e quebra o alinhamento com as features — é o teste
   canônico de significância de correlação entre duas séries autocorreladas,
   e estritamente mais conservador que o block-shuffle (que destrói
   autocorrelação entre blocos). Para cada rotação, recomputa as 22 `|IC|` e
   guarda o **máximo**. N = 1000 rotações → distribuição nula de `max|IC|`.

   Nota sobre "OOS": o IC de uma feature CRUA não é ajustado (não há
   parâmetro a treinar), então in-sample = out-of-sample para ele — o único
   grau de liberdade é *qual* feature/horizonte (22 escolhas), corrigido pelo
   max acima. O purged CV (`validacao.py`) é aplicado só ao modelo COMBINADO
   (XGBoost), onde há ajuste e o vazamento importa.
3. **p-valor = fração de permutações com `null_max ≥ obs_max`.** Corrige
   simultaneamente (a) o snooping de 22 testes e (b) a inflação por
   autocorrelação. Este é o teste padrão-ouro para "a melhor de K é real".

## Hold-out (uso único, travado em código)

Split **cronológico** dos 2 anos de BTCUSDT/1h:
- **Pesquisa: primeiros 65%** dos candles. Toda CV, permutação e escolha de
  feature acontece aqui. Pode-se iterar à vontade nesta porção.
- **Hold-out: últimos 35%.** Intocado durante a pesquisa. Avaliado **no
  máximo uma vez, jamais**, e só se a pesquisa encontrar edge sobrevivente. O
  código (`edge_lab.avaliar_holdout`) recusa rodar sem `--confirmo-uso-unico`
  e grava um registro permanente aqui embaixo no ato. Reavaliar o hold-out
  depois de ver seu resultado = reiniciar toda a pesquisa com dados novos.

**Corroboração cross-asset (hold-out barato adicional):** ETH e SOL (já
coletados). Um edge real nas features de BTC deve **replicar em sinal** (mesmo
sentido de IC) em ≥1 de {ETH, SOL}. Difícil de overfitar; não substitui o
hold-out temporal.

## Regra de decisão (pré-registrada)

**HÁ EDGE (prosseguir a construir uma estratégia em torno da(s) feature(s)
sobrevivente(s)):** exige as TRÊS condições:
1. p-valor da permutação do max `|IC_OOS|` **< 0.05**; **E**
2. a feature vencedora tem `|IC_OOS| ≥ 0.03` na porção de pesquisa (piso
   econômico frouxo — abaixo disso o edge dificilmente cobre custos de ~0.2%
   round-trip, mesmo se estatisticamente real); **E**
3. o sinal do IC **replica** em ≥1 de {ETH, SOL} na porção de pesquisa.

Só então: rodar o hold-out temporal UMA vez para confirmar (mesmo sinal,
`|IC|` não colapsa). Confirmado → construir estratégia; não confirmado →
FAIL.

**NÃO HÁ EDGE (FAIL):** qualquer condição acima falha. Conclusão honesta:
não há edge direcional tradeable neste conjunto de features em BTC 1h. Não
construir estratégia direcional sobre este sinal; levar ao usuário a decisão
entre (a) expandir o conjunto de features com hipótese nova pré-registrada,
ou (b) pivô de mercado (timeframe/instrumento/estrutura). **Nenhum ajuste de
parâmetro da estratégia antiga é considerado — a Etapa 1 já a matou.**

## Registro de mudanças de método (todas pré-resultado)

| Data | Mudança | Justificativa |
|---|---|---|
| 2026-07-24 | Null trocado de "circular block shuffle" para "rotação circular" | Rotação preserva 100% da autocorrelação; teste canônico e mais conservador. Feito antes de rodar o harness. |
| 2026-07-24 | IC medido sobre a porção de pesquisa inteira (não por-fold) para features cruas | Feature crua não tem ajuste → não há OOS a distinguir; per-fold não agregava rigor. Purged CV mantido só para o XGBoost combinado. |

## Resultado (2026-07-24) — SEM EDGE TRADEABLE

Pesquisa rodada na porção de pesquisa (BTCUSDT, 2024-07-26 → 2025-11-10,
n=11.325). **Hold-out temporal NÃO foi tocado** (a regra de decisão falhou
antes disso). Reprodutível via `research/edge_lab.py`.

**1. Teste primário (IC de feature única vs retorno futuro):** FALHA.
- Melhor `|IC|` = 0.0677 (`dist_vwap`, H=24) — acima do piso econômico 0.03,
  mas o **teste de permutação dá p = 0.378** (null p95 de max|IC| = 0.10).
  Testando 22 features autocorrelacionadas, um |IC| de 0.068 é comum por
  acaso. **Nenhuma feature prevê o retorno futuro além do ruído.** (Foi
  exatamente a armadilha que matou a estratégia original: um IC que "parece
  tradeable" mas não sobrevive à correção de múltiplas hipóteses.)

**2. Corroboração secundária (XGBoost combinado) — investigada a fundo:**
- Purged-CV AUC = 0.62 (barreira ±2%/H=8, base 11.5%). Submetida ao MESMO
  rigor: teste de permutação por rotação do label, **p = 0.0083** (observada
  acima de todas as 120 rotações; null p95 = 0.566). **É sinal não-linear
  REAL** que o IC de feature única perdeu — distinto do overfit in-sample da
  estratégia velha (que tinha AUC ~0.9999 in-sample, lixo).

**3. Teste econômico decisivo (o sinal sobrevive AOS CUSTOS?):** NÃO.
- Expectância líquida por trade (OOS, custo round-trip 0.30% = taxa+slippage
  do gate), operando os sinais mais bem-ranqueados pela probabilidade do
  modelo:

  | Faixa | net %/trade | win rate |
  |---|---|---|
  | Operar tudo (base) | −0.279% | 11.5% |
  | Top 5% prob | −0.145% | 26.0% |
  | Top 10% | −0.256% | 20.3% |
  | Bottom 10% | −0.265% | 9.2% |

- O modelo **ranqueia de verdade** (top 5% win 26% vs base 11.5% vs bottom
  9.2%), mas **toda faixa perde dinheiro após custos.** O alfa vale ~0.13%/
  trade; os custos são 0.30%/trade. **Alfa < metade do custo → edge
  estatístico REAL, economicamente MORTO** (afogado pelas taxas).

### Conclusão pré-registrada aplicada

A regra de decisão (p<0.05 no IC primário) FALHA → não há edge direcional na
família de features testada. O achado secundário (XGBoost) confirma-o por
outro caminho: mesmo o sinal não-linear real é fino demais para cobrir custos
em BTC 1h. **Não construir estratégia direcional sobre este conjunto de
features.** Levar ao usuário: (a) expandir features com hipótese nova
pré-registrada (order-flow/CVD/funding/cross-asset — odds modestas dada a
eficiência medida), ou (b) pivô de payoff/mercado (trend-following ou
timeframe maior, onde o problema "precisa de edge preditivo para bater uma
barreira 1:1" se dissolve). Nenhum ajuste da estratégia velha é considerado.

## Registro de uso do hold-out

| Data | Evento | Resultado |
|---|---|---|
| 2026-07-24 | Pesquisa concluída — regra de decisão falhou (sem edge economicamente tradeable) | Hold-out temporal **NÃO tocado** (preservado para uma futura hipótese que sobreviva à pesquisa) |

## Substrato versionado e fronteira congelada (I-11 — 2026-08-08)

Até esta data, todo veredito deste documento era **irreprodutível**. Não por
descuido de redação: por três defeitos no substrato.

**1. A tabela `klines` mudava, e de forma não-append.** Medido em 2026-08-08:

| série | velas | primeira vela |
|---|---:|---|
| BTCUSDT/1h | 17.563 | 2024-04-01 18:00Z |
| ETHUSDT/1h | 17.520 | 2024-04-03 13:00Z |
| SOLUSDT/1h | 17.520 | 2024-04-03 13:00Z |

As 43 velas extras do BTC estão no **início** da série. A tabela cresceu para
trás.

**2. A fronteira do hold-out era uma fração do tamanho.** `int(N × 0.65)`
resolvia para:

| série | índice | data de corte |
|---|---:|---|
| BTCUSDT | 11.415 | 2025-07-21 09:00Z |
| ETHUSDT | 11.388 | 2025-07-22 01:00Z |
| SOLUSDT | 11.388 | 2025-07-22 01:00Z |

Dezesseis horas de diferença entre ativos, sem nenhuma razão metodológica — e a
divisa andava sozinha a cada coleta. O registro de 2026-07-24 já mostrava que
**43,7% do hold-out de então havia sido porção de pesquisa** numa rodada
anterior.

**3. A trava de uso único não travava.** Era só o kwarg `confirmo_uso_unico`; o
registro neste arquivo ficava dentro de um `try/except: pass` e **não tinha
nenhum leitor**. Chamar duas vezes funcionava. `carry_lab` não tinha trava
alguma — `--holdout` era uma flag booleana.

### O que fica congelado a partir de agora

- **Fronteira do hold-out: `2025-07-22 00:00:00 UTC`** (`HOLDOUT_INICIO_MS =
  1753142400000` em `research/edge_lab.py`, reusada por `carry_lab.py`). Com a
  data fixa, os três pares passam a ter a **mesma** divisa e **6.133 velas** de
  hold-out cada, apesar das contagens totais diferentes. Mudar esta data invalida
  todos os vereditos anteriores e exige novo pré-registro **antes** da medição.
- **Substrato: `data/snapshots/2026-08-08/`**, versionado no git, com
  `manifest.json` contendo contagem, primeira/última vela e **sha256** por série.
  Os labs leem o snapshot; a tabela viva só é usada como fallback, e com aviso
  explícito de que o resultado não será re-derivável.
- **Trava de uso único com leitor**: `avaliar_holdout` recusa a segunda avaliação
  citando o registro anterior, e a gravação do registro deixou de ser silenciosa.

### Limite honesto desta frente

Isto **não reconstrói** os vereditos anteriores. A série sobre a qual os FAILs
de julho foram medidos não existe mais em lugar nenhum — não havia snapshot, e a
tabela já se moveu. Números re-derivados sobre este snapshot são **novos**, e é
a partir deles que a reprodutibilidade passa a valer.

Apresentá-los como "confirmação dos FAILs anteriores" seria o mesmo conforto
falso que esta frente existe para eliminar.

### Comando de reprodução

```
python -m research.snapshot --verificar          # sha256 bate com o manifest?
python -m research.reproduzir                    # gera a linha de base
python -m research.reproduzir --comparar         # exige diferença 0.0
```
