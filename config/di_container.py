"""
Dependency Injection Container — BotBinance Config
===================================================
Container para injeção de dependências usando dependency-injector.
Centraliza criação e lifecycle de componentes.

Padrões:
- Singleton para recursos compartilhados (DB, Logger).
- Factory para instâncias por request (WebSocket por symbol).

Uso:
  from config.di_container import DIContainer
  container = DIContainer()
  ws_client = container.websocket_client()
"""

from dependency_injector import containers, providers
from core.websocket_client import WebSocketClient
from infra.database import Database
from infra.logging import get_logger
from infra.pubsub_client import PubSubClient
from execution.order_manager import OrderManager
from execution.signal_executor import SignalExecutor


class DIContainer(containers.DeclarativeContainer):
    """
    Container de DI para toda a aplicação.
    """

    # Configurações
    config = providers.Configuration()

    # Infraestrutura (Singleton)
    logger = providers.Singleton(get_logger)
    database = providers.Singleton(Database)
    pubsub_client = providers.Singleton(PubSubClient)

    # Core (Factory para flexibilidade)
    websocket_client = providers.Factory(
        WebSocketClient,
        symbol="BTCUSDT"  # parâmetro fixo por enquanto
    )

    # Data
    # Adicionar conforme refatoração

    # AI
    # Adicionar conforme refatoração

    # Execution
    order_manager = providers.Singleton(
        OrderManager,
        api_key=config.api_key,
        api_secret=config.api_secret,
        testnet=config.testnet
    )

    signal_executor = providers.Singleton(
        SignalExecutor,
        order_manager=order_manager,
        score_calculator=providers.Object(None)  # será definido depois
    )