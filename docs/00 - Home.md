---
tags: [moc, home]
atualizado: 2026-06-20
branch: chore/aposentar-cluster-async
---

# 🤖 BinanceXBot — Mapa de Conteúdo (MOC)

Bot de trading algorítmico para Binance Futures, **long-only**, com estratégia
multi-filtro + ensemble de ML, gestão de risco (Kelly + circuit breaker) e deploy
em **Railway + Supabase**.

> **Estado atual:** Beta sólido · pronto para **paper trading** · `import main` limpo · `pytest` verde (**323 passed**, 6 skipped).
> **Nota de maturidade global: 🟢 7.6/10** — ver [[Pontuacoes do Projeto]].

## 🗺️ Navegação

### Arquitetura
- [[Visao Geral]] — visão de alto nível + diagrama do sistema
- [[Fluxo de Execucao]] — o que acontece quando `main.py` roda

### Módulos (relatórios)
- [[Core e Execucao]] — `main`, `executor`, `risco`, `suporte`, `indicadores`, estratégias
- [[ML e Sinais]] — `ensemble`, `ml_filtro` (XGBoost), `lstm_modelo` (MLP), `fsrs`, `score`, `regime`, `fear_greed`, `ollama_client`, `cvd_calculator`
- [[Dados e Infra]] — `database`, `logger`, `health`, `telegram_bot`, `dashboard`, `monitor_fluxo`, `config`
- [[Estrategias e Backtesting]] — `backtesting/`, testes, qualidade

### Operação
- [[Deploy Supabase]] — banco Postgres gerenciado (schema + migração)
- [[Deploy Railway]] — worker + dashboard
- [[Variaveis de Ambiente]] — referência completa de env vars

### Gestão
- [[Planejamento de Melhorias]] — backlog priorizado P0/P1/P2
- [[Pontuacoes do Projeto]] — scorecard de maturidade por dimensão

## ⚡ Resumo rápido

| Dimensão | Nota | Destaque |
|---|---|---|
| Executabilidade | 🟢 9 | clone→run OK + smoke test no CI |
| Gestão de risco | 🟢 9 | Kelly + circuit breaker + drawdown; trailing stop testado |
| Arquitetura | 🟢 8 | cluster async aposentado; arquitetura única |
| Testes | 🟢 8 | **323 testes**; risco 90%, score 75%, executor 82% |
| Documentação | 🟢 8 | vault Obsidian + deploy guides |
| Observabilidade | 🟡 7 | logs, health, Telegram, dashboard |
| Segurança | 🟡 7 | sem segredos hardcoded; paper por padrão |
| Deploy/Infra | 🟡 7 | Supabase + Railway prontos |
| ML / Sinais | 🟡 6 | XGBoost+MLP+FSRS; riscos de drift/overfitting |
| Qualidade de código | 🟡 6 | pre-commit ok; `indicadores.py` duplicado |

> Próximos focos: testar módulos de ML/backtesting e refatorar `indicadores.py`.
