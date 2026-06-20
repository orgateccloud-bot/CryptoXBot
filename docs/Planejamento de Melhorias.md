---
tags: [planejamento, backlog]
atualizado: 2026-06-20
---

# 🛠️ Planejamento de Melhorias

> Voltar: [[00 - Home]] · Base: relatórios em [[Core e Execucao]], [[ML e Sinais]], [[Dados e Infra]], [[Estrategias e Backtesting]]

Legenda esforço: ⏱️ pequeno (<1h) · ⏱️⏱️ médio (meio dia) · ⏱️⏱️⏱️ grande.

## ✅ Já concluído nesta sessão
- Showstoppers de clone limpo (`data/cvd_calculator`, `_score_regime`) — PR de fixes em `main`.
- Coerência de trading: VENDA ignorada explicitamente; voto de regime com 1D; lock no Executor; validação de resposta da Binance + fix do `fechar_posicao`.
- Whitelist SQL em `logger.exportar_csv`; docs (CLAUDE.md) alinhadas.
- Aposentadoria do cluster async → `_legado/` ([[Dados e Infra|PR #1]]).
- `requirements.txt` limpo (5 deps órfãs removidas; `psycopg` mantido p/ Supabase).
- Vault Obsidian + estrutura de deploy Supabase/Railway.
- ✅ **P0-1 testes do core** — `test_executor.py` (43), `test_risco.py` (74), `test_score.py` (136); **295 passed**; risco 90% / score 75% / executor 62%.

## 🔴 P0 — Antes de operar capital real
1. ✅ ~~Testes do core de trading~~ — **feito** (253 testes). Resta o **loop `_monitorar`** (trailing stop), que exige refatorar o `executor` para injeção de tempo. ⏱️⏱️
2. **`logger.py` multi-backend** — respeitar o roteador de `database.py` para não criar um SQLite paralelo em produção Supabase. ⏱️⏱️
3. **`pytest`/`pytest-cov` no `requirements.txt`** + criar `.secrets.baseline` e `.bandit` (ou remover refs) para o pre-commit não quebrar. ⏱️
4. **`database.fechar_pool()` no shutdown** (handler SIGTERM/SIGINT) — evita vazar conexões no restart do Railway. ⏱️

## 🟡 P1 — Robustez
5. **Refatorar `indicadores.py`** — remover duplicação de ATR/Bollinger/VWAP (2-3 versões divergentes); padronizar numpy + type hints. ⏱️⏱️
6. **Retry/backoff** nas chamadas de `ml_filtro`/`lstm_modelo`/`regime` (timeout 8s sem retry). ⏱️
7. **Persistir estado do `ScaleIn`** — hoje some no restart, deixando parcelas inconsistentes. ⏱️⏱️
8. **Lock no JSON do FSRS** (`fsrs_padroes.json`) — evitar corrupção concorrente. ⏱️
9. **Canonizar deploy Railway** — aposentar/atualizar `deploy.yml` (GCP) e `docker-compose.prod.yml` (`@Zeta`). ⏱️⏱️

## 🟢 P2 — Evolução
10. **Revalidação ML walk-forward** — overfitting do MLP (~264-517 features) e scaler drift (`@Sigma`). ⏱️⏱️⏱️
11. **SHORT real** (`executor.abrir_short`) — só após paper trading extenso + sign-off. ⏱️⏱️⏱️
12. **Determinismo no backtesting** — baseline de regressão (Sharpe/DD conhecidos). ⏱️⏱️
13. **`capital_inicio_dia` robusto** + **cache de saldo** no `risco.py`. ⏱️
14. **ADX validado** contra ta-lib em `regime.py`. ⏱️⏱️

## Sequência recomendada
```
P0 (1-4) → suíte cobre o core + Supabase íntegro   ← pré-requisito p/ capital real
P1 (5-9) → robustez de cálculo, rede e deploy
P2 (10-14) → evolução de modelo e estratégia
```
