---
tags: [scorecard, maturidade]
atualizado: 2026-06-20
nota_global: 7.0
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
| Gestão de risco (trading) | 🟢 **8** | 1.5 | Kelly + circuit breaker + drawdown + lock + validação de ordem |
| Segurança | 🟡 **7** | 1.2 | sem segredos hardcoded, detect-secrets, paper por padrão; gaps: `SECRET_KEY` default, `.secrets.baseline` ausente |
| **Cobertura de testes** | 🔴 **4** | 1.2 | core (executor/risco/score) sem testes diretos; ~42 testes só em dados/ML |
| Arquitetura & organização | 🟢 **8** | 1.0 | arquitetura única após aposentar cluster; gaps: `indicadores.py` duplicado, `logger` SQLite-only |
| ML / Sinais | 🟡 **6** | 1.0 | XGBoost+MLP+FSRS+ensemble funcionam; riscos: overfitting MLP, scaler drift, ADX manual |
| Qualidade de código | 🟡 **6** | 1.0 | pre-commit completo, mas configs faltando (`.bandit`/baseline) e duplicação |
| Deploy & Infra | 🟡 **7** | 1.0 | Supabase + Railway prontos e documentados; `deploy.yml`/compose ainda GCP |
| Observabilidade | 🟡 **7** | 0.8 | logs estruturados, `/health`, Telegram, dashboard; `logger` não vai p/ Supabase |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 8·1.5 + 7·1.2 + 4·1.2 + 8·1.0 + 6·1.0 + 6·1.0 + 7·1.0 + 7·0.8 + 8·0.8) / 11.0
= 77.7 / 11.0
≈ 7.06
```

> ## 🟡 Nota global: **7.0 / 10 — "Beta funcional"**
> Pronto para **paper trading**; a barreira para **capital real** é a
> **cobertura de testes do core de trading** (única dimensão 🔴).

## Radar (visão rápida)
```
Executabilidade   █████████░  9
Risco             ████████░░  8
Arquitetura       ████████░░  8
Documentação      ████████░░  8
Segurança         ███████░░░  7
Deploy/Infra      ███████░░░  7
Observabilidade   ███████░░░  7
ML/Sinais         ██████░░░░  6
Qualidade código  ██████░░░░  6
Testes            ████░░░░░░  4   ← gargalo
```

## Evolução nesta sessão
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | **7.0** | arquitetura única, Supabase/Railway documentados, vault |

Próximo salto previsto: **8.0+** ao concluir o **P0 de testes** em [[Planejamento de Melhorias]].
