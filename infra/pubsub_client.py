"""
PubSub Client — BotBinance Infra
==================================
Cliente para Google Cloud PubSub.
Permite decoupling de streams para escalabilidade cloud-native.

Features:
- Publish de ticks para tópicos.
- Subscribe para processamento distribuído.
- Async puro.

Uso:
  from infra.pubsub_client import publish_tick
  await publish_tick(tick)
"""

import asyncio
from typing import Dict, Any
from infra.logging import get_logger

logger = get_logger(__name__)

# TODO: Implementar com google-cloud-pubsub quando GCP estiver configurado
# from google.cloud import pubsub_v1

class PubSubClient:
    """Cliente PubSub (stub para desenvolvimento local)."""

    def __init__(self):
        self.topics = {}

    async def publish(self, topic: str, data: Dict[str, Any]):
        """Publica mensagem em tópico."""
        # Stub: apenas log
        logger.info("PubSub publish", extra={
            "topic": topic,
            "data": data
        })

    async def subscribe(self, topic: str, callback):
        """Inscreve em tópico."""
        # Stub
        pass


# Instância global
_client = PubSubClient()


async def publish_tick(tick: Dict[str, Any]):
    """Publica tick no PubSub."""
    await _client.publish("ticks", tick)