"""
Teste de paridade — backtesting/motor_vectorbt.py (P2-2a)
=============================================================
`vectorbt` é uma dependência OPCIONAL (não está em requirements.txt do
ambiente principal — ver PLANO_MODERNIZACAO.md sobre por quê: este projeto
roda numa máquina com Python GLOBAL compartilhado entre ferramentas, sem
venv dedicado; vectorbt só é instalado num `.venv/` local ao projeto para
testes/uso de pesquisa). `pytest.importorskip` pula todo o arquivo se
`vectorbt` não estiver instalado no ambiente que rodar a suíte.

Dois níveis de paridade, dos dois riscos reais desta migração:

1. SCORE por-bar (o risco maior): `_score_backtest_vetorizado` precisa
   reproduzir EXATAMENTE `_score_backtest` (motor_ensemble.py) — comparação
   elemento a elemento num dataset sintético com centenas de barras
   aleatórias, cobrindo os principais ramos condicionais.
2. Pipeline completo: `grid_search_vbt` roda sem erro e devolve resultados
   no formato esperado por `otimizador.imprimir_top`/`metricas.py`; contagem
   de trades de UM combo compatível é comparada (best-effort, não bar-a-bar)
   contra `otimizador._rodar_com_params` no mesmo dataset sintético.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

vbt = pytest.importorskip("vectorbt")

import indicadores as ind
from backtesting.motor_ensemble import ADX_TENDENCIA, ATR_EXTREMO, _adx, _score_backtest
from backtesting.motor_vectorbt import (
    _lista_para_array,
    _media_movel_anterior,
    _score_backtest_vetorizado,
    grid_search_vbt,
)
from backtesting.otimizador import _rodar_com_params


def _dataset_sintetico(n=400, seed=7):
    """OHLCV sintético determinístico (random walk com drift leve) — grande
    o suficiente para exercitar todos os ramos condicionais do score
    (tendência, lateral, volatilidade extrema) e passar o warmup de todos
    os indicadores (EMA50/RSI14/ATR14/Bollinger20/VWAP20/ADX14)."""
    rng = np.random.default_rng(seed)
    retornos = rng.normal(0.0002, 0.01, n)
    fechamentos = 100 * np.cumprod(1 + retornos)
    ruido = np.abs(rng.normal(0, 0.003, n)) * fechamentos
    maximas = fechamentos + ruido
    minimas = fechamentos - ruido
    aberturas = np.roll(fechamentos, 1)
    aberturas[0] = fechamentos[0]
    volumes = rng.uniform(50, 200, n)
    timestamps = (1_700_000_000_000 + np.arange(n) * 3_600_000).astype(np.int64)
    return timestamps, aberturas, maximas, minimas, fechamentos, volumes


def _klines_rows(timestamps, aberturas, maximas, minimas, fechamentos, volumes):
    return list(zip(timestamps.tolist(), aberturas.tolist(), maximas.tolist(),
                     minimas.tolist(), fechamentos.tolist(), volumes.tolist()))


class TestScoreVetorizadoParidade:
    def test_score_bate_elemento_a_elemento(self):
        ts, a, m, n_, f, v = _dataset_sintetico(n=400, seed=7)
        f, m, n_, v = f.tolist(), m.tolist(), n_.tolist(), v.tolist()

        ema20 = ind.ema(np.array(f), 20)
        ema50 = ind.ema(np.array(f), 50)
        rsi14 = ind.rsi(np.array(f), 14)
        atr14 = _lista_para_array(ind.atr(m, n_, f, 14))
        volr = _lista_para_array(ind.volume_relativo(v, 20))
        bbu, bbm, bbl = ind.bollinger(f, 20, 2)
        bw = _lista_para_array(ind.bandwidth(bbu, bbm, bbl))
        vwap = _lista_para_array(ind.vwap_rolling(m, n_, f, v, periodo=20))
        adx_vals = np.array(_adx(m, n_, f, 14), dtype=float)

        atr_med = _media_movel_anterior(atr14, 20)
        bw_med = _media_movel_anterior(bw, 20)
        atr_ratio = np.where(atr_med > 0, atr14 / np.where(atr_med > 0, atr_med, 1), 1.0)

        # tend_4h sintetico: alterna ALTA/LATERAL/BAIXA a cada 30 barras --
        # so precisamos exercitar os 3 ramos do score "mtf", nao replicar a
        # logica real de multi-timeframe (fora do escopo deste teste).
        tend_alta = np.zeros(len(f), dtype=bool)
        tend_baixa = np.zeros(len(f), dtype=bool)
        tend_alta[0::3] = True
        tend_baixa[1::3] = True
        tend_4h_str = np.where(tend_alta, "ALTA", np.where(tend_baixa, "BAIXA", "LATERAL"))

        score_vetorizado = _score_backtest_vetorizado(
            np.array(f), ema20, ema50, rsi14, atr14, atr_med, volr, bw, bw_med, vwap,
            tend_alta, tend_baixa, adx_vals, atr_ratio,
        )

        validos = ~(
            np.isnan(rsi14) | np.isnan(atr14) | np.isnan(volr) | np.isnan(bw) | np.isnan(vwap)
        )
        validos[:55] = False

        divergencias = []
        for i in range(len(f)):
            if not validos[i]:
                continue
            score_legado, _, _, _ = _score_backtest(
                f[i], ema20[i], ema50[i], rsi14[i], atr14[i], atr_med[i], volr[i],
                bw[i], bw_med[i], vwap[i], tend_4h_str[i], adx_vals[i], atr_ratio[i],
                None,  # ml_prob
            )
            if score_legado != score_vetorizado[i]:
                divergencias.append((i, score_legado, score_vetorizado[i]))

        assert divergencias == [], (
            f"{len(divergencias)} barras com score divergente (primeiras 5): "
            f"{divergencias[:5]}"
        )
        assert validos.sum() > 100, "dataset sintetico deveria ter >100 barras validas"


class TestGridSearchVbtPipeline:
    def test_roda_sem_erro_e_formato_do_resultado(self, monkeypatch):
        import backtesting.motor_vectorbt as mvbt

        ts, a, m, n_, f, v = _dataset_sintetico(n=400, seed=7)
        rows_1h = _klines_rows(ts, a, m, n_, f, v)
        # 4h: mesma serie subamostrada (so precisa passar o len>=55 minimo)
        rows_4h = rows_1h[::4]

        def _carregar_fake(symbol, intervalo):
            return rows_4h if intervalo == "4h" else rows_1h

        monkeypatch.setattr(mvbt, "carregar", _carregar_fake)

        resultados = grid_search_vbt("BTCUSDT", "1h", rapido=True)

        assert isinstance(resultados, list)
        if resultados:  # dataset sintetico pode nao gerar >=5 trades em todo combo
            r = resultados[0]
            for campo in (
                "params", "total", "win_rate", "retorno", "max_dd",
                "sharpe", "capital_final", "retornos_pct",
            ):
                assert campo in r, f"Campo '{campo}' ausente no resultado"
            assert r["total"] >= 5
            assert set(r["params"].keys()) == {
                "rsi_min", "rsi_max", "score_operar", "score_cheio",
                "stop_pct", "target_pct",
            }

    def test_dados_insuficientes_retorna_lista_vazia(self, monkeypatch):
        import backtesting.motor_vectorbt as mvbt

        monkeypatch.setattr(mvbt, "carregar", lambda symbol, intervalo: [])
        assert grid_search_vbt("BTCUSDT", "1h") == []

    def test_contagem_de_trades_proxima_do_motor_legado(self, monkeypatch):
        """Paridade best-effort (nao bar-a-bar) do pipeline completo: para UM
        combo compatível entre os dois motores, o numero de trades deve ser
        parecido (mesma logica de sinal, execucao/sizing vetorizados podem
        divergir em detalhes finos sem invalidar a paridade de TIMING)."""
        import backtesting.motor_vectorbt as mvbt

        ts, a, m, n_, f, v = _dataset_sintetico(n=400, seed=7)
        rows_1h = _klines_rows(ts, a, m, n_, f, v)
        rows_4h = rows_1h[::4]

        def _carregar_fake(symbol, intervalo):
            return rows_4h if intervalo == "4h" else rows_1h

        monkeypatch.setattr(mvbt, "carregar", _carregar_fake)

        resultados = grid_search_vbt("BTCUSDT", "1h", rapido=True)
        if not resultados:
            pytest.skip("dataset sintetico nao gerou nenhum combo com >=5 trades")

        # Reconstroi os inputs do motor legado (mesmos dados/indicadores)
        f_l, m_l, n_l, v_l = f.tolist(), m.tolist(), n_.tolist(), v.tolist()
        f4_l = [r[4] for r in rows_4h]
        ema20 = ind.ema(np.array(f_l), 20).tolist()
        ema50 = ind.ema(np.array(f_l), 50).tolist()
        rsi14 = ind.rsi(np.array(f_l), 14).tolist()
        atr14 = ind.atr(m_l, n_l, f_l, 14)
        volr = ind.volume_relativo(v_l, 20)
        bbu, bbm, bbl = ind.bollinger(f_l, 20, 2)
        bw = ind.bandwidth(bbu, bbm, bbl)
        vwap = ind.vwap_rolling(m_l, n_l, f_l, v_l, periodo=20)
        adx_vals = _adx(m_l, n_l, f_l, 14)
        ema20_4h = ind.ema(np.array(f4_l), 20).tolist()
        ema50_4h = ind.ema(np.array(f4_l), 50).tolist()

        params = dict(resultados[0]["params"])
        r_legado = _rodar_com_params(
            f_l, m_l, n_l, v_l, ts.tolist(), f4_l, [r[2] for r in rows_4h], [r[3] for r in rows_4h],
            ema20, ema50, rsi14, atr14, volr, bw, vwap, adx_vals, ema20_4h, ema50_4h,
            params,
        )

        total_legado = r_legado["total"] if r_legado else 0
        total_vbt = resultados[0]["total"]
        # Tolerancia generosa (execucao/sizing vetorizados nao sao bit-a-bit
        # identicos -- ver docstring do modulo) -- o que importa aqui e nao
        # ter uma divergencia GROSSEIRA (ex: 0 trades vs 50, ou 10x de difs).
        assert total_legado > 0, "motor legado nao gerou nenhum trade p/ o mesmo combo"
        razao = total_vbt / total_legado
        assert 0.5 <= razao <= 2.0, (
            f"contagem de trades muito diferente: legado={total_legado} vbt={total_vbt}"
        )
