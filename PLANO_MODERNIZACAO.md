# Plano de Modernização — CryptoXbot (BinanceXBot)

> Gerado 2026-07-09 a partir de: mapeamento estrutural + auditoria de docs +
> pesquisa de mercado (2024–2026). Estado base: bot spot, ensemble XGBoost+MLP,
> 723 testes verdes, serviço 24/7 (NSSM) em simulação. Fontes de mercado citadas
> ao final. Prioridade por ROI/esforço: **P0 = alto impacto, baixo esforço**.

## Diagnóstico em uma frase

As fundações estão certas (CVD, stop na exchange, crash-recovery, regime, paper
24/7). As lacunas de estado-da-arte são três: **validação de ML** (risco de edge
fantasma), **execução maker-first** (custo direto), e **sizing por volatilidade**.

---

## P0 — Alto impacto, baixo esforço (fazer primeiro)

| # | Ação | Onde | Por quê |
|---|------|------|---------|
| ✅ P0-1 | ~~**Purged + Embargoed CV** no treino XGBoost/MLP~~ **FEITO** (2026-07-09) | `validacao.py` (novo) usado em `ml_filtro.py` + `lstm_modelo.py` | K-fold padrão vaza futuro em séries temporais → backtest otimista que sangra ao vivo. Agora treino/holdout são purgados (gap = JANELA) e o AUC honesto vem de purged CV (mean±std, salvo no pickle). `backtesting/walk_forward.py` pode adotar o mesmo módulo (follow-up). |
| ✅ P0-2 | ~~**`LIMIT_MAKER` (post-only)** nas entradas~~ **FEITO** (2026-07-09) | `executor.py` (`_entrar_maker`) + `MAKER_*` no `.env` | Garante fee de maker, nunca cruza o spread. Entrada no melhor bid, re-quote se não preencher em MAKER_TIMEOUT_S, fills parciais acumulados. Fallback p/ LIMIT cruzando via `MAKER_FIRST=false`. |
| ✅ P0-3 | ~~**Vol targeting sobre o Kelly**: `size × (vol_alvo / vol_realizada)`~~ **FEITO** (2026-07-10) | `risco.py` (`fator_volatilidade`, `calcular_tamanho`, `validar_trade`) | Kelly fixo (25%) assume vol estável; BTC/ETH/SOL variam ordens de magnitude entre regimes. Multiplicador `atr_media/atr_atual` clampado em [0.5,1.5]. Achado da revisão adversarial: o teto pré-existente de 20% do capital dominava o sizing (stop de 1.5% + risco de 2% já implica notional >100%), tornando o Kelly/vol targeting cosmético em produção — corrigido escalando o próprio teto por `fator_volatilidade` (bypass preservado quando `fator_risco` é explícito). |
| ✅ P0-4 | ~~**Métricas Sortino/Calmar/Profit Factor/DSR** + registrar nº de trials no backtest~~ **FEITO** (2026-07-11) | `backtesting/metricas.py` (novo, ponto único de verdade para os 5 engines) | Sharpe pune upside e ignora caudas. DSR (Bailey & López de Prado) corrige Sharpe inflado por multiple-testing (edge fantasma) — `otimizador.py` agora reporta `n_trials` e o DSR do melhor resultado do grid search, desanualizando os Sharpes antes de aplicar a fórmula (bug de escala pego na especificação). Revisão adversarial encontrou e corrigiu um erro real na fórmula do deflator (`sharpe_esperado_max` omitia o termo SR̄/média dos trials — sem ele o DSR do melhor resultado do grid ficava artificialmente otimista, ex. 0.67 em vez do 0.07 correto) e um crash em `calmar_ratio` com perda total (`(1+retorno/100)` negativo virava número complexo no `round()`). |

## P1 — Médio impacto/esforço

| # | Ação | Onde | Por quê |
|---|------|------|---------|
| P1-1 | **Order Book Imbalance (OBI)** como feature (via `@depth`), confirmando o CVD | novo `data/` + `score.py`/`ensemble.py` | Feature ortogonal ao CVD (passivo vs agressivo); alinhamento OBI+CVD = alta convicção. Suavizar (média de N) contra spoofing. |
| P1-2 | **Risco de portfólio**: cap de exposição agregada + ajuste por correlação BTC/ETH/SOL | `risco.py` | 3 pares altamente correlacionados ≈ 1 aposta alavancada num crash. "1 posição por par" não protege o agregado. |
| P1-3 | **Meta-labeling (triple-barrier)** sobre o score/ensemble atual | `ensemble.py` + novo classificador | Rotula por barreira tocada (TP/SL/timeout) alinhado ao PnL; meta-modelo decide *operar/quanto*. Desacopla direção de tamanho. |
| P1-4 | **MLflow** para versionar XGBoost+MLP + alertar drift | infra ML | Protege o retreino automático de domingo de degradar performance silenciosamente (hoje sem alarme de drift). |
| P1-5 | **Consolidar duplicação de código** (ver Débito Técnico) | vários | `_klines` em 4 lugares; EMA/RSI reimplementados em `dashboard.py`/`analise_mercado.py`/`backtesting`. Drift de cálculo entre módulos. |

## P2 — Médio/alto esforço

| # | Ação | Por quê |
|---|------|---------|
| P2-1 | **OCO nativo** (`orderList/oco`) + `trailingDelta` server-side | Substitui o cancel-then-replace manual do trailing por bracket atômico na exchange. (Endpoint legado `order/oco` está deprecado.) |
| P2-2 | **VectorBT** p/ pesquisa (grid de params) + **NautilusTrader** como validador de execução | Split research (vetorizado, rápido) × execução (event-driven, realista). Reduz o gap backtest≠live — o maior risco do backtest atual. |
| P2-3 | **CVaR / Expected Shortfall** como gate de risco | Circuit breaker atual é limite de drawdown, não de cauda; cripto tem caudas gordas. |

## P3 — Estrutural (planejar com @Alfa/Plan Mode)

| # | Ação | Por quê |
|---|------|---------|
| P3-1 | **Núcleo event-driven (asyncio)** substituindo o polling `--intervalo 15` | Reage a market-data/fills em vez de ciclos fixos; reduz latência de decisão. Refator grande. |
| P3-2 | **Fractional differentiation** nas features + **HMM probabilístico** no `regime.py` | Features estacionárias com memória; regime como probabilidade/gate, não flag binário. |

---

## Débito técnico (do mapeamento) — limpeza/dedup

- **`_klines` duplicado em 4 lugares** (`dashboard.py:189`, `regime.py:36`, `suporte.py:42`, `estrategias/otimizada.py:43`) — cada um refaz fetch, sem cache, divergentes. Centralizar em `data/klines.py` com cache TTL.
- **EMA/RSI reimplementados** em `dashboard.py`, `analise_mercado.py`, `backtesting/motor.py` — drift de cálculo vs `indicadores.py` (a versão numpy canônica). Remover as cópias.
- **`docs/` vault** (snapshot 2026-06-20): sendo atualizado para o estado atual
  (Railway/Docker/Futures → NSSM/systemd/Spot).
- **`_legado/`**: removido do repo em 2026-07-09 (recuperável via histórico git).

---

## Fontes de mercado (verificadas)

- Execução: Binance Spot API docs (LIMIT_MAKER, orderList/oco, iceberg, STP).
- Microestrutura (OBI/CVD): quantstrategy.io, questdb, phemex, bookmap.
- Risco (vol targeting/CVaR): ResearchAffiliates, GARP digital-asset-risk, Risks (MDPI).
- ML (purged CV, meta-labeling, DSR, fracdiff): López de Prado *AFML* / *10 Reasons ML Funds Fail* (GARP), Bailey & López de Prado (Deflated Sharpe), Wikipedia purged-CV, quantreo, hudsonthames.
- Infra/backtest: NautilusTrader docs, VectorBT, pyquantnews, susanpotter backtest-bias.

> Detalhe completo com URLs por item: ver o relatório de pesquisa de mercado da
> sessão de 2026-07-09.
