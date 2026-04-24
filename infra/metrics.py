"""
Métricas de Observabilidade para BotBinance HFT
===========================================
Utiliza prometheus_client para expor métricas de performance em tempo real.
"""

from prometheus_client import Histogram, Gauge, start_http_server
import psutil
import time
from typing import Optional

# Métricas de Performance HFT
TICK_PROCESSING_LATENCY = Histogram(
    'botbinance_tick_processing_latency_seconds',
    'Latência de processamento do tick (WebSocket -> Engine)',
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
)

CVD_CALCULATION_TIME = Histogram(
    'botbinance_cvd_calculation_time_seconds',
    'Tempo de execução do cálculo do CVD',
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
)

MEMORY_USAGE = Gauge(
    'botbinance_memory_usage_bytes',
    'Consumo de Memória RAM do container'
)

class MetricsCollector:
    """Coletor de métricas para HFT"""

    def __init__(self, port: int = 8000):
        self.port = port
        self._started = False

    def start_server(self):
        """Inicia servidor HTTP para exposição das métricas"""
        if not self._started:
            start_http_server(self.port)
            self._started = True

    def update_memory_usage(self):
        """Atualiza métrica de uso de memória"""
        process = psutil.Process()
        memory_info = process.memory_info()
        MEMORY_USAGE.set(memory_info.rss)

    def record_tick_latency(self, latency_seconds: float):
        """Registra latência de processamento do tick"""
        TICK_PROCESSING_LATENCY.observe(latency_seconds)

    def record_cvd_time(self, calculation_time_seconds: float):
        """Registra tempo de cálculo do CVD"""
        CVD_CALCULATION_TIME.observe(calculation_time_seconds)

# Instância global do coletor
metrics_collector = MetricsCollector()

# Funções de conveniência para uso no código
def start_metrics_server(port: int = 8000):
    """Inicia servidor de métricas"""
    metrics_collector.port = port
    metrics_collector.start_server()

def update_memory_metric():
    """Atualiza métrica de memória"""
    metrics_collector.update_memory_usage()

def record_tick_latency(latency: float):
    """Registra latência do tick"""
    metrics_collector.record_tick_latency(latency)

def record_cvd_calculation_time(time_seconds: float):
    """Registra tempo de cálculo CVD"""
    metrics_collector.record_cvd_time(time_seconds)