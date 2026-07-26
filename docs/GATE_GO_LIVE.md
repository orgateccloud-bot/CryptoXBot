# GATE DE GO-LIVE — Critérios Pré-Registrados

> **Natureza deste documento:** contrato de decisão, escrito ANTES de existir
> qualquer resultado. Alterar estes critérios depois de ver os números anula o
> propósito do gate. Qualquer mudança exige justificativa escrita aqui, datada,
> e reinicia o relógio de validação.
>
> Criado em: 2026-07-23 · Status: **ETAPA 1 EXECUTADA EM 2026-07-24 —
> ESTRATÉGIA REPROVADA (4 de 5 critérios). Capital real segue PROIBIDO.**

## Estado atual (honesto)

- Engenharia: pronta (o scorecard do vault cobre infra, não estratégia).
- Estratégia: **medida pela primeira vez em 2026-07-24 e REPROVADA** — a
  configuração atual perde dinheiro no walk-forward de 2 anos (retorno
  −21.25% vs +14.09% do buy-and-hold, profit factor 0.86, DSR 0.23). Nos
  termos pré-registrados deste gate: estratégia morta; volta ao desenho
  (mudanças estruturais, não ajuste de parâmetros); o backtest reinicia do
  zero após qualquer mudança. Capital real: **proibido**.

## Etapa 1 — Backtest Walk-Forward (custo: 1 tarde)

Dados: mínimo 2 anos de candles 1h/4h (obrigatório cobrir mercado de alta,
de baixa e lateral — testar só em alta é autoengano).

**Comando congelado da medição oficial (B8, pré-registrado 2026-07-23 —
qualquer outro flag/parâmetro invalida a medição):**

```bash
python backtesting/coletar_dados.py
python backtesting/walk_forward.py --par BTCUSDT --intervalo 1h --treino 500 --teste 100 --capital 1000 --taxa 0.001
```

| Critério | Mínimo para aprovar | Resultado | Passou? |
|---|---|---|---|
| DSR (PSR, 1 único backtest — probabilidade) | ≥ 0.95 ¹ | **0.2260** | ❌ |
| Profit factor (após TAXA+SLIPPAGE do motor) | > 1.3 | **0.86** | ❌ |
| Nº de trades no teste (todas as janelas) | ≥ 100 | **156** | ✅ |
| Retorno vs buy-and-hold BTC no mesmo período | ≥ B&H ajustado a risco ² | **−21.25% vs +14.09%** (retorno negativo reprova direto pela definição ²) | ❌ |
| Max drawdown | ≤ 20% | **28.90%** | ❌ |

### Resultado da medição oficial (2026-07-24) — **ESTRATÉGIA REPROVADA (4 de 5)**

Registro B8 (reprodutibilidade):
- Comando: `python backtesting/walk_forward.py --par BTCUSDT --intervalo 1h --treino 500 --teste 100 --capital 1000 --taxa 0.001` (com `PYTHONIOENCODING=utf-8`)
- Código: commit `4f84f6b61d61964b6744da4b5885ec71e02efe04` (régua corrigida B1-B8, 15 testes)
- Dados: klines BTCUSDT — 1h: 17.520 candles (ts 1721768400000→1784836800000, 23/07/2024→23/07/2026); 4h: 4.380 candles (ts 1721779200000→1784836800000). F&G: 3.091 dias (alternative.me). Período efetivamente testado: 15/08/2024 21:00 → 21/07/2026 00:00 (169 janelas)
- Detalhe: 156 trades (52W/104L), win rate 33.3%, Sharpe −0.98, Sortino −1.56, Calmar −0.40, capital $1.000 → $787.46

**Sensibilidade de taxa (o veredito NÃO depende da fee):**

| Taxa/lado | PF | Retorno | DSR | Max DD |
|---|---|---|---|---|
| 0.100% (spot taker — oficial) | 0.86 | −21.25% | 0.2260 | 28.90% |
| 0.075% (maker+BNB) | 0.89 | −16.84% | 0.2896 | 25.77% |
| 0.040% (tarifa legada de futures — irrealista) | 0.93 | −10.24% | 0.3918 | 21.16% |

Mesmo com a taxa irrealisticamente baixa que o motor usava antes da correção
B5, a estratégia perde dinheiro e reprova em todos os critérios exceto o de
nº de trades. O problema não é custo de transação — é a estratégia.

**Quantificação do viés B1 (diagnóstico `--mtf-lookahead-legado`, taxa 0.100%):**
com o look-ahead legado: 148 trades, PF 0.87, retorno −19.14%, DSR 0.2544.
Neste dataset o delta do bug foi pequeno (~2 p.p. de retorno) — o veredito
não muda com ou sem o bug; ambos reprovam.

**Consequência pré-registrada** (parágrafo abaixo, escrito antes da medição):
estratégia morta; volta ao desenho; NÃO ajustar parâmetros até passar;
mudanças estruturais apenas; o backtest reinicia do zero. As Etapas 2 e 3
NÃO iniciam.

¹ **Correção pré-registrada (2026-07-23, ANTES de qualquer medição):** o
critério original "DSR > 0" era vácuo — `deflated_sharpe_ratio(rets, None)`
retorna o PSR, uma **probabilidade em (0,1)** que nunca é ≤ 0; até uma
estratégia perdedora marca ~0.05-0.30 e "passaria". O limiar honesto da
literatura (Bailey & López de Prado) é **≥ 0.95** (95% de confiança de
Sharpe verdadeiro > 0). Nenhum número havia sido produzido quando esta
correção foi feita — é pré-registro, não ajuste pós-hoc.

² **Definição pré-registrada de "ajustado a risco" (2026-07-23, antes de
qualquer medição):** aprova se `retorno_estrategia ≥ retorno_B&H` **ou**
(`retorno_estrategia > 0` **e** `retorno_estrategia/maxDD_estrategia ≥
retorno_B&H/maxDD_B&H`). B&H medido no período efetivamente TESTADO
(fechamento do 1º candle da 1ª janela de teste → último candle testado),
com max drawdown da série de preços 1h no mesmo intervalo. Ambos os lados
reportados brutos junto do veredito.

**Reprovou em qualquer linha → estratégia morta.** Volta ao desenho da
estratégia. NÃO ajustar parâmetros até passar (isso é overfitting manual);
mudanças estruturais apenas, e o backtest reinicia do zero.

**Ablação FSRS — já resolvida (2026-07-21), por remoção:** a versão original
deste gate pedia uma flag `--sem-fsrs` para medir a contribuição do FSRS.
Ficou obsoleto antes de executar: auditoria constatou que o FSRS **nunca
ativava no caminho de decisão ao vivo** (a condição `hasattr(ens_mod,
"symbol")` era sempre falsa, então o fator ficava fixo em 0.5 neutro — branch
morto desde sempre, não uma feature em uso), e o módulo `fsrs_trading.py` foi
removido do repo por decisão do usuário. Resultado prático: o walk-forward
padrão JÁ é o cenário "sem FSRS" — não há segunda medição a fazer.

## Etapa 2 — Paper Trading (custo: 90 dias corridos)

Só inicia se a Etapa 1 aprovar. Serviço 24/7 (NSSM/systemd), `DRY_RUN=true`.

| Critério | Mínimo para aprovar | Resultado | Passou? |
|---|---|---|---|
| Duração contínua | ≥ 90 dias | — | ☐ |
| Trades fechados (`pnl_usdt IS NOT NULL`) | ≥ 30 | — | ☐ |
| Profit factor | > 1.3 | — | ☐ |
| PnL total | > 0 | — | ☐ |
| Retorno vs buy-and-hold BTC no período | ≥ B&H | — | ☐ |
| Max drawdown sobre equity simulada | ≤ 15% (MAX_DRAWDOWN_TOTAL) | — | ☐ |

Medição: `python relatorio_gate.py` (fonte única de verdade — nada de conta
de cabeça). **Qualquer mudança de estratégia/parâmetro durante os 90 dias
zera o relógio.** Correção de bug de infra não zera, mas deve ser registrada.

### Correção de infra registrada (2026-07-26) — PnL passa a usar o FILL

O `GATE_GO_LIVE.md` exige que correção de bug de infra seja **registrada** (não
zera o relógio da Etapa 2). Registro:

`executor.fechar_posicao` calculava `pnl_usdt`/`pnl_pct` sobre `preco` — a
**referência** que o monitor observou ao decidir fechar — e não sobre o preço em
que o SELL preencheu. Também gravava essa referência em `sinais.preco_saida`.
Como `pnl_usdt` é exatamente a coluna que `relatorio_gate.py` usa para **profit
factor** e **PnL total** nesta Etapa 2, o registro era otimista pelo slippage de
saída.

Os dois preços diferem **mesmo em simulação**: um SELL MARKET chega em
`_enviar_ordem` sem preço, e o ramo simulado cai em `preco or self.get_preco()`
→ lê preço fresco. Logo o viés existia no registro de paper trading, não só em
modo real.

Corrigido: PnL, `sinais.preco_saida` e o label `FECHAR_LONG`/`STOP` passam a sair
do preço executado, via `executor.preco_medio_fill()`. A **decisão** de fechar
segue sendo tomada sobre `preco` em `avaliar_tick_monitor` — decide-se no que se
vê, contabiliza-se no que se executa.

`preco_medio_fill()` não lê `resp["price"]` cegamente: numa ordem MARKET a
Binance devolve `price: "0.00000000"`, então a fonte de verdade é
`cummulativeQuoteQty / executedQty`. Sem isso, a correção valeria só em
simulação e em modo real cairia no fallback silenciosamente. A mesma regra passou
a valer na **entrada**, que em modo real usava o preço-*limite* em vez do fill
médio (subestimava o lucro num LIMIT que cruza o book e preenche melhor).

**Consequência para o registro:** qualquer trade fechado ANTES desta data tem
`pnl_usdt` otimista pelo slippage de saída. Trades a partir daqui são
comparáveis entre si; a série anterior não é comparável com a nova.

### O `--modo-trend` NÃO conta como Etapa 2 (2026-07-25)

`python main.py --modo-trend --simulacao` roda o sistema Donchian 20/10 em
paper trading, mas **não é** a Etapa 2 deste gate e não avança nenhum critério
da tabela acima. Motivos:

1. A Etapa 2 só pode começar se a Etapa 1 aprovar — e ela **reprovou** (4 de 5).
2. A estratégia trend também tem FAIL pré-registrado próprio
   (`research/METODOLOGIA_TREND.md`, hold-out CONSUMIDO). Rodar em paper não
   revoga um FAIL.
3. O que aquele dry run mede é **execução** (latência sinal→fill, desvio
   ref↔fill, estabilidade 24/7), não performance de estratégia. É evidência
   sobre a infraestrutura, não sobre edge.

Trava correspondente no código: `--modo-trend` + `--real` → `SystemExit(1)` no
boot, mais uma recusa no próprio caminho da ordem
(`main._trend_abrir`). Ver `tests/test_trend_live.py::TestTravaDeSeguranca`.

## Etapa 3 — Capital Real Piloto (custo: 30 dias)

Só inicia se a Etapa 2 aprovar.

- Capital piloto: valor cuja perda TOTAL não incomoda (ordem de "um jantar
  caro", não de "um salário"). Definir valor por escrito aqui antes de ligar: R$ ____
- API: permissão SPOT apenas (leitura + trading spot), saque DESABILITADO,
  IP whitelist do servidor. Conferir contra `config/api_key_LEIA_ANTES_DE_PREENCHER.txt` (versão corrigida).
- Rodar 30 dias comparando execução real vs simulada (slippage e fills).
- Aprovação final: divergência real-vs-paper pequena E métricas da Etapa 2
  sustentadas → escalar gradualmente. Divergência grande → voltar à Etapa 2.

### Pré-condições de CONTA — verificadas em 2026-07-26, **nenhuma satisfeita**

Auditoria da conta real (investigação do "saldo zerado" no dashboard) mostrou
que, além dos gates de código, existem três condições de **conta/chave** que
nunca estiveram escritas aqui. Sem elas o `--real` não falha no boot — falha na
primeira ordem, ou pior, dimensiona sobre um número errado.

| Pré-condição | Como verificar | Estado em 2026-07-26 |
|---|---|---|
| Chave com `enableSpotAndMarginTrading` | `python binance_conta.py` → `restricoes_chave` | ❌ **False** — chave é read-only |
| USDT suficiente na Spot (é a moeda de entrada) | idem → `saldos` | ❌ **0.00000075** (pó). O valor está em BTC (0.0207 ≈ $1.339) |
| `ipRestrict` ligado na chave | idem → `restrito_por_ip` | ❌ **False** |

Notas que custaram tempo para descobrir e não devem ser redescobertas:

- **`canTrade` de `/api/v3/account` é da CONTA, não da CHAVE.** Ele estava
  `True` enquanto a chave era read-only. A fonte correta é
  `/sapi/v1/account/apiRestrictions` → `enableSpotAndMarginTrading`
  (`binance_conta.restricoes_chave()`).
- **Com chave read-only, toda ordem volta `-2015`** (Invalid API-key... for
  action). É falha visível em log, não silenciosa — mas só na hora da ordem.
- **O capital está do lado errado do par.** O bot compra com USDT
  (`abrir_long` → BUY); a conta está long BTC sem moeda de cotação. Mesmo com
  chave de trading, o sizing sai abaixo do `minQty` e `_enviar_ordem` recusa
  localmente.
- **`saldo > 0` não protege**: `main.py` faz `saldo if saldo > 0 else 100`, e
  `7.5e-07 > 0` é verdadeiro — o fallback de $100 **não** dispara, passa-se o pó
  adiante. Não confiar nesse guard para detectar conta vazia.
- **Drift de relógio medido: −675 ms** (mediana de 6 amostras corrigidas por
  RTT). Dentro da tolerância (a Binance rejeita `timestamp > serverTime + 1000`;
  atrasado só falha além do `recvWindow` de 5000 ms). Desde 2026-07-26 todas as
  chamadas assinadas de conta passam por `binance_conta.timestamp_ms()`, que
  compensa — antes só `executor.py` compensava e `risco.py` assinava com relógio
  cru.

## Correções da régua de medição (2026-07-23, pré-medição)

Verificação adversarial (workflow de 3 lentes independentes + juiz, antes de
qualquer execução) encontrou 8 achados bloqueantes no `walk_forward.py` —
todos **da ferramenta de medição, não da estratégia** — e todos corrigidos
ANTES da primeira medição (nenhum número existia quando as correções foram
feitas). Viés dominante dos bugs: **para cima** (aprovaria estratégia ruim).

| # | Bug | Correção |
|---|---|---|
| B1 | Filtro MTF usava `idx//4` → candle 4h AINDA ABERTO (look-ahead em ~75% das velas; MTF pesa 20% do score) | Join por timestamp: só o último 4h **fechado** no instante da decisão (`_mapear_idx4_fechado`); flag `--mtf-lookahead-legado` mantida só p/ quantificar o delta do viés |
| B2 | Alinhamento 1h→4h por aritmética de índice sem validar timestamps (gap no banco = resultado artefato) | `_validar_contiguidade` aborta a medição se as séries 1h/4h tiverem gap/duplicata |
| B3 | Critério "DSR > 0" vácuo (PSR ∈ (0,1), nunca ≤0) | Critério corrigido para **DSR ≥ 0.95** (ver nota ¹ da Etapa 1) |
| B4 | Sharpe/Sortino/DSR sobre retornos BRUTOS de taxa e em % de preço | Métricas de risco agora sobre retorno **líquido sobre o capital** (`ret_capital_pct`), consistente com PF/retorno/DD |
| B5 | `TAXA=0.04%` era tarifa de futures; Spot é 0.10% taker / 0.075% maker+BNB | Taxa parametrizável (`--taxa`), default **0.001** (conservador); sensibilidade 0.04%/0.075%/0.10% reportada junto do resultado |
| B6 | Fear&Greed fixo no score máximo (100) — na realidade o período testado teve 152 dias de bloqueio absoluto (F&G≤20 ou >80) ignorados | Histórico REAL do índice (alternative.me, `data/fng_historico.json`, 3.091 dias) via `_score_fear_greed` de produção; carry-forward causal de até 7 dias. Nota: o verificador sugeriu 100→50, mas 100 É o score de produção p/ F&G neutro (zona 35-65) — a correção certa era o histórico real |
| B7 | Benchmark B&H não era computado por ferramenta nenhuma | Computado dentro do `walk_forward.py` sobre o período TESTADO (ver nota ² da Etapa 1) |
| B8 | Pré-registro incompleto (flags CLI livres, sem hash) | Comando congelado na Etapa 1; hash do commit + contagem/intervalo da tabela klines registrados junto do resultado |

Correções adicionais (ressalvas do verificador promovidas a fix): posição
ainda aberta no fim dos dados é **fechada a mercado** (`FIM_DADOS`), não
descartada; gap de até 8 candles entre janelas eliminado (clip por
`JANELA_FUTURA` só nos labels de treino); AUC impresso rotulado como
**in-sample** (diagnóstico, não evidência de generalização).

Cobertura de teste da régua: `tests/test_walk_forward.py` (15 testes —
causalidade do MTF incluindo prova de que o mapeamento legado tinha
look-ahead, contiguidade aborta, PnL exato com taxa dos 2 lados, stop-first
no candle ambíguo, censura final, B&H) — o arquivo tinha ZERO cobertura.

### Ressalvas permanentes desta medição (registrar junto de qualquer resultado)

1. **Sizing**: a medição aposta ~100% (fator 1.0) ou 50% (fator 0.5) do
   capital por trade — não o Kelly fracionado de produção. Retorno/capital
   final/Calmar/maxDD **não transferem** para produção; valem como
   comparação interna da Etapa 1.
2. **Drawdown por trade fechado** (sem mark-to-market intra-trade) —
   subestima o drawdown de equity real em até ~1 distância de stop por
   episódio; não comparável ao critério de 15% da Etapa 2.
3. **Execução idealizada**: fill garantido no fechamento do candle do sinal
   (o bot real é maker-first com risco de não-fill) e stop com 5 bps de
   slippage fixo (caudas reais deslizam mais) — win rate/retorno são limite
   SUPERIOR do real.
4. **Data snooping estrutural**: STOP/TARGET/RSI/score/janelas são
   constantes fixadas com conhecimento do histórico; o walk-forward é
   out-of-sample para o XGBoost, não para a ESTRATÉGIA — mesmo DSR ≥ 0.95
   permanece otimista nesse aspecto.
5. **Skew treino/produção das features**: a "EMA" de `extrair_features` é
   na prática média simples e o `dist_vwap` usa VWAP cumulativo desde o
   candle 0 — causal (sem look-ahead), mas o modelo medido não é réplica
   bit-a-bit do que opera ao vivo.

## Registro de decisões

| Data | Evento | Decisão |
|---|---|---|
| 2026-07-23 | Gate criado | Capital real proibido até cumprir Etapas 1-3 |
| 2026-07-23 | Ablação FSRS da Etapa 1 marcada como resolvida | FSRS removido do repo em 2026-07-21 (nunca ativava no caminho ao vivo); walk-forward padrão já é o cenário sem FSRS |
| 2026-07-23 | Verificação adversarial da régua ANTES da 1ª medição | 8 bloqueantes corrigidos (B1-B8, tabela acima); critério DSR e definição de B&H pré-registrados; medição só roda com a régua corrigida |
| 2026-07-24 | **Etapa 1 executada — ESTRATÉGIA REPROVADA (4 de 5 critérios)** | Retorno −21.25% vs B&H +14.09%; PF 0.86; DSR 0.23; DD 28.9%. Veredito robusto à taxa (reprova até com fee irrealista de 0.04%) e ao bug B1 (reprova com e sem). Capital real segue proibido; Etapas 2-3 não iniciam; próximo passo é redesenho ESTRUTURAL da estratégia (não ajuste de parâmetros), com novo backtest do zero |
