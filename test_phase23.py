"""
Teste rápido para Phase 2.3: Execution Layer
"""
import asyncio
from config.di_container import DIContainer
from execution.order_manager import OrderSide, OrderType
from execution.signal_executor import SignalExecutor, TradingSignal, SignalType

async def test_order_manager():
    """Teste Order Manager."""
    print("Testing Order Manager...")

    container = DIContainer()
    order_manager = container.order_manager()

    await order_manager.initialize()

    # Testar ordem de compra
    order = await order_manager.place_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.001,
        order_type=OrderType.MARKET
    )

    if order:
        print(f"Order placed: {order.order_id} {order.side.value} {order.quantity}")
    else:
        print("Order rejected")

    # Aguardar processamento
    await asyncio.sleep(0.1)

    # Verificar posição
    position = await order_manager.get_position("BTCUSDT")
    if position:
        print(f"Position: qty={position.quantity:.6f} avg_price={position.avg_price:.2f}")

    await order_manager.close()

async def test_signal_executor():
    """Teste Signal Executor."""
    print("\nTesting Signal Executor...")

    container = DIContainer()
    signal_executor = container.signal_executor()

    # Simular sinal
    signal = TradingSignal(
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        confidence=0.8,
        score_result={
            "decisao": "OPERAR_CHEIO",
            "tamanho_fator": 1.0,
            "score_total": 85
        },
        timestamp=int(asyncio.get_event_loop().time() * 1000),
        price=50000.0,
        features={"rsi": 30.0, "atr": 500.0}
    )

    # Processar sinal
    success = await signal_executor.process_signal(signal)
    print(f"Signal processed: {success}")

async def main():
    await test_order_manager()
    await test_signal_executor()
    print("\nPhase 2.3 validation complete!")

if __name__ == "__main__":
    asyncio.run(main())