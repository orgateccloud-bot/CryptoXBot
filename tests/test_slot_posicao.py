"""Slot atômico de posição — regressão do incidente de 2026-08-20 10:01:23.

No primeiro ciclo com saldo destravado, as 3 threads de par leram
`posicoes_abertas == 0` no mesmo milissegundo, todas passaram no gate 5 e
abriram 3 posições com MAX_POSICOES_ABERTAS=1 (3 chaves posicao:* em
risk_state, janela de 13ms). Estes testes provam que a reserva atômica mata
a corrida — inclusive com threads REAIS sincronizadas por Barrier.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import risco


@pytest.fixture(autouse=True)
def _estado_limpo():
    risco._carregar_estado_persistido()
    with risco._lock_posicoes:
        risco._slots_reservados.clear()
        risco._estado_risco["posicoes_abertas"] = 0
    yield
    with risco._lock_posicoes:
        risco._slots_reservados.clear()
        risco._estado_risco["posicoes_abertas"] = 0


class TestReservaAtomica:
    def test_corrida_de_3_threads_so_1_ganha_o_slot(self):
        """O incidente real, reproduzido: 3 pares disputando no mesmo instante."""
        barreira = threading.Barrier(3)
        resultados = {}

        def disputar(par):
            barreira.wait()
            resultados[par] = risco.adquirir_slot_posicao(par)

        threads = [
            threading.Thread(target=disputar, args=(p,)) for p in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        vencedores = [p for p, ok in resultados.items() if ok]
        assert len(vencedores) == 1, f"corrida não resolvida: {resultados}"

    def test_corrida_repetida_50_rodadas(self):
        """Corrida é probabilística — uma vitória única não prova nada."""
        for _ in range(50):
            with risco._lock_posicoes:
                risco._slots_reservados.clear()
                risco._estado_risco["posicoes_abertas"] = 0
            barreira = threading.Barrier(3)
            resultados = []

            def disputar(par, barreira=barreira, resultados=resultados):
                barreira.wait()
                resultados.append(risco.adquirir_slot_posicao(par))

            threads = [
                threading.Thread(target=disputar, args=(p,))
                for p in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert sum(resultados) == 1

    def test_adquirir_e_idempotente_para_o_mesmo_par(self):
        assert risco.adquirir_slot_posicao("BTCUSDT") is True
        assert risco.adquirir_slot_posicao("BTCUSDT") is True  # re-entrada, não 2ª vaga
        assert risco.adquirir_slot_posicao("ETHUSDT") is False

    def test_liberar_devolve_a_vaga(self):
        assert risco.adquirir_slot_posicao("BTCUSDT") is True
        risco.liberar_slot_posicao("BTCUSDT")
        assert risco.adquirir_slot_posicao("ETHUSDT") is True

    def test_confirmar_vira_posicao_contada_e_segue_bloqueando(self):
        assert risco.adquirir_slot_posicao("BTCUSDT") is True
        risco.confirmar_slot_posicao("BTCUSDT")
        assert risco._estado_risco["posicoes_abertas"] == 1
        assert not risco._slots_reservados
        assert risco.adquirir_slot_posicao("ETHUSDT") is False

    def test_confirmar_sem_reserva_e_incremento_puro(self):
        """Compatibilidade: testes/chamadores que vão direto ao abrir_long."""
        risco.confirmar_slot_posicao("BTCUSDT")
        assert risco._estado_risco["posicoes_abertas"] == 1

    def test_posicao_contada_mais_reserva_nunca_excede_o_max(self):
        risco.confirmar_slot_posicao("BTCUSDT")  # 1 posição real
        assert risco.adquirir_slot_posicao("ETHUSDT") is False


class TestGate5ComReservas:
    def _validar(self, symbol):
        return risco.validar_trade("COMPRA", 100.0, 1000.0, stop=98.5, symbol=symbol)

    def test_reserva_ALHEIA_bloqueia_o_gate_5(self):
        assert risco.adquirir_slot_posicao("BTCUSDT") is True
        r = self._validar("ETHUSDT")
        assert r["pode"] is False
        assert "Posicao ja aberta" in r["motivo"]

    def test_reserva_PROPRIA_nao_bloqueia_a_revalidacao(self):
        """Modo real: o executor re-valida DEPOIS que o main reservou — a
        própria reserva não pode vetar o próprio trade."""
        assert risco.adquirir_slot_posicao("BTCUSDT") is True
        r = self._validar("BTCUSDT")
        assert r["pode"] is True, f"re-validação vetou o próprio slot: {r.get('motivo')}"
