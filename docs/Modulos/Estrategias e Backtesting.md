---
tags: [modulo, backtesting, testes, qualidade]
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
| `backtesting/otimizador.py` | grid search (~20 params, multi-par) |
| `backtesting/walk_forward.py` | validação rolling com retreino XGBoost |
| `backtesting/coletar_dados.py` | coleta histórico Binance → SQLite |

- ✅ **Sem look-ahead bias** (indicadores pré-computados antes do loop).
- ✅ **Realista:** slippage 0.05% + taxa Binance 0.04%/lado; calcula Sharpe, win rate, max drawdown.
- **Gaps:** otimizador sem early-stopping nem out-of-sample; nenhum teste de determinismo do backtest.

## Testes ativos 🔴 Baixa cobertura
- `tests/test_data.py` — CVD calculator + indicadores (9 testes).
- `tests/test_melhorias.py` — score (pesos), FSRS (11), ensemble+FSRS, Ollama (fallback), retreino (508 linhas).
- ✅ Corrigido nesta sessão: probe do Ollama em tempo de coleta (não aborta mais o pytest).
- **Lacunas críticas (0% de testes diretos):** `executor.py`, `risco.py`, `ml_filtro.py`, `score.py` (parcial), backtesting (determinismo).
- Estado: `pytest` → **42 passed, 6 skipped** (testes do cluster async foram para `_legado/`, ignorados por `pytest.ini`).

## Qualidade de código 🟢/🟡
- `.pre-commit-config.yaml` — Black, isort, Flake8, MyPy, Bandit, detect-secrets, yamllint.
  - ⚠️ Referencia `-c .bandit` e `.secrets.baseline` que **não existem** → pre-commit pode falhar.
- `renovate.json` — automerge patch/minor, agrupamento, security imediato.
- `requirements.txt` — ✅ limpo nesta sessão (removidas 5 deps órfãs do cluster; `psycopg` mantido p/ Supabase). Falta `pytest`/`pytest-cov` listados.

---

### Resumo de maturidade
| Área | Nota |
|---|---|
| Backtesting (motor/walk-forward) | 🟢 Alta |
| Tooling de qualidade | 🟡 Média (configs faltando) |
| Cobertura de testes do core | 🔴 Baixa |

Ações em [[Planejamento de Melhorias]].
