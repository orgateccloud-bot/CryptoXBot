-- BinanceXBot — P1-3: instrumentação de meta-labeling
-- Liga a linha de ENTRADA em `sinais` ao resultado do trade (preenchido por
-- executor.fechar_posicao no fechamento final), dando a sinais_executados()/
-- kelly_do_banco() e a um futuro dataset de meta-labeling um PnL real e a
-- barreira efetivamente tocada (em vez de inferir "ganho/perda" pelo sinal
-- do PnL, que hoje conflava trailing-stop-em-lucro com take-profit real).
-- Idempotente: seguro para rodar múltiplas vezes.

ALTER TABLE sinais ADD COLUMN IF NOT EXISTS preco_saida DOUBLE PRECISION;
ALTER TABLE sinais ADD COLUMN IF NOT EXISTS pnl_usdt DOUBLE PRECISION;
ALTER TABLE sinais ADD COLUMN IF NOT EXISTS pnl_pct DOUBLE PRECISION;
ALTER TABLE sinais ADD COLUMN IF NOT EXISTS barreira_tocada TEXT;
