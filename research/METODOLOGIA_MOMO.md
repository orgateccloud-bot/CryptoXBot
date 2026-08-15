# Metodologia MOMO — momentum cross-sectional BTC/ETH/SOL — pré-registro

> **Natureza deste documento:** contrato de método, escrito ANTES de rodar
> `research/momo_lab.py` e de ver qualquer resultado. Alterar as regras
> depois de ver os números anula o valor da pesquisa. Qualquer mudança exige
> justificativa datada no registro ao final.
>
> Criado em: 2026-08-14 · Direção: usuário ("pré-registra e roda as novas
> frentes em paralelo", após a explicação de que FAIL pré-registrado da
> Etapa 1 só se supera com estratégia NOVA aprovada em teste NOVO).

## Hipótese (congelada)

Momentum **relativo** entre criptoativos persiste em horizonte de dias:
o ativo com melhor retorno recente do universo {BTC, ETH, SOL} tende a
seguir melhor no período seguinte. É uma hipótese ORTOGONAL às já
reprovadas (a primária é reversão intradiária multi-sinal; a trend é
time-series momentum de UM ativo; esta é **seleção entre ativos**).

## Substrato (imutável)

`data/snapshots/2026-08-08` (manifest sha256) — closes 1h de BTCUSDT,
ETHUSDT e SOLUSDT, 2024-04 → 2026-04-03, **interseção de timestamps** dos
três. Série diária derivada do close da barra 00:00 UTC.

## Regras de execução simulada (congeladas)

- **Rebalance diário** no close 00:00 UTC: decide-se com dados até o close
  t (inclusive); a posição vale o retorno de t → t+1.
- **Sinal:** retorno total de k dias, `close_t / close_{t-k} − 1`, por ativo.
- **Carteira:** 100% no ativo de melhor sinal (top-1).
- **Variante com filtro TSMOM:** se o melhor sinal for ≤ 0, fica em caixa
  (USDT, retorno 0).
- **Custos:** taker spot 0,10% por perna. Trocar de ativo = 0,20%
  (vende + compra); entrar/sair de caixa = 0,10%. Sem slippage adicional
  (limitação declarada; barras diárias, uma troca por dia no máximo).

## Família de hipóteses (congelada) — 8 trials

k ∈ {3, 7, 14, 28} dias × {sem filtro, com filtro TSMOM} = **8 combinações**.
Nenhuma outra será adicionada depois de ver resultados sem re-congelar esta
lista e reiniciar a pesquisa. Cada execução de `rodar_pesquisa` soma 8 ao
contador de trials do DSR (`research/vereditos/momo_trials_count.json`).

## Split cronológico (mesma data dos demais labs)

- **Pesquisa:** interseção até 2025-07-21 23:00 UTC (exclusive
  `HOLDOUT_INICIO_MS = 1753142400000`).
- **Hold-out:** 2025-07-22 → 2026-04-03. **Uso único, travado em código**
  (`momo_lab.avaliar_holdout` exige `confirmo_uso_unico` e grava registro
  permanente aqui embaixo no ato; segunda chamada é recusada).

## Régua e decisão (pré-registradas)

Métricas na porção de PESQUISA, líquidas de custos, por combinação:
retorno anualizado (composto, 365d), Sharpe anualizado (diário × √365),
max drawdown. Benchmark: buy-and-hold de BTC na MESMA janela.

**SOBREVIVE (candidata a hold-out):** as TRÊS condições:
1. retorno anualizado líquido ≥ **8% a.a.** (o piso da casa, o mesmo do
   trend — abaixo disso não paga o risco de execução); **E**
2. Sharpe líquido ≥ **0,8**; **E**
3. Sharpe líquido ≥ Sharpe do buy-and-hold de BTC na mesma janela (se não
   bate o ativo passivo dominante, a rotação não paga os custos).

**Escolha para o hold-out:** SE ≥1 combinação sobrevive, leva-se ao
hold-out APENAS a de maior Sharpe líquido na pesquisa (uma única; as demais
morrem aqui). No hold-out ela precisa repetir: retorno ≥ 8% a.a. E Sharpe
≥ 0,8. Passou → Etapa 1 do gate reabre para ELA (walk-forward + paper).
Falhou → FAIL final desta família.

**FAIL da pesquisa:** nenhuma combinação sobrevive às três condições.
Conclusão honesta e permanente para esta família neste substrato.

## Limitações declaradas

- Universo de 3 ativos é pequeno para cross-section; o resultado se lê como
  "rotação BTC/ETH/SOL", não como fator geral.
- Execução no close diário idealiza o fill; se aprovada, a Etapa 2 (paper)
  mede o gap de execução com a telemetria existente.

## Registro de mudanças

- 2026-08-14 (ANTES da primeira medição; achados da revisão adversarial de
  2 céticos independentes): (a) o benchmark buy-and-hold de BTC passa a
  pagar a perna de entrada de 0,10% — uniformização com o VOLT; (b)
  `max_drawdown` corrigida para incluir o pico inicial 1,0 (queda no 1º dia
  avaliado contava como DD zero); (c) `avaliar_holdout` recusa rodar se o
  snapshot atual divergir do gravado pela pesquisa; (d) esclarecimento de
  semântica: "close 00:00 UTC" = fechamento da barra 1h com open-time
  00:00 (≈ preço de 01:00 UTC) — implementação ao vivo deve decidir nesse
  mesmo instante; (e) nota de leitura: o Sharpe de pesquisa da combinação
  ESCOLHIDA é otimista por seleção (max de 8 trials) — a estimativa
  não-enviesada é exclusivamente o hold-out.

## Registro de usos do hold-out

(nenhum)
