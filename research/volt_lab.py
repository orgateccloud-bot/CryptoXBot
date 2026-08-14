"""
VOLT lab — vol-targeting spot como overlay de sizing (pré-registro: METODOLOGIA_VOLT.md)
=========================================================================================
Roda a PESQUISA da família congelada (w ∈ {10,20,30} × σ_alvo ∈ {40%,60%}
= 6 trials) sobre o snapshot imutável, por ativo, líquida de custos de
turnover, e aplica a régua pré-registrada (ΔSharpe ≥ +0,20 E ΔDD ≥ 25% E
retorno > 0, em ≥2 de 3 ativos). Hold-out de USO ÚNICO atrás de
`avaliar_holdout(confirmo_uso_unico=True)`.

USO:
    python -m research.volt_lab                  # pesquisa (nunca o hold-out)
    python -m research.volt_lab --holdout --confirmo-uso-unico
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
from research.momo_lab import (  # noqa: E402
    CUSTO_PERNA,
    DIAS_ANO,
    HOLDOUT_INICIO_MS,
    SYMBOLS,
    carregar_diarias,
    max_drawdown,
    retorno_anualizado,
    sharpe,
)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_VEREDITOS = os.path.join(RAIZ, "research", "vereditos")
DOC_METODOLOGIA = os.path.join(RAIZ, "research", "METODOLOGIA_VOLT.md")

# Família congelada (METODOLOGIA_VOLT.md) — mudar aqui exige re-registrar lá.
JANELAS_VOL = (10, 20, 30)
ALVOS_VOL = (0.40, 0.60)  # constantes a priori, anualizadas — nada olha os dados
TRIALS_POR_EXECUCAO = len(JANELAS_VOL) * len(ALVOS_VOL)

PISO_DELTA_SHARPE = 0.20
PISO_REDUCAO_DD = 0.25
MIN_ATIVOS = 2


def vol_realizada(rets: np.ndarray, w: int) -> np.ndarray:
    """vol[t] = desvio-padrão dos retornos rets[t-w+1..t], anualizado.
    Causal: só passado (o retorno do dia t é conhecido no close de t)."""
    n = len(rets)
    vol = np.full(n, np.nan)
    for t in range(w - 1, n):
        vol[t] = np.std(rets[t - w + 1 : t + 1], ddof=1)
    return vol * np.sqrt(DIAS_ANO)


def simular_vt(closes_col: np.ndarray, w: int, sigma_alvo: float, custo: float = CUSTO_PERNA):
    """(rets_vt, rets_bh, exposicoes) na mesma janela avaliada.

    exp[t] = min(σ_alvo / vol[t], 1.0) — teto 1,0, spot puro. A exposição do
    dia t vale o retorno t → t+1; custo = turnover × 0,10%. O buy-and-hold
    paga a perna de entrada única, para a comparação não favorecer o overlay.
    """
    rets_d = closes_col[1:] / closes_col[:-1] - 1.0  # rets_d[i] = retorno do dia i+1
    vol = vol_realizada(rets_d, w)
    # primeiro t com vol definida: índice w-1 em rets_d ↔ dia w em closes
    rets_vt, rets_bh, exposicoes = [], [], []
    exp_anterior = 0.0
    for i in range(w - 1, len(rets_d) - 1):
        exp = float(min(sigma_alvo / vol[i], 1.0)) if vol[i] > 0 else 1.0
        bruto = exp * rets_d[i + 1]
        liquido = bruto - abs(exp - exp_anterior) * custo
        rets_vt.append(liquido)
        rets_bh.append(rets_d[i + 1])
        exposicoes.append(exp)
        exp_anterior = exp
    rets_bh = np.array(rets_bh, dtype=float)
    if len(rets_bh):
        rets_bh[0] -= custo  # perna de entrada do buy-and-hold
    return np.array(rets_vt, dtype=float), rets_bh, np.array(exposicoes, dtype=float)


def _fatia_pesquisa(ts_d: np.ndarray) -> slice:
    fim = int(np.searchsorted(ts_d, HOLDOUT_INICIO_MS))
    return slice(0, fim)


def _fatia_holdout(ts_d: np.ndarray, w: int) -> slice:
    """Warm-up de w dias antes do início: a 1ª exposição é decidida no close
    de 22/07 e o 1º retorno AVALIADO é 22→23/07 — dentro da janela
    pré-registrada e igual ao MOMO. (Off-by-one `ini - w - 1` pego pelo
    cético em 2026-08-14 e corrigido ANTES de qualquer medição.)"""
    ini = int(np.searchsorted(ts_d, HOLDOUT_INICIO_MS))
    return slice(max(ini - w, 0), len(ts_d))


def _somar_trials(quantos: int) -> int:
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    caminho = os.path.join(DIR_VEREDITOS, "volt_trials_count.json")
    total = 0
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            total = int(json.load(f).get("total", 0))
    total += quantos
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"total": total, "atualizado_em": datetime.now().isoformat()}, f)
    return total


def _avaliar_combo(closes: np.ndarray, w: int, alvo: float) -> dict:
    ativos = []
    for j, s in enumerate(SYMBOLS):
        rets_vt, rets_bh, exps = simular_vt(closes[:, j], w, alvo)
        sh_vt, sh_bh = sharpe(rets_vt), sharpe(rets_bh)
        dd_vt, dd_bh = max_drawdown(rets_vt), max_drawdown(rets_bh)
        delta_dd = 1.0 - (dd_vt / dd_bh) if dd_bh > 0 else 0.0
        ret_vt = retorno_anualizado(rets_vt)
        ativos.append(
            {
                "symbol": s,
                "sharpe_vt": round(sh_vt, 3),
                "sharpe_bh": round(sh_bh, 3),
                "delta_sharpe": round(sh_vt - sh_bh, 3),
                "dd_vt": round(dd_vt, 4),
                "dd_bh": round(dd_bh, 4),
                "reducao_dd": round(delta_dd, 3),
                "retorno_aa_vt": round(ret_vt, 4),
                "exposicao_media": round(float(np.mean(exps)), 3),
                "aprova": bool(
                    (sh_vt - sh_bh) >= PISO_DELTA_SHARPE
                    and delta_dd >= PISO_REDUCAO_DD
                    and ret_vt > 0.0
                ),
            }
        )
    aprovados = sum(a["aprova"] for a in ativos)
    return {
        "w": w,
        "sigma_alvo": alvo,
        "ativos": ativos,
        "ativos_aprovados": aprovados,
        "delta_sharpe_medio": round(float(np.mean([a["delta_sharpe"] for a in ativos])), 3),
        "sobrevive": bool(aprovados >= MIN_ATIVOS),
    }


def rodar_pesquisa(rotulo: str | None = None, salvar: bool = True) -> dict:
    ts_d, closes = carregar_diarias(rotulo)
    fat = _fatia_pesquisa(ts_d)
    ts_p, closes_p = ts_d[fat], closes[fat]

    combos = [_avaliar_combo(closes_p, w, alvo) for w in JANELAS_VOL for alvo in ALVOS_VOL]
    sobreviventes = [c for c in combos if c["sobrevive"]]
    escolhida = max(sobreviventes, key=lambda c: c["delta_sharpe_medio"]) if sobreviventes else None
    resultado = {
        "lab": "volt",
        "quando": datetime.now().isoformat(timespec="seconds"),
        "snapshot": snapshot.ler_manifest(rotulo)["rotulo"],
        "janela_pesquisa": [int(ts_p[0]), int(ts_p[-1])],
        "n_dias_pesquisa": int(len(ts_p)),
        "combos": combos,
        "sobreviventes": len(sobreviventes),
        "escolhida_para_holdout": (
            {"w": escolhida["w"], "sigma_alvo": escolhida["sigma_alvo"]} if escolhida else None
        ),
        "veredito_pesquisa": "SOBREVIVE" if sobreviventes else "FAIL",
        "trials_total": _somar_trials(TRIALS_POR_EXECUCAO) if salvar else None,
    }
    if salvar:
        os.makedirs(DIR_VEREDITOS, exist_ok=True)
        with open(os.path.join(DIR_VEREDITOS, "volt_pesquisa.json"), "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
    return resultado


def holdout_ja_consumido() -> str | None:
    caminho = os.path.join(DIR_VEREDITOS, "volt_holdout.json")
    return caminho if os.path.exists(caminho) else None


def avaliar_holdout(confirmo_uso_unico: bool = False, rotulo: str | None = None) -> dict:
    if not confirmo_uso_unico:
        raise SystemExit(
            "[VOLT] Hold-out é de USO ÚNICO. Só rode com confirmo_uso_unico=True "
            "e depois de a pesquisa ter sobrevivente (METODOLOGIA_VOLT.md)."
        )
    ja = holdout_ja_consumido()
    if ja:
        raise SystemExit(f"[VOLT] Hold-out JÁ CONSUMIDO ({ja}). FAIL/PASS é final.")
    caminho_pesq = os.path.join(DIR_VEREDITOS, "volt_pesquisa.json")
    if not os.path.exists(caminho_pesq):
        raise SystemExit("[VOLT] Rode a pesquisa antes do hold-out.")
    with open(caminho_pesq, encoding="utf-8") as f:
        pesq = json.load(f)
    escolhida = pesq.get("escolhida_para_holdout")
    if not escolhida:
        raise SystemExit("[VOLT] A pesquisa foi FAIL — não há combinação para levar ao hold-out.")
    rotulo_atual = snapshot.ler_manifest(rotulo)["rotulo"]
    if rotulo_atual != pesq.get("snapshot"):
        raise SystemExit(
            f"[VOLT] Snapshot atual ({rotulo_atual}) difere do da pesquisa "
            f"({pesq.get('snapshot')}) — o hold-out de uso único tem que rodar "
            "no MESMO substrato. Passe rotulo= explicitamente."
        )

    w, alvo = int(escolhida["w"]), float(escolhida["sigma_alvo"])
    ts_d, closes = carregar_diarias(rotulo)
    fat = _fatia_holdout(ts_d, w)
    combo = _avaliar_combo(closes[fat], w, alvo)
    passou = bool(combo["ativos_aprovados"] >= MIN_ATIVOS)
    resultado = {
        "lab": "volt",
        "quando": datetime.now().isoformat(timespec="seconds"),
        "combo": {"w": w, "sigma_alvo": alvo},
        "detalhe": combo,
        "veredito_holdout": "PASS" if passou else "FAIL",
    }
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    with open(os.path.join(DIR_VEREDITOS, "volt_holdout.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    with open(DOC_METODOLOGIA, "a", encoding="utf-8") as f:
        f.write(
            f"\n- {resultado['quando']}: hold-out CONSUMIDO — combo w={w} "
            f"σ_alvo={alvo:.0%} → {resultado['veredito_holdout']} "
            f"({combo['ativos_aprovados']}/3 ativos, ΔSharpe médio "
            f"{combo['delta_sharpe_medio']:+.2f}).\n"
        )
    return resultado


def _imprimir(r: dict) -> None:
    print(f"[VOLT] pesquisa @ snapshot {r['snapshot']} — {r['n_dias_pesquisa']} dias")
    for c in r["combos"]:
        tag = "SOBREVIVE" if c["sobrevive"] else "reprova"
        detalhe = " · ".join(
            f"{a['symbol'][:3]} ΔSh {a['delta_sharpe']:+.2f} ΔDD {a['reducao_dd']:+.0%}"
            for a in c["ativos"]
        )
        print(
            f"  w={c['w']:>2} alvo={c['sigma_alvo']:.0%} "
            f"[{c['ativos_aprovados']}/3] {detalhe} · {tag}"
        )
    print(f"[VOLT] veredito da PESQUISA: {r['veredito_pesquisa']}")
    if r["escolhida_para_holdout"]:
        print(f"[VOLT] escolhida p/ hold-out (ato deliberado): {r['escolhida_para_holdout']}")


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
