"""
Testes — rótulo de barreira tripla (frente E-10)
=================================================
O rótulo antigo era:

    preco_futuro = max(fechamentos[i+1 : i+JANELA+1])
    label = 1 if (preco_futuro - preco_entrada)/preco_entrada >= ALVO_PCT

Dois defeitos:

1. Lia só FECHAMENTOS. Stop e alvo são tocados INTRABAR, pela mínima e pela
   máxima — um alvo atingido no meio da vela e desfeito antes do fechamento
   não aparecia.
2. Não tinha barreira INFERIOR. `max()` ignora o CAMINHO: um trade que cai 3%
   (estourando o stop e encerrando a posição no prejuízo) e só depois sobe
   1,5% era rotulado **POSITIVO**. O modelo era otimizado para uma pergunta
   que a mesa não faz.

Medido sobre a série real (BTCUSDT 1h, 17.500 amostras): **118 rótulos**
positivos — 4,53% de todos os positivos antigos — tinham o stop tocado ANTES
do alvo. ETHUSDT 171 (4,04%), SOLUSDT 157 (2,73%).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml_filtro import rotular_barreira_tripla  # noqa: E402

ALVO = 0.015
STOP = 0.015
JANELA = 8
ENTRADA = 100.0


def _velas(movimentos):
    """(maximas, minimas) a partir de (alta_pct, baixa_pct) por vela.

    Índice 0 é a vela da ENTRADA e nunca é avaliada — o rótulo olha de i+1 em
    diante, porque a decisão é tomada no fechamento de i.
    """
    m = [ENTRADA]
    n = [ENTRADA]
    for alta, baixa in movimentos:
        m.append(ENTRADA * (1 + alta))
        n.append(ENTRADA * (1 + baixa))
    return m, n


def _rotular(movimentos, alvo=ALVO, stop=STOP, janela=JANELA):
    m, n = _velas(movimentos)
    return rotular_barreira_tripla(m, n, 0, ENTRADA, alvo, stop, janela)


class TestOBugQueIssoCorrige:
    def test_cai_estourando_o_stop_e_depois_sobe_e_NEGATIVO(self):
        # O caso do relatorio, literal: -3% (stop estourado) e so depois +1,5%.
        # O rotulo antigo via `max(fechamentos) >= +1,5%` e marcava POSITIVO.
        label, barreira, velas = _rotular(
            [(0.000, -0.030), (0.020, 0.010)]  # vela 1 fura o stop; vela 2 bate o alvo
        )
        assert label == 0, "trade que estourou o stop nao pode ser rotulado positivo"
        assert barreira == "STOP"
        assert velas == 1

    def test_sobe_antes_de_cair_e_POSITIVO(self):
        # A ordem importa: o mesmo par de velas, invertido, e um trade ganho.
        label, barreira, velas = _rotular(
            [(0.020, 0.010), (0.000, -0.030)]
        )
        assert label == 1
        assert barreira == "ALVO"
        assert velas == 1

    def test_alvo_tocado_INTRABAR_conta(self):
        # Segundo defeito: o alvo tocado no meio da vela e desfeito antes do
        # fechamento sumia, porque o rotulo antigo so lia fechamentos.
        label, barreira, _ = _rotular([(0.018, -0.005)])
        assert label == 1 and barreira == "ALVO"

    def test_stop_tocado_INTRABAR_conta(self):
        label, barreira, _ = _rotular([(0.005, -0.018)])
        assert label == 0 and barreira == "STOP"


class TestPrioridadeNoCandleAmbiguo:
    def test_stop_vence_quando_a_mesma_vela_toca_os_dois(self):
        # Nao da para saber a ordem intrabar. Assumir a pior e a mesma
        # convencao ja usada em walk_forward e no otimizador — e a unica
        # conservadora: rotular positivo um trade que PODE ter morrido antes
        # ensina o modelo a comprar exatamente o que o mata.
        label, barreira, velas = _rotular([(0.020, -0.020)])
        assert label == 0
        assert barreira == "STOP"
        assert velas == 1


class TestBarreiraVertical:
    def test_nao_andou_no_prazo_e_NEGATIVO(self):
        label, barreira, velas = _rotular([(0.005, -0.005)] * JANELA)
        assert label == 0
        assert barreira == "TEMPO"
        assert velas == JANELA

    def test_toca_o_alvo_DEPOIS_da_janela_nao_conta(self):
        movimentos = [(0.005, -0.005)] * JANELA + [(0.050, 0.040)]
        label, barreira, _ = _rotular(movimentos)
        assert label == 0 and barreira == "TEMPO"

    def test_ultima_vela_da_janela_ainda_conta(self):
        movimentos = [(0.005, -0.005)] * (JANELA - 1) + [(0.020, 0.010)]
        label, barreira, velas = _rotular(movimentos)
        assert label == 1 and barreira == "ALVO"
        assert velas == JANELA

    def test_serie_que_acaba_antes_da_janela_nao_estoura(self):
        # Fim do historico: nao ha velas suficientes para decidir. TEMPO, sem
        # IndexError.
        label, barreira, _ = _rotular([(0.005, -0.005)])
        assert label == 0 and barreira == "TEMPO"


class TestBarreirasAssimetricas:
    def test_stop_mais_largo_deixa_o_trade_sobreviver(self):
        # SOL tem stop de 3,0% contra alvo de 1,5%: uma queda de 2% que mataria
        # o BTC nao mata o SOL, e o trade segue vivo ate bater o alvo.
        movimentos = [(0.000, -0.020), (0.020, 0.010)]
        assert _rotular(movimentos, stop=0.015)[0] == 0  # BTC morre
        assert _rotular(movimentos, stop=0.030)[0] == 1  # SOL sobrevive

    def test_o_stop_vem_do_par_e_nao_de_um_numero_inventado(self):
        # A barreira inferior tem de ser o stop REAL — e ele que encerra a
        # posicao em producao.
        import inspect

        import ml_filtro

        fonte = inspect.getsource(ml_filtro.preparar_dataset)
        codigo = "\n".join(
            ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "get_params(symbol)" in codigo
        assert 'stop_pct = get_params(symbol)["stop_pct"]' in codigo


class TestContrato:
    def test_devolve_tripla_coerente(self):
        for movimentos in ([(0.020, 0.010)], [(0.000, -0.030)], [(0.001, -0.001)]):
            label, barreira, velas = _rotular(movimentos)
            assert label in (0, 1)
            assert barreira in ("ALVO", "STOP", "TEMPO")
            assert velas >= 1
            assert (label == 1) == (barreira == "ALVO")

    def test_preparar_dataset_usa_o_rotulador(self):
        import inspect

        import ml_filtro

        fonte = inspect.getsource(ml_filtro.preparar_dataset)
        codigo = "\n".join(
            ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "rotular_barreira_tripla(" in codigo
        # e o rotulo antigo nao pode ter sobrado em lugar nenhum
        assert "max(fechamentos[i + 1" not in codigo

    @pytest.mark.parametrize("janela", [1, 4, 8, 24])
    def test_respeita_a_janela_pedida(self, janela):
        movimentos = [(0.005, -0.005)] * 50
        _, barreira, velas = _rotular(movimentos, janela=janela)
        assert barreira == "TEMPO"
        assert velas == janela
