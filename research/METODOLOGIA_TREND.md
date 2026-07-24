# Redesenho estrutural — trend-following (pré-registro)

> Contrato de método, escrito ANTES de rodar o backtest. Mesmo propósito
> anti-overfit do GATE_GO_LIVE.md e do research/METODOLOGIA.md.
> Criado em: 2026-07-24 · Direção escolhida pelo usuário após a pesquisa de
> edge concluir que previsão direcional em BTC 1h é economicamente morta.

## Por que trend-following (a tese estrutural)

O diagnóstico provou que a estratégia velha precisava de um edge PREDITIVO
(win rate > breakeven) para bater uma barreira 1:1 — e não tinha. Alfa
0.13%/trade < custo 0.30%/trade.

Trend-following não joga esse jogo. Mecanismo de expectância positiva
documentado (time-series momentum; décadas de evidência cross-market, incl.
cripto): **skew positivo** — muitos trades de perda PEQUENA (a tendência não
se materializa, sai no trail) e poucos de ganho GRANDE (uma tendência real
corre). Win rate baixo (~35-45%), payoff ratio alto (>1.5). E o ponto que
mata o problema de custos: um trade vencedor é +10-30%, então 0.3% de custo
é ~1-3% do movimento (irrelevante), contra 15% do movimento na barreira +2%
da estratégia velha.

Modo de falha a respeitar: **whipsaw em mercado lateral** (entra tarde, sai
no stop, re-entra, sai — morte por mil cortes). Por isso o teste tem que
incluir mercado de baixa/lateral, e a comparação com buy-and-hold tem que
ser risk-adjusted (num período de alta, long-only trend pode parecer bom só
por capturar beta).

## Sistema (canônico, congelado — parâmetros NÃO ajustados)

**Donchian channel breakout, long-only** (long-only é restrição real do
`executor.py`; não é escolha de conveniência):
- **Entrada:** `close[i]` rompe acima do maior `close` dos N candles
  anteriores (Donchian superior, causal — não inclui o candle i).
- **Saída:** `close[i]` cai abaixo do menor `close` dos M candles anteriores
  (Donchian inferior). O M-exit É o trailing stop (recomputa a cada candle —
  deixa o ganho correr, corta a perda). Sem alvo fixo (o ponto do
  trend-following).
- **Uma posição por vez** (igual ao executor). Em posição, ignora novas
  entradas; checa saída a cada candle.
- **Parâmetros:** **N=20 (entrada), M=10 (saída)** — os números literais do
  Turtle System 1 (Dennis/Eckhardt), o sistema de trend mais documentado e
  menos ajustável (2 parâmetros, escolhidos por convenção histórica, NÃO por
  otimização nestes dados).

**Sizing:** risco fracionário fixo — arriscar 2% do capital até o stop
inicial (Donchian-M inferior no momento da entrada). `notional =
0.02*capital / ((entrada - stop)/entrada)`. É o sizing canônico de
trend-following (o mesmo espírito do Kelly/vol-target de `risco.py`).

**Custos:** iguais ao gate — 0.10%/lado (taxa spot taker; entrada por breakout
CRUZA o spread, então taker é honesto) + 0.05%/lado (slippage) = 0.30%
round-trip. Sem maker-first (breakout não descansa no book).

## Timeframe e universo (congelado)

- **Timeframe primário: 4h.** Trend-following pede timeframe alto (menos
  ruído, trends persistem); 1h — onde a estratégia velha falhou — é ruidoso
  demais. Daily seria o canônico do Turtle, mas 2 anos = ~730 candles daily
  (trades de menos). 4h é o meio-termo: 4.380 candles, higher-TF que 1h.
- **Universo: BTCUSDT, ETHUSDT, SOLUSDT — avaliação POOLED.** Trend-following
  é um fenômeno multi-mercado (o Turtle operava dezenas de mercados). Rodar
  o MESMO sistema nos 3 e agregar os trades (a) alcança massa estatística
  para o gate sem encurtar lookback/timeframe (o que descaracterizaria o
  sistema), e (b) testa a UNIVERSALIDADE do edge — um trend real replica
  entre ativos correlacionados-mas-distintos. Reporta per-asset E pooled; o
  **pooled é o primário**.
- **Robustez (reportada, não decide):** 1d (BTC) e 1h, como sensibilidade.

## Hold-out (uso único)

Split cronológico por ativo: **pesquisa = primeiros 65%** de cada série;
**hold-out = últimos 35%**, intocado. Avaliado no máximo uma vez, só se a
pesquisa passar a regra de decisão. Registrado abaixo no ato.

## Regra de decisão (pré-registrada)

Adaptação PRÉ-REGISTRADA do gate à classe de estratégia (trend-following é
baixa-frequência; o piso de ≥100 trades do gate foi calibrado para a
barreira de alta-frequência). Justificativa datada antes de qualquer
resultado: trend systems se avaliam por anos de dados × múltiplos mercados
com dezenas de trades, não por 100+ trades num ativo. O pooling BTC/ETH/SOL
recupera massa estatística honestamente.

**HÁ EDGE (prosseguir ao hold-out):** as QUATRO na porção de pesquisa,
POOLED:
1. Expectância líquida por trade **> 0** (após custos de 0.30%); **E**
2. Profit factor **> 1.3**; **E**
3. Payoff ratio (ganho médio / perda média) **> 1.5** (assinatura do
   skew positivo — trend-following que não tem isso não é trend-following); **E**
4. Retorno **≥ buy-and-hold ajustado a risco**: `ret_estrategia ≥ ret_B&H`
   OU `ret_estrategia/maxDD_estrategia ≥ ret_B&H/maxDD_B&H` (mesma definição
   do gate), com o retorno de B&H medido no MESMO período pooled.

Além disso, **≥ 30 trades pooled** (piso da Etapa 2 do gate; abaixo disso
qualquer métrica é frágil — reportar, não aprovar).

Passou → hold-out UMA vez (mesmas 4 condições não podem colapsar). Confirmado
→ integrar ao executor e rodar o GATE_GO_LIVE.md completo (Etapa 1
walk-forward do sistema novo → paper 90d → piloto). Não confirmado → FAIL.

**NÃO HÁ EDGE (FAIL):** qualquer condição falha. Levar ao usuário: ajustar o
sistema NÃO é permitido (seria overfit); as opções honestas seriam outro
timeframe canônico pré-registrado (1d) ou aceitar que nem trend-following
extrai edge líquido aqui e engavetar/pivotar.

## Registro de mudanças de método (pré-resultado)

| Data | Mudança | Justificativa |
|---|---|---|
| 2026-07-24 | Piso de trades do gate adaptado (100→30 pooled) para a classe trend-following | Trend é baixa-frequência; pooling BTC/ETH/SOL recupera massa. Decidido antes de ver performance. |

## Resultado (2026-07-24)

Rodado na porção de pesquisa (2 anos, hold-out INTOCADO). Reprodutível:
`python backtesting/trend_following.py --intervalo {4h,1d}`.

**Primário (4h pooled BTC/ETH/SOL, 198 trades): FALHOU (2 de 5).**
- Payoff ratio 1.91 (✓, o skew positivo do trend-following ESTÁ presente) e
  DD 22% vs 54% do B&H (metade) — o mecanismo funciona estruturalmente.
- Mas expectância −0.042%/trade (✗) e PF 0.96 (✗) — breakeven levemente
  negativo. **Bruto de custos, a expectância é ~+0.26%/trade (positiva)** — a
  estrutura captura edge, mas o custo de 0.30%/trade o come por pouco (198
  trades = arrasto de custo grande). BTC foi o pior (whipsaw numa alta forte,
  ficou atrás do B&H). Não bate B&H (nem risk-adjusted: −0.22 vs 0.39).

**Robustez, NÃO decide (1d pooled, 27 trades): 4 de 5.**
- Expectância +1.10%/trade, PF 2.67, payoff 2.48, risk-adjusted 3.28 vs 0.46
  (7× melhor que B&H), DD 3.0% vs 50.4%. Só falha o piso de ≥30 trades (27).
- Confirma a hipótese mecânica: no diário o trend segura por semanas, negocia
  27× em vez de 198×, o custo vira ruído, o edge bruto vira líquido forte.
- **NÃO é aprovação** (foi pré-registrado como robustez; 27 trades é raso; o
  SOL com PF 18 em 8 trades cheira a sorte de amostra pequena). É uma pista
  forte de que o timeframe é o lever certo.

### Consequência pré-registrada + próximo passo honesto

O primário (4h) falhou → não é aprovação; hold-out permanece intocado.
**Ajustar N/M/timeframe até passar é proibido** (overfit). Mas a pista do 1d
é forte e MECANICAMENTE motivada (não é fishing: o custo/frequência era o
problema diagnosticado, e o timeframe maior o resolve). O caminho disciplinado
NÃO é "declarar o 1d vencedor", e sim **RE-PRÉ-REGISTRAR uma avaliação diária
como PRIMÁRIA com massa estatística honesta** — antes de ver o resultado dela:
- coletar ~4-5 anos de candles diários (BTC/ETH desde ~2018-2020) → span de
  BULL 2021 + BEAR 2022 + recuperação 2023 + 2024-25 (o teste que trend-
  following EXIGE: múltiplos regimes, não uma única alta);
- + universo maior de moedas líquidas para engordar a contagem de trades;
- rodar UMA vez como primário; se passar, hold-out UMA vez.
Decisão de investir nisso: do usuário (é coleta + ciclo de avaliação novos).

## Registro de uso do hold-out

| Data | Evento | Resultado |
|---|---|---|
| 2026-07-24 | Primário 4h falhou (breakeven, não bate B&H) | Hold-out **NÃO tocado**. 1d (robustez) promissor mas raso — motiva re-pré-registro de avaliação diária primária, não uso do hold-out. |
