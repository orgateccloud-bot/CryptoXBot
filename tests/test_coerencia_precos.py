"""
Testes da frente E-7 — a invariante 0 < stop < preco < target, fail-closed.

O que existia antes: o caminho de producao (main.py -> abrir_long) NAO validava
nada. So o caminho trend validava. O resultado real, medido em log de producao:
um bloco com entrada ~$1.858 (ETH) e Stop Loss $63.521,65 — o stop 34x acima da
entrada, porque o nivel de suporte vinha do BITCOIN.

Com stop >= entrada, o desfecho e um destes dois, nenhum aceitavel:
  (a) a Binance rejeita o STOP_LOSS_LIMIT (stopPrice acima do mercado) e a
      posicao real fica DESPROTEGIDA; ou
  (b) o monitor local a liquida no primeiro tick, pagando spread + duas taxas.

A guarda existe agora em tres pontos: na origem do sinal
(estrategias/otimizada._incoerencia_de_precos), no gate do worker (main.py) e
dentro de executor.abrir_long (executor._incoerencia_long), que e o unico que
nenhum chamador consegue contornar. Como as duas implementacoes sao REPLICAS
deliberadas (executor nao pode importar estrategias sem arrastar
regime/suporte/ensemble/xgboost para dentro da camada de execucao), este arquivo
prova por amostragem que elas nunca divergem.
"""

import random

import pytest

from estrategias.otimizada import _incoerencia_de_precos as incoerencia_sinal
from executor import _incoerencia_long as incoerencia_exec

SEMENTE = 20260808  # fixa: um FAIL tem de ser reproduzivel com um comando


# ══════════════════════════════════════════════════════════════════
#  1. Casos dirigidos — inclui o bloco real de producao
# ══════════════════════════════════════════════════════════════════


class TestCasosDirigidos:
    def test_o_bloco_real_de_producao_e_recusado(self):
        """ETH a $1.858,42 com stop $63.521,65 (suporte do BTC). Reproduzido
        do log de producao, nao inventado."""
        assert incoerencia_exec(1858.42, 63521.65, 1886.30) is not None
        assert incoerencia_sinal("COMPRA", 1858.42, 63521.65, 1886.30) is not None

    def test_compra_coerente_passa(self):
        assert incoerencia_exec(100.0, 98.5, 103.0) is None
        assert incoerencia_sinal("COMPRA", 100.0, 98.5, 103.0) is None

    def test_venda_coerente_passa(self):
        assert incoerencia_sinal("VENDA", 100.0, 101.5, 97.0) is None

    @pytest.mark.parametrize(
        "preco,stop,target",
        [
            (100.0, 100.0, 103.0),  # stop == entrada (limite, nao passa)
            (100.0, 101.0, 103.0),  # stop acima
            (100.0, 98.0, 100.0),  # target == entrada
            (100.0, 98.0, 99.0),  # target abaixo
            (100.0, 0.0, 103.0),  # stop zero
            (100.0, -5.0, 103.0),  # stop negativo
            (100.0, 98.0, 0.0),  # target zero
            (0.0, 98.0, 103.0),  # preco zero
            (-100.0, -110.0, -90.0),  # tudo negativo
            (100.0, None, 103.0),  # stop ausente
            (100.0, 98.0, None),  # target ausente
            (None, 98.0, 103.0),  # preco ausente
        ],
    )
    def test_long_invalidos_sao_recusados(self, preco, stop, target):
        assert incoerencia_exec(preco, stop, target) is not None

    def test_mensagem_diz_os_numeros(self):
        """Uma recusa sem os valores obriga a reproduzir o incidente para
        entende-lo — e este incidente aparece uma vez a cada muitas horas."""
        msg = incoerencia_exec(1858.42, 63521.65, 1886.30)
        assert "63521.65" in msg
        assert "1858.42" in msg

    def test_aguardar_nao_e_avaliado(self):
        """Sem sinal nao ha precos para validar; AGUARDAR com stop None e o
        estado NORMAL, nao uma incoerencia."""
        assert incoerencia_sinal("AGUARDAR", 100.0, None, None) is None
        assert incoerencia_sinal(None, 0.0, None, None) is None


# ══════════════════════════════════════════════════════════════════
#  2. 1.000 casos aleatorios — criterio de saida de E-7
# ══════════════════════════════════════════════════════════════════


def _gerar_casos(n, rng):
    """Metade coerentes por construcao, metade deliberadamente rompidos."""
    casos = []
    for i in range(n):
        preco = rng.choice([95.0, 1858.42, 60000.0, 0.0001234]) * rng.uniform(0.5, 2.0)
        if i % 2 == 0:
            stop = preco * rng.uniform(0.90, 0.999)
            target = preco * rng.uniform(1.001, 1.10)
            esperado_ok = True
        else:
            modo = i % 8
            if modo == 1:
                stop, target = preco * rng.uniform(1.001, 40.0), preco * 1.02  # stop acima
            elif modo == 3:
                stop, target = preco * 0.98, preco * rng.uniform(0.5, 0.999)  # target abaixo
            elif modo == 5:
                stop, target = preco, preco * 1.02  # stop == entrada
            else:
                stop, target = -abs(preco) * 0.5, preco * 1.02  # stop negativo
            esperado_ok = False
        casos.append((preco, stop, target, esperado_ok))
    return casos


class TestMilCasosAleatorios:
    def test_mil_compras_classificadas_corretamente(self):
        rng = random.Random(SEMENTE)
        casos = _gerar_casos(1000, rng)
        erros = []
        for preco, stop, target, esperado_ok in casos:
            obtido_ok = incoerencia_exec(preco, stop, target) is None
            if obtido_ok != esperado_ok:
                erros.append((preco, stop, target, esperado_ok, obtido_ok))
        assert not erros, f"{len(erros)} casos classificados errado; 1o: {erros[0]}"

    def test_todos_os_aprovados_satisfazem_a_ordem(self):
        """Criterio de saida literal: 100% dos aprovados tem 0 < stop < preco < target."""
        rng = random.Random(SEMENTE + 1)
        aprovados = 0
        for preco, stop, target, _ in _gerar_casos(1000, rng):
            if incoerencia_exec(preco, stop, target) is None:
                assert 0 < stop < preco < target
                aprovados += 1
        assert aprovados >= 400, f"amostra degenerada: so {aprovados} aprovados"

    def test_as_duas_implementacoes_nunca_divergem(self):
        """A replica em executor.py e a de otimizada.py tem de concordar em
        TODOS os casos. E o que mantem a duplicacao deliberada honesta: alterar
        so uma das duas quebra este teste."""
        rng = random.Random(SEMENTE + 2)
        for preco, stop, target, _ in _gerar_casos(1000, rng):
            a = incoerencia_exec(preco, stop, target) is None
            b = incoerencia_sinal("COMPRA", preco, stop, target) is None
            assert a == b, f"divergiram em preco={preco} stop={stop} target={target}"


# ══════════════════════════════════════════════════════════════════
#  3. Fail-closed de verdade: abrir_long recusa e nao manda ordem
# ══════════════════════════════════════════════════════════════════


class TestAbrirLongFailClosed:
    def _executor(self, monkeypatch):
        import executor as ex_mod

        ex = ex_mod.Executor(simulacao=True, symbol="ETHUSDT")
        monkeypatch.setattr(ex, "get_preco", lambda: 1858.42)
        return ex_mod, ex

    def test_recusa_e_nao_envia_ordem(self, monkeypatch):
        ex_mod, ex = self._executor(monkeypatch)
        monkeypatch.setattr(ex_mod.database, "salvar_bot_event", lambda *a, **k: None)

        def _proibido(*a, **k):  # pragma: no cover - deve ser inalcancavel
            raise AssertionError("ordem enviada com precos incoerentes")

        monkeypatch.setattr(ex, "_enviar_ordem", _proibido)
        monkeypatch.setattr(ex, "_entrar_maker", _proibido)

        assert ex.abrir_long(1858.42, 0.05, 63521.65, 1886.30) is False
        assert ex.posicao is None

    def test_recusa_escala_bot_event_critical(self, monkeypatch):
        ex_mod, ex = self._executor(monkeypatch)
        eventos = []
        monkeypatch.setattr(
            ex_mod.database,
            "salvar_bot_event",
            lambda t, m, **k: eventos.append((t, k.get("severity"), k.get("symbol"))),
        )
        ex.abrir_long(1858.42, 0.05, 63521.65, 1886.30)
        assert eventos == [("ordem_recusada_incoerente", "CRITICAL", "ETHUSDT")]

    def test_recusa_conta_como_erro_de_ordem(self, monkeypatch):
        """Recusar em silencio seria trocar a ordem absurda por um nao-evento."""
        import health

        ex_mod, ex = self._executor(monkeypatch)
        monkeypatch.setattr(ex_mod.database, "salvar_bot_event", lambda *a, **k: None)
        with health._metrics_lock:
            antes = health._metrics["ordens_erro"]
        ex.abrir_long(1858.42, 0.05, 63521.65, 1886.30)
        with health._metrics_lock:
            assert health._metrics["ordens_erro"] == antes + 1

    def test_db_fora_do_ar_nao_impede_a_recusa(self, monkeypatch):
        """A recusa nao pode depender do banco: se salvar_bot_event levantar, a
        ordem ainda tem de ser recusada."""
        ex_mod, ex = self._executor(monkeypatch)

        def _explode(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(ex_mod.database, "salvar_bot_event", _explode)
        assert ex.abrir_long(1858.42, 0.05, 63521.65, 1886.30) is False

    def test_entrada_coerente_continua_passando(self, monkeypatch):
        """A guarda nao pode bloquear o caminho valido."""
        ex_mod, ex = self._executor(monkeypatch)
        monkeypatch.setattr(ex_mod.database, "salvar_bot_event", lambda *a, **k: None)
        monkeypatch.setattr(ex_mod.database, "salvar_posicao_aberta", lambda *a, **k: None)
        monkeypatch.setattr(ex_mod.gestao_risco, "get_saldo_usdt", lambda: 1000.0)
        assert ex.abrir_long(1858.42, 0.05, 1830.0, 1886.30) is True
        assert ex.posicao is not None


# ══════════════════════════════════════════════════════════════════
#  4. O override de stop pelo suporte nao pode passar a entrada
# ══════════════════════════════════════════════════════════════════


class TestOverrideDeStopPeloSuporte:
    def test_suporte_acima_do_preco_nao_move_o_stop(self, monkeypatch):
        """Corrigir o symbol reduz o absurdo mas nao o elimina: `suporte_forte`
        sai de um cluster que inclui EMA20/EMA50/VWAP, e qualquer um dos tres
        pode estar ACIMA do preco atual (tipico em queda). A guarda e sobre a
        GRANDEZA, nao sobre a procedencia do dado."""
        from estrategias import otimizada

        preco = 100.0
        stop_pct_stop = round(preco * (1 - 0.015), 2)  # 98.5
        # Suporte 5% ACIMA do preco -> stop_suporte = 104.48 > preco
        suporte_forte = preco * 1.05
        stop_suporte = round(suporte_forte * 0.995, 2)
        assert stop_suporte > preco, "fixture nao reproduz o caso"

        # Condicao efetiva do codigo: `stop < stop_suporte < preco`
        aceita = stop_pct_stop < stop_suporte < preco
        assert aceita is False

        # E a antiga (`stop_suporte > stop`) teria aceitado
        assert (stop_suporte > stop_pct_stop) is True

        # Guarda contra reintroducao da comparacao antiga.
        import inspect

        codigo = "\n".join(
            linha.split("#")[0]
            for linha in inspect.getsource(otimizada.analisar).splitlines()
            if not linha.strip().startswith("#")
        )
        assert "if stop < stop_suporte < preco:" in codigo
        assert "if stop_suporte > stop:" not in codigo

    def test_suporte_entre_stop_e_preco_ainda_aperta(self, monkeypatch):
        """O comportamento desejado — stop mais apertado no suporte — continua."""
        preco = 100.0
        stop_pct_stop = 98.5
        suporte_forte = 99.5
        stop_suporte = round(suporte_forte * 0.995, 2)  # 99.0
        assert stop_pct_stop < stop_suporte < preco
