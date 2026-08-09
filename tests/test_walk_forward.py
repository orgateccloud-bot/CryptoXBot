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

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

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
            # I-12: estes testes medem ARITMETICA de PnL; o historico de F&G e
            # irrelevante para isso e esta mockado como {}. Sem a flag, o novo
            # abort de F&G impediria a medicao.
            permitir_sem_fng=True,
        )
        assert r["total_trades"] >= 1
        op = r["operacoes"][0]
        assert op["tipo_saida"] == "STOP"
        entrada = pu(160) * (1 + SLIP)
        # I-12: le o parametro do PAR, como o codigo faz. Antes era
        # `wf.STOP_PCT`, a constante de modulo que nao correspondia a par
        # nenhum — o teste passava porque media contra a mesma ficcao.
        from config.params_pares import get_params
        stop_pct = get_params("SINT")["stop_pct"]
        stop = entrada * (1 - stop_pct)
        ps = stop * (1 - SLIP)
        usdt = min(1000.0 * 0.02 / stop_pct, 1000.0)  # teto de capital domina
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
            permitir_sem_fng=True,
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
            # I-12: estes testes medem ARITMETICA de PnL; o historico de F&G e
            # irrelevante para isso e esta mockado como {}. Sem a flag, o novo
            # abort de F&G impediria a medicao.
            permitir_sem_fng=True,
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
            # I-12: estes testes medem ARITMETICA de PnL; o historico de F&G e
            # irrelevante para isso e esta mockado como {}. Sem a flag, o novo
            # abort de F&G impediria a medicao.
            permitir_sem_fng=True,
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


# ══════════════════════════════════════════════════════════════
# Gate do Fear & Greed (I-12g)
# ══════════════════════════════════════════════════════════════
#
# Motivo destes testes: `data/fng_historico.json` NAO EXISTE nesta maquina.
# Toda medicao oficial do gate rodou com `fg_valor = 50` fixo — ou seja, sem
# o veto de sentimento que a producao aplica de verdade — e o unico registro
# disso era um `fng_historico_usado: false` enterrado no JSON de saida, que
# ninguem le. O gate novo aborta. Estes testes existem para que ele nao possa
# voltar a degradar em silencio: se alguem trocar o default de
# `permitir_sem_fng` para True, `test_default_e_fail_closed` quebra.


def _dias_de(ts1h):
    """Chaves 'YYYY-MM-DD' UTC cobrindo o periodo, como o arquivo real."""
    ini = datetime.fromtimestamp(ts1h[0] / 1000, tz=timezone.utc)
    fim = datetime.fromtimestamp(ts1h[-1] / 1000, tz=timezone.utc)
    dias, d = [], ini
    while d <= fim:
        dias.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return dias


class TestCoberturaFng:
    def test_historico_completo_cobre(self):
        ts = [T0 + i * MS_1H for i in range(24 * 10)]
        fng = {dia: 50 for dia in _dias_de(ts)}
        c = wf._cobertura_fng(fng, ts)
        assert c["cobre"] is True
        assert c["faltantes"] == 0
        assert c["dias"] == len(_dias_de(ts))

    def test_historico_que_para_no_meio_nao_cobre(self):
        # O caso que uma checagem de os.path.exists() deixaria passar: o
        # arquivo existe e tem dados, mas so da primeira metade do periodo.
        ts = [T0 + i * MS_1H for i in range(24 * 30)]
        dias = _dias_de(ts)
        fng = {dia: 50 for dia in dias[: len(dias) // 2]}
        c = wf._cobertura_fng(fng, ts)
        assert c["cobre"] is False
        assert c["faltantes"] > 0

    def test_buraco_curto_e_coberto_pelo_carry(self):
        # Carry-forward de ate 7 dias e causal (usa o ultimo valor JA
        # publicado), entao um buraco de 3 dias nao invalida a cobertura.
        ts = [T0 + i * MS_1H for i in range(24 * 20)]
        dias = _dias_de(ts)
        fng = {dia: 50 for dia in dias}
        for dia in dias[10:13]:
            del fng[dia]
        assert wf._cobertura_fng(fng, ts)["cobre"] is True

    def test_buraco_longo_nao_e_coberto_pelo_carry(self):
        ts = [T0 + i * MS_1H for i in range(24 * 20)]
        dias = _dias_de(ts)
        fng = {dia: 50 for dia in dias}
        for dia in dias[5:15]:  # 10 dias > janela de carry de 7
            del fng[dia]
        c = wf._cobertura_fng(fng, ts)
        assert c["cobre"] is False
        assert c["faltantes"] >= 3

    def test_historico_vazio_nao_cobre_nenhum_dia(self):
        ts = [T0 + i * MS_1H for i in range(24 * 5)]
        c = wf._cobertura_fng({}, ts)
        assert c["cobre"] is False
        assert c["faltantes"] == c["dias"] > 0

    def test_serie_vazia_nao_cobre(self):
        c = wf._cobertura_fng({"2024-01-01": 50}, [])
        assert c["cobre"] is False
        assert c["dias"] == 0


class TestGateFngNaMedicao:
    def test_default_e_fail_closed(self):
        # Trava de regressao: o default NAO pode virar True. Se virar, a
        # medicao volta a rodar sem sentimento sem ninguem perceber.
        padrao = inspect.signature(wf.walk_forward).parameters["permitir_sem_fng"].default
        assert padrao is False

    def test_fng_ausente_aborta_a_medicao(self, wf_sintetico):
        # A fixture ja mocka _carregar_fng -> {} (o estado real da maquina).
        def preco(i):
            return 100.0

        wf_sintetico(300, preco, entradas=set())
        with pytest.raises(wf.FngIndisponivel):
            wf.walk_forward("SINT", "1h", janela_treino=60, janela_teste=100)

    def test_cobertura_parcial_tambem_aborta(self, wf_sintetico, monkeypatch):
        def preco(i):
            return 100.0

        wf_sintetico(300, preco, entradas=set())
        ts = [T0 + i * MS_1H for i in range(300)]
        dias = _dias_de(ts)
        parcial = {dia: 50 for dia in dias[:2]}  # cobre so o comeco
        monkeypatch.setattr(wf, "_carregar_fng", lambda: parcial)
        with pytest.raises(wf.FngIndisponivel):
            wf.walk_forward("SINT", "1h", janela_treino=60, janela_teste=100)

    def test_flag_explicita_libera_e_fica_registrada_no_resultado(self, wf_sintetico):
        def preco(i):
            return 100.0

        wf_sintetico(300, preco, entradas={160})
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100,
            capital_inicial=1000.0, taxa=TAXA_TESTE, permitir_sem_fng=True,
        )
        # Nao basta rodar: o resultado tem de carregar a ressalva, senao o
        # numero circula depois sem o aviso de que veio sem sentimento.
        assert r["fng_historico_usado"] is False
        assert r["fng_cobertura"]["cobre"] is False
        assert r["fng_cobertura"]["faltantes"] == r["fng_cobertura"]["dias"]

    def test_historico_completo_passa_sem_a_flag(self, wf_sintetico, monkeypatch):
        def preco(i):
            return 100.0

        wf_sintetico(300, preco, entradas={160})
        ts = [T0 + i * MS_1H for i in range(300)]
        completo = {dia: 50 for dia in _dias_de(ts)}
        monkeypatch.setattr(wf, "_carregar_fng", lambda: completo)
        r = wf.walk_forward(
            "SINT", "1h", janela_treino=60, janela_teste=100,
            capital_inicial=1000.0, taxa=TAXA_TESTE,
        )
        assert r["fng_historico_usado"] is True
        assert r["fng_cobertura"]["cobre"] is True


# ══════════════════════════════════════════════════════════════
# Parametros por par (I-12f)
# ══════════════════════════════════════════════════════════════
#
# Antes, walk_forward media com STOP_PCT=0.020 / TARGET_PCT=0.040 fixos no
# modulo — valores que nao correspondiam a NENHUM par de config/params_pares.py
# (BTC 0.015/0.050, ETH 0.020/0.060, SOL 0.030/0.050). Como o sizing e
# `min(capital * 0.02 / stop_pct, capital)`, medir com o stop errado erra
# tambem o tamanho da posicao. Este teste prova que a leitura e por par.


class TestParamsPorPar:
    def test_pares_diferentes_produzem_stops_diferentes(self, wf_sintetico):
        from config.params_pares import get_params

        def preco(i):
            return 100.0 if i <= 160 else 90.0  # despenca e toca qualquer stop

        pu = wf_sintetico(300, preco, entradas={160})
        entrada = pu(160) * (1 + SLIP)

        saidas = {}
        for par in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            r = wf.walk_forward(
                par, "1h", janela_treino=60, janela_teste=100,
                capital_inicial=1000.0, taxa=TAXA_TESTE, permitir_sem_fng=True,
            )
            op = r["operacoes"][0]
            assert op["tipo_saida"] == "STOP"
            saidas[par] = op["preco_saida"]
            # o stop realizado bate com o stop_pct DAQUELE par
            # (`preco_saida` e gravado com round(...,2) — walk_forward.py:356)
            esperado = entrada * (1 - get_params(par)["stop_pct"]) * (1 - SLIP)
            assert op["preco_saida"] == round(esperado, 2)

        # e os tres sao mesmo distintos (o teste acima passaria se todos
        # lessem o mesmo par por engano e get_params concordasse)
        assert len(set(saidas.values())) == 3

    def test_par_desconhecido_cai_no_default_sem_quebrar(self):
        from config.params_pares import PARAMS_DEFAULT, get_params

        assert get_params("PARINEXISTENTE")["stop_pct"] == PARAMS_DEFAULT["stop_pct"]

    def test_fallbacks_do_modulo_nao_sao_mais_usados_por_nenhum_par(self):
        # Trava de documentacao viva: as constantes seguem no modulo so como
        # ultimo recurso. Se um dia algum par passar a valer exatamente elas,
        # este teste avisa que o comentario do modulo ficou desatualizado.
        from config.params_pares import PARAMS_PARES

        for par, p in PARAMS_PARES.items():
            assert (p["stop_pct"], p["target_pct"]) != (
                wf.STOP_PCT_FALLBACK,
                wf.TARGET_PCT_FALLBACK,
            ), f"{par} agora coincide com o fallback — revise o comentario de I-12f"
