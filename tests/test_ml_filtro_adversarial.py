"""
Suite adversarial complementar para ml_filtro.py (@Delta).

NAO substitui tests/test_ml_filtro.py — apenas ADICIONA cobertura de bordas,
branches de fallback e regressao. Mesmo contrato hermetico: SEM rede, SEM banco,
SEM treino real. Todo I/O (sqlite3, requests, open, pickle) e mockado.

Foco:
  * extrair_features: branches de fallback (rsi/atr/vr/bw None ou falsy),
    bb_pos degenerado, limiares de indice (54/55/56), correcao das variacoes
    1h/4h/24h e robustez com series degeneradas/constantes.
  * preparar_dataset: limiar exato de 100 linhas (99 vs 100), label 0 em
    tendencia de baixa, repasse correto dos parametros SQL (symbol/intervalo),
    formato/dtype do retorno.
  * prever: fallback de legado (data/modelo_xgb.pkl) para BTCUSDT, ausencia de
    fallback para outros simbolos, intervalo default repassado a Binance,
    arredondamento a 4 casas, parametros/timeout da requisicao, ausencia de
    chamadas de rede quando features insuficientes.
  * regressao: volume_relativo (sem IndexError), bollinger (sem NameError),
    extrair_features e prever nunca propagam excecao em entradas validas.
"""

import os
import sys
import math
from unittest import mock

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import ml_filtro  # noqa: E402
import indicadores as ind  # noqa: E402


# ---------------------------------------------------------------------------
# Geradores deterministicos
# ---------------------------------------------------------------------------

def _series(n=120, base=100.0, passo=0.5):
    f, m, mn, v = [], [], [], []
    for i in range(n):
        preco = base + passo * i + 2.0 * math.sin(i / 3.0)
        f.append(float(preco))
        m.append(float(preco + 1.5))
        mn.append(float(preco - 1.5))
        v.append(float(1000 + (i % 7) * 50))
    return f, m, mn, v


def _series_constante(n=120, preco=100.0, banda=1.0, vol=1000.0):
    """Serie totalmente plana: forca os branches de fallback (rsi falsy, bw 0)."""
    f = [float(preco)] * n
    m = [float(preco + banda)] * n
    mn = [float(preco - banda)] * n
    v = [float(vol)] * n
    return f, m, mn, v


def _series_descendente(n=120, base=1000.0, passo=2.0):
    f = [base - passo * i for i in range(n)]
    m = [x + 1.0 for x in f]
    mn = [x - 1.0 for x in f]
    v = [1000.0 + (i % 5) * 10 for i in range(n)]
    return f, m, mn, v


def _klines_de(f, m, mn, v):
    """Converte series para o formato bruto da Binance (lista de strings)."""
    return [[0, 0, str(m[i]), str(mn[i]), str(f[i]), str(v[i])] for i in range(len(f))]


def _fake_conn(rows):
    conn = mock.Mock()
    cursor = mock.Mock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    conn.close.return_value = None
    return conn


# ===========================================================================
# extrair_features — limiares de indice exatos
# ===========================================================================

def test_extrair_features_indice_56_retorna_lista():
    # 56 e o primeiro indice acima do limiar 55 (com 1 vela de folga).
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 56)
    assert feat is not None
    assert len(feat) == 11


def test_extrair_features_indice_54_exato_retorna_none():
    f, m, mn, v = _series(120)
    assert ml_filtro.extrair_features(f, m, mn, v, 54) is None


def test_extrair_features_indice_negativo_retorna_none():
    # i < 55 abrange negativos: deve cair no guard inicial sem indexar fora.
    f, m, mn, v = _series(120)
    assert ml_filtro.extrair_features(f, m, mn, v, -1) is None


# ===========================================================================
# extrair_features — branches de fallback (serie constante)
# ===========================================================================

def test_extrair_features_serie_constante_nao_lanca_e_tem_11():
    f, m, mn, v = _series_constante(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert feat is not None
    assert len(feat) == 11
    for x in feat:
        assert isinstance(x, (int, float, np.floating, np.integer))
        assert not (isinstance(x, float) and math.isnan(float(x)))


def test_extrair_features_rsi_fallback_50_em_serie_constante():
    # RSI de serie plana = 0.0 (falsy) -> branch `or 50` -> feat[2] = 50/100.
    f, m, mn, v = _series_constante(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert float(feat[2]) == pytest.approx(0.5, abs=1e-9)


def test_extrair_features_variacoes_zero_em_serie_constante():
    # var_1h/4h/24h (indices 7,8,9) sao zero quando o preco nao muda.
    f, m, mn, v = _series_constante(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert float(feat[7]) == pytest.approx(0.0, abs=1e-12)
    assert float(feat[8]) == pytest.approx(0.0, abs=1e-12)
    assert float(feat[9]) == pytest.approx(0.0, abs=1e-12)


def test_extrair_features_dist_ema_zero_em_serie_constante():
    # preco == ema -> distancias relativas (indices 0 e 1) = 0.
    f, m, mn, v = _series_constante(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert float(feat[0]) == pytest.approx(0.0, abs=1e-9)
    assert float(feat[1]) == pytest.approx(0.0, abs=1e-9)


def test_extrair_features_bb_pos_dentro_de_0_e_1():
    # Posicao no Bollinger deve permanecer no intervalo fechado [0, 1].
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert 0.0 <= float(feat[10]) <= 1.0


def test_extrair_features_atr_relativo_positivo():
    # feat[3] = atr_val / atr_med; ambos > 0 em serie nao-degenerada.
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert float(feat[3]) > 0.0


def test_extrair_features_var_1h_corresponde_ao_calculo_real():
    # Confere a aritmetica de var_1 = (f[-1]-f[-2])/f[-2] no indice escolhido.
    f, m, mn, v = _series(120)
    i = 100
    esperado = (f[i] - f[i - 1]) / f[i - 1]
    feat = ml_filtro.extrair_features(f, m, mn, v, i)
    assert float(feat[7]) == pytest.approx(esperado, rel=1e-9)


def test_extrair_features_var_24h_corresponde_ao_calculo_real():
    f, m, mn, v = _series(120)
    i = 100
    esperado = (f[i] - f[i - 24]) / f[i - 24]
    feat = ml_filtro.extrair_features(f, m, mn, v, i)
    assert float(feat[9]) == pytest.approx(esperado, rel=1e-9)


def test_extrair_features_volume_relativo_normalizado_nao_negativo():
    # feat[4] = min(vr,5)/5 -> em [0, 1], nunca negativo.
    f, m, mn, v = _series(120)
    feat = ml_filtro.extrair_features(f, m, mn, v, 100)
    assert 0.0 <= float(feat[4]) <= 1.0


# ===========================================================================
# preparar_dataset — limiar exato de 100 linhas
# ===========================================================================

def test_preparar_dataset_99_linhas_retorna_none():
    rows = [(100.0, 101.0, 99.0, 1000.0)] * 99
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    assert X is None and y is None


def test_preparar_dataset_exatamente_100_linhas_processa():
    # 100 nao e < 100 -> deve seguir; range(55, 100-JANELA) = 37 amostras.
    f, m, mn, v = _series(100)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(100)]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    assert X is not None and y is not None
    esperado = len(f) - ml_filtro.JANELA - 55
    assert X.shape[0] == esperado == 37
    assert X.shape[1] == 11


def test_preparar_dataset_label_0_em_tendencia_de_baixa():
    f, m, mn, v = _series_descendente(160)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    # Mercado caindo: nunca atinge +1.5% futuro -> todos os labels 0.
    assert y.shape[0] > 0
    assert y.sum() == 0


def test_preparar_dataset_repassa_symbol_e_intervalo_no_sql():
    f, m, mn, v = _series(200)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    conn = _fake_conn(rows)
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=conn):
        ml_filtro.preparar_dataset("4h", "ETHUSDT")
    # execute(query, (symbol, intervalo)) — confere a tupla de parametros.
    args, _ = conn.execute.call_args
    assert args[1] == ("ETHUSDT", "4h")


def test_preparar_dataset_usa_db_path_do_modulo():
    f, m, mn, v = _series(200)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)) as conn_mock:
        ml_filtro.preparar_dataset("1h", "BTCUSDT")
    conn_mock.assert_called_once_with(ml_filtro.DB_PATH)


def test_preparar_dataset_dtype_numerico():
    f, m, mn, v = _series(200)
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    with mock.patch.object(ml_filtro.sqlite3, "connect", return_value=_fake_conn(rows)):
        X, y = ml_filtro.preparar_dataset("1h", "BTCUSDT")
    assert np.issubdtype(X.dtype, np.floating)
    assert X.ndim == 2 and y.ndim == 1


# ===========================================================================
# prever — fallback de legado (data/modelo_xgb.pkl)
# ===========================================================================

def test_prever_btcusdt_usa_legado_quando_especifico_ausente():
    f, m, mn, v = _series(120)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    modelo.predict_proba.return_value = np.array([[0.4, 0.6]])
    artefato = {"modelo": modelo}  # sem chave 'intervalo' -> default '1h'
    resp = mock.Mock()
    resp.json.return_value = klines

    def fake_exists(p):
        return p == "data/modelo_xgb.pkl"  # so o legado existe

    with mock.patch.object(ml_filtro.os.path, "exists", side_effect=fake_exists), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp) as get_mock:
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert msg == "OK"
    assert prob == pytest.approx(0.6, abs=1e-6)
    # intervalo default '1h' precisa ter sido enviado a Binance.
    assert get_mock.call_args.kwargs["params"]["interval"] == "1h"


def test_prever_nao_btcusdt_ignora_legado():
    # Para simbolos != BTCUSDT, mesmo com legado presente, nao ha fallback.
    def fake_exists(p):
        return p == "data/modelo_xgb.pkl"

    with mock.patch.object(ml_filtro.os.path, "exists", side_effect=fake_exists), \
         mock.patch.object(ml_filtro.requests, "get") as get_mock:
        prob, msg = ml_filtro.prever("ETHUSDT")

    assert prob is None
    assert "nao treinado" in msg.lower()
    assert "ETHUSDT" in msg
    get_mock.assert_not_called()


# ===========================================================================
# prever — repasse correto de parametros e arredondamento
# ===========================================================================

def test_prever_envia_symbol_interval_limit_e_timeout():
    f, m, mn, v = _series(120)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    modelo.predict_proba.return_value = np.array([[0.5, 0.5]])
    artefato = {"modelo": modelo, "intervalo": "15m"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with mock.patch.object(ml_filtro.os.path, "exists", return_value=True), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp) as get_mock:
        ml_filtro.prever("SOLUSDT")

    kwargs = get_mock.call_args.kwargs
    assert kwargs["params"] == {"symbol": "SOLUSDT", "interval": "15m", "limit": 100}
    assert kwargs["timeout"] == 8
    # URL de klines da Binance.
    url = get_mock.call_args.args[0]
    assert url.endswith("/api/v3/klines")
    assert url.startswith(ml_filtro.BASE_URL)


def test_prever_arredonda_probabilidade_para_4_casas():
    f, m, mn, v = _series(120)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    # 0.123456789 deve virar 0.1235 (round a 4 casas).
    modelo.predict_proba.return_value = np.array([[1 - 0.123456789, 0.123456789]])
    artefato = {"modelo": modelo, "intervalo": "1h"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with mock.patch.object(ml_filtro.os.path, "exists", return_value=True), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp):
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert msg == "OK"
    assert prob == 0.1235
    assert isinstance(prob, float)


def test_prever_passa_feature_unica_ao_modelo():
    # O modelo recebe exatamente uma amostra com 11 features.
    f, m, mn, v = _series(120)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    modelo.predict_proba.return_value = np.array([[0.2, 0.8]])
    artefato = {"modelo": modelo, "intervalo": "1h"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with mock.patch.object(ml_filtro.os.path, "exists", return_value=True), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp):
        ml_filtro.prever("BTCUSDT")

    modelo.predict_proba.assert_called_once()
    enviado = modelo.predict_proba.call_args.args[0]
    assert len(enviado) == 1
    assert len(enviado[0]) == 11


def test_prever_features_insuficientes_nao_chama_modelo():
    # < 56 velas -> extrair_features no ultimo indice e None; nao usa o modelo.
    f, m, mn, v = _series(30)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    artefato = {"modelo": modelo, "intervalo": "1h"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with mock.patch.object(ml_filtro.os.path, "exists", return_value=True), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp):
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert prob is None
    assert msg == "Features insuficientes"
    modelo.predict_proba.assert_not_called()


# ===========================================================================
# _model_path — robustez de casing/simbolos
# ===========================================================================

def test_model_path_simbolo_ja_minusculo():
    assert ml_filtro._model_path("solusdt") == "data/modelo_xgb_solusdt.pkl"


def test_model_path_constante_modulo_consistente():
    assert ml_filtro.MODEL_PATH == ml_filtro._model_path("BTCUSDT")
    assert ml_filtro.SYMBOL == "BTCUSDT"


# ===========================================================================
# Regressao — indicadores consumidos por extrair_features
# ===========================================================================

def test_regressao_volume_relativo_sem_indexerror_serie_exata():
    # Serie de tamanho == periodo: ultimo ponto tem media valida, sem IndexError.
    vols = [1000.0 + i for i in range(20)]
    out = ind.volume_relativo(vols, 20)
    assert len(out) == 20
    assert out[-1] is not None  # ultimo ponto calculado


def test_regressao_volume_relativo_serie_curta_so_none():
    # Serie menor que o periodo: tudo None, mas sem estourar.
    out = ind.volume_relativo([1000.0] * 5, 20)
    assert out == [None] * 19  # periodo-1 padding (loop nao executa)


def test_regressao_volume_relativo_alinhamento_correto():
    # Garante que media[i-(periodo-1)] indexa sem IndexError em serie longa.
    vols = [1000.0 + (i % 7) * 13 for i in range(60)]
    out = ind.volume_relativo(vols, 20)
    assert len(out) == 60
    assert all(out[i] is None for i in range(19))
    assert all(out[i] is not None for i in range(19, 60))


def test_regressao_bollinger_sem_nameerror():
    # bollinger nao deve referenciar nomes indefinidos; retorna 3 listas.
    f = [100.0 + math.sin(i / 4.0) for i in range(40)]
    up, mid, low = ind.bollinger(f, 20, 2)
    assert len(up) == len(mid) == len(low) == 40
    # ultimo ponto: ordenacao coerente upper >= mid >= lower.
    assert up[-1] >= mid[-1] >= low[-1]


def test_regressao_bandwidth_serie_constante_sem_crash():
    # bandwidth com series constantes (mid != 0) retorna 0 sem dividir por zero.
    f = [100.0] * 40
    up, mid, low = ind.bollinger(f, 20, 2)
    bw = ind.bandwidth(up, mid, low)
    assert len(bw) == 40
    assert bw[-1] == pytest.approx(0.0, abs=1e-12)


def test_regressao_extrair_features_nunca_lanca_em_series_validas():
    # Varre diferentes perfis de serie; nenhuma deve propagar excecao.
    for gen in (_series, _series_constante, _series_descendente):
        f, m, mn, v = gen(120)
        feat = ml_filtro.extrair_features(f, m, mn, v, len(f) - 1)
        assert feat is not None
        assert len(feat) == 11


def test_regressao_prever_nao_lanca_com_modelo_e_klines_validos():
    # 'analisar/prever sem crash': caminho feliz completo nao propaga excecao.
    f, m, mn, v = _series(120)
    klines = _klines_de(f, m, mn, v)
    modelo = mock.Mock()
    modelo.predict_proba.return_value = np.array([[0.55, 0.45]])
    artefato = {"modelo": modelo, "intervalo": "1h"}
    resp = mock.Mock()
    resp.json.return_value = klines

    with mock.patch.object(ml_filtro.os.path, "exists", return_value=True), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(ml_filtro.pickle, "load", return_value=artefato), \
         mock.patch.object(ml_filtro.requests, "get", return_value=resp):
        prob, msg = ml_filtro.prever("BTCUSDT")

    assert msg == "OK"
    assert 0.0 <= prob <= 1.0
