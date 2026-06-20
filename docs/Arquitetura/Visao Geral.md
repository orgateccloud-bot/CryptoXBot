---
tags: [arquitetura]
---

# 🏛️ Visão Geral da Arquitetura

> Voltar: [[00 - Home]] · Relacionado: [[Fluxo de Execucao]]

## Princípio
Arquitetura **síncrona com threads** (uma thread por par + uma para WebSocket +
uma para retreino semanal). A alternativa async ("fase 2") foi **aposentada** em
`_legado/` (ver `_legado/LEIA-ME.md`) — hoje existe **uma única arquitetura**.

## Diagrama

```mermaid
flowchart TD
    subgraph ENTRADA
        MAIN[main.py — orquestrador<br/>threads: WS + loop/par + retreino]
    end
    subgraph TEMPO_REAL[Dados em tempo real]
        WS[WebSocket Binance Futures<br/>aggTrade → CVD BTC]
    end
    subgraph SINAL[Geração de sinal por par]
        OTM[estrategias/otimizada.py<br/>8 filtros + MTF + VWAP]
        SCORE[score.py — 0-100<br/>10 componentes]
        REG[regime.py]
        FG[fear_greed.py]
        ENS[ensemble.py<br/>XGBoost + MLP + FSRS]
        SUP[suporte.py — ScaleIn 3 parcelas]
    end
    subgraph EXEC[Execução e Risco]
        RISCO[risco.py — Kelly + circuit breaker]
        EXE[executor.py — LONG + trailing stop]
    end
    subgraph DADOS[Persistência e Observabilidade]
        DB[(database.py<br/>SQLite dev / Supabase prod)]
        LOG[logger.py]
        TG[telegram_bot.py]
        HEALTH[health.py /health]
        DASH[dashboard.py — Flask]
    end

    MAIN --> WS --> DB
    MAIN --> OTM
    OTM --> SCORE --> REG & FG
    OTM --> ENS
    OTM --> SUP
    OTM --> RISCO --> EXE --> DB
    MAIN --> HEALTH
    EXE --> TG
    DB --> DASH

    classDef ok fill:#0d2a1a,stroke:#34d399,color:#fff;
    class RISCO,EXE,DB ok;
```

## Camadas e notas
- **Orquestração** → [[Core e Execucao]]
- **Sinal / ML** → [[ML e Sinais]]
- **Persistência / observabilidade** → [[Dados e Infra]]
- **Validação histórica** → [[Estrategias e Backtesting]]
- **Operação** → [[Deploy Supabase]], [[Deploy Railway]]

## Decisões arquiteturais relevantes
1. **Long-only** hoje: a estratégia gera sinais de VENDA, mas o `executor` só
   abre LONG; a VENDA é ignorada explicitamente (ver [[Core e Execucao]]).
2. **Dois backends de dados** via a mesma fachada `database.py` (SQLite local,
   Postgres/Supabase em produção) — ver [[Dados e Infra]].
3. **Paper trading é o padrão** (`DRY_RUN=true`); ordens reais exigem
   `ALLOW_REAL_TRADING=true` (defesa em profundidade).
4. **Migração de deploy** GCP (legado) → **Railway + Supabase** (atual).
