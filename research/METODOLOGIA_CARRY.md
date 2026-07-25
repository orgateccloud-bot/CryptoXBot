# Pivô estrutural — funding/basis carry (pré-registro)

> Contrato de método, escrito ANTES de medir qualquer coisa. Hipótese **nova e
> independente** das anteriores (barreira 2:1, previsão direcional,
> trend-following) — logo tem hold-out próprio e virgem.
> Criado em: 2026-07-24.

## Por que carry é estruturalmente diferente

Tudo que falhou nesta sessão dependia de **prever** algo:
- Barreira 2:1 → precisava de win rate > breakeven. Não tinha (era moeda).
- Previsão direcional por ML → tinha sinal real (AUC 0.62, p=0.008) mas alfa
  0.13%/trade < custo 0.30%/trade.
- Trend-following → tinha skew positivo e proteção de bear excelente, mas não
  batia buy-and-hold (aritmética invariante à escala).

Carry **não prevê nada**. A posição é **delta-neutra** (imune à direção do
preço) e a receita vem de um pagamento **publicado e observável**: o funding
rate que os longs alavancados pagam aos shorts a cada 8h. O edge é
*estrutural* — você é remunerado por fornecer liquidez ao lado alavancado da
demanda, não por adivinhar preço.

## A estratégia (cash-and-carry delta-neutra)

- **Perna 1:** comprar N em BTC **spot**.
- **Perna 2:** vender a descoberto N em BTC **perpétuo** (USDT-M).
- Quantidades casadas ⇒ os PnLs de preço se cancelam exatamente (perp linear:
  PnL_short = −(P₁−P₀)·qty; spot = +(P₁−P₀)·qty). **Delta ≈ 0.**
- **Receita:** a cada 8h, o short recebe `funding_rate × notional` quando o
  funding é positivo; **paga** quando é negativo.

### Variante A — PRIMÁRIA: buy-and-hold da estrutura (ZERO parâmetros)

Entra no início do período, sai no fim. Uma entrada, uma saída. Coleta **todo**
o funding, positivo e negativo, sem filtro nenhum.

**Escolhida como primária deliberadamente porque tem ZERO graus de liberdade —
é literalmente impossível de sobreajustar.** Se ela passar, o edge é estrutural
puro, sem nenhum ajuste. É o teste mais honesto que existe para esta classe.

### Variante B — SECUNDÁRIA (robustez, NÃO decide)

Filtro canônico sem otimização: manter a estrutura quando a média móvel de 7
dias do funding for positiva; sair quando virar negativa; decisão no máximo 1×
por dia. Paga o custo de round-trip a cada alternância. Reportada para
diagnóstico; **não pode ser promovida a primária** (foi a lição amarga da
Rodada 3 do trend-following).

## Modelo de custo (honesto — é aqui que carry morre)

- **Entrada:** spot taker 0.10% + perp short taker 0.05% = **0.15%** do notional.
- **Saída:** spot taker 0.10% + fechar perp 0.05% = **0.15%**.
- **Round-trip: 0.30%** do notional. Taker nas duas pernas (conservador; maker
  reduziria, mas não assumo execução passiva).

## Modelo de capital (a parte que infla o retorno se ignorada)

Carry exige capital nas **duas** pernas:
- Perna spot: **1.00 × N** em USDT (compra à vista, wallet spot).
- Margem do short perp: **0.25 × N** (alavancagem 4×, liquidação a ~+25% de
  alta do preço). Wallets spot e futures são separadas na Binance.
- **Capital total = 1.25 × N.** Todo retorno é reportado **sobre 1.25N**, não
  sobre N — ignorar isso inflaria o retorno em 25%.

**Premissa de rebalanceamento (declarada):** posição rebalanceada para notional
constante (prática padrão: realizar funding e repor margem). Assim o retorno
sobre notional é exatamente `Σ funding_rates` do período, que é como carry é
cotado no mercado. Não depende de série de preço.

## Riscos NÃO capturados pelo backtest (declarados antes, não podem ser "descobertos" depois)

1. **Liquidação do short** numa alta violenta (>25% acima da entrada sem
   reposição de margem) — perda catastrófica da perna, quebra a neutralidade.
   Mitigável com monitor de margem, mas é risco operacional real.
2. **Risco de exchange/custódia** — capital preso em duas wallets numa única
   exchange.
3. **Mudança de regime de funding** — a Binance pode alterar cap/fórmula.
4. **Slippage em stress** — os 0.30% assumem book normal.
5. **Implicação arquitetural (custo de construção):** o bot é **spot-only**
   (`executor.py` → `/api/v3/order`); Futures hoje é lido apenas como
   sentimento. Carry exige uma **perna de execução em futures nova**
   (`/fapi/v1/order`), gestão de margem/colateral e monitor de delta. É a
   maior mudança arquitetural já proposta neste projeto — razão adicional
   para provar o edge ANTES de escrever qualquer linha de execução.

## Regra de decisão (pré-registrada — números ainda NÃO vistos)

Janela: histórico de funding disponível (BTC desde 2019-09, ETH desde
2019-11), split cronológico **pesquisa = primeiros 65%**, **hold-out = últimos
35% (virgem, uso único)**.

**HÁ EDGE (prosseguir ao hold-out)** — as QUATRO na variante A (primária),
sobre a porção de pesquisa, retorno **sobre capital 1.25N** e **líquido dos
0.30%**:
1. **Retorno anualizado > 10%.** Justificativa do piso (independente de
   resultado): rendimento de stablecoin realista é ~5% a.a. com risco
   operacional muito menor; carry precisa entregar ~2× isso para compensar
   risco de liquidação, execução e exchange. Abaixo de 10% não vale a
   complexidade arquitetural.
2. **≥ 70% dos meses positivos** — consistência. Carry que só funciona em bull
   é uma aposta de beta disfarçada.
3. **Pior mês > −3%** — controle de cauda.
4. **Sobrevive ao regime de funding negativo** (o bear de 2022): retorno do
   subperíodo com funding médio negativo não pode ser pior que **−5%**
   anualizado. Testa se a estrutura aguenta pagar funding sem filtro.

Além disso: **≥ 24 meses** de dados na porção de pesquisa (massa mínima).

Passou → hold-out **UMA vez**, mesmos 4 critérios. Confirmado → só então
avaliar a construção da perna de futures, e passar pelo GATE_GO_LIVE.md.

**NÃO HÁ EDGE (FAIL):** qualquer critério falha → não construir. **Fica
pré-proibido:** trocar critério, promover a variante B a primária, ou abrir
"Rodada 2" deste pivô. Uma medição, um veredito. (Pré-compromisso explícito
por causa da Rodada 3 do trend-following, onde a média-de-razões falhou por
0.38pp e a tentação de re-registrar foi real.)

---

# RESULTADO (2026-07-24) — pesquisa PASSOU, hold-out **FAIL por decaimento de edge**

Dados: funding real da Binance, BTC desde 2019-09 e ETH desde 2019-11
(7.531 + 7.297 pagamentos de 8h; ~6.9 e 6.7 anos). Variante A (primária,
zero parâmetros). Reprodutível: `python research/carry_lab.py [--holdout]`.

## Pesquisa (primeiros 65% — 2019-09 → 2024-01): **PASSOU 5 de 5**

| Critério | Resultado | |
|---|---|---|
| Anualizado > 10% | **+13.10% a.a.** | ✅ |
| ≥ 70% meses positivos | **93.4%** | ✅ |
| Pior mês > −3% | **−1.25%** | ✅ |
| Pior ano > −5% a.a. | **+0.63% a.a.** (nunca negativo) | ✅ |
| ≥ 24 meses | 53 | ✅ |

Destaque: **em 2022 — o bear que destruiu todas as outras hipóteses — o carry
ficou POSITIVO** (BTC +3.33%, ETH +0.63% a.a.). A neutralidade de delta
funcionou como prometido.

## Hold-out (últimos 35% — 2024-01 → 2026-07): **FAIL (1 de 5 critérios)**

| Critério | Resultado | |
|---|---|---|
| Anualizado > 10% | **+4.84% a.a.** | ❌ |
| ≥ 70% meses positivos | 89.8% | ✅ |
| Pior mês > −3% | −0.25% | ✅ |
| Pior ano > −5% a.a. | +0.72% a.a. | ✅ |
| ≥ 24 meses | 29 | ✅ |

## O diagnóstico: o edge é REAL, mas foi ARBITRADO

A falha é de **magnitude, não de existência** — e o padrão é inequívoco:

| Ano | BTC | ETH | média |
|---|---|---|---|
| 2021 | +24.50% | +30.05% | +27.3% |
| 2022 | +3.33% | +0.63% | +2.0% |
| 2023 | +6.30% | +6.61% | +6.5% |
| 2024 | +9.33% | +8.21% | +8.8% |
| 2025 | +4.10% | +3.95% | +4.0% |
| **2026** | **+1.43%** | **+0.72%** | **+1.1%** |

O prêmio de funding **comprimiu monotonicamente**: de ~13% a.a. (2019-2024)
para ~**1% a.a.** (2026). Toda a consistência se manteve (89.8% dos meses
positivos, pior mês −0.25%, nunca um ano negativo) — o que decaiu foi o
**tamanho** do prêmio. Interpretação econômica direta: à medida que o mercado
de cripto amadureceu e mais capital passou a executar exatamente este trade, o
prêmio pago pelos longs alavancados foi arbitrado para perto de zero. É o
ciclo de vida normal de um edge estrutural conhecido.

**Variante B (robustez, não decide):** pesquisa +10.12%/+14.14%; hold-out
+3.71%/+2.75% — **pior que a A** no hold-out (os custos das 27-33 alternâncias
excedem o ganho de evitar funding negativo). Confirma que A era a primária
correta e que **nenhuma variante alcança o piso**.

## Veredito: FAIL honrado — NÃO construir

Os 4.84% a.a. do hold-out ficam **abaixo do próprio benchmark de ~5% de
rendimento de stablecoin** que justificou o piso de 10% — e o ano corrente
(2026) roda a ~1%. Construir a perna de execução em futures (a maior mudança
arquitetural já proposta neste projeto), assumindo risco de liquidação, de
margem e de exchange, para capturar menos que um rendimento passivo de
stablecoin, seria irracional. Sem re-registro de critério (pré-proibido).

## Por que este resultado valida toda a metodologia

Este é o caso didático de **por que hold-out existe**: a porção de pesquisa
disse "13% a.a., aprovado em 5 de 5". Se eu tivesse construído com base nela
— o que qualquer backtest sem hold-out teria autorizado — teria entregado um
sistema complexo de futures para capturar um edge **que já não existe mais**.
O hold-out custou uma execução de 2 segundos e evitou semanas de construção
sobre uma premissa morta.

**Conhecimento durável:** o prêmio de funding em BTC/ETH era real e robusto
(positivo em todos os anos, inclusive no bear de 2022), e foi comprimido de
~13% a.a. para ~1% a.a. entre 2021 e 2026. Se algum dia o funding voltar a
níveis elevados (bull alavancado), a medição está pronta e roda em segundos —
`carry_lab.py` + `coletar_funding.py` ficam como instrumento permanente.

## Registro de uso do hold-out

| Data | Evento | Resultado |
|---|---|---|
| 2026-07-24 | Pesquisa (65%): 5 de 5 critérios | **APROVADO** → autorizou o uso único do hold-out |
| 2026-07-24 | **Hold-out CONSUMIDO (uso único)** | **FAIL** — +4.84% a.a. vs piso de 10%. Edge real mas arbitrado (~1% a.a. em 2026). Não construir. Hold-out de carry agora **QUEIMADO** (não pode ser reusado para nenhuma variante desta hipótese). |
