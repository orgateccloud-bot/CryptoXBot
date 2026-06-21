---
tags: [scorecard, maturidade]
atualizado: 2026-06-21
nota_global: 7.9
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
| **Cobertura de testes** | 🟢 **9** | 1.2 | **595 testes**; caminho de capital + sinal coberto (indicadores 100%, regime 99%, score 96%, otimizada 93%, risco 90%, executor 82%); gaps: backtesting/database/dashboard sem testes diretos |
| Arquitetura & organização | 🟢 **8** | 1.0 | arquitetura única após aposentar cluster; `indicadores.py` desduplicado; gap: `logger` SQLite-only |
| ML / Sinais | 🟡 **7** | 1.0 | XGBoost+MLP+FSRS+ensemble; ml_filtro/regime testados; riscos: overfitting MLP, scaler drift, ADX manual |
| Qualidade de código | 🟡 **7** | 1.0 | pre-commit completo; `indicadores.py` desduplicado + bugs corrigidos; gaps: `.bandit`/`.secrets.baseline` ausentes |
| Deploy & Infra | 🟡 **7** | 1.0 | Supabase + Railway prontos e documentados; `deploy.yml`/compose ainda GCP |
| Observabilidade | 🟡 **7** | 0.8 | logs estruturados, `/health`, Telegram, dashboard; `logger` não vai p/ Supabase |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 7·1.2 + 9·1.2 + 8·1.0 + 7·1.0 + 7·1.0 + 7·1.0 + 7·0.8 + 8·0.8) / 11.0
= 87.2 / 11.0
≈ 7.93
```

> ## 🟢 Nota global: **7.9 / 10 — "Beta sólido" (à porta do 8.0)**
> Pronto para **paper trading**, com **595 testes**. Nesta rodada foi corrigido um
> **3º showstopper**: a estratégia (`otimizada.analisar`) quebrava a cada ciclo por
> bugs em `indicadores.py` — agora coberta a 93% e o módulo a 100%.
> Para cruzar **8.0+**: `logger` multi-backend (PR dedicado), `.secrets.baseline`/`.bandit`,
> revalidação walk-forward do ML e testes de backtesting (ver [[Planejamento de Melhorias]]).

## Radar (visão rápida)
```
Testes            █████████░  9   ← 595 testes (era 4 no início)
Executabilidade   █████████░  9
Risco             █████████░  9
Arquitetura       ████████░░  8
Documentação      ████████░░  8
Segurança         ███████░░░  7
Deploy/Infra      ███████░░░  7
Observabilidade   ███████░░░  7
ML/Sinais         ███████░░░  7   ← ml_filtro/regime testados
Qualidade código  ███████░░░  7   ← indicadores desduplicado + bugs corrigidos
```

## Evolução nesta sessão
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | ~7.0 | arquitetura única, Supabase/Railway, vault |
| Pós testes do core | ~7.4 | 295 testes; core de trading coberto |
| Pós trailing stop testado | ~7.6 | 323 testes; `_monitorar` + equivalência provada |
| **Pós fix da estratégia + ML/sinais testados** | **7.9** | **595 testes**; 3º showstopper corrigido (otimizada/indicadores), indicadores 100% |

Próximo salto previsto: **8.0+** ao concluir `logger` multi-backend, hygiene de segurança e revalidação do ML.
