---
tags: [planejamento, backlog]
atualizado: 2026-07-22
---

# 🛠️ Planejamento de Melhorias

> Voltar: [[00 - Home]] · Base: relatórios em [[Core e Execucao]], [[ML e Sinais]], [[Dados e Infra]], [[Estrategias e Backtesting]]

Esta nota **duplicava** o backlog priorizado (P0/P1/P2/P3) que já vive em
`PLANO_MODERNIZACAO.md` (raiz do repo) — e drifta toda vez que o roadmap
muda sem esta nota ser atualizada junto (foi o que aconteceu: o backlog
detalhado aqui ficou parado em 2026-06-20, listando como "pendente" itens
que já tinham sido concluídos há semanas). Em vez de manter duas fontes de
verdade, esta nota agora só aponta para a real.

## Onde está o backlog de verdade

**`PLANO_MODERNIZACAO.md`** (raiz do repositório) é a fonte de verdade
única do roadmap — sempre atualizado a cada rodada de trabalho, com:
- **P0** — alto impacto / baixo esforço (concluído).
- **P1** — médio impacto/esforço (concluído: OBI, meta-labeling
  instrumentado, guard-rail de drift, `data/klines.py`).
- **Auditoria Geral — Rodada 2** — achados de segurança/confiabilidade
  pós-P0/P1 (boot crash-loop, CORS, FSRS, Flask-Cors — todos concluídos).
- **P2** — médio/alto esforço: P2-1 (OCO nativo, concluído), P2-2a
  (VectorBT, concluído) / P2-2b (NautilusTrader, adiado), P2-3 (CVaR de
  cauda, concluído), P2-4 (meta-labeling, aguardando dados reais), P2-5
  (observabilidade, concluído).
- **P3** — estrutural (planejar com Plan Mode antes de qualquer edição):
  núcleo event-driven, fractional differentiation + HMM, ADDM no guard-rail
  de drift.
- **Débito técnico** e **fontes de mercado** (pesquisa 2025-2026)
  verificadas por rodada.

## Como consultar

Para saber o que já foi feito, o que está aberto, e por quê, leia
`PLANO_MODERNIZACAO.md` diretamente — cada item tem justificativa, arquivos
tocados e (quando concluído) a data e o resumo da solução. Os relatórios de
módulo deste vault ([[Core e Execucao]], [[ML e Sinais]], [[Dados e Infra]],
[[Estrategias e Backtesting]]) detalham a implementação de cada item nos
arquivos correspondentes — o *quê* e *como* vivem lá; o *backlog priorizado*
vive só em `PLANO_MODERNIZACAO.md`.

Para o estado de maturidade geral por dimensão, ver [[Pontuacoes do Projeto]].
