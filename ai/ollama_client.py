"""
Ollama Client — Análise de Mercado com IA Local
=================================================
Usa modelos locais via Ollama para análise qualitativa.
Recomendado: gemma2:2b (2.5GB VRAM, < 500ms)
Alternativa:  phi3:mini  (3.5GB VRAM, ~1s)

Hardware suportado: 6GB VRAM, 16GB RAM, i5-14400F

Modelos disponíveis no sistema:
  gemma3:4b  — 3.2GB VRAM — RECOMENDADO para análises frequentes (30min)
  llama3     — 4.4GB VRAM — para análises profundas (sob demanda)
  gemma3:12b — 7.6GB — EVITAR (excede VRAM, offload para RAM)
  gemma3:27b — 17GB  — EVITAR (roda no CPU, muito lento para bot)

Uso:
  from ai.ollama_client import OllamaCliente
  cliente = OllamaCliente()
  analise = cliente.analisar_mercado(regime="TENDENCIA_ALTA", score=72, prob_ml=0.67, preco=85000)
  print(analise)
"""

import json
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS = "http://localhost:11434/api/tags"
MODELO_RAPIDO = "gemma3:4b"  # 3.2GB — cabe na VRAM (6GB), análises frequentes
MODELO_ANALITICO = "llama3:latest"  # 4.4GB — cabe na VRAM, análises profundas
TIMEOUT_S = 120  # 120s para primeiro carregamento (gemma3:4b indo para VRAM)
TIMEOUT_S_NORMAL = 25  # após modelo carregado, respostas são rápidas


class OllamaCliente:
    """Cliente para análise de mercado com modelos locais via Ollama."""

    def __init__(self, modelo: str = MODELO_RAPIDO):
        self.modelo = modelo
        self._disponivel: bool | None = None
        self._modelo_carregado: bool = False  # True após primeira resposta bem-sucedida

    # ── Verificação de disponibilidade ───────────────────────

    def esta_disponivel(self) -> bool:
        """Verifica se o servidor Ollama está rodando e o modelo disponível."""
        if self._disponivel is not None:
            return self._disponivel
        try:
            r = requests.get(OLLAMA_TAGS, timeout=3)
            modelos_instalados = [m["name"] for m in r.json().get("models", [])]
            # Aceita match parcial (ex: "gemma2:2b" casa com "gemma2:2b-instruct-q4")
            self._disponivel = any(self.modelo.split(":")[0] in m for m in modelos_instalados)
            if not self._disponivel:
                logger.warning(
                    f"Ollama rodando mas modelo '{self.modelo}' não encontrado. "
                    f"Instale: ollama pull {self.modelo}"
                )
        except Exception:
            self._disponivel = False
        return self._disponivel

    def _gerar(self, prompt: str, temperature: float = 0.1, max_tokens: int = 150) -> str | None:
        """Chama o Ollama e retorna o texto gerado."""
        if not self.esta_disponivel():
            return None
        # Primeiro carregamento (modelo ainda não está na VRAM) usa timeout longo
        timeout = TIMEOUT_S_NORMAL if self._modelo_carregado else TIMEOUT_S
        try:
            r = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.modelo,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        "top_p": 0.9,
                    },
                },
                timeout=timeout,
            )
            resposta = r.json().get("response", "").strip()
            if resposta:
                self._modelo_carregado = True
            return resposta
        except requests.exceptions.Timeout:
            status = "carregando" if not self._modelo_carregado else "resposta"
            logger.warning(f"Ollama timeout ({timeout}s) [{status}] — modelo {self.modelo}")
        except Exception as e:
            logger.error(f"Ollama erro: {e}")
        return None

    def warmup(self) -> bool:
        """
        Pré-carrega o modelo na VRAM antes do uso real.
        Chamar uma vez na inicialização do bot para evitar latência no primeiro sinal.
        Retorna True se o modelo respondeu.
        """
        print(f"\033[94m[Ollama] Carregando {self.modelo} na VRAM...\033[0m")
        resp = self._gerar("OK", temperature=0.0, max_tokens=2)
        if resp:
            print(f"\033[92m[Ollama] {self.modelo} pronto na VRAM.\033[0m")
            return True
        print(f"\033[91m[Ollama] Falha ao carregar {self.modelo}.\033[0m")
        return False

    # ── Análises principais ───────────────────────────────────

    def analisar_mercado(
        self,
        regime: str,
        score: int,
        prob_ml: float,
        preco: float,
        rsi: float = 0,
        cvd_tendencia: str = "",
        fear_greed: int = 0,
    ) -> str:
        """
        Análise concisa do estado atual do mercado em linguagem natural.
        Ideal para logs e alertas Telegram.

        Retorna string com análise de 1-2 frases, ou mensagem de fallback.
        """
        fg_txt = f"Fear&Greed: {fear_greed}/100 | " if fear_greed else ""
        cvd_txt = f"CVD: {cvd_tendencia} | " if cvd_tendencia else ""

        prompt = (
            f"Contexto BTC/USDT trading:\n"
            f"- Regime: {regime}\n"
            f"- Score do sistema: {score}/100\n"
            f"- Probabilidade ML de alta: {prob_ml*100:.0f}%\n"
            f"- Preço: ${preco:,.0f}\n"
            f"- RSI: {rsi:.0f} | {cvd_txt}{fg_txt}\n\n"
            f"Em exatamente 2 frases curtas em português: qual é o contexto "
            f"macro atual e se faz sentido operar agora?"
        )

        resposta = self._gerar(prompt, temperature=0.1, max_tokens=100)
        return (
            resposta or f"[Ollama indisponível] Regime:{regime} Score:{score} ML:{prob_ml*100:.0f}%"
        )

    def classificar_noticia(self, texto: str) -> dict:
        """
        Classifica o sentimento de uma notícia sobre Bitcoin/cripto.

        Retorna:
            {"sentimento": "BULLISH|BEARISH|NEUTRO",
             "impacto": "ALTO|MEDIO|BAIXO",
             "motivo": "breve explicação"}
        """
        prompt = (
            f'Classifique esta notícia sobre Bitcoin:\n"{texto[:400]}"\n\n'
            f"Responda APENAS em JSON válido, sem texto extra:\n"
            f'{{"sentimento": "BULLISH|BEARISH|NEUTRO", '
            f'"impacto": "ALTO|MEDIO|BAIXO", '
            f'"motivo": "máximo 10 palavras"}}'
        )

        resposta = self._gerar(prompt, temperature=0.0, max_tokens=80)
        if not resposta:
            return {"sentimento": "NEUTRO", "impacto": "BAIXO", "motivo": "Ollama indisponível"}

        try:
            # Extrai JSON mesmo se houver texto ao redor
            inicio = resposta.find("{")
            fim = resposta.rfind("}") + 1
            if inicio >= 0 and fim > inicio:
                resultado = json.loads(resposta[inicio:fim])
                # Valida e normaliza campos
                resultado["sentimento"] = resultado.get("sentimento", "NEUTRO").upper()
                resultado["impacto"] = resultado.get("impacto", "BAIXO").upper()
                resultado["motivo"] = resultado.get("motivo", "")
                return resultado
        except (json.JSONDecodeError, KeyError):
            pass

        return {"sentimento": "NEUTRO", "impacto": "BAIXO", "motivo": resposta[:60]}

    def explicar_sinal(
        self,
        sinal: str,
        score: int,
        componentes: dict,
        prob_ml: float,
        regime: str,
    ) -> str:
        """
        Explica em linguagem natural por que o bot emitiu (ou não) um sinal.
        Útil para logs detalhados e auditoria de trades.
        """
        # Pega os 3 componentes com maior contribuição
        top3 = sorted(componentes.items(), key=lambda x: x[1], reverse=True)[:3]
        top3_txt = ", ".join(f"{k}:{v:.0f}" for k, v in top3)

        prompt = (
            f"Bot de trading BTC emitiu sinal: {sinal}\n"
            f"Score total: {score}/100 | Regime: {regime} | ML: {prob_ml*100:.0f}%\n"
            f"Principais componentes: {top3_txt}\n\n"
            f"Em 1 frase curta em português: por que o bot tomou esta decisão?"
        )

        resposta = self._gerar(prompt, temperature=0.2, max_tokens=60)
        return resposta or f"Sinal {sinal}: score {score}/100, regime {regime}"

    def resumo_diario(
        self,
        n_trades: int,
        lucro_total_pct: float,
        win_rate: float,
        melhor_trade_pct: float,
        pior_trade_pct: float,
    ) -> str:
        """
        Gera resumo diário dos trades em linguagem natural.
        Para envio via Telegram às 23h.
        """
        prompt = (
            f"Resumo do dia de trading BTC/USDT:\n"
            f"- Trades: {n_trades}\n"
            f"- Resultado: {lucro_total_pct:+.2f}%\n"
            f"- Win rate: {win_rate:.0f}%\n"
            f"- Melhor trade: +{melhor_trade_pct:.2f}%\n"
            f"- Pior trade: {pior_trade_pct:.2f}%\n\n"
            f"Em 2 frases em português: avalie o desempenho do dia e sugira ajuste para amanhã."
        )

        resposta = self._gerar(prompt, temperature=0.3, max_tokens=120)
        hora = datetime.now().strftime("%d/%m/%Y")
        return resposta or (
            f"[{hora}] Trades:{n_trades} Resultado:{lucro_total_pct:+.2f}% "
            f"WinRate:{win_rate:.0f}%"
        )


# ── Instância global (singleton) ────────────────────────────────
_instancia: OllamaCliente | None = None


def get_ollama(modelo: str = MODELO_RAPIDO) -> OllamaCliente:
    """Retorna instância singleton do OllamaCliente."""
    global _instancia
    if _instancia is None or _instancia.modelo != modelo:
        _instancia = OllamaCliente(modelo=modelo)
    return _instancia


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ollama Client — Análise de Mercado")
    parser.add_argument("--modelo", default=MODELO_RAPIDO)
    parser.add_argument("--testar", action="store_true")
    parser.add_argument("--noticia", type=str, default="", help="Texto de notícia para classificar")
    args = parser.parse_args()

    cliente = OllamaCliente(modelo=args.modelo)

    if not cliente.esta_disponivel():
        print(f"\n[ERRO] Ollama não disponível ou modelo '{args.modelo}' não encontrado.")
        print(f"  Verifique se o Ollama está rodando: ollama serve")
        print(f"  Modelos disponíveis: gemma3:4b | llama3 | jarvis-otimizado")
        exit(1)

    print(f"[OK] Ollama disponível — modelo: {args.modelo}\n")

    if args.noticia:
        print("[NOTÍCIA] Classificando...")
        resultado = cliente.classificar_noticia(args.noticia)
        print(f"  Sentimento: {resultado['sentimento']}")
        print(f"  Impacto:    {resultado['impacto']}")
        print(f"  Motivo:     {resultado['motivo']}")

    elif args.testar:
        print("[TESTE] Análise de mercado simulada...")
        analise = cliente.analisar_mercado(
            regime="TENDENCIA_ALTA",
            score=74,
            prob_ml=0.68,
            preco=85000,
            rsi=58,
            cvd_tendencia="POSITIVO",
            fear_greed=62,
        )
        print(f"\nAnálise:\n{analise}\n")

        print("[TESTE] Classificação de notícia...")
        noticia_teste = "Bitcoin atinge nova máxima histórica com ETF aprovado pela SEC, fluxo de capital institucional recorde"
        r = cliente.classificar_noticia(noticia_teste)
        print(f"  {r}\n")

        print("[TESTE] Explicação de sinal...")
        exp = cliente.explicar_sinal(
            sinal="COMPRA",
            score=74,
            componentes={"ml": 18, "regime": 16, "cvd": 14},
            prob_ml=0.68,
            regime="TENDENCIA_ALTA",
        )
        print(f"  {exp}")
    else:
        # Teste rápido de conectividade
        resposta = cliente._gerar("Responda apenas: OK", max_tokens=5)
        print(f"Ping Ollama: {resposta or 'sem resposta'}")
