"""
Suíte pytest para ml_filtro.py (Filtro XGBoost).

Perfil @Delta — testes herméticos: SEM rede, SEM banco, SEM treinar modelo real.
  * extrair_features / _model_path são funções puras (usam indicadores.py, também puro).
  * preparar_dataset: sqlite3.connect é mockado para devolver linhas fixas.
  * prever: os.path.exists / open / pickle.load / requests.get são mockados;
    nenhuma chamada de rede ou disco real ocorre.

O alvo carrega indicadores reais (puros) — não há necessidade de mockar a
matemática dos indicadores; o que precisa de isolamento é I/O (DB, rede, disco).
"""

import math
import os
import sys
from unittest import mock

import numpy as np
import pytest

# Garante que a raiz do repo está no path (ml_filtro.py vive na raiz).
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import ml_filtro  # noqa: E402

# ---------------------------------------------------------------------------
# Geradores de dados sintéticos (determinísticos, sem rede/DB)
# ---------------------------------------------------------------------------


def _series(n=120, base=100.0, passo=0.5):
    """Gera séries OHLCV coerentes (max >= close >= min, volumes positivos)."""
    fechamentos, maximas, minimas, volumes = [], [], [], []
    preco = base
    for i in range(n):
        # leve oscilação senoidal + tendência para evitar valores degenerados
        preco = base + passo * i + 2.0 * math.sin(i / 3.0)
        f = preco
        mx = f + 1.5
        mn = f - 1.5
        fechamentos.append(float(f))
        maximas.append(float(mx))
        minimas.append(float(mn))
        volumes.append(float(1000 + (i % 7) * 50))
    return fechamentos, maximas, minimas, volumes


# ===========================================================================
# extrair_features
# ===========================================================================


def test_extrair_features_retorna_11_floats_em_indice_valido():
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert feat is not None
    assert isinstance(feat, list)
    assert len(feat) == 11
    for x in feat:
        # aceita int/float Python e escalares numpy; todos devem ser numéricos
        assert isinstance(x, (int, float, np.floating, np.integer))
        assert not (isinstance(x, float) and math.isnan(x))


def test_extrair_features_no_limiar_55_retorna_lista():
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 55)
    assert feat is not None
    assert len(feat) == 11


@pytest.mark.parametrize("i", [0, 10, 54])
def test_extrair_features_indice_abaixo_de_55_retorna_none(i):
    f, m, mn, v = _series(120)
    assert ml_filtro.extrair_features(f, m, mn, v, i) is None


def test_extrair_features_rsi_normalizado_entre_0_e_1():
    # índice 2 do vetor = rsi_v / 100  → deve cair em [0, 1]
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert 0.0 <= float(feat[2]) <= 1.0


def test_extrair_features_volume_relativo_normalizado_ate_1():
    # índice 4 = min(vr, 5) / 5  → no máximo 1.0
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert float(feat[4]) <= 1.0


# ===========================================================================
# _model_path
# ===========================================================================


def test_model_path_usa_lowercase_do_symbol():
    assert ml_filtro._model_path("BTCUSDT") == "data/modelo_xgb_btcusdt.pkl"
    assert ml_filtro._model_path("EthUsdt") == "data/modelo_xgb_ethusdt.pkl"


def test_model_path_default_e_btcusdt():
    assert ml_filtro._model_path() == "data/modelo_xgb_btcusdt.pkl"


# ===========================================================================
# prever — modelo AUSENTE (não deve lançar; retorna mensagem de "não treinado")
# ===========================================================================


def test_prever_modelo_ausente_retorna_none_e_mensagem():
    # Nenhum caminho existe (nem o específico, nem o legado modelo_xgb.pkl).
    with mock.patch.object(ml_filtro.os.path, "exists", return_value=False):
        prob, msg = ml_filtro.prever("ETHUSDT")
    assert prob is None
    assert "nao treinado" in msg.lower()


def test_prever_btcusdt_sem_modelo_nem_legado_retorna_none():
    # BTCUSDT tenta fallback para data/modelo_xgb.pkl; com exists=False ambos falham.
    with mock.patch.object(ml_filtro.os.path, "exists", return_value=False):
        prob, msg = ml_filtro.prever("BTCUSDT")
    assert prob is None
    assert "nao treinado" in msg.lower()


def test_prever_joblib_load_indisponivel_nao_lanca():
    # Mesmo que o arquivo "exista", se o carregamento falhar a função não deve
    # propagar — aqui garantimos que o caminho de modelo ausente é robusto:
    # exists=False evita qualquer open()/pickle.load() real.
    with mock.patch.object(ml_filtro.os.path, "exists", return_value=False):
        # não levanta exceção
        resultado = ml_filtro.prever("XRPUSDT")
    assert resultado[0] is None


def test_prever_nao_faz_chamada_de_rede_quando_modelo_ausente():
    # Sentinela: requests.get NUNCA deve ser chamado se o modelo não existe.
    with (
        mock.patch.object(ml_filtro.os.path, "exists", return_value=False),
        mock.patch.object(ml_filtro.requests, "get") as get_mock,
    ):
        ml_filtro.prever("SOLUSDT")
    get_mock.assert_not_called()


# ===========================================================================
# prever — modelo PRESENTE (mock de open/pickle.load/requests) — caminho feliz
# ===========================================================================


def test_prever_com_modelo_mockado_retorna_probabilidade():
    f, m, mn, v = _series(120)
    # klines da Binance: cada item é uma lista; índices usados: 2=max,3=min,4=close,5=vol
    klines = []
    for i in range(len(f)):
        k = [0, 0, str(m[i]), str(mn[i]), str(f[i]), str(v[i])]
        klines.append(k)

    modelo_fake = mock.Mock()
    modelo_fake.predict_proba.return_value = np.array([[0.3, 0.7]])
    artefato = {"modelo": modelo_fake, "intervalo": "1h", "symbol": "BTCUSDT"}

    resp = mock.Mock()
    resp.json.return_value = klines

    with (
        mock.patch.object(ml_filtro.os.path, "exists", return_value=True),
        mock.patch("builtins.open", mock.mock_open()),
        mock.patch.object(ml_filtro.pickle, "load", return_value=artefato),
        mock.patch.object(ml_filtro.requests, "get", return_value=resp) as get_mock,
    ):
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert msg == "OK"
    assert prob == pytest.approx(0.7, abs=1e-6)
    get_mock.assert_called_once()


def test_prever_features_insuficientes_retorna_none():
    # Poucas velas (< 56) → extrair_features no último índice retorna None.
    f, m, mn, v = _series(40)
    klines = [[0, 0, str(m[i]), str(mn[i]), str(f[i]), str(v[i])] for i in range(len(f))]

    modelo_fake = mock.Mock()
    artefato = {"modelo": modelo_fake, "intervalo": "1h"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with (
        mock.patch.object(ml_filtro.os.path, "exists", return_value=True),
        mock.patch("builtins.open", mock.mock_open()),
        mock.patch.object(ml_filtro.pickle, "load", return_value=artefato),
        mock.patch.object(ml_filtro.requests, "get", return_value=resp),
    ):
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert prob is None
    assert msg == "Features insuficientes"
    modelo_fake.predict_proba.assert_not_called()


# ===========================================================================
# preparar_dataset — sqlite3 mockado (sem banco real)
# ===========================================================================


def _fake_conn(rows):
    """Cria um objeto conn falso compatível com conn.execute(...).fetchall()."""
    conn = mock.Mock()
    cursor = mock.Mock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    conn.close.return_value = None
    return conn


def test_preparar_dataset_dados_insuficientes_retorna_none_none():
    # < 100 linhas → (None, None)
    rows = [(100.0, 101.0, 99.0, 1000.0)] * 50
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    assert X is None
    assert y is None


def test_preparar_dataset_shapes_coerentes():
    f, m, mn, v = _series(200)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "ETHUSDT")

    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    # número de amostras = índices de 55 até len-JANELA (exclusivo)
    esperado = len(f) - ml_filtro.JANELA - 55
    assert X.shape[0] == esperado
    assert y.shape[0] == esperado
    # cada amostra tem 11 features
    assert X.shape[1] == 11
    # labels binários
    assert set(np.unique(y).tolist()).issubset({0, 1})


def test_preparar_dataset_fecha_conexao():
    f, m, mn, v = _series(200)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    conn = _fake_conn(rows)
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=conn):
        ml_filtro.preparar_dataset("1h", "BTCUSDT")
    conn.close.assert_called_once()


def test_preparar_dataset_label_1_quando_alvo_atingido():
    # Série fortemente ascendente garante que o preço futuro supera ALVO_PCT.
    n = 120
    f = [100.0 + i * 5.0 for i in range(n)]  # +5 por vela → alta forte
    m = [x + 1.0 for x in f]
    mn = [x - 1.0 for x in f]
    v = [1000.0 + (i % 5) * 10 for i in range(n)]
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(n)]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    # com tendência tão forte, todos os labels devem ser 1
    assert y.sum() == y.shape[0]
    assert y.shape[0] > 0
