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

> **Estado atual:** engenharia pronta · **estratégia NÃO validada** (nenhum
> backtest consolidado nem paper trading com números — ver [[GATE_GO_LIVE]]) ·
> serviço 24/7 em `--simulacao` · `pytest` verde (**971 passed**, 8 skipped).
> **Nota de maturidade global: 🟡 7.9/10** — ver [[Pontuacoes do Projeto]]
> (12ª dimensão, rentabilidade validada, está em 0).
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
- [[GATE_GO_LIVE]] — **critérios pré-registrados de go-live** (3 etapas antes de capital real)
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

> 🎯 **7.9/10 — engenharia pronta, estratégia não validada.** Rodada 3
> (2026-07-22) fechou o gap de engenharia mais crítico — locking do executor
> e reconciliação de boot — e conectou a observabilidade. A rodada de
> honestidade (2026-07-23) adicionou a dimensão que faltava ao scorecard:
> **rentabilidade validada = 0** (nunca houve backtest consolidado nem paper
> trading com números). A fronteira agora não é código: é cumprir as 3
> etapas de [[GATE_GO_LIVE]] (walk-forward → 90 dias de paper medidos por
> `relatorio_gate.py` → capital piloto). Capital real segue proibido até lá.
