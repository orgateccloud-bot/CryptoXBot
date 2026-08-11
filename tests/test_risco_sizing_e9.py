"""
Testes — o que realmente dimensiona a posição (frente E-9, ação 2)
====================================================================
`MAX_RISCO_POR_TRADE` anunciava "2% do capital por operação". Isso nunca
aconteceu: quem manda no tamanho é o teto de exposição de 20% do capital em
`calcular_tamanho`, que domina para qualquer fator de risco acima de
0,20*stop_pct — 0,3% no BTC. Com o Kelly no próprio teto de 2%, o caminho por
risco jamais chegava a bindar.

A frente E-9 alinhou a declaração à realidade: a constante virou 0,009, que é
o MAIOR risco por trade realmente possível (0,20 x 1,5 x 3,0%). Estes testes
existem para duas coisas:

  1. provar que o alinhamento NÃO mudou sizing — 0,9% foi escolhido
     exatamente por isso, e 0,6% ou 0,3% teriam mudado (9 e 57 casos de 72);
  2. impedir que a declaração volte a ser ficção — se alguém adicionar um par
     com stop mais largo, o teto real sobe e `test_a_constante_e_o_teto_real`
     falha, forçando a re-derivação em vez de deixar o número mentir de novo.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import risco  # noqa: E402
from config.params_pares import PARAMS_PARES  # noqa: E402

PRECOS = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0, "SOLUSDT": 150.0}
CAPITAIS = (100.0, 1000.0, 25000.0)
# atr_relativo BAIXO => mult_vol ALTO (ate VOL_TARGET_MULT_MAX)
ATRS = (None, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0)


def _casos():
    for cap in CAPITAIS:
        for par, p in PARAMS_PARES.items():
            preco = PRECOS.get(par)
            if preco is None:
                continue
            yield cap, par, preco, preco * (1 - p["stop_pct"]), p["stop_pct"]


class TestDeclaracaoBateComARealidade:
    def test_a_constante_e_o_teto_real(self):
        """0,009 = 0,20 (teto de exposicao) x 1,5 (vol max) x 3,0% (stop mais
        largo). Se um par novo tiver stop maior, este teste falha — e e para
        falhar: a constante teria voltado a subestimar o risco possivel."""
        maior_stop = max(p["stop_pct"] for p in PARAMS_PARES.values())
        teto_real = 0.20 * risco.VOL_TARGET_MULT_MAX * maior_stop
        assert risco.MAX_RISCO_POR_TRADE == pytest.approx(teto_real, rel=1e-9), (
            f"MAX_RISCO_POR_TRADE ({risco.MAX_RISCO_POR_TRADE:.4%}) nao bate com o "
            f"teto real ({teto_real:.4%}); o stop mais largo hoje e {maior_stop:.1%}"
        )

    def test_nenhum_par_arrisca_mais_que_o_declarado(self):
        for cap, par, preco, stop, _ in _casos():
            for atr in ATRS:
                qty = risco.calcular_tamanho(cap, preco, stop, atr_relativo=atr)
                risco_pct = qty * (preco - stop) / cap
                assert risco_pct <= risco.MAX_RISCO_POR_TRADE + 1e-9, (
                    f"{par} cap={cap} atr={atr}: arriscou {risco_pct:.4%} > "
                    f"{risco.MAX_RISCO_POR_TRADE:.4%}"
                )

    def test_o_teto_declarado_e_alcancado_de_fato(self):
        # Um teto que nunca e tocado nao descreve nada. O par de stop mais
        # largo, em vol minima, tem de chegar nele.
        par = max(PARAMS_PARES, key=lambda k: PARAMS_PARES[k]["stop_pct"])
        preco = PRECOS[par]
        stop = preco * (1 - PARAMS_PARES[par]["stop_pct"])
        qty = risco.calcular_tamanho(1000.0, preco, stop, atr_relativo=0.1)
        assert qty * (preco - stop) / 1000.0 == pytest.approx(risco.MAX_RISCO_POR_TRADE, rel=1e-6)


class TestQuemMandaNoTamanho:
    def test_o_teto_de_exposicao_domina_na_vol_de_referencia(self):
        # A constatacao central do E-9: o notional e ~20% do capital para
        # TODOS os pares, independente do stop.
        for cap, par, preco, stop, _ in _casos():
            qty = risco.calcular_tamanho(cap, preco, stop, atr_relativo=1.0)
            notional = qty * preco
            assert notional / cap == pytest.approx(0.20, abs=0.005), (
                f"{par} cap={cap}: notional {notional/cap:.1%} do capital, "
                f"esperado ~20% (o teto)"
            )

    def test_o_risco_por_trade_varia_com_o_stop_nao_com_o_kelly(self):
        # Consequencia direta: como o teto iguala o NOTIONAL, o risco acaba
        # proporcional ao stop_pct. SOL arrisca 2x o BTC. Nao e um bug do
        # calculo — e a politica que o teto impoe, e esta pinada aqui para que
        # a decisao de mudar (ou nao) seja consciente.
        riscos = {}
        for cap, par, preco, stop, _ in _casos():
            if cap != 1000.0:
                continue
            qty = risco.calcular_tamanho(cap, preco, stop, atr_relativo=1.0)
            riscos[par] = qty * (preco - stop) / cap
        assert riscos["BTCUSDT"] == pytest.approx(0.0030, abs=1e-4)
        assert riscos["ETHUSDT"] == pytest.approx(0.0040, abs=1e-4)
        assert riscos["SOLUSDT"] == pytest.approx(0.0060, abs=1e-4)
        assert riscos["SOLUSDT"] / riscos["BTCUSDT"] == pytest.approx(2.0, abs=0.02)

    def test_kelly_nao_altera_o_tamanho(self):
        # Enquanto o teto dominar, o Kelly e decorativo. Se um dia deixar de
        # ser, este teste falha e avisa que a hierarquia mudou.
        preco, stop = 60000.0, 60000.0 * (1 - 0.015)
        base = risco.calcular_tamanho(1000.0, preco, stop, atr_relativo=1.0)
        for kelly in (0.004, 0.006, 0.009):
            assert risco.calcular_tamanho(1000.0, preco, stop, fator_risco=kelly) == pytest.approx(
                base, rel=1e-9
            ), f"kelly={kelly} mudou o tamanho"

    def test_vol_targeting_continua_funcionando_atraves_do_teto(self):
        # O teto escala com mult_vol, entao o vol targeting NAO ficou inerte —
        # e o unico mecanismo que ainda move o tamanho.
        preco, stop = 60000.0, 60000.0 * (1 - 0.015)
        alta_vol = risco.calcular_tamanho(1000.0, preco, stop, atr_relativo=3.0)
        baixa_vol = risco.calcular_tamanho(1000.0, preco, stop, atr_relativo=0.4)
        assert baixa_vol > alta_vol, "vol targeting parou de afetar o tamanho"
        assert baixa_vol / alta_vol == pytest.approx(
            risco.VOL_TARGET_MULT_MAX / risco.VOL_TARGET_MULT_MIN, rel=0.01
        )


class TestAlinhamentoNaoMudouSizing:
    """0,9% foi escolhido por ser o único valor que preserva o comportamento
    anterior em todos os casos. Estes testes recalculam o tamanho sob o valor
    ANTIGO (2%) e exigem igualdade — se alguém baixar a constante achando que
    é só cosmético, o sizing muda e isto quebra."""

    def test_identico_ao_comportamento_com_2_por_cento(self, monkeypatch):
        atual = {}
        for cap, par, preco, stop, _ in _casos():
            for atr in ATRS:
                atual[(cap, par, atr)] = risco.calcular_tamanho(cap, preco, stop, atr_relativo=atr)

        monkeypatch.setattr(risco, "MAX_RISCO_POR_TRADE", 0.02)  # valor antigo
        for cap, par, preco, stop, _ in _casos():
            for atr in ATRS:
                antigo = risco.calcular_tamanho(cap, preco, stop, atr_relativo=atr)
                assert antigo == pytest.approx(atual[(cap, par, atr)], rel=1e-12), (
                    f"{par} cap={cap} atr={atr}: o alinhamento MUDOU o sizing "
                    f"({antigo} -> {atual[(cap, par, atr)]})"
                )

    @pytest.mark.parametrize("valor_menor", [0.006, 0.003])
    def test_valores_menores_mudariam_o_sizing(self, monkeypatch, valor_menor):
        # Prova de que a escolha de 0,9% nao foi arbitraria: qualquer valor
        # abaixo faz o caminho por risco bindar em algum caso.
        atual = {}
        for cap, par, preco, stop, _ in _casos():
            for atr in ATRS:
                atual[(cap, par, atr)] = risco.calcular_tamanho(cap, preco, stop, atr_relativo=atr)

        monkeypatch.setattr(risco, "MAX_RISCO_POR_TRADE", valor_menor)
        difs = 0
        for cap, par, preco, stop, _ in _casos():
            for atr in ATRS:
                novo = risco.calcular_tamanho(cap, preco, stop, atr_relativo=atr)
                if abs(novo - atual[(cap, par, atr)]) > 1e-12:
                    difs += 1
        assert difs > 0, f"{valor_menor:.1%} nao mudaria nada — a escolha de 0,9% seria arbitraria"
