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
- **Riscos:** `PARES_ATIVOS` hardcoded (`main.py:66`); `_estado_pares`/scale-in compartilhados entre threads sem lock; sem heartbeat de timeout do WS (conexão zumbi possível).
- ✅ Já corrigido nesta sessão: VENDA ignorada explicitamente (não consome mais validação/scale-in/Telegram).

## `executor.py` — Execução LONG 🟢 Alta
- **Propósito:** ciclo de vida de 1 posição LONG: entrada LIMIT, trailing stop, take-profit parcial (50%).
- **API:** `Executor(simulacao, symbol)`, `abrir_long()`, `fechar_posicao()`, `get_preco()`, `status()`.
- **Riscos:** fecha sempre a MARKET sem proteção de slippage (`executor.py:181`); `preco*1.001` hardcoded para o LIMIT; trailing dorme 10s.
- ✅ Corrigido nesta sessão: `threading.Lock` no estado da posição; validação da resposta da Binance; `fechar_posicao` não marca fechado se a ordem real não preencher.

## `risco.py` — Gestão de Risco 🟢 Alta
- **Propósito:** Kelly fracionado (0.25), drawdown diário (5%) / total (15%), circuit breaker, tamanho de posição.
- **API:** `kelly()`, `kelly_do_banco()`, `calcular_tamanho()`, `validar_trade()`, `verificar_volatilidade()`, `get_saldo_usdt/btc()`, `status()`.
- **Riscos:** `capital_inicio_dia` frágil se DB vazio em rally/crash overnight (`risco.py:249`); saldo sem cache (2 HTTP por validação); volatilidade compara `abertura[T-1h]` vs `fechamento[T]` (não a mesma 1h).
- **Força:** estado persistido em DB (sobrevive restart do Railway).

## `suporte.py` — Suportes + ScaleIn 🟡 Média
- **Propósito:** detecção de suportes (Pivot, Bollinger, VWAP, EMAs, Volume Profile) + clustering + entrada em 3 parcelas (40/40/20).
- **API:** `detectar_suportes()`, `class ScaleIn` (`entrada_parcela1/2/3`, `preco_medio`, `completo`).
- **Riscos:** ScaleIn **sem persistência** (perde estado em restart); Pivot Points trivial (min/max em janela 5); tolerâncias fixas (0.3-0.5%) não adaptam ao preço.

## `indicadores.py` — Biblioteca técnica 🔴 Baixa
- **Propósito:** EMA, SMA, RSI, MACD, ATR, Bollinger, VWAP, volume, MTF.
- **Riscos:** **duplicação massiva** — ATR, Bollinger e VWAP têm 2-3 implementações (numpy vs loop) que podem divergir; sem tratamento de NaN; sem type hints no legado.
- **Ação:** refatoração é pré-requisito para confiança nos cálculos → ver [[Planejamento de Melhorias]].

## `analise_mercado.py` — Scraper de mercado 🟡 Média
- **Propósito:** preço, order book, funding, open interest, EMA/RSI públicos (sem API key).
- **Riscos:** múltiplas chamadas HTTP repetidas sem cache; `relatorio_completo()` só imprime (não retorna dict para logging).

## `estrategias/otimizada.py` — Estratégia principal 🟡 Média
- **Propósito:** 8 filtros (EMA, MTF 4H, ATR, volume, Bollinger, VWAP, regime, F&G) + ensemble ML → score → decisão.
- **Deps:** `indicadores`, `regime`, `fear_greed`, `suporte`, `ensemble`, `score`, `config.params_pares`.
- **Riscos:** lógica dupla (filtros binários + score) coexistindo; thresholds hardcoded (`ATR_MIN_RATIO=0.6`, `VOL_MIN_RATIO=1.3`); fallback de ensemble trivial (prob=0.5).

## `estrategias/ema_rsi_cvd.py` — Baseline 🟡 Média
- **Propósito:** estratégia simples auditável (EMA cross + RSI + CVD + funding).
- **Riscos:** duplica EMA/RSI em vez de usar `indicadores`; thresholds de funding muito apertados para condições normais; RSI range diverge de `params_pares`.

---

### Resumo de maturidade
| Módulo | Nota |
|---|---|
| executor.py | 🟢 Alta |
| risco.py | 🟢 Alta |
| main.py | 🟡 Média |
| suporte.py | 🟡 Média |
| analise_mercado.py | 🟡 Média |
| estrategias/otimizada.py | 🟡 Média |
| estrategias/ema_rsi_cvd.py | 🟡 Média |
| indicadores.py | 🔴 Baixa (duplicação) |
