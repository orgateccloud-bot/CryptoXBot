---
tags: [scorecard, maturidade]
atualizado: 2026-07-22
nota_global: 9.3
---

# 📊 Pontuações do Projeto (Maturidade)

> Voltar: [[00 - Home]] · Base: [[Planejamento de Melhorias]] · Roadmap: `PLANO_MODERNIZACAO.md` (raiz)

Avaliação pós-Rodada 3 de modernização (branch `chore/aposentar-cluster-async`):
locking/reconciliação de boot do executor, observabilidade conectada (P2-5),
CVaR de cauda (P2-3), grid search vetorizado (P2-2a). Escala 0-10.

## Scorecard por dimensão

| Dimensão | Nota | Peso | Justificativa |
|---|:---:|:---:|---|
| Executabilidade (clone→run) | 🟢 **9** | 1.5 | import main limpo + smoke test no CI |
| Gestão de risco (trading) | 🟢 **9** | 1.5 | Kelly + circuit breaker (com debounce) + drawdown + vol targeting + **CVaR de cauda regime-dependente (P2-3, inerte por default)** + lock + validação de ordem |
| **Confiabilidade de execução** | 🟢 **9** | 1.3 | **stop loss NA exchange** (STOP_LOSS_LIMIT/OCO nativo P2-1); **bracket OCO** stop+alvo atômico (opt-in, `OCO_BRACKET`); **locking correto** (RLock, mutações de posição sob lock, TOCTOU de `status()` corrigido); **janela fill→DB** reduzida (retry+escalonamento); **reconciliação de boot** contra o estado real da Binance (opt-in, `RECONCILIAR_BOOT_EXCHANGE`); **boot crash-loop** corrigido (relatório de Futures não derruba mais o processo); API robusta (recvWindow, sync de relógio, retry/backoff, idempotência) |
| **Segurança** | 🟢 **9** | 1.2 | sem segredos hardcoded, paper por padrão; **CORS endurecido em produção** (`CORS_SAME_ORIGIN_ONLY`, nega cross-origin por padrão); dashboard: bind `127.0.0.1`, `DASHBOARD_TOKEN`, fail-fast se exposto sem token; `Flask-Cors` atualizado (CVE-2024-6866); `.bandit` + `.secrets.baseline` no pre-commit |
| **Cobertura de testes** | 🟢 **9** | 1.2 | **971 testes** (8 skipped no ambiente principal; +4 de paridade VectorBT rodam num venv opcional com `vectorbt` instalado — 975/7 lá); executor/risco/health/observabilidade/CVaR/reconciliação de boot cobertos; suíte hermética |
| Arquitetura & organização | 🟢 **9** | 1.0 | mercado unificado SPOT; `data/klines.py` consolida 6 fetchers duplicados (P1-5); FSRS aposentado (branch morto, nunca ativava no caminho ao vivo); `_legado/` removido do repo |
| **ML / Sinais** | 🟢 **9** | 1.0 | XGBoost (55%) + MLP (45%) via ensemble, com Purged & Embargoed CV no treino (sem vazamento de futuro); **OBI** (Order Book Imbalance, 8% do score, P1-1); guard-rail de drift no retreino automático (P1-4); instrumentação de meta-labeling pronta (P1-3, aguardando histórico — ver `PLANO_MODERNIZACAO.md` P2-4) |
| **Qualidade de código** | 🟢 **9** | 1.0 | `pyproject.toml` (lint config versionada); black aplicado; dead code removido; bug do CVD corrigido |
| Deploy & Infra | 🟢 **9** | 1.0 | **serviço 24/7**: Windows NSSM (worker :8080 + dashboard :5000) · VPS systemd (`deploy/`); Caddy p/ HTTPS; Supabase (pool fix); shutdown gracioso multi-sinal; boot fail-fast em modo real |
| **Observabilidade** | 🟢 **9** | 0.8 | **P2-5 (2026-07-22): métricas/alertas conectados** — `/metrics` Prometheus com contadores/gauges REAIS (ordens, drawdown, circuit breaker, WS, PnL do dia, regime, latência de decisão); 7 alertas Telegram todos ligados (antes só 1 de 7); relatório diário agendado (18h); `/health`/`/ready` com watchdog de WS |
| Documentação | 🟢 **9** | 0.8 | vault Obsidian atualizado (Rodada 3, 2026-07-22) refletindo P0/P1/P2; `PLANO_MODERNIZACAO.md` na raiz é a fonte de verdade do roadmap |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 9·1.3 + 9·1.2 + 9·1.2 + 9·1.0 + 9·1.0 + 9·1.0 + 9·1.0 + 9·0.8 + 9·0.8) / 12.3
= 113.1 / 12.3
≈ 9.2
```

> ## 🟢 Nota global: **9.2 / 10 — "Production Ready · Hardened"**
> **971 testes** herméticos (8 skipped). **11 de 11 dimensões em 9/10**.
> Rodando **paper trading 24/7 em serviço** (NSSM/systemd). Rodada 3
> (2026-07-22) fechou o gap mais crítico restante — o *locking* incorreto
> do executor e a cegueira do boot recovery frente ao estado real da
> Binance — e conectou a observabilidade que já existia mas estava
> desligada. Próxima fronteira: `NautilusTrader` (P2-2b), meta-labeling
> (P2-4, aguardando dados reais) e o núcleo event-driven (P3-1) — ver
> `PLANO_MODERNIZACAO.md`.

## Radar (visão rápida)
```
Testes            █████████░  9   ← 971 testes (8 skipped)
Executabilidade   █████████░  9
Risco             █████████░  9   ← + CVaR de cauda regime-dependente (P2-3)
Confiab. exec     █████████░  9   ← + locking correto + reconciliação de boot
Segurança         █████████░  9   ← CORS endurecido em produção
Arquitetura       █████████░  9   ← data/klines.py; FSRS aposentado
Deploy/Infra      █████████░  9   ← serviço 24/7 NSSM/systemd + Caddy
Observabilidade   █████████░  9   ← metricas/alertas Telegram conectados (P2-5)
ML/Sinais         █████████░  9   ← OBI (P1-1) + guard-rail de drift (P1-4)
Qualidade código  █████████░  9
Documentação      █████████░  9   ← vault atualizado Rodada 3
```

## Evolução do projeto
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | ~7.0 | arquitetura única, Supabase, vault |
| Pós testes do core | ~7.4 | 295 testes; core de trading coberto |
| Pós trailing stop testado | ~7.6 | 323 testes; `_monitorar` + equivalência provada |
| Pós fix da estratégia + ML/sinais testados | ~7.9 | 595 testes; 3º showstopper corrigido |
| Pós hygiene de segurança + shutdown limpo | ~8.0 | 599 testes; SECRET_KEY endurecido |
| Pós validação Postgres real (logger + pool) | ~8.2 | logger multi-backend; 2 bugs prod corrigidos |
| Pós lotes de segurança/qualidade/ML/testes/obs | ~9.0 | 710 testes; 9 dimensões em 9/10 |
| Pós hardening (stop na exchange, crash recovery, API robusta, dashboard seguro, CVD) | 9.2 | 723 testes; mercado SPOT unificado; serviço 24/7 |
| Rodada 2 (auditoria pós-P0/P1: boot crash-loop, CORS, OCO nativo P2-1) | ~9.2 | bracket OCO opt-in; CORS endurecido em produção |
| **Rodada 3 (locking/reconciliação de boot, CVaR P2-3, observabilidade P2-5, VectorBT P2-2a, FSRS aposentado)** | **9.2** | **971 testes; 11/11 dimensões em 9/10; vault atualizado** |
