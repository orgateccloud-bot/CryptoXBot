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

---

# RODADA 2 — Avaliação diária como PRIMÁRIA (re-pré-registro)

> Escrito em 2026-07-24, **ANTES de coletar os dados novos e de ver qualquer
> resultado desta rodada**. Motivação explícita e honesta: a Rodada 1 reprovou
> o 4h como primário; o 1d (robustez) deu 4/5 falhando só o piso de trades.
> Promover o 1d a primário exige um teste NOVO e mais duro — não reciclar o
> resultado que já vi. É isso que este re-pré-registro faz: **mais dados,
> mais regimes, mais mercados, critérios iguais ou mais rígidos.**
>
> Reconhecimento do que isto NÃO apaga: a escolha do timeframe diário foi
> informada por ter visto o 4h falhar e o 1d brilhar em 2 anos. Isso é uma
> hipótese-derivada-dos-dados, e a defesa contra o viés é (a) dados novos que
> a Rodada 1 não usou (2018-2024, incl. bear market), (b) mercados novos, e
> (c) hold-out temporal intocado avaliado uma única vez.

## Sistema (IDÊNTICO — nada ajustado)

Donchian 20/10, long-only, sizing por risco 2%, custos 0.30% round-trip.
**Zero parâmetros mudados** em relação à Rodada 1 — só o timeframe (1d, o
canônico do Turtle) e os dados. Se eu mexesse em N/M aqui, seria overfit.

## Dados (congelados ANTES da coleta)

- **Timeframe: 1d.**
- **Histórico: ~5 anos** (~1.800-2.000 candles diários por moeda, o máximo que
  a Binance dá para as majors), cobrindo obrigatoriamente: **bull 2021, bear
  2022, recuperação 2023, e 2024-2026.** É o teste que trend-following exige
  (múltiplos regimes) e que a Rodada 1 não tinha (só uma alta de 2 anos).
- **Cesta congelada (7 moedas):** BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT,
  ADAUSDT, SOLUSDT, LINKUSDT. Critério de escolha, declarado antes de ver
  performance: majors com liquidez alta e histórico longo na Binance
  (SOL entra apesar de listagem mais recente ~2020, por já estar no
  universo do bot). **Não são escolhidas por performance** — nenhuma foi
  testada em trend diário antes deste registro (as 3 da Rodada 1 estão
  incluídas por já pertencerem ao bot, e seu resultado prévio em 1d é
  conhecido; as 4 novas são cegas).
- Split: **pesquisa = primeiros 65% de cada série; hold-out = últimos 35%,
  intocado.**

## Regra de decisão (PRIMÁRIA, mesmas 4 + piso de trades restaurado)

Pooled sobre as 7 moedas, na porção de pesquisa:
1. Expectância líquida por trade **> 0**; **E**
2. Profit factor **> 1.3**; **E**
3. Payoff ratio **> 1.5**; **E**
4. Retorno **≥ B&H ajustado a risco** (mesma definição); **E**
5. **≥ 100 trades pooled** — piso RESTAURADO ao valor original do gate
   (não os 30 relaxados da Rodada 1): com 7 moedas × 5 anos há massa para
   isso, então a folga não é mais necessária. **Mais rígido de propósito.**

Adicionalmente, como **evidência de robustez de regime** (reportada e exigida
qualitativamente, não um 6º critério numérico): a estratégia não pode depender
de um único ano nem de uma única moeda — reportar PnL por ano-calendário e por
moeda; se >70% de todo o lucro vier de um único ano OU de uma única moeda,
tratar como frágil e NÃO aprovar mesmo com os 5 critérios verdes.

Passou → hold-out UMA vez (as 5 condições não podem colapsar). Confirmado →
integrar ao `executor.py` e rodar o `GATE_GO_LIVE.md` do zero (Etapa 1
walk-forward do sistema novo → paper 90d → piloto). Falhou → FAIL definitivo
para trend-following long-only nesta forma; levar ao usuário engavetar ou
pivotar (não ajustar).

## Resultado da RODADA 2 (2026-07-24) — 5 de 6; FAIL pelo critério de B&H

Dados: 7 moedas × 2.100 candles diários (out/2020 → jul/2026); porção de
pesquisa = out/2020 → ~meados/2024 (inclui **bull 2021 E bear 2022**).
Hold-out (35% final) **NÃO tocado**. Comando: `python
backtesting/trend_following.py --intervalo 1d`.

**Pooled (185 trades):**

| Critério | Resultado | |
|---|---|---|
| Expectância líquida > 0 | **+2.316%/trade** | ✅ |
| Profit factor > 1.3 | **3.89** | ✅ |
| Payoff ratio > 1.5 | **4.19** | ✅ |
| ≥ 100 trades pooled | **185** | ✅ |
| Robustez de regime | maior ano 48%, maior ativo 29% do lucro | ✅ |
| **≥ B&H ajustado a risco** | **+76.8% vs +1871.1%** (ratio 9.00 vs 22.26) | ❌ |

Win rate 48.1%, retorno médio **+76.8% com DD de 8.5%** vs B&H **+1871% com
DD de 84.0%**. PnL por ano: 2020 +207, 2021 +2781, **2022 −419** (sobreviveu
ao bear com perda pequena), 2023 +677, 2024 +2128. Lucro distribuído entre as
7 moedas (nenhuma > 29%).

**Veredito pré-registrado: FAIL.** Honrado sem reescrever o critério depois
de ver o número — é a regra que dá valor a todo o exercício.

### A tensão metodológica que este resultado revela (para decisão do usuário)

O critério "≥ B&H ajustado a risco" foi **importado do gate original**, que
foi desenhado para uma estratégia de alta frequência em BTC. Aplicado a uma
janela que contém a maior alta da história da cripto (SOL +9.584%, BNB
+1.869% no período), ele é quase impossível de satisfazer **por construção**:
nenhum sistema com stop acompanha buy-and-hold numa explosão dessas — é
matemática, não deficiência da estratégia.

Ao mesmo tempo, o critério reprova um sistema que entrega **+77% com 8.5% de
drawdown** contra **+1871% com 84% de drawdown**. São perfis de risco
incomparáveis: o B&H exigiria suportar perder 84% do pico (e, na prática,
quase ninguém segura — vende no fundo). A razão retorno/DD favorece o B&H
(22 vs 9) só porque o numerador é astronômico.

**Isto NÃO é um pedido para relaxar o critério** — mudá-lo agora seria
exatamente o overfitting que o gate existe para impedir. É a constatação de
que o critério herdado pode estar medindo a pergunta errada para esta classe
de estratégia, e a decisão sobre isso é do usuário, com duas saídas honestas:
(a) aceitar o FAIL e encerrar trend-following long-only; ou (b) re-pré-
registrar UMA Rodada 3 com um critério de comparação declarado antes dos
números e justificado por escrito (ex.: benchmark de risco-alvo comparável, ou
janela que não seja dominada por um único bull histórico), sabendo que
qualquer alteração de critério pós-resultado enfraquece a evidência e deve
ser registrada como tal.

---

# RODADA 3 — Benchmark risco-equivalente + teste por regime (re-pré-registro)

> Escrito em 2026-07-24. **Declaração de custo epistemológico, sem
> eufemismo:** este re-pré-registro acontece DEPOIS de eu ter visto a Rodada 2
> reprovar no critério de B&H. Mudar critério após um FAIL enfraquece a
> evidência — qualquer leitor deve descontar este resultado em relação a um
> pré-registro virgem. Está registrado aqui exatamente para que esse desconto
> seja possível. Decisão do usuário, com o custo explicitado antes.

## Critério A — benchmark risco-equivalente: **FALHA POR ARITMÉTICA (derivado, não medido)**

Definição (a que motivou a Rodada 3): comparar a estratégia com um
buy-and-hold **escalado ao mesmo risco**, investindo só a fração
`f = DD_estrategia / DD_B&H` do capital (resto em caixa), de modo que ambos
tenham o mesmo drawdown.

Aplicado aos números **já publicados** na Rodada 2 (nenhum dado novo
necessário):
- `f = 8.5% / 84.0% = 0.101` → `B&H risco-equivalente = 0.101 × 1871.1% =`
  **+189.3%** vs estratégia **+76.8%** → **FALHA**.
- Espelho (alavancar a estratégia ao risco do B&H): `84.0/8.5 = 9.88×` →
  `9.88 × 76.8% =` **+758.6%** vs **+1871.1%** → **FALHA**.

**Conclusão: o critério A não resgata a estratégia, e isso era previsível.**
Dominância em MAR ratio (retorno/DD: 9.00 vs 22.26) é **invariante à escala**
— nenhuma normalização de risco a inverte. Registrado explicitamente porque é
um resultado importante sobre o *gate*, não sobre a estratégia: o critério
original ">= B&H ajustado a risco" NÃO era injustamente severo, e trocá-lo
não muda o veredito. A hipótese de que "o critério herdado media a pergunta
errada" está, nesta dimensão, **refutada**.

## Critério B — teste por REGIME (o que carrega informação nova)

Justificativa de primeiros princípios, independente de qualquer resultado: a
proposta de valor do trend-following **não é** vencer buy-and-hold num bull
market — é **capturar parte do bull e evitar a maior parte do bear**. Avaliado
pooled sobre uma janela dominada por um bull histórico (~80% do período), B&H
vence trivialmente e o teste não informa nada. Avaliado **por regime**, mede-se
exatamente o que o sistema promete.

**Regimes definidos por ano-calendário, pelo retorno equal-weighted de B&H da
cesta (não por performance da estratégia):**
- **BEAR:** ano com B&H equal-weighted **< −20%**.
- **BULL:** ano com B&H equal-weighted **> +20%**.
- **LATERAL:** o resto.

**Critérios (declarados ANTES de computar — nenhum destes números foi visto):**
1. **Proteção no bear (o essencial):** em TODO ano BEAR, o retorno da
   estratégia deve ser **> retorno do B&H** (perder menos, ou lucrar). Se
   falhar em qualquer bear, FAIL — é a razão de existir do sistema.
2. **Participação no bull:** a média, sobre os anos BULL, de
   `ret_estrategia / ret_B&H` deve ser **≥ 0.30** (capturar ≥30% da alta).
3. **Sem ano catastrófico:** nenhum ano-calendário com perda da estratégia
   **> 25%** do capital.
4. Mantidos da Rodada 2 (já verdes, não podem regredir): expectância > 0,
   PF > 1.3, payoff > 1.5, ≥100 trades, robustez de concentração.

Passou → hold-out UMA vez, verificando os mesmos critérios por regime.
Falhou → **FAIL definitivo** para trend-following long-only; encerrar a linha
de pesquisa (não haverá Rodada 4 — três rodadas com critérios progressivamente
re-registrados é o limite honesto).

## Resultado da RODADA 3 (2026-07-24) — **FAIL DEFINITIVO** (por 0.38 pp)

Comando: `python backtesting/trend_following.py --intervalo 1d --regime`.
Hold-out **NÃO tocado**.

| Ano | Regime | B&H % | Estratégia % | Captura |
|---|---|---|---|---|
| 2020 | BULL | +37.3 | +3.0 | 7.9% |
| 2021 | BULL | +1686.4 | +39.7 | 2.4% |
| **2022** | **BEAR** | **−71.0** | **−6.0** | — |
| 2023 | BULL | +225.0 | +9.7 | 4.3% |
| 2024 | BULL | +29.3 | +30.4 | 103.9% |

| Critério | Resultado | |
|---|---|---|
| Proteção em TODO bear | **2022: −6.0% vs −71.0% do B&H** | ✅ |
| Captura média no bull ≥ 30% | **29.62%** | ❌ |
| Nenhum ano < −25% | pior: −6.0% | ✅ |

**A proteção no bear é espetacular e é o núcleo da tese:** em 2022 o
buy-and-hold da cesta perdeu **71%**; a estratégia perdeu **6%**. Ela entregou
exatamente o que trend-following promete.

**Mas o critério de captura no bull falhou por 0.38 ponto percentual
(29.62% vs 30.00%). Veredito pré-registrado: FAIL DEFINITIVO. Honrado.**

### Falha de desenho do MEU critério (documentada como lição, NÃO como recurso)

O critério 2 usava a **média das razões** `ret_estrategia / ret_B&H` por ano
bull. Essa estatística é patológica quando o denominador é astronômico: em
2021 o B&H fez +1686%, e capturar 2.4% disso significa **+39.7% de retorno
absoluto** — um ótimo ano, pontuado como quase-zero. A média foi esmagada
pelos anos de bull explosivo e salva apenas pelo 2024 (103.9%).

Um critério melhor (retorno absoluto nos anos bull, ou **mediana** da captura,
ou captura ponderada por tempo-no-mercado) provavelmente teria passado. **Eu
reconheço isso e explicitamente NÃO uso como motivo para reverter o veredito
nem para abrir uma Rodada 4** — que, aliás, foi pré-proibida neste documento
antes de eu ver estes números. Trocar a estatística agora, sabendo que a nova
passaria, seria o overfitting exato que este arcabouço todo existe para
impedir. O custo de desenhar um critério imperfeito é pagar por ele.

### Conclusão da linha de pesquisa trend-following

**FAIL definitivo** — encerrada em 3 rodadas, conforme pré-registro. O que
fica documentado como conhecimento durável (e é substancial):
1. O sistema tem **proteção de bear comprovada e forte** (−6% vs −71%).
2. Tem qualidade estatística sólida (expectância +2.32%/trade, PF 3.89,
   payoff 4.19, 185 trades, lucro distribuído entre 5 anos e 7 moedas).
3. **Não vence buy-and-hold** em nenhuma normalização de risco na janela
   2020-2024 — refutado por aritmética invariante à escala (MAR 9.0 vs 22.3),
   não por escolha de critério.
4. O **hold-out (35% final) permanece virgem** — nenhuma das 3 rodadas o
   consumiu. Se algum dia houver uma hipótese nova e independente, ele está lá.

Próximo passo é decisão do usuário, e as opções honestas NÃO incluem mais
rodadas deste sistema: encerrar a busca por estratégia direcional, ou pivotar
para uma classe estruturalmente diferente (funding/basis, market-making de
rebate) com pré-registro virgem.

---

# OBJETIVO NOVO — "exposição com risco controlado" (pré-registro, 2026-07-24)

> **O FAIL contra buy-and-hold permanece de pé e não é contestado.** Este não é
> um re-registro de critério para sustentar a mesma alegação — é a declaração
> de uma alegação **diferente e mais modesta**, que os dados podem refutar.
> Decisão do usuário, com o custo explicitado.

## A alegação que passa a ser testada

**NÃO se alega:** "esta estratégia bate o mercado". (Refutado: MAR 9.0 vs
22.3, aritmética invariante à escala.)

**Alega-se:** "esta estratégia entrega retorno positivo com drawdown
substancialmente menor que a exposição passiva". É um objetivo legítimo e
distinto — um investidor que não tolera perder 80% do capital pode
racionalmente preferir +50% com 13% de drawdown a +415% com 77%. A pergunta
honesta é se os dados sustentam ESSA afirmação, fora da amostra.

## Por que o hold-out ainda é um teste limpo

Os parâmetros **nunca foram otimizados**: N=20/M=10 vêm do Turtle System 1
(convenção histórica), usados sem alteração nas 3 rodadas. O sweep de M
(2026-07-24) foi declarado diagnóstico e **nenhum valor foi selecionado dele**
— o canônico M=10, aliás, já tinha o melhor acerto da faixa testada. Portanto o
sistema não foi ajustado à porção de pesquisa, e o hold-out (últimos 35%,
nunca consumido em nenhuma das 3 rodadas) permanece um teste out-of-sample
válido.

## Medição em nível de CARTEIRA (não média de ativos)

Mudança metodológica declarada: o objetivo é sobre **risco de carteira**, então
a medição passa a construir uma **curva de equity combinada** (alocação igual
de capital nos ativos, trades de todos mesclados cronologicamente) e medir o
drawdown DA CARTEIRA. Média de drawdowns individuais superestima o risco real
(ignora diversificação) e seria desonesta a favor da estratégia em um sentido e
contra em outro — a curva combinada é a medida correta.

Limitação declarada: drawdown sobre trades FECHADOS (sem mark-to-market
intra-trade), igual às rodadas anteriores — subestima o DD real em até ~1
distância de stop por episódio.

## Critérios (declarados ANTES de medir — nenhum destes números foi visto)

Valem para a porção de pesquisa **E** para o hold-out; **o hold-out decide**.

1. **Retorno anualizado da carteira > 8% a.a.** (líquido de custos).
   Justificativa: ~5% a.a. de rendimento de stablecoin + ~3pp de prêmio
   mínimo por assumir risco de mercado, execução e operação. Abaixo disso,
   não compensa operar nada.
2. **Max drawdown da carteira ≤ 20%.** Herdado do próprio
   `GATE_GO_LIVE.md` (Etapa 1) — mantém consistência com o padrão do projeto.
3. **DD da carteira ≤ 35% do DD do buy-and-hold** equal-weighted no mesmo
   período. É a prova de que a proteção é material e não artefato de janela —
   o núcleo da alegação nova.
4. **MAR ratio (retorno anualizado / max DD) ≥ 0.5.** Referência da indústria
   de managed futures (fundos da classe operam tipicamente 0.3-0.5); 0.5 é
   exigente sem ser arbitrário.
5. **≥ 60% dos anos-calendário positivos** (consistência).
6. **≥ 100 trades** na carteira (massa estatística; o piso do gate).

**Passou nos dois (pesquisa e hold-out)** → a alegação de "exposição com risco
controlado" está sustentada out-of-sample; segue para paper trading em
`DRY_RUN` contra a Binance real (Etapa 2 do `GATE_GO_LIVE.md`: 90 dias, ≥30
trades fechados) antes de qualquer capital.

**Falhou no hold-out** → a alegação nova também não se sustenta; encerrar de
vez, sem re-registro. **Pré-proibido:** ajustar parâmetro, trocar critério, ou
reavaliar o hold-out por qualquer motivo (ele é consumido nesta medição).

## RESULTADO do objetivo "risco controlado" (2026-07-24) — **FAIL (5 de 6)**

Medição em nível de carteira (7 ativos, alocação igual, trades mesclados por
instante de saída). Reprodutível: `python backtesting/trend_following.py
--intervalo 1d --carteira [--holdout]`.

### Pesquisa (3.7 anos, 185 trades): PASSOU 6 de 6
+76.77% total (**+20.56% a.a.**), max DD **4.96%**, MAR **4.15**, 80% dos anos
positivos. DD da carteira = **5.9%** do DD do B&H (84.0%).

### Hold-out (2.0 anos, 107 trades) — CONSUMIDO: **FAIL**

| Critério | Resultado | |
|---|---|---|
| Anualizado > 8% a.a. | **+5.70%** | ❌ |
| Max DD ≤ 20% | 5.45% | ✅ |
| DD ≤ 35% do DD do B&H | **7.8%** | ✅ |
| MAR ≥ 0.5 | 1.05 | ✅ |
| ≥ 60% dos anos positivos | 67% | ✅ |
| ≥ 100 trades | 107 | ✅ |

### O que FOI validado out-of-sample (registrar a favor, sem ambiguidade)

**A tese de proteção foi confirmada de forma espetacular.** No período do
hold-out o buy-and-hold da cesta **perdeu 20.55%** com drawdown de **69.9%**;
a estratégia **ganhou +11.46%** com drawdown de **5.45%**. Ou seja: fora da
amostra ela **bateu o mercado em ~32 pontos percentuais com 1/13 do
drawdown**. O mecanismo (sair para caixa quando a tendência quebra) funcionou
exatamente como a teoria prevê, num regime que não estava na porção de
pesquisa.

### Por que o FAIL é correto mesmo assim

O critério reprovado é **retorno absoluto**, e o piso de 8% foi justificado
antes de qualquer número: ~5% a.a. de stablecoin + ~3pp de prêmio por assumir
risco de mercado, execução e operação 24/7. A **+5.70% a.a., a estratégia
entrega essencialmente o mesmo que um rendimento passivo de stablecoin** — e
cobra por isso risco de mercado, complexidade arquitetural e carga
operacional. Um agente racional prefere os ~5% sem risco. O piso estava certo;
o FAIL não é tecnicalidade contratual, é economicamente correto.

### Caracterização honesta do sistema (conhecimento durável)

Dependência de regime, medida nos dois lados:
- **Bull (pesquisa, 2020-2024):** +20.56% a.a., DD 4.96%.
- **Bear (hold-out, 2024-2026):** +5.70% a.a., DD 5.45% — enquanto o mercado
  caía 20.55% com DD de 69.9%.

Leitura em uma frase: **"nunca perde muito, mas em mercado de baixa rende
apenas o de uma stablecoin."** O drawdown de ~5% é notavelmente estável entre
regimes — a proteção é real e replicável. O que não é replicável é retorno
absoluto suficiente para justificar operar.

### Veredito e estado final da linha

**FAIL pré-registrado, honrado. Não implantar** — nem em capital real (o
`GATE_GO_LIVE.md` exige Etapa 1 aprovada, que nunca foi) nem como estratégia
aprovada. **O hold-out do trend está agora CONSUMIDO** — não existe mais teste
limpo disponível para nenhuma variante deste sistema. Encerrado sem
re-registro, conforme pré-compromisso.

## Registro de uso do hold-out

| Data | Evento | Resultado |
|---|---|---|
| 2026-07-24 | Rodada 1: primário 4h falhou (breakeven, não bate B&H) | Hold-out **NÃO tocado**. 1d (robustez) promissor mas raso — motivou o re-pré-registro. |
| 2026-07-24 | Rodada 2 (1d primária, 7 moedas, 5 anos): 5 de 6, FAIL só no critério de B&H | Hold-out **NÃO tocado** (preservado; nenhum resultado desta rodada o consumiu). |
| 2026-07-24 | Rodada 3 (critério A refutado por aritmética; critério B FAIL por 0.38pp) | Hold-out **NÃO tocado**. Linha de pesquisa trend-following ENCERRADA — hold-out preservado virgem para uma futura hipótese independente. |
| 2026-07-24 | Objetivo novo (risco controlado): pesquisa 6/6 → **hold-out CONSUMIDO** | **FAIL (5 de 6)** — +5.70% a.a. vs piso de 8%. Proteção VALIDADA out-of-sample (B&H −20.55%/DD 69.9% vs estratégia +11.46%/DD 5.45%), mas retorno absoluto no nível de stablecoin. Hold-out do trend agora **QUEIMADO** — sem teste limpo restante. |

---

# ANEXO (2026-07-25) — Dry run de validação de EXECUÇÃO

> Registrado a pedido do usuário, DEPOIS do FAIL e sem alterá-lo. Este anexo
> não é uma "Rodada 5", não re-registra critério nenhum e não reabre a
> hipótese. **O veredito acima segue valendo integralmente.**

## O que é (e o que explicitamente NÃO é)

O sistema Donchian 20/10 foi ligado ao caminho ao vivo do bot **apenas em
`DRY_RUN`/paper trading**, via `python main.py --modo-trend --simulacao`.

**É:** um experimento de validação de **execução**. Mede exatamente o que
nenhum backtest consegue medir, porque idealiza: latência entre o sinal e o
fill, desvio entre o preço de referência da decisão (close do candle fechado,
que o backtest assume como preço de entrada) e o preço realmente executado,
comportamento do serviço em 24/7, e estabilidade da infraestrutura.

**NÃO é:** aprovação da estratégia. Nenhum número produzido por este dry run
pode promover o sistema a capital real. Isso exigiria hipótese nova, dados
novos, hold-out novo (o do trend está QUEIMADO) e o `GATE_GO_LIVE.md` — cuja
Etapa 1 também está reprovada. Resultado bom no dry run é evidência sobre a
**infraestrutura**, não sobre o **edge**.

## Trava de segurança (três camadas, não convenção)

1. **Boot:** `main._validar_trend_so_em_simulacao()` → `SystemExit(1)` se
   `--modo-trend` vier com `--real`. Recusa a **intenção**, antes do downgrade
   por `ALLOW_REAL_TRADING` — senão quem hoje é rebaixado para paper por falta
   da env passaria a operar trend com capital real no dia em que a ligasse.
2. **Caminho do dinheiro:** `main._trend_abrir()` recusa se
   `executor.simulacao is False`, mesmo que a camada 1 seja contornada
   (import direto, teste, edição futura do argparse).
3. **Gates herdados:** `DRY_RUN` + `ALLOW_REAL_TRADING` + `ENV=production` +
   flag `--real` continuam todos necessários para qualquer ordem real, e o
   serviço NSSM tem `--simulacao` fixo.

Coberto por `tests/test_trend_live.py::TestTravaDeSeguranca`.

## Paridade com o backtest (senão a medição não significa nada)

- Os níveis vêm da **mesma função** do backtest — `trend_following.
  donchian_niveis` — não de uma reimplementação "equivalente"
  (`test_mesma_funcao_de_niveis` trava isso por identidade de objeto).
- **A vela em formação é descartada.** A Binance devolve o candle vivo como
  último elemento de `/api/v3/klines`; usá-lo seria look-ahead ao vivo (o preço
  ainda se move) e quebraria a paridade com o backtest, que decide no
  fechamento. `trend_live._closes_fechados()` corta o último elemento sempre —
  conservador: no pior caso o dado é um pouco mais velho, nunca do futuro.
- **Uma decisão por candle fechado.** Com `--intervalo 15`, a mesma barra
  diária é vista ~96×; `_trend_ultimo_bucket` garante que só a primeira decide.
  Sem isso o bot reentraria no mesmo dia em que saiu — algo que o backtest
  nunca faz. Falha de fetch **não** marca o bucket (uma queda de rede às 00h01
  não pode cancelar a decisão do dia).
- **`Executor(modo_trend=True)`** desliga o `target2 = entrada*1.05` hardcoded
  e o trailing por percentual fixo. Ambos cortariam a cauda direita — que é
  literalmente todo o edge do trend-following (win rate 42-48%, payoff alto).
  Quem trilha o stop é o canal Donchian-M, via `_aplicar_novo_stop`.

## Divergência conhecida e declarada (não é bug — é o que se quer medir)

O backtest sai no **close** abaixo do canal Donchian-M. Ao vivo, o mesmo nível
também vive como `STOP_LOSS_LIMIT` na exchange, então um toque **intrabar**
dispara antes do fechamento do candle. Consequência: saídas ao vivo são
**iguais ou mais precoces** que as do backtest, nunca mais tardias.
Quantificar esse gap é um objetivo declarado do experimento — não uma surpresa
a ser "descoberta" depois.

Segunda divergência menor, também declarada: `_trend_ultimo_bucket` vive em
memória. Um restart do serviço no meio do dia reavalia o candle corrente; se
uma posição já tinha sido aberta e fechada naquele mesmo dia, o bot pode
reentrar no dia — o que o backtest não faz. Só afeta o dia de um restart, e
como isto é paper trading, não se paga nada por isso; registrado para não ser
"descoberto" como surpresa ao ler os logs.

## Telemetria coletada

Compartilhada com a estratégia otimizada (`main._registrar_execucao`), de
propósito: assim os números das duas são comparáveis, e a estratégia otimizada
— que opera em 1h/4h — fornece **dezenas de amostras por mês** contra as ~5-8
por ano do trend diário. O trend mede fidelidade ao backtest; a otimizada
fornece massa estatística para a mesma pergunta ("a execução come o edge?").

Três preços, não dois:

- `ref` — preço da decisão (trend: close do candle fechado; otimizada:
  `f1h[-1]`, vela em formação vinda do cache com TTL de 30s).
- `mercado` — preço fresco no instante da ordem. Contra `ref`, mede o custo de
  ter decidido sobre dado velho.
- `fill` — preço de abertura efetivo.

Gauges em `/metrics`: `exec_desvio_ref_mercado_pct`, `exec_desvio_ref_fill_pct`,
`exec_latencia_sinal_fill_ms`, `exec_estrategia_trend` (1/0),
`exec_desvio_saida_ref_fill_pct`. Eventos `execucao_entrada`/`execucao_saida`
em `bot_events`.

**Achado ao instrumentar (registrado, não corrigido):** `fechar_posicao`
calcula o PnL sobre a referência que o monitor observou, não sobre o fill —
inclusive em simulação, porque um SELL MARKET lê preço fresco no ramo simulado.
O `pnl_usdt` gravado é otimista por exatamente
`exec_desvio_saida_ref_fill_pct`. Relevante porque é a mesma coluna que a
Etapa 2 do `GATE_GO_LIVE.md` usa para profit factor.

## Nota de amostragem (por que o trend sozinho não basta)

`MAX_POSICOES_ABERTAS = 1` é teto **global** (`risco.py:37`), não por par. Com
3 pares em `PARES_ATIVOS` e ~3 entradas/ano/moeda no trend diário, as três
moedas competem por uma vaga única: na prática **5-8 fills/ano**. Amostra fraca
para estimar latência e slippage — daí instrumentar também a otimizada, em vez
de mudar o trend para 4h (o que quebraria a paridade com o sistema testado, e o
4h já havia reprovado na Rodada 1).
