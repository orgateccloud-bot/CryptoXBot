"""
Suíte pytest para lstm_modelo.py (MLP sequencial — até 45% do peso do ensemble).

Perfil @Delta — testes herméticos: SEM rede, SEM banco, SEM treinar modelo real
(mesmo princípio do header de tests/test_ml_filtro.py):
  * preparar_sequencias: sqlite3.connect é mockado — o rótulo de barreira
    tripla é exercitado com séries construídas, nunca com o banco vivo.
  * promover_modelo: função pura — o gate é testável sem treinar nada
    (foi desenhada para isso, ver o docstring dela).
  * prever: os.path.exists / open / pickle.load / requests.get mockados.

Contexto (scorecard 2026-08-19, achado #2): até esta suíte o MLP treinava com
o rótulo PRÉ-E-10 (máximo de fechamentos futuros, sem barreira de stop — o
defeito que ml_filtro já tinha corrigido), servia com 100 velas onde a
paridade treino/serve exige CONTEXTO_MINIMO=300, e não tinha um único teste
direto. A matemática do rótulo em si já é coberta por tests/test_ml_rotulo.py;
aqui o que se prova é que o MLP USA aquele rótulo (a mesma função importada,
não uma cópia) e que a inferência pede o contexto que o treino viu.
"""

import inspect
import math
import os
import sys
from unittest import mock

import numpy as np
import pytest

# Garante que a raiz do repo está no path (lstm_modelo.py vive na raiz).
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import lstm_modelo  # noqa: E402
import ml_filtro  # noqa: E402

# Primeiro índice de vela que ganha rótulo: 55 velas para extrair_features
# devolver algo + SEQ_LEN features para achatar a primeira sequência.
PRIMEIRO_IDX_ROTULADO = 55 + lstm_modelo.SEQ_LEN

# ---------------------------------------------------------------------------
# Geradores de dados sintéticos (determinísticos, sem rede/DB)
# ---------------------------------------------------------------------------


def _serie_lateral(n: int = 250) -> tuple[list[float], list[float], list[float], list[float]]:
    """Série lateral (~100 ± 0,05): nenhuma barreira de ±1,5% é tocada."""
    fechamentos, maximas, minimas, volumes = [], [], [], []
    for i in range(n):
        f = 100.0 + 0.05 * math.sin(i / 3.0)
        fechamentos.append(f)
        maximas.append(f + 0.02)
        minimas.append(f - 0.02)
        volumes.append(float(1000 + (i % 7) * 50))
    return fechamentos, maximas, minimas, volumes


def _serie_ascendente(n: int = 250) -> tuple[list[float], list[float], list[float], list[float]]:
    """+5 por vela: o alvo é batido dentro da janela e o stop nunca é tocado."""
    fechamentos = [100.0 + 5.0 * i for i in range(n)]
    maximas = [f + 1.5 for f in fechamentos]
    minimas = [f - 1.5 for f in fechamentos]
    volumes = [float(1000 + (i % 5) * 10) for i in range(n)]
    return fechamentos, maximas, minimas, volumes


def _fake_conn(rows):
    """Objeto conn falso compatível com conn.execute(...).fetchall()."""
    conn = mock.Mock()
    cursor = mock.Mock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    conn.close.return_value = None
    return conn


def _preparar(f, m, mn, v):
    rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
    with mock.patch.object(lstm_modelo.sqlite3, "connect", return_value=_fake_conn(rows)):
        return lstm_modelo.preparar_sequencias("1h")


# ===========================================================================
# Rótulo — o MLP usa a barreira tripla de ml_filtro (a mesma função)
# ===========================================================================


class TestRotuloBarreiraTripla:
    def test_reusa_o_rotulador_do_ml_filtro_sem_duplicar(self):
        # Mesmo padrão de verificar_drift_e_registrar: a função é IMPORTADA,
        # não copiada — divergência futura entre XGBoost e MLP fica impossível.
        assert lstm_modelo.rotular_barreira_tripla is ml_filtro.rotular_barreira_tripla

    def test_preparar_sequencias_usa_o_rotulador_e_o_rotulo_antigo_sumiu(self):
        fonte = inspect.getsource(lstm_modelo.preparar_sequencias)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert "rotular_barreira_tripla(" in codigo
        # o rótulo antigo (max de fechamentos futuros) não pode ter sobrado
        assert "max(fechamentos[idx + 1" not in codigo

    def test_o_stop_vem_do_par_e_nao_de_um_numero_inventado(self):
        # A barreira inferior tem de ser o stop REAL (config/params_pares.py),
        # o mesmo que encerra a posição em produção — como em ml_filtro.
        fonte = inspect.getsource(lstm_modelo.preparar_sequencias)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert 'stop_pct = get_params(SYMBOL)["stop_pct"]' in codigo

    def test_stop_estourado_antes_do_alvo_e_negativo_no_dataset(self):
        # O caso do scorecard, através de preparar_sequencias: a vela seguinte
        # à entrada fura o stop (-3% intrabar) e SÓ DEPOIS o preço sobe +2%.
        # O rótulo antigo via `max(fechamentos futuros) >= +1,5%` e marcava
        # POSITIVO; a barreira tripla marca o que a mesa viveu: STOP.
        f, m, mn, v = _serie_lateral(250)
        t = 150
        mn[t] = 97.0  # fura o stop de 1,5% (entrada ~100 -> stop ~98,5)
        f[t + 1] = 102.0  # o rali vem tarde demais para quem entrou antes
        m[t + 1] = 102.5

        X, y = _preparar(f, m, mn, v)

        entrada = f[t - 1]
        # sanidade: o rótulo ANTIGO diria positivo para a entrada em t-1
        assert max(f[t : t + lstm_modelo.JANELA_FUTURA + 1]) >= entrada * (
            1 + lstm_modelo.ALVO_PCT
        )
        # a barreira tripla diz: o trade morreu no stop antes do rali
        assert y[t - 1 - PRIMEIRO_IDX_ROTULADO] == 0
        # e quem entra DEPOIS do stop pega o rali — a ordem do caminho importa
        assert y[t - PRIMEIRO_IDX_ROTULADO] == 1

    def test_serie_lateral_e_toda_negativa_por_tempo(self):
        f, m, mn, v = _serie_lateral(250)
        X, y = _preparar(f, m, mn, v)
        assert y.shape[0] > 0
        assert y.sum() == 0

    def test_serie_ascendente_forte_rotula_positivo(self):
        f, m, mn, v = _serie_ascendente(250)
        X, y = _preparar(f, m, mn, v)
        assert y.shape[0] > 0
        assert y.sum() == y.shape[0]

    def test_shapes_e_contagem_coerentes(self):
        n = 250
        f, m, mn, v = _serie_lateral(n)
        X, y = _preparar(f, m, mn, v)
        # amostras: velas 55..n-1 geram features; as SEQ_LEN primeiras só
        # servem de contexto; as JANELA_FUTURA finais não têm janela completa.
        esperado = n - 55 - lstm_modelo.SEQ_LEN - lstm_modelo.JANELA_FUTURA
        assert X.shape[0] == esperado
        assert y.shape[0] == esperado
        # SEQ_LEN velas achatadas + deltas entre velas consecutivas
        assert X.shape[1] == (
            lstm_modelo.SEQ_LEN * lstm_modelo.N_FEATURES
            + (lstm_modelo.SEQ_LEN - 1) * lstm_modelo.N_FEATURES
        )
        assert set(np.unique(y).tolist()).issubset({0.0, 1.0})

    def test_dados_insuficientes_retorna_none_none(self):
        f, m, mn, v = _serie_lateral(150)  # < 200 linhas
        X, y = _preparar(f, m, mn, v)
        assert X is None
        assert y is None

    def test_fecha_conexao(self):
        f, m, mn, v = _serie_lateral(250)
        rows = [(f[i], m[i], mn[i], v[i]) for i in range(len(f))]
        conn = _fake_conn(rows)
        with mock.patch.object(lstm_modelo.sqlite3, "connect", return_value=conn):
            lstm_modelo.preparar_sequencias("1h")
        conn.close.assert_called_once()


# ===========================================================================
# Gate de promoção (E-10) — função pura, testável sem treinar nada
# ===========================================================================


class TestGateDePromocao:
    def test_piso_e_055(self):
        # 0,55 não é meta ambiciosa — é o mínimo para o modelo não ser moeda.
        assert lstm_modelo.CV_AUC_MINIMO == 0.55

    @pytest.mark.parametrize(
        "cv_mean, esperado",
        [(0.55, True), (0.60, True), (0.5499, False), (0.50, False), (0.0, False)],
    )
    def test_cv_mean_contra_o_piso(self, cv_mean, esperado):
        assert lstm_modelo.promover_modelo(cv_mean, auc=0.99) is esperado

    def test_cv_none_cai_no_auc_do_holdout(self):
        # Só quando a CV purgada não pôde ser calculada (série curta demais).
        assert lstm_modelo.promover_modelo(None, 0.60) is True
        assert lstm_modelo.promover_modelo(None, 0.50) is False

    def test_cv_presente_tem_precedencia_sobre_auc(self):
        # O AUC de holdout alto NÃO salva um cv_auc ruim — a CV purgada é a
        # estimativa honesta e é ela que manda quando existe.
        assert lstm_modelo.promover_modelo(0.50, 0.99) is False

    def test_valores_invalidos_nao_promovem(self):
        assert lstm_modelo.promover_modelo(None, None) is False
        assert lstm_modelo.promover_modelo("nao-numerico", 0.99) is False
        assert lstm_modelo.promover_modelo(float("nan"), 0.99) is False

    def test_treinar_consulta_o_gate_antes_de_salvar(self):
        # Sem treinar: o gate tem de aparecer no código de treinar() ANTES da
        # troca atômica do pickle — um modelo reprovado nunca chega ao disco.
        fonte = inspect.getsource(lstm_modelo.treinar)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#"))
        assert codigo.index("promover_modelo(") < codigo.index("os.replace(")


# ===========================================================================
# prever — recusa E-7 (sem transferência de domínio) + paridade de contexto
# ===========================================================================


def _klines(f, m, mn, v):
    """Formato da Binance: índices usados são 2=max, 3=min, 4=close, 5=vol."""
    return [[0, 0, str(m[i]), str(mn[i]), str(f[i]), str(v[i])] for i in range(len(f))]


class TestPreverRecusaE7:
    def test_modelo_ausente_retorna_none_e_mensagem(self):
        with mock.patch.object(lstm_modelo.os.path, "exists", return_value=False):
            prob, msg = lstm_modelo.prever("BTCUSDT")
        assert prob is None
        assert "nao treinado" in msg.lower()

    def test_recusa_par_divergente_do_artefato(self):
        # O modelo é global e foi treinado em BTCUSDT: alimentá-lo com features
        # de ETH seria transferência de domínio nunca validada, com 45% do peso
        # do ensemble — a recusa derruba o ensemble no ramo "Apenas XGBoost".
        artefato = {"modelo": mock.Mock(), "scaler": mock.Mock(), "symbol": "BTCUSDT"}
        with (
            mock.patch.object(lstm_modelo.os.path, "exists", return_value=True),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(lstm_modelo.pickle, "load", return_value=artefato),
        ):
            prob, msg = lstm_modelo.prever("ETHUSDT")
        assert prob is None
        assert "treinado em btcusdt" in msg.lower()
        assert "ethusdt" in msg.lower()

    def test_artefato_legado_sem_symbol_e_tratado_como_btcusdt(self):
        # Pickles anteriores a E-7 não gravavam 'symbol'; eles são, de fato,
        # BTCUSDT — logo continuam recusados para qualquer outro par.
        artefato = {"modelo": mock.Mock(), "scaler": mock.Mock()}
        with (
            mock.patch.object(lstm_modelo.os.path, "exists", return_value=True),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(lstm_modelo.pickle, "load", return_value=artefato),
        ):
            prob, msg = lstm_modelo.prever("SOLUSDT")
        assert prob is None
        assert "treinado em btcusdt" in msg.lower()

    def test_recusa_nao_faz_chamada_de_rede(self):
        # Sentinela: a recusa E-7 acontece ANTES de qualquer I/O de mercado.
        artefato = {"modelo": mock.Mock(), "scaler": mock.Mock(), "symbol": "BTCUSDT"}
        with (
            mock.patch.object(lstm_modelo.os.path, "exists", return_value=True),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(lstm_modelo.pickle, "load", return_value=artefato),
            mock.patch("requests.get") as get_mock,
        ):
            lstm_modelo.prever("ETHUSDT")
        get_mock.assert_not_called()


class TestPreverParidadeDeContexto:
    def _prever_mockado(self, n_velas: int):
        f, m, mn, v = _serie_lateral(n_velas)
        resp = mock.Mock()
        resp.json.return_value = _klines(f, m, mn, v)

        scaler_fake = mock.Mock()
        scaler_fake.transform.side_effect = lambda X: X
        modelo_fake = mock.Mock()
        modelo_fake.predict_proba.return_value = np.array([[0.25, 0.75]])
        artefato = {
            "modelo": modelo_fake,
            "scaler": scaler_fake,
            "intervalo": "1h",
            "symbol": "BTCUSDT",
        }

        with (
            mock.patch.object(lstm_modelo.os.path, "exists", return_value=True),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(lstm_modelo.pickle, "load", return_value=artefato),
            mock.patch("requests.get", return_value=resp) as get_mock,
        ):
            prob, msg = lstm_modelo.prever("BTCUSDT")
        return prob, msg, get_mock

    def test_inferencia_pede_o_contexto_que_o_treino_viu(self):
        # E-10: era limit=100 — o ATR de Wilder não converge com 100 velas e a
        # MESMA barra ganhava outro valor no serve. Como o MLP consome as
        # últimas SEQ_LEN features (não só a última), cada uma precisa de
        # CONTEXTO_MINIMO velas antes de si.
        limite_exigido = ml_filtro.CONTEXTO_MINIMO + lstm_modelo.SEQ_LEN
        prob, msg, get_mock = self._prever_mockado(limite_exigido)
        get_mock.assert_called_once()
        params = get_mock.call_args.kwargs["params"]
        assert params["limit"] == limite_exigido
        assert params["limit"] >= ml_filtro.CONTEXTO_MINIMO
        assert params["symbol"] == "BTCUSDT"

    def test_caminho_feliz_retorna_probabilidade(self):
        prob, msg, _ = self._prever_mockado(ml_filtro.CONTEXTO_MINIMO + lstm_modelo.SEQ_LEN)
        assert msg == "OK"
        assert prob == pytest.approx(0.75, abs=1e-6)

    def test_features_insuficientes_retorna_none(self):
        # Resposta curta demais (< 55 + SEQ_LEN velas): degrada com mensagem,
        # nunca alimenta o modelo com uma sequência incompleta.
        prob, msg, _ = self._prever_mockado(60)
        assert prob is None
        assert msg == "Features insuficientes"
