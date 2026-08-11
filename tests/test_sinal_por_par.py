"""
Testes da frente E-7 — o sinal de cada par vem dos dados DAQUELE par.

O que se media antes desta frente, com file:line verificado:

  - `suporte.py` tinha `SYMBOL = "BTCUSDT"` de modulo e a assinatura era
    `detectar_suportes(intervalo="1h")`. ETHUSDT e SOLUSDT recebiam niveis de
    suporte do BITCOIN, e esse nivel SOBRESCREVIA o stop em
    estrategias/otimizada.py. Para ETH (preco ~$1.858) qualquer suporte de BTC
    e "acima do preco", entao a comparacao `stop_suporte > stop` era sempre
    verdadeira: dai o bloco real de producao com entrada ~$1.858 e stop
    $63.521,65. Nao era caso de borda, era reprodutivel por construcao.
  - `regime.py` tinha `SYMBOL = "BTCUSDT"` e `detectar()` sem par. O regime pesa
    18% do score unificado e ainda modula os pesos do ensemble.
  - `ensemble.prever(regime_atual=...)` nao tinha symbol para passar adiante,
    apesar de existirem data/modelo_xgb_ethusdt.pkl e _solusdt.pkl JA TREINADOS.
    main.py tentava passar o par com
        ens_mod.prever(symbol=par) if hasattr(ens_mod, "symbol") else ens_mod.prever()
    e `ens_mod` e o MODULO ensemble, que nunca teve atributo `symbol`: a condicao
    era permanentemente False. O ramo com symbol nunca executou uma unica vez —
    e em code review o bug parecia resolvido.

Nenhum teste aqui toca a rede: `obter_klines` e substituido em cada modulo.
"""

import math

import pytest

import ensemble
import regime
import suporte
from estrategias import otimizada

PARES = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# Ordens de grandeza reais (ago/2026): usar precos distintos por par e o que
# torna a contaminacao visivel — um suporte de BTC num sinal de SOL nao e um
# numero "um pouco errado", e tres ordens de magnitude fora.
BASE_POR_PAR = {"BTCUSDT": 60000.0, "ETHUSDT": 1850.0, "SOLUSDT": 95.0}


def _serie(symbol, intervalo, n):
    """Serie deterministica e DIFERENTE por (symbol, intervalo).

    A forma tambem muda com o par (fase do seno), nao apenas a escala: assim os
    testes nao podem passar por acidente de proporcionalidade — um regime
    calculado sobre a serie de BTC daria classificacao diferente da de SOL.
    """
    base = BASE_POR_PAR[symbol]
    fase = {"BTCUSDT": 0.0, "ETHUSDT": 1.1, "SOLUSDT": 2.3}[symbol]
    passo = {"1h": 0.7, "4h": 1.9, "1d": 3.1}.get(intervalo, 1.0)
    fech, maxi, mini, vol = [], [], [], []
    for i in range(n):
        p = base * (1 + 0.004 * i * passo + 0.02 * math.sin(fase + i / 6.0))
        fech.append(p)
        maxi.append(p * 1.004)
        mini.append(p * 0.996)
        vol.append(1000.0 + 40.0 * ((i * 7 + int(fase * 10)) % 13))
    return {"abertura": fech[:], "maxima": maxi, "minima": mini, "fechamento": fech, "volume": vol}


@pytest.fixture
def klines_por_par(monkeypatch):
    """Substitui obter_klines nos TRES modulos que o importam, registrando os
    pares efetivamente pedidos."""
    pedidos = []

    def _fake(symbol, intervalo, limite=100, **k):
        pedidos.append((symbol, intervalo))
        if symbol not in BASE_POR_PAR:
            raise AssertionError(f"par inesperado pedido a obter_klines: {symbol!r}")
        return _serie(symbol, intervalo, limite)

    for mod in (regime, suporte, otimizada):
        monkeypatch.setattr(mod, "obter_klines", _fake)
    return pedidos


# ══════════════════════════════════════════════════════════════════
#  1. regime.detectar — 18% do score deixa de ser leitura do BTC
# ══════════════════════════════════════════════════════════════════


class TestRegimePorPar:
    def test_pede_o_par_que_recebeu(self, klines_por_par):
        regime.detectar("SOLUSDT")
        assert {s for s, _ in klines_por_par} == {"SOLUSDT"}
        assert [i for _, i in klines_por_par] == ["1h", "4h", "1d"]

    def test_resultado_carrega_o_par(self, klines_por_par):
        assert regime.detectar("ETHUSDT")["symbol"] == "ETHUSDT"

    def test_regimes_dos_tres_pares_nao_sao_identicos(self, klines_por_par):
        """Antes eram BIT-IDENTICOS: os tres liam a mesma serie de BTCUSDT."""
        vistos = {p: regime.detectar(p) for p in PARES}
        assinaturas = {
            p: (r["regime_final"], r["score"], r["detalhes_tf"]["1h"]["adx"])
            for p, r in vistos.items()
        }
        assert len(set(assinaturas.values())) == 3, f"pares colidiram: {assinaturas}"

    def test_symbol_e_obrigatorio(self):
        with pytest.raises(TypeError):
            regime.detectar()


# ══════════════════════════════════════════════════════════════════
#  2. suporte.detectar_suportes — a causa direta do stop absurdo
# ══════════════════════════════════════════════════════════════════


class TestSuportePorPar:
    def test_pede_o_par_que_recebeu(self, klines_por_par):
        suporte.detectar_suportes("ETHUSDT", "1h")
        assert {s for s, _ in klines_por_par} == {"ETHUSDT"}

    def test_niveis_ficam_na_ordem_de_grandeza_do_par(self, klines_por_par):
        """O teste que o bug de producao teria falhado.

        Suporte de ETH tem de estar na casa dos milhares, nao das dezenas de
        milhares. O bloco real trazia stop $63.521,65 para uma entrada de
        $1.858 — 34x a entrada.
        """
        for par in PARES:
            r = suporte.detectar_suportes(par, "1h")
            base = BASE_POR_PAR[par]
            assert r["symbol"] == par
            assert 0.5 * base < r["preco"] < 3.0 * base
            if r["suporte_forte"] > 0:
                assert 0.5 * base < r["suporte_forte"] < 3.0 * base

    def test_suportes_dos_tres_pares_nao_colidem(self, klines_por_par):
        fortes = {p: suporte.detectar_suportes(p, "1h")["suporte_forte"] for p in PARES}
        assert len(set(fortes.values())) == 3, f"pares colidiram: {fortes}"

    def test_symbol_e_obrigatorio(self):
        with pytest.raises(TypeError):
            suporte.detectar_suportes()

    def test_chamada_posicional_antiga_falha_alto(self, klines_por_par):
        """`detectar_suportes("1h")` passaria "1h" COMO SYMBOL.

        Sem a validacao de data/klines.py isso viraria HTTP 400 -> None -> o
        dict-vazio de fallback, e o par seguiria operando com suporte_forte=0:
        o mesmo modo de falha silenciosa, so trocado de ativo-errado para
        dado-ausente. O fake acima levanta AssertionError; contra a Binance de
        verdade, obter_klines levanta ValueError antes de qualquer requisicao.
        """
        with pytest.raises((AssertionError, ValueError)):
            suporte.detectar_suportes("1h")


# ══════════════════════════════════════════════════════════════════
#  3. ensemble — o guard morto que fazia o bug parecer resolvido
# ══════════════════════════════════════════════════════════════════


class TestEnsemblePorPar:
    def test_repassa_symbol_para_xgb_e_mlp(self, monkeypatch):
        recebidos = {}
        monkeypatch.setitem(
            __import__("sys").modules,
            "ml_filtro",
            type(
                "M",
                (),
                {"prever": staticmethod(lambda s: (recebidos.setdefault("xgb", s), 0.7)[1])},
            ),
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "lstm_modelo",
            type(
                "M",
                (),
                {"prever": staticmethod(lambda s: (recebidos.setdefault("mlp", s), 0.6)[1])},
            ),
        )
        r = ensemble.prever("SOLUSDT", "TENDENCIA_ALTA")
        assert recebidos == {"xgb": "SOLUSDT", "mlp": "SOLUSDT"}
        assert r["symbol"] == "SOLUSDT"

    def test_symbol_e_obrigatorio(self):
        with pytest.raises(TypeError):
            ensemble.prever()

    def test_guard_morto_nao_voltou(self):
        """`hasattr(ens_mod, "symbol")` era permanentemente False.

        Este assert documenta o porque: `ensemble` e um modulo e nunca teve um
        atributo `symbol`. Se alguem reintroduzir aquele padrao, o ramo com
        symbol volta a nunca executar.
        """
        assert not hasattr(ensemble, "symbol")

    def test_main_chama_prever_com_o_par(self):
        """Compara so as linhas de CODIGO de loop_par.

        O comentario que documenta E-7 cita literalmente o guard antigo, para que
        quem ler saiba o que era e por que sumiu — entao a busca ingenua no fonte
        inteiro acusaria o proprio comentario.
        """
        import inspect

        import main

        codigo = "\n".join(
            linha.split("#")[0]
            for linha in inspect.getsource(main.loop_par).splitlines()
            if not linha.strip().startswith("#")
        )
        assert 'hasattr(ens_mod, "symbol")' not in codigo, "guard morto reintroduzido"
        assert "ens_mod.prever(par)" in codigo


# ══════════════════════════════════════════════════════════════════
#  4. Ponta a ponta: analisar() dos tres pares nao colide
# ══════════════════════════════════════════════════════════════════


class TestAnalisarPorPar:
    @pytest.fixture(autouse=True)
    def _sem_io_externo(self, monkeypatch):
        """Fear&Greed e o banco fora do caminho; ensemble com valor por par."""
        monkeypatch.setattr(
            otimizada.fg,
            "obter",
            lambda: {
                "valor": 50,
                "classificacao_pt": "Neutro",
                "pode_operar": True,
                "reducao_alvo": False,
            },
        )
        monkeypatch.setattr(otimizada.database, "salvar_sinal", lambda *a, **k: 1)

    def test_precos_regime_e_suporte_diferem_entre_pares(self, klines_por_par):
        """Criterio de saida de E-7: com fixtures DIFERENTES por par, os campos
        que vinham do BTC passam a diferir. Antes eram bit-identicos.

        `regime_score` NAO entra na lista: com as tres series em alta ele satura
        no teto do componente ADX e coincide legitimamente. Usar um campo saturado
        como discriminante daria um teste que passa ou falha pela forma da fixture,
        nao pelo comportamento do codigo — o ADX cru, que nao satura, entra no
        lugar. A diferenca de REGIME propriamente dita e coberta pelo teste
        dirigido abaixo.
        """
        res = {p: otimizada.analisar(p, ensemble_result={"prob_ensemble": 0.7}) for p in PARES}
        for campo in ("preco", "suporte_forte", "vwap", "atr"):
            valores = {p: res[p][campo] for p in PARES}
            assert len(set(valores.values())) == 3, f"{campo} colidiu entre pares: {valores}"

    def test_regime_final_difere_quando_os_mercados_diferem(self, monkeypatch):
        """Teste DIRIGIDO: BTC em alta, ETH em queda, SOL de lado.

        Este e o assert que o codigo anterior nao tinha como satisfazer de forma
        alguma: `reg.detectar()` lia BTCUSDT para os tres, entao os tres saiam
        TENDENCIA_ALTA independentemente do que ETH e SOL estivessem fazendo.
        """
        formas = {"BTCUSDT": +1.0, "ETHUSDT": -1.0, "SOLUSDT": 0.0}

        def _fake(symbol, intervalo, limite=100, **k):
            base = BASE_POR_PAR[symbol]
            direcao = formas[symbol]
            f = [base * (1 + 0.006 * i * direcao) for i in range(limite)]
            return {
                "abertura": f[:],
                "maxima": [p * 1.002 for p in f],
                "minima": [p * 0.998 for p in f],
                "fechamento": f,
                "volume": [1000.0] * limite,
            }

        for mod in (regime, suporte, otimizada):
            monkeypatch.setattr(mod, "obter_klines", _fake)

        regimes = {p: regime.detectar(p)["regime_final"] for p in PARES}
        assert regimes["BTCUSDT"] == "TENDENCIA_ALTA"
        assert regimes["ETHUSDT"] == "TENDENCIA_BAIXA"
        assert regimes["BTCUSDT"] != regimes["ETHUSDT"] != regimes["SOLUSDT"]

    def test_cada_par_le_apenas_os_proprios_klines(self, klines_por_par):
        otimizada.analisar("SOLUSDT", ensemble_result={"prob_ensemble": 0.7})
        assert {s for s, _ in klines_por_par} == {
            "SOLUSDT"
        }, "algum modulo do caminho voltou a ler outro par"

    def test_analisar_nao_escreve_no_banco(self, klines_por_par, monkeypatch):
        """E-7: leitura separada de escrita. dashboard.py chamava analisar() a
        cada 30s por par so para exibir, gravando na tabela que o gate le."""
        gravou = []
        monkeypatch.setattr(
            otimizada.database, "salvar_sinal", lambda *a, **k: gravou.append(a) or 1
        )
        for p in PARES:
            otimizada.analisar(p, ensemble_result={"prob_ensemble": 0.9})
        assert gravou == []
