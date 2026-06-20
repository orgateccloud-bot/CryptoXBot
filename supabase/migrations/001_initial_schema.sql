-- BinanceXBot — Schema inicial Supabase / Postgres
-- Equivalente às tabelas criadas por database._inicializar_postgres()
-- Idempotente: seguro para rodar múltiplas vezes (CREATE IF NOT EXISTS)

-- ────────────────────────────────────────────────────────────────
-- trades: cada trade >= 0.1 BTC recebido via WebSocket aggTrade
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
    preco       DOUBLE PRECISION NOT NULL,
    volume_btc  DOUBLE PRECISION NOT NULL,
    volume_usdt DOUBLE PRECISION NOT NULL,
    direcao     TEXT NOT NULL,          -- 'COMPRA' | 'VENDA'
    eh_baleia   BOOLEAN DEFAULT false,
    trade_id    BIGINT,                 -- ID Binance para deduplicação
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp
    ON trades(symbol, timestamp DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id
    ON trades(trade_id) WHERE trade_id IS NOT NULL;

-- ────────────────────────────────────────────────────────────────
-- snapshots_mercado: estado de indicadores a cada ciclo de sinal
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS snapshots_mercado (
    id                    BIGSERIAL PRIMARY KEY,
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol                TEXT NOT NULL DEFAULT 'BTCUSDT',
    preco                 DOUBLE PRECISION,
    variacao_24h          DOUBLE PRECISION,
    volume_24h_btc        DOUBLE PRECISION,
    funding_rate          DOUBLE PRECISION,
    open_interest_btc     DOUBLE PRECISION,
    ema20_1h              DOUBLE PRECISION,
    ema50_1h              DOUBLE PRECISION,
    rsi_1h                DOUBLE PRECISION,
    tendencia             TEXT,
    pressao_order_book    TEXT,
    liquidez_compra_usdt  DOUBLE PRECISION,
    liquidez_venda_usdt   DOUBLE PRECISION,
    raw_payload           JSONB          -- payload completo para auditoria
);

CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_timestamp
    ON snapshots_mercado(symbol, timestamp DESC);

-- ────────────────────────────────────────────────────────────────
-- cvd_historico: CVD acumulado por sessão WebSocket
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cvd_historico (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol      TEXT NOT NULL DEFAULT 'BTCUSDT',
    cvd         DOUBLE PRECISION NOT NULL,
    compras_btc DOUBLE PRECISION NOT NULL,
    vendas_btc  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cvd_symbol_timestamp
    ON cvd_historico(symbol, timestamp DESC);

-- ────────────────────────────────────────────────────────────────
-- sinais: cada sinal gerado pelo bot (COMPRA / VENDA / AGUARDAR)
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sinais (
    id           BIGSERIAL PRIMARY KEY,
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol       TEXT NOT NULL DEFAULT 'BTCUSDT',
    tipo         TEXT NOT NULL,         -- 'COMPRA' | 'VENDA' | 'AGUARDAR'
    preco        DOUBLE PRECISION NOT NULL,
    motivo       TEXT,
    score        DOUBLE PRECISION,      -- score 0-100
    source       TEXT,                  -- 'otimizada' | 'ema_rsi_cvd'
    executado    BOOLEAN DEFAULT false,
    executado_em TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sinais_symbol_timestamp
    ON sinais(symbol, timestamp DESC);

-- ────────────────────────────────────────────────────────────────
-- risk_state: estado persistido do gestor de risco
-- Permite restart sem perder drawdown diário ou bloqueios
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_state (
    name       TEXT PRIMARY KEY,        -- 'default' para estado global
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    data       JSONB NOT NULL           -- drawdown, posicoes, bloqueio, dia
);

-- ────────────────────────────────────────────────────────────────
-- bot_events: auditoria operacional — erros, alertas, circuit breaker
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_events (
    id         BIGSERIAL PRIMARY KEY,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
    service    TEXT,                    -- 'worker' | 'dashboard'
    symbol     TEXT,
    event_type TEXT NOT NULL,
    severity   TEXT DEFAULT 'INFO',     -- 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
    message    TEXT NOT NULL,
    data       JSONB
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp
    ON bot_events(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_events_severity
    ON bot_events(severity, timestamp DESC);
