"""
Test Runner — Suite Completa
============================
Executa todos os testes e gera relatório de cobertura.
"""

import unittest
import sys
import time
from pathlib import Path

# Adicionar diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Importar todos os testes
from tests.test_data import TestCVDCalculator, TestIndicadores
from tests.test_ai import TestAIInference, TestPredictionResult
from tests.test_execution import TestOrderManager, TestRiskManager, TestSignalExecutor
from tests.test_integration import TestEndToEnd, TestPerformance
from tests.test_melhorias import (TestScorePesos, TestFSRS, TestEnsembleFSRS,
                                   TestOllamaCliente, TestRetreinamentoAutomatico)


def run_tests():
    """Executar suite completa de testes."""
    print("=" * 60)
    print("  BOTBINANCE TEST SUITE")
    print("=" * 60)

    # Criar test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Adicionar test cases
    test_classes = [
        TestCVDCalculator, TestIndicadores,
        TestAIInference, TestPredictionResult,
        TestOrderManager, TestRiskManager, TestSignalExecutor,
        TestEndToEnd, TestPerformance,
        # Melhorias de IA
        TestScorePesos, TestFSRS, TestEnsembleFSRS,
        TestOllamaCliente, TestRetreinamentoAutomatico,
    ]

    for test_class in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    # Executar testes
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    start_time = time.time()

    print(f"\nExecutando {suite.countTestCases()} testes...\n")

    result = runner.run(suite)

    end_time = time.time()
    duration = end_time - start_time

    # Relatório final
    print("\n" + "=" * 60)
    print("  RESULTADO DOS TESTES")
    print("=" * 60)

    print(f"Tempo total: {duration:.2f}s")
    print(f"Testes executados: {result.testsRun}")
    print(f"Sucessos: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Falhas: {len(result.failures)}")
    print(f"Erros: {len(result.errors)}")

    if result.failures:
        print(f"\n🚨 FALHAS:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback.strip()}")

    if result.errors:
        print(f"\n💥 ERROS:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback.strip()}")

    # Status final
    if result.wasSuccessful():
        print("\n✅ TODOS OS TESTES PASSARAM!")
        print("🎯 Sistema pronto para produção!")
        return 0
    else:
        print(f"\n❌ {len(result.failures) + len(result.errors)} testes falharam!")
        print("🔧 Corrigir falhas antes de prosseguir.")
        return 1


def run_performance_benchmarks():
    """Executar apenas benchmarks de performance."""
    print("\n" + "=" * 40)
    print("  PERFORMANCE BENCHMARKS")
    print("=" * 40)

    # Importar e executar testes de performance
    from tests.test_integration import TestPerformance

    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestPerformance))

    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--performance':
        success = run_performance_benchmarks()
    else:
        success = run_tests()

    sys.exit(0 if success else 1)