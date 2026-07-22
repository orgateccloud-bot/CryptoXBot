---
tags: [moc, home]
atualizado: 2026-07-22
branch: chore/aposentar-cluster-async
---

# 🤖 BinanceXBot — Mapa de Conteúdo (MOC)

Bot de trading algorítmico para Binance **Spot** (execução via `/api/v3/order`),
**long-only**, com estratégia multi-filtro + ensemble de ML, gestão de risco
(Kelly + circuit breaker + CVaR de cauda) e deploy como **serviço 24/7
(Windows NSSM · VPS systemd) + Supabase**. Futures (`fapi.binance.com`) é lido
**apenas** para funding rate / open interest como sentimento — não é o
mercado de execução.

> **Estado atual:** Beta maduro · paper trading 24/7 em serviço · locking do
> executor e reconciliação de boot endurecidos · observabilidade conectada ·
> `import main` limpo · `pytest` verde (**971 passed**, 8 skipped).
> **Nota de maturidade global: 🟢 9.2/10** — ver [[Pontuacoes do Projeto]].
> **Roadmap de modernização:** `PLANO_MODERNIZACAO.md` (raiz) — P0–P3.

## 🗺️ Navegação

### Arquitetura
- [[Visao Geral]] — visão de alto nível + diagrama do sistema
- [[Fluxo de Execucao]] — o que acontece quando `main.py` roda

### Módulos (relatórios)
- [[Core e Execucao]] — `main`, `executor`, `risco`, `suporte`, `indicadores`, estratégias
- [[ML e Sinais]] — `ensemble`, `ml_filtro` (XGBoost), `lstm_modelo` (MLP), `score`, `regime`, `fear_greed`, `ollama_client`, `cvd_calculator`
- [[Dados e Infra]] — `database`, `logger`, `health`, `telegram_bot`, `dashboard`, `monitor_fluxo`, `config`
- [[Estrategias e Backtesting]] — `backtesting/`, testes, qualidade

### Operação
- [[Deploy Supabase]] — banco Postgres gerenciado (schema + migração)
- [[Deploy VPS]] — serviço 24/7 (Windows NSSM · VPS systemd) + Caddy
- [[Variaveis de Ambiente]] — referência completa de env vars

### Gestão
- [[Planejamento de Melhorias]] — ver `PLANO_MODERNIZACAO.md` (raiz), fonte de verdade do roadmap
- [[Pontuacoes do Projeto]] — scorecard de maturidade por dimensão

## ⚡ Resumo rápido

| Dimensão | Nota | Destaque |
|---|---|---|
| Testes | 🟢 9 | **971 testes** (8 skipped); +4 de paridade VectorBT num venv opcional |
| Executabilidade | 🟢 9 | clone→run OK + smoke test no CI |
| Gestão de risco | 🟢 9 | Kelly + circuit breaker (debounce) + drawdown + vol targeting + CVaR de cauda regime-dependente |
| Confiabilidade de execução | 🟢 9 | stop na exchange + bracket OCO opt-in + locking correto (RLock) + reconciliação de boot opt-in + boot crash-loop corrigido |
| Arquitetura | 🟢 9 | `data/klines.py` consolida 6 fetchers; FSRS aposentado |
| Documentação | 🟢 9 | vault Obsidian atualizado (Rodada 3, 2026-07-22) |
| Segurança | 🟢 9 | CORS endurecido em produção; Flask-Cors atualizado; dashboard fail-fast |
| Deploy/Infra | 🟢 9 | serviço 24/7 (NSSM/systemd) + Supabase; Caddy p/ HTTPS |
| Observabilidade | 🟢 9 | métricas/gauges reais + 7 alertas Telegram conectados + relatório diário |
| ML / Sinais | 🟢 9 | XGBoost+MLP com Purged CV; OBI (8% do score); guard-rail de drift |
| Qualidade de código | 🟢 9 | dead code removido + bugs corrigidos + black aplicado |

> 🎯 **9.2/10.** Rodada 3 (2026-07-22) fechou o gap mais crítico restante — o
> *locking* incorreto do executor (mutações de posição fora do lock, TOCTOU
> em `status()`) e a cegueira do boot recovery frente ao estado real da
> Binance — e conectou a observabilidade que já existia mas estava
> desligada (contadores/gauges/alertas Telegram). Também: CVaR de cauda
> regime-dependente (P2-3), grid search vetorizado via VectorBT (P2-2a),
> FSRS aposentado. Próximos focos: `NautilusTrader` (P2-2b), meta-labeling
> (P2-4, aguardando dados reais) — ver `PLANO_MODERNIZACAO.md`.
