"""
Testes do OllamaCliente — APOSENTADOS junto com o modulo (M-1).
==============================================================
Extraidos de tests/test_melhorias.py quando `ai/ollama_client.py` foi para
`_legado/`. Nao rodam na suite: o modulo que eles testam nao esta mais na
arvore viva. Preservados para que o rollback seja `git mv` dos dois, nao
reescrever teste do zero. Ver _legado/LEIA-ME.md.
"""

import unittest  # noqa: F401  (usado pelas classes abaixo)

# ══════════════════════════════════════════════════════════════
# 2. FSRS — filtro adaptativo
# ══════════════════════════════════════════════════════════════


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
        from ai.ollama_client import MODELO_ANALITICO, MODELO_RAPIDO, OllamaCliente

        self.OllamaCliente = OllamaCliente
        self.MODELO_RAPIDO = MODELO_RAPIDO
        self.MODELO_ANALITICO = MODELO_ANALITICO

    def test_modelos_configurados_corretos(self):
        from ai.ollama_client import MODELO_ANALITICO, MODELO_RAPIDO

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


