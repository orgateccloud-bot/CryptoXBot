"""
Event Loop Principal — BotBinance Core
========================================
Ponto de entrada assíncrono puro (asyncio).
Gerencia o ciclo de vida da aplicação HFT: inicialização, execução e shutdown graceful.

Design Patterns:
- Event-Driven: Async generators para streams.
- DI: Injeção de dependências via container.
- Graceful Shutdown: Sinal handlers para cleanup.

Uso:
  from core.event_loop import run_application
  asyncio.run(run_application())
"""

import asyncio
import signal
import logging
from typing import Optional
from config.di_container import DIContainer
from infra.logging import get_logger
from infra.metrics import start_metrics_server, update_memory_metric

logger = get_logger(__name__)


class Application:
    """
    Classe principal da aplicação, gerenciando lifecycle async.
    """
    def __init__(self, container: DIContainer):
        self.container = container
        self.tasks: set[asyncio.Task] = set()
        self.shutdown_event = asyncio.Event()

    async def initialize(self):
        """Inicializa componentes core (WebSocket, Database, OrderManager, etc.)."""
        logger.info("Inicializando aplicação BotBinance...")

        # Inicializar servidor de métricas
        start_metrics_server(port=8000)
        logger.info("Servidor de métricas iniciado na porta 8000")

        # Inicializar database
        db = self.container.database()
        await db.initialize()

        # Inicializar order manager
        order_manager = self.container.order_manager()
        await order_manager.initialize()

        # Outras inicializações...
        logger.info("Aplicação inicializada com sucesso.")

    async def run(self):
        """Loop principal da aplicação."""
        logger.info("Iniciando loop principal...")

        # Criar tasks principais
        websocket_task = asyncio.create_task(self._run_websocket())
        signal_executor_task = asyncio.create_task(self._run_signal_executor())
        metrics_task = asyncio.create_task(self._run_metrics_updater())

        self.tasks.add(websocket_task)
        self.tasks.add(signal_executor_task)
        self.tasks.add(metrics_task)

        # Aguardar shutdown
        await self.shutdown_event.wait()

        # Cancelar tasks
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

        logger.info("Loop principal encerrado.")

    async def _run_signal_executor(self):
        """Executa o signal executor (placeholder por enquanto)."""
        signal_executor = self.container.signal_executor()
        logger.info("Signal executor running...")

        # Placeholder: simular processamento de sinais
        while not self.shutdown_event.is_set():
            await asyncio.sleep(1)  # verificar sinais a cada segundo
            # Aqui integrar com pubsub para receber sinais

    async def _run_metrics_updater(self):
        """Atualiza métricas periodicamente."""
        while not self.shutdown_event.is_set():
            update_memory_metric()
            await asyncio.sleep(5)  # atualizar a cada 5 segundos

    async def shutdown(self):
        """Shutdown graceful."""
        logger.info("Iniciando shutdown graceful...")
        self.shutdown_event.set()

    def handle_signal(self, signum, frame):
        """Handler para sinais do sistema (SIGINT, SIGTERM)."""
        logger.info(f"Sinal {signum} recebido, iniciando shutdown...")
        asyncio.create_task(self.shutdown())


async def run_application(container: Optional[DIContainer] = None) -> None:
    """
    Função principal para executar a aplicação.
    """
    if container is None:
        container = DIContainer()

    app = Application(container)

    # Configurar sinais
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, app.handle_signal, sig, None)

    try:
        await app.initialize()
        await app.run()
    except Exception as e:
        logger.error(f"Erro crítico na aplicação: {e}")
        raise
    finally:
        logger.info("Aplicação encerrada.")