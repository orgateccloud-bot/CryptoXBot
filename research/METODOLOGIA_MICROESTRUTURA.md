# Pré-registro — hipótese de microestrutura (frente E-11)

> **Escrito e commitado ANTES da primeira medição.** O hash deste commit é a
> evidência de anterioridade. Alterar critério depois de ver resultado
> invalida a rodada — e um FAIL é resultado válido e **final**.

## Situação

Cinco hipóteses já foram reprovadas com critério pré-registrado (edge_lab,
trend em duas variantes, carry, e a re-derivação de parâmetros de E-9). A
Etapa 1 do gate reprovou em 4 de 5 critérios. Sem edge, toda a qualidade de
execução só determina a velocidade da perda.

Esta frente é a mais longa e a **menos urgente em risco imediato** — as travas
mantêm o capital fora.

## O que motiva especificamente microestrutura

O bot já tem um componente de microestrutura, e ele está **matematicamente
morto**. Não é uma suposição:

`data.cvd_calculator.calculate_cvd` devolve `divergence_score =
tanh(slope / std(cvd))`, com `slope` de `polyfit` sobre `x = 0..n-1`. Por
Cauchy-Schwarz, `|slope/std(y)| <= 1/std(x)`, e `std(x) = sqrt((n²-1)/12)`.

| janela | std(x) | teto de \|divergence_score\| | passa do limiar 0,1? |
|---:|---:|---:|---|
| 20 | 5,77 | 0,1717 | sim |
| 34 | 9,81 | 0,1016 | sim (por pouco) |
| **35** | 10,10 | **0,0988** | **não** |
| **50** (produção) | 14,43 | **0,0692** | **não** |
| 100 | 28,87 | 0,0346 | não |

Com `periodo=50` (score.py:95), o teto é **0,0692** contra o limiar de **0,1**.
Verificado empiricamente: 10.000 séries aleatórias e o caso perfeitamente
linear atingem no máximo **0,069185** — exatamente o limite teórico.

Consequência: `cvd_trend` é sempre 0, nenhum dos quatro ramos de divergência
casa, e o componente entrega **50 constante** — 7% do score que não
discrimina nada. Fixado em `tests/test_cvd_inerte.py`.

**Isto não é a hipótese; é o motivo de ela valer a pena.** O sinal de
microestrutura nunca foi testado — foi normalizado até morrer antes de
qualquer medição.

---

## Hipótese

> O desequilíbrio de fluxo assinado (order flow imbalance) medido em janela
> curta, sobre o livro e sobre trades agregados, carrega informação sobre o
> retorno das próximas N barras, **acima do custo de execução**.

## Família de features — CONGELADA

Nenhuma feature entra depois. Adicionar variável ao ver resultado é o
mecanismo pelo qual as cinco hipóteses anteriores teriam "passado".

1. `ofi_book` — order flow imbalance do livro (I-11: já existe `@depth`), soma
   assinada das variações de tamanho no melhor bid/ask.
2. `cvd_slope_norm` — o CVD com normalização **correta**: `slope · std(x) /
   std(cvd)`, que remove a dependência de `n` que matou o componente atual.
3. `desequilibrio_agressor` — fração do volume iniciada por comprador, em
   janela de 1 min, menos 0,5.
4. `intensidade_trades` — contagem de trades na janela / média de 20 janelas.
5. `spread_rel` — (ask − bid) / mid.

Horizontes: 1, 4 e 8 barras de 1h. Alvo: retorno **líquido de custo**.

## Dados

- **Universo novo**: BTCUSDT, ETHUSDT, SOLUSDT — mas o `@depth` e o
  `@aggTrade` só existem a partir da coleta ao vivo, então a série é **nova
  por construção**. Coletor: o de I-11d (determinístico).
- **Não reutilizar** o hold-out de trend nem o de carry (queimados por
  escrito), nem o temporal do `edge_lab` (43,7% já foi porção de pesquisa).

## Partição — por DATA, fixa

| janela | período | uso |
|---|---|---|
| PESQUISA | início da coleta → `2026-11-30` | tudo: seleção, ajuste, exploração |
| **HOLD-OUT** | `2026-12-01` em diante | **USO ÚNICO**, no fim |

Por data e não por proporção: uma fronteira que se move quando a série cresce
não é hold-out (I-11b).

## Critério de PASS — numérico, e todo ele

Medido no HOLD-OUT, sobre retorno **líquido**:

1. **IC out-of-sample > 0,03**, com p < 0,01 por teste de permutação (1.000
   permutações do alvo).
2. **≥ 200 observações** no hold-out.
3. Sharpe líquido da carteira construída sobre o sinal **> 0,5**.
4. **DSR ≥ 0,95**, deflacionado por `n_trials` = número real de combinações
   avaliadas na pesquisa.
5. A margem contra o piso tem de ser **positiva depois do custo**: 0,10%/lado
   de SPOT taker mais o slippage medido no próprio track record de paper de
   E-8. Nas hipóteses já testadas essa margem foi **negativa por 2,3 p.p.** —
   o custo não é detalhe, é o que decide.

Reprovar em qualquer um é reprovação da rodada.

## Proibições

- Não re-registrar critério depois de ver resultado.
- Não promover variante secundária se a primária reprovar.
- Não rodar o hold-out mais de uma vez — trava de uso único com leitor (I-11c).
- Não adicionar feature à família congelada acima.
- Um FAIL encerra a hipótese. Não vira "rodada 2 com ajustes".

## Reprodução

Comando único, registrado aqui antes da primeira execução:

```bash
python research/micro_lab.py --par BTCUSDT
```

E o veredito tem de ser re-derivável com diferença **0.0** por
`python -m research.reproduzir --comparar` (critério de saída de I-11e).

## Estado

**Nada foi medido ainda.** `research/micro_lab.py` não existe; a coleta de
`@depth`/`@aggTrade` histórico não começou. Este documento é o contrato, e
existe antes da primeira medição justamente para que o resultado, qualquer que
seja, valha.

## Consumos do hold-out

<!-- linhas append-only, gravadas pelo harness quando ele existir -->
