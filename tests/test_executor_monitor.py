"""
Testes da decisão pura do monitor de posição (trailing stop / take-profit).

Cobre `executor.avaliar_tick_monitor` — a lógica por-tick extraída de
`Executor._monitorar` (refactor comportamento-preservante). Função pura, sem
rede/banco/thread, então é testável de forma exaustiva.

Convenção dos cenários: entrada=100.0 para aritmética simples.
  - ativacao padrão = 1% (0.01)  → ganho >= 1% liga o trailing
  - distancia padrão = 0.8% (0.008) → stop segue 0.8% abaixo do pico
"""

from unittest.mock import MagicMock

import pytest

import executor as E
from executor import (
    Executor,
    avaliar_tick_monitor,
    TRAILING_ATIVACAO,
    TRAILING_DISTANCIA,
)


ENTRADA = 100.0
STOP = 98.0
T1 = 105.0
T2 = 110.0


def _aval(preco, *, stop=STOP, t1=T1, t2=T2, parcial=False, pico=ENTRADA):
    return avaliar_tick_monitor(ENTRADA, stop, t1, t2, parcial, preco, pico)


# ── 1. Stop Loss (terminal) ────────────────────────────────────────────────

def test_stop_loss_abaixo_do_stop():
    d = _aval(97.0)
    assert d["fechar_total"] == "Stop Loss"
    assert d["encerrar"] is True
    # terminal: não dispara parcial nem trailing
    assert d["fechar_parcial"] is False
    assert d["novo_stop_trailing"] is None
    assert d["stop_breakeven"] is None


def test_stop_loss_borda_exata_igual():
    # preco == stop_atual deve disparar (condição é <=)
    d = _aval(STOP)
    assert d["fechar_total"] == "Stop Loss"
    assert d["encerrar"] is True


def test_um_centavo_acima_do_stop_nao_dispara():
    d = _aval(STOP + 0.01)
    assert d["fechar_total"] is None
    assert d["encerrar"] is False


def test_stop_tem_precedencia_sobre_tudo():
    # Mesmo com parcial_feita e preco que tocaria outras coisas, stop manda.
    d = avaliar_tick_monitor(ENTRADA, 100.0, T1, T2, True, 99.0, ENTRADA)
    assert d["fechar_total"] == "Stop Loss"
    assert d["encerrar"] is True


# ── 2. Take-Profit Parcial (não terminal) ──────────────────────────────────

def test_parcial_dispara_no_target1():
    d = _aval(T1, parcial=False)
    assert d["fechar_parcial"] is True
    assert d["stop_breakeven"] == pytest.approx(ENTRADA * 1.002)
    assert d["encerrar"] is False
    assert d["fechar_total"] is None


def test_parcial_borda_exata():
    d = _aval(T1, parcial=False)          # preco == target1
    assert d["fechar_parcial"] is True


def test_parcial_nao_dispara_abaixo_do_target1():
    d = _aval(T1 - 0.01, parcial=False)
    assert d["fechar_parcial"] is False
    assert d["stop_breakeven"] is None


def test_parcial_nao_redispara_se_ja_feita():
    # parcial_feita=True: nunca dispara o parcial de novo
    d = avaliar_tick_monitor(ENTRADA, STOP, T1, T2, True, T1, ENTRADA)
    assert d["fechar_parcial"] is False


# ── 3. Take-Profit Final / Target 2 (terminal, requer parcial_feita) ───────

def test_target2_dispara_com_parcial_feita():
    d = avaliar_tick_monitor(ENTRADA, STOP, T1, T2, True, T2, ENTRADA)
    assert d["fechar_total"] == "Take Profit Final"
    assert d["encerrar"] is True


def test_target2_borda_exata():
    d = avaliar_tick_monitor(ENTRADA, STOP, T1, T2, True, T2, ENTRADA)
    assert d["fechar_total"] == "Take Profit Final"


def test_target2_nao_dispara_sem_parcial_feita():
    # preco >= target2 mas parcial ainda não feita → cai no parcial, não no final
    d = avaliar_tick_monitor(ENTRADA, STOP, T1, T2, False, T2, ENTRADA)
    assert d["fechar_total"] is None
    assert d["fechar_parcial"] is True   # target1 também foi ultrapassado


def test_target2_abaixo_com_parcial_feita_vai_para_trailing():
    # parcial_feita, preco < target2, ganho >= 1% → trailing avalia
    d = avaliar_tick_monitor(ENTRADA, STOP, T1, T2, True, 109.0, ENTRADA)
    assert d["fechar_total"] is None
    assert d["novo_stop_trailing"] == pytest.approx(109.0 * (1 - TRAILING_DISTANCIA))


# ── 4. Trailing Stop ───────────────────────────────────────────────────────

def test_trailing_ativa_e_sobe_stop():
    # preco=102 (ganho 2% >= 1%), pico=100 → pico vira 102, novo_stop=102*0.992
    d = _aval(102.0, pico=ENTRADA)
    assert d["preco_pico"] == 102.0
    assert d["novo_stop_trailing"] == pytest.approx(102.0 * (1 - TRAILING_DISTANCIA))
    assert d["fechar_parcial"] is False   # 102 < target1


def test_trailing_nao_ativa_abaixo_do_ganho_minimo():
    # ganho 0.5% < 1% → sem trailing, pico inalterado
    d = _aval(100.5, pico=ENTRADA)
    assert d["novo_stop_trailing"] is None
    assert d["preco_pico"] == ENTRADA


def test_trailing_borda_exata_de_ativacao():
    # ganho exatamente == ativacao (1%) deve ligar (condição é >=)
    preco = ENTRADA * (1 + TRAILING_ATIVACAO)   # 101.0
    d = _aval(preco, pico=ENTRADA)
    assert d["preco_pico"] == preco
    assert d["novo_stop_trailing"] == pytest.approx(preco * (1 - TRAILING_DISTANCIA))


def test_pico_nao_recua_quando_preco_cai():
    # pico anterior 103, preco atual 102 → pico permanece 103
    d = _aval(102.0, pico=103.0)
    assert d["preco_pico"] == 103.0
    assert d["novo_stop_trailing"] == pytest.approx(103.0 * (1 - TRAILING_DISTANCIA))


def test_pico_sobe_quando_preco_supera():
    d = _aval(104.0, pico=103.0)
    assert d["preco_pico"] == 104.0


def test_trailing_nunca_abaixa_o_stop():
    # stop já alto (104); novo_stop calculado (101.18) é menor → não altera
    d = _aval(102.0, stop=104.0, pico=ENTRADA)
    # nesse caso preco(102) > stop(104)? Não: 102 <= 104 → na verdade isso é STOP.
    # Ajuste: para isolar o trailing, use stop alto mas preco acima dele.
    d = _aval(104.5, stop=104.0, pico=ENTRADA)
    assert d["fechar_total"] is None
    # pico=104.5 → novo_stop=104.5*0.992=103.68 < 104 → não sobe
    assert d["novo_stop_trailing"] is None


def test_trailing_sobe_apenas_se_maior_que_stop_atual():
    # stop atual 101; pico 104.5 → novo_stop 103.68 > 101 → sobe
    d = _aval(104.5, stop=101.0, pico=ENTRADA)
    assert d["novo_stop_trailing"] == pytest.approx(104.5 * (1 - TRAILING_DISTANCIA))


# ── 5. Coexistência Parcial + Trailing no mesmo tick ───────────────────────

def test_parcial_e_trailing_no_mesmo_tick():
    # preco=105 (== target1): dispara parcial E, como ganho=5% >= 1%, trailing.
    # O trailing compara contra o stop do SNAPSHOT (98), não contra o breakeven.
    d = _aval(T1, parcial=False, pico=ENTRADA)
    assert d["fechar_parcial"] is True
    assert d["stop_breakeven"] == pytest.approx(ENTRADA * 1.002)   # 100.2
    novo = T1 * (1 - TRAILING_DISTANCIA)                            # 104.16
    assert d["novo_stop_trailing"] == pytest.approx(novo)
    assert novo > d["stop_breakeven"]   # trailing sobrescreve o breakeven


def test_parcial_sem_trailing_quando_ganho_baixo():
    # target1 baixo (100.5) para que o parcial dispare com ganho < 1%
    d = avaliar_tick_monitor(ENTRADA, STOP, 100.5, T2, False, 100.5, ENTRADA)
    assert d["fechar_parcial"] is True
    assert d["novo_stop_trailing"] is None   # ganho 0.5% não liga trailing
    # nesse caso o stop final fica no breakeven (aplicado pelo _monitorar)


# ── 6. Nenhuma ação ────────────────────────────────────────────────────────

def test_nenhuma_acao_zona_neutra():
    # preco entre stop e target1, ganho < 1%
    d = _aval(100.5, pico=ENTRADA)
    assert d == {
        "fechar_total": None,
        "encerrar": False,
        "fechar_parcial": False,
        "stop_breakeven": None,
        "preco_pico": ENTRADA,
        "novo_stop_trailing": None,
    }


# ── 7. Sequência (simula o loop, demonstra o ratchet do trailing) ──────────

def test_sequencia_ratchet_e_reversao():
    """Preço sobe (stop sobe junto), depois reverte e bate no stop trailado."""
    pico = ENTRADA
    stop = STOP
    # tick 1: 102 → stop sobe p/ 102*0.992 = 101.184
    d = avaliar_tick_monitor(ENTRADA, stop, T1, T2, False, 102.0, pico)
    pico = d["preco_pico"]
    if d["novo_stop_trailing"] is not None:
        stop = d["novo_stop_trailing"]
    assert pico == 102.0
    assert stop == pytest.approx(101.184)

    # tick 2: 104 → pico=104, stop sobe p/ 104*0.992 = 103.168
    d = avaliar_tick_monitor(ENTRADA, stop, T1, T2, False, 104.0, pico)
    pico = d["preco_pico"]
    if d["novo_stop_trailing"] is not None:
        stop = d["novo_stop_trailing"]
    assert pico == 104.0
    assert stop == pytest.approx(103.168)

    # tick 3: reversão para 103.0 → <= stop trailado (103.168) → Stop Loss
    d = avaliar_tick_monitor(ENTRADA, stop, T1, T2, False, 103.0, pico)
    assert d["fechar_total"] == "Stop Loss"
    assert d["encerrar"] is True


# ── 8. Wrapper _monitorar (I/O do loop) — integração com mocks ─────────────

def _exec_com_posicao(stop=STOP, t1=T1, t2=T2, parcial=False):
    ex = Executor(simulacao=True, symbol="BTCUSDT")
    ex.posicao = {
        "tipo": "LONG", "entrada": ENTRADA, "tamanho_btc": 0.01,
        "stop_inicial": stop, "stop_atual": stop, "target1": t1,
        "target2": t2, "parcial_feita": parcial, "abertura": "x", "order_id": "SIM-1",
    }
    ex._ativo = True
    ex.fechar_posicao = MagicMock()
    return ex


def test_monitorar_stop_loss_fecha_e_encerra(monkeypatch):
    ex = _exec_com_posicao()
    ex.get_preco = lambda: 97.0          # <= stop → Stop Loss (terminal: break)
    ex._monitorar()
    ex.fechar_posicao.assert_called_once_with(97.0, "Stop Loss")


def test_monitorar_target2_fecha_e_encerra(monkeypatch):
    ex = _exec_com_posicao(parcial=True)
    ex.get_preco = lambda: T2            # parcial_feita + preco>=target2 → final
    ex._monitorar()
    ex.fechar_posicao.assert_called_once_with(T2, "Take Profit Final")


def test_monitorar_preco_invalido_nao_fecha(monkeypatch):
    ex = _exec_com_posicao()
    ex.get_preco = lambda: 0.0           # preco<=0 → sleep(5)+continue
    # sleep(5) encerra o loop (flag _ativo=False) para não rodar infinito
    monkeypatch.setattr(E.time, "sleep", lambda s: setattr(ex, "_ativo", False))
    ex._monitorar()
    ex.fechar_posicao.assert_not_called()


def test_monitorar_parcial_e_trailing_atualizam_stop(monkeypatch):
    ex = _exec_com_posicao(parcial=False)
    ex.get_preco = lambda: T1            # == target1 → parcial + trailing no tick
    # quebra o loop após 1 iteração (o caminho parcial NÃO dá break)
    monkeypatch.setattr(E.time, "sleep", lambda s: setattr(ex, "_ativo", False))
    ex._monitorar()
    ex.fechar_posicao.assert_called_once_with(T1, "Take Profit Parcial (50%)", parcial=True)
    # trailing (104.16) sobrescreve o breakeven (100.2) no mesmo tick
    assert ex.posicao["stop_atual"] == pytest.approx(T1 * (1 - TRAILING_DISTANCIA))


# ── 9. Verificação por ORÁCULO — equivalência ao _monitorar original ───────
# Replica a lógica EXATA do loop pré-refactor e compara, numa grade densa de
# entradas, o efeito resultante (fechamento, parcial, stop final, pico final)
# contra a aplicação da nova função pura. Se baterem em todos os casos, o
# refactor é comprovadamente comportamento-preservante.

def _efeito_original(entrada, stop, t1, t2, parcial, preco, pico,
                     ativ=TRAILING_ATIVACAO, dist=TRAILING_DISTANCIA):
    """Mirror fiel do corpo do loop _monitorar ANTES do refactor."""
    close, brk, did_partial = None, False, False
    stop_after, pico_after = stop, pico
    ganho = (preco - entrada) / entrada
    # 1. Stop Loss (terminal)
    if preco <= stop:
        return ("Stop Loss", True, False, stop_after, pico_after)
    # 2. Partial (não encerra; seta breakeven em self.posicao)
    if not parcial and preco >= t1:
        did_partial = True
        stop_after = entrada * 1.002
    # 3. Target 2 (terminal)
    if parcial and preco >= t2:
        return ("Take Profit Final", True, did_partial, stop_after, pico_after)
    # 4. Trailing — compara contra o stop do SNAPSHOT (stop original), não o breakeven
    if ganho >= ativ:
        if preco > pico_after:
            pico_after = preco
        novo = pico_after * (1 - dist)
        if novo > stop:
            stop_after = novo
    return (close, brk, did_partial, stop_after, pico_after)


def _efeito_refatorado(entrada, stop, t1, t2, parcial, preco, pico):
    """Aplica a decisão pura como o _monitorar refatorado faz."""
    d = avaliar_tick_monitor(entrada, stop, t1, t2, parcial, preco, pico)
    close, brk, did_partial = d["fechar_total"], d["encerrar"], d["fechar_parcial"]
    pico_after, stop_after = d["preco_pico"], stop
    if brk:
        return (close, brk, did_partial, stop_after, pico_after)
    if did_partial:
        stop_after = d["stop_breakeven"]
    if d["novo_stop_trailing"] is not None:
        stop_after = d["novo_stop_trailing"]
    return (close, brk, did_partial, stop_after, pico_after)


def test_oraculo_equivalencia_em_grade_densa():
    import itertools
    entrada = 100.0
    stops = [96.0, 98.0, 100.2, 101.0, 104.0]
    t1s = [103.0, 105.0]
    t2s = [108.0, 110.0]
    parciais = [False, True]
    picos = [100.0, 102.0, 104.0, 106.0]
    precos = [90.0 + 0.5 * i for i in range(51)]  # 90.0 .. 115.0

    casos = 0
    for stop, t1, t2, parcial, pico in itertools.product(stops, t1s, t2s, parciais, picos):
        for preco in precos:
            esperado = _efeito_original(entrada, stop, t1, t2, parcial, preco, pico)
            obtido = _efeito_refatorado(entrada, stop, t1, t2, parcial, preco, pico)
            # comparar com tolerância nos floats (stop/pico)
            assert esperado[:3] == obtido[:3], (
                f"divergência categórica em stop={stop} t1={t1} t2={t2} "
                f"parcial={parcial} pico={pico} preco={preco}: {esperado} != {obtido}"
            )
            assert esperado[3] == pytest.approx(obtido[3]), f"stop divergente: {esperado} vs {obtido}"
            assert esperado[4] == pytest.approx(obtido[4]), f"pico divergente: {esperado} vs {obtido}"
            casos += 1
    assert casos == len(stops) * len(t1s) * len(t2s) * len(parciais) * len(picos) * len(precos)
