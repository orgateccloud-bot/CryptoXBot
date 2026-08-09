"""
Testes — backtesting/walk_forward.py (medição oficial da Etapa 1 do gate)
==========================================================================
Exigidos pela verificação adversarial de 2026-07-23 antes de promover o
arquivo a medição oficial (docs/GATE_GO_LIVE.md). Cobrem os dois riscos que
invalidariam o gate:

1. CAUSALIDADE — o filtro MTF só pode usar candles 4h já FECHADOS no
   instante da decisão (bug B1: o mapeamento antigo idx//4 enxergava o
   candle 4h ainda aberto — look-ahead em ~75% das velas).
2. CONTABILIDADE — taxa nos dois lados, stop-first no candle ambíguo,
   censura final fechada a mercado (não descartada), retorno sobre capital.

Herméticos: `carregar` é monkeypatchado com séries sintéticas; o score é
mockado para controlar exatamente quando há entrada; XGBoost nunca treina
(janela de treino pequena demais → modelo None).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtesting.walk_forward as wf

MS_1H = wf.MS_1H
MS_4H = wf.MS_4H
T0 = 1_700_000_000_000  # epoch ms arbitrário, alinhado


# ══════════════════════════════════════════════════════════════
# _validar_contiguidade (B2)
# ══════════════════════════════════════════════════════════════


class TestContiguidade:
    def test_serie_contigua_passa(self):
        ts = [T0 + i * MS_1H for i in range(100)]
        wf._validar_contiguidade(ts, MS_1H, "teste/1h")  # não deve lançar

    def test_gap_aborta(self):
        ts = [T0 + i * MS_1H for i in range(50)]
        ts += [ts[-1] + 2 * MS_1H + i * MS_1H for i in range(50)]  # pula 1 candle
        with pytest.raises(SystemExit):
            wf._validar_contiguidade(ts, MS_1H, "teste/1h")

    def test_duplicata_aborta(self):
        ts = [T0, T0 + MS_1H, T0 + MS_1H, T0 + 2 * MS_1H]
        with pytest.raises(SystemExit):
            wf._validar_contiguidade(ts, MS_1H, "teste/1h")


# ══════════════════════════════════════════════════════════════
# _mapear_idx4_fechado (B1 — causalidade do MTF)
# ══════════════════════════════════════════════════════════════


class TestMapeamento4hFechado:
    def test_nenhum_4h_fechado_no_inicio(self):
        # Candle 4h 0 abre em T0 e fecha em T0+4h. Decisões dos candles 1h
        # 0-2 (fechamentos em T0+1h/2h/3h) acontecem ANTES desse fechamento.
        ts1h = [T0 + i * MS_1H for i in range(8)]
        ts4h = [T0, T0 + MS_4H]
        m = wf._mapear_idx4_fechado(ts1h, ts4h)
        assert m[0] == -1 and m[1] == -1 and m[2] == -1

    def test_4h_fechado_exatamente_no_instante_da_decisao_conta(self):
        # Decisão do candle 1h 3 é em T0+4h — exatamente o fechamento do
        # 4h 0 (fechado NO instante = utilizável, sem look-ahead).
        ts1h = [T0 + i * MS_1H for i in range(8)]
        ts4h = [T0, T0 + MS_4H]
        m = wf._mapear_idx4_fechado(ts1h, ts4h)
        assert m[3] == 0
        # candles 1h 4-6 (decisões 5h/6h/7h): ainda só o 4h 0 fechado
        assert m[4] == 0 and m[5] == 0 and m[6] == 0
        # candle 1h 7 (decisão 8h): 4h 1 fecha em 8h → índice 1
        assert m[7] == 1

    def test_nunca_aponta_para_candle_aberto(self):
        # Propriedade estrutural: para todo i, o 4h apontado já FECHOU no
        # instante da decisão (teria pego o bug idx//4 imediatamente).
        n1h, n4h = 200, 50
        ts1h = [T0 + i * MS_1H for i in range(n1h)]
        ts4h = [T0 + j * MS_4H for j in range(n4h)]
        m = wf._mapear_idx4_fechado(ts1h, ts4h)
        for i, j in enumerate(m):
            if j >= 0:
                assert ts4h[j] + MS_4H <= ts1h[i] + MS_1H, f"look-ahead em i={i}: 4h {j} aberto"

    def test_mapeamento_legado_tem_lookahead_confirmado(self):
        # Documenta POR QUE o mapeamento antigo era bug: idx//4 aponta para
        # o candle 4h que CONTÉM a vela 1h (ainda aberto em 3 de cada 4).
        ts1h = [T0 + i * MS_1H for i in range(8)]
        ts4h = [T0 + j * MS_4H for j in range(2)]
        for i in range(8):
            idx_legado = min(i // 4, len(ts4h) - 1)
            fechado_na_decisao = ts4h[idx_legado] + MS_4H <= ts1h[i] + MS_1H
            if i in (0, 1, 2, 4, 5, 6):  # 6 de 8 velas: candle 4h ainda aberto
                assert not fechado_na_decisao


# ══════════════════════════════════════════════════════════════
# _fng_do_dia (B6)
# ══════════════════════════════════════════════════════════════


class TestFngDoDia:
    def test_dia_exato(self):
        # T0 = 2023-11-14 UTC
        fng = {"2023-11-14": 72}
        assert wf._fng_do_dia(fng, T0) == 72

    def test_carry_forward_do_dia_anterior(self):
        fng = {"2023-11-12": 30}
        assert wf._fng_do_dia(fng, T0) == 30

    def test_sem_historico_retorna_none(self):
        assert wf._fng_do_dia({}, T0) is None
        assert wf._fng_do_dia({"2020-01-01": 50}, T0) is None  # >7 dias atrás


# ══════════════════════════════════════════════════════════════
# Contabilidade fim-a-fim (walk_forward com séries sintéticas)
# ══════════════════════════════════════════════════════════════


def _montar_dados(n, preco_fn):
    """Klines 1h sintéticos contíguos + 4h agregados (contíguos)."""
    k1h = []
    for i in range(n):
        p = preco_fn(i)
        k1h.append((T0 + i * MS_1H, p, p * 1.001, p * 0.999, p, 100.0))
    n4 = n // 4
    k4h = []
    for j in range(n4):
        grupo = [preco_fn(i) for i in range(j * 4, j * 4 + 4)]
        k4h.append((T0 + j * MS_4H, grupo[0], max(grupo) * 1.001, min(grupo) * 0.999, grupo[-1], 400.0))
    return k1h, k4h


@pytest.fixture
def wf_sintetico(monkeypatch):
    """walk_forward hermético: preços controlados pelo teste, score mockado,
    sem F&G, sem XGBoost (extrair_features → None ⇒ X_train vazio ⇒ modelo
    None). Para o score_fake saber QUAL candle está sendo avaliado, cada
    índice recebe uma perturbação única de preço (i*1e-6) e o preço é
    resolvido de volta para o índice via mapa — `preparar` devolve a função
    de preço perturbada para os asserts de PnL usarem o valor exato."""

    estado = {"dados": None, "entradas": set(), "mapa_preco": {}}

    def preparar(n, preco_fn, entradas):
        def preco_unico(i):
            return preco_fn(i) + i * 1e-6

        estado["dados"] = _montar_dados(n, preco_unico)
        estado["entradas"] = set(entradas)
        estado["mapa_preco"] = {round(preco_unico(i), 9): i for i in range(n)}
        return preco_unico

    def carregar_fake(symbol, intervalo):
        k1h, k4h = estado["dados"]
        return k4h if intervalo == "4h" else k1h

    def score_fake(*a, preco=None, **k):
        # I-12: `_score_backtest` foi eliminada; walk_forward chama
        # `score_unificado`, que recebe TUDO por keyword e devolve 5 posicoes
        # (a quinta e a lista de avisos sobre o que a regua nao consegue medir).
        i = estado["mapa_preco"].get(round(preco, 9))
        if i in estado["entradas"]:
            return 100, "OPERAR_CHEIO", 1.0, {}, ()
        return 0, "AGUARDAR", 0.0, {}, ()

    monkeypatch.setattr(wf, "carregar", carregar_fake)
    monkeypatch.setattr(wf, "score_unificado", score_fake)
    monkeypatch.setattr(wf, "_carregar_fng", lambda: {})
    monkeypatch.setattr(wf, "extrair_features", lambda *a, **k: None)

    return preparar


TAXA_TESTE = 0.001
SLIP = wf.SLIPPAGE


class TestContabilidade:
    def test_stop_pnl_exato_com_taxa_dos_dois_lados(self, wf_sintetico):
        # Entrada no candle 160 a preço ~100; candle 161 despenca e toca o stop.
        def preco(i):
            if i <= 160:
                return 100.0
            return 90.0  # low ~89.91 <= stop ~97.95

        pu = wf_sintetico(300, preco, entradas={160})
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100, capital_inicial=1000.0,
            taxa=TAXA_TESTE,
        )
        assert r["total_trades"] >= 1
        op = r["operacoes"][0]
        assert op["tipo_saida"] == "STOP"
        entrada = pu(160) * (1 + SLIP)
        stop = entrada * (1 - wf.STOP_PCT)
        ps = stop * (1 - SLIP)
        usdt = 1000.0  # min(1000*0.02/0.02, 1000) * 1.0
        pnl_esperado = usdt * ((ps - entrada) / entrada) - usdt * TAXA_TESTE * 2
        assert op["resultado"] == pytest.approx(pnl_esperado, abs=1e-6)
        # B4: retorno sobre capital antes do trade, líquido de taxa
        assert op["ret_capital_pct"] == pytest.approx(pnl_esperado / 1000.0 * 100, abs=1e-4)

    def test_candle_ambiguo_stop_sai_primeiro(self, wf_sintetico, monkeypatch):
        # Candle 161 com range gigante: toca stop E target — stop tem
        # prioridade (convenção conservadora).
        def preco(i):
            return 100.0

        pu = wf_sintetico(300, preco, entradas={160})
        # reconstrói com a MESMA função perturbada e sobrescreve high/low do
        # candle 161 (close intacto — é a chave de lookup do score_fake)
        k1h, k4h = _montar_dados(300, pu)
        c = k1h[161]
        k1h[161] = (c[0], c[1], 110.0, 90.0, c[4], c[5])

        monkeypatch.setattr(wf, "carregar", lambda s, itv: k4h if itv == "4h" else k1h)
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100,
            capital_inicial=1000.0, taxa=TAXA_TESTE,
        )
        assert r["operacoes"][0]["tipo_saida"] == "STOP"

    def test_censura_final_fecha_a_mercado(self, wf_sintetico):
        # Entrada perto do fim; preço nunca toca stop nem target → posição
        # deve ser fechada como FIM_DADOS (não descartada).
        def preco(i):
            return 100.0

        pu = wf_sintetico(300, preco, entradas={210})
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100, capital_inicial=1000.0,
            taxa=TAXA_TESTE,
        )
        tipos = [o["tipo_saida"] for o in r["operacoes"]]
        assert "FIM_DADOS" in tipos
        # PnL do fechamento a mercado no ÚLTIMO candle testado (214):
        # janela única de teste = [115, 215) com treino=60/teste=100.
        op = [o for o in r["operacoes"] if o["tipo_saida"] == "FIM_DADOS"][0]
        entrada = pu(210) * (1 + SLIP)
        saida = pu(214) * (1 - SLIP)
        pnl_esperado = 1000.0 * ((saida - entrada) / entrada) - 1000.0 * TAXA_TESTE * 2
        assert op["resultado"] == pytest.approx(pnl_esperado, abs=1e-6)

    def test_buy_and_hold_do_periodo_testado(self, wf_sintetico):
        # Preço sobe ~10% linearmente a partir do 1º candle testado → B&H
        # positivo e sem drawdown (série monotônica crescente).
        primeiro_teste = 55 + 60  # inicio + janela_treino

        def preco(i):
            if i < primeiro_teste:
                return 100.0
            return 100.0 * (1 + 0.10 * (i - primeiro_teste) / (299 - primeiro_teste))

        wf_sintetico(300, preco, entradas={140})  # 1 entrada qualquer p/ ter ops
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100, capital_inicial=1000.0,
            taxa=TAXA_TESTE,
        )
        bh = r["buy_and_hold"]
        assert bh is not None
        # último candle testado é 214, não 299 → retorno parcial da rampa
        assert bh["retorno_%"] > 4.0
        assert bh["max_dd_%"] == pytest.approx(0.0, abs=0.01)

    def test_gap_no_banco_aborta_a_medicao(self, wf_sintetico, monkeypatch):
        def preco(i):
            return 100.0

        pu = wf_sintetico(300, preco, entradas=set())
        k1h, k4h = _montar_dados(300, pu)
        k1h_com_gap = k1h[:100] + k1h[101:]  # remove 1 candle do meio

        monkeypatch.setattr(
            wf, "carregar", lambda s, itv: k4h if itv == "4h" else k1h_com_gap
        )
        with pytest.raises(SystemExit):
            wf.walk_forward("SINT", "1h", janela_treino=60, janela_teste=100)
