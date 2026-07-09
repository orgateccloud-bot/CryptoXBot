---
tags: [modulo, core, execucao, risco]
---

# ⚙️ Core e Execução

> Voltar: [[00 - Home]] · Relacionado: [[ML e Sinais]] · [[Fluxo de Execucao]]

Núcleo de orquestração, execução de ordens e gestão de risco.

---

## `main.py` — Orquestrador 🟡 Média
- **Propósito:** WebSocket CVD + loop de estratégia por par + retreino semanal.
- **API:** `main()`, `websocket_handler()`, `process_message()`, `loop_par()`, `iniciar_retreinamento_automatico()`.
- **Deps:** `database`, `risco`, `suporte.ScaleIn`, `executor`, `estrategias.otimizada`, `regime`, `fear_greed`, `ensemble`, `telegram_bot`.
- **Riscos:** `PARES_ATIVOS` hardcoded; `_estado_pares`/scale-in compartilhados entre threads sem lock.
- ✅ Já corrigido: VENDA ignorada explicitamente (não consome mais validação/scale-in/Telegram); **watchdog do WS** (`/ready` acusa `degraded` se o `@aggTrade` fica >120s sem mensagem — pega conexão zumbi/CVD congelado); **shutdown gracioso multi-sinal** (SIGTERM/SIGINT/SIGBREAK).

## `executor.py` — Execução LONG 🟢 Alta
- **Propósito:** ciclo de vida de 1 posição LONG: entrada LIMIT, trailing stop, take-profit parcial (50%).
- **API:** `Executor(simulacao, symbol)`, `abrir_long()`, `fechar_posicao()`, `get_preco()`, `status()`, `avaliar_tick_monitor()` (decisão pura do trailing).
- ✅ **Hardening de execução (modo real):**
  - **Stop loss NA exchange** — `STOP_LOSS_LIMIT` colocado na Binance após o fill
    (sobrevive a crash do bot); trailing/breakeven via cancel-then-replace com
    restauração do nível antigo se o novo falhar.
  - **Crash recovery** — posição persistida no DB (`salvar_posicao_aberta`); no
    boot, `loop_par` recupera e religa o monitor (sem posição órfã pós-restart).
  - **API robusta** — `recvWindow=5000` + sync de relógio (offset serverTime,
    corrige -1021) + retry/backoff (429/-1003/5xx) + `newClientOrderId`
    idempotente + consulta pós-timeout (elimina ordem fantasma).
  - **Precisão via `exchangeInfo` dinâmico** (tickSize/stepSize por símbolo).
- ✅ Anteriormente: `threading.Lock` no estado da posição; validação da resposta
  da Binance; `fechar_posicao` não marca fechado se a ordem real não preencher.
- ✅ **Trailing stop testado (82% cobertura):** decisão por-tick extraída para a função pura `avaliar_tick_monitor`; equivalência ao loop original provada por oráculo (8.160 casos).

## `risco.py` — Gestão de Risco 🟢 Alta
- **Propósito:** Kelly fracionado (0.25), drawdown diário (5%) / total (15%), circuit breaker, tamanho de posição.
- **API:** `kelly()`, `kelly_do_banco()`, `calcular_tamanho()`, `validar_trade()`, `verificar_volatilidade()`, `get_saldo_usdt/btc()`, `status()`.
- **Riscos:** `capital_inicio_dia` frágil se DB vazio em rally/crash overnight (`risco.py:249`); saldo sem cache (2 HTTP por validação); volatilidade compara `abertura[T-1h]` vs `fechamento[T]` (não a mesma 1h).
- **Força:** estado persistido em DB (sobrevive restart do serviço NSSM/systemd).

## `suporte.py` — Suportes + ScaleIn 🟡 Média
- **Propósito:** detecção de suportes (Pivot, Bollinger, VWAP, EMAs, Volume Profile) + clustering + entrada em 3 parcelas (40/40/20).
- **API:** `detectar_suportes()`, `class ScaleIn` (`entrada_parcela1/2/3`, `preco_medio`, `completo`).
- **Riscos:** ScaleIn **sem persistência** (perde estado em restart); Pivot Points trivial (min/max em janela 5); tolerâncias fixas (0.3-0.5%) não adaptam ao preço.

## `indicadores.py` — Biblioteca técnica 🟢 Alta
- **Propósito:** EMA, SMA, RSI, MACD, ATR, Bollinger, VWAP, volume, MTF.
- ✅ **Corrigido nesta sessão (showstopper):** removidas as duplicatas numpy mortas (eram sombreadas); `import math` adicionado (bollinger dava NameError); `volume_relativo` corrigido (dava IndexError sempre). Esses dois bugs quebravam `otimizada.analisar()` a cada ciclo.
- **Cobertura: 100%** (`test_indicadores` + adversarial). Foi o pré-requisito para a estratégia funcionar.

## `analise_mercado.py` — Scraper de mercado 🟡 Média
- **Propósito:** preço, order book, funding, open interest, EMA/RSI públicos (sem API key).
- **Riscos:** múltiplas chamadas HTTP repetidas sem cache; `relatorio_completo()` só imprime (não retorna dict para logging).

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
| executor.py | 🟢 Alta (stop na exchange + crash recovery + API robusta) |
| risco.py | 🟢 Alta |
| estrategias/otimizada.py | 🟢 Alta (93%, regressão E2E) |
| main.py | 🟡 Média |
| suporte.py | 🟡 Média |
| analise_mercado.py | 🟡 Média |
