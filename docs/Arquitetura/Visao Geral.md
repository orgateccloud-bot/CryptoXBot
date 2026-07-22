---
tags: [arquitetura]
atualizado: 2026-07-22
---

# 🏛️ Visão Geral da Arquitetura

> Voltar: [[00 - Home]] · Relacionado: [[Fluxo de Execucao]]

## Princípio
Arquitetura **síncrona com threads** (uma thread por par + duas para
WebSocket — `@aggTrade`/CVD e `@depth`/OBI — + uma para retreino semanal +
uma para relatório diário). A alternativa async ("fase 2") foi **aposentada
e removida do repo** (o histórico git preserva) — hoje existe **uma única
arquitetura**.

Mercado: **Binance Spot** (execução via `/api/v3/order`, maker-first via
`LIMIT_MAKER`, proteção via `STOP_LOSS_LIMIT` ou bracket OCO nativo
opt-in). Futures (`fapi.binance.com`) é lido **apenas** para funding rate /
open interest como sentimento — não é o mercado de execução.

## Diagrama

```mermaid
flowchart TD
    subgraph ENTRADA
        MAIN[main.py — orquestrador<br/>threads: WS×2 + loop/par + retreino + relatório]
    end
    subgraph TEMPO_REAL[Dados em tempo real]
        WS[WebSocket @aggTrade<br/>→ CVD BTC]
        WSD[WebSocket @depth<br/>→ OBI suavizado]
    end
    subgraph SINAL[Geração de sinal por par]
        OTM[estrategias/otimizada.py<br/>8 filtros + MTF + VWAP]
        SCORE[score.py — 0-100<br/>10 componentes, incl. OBI]
        REG[regime.py]
        FG[fear_greed.py]
        ENS[ensemble.py<br/>XGBoost + MLP]
        SUP[suporte.py — ScaleIn 3 parcelas]
        KLI[(data/klines.py<br/>cache TTL compartilhado)]
    end
    subgraph EXEC[Execução e Risco]
        RISCO[risco.py — Kelly + circuit breaker<br/>+ drawdown + CVaR de cauda]
        EXE[executor.py — LONG + trailing stop<br/>+ locking + reconciliação de boot]
    end
    subgraph DADOS[Persistência e Observabilidade]
        DB[(database.py<br/>SQLite dev / Supabase prod)]
        LOG[logger.py]
        TG[telegram_bot.py]
        HEALTH[health.py /metrics /health]
        DASH[dashboard.py — Flask]
    end

    MAIN --> WS --> DB
    MAIN --> WSD
    MAIN --> OTM
    OTM --> SCORE --> REG & FG
    OTM --> ENS
    OTM --> SUP
    OTM -.-> KLI
    REG -.-> KLI
    SUP -.-> KLI
    OTM --> RISCO --> EXE --> DB
    MAIN --> HEALTH
    EXE --> TG
    RISCO --> TG
    EXE --> HEALTH
    RISCO --> HEALTH
    DB --> DASH

    classDef ok fill:#0d2a1a,stroke:#34d399,color:#fff;
    class RISCO,EXE,DB ok;
```

## Camadas e notas
- **Orquestração** → [[Core e Execucao]]
- **Sinal / ML** → [[ML e Sinais]]
- **Persistência / observabilidade** → [[Dados e Infra]]
- **Validação histórica** → [[Estrategias e Backtesting]]
- **Operação** → [[Deploy Supabase]], [[Deploy VPS]]

## Decisões arquiteturais relevantes
1. **Long-only** hoje: a estratégia gera sinais de VENDA, mas o `executor` só
   abre LONG; a VENDA é ignorada explicitamente (ver [[Core e Execucao]]).
2. **Dois backends de dados** via a mesma fachada `database.py` (SQLite local,
   Postgres/Supabase em produção) — ver [[Dados e Infra]].
3. **Paper trading é o padrão** (`DRY_RUN=true`); ordens reais exigem
   `ALLOW_REAL_TRADING=true` **e** a flag `--real` na linha de comando
   (defesa em profundidade — o `.env` sozinho não basta).
4. **Deploy como serviço 24/7**: Windows NSSM (PC) ou systemd na VPS
   (`deploy/`) + Supabase. Docker/Railway/GCP foram removidos do repo
   (ver [[Deploy VPS]]).
5. **Proteção pós-entrada em camadas**: `STOP_LOSS_LIMIT` puro por padrão
   (sobrevive a crash do bot); bracket OCO nativo (stop+alvo atômico) e
   trailing server-side são opt-in (`OCO_BRACKET`/`OCO_TRAILING_DELTA_BIPS`)
   — ativar só após validar em paper. O `executor.py` usa `RLock` (não
   `Lock`) porque `fechar_posicao`/`_aplicar_novo_stop` podem ser chamados
   de dentro do próprio monitor, que já segura o lock.
6. **Reconciliação de boot é opt-in** (`RECONCILIAR_BOOT_EXCHANGE`): por
   padrão, um restart confia cegamente no que está persistido no banco;
   ligada, cruza com o estado real da Binance (saldo/ordens abertas/
   histórico de trades) antes de religar o monitor — detecta posição órfã
   ou já fechada fora do bot.
