"""
Edge Lab — harness de descoberta de edge (pré-registrado em METODOLOGIA.md)
============================================================================
Responde à pergunta que a Etapa 1 do gate deixou aberta: *alguma feature tem
poder preditivo real sobre o retorno futuro, out-of-sample, depois de
corrigir por quantas hipóteses testei?* Se nada tiver, "não há edge
direcional tradeable neste conjunto de features em BTC 1h" é o resultado.

Método (resumo — contrato completo em research/METODOLOGIA.md):
  - Features: as 11 de ml_filtro.extrair_features (o sinal real do projeto).
  - Label primário: retorno futuro em H candles; edge = |Spearman IC|.
  - Correção de múltiplas hipóteses: teste de permutação por ROTAÇÃO CIRCULAR
    do label (preserva autocorrelação, quebra alinhamento), max-statistic
    sobre a família de 11 features × {8, 24} horizontes.
  - Hold-out temporal (últimos 35%) travado: uso único via avaliar_holdout().
  - Corroboração: replicação de sinal do IC em ETH/SOL; AUC do XGBoost
    combinado via purged CV (secundária).

Funções puras (operam sobre arrays) no topo — testáveis sem banco. I/O e
runner no fim.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ml_filtro import extrair_features  # noqa: E402
from validacao import purged_cv_auc, purged_kfold_indices  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "btc_data.db")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

WARMUP = 55  # extrair_features retorna None antes disso
FEATURE_NAMES = [
    "dist_ema20", "dist_ema50", "rsi", "atr_rel", "vol_rel_norm",
    "boll_rel", "dist_vwap", "var_1", "var_4", "var_24", "bb_pos",
]
HORIZONTES = (8, 24)
HOLDOUT_FRAC = 0.35
N_PERM = 1000
IC_PISO_ECONOMICO = 0.03  # regra de decisão (METODOLOGIA.md)
METODOLOGIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "METODOLOGIA.md")


# ══════════════════════════════════════════════════════════════
# Estatística de edge (puras)
# ══════════════════════════════════════════════════════════════


def spearman_ic(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman IC (correlação de rank). 0.0 se degenerado (< 3 pts ou
    variância de rank nula)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 3 or len(a) != len(b):
        return 0.0
    ra = rankdata(a) - (len(a) + 1) / 2
    rb = rankdata(b) - (len(b) + 1) / 2
    denom = np.sqrt((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / denom) if denom > 0 else 0.0


def label_forward_return(fechamentos: np.ndarray, H: int) -> tuple[np.ndarray, np.ndarray]:
    """fwd_ret[i] = (f[i+H]-f[i])/f[i]. Máscara False para i >= n-H (sem futuro)."""
    f = np.asarray(fechamentos, dtype=float)
    n = len(f)
    fwd = np.full(n, np.nan)
    if n > H:
        fwd[: n - H] = (f[H:] - f[: n - H]) / f[: n - H]
    valido = ~np.isnan(fwd)
    return fwd, valido


def label_triple_barrier(
    fechamentos: np.ndarray, maximas: np.ndarray, minimas: np.ndarray,
    alvo_pct: float, stop_pct: float, H: int,
) -> tuple[np.ndarray, np.ndarray]:
    """1 se o ALVO (+alvo_pct) é tocado ANTES do stop (-stop_pct) dentro de H
    candles; 0 se stop primeiro OU timeout (nenhum tocado). Ordem intra-candle:
    checa mínima (stop) antes de máxima (alvo) — conservador, igual à
    convenção do walk_forward. Máscara False para i >= n-H."""
    f = np.asarray(fechamentos, dtype=float)
    m = np.asarray(maximas, dtype=float)
    n = np.asarray(minimas, dtype=float)
    N = len(f)
    y = np.zeros(N, dtype=float)
    valido = np.zeros(N, dtype=bool)
    for i in range(N - H):
        entrada = f[i]
        nivel_stop = entrada * (1 - stop_pct)
        nivel_alvo = entrada * (1 + alvo_pct)
        valido[i] = True
        for j in range(i + 1, i + H + 1):
            if n[j] <= nivel_stop:  # stop primeiro (conservador)
                y[i] = 0.0
                break
            if m[j] >= nivel_alvo:
                y[i] = 1.0
                break
        # timeout -> y[i] permanece 0.0
    return y, valido


def ic_familia(
    X: np.ndarray, labels_por_h: dict[int, np.ndarray], validos_por_h: dict[int, np.ndarray],
) -> dict[tuple[int, int], float]:
    """IC de cada (feature, horizonte) sobre as amostras válidas. Retorna
    {(idx_feature, H): IC}. Pares não-finitos (NaN de feature ou label) são
    descartados — importante porque a rotação da máscara no teste de
    permutação pode parear uma linha com feature NaN."""
    ics = {}
    for H, label in labels_por_h.items():
        vmask = validos_por_h[H]
        lab = label[vmask]
        for fi in range(X.shape[1]):
            fv = X[vmask, fi]
            fin = np.isfinite(fv) & np.isfinite(lab)
            ics[(fi, H)] = spearman_ic(fv[fin], lab[fin])
    return ics


def _rotacao(x: np.ndarray, offset: int) -> np.ndarray:
    return np.concatenate([x[offset:], x[:offset]])


def teste_permutacao_max(
    X: np.ndarray, labels_por_h: dict[int, np.ndarray], validos_por_h: dict[int, np.ndarray],
    n_perm: int = N_PERM, seed: int = 42,
) -> dict:
    """Teste de permutação por rotação circular com max-statistic.
    Rotaciona cada label por um offset aleatório grande (preserva
    autocorrelação, quebra alinhamento), recomputa |IC| de toda a família e
    guarda o máximo -> distribuição nula de max|IC|. p = fração de nulls
    >= obs_max. Corrige simultaneamente múltiplas hipóteses + autocorrelação."""
    rng = np.random.default_rng(seed)
    ics_obs = ic_familia(X, labels_por_h, validos_por_h)
    obs_max = max(abs(v) for v in ics_obs.values())

    n = X.shape[0]
    hmax = max(labels_por_h)
    lo, hi = hmax + 1, n - hmax - 1  # offsets que garantem decorrelação
    null_maxes = np.empty(n_perm)
    for p in range(n_perm):
        off = int(rng.integers(lo, hi))
        labels_rot = {H: _rotacao(lab, off) for H, lab in labels_por_h.items()}
        # a máscara de validade acompanha a rotação (o label rotacionado em i
        # é válido se o original na posição rotacionada era válido)
        validos_rot = {H: _rotacao(v, off) for H, v in validos_por_h.items()}
        ics_p = ic_familia(X, labels_rot, validos_rot)
        null_maxes[p] = max(abs(v) for v in ics_p.values())

    p_valor = float((np.sum(null_maxes >= obs_max) + 1) / (n_perm + 1))  # +1: conservador
    melhor = max(ics_obs.items(), key=lambda kv: abs(kv[1]))
    return {
        "obs_max": obs_max,
        "p_valor": p_valor,
        "melhor_feature_idx": melhor[0][0],
        "melhor_horizonte": melhor[0][1],
        "melhor_ic": melhor[1],
        "ics_obs": ics_obs,
        "null_p95": float(np.quantile(null_maxes, 0.95)),
    }


# ══════════════════════════════════════════════════════════════
# Construção da matriz de features (I/O + cache)
# ══════════════════════════════════════════════════════════════


def carregar_klines(symbol: str, intervalo: str = "1h"):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT timestamp, maxima, minima, fechamento, volume FROM klines "
        "WHERE symbol=? AND intervalo=? ORDER BY timestamp ASC",
        (symbol, intervalo),
    ).fetchall()
    conn.close()
    ts = np.array([r[0] for r in rows])
    m = np.array([r[1] for r in rows], dtype=float)
    n = np.array([r[2] for r in rows], dtype=float)
    f = np.array([r[3] for r in rows], dtype=float)
    v = np.array([r[4] for r in rows], dtype=float)
    return ts, m, n, f, v


def construir_matriz_features(f, m, n, v, symbol: str, usar_cache=True) -> np.ndarray:
    """Matriz (N, 11) chamando ml_filtro.extrair_features EXATAMENTE (mesmo
    sinal que o projeto usa). Linhas < WARMUP viram NaN. Caro (O(n^2)) — cacheado
    em disco por symbol+hash de tamanho."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"feat_{symbol}_{len(f)}.npy")
    if usar_cache and os.path.exists(cache):
        return np.load(cache)
    N = len(f)
    X = np.full((N, len(FEATURE_NAMES)), np.nan)
    fl, ml, nl, vl = f.tolist(), m.tolist(), n.tolist(), v.tolist()
    for i in range(WARMUP, N):
        feat = extrair_features(fl, ml, nl, vl, i)
        if feat is not None:
            X[i] = feat
    if usar_cache:
        np.save(cache, X)
    return X


def _labels_para(f, m, n, H, alvo=0.04, stop=0.02):
    fwd, vfwd = label_forward_return(f, H)
    return fwd, vfwd


# ══════════════════════════════════════════════════════════════
# Runner de pesquisa (só porção de pesquisa — NUNCA toca hold-out)
# ══════════════════════════════════════════════════════════════


def _porcao_pesquisa(N: int) -> tuple[int, int]:
    """[warmup, corte) — os primeiros (1-HOLDOUT_FRAC) candles."""
    corte = int(N * (1 - HOLDOUT_FRAC))
    return WARMUP, corte


def preparar_para_ic(X, f, m, n, ini, fim):
    """Recorta features + labels forward-return (H em HORIZONTES) ao intervalo
    [ini, fim), alinhados. Retorna (Xr, labels_por_h, validos_por_h) onde as
    máscaras já excluem linhas com feature NaN."""
    Xr = X[ini:fim]
    fr, mr, nr = f[ini:fim], m[ini:fim], n[ini:fim]
    feat_ok = ~np.isnan(Xr).any(axis=1)
    labels, validos = {}, {}
    for H in HORIZONTES:
        fwd, vfwd = label_forward_return(fr, H)
        labels[H] = fwd
        validos[H] = vfwd & feat_ok
    return Xr, labels, validos


def rodar_pesquisa(symbol="BTCUSDT", com_xgb=True, seed=42) -> dict:
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    ini, fim = _porcao_pesquisa(len(f))
    Xr, labels, validos = preparar_para_ic(X, f, m, n, ini, fim)

    perm = teste_permutacao_max(Xr, labels, validos, n_perm=N_PERM, seed=seed)

    # AUC secundária do XGBoost combinado (barreira simétrica ±2%, base 50%)
    auc_xgb = None
    if com_xgb:
        auc_xgb = _auc_xgb_combinado(X, f, m, n, ini, fim)

    return {
        "symbol": symbol,
        "periodo_pesquisa": (int(ts[ini]), int(ts[fim - 1])),
        "n_amostras": int(validos[HORIZONTES[0]].sum()),
        "perm": perm,
        "auc_xgb": auc_xgb,
    }


def _auc_xgb_combinado(X, f, m, n, ini, fim) -> dict | None:
    """Purged-CV AUC do XGBoost nas 11 features sobre barreira simétrica ±2%,
    H=8 (a JANELA do bot). Secundária — corroboração, não decide."""
    try:
        from xgboost import XGBClassifier
    except Exception:
        return None
    H = 8
    y, vy = label_triple_barrier(f[ini:fim], m[ini:fim], n[ini:fim], 0.02, 0.02, H)
    Xr = X[ini:fim]
    ok = vy & ~np.isnan(Xr).any(axis=1)
    Xf, yf = Xr[ok], y[ok]
    if len(yf) < 200 or len(np.unique(yf)) < 2:
        return None

    def build():
        return XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.03, subsample=0.8,
            colsample_bytree=0.8, eval_metric="logloss", verbosity=0,
        )

    aucs = purged_cv_auc(build, Xf, yf, n_splits=5, horizonte=H, embargo=H)
    if not aucs:
        return None
    return {"auc_medio": float(np.mean(aucs)), "auc_std": float(np.std(aucs)),
            "folds": [round(a, 4) for a in aucs], "base_rate": float(yf.mean())}


def ic_por_asset(symbol, fi, H, ini_frac=0.0, fim_frac=1 - HOLDOUT_FRAC) -> float:
    """IC de uma feature específica em outro ativo, na mesma fração temporal —
    para a corroboração cross-asset (replicação de sinal)."""
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    N = len(f)
    ini, fim = max(WARMUP, int(N * ini_frac)), int(N * fim_frac)
    Xr, labels, validos = preparar_para_ic(X, f, m, n, ini, fim)
    vmask = validos[H]
    return spearman_ic(Xr[vmask, fi], labels[H][vmask])


# ══════════════════════════════════════════════════════════════
# Hold-out (uso único, travado)
# ══════════════════════════════════════════════════════════════


def avaliar_holdout(symbol, fi, H, confirmo_uso_unico=False) -> dict:
    """Avalia a feature vencedora no HOLD-OUT temporal (últimos 35%). Recusa
    rodar sem confirmo_uso_unico e grava registro permanente em METODOLOGIA.md.
    Reavaliar depois de ver o resultado = reiniciar a pesquisa com dados novos."""
    if not confirmo_uso_unico:
        raise RuntimeError(
            "Hold-out é de USO ÚNICO. Só rode com confirmo_uso_unico=True e "
            "APENAS se a pesquisa (rodar_pesquisa) encontrou edge sobrevivente. "
            "Ver research/METODOLOGIA.md."
        )
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    N = len(f)
    ini = int(N * (1 - HOLDOUT_FRAC))
    Xr, labels, validos = preparar_para_ic(X, f, m, n, ini, N)
    vmask = validos[H]
    ic = spearman_ic(Xr[vmask, fi], labels[H][vmask])
    registro = (
        f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} | Hold-out avaliado "
        f"(symbol={symbol}, feature={FEATURE_NAMES[fi]}, H={H}) | "
        f"IC={ic:+.4f} (n={int(vmask.sum())}) |\n"
    )
    try:
        with open(METODOLOGIA, "a", encoding="utf-8") as fp:
            fp.write(registro)
    except Exception:
        pass
    return {"symbol": symbol, "feature": FEATURE_NAMES[fi], "H": H,
            "ic_holdout": ic, "n": int(vmask.sum())}


# ══════════════════════════════════════════════════════════════
# Relatório / CLI
# ══════════════════════════════════════════════════════════════


def imprimir_pesquisa(r, cross=None):
    p = r["perm"]
    di = datetime.fromtimestamp(r["periodo_pesquisa"][0] / 1000, tz=timezone.utc)
    df = datetime.fromtimestamp(r["periodo_pesquisa"][1] / 1000, tz=timezone.utc)
    print("=" * 70)
    print(f"  EDGE LAB — pesquisa (porção de pesquisa, hold-out INTOCADO)")
    print(f"  {r['symbol']} | {di:%Y-%m-%d} -> {df:%Y-%m-%d} | n={r['n_amostras']}")
    print("=" * 70)
    print("\n  IC (Spearman) por feature × horizonte:")
    print(f"  {'feature':<14} {'H=8':>9} {'H=24':>9}")
    for fi, nome in enumerate(FEATURE_NAMES):
        ic8 = p["ics_obs"].get((fi, 8), 0.0)
        ic24 = p["ics_obs"].get((fi, 24), 0.0)
        print(f"  {nome:<14} {ic8:>+9.4f} {ic24:>+9.4f}")

    melhor_nome = FEATURE_NAMES[p["melhor_feature_idx"]]
    print(f"\n  Melhor |IC|: {abs(p['melhor_ic']):.4f} "
          f"({melhor_nome}, H={p['melhor_horizonte']}, IC={p['melhor_ic']:+.4f})")
    print(f"  Teste de permutação (max-statistic, {N_PERM} rotações):")
    print(f"    p-valor = {p['p_valor']:.4f}   (null p95 de max|IC| = {p['null_p95']:.4f})")

    if r.get("auc_xgb"):
        a = r["auc_xgb"]
        print(f"\n  [secundária] XGBoost combinado (barreira ±2%, base "
              f"{a['base_rate']:.2%}): AUC {a['auc_medio']:.4f} ± {a['auc_std']:.4f} "
              f"(folds {a['folds']})")

    if cross:
        print("\n  Corroboração cross-asset (sinal do IC da feature vencedora):")
        for sym, ic in cross.items():
            mesmo = "mesmo sinal" if (ic * p["melhor_ic"] > 0) else "SINAL OPOSTO"
            print(f"    {sym}: IC={ic:+.4f}  ({mesmo})")

    # Regra de decisão pré-registrada
    print("\n" + "-" * 70)
    cond_p = p["p_valor"] < 0.05
    cond_ic = abs(p["melhor_ic"]) >= IC_PISO_ECONOMICO
    cond_cross = bool(cross) and any(ic * p["melhor_ic"] > 0 for ic in cross.values())
    print(f"  Regra de decisão (METODOLOGIA.md):")
    print(f"    [{'x' if cond_p else ' '}] p-valor < 0.05                    ({p['p_valor']:.4f})")
    print(f"    [{'x' if cond_ic else ' '}] |IC| >= {IC_PISO_ECONOMICO}                     ({abs(p['melhor_ic']):.4f})")
    print(f"    [{'x' if cond_cross else ' '}] replica sinal em ETH ou SOL")
    if cond_p and cond_ic and cond_cross:
        print("\n  >> HÁ EDGE CANDIDATO — prosseguir ao hold-out (uso único) para confirmar.")
    else:
        print("\n  >> SEM EDGE nesta família de features. Conclusão pré-registrada:")
        print("     não construir estratégia direcional sobre este sinal. Levar ao")
        print("     usuário: expandir features (hipótese nova pré-registrada) ou pivô.")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Edge Lab — descoberta de edge (pré-registrado)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--sem-xgb", action="store_true", help="pula a AUC secundária do XGBoost")
    ap.add_argument("--sem-cross", action="store_true", help="pula corroboração ETH/SOL")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    r = rodar_pesquisa(args.symbol, com_xgb=not args.sem_xgb, seed=args.seed)
    cross = None
    if not args.sem_cross:
        fi = r["perm"]["melhor_feature_idx"]
        H = r["perm"]["melhor_horizonte"]
        cross = {}
        for sym in ("ETHUSDT", "SOLUSDT"):
            try:
                cross[sym] = ic_por_asset(sym, fi, H)
            except Exception as e:
                print(f"[aviso] cross {sym} falhou: {e}")
    imprimir_pesquisa(r, cross)


if __name__ == "__main__":
    main()
