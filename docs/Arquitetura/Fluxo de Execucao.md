---
tags: [arquitetura, fluxo]
atualizado: 2026-07-22
---

# 🔄 Fluxo de Execução (`main.py`)

> Voltar: [[00 - Home]] · Relacionado: [[Visao Geral]] · [[Core e Execucao]]

## Passo a passo

1. **Inicialização** (`main.py:main`) — parse de CLI (`--real`, `--par`,
   `--intervalo`, `--simulacao`, `--relatorio`, `--backtest`, `--treinar-ml`);
   `database.inicializar()`; valida `ALLOW_REAL_TRADING` (a flag `--real`
   também precisa ser passada — o `.env` sozinho não ativa modo real);
   banner de status.
2. **Pré-loop** — `regime.imprimir()`, `fear_greed.imprimir()`,
   `relatorio_completo()` (analise_mercado, mercado Futures/informativo —
   protegido por try/except desde 2026-07-17: uma falha na API de Futures
   não derruba mais o boot inteiro), agenda **retreino semanal** (domingo
   02h) e **relatório diário via Telegram** (18h) em threads daemon.
3. **Boot recovery / reconciliação** (`loop_par`, início) — se havia posição
   persistida no DB, religa o monitor. Com `RECONCILIAR_BOOT_EXCHANGE=true`
   (opt-in, default off), cruza antes com o estado real da Binance
   (saldo/ordens abertas/`myTrades`) para detectar posição órfã (real na
   exchange, sem registro local) ou já fechada fora do bot — ver
   `Executor.reconciliar_boot()`.
4. **Threads** — uma `loop_par(par)` por par de `PARES_ATIVOS`
   (`BTCUSDT, ETHUSDT, SOLUSDT`) + **duas** conexões WebSocket async: `@aggTrade`
   (CVD BTC, backoff exponencial + dedupe por `aggregate trade id`; parser usa
   `data["a"]` — não `data["t"]`) e `@depth20@100ms` (OBI suavizado, P1-1;
   stale só degrada esse componente do score para neutro, não derruba o
   worker). Reconexões incrementam `botbinance_ws_reconexoes` (`/metrics`).
5. **Loop de sinal por par** (a cada N min) — snapshot do CVD/OBI →
   `ensemble.prever()` (XGBoost+MLP) → `estrategias.otimizada.analisar()` →
   `score.calcular()` (10 componentes, incl. OBI 8%) → decisão
   `OPERAR_CHEIO | OPERAR_REDUZIDO | AGUARDAR`. Gauges de observabilidade
   (`regime`, `ml_prob`, `pnl_dia`, `drawdown_dia`, `latencia_decisao_ms`)
   atualizados a cada ciclo.
6. **Execução** — só para `COMPRA`: `risco.validar_trade()` (Kelly, drawdown,
   volatilidade, exposição de portfólio, **CVaR de cauda regime-dependente**
   — P2-3, inerte por default) → `ScaleIn` (3 parcelas) →
   `executor.abrir_long()` (entrada maker-first via `LIMIT_MAKER`, com
   fallback taker). Sinal `VENDA` é **ignorado explicitamente** (short não
   implementado).
7. **Proteção pós-entrada** — `STOP_LOSS_LIMIT` puro na exchange por padrão
   (sobrevive a crash do bot); com `OCO_BRACKET=true` (opt-in), stop+alvo
   final viram um par atômico one-cancels-the-other (`orderList/oco`) — o
   alvo também passa a sobreviver a crash, não só o stop.
8. **Monitor** (thread por posição em `executor._monitorar`, a cada 10s) —
   stop loss, take-profit parcial (50%), trailing stop (após +1%,
   server-side se `OCO_TRAILING_DELTA_BIPS>0`), take-profit final. Mutações
   de `self.posicao` protegidas por `RLock` (antes: só a troca do ponteiro
   estava protegida); janela entre o fill e o registro no DB reduzida via
   retry+escalonamento (bot_event CRITICAL + Telegram se persistir a falha).
9. **Persistência contínua** — trades, snapshots, CVD e eventos em
   `database`; alertas via `telegram_bot` (7 tipos conectados desde P2-5:
   sinal, trade aberto/fechado, stop, trailing, circuit breaker, relatório
   diário); `/health` (sempre 200), `/ready` (watchdog do WS: `degraded` se
   sem mensagem >120s) e `/metrics` (Prometheus, contadores+gauges reais)
   para o serviço (NSSM/systemd).

## Pontos de atenção do fluxo
- `loop_par` usa `time.sleep(intervalo*60)` síncrono — um par lento atrasa só a
  si mesmo (threads independentes), mas não há cancelamento cooperativo.
- O snapshot de CVD/OBI usa `_lock` global; `_estado_pares` (scale-in) é
  compartilhado entre threads — ver riscos em [[Core e Execucao]].
- Indicadores de klines (EMA/RSI/ATR/etc.) passam por `data/klines.py`, um
  cache TTL compartilhado — consolidou 6 fetchers duplicados (P1-5).
- Retreino: `ml_filtro` (XGBoost) por par + `lstm_modelo` (MLP) para BTC,
  com guard-rail de drift (compara AUC contra a média histórica antes de
  promover o modelo novo).

Ver detalhes módulo a módulo em [[Core e Execucao]] e [[ML e Sinais]].
