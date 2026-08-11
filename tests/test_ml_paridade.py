"""
Testes — paridade treino vs. inferência das 11 features (frente E-10)
======================================================================
Critério de saída do E-10, literal: para 200 barras, |feature_treino −
feature_inferencia| < 1e-6 nas 11 features.

O DEFEITO QUE ISTO PEGA. `ml_filtro.extrair_features` é chamado dos dois lados
e fatia `[:i+1]`. O treino passa a série INTEIRA; a inferência passa uma janela
buscada da API. Qualquer indicador que dependa do TAMANHO da janela produz
valores diferentes para a MESMA barra. Medido em BTCUSDT 1h (17.563 velas),
antes da correção:

    feature      |dif| media   |dif| max
    dist_vwap      0,258252     0,494499   <- e com SINAL INVERTIDO
    atr_rel        0,000242     0,003428

Na barra 17.525: treino −0,1831 contra inferência +0,0097. O modelo aprendeu
numa variedade que a produção nunca visita — e `dist_vwap` alimenta um
componente que pesa 20 dos 100 pontos do score.

As causas eram duas, e as duas precisavam de correção:
  1. VWAP CUMULATIVO (ancorado no início da janela) → `vwap_rolling(20)`;
  2. ATR de Wilder, recursivo, sem contexto suficiente → `CONTEXTO_MINIMO=300`.

Este teste roda sobre série SINTÉTICA determinística — sem banco, sem rede.
Um teste que dependesse de `data/btc_data.db` não rodaria num clone limpo, e
foi justamente a ausência de um teste assim que deixou o defeito passar.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ml_filtro  # noqa: E402
from ml_filtro import CONTEXTO_MINIMO, extrair_features  # noqa: E402

TOLERANCIA = 1e-6

NOMES = (
    "dist_ema20",
    "dist_ema50",
    "rsi_norm",
    "atr_rel",
    "vol_rel_norm",
    "bb_rel",
    "dist_vwap",
    "var_1",
    "var_4",
    "var_24",
    "bb_pos",
)


def _serie(n=1200, seed=7):
    """Random walk determinístico com tendência e volatilidade variáveis.

    Sem numpy.random e sem `random` global: um LCG explícito para que a série
    seja idêntica em qualquer máquina e versão de Python.
    """
    estado = seed
    f, m, mn, v = [], [], [], []
    preco = 50_000.0
    for i in range(n):
        estado = (1_103_515_245 * estado + 12_345) % (2**31)
        r = estado / (2**31)
        # deriva lenta + choque; a volatilidade sobe e desce ao longo da serie
        vol = 0.004 * (1.0 + 0.8 * math.sin(i / 97.0))
        preco *= 1.0 + (r - 0.5) * 2 * vol + 0.00002
        alta = preco * (1 + vol * 0.6)
        baixa = preco * (1 - vol * 0.6)
        f.append(preco)
        m.append(alta)
        mn.append(baixa)
        v.append(100.0 + 900.0 * r)
    return f, m, mn, v


@pytest.fixture(scope="module")
def serie():
    return _serie()


def _pares_para_comparar(serie, quantas=200):
    """(features do treino, features da inferência) na MESMA barra."""
    f, m, mn, v = serie
    n = len(f)
    passo = max(1, (n - CONTEXTO_MINIMO - 10) // quantas)
    for i in range(CONTEXTO_MINIMO + 5, n, passo):
        # TREINO: a serie inteira ate i
        ft = extrair_features(f, m, mn, v, i)
        # INFERENCIA: a janela que prever() busca da API, terminando em i
        j0 = i - (CONTEXTO_MINIMO - 1)
        fi = extrair_features(
            f[j0 : i + 1],
            m[j0 : i + 1],
            mn[j0 : i + 1],
            v[j0 : i + 1],
            CONTEXTO_MINIMO - 1,
        )
        if ft is None or fi is None:
            continue
        yield i, ft, fi


class TestParidadeDasFeatures:
    def test_as_11_features_batem_em_200_barras(self, serie):
        piores = {nome: (0.0, None) for nome in NOMES}
        comparadas = 0
        for i, ft, fi in _pares_para_comparar(serie):
            comparadas += 1
            assert len(ft) == len(fi) == len(NOMES)
            for k, nome in enumerate(NOMES):
                d = abs(ft[k] - fi[k])
                if d > piores[nome][0]:
                    piores[nome] = (d, i)
        assert comparadas >= 150, f"so {comparadas} barras comparadas"

        divergentes = {n: p for n, p in piores.items() if p[0] >= TOLERANCIA}
        assert not divergentes, "train/serve skew: " + "; ".join(
            f"{n}: |dif| {d:.8f} na barra {i}" for n, (d, i) in divergentes.items()
        )

    def test_uma_janela_curta_demais_QUEBRA_a_paridade(self, serie):
        """Prova que a tolerância não é frouxa demais para detectar o defeito.

        Sem esta contraprova, o teste acima passaria mesmo que `CONTEXTO_MINIMO`
        voltasse a 100 e a tolerância fosse generosa — e daria a impressão de
        que o skew foi resolvido quando só teria sido escondido.
        """
        f, m, mn, v = serie
        i = len(f) - 1
        ft = extrair_features(f, m, mn, v, i)
        curta = 100  # o valor ANTIGO
        j0 = i - (curta - 1)
        fi = extrair_features(
            f[j0 : i + 1], m[j0 : i + 1], mn[j0 : i + 1], v[j0 : i + 1], curta - 1
        )
        difs = [abs(a - b) for a, b in zip(ft, fi)]
        assert max(difs) > TOLERANCIA, (
            "com janela de 100 velas a paridade DEVERIA quebrar; se nao quebra, "
            "a serie sintetica ficou lisa demais para exercer o defeito"
        )


class TestOrigemDoDefeito:
    def test_vwap_e_rolling_nao_cumulativo(self):
        # O VWAP cumulativo ancora no inicio da janela: com 2 anos de serie
        # fica a ~20% do preco atual, com 100 velas fica colado nele. E dai que
        # vinha o fator de ~30x e a inversao de sinal.
        import inspect

        fonte = inspect.getsource(extrair_features)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert "vwap_rolling(" in codigo
        assert "ind.vwap(" not in codigo, "o VWAP cumulativo voltou"

    def test_contexto_minimo_cobre_o_arranque_do_atr(self):
        # ATR de Wilder e uma EMA de periodo 14: o peso do arranque decai como
        # (13/14)^k. Para ficar abaixo de 1e-6 sao ~190 velas; 300 da folga.
        decaimento = (13 / 14) ** (CONTEXTO_MINIMO - 50)
        assert decaimento < TOLERANCIA, (
            f"CONTEXTO_MINIMO={CONTEXTO_MINIMO} deixa {decaimento:.2e} de memoria "
            f"do arranque do ATR — acima da tolerancia de paridade"
        )

    def test_inferencia_pede_o_contexto_minimo(self):
        # A paridade so vale se `prever()` REALMENTE buscar essas velas.
        import inspect

        fonte = inspect.getsource(ml_filtro.prever)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert (
            '"limit": CONTEXTO_MINIMO' in codigo
        ), "prever() nao pede CONTEXTO_MINIMO velas — a paridade fica so no teste"


class TestContratoDasFeatures:
    def test_sao_exatamente_11(self, serie):
        f, m, mn, v = serie
        feat = extrair_features(f, m, mn, v, len(f) - 1)
        assert len(feat) == 11, (
            "o numero de features mudou; o pickle treinado antes fica incompativel "
            "e o modelo precisa ser RETREINADO"
        )

    def test_nenhuma_feature_e_nan_ou_infinita(self, serie):
        for i, ft, fi in _pares_para_comparar(serie, quantas=50):
            for k, nome in enumerate(NOMES):
                assert math.isfinite(ft[k]), f"{nome} nao-finita no treino, barra {i}"
                assert math.isfinite(fi[k]), f"{nome} nao-finita na inferencia, barra {i}"

    def test_antes_de_55_devolve_none(self, serie):
        f, m, mn, v = serie
        assert extrair_features(f, m, mn, v, 54) is None
        assert extrair_features(f, m, mn, v, 55) is not None
