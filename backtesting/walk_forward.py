"""
Walk-Forward Validation + Retreino Automatico — BotBinance
============================================================
Divide os dados em janelas de treino/teste sequenciais,
retreinando o ML a cada janela para simular uso real.

Janelas:
  [===TREINO===][=TESTE=]
       [===TREINO===][=TESTE=]
            [===TREINO===][=TESTE=]

Parametros:
  - Janela de treino: 500 candles (1h ~ 21 dias)
  - Janela de teste:  100 candles (1h ~ 4 dias)
  - Passo: 100 candles (avanca a janela)

MEDICAO OFICIAL DO GATE (docs/GATE_GO_LIVE.md, Etapa 1). Correcoes da regua
aplicadas apos verificacao adversarial de 2026-07-23 (registradas no gate):
  B1/B2 — filtro MTF usa o ultimo candle 4h FECHADO no instante da decisao
          (join por timestamp, nao idx//4 que enxergava o candle 4h ainda
          aberto — look-ahead em ~75% das velas); series 1h/4h validadas por
          contiguidade de timestamps (gap no banco ABORTA a medicao).
  B4    — Sharpe/Sortino/DSR calculados sobre retornos LIQUIDOS de taxa e
          sobre o capital (pnl/capital_antes), consistentes com PF/retorno/DD.
  B5    — taxa parametrizavel (--taxa); default 0.001 = spot taker Binance
          sem descontos (o 0.0004 herdado do motor era tarifa de futures).
  B6    — componente Fear & Greed usa o HISTORICO REAL do indice
          (data/fng_historico.json, alternative.me) passado pela funcao de
          score de producao — dias de medo/ganancia extremos bloqueiam
          entrada como em producao, em vez de score 100 fixo.
  B7    — benchmark buy-and-hold computado sobre o periodo efetivamente
          TESTADO (fechamento do 1o candle testado -> ultimo), com max
          drawdown da serie de precos e comparacao ajustada a risco.
  Censura final — posicao ainda aberta no fim dos dados e fechada A MERCADO
          no ultimo candle testado (tipo_saida=FIM_DADOS), nao descartada.
  Gap de janelas — teste_fim nao e mais clipado por JANELA_FUTURA (o clip
          so e necessario nos LABELS de treino); nenhum candle fica sem
          checagem de stop/target entre janelas.

--mtf-lookahead-legado: APENAS DIAGNOSTICO — reproduz o bug B1 (idx//4)
para quantificar o delta do vies. NUNCA usar para medicao oficial.

Uso:
  python backtesting/walk_forward.py
  python backtesting/walk_forward.py --par ETHUSDT
  python backtesting/walk_forward.py --treino 720 --teste 168
  python backtesting/walk_forward.py --taxa 0.00075   # maker+BNB
"""

import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicadores as ind
from backtesting.metricas import (
    calmar_ratio,
    probabilistic_sharpe_ratio,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)
from backtesting.motor_ensemble import SLIPPAGE, _adx, _score_backtest
from ml_filtro import extrair_features
from score import _score_fear_greed

DB_PATH = "data/btc_data.db"
FNG_PATH = "data/fng_historico.json"

STOP_PCT = 0.020
TARGET_PCT = 0.040
ALVO_PCT = 0.015
JANELA_FUTURA = 8
ADX_TENDENCIA = 25
ATR_EXTREMO = 2.5

# B5: taxa default = Binance SPOT taker sem descontos (0.10%). O bot real e
# maker-first (0.10% maker sem BNB / 0.075% com BNB) — 0.001 e o conservador.
TAXA_SPOT_DEFAULT = 0.001

MS_1H = 3_600_000
MS_4H = 14_400_000


def carregar(symbol, intervalo):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT timestamp, abertura, maxima, minima, fechamento, volume
        FROM klines WHERE symbol=? AND intervalo=?
        ORDER BY timestamp ASC
    """,
        (symbol, intervalo),
    ).fetchall()
    conn.close()
    return rows


def _validar_contiguidade(ts: list, passo_ms: int, nome: str) -> None:
    """B2: serie de klines precisa ser estritamente contigua — um gap no
    banco desalinharia o join 1h/4h e tornaria a medicao um artefato do
    estado do banco. ABORTA (SystemExit) se houver gap/duplicata."""
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] != passo_ms:
            dt_a = datetime.fromtimestamp(ts[i - 1] / 1000, tz=timezone.utc)
            dt_b = datetime.fromtimestamp(ts[i] / 1000, tz=timezone.utc)
            sys.exit(
                f"[GATE] ABORTADO: serie {nome} nao contigua entre "
                f"{dt_a:%Y-%m-%d %H:%M} e {dt_b:%Y-%m-%d %H:%M} "
                f"(delta {ts[i]-ts[i-1]}ms, esperado {passo_ms}ms). "
                f"Re-colete os dados (coletar_dados.py) antes de medir."
            )


def _mapear_idx4_fechado(ts1h: list, ts4h: list) -> list:
    """B1: para cada candle 1h i (decisao no FECHAMENTO, instante
    ts1h[i]+1h), o indice do ultimo candle 4h ja FECHADO nesse instante
    (ts4h[j]+4h <= ts1h[i]+1h). -1 se nenhum 4h fechado ainda."""
    resultado = []
    j = -1
    for i in range(len(ts1h)):
        instante_decisao = ts1h[i] + MS_1H
        while j + 1 < len(ts4h) and ts4h[j + 1] + MS_4H <= instante_decisao:
            j += 1
        resultado.append(j)
    return resultado


def _carregar_fng() -> dict:
    """Historico diario do Fear & Greed (alternative.me), dia UTC -> valor.
    Publicado no INICIO de cada dia (causal para decisoes do proprio dia)."""
    if not os.path.exists(FNG_PATH):
        return {}
    with open(FNG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _fng_do_dia(fng: dict, ts_ms: int) -> int | None:
    """Valor do F&G para o dia UTC do timestamp; carry-forward de ate 7 dias
    (causal — usa sempre o ultimo valor ja publicado)."""
    if not fng:
        return None
    dia = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    for _ in range(8):
        chave = dia.strftime("%Y-%m-%d")
        if chave in fng:
            return fng[chave]
        dia = datetime.fromtimestamp(dia.timestamp() - 86400, tz=timezone.utc)
    return None


def walk_forward(
    symbol="BTCUSDT",
    intervalo="1h",
    janela_treino=500,
    janela_teste=100,
    capital_inicial=1000.0,
    taxa=TAXA_SPOT_DEFAULT,
    mtf_lookahead_legado=False,
):
    """
    Walk-forward validation com retreino do XGBoost a cada janela.
    """
    k1h = carregar(symbol, intervalo)
    k4h = carregar(symbol, "4h")

    if len(k1h) < janela_treino + janela_teste + 100:
        return {
            "erro": f"Dados insuficientes ({len(k1h)} candles). Precisa de {janela_treino + janela_teste + 100}."
        }

    f1h = [r[4] for r in k1h]
    m1h = [r[2] for r in k1h]
    n1h = [r[3] for r in k1h]
    v1h = [r[5] for r in k1h]
    ts1h = [r[0] for r in k1h]

    f4h = [r[4] for r in k4h]
    ts4h = [r[0] for r in k4h]

    # B2: gap no banco = medicao abortada (nao reprodutivel/auditavel).
    _validar_contiguidade(ts1h, MS_1H, f"{symbol}/1h")
    _validar_contiguidade(ts4h, MS_4H, f"{symbol}/4h")

    ema20_4h = ind.ema(f4h, 20)
    ema50_4h = ind.ema(f4h, 50)

    # B1: join por timestamp — so candle 4h FECHADO no instante da decisao.
    idx4_fechado = _mapear_idx4_fechado(ts1h, ts4h)

    # B6: historico real do Fear & Greed (score da funcao de producao).
    fng = _carregar_fng()
    fng_ausente = not fng

    # Pre-computar indicadores 1H
    ema20 = ind.ema(f1h, 20)
    ema50 = ind.ema(f1h, 50)
    rsi14 = ind.rsi(f1h, 14)
    atr14 = ind.atr(m1h, n1h, f1h, 14)
    volr = ind.volume_relativo(v1h, 20)
    bbu, bbm, bbl = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bbu, bbm, bbl)
    vwap = ind.vwap_rolling(m1h, n1h, f1h, v1h, periodo=20)
    adx_vals = _adx(m1h, n1h, f1h, 14)

    def tend_4h_em(idx_1h):
        if mtf_lookahead_legado:
            # DIAGNOSTICO B1: mapeamento antigo (candle 4h AINDA ABERTO —
            # look-ahead). Mantido apenas para quantificar o delta do vies.
            idx4 = min(idx_1h // 4, len(f4h) - 1)
        else:
            idx4 = idx4_fechado[idx_1h]
            if idx4 < 0:
                return "LATERAL"
        if idx4 >= len(ema20_4h):
            return "LATERAL"
        p = f4h[idx4]
        e20 = ema20_4h[idx4]
        e50 = ema50_4h[idx4]
        if p > e20 > e50:
            return "ALTA"
        if p < e20 < e50:
            return "BAIXA"
        return "LATERAL"

    # Walk-forward loop
    capital = capital_inicial
    todas_ops = []
    janelas = []
    posicao = None
    janela_num = 0
    ultimo_i_testado = None

    inicio = 55  # minimo para features
    fim_dados = len(f1h)

    current = inicio + janela_treino

    def _fechar(preco_saida, tipo_saida, i, contabiliza_ganho=None):
        """Fecha a posicao corrente no preco dado, com taxa nos 2 lados.
        B4: registra tambem o retorno LIQUIDO sobre o capital antes do trade."""
        nonlocal capital, posicao
        pnl = (
            posicao["usdt"] * ((preco_saida - posicao["entrada"]) / posicao["entrada"])
            - posicao["usdt"] * taxa * 2
        )
        capital_antes = capital
        capital += pnl
        todas_ops.append(
            {
                "resultado": pnl,
                "resultado_pct": round((preco_saida - posicao["entrada"]) / posicao["entrada"] * 100, 4),
                "ret_capital_pct": round(pnl / capital_antes * 100, 4),
                "tipo_saida": tipo_saida,
                "janela": janela_num,
                "entrada_dt": posicao.get("dt", ""),
                "saida_dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime("%d/%m/%Y %H:%M"),
                "preco_entrada": posicao["entrada"],
                "preco_saida": round(preco_saida, 2),
            }
        )
        posicao = None
        return pnl

    while current + janela_teste < fim_dados:
        janela_num += 1
        treino_inicio = current - janela_treino
        treino_fim = current
        teste_inicio = current
        # Sem clip por JANELA_FUTURA: o clip so e necessario nos LABELS de
        # treino (que olham JANELA_FUTURA a frente), nao no loop de teste.
        teste_fim = current + janela_teste

        # --- Treinar XGBoost nesta janela ---
        from xgboost import XGBClassifier

        X_train, y_train = [], []
        for i in range(max(treino_inicio, 55), treino_fim - JANELA_FUTURA):
            feat = extrair_features(f1h, m1h, n1h, v1h, i)
            if feat is None:
                continue
            preco_futuro = max(f1h[i + 1 : i + JANELA_FUTURA + 1])
            label = 1 if (preco_futuro - f1h[i]) / f1h[i] >= ALVO_PCT else 0
            X_train.append(feat)
            y_train.append(label)

        modelo = None
        auc_janela = 0.0
        if len(X_train) > 50:
            X_tr = np.array(X_train)
            y_tr = np.array(y_train)
            ratio = (len(y_tr) - y_tr.sum()) / max(y_tr.sum(), 1)

            modelo = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                scale_pos_weight=ratio,
                eval_metric="logloss",
                verbosity=0,
            )
            modelo.fit(X_tr, y_tr)

            # AUC IN-SAMPLE (diagnostico apenas — calculado no proprio
            # treino; NAO usar como evidencia de generalizacao).
            try:
                from sklearn.metrics import roc_auc_score

                y_prob_tr = modelo.predict_proba(X_tr)[:, 1]
                auc_janela = roc_auc_score(y_tr, y_prob_tr)
            except Exception:
                pass

        # --- Testar nesta janela ---
        ops_janela = 0
        ganhos_janela = 0

        for i in range(teste_inicio, teste_fim):
            ultimo_i_testado = i
            preco = f1h[i]
            e20 = ema20[i]
            e50 = ema50[i]
            rsi_v = rsi14[i]
            atr_v = atr14[i]
            vr = volr[i]
            bw_v = bw[i]
            vwap_v = vwap[i]
            adx_v = adx_vals[i]

            if any(x is None for x in [rsi_v, atr_v, vr, bw_v, vwap_v]):
                continue

            atr_med = sum(x for x in atr14[max(0, i - 20) : i] if x) / max(
                1, len([x for x in atr14[max(0, i - 20) : i] if x])
            )
            atr_ratio = atr_v / atr_med if atr_med > 0 else 1.0

            # Saida
            if posicao:
                mn = n1h[i]
                mx = m1h[i]

                if mn <= posicao["stop"]:
                    pnl = _fechar(posicao["stop"] * (1 - SLIPPAGE), "STOP", i)
                    ops_janela += 1
                elif mx >= posicao["target"]:
                    pnl = _fechar(posicao["target"] * (1 - SLIPPAGE), "TARGET", i)
                    ops_janela += 1
                    if pnl > 0:
                        ganhos_janela += 1

            # Entrada
            if posicao is None:
                t4h = tend_4h_em(i)

                # ML prob
                ml_p = None
                if modelo:
                    feat = extrair_features(f1h, m1h, n1h, v1h, i)
                    if feat:
                        ml_p = float(modelo.predict_proba([feat])[0][1])

                # B6: score do F&G real do dia (funcao de producao); sem
                # historico disponivel, cai no default legado (100).
                fng_valor = _fng_do_dia(fng, ts1h[i])
                fg_score = _score_fear_greed(fng_valor) if fng_valor is not None else 100

                score, decisao, fator, _ = _score_backtest(
                    preco,
                    e20,
                    e50,
                    rsi_v,
                    atr_v,
                    atr_med,
                    vr,
                    bw_v,
                    sum(x for x in bw[max(0, i - 20) : i] if x)
                    / max(1, len([x for x in bw[max(0, i - 20) : i] if x])),
                    vwap_v,
                    t4h,
                    adx_v,
                    atr_ratio,
                    ml_p,
                    fear_greed_score=fg_score,
                )

                if fator > 0:
                    entrada = preco * (1 + SLIPPAGE)
                    usdt = min(capital * 0.02 / STOP_PCT, capital) * fator
                    posicao = {
                        "entrada": entrada,
                        "stop": entrada * (1 - STOP_PCT),
                        "target": entrada * (1 + TARGET_PCT),
                        "usdt": usdt,
                        "dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime("%d/%m/%Y %H:%M"),
                    }

        dt_ini = datetime.fromtimestamp(ts1h[treino_inicio] / 1000).strftime("%d/%m/%Y")
        dt_fim = datetime.fromtimestamp(ts1h[min(teste_fim, len(ts1h) - 1)] / 1000).strftime(
            "%d/%m/%Y"
        )

        janelas.append(
            {
                "janela": janela_num,
                "periodo": f"{dt_ini} - {dt_fim}",
                "treino_size": len(X_train),
                "auc_treino": round(auc_janela, 4),
                "trades": ops_janela,
                "ganhos": ganhos_janela,
                "capital": round(capital, 2),
            }
        )

        print(
            f"  Janela {janela_num:2d}: {dt_ini}-{dt_fim} | "
            f"Treino: {len(X_train)} amostras, AUC(in-sample): {auc_janela:.4f} | "
            f"Trades: {ops_janela} | Capital: ${capital:,.2f}"
        )

        current += janela_teste

    # Censura final: posicao ainda aberta e fechada A MERCADO no ultimo
    # candle testado (omiti-la enviesaria win rate/retorno/DD para cima).
    if posicao is not None and ultimo_i_testado is not None:
        _fechar(f1h[ultimo_i_testado] * (1 - SLIPPAGE), "FIM_DADOS", ultimo_i_testado)

    # Metricas finais
    if not todas_ops:
        return {"erro": "Nenhuma operacao no walk-forward."}

    total = len(todas_ops)
    ganhos = [o for o in todas_ops if o["resultado"] > 0]
    perdas = [o for o in todas_ops if o["resultado"] <= 0]
    wrate = len(ganhos) / total * 100
    lucro = sum(o["resultado"] for o in ganhos)
    perda = abs(sum(o["resultado"] for o in perdas))
    pf = profit_factor(lucro, perda)
    retorno = (capital - capital_inicial) / capital_inicial * 100

    pico = capital_inicial
    cap = capital_inicial
    max_dd = 0
    for o in todas_ops:
        cap += o["resultado"]
        if cap > pico:
            pico = cap
        dd = (pico - cap) / pico * 100 if pico > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # B4: metricas de risco sobre retornos LIQUIDOS de taxa e sobre o
    # CAPITAL (nao % de preco bruta) — consistente com PF/retorno/DD.
    rets = [o["ret_capital_pct"] for o in todas_ops]
    sharpe = sharpe_ratio(rets)

    # Periodo do backtest (para Calmar) — mesmo formato de datetime ja usado
    dt_ini = datetime.strptime(todas_ops[0]["entrada_dt"], "%d/%m/%Y %H:%M")
    dt_fim = datetime.strptime(todas_ops[-1]["saida_dt"], "%d/%m/%Y %H:%M")
    dias_periodo = max((dt_fim - dt_ini).total_seconds() / 86400, 0)

    mg = sum(o["resultado_pct"] for o in ganhos) / len(ganhos) if ganhos else 0
    mp = abs(sum(o["resultado_pct"] for o in perdas) / len(perdas)) if perdas else 0

    # B7: benchmark buy-and-hold sobre o periodo efetivamente TESTADO
    # (primeiro candle da 1a janela de teste -> ultimo candle testado).
    primeiro_i_teste = inicio + janela_treino
    bh = None
    if ultimo_i_testado is not None and primeiro_i_teste < len(f1h):
        p_ini = f1h[primeiro_i_teste]
        p_fim = f1h[ultimo_i_testado]
        bh_ret = (p_fim / p_ini - 1) * 100
        # Max drawdown da serie de PRECO no mesmo periodo (mark-to-market)
        pico_p = p_ini
        bh_dd = 0.0
        for px in f1h[primeiro_i_teste : ultimo_i_testado + 1]:
            if px > pico_p:
                pico_p = px
            dd = (pico_p - px) / pico_p * 100
            if dd > bh_dd:
                bh_dd = dd
        bh = {
            "retorno_%": round(bh_ret, 2),
            "max_dd_%": round(bh_dd, 2),
            "calmar_simples": round(bh_ret / bh_dd, 4) if bh_dd > 0 else None,
            "preco_inicio": p_ini,
            "preco_fim": p_fim,
            "dt_inicio": datetime.fromtimestamp(ts1h[primeiro_i_teste] / 1000).strftime(
                "%d/%m/%Y %H:%M"
            ),
            "dt_fim": datetime.fromtimestamp(ts1h[ultimo_i_testado] / 1000).strftime(
                "%d/%m/%Y %H:%M"
            ),
        }

    return {
        "symbol": symbol,
        "intervalo": intervalo,
        "janela_treino": janela_treino,
        "janela_teste": janela_teste,
        "taxa": taxa,
        "mtf_lookahead_legado": mtf_lookahead_legado,
        "fng_historico_usado": not fng_ausente,
        "total_janelas": janela_num,
        "total_trades": total,
        "win_rate_%": round(wrate, 1),
        "trades_ganhos": len(ganhos),
        "trades_perdas": len(perdas),
        "profit_factor": round(pf, 2),
        "retorno_total_%": round(retorno, 2),
        "capital_inicial": capital_inicial,
        "capital_final": round(capital, 2),
        "max_drawdown_%": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino_ratio(rets), 2),
        "calmar_ratio": round(calmar_ratio(retorno, max(max_dd, 0.0), dias_periodo), 2),
        # I-12: era `"dsr": deflated_sharpe_ratio(rets, None)`, que devolvia PSR
        # PURO (benchmark SR=0) com nome de DSR. Sem o numero de tentativas
        # nao ha deflacao a aplicar. A chave agora diz o que o numero e.
        "psr": round(probabilistic_sharpe_ratio(rets, 0.0), 4),
        "media_ganho_%": round(mg, 2),
        "media_perda_%": round(mp, 2),
        "buy_and_hold": bh,
        "janelas": janelas,
        "operacoes": todas_ops,
    }


def imprimir_relatorio(r):
    if "erro" in r:
        print(f"ERRO: {r['erro']}")
        return

    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD VALIDATION — {r['symbol']} [{r['intervalo']}]")
    if r.get("mtf_lookahead_legado"):
        print("  *** MODO DIAGNOSTICO (MTF com look-ahead legado — B1) ***")
        print("  *** NAO USAR COMO MEDICAO OFICIAL DO GATE ***")
    print(f"{'='*65}")
    print(
        f"  Janelas:  {r['total_janelas']} (treino: {r['janela_treino']} / teste: {r['janela_teste']})"
    )
    print(f"  Taxa/lado: {r['taxa']*100:.3f}%  |  F&G historico: "
          f"{'SIM' if r.get('fng_historico_usado') else 'NAO (score fixo 100)'}")
    print(f"  Trades:   {r['total_trades']} ({r['trades_ganhos']}W / {r['trades_perdas']}L)")
    print()
    print(f"  Win Rate:      {r['win_rate_%']:6.1f}%")
    print(f"  Profit Factor: {r['profit_factor']:6.2f}")
    print(f"  Sharpe Ratio:  {r['sharpe_ratio']:6.2f}  (sobre ret. liquido/capital)")
    print(f"  Max Drawdown:  {r['max_drawdown_%']:6.2f}%  (equity por trade fechado)")
    print(f"  Sortino Ratio: {r.get('sortino_ratio', 0):6.2f}")
    print(f"  Calmar Ratio:  {r.get('calmar_ratio', 0):6.2f}")
    # I-12: e PSR, nao DSR — o rotulo "DSR/PSR" deixava ambiguo qual dos dois.
    print(f"  PSR:           {r.get('psr', 0):6.4f}  (prob. Sharpe > 0; criterio do gate: >= 0.95)")
    print("                 (NAO e DSR: sem correcao por multiple-testing — ver metricas.py)")
    print(f"  Retorno Total: {r['retorno_total_%']:6.2f}%")
    print(f"  Capital:       ${r['capital_inicial']:,.2f} -> ${r['capital_final']:,.2f}")

    bh = r.get("buy_and_hold")
    if bh:
        print(f"\n  Buy-and-hold no periodo TESTADO ({bh['dt_inicio']} -> {bh['dt_fim']}):")
        print(f"    Retorno: {bh['retorno_%']:+.2f}%  |  Max DD (preco): {bh['max_dd_%']:.2f}%")
        print(f"    vs estrategia: {r['retorno_total_%']:+.2f}%  |  Max DD: {r['max_drawdown_%']:.2f}%")

    print()

    # Tabela de janelas
    print(f"  {'Jan':>3} {'Periodo':>25} {'Treino':>6} {'AUC(is)':>7} {'Trades':>6} {'Capital':>10}")
    print(f"  {'-'*62}")
    for j in r["janelas"]:
        print(
            f"  {j['janela']:3d} {j['periodo']:>25} {j['treino_size']:6d} "
            f"{j['auc_treino']:7.4f} {j['trades']:6d} ${j['capital']:>9,.2f}"
        )

    # AUC medio — IN-SAMPLE (diagnostico, nao evidencia de generalizacao)
    aucs = [j["auc_treino"] for j in r["janelas"] if j["auc_treino"] > 0]
    if aucs:
        print(
            f"\n  AUC medio (IN-SAMPLE, diagnostico): {statistics.mean(aucs):.4f} "
            f"(min: {min(aucs):.4f}, max: {max(aucs):.4f})"
        )

    print(f"\n{'-'*65}")
    print("  NOTA: o veredito oficial e EXCLUSIVAMENTE a tabela de criterios")
    print("  de docs/GATE_GO_LIVE.md (Etapa 1) — nao este relatorio.")
    print(f"{'='*65}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Walk-Forward Validation")
    parser.add_argument("--par", default="BTCUSDT")
    parser.add_argument("--intervalo", default="1h")
    parser.add_argument("--treino", type=int, default=500)
    parser.add_argument("--teste", type=int, default=100)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument(
        "--taxa",
        type=float,
        default=TAXA_SPOT_DEFAULT,
        help="taxa por lado (0.001 = spot taker; 0.00075 = maker+BNB)",
    )
    parser.add_argument(
        "--mtf-lookahead-legado",
        action="store_true",
        help="APENAS DIAGNOSTICO: reproduz o bug B1 para quantificar o vies",
    )
    args = parser.parse_args()

    print(f"\n[WALK-FORWARD] {args.par} — Validacao com retreino automatico...\n")
    r = walk_forward(
        args.par,
        args.intervalo,
        args.treino,
        args.teste,
        args.capital,
        taxa=args.taxa,
        mtf_lookahead_legado=args.mtf_lookahead_legado,
    )
    if r:
        imprimir_relatorio(r)
