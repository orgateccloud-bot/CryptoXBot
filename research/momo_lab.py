"""
MOMO lab — momentum cross-sectional BTC/ETH/SOL (pré-registro: METODOLOGIA_MOMO.md)
====================================================================================
Roda a PESQUISA da família congelada (k ∈ {3,7,14,28} × {sem,com} filtro
TSMOM = 8 trials) sobre o snapshot imutável, líquida de custos, e aplica a
régua pré-registrada. O hold-out (2025-07-22 →) é de USO ÚNICO e fica atrás
de `avaliar_holdout(confirmo_uso_unico=True)`, que grava registro permanente
na própria metodologia.

USO:
    python -m research.momo_lab                  # pesquisa (nunca o hold-out)
    python -m research.momo_lab --holdout --confirmo-uso-unico
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from research import snapshot  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_VEREDITOS = os.path.join(RAIZ, "research", "vereditos")
DOC_METODOLOGIA = os.path.join(RAIZ, "research", "METODOLOGIA_MOMO.md")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
HOLDOUT_INICIO_MS = 1753142400000  # 2025-07-22 00:00 UTC — mesma data do edge_lab
CUSTO_PERNA = 0.001  # taker spot 0,10% por perna (pré-registrado)
DIA_MS = 86_400_000
DIAS_ANO = 365.0

# Família congelada (METODOLOGIA_MOMO.md) — mudar aqui exige re-registrar lá.
LOOKBACKS = (3, 7, 14, 28)
FILTROS = (False, True)
TRIALS_POR_EXECUCAO = len(LOOKBACKS) * len(FILTROS)

PISO_RETORNO_AA = 0.08
PISO_SHARPE = 0.8


# ── métricas (líquidas entram prontas; aqui é só aritmética) ─────────────


def retorno_anualizado(rets: np.ndarray) -> float:
    if len(rets) == 0:
        return 0.0
    total = float(np.prod(1.0 + rets))
    if total <= 0.0:
        return -1.0
    return total ** (DIAS_ANO / len(rets)) - 1.0


def sharpe(rets: np.ndarray) -> float:
    if len(rets) < 2:
        return 0.0
    dp = float(np.std(rets, ddof=1))
    if dp == 0.0:
        return 0.0
    return float(np.mean(rets)) / dp * float(np.sqrt(DIAS_ANO))


def max_drawdown(rets: np.ndarray) -> float:
    """Máximo drawdown (fração positiva, ex.: 0.35 = -35%)."""
    if len(rets) == 0:
        return 0.0
    # o pico inicial 1.0 (capital de partida) entra na curva — sem ele, uma
    # queda logo no 1º dia avaliado seria subestimada (achado do cético,
    # 2026-08-14, corrigido antes de qualquer medição)
    curva = np.concatenate(([1.0], np.cumprod(1.0 + rets)))
    pico = np.maximum.accumulate(curva)
    return float(np.max(1.0 - curva / pico))


# ── substrato ────────────────────────────────────────────────────────────


def carregar_diarias(rotulo: str | None = None):
    """(ts_d, closes[n_dias, 3]) — close da barra 00:00 UTC, interseção dos 3.

    A decisão do dia t usa dados até este close; a posição vale t → t+1.
    """
    series = {}
    ts_comum = None
    for s in SYMBOLS:
        ts, _m, _n, f, _v = snapshot.carregar_serie(s, "1h", rotulo)
        series[s] = (ts, f)
        ts_comum = ts if ts_comum is None else np.intersect1d(ts_comum, ts)
    ts_diario = ts_comum[ts_comum % DIA_MS == 0]
    closes = np.empty((len(ts_diario), len(SYMBOLS)), dtype=float)
    for j, s in enumerate(SYMBOLS):
        ts, f = series[s]
        idx = np.searchsorted(ts, ts_diario)
        closes[:, j] = f[idx]
    return ts_diario, closes


def sinais_momentum(closes: np.ndarray, k: int) -> np.ndarray:
    """sig[t, a] = closes[t]/closes[t-k] − 1; NaN nos primeiros k dias."""
    sig = np.full_like(closes, np.nan)
    sig[k:] = closes[k:] / closes[:-k] - 1.0
    return sig


def simular(closes: np.ndarray, k: int, com_filtro: bool, custo: float = CUSTO_PERNA):
    """Retornos diários LÍQUIDOS da carteira top-1 (−1 = caixa).

    Causalidade: a posição do dia t sai de sinais com dados ATÉ t e vale o
    retorno t → t+1. Custos no dia da troca: ativo→ativo 2 pernas,
    caixa↔ativo 1 perna.
    """
    sig = sinais_momentum(closes, k)
    n = len(closes)
    rets = []
    posicoes = []
    pos_anterior = -1  # começa em caixa
    for t in range(k, n - 1):
        escolha = int(np.argmax(sig[t]))
        if com_filtro and sig[t, escolha] <= 0.0:
            escolha = -1
        if escolha == -1:
            bruto = 0.0
        else:
            bruto = closes[t + 1, escolha] / closes[t, escolha] - 1.0
        if escolha != pos_anterior:
            pernas = 2 if (escolha != -1 and pos_anterior != -1) else 1
            bruto -= pernas * custo
        rets.append(bruto)
        posicoes.append(escolha)
        pos_anterior = escolha
    return np.array(rets, dtype=float), np.array(posicoes, dtype=int)


def _fatia_pesquisa(ts_d: np.ndarray) -> slice:
    fim = int(np.searchsorted(ts_d, HOLDOUT_INICIO_MS))
    return slice(0, fim)


def _fatia_holdout(ts_d: np.ndarray, k: int) -> slice:
    """Hold-out com warm-up de k dias ANTES do início (o sinal do 1º dia do
    hold-out usa história da pesquisa — causal; o que não pode é AVALIAR
    retorno em dias de pesquisa)."""
    ini = int(np.searchsorted(ts_d, HOLDOUT_INICIO_MS))
    return slice(max(ini - k, 0), len(ts_d))


# ── contador de trials (deflator do DSR da casa) ─────────────────────────


def _somar_trials(quantos: int) -> int:
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    caminho = os.path.join(DIR_VEREDITOS, "momo_trials_count.json")
    total = 0
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            total = int(json.load(f).get("total", 0))
    total += quantos
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"total": total, "atualizado_em": datetime.now().isoformat()}, f)
    return total


# ── pesquisa (nunca toca o hold-out) ─────────────────────────────────────


def rodar_pesquisa(rotulo: str | None = None, salvar: bool = True) -> dict:
    ts_d, closes = carregar_diarias(rotulo)
    fat = _fatia_pesquisa(ts_d)
    ts_p, closes_p = ts_d[fat], closes[fat]

    combos = []
    for k in LOOKBACKS:
        for com_filtro in FILTROS:
            rets, pos = simular(closes_p, k, com_filtro)
            # benchmark na MESMA janela avaliada (dias k..n-2 da pesquisa)
            bh_btc = (closes_p[k + 1 :, 0] / closes_p[k:-1, 0] - 1.0).copy()
            if len(bh_btc):
                bh_btc[0] -= CUSTO_PERNA  # B&H paga a perna de entrada (uniforme c/ VOLT)
            ret_aa = retorno_anualizado(rets)
            sh = sharpe(rets)
            sh_bh = sharpe(bh_btc)
            combos.append(
                {
                    "k": k,
                    "filtro_tsmom": com_filtro,
                    "retorno_aa": round(ret_aa, 4),
                    "sharpe": round(sh, 3),
                    "max_dd": round(max_drawdown(rets), 4),
                    "sharpe_bh_btc": round(sh_bh, 3),
                    "dias_avaliados": int(len(rets)),
                    "pct_em_caixa": round(float(np.mean(pos == -1)), 3),
                    "trocas": int(np.sum(np.diff(pos) != 0)),
                    "sobrevive": bool(
                        ret_aa >= PISO_RETORNO_AA and sh >= PISO_SHARPE and sh >= sh_bh
                    ),
                }
            )

    sobreviventes = [c for c in combos if c["sobrevive"]]
    escolhida = max(sobreviventes, key=lambda c: c["sharpe"]) if sobreviventes else None
    resultado = {
        "lab": "momo",
        "quando": datetime.now().isoformat(timespec="seconds"),
        "snapshot": snapshot.ler_manifest(rotulo)["rotulo"],
        "janela_pesquisa": [int(ts_p[0]), int(ts_p[-1])],
        "n_dias_pesquisa": int(len(ts_p)),
        "combos": combos,
        "sobreviventes": len(sobreviventes),
        "escolhida_para_holdout": (
            {"k": escolhida["k"], "filtro_tsmom": escolhida["filtro_tsmom"]} if escolhida else None
        ),
        "veredito_pesquisa": "SOBREVIVE" if sobreviventes else "FAIL",
        "trials_total": _somar_trials(TRIALS_POR_EXECUCAO) if salvar else None,
    }
    if salvar:
        os.makedirs(DIR_VEREDITOS, exist_ok=True)
        with open(os.path.join(DIR_VEREDITOS, "momo_pesquisa.json"), "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
    return resultado


# ── hold-out (uso único, travado) ────────────────────────────────────────


def holdout_ja_consumido() -> str | None:
    caminho = os.path.join(DIR_VEREDITOS, "momo_holdout.json")
    return caminho if os.path.exists(caminho) else None


def avaliar_holdout(confirmo_uso_unico: bool = False, rotulo: str | None = None) -> dict:
    if not confirmo_uso_unico:
        raise SystemExit(
            "[MOMO] Hold-out é de USO ÚNICO. Só rode com confirmo_uso_unico=True "
            "e depois de a pesquisa ter sobrevivente (METODOLOGIA_MOMO.md)."
        )
    ja = holdout_ja_consumido()
    if ja:
        raise SystemExit(f"[MOMO] Hold-out JÁ CONSUMIDO ({ja}). FAIL/PASS é final.")
    caminho_pesq = os.path.join(DIR_VEREDITOS, "momo_pesquisa.json")
    if not os.path.exists(caminho_pesq):
        raise SystemExit("[MOMO] Rode a pesquisa antes do hold-out.")
    with open(caminho_pesq, encoding="utf-8") as f:
        pesq = json.load(f)
    escolhida = pesq.get("escolhida_para_holdout")
    if not escolhida:
        raise SystemExit(
            "[MOMO] A pesquisa foi FAIL — não há combinação para levar ao "
            "hold-out. Consumi-lo seria queimar o dado à toa."
        )
    rotulo_atual = snapshot.ler_manifest(rotulo)["rotulo"]
    if rotulo_atual != pesq.get("snapshot"):
        raise SystemExit(
            f"[MOMO] Snapshot atual ({rotulo_atual}) difere do da pesquisa "
            f"({pesq.get('snapshot')}) — o hold-out de uso único tem que rodar "
            "no MESMO substrato. Passe rotulo= explicitamente."
        )

    k, com_filtro = int(escolhida["k"]), bool(escolhida["filtro_tsmom"])
    ts_d, closes = carregar_diarias(rotulo)
    fat = _fatia_holdout(ts_d, k)
    rets, _pos = simular(closes[fat], k, com_filtro)
    ret_aa, sh = retorno_anualizado(rets), sharpe(rets)
    passou = bool(ret_aa >= PISO_RETORNO_AA and sh >= PISO_SHARPE)
    resultado = {
        "lab": "momo",
        "quando": datetime.now().isoformat(timespec="seconds"),
        "combo": {"k": k, "filtro_tsmom": com_filtro},
        "retorno_aa": round(ret_aa, 4),
        "sharpe": round(sh, 3),
        "max_dd": round(max_drawdown(rets), 4),
        "veredito_holdout": "PASS" if passou else "FAIL",
    }
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    with open(os.path.join(DIR_VEREDITOS, "momo_holdout.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    with open(DOC_METODOLOGIA, "a", encoding="utf-8") as f:
        f.write(
            f"\n- {resultado['quando']}: hold-out CONSUMIDO — combo k={k} "
            f"filtro={com_filtro} → {resultado['veredito_holdout']} "
            f"(ret {ret_aa:+.2%} a.a., sharpe {sh:.2f}).\n"
        )
    return resultado


def _imprimir(r: dict) -> None:
    print(f"[MOMO] pesquisa @ snapshot {r['snapshot']} — {r['n_dias_pesquisa']} dias")
    for c in r["combos"]:
        tag = "SOBREVIVE" if c["sobrevive"] else "reprova"
        print(
            f"  k={c['k']:>2} filtro={str(c['filtro_tsmom']):<5} "
            f"ret {c['retorno_aa']:+8.2%} a.a. · sharpe {c['sharpe']:+5.2f} "
            f"(bh {c['sharpe_bh_btc']:+5.2f}) · dd {c['max_dd']:6.2%} "
            f"· caixa {c['pct_em_caixa']:5.1%} · {tag}"
        )
    print(f"[MOMO] veredito da PESQUISA: {r['veredito_pesquisa']}")
    if r["escolhida_para_holdout"]:
        print(f"[MOMO] escolhida p/ hold-out (ato deliberado): {r['escolhida_para_holdout']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--confirmo-uso-unico", action="store_true")
    args = ap.parse_args(argv)
    if args.holdout:
        r = avaliar_holdout(confirmo_uso_unico=args.confirmo_uso_unico)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    _imprimir(rodar_pesquisa())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
