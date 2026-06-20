"""
Script de Teste do Núcleo Refatorado — BotBinance
==================================================
Testa o event loop e WebSocket client de forma isolada.

Uso:
  python scripts/test_core.py
"""

import asyncio
from core.event_loop import run_application
from config.di_container import DIContainer


async def test_websocket():
    """Testa WebSocket por 10 segundos."""
    container = DIContainer()
    ws_client = container.websocket_client()

    # Criar task para WebSocket
    task = asyncio.create_task(ws_client.run())

    # Aguardar 10 segundos
    await asyncio.sleep(10)

    # Parar
    ws_client.stop()
    await task


if __name__ == "__main__":
    print("Testando núcleo refatorado...")
    asyncio.run(test_websocket())
    print("Teste concluído.")