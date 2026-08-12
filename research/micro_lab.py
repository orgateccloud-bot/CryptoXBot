"""
micro_lab — harness da hipótese de microestrutura (frente E-11)
================================================================
Implementa `METODOLOGIA_MICROESTRUTURA.md`, e SÓ ela. O contrato está lá,
commitado antes de qualquer medição; este arquivo é o instrumento. As cinco
features são as congeladas no documento — nenhuma entra, nenhuma sai:

    1. ofi_book                (livro — exige research/coletar_book.py rodando)
    2. cvd_slope_norm          (tape, com a normalização CORRETA)
    3. desequilibrio_agressor  (tape, janela de 1 min)
    4. intensidade_trades      (tape, 1 min vs média de 20)
    5. spread_rel              (livro)

CONSTANTES DE IMPLEMENTAÇÃO — fixadas aqui ANTES da primeira medição, para que
não exista o grau de liberdade "ajustei a janela depois de ver o IC":
janela curta = 60 min para ofi_book/cvd_slope_norm/spread_rel; 1 min para
desequilibrio/intensidade (como manda o doc); barras de 1h; horizontes 1/4/8.

FAIL-CLOSED: sem a série do livro cobrindo o período, o lab ABORTA — medir a
hipótese com 3 das 5 features seria medir outra hipótese e chamar pelo mesmo
nome. É o mesmo princípio do F&G em I-12g.

USO (comando congelado no doc):
    python research/micro_lab.py --par BTCUSDT              # porção de PESQUISA
    python research/micro_lab.py --par BTCUSDT --status     # cobertura de dados
    python research/micro_lab.py --par BTCUSDT --holdout --confirmo-uso-unico
                                                            # USO ÚNICO, no fim
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from backtesting.metricas import deflated_sharpe_ratio  # noqa: E402
from research.edge_lab import spearman_ic, teste_permutacao_max  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_TAPE = os.path.join(RAIZ, "data", "btc_data.db")
DB_BOOK = os.path.join(RAIZ, "data", "book_btc.db")
DIR_VEREDITOS = os.path.join(RAIZ, "research", "vereditos")
DOC_METODOLOGIA = os.path.join(RAIZ, "research", "METODOLOGIA_MICROESTRUTURA.md")

# Partição por DATA, fixa — copiada do doc, nunca derivada do tamanho da série
HOLDOUT_INICIO_MS = int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)

FEATURES = [
    "ofi_book",
    "cvd_slope_norm",
    "desequilibrio_agressor",
    "intensidade_trades",
    "spread_rel",
]
HORIZONTES = [1, 4, 8]  # barras de 1h
JANELA_CURTA_MIN = 60
JANELA_INTENSIDADE = 20  # médias de 1 min
MIN_BARRAS_PESQUISA = 300
MIN_OBS_HOLDOUT = 200  # critério 2 do doc
MINUTO_MS = 60_000
HORA_MS = 3_600_000

# Custo: 0,10%/lado SPOT taker (doc). O slippage medido do paper entra por
# fora, em custo_roundtrip() — hoje a base tem 1 trade e o valor é ~0; o
# número impresso diz o n para ninguém confundir "não medido" com "zero".
TAXA_LADO = 0.001


# ── features puras ─────────────────────────────────────────────


def cvd_slope_norm(cvd: np.ndarray) -> float:
    """Slope do CVD com a normalização CORRETA: slope * std(x) / std(cvd).

    A versão de produção fazia `slope / std(cvd)` e, por Cauchy-Schwarz,
    ficava presa em |v| <= 1/std(x) — com janela 50, teto 0,069 contra limiar
    0,1: componente morto. Multiplicar por std(x) devolve exatamente a
    correlação de Pearson entre o CVD e o tempo, que vive em [-1, 1]
    independente do tamanho da janela. É a feature 2 congelada no doc.
    """
    y = np.asarray(cvd, dtype=float)
    n = len(y)
    if n < 3:
        return 0.0
    x = np.arange(n, dtype=float)
    sy = y.std()
    if sy == 0:
        return 0.0
    slope = np.polyfit(x, y, 1)[0]
    return float(slope * x.std() / sy)


def desequilibrio_agressor(vol_compra_1min: float, vol_total_1min: float) -> float:
    """Fração do volume iniciada por comprador no último minuto, menos 0,5."""
    if vol_total_1min <= 0:
        return 0.0
    return vol_compra_1min / vol_total_1min - 0.5


def intensidade_trades(n_ultimo_min: float, media_20_min: float) -> float:
    """Contagem do último minuto / média das 20 janelas anteriores."""
    if media_20_min <= 0:
        return 0.0
    return n_ultimo_min / media_20_min


# ── carga de dados (minuto a minuto) ───────────────────────────


def carregar_tape_minuto(symbol: str, db: str = DB_TAPE) -> dict[int, tuple]:
    """{minuto_ms: (n_trades, vol_compra, vol_total, delta_cvd, ultimo_preco)}.

    `trades.timestamp` é ISO ingênuo em hora LOCAL (database.py grava
    datetime.now()); o coletor de book usa epoch do relógio local — converter
    via fromisoformat().timestamp() deixa os dois no MESMO eixo.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    por_minuto: dict[int, list] = {}
    cur = con.execute(
        "SELECT timestamp, preco, volume_btc, direcao FROM trades" " WHERE symbol = ? ORDER BY id",
        (symbol,),
    )
    for ts, preco, vol, direcao in cur:
        try:
            epoch = datetime.fromisoformat(str(ts)[:26]).timestamp()
        except ValueError:
            continue
        minuto = int(epoch * 1000) // MINUTO_MS * MINUTO_MS
        acc = por_minuto.setdefault(minuto, [0, 0.0, 0.0, 0.0, 0.0])
        acc[0] += 1
        if direcao == "COMPRA":
            acc[1] += vol
            acc[3] += vol
        else:
            acc[3] -= vol
        acc[2] += vol
        acc[4] = preco
    con.close()
    return {m: tuple(v) for m, v in por_minuto.items()}


def carregar_book_minuto(symbol: str, db: str = DB_BOOK) -> dict[int, tuple]:
    """{minuto_ms: (ofi, spread_rel_medio)} — produzido por coletar_book.py."""
    if not os.path.exists(db):
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        rows = con.execute(
            "SELECT minuto_ms, ofi, spread_rel_medio FROM book_minuto WHERE symbol = ?",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    return {int(m): (o, s) for m, o, s in rows}


def construir_barras(tape: dict[int, tuple], book: dict[int, tuple]) -> dict:
    """Barras de 1h onde TAPE e LIVRO existem juntos.

    Uma barra fecha em `h` (topo da hora). As features usam os minutos
    (h-60, h]; o preço da barra é o último trade dentro dela. Barras sem
    cobertura de livro são descartadas — e a fração descartada é reportada,
    nunca engolida (o operador precisa saber se está medindo 90% ou 9% do
    período).
    """
    if not tape:
        return {"ts": np.array([]), "px": np.array([]), "X": np.zeros((0, len(FEATURES)))}
    minutos = sorted(tape)
    horas = sorted({(m // HORA_MS) * HORA_MS for m in minutos})

    ts_l, px_l, feats_l = [], [], []
    descartadas_sem_book = 0
    for h in horas:
        janela = [h + HORA_MS - (JANELA_CURTA_MIN - i) * MINUTO_MS for i in range(JANELA_CURTA_MIN)]
        tape_j = [tape.get(m) for m in janela]
        presentes = [t for t in tape_j if t]
        if len(presentes) < JANELA_CURTA_MIN // 2:  # hora sem tape razoável
            continue
        book_j = [book.get(m) for m in janela]
        book_pres = [b for b in book_j if b]
        if len(book_pres) < JANELA_CURTA_MIN // 2:
            descartadas_sem_book += 1
            continue

        px = presentes[-1][4]
        ult = tape_j[-1] or presentes[-1]

        # 2. cvd_slope_norm — série de CVD acumulado nos 60 min
        cvd_serie = np.cumsum([t[3] if t else 0.0 for t in tape_j])
        # 3. desequilíbrio no último minuto com trade
        f3 = desequilibrio_agressor(ult[1], ult[2])
        # 4. intensidade: último minuto vs média dos 20 anteriores
        ns = [t[0] if t else 0 for t in tape_j]
        media20 = float(np.mean(ns[-(JANELA_INTENSIDADE + 1) : -1])) if len(ns) > 1 else 0.0
        f4 = intensidade_trades(ns[-1], media20)
        # 1. OFI somado nos 60 min · 5. spread médio
        f1 = float(sum(b[0] for b in book_pres))
        f5 = float(np.mean([b[1] for b in book_pres]))

        ts_l.append(h + HORA_MS)
        px_l.append(px)
        feats_l.append([f1, cvd_slope_norm(cvd_serie), f3, f4, f5])

    return {
        "ts": np.asarray(ts_l, dtype=np.int64),
        "px": np.asarray(px_l, dtype=float),
        "X": np.asarray(feats_l, dtype=float) if feats_l else np.zeros((0, len(FEATURES))),
        "descartadas_sem_book": descartadas_sem_book,
    }


# ── custo e labels ─────────────────────────────────────────────


def custo_roundtrip(db: str = DB_TAPE) -> tuple[float, int]:
    """0,10%/lado + slippage medido no paper de E-8. Devolve (custo, n_base).

    Com base pequena o slippage tende a 0 — o chamador IMPRIME o n para que
    "não medido ainda" nunca se disfarce de "zero".
    """
    slippage, n = 0.0, 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT preco, preco_saida, pnl_pct FROM sinais"
            " WHERE pnl_usdt IS NOT NULL AND preco > 0 AND preco_saida > 0"
        ).fetchall()
        con.close()
        n = len(rows)
    except sqlite3.Error:
        rows = []
    # Sem medição robusta de slippage por trade no schema atual, o termo fica
    # em 0 e o n reporta a base; quando E-8 acumular fills com desvio
    # registrado, este é o único lugar a tocar.
    return 2 * TAXA_LADO + slippage, n


def labels_liquidos(px: np.ndarray, custo: float) -> tuple[dict, dict]:
    """Retorno forward LÍQUIDO por horizonte, com máscara de validade."""
    labels, validos = {}, {}
    n = len(px)
    for H in HORIZONTES:
        fwd = np.full(n, np.nan)
        if n > H:
            fwd[: n - H] = (px[H:] - px[: n - H]) / px[: n - H] - custo
        labels[H] = np.nan_to_num(fwd)
        validos[H] = ~np.isnan(np.where(np.arange(n) < n - H, 0.0, np.nan))
    return labels, validos


# ── partição por DATA (fixa) ───────────────────────────────────


def particionar(ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(máscara_pesquisa, máscara_holdout) — fronteira é DATA, não fração."""
    pesquisa = ts < HOLDOUT_INICIO_MS
    return pesquisa, ~pesquisa


# ── trava de uso único do hold-out ─────────────────────────────


def caminho_lock(symbol: str) -> str:
    return os.path.join(DIR_VEREDITOS, f"microestrutura_holdout_{symbol}.json")


def holdout_ja_consumido(symbol: str) -> str | None:
    p = caminho_lock(symbol)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh).get("consumido_em", "data desconhecida")
    return None


def registrar_consumo_holdout(symbol: str, resultado: dict) -> None:
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    with open(caminho_lock(symbol), "w", encoding="utf-8") as fh:
        json.dump(resultado, fh, ensure_ascii=False, indent=2, default=float)
    # linha append-only no doc de metodologia (seção "Consumos do hold-out")
    with open(DOC_METODOLOGIA, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n- {resultado['consumido_em']} · {symbol} · "
            f"veredito **{resultado['veredito']}** · lock "
            f"`{os.path.basename(caminho_lock(symbol))}`\n"
        )


# ── medições ───────────────────────────────────────────────────


def _trials_path() -> str:
    return os.path.join(DIR_VEREDITOS, "microestrutura_trials.json")


def registrar_trials(symbol: str, sharpes: list[float]) -> dict:
    """Contador append-only de combinações avaliadas — alimenta o DSR."""
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    p = _trials_path()
    dados = {"rodadas": [], "sharpes": []}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            dados = json.load(fh)
    dados["rodadas"].append(
        {"symbol": symbol, "em": datetime.now().isoformat(timespec="seconds"), "n": len(sharpes)}
    )
    dados["sharpes"].extend(float(s) for s in sharpes)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2)
    return dados


def retornos_carteira(feature: np.ndarray, retorno_fwd: np.ndarray, custo: float) -> np.ndarray:
    """Retornos líquidos de posição = sinal(z-score da feature), pagando custo
    a cada inversão. Carteira deliberadamente ingênua — o critério 3 pergunta
    se o SINAL paga o custo, não se uma otimização de carteira paga."""
    feature = np.asarray(feature, dtype=float)
    z = (feature - feature.mean()) / (feature.std() or 1.0)
    pos = np.sign(z)
    troca = np.abs(np.diff(pos, prepend=pos[:1])) / 2.0  # 1 a cada inversão
    return pos * retorno_fwd - troca * custo


def sharpe_carteira(feature: np.ndarray, retorno_fwd: np.ndarray, custo: float) -> float:
    """Sharpe líquido anualizado da carteira ingênua (barras de 1h)."""
    if len(feature) < 30:
        return 0.0
    ret = retornos_carteira(feature, retorno_fwd, custo)
    if ret.std() == 0:
        return 0.0
    return float(ret.mean() / ret.std() * np.sqrt(24 * 365))


def rodar_pesquisa(symbol: str, tape_db: str = DB_TAPE, book_db: str = DB_BOOK) -> dict:
    tape = carregar_tape_minuto(symbol, tape_db)
    book = carregar_book_minuto(symbol, book_db)
    barras = construir_barras(tape, book)
    ts, px, X = barras["ts"], barras["px"], barras["X"]

    pesquisa, _ = particionar(ts)
    n_pesq = int(pesquisa.sum())
    if n_pesq < MIN_BARRAS_PESQUISA:
        # FAIL-CLOSED, com o diagnóstico completo: quanto de tape existe,
        # quanto de livro existe, e o que falta para a primeira medição.
        raise SystemExit(
            f"[MICRO] Coleta insuficiente para medir: {n_pesq} barras completas na porção de "
            f"PESQUISA (mínimo {MIN_BARRAS_PESQUISA}).\n"
            f"  Barras com tape mas SEM livro: {barras.get('descartadas_sem_book', 0)}\n"
            f"  A série do livro vem de research/coletar_book.py — ele precisa rodar 24/7.\n"
            f"  Nada foi medido; a metodologia continua virgem."
        )

    Xp, pxp = X[pesquisa], px[pesquisa]
    custo, n_base_custo = custo_roundtrip(tape_db)
    labels, validos = labels_liquidos(pxp, custo)

    perm = teste_permutacao_max(Xp, labels, validos)
    sharpes = [
        sharpe_carteira(Xp[:, j], labels[H], custo)
        for j in range(len(FEATURES))
        for H in HORIZONTES
    ]
    trials = registrar_trials(symbol, sharpes)

    melhor_j = perm["melhor_feature_idx"]
    resultado = {
        "symbol": symbol,
        "fase": "PESQUISA",
        "em": datetime.now().isoformat(timespec="seconds"),
        "n_barras": n_pesq,
        "custo_roundtrip": custo,
        "n_base_slippage": n_base_custo,
        "ic_max_abs": perm["obs_max"],
        "p_valor": perm["p_valor"],
        "melhor_feature": FEATURES[melhor_j],
        "melhor_horizonte": perm["melhor_horizonte"],
        "melhor_ic": perm["melhor_ic"],
        "n_trials_acumulado": len(trials["sharpes"]),
        "combo_promovido": {"feature": FEATURES[melhor_j], "H": perm["melhor_horizonte"]},
    }
    os.makedirs(DIR_VEREDITOS, exist_ok=True)
    with open(
        os.path.join(DIR_VEREDITOS, f"microestrutura_pesquisa_{symbol}.json"),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(resultado, fh, ensure_ascii=False, indent=2, default=float)
    return resultado


def avaliar_holdout(
    symbol: str,
    confirmo_uso_unico: bool = False,
    tape_db: str = DB_TAPE,
    book_db: str = DB_BOOK,
) -> dict:
    """USO ÚNICO. Avalia os 5 critérios do doc sobre o combo promovido."""
    if not confirmo_uso_unico:
        raise SystemExit(
            "Hold-out é de USO ÚNICO (METODOLOGIA_MICROESTRUTURA.md). "
            "Rode com --confirmo-uso-unico apenas quando a pesquisa terminar."
        )
    anterior = holdout_ja_consumido(symbol)
    if anterior:
        raise SystemExit(
            f"Hold-out de {symbol} JÁ FOI CONSUMIDO em {anterior}. O resultado que "
            f"existe é o resultado. Não há segunda rodada."
        )
    pesq_path = os.path.join(DIR_VEREDITOS, f"microestrutura_pesquisa_{symbol}.json")
    if not os.path.exists(pesq_path):
        raise SystemExit(
            "Não existe pesquisa registrada — o hold-out avalia o combo promovido dela."
        )
    with open(pesq_path, encoding="utf-8") as fh:
        combo = json.load(fh)["combo_promovido"]

    tape = carregar_tape_minuto(symbol, tape_db)
    book = carregar_book_minuto(symbol, book_db)
    barras = construir_barras(tape, book)
    ts, px, X = barras["ts"], barras["px"], barras["X"]
    _, holdout = particionar(ts)

    Xh, pxh = X[holdout], px[holdout]
    n = len(pxh)
    custo, _ = custo_roundtrip(tape_db)
    labels, _ = labels_liquidos(pxh, custo)

    j = FEATURES.index(combo["feature"])
    H = int(combo["H"])
    corte = n - H if n > H else 0
    ic = spearman_ic(Xh[:corte, j], labels[H][:corte]) if corte >= 3 else 0.0

    # permutação SÓ do combo promovido (uma hipótese no hold-out, não quinze)
    rng = np.random.default_rng(7)
    nulos = []
    for _ in range(1000):
        off = int(rng.integers(H + 1, max(H + 2, corte - H - 1)))
        rot = np.concatenate([labels[H][off:corte], labels[H][:off]])
        nulos.append(abs(spearman_ic(Xh[:corte, j], rot)))
    p = float((np.sum(np.asarray(nulos) >= abs(ic)) + 1) / 1001)

    sh = sharpe_carteira(Xh[:, j], labels[1], custo)
    with open(_trials_path(), encoding="utf-8") as fh:
        sharpes_trials = json.load(fh)["sharpes"]
    dsr = deflated_sharpe_ratio(
        [float(r) for r in retornos_carteira(Xh[:, j], labels[1], custo)], sharpes_trials
    )
    margem = (
        float(labels[H][:corte][Xh[:corte, j] > np.median(Xh[:corte, j])].mean()) if corte else 0.0
    )

    criterios = {
        "ic_oos > 0.03 com p < 0.01": bool(ic > 0.03 and p < 0.01),
        f"n >= {MIN_OBS_HOLDOUT}": bool(corte >= MIN_OBS_HOLDOUT),
        "sharpe_liquido > 0.5": bool(sh > 0.5),
        "dsr >= 0.95": bool(dsr >= 0.95),
        "margem_liquida > 0": bool(margem > 0),
    }
    resultado = {
        "symbol": symbol,
        "fase": "HOLDOUT",
        "consumido_em": datetime.now().isoformat(timespec="seconds"),
        "combo": combo,
        "n_obs": corte,
        "ic": ic,
        "p_valor": p,
        "sharpe_liquido": sh,
        "dsr": dsr,
        "margem_liquida": margem,
        "criterios": criterios,
        "veredito": "PASS" if all(criterios.values()) else "FAIL",
    }
    registrar_consumo_holdout(symbol, resultado)
    return resultado


# ── CLI ────────────────────────────────────────────────────────


def cmd_status(symbol: str) -> int:
    tape = carregar_tape_minuto(symbol)
    book = carregar_book_minuto(symbol)
    barras = construir_barras(tape, book)
    n = len(barras["ts"])
    pesq = int(particionar(barras["ts"])[0].sum()) if n else 0
    print(f"\n  {symbol}")
    print(f"  minutos de tape : {len(tape):>8}")
    print(f"  minutos de livro: {len(book):>8}  (coletar_book.py)")
    print(f"  barras completas: {n:>8}  (pesquisa: {pesq}, mínimo p/ medir: {MIN_BARRAS_PESQUISA})")
    print(f"  descartadas sem livro: {barras.get('descartadas_sem_book', 0)}")
    lock = holdout_ja_consumido(symbol)
    print(f"  hold-out: {'CONSUMIDO em ' + lock if lock else 'virgem (abre 2026-12-01)'}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harness pré-registrado de microestrutura (E-11)")
    ap.add_argument("--par", default="BTCUSDT")
    ap.add_argument("--status", action="store_true", help="cobertura de dados, sem medir")
    ap.add_argument("--holdout", action="store_true", help="USO ÚNICO — só no fim da pesquisa")
    ap.add_argument("--confirmo-uso-unico", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        return cmd_status(args.par)
    if args.holdout:
        r = avaliar_holdout(args.par, confirmo_uso_unico=args.confirmo_uso_unico)
    else:
        r = rodar_pesquisa(args.par)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
