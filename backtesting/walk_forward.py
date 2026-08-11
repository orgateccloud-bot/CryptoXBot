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

--politica-saida: "producao" (default) modela a saida que o bot executa —
parcial de 50% no target1, stop a breakeven, target2 e trailing. "alvo_unico"
e o modelo antigo (um stop, um alvo), que nao corresponde ao bot e serve so
para medir a diferenca.

Uso:
  python backtesting/walk_forward.py
  python backtesting/walk_forward.py --par ETHUSDT
  python backtesting/walk_forward.py --treino 720 --teste 168
  python backtesting/walk_forward.py --taxa 0.00075   # maker+BNB
  python backtesting/walk_forward.py --politica-saida alvo_unico  # comparacao
"""

import json
import os
import sqlite3
import statistics
import sys
from collections import Counter
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
from backtesting.motor_ensemble import SLIPPAGE, _adx
from backtesting.regua import score_unificado
from config.params_pares import get_params
from ml_filtro import extrair_features

DB_PATH = "data/btc_data.db"
FNG_PATH = "data/fng_historico.json"

# I-12: eram os limiares USADOS na medicao, como constantes de modulo. Nao
# batiam com NENHUM par de config/params_pares.py:
#
#     par        stop_pct  target_pct     walk_forward media com
#     BTCUSDT      0,015      0,050          0,020 / 0,040
#     ETHUSDT      0,020      0,060          0,020 / 0,040
#     SOLUSDT      0,030      0,050          0,020 / 0,040
#
# O relatorio dizia "ETH/SOL medidos com os limiares do BTC"; e pior — os
# limiares nao eram de ninguem. O gate mediu os tres pares com parametros
# ficticios, e o SIZING depende de stop_pct (usdt = capital*0,02/STOP_PCT),
# entao o tamanho de posicao tambem estava errado nos tres.
#
# Mantidos so como fallback para um par sem entrada em params_pares.
STOP_PCT_FALLBACK = 0.020
TARGET_PCT_FALLBACK = 0.040
ALVO_PCT = 0.015
JANELA_FUTURA = 8
ADX_TENDENCIA = 25
ATR_EXTREMO = 2.5

# B5: taxa default = Binance SPOT taker sem descontos (0.10%). O bot real e
# maker-first (0.10% maker sem BNB / 0.075% com BNB) — 0.001 e o conservador.
TAXA_SPOT_DEFAULT = 0.001

MS_1H = 3_600_000
MS_4H = 14_400_000

# ── Politica de saida real (I-12h) ────────────────────────────────────────
#
# O backtest media UMA saida: stop fixo ou alvo fixo. O bot roda outra coisa
# (executor.avaliar_tick_monitor):
#
#   1. stop loss                              -> terminal
#   2. no target1, vende 50% e move o stop para breakeven (entrada*1,002)
#   3. com a parcial feita, target2 fecha o resto -> terminal
#   4. a partir de +1% de ganho, o stop segue o pico a -0,8% (trailing)
#
# O item 4 e o que mais falta: sem trailing, o backtest deixa cada posicao
# correr ate o alvo cheio ou ate o stop inicial. Em producao a maioria sai
# muito antes, arrastada pelo trailing.
#
# Sobre 2 e 3: `target2` e `entrada*1,05` HARDCODED em executor.abrir_long,
# enquanto `target1` vem do par. Nos tres pares configurados isso torna o
# "runner" inerte:
#
#     par        target1 (par)   target2 (fixo)   runner corre?
#     BTCUSDT      1,050            1,050          nao — iguais
#     ETHUSDT      1,060            1,050          nao — target2 ABAIXO
#     SOLUSDT      1,050            1,050          nao — iguais
#
# Ou seja: a metade "runner" fecha no tick seguinte ao da parcial, no mesmo
# preco. Nao e um bug do backtest — e o que a producao faz. O backtest passa
# a reproduzir isso em vez de fingir uma saida unica.
FRACAO_PARCIAL = 0.5  # vende metade no target1
BREAKEVEN_MULT = 1.002  # stop pos-parcial (executor.avaliar_tick_monitor)
TARGET2_MULT = 1.05  # alvo do runner, hardcoded em executor.abrir_long
TRAILING_ATIVACAO = 0.01  # ativa trailing apos 1% de ganho
TRAILING_DISTANCIA = 0.008  # stop segue 0,8% abaixo do pico

POLITICAS_SAIDA = ("producao", "alvo_unico")


def avaliar_tick_saida(
    entrada,
    stop_atual,
    target1,
    target2,
    parcial_feita,
    preco_alta,
    preco_baixa,
    preco_pico,
    trailing_ativacao=TRAILING_ATIVACAO,
    trailing_distancia=TRAILING_DISTANCIA,
):
    """Decisao PURA de um tick de saida, com alta e baixa separadas.

    Espelha `executor.avaliar_tick_monitor` na ordem e nos limiares. A unica
    diferenca e que o preco entra em duas pontas: um candle 1h nao tem preco
    unico, e a convencao conservadora do backtest e testar o STOP contra a
    MINIMA e os ALVOS contra a MAXIMA. Com preco_alta == preco_baixa a funcao
    decide exatamente o mesmo que a de producao — `tests/test_walk_forward.py
    ::TestParidadeComProducao` prova isso sobre uma grade de estados.

    Nao decide `parcial_feita and preco >= target2` no mesmo tick em que a
    parcial dispara: producao le o snapshot do estado, entao o runner so pode
    fechar num tick POSTERIOR. O loop do backtest reproduz isso chamando esta
    funcao duas vezes no mesmo candle (uma hora tem ticks de sobra).

    `stop_breakeven` e `novo_stop_trailing` saem SEPARADOS, como em producao,
    e o chamador aplica nesta ordem — breakeven, depois trailing. Nao e
    detalhe cosmetico: `executor._aplicar_novo_stop` grava `stop_atual =
    novo_stop` sem checar se o valor SOBE (executor.py:1212), entao a parcial
    pode BAIXAR um stop que o trailing ja tinha subido. Um `max()` aqui
    mediria um bot melhor do que o que roda.
    """
    acao = {
        "fechar_total": None,
        "fechar_parcial": False,
        "stop_breakeven": None,
        "novo_stop_trailing": None,
        "preco_pico": preco_pico,
    }

    # 1. Stop (terminal) — contra a MINIMA. Vem primeiro tambem no candle
    #    ambiguo, que toca stop e alvo: nao da para saber a ordem intra-candle
    #    e a convencao conservadora e assumir a pior.
    if preco_baixa <= stop_atual:
        acao["fechar_total"] = "STOP"
        return acao

    # 2. Parcial de 50% no target1 — nao encerra
    if not parcial_feita and preco_alta >= target1:
        acao["fechar_parcial"] = True
        acao["stop_breakeven"] = entrada * BREAKEVEN_MULT

    # 3. Target2 fecha o resto (terminal) — so com a parcial JA feita
    if parcial_feita and preco_alta >= target2:
        acao["fechar_total"] = "TARGET_FINAL"
        return acao

    # 4. Trailing a partir de trailing_ativacao de ganho. A comparacao e
    #    contra o stop do SNAPSHOT, nao contra o breakeven recem-decidido —
    #    de novo, como em producao.
    if (preco_alta - entrada) / entrada >= trailing_ativacao:
        pico = preco_alta if preco_alta > preco_pico else preco_pico
        acao["preco_pico"] = pico
        novo = pico * (1 - trailing_distancia)
        if novo > stop_atual:
            acao["novo_stop_trailing"] = novo

    return acao


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


class FngIndisponivel(RuntimeError):
    """Historico de Fear & Greed ausente ou sem cobertura do periodo (I-12).

    Excecao propria para que um chamador programatico consiga distinguir "nao ha
    dado de sentimento" de qualquer outra falha — e para que o `--sem-fng` seja
    uma decisao explicita, registrada no resultado, em vez de um default mudo.
    """


def _cobertura_fng(fng: dict, ts1h: list) -> dict:
    """Quantos dias do periodo medido tem valor de F&G (com carry de 7 dias).

    Checar EXISTENCIA do arquivo nao basta: um historico que cobre 2024 e para
    em 2025 passaria na checagem e deixaria metade da medicao sem veto de
    sentimento. O que importa e a cobertura do periodo que sera medido.
    """
    if not ts1h:
        return {"cobre": False, "dias": 0, "faltantes": 0, "inicio": "-", "fim": "-"}
    ini = datetime.fromtimestamp(ts1h[0] / 1000, tz=timezone.utc)
    fim = datetime.fromtimestamp(ts1h[-1] / 1000, tz=timezone.utc)
    dias, faltantes = 0, 0
    d = ini
    while d <= fim:
        dias += 1
        if _fng_do_dia(fng, int(d.timestamp() * 1000)) is None:
            faltantes += 1
        d = datetime.fromtimestamp(d.timestamp() + 86400, tz=timezone.utc)
    return {
        "cobre": dias > 0 and faltantes == 0,
        "dias": dias,
        "faltantes": faltantes,
        "inicio": ini.strftime("%Y-%m-%d"),
        "fim": fim.strftime("%Y-%m-%d"),
    }


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
    permitir_sem_fng=False,
    politica_saida="producao",
):
    """
    Walk-forward validation com retreino do XGBoost a cada janela.

    politica_saida (I-12h):
      "producao"   — parcial 50% no target1, stop a breakeven, target2 e
                     trailing, como executor.avaliar_tick_monitor. Default.
      "alvo_unico" — modelo antigo (um stop, um alvo). Nao corresponde ao bot;
                     existe so para medir o quanto a politica real muda o
                     resultado.
    """
    if politica_saida not in POLITICAS_SAIDA:
        raise ValueError(f"politica_saida invalida: {politica_saida!r}; use {POLITICAS_SAIDA}")

    # I-12: parametros DO PAR, nao constantes de modulo.
    _p = get_params(symbol)
    stop_pct = _p.get("stop_pct", STOP_PCT_FALLBACK)
    target_pct = _p.get("target_pct", TARGET_PCT_FALLBACK)
    rsi_min = _p.get("rsi_min", 42)
    rsi_max = _p.get("rsi_max", 62)
    score_operar = _p.get("score_operar", 60)
    score_cheio = _p.get("score_cheio", 70)
    print(
        f"  [PARAMS] {symbol}: stop={stop_pct:.3f} target={target_pct:.3f} "
        f"rsi=[{rsi_min},{rsi_max}] score_operar={score_operar} "
        f"saida={politica_saida}"
    )
    if politica_saida == "producao" and target_pct >= TARGET2_MULT - 1:
        # Ver a nota da politica no topo: target2 e fixo em 1,05. Quando
        # target1 >= target2, a metade "runner" fecha no tick seguinte ao da
        # parcial, no mesmo preco — a saida em dois estagios e decorativa.
        print(
            f"  [NOTA] target1 ({1 + target_pct:.3f}) >= target2 ({TARGET2_MULT:.3f}): "
            f"o runner nao corre; quem move a saida e o trailing."
        )

    k1h = carregar(symbol, intervalo)
    k4h = carregar(symbol, "4h")

    if len(k1h) < janela_treino + janela_teste + 100:
        return {
            "erro": f"Dados insuficientes ({len(k1h)} candles). Precisa de "
            "{janela_treino + janela_teste + 100}."
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

    # B6/I-12: historico real do Fear & Greed — e ABORTA se nao houver.
    #
    # `data/fng_historico.json` NAO EXISTE nesta maquina. Toda medicao do gate
    # rodou sem F&G, e o unico vestigio disso era o campo
    # `fng_historico_usado: false` enterrado no JSON de saida — ninguem le.
    #
    # Nao e detalhe: em producao o Fear & Greed e um BLOQUEIO ABSOLUTO (veta em
    # <= 20 e em > 80). Medir sem ele significa medir uma estrategia que nunca
    # e vetada por sentimento — e o backtest ganha todos os trades que a
    # producao teria recusado em panico ou euforia extrema. Sempre para melhor.
    fng = _carregar_fng()
    fng_ausente = not fng
    cobertura = _cobertura_fng(fng, ts1h)
    if not permitir_sem_fng and (fng_ausente or not cobertura["cobre"]):
        raise FngIndisponivel(
            f"Fear & Greed indisponivel ou incompleto — medicao ABORTADA.\n"
            f"  arquivo .......... {FNG_PATH} "
            f"({'ausente' if fng_ausente else str(len(fng)) + ' dias'})\n"
            f"  periodo medido ... {cobertura['inicio']} -> {cobertura['fim']}\n"
            f"  dias sem valor ... {cobertura['faltantes']} de {cobertura['dias']}\n"
            f"\n"
            f"  Em producao o F&G VETA a entrada (<=20 ou >80). Sem o historico, o\n"
            f"  backtest nunca aplica esse veto e superestima o desempenho.\n"
            f"  Para medir mesmo assim (exploratorio, NAO vale para o gate):\n"
            f"    python backtesting/walk_forward.py --par {symbol} --sem-fng"
        )
    if fng_ausente or not cobertura["cobre"]:
        print(
            f"  [AVISO] F&G incompleto ({cobertura['faltantes']}/{cobertura['dias']} dias "
            f"sem valor). Resultado NAO vale para o gate."
        )

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

    def _perna(preco_saida, fracao, rotulo, i):
        """Vende `fracao` do notional AINDA ABERTO, com taxa nos 2 lados.

        A taxa se divide proporcionalmente: duas pernas de meio notional a
        `taxa*2` custam o mesmo que uma perna cheia — a conta nao muda por
        fatiar a saida, so por sair em precos diferentes.
        """
        notional = posicao["usdt_aberto"] * fracao
        pnl = (
            notional * ((preco_saida - posicao["entrada"]) / posicao["entrada"])
            - notional * taxa * 2
        )
        posicao["usdt_aberto"] -= notional
        posicao["pnl_acumulado"] += pnl
        posicao["pernas"].append(
            {
                "rotulo": rotulo,
                "preco": round(preco_saida, 6),
                "notional": round(notional, 4),
                "pnl": pnl,
                "dt": datetime.fromtimestamp(ts1h[i] / 1000).strftime("%d/%m/%Y %H:%M"),
            }
        )
        return pnl

    def _fechar(preco_saida, tipo_saida, i):
        """Encerra o round trip: vende o que sobrou e registra UMA operacao.

        Uma posicao pode sair em duas pernas (parcial + final), mas
        economicamente e UM trade. Registrar duas operacoes inflaria
        `total_trades` e mediria Sharpe/win rate sobre meias-posicoes.
        `resultado` e o PnL do round trip inteiro; as pernas ficam em
        `pernas` para auditoria.

        B4: registra tambem o retorno LIQUIDO sobre o capital antes do trade.
        """
        nonlocal capital, posicao
        _perna(preco_saida, 1.0, tipo_saida, i)
        pnl = posicao["pnl_acumulado"]
        capital_antes = capital
        capital += pnl
        todas_ops.append(
            {
                "resultado": pnl,
                # I-12h: com saida em pernas, "(saida-entrada)/entrada" nao
                # descreve o trade — cada perna saiu num preco. O retorno do
                # round trip e o PnL liquido sobre o notional aplicado.
                "resultado_pct": round(pnl / posicao["usdt"] * 100, 4),
                "ret_capital_pct": round(pnl / capital_antes * 100, 4),
                "tipo_saida": tipo_saida,
                "parcial_feita": posicao["parcial_feita"],
                "pernas": posicao["pernas"],
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

                if politica_saida == "alvo_unico":
                    # Modo legado, mantido so para comparacao: uma saida so.
                    if mn <= posicao["stop_atual"]:
                        pnl = _fechar(posicao["stop_atual"] * (1 - SLIPPAGE), "STOP", i)
                        ops_janela += 1
                        if pnl > 0:
                            ganhos_janela += 1
                    elif mx >= posicao["target1"]:
                        pnl = _fechar(posicao["target1"] * (1 - SLIPPAGE), "TARGET", i)
                        ops_janela += 1
                        if pnl > 0:
                            ganhos_janela += 1
                else:
                    # I-12h: politica de producao. Duas passadas no mesmo
                    # candle porque uma hora tem muitos ticks: producao le o
                    # snapshot de `parcial_feita`, entao a parcial dispara num
                    # tick e o runner so pode fechar no seguinte — que ainda
                    # cai dentro da mesma hora.
                    #
                    # VIES CONHECIDO, medido em 2026-08-09 (BTCUSDT, 488
                    # trades): TARGET_FINAL nunca dispara. Na 2a passada o
                    # stop e checado antes do target2, e a essa altura
                    # `stop_atual` ja e o trailing do pico DESTE candle
                    # (pico*0,992). Para o runner chegar em target2, a minima
                    # do candle teria de ficar a menos de 0,8% da propria
                    # maxima — praticamente nunca. Producao, que roda por
                    # tick, fecharia em target2 (~pico) no tick seguinte ao da
                    # parcial. O vies e conservador e limitado: ~0,8% sobre
                    # METADE da posicao, em 3 dos 488 trades. Manter a
                    # convencao stop-first (a ordem intra-candle e desconhecida
                    # e e a mesma escolha ja feita no candle ambiguo).
                    for _passada in range(2):
                        if posicao is None:
                            break
                        d = avaliar_tick_saida(
                            entrada=posicao["entrada"],
                            stop_atual=posicao["stop_atual"],
                            target1=posicao["target1"],
                            target2=posicao["target2"],
                            parcial_feita=posicao["parcial_feita"],
                            preco_alta=mx,
                            preco_baixa=mn,
                            preco_pico=posicao["pico"],
                        )
                        posicao["pico"] = d["preco_pico"]

                        if d["fechar_parcial"]:
                            _perna(
                                posicao["target1"] * (1 - SLIPPAGE),
                                FRACAO_PARCIAL,
                                "TARGET_PARCIAL",
                                i,
                            )
                            posicao["parcial_feita"] = True

                        # Ordem identica a de _monitorar: breakeven primeiro,
                        # trailing depois. Ver a nota em avaliar_tick_saida —
                        # a parcial PODE baixar um stop ja trilhado, e o
                        # backtest tem de reproduzir isso, nao corrigir.
                        if d["stop_breakeven"] is not None:
                            posicao["stop_atual"] = d["stop_breakeven"]
                            posicao["stop_movido"] = True
                        if d["novo_stop_trailing"] is not None:
                            posicao["stop_atual"] = d["novo_stop_trailing"]
                            posicao["stop_movido"] = True

                        if d["fechar_total"] == "STOP":
                            # Rotulo diz QUAL stop levou a posicao: o inicial,
                            # ou um ja arrastado pelo breakeven/trailing. Sem
                            # isso, "STOP" no relatorio confunde perda com
                            # lucro travado.
                            rotulo = "STOP_MOVIDO" if posicao["stop_movido"] else "STOP"
                            pnl = _fechar(posicao["stop_atual"] * (1 - SLIPPAGE), rotulo, i)
                            ops_janela += 1
                            if pnl > 0:
                                ganhos_janela += 1
                            break
                        if d["fechar_total"] == "TARGET_FINAL":
                            pnl = _fechar(posicao["target2"] * (1 - SLIPPAGE), "TARGET_FINAL", i)
                            ops_janela += 1
                            if pnl > 0:
                                ganhos_janela += 1
                            break
                        if not d["fechar_parcial"]:
                            break  # nada mudou nesta passada; a 2a seria igual

            # Entrada
            if posicao is None:
                t4h = tend_4h_em(i)

                # ML prob
                ml_p = None
                if modelo:
                    feat = extrair_features(f1h, m1h, n1h, v1h, i)
                    if feat:
                        ml_p = float(modelo.predict_proba([feat])[0][1])

                # B6/I-12: valor BRUTO do F&G do dia, nao o score.
                #
                # Passava-se `fear_greed_score` (0-100 ja convertido), e por isso
                # os BLOQUEIOS ABSOLUTOS de medo/ganancia extremos nunca podiam
                # disparar: score.calcular decide o bloqueio pelo valor cru
                # (<= 20 ou > 80), nao pelo score. F&G era componente de peso, e
                # em producao e tambem um veto.
                #
                # Sem historico, 50 (neutro) — nao 100. O default antigo de 100
                # era o SCORE maximo: o backtest ganhava o componente inteiro de
                # graca todo dia em que faltasse dado.
                fng_valor = _fng_do_dia(fng, ts1h[i])
                fg_valor = fng_valor if fng_valor is not None else 50

                score, decisao, fator, _, _avisos = score_unificado(
                    preco=preco,
                    ema20=e20,
                    ema50=e50,
                    rsi=rsi_v,
                    atr_atual=atr_v,
                    atr_media=atr_med,
                    vol_rel=vr,
                    vwap_val=vwap_v,
                    tend_4h=t4h,
                    adx=adx_v,
                    atr_ratio=atr_ratio,
                    ml_prob=ml_p,
                    fear_greed_valor=fg_valor,
                    rsi_min=rsi_min,
                    rsi_max=rsi_max,
                    score_operar=score_operar,
                    score_cheio=score_cheio,
                )

                if fator > 0:
                    entrada = preco * (1 + SLIPPAGE)
                    usdt = min(capital * 0.02 / stop_pct, capital) * fator
                    posicao = {
                        "entrada": entrada,
                        "stop_inicial": entrada * (1 - stop_pct),
                        "stop_atual": entrada * (1 - stop_pct),
                        "stop_movido": False,
                        # target1 vem do par; target2 e o 1,05 hardcoded de
                        # executor.abrir_long (ver nota da politica no topo)
                        "target1": entrada * (1 + target_pct),
                        "target2": entrada * TARGET2_MULT,
                        "parcial_feita": False,
                        "pico": entrada,
                        "usdt": usdt,  # notional inicial (para resultado_pct)
                        "usdt_aberto": usdt,  # o que ainda nao foi vendido
                        "pnl_acumulado": 0.0,
                        "pernas": [],
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
        "politica_saida": politica_saida,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        # I-12h: como as posicoes efetivamente sairam. Se "STOP_MOVIDO"
        # domina, quem determina o resultado e o trailing — nao o alvo.
        "saidas_por_tipo": dict(sorted(Counter(o["tipo_saida"] for o in todas_ops).items())),
        "trades_com_parcial": sum(1 for o in todas_ops if o.get("parcial_feita")),
        "fng_historico_usado": not fng_ausente,
        "fng_cobertura": cobertura,
        "vale_para_o_gate": bool(cobertura["cobre"]),
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
        f"  Janelas:  {r['total_janelas']} (treino: {r['janela_treino']} / teste: "
        f"{r['janela_teste']})"
    )
    print(
        f"  Taxa/lado: {r['taxa']*100:.3f}%  |  F&G historico: "
        f"{'SIM' if r.get('fng_historico_usado') else 'NAO (F&G neutro 50)'}"
    )
    print(f"  Trades:   {r['total_trades']} ({r['trades_ganhos']}W / {r['trades_perdas']}L)")

    # I-12h: por onde as posicoes sairam de verdade.
    saidas = r.get("saidas_por_tipo") or {}
    if saidas:
        print(
            f"  Politica de saida: {r.get('politica_saida', '?')}"
            f"  (stop {r.get('stop_pct', 0)*100:.1f}% / alvo {r.get('target_pct', 0)*100:.1f}%)"
        )
        detalhe = "  ".join(
            f"{tipo}={n} ({n / r['total_trades'] * 100:.0f}%)" for tipo, n in saidas.items()
        )
        print(f"  Saidas:   {detalhe}")
        print(f"  Com parcial de 50%: {r.get('trades_com_parcial', 0)} de {r['total_trades']}")
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
        print(
            f"    vs estrategia: {r['retorno_total_%']:+.2f}%  |  Max DD: "
            f"{r['max_drawdown_%']:.2f}%"
        )

    print()

    # Tabela de janelas
    print(
        f"  {'Jan':>3} {'Periodo':>25} {'Treino':>6} {'AUC(is)':>7} {'Trades':>6} {'Capital':>10}"
    )
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
        "--sem-fng",
        action="store_true",
        help="mede sem historico de Fear & Greed (EXPLORATORIO — nao vale p/ o gate)",
    )
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
    parser.add_argument(
        "--politica-saida",
        choices=POLITICAS_SAIDA,
        default="producao",
        help="producao = parcial 50%% + breakeven + trailing (o que o bot faz); "
        "alvo_unico = modelo antigo, so p/ comparar",
    )
    args = parser.parse_args()

    print(f"\n[WALK-FORWARD] {args.par} — Validacao com retreino automatico...\n")
    try:
        r = walk_forward(
            args.par,
            args.intervalo,
            args.treino,
            args.teste,
            args.capital,
            taxa=args.taxa,
            mtf_lookahead_legado=args.mtf_lookahead_legado,
            permitir_sem_fng=args.sem_fng,
            politica_saida=args.politica_saida,
        )
    except FngIndisponivel as exc:
        # I-12: exit != 0. Uma medicao que nao vale para o gate nao pode sair
        # com codigo 0 e ser encadeada por um script como se valesse.
        print(f"\n[GATE] {exc}")
        raise SystemExit(2) from exc
    if r:
        imprimir_relatorio(r)
