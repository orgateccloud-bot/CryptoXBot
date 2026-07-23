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

```bash
python backtesting/coletar_dados.py
python backtesting/walk_forward.py
```

| Critério | Mínimo para aprovar | Resultado | Passou? |
|---|---|---|---|
| Deflated Sharpe Ratio (DSR) | > 0 | — | ☐ |
| Profit factor (após TAXA+SLIPPAGE do motor) | > 1.3 | — | ☐ |
| Nº de trades no teste (todas as janelas) | ≥ 100 | — | ☐ |
| Retorno vs buy-and-hold BTC no mesmo período | ≥ B&H ajustado a risco | — | ☐ |
| Max drawdown | ≤ 20% | — | ☐ |

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

## Registro de decisões

| Data | Evento | Decisão |
|---|---|---|
| 2026-07-23 | Gate criado | Capital real proibido até cumprir Etapas 1-3 |
| 2026-07-23 | Ablação FSRS da Etapa 1 marcada como resolvida | FSRS removido do repo em 2026-07-21 (nunca ativava no caminho ao vivo); walk-forward padrão já é o cenário sem FSRS |
