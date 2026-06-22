"""
Testes — Melhorias de IA (FSRS, Ensemble, Score, Ollama)
=========================================================
Cobre as 5 melhorias implementadas:
  1. score.py        — peso ML elevado para 20%
  2. fsrs_trading.py — filtro adaptativo FSRS
  3. ensemble.py     — integração FSRS no ensemble
  4. ollama_client   — cliente Ollama (gemma3:4b)
  5. main.py         — funções de retreinamento automático
"""

import sys
import os
import unittest
import json
import tempfile
import time

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
# 2. FSRS — filtro adaptativo
# ══════════════════════════════════════════════════════════════


class TestFSRS(unittest.TestCase):
    """Testes do FSRSFiltro — comportamento adaptativo."""

    def setUp(self):
        from fsrs_trading import FSRSFiltro

        # Usa arquivo temporário para não poluir dados reais
        self.tmp = tempfile.mktemp(suffix=".json")
        self.fsrs = FSRSFiltro(db_path=self.tmp)

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    # ── Padrão desconhecido ──────────────────────────────────

    def test_fator_neutro_sem_historico(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.004,
            "cvd_score": 70,
            "vol_rel": 1.5,
        }
        fator = self.fsrs.avaliar(features)
        self.assertEqual(fator, 0.5, "Padrão desconhecido deve retornar 0.5")

    def test_quantizar_features_retorna_string(self):
        features = {
            "regime": "LATERAL",
            "rsi": 42,
            "dist_ema20": -0.001,
            "cvd_score": 40,
            "vol_rel": 0.8,
        }
        pid, desc = self.fsrs.quantizar_features(features)
        self.assertIsInstance(pid, str)
        self.assertIn("LATERAL", pid)
        self.assertIn("|", pid)

    # ── Registro e aprendizado ──────────────────────────────

    def test_fator_aumenta_apos_lucros(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 60,
            "dist_ema20": 0.005,
            "cvd_score": 72,
            "vol_rel": 1.6,
        }
        for _ in range(5):
            self.fsrs.registrar_resultado(features, lucro_pct=0.02)
        fator = self.fsrs.avaliar(features)
        self.assertGreater(fator, 0.5, "Fator deve aumentar após lucros consecutivos")

    def test_fator_diminui_apos_perdas(self):
        features = {
            "regime": "LATERAL",
            "rsi": 50,
            "dist_ema20": 0.001,
            "cvd_score": 50,
            "vol_rel": 0.9,
        }
        for _ in range(4):
            self.fsrs.registrar_resultado(features, lucro_pct=-0.015)
        fator = self.fsrs.avaliar(features)
        self.assertLess(fator, 0.5, "Fator deve diminuir após perdas consecutivas")

    def test_fator_entre_0_e_1(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.004,
            "cvd_score": 68,
            "vol_rel": 1.4,
        }
        # Mistura lucros e perdas
        self.fsrs.registrar_resultado(features, lucro_pct=0.018)
        self.fsrs.registrar_resultado(features, lucro_pct=-0.012)
        self.fsrs.registrar_resultado(features, lucro_pct=0.025)
        fator = self.fsrs.avaliar(features)
        self.assertGreaterEqual(fator, 0.0)
        self.assertLessEqual(fator, 1.0)

    def test_padroes_diferentes_sao_independentes(self):
        feat_alta = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 60,
            "dist_ema20": 0.005,
            "cvd_score": 75,
            "vol_rel": 1.8,
        }
        feat_lateral = {
            "regime": "LATERAL",
            "rsi": 50,
            "dist_ema20": 0.0,
            "cvd_score": 50,
            "vol_rel": 0.9,
        }

        # Alta: muitos lucros
        for _ in range(5):
            self.fsrs.registrar_resultado(feat_alta, lucro_pct=0.02)
        # Lateral: muitas perdas
        for _ in range(4):
            self.fsrs.registrar_resultado(feat_lateral, lucro_pct=-0.015)

        fator_alta = self.fsrs.avaliar(feat_alta)
        fator_lateral = self.fsrs.avaliar(feat_lateral)
        self.assertGreater(
            fator_alta,
            fator_lateral,
            "Padrão com lucros deve ter fator maior que padrão com perdas",
        )

    # ── Persistência ─────────────────────────────────────────

    def test_persiste_em_json(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.004,
            "cvd_score": 70,
            "vol_rel": 1.5,
        }
        self.fsrs.registrar_resultado(features, lucro_pct=0.02)
        # Criar novo objeto lendo o mesmo arquivo
        from fsrs_trading import FSRSFiltro

        fsrs2 = FSRSFiltro(db_path=self.tmp)
        fator = fsrs2.avaliar(features)
        self.assertGreater(fator, 0.5, "Dados devem persistir entre instâncias")

    def test_json_valido_no_disco(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 60,
            "dist_ema20": 0.005,
            "cvd_score": 72,
            "vol_rel": 1.6,
        }
        self.fsrs.registrar_resultado(features, lucro_pct=0.018)
        with open(self.tmp) as f:
            dados = json.load(f)
        self.assertIsInstance(dados, dict)
        self.assertGreater(len(dados), 0)
        # Verificar campos obrigatórios
        for v in dados.values():
            self.assertIn("estabilidade", v)
            self.assertIn("dificuldade", v)
            self.assertIn("n_reviews", v)

    # ── Detalhes ─────────────────────────────────────────────

    def test_avaliar_com_detalhe_sem_historico(self):
        features = {
            "regime": "INDEF",
            "rsi": 50,
            "dist_ema20": 0.0,
            "cvd_score": 50,
            "vol_rel": 1.0,
        }
        d = self.fsrs.avaliar_com_detalhe(features)
        self.assertEqual(d["fator_confianca"], 0.5)
        self.assertEqual(d["status"], "DESCONHECIDO")
        self.assertEqual(d["n_reviews"], 0)

    def test_avaliar_com_detalhe_apos_trades(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 62,
            "dist_ema20": 0.006,
            "cvd_score": 78,
            "vol_rel": 1.9,
        }
        for _ in range(6):
            self.fsrs.registrar_resultado(features, lucro_pct=0.02)

        d = self.fsrs.avaliar_com_detalhe(features)
        self.assertEqual(d["n_reviews"], 6)
        self.assertEqual(d["taxa_acerto_pct"], 100.0)
        self.assertEqual(d["status"], "CONFIAVEL")

    def test_n_lucros_n_perdas_corretos(self):
        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.004,
            "cvd_score": 70,
            "vol_rel": 1.5,
        }
        self.fsrs.registrar_resultado(features, lucro_pct=0.015)
        self.fsrs.registrar_resultado(features, lucro_pct=-0.012)
        self.fsrs.registrar_resultado(features, lucro_pct=0.022)

        pid, _ = self.fsrs.quantizar_features(features)
        p = self.fsrs.padroes[pid]
        self.assertEqual(p.n_reviews, 3)
        self.assertEqual(p.n_lucros, 2)
        self.assertEqual(p.n_perdas, 1)


# ══════════════════════════════════════════════════════════════
# 3. Ensemble — integração FSRS
# ══════════════════════════════════════════════════════════════


class TestEnsembleFSRS(unittest.TestCase):
    """Testes da integração FSRS no ensemble."""

    def test_fsrs_disponivel_no_ensemble(self):
        import ensemble

        self.assertTrue(
            ensemble._FSRS_DISPONIVEL, "_FSRS_DISPONIVEL deve ser True (fsrs_trading.py presente)"
        )

    def test_prever_retorna_fator_fsrs(self):
        """prever() deve retornar chave fator_fsrs."""
        import ensemble

        result = ensemble.prever()
        self.assertIn("fator_fsrs", result, "Resultado do ensemble deve conter 'fator_fsrs'")
        self.assertGreaterEqual(result["fator_fsrs"], 0.0)
        self.assertLessEqual(result["fator_fsrs"], 1.0)

    def test_prever_retorna_fsrs_detalhe(self):
        """prever() deve retornar fsrs_detalhe quando features fornecidas."""
        import ensemble

        features = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.004,
            "cvd_score": 70,
            "vol_rel": 1.5,
        }
        result = ensemble.prever(features_fsrs=features)
        self.assertIn("fsrs_detalhe", result)
        self.assertIsInstance(result["fsrs_detalhe"], dict)

    def test_fator_fsrs_neutro_sem_features(self):
        """Sem features_fsrs, fator deve ser 0.5 (neutro)."""
        import ensemble

        result = ensemble.prever(features_fsrs=None)
        self.assertEqual(result["fator_fsrs"], 0.5, "Sem features_fsrs, fator FSRS deve ser 0.5")

    def test_estrutura_resultado_ensemble(self):
        """Verifica que todos os campos obrigatórios estão presentes."""
        import ensemble

        result = ensemble.prever()
        campos = [
            "prob_ensemble",
            "prob_xgb",
            "prob_lstm",
            "concordancia",
            "confianca",
            "pode_operar",
            "motivo",
            "regime",
            "pesos_aplicados",
            "fator_fsrs",
            "fsrs_detalhe",
        ]
        for campo in campos:
            self.assertIn(campo, result, f"Campo '{campo}' ausente no resultado do ensemble")

    def test_pesos_aplicados_somam_um(self):
        """Pesos XGB + LSTM devem somar 1.0."""
        import ensemble

        result = ensemble.prever()
        pesos = result["pesos_aplicados"]
        soma = round(pesos["xgb"] + pesos["lstm"], 2)
        self.assertEqual(soma, 1.0, f"Pesos XGB+LSTM devem somar 1.0, soma: {soma}")

    def test_ajustes_por_regime(self):
        """Ajustes de regime devem alterar pesos."""
        import ensemble

        r_alta = ensemble.prever(regime_atual="TENDENCIA_ALTA")
        r_lateral = ensemble.prever(regime_atual="LATERAL")
        # Em lateral, XGB deve ter peso maior
        self.assertGreater(
            r_lateral["pesos_aplicados"]["xgb"],
            r_alta["pesos_aplicados"]["xgb"],
            "Em mercado lateral, XGB deve ter peso maior que em tendência",
        )


# ══════════════════════════════════════════════════════════════
# 4. Ollama Client
# ══════════════════════════════════════════════════════════════


def _ollama_rodando():
    """Probe seguro: True se o servidor Ollama responde, False em qualquer falha.

    Avaliado em tempo de coleta pelo @skipUnless; NUNCA pode lançar exceção
    (senão a coleta inteira do pytest aborta quando o Ollama não está rodando).
    """
    try:
        import requests

        return requests.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


class TestOllamaCliente(unittest.TestCase):
    """Testes do OllamaCliente — com e sem servidor ativo."""

    def setUp(self):
        from ai.ollama_client import OllamaCliente, MODELO_RAPIDO, MODELO_ANALITICO

        self.OllamaCliente = OllamaCliente
        self.MODELO_RAPIDO = MODELO_RAPIDO
        self.MODELO_ANALITICO = MODELO_ANALITICO

    def test_modelos_configurados_corretos(self):
        from ai.ollama_client import MODELO_RAPIDO, MODELO_ANALITICO

        self.assertEqual(
            MODELO_RAPIDO, "gemma3:4b", "Modelo rápido deve ser gemma3:4b (3.2GB, cabe na VRAM)"
        )
        self.assertEqual(
            MODELO_ANALITICO, "llama3:latest", "Modelo analítico deve ser llama3:latest (4.4GB)"
        )

    def test_inicializacao(self):
        c = self.OllamaCliente()
        self.assertEqual(c.modelo, self.MODELO_RAPIDO)
        self.assertIsNone(c._disponivel)
        self.assertFalse(c._modelo_carregado)

    def test_fallback_analisar_mercado_sem_ollama(self):
        """Sem Ollama, deve retornar mensagem de fallback (não lançar exceção)."""
        c = self.OllamaCliente(modelo="modelo-inexistente-xyz")
        c._disponivel = False  # forçar modo offline
        resultado = c.analisar_mercado(regime="TENDENCIA_ALTA", score=72, prob_ml=0.67, preco=85000)
        self.assertIsInstance(resultado, str)
        self.assertGreater(len(resultado), 0)
        self.assertIn("72", resultado)  # score deve aparecer no fallback

    def test_fallback_classificar_noticia_sem_ollama(self):
        """Sem Ollama, deve retornar dict com NEUTRO."""
        c = self.OllamaCliente(modelo="modelo-inexistente-xyz")
        c._disponivel = False
        resultado = c.classificar_noticia("Bitcoin sobe 10% hoje")
        self.assertIsInstance(resultado, dict)
        self.assertIn("sentimento", resultado)
        self.assertIn("impacto", resultado)
        self.assertIn("motivo", resultado)
        self.assertEqual(resultado["sentimento"], "NEUTRO")

    def test_fallback_explicar_sinal_sem_ollama(self):
        c = self.OllamaCliente(modelo="modelo-inexistente-xyz")
        c._disponivel = False
        resultado = c.explicar_sinal(
            sinal="COMPRA",
            score=74,
            componentes={"ml": 18, "regime": 16},
            prob_ml=0.68,
            regime="TENDENCIA_ALTA",
        )
        self.assertIsInstance(resultado, str)
        self.assertIn("COMPRA", resultado)

    @unittest.skipUnless(_ollama_rodando(), "Ollama não está rodando")
    def test_ollama_disponivel_com_gemma3(self):
        """Teste real: verifica que gemma3:4b está disponível."""
        c = self.OllamaCliente(modelo="gemma3:4b")
        self.assertTrue(c.esta_disponivel())

    def test_singleton_get_ollama(self):
        from ai.ollama_client import get_ollama

        c1 = get_ollama()
        c2 = get_ollama()
        self.assertIs(c1, c2, "get_ollama() deve retornar a mesma instância")


class TestOllamaIntegrado(unittest.TestCase):
    """
    Testes de integração reais com Ollama.
    Só executam se o servidor estiver ativo.
    """

    _ollama_ativo = None

    @classmethod
    def setUpClass(cls):
        try:
            import requests

            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            cls._ollama_ativo = r.status_code == 200
        except Exception:
            cls._ollama_ativo = False

    def setUp(self):
        if not self._ollama_ativo:
            self.skipTest("Ollama não está rodando — pulando testes de integração")
        from ai.ollama_client import OllamaCliente

        self.cliente = OllamaCliente(modelo="gemma3:4b")

    def test_classificar_noticia_bullish(self):
        resultado = self.cliente.classificar_noticia(
            "Bitcoin ETF aprovado pela SEC, entrada de capital institucional recorde"
        )
        self.assertIn(resultado["sentimento"], ["BULLISH", "NEUTRO"])
        self.assertIn(resultado["impacto"], ["ALTO", "MEDIO", "BAIXO"])

    def test_classificar_noticia_bearish(self):
        resultado = self.cliente.classificar_noticia(
            "Governo proíbe Bitcoin, exchanges fecham, preço despenca 40%"
        )
        self.assertIn(resultado["sentimento"], ["BEARISH", "NEUTRO"])

    def test_analisar_mercado_retorna_texto(self):
        resultado = self.cliente.analisar_mercado(
            regime="TENDENCIA_ALTA", score=74, prob_ml=0.67, preco=85000, rsi=58
        )
        self.assertIsInstance(resultado, str)
        self.assertGreater(len(resultado), 20, "Análise deve ter mais de 20 caracteres")

    def test_explicar_sinal_retorna_texto(self):
        resultado = self.cliente.explicar_sinal(
            sinal="COMPRA",
            score=74,
            componentes={"ml": 18.0, "regime": 16.0, "cvd": 14.0},
            prob_ml=0.68,
            regime="TENDENCIA_ALTA",
        )
        self.assertIsInstance(resultado, str)
        self.assertGreater(len(resultado), 10)

    def test_latencia_gemma3_4b(self):
        """Após carregamento, latência deve ser < 15s."""
        self.cliente.warmup()  # garante modelo na VRAM
        inicio = time.time()
        self.cliente._gerar("Responda: OK", max_tokens=5)
        latencia = time.time() - inicio
        self.assertLess(
            latencia, 15.0, f"Latência {latencia:.1f}s acima do esperado (<15s) para gemma3:4b"
        )


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


# ══════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Testes das melhorias de IA")
    parser.add_argument(
        "--ollama",
        action="store_true",
        help="Incluir testes de integração com Ollama (requer servidor ativo)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    verbosity = 2 if args.verbose else 1

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    # Sempre executar
    for cls in [
        TestScorePesos,
        TestFSRS,
        TestEnsembleFSRS,
        TestOllamaCliente,
        TestRetreinamentoAutomatico,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    # Opcional: testes reais com Ollama
    if args.ollama:
        suite.addTests(loader.loadTestsFromTestCase(TestOllamaIntegrado))

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
