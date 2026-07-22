"""
Gestão de Risco — BotBinance
==============================
Módulos:
  1. Kelly Criterion        — tamanho ideal da posição
  2. Max Drawdown Diário    — trava o bot se perder X% no dia
  3. Circuit Breaker        — desliga em volatilidade extrema
  4. Calculadora de posição — tamanho em BTC dado o risco em USDT
  5. Validador de trade     — checa todas as condições antes de executar
"""

import hashlib
import hmac
import statistics
import threading
import time
from datetime import date, datetime

import requests

import database
import health
import telegram_bot
from backtesting.metricas import cvar_historico
from config.params_pares import PARAMS_PARES
from config.runtime_settings import API_KEY, API_SECRET, REST_BASE_URL
from data.klines import obter_klines

BASE_URL = REST_BASE_URL  # P0-1: endpoint unico (spot por padrao) vindo do config

# ── Configurações de risco ────────────────────────────────────
MAX_RISCO_POR_TRADE = 0.02  # 2% do capital por operação
MAX_DRAWDOWN_DIARIO = 0.05  # 5% — trava o bot no dia
MAX_DRAWDOWN_TOTAL = 0.15  # 15% — desliga o bot até revisão manual
KELLY_FATOR = 0.25  # Kelly fracionado (25% do Kelly puro) — conservador
VOLATILIDADE_MAXIMA = 0.08  # 8% de variação em 1h = mercado extremo
MAX_POSICOES_ABERTAS = 1  # só 1 posição por vez
FUNDING_LIMITE = 0.10  # % — não operar se funding acima disso
VOL_TARGET_MULT_MIN = 0.5  # nunca reduz abaixo de 50% do fator Kelly
VOL_TARGET_MULT_MAX = 1.5  # nunca aumenta acima de 150% do fator Kelly

# P2-3: CVaR (Expected Shortfall) regime-dependente como gate de cauda —
# circuit breaker (check 4) e limite de drawdown (check 3), ambos existentes;
# cripto tem caudas gordas, entao um gate de cauda e complementar, nao
# redundante. Limites em % (pnl_pct), mesma unidade de backtesting.metricas.
# cvar_historico(). VOLATILIDADE=0.0 e defesa em profundidade (o check 4 ja
# bloqueia por volatilidade extrema antes deste gate ser alcançado).
CVAR_NIVEL_CONFIANCA = 0.95
CVAR_MIN_HISTORICO = 10  # mesmo piso de kelly_do_banco() -- historico curto = gate inerte
CVAR_LIMITE_POR_REGIME = {
    "TENDENCIA_ALTA": -6.0,
    "TENDENCIA_BAIXA": -4.0,
    "LATERAL": -3.0,
    "VOLATILIDADE": 0.0,
    "INDEFINIDO": -3.0,
}


# ── Estado do dia ─────────────────────────────────────────────

_estado_risco = {
    "capital_inicio_dia": None,
    "pnl_dia": 0.0,
    "data_dia": None,
    "bloqueado": False,
    "motivo_bloqueio": "",
    "posicoes_abertas": 0,
    # Auditoria 2026-07-22 (P2-5): debounce do circuit breaker de volatilidade
    # (gate 4 de validar_trade) -- sem isso, cada chamada durante um episodio
    # de volatilidade alta sustentada re-incrementaria a metrica e re-enviaria
    # o alerta Telegram. Nao e persistido deliberadamente (um restart no meio
    # do episodio so re-dispara 1 alerta a mais, sem risco).
    "circuit_breaker_ativo": False,
}
_estado_carregado = False
# main.py roda 1 thread por par (loop_par) e todas compartilham este mesmo
# modulo/_estado_risco -- sem lock, abrir/fechar posicoes em pares
# diferentes ao mesmo tempo pode perder um incremento/decremento (race).
_lock_posicoes = threading.Lock()


def incrementar_posicoes_abertas():
    with _lock_posicoes:
        _estado_risco["posicoes_abertas"] += 1


def decrementar_posicoes_abertas():
    with _lock_posicoes:
        _estado_risco["posicoes_abertas"] = max(0, _estado_risco["posicoes_abertas"] - 1)


def _carregar_estado_persistido():
    global _estado_carregado
    if _estado_carregado:
        return
    _estado_carregado = True
    try:
        salvo = database.carregar_risk_state()
        if salvo:
            _estado_risco.update(salvo)
    except Exception:
        pass


def persistir_estado():
    """Persist risk state so Railway restarts do not reset safeguards."""
    try:
        database.salvar_risk_state(_estado_risco)
    except Exception:
        pass


def _resetar_se_novo_dia():
    _carregar_estado_persistido()
    hoje = str(date.today())
    if _estado_risco["data_dia"] != hoje:
        _estado_risco["data_dia"] = hoje
        _estado_risco["pnl_dia"] = 0.0
        _estado_risco["bloqueado"] = False
        _estado_risco["motivo_bloqueio"] = ""
        # Não reseta capital — mantém histórico
        persistir_estado()


def registrar_resultado(pnl_usdt):
    """Registra resultado de um trade fechado."""
    _resetar_se_novo_dia()
    _estado_risco["pnl_dia"] += pnl_usdt
    persistir_estado()


# ── Kelly Criterion ───────────────────────────────────────────


def kelly(win_rate, ratio_rr=2.0):
    """
    Fórmula de Kelly: f = W - (1-W)/R
    win_rate: taxa de acerto (0.0 a 1.0)
    ratio_rr: razão risco/retorno (2.0 = 2:1)
    Retorna fração do capital a arriscar (já fracionada).
    """
    if win_rate <= 0 or ratio_rr <= 0:
        return MAX_RISCO_POR_TRADE
    kelly_puro = win_rate - (1 - win_rate) / ratio_rr
    kelly_puro = max(kelly_puro, 0)
    return round(kelly_puro * KELLY_FATOR, 4)


def kelly_do_banco():
    """Calcula Kelly com base no histórico de trades no banco.

    P1-3: win-rate real via pnl_usdt (sinais_executados() já filtra por
    trades FECHADOS, pnl_usdt IS NOT NULL). Antes desta correção, "ganhos"
    era contado por `tipo == "COMPRA"` — um proxy sem sentido: as linhas
    retornadas eram sempre eventos de FECHAMENTO (tipo FECHAR_LONG/STOP),
    nunca "COMPRA", então o win-rate calculado aqui já era sempre 0% antes
    da instrumentação de meta-labeling (P1-3) religar essas linhas às
    entradas — e ficaria 100% depois (linhas voltam a ser sempre
    COMPRA/VENDA de entrada), igualmente sem relação com o resultado real.
    """
    try:
        sinais_rows = database.sinais_executados(limit=1000)

        if len(sinais_rows) < 10:
            return MAX_RISCO_POR_TRADE  # sem histórico suficiente

        ganhos = sum(1 for s in sinais_rows if (s.get("pnl_usdt") or 0) > 0)
        wr = ganhos / len(sinais_rows)
        return kelly(wr, 2.0)
    except Exception:
        return MAX_RISCO_POR_TRADE


# ── CVaR regime-dependente (P2-3) ────────────────────────────────


def cvar_excede_limite(
    retornos_pct: list[float],
    regime_atual: str | None,
    nivel_confianca: float = CVAR_NIVEL_CONFIANCA,
) -> tuple[bool, float]:
    """Retorna (excedeu: bool, cvar: float). `excedeu` é True quando o CVaR
    (perda esperada da cauda, valor negativo) é PIOR que o limite do regime
    (cvar < limite). regime_atual None/desconhecido usa o limite mais
    conservador (mesmo de LATERAL/INDEFINIDO). Histórico curto (<
    CVAR_MIN_HISTORICO) -> (False, 0.0), gate inerte (mesmo piso de
    kelly_do_banco())."""
    if len(retornos_pct) < CVAR_MIN_HISTORICO:
        return False, 0.0
    limite = CVAR_LIMITE_POR_REGIME.get(regime_atual, CVAR_LIMITE_POR_REGIME["INDEFINIDO"])
    cvar = cvar_historico(retornos_pct, nivel_confianca=nivel_confianca)
    return cvar < limite, cvar


# ── Vol Targeting (P0-3) ────────────────────────────────────────


def fator_volatilidade(atr_relativo):
    """Multiplicador de vol targeting: size x (vol_alvo / vol_realizada).

    atr_relativo = atr_atual / atr_media (idioma já usado em estrategias/
    otimizada.py e ml_filtro.py). vol_alvo = atr_media (baseline recente),
    vol_realizada = atr_atual -> multiplicador = atr_media/atr_atual =
    1/atr_relativo.

    None ou <=0 (sem dado, ou dado corrompido/negativo) => neutro (1.0).
    Vol atual ACIMA da média (atr_relativo > 1) encolhe o tamanho;
    vol ABAIXO da média (atr_relativo < 1) permite crescer até o teto.
    """
    if atr_relativo is None or atr_relativo <= 0:
        return 1.0
    mult = 1.0 / atr_relativo
    return round(min(max(mult, VOL_TARGET_MULT_MIN), VOL_TARGET_MULT_MAX), 4)


# ── Risco de Portfolio (P1-2) ──────────────────────────────────

TETO_EXPOSICAO_AGREGADA_PCT = 0.40  # 40% do capital. Maior que o teto de
# posicao unica (20% base, ate 30% com vol targeting no melhor caso:
# capital*0.20*mult_vol, mult_vol max 1.5x) para garantir que 1 posicao
# solitaria NUNCA seja bloqueada por este teto (invariante de inercia) --
# 0.40 da 10pp de margem acima do pior caso de posicao unica (30%). Valor
# inicial nao-backtestado; revisar quando/se MAX_POSICOES_ABERTAS>1.
CORRELACAO_DEFAULT_AUSENTE = 1.0  # par sem dado -> assume correlacao maxima
# (conservador: subestimar risco de correlacao e pior que superestimar).

_cache_correlacao = {"matriz": {}, "timestamp": 0.0}  # timestamp=0.0 (nao
# None) para a checagem de TTL nunca comparar None a float na 1a chamada.
_lock_correlacao = threading.Lock()  # lock DEDICADO, distinto de _lock_posicoes.


def correlacao_pearson(retornos_a: list[float], retornos_b: list[float]) -> float:
    """Coeficiente de Pearson entre duas series de retornos (mesmo tamanho).

    Guardas: tamanhos diferentes -> ValueError (erro claro, nao silencioso);
    <2 pontos -> 0.0 (neutro, sem dado suficiente); std==0 em qualquer serie
    (serie constante) -> 0.0 (correlacao indefinida com variancia zero; 0.0
    e mais seguro que crashar ou assumir correlacao maxima aqui). Resultado
    clampado em [-1.0, 1.0] (protecao contra erro de ponto flutuante).
    """
    if len(retornos_a) != len(retornos_b):
        raise ValueError("retornos_a e retornos_b precisam ter o mesmo tamanho")
    if len(retornos_a) < 2:
        return 0.0
    std_a = statistics.stdev(retornos_a)
    std_b = statistics.stdev(retornos_b)
    if std_a == 0 or std_b == 0:
        return 0.0
    media_a = statistics.mean(retornos_a)
    media_b = statistics.mean(retornos_b)
    cov = sum((a - media_a) * (b - media_b) for a, b in zip(retornos_a, retornos_b)) / (
        len(retornos_a) - 1
    )
    corr = cov / (std_a * std_b)
    return max(-1.0, min(1.0, corr))


def _precos_para_retornos(precos: list[float]) -> list[float]:
    """Retornos percentuais simples r_i=(p_i-p_{i-1})/p_{i-1}. Pula pontos
    onde p_{i-1}<=0 (dado corrompido da API) em vez de propagar ZeroDivisionError
    -- mesma filosofia de robustez silenciosa ja usada no resto do arquivo."""
    retornos = []
    for i in range(1, len(precos)):
        anterior = precos[i - 1]
        if anterior <= 0:
            continue
        retornos.append((precos[i] - anterior) / anterior)
    return retornos


def matriz_correlacao(precos_por_symbol: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """dict aninhado symbol->symbol->float (simetrico, ambas as direcoes
    populadas: matriz[a][b] == matriz[b][a]). Diagonal (symbol,symbol)=1.0
    populada explicitamente. Series de tamanhos diferentes entre symbols
    sao truncadas para o comprimento comum MINIMO, alinhando pelo FIM das
    listas (os N precos mais recentes de cada symbol) -- alinhar pelo inicio
    misturaria periodos de tempo diferentes entre symbols."""
    if not precos_por_symbol:
        return {}
    symbols = list(precos_por_symbol.keys())
    tamanho_comum = min(len(precos_por_symbol[s]) for s in symbols)
    retornos_por_symbol = {
        s: _precos_para_retornos(precos_por_symbol[s][-tamanho_comum:]) for s in symbols
    }
    # _precos_para_retornos pode pular pontos com preco anterior<=0 (dado
    # corrompido da API), o que produz listas de RETORNOS de tamanhos
    # diferentes mesmo quando as listas de PRECOS truncadas acima ja tem o
    # mesmo tamanho -- truncar de novo aqui, agora sobre os retornos, para
    # correlacao_pearson nunca receber series de tamanhos diferentes.
    tamanho_comum_retornos = min((len(v) for v in retornos_por_symbol.values()), default=0)
    retornos_por_symbol = {
        s: v[-tamanho_comum_retornos:] if tamanho_comum_retornos else []
        for s, v in retornos_por_symbol.items()
    }
    matriz: dict[str, dict[str, float]] = {s: {} for s in symbols}
    for i, a in enumerate(symbols):
        matriz[a][a] = 1.0
        for b in symbols[i + 1 :]:
            c = correlacao_pearson(retornos_por_symbol[a], retornos_por_symbol[b])
            matriz[a][b] = c
            matriz[b][a] = c
    return matriz


def _obter_precos_klines(symbol: str, intervalo: str = "1h", limit: int = 100) -> list[float]:
    """P1-5: delega para data.klines (cache TTL + REST_BASE_URL). Mesmo
    contrato de antes: falha de rede -> [] (lista vazia, nao propaga
    excecao)."""
    dados = obter_klines(symbol, intervalo, limit)
    return dados["fechamento"] if dados is not None else []


def obter_matriz_correlacao_cache(ttl_segundos: int = 900) -> dict[str, dict[str, float]]:
    """Cache module-level com lock dedicado (_lock_correlacao). TODA a
    logica (checagem de TTL + fetch se necessario + gravacao) roda dentro
    de UM UNICO 'with' -- lock grosso e simples, serializa fetches
    concorrentes em vez de arriscar fetches redundantes ou corrupcao de
    cache por double-checked locking malfeito."""
    with _lock_correlacao:
        agora = time.time()
        if agora - _cache_correlacao["timestamp"] < ttl_segundos and _cache_correlacao["matriz"]:
            return _cache_correlacao["matriz"]
        precos_por_symbol = {}
        for symbol in PARAMS_PARES.keys():
            precos = _obter_precos_klines(symbol)
            if precos:
                precos_por_symbol[symbol] = precos
        nova_matriz = matriz_correlacao(precos_por_symbol) if precos_por_symbol else {}
        if nova_matriz:
            _cache_correlacao["matriz"] = nova_matriz
            _cache_correlacao["timestamp"] = agora
        # se nova_matriz veio vazia (falha total de rede) e ja existe uma
        # matriz anterior valida, MANTEM a matriz antiga (nao sobrescreve
        # com {} por causa de uma falha transitoria) e nao atualiza o
        # timestamp (proxima chamada tenta de novo, nao espera o TTL).
        return _cache_correlacao["matriz"]


def _corr_lookup(a: str, b: str, matriz_corr: dict[str, dict[str, float]]) -> float:
    """Codigo de lookup EXATO a ser usado por exposicao_agregada_efetiva.
    a==b sempre retorna 1.0 (auto-correlacao hardcoded, defesa em profundidade
    independente do conteudo da matriz). Par ausente da matriz (symbol
    desconhecido ou fetch falhou para aquele symbol) usa
    CORRELACAO_DEFAULT_AUSENTE=1.0 (conservador)."""
    if a == b:
        return 1.0
    return matriz_corr.get(a, {}).get(b, CORRELACAO_DEFAULT_AUSENTE)


def exposicao_agregada_efetiva(
    posicoes_abertas: list[dict],
    novo_symbol: str,
    novo_notional_usdt: float,
    matriz_corr: dict[str, dict[str, float]],
) -> float:
    """posicoes_abertas = [{"symbol": str, "notional_usdt": float}, ...]
    das posicoes JA abertas (SEM a candidata). Formula (soma dupla completa
    sobre TODOS os pares i,j, incluindo diagonal e ambas as ordens (i,j) e
    (j,i) -- implementado como double loop literal para evitar erro de
    fator 2 de otimizacoes manuais):

        exposicao_efetiva = sqrt( sum_i sum_j notional_i * notional_j * corr(i,j) )

    onde a lista de posicoes = posicoes_abertas + [candidata].

    Prova algebrica dos casos de teste (2 posicoes, notionals n1, n2):
      corr=1.0 -> soma = n1^2 + n2^2 + 2*n1*n2 = (n1+n2)^2 -> sqrt = n1+n2
                  (aditividade total, sem beneficio de diversificacao)
      corr=0.0 -> soma = n1^2 + n2^2 (termos cruzados somem) -> sqrt =
                  sqrt(n1^2+n2^2) (Pitagoras, beneficio maximo)
      1 posicao so (candidata, posicoes_abertas=[]) -> soma = n^2*1.0 ->
                  sqrt = n (INVARIANTE DE INERCIA: exposicao == proprio
                  notional exatamente)
    """
    todas = list(posicoes_abertas) + [{"symbol": novo_symbol, "notional_usdt": novo_notional_usdt}]
    soma = 0.0
    for i in todas:
        for j in todas:
            corr = _corr_lookup(i["symbol"], j["symbol"], matriz_corr)
            soma += i["notional_usdt"] * j["notional_usdt"] * corr
    return soma**0.5


# ── Tamanho da posição ────────────────────────────────────────


def calcular_tamanho(
    capital_usdt, preco_entrada, stop_loss, fator_risco=None, *, atr_relativo=None
):
    """
    Calcula tamanho da posição baseado no risco máximo em USDT.

    capital_usdt:   saldo disponível
    preco_entrada:  preço de entrada
    stop_loss:      preço do stop
    fator_risco:    fração do capital a arriscar (usa Kelly se None)
    atr_relativo:   atr_atual/atr_media (P0-3, vol targeting). Só tem efeito
                     quando fator_risco não é passado explicitamente — um
                     fator_risco explícito é um bypass intencional do cálculo
                     padrão (inclusive do teto), e o vol targeting não deve
                     sobrescrever essa escolha do chamador.
    """
    mult_vol = 1.0
    if fator_risco is None:
        fator_risco = kelly_do_banco()
        fator_risco = min(fator_risco, MAX_RISCO_POR_TRADE)
        mult_vol = fator_volatilidade(atr_relativo)
        fator_risco = fator_risco * mult_vol
        # RE-cap: o multiplicador pode chegar a 1.5x o fator já capado pelo
        # Kelly; sem este segundo min(), o invariante "nunca arrisca mais
        # que MAX_RISCO_POR_TRADE" quebraria sempre que o Kelly já estivesse
        # no teto e a vol estivesse abaixo da média.
        fator_risco = min(fator_risco, MAX_RISCO_POR_TRADE)

    risco_usdt = capital_usdt * fator_risco
    distancia_stop = abs(preco_entrada - stop_loss)

    if distancia_stop <= 0:
        return 0.0

    # Tamanho em BTC = risco em USDT / distância do stop em USDT por BTC
    tamanho_btc = risco_usdt / distancia_stop
    tamanho_usdt = tamanho_btc * preco_entrada

    # Teto de exposição por posição: 20% do capital na vol de referência,
    # também modulado por mult_vol (P0-3) — sem isso, com o stop de 1.5%
    # usado por validar_trade, este teto domina o sizing em praticamente
    # todo cenário real (risco_usdt/distancia_stop já excede 20% do capital
    # para qualquer fator_risco >= ~0.3%, bem abaixo do range normal do
    # Kelly) e o multiplicador de vol targeting nunca chega a se refletir
    # no tamanho final. Escalar o teto junto faz o teto respirar entre 10%
    # e 30% do capital conforme a volatilidade, preservando o propósito da
    # feature mesmo quando ela é o fator dominante. mult_vol fica 1.0
    # (teto fixo em 20%) no caminho de fator_risco explícito, mesma
    # semântica de bypass do cálculo padrão usada acima.
    tamanho_usdt = min(tamanho_usdt, capital_usdt * 0.20 * mult_vol)
    tamanho_btc = tamanho_usdt / preco_entrada

    return round(tamanho_btc, 6)


# ── Circuit Breaker ────────────────────────────────────────────


def verificar_volatilidade(symbol="BTCUSDT"):
    """Retorna variação % da última hora. Se > 8%, mercado extremo.

    P1-5: delega para data.klines (cache TTL) -- antes hardcoded para
    https://api.binance.com, ignorando BASE_URL/REST_BASE_URL (bug real
    corrigido de brinde; _obter_precos_klines, logo acima, já usava o
    config corretamente). Preserva EXATAMENTE a semântica original:
    abertura da penúltima vela (k[-2]) até o fechamento da última (k[-1]),
    não só a variação intra-vela da última candle sozinha."""
    try:
        dados = obter_klines(symbol, "1h", 2)
        if dados is None or len(dados["fechamento"]) < 2:
            return 0.0
        abertura = dados["abertura"][-2]
        fechamento = dados["fechamento"][-1]
        return abs(fechamento - abertura) / abertura
    except Exception:
        return 0.0


# ── Saldo real da conta ────────────────────────────────────────


def get_saldo_usdt():
    """Retorna saldo disponível em USDT na conta Spot."""
    try:
        # P0-4: recvWindow evita rejeicao -1021 por clock drift
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        r = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=8,
        )
        balances = r.json().get("balances", [])
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["free"])
    except Exception:
        pass
    return 0.0


def get_saldo_btc():
    """Retorna saldo disponível em BTC na conta Spot."""
    try:
        # P0-4: recvWindow evita rejeicao -1021 por clock drift
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        r = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=params,
            headers={"X-MBX-APIKEY": API_KEY},
            timeout=8,
        )
        balances = r.json().get("balances", [])
        for b in balances:
            if b["asset"] == "BTC":
                return float(b["free"])
    except Exception:
        pass
    return 0.0


# ── Validador completo ────────────────────────────────────────


def validar_trade(
    sinal,
    preco,
    capital_usdt,
    *,
    atr_relativo=None,
    posicoes_abertas_detalhe: list[dict] | None = None,
    symbol: str | None = None,
    regime: str | None = None,
):
    """
    Valida todas as condições de risco antes de executar um trade.
    Retorna: {"pode": bool, "motivo": str, "tamanho_btc": float}

    regime: regime de mercado atual (TENDENCIA_ALTA/BAIXA, LATERAL,
    VOLATILIDADE, INDEFINIDO — ver regime.py). None (default, inerte) pula o
    gate de CVaR (P2-3) inteiro, mesmo padrão de posicoes_abertas_detalhe.

    posicoes_abertas_detalhe: [{"symbol": str, "notional_usdt": float}, ...]
    das posicoes JA abertas (SEM a candidata), usado no check 6.5 de
    exposicao agregada de portfolio (P1-2). None/vazio (hoje sempre, pois
    nenhum chamador real ainda popula isso) pula o check inteiro sem
    nenhuma chamada de rede.
    symbol: par da candidata. Simplificacao deliberada: validar_trade() nao
    recebe hoje um parametro de symbol/par (so sinal/preco/capital), e
    adiciona-lo como obrigatorio quebraria os call sites existentes
    (main.py, executor.py) -- fora de escopo. Quando None (sempre, hoje),
    usa o placeholder "__CANDIDATA__" como chave da posicao candidata no
    calculo de exposicao agregada; como o lookup de correlacao trata
    a==b como 1.0 hardcoded e o placeholder nunca colide com um symbol
    real de posicoes_abertas_detalhe (so populado quando alguem no futuro
    passar dados reais), isso nao quebra a matematica nem o invariante de
    inercia. Resolver de verdade (symbol real) fica para quando
    posicoes_abertas_detalhe for populado de verdade em producao.
    """
    _resetar_se_novo_dia()

    # 1. Bot bloqueado?
    if _estado_risco["bloqueado"]:
        return {
            "pode": False,
            "motivo": f"Bot bloqueado: {_estado_risco['motivo_bloqueio']}",
            "tamanho_btc": 0,
        }

    # 2. Saldo suficiente?
    if capital_usdt < 10:
        return {"pode": False, "motivo": "Saldo insuficiente (< $10)", "tamanho_btc": 0}

    # 3. Drawdown diário excedido?
    if _estado_risco["capital_inicio_dia"]:
        dd_dia = _estado_risco["pnl_dia"] / _estado_risco["capital_inicio_dia"]
        if dd_dia <= -MAX_DRAWDOWN_DIARIO:
            _estado_risco["bloqueado"] = True
            _estado_risco["motivo_bloqueio"] = f"Max drawdown diario atingido ({dd_dia*100:.1f}%)"
            persistir_estado()
            # Sem debounce extra: o proprio bloqueado=True faz o gate 1 (acima)
            # retornar cedo em qualquer chamada seguinte -- isto so executa 1x.
            health.increment_metric("drawdown_bloqueios")
            try:
                database.salvar_bot_event(
                    "drawdown_bloqueio",
                    _estado_risco["motivo_bloqueio"],
                    service="risco",
                    severity="CRITICAL",
                )
            except Exception:
                pass
            try:
                telegram_bot.alerta_circuit_breaker(_estado_risco["motivo_bloqueio"])
            except Exception:
                pass
            return {"pode": False, "motivo": _estado_risco["motivo_bloqueio"], "tamanho_btc": 0}

    # 4. Volatilidade extrema?
    vol = verificar_volatilidade()
    if vol > VOLATILIDADE_MAXIMA:
        motivo = f"Volatilidade extrema: {vol*100:.1f}%"
        if not _estado_risco["circuit_breaker_ativo"]:
            # Debounce: so conta/alerta na TRANSICAO para o estado ativado,
            # nao em toda chamada enquanto a volatilidade seguir alta.
            _estado_risco["circuit_breaker_ativo"] = True
            health.increment_metric("circuit_breaker_ativacoes")
            try:
                database.salvar_bot_event(
                    "circuit_breaker_volatilidade", motivo, service="risco", severity="WARNING"
                )
            except Exception:
                pass
            try:
                telegram_bot.alerta_circuit_breaker(motivo)
            except Exception:
                pass
        return {"pode": False, "motivo": motivo, "tamanho_btc": 0}
    _estado_risco["circuit_breaker_ativo"] = False

    # 5. Posições abertas?
    if _estado_risco["posicoes_abertas"] >= MAX_POSICOES_ABERTAS:
        return {
            "pode": False,
            "motivo": "Posicao ja aberta — aguardar fechamento",
            "tamanho_btc": 0,
        }

    # 6. Calcular tamanho
    stop = preco * (1 - 0.015) if sinal == "COMPRA" else preco * (1 + 0.015)
    tamanho = calcular_tamanho(capital_usdt, preco, stop, atr_relativo=atr_relativo)

    if tamanho <= 0:
        return {"pode": False, "motivo": "Tamanho calculado zerado", "tamanho_btc": 0}

    novo_notional_usdt = tamanho * preco
    matriz_corr = {}
    if posicoes_abertas_detalhe:
        # SO busca a matriz de correlacao (com chance de chamada de rede,
        # via cache) quando ha de fato outras posicoes abertas -- nunca
        # gasta rede quando o parametro nao e passado ou vem vazio/None.
        matriz_corr = obter_matriz_correlacao_cache()
    exposicao_efetiva = exposicao_agregada_efetiva(
        posicoes_abertas_detalhe or [],
        symbol or "__CANDIDATA__",
        novo_notional_usdt,
        matriz_corr,
    )
    # 6.5 Exposicao agregada excede o teto de portfolio?
    if posicoes_abertas_detalhe and exposicao_efetiva > capital_usdt * TETO_EXPOSICAO_AGREGADA_PCT:
        return {
            "pode": False,
            "motivo": f"Exposicao agregada de portfolio excedida ({exposicao_efetiva:.2f} USDT)",
            "tamanho_btc": 0,
            "exposicao_agregada_efetiva": round(exposicao_efetiva, 2),
        }

    # 6.6 CVaR de cauda (regime-dependente) excede o limite? (P2-3, inerte por
    # default -- so roda quando `regime` e passado pelo chamador)
    if regime is not None:
        try:
            retornos_pct = [
                s["pnl_pct"]
                for s in database.sinais_executados(limit=1000)
                if s.get("pnl_pct") is not None
            ]
        except Exception:
            retornos_pct = []
        cvar_excedeu, cvar = cvar_excede_limite(retornos_pct, regime)
        if cvar_excedeu:
            return {
                "pode": False,
                "motivo": f"CVaR de cauda excedido p/ regime {regime} ({cvar:.2f}%)",
                "tamanho_btc": 0,
                "cvar_pct": round(cvar, 4),
            }

    # Inicializar capital do dia se necessário
    if _estado_risco["capital_inicio_dia"] is None:
        _estado_risco["capital_inicio_dia"] = capital_usdt
        persistir_estado()

    return {
        "pode": True,
        "motivo": "Todos os criterios de risco aprovados",
        "tamanho_btc": tamanho,
        "risco_usdt": round(tamanho * abs(preco - stop), 2),
        "fator_kelly": kelly_do_banco(),
        "fator_volatilidade": fator_volatilidade(atr_relativo),
        "exposicao_agregada_efetiva": round(exposicao_efetiva, 2),
    }


def status_leve() -> dict:
    """Subconjunto de status() SEM nenhuma chamada de rede (sem saldo/
    volatilidade) -- usado pela observabilidade (health.set_gauge), chamada a
    cada ciclo de estrategia sem gastar rate-limit da Binance."""
    _resetar_se_novo_dia()
    dd_dia = 0.0
    if _estado_risco["capital_inicio_dia"]:
        dd_dia = _estado_risco["pnl_dia"] / _estado_risco["capital_inicio_dia"] * 100
    return {
        "pnl_dia": round(_estado_risco["pnl_dia"], 2),
        "drawdown_dia_%": round(dd_dia, 2),
        "bloqueado": _estado_risco["bloqueado"],
    }


def status():
    """Retorna estado atual do módulo de risco."""
    leve = status_leve()
    saldo_usdt = get_saldo_usdt()
    saldo_btc = get_saldo_btc()
    vol = verificar_volatilidade()

    return {
        "saldo_usdt": round(saldo_usdt, 4),
        "saldo_btc": round(saldo_btc, 8),
        "pnl_dia": leve["pnl_dia"],
        "drawdown_dia_%": leve["drawdown_dia_%"],
        "volatilidade_%": round(vol * 100, 2),
        "bloqueado": leve["bloqueado"],
        "motivo_bloqueio": _estado_risco["motivo_bloqueio"],
        "posicoes_abertas": _estado_risco["posicoes_abertas"],
        "kelly_%": round(kelly_do_banco() * 100, 2),
        "max_dd_diario_%": MAX_DRAWDOWN_DIARIO * 100,
        "max_dd_total_%": MAX_DRAWDOWN_TOTAL * 100,
    }


if __name__ == "__main__":
    print("\n=== STATUS DE RISCO ===")
    s = status()
    for k, v in s.items():
        print(f"  {k:25s}: {v}")

    print("\n=== SIMULACAO DE TAMANHO ===")
    preco = 68000
    capital = get_saldo_usdt()
    if capital < 1:
        capital = 100  # simulação
        print("  (Usando capital simulado de $100)")
    stop = preco * 0.985
    tam = calcular_tamanho(capital, preco, stop)
    print(f"  Capital:   ${capital:.2f}")
    print(f"  Preco:     ${preco:,}")
    print(f"  Stop:      ${stop:,.2f}")
    print(f"  Tamanho:   {tam:.6f} BTC (${tam*preco:.2f})")
    print(f"  Risco max: ${tam * (preco - stop):.2f} ({MAX_RISCO_POR_TRADE*100:.0f}% do capital)")
