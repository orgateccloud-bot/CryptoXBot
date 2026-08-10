"""
Estratégia Otimizada — Camada 1+2
===================================
Filtros adicionados sobre a estratégia base (EMA+RSI):

  1. Multi-Timeframe (MTF): 4H deve confirmar tendência de alta antes de entrar no 1H
  2. ATR Filter:            Não entrar se ATR < 50% da média (mercado lateral/sem força)
  3. Volume Filter:         Vela de sinal deve ter volume > 1.3x a média de 20 períodos
  4. Bollinger Squeeze:     Não entrar após squeeze (banda estreita sem expansão)
  5. VWAP Filter:           Preço deve estar acima do VWAP para longs
  6. Market Structure:      Só comprar em HH/HL (estrutura de alta confirmada)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import database
import ensemble as ens
import fear_greed as fg
import indicadores as ind
import regime as reg
import score as sc
import suporte as sup
from config.params_pares import get_params
from data.klines import obter_klines

# Filtros estruturais (iguais para todos os pares)
ATR_MIN_RATIO = 0.6
VOL_MIN_RATIO = 1.3
VWAP_FILTER = True
MTF_FILTER = True


def _klines(intervalo, limite=100, symbol="BTCUSDT"):
    """P1-5: delega para data.klines (cache TTL + REST_BASE_URL). Mesmo
    contrato de antes (lança em falha -- este arquivo nunca engolia
    exceção de rede, e o try/except de main.py:loop_par já cobre isso)."""
    dados = obter_klines(symbol, intervalo, limite)
    if dados is None:
        raise RuntimeError(f"Falha ao obter klines de {symbol}/{intervalo}")
    return dados


def analisar(
    symbol="BTCUSDT",
    cvd_atual=None,
    ml_prob=None,
    ensemble_result=None,
    historico_ticks=None,
    obi=None,
):
    """
    Avalia a estratégia otimizada para o par especificado.
    ml_prob: probabilidade do XGBoost (retrocompat)
    ensemble_result: resultado do ensemble (XGB + LSTM)
    historico_ticks: ticks brutos recentes (formato preco/is_buyer_maker/
        quantidade) para o componente CVD do score (P1-1) -- None (default)
        preserva o comportamento anterior (componente CVD neutro em 50).
        Hoje só populado de verdade para BTCUSDT (único par com WebSocket
        de ticks ao vivo em main.py).
    obi: Order Book Imbalance suavizado (P1-1), em [-1,1] -- None (default)
        preserva o componente OBI do score neutro em 50. Idem historico_ticks,
        só populado para BTCUSDT (único par com WebSocket @depth ao vivo).
    """
    # Carregar parâmetros otimizados para o par
    p = get_params(symbol)
    RSI_MIN = p["rsi_min"]
    RSI_MAX = p["rsi_max"]
    STOP_PCT = p["stop_pct"]
    TARGET_PCT = p["target_pct"]

    d1h = _klines("1h", 100, symbol)
    d4h = _klines("4h", 60, symbol)

    f1h = d1h["fechamento"]
    preco = f1h[-1]

    # ── Indicadores 1H ────────────────────────────────────────
    ema20 = ind.ema(f1h, 20)[-1]
    ema50 = ind.ema(f1h, 50)[-1]
    rsi14 = ind.rsi(f1h)[-1]
    atr14 = ind.atr(d1h["maxima"], d1h["minima"], f1h, 14)
    atr_media = sum(x for x in atr14[-20:] if x) / 20
    atr_atual = atr14[-1] if atr14[-1] else 0

    vol_rel = ind.volume_relativo(d1h["volume"], 20)[-1] or 0

    bb_upper, bb_mid, bb_lower = ind.bollinger(f1h, 20, 2)
    bw = ind.bandwidth(bb_upper, bb_mid, bb_lower)
    bw_atual = bw[-1] or 0
    bw_media = sum(x for x in bw[-20:] if x) / 20

    vwap_val = ind.vwap(d1h["maxima"], d1h["minima"], f1h, d1h["volume"])[-1]

    # ── Multi-Timeframe 4H ────────────────────────────────────
    f4h = d4h["fechamento"]
    ema20_4h = ind.ema(f4h, 20)[-1]
    ema50_4h = ind.ema(f4h, 50)[-1]
    tend_4h = (
        "ALTA"
        if f4h[-1] > ema20_4h > ema50_4h
        else "BAIXA" if f4h[-1] < ema20_4h < ema50_4h else "LATERAL"
    )

    funding = 0.0

    # ── Regime de Mercado ─────────────────────────────────────
    # E-7: regime DO PAR. Antes reg.detectar() lia sempre BTCUSDT, e este valor
    # entra em dois lugares que somam boa parte da decisao: o filtro "regime"
    # abaixo e o componente de 18% do score unificado.
    regime_info = reg.detectar(symbol)
    fear_info = fg.obter()

    # ── Ensemble ML (XGBoost + LSTM) ──────────────────────────
    ensemble_indisponivel = False
    if ensemble_result is None:
        try:
            # E-7: ensemble DO PAR. Ha modelo XGBoost treinado para cada um dos
            # tres pares (data/modelo_xgb_{par}.pkl) — o que faltava era alguem
            # passar o symbol para que fosse carregado.
            ensemble_result = ens.prever(symbol, regime_info["regime_final"])
        except Exception as e:
            # E-10: o fail-open PREMIAVA a falha. Substituia o ensemble por
            # {prob 0.5, pode_operar True}, e `_score_ml(0.5)` vale 50 de 100
            # num componente que pesa 20 pontos — modelo quebrado entregava 10
            # pontos de graca e ainda liberava o filtro "ml". Pior: `pode_operar`
            # nao e gate da decisao (quem decide e o score), entao nem o filtro
            # segurava.
            #
            # Agora e fail-CLOSED: modelo indisponivel nao pontua neutro, nao
            # pontua nada — a avaliacao devolve AGUARDAR. Reduzir exposicao
            # seria o minimo; nao operar e o correto enquanto 20 dos 100 pontos
            # do score vem de um numero que nao existe.
            print(f"[OTIMIZADA] {symbol}: ensemble indisponivel ({e}) — AGUARDAR")
            ensemble_indisponivel = True
            ensemble_result = {"prob_ensemble": None, "pode_operar": False,
                               "confianca": "INDISPONIVEL"}

    ml_ensemble_prob = ensemble_result.get("prob_ensemble", ml_prob or 0.5)
    ml_pode = ensemble_result.get("pode_operar", True)

    # ── Avaliação dos filtros ──────────────────────────────────
    filtros = {
        "ema_1h": preco > ema20 > ema50,
        "rsi": RSI_MIN <= (rsi14 or 0) <= RSI_MAX,
        "atr": atr_atual >= atr_media * ATR_MIN_RATIO,
        "volume": vol_rel >= VOL_MIN_RATIO,
        "bollinger": bw_atual >= bw_media * 0.8,
        "vwap": preco > vwap_val if VWAP_FILTER else True,
        "mtf_4h": tend_4h in ("ALTA", "LATERAL") if MTF_FILTER else True,
        "regime": regime_info["pode_operar"] and regime_info["regime_final"] == "TENDENCIA_ALTA",
        "fear_greed": fear_info["pode_operar"],
        "cvd": (cvd_atual is None) or (cvd_atual > 0),
        "ml": ml_pode and ml_ensemble_prob >= 0.55,
    }

    aprovados = sum(filtros.values())
    total_fil = len(filtros)

    # ── Score Unificado ───────────────────────────────────────
    score_result = sc.calcular(
        regime_info=regime_info,
        fear_info=fear_info,
        tend_4h=tend_4h,
        ml_prob=ml_ensemble_prob,
        preco=preco,
        ema20=ema20,
        ema50=ema50,
        rsi=rsi14,
        vwap_val=vwap_val,
        vol_rel=vol_rel,
        atr_atual=atr_atual,
        atr_media=atr_media,
        historico_ticks=historico_ticks,
        obi=obi,
        rsi_min=RSI_MIN,
        rsi_max=RSI_MAX,
        score_operar=p["score_operar"],
        score_cheio=p["score_cheio"],
    )

    # Decisao baseada no score (substitui logica de filtros binarios)
    decisao = score_result["decisao"]
    tamanho_fator = score_result["tamanho_fator"]

    # E-10: ensemble indisponivel veta a entrada, seja qual for o score. O
    # componente ML pesa 20 dos 100 pontos; operar sem ele e operar com um
    # quinto da regua faltando — e antes disso o fail-open ainda ENTREGAVA
    # esses pontos como se o modelo tivesse opinado.
    if ensemble_indisponivel:
        decisao = "AGUARDAR"
        tamanho_fator = 0.0
        sinal = "AGUARDAR"
    elif decisao == "OPERAR_CHEIO" and filtros["ema_1h"] and filtros["cvd"]:
        sinal = "COMPRA"
    elif decisao == "OPERAR_REDUZIDO" and filtros["ema_1h"] and filtros["cvd"]:
        sinal = "COMPRA"
    else:
        sinal = "AGUARDAR"

    # Sinal de venda (short)
    filtros_short = {
        "ema_1h": preco < ema20 < ema50,
        "rsi": RSI_MIN <= (rsi14 or 0) <= RSI_MAX,
        "atr": atr_atual >= atr_media * ATR_MIN_RATIO,
        "volume": vol_rel >= VOL_MIN_RATIO,
        "vwap": preco < vwap_val if VWAP_FILTER else True,
        "mtf_4h": tend_4h == "BAIXA" if MTF_FILTER else True,
        "regime": regime_info["pode_operar"] and regime_info["regime_final"] == "TENDENCIA_BAIXA",
        "fear_greed": fear_info["pode_operar"],
        "cvd": (cvd_atual is None) or (cvd_atual < 0),
        "ml": (ml_prob is None) or (ml_prob >= 0.60),
    }
    if all(filtros_short.values()) and decisao != "AGUARDAR":
        sinal = "VENDA"

    # ── Suportes e Resistencias ─────────────────────────────────
    # E-7: suportes DO PAR. Esta linha era `sup.detectar_suportes("1h")`, sem
    # symbol, e o nivel devolvido (do BITCOIN) sobrescrevia o stop logo abaixo.
    # Era a causa direta do bloco de producao com entrada ~$1.858 e stop
    # $63.521,65: para ETH, qualquer suporte de BTC e "acima do preco", entao a
    # comparacao `stop_suporte > stop` era sempre verdadeira.
    suporte_info = sup.detectar_suportes(symbol, "1h")

    stop = (
        round(preco * (1 - STOP_PCT), 2)
        if sinal == "COMPRA"
        else round(preco * (1 + STOP_PCT), 2) if sinal == "VENDA" else None
    )
    target = (
        round(preco * (1 + TARGET_PCT), 2)
        if sinal == "COMPRA"
        else round(preco * (1 - TARGET_PCT), 2) if sinal == "VENDA" else None
    )

    # Stop abaixo do suporte forte (mais inteligente que % fixo)
    #
    # E-7: o override so pode APERTAR o stop, nunca move-lo para cima do preco.
    # Corrigir o symbol (acima) reduz o absurdo mas nao o elimina: `suporte_forte`
    # sai de um cluster que inclui EMA20, EMA50 e VWAP, e qualquer um dos tres
    # pode estar ACIMA do preco atual (tipico em queda). Nesse caso a condicao
    # `stop_suporte > stop` continua verdadeira e o stop passaria a entrada — com
    # o par certo, so por uma margem menor. A guarda e sobre a GRANDEZA, nao
    # sobre a procedencia do dado.
    if sinal == "COMPRA" and suporte_info["suporte_forte"] > 0:
        stop_suporte = round(suporte_info["suporte_forte"] * 0.995, 2)  # 0.5% abaixo do suporte
        if stop < stop_suporte < preco:
            stop = stop_suporte  # stop mais apertado, e ainda abaixo da entrada

    # Ajustar target se Fear & Greed pede reducao
    if target and fear_info.get("reducao_alvo"):
        alvo_pct = TARGET_PCT * 0.5
        target = (
            round(preco * (1 + alvo_pct), 2)
            if sinal == "COMPRA"
            else round(preco * (1 - alvo_pct), 2) if sinal == "VENDA" else target
        )

    # E-8: `timestamp` e ISO-8601, porque e o campo que vai para o BANCO
    # (logger.registrar_avaliacao o grava em log_avaliacoes.timestamp). Era
    # '%d/%m/%Y %H:%M:%S', e a query do relatorio diario filtra
    # `timestamp LIKE 'YYYY-MM-DD%'` — que nunca casa com '08/08/2026 09:10:53'.
    # Medido em 2026-08-08: 7.625 avaliacoes gravadas, e a query do dia devolvia
    # 0 linhas enquanto 111 tinham sido gravadas naquele dia. logger.py converte
    # o None resultante em 0.0, entao o alerta das 18h reportava "0 avaliacoes,
    # PnL 0,00" todos os dias — uma mentira na direcao tranquilizadora, o pior
    # tipo num sistema de risco.
    #
    # `timestamp_br` existe para a apresentacao continuar legivel sem que o
    # formato humano volte a contaminar o banco.
    agora = datetime.now()
    resultado = {
        "timestamp": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_br": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "sinal": sinal,
        "score": score_result["score_total"],
        "score_decisao": decisao,
        "tamanho_fator": tamanho_fator,
        "preco": preco,
        "ema20_1h": round(ema20, 2),
        "ema50_1h": round(ema50, 2),
        "ema20_4h": round(ema20_4h, 2),
        "ema50_4h": round(ema50_4h, 2),
        "rsi": round(rsi14 or 0, 2),
        "atr": round(atr_atual, 2),
        "atr_media": round(atr_media, 2),
        "volume_rel": round(vol_rel, 2),
        "vwap": round(vwap_val, 2),
        "bw": round(bw_atual, 4),
        "bw_media": round(bw_media, 4),
        "tend_4h": tend_4h,
        "funding_%": round(funding, 4),
        "regime": regime_info["regime_final"],
        "regime_score": regime_info["score"],
        "fear_greed": fear_info["valor"],
        "fear_greed_pt": fear_info["classificacao_pt"],
        "cvd": cvd_atual,
        "ml_prob": ml_prob,
        "ml_ensemble": ml_ensemble_prob,
        "ml_confianca": ensemble_result.get("confianca", "?"),
        "ml_xgb": ensemble_result.get("prob_xgb"),
        "ml_lstm": ensemble_result.get("prob_lstm"),
        "stop_loss": stop,
        "take_profit": target,
        "filtros": filtros,
        "filtros_ok": aprovados,
        "filtros_total": total_fil,
        "score_result": score_result,
        "suporte_forte": suporte_info["suporte_forte"],
        "suporte_dist_%": suporte_info["distancia_%"],
        "suporte_zona": suporte_info["na_zona"],
        "suporte_info": suporte_info,
    }

    resultado["symbol"] = symbol
    resultado["sinal_id"] = None  # P1-3: id da linha em `sinais`, se de fato salva

    # ── E-7: invariante de coerencia, na origem do sinal ──────────
    # Um sinal com stop >= preco nao e um sinal ruim, e um sinal INVALIDO: se
    # chegar ao executor, ou a Binance rejeita o STOP_LOSS_LIMIT (posicao real
    # DESPROTEGIDA) ou o monitor local o liquida no primeiro tick, pagando
    # spread + duas taxas. Rebaixar para AGUARDAR aqui e mais seguro que
    # confiar em quem consome o dict, e o motivo fica no proprio resultado.
    incoerencia = _incoerencia_de_precos(sinal, preco, stop, target)
    if incoerencia:
        resultado["sinal"] = "AGUARDAR"
        resultado["sinal_original"] = sinal
        resultado["incoerencia"] = incoerencia
        sinal = "AGUARDAR"

    if sinal != "AGUARDAR":
        motivo = (
            f"Score:{score_result['score_total']} | MTF:{tend_4h} | "
            f"RSI:{rsi14:.1f} | ATR:{atr_atual:.0f} | "
            f"VolRel:{vol_rel:.2f}x | FG:{fear_info['valor']}"
        )
        resultado["motivo_sinal"] = motivo

    return resultado


def _incoerencia_de_precos(sinal, preco, stop, target):
    """Devolve str descrevendo a incoerencia, ou None se os precos sao validos.

    Invariante (E-7):
        COMPRA -> 0 < stop < preco < target
        VENDA  -> 0 < target < preco < stop

    Funcao pura e separada de proposito: e reusada por main.py e por
    executor.abrir_long, para que a checagem seja a MESMA nos tres pontos em vez
    de tres reimplementacoes que podem divergir.
    """
    if sinal not in ("COMPRA", "VENDA"):
        return None
    if not preco or preco <= 0:
        return f"preco invalido: {preco}"
    if stop is None or target is None:
        return f"stop/target ausentes (stop={stop}, target={target})"
    if stop <= 0 or target <= 0:
        return f"stop/target nao positivos (stop={stop}, target={target})"
    if sinal == "COMPRA":
        if not stop < preco:
            return f"COMPRA com stop {stop} >= entrada {preco}"
        if not preco < target:
            return f"COMPRA com target {target} <= entrada {preco}"
    else:
        if not stop > preco:
            return f"VENDA com stop {stop} <= entrada {preco}"
        if not target < preco:
            return f"VENDA com target {target} >= entrada {preco}"
    return None


def registrar_sinal(resultado):
    """Persiste o sinal em `sinais` e devolve o id (ou None).

    E-7: a ESCRITA saiu de `analisar()`, que agora e puro (le mercado, decide,
    devolve dict). Motivo medido: dashboard.py chamava `analisar()` a cada 30s
    por par so para exibir, e cada chamada gravava uma linha em `sinais` — a
    MESMA tabela que a Etapa 2 do gate le para julgar se ha edge. O banco de
    decisao estava sendo escrito pela camada de apresentacao.

    Idempotencia nao e garantida aqui: chamar duas vezes grava duas linhas. O
    contrato e que so o worker chame, uma vez por avaliacao.
    """
    if resultado.get("sinal") in (None, "AGUARDAR"):
        return None
    # P1-3: o id liga entrada e resultado do mesmo trade
    # (marcar_sinal_executado / atualizar_sinal_fechamento).
    resultado["sinal_id"] = database.salvar_sinal(
        resultado["sinal"],
        resultado["preco"],
        resultado.get("motivo_sinal", ""),
        symbol=resultado.get("symbol", "BTCUSDT"),
        score=resultado.get("score"),
        source="estrategia_otimizada",
    )
    return resultado["sinal_id"]


def imprimir(
    symbol="BTCUSDT",
    cvd_atual=None,
    ml_prob=None,
    ensemble_result=None,
    historico_ticks=None,
    obi=None,
    resultado=None,
):
    """Imprime o bloco de analise.

    E-7: aceita `resultado` ja calculado. Antes esta funcao SEMPRE chamava
    analisar() de novo — main.py fazia analisar_otimizada(...) e em seguida
    imprimir_otimizada(...), entao cada avaliacao rodava a estrategia DUAS vezes,
    gravava DUAS linhas em `sinais` e podia imprimir numeros de um calculo
    diferente do que foi enviado ao executor (klines fora do TTL de 30s,
    fear&greed, ensemble). Passar o dict elimina os tres problemas de uma vez.
    """
    r = (
        resultado
        if resultado is not None
        else analisar(symbol, cvd_atual, ml_prob, ensemble_result, historico_ticks, obi)
    )
    verde = "\033[92m"
    vermelho = "\033[91m"
    amarelo = "\033[93m"
    cinza = "\033[90m"
    reset = "\033[0m"

    cor_sinal = verde if r["sinal"] == "COMPRA" else vermelho if r["sinal"] == "VENDA" else amarelo

    print("\n" + "=" * 60)
    print(
        f"  ESTRATEGIA OTIMIZADA — {r.get('symbol', 'BTCUSDT')}  (MTF + ATR + Volume + VWAP + ML)"
    )
    print(f"  {r.get('timestamp_br') or r['timestamp']}")
    print("=" * 60)
    print(f"  Preco:    ${r['preco']:,.2f}")
    print(f"  EMA20/50 1H: ${r['ema20_1h']:,.2f} / ${r['ema50_1h']:,.2f}")
    print(f"  EMA20/50 4H: ${r['ema20_4h']:,.2f} / ${r['ema50_4h']:,.2f}  [{r['tend_4h']}]")
    print(f"  RSI:      {r['rsi']}  |  ATR: {r['atr']:.1f} (media: {r['atr_media']:.1f})")
    print(f"  Volume:   {r['volume_rel']:.2f}x media  |  VWAP: ${r['vwap']:,.2f}")
    print(f"  Bollinger BW: {r['bw']:.4f} (media: {r['bw_media']:.4f})")
    ens_p = r.get("ml_ensemble")
    xgb_p = r.get("ml_xgb")
    lstm_p = r.get("ml_lstm")
    print(
        f"  ML Ensemble: {ens_p*100:.1f}% "
        f"(XGB:{xgb_p*100:.0f}% LSTM:{lstm_p*100:.0f}% {r.get('ml_confianca','?')})"
        if ens_p and xgb_p and lstm_p
        else f"  ML Prob: {(r.get('ml_prob') or 0)*100:.1f}%"
    )
    print(f"  Regime:   {r.get('regime','?')}  (score {r.get('regime_score','?')}/100)")
    print(f"  Fear & Greed: {r.get('fear_greed','?')}/100 — {r.get('fear_greed_pt','?')}")
    print()

    print("  FILTROS:")
    for nome, ok in r["filtros"].items():
        icone = f"{verde}OK  {reset}" if ok else f"{vermelho}FAIL{reset}"
        print(f"  [{icone}] {nome}")

    print(f"\n  Filtros aprovados: {r['filtros_ok']}/{r['filtros_total']}")

    # Score visual
    sc.imprimir_score(r["score_result"])

    print(f"\n  {cor_sinal}*** SINAL: {r['sinal']} ***{reset}")
    if r["sinal"] != "AGUARDAR":
        print(f"  Stop Loss:    ${r['stop_loss']:,.2f}")
        print(f"  Take Profit:  ${r['take_profit']:,.2f}")
        if r.get("tamanho_fator", 1.0) < 1.0:
            print(f"  {amarelo}Tamanho: 50% (score entre 60-74){reset}")

    print("=" * 60)
    return r


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--par", default="BTCUSDT")
    args = parser.parse_args()
    database.inicializar()
    imprimir(symbol=args.par)
