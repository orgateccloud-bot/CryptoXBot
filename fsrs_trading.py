"""
FSRS Trading — Filtro Adaptativo de Confiança
===============================================
Baseado no algoritmo Free Spaced Repetition Scheduler (FSRS v4).
Adaptado para trading: cada "padrão de sinal" é tratado como um flashcard
que ganha/perde estabilidade conforme o resultado dos trades.

Conceito:
  - Padrão com lucros recentes → alta estabilidade → confiança alta
  - Padrão com perdas recentes → baixa estabilidade → confiança reduzida
  - Padrão sem feedback → neutro (fator 0.5)

Uso:
  from fsrs_trading import FSRSFiltro
  fsrs = FSRSFiltro()

  # Antes de operar:
  fator = fsrs.avaliar(features_dict)  # retorna 0.0-1.0

  # Após fechar trade:
  fsrs.registrar_resultado(features_dict, lucro_pct=0.018)  # lucro positivo
  fsrs.registrar_resultado(features_dict, lucro_pct=-0.012) # perda

  # Ver estatísticas:
  fsrs.imprimir_padroes()
"""

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime

DB_PATH = "data/fsrs_padroes.json"


@dataclass
class PadraoSinal:
    """
    Representa um padrão de sinal de mercado como 'flashcard' do FSRS.

    Campos FSRS:
      dificuldade:  0.1-0.9 (quanto o padrão é difícil de prever)
      estabilidade: dias até 90% de esquecimento (analogia: confiança)
      n_reviews:    quantas vezes este padrão foi visto
    """

    id: str
    descricao: str
    dificuldade: float = 0.3
    estabilidade: float = 1.0
    n_reviews: int = 0
    n_lucros: int = 0
    n_perdas: int = 0
    lucro_total_pct: float = 0.0
    ultima_revisao: str = ""

    @property
    def fator_confianca(self) -> float:
        """
        Retorna fator 0.0-1.0 para multiplicar a probabilidade do ensemble.
        - Alta estabilidade + baixa dificuldade → próximo de 1.0
        - Baixa estabilidade + alta dificuldade → próximo de 0.3
        """
        if self.n_reviews == 0:
            return 0.5  # sem histórico = neutro

        # Decaimento exponencial baseado na estabilidade
        # estabilidade = 1: fator_base ≈ 0.90
        # estabilidade = 5: fator_base ≈ 0.98
        # estabilidade = 0.1: fator_base ≈ 0.37
        fator_base = 1.0 - math.exp(-self.estabilidade / 2.0)

        # Penalidade por dificuldade alta
        penalidade = 1.0 - (self.dificuldade * 0.4)  # dificul=0.9 → penalidade 36%

        # Bonus por taxa de acerto
        taxa_acerto = self.n_lucros / max(self.n_reviews, 1)
        bonus = 0.0
        if taxa_acerto >= 0.6:
            bonus = (taxa_acerto - 0.5) * 0.3  # até +15%

        fator = min(1.0, max(0.1, fator_base * penalidade + bonus))
        return round(fator, 3)

    @property
    def taxa_acerto_pct(self) -> float:
        return round(self.n_lucros / max(self.n_reviews, 1) * 100, 1)


class FSRSFiltro:
    """Filtro adaptativo baseado em FSRS para sinais de trading."""

    # Parâmetros FSRS v4 (otimizados para trading)
    W = [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94]

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.padroes: dict[str, PadraoSinal] = {}
        self._carregar()

    # ── Quantização de features ───────────────────────────────

    def quantizar_features(self, features: dict) -> tuple[str, str]:
        """
        Converte features contínuas em categorias discretas para criar
        o ID único do padrão. Retorna (id, descricao).
        """
        # Regime de mercado
        regime = features.get("regime", "INDEF")

        # RSI categorizado
        rsi = features.get("rsi", 50)
        if rsi > 65:
            rsi_cat = "SOBRE"
        elif rsi < 35:
            rsi_cat = "SVEND"
        elif rsi >= 55:
            rsi_cat = "BULL"
        elif rsi <= 45:
            rsi_cat = "BEAR"
        else:
            rsi_cat = "NEUT"

        # EMA20 (distância do preço em %)
        dist_ema = features.get("dist_ema20", 0)
        if dist_ema > 0.003:
            ema_cat = "ACIMA"
        elif dist_ema < -0.003:
            ema_cat = "ABAIXO"
        else:
            ema_cat = "NEUT"

        # CVD score ou prob CVD
        cvd = features.get("cvd_score", features.get("prob_cvd", 50))
        if cvd > 65:
            cvd_cat = "POS"
        elif cvd < 35:
            cvd_cat = "NEG"
        else:
            cvd_cat = "NEUT"

        # Volume relativo
        vol = features.get("vol_rel", 1.0)
        vol_cat = "ALTO" if vol >= 1.5 else "BAIXO" if vol < 0.7 else "NORM"

        padrao_id = f"{regime}|RSI_{rsi_cat}|EMA_{ema_cat}|CVD_{cvd_cat}|VOL_{vol_cat}"
        descricao = f"Regime:{regime} RSI:{rsi_cat} EMA:{ema_cat} " f"CVD:{cvd_cat} Vol:{vol_cat}"
        return padrao_id, descricao

    # ── Avaliação ────────────────────────────────────────────

    def avaliar(self, features: dict) -> float:
        """
        Retorna fator de confiança para o padrão atual (0.0-1.0).

        Valores:
          0.5 = padrão desconhecido (neutro, não penaliza nem bônus)
          0.8+ = padrão historicamente confiável
          0.3- = padrão com histórico ruim (penaliza sinal)
        """
        padrao_id, _ = self.quantizar_features(features)
        if padrao_id not in self.padroes:
            return 0.5
        return self.padroes[padrao_id].fator_confianca

    def avaliar_com_detalhe(self, features: dict) -> dict:
        """Retorna avaliação completa com estatísticas do padrão."""
        padrao_id, descricao = self.quantizar_features(features)
        fator = 0.5
        detalhes = {
            "padrao_id": padrao_id,
            "descricao": descricao,
            "fator_confianca": 0.5,
            "n_reviews": 0,
            "taxa_acerto_pct": 0.0,
            "estabilidade": 0.0,
            "dificuldade": 0.0,
            "status": "DESCONHECIDO",
        }

        if padrao_id in self.padroes:
            p = self.padroes[padrao_id]
            fator = p.fator_confianca
            detalhes.update(
                {
                    "fator_confianca": fator,
                    "n_reviews": p.n_reviews,
                    "taxa_acerto_pct": p.taxa_acerto_pct,
                    "estabilidade": round(p.estabilidade, 2),
                    "dificuldade": round(p.dificuldade, 2),
                    "status": (
                        "CONFIAVEL" if fator >= 0.7 else "CAUTELOSO" if fator >= 0.5 else "EVITAR"
                    ),
                }
            )

        return detalhes

    # ── Registro de resultado ────────────────────────────────

    def registrar_resultado(self, features: dict, lucro_pct: float):
        """
        Atualiza o padrão com o resultado do trade.

        Args:
            features: dict com as features no momento do sinal
            lucro_pct: resultado em decimal (ex: 0.018 = +1.8%, -0.012 = -1.2%)
        """
        padrao_id, descricao = self.quantizar_features(features)

        if padrao_id not in self.padroes:
            self.padroes[padrao_id] = PadraoSinal(
                id=padrao_id,
                descricao=descricao,
            )

        p = self.padroes[padrao_id]
        p.n_reviews += 1
        p.ultima_revisao = datetime.now().isoformat()
        p.lucro_total_pct += lucro_pct

        if lucro_pct > 0:
            p.n_lucros += 1
            # Grade baseada no lucro (0-4, como no FSRS)
            grade = min(4.0, 2.0 + lucro_pct * 100)  # 1.5%=3.5, 3%=5→4
            # Atualizar estabilidade (cresce em trades vencedores)
            p.estabilidade = p.estabilidade * (1.0 + 0.1 * grade)
            # Reduzir dificuldade gradualmente
            p.dificuldade = max(0.1, p.dificuldade - 0.05)
        else:
            p.n_perdas += 1
            # Grade baixa em perdas
            grade = max(0.0, 1.0 + lucro_pct * 50)  # -2%=0, -1%=0.5
            # Reduzir estabilidade significativamente
            fator_reducao = 0.3 + 0.2 * grade  # 0.3-0.5x
            p.estabilidade = max(0.1, p.estabilidade * fator_reducao)
            # Aumentar dificuldade
            p.dificuldade = min(0.9, p.dificuldade + 0.08)

        self._salvar()
        return p

    # ── Persistência ─────────────────────────────────────────

    def _carregar(self):
        try:
            with open(self.db_path) as f:
                dados = json.load(f)
            for k, v in dados.items():
                # Compatibilidade com campos novos
                v.setdefault("n_lucros", 0)
                v.setdefault("n_perdas", 0)
                v.setdefault("lucro_total_pct", 0.0)
                v.setdefault("ultima_revisao", "")
                self.padroes[k] = PadraoSinal(**v)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _salvar(self):
        import threading as _threading

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        dados = {}
        for k, v in self.padroes.items():
            dados[k] = {
                "id": v.id,
                "descricao": v.descricao,
                "dificuldade": v.dificuldade,
                "estabilidade": v.estabilidade,
                "n_reviews": v.n_reviews,
                "n_lucros": v.n_lucros,
                "n_perdas": v.n_perdas,
                "lucro_total_pct": v.lucro_total_pct,
                "ultima_revisao": v.ultima_revisao,
            }
        # Escreve atomicamente: temp file + rename para evitar corrupção
        tmp_path = self.db_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.db_path)

    # ── Relatório ─────────────────────────────────────────────

    def imprimir_padroes(self, top_n: int = 10):
        """Imprime os padrões mais relevantes."""
        verde = "\033[92m"
        amarelo = "\033[93m"
        vermelho = "\033[91m"
        cinza = "\033[90m"
        reset = "\033[0m"

        if not self.padroes:
            print(f"{cinza}[FSRS] Nenhum padrão registrado ainda.{reset}")
            return

        padroes_ord = sorted(self.padroes.values(), key=lambda p: p.n_reviews, reverse=True)[:top_n]

        print("\n" + "=" * 65)
        print("  FSRS — PADRÕES DE SINAL (ordenados por frequência)")
        print("=" * 65)
        print(f"  {'Padrão':<42} {'Rev':>4} {'%Acerto':>8} {'Fator':>6}")
        print(f"  {'-'*60}")

        for p in padroes_ord:
            fator = p.fator_confianca
            cor = verde if fator >= 0.7 else amarelo if fator >= 0.5 else vermelho
            print(
                f"  {p.descricao[:42]:<42} {p.n_reviews:>4} "
                f"{p.taxa_acerto_pct:>7.1f}% {cor}{fator:>5.2f}{reset}"
            )

        print(f"\n  Total de padrões únicos: {len(self.padroes)}")
        print("=" * 65)


# ── Instância global (singleton) ────────────────────────────────
_instancia: FSRSFiltro | None = None


def get_fsrs() -> FSRSFiltro:
    """Retorna instância singleton do FSRSFiltro."""
    global _instancia
    if _instancia is None:
        _instancia = FSRSFiltro()
    return _instancia


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FSRS Trading — Filtro Adaptativo")
    parser.add_argument("--listar", action="store_true", help="Listar padrões conhecidos")
    parser.add_argument("--testar", action="store_true", help="Testar com features simuladas")
    args = parser.parse_args()

    fsrs = FSRSFiltro()

    if args.listar:
        fsrs.imprimir_padroes()

    elif args.testar:
        print("[FSRS] Testando com features simuladas...")

        # Simular alguns trades
        features_alta = {
            "regime": "TENDENCIA_ALTA",
            "rsi": 58,
            "dist_ema20": 0.005,
            "cvd_score": 72,
            "vol_rel": 1.6,
        }
        features_lateral = {
            "regime": "LATERAL",
            "rsi": 50,
            "dist_ema20": 0.001,
            "cvd_score": 50,
            "vol_rel": 0.9,
        }

        print(f"\n  Fator inicial (sem histórico): {fsrs.avaliar(features_alta)}")

        fsrs.registrar_resultado(features_alta, lucro_pct=0.018)
        fsrs.registrar_resultado(features_alta, lucro_pct=0.025)
        fsrs.registrar_resultado(features_alta, lucro_pct=-0.012)
        fsrs.registrar_resultado(features_lateral, lucro_pct=-0.015)
        fsrs.registrar_resultado(features_lateral, lucro_pct=-0.010)

        d = fsrs.avaliar_com_detalhe(features_alta)
        print(f"\n  Padrão tendência alta:")
        print(
            f"    Fator: {d['fator_confianca']} | Acerto: {d['taxa_acerto_pct']}% | Status: {d['status']}"
        )

        d2 = fsrs.avaliar_com_detalhe(features_lateral)
        print(f"\n  Padrão lateral:")
        print(
            f"    Fator: {d2['fator_confianca']} | Acerto: {d2['taxa_acerto_pct']}% | Status: {d2['status']}"
        )

        fsrs.imprimir_padroes()
    else:
        fsrs.imprimir_padroes()
