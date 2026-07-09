---
tags: [moc, home]
atualizado: 2026-07-09
branch: chore/aposentar-cluster-async
---

# 🤖 BinanceXBot — Mapa de Conteúdo (MOC)

Bot de trading algorítmico para Binance **Spot** (execução via `/api/v3/order`),
**long-only**, com estratégia multi-filtro + ensemble de ML, gestão de risco
(Kelly + circuit breaker) e deploy como **serviço 24/7 (Windows NSSM · VPS
systemd) + Supabase**. Futures (`fapi.binance.com`) é lido **apenas** para
funding rate / open interest como sentimento — não é o mercado de execução.

> **Estado atual:** Beta maduro · paper trading 24/7 em serviço · hardening de
> segurança/confiabilidade concluído · `import main` limpo · `pytest` verde
> (**723 passed**, 7 skipped).
> **Nota de maturidade global: 🟢 9.2/10** — ver [[Pontuacoes do Projeto]].
> **Roadmap de modernização:** `PLANO_MODERNIZACAO.md` (raiz) — P0–P3.

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
- [[Deploy VPS]] — serviço 24/7 (Windows NSSM · VPS systemd) + Caddy
- [[Variaveis de Ambiente]] — referência completa de env vars

### Gestão
- [[Planejamento de Melhorias]] — backlog priorizado P0/P1/P2
- [[Pontuacoes do Projeto]] — scorecard de maturidade por dimensão

## ⚡ Resumo rápido

| Dimensão | Nota | Destaque |
|---|---|---|
| Testes | 🟢 9 | **723 testes**; indicadores 100%, regime 99%, score 96%, otimizada 93% |
| Executabilidade | 🟢 9 | clone→run OK + smoke test no CI |
| Gestão de risco | 🟢 9 | Kelly + circuit breaker + drawdown; trailing stop testado |
| Confiabilidade de execução | 🟢 9 | stop loss NA exchange + crash recovery de posição + API robusta (retry/idempotência) |
| Arquitetura | 🟢 9 | cluster async aposentado; `indicadores.py` desduplicado |
| Documentação | 🟢 8 | vault Obsidian + deploy guides |
| Segurança | 🟢 9 | SECRET_KEY endurecido; dashboard bind local + token + CSP + rate limit; libs vendorizadas |
| Deploy/Infra | 🟢 9 | serviço 24/7 (NSSM/systemd) + Supabase; Caddy p/ HTTPS |
| Observabilidade | 🟢 9 | `/health`, `/ready` (watchdog WS), `/metrics`; logger persiste no Supabase |
| ML / Sinais | 🟢 9 | XGBoost+MLP+FSRS; ml_filtro/regime/ensemble testados |
| Qualidade de código | 🟢 9 | indicadores desduplicado + bugs corrigidos + black aplicado |

> 🎯 **9.2/10.** Hardening recente concluído: stop loss na exchange, crash
> recovery de posição, API robusta (recvWindow/sync de relógio/retry/idempotência),
> dashboard seguro e bug do CVD corrigido (`data["a"]` do @aggTrade). Próximos
> focos de modernização (validação de ML, execução maker-first, sizing por
> volatilidade) estão em `PLANO_MODERNIZACAO.md`.
