"""
Testes — a purga não pode apagar o estado de paper (frente M-1)
================================================================
`scripts/purgar_fixtures_producao.py` é o **único script destrutivo do
repositório**, e o critério nº 1 casava com toda posição legítima de paper:

    if order_id.startswith("SIM-"):
        return f'order_id sintetico "{order_id}"'

`executor.py:502` gera exatamente esse formato em simulação — o modo em que o
BXBotWorker roda 24/7 acumulando os trades da Etapa 2 do gate. Um
`--confirmar` apagava tudo.

O executor já tinha a guarda certa em `reidratar_posicao`
(`if not self.simulacao and order_id.startswith("SIM-")`, :1836); faltava
aqui. O modo vem do canal que o I-8 criou para isso: o evento `modo_efetivo`
em `bot_events` (main.py:1631), porque o ambiente do processo que roda a purga
não reflete o `--real` do worker.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import purgar_fixtures_producao as purga  # noqa: E402

_POS_PAPER = {
    "order_id": "SIM-1786000000",
    "abertura": "2026-08-11T09:30:00",
    "entrada": 64000.0,
}


class TestPosicaoDePaperEPreservada:
    def test_sim_nao_e_fixture_quando_o_worker_esta_em_paper(self):
        # O caso que apagava a Etapa 2 inteira.
        assert purga._posicao_suspeita("pos:BTCUSDT", _POS_PAPER, em_simulacao=True) is None

    def test_sim_E_fixture_quando_o_worker_esta_em_real(self):
        # Em modo real um `SIM-` e mesmo residuo de teste — a guarda nao pode
        # virar um passe livre.
        motivo = purga._posicao_suspeita("pos:BTCUSDT", _POS_PAPER, em_simulacao=False)
        assert motivo is not None and "SIM-" in motivo

    def test_os_outros_criterios_seguem_valendo_em_paper(self):
        # A guarda e so para o `SIM-`. Uma abertura que nao e ISO continua
        # sendo fixture, esteja o bot em paper ou nao.
        ruim = dict(_POS_PAPER, abertura="x")
        assert purga._posicao_suspeita("pos:BTCUSDT", ruim, em_simulacao=True) is not None


class TestDeteccaoDeModo:
    def _fake_db(self, monkeypatch, eventos, explode=False):
        import types

        mod = types.ModuleType("database")

        def _listar(**kwargs):
            if explode:
                raise RuntimeError("banco fora do ar")
            return eventos

        mod.listar_bot_events = _listar
        monkeypatch.setitem(sys.modules, "database", mod)

    def test_le_simulacao_do_evento(self, monkeypatch):
        self._fake_db(monkeypatch, [{"message": "worker iniciado em modo SIMULACAO (...)"}])
        assert purga._worker_em_simulacao() is True

    def test_le_real_do_evento(self, monkeypatch):
        self._fake_db(monkeypatch, [{"message": "worker iniciado em modo REAL (...)"}])
        assert purga._worker_em_simulacao() is False

    @pytest.mark.parametrize("cenario", ["sem_evento", "banco_fora"])
    def test_na_duvida_assume_paper(self, monkeypatch, cenario):
        """Fail-safe na direção que PRESERVA dado.

        Assumir "real" quando não se sabe apagaria o estado de paper; assumir
        "paper" no máximo deixa um resíduo de teste para trás. As duas falhas
        não são simétricas.
        """
        self._fake_db(monkeypatch, [], explode=(cenario == "banco_fora"))
        assert purga._worker_em_simulacao() is True


class TestTimeoutsEmAnaliseMercado:
    def test_toda_requisicao_tem_timeout(self):
        """Sem timeout, um socket pendurado trava `relatorio_completo()` para
        sempre — e `start_health_server` roda ANTES, então o worker fica vivo,
        `/health` responde 200 e o loop de trading nunca começa. É um apagão
        que o NSSM não detecta, porque o processo não morre."""
        import inspect

        import analise_mercado

        fonte = inspect.getsource(analise_mercado)
        assert fonte.count("requests.get(") == fonte.count(
            "timeout=TIMEOUT_HTTP"
        ), "alguma requisicao de analise_mercado ficou sem timeout"

    def test_timeout_e_finito_e_curto(self):
        import analise_mercado

        assert 0 < analise_mercado.TIMEOUT_HTTP <= 30
