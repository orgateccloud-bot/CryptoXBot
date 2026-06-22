---
tags: [scorecard, maturidade]
atualizado: 2026-06-22
nota_global: 9.0
---

# 📊 Pontuações do Projeto (Maturidade)

> Voltar: [[00 - Home]] · Base: [[Planejamento de Melhorias]]

Avaliação final da sessão rumo a 9.0/10 (branch
`chore/aposentar-cluster-async`, commit `5991003`). Escala 0-10.

## Scorecard por dimensão

| Dimensão | Nota | Peso | Justificativa |
|---|:---:|:---:|---|
| Executabilidade (clone→run) | 🟢 **9** | 1.5 | import main limpo + smoke test no CI; era 🔴 2 no início |
| Gestão de risco (trading) | 🟢 **9** | 1.5 | Kelly + circuit breaker + drawdown + lock + validação de ordem; trailing stop testado + equivalência provada |
| **Segurança** | 🟢 **9** | 1.2 | sem segredos hardcoded, paper por padrão; SECRET_KEY efêmera em prod; **`.bandit` corrigido de INI→YAML** (hook estava morto — falsa sensação de segurança); **CORS_ORIGINS warning em prod**; sem hardcoded secrets em allowlist |
| **Cobertura de testes** | 🟢 **9** | 1.2 | **710 testes** (+42); health/database/analise_mercado/backtesting/ensemble/dashboard cobertos; suite hermética |
| Arquitetura & organização | 🟢 **9** | 1.0 | cluster aposentado; indicadores desduplicado; **ema_rsi_cvd.py órfão arquivado**; **ai/__init__.py** (mypy desbloqueado) |
| **ML / Sinais** | 🟢 **9** | 1.0 | XGBoost+MLP+FSRS+ensemble **testados com 17 testes herméticos**; retry/backoff HTTP; FSRS escrita atômica; cache thread-safe; pesos por regime + fallback cobertos |
| **Qualidade de código** | 🟢 **9** | 1.0 | **F821 corrigido** (motor.py); **pyproject.toml** (lint config versionada); **black aplicado em 53 arquivos** (zero mudança semântica); dead code removido |
| Deploy & Infra | 🟢 **9** | 1.0 | Railway+Supabase único; pool fix; shutdown limpo; **railway.toml** com 2 serviços documentados + variáveis obrigatórias; **boot fail-fast** em modo real |
| **Observabilidade** | 🟢 **9** | 0.8 | logs estruturados, /health, /ready, Telegram, dashboard; **`/metrics` Prometheus** (contadores: sinais, ordens, erros, circuit breaker, ws_reconexoes, uptime); logger persiste no Supabase |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 9·1.2 + 9·1.2 + 9·1.0 + 9·1.0 + 9·1.0 + 9·1.0 + 9·0.8 + 8·0.8) / 11.0
= 99.0 / 11.0
= 9.0
```

> ## 🟢 Nota global: **9.0 / 10 — "Production Ready"**
> **710 testes** herméticos. **9 de 10 dimensões em 9/10**. Pronto para **paper
> trading extenso e validação de mercado real**. O gap restante (Documentação 8)
> é cosmético — o projeto está apto para deploy no Railway.

## Radar (visão rápida)
```
Testes            █████████░  9   ← 710 testes (+42 ensemble/dashboard)
Executabilidade   █████████░  9
Risco             █████████░  9
Segurança         █████████░  9   ← .bandit corrigido (hook estava morto)
Arquitetura       █████████░  9   ← ema_rsi_cvd.py órfão arquivado
Deploy/Infra      █████████░  9   ← railway.toml 2 serviços + boot validation
Observabilidade   █████████░  9   ← /metrics Prometheus
ML/Sinais         █████████░  9   ← 17 testes ensemble (pesos/fallback/FSRS)
Qualidade código  █████████░  9   ← black 53 arquivos + pyproject.toml
Documentação      ████████░░  8
```

## Evolução nesta sessão
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | ~7.0 | arquitetura única, Supabase/Railway, vault |
| Pós testes do core | ~7.4 | 295 testes; core de trading coberto |
| Pós trailing stop testado | ~7.6 | 323 testes; `_monitorar` + equivalência provada |
| Pós fix da estratégia + ML/sinais testados | ~7.9 | 595 testes; 3º showstopper corrigido |
| Pós hygiene de segurança + shutdown limpo | ~8.0 | 599 testes; SECRET_KEY endurecido |
| Pós validação Postgres real (logger + pool) | ~8.2 | logger multi-backend; 2 bugs prod corrigidos |
| Pós lotes 9.0 (segurança/qualidade/ML/testes/obs) | 8.9 | 668 testes; 7 dimensões em 9/10 |
| **Pós black + ensemble + dashboard (9.0 final)** | **9.0** | **710 testes; 9 dimensões em 9/10** |
