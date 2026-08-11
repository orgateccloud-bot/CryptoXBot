"""
Trend-following AO VIVO (Donchian 20/10) — apenas DRY_RUN
==========================================================
⚠️ ESTRATÉGIA REPROVADA NO GATE. Este módulo existe para **validação de
EXECUÇÃO**, não porque a estratégia foi aprovada:

    research/METODOLOGIA_TREND.md — 4 formulações testadas, todas FAIL.
    A última (risco controlado) passou 6/6 na pesquisa e reprovou no hold-out
    (+5.70% a.a. vs piso de 8%). O hold-out está CONSUMIDO.

O objetivo do dry run é medir o que o backtest **idealiza**: latência entre
sinal e fill, slippage real, comportamento em 24/7, estabilidade do serviço.
Nenhum resultado daqui aprova a estratégia para capital real — isso exigiria
hipótese nova, dados novos e hold-out novo.

**Trava de segurança:** `main.py` recusa iniciar com `--estrategia trend` fora
de simulação (ver `_validar_trend_so_em_simulacao`). Não é convenção — é
`sys.exit`.

Paridade com o backtest: os níveis vêm de `backtesting.trend_following.
donchian_niveis` (a MESMA função do backtest). Se a lógica divergir, a
validação de execução não significaria nada.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.trend_following import M_SAIDA, N_ENTRADA, donchian_niveis  # noqa: E402
from data.klines import obter_klines  # noqa: E402

INTERVALO_PADRAO = "1d"
# Precisa de N+M de folga para os canais + margem; 120 candles diários é
# ~4 meses, suficiente e barato.
LIMITE_CANDLES = 120


def _closes_fechados(symbol: str, intervalo: str) -> np.ndarray | None:
    """Closes apenas de candles FECHADOS.

    A Binance devolve a vela EM FORMAÇÃO como último elemento de
    /api/v3/klines. Usá-la seria look-ahead ao vivo (o preço ainda se move) e
    quebraria a paridade com o backtest, que decide no fechamento. Descartamos
    o último elemento SEMPRE — conservador: no pior caso usamos dado um pouco
    mais velho, nunca dado do futuro.
    """
    dados = obter_klines(symbol, intervalo, LIMITE_CANDLES)
    if not dados or not dados.get("fechamento"):
        return None
    fechados = dados["fechamento"][:-1]  # <- descarta a vela em formação
    if len(fechados) < max(N_ENTRADA, M_SAIDA) + 2:
        return None
    return np.asarray(fechados, dtype=float)


def sinal_trend(
    symbol: str,
    tem_posicao: bool,
    intervalo: str = INTERVALO_PADRAO,
    N: int = N_ENTRADA,
    M: int = M_SAIDA,
) -> dict:
    """Sinal do sistema Donchian sobre candles fechados.

    Retorna dict com:
      sinal:      "COMPRA" | "FECHAR" | "AGUARDAR"
      preco_ref:  close do último candle FECHADO (a referência da decisão —
                  comparar com o preço de fill mede o slippage de timing)
      canal_alto: nível de rompimento de entrada (Donchian-N superior)
      canal_baixo:nível de saída/stop (Donchian-M inferior)
      motivo:     texto legível
    """
    closes = _closes_fechados(symbol, intervalo)
    if closes is None:
        return {
            "sinal": "AGUARDAR",
            "preco_ref": None,
            "canal_alto": None,
            "canal_baixo": None,
            "motivo": "dados insuficientes/indisponíveis",
        }

    up_entrada, _ = donchian_niveis(closes, N)
    _, low_saida = donchian_niveis(closes, M)
    i = len(closes) - 1
    preco = float(closes[i])
    canal_alto = float(up_entrada[i]) if not np.isnan(up_entrada[i]) else None
    canal_baixo = float(low_saida[i]) if not np.isnan(low_saida[i]) else None

    if tem_posicao:
        if canal_baixo is not None and preco < canal_baixo:
            return {
                "sinal": "FECHAR",
                "preco_ref": preco,
                "canal_alto": canal_alto,
                "canal_baixo": canal_baixo,
                "motivo": f"close {preco:,.2f} < Donchian-{M} {canal_baixo:,.2f}",
            }
        return {
            "sinal": "AGUARDAR",
            "preco_ref": preco,
            "canal_alto": canal_alto,
            "canal_baixo": canal_baixo,
            "motivo": f"em posição; trail em {canal_baixo:,.2f}" if canal_baixo else "em posição",
        }

    if canal_alto is not None and preco > canal_alto:
        return {
            "sinal": "COMPRA",
            "preco_ref": preco,
            "canal_alto": canal_alto,
            "canal_baixo": canal_baixo,
            "motivo": f"close {preco:,.2f} > Donchian-{N} {canal_alto:,.2f}",
        }
    return {
        "sinal": "AGUARDAR",
        "preco_ref": preco,
        "canal_alto": canal_alto,
        "canal_baixo": canal_baixo,
        "motivo": f"sem rompimento (topo {canal_alto:,.2f})" if canal_alto else "sem canal ainda",
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Sinal trend ao vivo (diagnóstico)")
    ap.add_argument("--par", default="BTCUSDT")
    ap.add_argument("--intervalo", default=INTERVALO_PADRAO)
    ap.add_argument("--com-posicao", action="store_true")
    args = ap.parse_args()
    r = sinal_trend(args.par, args.com_posicao, args.intervalo)
    print(f"\n  {args.par} [{args.intervalo}] — SINAL: {r['sinal']}")
    print(f"    preço ref (último close FECHADO): {r['preco_ref']}")
    print(f"    canal alto (entrada N={N_ENTRADA}): {r['canal_alto']}")
    print(f"    canal baixo (saída M={M_SAIDA}):   {r['canal_baixo']}")
    print(f"    motivo: {r['motivo']}\n")
