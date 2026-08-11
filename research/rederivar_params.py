"""
Re-derivação dos parâmetros vivos com OOS obrigatório (frente E-9, ação 3)
==========================================================================
Contrato pré-registrado em `research/METODOLOGIA_PARAMS.md`. Leia antes.

O grid que produziu `config/params_pares.py` não tinha out-of-sample: media e
selecionava na mesma série. O vencedor de milhares de combinações sobre uma
amostra única tem Sharpe alto por construção. Aqui a seleção acontece no
TREINO, a confirmação no OOS, e o hold-out é tocado UMA vez só, no fim, para o
DSR — e a trava recusa a segunda vez.

Uso:
  python research/rederivar_params.py --par BTCUSDT
  python research/rederivar_params.py --par BTCUSDT --rapido
  python research/rederivar_params.py --par BTCUSDT --holdout --confirmo-uso-unico
"""

import argparse
import io
import itertools
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicadores as ind  # noqa: E402
from backtesting import metricas  # noqa: E402
from backtesting.motor_ensemble import _adx, carregar  # noqa: E402
from backtesting.otimizador import MIN_TRADES, _rodar_com_params  # noqa: E402
from backtesting.walk_forward import _carregar_fng, _cobertura_fng  # noqa: E402

METODOLOGIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "METODOLOGIA_PARAMS.md")
MANIFEST_FNG = "data/fng_historico.manifest.json"
_MARCA_HOLDOUT = "HOLD-OUT PARAMS CONSUMIDO"

# Fronteiras PRE-REGISTRADAS, por data (nao por proporcao) — ver METODOLOGIA.
CORTE_OOS = "2025-08-01"
CORTE_HOLDOUT = "2026-01-01"

# Critérios pré-registrados
DSR_MINIMO = 0.95


class FngObrigatorio(RuntimeError):
    """Sem histórico de Fear & Greed não existe re-derivação válida."""


class HoldoutJaConsumido(RuntimeError):
    """O hold-out é de uso único."""


def _ms(data_iso: str) -> int:
    d = datetime.strptime(data_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _grid(rapido: bool):
    """O grid. Fixo aqui de propósito: mudá-lo depois de ver resultado é
    exatamente a proibição do pré-registro."""
    if rapido:
        stops = (0.015, 0.020, 0.030)
        targets = (0.030, 0.050, 0.060)
        rsis = ((38, 60), (42, 62))
        scores = ((50, 65), (60, 70))
    else:
        stops = (0.010, 0.015, 0.020, 0.025, 0.030)
        targets = (0.020, 0.030, 0.040, 0.050, 0.060, 0.080)
        rsis = ((35, 60), (38, 60), (38, 62), (42, 60), (42, 65), (45, 68))
        scores = ((45, 60), (50, 65), (55, 70), (60, 70), (60, 75))
    for s, t, (rmin, rmax), (sop, sch) in itertools.product(stops, targets, rsis, scores):
        yield {
            "stop_pct": s,
            "target_pct": t,
            "rsi_min": rmin,
            "rsi_max": rmax,
            "score_operar": sop,
            "score_cheio": sch,
        }


def _indicadores(k1h, k4h):
    """Pré-computa tudo uma vez; o grid só reusa (é o que torna a varredura
    viável sem ML)."""
    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]
    f4h = [r[4] for r in k4h]
    m4h = [r[2] for r in k4h]
    n4h = [r[3] for r in k4h]
    ts4h = [r[0] for r in k4h]
    # Mesma pre-computacao de otimizador.grid_search — copiar a API errada
    # daria indicadores diferentes dos que o grid original usava, e a
    # comparacao entre a re-derivacao e o legado deixaria de valer.
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    return {
        "f1h": f1h,
        "m1h": m1h,
        "n1h": n1h,
        "v1h": v1h,
        "ts1h": ts1h,
        "ts4h": ts4h,
        "f4h": f4h,
        "m4h": m4h,
        "n4h": n4h,
        "ema20": ind.ema(f1h, 20),
        "ema50": ind.ema(f1h, 50),
        "rsi14": ind.rsi(f1h, 14),
        "atr14": ind.atr(m1h, n1h, f1h, 14),
        "volr": ind.volume_relativo(v1h, 20),
        "bw": ind.bandwidth(bbu, bbm, bbl),
        "vwap": ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20),
        "adx_vals": _adx(m1h, n1h, f1h, 14),
        "ema20_4h": ind.ema(f4h, 20),
        "ema50_4h": ind.ema(f4h, 50),
    }


def _fatiar(d: dict, ini_ms: int | None, fim_ms: int | None) -> dict:
    """Recorta a janela por TIMESTAMP, mantendo 1h e 4h coerentes.

    Recortar por índice seria mais simples e erraria: as duas séries têm
    origens diferentes (a 1h começa 46h antes da 4h no snapshot deste projeto —
    foi essa diferença que produziu look-ahead em 100% das barras antes de
    I-12b).
    """
    ts1h = d["ts1h"]
    i0 = 0
    i1 = len(ts1h)
    if ini_ms is not None:
        i0 = next((i for i, t in enumerate(ts1h) if t >= ini_ms), len(ts1h))
    if fim_ms is not None:
        i1 = next((i for i, t in enumerate(ts1h) if t >= fim_ms), len(ts1h))
    # `_rodar_com_params` comeca em i=55 (aquecimento dos indicadores); manter
    # 55 barras ANTES da janela evita que a fatia perca o proprio inicio.
    i0_ind = max(0, i0 - 55)

    ts4h = d["ts4h"]
    j0 = next((j for j, t in enumerate(ts4h) if t >= ts1h[i0_ind]), len(ts4h)) if i0_ind else 0
    j1 = next((j for j, t in enumerate(ts4h) if t >= ts1h[i1 - 1]), len(ts4h)) if i1 else 0
    j1 = min(len(ts4h), j1 + 1)

    corte_1h = (
        "f1h",
        "m1h",
        "n1h",
        "v1h",
        "ts1h",
        "ema20",
        "ema50",
        "rsi14",
        "atr14",
        "volr",
        "bw",
        "vwap",
        "adx_vals",
    )
    corte_4h = ("ts4h", "f4h", "m4h", "n4h", "ema20_4h", "ema50_4h")
    out = {k: d[k][i0_ind:i1] for k in corte_1h}
    out.update({k: d[k][j0:j1] for k in corte_4h})
    return out


def _avaliar(fatia: dict, params: dict, fng: dict) -> dict | None:
    return _rodar_com_params(
        fatia["f1h"],
        fatia["m1h"],
        fatia["n1h"],
        fatia["v1h"],
        fatia["ts1h"],
        fatia["ts4h"],
        fatia["f4h"],
        fatia["m4h"],
        fatia["n4h"],
        fatia["ema20"],
        fatia["ema50"],
        fatia["rsi14"],
        fatia["atr14"],
        fatia["volr"],
        fatia["bw"],
        fatia["vwap"],
        fatia["adx_vals"],
        fatia["ema20_4h"],
        fatia["ema50_4h"],
        params,
        fng=fng,
        politica_saida="producao",
    )


def _commit_da_regua() -> str:
    try:
        return (
            subprocess.run(
                ["git", "log", "-1", "--format=%h", "--", "backtesting/regua.py"],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            ).stdout.strip()
            or "desconhecido"
        )
    except Exception:
        return "desconhecido"


def _sha_fng() -> str:
    try:
        with io.open(MANIFEST_FNG, encoding="utf-8") as f:
            return json.load(f).get("sha256", "desconhecido")
    except Exception:
        return "desconhecido"


def holdout_ja_consumido(par: str) -> str | None:
    try:
        with io.open(METODOLOGIA, encoding="utf-8") as f:
            for linha in f:
                if _MARCA_HOLDOUT in linha and f"par={par}" in linha:
                    return linha.strip()
    except FileNotFoundError:
        return None
    return None


def registrar_consumo(par: str, res: dict) -> None:
    """Sem try/except mudo: se não gravar, a trava não existe na próxima vez."""
    linha = (
        f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} | {_MARCA_HOLDOUT} "
        f"(par={par}, corte={CORTE_HOLDOUT}) | "
        f"dsr={res.get('dsr')} n_trials={res.get('n_trials')} "
        f"trades={res.get('trades')} aprovado={res.get('aprovado')} |\n"
    )
    with io.open(METODOLOGIA, "a", encoding="utf-8", newline="\n") as f:
        f.write(linha)


def rodar(par: str, rapido: bool, usar_holdout: bool) -> dict:
    fng = _carregar_fng()
    if not fng:
        raise FngObrigatorio(
            "data/fng_historico.json ausente. Sem o veto de sentimento a\n"
            "  re-derivacao mede outra estrategia (I-12g). Rode:\n"
            "    python scripts/coletar_fng_historico.py"
        )

    k1h, k4h = carregar(par, "1h"), carregar(par, "4h")
    if len(k1h) < 2000:
        raise RuntimeError(f"{par}: apenas {len(k1h)} candles 1h — insuficiente")
    d = _indicadores(k1h, k4h)

    cobertura = _cobertura_fng(fng, d["ts1h"])
    if not cobertura["cobre"]:
        raise FngObrigatorio(
            f"F&G nao cobre o periodo: {cobertura['faltantes']} de "
            f"{cobertura['dias']} dias sem valor "
            f"({cobertura['inicio']} -> {cobertura['fim']})"
        )

    treino = _fatiar(d, None, _ms(CORTE_OOS))
    oos = _fatiar(d, _ms(CORTE_OOS), _ms(CORTE_HOLDOUT))
    print(f"  [JANELAS] treino {len(treino['ts1h'])} candles | oos {len(oos['ts1h'])}")

    # ── 1. SELECAO no treino ──────────────────────────────────────
    combos = list(_grid(rapido))
    print(f"  [TREINO] avaliando {len(combos)} combinacoes...")
    sobreviventes = []
    for n, params in enumerate(combos, 1):
        r = _avaliar(treino, params, fng)
        if r and r["total"] >= MIN_TRADES:
            sobreviventes.append(r)
        if n % 50 == 0:
            print(f"    {n}/{len(combos)}  sobreviventes={len(sobreviventes)}", flush=True)

    n_trials = len(combos)  # AVALIADAS, nao sobreviventes
    print(
        f"  [TREINO] {len(sobreviventes)} de {n_trials} passaram do piso de " f"{MIN_TRADES} trades"
    )
    if not sobreviventes:
        return {
            "par": par,
            "aprovado": False,
            "n_trials": n_trials,
            "motivo": f"nenhuma combinacao alcancou {MIN_TRADES} trades no treino",
        }

    sobreviventes.sort(key=lambda r: r["sharpe"], reverse=True)
    melhor = sobreviventes[0]
    print(f"  [TREINO] melhor: {melhor['params']}")
    print(
        f"           {melhor['total']} trades, Sharpe {melhor['sharpe']:.2f}, "
        f"Ret {melhor['retorno']:.2f}%, DD {melhor['max_dd']:.2f}%"
    )

    # ── 2. CONFIRMACAO no OOS (so o vencedor) ─────────────────────
    r_oos = _avaliar(oos, melhor["params"], fng)
    if not r_oos:
        return {
            "par": par,
            "aprovado": False,
            "n_trials": n_trials,
            "params": melhor["params"],
            "motivo": "vencedor nao operou no OOS",
        }
    print(
        f"  [OOS]    {r_oos['total']} trades, Sharpe {r_oos['sharpe']:.2f}, "
        f"Ret {r_oos['retorno']:.2f}%, DD {r_oos['max_dd']:.2f}%"
    )

    reprovas = []
    if r_oos["total"] < MIN_TRADES:
        reprovas.append(f"OOS com {r_oos['total']} trades (< {MIN_TRADES})")
    if r_oos["sharpe"] <= 0:
        reprovas.append(f"Sharpe OOS {r_oos['sharpe']:.2f} <= 0")
    if r_oos["retorno"] <= 0:
        reprovas.append(f"retorno OOS {r_oos['retorno']:.2f}% <= 0")

    resultado = {
        "par": par,
        "params": melhor["params"],
        "n_trials": n_trials,
        "treino": {k: melhor[k] for k in ("total", "win_rate", "retorno", "max_dd", "sharpe")},
        "oos": {k: r_oos[k] for k in ("total", "win_rate", "retorno", "max_dd", "sharpe")},
        "commit_regua": _commit_da_regua(),
        "hash_snapshot": _sha_fng(),
        "janela": f"treino ..{CORTE_OOS} | oos {CORTE_OOS}..{CORTE_HOLDOUT} | "
        f"holdout {CORTE_HOLDOUT}..",
    }

    if reprovas:
        resultado.update({"aprovado": False, "motivo": "; ".join(reprovas)})
        print(f"  [VEREDITO] REPROVADO no OOS: {resultado['motivo']}")
        print("  [HOLD-OUT] NAO tocado — reprovar antes do hold-out o preserva.")
        return resultado

    if not usar_holdout:
        resultado.update({"aprovado": None, "motivo": "passou no OOS; hold-out ainda nao medido"})
        print("  [VEREDITO] passou no OOS. Para o DSR final:")
        print(
            f"    python research/rederivar_params.py --par {par} --holdout "
            f"--confirmo-uso-unico"
        )
        return resultado

    # ── 3. HOLD-OUT (uso unico) ───────────────────────────────────
    hold = _fatiar(d, _ms(CORTE_HOLDOUT), None)
    r_hold = _avaliar(hold, melhor["params"], fng)
    if not r_hold:
        resultado.update({"aprovado": False, "motivo": "vencedor nao operou no hold-out"})
        return resultado
    sharpes_pt = [r["sharpe"] / (252**0.5) for r in sobreviventes]
    dsr = metricas.deflated_sharpe_ratio(r_hold["retornos_pct"], sharpes_pt)
    resultado["holdout"] = {
        k: r_hold[k] for k in ("total", "win_rate", "retorno", "max_dd", "sharpe")
    }
    resultado["dsr"] = round(dsr, 4)
    resultado["trades"] = r_hold["total"]
    resultado["aprovado"] = bool(dsr >= DSR_MINIMO)
    print(
        f"  [HOLD-OUT] {r_hold['total']} trades, Sharpe {r_hold['sharpe']:.2f}, "
        f"Ret {r_hold['retorno']:.2f}%"
    )
    print(f"  [DSR] {dsr:.4f}  (minimo pre-registrado: {DSR_MINIMO})")
    print(f"  [VEREDITO] {'APROVADO' if resultado['aprovado'] else 'REPROVADO'}")
    return resultado


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-deriva os parametros com OOS (E-9)")
    ap.add_argument("--par", default="BTCUSDT")
    ap.add_argument("--rapido", action="store_true", help="grid menor")
    ap.add_argument(
        "--holdout", action="store_true", help="mede o HOLD-OUT (uso unico) e calcula o DSR final"
    )
    ap.add_argument(
        "--confirmo-uso-unico", action="store_true", help="obrigatorio junto de --holdout"
    )
    args = ap.parse_args()

    if args.holdout:
        if not args.confirmo_uso_unico:
            print(
                "[E-9] --holdout exige --confirmo-uso-unico. O hold-out e de USO\n"
                "      UNICO: quem ja viu o resultado nao decide mais o criterio."
            )
            return 2
        anterior = holdout_ja_consumido(args.par)
        if anterior:
            print(f"[E-9] hold-out de {args.par} JA CONSUMIDO:\n      {anterior}")
            print(
                "      Rodar de novo nao produz evidencia nova — produz um numero\n"
                "      escolhido. Ver research/METODOLOGIA_PARAMS.md."
            )
            return 3

    print(f"\n[E-9] Re-derivacao de parametros — {args.par}")
    print("      pre-registro: research/METODOLOGIA_PARAMS.md")
    try:
        res = rodar(args.par, args.rapido, args.holdout)
    except FngObrigatorio as e:
        print(f"\n[E-9] ABORTADO: {e}")
        return 2

    if args.holdout:
        registrar_consumo(args.par, res)

    print("\n" + json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if res.get("aprovado") else 1


if __name__ == "__main__":
    raise SystemExit(main())
