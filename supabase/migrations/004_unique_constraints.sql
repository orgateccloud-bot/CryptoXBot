-- 004 — UNIQUE que torna o ON CONFLICT do migrador REAL (I-13)
-- =============================================================
-- STATUS: PROPOSTA. Preparada em 2026-08-12; NAO aplicada em lugar nenhum.
-- Aplicar e decisao do operador — este arquivo existe para que a decisao
-- seja um "aplica" de uma palavra, com a evidencia ja medida.
--
-- POR QUE: o ON CONFLICT DO NOTHING de snapshots_mercado, cvd_historico,
-- sinais e bot_events nao faz nada hoje — a unica chave unica dessas tabelas
-- e o id BIGSERIAL, que nunca conflita. A idempotencia do migrador vem de
-- uma guarda de aplicacao (_tabelas_ocupadas). Estas constraints movem a
-- garantia para o BANCO, que e onde ela pertence.
--
-- EVIDENCIA (medida em 2026-08-12, sobre 4,5 meses de dados reais):
--   snapshots_mercado (symbol, timestamp)   0 grupos duplicados em 2.843
--   sinais            (symbol, timestamp)   0 grupos duplicados em 5.339
--   bot_events        (timestamp, event_type) 0 grupos duplicados em 34
--   trades            FICA DE FORA: 11.178 colisoes legitimas na chave
--                     composta (aggTrades repetem preco/volume no mesmo
--                     microssegundo). A dedupe dela continua sendo o indice
--                     parcial idx_trades_trade_id (58% das linhas tem
--                     trade_id NULL pos-purga — limitacao conhecida).
--
-- CHECKLIST DO "APLICA" (tudo junto, senao fica pela metade):
--   1. Rodar este arquivo no Postgres de destino.
--   2. Espelhar os tres indices em database.py:_inicializar_postgres, para
--      que um banco criado do zero nasca igual ao migrado.
--   3. Adicionar ON CONFLICT DO NOTHING aos escritores salvar_snapshot,
--      salvar_cvd e salvar_sinal (colisao = escrita duplicada, e suprimir e
--      o comportamento certo; salvar_sinal com RETURNING devolve None no
--      conflito e os chamadores ja tratam None).
--   4. Ampliar mig.IDEMPOTENTES para incluir as tres tabelas — o teste
--      test_schema_nao_ganhou_unique_sem_atualizar_IDEMPOTENTES vai COBRAR
--      isso assim que o passo 2 for feito.

CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_symbol_ts
    ON snapshots_mercado (symbol, timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cvd_symbol_ts
    ON cvd_historico (symbol, timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sinais_symbol_ts
    ON sinais (symbol, timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_ts_tipo
    ON bot_events (timestamp, event_type);
