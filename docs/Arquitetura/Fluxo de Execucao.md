---
tags: [arquitetura, fluxo]
---

# 🔄 Fluxo de Execução (`main.py`)

> Voltar: [[00 - Home]] · Relacionado: [[Visao Geral]] · [[Core e Execucao]]

## Passo a passo

1. **Inicialização** (`main.py:main`) — parse de CLI (`--real`, `--par`,
   `--intervalo`, `--simulacao`, `--relatorio`, `--backtest`, `--treinar-ml`);
   `database.inicializar()`; valida `ALLOW_REAL_TRADING`; banner de status.
2. **Pré-loop** — `regime.imprimir()`, `fear_greed.imprimir()`,
   `relatorio_completo()` (analise_mercado), agenda **retreino semanal**
   (domingo 02h) em thread daemon.
3. **Threads** — uma `loop_par(par)` por par de `PARES_ATIVOS`
   (`BTCUSDT, ETHUSDT, SOLUSDT`) + `websocket_handler()` async (CVD BTC, com
   backoff exponencial + dedupe por `trade_id`).
4. **Loop de sinal por par** (a cada N min) — snapshot do CVD →
   `ensemble.prever()` → `estrategias.otimizada.analisar()` → `score.calcular()`
   → decisão `OPERAR_CHEIO | OPERAR_REDUZIDO | AGUARDAR`.
5. **Execução** — só para `COMPRA`: `risco.validar_trade()` (Kelly, drawdown,
   volatilidade) → `ScaleIn` (3 parcelas) → `executor.abrir_long()`.
   Sinal `VENDA` é **ignorado explicitamente** (short não implementado).
6. **Monitor** (thread por posição em `executor._monitorar`, a cada 10s) — stop
   loss, take-profit parcial (50%), trailing stop (após +1%), take-profit final.
7. **Persistência contínua** — trades, snapshots, CVD e eventos em `database`;
   alertas via `telegram_bot`; `/health` para probes do Railway.

## Pontos de atenção do fluxo
- `loop_par` usa `time.sleep(intervalo*60)` síncrono — um par lento atrasa só a
  si mesmo (threads independentes), mas não há cancelamento cooperativo.
- O snapshot de CVD usa `_lock` global; `_estado_pares` (scale-in) é compartilhado
  entre threads — ver riscos em [[Core e Execucao]].
- Retreino: `ml_filtro` (XGBoost) por par + `lstm_modelo` (MLP) para BTC.

Ver detalhes módulo a módulo em [[Core e Execucao]] e [[ML e Sinais]].
