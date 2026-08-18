"""
Testes — Melhorias de IA (Score, retreino, relatorio diario)
=========================================================
  * score.py   — pesos do score (ML em 20%)
  * main.py    — retreino automático e relatório diário

M-1: as classes de FSRS saíram quando o módulo foi removido (2026-07-21) e
as de Ollama foram para `_legado/test_melhorias_ollama.py` junto com
`ai/ollama_client.py`.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════════════════════
# 1. Score — peso ML
# ══════════════════════════════════════════════════════════════


class TestScorePesos(unittest.TestCase):
    """Verifica que os pesos do score somam 100 e ML=20."""

    def setUp(self):
        import score

        self.score = score
        self.PESOS = score.PESOS

    def test_peso_ml_igual_20(self):
        self.assertEqual(self.PESOS["ml"], 20, "Peso do ML deve ser 20% após melhoria")

    def test_soma_pesos_igual_100(self):
        total = sum(self.PESOS.values())
        self.assertEqual(total, 100, f"Pesos devem somar 100, soma atual: {total}")

    def test_regime_e_maior_ou_igual_15(self):
        self.assertGreaterEqual(
            self.PESOS["regime"], 15, "Regime deve manter peso relevante (>= 15%)"
        )

    def test_ml_e_maior_componente_ou_empatado(self):
        maior = max(self.PESOS.values())
        self.assertGreaterEqual(
            self.PESOS["ml"], maior * 0.95, "ML deve ser o maior componente ou empatado com o maior"
        )

    def test_todos_pesos_positivos(self):
        for nome, peso in self.PESOS.items():
            self.assertGreater(peso, 0, f"Peso de '{nome}' deve ser > 0")

    def test_score_ml_function(self):
        """_score_ml deve retornar 0-100."""
        score_ml = self.score._score_ml
        self.assertEqual(score_ml(None), 50)  # sem modelo = neutro
        self.assertEqual(score_ml(0.5), 50)
        self.assertEqual(score_ml(1.0), 100)
        self.assertEqual(score_ml(0.0), 0)
        self.assertLessEqual(score_ml(0.7, "LATERAL"), 80)  # penaliza em lateral


# ══════════════════════════════════════════════════════════════
# 5. Retreinamento automático (main.py)
# ══════════════════════════════════════════════════════════════


class TestRetreinamentoAutomatico(unittest.TestCase):
    """Verifica funções de retreinamento no main.py."""

    def test_constantes_retreinamento(self):
        import main

        self.assertEqual(main._RETREINAMENTO_DIA, 6, "_RETREINAMENTO_DIA deve ser 6 (domingo)")
        self.assertEqual(main._RETREINAMENTO_HORA, 2, "_RETREINAMENTO_HORA deve ser 2 (02:00)")

    def test_iniciar_retreinamento_cria_thread(self):
        import threading

        import main

        threads_antes = {t.name for t in threading.enumerate()}
        main.iniciar_retreinamento_automatico(["BTCUSDT"])
        time.sleep(0.2)
        threads_depois = {t.name for t in threading.enumerate()}
        novas = threads_depois - threads_antes
        self.assertIn("retrain-weekly", novas, "Thread 'retrain-weekly' deve ser criada")

    def test_retreinar_nao_crasha_sem_dados(self):
        """_retreinar_modelos não deve lançar exceção mesmo sem dados no banco."""
        import main

        try:
            main._retreinar_modelos(["BTCUSDT"])
        except SystemExit:
            pass
        except Exception as e:
            self.fail(f"_retreinar_modelos lançou exceção inesperada: {e}")


class TestRelatorioDiarioAutomatico(unittest.TestCase):
    """P2-5: relatorio_diario existia pronto em telegram_bot.py mas nunca era
    chamado -- verifica a thread de agendamento adicionada em main.py."""

    def test_constante_relatorio(self):
        import main

        self.assertEqual(main._RELATORIO_HORA, 18, "_RELATORIO_HORA deve ser 18 (18h)")

    def test_iniciar_relatorio_diario_cria_thread(self):
        import threading

        import main

        threads_antes = {t.name for t in threading.enumerate()}
        main.iniciar_relatorio_diario("BTCUSDT")
        time.sleep(0.2)
        threads_depois = {t.name for t in threading.enumerate()}
        novas = threads_depois - threads_antes
        self.assertIn("relatorio-diario", novas, "Thread 'relatorio-diario' deve ser criada")

    def test_dados_relatorio_diario_retorna_campos_esperados(self):
        from logger import logger as logger_singleton

        d = logger_singleton.dados_relatorio_diario("BTCUSDT")
        for campo in (
            "hoje",
            "avaliacoes",
            "score_medio",
            "sinais_gerados",
            "trades_dia",
            "ganhos_dia",
            "win_rate",
            "pnl_usdt",
            "pnl_pct",
        ):
            self.assertIn(campo, d, f"Campo '{campo}' ausente em dados_relatorio_diario")

    def test_dados_relatorio_diario_sem_trades_win_rate_none(self):
        """Sem nenhum trade fechado hoje, win_rate e None — 0/0 nao e 0%.
        (Ate 2026-08-18 devolvia 0.0 e o alerta das 18h imprimia
        'Win Rate: 0.0%', afirmando derrota total sem nenhuma medicao.)"""
        from logger import logger as logger_singleton

        d = logger_singleton.dados_relatorio_diario("SYMBOL_INEXISTENTE_XYZ")
        self.assertEqual(d["trades_dia"], 0)
        self.assertIsNone(d["win_rate"])

    def test_relatorio_telegram_honesto_sem_trades_e_sem_saldo(self):
        """A mensagem das 18h nunca inventa numero: win_rate None vira '—',
        erro de saldo vira 'sem leitura' (nunca $0.00), marca e CryptoXbot."""
        import telegram_bot

        capturadas = []
        original = telegram_bot._enviar
        telegram_bot._enviar = lambda msg: capturadas.append(msg) or True
        try:
            telegram_bot.relatorio_diario(0.0, 0, None, None, saldo_erro="timeout na API")
        finally:
            telegram_bot._enviar = original
        msg = capturadas[0]
        self.assertIn("CryptoXbot", msg)
        self.assertNotIn("BotBinance", msg)
        self.assertIn("Win Rate:</b> —", msg)
        self.assertIn("sem leitura", msg)
        self.assertNotIn("$0.00\n", msg.split("Saldo")[1])

    def test_relatorio_telegram_com_dados_reais_formata_numeros(self):
        import telegram_bot

        capturadas = []
        original = telegram_bot._enviar
        telegram_bot._enviar = lambda msg: capturadas.append(msg) or True
        try:
            telegram_bot.relatorio_diario(12.5, 4, 350.0, 75.0)
        finally:
            telegram_bot._enviar = original
        msg = capturadas[0]
        self.assertIn("+$12.50", msg)
        self.assertIn("Win Rate:</b> 75.0%", msg)
        self.assertIn("$350.00", msg)


# ══════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Testes das melhorias de IA")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    verbosity = 2 if args.verbose else 1

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    # M-1: a lista citava `TestFSRS` e `TestEnsembleFSRS` (o FSRS saiu do repo
    # em 2026-07-21) e as duas classes de Ollama, que foram para `_legado/`
    # junto com o módulo. Este bloco já estava quebrado — ninguém rodava o
    # arquivo direto, porque o pytest coleta as classes sozinho e ignora o
    # `__main__`.
    for cls in [
        TestScorePesos,
        TestRetreinamentoAutomatico,
        TestRelatorioDiarioAutomatico,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    print(f"\n{'='*50}")
    print(
        f"  {result.testsRun} testes | "
        f"{len(result.failures)} falhas | "
        f"{len(result.errors)} erros"
    )
    if result.wasSuccessful():
        print("  RESULTADO: OK")
    else:
        print("  RESULTADO: FALHOU")
    print("=" * 50)

    sys.exit(0 if result.wasSuccessful() else 1)
