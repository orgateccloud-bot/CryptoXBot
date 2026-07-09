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
| P0-1 | **Purged + Embargoed CV** no treino XGBoost/MLP | `ml_filtro.py`, `backtesting/walk_forward.py` | K-fold padrão vaza futuro em séries temporais → backtest otimista que sangra ao vivo. Conserto nº1 do pipeline de ML. |
| P0-2 | **`LIMIT_MAKER` (post-only)** nas entradas em vez de LIMIT agressivo/MARKET | `executor.py` | Garante fee de maker, nunca cruza o spread. Maior ganho de custo por esforço. Re-quote (cancel-replace) se não preencher em N s. |
| P0-3 | **Vol targeting sobre o Kelly**: `size × (vol_alvo / vol_realizada)` | `risco.py` | Kelly fixo (25%) assume vol estável; BTC/ETH/SOL variam ordens de magnitude entre regimes. Corta drawdown com baixo esforço. |
| P0-4 | **Métricas Sortino/Calmar/Profit Factor/DSR** + registrar nº de trials no backtest | `backtesting/*` | Sharpe pune upside e ignora caudas. DSR corrige Sharpe inflado por multiple-testing (edge fantasma). |

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
