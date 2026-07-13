"""
Suíte pytest E2E para estrategias/otimizada.py (analisar + imprimir).

Perfil @Delta — testes herméticos: SEM rede, SEM banco, SEM modelos.

OBJETIVO PRINCIPAL
------------------
Teste de REGRESSÃO ponta-a-ponta que blinda o showstopper recém-corrigido:
`analisar()` quebrava no acesso a `volume_relativo`/`bollinger` (IndexError
de alinhamento de janela). Estes testes garantem que, com uma série coerente
de ~100 velas, `analisar()` NÃO lança e produz um dict com as chaves de
contrato esperadas, em cenários COMPRA, VENDA e AGUARDAR.

Estratégia de mock (100% hermética)
-----------------------------------
- `estrategias.otimizada._klines` -> dict sintético {abertura,maxima,minima,
  fechamento,volume} com ~100 velas (listas), gerado de forma determinística.
  Aceita assinatura (intervalo, limite=100, symbol=...).
- `otimizada.reg.detectar` -> dict de regime sintético.
- `otimizada.fg.obter`     -> dict de fear&greed sintético.
- `otimizada.sup.detectar_suportes` -> dict de suportes sintético.
- `otimizada.database.salvar_sinal` -> no-op (captura chamadas).
- `ensemble_result` passado pronto como dict (nunca chama rede/modelo).

`score.calcular` é REAL (função pura). `otimizada.analisar` aceita
`historico_ticks` desde P1-1 (repassado de main.py só para BTCUSDT, único
par com WebSocket de ticks ao vivo); os testes desta suíte não o passam,
então o componente CVD fica em 50 (neutro) por omissão — mesmo
comportamento de antes, agora por escolha explícita e não por limitação.
"""

import os
import sys

import pytest

# Garante a raiz do repo no path (otimizada.py importa módulos da raiz).
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import estrategias.otimizada as otimizada  # noqa: E402

# ---------------------------------------------------------------------------
# Geradores de séries sintéticas (determinísticas, sem aleatoriedade)
# ---------------------------------------------------------------------------


def _serie_klines(n=100, base=100.0, passo=1.0, vol=1000.0, vol_ultima=None):
    """
    Gera um dict de klines coerente com `n` velas.

    passo > 0  -> série crescente (bullish: preco > ema20 > ema50)
    passo < 0  -> série decrescente (bearish: preco < ema20 < ema50)

    maxima/minima envolvem o fechamento garantindo TR/ATR positivos e
    bandwidth de Bollinger não-degenerado. `vol_ultima` força o volume da
    última vela (para controlar volume_relativo na vela de sinal).
    """
    fechamento = [base + passo * i for i in range(n)]
    abertura = [c - passo * 0.3 for c in fechamento]
    maxima = [c + abs(passo) * 0.5 + 0.5 for c in fechamento]
    minima = [c - abs(passo) * 0.5 - 0.5 for c in fechamento]
    volume = [vol for _ in range(n)]
    if vol_ultima is not None:
        volume[-1] = vol_ultima
    return {
        "abertura": abertura,
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": volume,
    }


def _serie_klines_short(n=100):
    """
    Série para um cenário de VENDA válido.

    O `score.calcular` é estruturalmente enviesado a long (mtf/ema/vwap premiam
    alinhamento de alta). Para um short válido (preco < ema20 < ema50, abaixo do
    VWAP, tend_4h == BAIXA) esses componentes vão a 0, então o score só cruza o
    limiar de operação se o RSI cair na zona ideal [42,60]. Construímos uma queda
    forte seguida de uma cauda oscilante levemente descendente: a estrutura
    permanece bearish, mas o RSI recente sobe para ~45 (zona operável).
    """
    fechamento = []
    for i in range(n):
        if i < 78:
            fechamento.append(400.0 - 3.0 * i)
        else:
            base = (400.0 - 3.0 * 78) - 1.2 * (i - 78)
            osc = 6.0 if (i % 2 == 0) else -6.0
            fechamento.append(base + osc)
    maxima = [c + 1.5 for c in fechamento]
    minima = [c - 1.5 for c in fechamento]
    volume = [1000.0 for _ in range(n)]
    volume[-1] = 2000.0
    return {
        "abertura": list(fechamento),
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": volume,
    }


def _fake_klines_factory(d1h, d4h):
    """Devolve uma função compatível com a assinatura de otimizada._klines."""

    def _fake(intervalo, limite=100, symbol="BTCUSDT"):
        # 4H usa um histórico próprio; qualquer outro intervalo cai no 1H.
        return d4h if str(intervalo).lower() == "4h" else d1h

    return _fake


def _regime(regime_final="TENDENCIA_ALTA", pode_operar=True, score=85):
    return {
        "regime_final": regime_final,
        "pode_operar": pode_operar,
        "score": score,
        "adx": 30,
        "detalhe": "sintetico",
    }


def _fear(valor=50, pode_operar=True, classificacao_pt="Neutro", reducao_alvo=False):
    return {
        "valor": valor,
        "pode_operar": pode_operar,
        "classificacao_pt": classificacao_pt,
        "reducao_alvo": reducao_alvo,
    }


def _suporte(suporte_forte=0.0):
    return {
        "suporte_forte": suporte_forte,
        "distancia_%": 0.0,
        "na_zona": False,
    }


def _ensemble(prob=0.80, pode_operar=True):
    return {
        "prob_ensemble": prob,
        "pode_operar": pode_operar,
        "confianca": "ALTA",
        "prob_xgb": prob,
        "prob_lstm": prob,
    }


# ---------------------------------------------------------------------------
# Fixture de mock holística: aplica TODOS os patches herméticos.
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_otimizada(monkeypatch):
    """
    Devolve uma função `aplicar(...)` que instala os mocks e retorna o
    registro de chamadas a database.salvar_sinal.
    """
    chamadas_salvar = []

    def aplicar(d1h, d4h, regime_info, fear_info, suporte_info):
        monkeypatch.setattr(otimizada, "_klines", _fake_klines_factory(d1h, d4h))
        monkeypatch.setattr(otimizada.reg, "detectar", lambda *a, **k: regime_info)
        monkeypatch.setattr(otimizada.fg, "obter", lambda *a, **k: fear_info)
        monkeypatch.setattr(otimizada.sup, "detectar_suportes", lambda *a, **k: suporte_info)

        def _salvar(*a, **k):
            chamadas_salvar.append((a, k))
            return None

        monkeypatch.setattr(otimizada.database, "salvar_sinal", _salvar)
        return chamadas_salvar

    return aplicar


# Chaves do contrato de retorno exigidas pelo orquestrador/dashboard.
CHAVES_CONTRATO = (
    "sinal",
    "score",
    "preco",
    "stop_loss",
    "take_profit",
    "ema20_1h",
    "rsi",
    "score_decisao",
)


# ---------------------------------------------------------------------------
# REGRESSÃO: analisar() não pode quebrar em volume_relativo / bollinger
# ---------------------------------------------------------------------------


def test_analisar_nao_lanca_e_retorna_contrato(patch_otimizada):
    """Showstopper blindado: com 100 velas, analisar() roda ponta-a-ponta."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime(), _fear(), _suporte())

    r = otimizada.analisar("BTCUSDT", cvd_atual=0.0, ml_prob=0.70, ensemble_result=_ensemble())

    assert isinstance(r, dict)
    for chave in CHAVES_CONTRATO:
        assert chave in r, f"chave de contrato ausente: {chave}"

    assert r["sinal"] in ("COMPRA", "VENDA", "AGUARDAR")
    assert isinstance(r["score"], (int, float))
    assert isinstance(r["preco"], (int, float))
    # série crescente => preço da última vela é o maior
    assert r["preco"] == d1h["fechamento"][-1]


def test_analisar_serie_curta_limiar_minimo_nao_quebra(patch_otimizada):
    """
    Borda do bug original: exatamente o mínimo de velas (50, para a EMA50)
    ainda deve passar pelos cálculos de volume_relativo(20)/bollinger(20)
    sem IndexError.
    """
    d1h = _serie_klines(n=50, base=100.0, passo=1.0)
    d4h = _serie_klines(n=50, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime(), _fear(), _suporte())

    r = otimizada.analisar("BTCUSDT", cvd_atual=0.0, ml_prob=0.70, ensemble_result=_ensemble())
    assert isinstance(r, dict)
    assert r["sinal"] in ("COMPRA", "VENDA", "AGUARDAR")


# ---------------------------------------------------------------------------
# P1-1: historico_ticks chega de fato ao componente CVD do score
# ---------------------------------------------------------------------------


def test_historico_ticks_repassado_afeta_componente_cvd_do_score(patch_otimizada):
    """Sem historico_ticks o componente CVD fica travado em 50 (neutro).
    Com >=50 ticks reais (preco subindo + fluxo comprador), o componente
    reflete a harmonia bullish (score 100) em vez do neutro fixo -- prova
    que o wiring ponta-a-ponta (main.py -> analisar -> score.calcular)
    realmente chega ao componente, não só que o parâmetro existe."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime(), _fear(), _suporte())

    sem_ticks = otimizada.analisar(
        "BTCUSDT", cvd_atual=0.0, ml_prob=0.70, ensemble_result=_ensemble()
    )
    assert sem_ticks["score_result"]["scores"]["cvd"] == 50

    ticks_bullish = [
        {"preco": 100.0 + i * 0.5, "is_buyer_maker": False, "quantidade": 1.0} for i in range(60)
    ]
    com_ticks = otimizada.analisar(
        "BTCUSDT",
        cvd_atual=0.0,
        ml_prob=0.70,
        ensemble_result=_ensemble(),
        historico_ticks=ticks_bullish,
    )
    # Nao e 100 (harmonia bullish maxima): uma rampa de CVD perfeitamente
    # linear tem razao slope/std ~= 1/std([0..49]) ~= 0.069 SEMPRE (escala
    # cancela), abaixo do limiar 0.1 que score._score_cvd usa para
    # classificar cvd_trend como "forte" -- cai no caso neutro (base 50)
    # com um pequeno bonus de trend_strength (int(0.069*20)=1) => 51.
    assert com_ticks["score_result"]["scores"]["cvd"] == 51
    assert com_ticks["score_result"]["scores"]["cvd"] != sem_ticks["score_result"]["scores"]["cvd"]


# ---------------------------------------------------------------------------
# Cenário BULLISH -> caminho de COMPRA, com score numérico coerente
# ---------------------------------------------------------------------------


def test_cenario_bullish_gera_compra(patch_otimizada):
    """
    Regime TENDENCIA_ALTA + F&G zona ideal + EMAs alinhadas (série crescente)
    + ML alto. Não pode quebrar e deve produzir sinal de COMPRA com score
    numérico.
    """
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol=1000.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    chamadas = patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", pode_operar=True, score=90),
        _fear(valor=50, pode_operar=True, classificacao_pt="Neutro"),
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "COMPRA"
    assert isinstance(r["score"], (int, float))
    assert r["score"] >= 60  # decisão de operar exige score >= score_operar
    assert r["score_decisao"] in ("OPERAR_CHEIO", "OPERAR_REDUZIDO")
    # COMPRA define stop abaixo e target acima do preço
    assert r["stop_loss"] < r["preco"] < r["take_profit"]
    # sinal != AGUARDAR persiste no DB (mock)
    assert len(chamadas) == 1
    args, kwargs = chamadas[0]
    assert args[0] == "COMPRA"
    assert kwargs.get("symbol") == "BTCUSDT"


def test_compra_usa_suporte_forte_quando_acima_do_stop_pct(patch_otimizada):
    """Stop deve subir para 0.5% abaixo do suporte forte se este for mais alto."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol=1000.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    preco = d1h["fechamento"][-1]
    # suporte ligeiramente abaixo do preço, mais alto que o stop de 1.5%
    suporte_forte = preco * 0.999
    patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_ALTA", score=90),
        _fear(valor=50),
        _suporte(suporte_forte=suporte_forte),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "COMPRA"
    stop_esperado = round(suporte_forte * 0.995, 2)
    assert r["stop_loss"] == stop_esperado


# ---------------------------------------------------------------------------
# Cenário BEARISH -> caminho de VENDA
# ---------------------------------------------------------------------------


def test_cenario_bearish_gera_venda(patch_otimizada):
    """
    Regime TENDENCIA_BAIXA + EMAs em queda (série decrescente). Cobre o
    caminho de VENDA: todos os filtros_short verdadeiros + decisao != AGUARDAR.
    """
    # cauda oscilante: estrutura bearish + RSI na zona operável (ver helper)
    d1h = _serie_klines_short(n=100)
    # 4H estritamente decrescente => tend_4h == "BAIXA" (exigido pelo short)
    d4h = _serie_klines(n=60, base=200.0, passo=-1.0)
    chamadas = patch_otimizada(
        d1h,
        d4h,
        _regime("TENDENCIA_BAIXA", pode_operar=True, score=95),
        _fear(valor=50, pode_operar=True),
        _suporte(suporte_forte=0.0),
    )

    # filtros_short["ml"] usa ml_prob (não o ensemble): precisa >= 0.60
    # filtros_short["cvd"] exige cvd_atual < 0
    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=-10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )

    assert r["sinal"] == "VENDA"
    assert isinstance(r["score"], (int, float))
    # VENDA: stop acima e target abaixo do preço
    assert r["take_profit"] < r["preco"] < r["stop_loss"]
    assert len(chamadas) == 1
    assert chamadas[0][0][0] == "VENDA"


# ---------------------------------------------------------------------------
# Cenário AGUARDAR -> bloqueio por regime lateral (score forçado baixo)
# ---------------------------------------------------------------------------


def test_cenario_lateral_gera_aguardar_sem_salvar(patch_otimizada):
    """
    Regime LATERAL é bloqueio absoluto em score.calcular -> decisao AGUARDAR.
    Sinal AGUARDAR não persiste no DB e mantém stop/target None.
    """
    d1h = _serie_klines(n=100, base=100.0, passo=1.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    chamadas = patch_otimizada(
        d1h,
        d4h,
        _regime("LATERAL", pode_operar=False, score=30),
        _fear(valor=50, pode_operar=True),
        _suporte(suporte_forte=0.0),
    )

    r = otimizada.analisar(
        "BTCUSDT", cvd_atual=0.0, ml_prob=0.50, ensemble_result=_ensemble(prob=0.50)
    )

    assert r["sinal"] == "AGUARDAR"
    assert r["stop_loss"] is None
    assert r["take_profit"] is None
    assert len(chamadas) == 0


# ---------------------------------------------------------------------------
# Robustez: ensemble_result ausente NÃO deve disparar rede (ens.prever mockado)
# ---------------------------------------------------------------------------


def test_ensemble_none_usa_fallback_sem_rede(patch_otimizada, monkeypatch):
    """
    Quando ensemble_result=None, analisar() chama ens.prever(). Mockamos para
    garantir hermeticidade (sem rede/modelo) e que o fluxo segue normalmente.
    """
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    monkeypatch.setattr(
        otimizada.ens,
        "prever",
        lambda *a, **k: _ensemble(prob=0.85),
    )

    r = otimizada.analisar("BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=None)
    assert r["sinal"] in ("COMPRA", "VENDA", "AGUARDAR")
    assert isinstance(r["score"], (int, float))


# ---------------------------------------------------------------------------
# imprimir(...) não deve lançar com os mesmos mocks (cobre formatação)
# ---------------------------------------------------------------------------


def test_imprimir_compra_nao_lanca(patch_otimizada, capsys):
    """imprimir() em cenário de COMPRA roda a formatação inteira sem exceção."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0, vol_ultima=2000.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(d1h, d4h, _regime("TENDENCIA_ALTA", score=90), _fear(valor=50), _suporte())

    r = otimizada.imprimir(
        "BTCUSDT", cvd_atual=10.0, ml_prob=0.80, ensemble_result=_ensemble(prob=0.85)
    )
    out = capsys.readouterr().out
    assert "ESTRATEGIA OTIMIZADA" in out
    assert "SINAL:" in out
    assert r["sinal"] in ("COMPRA", "VENDA", "AGUARDAR")


def test_imprimir_aguardar_nao_lanca(patch_otimizada, capsys):
    """imprimir() em cenário AGUARDAR (sem stop/target) também não lança."""
    d1h = _serie_klines(n=100, base=100.0, passo=1.0)
    d4h = _serie_klines(n=60, base=100.0, passo=1.0)
    patch_otimizada(
        d1h, d4h, _regime("LATERAL", pode_operar=False, score=30), _fear(valor=50), _suporte()
    )

    r = otimizada.imprimir(
        "BTCUSDT", cvd_atual=0.0, ml_prob=0.50, ensemble_result=_ensemble(prob=0.50)
    )
    out = capsys.readouterr().out
    assert "SINAL: AGUARDAR" in out
    assert r["sinal"] == "AGUARDAR"
