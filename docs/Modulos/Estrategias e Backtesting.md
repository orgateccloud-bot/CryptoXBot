---
tags: [modulo, backtesting, testes, qualidade]
atualizado: 2026-07-22
---

# 🧪 Estratégias, Backtesting e Qualidade

> Voltar: [[00 - Home]] · Relacionado: [[Core e Execucao]] · [[Planejamento de Melhorias]]

---

## Backtesting 🟢 Alta
| Arquivo | Propósito |
|---|---|
| `backtesting/motor.py` | motor base EMA+RSI+CVD (1h/4h) |
| `backtesting/motor_ensemble.py` | **principal** — score + ensemble + ADX |
| `backtesting/motor_otimizado.py` | variante sem ML (regime+CVD) |
| `backtesting/otimizador.py` | grid search Python puro (~20 params, multi-par) — mantido como oráculo de correção |
| `backtesting/motor_vectorbt.py` | **novo (P2-2a)** — grid search vetorizado (VectorBT/numba), aditivo, não substitui `otimizador.py`. Ver nota abaixo. |
| `backtesting/metricas.py` | ponto único de verdade das métricas: Sharpe, Sortino, Calmar, Profit Factor, Deflated Sharpe Ratio (P0-4), **CVaR histórico** (P2-3, reaproveitado por `risco.py`) |
| `backtesting/walk_forward.py` | validação rolling com retreino XGBoost — **status ainda em aberto**: confirmado não-importado por nenhum outro módulo e sem teste dedicado (ver `PLANO_MODERNIZACAO.md`) |
| `backtesting/coletar_dados.py` | coleta histórico Binance → SQLite |

- ✅ **Sem look-ahead bias** (indicadores pré-computados antes do loop).
- ✅ **Realista:** slippage 0.05% + taxa Binance 0.04%/lado; calcula Sharpe/Sortino/Calmar/DSR, win rate, max drawdown, **n_trials** (P0-4 — corrige otimismo de múltiplas comparações no grid search).
- **Gaps:** otimizador sem early-stopping nem out-of-sample.

### `motor_vectorbt.py` (P2-2a, 2026-07-22) — o que é e o que não é
Ataca o gargalo de **velocidade** do grid search (até 8000 backtests Python
puro sequenciais em `otimizador.py`) via VectorBT/numba — não muda a regra
de sinal, reproduz `_score_backtest` vetorizado e validado **bit-a-bit**
contra o legado (`tests/test_motor_vectorbt.py`, dataset sintético, zero
divergência). `vectorbt` é dependência **opcional**
(`requirements-backtest.txt`, não entra no ambiente de produção) — os
testes de paridade usam `pytest.importorskip` e só rodam se instalado.

> ⚠️ **Nunca instalar `vectorbt` no Python global/compartilhado da máquina**
> — esta máquina não tem venv dedicado ao projeto; um `pip install vectorbt`
> sem pin explícito de numpy/scipy/scikit-learn já causou um incidente real
> (upgrade em cascata para numpy≥2.4.6/pandas≥3.0, quebrando outras
> ferramentas no mesmo Python). Sempre usar um `.venv/` local ao projeto.

`NautilusTrader` (validador de execução event-driven, P2-2b) fica para uma
rodada futura — preocupação ortogonal ao VectorBT (velocidade de pesquisa
vs. realismo de fill), não bloqueada por esta.

## Testes ativos 🟢 Core coberto (971 testes)
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
- `tests/test_motor_vectorbt.py` — paridade score vetorizado vs. legado +
  pipeline completo (skip automático sem `vectorbt` instalado).
- `tests/test_health.py` — `/metrics`/`/health`/`/ready` + **`set_gauge`/
  `set_regime_atual`** (P2-5, novo).
- ✅ **Trailing stop**: `_monitorar` refatorado p/ função pura `avaliar_tick_monitor` + **oráculo de equivalência** (8.160 casos) prova que o refactor preserva o comportamento.
- `test_indicadores`(+adv), `test_otimizada_e2e`(+adv), `test_ml_filtro`(+adv), `test_regime`, `test_suporte`, `test_logger`.
  - Cobertura: indicadores **100%**, regime **99%**, score **96%**, otimizada **93%**.
- **Lacunas remanescentes:** `backtesting/{coletar_dados,motor_ensemble,otimizador,walk_forward}.py` sem teste dedicado (exceto via `metricas.py`, coberto indiretamente); `fear_greed.py`/`monitor_fluxo.py`/`telegram_bot.py` sem teste direto.
- Estado: `pytest` → **971 passed, 8 skipped** no ambiente principal (suite
  hermética); **975 passed, 7 skipped** num `.venv/` local com `vectorbt`
  instalado (roda também `test_motor_vectorbt.py`).

## Qualidade de código 🟢
- `.pre-commit-config.yaml` — Black, isort, Flake8, MyPy, Bandit, detect-secrets, yamllint.
  - ✅ `.bandit` (YAML) + `.secrets.baseline` presentes e funcionais.
- `pyproject.toml` — config de lint versionada; `black` aplicado no repo.
- `renovate.json` — automerge patch/minor, agrupamento, security imediato.
- `requirements.txt` — limpo (produção/worker/dashboard); `requirements-dev.txt`
  com `pytest`/`pytest-cov`/lint; `requirements-backtest.txt` (novo, P2-2a)
  **opcional**, só `vectorbt` para pesquisa — nunca no ambiente de produção.

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| Backtesting (motor/vetorizado) | 🟢 Alta |
| `backtesting/walk_forward.py` | 🟡 Média (status ainda não decidido — CLI oficial ou aposentar) |
| Tooling de qualidade | 🟢 Alta (`.bandit`/`.secrets.baseline`/`pyproject.toml` presentes) |
| Cobertura de testes do core | 🟢 Alta (971 testes, suite hermética) |

Ações em `PLANO_MODERNIZACAO.md` (raiz) — fonte de verdade do roadmap.
