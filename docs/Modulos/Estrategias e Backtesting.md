---
tags: [modulo, backtesting, testes, qualidade]
atualizado: 2026-08-09
---

# 🧪 Estratégias, Backtesting e Qualidade

> Voltar: [[00 - Home]] · Relacionado: [[Core e Execucao]] · [[Planejamento de Melhorias]]

---

## Backtesting 🟢 Alta
| Arquivo | Propósito |
|---|---|
| `backtesting/walk_forward.py` | **medição válida** — validação rolling com retreino XGBoost, régua unificada, causalidade MTF e política de saída de produção. 41 testes dedicados. |
| `backtesting/regua.py` | régua única (I-12): delega a `score.calcular`, a mesma da produção |
| `backtesting/alinhamento.py` | mapeamento causal 1h→4h (`mapear_idx_fechado`) — substitui as 4 cópias de `i//4` |
| `backtesting/motor_ensemble.py` | score + ensemble + ADX; serve `/api/backtest` (rota **desligada**, ver abaixo) |
| `backtesting/motor.py` | motor base EMA+RSI+CVD — **portão ativo**: 8 entradas do score são constantes hardcoded, exige `BACKTEST_MOCKS=1` (I-12d) |
| `backtesting/otimizador.py` | grid search Python puro (~20 params, multi-par) |
| `backtesting/metricas.py` | ponto único de verdade das métricas: Sharpe, Sortino, Calmar, Profit Factor, PSR/DSR, **CVaR histórico** (P2-3, reaproveitado por `risco.py`) |
| `backtesting/trend_following.py` | backtest canônico do sistema Donchian |
| `backtesting/coletar_dados.py` | coleta histórico Binance → SQLite |

- ✅ **Causalidade MTF** — `alinhamento.mapear_idx_fechado` só enxerga candles
  4h já fechados. O antigo `idx4 = i//4` lia o candle ainda aberto em **100%**
  das barras (as séries 1h e 4h têm origens diferentes).
- ✅ **Taxa de SPOT** (0,10%/lado) em todos os motores — era 0,04%, tarifa de
  **futuros**, num bot que executa `/api/v3/order`.
- ✅ **Política de saída de produção** em `walk_forward.py` (I-12h): parcial de
  50%, breakeven, target2 e trailing — não uma saída única.
- ⚠️ **Sem histórico de Fear & Greed.** `data/fng_historico.json` não existe;
  `walk_forward.py` **aborta** (exit 2) em vez de degradar para neutro, e rodar
  assim exige `--sem-fng` (I-12g).
- **Gaps:** otimizador sem early-stopping nem out-of-sample obrigatório.

### Rota `/api/backtest` — desligada (I-12a)
Servia +2,54% / Sharpe 1,04 / "ESTRATÉGIA PROMISSORA" por HTTP, 24/7. Com
causalidade e taxa spot o mesmo motor dá **−45,83%**; com a régua unificada,
**−38,98%**. A rota exige `BACKTEST_HTTP=1` e devolve 409 com o histórico dos
números. A medição válida é `python backtesting/walk_forward.py`.

### Aposentados em `_legado/` (I-12d, 2026-08-09)
`motor_otimizado.py` (órfão confirmado; o look-ahead no gate MTF obrigatório
contaminava a contagem de todos os 7 filtros em AND) e `motor_vectorbt.py` +
`test_motor_vectorbt.py` + `requirements-backtest.txt` (a referência de
paridade `_score_backtest` deixou de existir; nunca executou neste ambiente;
`fator` usado como booleano quebrava o sizing de meia posição sem que o teste
pudesse detectar). Motivos completos e plano de rollback em
[`_legado/LEIA-ME.md`](../../_legado/LEIA-ME.md).

`NautilusTrader` (validador de execução event-driven, P2-2b) segue fora de
escopo.

## Testes ativos 🟢 Core coberto (1.486 testes)
- `tests/test_data.py` — CVD calculator + indicadores (9 testes).
- `tests/test_melhorias.py` — score (pesos), ensemble, Ollama (fallback), retreino automático (semanal + relatório diário, P2-5).
- `tests/test_executor.py` (+ `test_executor_oco.py`, `test_executor_maker.py`,
  `test_executor_monitor.py`, `test_executor_reconciliacao.py`) — herméticos
  (sem rede/banco). Cobertura ampliada: **`TestExecutorConcorrencia`**
  (threads reais, TOCTOU, rede-fora-do-lock) e suíte dedicada de
  `reconciliar_boot()` (2026-07-22).
- `tests/test_risco.py` — Kelly, drawdown, circuit breaker (com debounce),
  exposição de portfólio, **CVaR de cauda** (P2-3, novo).
- `tests/test_backtesting_metricas.py` — Sharpe/Sortino/Calmar/DSR +
  **CVaR histórico** (P2-3, novo).
- `tests/test_walk_forward.py` — causalidade MTF, contabilidade de PnL,
  portão de F&G, params por par e **paridade da política de saída** com
  `executor.avaliar_tick_monitor` (360 combinações de estado).
- `tests/test_regua_medicao.py` — régua unificada; prova que `_score_backtest`
  foi eliminada.
- `tests/test_motores_aposentados.py` — trava a aposentadoria (nenhum import
  vivo dos módulos em `_legado/`) e o portão do `motor.py` nos dois
  entrypoints.
- `tests/test_health.py` — `/metrics`/`/health`/`/ready` + **`set_gauge`/
  `set_regime_atual`** (P2-5, novo).
- ✅ **Trailing stop**: `_monitorar` refatorado p/ função pura `avaliar_tick_monitor` + **oráculo de equivalência** (8.160 casos) prova que o refactor preserva o comportamento.
- `test_indicadores`(+adv), `test_otimizada_e2e`(+adv), `test_ml_filtro`(+adv), `test_regime`, `test_suporte`, `test_logger`.
  - Cobertura: indicadores **100%**, regime **99%**, score **96%**, otimizada **93%**.
- **Lacunas remanescentes:** `backtesting/{coletar_dados,motor_ensemble,otimizador}.py`
  sem teste dedicado (`walk_forward.py` saiu desta lista em I-12: 41 testes);
  `fear_greed.py`/`monitor_fluxo.py`/`telegram_bot.py` sem teste direto.
- Estado: `pytest` → **1.486 passed, 7 skipped** (suite hermética). O
  `.venv/` com `vectorbt` deixou de ser um cenário: `test_motor_vectorbt.py`
  foi aposentado em I-12d.

## Qualidade de código 🟢
- `.pre-commit-config.yaml` — Black, isort, Flake8, MyPy, Bandit, detect-secrets, yamllint.
  - ✅ `.bandit` (YAML) + `.secrets.baseline` presentes e funcionais.
- `pyproject.toml` — config de lint versionada; `black` aplicado no repo.
- `renovate.json` — automerge patch/minor, agrupamento, security imediato.
- `requirements.txt` — limpo (produção/worker/dashboard); `requirements-dev.txt`
  com `pytest`/`pytest-cov`/lint. `requirements-backtest.txt` (só `vectorbt`)
  foi para `_legado/` junto com o módulo que o usava (I-12d).

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| Backtesting (régua unificada + causalidade) | 🟢 Alta |
| `backtesting/walk_forward.py` | 🟢 Alta — **é a medição oficial** (I-12); falta histórico de F&G para valer no gate |
| Tooling de qualidade | 🟢 Alta (`.bandit`/`.secrets.baseline`/`pyproject.toml` presentes) |
| Cobertura de testes do core | 🟢 Alta (1.486 testes, suite hermética) |

Ações em `PLANO_MODERNIZACAO.md` (raiz) — fonte de verdade do roadmap.
