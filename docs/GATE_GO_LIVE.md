# GATE DE GO-LIVE — Critérios Pré-Registrados

> **Natureza deste documento:** contrato de decisão, escrito ANTES de existir
> qualquer resultado. Alterar estes critérios depois de ver os números anula o
> propósito do gate. Qualquer mudança exige justificativa escrita aqui, datada,
> e reinicia o relógio de validação.
>
> Criado em: 2026-07-23 · Status: **NENHUMA ETAPA CUMPRIDA**

## Estado atual (honesto)

- Engenharia: pronta (o scorecard do vault cobre infra, não estratégia).
- Estratégia: **nunca validada**. Sem backtest consolidado, sem paper trading
  com números. Capital real: **proibido** até completar as 3 etapas abaixo.

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
| DSR (PSR, 1 único backtest — probabilidade) | ≥ 0.95 ¹ | — | ☐ |
| Profit factor (após TAXA+SLIPPAGE do motor) | > 1.3 | — | ☐ |
| Nº de trades no teste (todas as janelas) | ≥ 100 | — | ☐ |
| Retorno vs buy-and-hold BTC no mesmo período | ≥ B&H ajustado a risco ² | — | ☐ |
| Max drawdown | ≤ 20% | — | ☐ |

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

## Etapa 3 — Capital Real Piloto (custo: 30 dias)

Só inicia se a Etapa 2 aprovar.

- Capital piloto: valor cuja perda TOTAL não incomoda (ordem de "um jantar
  caro", não de "um salário"). Definir valor por escrito aqui antes de ligar: R$ ____
- API: permissão SPOT apenas (leitura + trading spot), saque DESABILITADO,
  IP whitelist do servidor. Conferir contra `config/api_key_LEIA_ANTES_DE_PREENCHER.txt` (versão corrigida).
- Rodar 30 dias comparando execução real vs simulada (slippage e fills).
- Aprovação final: divergência real-vs-paper pequena E métricas da Etapa 2
  sustentadas → escalar gradualmente. Divergência grande → voltar à Etapa 2.

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
