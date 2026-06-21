---
tags: [moc, home]
atualizado: 2026-06-20
branch: chore/aposentar-cluster-async
---

# 🤖 BinanceXBot — Mapa de Conteúdo (MOC)

Bot de trading algorítmico para Binance Futures, **long-only**, com estratégia
multi-filtro + ensemble de ML, gestão de risco (Kelly + circuit breaker) e deploy
em **Railway + Supabase**.

> **Estado atual:** Beta maduro · pronto para **paper trading** · `import main` limpo · `pytest` verde (**599 passed**, 6 skipped).
> **Nota de maturidade global: 🟢 8.0/10** — ver [[Pontuacoes do Projeto]].

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
| Testes | 🟢 9 | **595 testes**; indicadores 100%, regime 99%, score 96%, otimizada 93% |
| Executabilidade | 🟢 9 | clone→run OK + smoke test no CI |
| Gestão de risco | 🟢 9 | Kelly + circuit breaker + drawdown; trailing stop testado |
| Arquitetura | 🟢 8 | cluster async aposentado; `indicadores.py` desduplicado |
| Documentação | 🟢 8 | vault Obsidian + deploy guides |
| Segurança | 🟢 8 | SECRET_KEY endurecido; pre-commit funcional |
| ML / Sinais | 🟡 7 | XGBoost+MLP+FSRS; ml_filtro/regime testados |
| Qualidade de código | 🟡 7 | indicadores desduplicado + bugs corrigidos |
| Observabilidade | 🟡 7 | logs, health, Telegram, dashboard |
| Deploy/Infra | 🟡 7 | Supabase + Railway prontos; fecha pool no SIGTERM |

> 🎯 **Meta 8.0 atingida.** Próximos focos para **8.5+**: `logger` Postgres (PR dedicado), revalidação walk-forward do ML, testes de backtesting, canonizar deploy Railway.
