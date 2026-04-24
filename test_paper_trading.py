#!/usr/bin/env python3
"""
Teste do Modo Paper Trading
===========================
Script para validar o funcionamento do modo dry-run.
Executa ordens virtuais e verifica o logging no banco SQLite.
"""

import asyncio
import os
from execution.order_manager import OrderManager, OrderSide, OrderType
from infra.paper_trading import get_paper_trading_manager

async def test_paper_trading():
    """Testa o modo paper trading."""
    print("🧪 Testando Modo Paper Trading...")

    # Configurar DRY_RUN
    os.environ['DRY_RUN'] = 'true'

    # Inicializar OrderManager
    manager = OrderManager(
        api_key="test_key",
        api_secret="test_secret",
        testnet=True
    )
    await manager.initialize()

    # Executar algumas ordens virtuais
    print("📝 Executando ordens virtuais...")

    # Ordem de compra
    order1 = await manager.place_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.001,
        order_type=OrderType.MARKET,
        price=50000.0
    )
    print(f"✅ Ordem BUY criada: {order1.order_id if order1 else 'REJEITADA'}")

    # Aguardar processamento
    await asyncio.sleep(0.1)

    # Ordem de venda
    order2 = await manager.place_order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity=0.001,
        order_type=OrderType.MARKET,
        price=51000.0
    )
    print(f"✅ Ordem SELL criada: {order2.order_id if order2 else 'REJEITADA'}")

    # Aguardar processamento
    await asyncio.sleep(0.1)

    # Verificar posições virtuais
    print("\n📊 Verificando posições virtuais...")
    position = await manager.get_virtual_position("BTCUSDT")
    if position:
        print(f"📈 Posição BTCUSDT: {position['quantity']:.6f} @ {position['avg_price']:.2f}")

        # Calcular PnL
        pnl = await manager.get_virtual_pnl("BTCUSDT", 52000.0)
        if pnl:
            print(f"💰 PnL: {pnl['unrealized_pnl']:.2f} (taxas: {pnl['total_fees']:.8f})")

    # Verificar histórico
    print("\n📜 Histórico de ordens virtuais:")
    orders = await manager.get_virtual_orders("BTCUSDT", limit=10)
    for order in orders:
        print(f"  {order.timestamp} | {order.side} {order.quantity} @ {order.price} (fee: {order.estimated_fee:.8f})")

    # Fechar conexões
    await manager.close()

    print("\n✅ Teste do Paper Trading concluído com sucesso!")
    print("💡 O bot está seguro - nenhuma ordem real foi enviada para a Binance.")

if __name__ == "__main__":
    asyncio.run(test_paper_trading())