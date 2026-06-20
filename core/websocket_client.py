"""
WebSocket Client Assíncrono — BotBinance Core
===============================================
Cliente WebSocket resiliente para Binance Futures.
Implementa retry exponencial, state management e processamento async de ticks.

Features:
- Async puro com websockets library.
- Backoff exponencial com jitter.
- State manager para deduplicação de trades.
- Logging estruturado com latência.
- Integração com PubSub para cloud-native.

Uso:
  from core.websocket_client import WebSocketClient
  client = WebSocketClient(symbol="BTCUSDT")
  await client.run()
"""

import asyncio
import websockets
import json
import time
import random
import os
from typing import Dict, Any, AsyncGenerator
from config.runtime_settings import MIN_BTC_VOLUME, SYMBOL_WS, WHALE_BTC_VOLUME
from infra.logging import get_logger
from infra.pubsub_client import publish_tick
from data.stream_processor import process_tick

logger = get_logger(__name__)


class WebSocketClient:
    """
    Cliente WebSocket assíncrono com resiliência.
    """
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.url = f"wss://fstream.binance.com/ws/{symbol.lower()}@aggTrade"
        self.state = WebSocketState()
        self._running = True

    async def run(self):
        """Loop principal com retry."""
        max_retries = 10
        base_delay = 1.0
        max_delay = 300.0
        jitter_factor = 0.1
        attempt = 0

        while self._running and attempt < max_retries:
            try:
                async with websockets.connect(self.url, ping_interval=30, ping_timeout=10) as ws:
                    self.state.connected = True
                    self.state.last_message_time = time.time()
                    logger.info("WebSocket conectado", extra={
                        "symbol": self.symbol,
                        "attempt": attempt
                    })

                    # Chaos testing: simular falhas se flag estiver presente
                    chaos_enabled = os.getenv("CHAOS_TESTING", "false").lower() == "true"
                    if chaos_enabled:
                        await self._inject_chaos_failure(ws)

                    async for message in self._message_generator(ws):
                        if not self._running:
                            break
                        await self._process_message(message)

            except (websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.WebSocketException,
                    asyncio.TimeoutError) as e:
                self.state.connected = False
                latency = (time.time() - self.state.last_message_time) * 1000
                self.state.latency_ms = latency

                # Backoff exponencial com jitter
                delay = min(base_delay * (2 ** attempt), max_delay)
                jitter = random.uniform(-jitter_factor * delay, jitter_factor * delay)
                delay += jitter
                delay = max(0.1, delay)

                logger.warning("WebSocket desconectado", extra={
                    "error": str(e),
                    "symbol": self.symbol,
                    "attempt": attempt,
                    "latency_ms": latency,
                    "next_retry_in_s": delay
                })

                await asyncio.sleep(delay)
                attempt += 1

            except Exception as e:
                logger.error("Erro crítico WebSocket", extra={
                    "error": str(e),
                    "symbol": self.symbol,
                    "attempt": attempt
                })
                attempt += 1
                await asyncio.sleep(1.0)

        logger.critical("Máximo de tentativas atingido", extra={
            "symbol": self.symbol,
            "max_retries": max_retries
        })

    async def _message_generator(self, ws) -> AsyncGenerator[str, None]:
        """Generator para mensagens WebSocket."""
        async for message in ws:
            yield message

    async def _process_message(self, message: str):
        """Processa mensagem e atualiza state."""
        try:
            data = json.loads(message)
            trade_id = int(data["t"])

            # Deduplicação
            if trade_id <= self.state.last_trade_id:
                return

            self.state.last_trade_id = trade_id
            self.state.last_message_time = time.time()

            # Criar tick
            tick = {
                "trade_id": trade_id,
                "price": float(data["p"]),
                "quantity": float(data["q"]),
                "is_buyer_maker": data["m"],
                "timestamp": int(data["T"])
            }

            # Publicar no PubSub (cloud-native)
            await publish_tick(tick)

            # Processar localmente (CVD, etc.)
            await process_tick(tick)

        except Exception as e:
            logger.error("Erro processando mensagem WebSocket", extra={
                "error": str(e),
                "symbol": self.symbol
            })

    async def _inject_chaos_failure(self, ws):
        """
        Injeta falhas aleatórias para teste de caos.
        Simula desconexões, timeouts e erros de rede.
        """
        # Probabilidades de falha (configuráveis via env)
        connection_drop_prob = float(os.getenv("CHAOS_CONNECTION_DROP_PROB", "0.1"))
        timeout_prob = float(os.getenv("CHAOS_TIMEOUT_PROB", "0.05"))
        network_error_prob = float(os.getenv("CHAOS_NETWORK_ERROR_PROB", "0.02"))

        # Aguardar tempo aleatório antes de injetar falha
        chaos_delay = random.uniform(5.0, 30.0)  # 5-30 segundos
        await asyncio.sleep(chaos_delay)

        # Escolher tipo de falha baseado em probabilidades
        rand = random.random()
        if rand < connection_drop_prob:
            logger.warning("CHAOS: Simulando queda de conexão", extra={
                "chaos_type": "connection_drop",
                "delay_before_failure": chaos_delay
            })
            # Forçar fechamento da conexão
            await ws.close(code=1006, reason="Chaos testing: connection drop")

        elif rand < connection_drop_prob + timeout_prob:
            logger.warning("CHAOS: Simulando timeout", extra={
                "chaos_type": "timeout",
                "delay_before_failure": chaos_delay
            })
            # Simular timeout aguardando mais tempo que o ping_timeout
            await asyncio.sleep(15.0)  # Timeout maior que ping_timeout=10

        elif rand < connection_drop_prob + timeout_prob + network_error_prob:
            logger.warning("CHAOS: Simulando erro de rede", extra={
                "chaos_type": "network_error",
                "delay_before_failure": chaos_delay
            })
            # Levantar uma exceção de rede
            raise websockets.exceptions.ConnectionClosedError(None, None, "Chaos testing: network error")

    def stop(self):
        """Para o cliente."""
        self._running = False


class WebSocketState:
    """
    State manager para WebSocket.
    """
    def __init__(self):
        self.last_trade_id: int = 0
        self.connected: bool = False
        self.last_message_time: float = 0.0
        self.latency_ms: float = 0.0
