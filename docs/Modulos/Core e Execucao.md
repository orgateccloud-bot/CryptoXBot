---
tags: [modulo, core, execucao, risco]
atualizado: 2026-07-22
---

# ⚙️ Core e Execução

> Voltar: [[00 - Home]] · Relacionado: [[ML e Sinais]] · [[Fluxo de Execucao]]

Núcleo de orquestração, execução de ordens e gestão de risco.

---

## `main.py` — Orquestrador 🟡 Média
- **Propósito:** WebSocket CVD + OBI + loop de estratégia por par + retreino
  semanal + relatório diário.
- **API:** `main()`, `websocket_handler()`, `process_message()`, `loop_par()`,
  `iniciar_retreinamento_automatico()`, `iniciar_relatorio_diario()` (P2-5).
- **Deps:** `database`, `health`, `risco`, `suporte.ScaleIn`, `executor`,
  `estrategias.otimizada`, `regime`, `fear_greed`, `ensemble`, `telegram_bot`.
- **Riscos:** `PARES_ATIVOS` hardcoded; `_estado_pares`/scale-in compartilhados entre threads sem lock.
- ✅ Já corrigido: VENDA ignorada explicitamente (não consome mais validação/scale-in/Telegram); **watchdog do WS** (`/ready` acusa `degraded` se o `@aggTrade` fica >120s sem mensagem — pega conexão zumbi/CVD congelado); **shutdown gracioso multi-sinal** (SIGTERM/SIGINT/SIGBREAK); **boot crash-loop** (2026-07-17) — `relatorio_completo()` (Futures, informativo) agora protegido por try/except; uma falha na API de Futures não derruba mais o boot inteiro (NSSM reiniciaria em loop).
- ✅ **P2-5 (2026-07-22):** gauges (`regime`, `ml_prob`, `pnl_dia`, `drawdown_dia`, `latência de decisão`) atualizados a cada ciclo via `risco.status_leve()` (sem chamada de rede extra); `increment_metric("ws_reconexoes")` nos dois streams; nova thread `iniciar_relatorio_diario` (18h) liga `telegram_bot.relatorio_diario()`, que nunca tinha agendamento.

## `executor.py` — Execução LONG 🟢 Alta
- **Propósito:** ciclo de vida de 1 posição LONG: entrada maker-first, trailing stop, take-profit parcial (50%), proteção via stop puro ou bracket OCO.
- **API:** `Executor(simulacao, symbol)`, `abrir_long()`, `fechar_posicao()`, `get_preco()`, `status()`, `reidratar_posicao()`, `reconciliar_boot()` (P2-5), `avaliar_tick_monitor()` (decisão pura do trailing).
- ✅ **Hardening de execução (modo real):**
  - **Stop loss NA exchange** — `STOP_LOSS_LIMIT` colocado na Binance após o fill
    (sobrevive a crash do bot); trailing/breakeven via cancel-then-replace com
    restauração do nível antigo se o novo falhar.
  - **Bracket OCO nativo** (opt-in, `OCO_BRACKET`) — stop + alvo final num par
    atômico one-cancels-the-other; o alvo também sobrevive a crash (antes só
    o stop vivia na exchange). Trailing server-side opcional
    (`OCO_TRAILING_DELTA_BIPS`). Fallback garantido para o stop puro se o OCO
    falhar.
  - **Entrada maker-first** — `LIMIT_MAKER` no melhor bid (sempre fee de
    maker, sem cruzar o spread); re-quota até `MAKER_MAX_REQUOTES`, cai para
    LIMIT taker se `MAKER_FIRST=false`.
  - **Crash recovery** — posição persistida no DB (`salvar_posicao_aberta`,
    com retry+escalonamento se a persistência falhar — bot_event CRITICAL +
    Telegram); no boot, `loop_par` recupera e religa o monitor.
  - **API robusta** — `recvWindow=5000` + sync de relógio (offset serverTime,
    corrige -1021) + retry/backoff (429/-1003/5xx) + `newClientOrderId`
    idempotente + consulta pós-timeout (elimina ordem fantasma).
  - **Precisão via `exchangeInfo` dinâmico** (tickSize/stepSize por símbolo).
- ✅ **Locking correto (2026-07-22)** — `RLock` (não `Lock`, porque
  `fechar_posicao`/`_aplicar_novo_stop` podem ser chamados de dentro do
  próprio `_monitorar`, que já segura o lock); todas as mutações de campo de
  `self.posicao` agora sob lock (antes só a troca do ponteiro estava
  protegida); `status()` tira um snapshot único antes de ler qualquer campo
  (corrige um TOCTOU real: um fechamento concorrente entre a checagem e a
  leitura estourava `TypeError`). Regra de escopo: o lock nunca envolve uma
  chamada de rede.
- ✅ **Reconciliação de boot** (opt-in, `RECONCILIAR_BOOT_EXCHANGE`, default
  off) — `reconciliar_boot()` cruza o DB com o estado real da Binance
  (saldo/ordens abertas/`myTrades`) antes de religar o monitor: detecta
  posição órfã (real na exchange, sem registro local — reconstrói via
  `myTrades`) ou já fechada fora do bot (remove do DB sem religar). Estado
  ambíguo sempre prefere alertar + cair no comportamento legado a decidir
  sozinho vender ou recriar proteção.
- ✅ **Trailing stop testado (82%+ cobertura):** decisão por-tick extraída para a função pura `avaliar_tick_monitor`; equivalência ao loop original provada por oráculo (8.160 casos); nova suíte `TestExecutorConcorrencia` (threads reais, TOCTOU, rede fora do lock) + suíte dedicada de reconciliação de boot.

## `risco.py` — Gestão de Risco 🟢 Alta
- **Propósito:** Kelly fracionado (0.25), drawdown diário (5%) / total (15%),
  circuit breaker de volatilidade, exposição agregada de portfólio, **CVaR de
  cauda regime-dependente**, tamanho de posição.
- **API:** `kelly()`, `kelly_do_banco()`, `calcular_tamanho()`,
  `validar_trade()`, `verificar_volatilidade()`, `get_saldo_usdt/btc()`,
  `status()`, `status_leve()` (P2-5, sem rede), `cvar_excede_limite()` (P2-3).
- **Riscos:** `capital_inicio_dia` frágil se DB vazio em rally/crash overnight (`risco.py:249`); saldo sem cache (2 HTTP por validação); volatilidade compara `abertura[T-1h]` vs `fechamento[T]` (não a mesma 1h).
- **Força:** estado persistido em DB (sobrevive restart do serviço NSSM/systemd).
- ✅ **CVaR de cauda regime-dependente (P2-3, 2026-07-22)** — novo gate 6.6
  em `validar_trade()` (kwarg `regime=None`, inerte por default, mesmo
  padrão do check 6.5 de exposição agregada). Circuit breaker existente é
  limite de drawdown, não de cauda; cripto tem caudas gordas. Fonte:
  `database.sinais_executados()` (mesma de `kelly_do_banco()`); histórico
  <10 trades fechados = gate inerte.
- ✅ **Observabilidade conectada (P2-5)** — bloqueios de drawdown e ativações
  de circuit breaker agora incrementam métricas (`/metrics`) e disparam
  `bot_event` + alerta Telegram; o circuit breaker de volatilidade ganhou
  debounce (`_estado_risco["circuit_breaker_ativo"]`) para não re-alertar a
  cada chamada durante um episódio sustentado.

## `suporte.py` — Suportes + ScaleIn 🟡 Média
- **Propósito:** detecção de suportes (Pivot, Bollinger, VWAP, EMAs, Volume Profile) + clustering + entrada em 3 parcelas (40/40/20).
- **API:** `detectar_suportes()`, `class ScaleIn` (`entrada_parcela1/2/3`, `preco_medio`, `completo`).
- **Riscos:** ScaleIn **sem persistência** (perde estado em restart); Pivot Points trivial (min/max em janela 5); tolerâncias fixas (0.3-0.5%) não adaptam ao preço.

## `indicadores.py` — Biblioteca técnica 🟢 Alta
- **Propósito:** EMA, SMA, RSI, MACD, ATR, Bollinger, VWAP, volume, MTF.
- ✅ **Corrigido nesta sessão (showstopper):** removidas as duplicatas numpy mortas (eram sombreadas); `import math` adicionado (bollinger dava NameError); `volume_relativo` corrigido (dava IndexError sempre). Esses dois bugs quebravam `otimizada.analisar()` a cada ciclo.
- **Cobertura: 100%** (`test_indicadores` + adversarial). Foi o pré-requisito para a estratégia funcionar.

## `analise_mercado.py` — Scraper de mercado 🟡 Média
- **Propósito:** preço, order book, funding, open interest, EMA/RSI públicos (sem API key), mercado **Futures** (informativo — não é o mercado de execução).
- **Riscos:** múltiplas chamadas HTTP repetidas sem cache; `relatorio_completo()` só imprime (não retorna dict para logging).
- ✅ **Não é código órfão** (correção de uma afirmação anterior deste vault):
  `main.py:40` importa `relatorio_completo`, `main.py` chama no boot. Até
  2026-07-17 essa chamada não tinha try/except — uma falha na API de
  Futures (rate limit/geo-block/instabilidade) derrubava o boot inteiro
  mesmo com o Spot saudável; como o NSSM reinicia automaticamente, virava
  crash-loop. Corrigido (mesmo padrão de `reg.imprimir()`/`fg.imprimir()`
  logo abaixo).

## `estrategias/otimizada.py` — Estratégia principal 🟢 Alta
- **Propósito:** 8 filtros (EMA, MTF 4H, ATR, volume, Bollinger, VWAP, regime, F&G) + ensemble ML → score → decisão.
- **Deps:** `indicadores`, `regime`, `fear_greed`, `suporte`, `ensemble`, `score`, `config.params_pares`.
- ✅ **Voltou a funcionar nesta sessão:** quebrava a cada ciclo via `indicadores` (volume_relativo/bollinger). Agora coberta **93%** por `test_otimizada_e2e` (regressão ponta-a-ponta).
- **Riscos remanescentes:** lógica dupla (filtros binários + score); thresholds hardcoded (`ATR_MIN_RATIO=0.6`, `VOL_MIN_RATIO=1.3`); fallback de ensemble trivial (prob=0.5).

> A antiga baseline `estrategias/ema_rsi_cvd.py` foi **aposentada e removida do
> repo** (órfã; o histórico git preserva). A estratégia ativa é `otimizada.py`.

---

### Resumo de maturidade
| Módulo | Nota |
|---|---|
| indicadores.py | 🟢 Alta (100% cobertura) |
| executor.py | 🟢 Alta (stop na exchange/OCO + locking correto + reconciliação de boot + crash recovery + API robusta) |
| risco.py | 🟢 Alta (+ CVaR de cauda regime-dependente) |
| estrategias/otimizada.py | 🟢 Alta (93%, regressão E2E) |
| main.py | 🟡 Média |
| suporte.py | 🟡 Média |
| analise_mercado.py | 🟡 Média |
