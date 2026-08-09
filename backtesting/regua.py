"""
Régua única de medição (I-12).
===============================

## O problema

Havia duas funções de score no repositório, e a que decidia no backtest não era
a que decide em produção:

| componente | `score.calcular` (produção) | `_score_backtest` (backtest) |
|---|---:|---:|
| regime      | 18 | 25 |
| ml          | 20 | 15 |
| mtf         | 12 | 20 |
| ema         |  8 | 10 |
| fear_greed  |  8 | 10 |
| rsi         |  8 |  8 |
| vwap        |  5 |  5 |
| volume      |  4 |  4 |
| atr         |  2 |  3 |
| **cvd**     |  **7** | **ausente** |
| **obi**     |  **8** | **ausente** |

Quinze pontos de peso — CVD e OBI — simplesmente não existiam no backtest, e os
outros nove componentes tinham pesos diferentes. Os bloqueios absolutos também
divergiam: produção bloqueia em `LATERAL` e em Fear&Greed <= 20 ou > 80; o
backtest bloqueava só por ADX baixo e ATR extremo.

Consequência: todo número de backtest media uma estratégia que não é a que roda.
Otimizar parâmetros contra essa régua escolhe o ótimo de OUTRA função.

## O que este módulo faz

`score_unificado()` chama `score.calcular` — a de produção, sem cópia — a partir
dos arrays que um backtest tem. O adaptador existe porque `score.calcular` pede
`regime_info` e `fear_info` (dicts que em produção vêm de `regime.detectar()` e
`fear_greed.obter()`, ambos com I/O), e o backtest precisa construí-los do
histórico.

A classificação de regime reusa os MESMOS limiares de `regime.py`
(`ADX_TENDENCIA`, `ATR_EXTREMO`) e a mesma árvore de decisão de
`regime._classificar_tf` — não uma reimplementação aproximada.

## As duas diferenças que permanecem, e por quê

**1. CVD e OBI entram neutros (50).** Não é escolha: são 15% do peso que
dependem de tick-a-tick (`@aggTrade`) e de livro de ordens (`@depth`), e não
existe histórico desses streams. Fingir um valor seria inventar sinal; usar
pesos diferentes seria voltar ao problema original. Neutro é a única resposta
honesta, e o resultado é conservador — o backtest não recebe crédito por um
componente que não pode medir.

**2. O regime vem de UM timeframe, não de três.** Produção vota entre 1h/4h/1d
com peso duplo no 4h (`regime.detectar`). O backtest tem 1h e 4h; o 1d não é
carregado pelos motores. O adaptador aceita `regime_final` pronto quando o
chamador puder computá-lo melhor (walk_forward pode), e cai na classificação de
1 timeframe quando não.

Ambas ficam registradas em `avisos` no retorno, para que nenhum relatório
apresente o número como equivalente ao de produção sem dizer o que falta.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score as score_producao  # noqa: E402
from regime import ADX_TENDENCIA, ATR_EXTREMO  # noqa: E402

# Registrado no retorno de score_unificado(): o que esta régua NÃO consegue
# medir. Existe para que um relatório não possa omitir a limitação por descuido.
AVISOS_PADRAO = (
    "cvd e obi neutros (50): sem historico de @aggTrade/@depth — 15% do peso",
    "regime de 1 timeframe: producao vota 1h/4h/1d com peso duplo no 4h",
)


def classificar_regime(preco, ema20, ema50, adx, atr_ratio) -> str:
    """Mesma árvore de decisão de `regime._classificar_tf`, sobre escalares.

    Reusa os limiares importados de `regime.py` em vez de repetir os números:
    se alguém ajustar ADX_TENDENCIA lá, o backtest acompanha. Foi exatamente a
    divergência silenciosa entre duas cópias que I-12 existe para eliminar.
    """
    if atr_ratio > ATR_EXTREMO:
        return "VOLATILIDADE"
    if adx >= ADX_TENDENCIA and preco > ema20 > ema50:
        return "TENDENCIA_ALTA"
    if adx >= ADX_TENDENCIA and preco < ema20 < ema50:
        return "TENDENCIA_BAIXA"
    return "LATERAL"


def forca_do_regime(adx, votos_no_regime: int = 3, total_votos: int = 3) -> int:
    """Mesma aritmética de `regime.detectar()`: score_adx + score_tf.

    Com um único timeframe, `votos_no_regime == total_votos` e o componente de
    votos vai ao máximo — o que é coerente: não há discordância entre timeframes
    porque só há um. O que se perde é a PENALIZAÇÃO por conflito, e é por isso
    que essa limitação está em AVISOS_PADRAO.
    """
    score_adx = min(adx / 40.0 * 50, 50)  # ADX_FORTE = 40 em regime.py
    score_tf = (votos_no_regime / max(total_votos, 1)) * 50
    return round(score_adx + score_tf)


def score_unificado(
    preco,
    ema20,
    ema50,
    rsi,
    atr_atual,
    atr_media,
    vol_rel,
    vwap_val,
    tend_4h,
    adx,
    atr_ratio,
    ml_prob=None,
    fear_greed_valor: int = 50,
    regime_final: str | None = None,
    regime_score: int | None = None,
    rsi_min: int = 42,
    rsi_max: int = 62,
    score_operar: int = 60,
    score_cheio: int = 70,
):
    """Score de PRODUÇÃO calculado sobre dados de backtest.

    Devolve `(score_total, decisao, tamanho_fator, scores, avisos)` — as quatro
    primeiras posições são a mesma tupla que `_score_backtest` devolvia, para que
    os motores troquem a chamada sem reescrever o laço.

    `fear_greed_valor` default 50 = zona neutra. O walk_forward passa o valor
    REAL do histórico do índice; os motores que não têm esse dado ficam com o
    neutro — e, ao contrário do `_score_backtest`, isso não vira score 100 de
    graça: `_score_fear_greed(50)` é o valor que a produção daria para F&G 50, e
    os bloqueios de medo/ganância extremos passam a poder disparar.
    """
    if regime_final is None:
        regime_final = classificar_regime(preco, ema20, ema50, adx, atr_ratio)
    if regime_score is None:
        regime_score = forca_do_regime(adx)

    regime_info = {"regime_final": regime_final, "score": regime_score}
    fear_info = {"valor": int(fear_greed_valor)}

    r = score_producao.calcular(
        regime_info=regime_info,
        fear_info=fear_info,
        tend_4h=tend_4h,
        ml_prob=ml_prob,
        preco=preco,
        ema20=ema20,
        ema50=ema50,
        rsi=rsi,
        vwap_val=vwap_val,
        vol_rel=vol_rel,
        atr_atual=atr_atual,
        atr_media=atr_media,
        # Sem histórico de tick nem de livro — ver AVISOS_PADRAO.
        historico_ticks=None,
        obi=None,
        rsi_min=rsi_min,
        rsi_max=rsi_max,
        score_operar=score_operar,
        score_cheio=score_cheio,
    )
    return (
        r["score_total"],
        r["decisao"],
        r["tamanho_fator"],
        r["scores"],
        AVISOS_PADRAO,
    )
