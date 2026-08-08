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
import hashlib
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
N_PERM = 1000
IC_PISO_ECONOMICO = 0.03  # regra de decisão (METODOLOGIA.md)
METODOLOGIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "METODOLOGIA.md")

# ── I-11: fronteira do hold-out por DATA, não por fração ──────────────────
#
# `int(N * (1 - HOLDOUT_FRAC))` amarrava a fronteira ao TAMANHO da série. Toda
# coleta nova movia a divisa entre pesquisa e hold-out — sem ninguém decidir
# nada, sem registro, sem aviso.
#
# Medido no snapshot de 2026-08-08, com a fórmula antiga:
#
#     BTCUSDT: N=17.563  ini=11.415  ->  2025-07-21 09:00Z
#     ETHUSDT: N=17.520  ini=11.388  ->  2025-07-22 01:00Z
#     SOLUSDT: N=17.520  ini=11.388  ->  2025-07-22 01:00Z
#
# Dezesseis horas de diferença entre ativos, por nenhuma razão metodológica: só
# porque a tabela do BTC tinha 43 velas a mais — e no INÍCIO da série, ou seja
# crescimento não-append. Pior, o registro de 2026-07-24 mostra que 43,7% do
# hold-out de hoje já foi porção de PESQUISA numa rodada anterior: dado já visto
# voltando a servir de teste cego.
#
# A data abaixo está CONGELADA no código e registrada em METODOLOGIA.md. Mudá-la
# invalida todos os vereditos anteriores e exige novo pré-registro por escrito.
HOLDOUT_INICIO_MS = 1753142400000  # 2025-07-22 00:00:00 UTC

# Mantida só para documentar a origem da data acima (~35% final da série em
# 2026-08-08) e para os testes que citam a fração histórica. NÃO decide mais
# fronteira nenhuma — quem decide é HOLDOUT_INICIO_MS.
HOLDOUT_FRAC = 0.35


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
    """Série da pesquisa. Lê o SNAPSHOT imutável quando existe (I-11).

    A tabela `klines` viva muda: entre a medição oficial e 2026-08-08 ela passou
    de 17.520 para 17.563 velas em BTCUSDT/1h, e as 43 novas entraram no INÍCIO
    da série. Um veredito derivado de dado móvel não é re-derivável, e um FAIL
    que ninguém consegue reproduzir cede na primeira dúvida.

    Fallback para o banco só quando não há snapshot — com aviso, porque nesse
    modo o resultado não é reprodutível e quem lê o número precisa saber disso.
    """
    try:
        from research.snapshot import carregar_serie

        return carregar_serie(symbol, intervalo)
    except FileNotFoundError:
        print(
            f"[edge_lab] AVISO: sem snapshot — lendo a tabela VIVA para "
            f"{symbol}/{intervalo}. O resultado NÃO será re-derivável. "
            f"Rode: python -m research.snapshot --criar"
        )
    except KeyError as exc:
        print(f"[edge_lab] AVISO: {exc} — caindo para a tabela viva.")

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


def _chave_cache(f, m, n, v, symbol: str) -> str:
    """Chave do cache de features, derivada do CONTEÚDO da série (I-11).

    Era `feat_{symbol}_{len(f)}.npy` — chaveada pelo TAMANHO. Duas séries
    diferentes com a mesma contagem (uma vela corrigida, um período trocado,
    uma coleta com a vela em formação congelada) recebiam as features UMA DA
    OUTRA, em silêncio, e o IC saía de dados que não eram os carregados.

    Num módulo cujo propósito é reprodutibilidade, isso é o defeito mais grave
    possível: o resultado depende de um arquivo em disco que o código acha que
    corresponde à série, sem nunca verificar.
    """
    h = hashlib.sha256()
    for arr in (f, m, n, v):
        h.update(np.asarray(arr, dtype=float).tobytes())
    return f"feat_{symbol}_{len(f)}_{h.hexdigest()[:16]}.npy"


def construir_matriz_features(f, m, n, v, symbol: str, usar_cache=True) -> np.ndarray:
    """Matriz (N, 11) chamando ml_filtro.extrair_features EXATAMENTE (mesmo
    sinal que o projeto usa). Linhas < WARMUP viram NaN. Caro (O(n^2)) — cacheado
    em disco por symbol + hash do CONTEÚDO da série (ver _chave_cache)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, _chave_cache(f, m, n, v, symbol))
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


def indice_do_holdout(ts) -> int:
    """Primeiro índice em ou após HOLDOUT_INICIO_MS (I-11).

    `ts` é o vetor de timestamps da série. A busca é por DATA, então o índice
    resultante muda quando a série muda — mas o CONJUNTO de velas de cada lado
    da fronteira não muda. É essa a propriedade que faz um veredito ser
    re-derivável: antes, acrescentar 43 velas no início da série movia a divisa e
    transferia dado de hold-out para pesquisa em silêncio.
    """
    import numpy as np

    idx = int(np.searchsorted(np.asarray(ts), HOLDOUT_INICIO_MS, side="left"))
    if idx <= WARMUP or idx >= len(ts):
        raise ValueError(
            f"Fronteira de hold-out ({HOLDOUT_INICIO_MS}) cai fora da série "
            f"(N={len(ts)}, idx={idx}). A série não cobre o período pré-registrado."
        )
    return idx


def _porcao_pesquisa(ts) -> tuple[int, int]:
    """[warmup, corte) — tudo ANTES da data de hold-out.

    Assinatura mudou de `(N: int)` para `(ts)` de propósito: receber só o
    tamanho tornava impossível calcular a fronteira por data, e era exatamente
    esse acoplamento ao tamanho que deixava o hold-out à deriva.
    """
    return WARMUP, indice_do_holdout(ts)


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
    ini, fim = _porcao_pesquisa(ts)
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


def retorno_barreira(f, m, n, i, alvo, stop, H) -> float:
    """Retorno BRUTO (long) do trade barreira a partir de i: +alvo se o alvo é
    tocado antes do stop dentro de H; -stop se stop primeiro; retorno a mercado
    se timeout. Ordem intra-candle: stop antes de alvo (conservador)."""
    entrada = f[i]
    ns, na = entrada * (1 - stop), entrada * (1 + alvo)
    for j in range(i + 1, i + H + 1):
        if n[j] <= ns:
            return -stop
        if m[j] >= na:
            return alvo
    return (f[i + H] - entrada) / entrada


def _oos_probs_xgb(Xf, yf, H, n_splits=5):
    """Probabilidades OOS por amostra via purged CV (XGBoost 11-features). NaN
    nas amostras que nunca caíram num fold de teste válido."""
    from xgboost import XGBClassifier

    prob = np.full(len(yf), np.nan)
    for tr, te in purged_kfold_indices(len(Xf), n_splits, H, H):
        if len(tr) < 50 or len(np.unique(yf[tr])) < 2:
            continue
        mdl = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.03,
                            subsample=0.8, colsample_bytree=0.8,
                            eval_metric="logloss", verbosity=0)
        mdl.fit(Xf[tr], yf[tr])
        prob[te] = mdl.predict_proba(Xf[te])[:, 1]
    return prob


def teste_permutacao_auc_xgb(symbol="BTCUSDT", H=8, alvo=0.02, stop=0.02,
                             n_perm=120, seed=11) -> dict:
    """Submete a AUC do XGBoost combinado ao MESMO rigor do teste primário:
    rotaciona o label (preserva autocorrelação, quebra alinhamento) e recomputa
    a purged-CV AUC -> null. p = fração de nulls >= AUC observada. Responde se a
    AUC 'boa' é sinal não-linear real ou artefato de labels sobrepostos."""
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    ini, fim = _porcao_pesquisa(ts)
    y, vy = label_triple_barrier(f[ini:fim], m[ini:fim], n[ini:fim], alvo, stop, H)
    Xr = X[ini:fim]
    ok = vy & ~np.isnan(Xr).any(axis=1)
    Xf, yf = Xr[ok], y[ok]

    def _auc(yy):
        aucs = purged_cv_auc(
            lambda: __import__("xgboost").XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.03, subsample=0.8,
                colsample_bytree=0.8, eval_metric="logloss", verbosity=0),
            Xf, yy, n_splits=5, horizonte=H, embargo=H)
        return float(np.mean(aucs)) if aucs else np.nan

    auc_obs = _auc(yf)
    rng = np.random.default_rng(seed)
    N = len(yf)
    nulls = []
    for _ in range(n_perm):
        off = int(rng.integers(H + 1, N - H - 1))
        nulls.append(_auc(np.concatenate([yf[off:], yf[:off]])))
    nulls = np.array([a for a in nulls if not np.isnan(a)])
    p = float((np.sum(nulls >= auc_obs) + 1) / (len(nulls) + 1))
    return {"auc_obs": auc_obs, "p_valor": p, "null_media": float(nulls.mean()),
            "null_p95": float(np.quantile(nulls, 0.95)), "base_rate": float(yf.mean())}


def avaliar_economia(symbol="BTCUSDT", H=8, alvo=0.02, stop=0.02, custo=0.003) -> dict:
    """A pergunta decisiva: o ranking do XGBoost sobrevive AOS CUSTOS? Usa
    probabilidades OOS (purged CV) para computar a expectância LÍQUIDA por trade
    dos sinais mais bem-ranqueados. Um edge estatístico com net <= 0 no melhor
    balde é economicamente morto (afogado pelos custos)."""
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    ini, fim = _porcao_pesquisa(ts)
    fr, mr, nr = f[ini:fim], m[ini:fim], n[ini:fim]
    y, vy = label_triple_barrier(fr, mr, nr, alvo, stop, H)
    Xr = X[ini:fim]
    ok = vy & ~np.isnan(Xr).any(axis=1)
    idx_ok = np.where(ok)[0]
    Xf, yf = Xr[ok], y[ok]
    ret = np.array([retorno_barreira(fr, mr, nr, int(i), alvo, stop, H) for i in idx_ok])
    prob = _oos_probs_xgb(Xf, yf, H)
    val = ~np.isnan(prob)
    prob, ret, yv = prob[val], ret[val], yf[val]
    net = ret - custo
    ordem = np.argsort(-prob)
    baldes = {}
    for k in (0.05, 0.10, 0.20, 0.30):
        sel = ordem[: int(k * len(ordem))]
        baldes[k] = {"n": len(sel), "net_medio": float(net[sel].mean()),
                     "win": float(yv[sel].mean()), "soma": float(net[sel].sum())}
    return {"custo": custo, "n": len(net), "net_base": float(net.mean()),
            "win_base": float(yv.mean()), "baldes": baldes}


def ic_por_asset(symbol, fi, H) -> float:
    """IC de uma feature específica em outro ativo, na MESMA JANELA TEMPORAL —
    para a corroboração cross-asset (replicação de sinal).

    I-11: os parâmetros `ini_frac`/`fim_frac` foram removidos. Com frações, cada
    ativo era recortado por uma porcentagem do PRÓPRIO tamanho, então BTC (17.563
    velas) e ETH (17.520) eram medidos em janelas que começavam e terminavam em
    datas diferentes — e a corroboração "mesma fração temporal" comparava
    períodos distintos. Agora os três compartilham a fronteira por data, que é o
    que "mesma janela" tinha de significar desde o começo.
    """
    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    ini, fim = _porcao_pesquisa(ts)
    Xr, labels, validos = preparar_para_ic(X, f, m, n, ini, fim)
    vmask = validos[H]
    return spearman_ic(Xr[vmask, fi], labels[H][vmask])


# ══════════════════════════════════════════════════════════════
# Hold-out (uso único, travado)
# ══════════════════════════════════════════════════════════════


_MARCA_HOLDOUT = "HOLD-OUT CONSUMIDO"


def holdout_ja_consumido(symbol: str) -> str | None:
    """Linha do registro anterior, ou None se o hold-out deste symbol é virgem.

    I-11: a trava de uso único era SÓ o kwarg `confirmo_uso_unico`, e o registro
    em METODOLOGIA.md ficava dentro de um `try/except: pass` que ninguém lia.
    Ou seja: passar True duas vezes funcionava, e o "registro permanente" não
    tinha efeito nenhum sobre a segunda chamada.

    Um hold-out que pode ser reavaliado não é hold-out — é mais um conjunto de
    validação, e o veredito derivado dele não vale o papel em que foi escrito.
    Agora a trava LÊ o próprio registro que ela escreve.
    """
    try:
        with open(METODOLOGIA, encoding="utf-8") as fp:
            for linha in fp:
                if _MARCA_HOLDOUT in linha and f"symbol={symbol}" in linha:
                    return linha.strip()
    except FileNotFoundError:
        return None
    return None


def avaliar_holdout(symbol, fi, H, confirmo_uso_unico=False, permitir_reuso=False) -> dict:
    """Avalia a feature vencedora no HOLD-OUT temporal (a partir de
    HOLDOUT_INICIO_MS). Uso ÚNICO, agora imposto por leitura do registro.

    Reavaliar depois de ver o resultado = reiniciar a pesquisa com dados novos.
    `permitir_reuso` existe só para os testes desta trava; usá-lo em pesquisa
    real invalida o veredito e o registro dirá isso.
    """
    if not confirmo_uso_unico:
        raise RuntimeError(
            "Hold-out é de USO ÚNICO. Só rode com confirmo_uso_unico=True e "
            "APENAS se a pesquisa (rodar_pesquisa) encontrou edge sobrevivente. "
            "Ver research/METODOLOGIA.md."
        )

    anterior = holdout_ja_consumido(symbol)
    if anterior and not permitir_reuso:
        raise RuntimeError(
            f"HOLD-OUT DE {symbol} JÁ FOI CONSUMIDO — segunda avaliação recusada.\n"
            f"  Registro anterior: {anterior}\n"
            f"  Um hold-out reavaliado deixa de ser cego: o resultado da primeira\n"
            f"  passada já influenciou quem decidiu rodar a segunda. Para testar de\n"
            f"  novo é preciso DADO NOVO (período ou universo novo), pré-registrado\n"
            f"  em research/METODOLOGIA.md antes da medição."
        )

    ts, m, n, f, v = carregar_klines(symbol)
    X = construir_matriz_features(f, m, n, v, symbol)
    N = len(f)
    ini = indice_do_holdout(ts)
    Xr, labels, validos = preparar_para_ic(X, f, m, n, ini, N)
    vmask = validos[H]
    ic = spearman_ic(Xr[vmask, fi], labels[H][vmask])
    registro = (
        f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} | {_MARCA_HOLDOUT} "
        f"(symbol={symbol}, feature={FEATURE_NAMES[fi]}, H={H}, "
        f"inicio_ms={HOLDOUT_INICIO_MS}, n_velas={N - ini}) | "
        f"IC={ic:+.4f} (n={int(vmask.sum())}) |\n"
    )
    # Sem try/except mudo: se o registro não puder ser gravado, a trava não
    # existe para a próxima execução, e é melhor a medição falhar do que produzir
    # um veredito que se apresenta como uso único sem ser.
    with open(METODOLOGIA, "a", encoding="utf-8") as fp:
        fp.write(registro)
    return {"symbol": symbol, "feature": FEATURE_NAMES[fi], "H": H,
            "ic_holdout": ic, "n": int(vmask.sum()),
            "holdout_inicio_ms": HOLDOUT_INICIO_MS, "n_velas_holdout": N - ini}


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
    # I-11: duas funções que PRODUZIRAM veredito e não tinham nenhum call site —
    # nem no argparse, nem em teste, nem em outro módulo. O veredito existia em
    # prosa e o comando que o gerou não existia mais. Sem entrypoint, "re-derive
    # você mesmo" significava reescrever a chamada de cabeça.
    ap.add_argument("--perm-auc", action="store_true",
                    help="teste de permutação da AUC do XGBoost (secundário)")
    ap.add_argument("--economia", action="store_true",
                    help="expectância líquida por balde de probabilidade (custos)")
    ap.add_argument("--custo", type=float, default=0.003,
                    help="custo round-trip para --economia (default 0.30%%)")
    args = ap.parse_args()

    if args.perm_auc:
        r = teste_permutacao_auc_xgb(args.symbol, seed=args.seed)
        print(f"  AUC obs = {r.get('auc_obs'):.4f} | p = {r.get('p'):.4f} "
              f"(n_perm={r.get('n_perm')})")
        return
    if args.economia:
        r = avaliar_economia(args.symbol, custo=args.custo)
        print(f"  custo={r['custo']:.4f} n={r['n']} net_base={r['net_base']:+.5f} "
              f"win_base={r['win_base']:.3f}")
        for nome, b in r["baldes"].items():
            print(f"    {nome:<10} n={b['n']:>5} net={b['net']:+.5f} win={b['win']:.3f}")
        return

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
