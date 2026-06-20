---
tags: [scorecard, maturidade]
atualizado: 2026-06-20
nota_global: 7.6
---

# 📊 Pontuações do Projeto (Maturidade)

> Voltar: [[00 - Home]] · Base: [[Planejamento de Melhorias]]

Avaliação do estado **pós-aposentadoria do cluster async** (branch
`chore/aposentar-cluster-async`). Escala 0-10. Peso reflete a criticidade para um
**bot de trading** (risco e executabilidade pesam mais).

## Scorecard por dimensão

| Dimensão | Nota | Peso | Justificativa |
|---|:---:|:---:|---|
| Executabilidade (clone→run) | 🟢 **9** | 1.5 | `import main` limpo + smoke test no CI; era 🔴 2 no início |
| Gestão de risco (trading) | 🟢 **9** | 1.5 | Kelly + circuit breaker + drawdown + lock + validação de ordem; **trailing stop testado + equivalência provada** |
| Segurança | 🟡 **7** | 1.2 | sem segredos hardcoded, detect-secrets, paper por padrão; gaps: `SECRET_KEY` default, `.secrets.baseline` ausente |
| **Cobertura de testes** | 🟢 **8** | 1.2 | **323 testes**; core money-touching coberto (risco 90%, score 75%, executor **82%** incl. trailing stop); gaps: ml_filtro/regime/backtesting (secundários) |
| Arquitetura & organização | 🟢 **8** | 1.0 | arquitetura única após aposentar cluster; gaps: `indicadores.py` duplicado, `logger` SQLite-only |
| ML / Sinais | 🟡 **6** | 1.0 | XGBoost+MLP+FSRS+ensemble funcionam; riscos: overfitting MLP, scaler drift, ADX manual |
| Qualidade de código | 🟡 **6** | 1.0 | pre-commit completo, mas configs faltando (`.bandit`/baseline) e duplicação |
| Deploy & Infra | 🟡 **7** | 1.0 | Supabase + Railway prontos e documentados; `deploy.yml`/compose ainda GCP |
| Observabilidade | 🟡 **7** | 0.8 | logs estruturados, `/health`, Telegram, dashboard; `logger` não vai p/ Supabase |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 7·1.2 + 8·1.2 + 8·1.0 + 6·1.0 + 6·1.0 + 7·1.0 + 7·0.8 + 8·0.8) / 11.0
= 84.0 / 11.0
≈ 7.64
```

> ## 🟢 Nota global: **7.6 / 10 — "Beta sólido"**
> Pronto para **paper trading**, com **323 testes** cobrindo todo o caminho de
> capital (risco/score/executor, incl. trailing stop com equivalência provada).
> Para chegar a **8.0+**: testar ML (ml_filtro/regime) e backtesting, refatorar
> `indicadores.py` e `logger` multi-backend (ver [[Planejamento de Melhorias]]).

## Radar (visão rápida)
```
Executabilidade   █████████░  9
Risco             █████████░  9   ← trailing stop testado + verificado
Arquitetura       ████████░░  8
Documentação      ████████░░  8
Testes            ████████░░  8   ← era 4 (gargalo resolvido)
Segurança         ███████░░░  7
Deploy/Infra      ███████░░░  7
Observabilidade   ███████░░░  7
ML/Sinais         ██████░░░░  6
Qualidade código  ██████░░░░  6
```

## Evolução nesta sessão
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | ~7.0 | arquitetura única, Supabase/Railway documentados, vault |
| Pós testes do core | ~7.4 | 295 testes (+253); core de trading coberto |
| **Pós trailing stop testado** | **7.6** | **323 testes**; `_monitorar` refatorado/coberto + equivalência provada |

Próximo salto previsto: **8.0+** ao testar ML/backtesting e refatorar `indicadores.py` (ver [[Planejamento de Melhorias]]).
