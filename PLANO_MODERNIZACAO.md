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
| ✅ P1-1 | ~~**Order Book Imbalance (OBI)** como feature (via `@depth`), confirmando o CVD~~ **FEITO** (2026-07-12), escopo mínimo (BTCUSDT + `score.py`, sem ML) | `main.py` (WS `@depth20@100ms`), `score.py` (`_score_obi`, PESOS rebalanceado) | Investigação achou 2 problemas na premissa: (1) o componente CVD do score (15% do peso) já era código morto em produção — `historico_ticks` nunca chegava ao caminho ao vivo, e a própria integração `_score_cvd`→`calculate_cvd` tinha um bug real de contrato (`preco`/`quantidade` em português vs `price`/`quantity` esperado em inglês, confirmado via `KeyError` rodando o código — `backtesting/motor.py` já crashava nisso); (2) implementar OBI via diff-depth stream exigiria o protocolo de sincronização snapshot+`U`/`u`/`pu` da Binance, sem nenhum precedente no repo — resolvido usando o **Partial Book Depth Stream** (`@depth20@100ms`, top-N completo a cada mensagem, sem estado de continuidade). Ambos corrigidos: `_score_cvd` ganhou tradução de contrato; `main.py` passou a manter um buffer rolante de ticks brutos (`obter_historico_ticks_btc`) e uma 2ª conexão WS independente para `@depth` com suavização (média móvel de 30 msgs) e degradação para neutro se stale. Novo componente `obi` (8% do peso) em `score.py`, `cvd` reduzido de 15%→7% (soma de "fluxo" preservada em 15%). Escopo BTCUSDT-only e sem mudança em `ensemble.py`/ML por decisão explícita (ver AskUserQuestion) — ETHUSDT/SOLUSDT continuam com CVD/OBI neutros (sem WS de ticks ao vivo para esses pares). |
| ✅ P1-2 | ~~**Risco de portfólio**: cap de exposição agregada + ajuste por correlação BTC/ETH/SOL~~ **INFRA PRONTA, INERTE** (2026-07-12) | `risco.py` (`matriz_correlacao`, `exposicao_agregada_efetiva`, check 6.5 em `validar_trade`) | Investigação mostrou que `MAX_POSICOES_ABERTAS=1` já é um contador **global** (não por par, `_estado_risco["posicoes_abertas"]`) e hoje bloqueia qualquer 2ª posição simultânea entre BTCUSDT/ETHUSDT/SOLUSDT — a premissa original ("1 posição por par não protege o agregado") estava errada sobre o mecanismo atual. Construída a infraestrutura completa (correlação de Pearson entre pares via klines cacheados 15min, exposição efetiva ponderada por correlação `sqrt(Σᵢⱼ nᵢnⱼcorr(i,j))`) e testada (37 testes novos), mas **deliberadamente mantida inerte**: `validar_trade()` só aplica o teto quando `posicoes_abertas_detalhe` é passado, e nenhum call site real (`main.py`/`executor.py`) popula isso hoje — decisão do usuário foi manter `MAX_POSICOES_ABERTAS=1` por ora. Elevar o limite para ativar isso de fato é uma decisão de risco separada, ainda não tomada. Bug real pego antes do commit: `matriz_correlacao` podia crashar quando `_precos_para_retornos` descartava pontos de preço não-positivos, produzindo listas de retornos de tamanhos diferentes mesmo com preços de entrada do mesmo tamanho — corrigido truncando também os retornos ao mínimo comum. |
| 🔧 P1-3 | ~~**Meta-labeling (triple-barrier)** sobre o score/ensemble atual~~ **INSTRUMENTAÇÃO DO DB FEITA** (2026-07-13), meta-modelo em si NÃO iniciado | `database.py`, `executor.py`, `estrategias/otimizada.py` | Investigação mostrou que meta-labeling **não é treinável com os dados de produção de hoje**: a tabela `sinais` não tinha ligação entrada↔saída (sem PnL, sem barreira tocada, sem score-na-entrada nas linhas de fechamento) — `risco.kelly_do_banco()` (que já consome essa tabela) calculava win-rate via `tipo=="COMPRA"`, um proxy sem sentido matemático. Decisão do usuário: corrigir a instrumentação do banco primeiro, meta-modelo fica para quando houver histórico real acumulado. Feito: novas colunas em `sinais` (`preco_saida`, `pnl_usdt`, `pnl_pct`, `barreira_tocada`, SQLite+Postgres+`supabase/migrations/002_meta_labeling_columns.sql`); `salvar_sinal()` retorna o id da linha; `sinal_id` passa a ser linkado ponta-a-ponta (`estrategias/otimizada.py` → `main.py` → `executor.abrir_long`) e persistido em `self.posicao`; `executor.fechar_posicao()` classifica a barreira pelo **motivo exato** ("Stop Loss"/"Take Profit Final"/"Take Profit Parcial (50%)", não mais pelo sinal do PnL — bug real: um trailing-stop em lucro era rotulado igual a um target de verdade) e acumula PnL de fechamentos parciais para o resultado final bater certo; `kelly_do_banco()` corrigido para usar `pnl_usdt>0` real. `sinais_executados()` agora filtra por trades **fechados** (`pnl_usdt IS NOT NULL`), não só `executado=true` (uma posição ainda aberta não deve contar como resultado conhecido). |
| ✅ P1-4 | ~~**MLflow** para versionar XGBoost+MLP + alertar drift~~ **FEITO** (2026-07-13), sem MLflow (guard-rail leve) | `database.py` (tabela `model_metricas`), `validacao.detectar_drift`, `ml_filtro.verificar_drift_e_registrar` (reusado por `lstm_modelo.py`) | Investigação mostrou que um servidor MLflow completo era desproporcional: só 4 linhagens de modelo (BTCUSDT/ETHUSDT/SOLUSDT XGBoost + 1 MLP global), retreinadas 1x/semana, e o retreino automático hoje sobrescrevia o `.pkl` sem NENHUMA checagem — o AUC (já calculado via purged CV, P0-1) era só impresso no console e perdido. Decisão do usuário: guard-rail leve, sem MLflow (sem processo novo p/ gerenciar no NSSM/systemd). Feito: tabela `model_metricas` (SQLite+Postgres+`supabase/migrations/003_model_metricas.sql`) registra AUC/cv_auc por retreino; `detectar_drift()` compara o retreino atual contra a média histórica (piso absoluto de AUC OU queda de 2 desvios-padrão, com piso mínimo de desvio para não falso-positivar em histórico de baixíssima variância) e ALERTA via `bot_events` (severity=WARNING) sem bloquear o retreino — decisão explícita de "alert-only, não block". |
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
