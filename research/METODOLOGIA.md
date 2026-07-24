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

## Registro de uso do hold-out

| Data | Evento | Resultado |
|---|---|---|
| — | Hold-out temporal ainda NÃO avaliado | — |
