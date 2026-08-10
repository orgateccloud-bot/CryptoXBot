# Pré-registro — re-derivação dos parâmetros vivos (frente E-9, ação 3)

> Escrito **antes** de rodar qualquer combinação. Este documento é o contrato:
> alterar critério depois de ver resultado invalida a rodada.

## Por que existe

`config/params_pares.py` governa o trading ao vivo e veio de um grid de até
8.000 combinações **sem out-of-sample**, ordenado por um Sharpe cego a custo e
a sizing, com look-ahead no MTF e sem veto de sentimento. O vencedor de 8.000
sorteios sobre uma série única tem Sharpe alto por construção — é o máximo de
uma amostra, não uma estimativa. A frente E-9 re-deriva, ou declara que nada
passou.

## Dados

* Fonte: `data/btc_data.db`, klines 1h e 4h, coletadas por
  `backtesting/coletar_dados.py` (determinístico desde I-11d).
* Fear & Greed: `data/fng_historico.json`, sha256 no manifest
  (`data/fng_historico.manifest.json`). **Sem o histórico a rodada aborta** —
  medir sem o veto de sentimento é medir outra estratégia (I-12g).
* Régua: `backtesting/regua.py` → `score.calcular`, a mesma da produção (I-12).
* Alinhamento MTF: `backtesting/alinhamento.mapear_idx_fechado` (causal).
* Custo: taxa SPOT 0,10%/lado + slippage 0,05%/lado.
* Política de saída: a de **produção** — parcial de 50% no target1, breakeven,
  target2 e trailing (I-12h).

## Partição por DATA (fixa, não por proporção)

| janela | período | uso |
|---|---|---|
| **TREINO** | início da série → 2025-08-01 | seleção: roda o grid inteiro |
| **OOS** | 2025-08-01 → 2026-01-01 | confirmação: só o vencedor do treino |
| **HOLD-OUT** | 2026-01-01 → fim da série | **USO ÚNICO**: só o DSR final |

Partição por data, não por proporção, para que a fronteira não se mova quando
a série crescer — mesma decisão de I-11b.

## Critérios de aprovação (todos, e nesta ordem)

1. **≥ 50 trades no treino** — um Sharpe sobre 5 trades é ruído, e era esse o
   piso do otimizador antigo.
2. **≥ 50 trades no OOS.**
3. **Sharpe líquido no OOS > 0** — sobre retorno líquido de taxa e ponderado
   pelo tamanho da posição, não sobre variação bruta de preço.
4. **Retorno no OOS > 0.**
5. **DSR ≥ 0,95 no HOLD-OUT**, deflacionado por `n_trials` = número de
   combinações **avaliadas** (não as que sobreviveram ao piso).

Reprovar em qualquer um é reprovação da rodada inteira.

## Proibições

* Não re-registrar critério depois de ver resultado.
* Não promover o segundo colocado se o primeiro reprovar.
* Não rodar o hold-out mais de uma vez. A trava exige
  `--confirmo-uso-unico` e grava o consumo no fim deste arquivo.
* Não ajustar o grid para fazer um critério passar.
* Se nada passar, o resultado é: **o bot permanece em paper** e
  `config/params_pares.py` continua com `PROCEDENCIA` vazia. Esse desfecho é
  previsto e aceitável — não é motivo para uma segunda rodada.

## Consumos do hold-out

<!-- linhas append-only, gravadas por research/rederivar_params.py -->

---

## Resultado da rodada — BTCUSDT, 2026-08-10

**REPROVADO no OOS. O hold-out NÃO foi tocado** — reprovar antes dele é o que o
preserva para uma eventual estratégia futura.

| janela | trades | win rate | retorno | Sharpe | max DD |
|---|---:|---:|---:|---:|---:|
| TREINO (seleção, 900 combinações) | 208 | 71,6% | **−10,36%** | **−1,18** | 12,92% |
| OOS (confirmação) | 39 | 66,7% | **−8,09%** | **−4,65** | 8,79% |
| HOLD-OUT | — | — | — | — | intocado |

Melhor combinação do treino: `stop_pct=0,030  target_pct=0,040  rsi=[42,60]
score_operar=60  score_cheio=75`.

Reprovou nos três critérios de OOS: menos de 50 trades (39), Sharpe ≤ 0 e
retorno ≤ 0.

### O que este número diz, e é mais forte que "não passou"

**900 de 900 combinações passaram do piso de 50 trades, e a MELHOR delas perde
10,36% dentro da própria amostra de seleção**, com Sharpe −1,18. Não é um caso
de overfit — overfit é quando o vencedor brilha no treino e apaga fora dele.
Aqui não há nada para superajustar: sob régua causal, custo de SPOT, F&G real e
a política de saída que o bot executa, o espaço de parâmetros inteiro é
negativo.

Vale notar a win rate de 66–72% junto de retorno negativo. É a assinatura do
trailing stop: muitos acertos pequenos (sai a 0,8% do pico) contra perdas que
vão até o stop cheio. Quem julgar por win rate conclui o oposto da verdade.

### Consequência

`config/params_pares.py` permanece com `PROCEDENCIA` vazia e o bot permanece em
paper. Esse desfecho estava previsto no pré-registro e **não é motivo para uma
segunda rodada** — mexer no grid agora seria ajustar até passar.

O caminho não é procurar outro conjunto de parâmetros da mesma estratégia: é a
frente E-10 (ML honesto ou desligado) e a busca por um edge que exista antes de
qualquer otimização.
