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
- ✅ **P0-1 testes do core** — `test_executor.py` (43), `test_risco.py` (74), `test_score.py` (136), `test_executor_monitor.py` (28); **323 passed**; risco 90% / score 75% / executor 82%.
- ✅ **Trailing stop testado** — `_monitorar` refatorado p/ função pura `avaliar_tick_monitor`, coberto + **oráculo de equivalência** (8.160 casos) provando preservação de comportamento.
- ✅ **3º showstopper corrigido** — `otimizada.analisar()` quebrava todo ciclo (`volume_relativo` IndexError + `bollinger` NameError em `indicadores.py`). Corrigido + `indicadores.py` desduplicado (100% cobertura) + regressão E2E (`otimizada` 93%).
- ✅ **Testes ML/sinais** — `ml_filtro` (65%), `regime` (99%), `suporte` (69%), `indicadores` (100%); **595 testes** no total.
- ✅ **Resiliência do `logger`** — `LoggerBot` agora delega `.warning/.error/.critical` (corrige AttributeError nos erros do WebSocket).
- ✅ **Hygiene de segurança** — `SECRET_KEY` endurecido em produção; `.secrets.baseline` + `.bandit` criados (pre-commit funcional); `requirements-dev.txt`.
- ✅ **Shutdown limpo** — `database.fechar_pool()` no `finally` + handler de SIGTERM (Railway).

## 🔴 P0 — Antes de operar capital real
1. ✅ ~~Testes do core + trailing stop + estratégia/ML~~ — **feito** (599 testes; otimizada 93%, indicadores 100%, regime 99%, score 96%).
2. **`logger.py` Postgres/Supabase** — hoje grava SEMPRE em SQLite local (split-brain em produção). Requer port das 3 tabelas (DDL + placeholders `%s`) via roteador de `database.py` + remover efeito de import. **PR dedicado** (precisa de instância Supabase p/ validar). ⏱️⏱️
3. ✅ ~~`pytest`/`pytest-cov` declarados + `.secrets.baseline`/`.bandit`~~ — **feito** (`requirements-dev.txt` + configs do pre-commit).
4. ✅ ~~`database.fechar_pool()` no shutdown~~ — **feito** (SIGTERM + finally).

## 🟡 P1 — Robustez
5. ✅ ~~Refatorar `indicadores.py`~~ — **feito** (duplicatas mortas removidas, bugs corrigidos, 100% cobertura).
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
