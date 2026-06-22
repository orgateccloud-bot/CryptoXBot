---
tags: [scorecard, maturidade]
atualizado: 2026-06-22
nota_global: 8.2
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
| Deploy & Infra | 🟢 **8** | 1.0 | **alvo único Railway+Supabase** (Docker/GCP aposentados); backend Postgres validado (fix do pool `open=True`); shutdown limpo; gap: sem IaC p/ Railway |
| Observabilidade | 🟢 **8** | 0.8 | logs estruturados, `/health`, Telegram, dashboard; **`logger` agora persiste no Supabase** (sem split-brain) |
| Documentação | 🟢 **8** | 0.8 | vault Obsidian + relatórios + deploy guides; CLAUDE.md alinhado |

## Nota global ponderada

```
Σ(nota × peso) / Σ(peso)
= (9·1.5 + 9·1.5 + 8·1.2 + 9·1.2 + 8·1.0 + 7·1.0 + 7·1.0 + 8·1.0 + 8·0.8 + 8·0.8) / 11.0
= 90.2 / 11.0
≈ 8.20
```

> ## 🟢 Nota global: **8.2 / 10 — "Beta maduro"**
> Pronto para **paper trading**, com **600 testes**. O backend **Postgres/Supabase
> foi validado end-to-end** contra um Postgres real provisionado — o que revelou e
> corrigiu 2 bugs de produção: pool sem `open=True` (Supabase inoperante em
> psycopg_pool ≥ 3.2) e o `logger` que só gravava em SQLite (split-brain).
> Caminho para **8.5+**: revalidação walk-forward do ML, testes de backtesting,
> canonizar deploy Railway/aposentar GCP (ver [[Planejamento de Melhorias]]).

## Radar (visão rápida)
```
Testes            █████████░  9   ← 600 testes (era 4 no início)
Executabilidade   █████████░  9
Risco             █████████░  9
Segurança         ████████░░  8
Arquitetura       ████████░░  8
Documentação      ████████░░  8
Deploy/Infra      ████████░░  8   ← backend Postgres validado (fix do pool)
Observabilidade   ████████░░  8   ← logger persiste no Supabase
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
| Pós hygiene de segurança + shutdown limpo | ~8.0 | 599 testes; SECRET_KEY endurecido, pre-commit funcional, `fechar_pool` no SIGTERM |
| **Pós validação Postgres real (logger + pool)** | **8.2** | logger multi-backend validado em Postgres; **2 bugs de produção corrigidos** (pool `open=True`, split-brain do logger) |

Próximo salto previsto: **8.5+** ao revalidar o ML (walk-forward), testar backtesting e canonizar o deploy Railway (aposentar GCP).
