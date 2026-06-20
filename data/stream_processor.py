"""
Stream Processor — BotBinance Data
===================================
Processamento assíncrono de streams de ticks.
Calcula CVD em tempo real de forma vetorizada.

Features:
- Buffer circular para ticks recentes.
- Cálculo CVD incremental.
- Event emission para subscribers.

Uso:
  from data.stream_processor import process_tick
  await process_tick(tick)
"""

import asyncio
from collections import deque
from typing import Dict, Any, Deque
import numpy as np
import time
from infra.logging import get_logger
from infra.metrics import record_tick_latency, record_cvd_calculation_time

logger = get_logger(__name__)


class StreamProcessor:
    """Processador de streams com buffer e CVD."""

    def __init__(self, buffer_size: int = 1000):
        self.buffer: Deque[Dict[str, Any]] = deque(maxlen=buffer_size)
        self.cvd = 0.0
        self._subscribers = []

    async def process_tick(self, tick: Dict[str, Any]):
        """Processa tick e atualiza CVD."""
        start_time = time.time()

        self.buffer.append(tick)

        # Atualizar CVD com medição de tempo
        cvd_start = time.time()
        quantity = tick["quantity"]
        if tick["is_buyer_maker"]:
            self.cvd -= quantity  # Venda
        else:
            self.cvd += quantity  # Compra
        cvd_time = time.time() - cvd_start
        record_cvd_calculation_time(cvd_time)

        # Emitir evento para subscribers
        await self._notify_subscribers(tick)

        # Registrar latência total do processamento
        total_latency = time.time() - start_time
        record_tick_latency(total_latency)

    def get_recent_ticks(self, n: int = 100) -> list:
        """Retorna últimos N ticks."""
        return list(self.buffer)[-n:]

    def get_cvd(self) -> float:
        """Retorna CVD atual."""
        return self.cvd

    def subscribe(self, callback):
        """Adiciona subscriber."""
        self._subscribers.append(callback)

    async def _notify_subscribers(self, tick: Dict[str, Any]):
        """Notifica subscribers."""
        for callback in self._subscribers:
            try:
                await callback(tick)
            except Exception as e:
                logger.error("Erro em subscriber", extra={"error": str(e)})


# Instância global
_processor = StreamProcessor()


async def process_tick(tick: Dict[str, Any]):
    """Função global para processar tick."""
    await _processor.process_tick(tick)


def get_stream_processor() -> StreamProcessor:
    """Retorna processador global."""
    return _processor