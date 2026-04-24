"""
Parâmetros Otimizados por Par — BotBinance
============================================
Gerado via Grid Search (144-8000 combinações por par, 2 anos de dados 1H).

Cada par tem suas próprias características de volatilidade e liquidez,
exigindo parâmetros distintos para maximizar Sharpe e controlar Drawdown.

Fonte:
  BTC: Grid Search  144 combinações → Stop 1.5%/Target 5.0% — Sharpe 3.24
  ETH: Grid Search 8000 combinações → Stop 2.0%/Target 6.0% — Sharpe 1.79
  SOL: Grid Search 8000 combinações → Stop 3.0%/Target 5.0% — Sharpe 2.98
"""

PARAMS_PARES = {
    "BTCUSDT": {
        "stop_pct":     0.015,   # 1.5%
        "target_pct":   0.050,   # 5.0%
        "rsi_min":      42,
        "rsi_max":      60,
        "score_operar": 60,
        "score_cheio":  70,
        # Resultados grid search:
        # 185 trades, WR 33.5%, Ret +57.6%, DD 8.98%, Sharpe 3.24
    },
    "ETHUSDT": {
        "stop_pct":     0.020,   # 2.0%
        "target_pct":   0.060,   # 6.0%
        "rsi_min":      38,
        "rsi_max":      62,
        "score_operar": 50,
        "score_cheio":  65,
        # Resultados grid search:
        # 282 trades, WR 30.9%, Ret +35.9%, DD 14.4%, Sharpe 1.79
    },
    "SOLUSDT": {
        "stop_pct":     0.030,   # 3.0%  (SOL mais volátil, stop maior)
        "target_pct":   0.050,   # 5.0%
        "rsi_min":      38,
        "rsi_max":      60,
        "score_operar": 50,
        "score_cheio":  65,
        # Resultados grid search:
        # 343 trades, WR 47.5%, Ret +122.0%, DD 13.4%, Sharpe 2.98
    },
}

# Parâmetros default (fallback para pares não configurados)
PARAMS_DEFAULT = PARAMS_PARES["BTCUSDT"]


def get_params(symbol: str) -> dict:
    """Retorna os parâmetros otimizados para o par. Fallback para BTC se não configurado."""
    return PARAMS_PARES.get(symbol.upper(), PARAMS_DEFAULT)
