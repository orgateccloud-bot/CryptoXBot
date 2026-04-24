# Project Guidelines

## Code Style
- Use Python 3.11+ with asyncio for low-latency operations.
- Indicators return pure Python lists, no pandas (see [indicadores.py](indicadores.py)).
- API calls use timeout=8s, return dicts with lists (see [estrategias/otimizada.py](estrategias/otimizada.py)).
- Thread-safe access to shared state with `threading.Lock()` (see `main.py`).

## Architecture
BotBinance is a high-frequency trading bot for Binance Futures (BTC/ETH/SOL) using AI ensemble (XGBoost + MLP).
- **WebSocket Thread**: Real-time CVD calculation.
- **Strategy Thread**: Signal evaluation every N minutes.
- **Executor Thread**: Order management with trailing stop.
- **Dashboard Thread**: Flask/SocketIO for monitoring.
Key components: [executor.py](executor.py) (orders), [score.py](score.py) (unified scoring), [ensemble.py](ensemble.py) (ML voting).

## Build and Test
```bash
pip install -r requirements.txt
python testar_api.py  # Validate API keys
python main.py --simulacao  # Safe paper trading
python main.py --backtest 1h  # Historical testing
```
No formal tests; validate with backtesting.

## Conventions
- **Score System**: 0-100 unified score (9 components); >=70 full size, 60-69 half size, <60 wait.
- **Precision**: Use `_arredondar_qty()` for each pair's qty_step (see [executor.py](executor.py)).
- **Database**: SQLite multi-thread with `check_same_thread=False`.
- **ML Models**: Retrain weekly: `python ml_filtro.py --treinar`.
- **Pitfalls**: Always use `_lock` for shared state; handle WebSocket crashes with retry; API keys in settings.py risk exposure—use .env or settings.local.json.

See [deploy/DEPLOY.md](deploy/DEPLOY.md) for deployment details.</content>
<parameter name="filePath">C:\Users\Veloso\BotBinance\.github\copilot-instructions.md