---
tags: [scorecard, maturidade]
atualizado: 2026-06-21
nota_global: 8.0
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
| Segurança | 🟢 **8** | 1.2 | sem segredos hardcoded, paper por padrão; **SECRET_KEY endurecido em prod**, `.secrets.baseline`+`.bandit` (pre-commit funcional); gap menor: CORS `*`, dashboard sem auth |
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
= (9·1.5 + 9·1.5 + 8·1.2 + 9·1.2 + 8·1.0 + 7·1.0 + 7·1.0 + 7·1.0 + 7·0.8 + 8·0.8) / 11.0
= 88.4 / 11.0
≈ 8.04
```

> ## 🟢 Nota global: **8.0 / 10 — "Beta maduro"**
> **Meta de 8.0 atingida.** Pronto para **paper trading**, com **599 testes**, um 3º
> showstopper corrigido (estratégia), segurança endurecida (SECRET_KEY + pre-commit
> funcional) e shutdown limpo (fecha pool no SIGTERM do Railway).
> Caminho para **8.5+**: `logger` Postgres (PR dedicado), revalidação walk-forward
> do ML, testes de backtesting, canonizar deploy Railway (ver [[Planejamento de Melhorias]]).

## Radar (visão rápida)
```
Testes            █████████░  9   ← 599 testes (era 4 no início)
Executabilidade   █████████░  9
Risco             █████████░  9
Segurança         ████████░░  8   ← SECRET_KEY + pre-commit funcional
Arquitetura       ████████░░  8
Documentação      ████████░░  8
Deploy/Infra      ███████░░░  7
Observabilidade   ███████░░░  7
ML/Sinais         ███████░░░  7
Qualidade código  ███████░░░  7
```

## Evolução nesta sessão
| Momento | Nota global | Marco |
|---|:---:|---|
| Início | ~3.5 | não iniciava de clone limpo (2 showstoppers) |
| Pós P0/P1/P2 | ~6.3 | executável, coerente, seguro nas ordens |
| Pós aposentadoria + docs | ~7.0 | arquitetura única, Supabase/Railway, vault |
| Pós testes do core | ~7.4 | 295 testes; core de trading coberto |
| Pós trailing stop testado | ~7.6 | 323 testes; `_monitorar` + equivalência provada |
| Pós fix da estratégia + ML/sinais testados | ~7.9 | 595 testes; 3º showstopper corrigido (otimizada/indicadores) |
| **Pós hygiene de segurança + shutdown limpo** | **8.0** | **599 testes**; SECRET_KEY endurecido, pre-commit funcional, `fechar_pool` no SIGTERM |

Próximo salto previsto: **8.5+** ao concluir `logger` Postgres, revalidação walk-forward do ML e testes de backtesting.
