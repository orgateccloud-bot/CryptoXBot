"""
Métricas puras de backtesting — BotBinance
=============================================
Sem I/O algum (sem sqlite3/requests/etc). Extrai cálculos duplicados
5x entre os engines de backtest para um único ponto de verdade.
"""

import math
import statistics

GAMMA_EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(retornos_pct: list[float], periodos_por_ano: int = 252) -> float:
    """Extrai o cálculo já duplicado 5x nos engines. stdev AMOSTRAL (n-1)."""
    if len(retornos_pct) < 2:
        return 0.0
    std = statistics.stdev(retornos_pct)
    if std == 0:
        return 0.0
    media = statistics.mean(retornos_pct)
    return (media / std) * (periodos_por_ano**0.5)


def sortino_ratio(
    retornos_pct: list[float], mar_pct: float = 0.0, periodos_por_ano: int = 252
) -> float:
    """Full downside deviation: media sobre TODOS os n retornos (denominador
    populacional n, upside tratado como 0 no desvio)."""
    if not retornos_pct:
        return 0.0
    n = len(retornos_pct)
    media = statistics.mean(retornos_pct)
    downside_var = sum(min(0.0, r - mar_pct) ** 2 for r in retornos_pct) / n
    downside_dev = downside_var**0.5
    if downside_dev == 0:
        return 999.0 if media > mar_pct else 0.0
    return ((media - mar_pct) / downside_dev) * (periodos_por_ano**0.5)


def calmar_ratio(
    retorno_total_pct: float, max_drawdown_pct: float, dias_periodo: float, dias_ano: int = 365
) -> float:
    """dias_periodo calculado PELO CALLER (parse de datas) -- esta função é
    só aritmética pura. Pouco confiável para dias_periodo muito curto."""
    base = 1 + retorno_total_pct / 100
    if base <= 0:
        # Perda total ou pior (retorno_total_pct <= -100%): base**fracionario
        # vira numero complexo em Python -- CAGR nao tem significado aqui,
        # sentinel simetrico ao 999.0 "otimo" usado no resto do modulo.
        return -999.0
    dias = max(dias_periodo, 1)
    retorno_anualizado = (base ** (dias_ano / dias) - 1) * 100
    if max_drawdown_pct > 0:
        return retorno_anualizado / max_drawdown_pct
    return 999.0 if retorno_anualizado > 0 else 0.0


def profit_factor(lucro_bruto: float, perda_bruta: float) -> float:
    return lucro_bruto / perda_bruta if perda_bruta else 999.0


def _skew_kurtosis_populacional(retornos_pct: list[float]) -> tuple[float, float]:
    """Skewness e kurtosis RAW (normal=3), momento com denominador POPULACIONAL n."""
    n = len(retornos_pct)
    media = statistics.mean(retornos_pct)
    var_pop = sum((r - media) ** 2 for r in retornos_pct) / n
    std_pop = var_pop**0.5
    if std_pop == 0:
        return 0.0, 3.0  # neutro (evita divisao por zero)
    m3 = sum((r - media) ** 3 for r in retornos_pct) / n
    m4 = sum((r - media) ** 4 for r in retornos_pct) / n
    return m3 / std_pop**3, m4 / std_pop**4


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probabilistic_sharpe_ratio(retornos_pct: list[float], sr_benchmark: float = 0.0) -> float:
    """Bailey & Lopez de Prado 2012. SR_hat usado aqui é SEMPRE nao-anualizado
    (periodos_por_ano=1) -- sr_benchmark deve estar na MESMA escala nao-anualizada."""
    n = len(retornos_pct)
    if n < 2:
        return 0.5
    sr_hat = sharpe_ratio(retornos_pct, periodos_por_ano=1)
    skew, kurt = _skew_kurtosis_populacional(retornos_pct)
    denom_sq = 1 - skew * sr_hat + ((kurt - 1) / 4) * sr_hat**2
    if denom_sq <= 0:
        return 0.5
    z = (sr_hat - sr_benchmark) * ((n - 1) ** 0.5) / (denom_sq**0.5)
    return _phi(z)


def sharpe_esperado_max(sharpes_trials: list[float]) -> float:
    """Bailey & Lopez de Prado 2014 (Deflated Sharpe Ratio), formula
    getExpectedMaxSR: SR_bar + sigma_SR * [(1-gamma)*Zinv(1-1/N) +
    gamma*Zinv(1-1/(N*e))] -- o termo SR_bar (media dos trials) e parte
    da formula original, sem ele o deflator subestima o Sharpe esperado
    sob a hipotese nula e o DSR fica artificialmente otimista. ATENCAO:
    os valores em sharpes_trials devem estar na MESMA escala (nao-anualizada)
    que sera usada em probabilistic_sharpe_ratio -- se o caller so tem
    Sharpes anualizados (ex: sharpe = media/std * sqrt(252)), desanualizar
    antes: sharpe / sqrt(252)."""
    n = len(sharpes_trials)
    if n <= 1:
        return 0.0
    sr_bar = statistics.mean(sharpes_trials)
    sigma_n = statistics.stdev(sharpes_trials)
    if sigma_n == 0:
        return sr_bar
    nd = statistics.NormalDist()
    termo1 = nd.inv_cdf(1 - 1 / n)
    termo2 = nd.inv_cdf(1 - 1 / (n * math.e))
    return sr_bar + sigma_n * (
        (1 - GAMMA_EULER_MASCHERONI) * termo1 + GAMMA_EULER_MASCHERONI * termo2
    )


def cvar_historico(retornos_pct: list[float], nivel_confianca: float = 0.95) -> float:
    """CVaR histórico (Expected Shortfall) não-paramétrico: média dos retornos
    na cauda (os piores, abaixo do quantil 1-nivel_confianca). Mesma unidade/
    escala de retornos_pct. 0.0 se dados insuficientes ou nivel_confianca fora
    de (0,1) -- mesmo padrão de guarda das demais métricas deste módulo."""
    n = len(retornos_pct)
    if n < 2 or not (0.0 < nivel_confianca < 1.0):
        return 0.0
    ordenados = sorted(retornos_pct)
    n_cauda = max(1, math.ceil((1 - nivel_confianca) * n))
    return statistics.mean(ordenados[:n_cauda])


def deflated_sharpe_ratio(retornos_pct: list[float], sharpes_trials: list[float]) -> float:
    """DSR = PSR(SR*) com SR* = sharpe_esperado_max(sharpes_trials).

    I-12: `sharpes_trials` deixou de ter default None.

    O que a versão anterior fazia: com `sharpes_trials=None`, o benchmark caía
    para SR* = 0 e a função devolvia **PSR puro** — a probabilidade de o Sharpe
    ser maior que ZERO, sem nenhuma correção por quantas configurações foram
    testadas. Quatro dos cinco chamadores passavam None e gravavam o resultado
    sob a chave `"dsr"`. O dashboard servia `dsr = 0.839`: um número que não era
    DSR e que, lido como DSR, sugere "sobrevive à correção por múltiplas
    hipóteses" quando nenhuma correção foi aplicada.

    O deflated Sharpe existe precisamente para punir a busca. Sem o número de
    tentativas ele não tem como punir nada — chamar o resultado de DSR é o mesmo
    erro de chamar de "hold-out" um conjunto que foi reavaliado.

    Quem quer só a probabilidade contra SR=0 deve chamar
    `probabilistic_sharpe_ratio(retornos, 0.0)` e gravar sob a chave `"psr"`:
    igualmente útil, e honesto. Uma lista com <= 1 trial continua aceita e
    equivale a PSR — uma única tentativa não tem multiplicidade a corrigir; o
    que se recusa é o silêncio de não informar.
    """
    if sharpes_trials is None:
        raise TypeError(
            "deflated_sharpe_ratio exige sharpes_trials (I-12). Sem o numero de "
            "tentativas nao ha deflacao — o resultado seria PSR puro com nome de "
            "DSR. Para PSR, use probabilistic_sharpe_ratio(retornos, 0.0)."
        )
    if len(sharpes_trials) <= 1:
        sr_benchmark = 0.0
    else:
        sr_benchmark = sharpe_esperado_max(sharpes_trials)
    return probabilistic_sharpe_ratio(retornos_pct, sr_benchmark)
