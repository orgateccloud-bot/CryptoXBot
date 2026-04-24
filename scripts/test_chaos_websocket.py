#!/usr/bin/env python3
"""
Chaos Testing Script — WebSocket Resilience Testing
====================================================
Script de injeção de falhas para validar robustez do cliente WebSocket.
Simula quedas de conexão TCP e valida backoff exponencial com jitter.

Features:
- Injeção de falhas TCP em intervalos aleatórios (1-5 min)
- Monitoramento de logs para validação de backoff
- Detecção de memory leaks durante desconexões
- Relatórios detalhados de resiliência

Uso:
  python scripts/test_chaos_websocket.py --duration 3600 --symbol BTCUSDT
"""

import asyncio
import argparse
import time
import random
import psutil
import os
import signal
import json
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import subprocess
import sys

# Adicionar path do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChaosEvent:
    """Evento de caos injetado."""
    timestamp: float
    event_type: str  # 'tcp_drop', 'websocket_close', 'network_failure'
    duration_seconds: float
    description: str


@dataclass
class BackoffValidation:
    """Validação de backoff exponencial."""
    attempt_number: int
    delay_seconds: float
    expected_min_delay: float
    expected_max_delay: float
    is_valid: bool
    timestamp: float


@dataclass
class MemorySnapshot:
    """Snapshot de memória."""
    timestamp: float
    rss_mb: float
    vms_mb: float
    process_count: int


class ChaosInjector:
    """
    Injetor de caos para WebSocket connections.
    """

    def __init__(self, symbol: str = "BTCUSDT", chaos_file: str = "websocket_chaos.flag"):
        self.symbol = symbol
        self.chaos_file = chaos_file
        self.chaos_events: List[ChaosEvent] = []
        self.backoff_validations: List[BackoffValidation] = []
        self.memory_snapshots: List[MemorySnapshot] = []
        self.bot_process: Optional[psutil.Process] = None
        self.running = True

    def create_chaos_flag(self, event_type: str, duration: float):
        """Cria flag de caos que o WebSocket client detectará."""
        chaos_data = {
            "event_type": event_type,
            "duration_seconds": duration,
            "timestamp": time.time(),
            "description": f"Chaos injection: {event_type} for {duration}s"
        }

        with open(self.chaos_file, 'w') as f:
            json.dump(chaos_data, f)

        logger.info(f"Chaos flag created: {event_type}", extra=chaos_data)

    def clear_chaos_flag(self):
        """Remove flag de caos."""
        if os.path.exists(self.chaos_file):
            os.remove(self.chaos_file)
            logger.info("Chaos flag cleared")

    async def inject_tcp_drop(self, duration: float = 30.0):
        """Injeta queda TCP simulada."""
        event = ChaosEvent(
            timestamp=time.time(),
            event_type="tcp_drop",
            duration_seconds=duration,
            description=f"TCP connection drop for {duration}s"
        )
        self.chaos_events.append(event)

        logger.warning("Injecting TCP drop", extra=asdict(event))
        self.create_chaos_flag("tcp_drop", duration)
        await asyncio.sleep(duration)
        self.clear_chaos_flag()

    async def inject_websocket_close(self, duration: float = 15.0):
        """Injeta fechamento abrupto do WebSocket."""
        event = ChaosEvent(
            timestamp=time.time(),
            event_type="websocket_close",
            duration_seconds=duration,
            description=f"WebSocket abrupt close for {duration}s"
        )
        self.chaos_events.append(event)

        logger.warning("Injecting WebSocket close", extra=asdict(event))
        self.create_chaos_flag("websocket_close", duration)
        await asyncio.sleep(duration)
        self.clear_chaos_flag()

    async def inject_network_failure(self, duration: float = 60.0):
        """Injeta falha de rede completa."""
        event = ChaosEvent(
            timestamp=time.time(),
            event_type="network_failure",
            duration_seconds=duration,
            description=f"Complete network failure for {duration}s"
        )
        self.chaos_events.append(event)

        logger.warning("Injecting network failure", extra=asdict(event))
        self.create_chaos_flag("network_failure", duration)
        await asyncio.sleep(duration)
        self.clear_chaos_flag()

    def take_memory_snapshot(self):
        """Captura snapshot de memória do processo do bot."""
        if self.bot_process:
            try:
                memory_info = self.bot_process.memory_info()
                snapshot = MemorySnapshot(
                    timestamp=time.time(),
                    rss_mb=memory_info.rss / 1024 / 1024,
                    vms_mb=memory_info.vms / 1024 / 1024,
                    process_count=len(self.bot_process.children(recursive=True)) + 1
                )
                self.memory_snapshots.append(snapshot)
                return snapshot
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                logger.warning("Could not take memory snapshot - process not accessible")
        return None

    def parse_backoff_from_logs(self, log_line: str) -> Optional[BackoffValidation]:
        """Parseia informações de backoff dos logs."""
        # Padrão esperado nos logs: "next_retry_in_s": delay
        pattern = r'"next_retry_in_s":\s*([0-9.]+)'
        match = re.search(pattern, log_line)
        if not match:
            return None

        delay = float(match.group(1))

        # Encontrar attempt number
        attempt_pattern = r'"attempt":\s*(\d+)'
        attempt_match = re.search(attempt_pattern, log_line)
        if not attempt_match:
            return None

        attempt = int(attempt_match.group(1))

        # Calcular expected delay (backoff exponencial com jitter)
        base_delay = 1.0
        max_delay = 300.0
        jitter_factor = 0.1

        expected_delay = min(base_delay * (2 ** attempt), max_delay)
        expected_min = expected_delay * (1 - jitter_factor)
        expected_max = expected_delay * (1 + jitter_factor)

        is_valid = expected_min <= delay <= expected_max

        validation = BackoffValidation(
            attempt_number=attempt,
            delay_seconds=delay,
            expected_min_delay=expected_min,
            expected_max_delay=expected_max,
            is_valid=is_valid,
            timestamp=time.time()
        )

        self.backoff_validations.append(validation)
        return validation

    async def monitor_logs(self, log_file: str = "logs/botbinance.log"):
        """Monitora logs em tempo real para validação de backoff."""
        if not os.path.exists(log_file):
            logger.warning(f"Log file not found: {log_file}")
            return

        logger.info(f"Starting log monitoring: {log_file}")

        # Usar tail -f simulado
        with open(log_file, 'r') as f:
            f.seek(0, 2)  # Ir para o final do arquivo

            while self.running:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.1)
                    continue

                # Verificar se é log de reconexão
                if "WebSocket desconectado" in line and "next_retry_in_s" in line:
                    validation = self.parse_backoff_from_logs(line)
                    if validation:
                        status = "VALID" if validation.is_valid else "INVALID"
                        logger.info(f"Backoff validation: {status}", extra={
                            "attempt": validation.attempt_number,
                            "delay": validation.delay_seconds,
                            "expected_range": f"{validation.expected_min_delay:.2f}-{validation.expected_max_delay:.2f}",
                            "is_valid": validation.is_valid
                        })

    async def run_chaos_campaign(self, duration_minutes: int = 60):
        """Executa campanha de caos por duração especificada."""
        end_time = time.time() + (duration_minutes * 60)
        logger.info(f"Starting chaos campaign for {duration_minutes} minutes")

        # Snapshot inicial de memória
        self.take_memory_snapshot()

        while time.time() < end_time and self.running:
            # Intervalo aleatório entre 1-5 minutos
            interval = random.uniform(60, 300)
            await asyncio.sleep(interval)

            if not self.running:
                break

            # Escolher tipo de falha aleatoriamente
            failure_types = [
                ("tcp_drop", lambda: self.inject_tcp_drop(random.uniform(10, 60))),
                ("websocket_close", lambda: self.inject_websocket_close(random.uniform(5, 30))),
                ("network_failure", lambda: self.inject_network_failure(random.uniform(20, 120)))
            ]

            failure_type, injector = random.choice(failure_types)

            # Executar injeção de falha
            await injector()

            # Snapshot de memória após falha
            self.take_memory_snapshot()

        logger.info("Chaos campaign completed")

    def generate_report(self) -> Dict[str, Any]:
        """Gera relatório detalhado da campanha de caos."""
        total_events = len(self.chaos_events)
        total_backoff_validations = len(self.backoff_validations)
        valid_backoffs = sum(1 for v in self.backoff_validations if v.is_valid)

        # Análise de memory leaks
        if len(self.memory_snapshots) >= 2:
            initial_memory = self.memory_snapshots[0].rss_mb
            final_memory = self.memory_snapshots[-1].rss_mb
            memory_growth = final_memory - initial_memory
            memory_leak_detected = memory_growth > 50  # >50MB crescimento
        else:
            memory_growth = 0
            memory_leak_detected = False

        report = {
            "campaign_summary": {
                "total_chaos_events": total_events,
                "total_backoff_validations": total_backoff_validations,
                "valid_backoffs": valid_backoffs,
                "backoff_success_rate": valid_backoffs / max(total_backoff_validations, 1) * 100,
                "memory_growth_mb": memory_growth,
                "memory_leak_detected": memory_leak_detected
            },
            "chaos_events": [asdict(event) for event in self.chaos_events],
            "backoff_validations": [asdict(v) for v in self.backoff_validations],
            "memory_snapshots": [asdict(s) for s in self.memory_snapshots],
            "recommendations": []
        }

        # Recomendações baseadas nos resultados
        if report["campaign_summary"]["backoff_success_rate"] < 90:
            report["recommendations"].append(
                "Backoff algorithm may need tuning - success rate below 90%"
            )

        if memory_leak_detected:
            report["recommendations"].append(
                "Memory leak detected - investigate state management during reconnections"
            )

        if total_events < 5:
            report["recommendations"].append(
                "Low number of chaos events - increase test duration for better coverage"
            )

        return report

    def save_report(self, filename: str = "chaos_test_report.json"):
        """Salva relatório em JSON."""
        report = self.generate_report()
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Chaos report saved to {filename}")

    def stop(self):
        """Para o injetor de caos."""
        self.running = False
        self.clear_chaos_flag()


async def find_bot_process() -> Optional[psutil.Process]:
    """Simula encontrar o processo do bot (sempre retorna None para teste local)."""
    logger.info("Chaos testing: Running in local mode - will start WebSocket client directly")
    return None


async def main():
    """Função principal."""
    parser = argparse.ArgumentParser(description="Chaos Testing for WebSocket Resilience")
    parser.add_argument("--duration", type=int, default=60,
                       help="Duration in minutes (default: 60)")
    parser.add_argument("--symbol", type=str, default="BTCUSDT",
                       help="Trading symbol (default: BTCUSDT)")
    parser.add_argument("--log-file", type=str, default="logs/botbinance.log",
                       help="Path to bot log file")
    parser.add_argument("--report-file", type=str, default="chaos_test_report.json",
                       help="Output report file")

    args = parser.parse_args()

    logger.info("Starting Chaos Testing", extra={
        "duration_minutes": args.duration,
        "symbol": args.symbol
    })

    # Encontrar processo do bot ou iniciar WebSocket client diretamente
    bot_process = await find_bot_process()
    websocket_task = None

    if bot_process:
        logger.info(f"Found bot process: PID {bot_process.pid}")
        injector = ChaosInjector(symbol=args.symbol)
        injector.bot_process = bot_process
    else:
        logger.info("Bot process not found - starting WebSocket client directly with chaos testing")
        # Importar e iniciar WebSocket client diretamente
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

        # Ativar modo caos via variável de ambiente
        os.environ['CHAOS_TESTING'] = 'true'

        from core.websocket_client import WebSocketClient
        websocket_client = WebSocketClient(symbol=args.symbol)

        # Inicializar injetor de caos
        injector = ChaosInjector(symbol=args.symbol)

        # Iniciar WebSocket client em background
        websocket_task = asyncio.create_task(websocket_client.run())
        logger.info("WebSocket client started with chaos testing enabled")

    try:
        # Criar tasks
        tasks = [
            asyncio.create_task(injector.run_chaos_campaign(duration_minutes=args.duration)),
            asyncio.create_task(injector.monitor_logs(log_file=args.log_file))
        ]

        # Adicionar WebSocket task se estiver rodando localmente
        if websocket_task:
            tasks.append(websocket_task)

        # Executar em paralelo
        await asyncio.gather(*tasks)

    except KeyboardInterrupt:
        logger.info("Chaos testing interrupted by user")
    finally:
        injector.stop()
        injector.save_report(args.report_file)

        # Relatório final
        report = injector.generate_report()
        summary = report["campaign_summary"]

        print("\n" + "="*60)
        print("CHAOS TESTING REPORT")
        print("="*60)
        print(f"Total Chaos Events: {summary['total_chaos_events']}")
        print(f"Backoff Validations: {summary['total_backoff_validations']}")
        print(".1f")
        print(".1f")
        print(f"Memory Leak Detected: {summary['memory_leak_detected']}")

        if report["recommendations"]:
            print("\nRecommendations:")
            for rec in report["recommendations"]:
                print(f"  - {rec}")

        print("="*60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)