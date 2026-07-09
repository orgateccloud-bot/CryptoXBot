---
tags: [scorecard, maturidade]
atualizado: 2026-07-09
nota_global: 9.2
---

# 📊 Pontuações do Projeto (Maturidade)

> Voltar: [[00 - Home]] · Base: [[Planejamento de Melhorias]] · Roadmap: `PLANO_MODERNIZACAO.md` (raiz)

Avaliação pós-hardening de segurança/confiabilidade (branch
`chore/aposentar-cluster-async`). Escala 0-10.

## Scorecard por dimensão

| Dimensão | Nota | Peso | Justificativa |
|---|:---:|:---:|---|
| Executabilidade (clone→run) | 🟢 **9** | 1.5 | import main limpo + smoke test no CI; era 🔴 2 no início |
| Gestão de risco (trading) | 🟢 **9** | 1.5 | Kelly + circuit breaker + drawdown + lock + validação de ordem; trailing stop testado + equivalência provada |
| **Confiabilidade de execução** | 🟢 **9** | 1.3 | **stop loss NA exchange** (STOP_LOSS_LIMIT sobrevive a crash); **crash recovery** de posição (persistida no DB, recuperada no boot); **API robusta** (recvWindow, sync de relógio, retry/backoff 429/5xx, newClientOrderId idempotente, consulta pós-timeout); **precisão via exchangeInfo dinâmico** |
| **Segurança** | 🟢 **9** | 1.2 | sem segredos hardcoded, paper por padrão; SECRET_KEY endurecido em prod; **dashboard**: bind `127.0.0.1`, `DASHBOARD_TOKEN`, CSP + X-Frame-Options, rate limit por IP, `esc()` anti-XSS, libs vendorizadas em `static/vendor/` (sem CDN); `.bandit` + `.secrets.baseline` no pre-commit |
| **Cobertura de testes** | 🟢 **9** | 1.2 | **723 testes** (7 skipped); health/database/analise_mercado/backtesting/ensemble/dashboard cobertos; suite hermética |
| Arquitetura & organização | 🟢 **9** | 1.0 | cluster async aposentado; indicadores desduplicado; **mercado unificado SPOT** (sinal + execução); `_legado/` removido do repo |
| **ML / Sinais** | 🟢 **9** | 1.0 | XGBoost (55%) + MLP (45%) + FSRS **testados com testes herméticos**; retry/backoff HTTP; FSRS escrita atômica; pickle salvo atomicamente (`tmp`+`os.replace`); pesos por regime + fallback cobertos |
| **Qualidade de código** | 🟢 **9** | 1.0 | F821 corrigido; `pyproject.toml` (lint config versionada); black aplicado; dead code removido; **bug do CVD corrigido** (parser usa `data["a"]` do @aggTrade, não `data["t"]`) |
| Deploy & Infra | 🟢 **9** | 1.0 | **serviço 24/7**: Windows NSSM (worker :8080 + dashboard :5000) · VPS systemd (`deploy/`); Caddy p/ HTTPS; Supabase (pool fix); **shutdown gracioso multi-sinal** (SIGTERM/SIGINT/SIGBREAK); boot fail-fast em modo real; Docker/Railway/GCP removidos do repo |
| **Observabilidade** | 🟢 **9** | 0.8 | logs estruturados, `/health`, `/ready` (**watchdog do WS**: `degraded` se sem mensagem >120s), `/metrics` Prometheus, Telegram, dashboard; logger persiste no Supabase |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado; `PLANO_MODERNIZACAO.md` na raiz |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 9·1.3 + 9·1.2 + 9·1.2 + 9·1.0 + 9·1.0 + 9·1.0 + 9·1.0 + 9·0.8 + 8·0.8) / 12.3
= (13.5 + 13.5 + 11.7 + 10.8 + 10.8 + 9.0 + 9.0 + 9.0 + 9.0 + 7.2 + 6.4) / 12.3
= 110.9 / 12.3
≈ 9.2
```

> ## 🟢 Nota global: **9.2 / 10 — "Production Ready · Hardened"**
> **723 testes** herméticos. **10 de 11 dimensões em 9/10**. Rodando **paper
> trading 24/7 em serviço** (NSSM/systemd). Hardening de segurança e
> confiabilidade de execução concluído (stop na exchange, crash recovery, API
> robusta, dashboard seguro, bug do CVD). O gap restante (Documentação 8) é
> cosmético; a próxima fronteira é **modernização de ML/execução** — ver
> `PLANO_MODERNIZACAO.md`.

## Radar (visão rápida)
```
Testes            █████████░  9   ← 723 testes (7 skipped)
Executabilidade   █████████░  9
Risco             █████████░  9
Confiab. exec     █████████░  9   ← stop na exchange + crash recovery + API robusta
Segurança         █████████░  9   ← dashboard bind local+token+CSP; libs vendorizadas
Arquitetura       █████████░  9   ← mercado unificado SPOT; _legado/ removido
Deploy/Infra      █████████░  9   ← serviço 24/7 NSSM/systemd + Caddy
Observabilidade   █████████░  9   ← /ready watchdog WS + /metrics
ML/Sinais         █████████░  9   ← XGBoost 55% + MLP 45% + FSRS testados
Qualidade código  █████████░  9   ← bug do CVD corrigido (data["a"])
Documentação      ████████░░  8
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
| **Pós hardening (stop na exchange, crash recovery, API robusta, dashboard seguro, CVD)** | **9.2** | **723 testes; mercado SPOT unificado; serviço 24/7 (NSSM/systemd)** |
