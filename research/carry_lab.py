"""
Carry lab — medição do funding/basis delta-neutro (pré-registrado)
==================================================================
Contrato: research/METODOLOGIA_CARRY.md (escrito antes desta medição).

Variante A (PRIMÁRIA, zero parâmetros): entra no início, sai no fim, coleta
todo o funding — positivo e negativo. Impossível de sobreajustar.
Variante B (robustez, NÃO decide): filtro de média móvel 7d do funding.

Contabilidade (toda pré-registrada):
  - Retorno sobre NOTIONAL no período = Σ funding_rates (premissa de
    rebalanceamento para notional constante — como carry é cotado).
  - Custo round-trip 0.30% do notional, UMA vez na variante A.
  - Capital = 1.25 × notional (1.00 spot + 0.25 margem do short 4×).
  - Anualização SIMPLES (lucro é retirado no rebalanceamento; conservador
    frente à composta).

Uso:
  python research/carry_lab.py                 # pesquisa (hold-out intocado)
  python research/carry_lab.py --variante B
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "btc_data.db"
)
METODOLOGIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "METODOLOGIA_CARRY.md")
_MARCA_HOLDOUT = "HOLD-OUT CONSUMIDO"

# I-11: a fronteira do hold-out vem de edge_lab — uma data unica para toda a
# pesquisa do repositorio. Duas datas seriam duas metodologias.
from research.edge_lab import HOLDOUT_INICIO_MS  # noqa: E402

PARES = ("BTCUSDT", "ETHUSDT")
CUSTO_ROUND_TRIP = 0.003   # 0.30% do notional (taker nas 2 pernas, ida e volta)
CAPITAL_MULT = 1.25        # 1.00 spot + 0.25 margem (short perp 4x)
# Mantida so como documentacao da origem da data em edge_lab; nao decide mais
# fronteira nenhuma (I-11).
HOLDOUT_FRAC = 0.35
MS_ANO = 365.25 * 86400 * 1000

# Pisos pré-registrados (METODOLOGIA_CARRY.md)
MIN_ANUALIZADO = 0.10      # 10% a.a. sobre capital
MIN_MESES_POS = 0.70       # 70% dos meses positivos
PIOR_MES_LIMITE = -0.03    # pior mês > -3%
PIOR_ANO_LIMITE = -0.05    # pior ano (regime de funding adverso) > -5% a.a.
MIN_MESES = 24


def carregar_funding(symbol) -> tuple[np.ndarray, np.ndarray]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT funding_time, funding_rate FROM funding WHERE symbol=? ORDER BY funding_time ASC",
        (symbol,),
    ).fetchall()
    conn.close()
    if not rows:
        return np.array([]), np.array([])
    ts = np.array([r[0] for r in rows], dtype=np.int64)
    rate = np.array([r[1] for r in rows], dtype=float)
    return ts, rate


def porcao(ts, rate, holdout=False):
    """Recorta pesquisa/hold-out pela DATA fixa, nao por fracao (I-11).

    Era `int(len(ts) * (1 - HOLDOUT_FRAC))`: a fronteira andava a cada coleta,
    exatamente como em edge_lab. Aqui o efeito seria ainda mais direto, porque a
    serie de funding cresce a cada 8 HORAS — a divisa entre pesquisa e teste cego
    se moveria tres vezes por dia.
    """
    if len(ts) == 0:
        return ts, rate
    corte = int(np.searchsorted(np.asarray(ts), HOLDOUT_INICIO_MS, side="left"))
    return (ts[corte:], rate[corte:]) if holdout else (ts[:corte], rate[:corte])


def _mes(ts_ms) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{d.year}-{d.month:02d}"


def _ano(ts_ms) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).year


def variante_A(ts, rate) -> dict:
    """PRIMÁRIA: buy-and-hold da estrutura. Zero parâmetros."""
    if len(rate) < 2:
        return {"n": 0}
    anos = (ts[-1] - ts[0]) / MS_ANO
    ret_notional = float(rate.sum())                      # Σ funding
    ret_liq_notional = ret_notional - CUSTO_ROUND_TRIP    # custo 1x
    ret_capital = ret_liq_notional / CAPITAL_MULT
    anualizado = ret_capital / anos if anos > 0 else 0.0

    # mensal: soma do funding no mês / capital (custo alocado no 1º mês)
    por_mes = defaultdict(float)
    for t, r in zip(ts, rate):
        por_mes[_mes(t)] += r
    meses = sorted(por_mes)
    if meses:
        por_mes[meses[0]] -= CUSTO_ROUND_TRIP
    mensais = {m: por_mes[m] / CAPITAL_MULT for m in meses}
    vals = np.array([mensais[m] for m in meses])
    frac_pos = float((vals > 0).mean()) if len(vals) else 0.0
    pior_mes = float(vals.min()) if len(vals) else 0.0
    pior_mes_nome = meses[int(vals.argmin())] if len(vals) else "—"

    # por ano: retorno anualizado do ano e funding médio (identifica regime adverso)
    por_ano_soma = defaultdict(float)
    por_ano_n = defaultdict(int)
    for t, r in zip(ts, rate):
        por_ano_soma[_ano(t)] += r
        por_ano_n[_ano(t)] += 1
    anuais = {}
    for a in sorted(por_ano_soma):
        n = por_ano_n[a]
        if n < 90:  # ano-borda com poucos pagamentos: fora do teste de regime
            continue
        frac_ano = n / (3 * 365.25)  # fração de ano coberta (3 pagamentos/dia)
        ret_ano_cap = por_ano_soma[a] / CAPITAL_MULT
        anuais[a] = {
            "ret_cap": ret_ano_cap,
            "anualizado": ret_ano_cap / frac_ano if frac_ano > 0 else 0.0,
            "funding_medio": por_ano_soma[a] / n,
            "n": n,
        }
    return {
        "n": len(rate), "anos": anos,
        "ret_notional": ret_notional, "ret_capital": ret_capital,
        "anualizado": anualizado,
        "frac_meses_pos": frac_pos, "n_meses": len(meses),
        "pior_mes": pior_mes, "pior_mes_nome": pior_mes_nome,
        "mensais": mensais, "anuais": anuais,
        "frac_pagamentos_neg": float((rate < 0).mean()),
        "funding_medio": float(rate.mean()),
    }


def variante_B(ts, rate, janela_dias=7) -> dict:
    """ROBUSTEZ (não decide): mantém a estrutura só quando a média móvel de 7d
    do funding é positiva. Paga 0.30% a cada alternância (entrada+saída)."""
    if len(rate) < 30:
        return {"n": 0}
    j = janela_dias * 3  # 3 pagamentos por dia
    dentro = False
    ret = 0.0
    alternancias = 0
    for i in range(len(rate)):
        if i >= j:
            media = rate[i - j : i].mean()  # causal: só passado
            quer_dentro = media > 0
            if quer_dentro != dentro:
                ret -= CUSTO_ROUND_TRIP / 2  # meia perna por transição
                alternancias += 1
                dentro = quer_dentro
        if dentro:
            ret += rate[i]
    anos = (ts[-1] - ts[0]) / MS_ANO
    ret_cap = ret / CAPITAL_MULT
    return {"n": len(rate), "anos": anos, "ret_capital": ret_cap,
            "anualizado": ret_cap / anos if anos > 0 else 0.0,
            "alternancias": alternancias}


def avaliar(holdout=False, variante="A") -> dict:
    res = {}
    for sym in PARES:
        ts, rate = carregar_funding(sym)
        if len(ts) == 0:
            continue
        ts, rate = porcao(ts, rate, holdout)
        res[sym] = variante_A(ts, rate) if variante == "A" else variante_B(ts, rate)
    return res


def imprimir(res, variante="A", holdout=False):
    rotulo = "HOLD-OUT" if holdout else "PESQUISA (hold-out intocado)"
    print("=" * 74)
    print(f"  CARRY DELTA-NEUTRO (funding) — variante {variante} — {rotulo}")
    print(f"  custo {CUSTO_ROUND_TRIP*100:.2f}% round-trip | capital {CAPITAL_MULT}×notional")
    print("=" * 74)

    if variante == "B":
        for sym, r in res.items():
            if not r.get("n"):
                continue
            print(f"  {sym}: {r['anualizado']*100:+.2f}% a.a. sobre capital "
                  f"({r['alternancias']} alternâncias em {r['anos']:.1f} anos)")
        print("\n  (variante B é robustez — NÃO decide o veredito)")
        return None

    for sym, r in res.items():
        if not r.get("n"):
            continue
        print(f"\n  {sym} — {r['n']} pagamentos, {r['anos']:.1f} anos, "
              f"{r['n_meses']} meses")
        print(f"    funding médio/8h: {r['funding_medio']:+.6f} | "
              f"pagamentos negativos: {r['frac_pagamentos_neg']*100:.1f}%")
        print(f"    retorno s/ notional: {r['ret_notional']*100:+.2f}%  ->  "
              f"s/ capital: {r['ret_capital']*100:+.2f}%")
        print(f"    ANUALIZADO s/ capital: {r['anualizado']*100:+.2f}% a.a.")
        print(f"    meses positivos: {r['frac_meses_pos']*100:.1f}% | "
              f"pior mês: {r['pior_mes']*100:+.2f}% ({r['pior_mes_nome']})")
        if r["anuais"]:
            pior_a = min(r["anuais"], key=lambda a: r["anuais"][a]["anualizado"])
            print(f"    por ano (anualizado s/ capital):")
            for a, d in r["anuais"].items():
                marca = "  <-- pior" if a == pior_a else ""
                print(f"      {a}: {d['anualizado']*100:+7.2f}% a.a. "
                      f"(funding médio {d['funding_medio']:+.6f}){marca}")

    # ── veredito: média equal-weighted dos pares (primária) ──
    validos = [r for r in res.values() if r.get("n")]
    if not validos:
        print("\n  Sem dados.")
        return None
    anual = float(np.mean([r["anualizado"] for r in validos]))
    frac_pos = float(np.mean([r["frac_meses_pos"] for r in validos]))
    pior_mes = float(min(r["pior_mes"] for r in validos))
    n_meses = int(min(r["n_meses"] for r in validos))
    piores_anos = []
    for r in validos:
        if r["anuais"]:
            piores_anos.append(min(d["anualizado"] for d in r["anuais"].values()))
    pior_ano = float(min(piores_anos)) if piores_anos else 0.0

    print("\n" + "-" * 74)
    print("  Regra de decisão (METODOLOGIA_CARRY.md — equal-weighted BTC/ETH):")
    c1 = anual > MIN_ANUALIZADO
    c2 = frac_pos >= MIN_MESES_POS
    c3 = pior_mes > PIOR_MES_LIMITE
    c4 = pior_ano > PIOR_ANO_LIMITE
    c5 = n_meses >= MIN_MESES
    print(f"    [{'x' if c1 else ' '}] anualizado > 10%              ({anual*100:+.2f}% a.a.)")
    print(f"    [{'x' if c2 else ' '}] >= 70% dos meses positivos    ({frac_pos*100:.1f}%)")
    print(f"    [{'x' if c3 else ' '}] pior mês > -3%                ({pior_mes*100:+.2f}%)")
    print(f"    [{'x' if c4 else ' '}] pior ano > -5% a.a.           ({pior_ano*100:+.2f}% a.a.)")
    print(f"    [{'x' if c5 else ' '}] >= 24 meses de dados          ({n_meses})")
    ok = all([c1, c2, c3, c4, c5])
    if ok:
        print("\n  >> HÁ EDGE ESTRUTURAL — prosseguir ao hold-out (USO ÚNICO).")
    else:
        print("\n  >> FAIL — pré-registrado: não construir, sem re-registro de critério.")
    print("=" * 74)
    return {"anualizado": anual, "frac_meses_pos": frac_pos, "pior_mes": pior_mes,
            "pior_ano": pior_ano, "n_meses": n_meses, "aprovado": ok}


def holdout_ja_consumido(variante: str) -> str | None:
    """Registro anterior do hold-out desta variante, ou None se virgem (I-11).

    carry_lab NAO TINHA TRAVA NENHUMA: `--holdout` era so uma flag booleana, sem
    confirmacao e sem registro. Dava para rodar o teste cego quantas vezes se
    quisesse, e cada rodada envenenava a seguinte — quem ja viu o resultado nao
    decide mais as por acaso.
    """
    try:
        with open(METODOLOGIA, encoding="utf-8") as fp:
            for linha in fp:
                if _MARCA_HOLDOUT in linha and f"variante={variante}" in linha:
                    return linha.strip()
    except FileNotFoundError:
        return None
    return None


def registrar_consumo_holdout(variante: str, resultado: dict) -> None:
    """Grava o consumo do hold-out. Sem try/except mudo: se nao gravar, a trava
    nao existe para a proxima execucao."""
    linha = (
        f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} | {_MARCA_HOLDOUT} "
        f"(variante={variante}, inicio_ms={HOLDOUT_INICIO_MS}) | "
        f"anualizado={resultado.get('anualizado', float('nan')):+.4f} "
        f"meses={resultado.get('n_meses', 0)} "
        f"aprovado={resultado.get('aprovado')} |\n"
    )
    with open(METODOLOGIA, "a", encoding="utf-8") as fp:
        fp.write(linha)


def main():
    ap = argparse.ArgumentParser(description="Carry lab — funding delta-neutro")
    ap.add_argument("--variante", default="A", choices=["A", "B"])
    ap.add_argument("--holdout", action="store_true",
                    help="USO ÚNICO — só após a pesquisa aprovar")
    ap.add_argument("--confirmo-uso-unico", action="store_true",
                    help="obrigatório junto de --holdout (I-11)")
    ap.add_argument("--permitir-reuso", action="store_true",
                    help="só para testar a própria trava; invalida o veredito")
    args = ap.parse_args()

    if args.holdout:
        if not args.confirmo_uso_unico:
            raise SystemExit(
                "--holdout exige --confirmo-uso-unico. O hold-out do carry e de USO\n"
                "UNICO e ate agora nao tinha trava alguma: era so uma flag. Ver\n"
                "research/METODOLOGIA_CARRY.md."
            )
        anterior = holdout_ja_consumido(args.variante)
        if anterior and not args.permitir_reuso:
            raise SystemExit(
                f"HOLD-OUT DA VARIANTE {args.variante} JA FOI CONSUMIDO.\n"
                f"  Registro anterior: {anterior}\n"
                f"  Reavaliar exige DADO NOVO, pre-registrado antes da medicao."
            )

    resultado = avaliar(args.holdout, args.variante)
    imprimir(resultado, args.variante, args.holdout)
    if args.holdout:
        registrar_consumo_holdout(args.variante, resultado)


if __name__ == "__main__":
    main()
