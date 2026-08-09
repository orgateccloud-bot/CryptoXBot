"""
Alinhamento causal entre timeframes (I-12).
============================================

## O defeito que este módulo elimina

Quatro motores de backtest mapeavam o candle 1h `i` para o 4h com

    idx4 = min(i // 4, len(f4h) - 1)

Isso devolve o candle 4h que **contém** o 1h — e esse 4h só fecha depois. No
instante da decisão (fechamento do 1h `i`), seu `close`, `EMA20` e `EMA50` ainda
vão mudar, porque incorporam candles 1h que ainda não aconteceram.

Medido em BTCUSDT/1h no snapshot de 2026-08-08: **17.563 de 17.563 barras
(100%)** liam um 4h ainda aberto. Não é viés sutil de borda — é o futuro
entrando em toda barra, no componente MTF, que pesa 12% do score unificado e é
um dos filtros binários da estratégia.

A correção existia desde a Etapa 1 do gate, dentro de `walk_forward.py`, e nunca
foi propagada. Este módulo é essa função, num lugar só, para que "propagar" deixe
de ser copiar.

## Contrato

`mapear_idx_fechado(ts_rapido, ts_lento, ms_rapido, ms_lento)` devolve, para cada
índice `i` do timeframe rápido, o índice do último candle lento **já fechado** no
instante `ts_rapido[i] + ms_rapido` (o fechamento do candle rápido, que é quando
a decisão acontece). `-1` quando nenhum candle lento fechou ainda.

Os dois vetores de timestamp precisam estar em ordem crescente e em milissegundos
— o formato que a tabela `klines` e a API da Binance usam.
"""

from __future__ import annotations

MS_1M = 60_000
MS_15M = 15 * MS_1M
MS_1H = 60 * MS_1M
MS_4H = 4 * MS_1H
MS_1D = 24 * MS_1H

# Duração de cada intervalo, para quem tem o rótulo e não o número.
DURACAO_MS = {
    "1m": MS_1M,
    "15m": MS_15M,
    "1h": MS_1H,
    "4h": MS_4H,
    "1d": MS_1D,
}


def mapear_idx_fechado(
    ts_rapido: list[int],
    ts_lento: list[int],
    ms_rapido: int = MS_1H,
    ms_lento: int = MS_4H,
) -> list[int]:
    """Para cada candle rápido `i`, o índice do último candle lento JÁ FECHADO.

    Varredura de dois ponteiros — O(n+m), não O(n·m): o mapeamento é recalculado
    a cada backtest e a série tem dezenas de milhares de barras.

    -1 significa "nenhum candle lento fechou ainda"; o chamador precisa tratar
    esse caso explicitamente (tipicamente devolvendo o valor neutro do
    indicador). Devolver 0 no lugar seria reintroduzir look-ahead pela porta dos
    fundos, no começo da série.
    """
    resultado: list[int] = []
    j = -1
    n_lento = len(ts_lento)
    for i in range(len(ts_rapido)):
        instante_decisao = ts_rapido[i] + ms_rapido
        while j + 1 < n_lento and ts_lento[j + 1] + ms_lento <= instante_decisao:
            j += 1
        resultado.append(j)
    return resultado


def mapear_por_intervalo(
    ts_rapido: list[int], ts_lento: list[int], intervalo_rapido: str, intervalo_lento: str
) -> list[int]:
    """Mesma coisa, resolvendo a duração pelos rótulos ('1h', '4h')."""
    try:
        return mapear_idx_fechado(
            ts_rapido, ts_lento, DURACAO_MS[intervalo_rapido], DURACAO_MS[intervalo_lento]
        )
    except KeyError as exc:
        raise ValueError(
            f"intervalo sem duracao conhecida: {exc}. Conhecidos: {sorted(DURACAO_MS)}"
        ) from exc


def violacoes_de_causalidade(
    ts_rapido: list[int], ts_lento: list[int], idxs: list[int],
    ms_rapido: int = MS_1H, ms_lento: int = MS_4H,
) -> list[tuple[int, int]]:
    """(i, idx4) das barras em que o candle lento escolhido NÃO havia fechado.

    Existe para que o teste de causalidade seja uma consulta e não uma
    reimplementação da regra: `assert violacoes_de_causalidade(...) == []`.
    Também serve para auditar um mapeamento vindo de fora (por exemplo, provar
    que o `i // 4` antigo viola em 100% das barras).
    """
    ruins = []
    for i, j in enumerate(idxs):
        if j < 0:
            continue  # "sem dado ainda" é válido, não é violação
        if j >= len(ts_lento):
            ruins.append((i, j))
            continue
        if ts_lento[j] + ms_lento > ts_rapido[i] + ms_rapido:
            ruins.append((i, j))
    return ruins
