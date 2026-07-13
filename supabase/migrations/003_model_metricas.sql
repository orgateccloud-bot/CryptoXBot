-- BinanceXBot — P1-4: guard-rail de drift no retreino automatico (sem MLflow)
-- Historico de AUC por retreino (symbol + modelo_tipo), usado por
-- validacao.detectar_drift() para alertar (via bot_events) quando um
-- retreino semanal produz um modelo pior que o historico recente --
-- ver database.salvar_metricas_modelo / historico_cv_auc_modelo.
-- Idempotente: seguro para rodar múltiplas vezes.

CREATE TABLE IF NOT EXISTS model_metricas (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol      TEXT NOT NULL,
    modelo_tipo TEXT NOT NULL,
    auc         DOUBLE PRECISION,
    cv_auc_mean DOUBLE PRECISION,
    cv_auc_std  DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_model_metricas_symbol_tipo
    ON model_metricas(symbol, modelo_tipo, timestamp DESC);
