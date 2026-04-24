"""
Structured Logging — BotBinance Infra
=======================================
Logging estruturado compatível com GCP Cloud Logging.
Usa JSON format com campos extras para tracing e métricas.

Features:
- Structured logs (JSON).
- Níveis configuráveis.
- Integração GCP (opcional).
- Async-safe.

Uso:
  from infra.logging import get_logger
  logger = get_logger(__name__)
  logger.info("Mensagem", extra={"key": "value"})
"""

import logging
import json
import sys
from typing import Dict, Any
from config.settings import LOG_LEVEL


class StructuredFormatter(logging.Formatter):
    """Formatter JSON para logs estruturados."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Adicionar campos extras
        if hasattr(record, 'extra') and record.extra:
            log_entry.update(record.extra)

        # Adicionar exception se houver
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_logging():
    """Configura logging global."""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # Remover handlers existentes
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Handler para stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)

    # TODO: Adicionar handler GCP Cloud Logging
    # from google.cloud import logging as cloud_logging
    # client = cloud_logging.Client()
    # handler = client.get_default_handler()
    # logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Retorna logger configurado."""
    return logging.getLogger(name)


# Inicializar na importação
setup_logging()