"""
Suíte pytest para regime.py (Detecção de Regime de Mercado).

Perfil @Delta — testes herméticos: SEM rede, SEM banco.

A única dependência externa de I/O de regime.py é `regime._klines`, que faz
HTTP para a Binance via `requests`. Todos os testes que exercitam `detectar()`
substituem `regime._klines` (monkeypatch) por uma função determinística que
devolve séries sintéticas por timeframe. Nenhum teste toca a rede.

As funções de cálculo (`_classificar_tf`, `_adx`) são puras e recebem dados
sintéticos diretamente — não precisam de mock.

Os testes refletem o comportamento REAL do fonte (verificado por execução):
  - séries de alta limpa produzem ADX ~100 e regime TENDENCIA_ALTA;
  - séries de baixa limpa produzem TENDENCIA_BAIXA;
  - ruído senoidal de baixa amplitude produz ADX baixo -> LATERAL;
  - uma vela final com range gigante eleva o ATR ratio -> VOLATILIDADE.
"""

import math
import os
import sys

import pytest

# regime.py vive na raiz do repo.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import regime  # noqa: E402

# ---------------------------------------------------------------------------
# Geradores de séries sintéticas
# ---------------------------------------------------------------------------

N = 60  # mesmo `limite` default usado por regime._klines


def _serie_alta(n=N):
    """Tendência de alta limpa e monotônica."""
    f = [100.0 + i for i in range(n)]
    h = [x + 1.0 for x in f]
    l = [x - 1.0 for x in f]
    v = [1000.0] * n
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _serie_baixa(n=N):
    """Tendência de baixa limpa e monotônica."""
    f = [200.0 - i for i in range(n)]
    h = [x + 1.0 for x in f]
    l = [x - 1.0 for x in f]
    v = [1000.0] * n
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _serie_lateral(n=N):
    """Mercado de range: oscilação senoidal de baixa amplitude (ADX fraco)."""
    f = [100.0 + math.sin(i / 2) for i in range(n)]
    h = [x + 0.5 for x in f]
    l = [x - 0.5 for x in f]
    v = [1000.0] * n
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _serie_volatilidade(n=N):
    """Série calma com uma vela final de range explosivo -> ATR ratio alto."""
    f = [100.0 + 0.1 * i for i in range(n)]
    h = [x + 0.5 for x in f]
    l = [x - 0.5 for x in f]
    # Última vela com amplitude gigante (50x a faixa normal).
    h[-1] = f[-1] + 50.0
    l[-1] = f[-1] - 50.0
    v = [1000.0] * n
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _mock_klines(mapa):
    """Fábrica de substituto para regime._klines a partir de um dict por timeframe."""

    def fake(intervalo, limite=60):
        return mapa[intervalo]

    return fake


# ===========================================================================
# _classificar_tf — classificação por timeframe
# ===========================================================================


class TestClassificarTF:

    def test_serie_alta_eh_tendencia_alta(self):
        r = regime._classificar_tf(_serie_alta(), "1H")
        assert r["regime"] == "TENDENCIA_ALTA"
        assert r["direcao"] == "ALTA"

    def test_serie_baixa_eh_tendencia_baixa(self):
        r = regime._classificar_tf(_serie_baixa(), "4H")
        assert r["regime"] == "TENDENCIA_BAIXA"
        assert r["direcao"] == "BAIXA"

    def test_serie_lateral_eh_lateral(self):
        r = regime._classificar_tf(_serie_lateral(), "1D")
        assert r["regime"] == "LATERAL"
        # ADX fraco caracteriza o range (abaixo do limiar de tendência).
        assert r["adx"] < regime.ADX_TENDENCIA

    def test_dados_none_retorna_indefinido(self):
        r = regime._classificar_tf(None, "1H")
        assert r == {
            "regime": "INDEFINIDO",
            "adx": 0,
            "atr_ratio": 0,
            "direcao": "NEUTRO",
        }

    def test_label_propagado(self):
        r = regime._classificar_tf(_serie_alta(), "4H")
        assert r["label"] == "4H"

    def test_chaves_obrigatorias_presentes(self):
        r = regime._classificar_tf(_serie_alta(), "1H")
        for chave in (
            "regime",
            "adx",
            "atr_ratio",
            "direcao",
            "ema20",
            "ema50",
            "ema200",
            "vol_crescente",
            "preco",
            "label",
        ):
            assert chave in r

    def test_atr_ratio_e_adx_sao_numericos(self):
        r = regime._classificar_tf(_serie_alta(), "1H")
        assert isinstance(r["adx"], (int, float))
        assert isinstance(r["atr_ratio"], (int, float))
        assert r["adx"] > 0

    def test_volatilidade_classificada_como_volatilidade(self):
        r = regime._classificar_tf(_serie_volatilidade(), "1H")
        assert r["regime"] == "VOLATILIDADE"
        # ATR ratio deve exceder o limiar extremo.
        assert r["atr_ratio"] > regime.ATR_EXTREMO


# ===========================================================================
# _adx — Average Directional Index
# ===========================================================================


class TestADX:

    def test_serie_suficiente_retorna_valor_positivo(self):
        s = _serie_alta()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert vals  # lista não vazia
        assert vals[-1] > 0

    def test_tamanho_igual_ao_input(self):
        s = _serie_alta()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert len(vals) == len(s["fechamento"])

    def test_serie_curta_nao_lanca_e_retorna_zeros(self):
        # n < periodo + 2 -> retorna [0.0]*n sem exceção.
        maximas = [3.0, 4.0, 5.0]
        minimas = [1.0, 2.0, 3.0]
        fechamentos = [2.0, 3.0, 4.0]
        vals = regime._adx(maximas, minimas, fechamentos, 14)
        assert vals == [0.0, 0.0, 0.0]

    def test_serie_curta_tamanho_preservado(self):
        n = 10
        vals = regime._adx([2.0] * n, [1.0] * n, [1.5] * n, 14)
        assert len(vals) == n
        assert all(x == 0.0 for x in vals)

    def test_tendencia_forte_adx_alto(self):
        # Tendência limpa e forte deve gerar ADX elevado.
        s = _serie_alta()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert vals[-1] >= regime.ADX_TENDENCIA


# ===========================================================================
# detectar — veredicto consolidado multi-timeframe
# ===========================================================================


class TestDetectar:

    def test_estrutura_de_retorno(self, monkeypatch):
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        for chave in ("regime_final", "pode_operar", "motivo", "score", "votos", "detalhes_tf"):
            assert chave in r

    def test_detalhes_tf_tem_os_tres_timeframes(self, monkeypatch):
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert set(r["detalhes_tf"].keys()) == {"1h", "4h", "1d"}

    def test_score_e_numerico_nao_negativo(self, monkeypatch):
        # NOTA (comportamento REAL): o score NÃO é clampado em 100. Ele soma
        # score_adx (cap 50) + score_tf (votos/3*50). Como o voto total é 4
        # (4H peso duplo), um consenso total dá votos=4 -> score_tf=66.7, logo
        # o score pode ultrapassar 100 (ex.: 117). Os testes refletem o fonte:
        # garantimos apenas que é um inteiro não-negativo.
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert isinstance(r["score"], int)
        assert r["score"] >= 0

    def test_score_lateral_dentro_de_0_100(self, monkeypatch):
        # Em cenário lateral (ADX baixo, consenso parcial) o score fica na
        # faixa nominal 0-100 esperada.
        mapa = {"1h": _serie_lateral(), "4h": _serie_lateral(), "1d": _serie_lateral()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert 0 <= r["score"] <= 100

    def test_votos_somam_quatro(self, monkeypatch):
        # O voto é [1h, 4h, 4h, 1d] -> 4 votos no total (4H tem peso duplo).
        mapa = {"1h": _serie_alta(), "4h": _serie_lateral(), "1d": _serie_baixa()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert sum(r["votos"].values()) == 4

    def test_consenso_alta(self, monkeypatch):
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "TENDENCIA_ALTA"
        assert r["pode_operar"] is True

    def test_consenso_baixa_pode_operar(self, monkeypatch):
        mapa = {"1h": _serie_baixa(), "4h": _serie_baixa(), "1d": _serie_baixa()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "TENDENCIA_BAIXA"
        assert r["pode_operar"] is True

    def test_lateral_nao_pode_operar(self, monkeypatch):
        mapa = {"1h": _serie_lateral(), "4h": _serie_lateral(), "1d": _serie_lateral()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "LATERAL"
        assert r["pode_operar"] is False

    def test_dados_none_resulta_indefinido(self, monkeypatch):
        # _klines devolve None para todos os TFs -> classificação INDEFINIDO.
        monkeypatch.setattr(regime, "_klines", lambda i, l=60: None)
        r = regime.detectar()
        assert r["regime_final"] == "INDEFINIDO"
        assert r["pode_operar"] is False
        assert r["detalhes_tf"]["1h"]["regime"] == "INDEFINIDO"

    # --- VOLATILIDADE ------------------------------------------------------

    def test_volatilidade_tem_prioridade_e_bloqueia_operacao(self, monkeypatch):
        # Basta UM timeframe em VOLATILIDADE para travar tudo (prioridade absoluta).
        mapa = {"1h": _serie_volatilidade(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "VOLATILIDADE"
        assert r["pode_operar"] is False

    # --- REGRESSÃO: 1D participa do voto (correção M-1) --------------------

    def test_regressao_1d_presente_nos_detalhes(self, monkeypatch):
        mapa = {"1h": _serie_alta(), "4h": _serie_lateral(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert "1d" in r["detalhes_tf"]
        assert r["detalhes_tf"]["1d"]["regime"] == "TENDENCIA_ALTA"

    def test_regressao_1d_decide_o_voto(self, monkeypatch):
        """
        Cenário onde 1D é decisivo. Voto = [1h, 4h, 4h, 1d].

        Com 1H=ALTA, 4H=LATERAL, 1D=ALTA:
          votos -> ALTA=2 (1h + 1d), LATERAL=2 (4h x2)
          ALTA atinge o quórum (>=2) ANTES de LATERAL na cascata de ifs,
          logo regime_final = TENDENCIA_ALTA e pode_operar = True.

        Se o 1D fosse ignorado (bug M-1, voto = [1h, 4h, 4h]):
          votos -> ALTA=1, LATERAL=2 -> regime_final = LATERAL (não operaria).

        O contrafactual (test abaixo) confirma a inversão.
        """
        mapa = {"1h": _serie_alta(), "4h": _serie_lateral(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["votos"]["TENDENCIA_ALTA"] == 2
        assert r["regime_final"] == "TENDENCIA_ALTA"
        assert r["pode_operar"] is True

    def test_regressao_1d_contrafactual_inverte_resultado(self, monkeypatch):
        """
        Mesmo 1H e 4H do teste anterior, mas trocando 1D=ALTA por 1D=LATERAL.
        Como 1D agora vota LATERAL, o resultado muda para LATERAL (não opera),
        provando que o voto do 1D realmente influencia o veredicto final.
        """
        mapa = {"1h": _serie_alta(), "4h": _serie_lateral(), "1d": _serie_lateral()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["votos"]["LATERAL"] == 3
        assert r["regime_final"] == "LATERAL"
        assert r["pode_operar"] is False

    def test_sem_rede_klines_nao_e_chamado_de_verdade(self, monkeypatch):
        # Garante que detectar() usa o substituto (hermético) e não a Binance.
        chamadas = []

        def fake(intervalo, limite=60):
            chamadas.append(intervalo)
            return _serie_alta()

        monkeypatch.setattr(regime, "_klines", fake)
        regime.detectar()
        # detectar() consulta exatamente os 3 timeframes.
        assert chamadas == ["1h", "4h", "1d"]


# ===========================================================================
# Geradores sintéticos auxiliares (cobertura adversarial — @Delta)
# ===========================================================================


def _serie_plana(n=N):
    """Série totalmente plana: TR=0 em todas as velas.

    Exercita os ramos de divisão-por-zero defensivos: ATR média 0 -> atr_ratio
    cai para o fallback 1.0; DI/DX = 0 -> ADX = 0; EMAs coincidem -> direcao
    NEUTRO -> regime LATERAL.
    """
    f = [100.0] * n
    return {"abertura": f, "maxima": f, "minima": f, "fechamento": f, "volume": [1000.0] * n}


def _serie_volume_crescente(n=N):
    """Tendência de alta com volume estritamente crescente (>= 20 velas)."""
    f = [100.0 + i for i in range(n)]
    h = [x + 1.0 for x in f]
    l = [x - 1.0 for x in f]
    v = [1000.0 + 50.0 * i for i in range(n)]
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _serie_volume_decrescente(n=N):
    """Tendência de alta com volume DEcrescente -> vol_crescente False."""
    f = [100.0 + i for i in range(n)]
    h = [x + 1.0 for x in f]
    l = [x - 1.0 for x in f]
    v = [5000.0 - 50.0 * i for i in range(n)]
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


def _serie_curta(n=15):
    """Série de alta com menos de 20 velas (atalhos vol_media / ema200)."""
    f = [100.0 + i for i in range(n)]
    h = [x + 1.0 for x in f]
    l = [x - 1.0 for x in f]
    v = [1000.0] * n
    return {"abertura": f, "maxima": h, "minima": l, "fechamento": f, "volume": v}


# ===========================================================================
# _adx — bordas e ramos defensivos
# ===========================================================================


class TestADXBordas:

    def test_limiar_exato_periodo_mais_um_retorna_zeros(self):
        # n == periodo + 1 (15) ainda é < periodo + 2 -> ramo de série curta.
        n = 15
        vals = regime._adx([2.0] * n, [1.0] * n, [1.5] * n, 14)
        assert vals == [0.0] * n

    def test_limiar_exato_periodo_mais_dois_computa(self):
        # n == periodo + 2 (16): primeiro tamanho que NÃO cai no ramo curto.
        n = 16
        vals = regime._adx([2.0] * n, [1.0] * n, [1.5] * n, 14)
        # Tamanho preservado e sem exceção; série plana -> ADX 0.
        assert len(vals) == n
        assert vals[-1] == 0.0

    def test_serie_plana_adx_zero_sem_divisao_por_zero(self):
        # TR/DM todos 0 -> atr_s 0 -> di 0 -> soma 0 -> dx 0 -> ADX 0.
        s = _serie_plana()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert len(vals) == len(s["fechamento"])
        assert vals[-1] == 0.0
        assert all(x == 0.0 for x in vals)

    def test_serie_vazia_retorna_lista_vazia(self):
        # n == 0 < periodo+2 -> [0.0]*0 == [].
        assert regime._adx([], [], [], 14) == []

    def test_padding_no_inicio_e_zero(self):
        # Os primeiros valores (antes do ADX começar) são padding 0.0.
        s = _serie_alta()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert vals[0] == 0.0

    def test_baixa_tambem_gera_adx_forte(self):
        # Simetria: tendência de baixa limpa também produz ADX >= limiar.
        s = _serie_baixa()
        vals = regime._adx(s["maxima"], s["minima"], s["fechamento"], 14)
        assert vals[-1] >= regime.ADX_TENDENCIA


# ===========================================================================
# _classificar_tf — ramos internos (EMA, volume, ATR, direção)
# ===========================================================================


class TestClassificarTFRamos:

    def test_serie_plana_atr_ratio_fallback_um(self):
        # atr_media == 0 -> atr_ratio = 1.0 (não estoura ZeroDivisionError).
        r = regime._classificar_tf(_serie_plana(), "1H")
        assert r["atr_ratio"] == 1.0
        assert r["direcao"] == "NEUTRO"
        assert r["regime"] == "LATERAL"

    def test_ema200_fallback_para_ema50_em_serie_de_50(self):
        # len(f) == 50 -> NÃO entra em len(f) > 50 -> ema200 = ema50.
        r = regime._classificar_tf(_serie_alta(50), "1H")
        assert r["ema200"] == r["ema50"]

    def test_ema200_distinta_em_serie_longa(self):
        # len(f) == 60 > 50 -> ema200 calculada com min(200, n-1) -> distinta.
        r = regime._classificar_tf(_serie_alta(60), "1H")
        assert r["ema200"] != r["ema50"]

    def test_volume_crescente_true(self):
        r = regime._classificar_tf(_serie_volume_crescente(), "1H")
        assert r["vol_crescente"] is True

    def test_volume_decrescente_false(self):
        r = regime._classificar_tf(_serie_volume_decrescente(), "1H")
        assert r["vol_crescente"] is False

    def test_serie_curta_nao_lanca(self):
        # < 20 velas: vol_media usa v[-1]; ema200 cai no fallback. Sem exceção.
        r = regime._classificar_tf(_serie_curta(15), "1H")
        assert r["regime"] in {"TENDENCIA_ALTA", "TENDENCIA_BAIXA", "LATERAL", "VOLATILIDADE"}
        assert r["label"] == "1H"

    def test_preco_propagado_e_ultimo_fechamento(self):
        s = _serie_alta()
        r = regime._classificar_tf(s, "1H")
        assert r["preco"] == s["fechamento"][-1]

    def test_volatilidade_atr_ratio_excede_limiar(self):
        r = regime._classificar_tf(_serie_volatilidade(), "1H")
        assert r["atr_ratio"] > regime.ATR_EXTREMO
        assert r["regime"] == "VOLATILIDADE"


# ===========================================================================
# detectar — score, prioridades e o ramo INDEFINIDO
# ===========================================================================


class TestDetectarRamos:

    def test_score_adx_capado_em_50(self, monkeypatch):
        # Consenso de alta limpa -> ADX ~100 -> score_adx no teto (50).
        # score_tf = votos(4)/3*50 = 66.7 -> score total 117 (sem clamp).
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["score"] == 117
        # Confirma matematicamente o teto do componente ADX.
        adx_medio = (r["detalhes_tf"]["1h"]["adx"] + r["detalhes_tf"]["4h"]["adx"]) / 2
        score_adx = min(adx_medio / regime.ADX_FORTE * 50, 50)
        assert score_adx == 50

    def test_volatilidade_no_1d_tem_prioridade(self, monkeypatch):
        # VOLATILIDADE em QUALQUER timeframe (aqui o 1D) bloqueia tudo.
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_volatilidade()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "VOLATILIDADE"
        assert r["pode_operar"] is False
        assert "ATR" in r["motivo"]

    def test_volatilidade_no_4h_tem_prioridade(self, monkeypatch):
        mapa = {"1h": _serie_alta(), "4h": _serie_volatilidade(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["regime_final"] == "VOLATILIDADE"
        assert r["pode_operar"] is False

    def test_4h_peso_duplo_decide_sozinho(self, monkeypatch):
        # 4H=BAIXA (peso 2) com 1H=ALTA e 1D=LATERAL: BAIXA atinge quórum.
        # Demonstra que o 4H sozinho carrega o veredicto via peso duplo.
        mapa = {"1h": _serie_alta(), "4h": _serie_baixa(), "1d": _serie_lateral()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert r["votos"]["TENDENCIA_BAIXA"] == 2
        assert r["regime_final"] == "TENDENCIA_BAIXA"

    def test_indefinido_so_quando_todos_klines_none(self, monkeypatch):
        # INDEFINIDO real: nenhum TF vota (todos viram INDEFINIDO, não contados).
        monkeypatch.setattr(regime, "_klines", lambda i, l=60: None)
        r = regime.detectar()
        assert r["regime_final"] == "INDEFINIDO"
        assert sum(r["votos"].values()) == 0
        assert r["score"] == 0
        assert "Conflito" in r["motivo"]

    def test_um_klines_none_nao_quebra_detectar(self, monkeypatch):
        # Apenas o 1H falha (None); 4H e 1D mantêm consenso de alta.
        def fake(intervalo, limite=60):
            if intervalo == "1h":
                return None
            return _serie_alta()

        monkeypatch.setattr(regime, "_klines", fake)
        r = regime.detectar()
        assert r["detalhes_tf"]["1h"]["regime"] == "INDEFINIDO"
        # 4H (peso 2) + 1D -> ALTA com quórum.
        assert r["regime_final"] == "TENDENCIA_ALTA"

    def test_votos_contam_todos_regimes(self, monkeypatch):
        # As quatro chaves de voto existem sempre, mesmo zeradas.
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.detectar()
        assert set(r["votos"].keys()) == {
            "TENDENCIA_ALTA",
            "TENDENCIA_BAIXA",
            "LATERAL",
            "VOLATILIDADE",
        }


# ===========================================================================
# _klines — herméticos: requests é mockado, ZERO rede
# ===========================================================================


class TestKlines:
    # P1-5: o fetch/parse/cache real agora vive em data/klines.py (com sua
    # própria suíte, tests/test_klines.py) — aqui só travamos que
    # regime._klines() delega para lá com os args corretos e repassa o
    # retorno (incl. None) sem alterar.

    def test_delega_para_data_klines_com_symbol_e_args_corretos(self, monkeypatch):
        capturado = {}

        def _fake_obter_klines(symbol, intervalo, limite):
            capturado["args"] = (symbol, intervalo, limite)
            return {"fechamento": [1.0, 2.0]}

        monkeypatch.setattr(regime, "obter_klines", _fake_obter_klines)
        d = regime._klines("1d", 4)
        assert capturado["args"] == (regime.SYMBOL, "1d", 4)
        assert d == {"fechamento": [1.0, 2.0]}

    def test_repassa_none_sem_alterar(self, monkeypatch):
        monkeypatch.setattr(regime, "obter_klines", lambda *a, **k: None)
        assert regime._klines("1h", 60) is None


# ===========================================================================
# imprimir — caminho de saída (cobre o mapa de cores e o retorno)
# ===========================================================================


class TestImprimir:

    def test_imprimir_retorna_dict_de_detectar(self, monkeypatch, capsys):
        mapa = {"1h": _serie_alta(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.imprimir()
        out = capsys.readouterr().out
        assert r["regime_final"] == "TENDENCIA_ALTA"
        assert "DETECCAO DE REGIME DE MERCADO" in out
        assert "Pode Operar" in out
        assert "TENDENCIA_ALTA" in out

    def test_imprimir_cobre_cor_indefinido(self, monkeypatch, capsys):
        # Caminho INDEFINIDO usa a cor cinza (\033[90m) — exercita o ramo.
        monkeypatch.setattr(regime, "_klines", lambda i, l=60: None)
        r = regime.imprimir()
        out = capsys.readouterr().out
        assert r["regime_final"] == "INDEFINIDO"
        assert "INDEFINIDO" in out

    def test_imprimir_cobre_cor_volatilidade(self, monkeypatch, capsys):
        mapa = {"1h": _serie_volatilidade(), "4h": _serie_alta(), "1d": _serie_alta()}
        monkeypatch.setattr(regime, "_klines", _mock_klines(mapa))
        r = regime.imprimir()
        out = capsys.readouterr().out
        assert r["regime_final"] == "VOLATILIDADE"
        assert "VOLATILIDADE" in out
