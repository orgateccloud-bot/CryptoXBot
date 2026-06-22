---
tags: [moc, home]
atualizado: 2026-06-20
branch: chore/aposentar-cluster-async
---

# 🤖 BinanceXBot — Mapa de Conteúdo (MOC)

Bot de trading algorítmico para Binance Futures, **long-only**, com estratégia
multi-filtro + ensemble de ML, gestão de risco (Kelly + circuit breaker) e deploy
em **Railway + Supabase**.

> **Estado atual:** Beta maduro · pronto para **paper trading** · `import main` limpo · `pytest` verde (**599 passed**, 7 skipped).
> **Nota de maturidade global: 🟢 8.2/10** — ver [[Pontuacoes do Projeto]].

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
| Deploy/Infra | 🟢 8 | Supabase/Railway prontos; backend Postgres validado |
| Observabilidade | 🟢 8 | logger persiste no Supabase (sem split-brain) |
| ML / Sinais | 🟡 7 | XGBoost+MLP+FSRS; ml_filtro/regime testados |
| Qualidade de código | 🟡 7 | indicadores desduplicado + bugs corrigidos |

> 🎯 **8.2/10.** Validação contra Postgres real corrigiu 2 bugs de produção (pool `open=True`, split-brain do logger). Próximos focos para **8.5+**: revalidação walk-forward do ML, testes de backtesting, canonizar deploy Railway.
