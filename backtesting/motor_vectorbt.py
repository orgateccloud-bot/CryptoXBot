"""
Motor de backtest vetorizado (VectorBT) — P2-2a
==================================================
ADITIVO: não substitui backtesting/otimizador.py (grid search Python puro,
mantido como oráculo de correção) — ataca o gargalo de VELOCIDADE do grid
search (até 8000 backtests sequenciais em Python puro) via VectorBT (numba
JIT), reproduzindo a MESMA regra de sinal de _score_backtest
(backtesting/motor_ensemble.py) e o mesmo formato de resultado de
_rodar_com_params (backtesting/otimizador.py), para reusar
backtesting/metricas.py e otimizador.imprimir_top sem mudança.

Spike de dependência (2026-07-22): `pip install vectorbt` resolve limpo
contra numpy==2.4.3/scipy==1.17.1/scikit-learn==1.8.0 já fixados neste
projeto — SEM downgrade. numba/llvmlite têm wheel pré-compilado (sem
precisar de toolchain Rust/C++ no Windows dev).

IMPORTANTE — o que é vetorizado e o que fica como loop Python:
  - O SCORE por-bar é calculado UMA VEZ SÓ para o dataset inteiro. Isso não
    é uma simplificação desta migração — é o comportamento do código
    LEGADO: a chamada a `_score_backtest()` dentro de
    `otimizador._rodar_com_params()` NÃO passa rsi_min/rsi_max/
    score_operar/score_cheio, então o componente "rsi" do score sempre usa
    os defaults *internos* de `_score_backtest` (42/60) — o grid dessas 4
    variáveis só afeta (a) o gate de entrada `rsi_min<=rsi<=rsi_max` e (b) o
    threshold `score>=score_operar/score_cheio`, aplicados DEPOIS do score
    já calculado. Reproduzido aqui de propósito, para bater com
    `_rodar_com_params` byte a byte — não "corrigido", pois mudar isso
    mudaria os resultados do grid search legado também.
  - Combos de (rsi_min, rsi_max, score_operar, score_cheio) viram COLUNAS de
    um DataFrame de sinais de entrada — cálculo vetorizado (numpy), sem loop.
  - Combos de (stop_pct, target_pct) permanecem um loop Python (só ~20-25
    iterações no grid completo) — cada iteração roda UMA chamada a
    `vbt.Portfolio.from_signals` processando TODAS as colunas de entrada
    simultaneamente (JIT numba), em vez do loop legado que roda um backtest
    Python puro POR combinação completa (até 8000×).
  - Sizing: `size = min(0.02/stop_pct, 1.0)` com `size_type="percent"`
    replica "arriscar 2% do capital ATUAL, dimensionado pela distância do
    stop" — equivalente vetorizado da fórmula legada
    `min(capital*0.02/stop_pct, capital)` (fração do equity corrente; o
    VectorBT também simula bar-a-bar com equity real, não é uma
    aproximação estática).
  - Prioridade stop-vs-alvo no mesmo candle: confirmado empiricamente (spike
    manual) que o VectorBT, por padrão, sai pelo STOP quando ambos são
    tocados no mesmo bar — mesma prioridade do `if mn<=stop: ... elif
    mx>=target: ...` do loop legado.
  - Divergência conhecida e aceita: as médias móveis `atr_med`/`bw_med`
    (janela de 20 barras anteriores, replicadas aqui via
    `rolling(20, min_periods=1)` deslocado) filtram apenas NaN; o loop
    legado filtra `if x` (exclui também zeros exatos). Diferença
    irrelevante na prática (ATR/bandwidth raramente são exatamente 0.0).

Teste de paridade obrigatório (tests/test_motor_vectorbt.py): compara o
SCORE por-bar (vetorizado vs. `_score_backtest`, elemento a elemento) num
dataset sintético determinístico antes de qualquer decisão real de
parâmetro usar este motor.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import vectorbt as vbt

import indicadores as ind
from backtesting.motor_ensemble import ADX_TENDENCIA, ATR_EXTREMO, SLIPPAGE, TAXA, _adx, carregar

# Defaults internos de _score_backtest para o componente "rsi" do score —
# NÃO são o rsi_min/rsi_max do grid (ver docstring do módulo).
_RSI_MIN_SCORE = 42
_RSI_MAX_SCORE = 60


def _lista_para_array(lista) -> np.ndarray:
    """Converte uma lista com padding `None` (retorno de atr/bollinger/
    bandwidth/vwap_rolling/volume_relativo em indicadores.py) para um
    numpy array com NaN no lugar de None."""
    return np.array([np.nan if x is None else x for x in lista], dtype=float)


def _media_movel_anterior(arr: np.ndarray, janela: int = 20) -> np.ndarray:
    """Replica `sum(x for x in arr[max(0,i-janela):i] if x) / max(1, count)`
    do loop legado: média móvel sobre os `janela` valores ANTERIORES ao
    índice i (exclusive), ignorando NaN. `shift(1)` desloca o fim da janela
    de i para i-1; `min_periods=1` bate com o denominador "contagem de
    valores não-None" do original (em vez de NaN quando há poucos pontos)."""
    return pd.Series(arr).shift(1).rolling(janela, min_periods=1).mean().to_numpy()


def _score_backtest_vetorizado(
    preco: np.ndarray,
    ema20: np.ndarray,
    ema50: np.ndarray,
    rsi_v: np.ndarray,
    atr_v: np.ndarray,
    atr_med: np.ndarray,
    vol_rel: np.ndarray,
    bw_v: np.ndarray,
    bw_med: np.ndarray,
    vwap_v: np.ndarray,
    tend_4h_alta: np.ndarray,
    tend_4h_baixa: np.ndarray,
    adx_v: np.ndarray,
    atr_ratio: np.ndarray,
) -> np.ndarray:
    """Versão vetorizada (numpy) de `motor_ensemble._score_backtest`, SEM
    `ml_prob` (sempre `None` no caminho de grid search — `scores["ml"]=50`
    constante) e SEM rsi_min/rsi_max/score_operar/score_cheio como
    parâmetro (o código legado também não os passa nesta chamada — ver
    docstring do módulo). Retorna o score arredondado (float, para permitir
    NaN nas barras onde os indicadores de entrada ainda são NaN)."""
    # Regime (25%)
    cond_extremo = atr_ratio > ATR_EXTREMO
    cond_alta = (adx_v >= ADX_TENDENCIA) & (preco > ema20) & (ema20 > ema50)
    cond_baixa = (adx_v >= ADX_TENDENCIA) & (preco < ema20) & (ema20 < ema50)
    regime = np.select(
        [cond_extremo, cond_alta, cond_baixa],
        [0.0, np.minimum(100, 60 + adx_v), 10.0],
        default=20.0,
    )

    # MTF (20%)
    mtf = np.select([tend_4h_alta, tend_4h_baixa], [100.0, 0.0], default=50.0)

    # ML (15%) — constante, ml_prob sempre None neste caminho
    ml = np.full_like(preco, 50.0)

    # EMA (10%)
    cond1 = (preco > ema20) & (ema20 > ema50)
    cond2 = preco > ema20
    cond3 = preco > ema50
    dist_ema = (preco - ema50) / ema50 * 100
    ema_score = np.select(
        [cond1, cond2, cond3],
        [np.minimum(100, 70 + dist_ema * 10), 50.0, 30.0],
        default=0.0,
    )

    # Fear & Greed (10%) — constante, sem API no backtest
    fear_greed = np.full_like(preco, 100.0)

    # RSI (8%) — usa os DEFAULTS 42/60 (ver _RSI_MIN_SCORE/_RSI_MAX_SCORE),
    # não o rsi_min/rsi_max do grid.
    rsi_ideal = (_RSI_MIN_SCORE + _RSI_MAX_SCORE) / 2
    dist_centro = np.abs(rsi_v - rsi_ideal) / ((_RSI_MAX_SCORE - _RSI_MIN_SCORE) / 2)
    cond_dentro = (rsi_v >= _RSI_MIN_SCORE) & (rsi_v <= _RSI_MAX_SCORE)
    cond_abaixo = rsi_v < _RSI_MIN_SCORE
    val_dentro = np.trunc(100 - dist_centro * 30)
    val_abaixo = np.maximum(0, np.trunc((rsi_v - 30) / (_RSI_MIN_SCORE - 30) * 60))
    val_acima = np.maximum(0, np.trunc((80 - rsi_v) / (80 - _RSI_MAX_SCORE) * 60))
    rsi_score = np.select([cond_dentro, cond_abaixo], [val_dentro, val_abaixo], default=val_acima)

    # VWAP (5%)
    vwap_positivo = vwap_v > 0
    dist_pct = np.where(
        vwap_positivo, (preco - vwap_v) / np.where(vwap_positivo, vwap_v, 1) * 100, 0.0
    )
    val_pos = np.minimum(100, 70 + dist_pct * 5)
    val_neg = np.maximum(0, 40 + dist_pct * 8)
    vwap_score = np.where(vwap_positivo, np.where(dist_pct > 0, val_pos, val_neg), 50.0)

    # Volume (4%)
    volume_score = np.select(
        [vol_rel >= 2.0, vol_rel >= 1.3, vol_rel >= 1.0, vol_rel >= 0.7],
        [100.0, 80.0, 60.0, 40.0],
        default=20.0,
    )

    # ATR (3%)
    atr_med_positivo = atr_med > 0
    ratio_atr = np.where(atr_med_positivo, atr_v / np.where(atr_med_positivo, atr_med, 1), 0.0)
    val_atr = np.select(
        [ratio_atr > 2.5, (ratio_atr >= 0.7) & (ratio_atr <= 1.8), ratio_atr < 0.7],
        [0.0, 100.0, 30.0],
        default=60.0,
    )
    atr_score = np.where(atr_med_positivo, val_atr, 50.0)

    total = (
        regime * 25
        + mtf * 20
        + ml * 15
        + ema_score * 10
        + fear_greed * 10
        + rsi_score * 8
        + vwap_score * 5
        + volume_score * 4
        + atr_score * 3
    ) / 100
    return np.round(total)


def _preparar_indicadores(symbol: str, intervalo: str):
    """Carrega klines e computa todos os arrays de indicadores necessários
    (mesma fonte/fórmulas de otimizador.grid_search). Retorna None se dados
    insuficientes."""
    k1h = carregar(symbol, intervalo)
    k4h = carregar(symbol, "4h")
    if len(k1h) < 100 or len(k4h) < 55:
        return None

    ts1h = np.array([r[0] for r in k1h])
    m1h = np.array([r[2] for r in k1h], dtype=float)
    n1h = np.array([r[3] for r in k1h], dtype=float)
    f1h = np.array([r[4] for r in k1h], dtype=float)
    v1h = np.array([r[5] for r in k1h], dtype=float)
    f4h = np.array([r[4] for r in k4h], dtype=float)

    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = _lista_para_array(ind.atr(m1h.tolist(), n1h.tolist(), f1h.tolist(), 14))
    volr = _lista_para_array(ind.volume_relativo(v1h.tolist(), 20))
    bbu, bbm, bbl = ind.bollinger(f1h.tolist(), 20, 2)
    bw = _lista_para_array(ind.bandwidth(bbu, bbm, bbl))
    vwap = _lista_para_array(
        ind.vwap_rolling(m1h.tolist(), n1h.tolist(), f1h.tolist(), v1h.tolist(), periodo=20)
    )
    adx_vals = np.array(_adx(m1h.tolist(), n1h.tolist(), f1h.tolist(), 14), dtype=float)

    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    atr_med = _media_movel_anterior(atr14, 20)
    bw_med = _media_movel_anterior(bw, 20)
    atr_ratio = np.where(atr_med > 0, atr14 / np.where(atr_med > 0, atr_med, 1), 1.0)

    idx4 = np.minimum(np.arange(len(f1h)) // 4, len(f4h) - 1)
    p4, e20_4, e50_4 = f4h[idx4], ema20_4h[idx4], ema50_4h[idx4]
    tend_alta = (p4 > e20_4) & (e20_4 > e50_4)
    tend_baixa = (p4 < e20_4) & (e20_4 < e50_4)

    score = _score_backtest_vetorizado(
        f1h, ema20, ema50, rsi14, atr14, atr_med, volr, bw, bw_med, vwap,
        tend_alta, tend_baixa, adx_vals, atr_ratio,
    )

    # Barras validas: mesmo warmup (range(55,...)) + nenhum indicador NaN
    # (equivalente ao "if any(x is None ...): continue" do loop legado).
    validos = ~(np.isnan(rsi14) | np.isnan(atr14) | np.isnan(volr) | np.isnan(bw) | np.isnan(vwap))
    validos[:55] = False

    return {
        "ts1h": ts1h,
        "f1h": f1h,
        "m1h": m1h,
        "n1h": n1h,
        "rsi14": rsi14,
        "score": score,
        "validos": validos,
    }


def grid_search_vbt(
    symbol: str = "BTCUSDT", intervalo: str = "1h", rapido: bool = False,
    capital_inicial: float = 1000.0,
) -> list[dict]:
    """Grid search vetorizado — mesmo formato de resultado de
    otimizador._rodar_com_params (params/total/win_rate/retorno/max_dd/
    sharpe/capital_final/retornos_pct), reusável por
    otimizador.imprimir_top/metricas.deflated_sharpe_ratio sem mudança."""
    dados = _preparar_indicadores(symbol, intervalo)
    if dados is None:
        print(f"Dados insuficientes para {symbol}. Rode coletar_dados.py primeiro.")
        return []

    score = dados["score"]
    rsi14 = dados["rsi14"]
    validos = dados["validos"]

    dt_index = pd.to_datetime(dados["ts1h"], unit="ms")
    close = pd.Series(dados["f1h"], index=dt_index)
    high = pd.Series(dados["m1h"], index=dt_index)
    low = pd.Series(dados["n1h"], index=dt_index)
    open_ = close.shift(1).fillna(close.iloc[0])

    if rapido:
        grid_rsi_score = {
            "rsi_min": [40, 42], "rsi_max": [60, 65],
            "score_operar": [55, 60], "score_cheio": [70, 75],
        }
        grid_stop_target = {
            "stop_pct": [0.015, 0.020, 0.025], "target_pct": [0.030, 0.040, 0.050],
        }
    else:
        grid_rsi_score = {
            "rsi_min": [38, 40, 42, 45], "rsi_max": [58, 60, 62, 65, 68],
            "score_operar": [50, 55, 60, 65], "score_cheio": [65, 70, 75, 80],
        }
        grid_stop_target = {
            "stop_pct": [0.010, 0.015, 0.020, 0.025, 0.030],
            "target_pct": [0.020, 0.030, 0.040, 0.050, 0.060],
        }

    params_por_combo: dict[str, dict] = {}
    entries_cols: dict[str, np.ndarray] = {}
    for rsi_min, rsi_max, score_op, score_ch in itertools.product(
        grid_rsi_score["rsi_min"], grid_rsi_score["rsi_max"],
        grid_rsi_score["score_operar"], grid_rsi_score["score_cheio"],
    ):
        if rsi_min >= rsi_max or score_op >= score_ch:
            continue
        fator = np.select([score >= score_ch, score >= score_op], [1.0, 0.5], default=0.0)
        rsi_gate = (rsi14 >= rsi_min) & (rsi14 <= rsi_max)
        entrada = validos & rsi_gate & (fator > 0)
        nome = f"c{len(entries_cols)}"
        entries_cols[nome] = entrada
        params_por_combo[nome] = {
            "rsi_min": rsi_min, "rsi_max": rsi_max,
            "score_operar": score_op, "score_cheio": score_ch,
        }

    if not entries_cols:
        return []

    entries_df = pd.DataFrame(entries_cols, index=dt_index)
    combos_stop_target = [
        (sp, tp)
        for sp, tp in itertools.product(
            grid_stop_target["stop_pct"], grid_stop_target["target_pct"]
        )
        if sp < tp
    ]

    resultados = []
    for stop_pct, target_pct in combos_stop_target:
        pf = vbt.Portfolio.from_signals(
            close=close, open=open_, high=high, low=low,
            entries=entries_df,
            sl_stop=stop_pct, tp_stop=target_pct,
            fees=TAXA, slippage=SLIPPAGE,
            size=min(0.02 / stop_pct, 1.0), size_type="percent",
            init_cash=capital_inicial, accumulate=False, freq=intervalo,
        )
        contagens = pf.trades.count()
        registros = pf.trades.records_readable

        for nome in entries_df.columns:
            total_trades = int(contagens.get(nome, 0))
            if total_trades < 5:
                continue
            trades_col = registros[registros["Column"] == nome]
            retornos_pct = (trades_col["Return"] * 100).tolist()
            ganhos = int((trades_col["PnL"] > 0).sum())
            wrate = ganhos / total_trades * 100
            capital_final = float(capital_inicial + trades_col["PnL"].sum())
            retorno = (capital_final - capital_inicial) / capital_inicial * 100
            equity_acum = capital_inicial + trades_col["PnL"].cumsum()
            pico_acum = equity_acum.cummax()
            dd_serie = (pico_acum - equity_acum) / pico_acum * 100
            max_dd = float(dd_serie.max()) if len(dd_serie) else 0.0

            from backtesting import metricas

            sharpe = metricas.sharpe_ratio(retornos_pct)

            params = dict(params_por_combo[nome])
            params["stop_pct"] = stop_pct
            params["target_pct"] = target_pct
            resultados.append({
                "params": params,
                "total": total_trades,
                "win_rate": round(wrate, 1),
                "retorno": round(retorno, 2),
                "max_dd": round(max_dd, 2),
                "sharpe": round(sharpe, 2),
                "capital_final": round(capital_final, 2),
                "retornos_pct": retornos_pct,
            })

    return resultados


if __name__ == "__main__":
    import argparse

    from backtesting.otimizador import imprimir_top

    parser = argparse.ArgumentParser(description="Grid Search Vetorizado (VectorBT) — P2-2a")
    parser.add_argument("--par", default="BTCUSDT")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--rapido", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--ordenar", default="sharpe", choices=["sharpe", "retorno", "score"])
    args = parser.parse_args()

    print(f"\n[GRID SEARCH VBT] {args.par} — Otimizando hiperparâmetros (VectorBT)...\n")
    resultados = grid_search_vbt(args.par, args.intervalo, args.rapido)
    if resultados:
        imprimir_top(resultados, args.top, args.ordenar)
