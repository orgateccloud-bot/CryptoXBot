"""
Testes — a suíte não pode tocar no estado de PRODUÇÃO
======================================================
Regressão real (2026-07-31). Não havia `conftest.py` no repo, então
`pytest tests/` escrevia em `data/btc_data.db` — o mesmo arquivo que o serviço
BXBotWorker usa 24/7. `tests/test_executor_monitor.py` instancia um `Executor`
real e chama `_persistir_posicao()`.

O banco vivo ficou com `risk_state['posicao:BTCUSDT']` = entrada 100.0,
order_id "SIM-1", abertura "x". O boot readotava sem validar; com BTC a ~64.000
o monitor via preço >> target1 (105.0) e "fechava" no primeiro tick, gravando
PnL de +62.658% e +64.429% em `sinais` — que alimentaram `registrar_resultado`
e o `pnl_dia` persistido. Ocorreu três vezes (09/07, 19/07, 31/07).

O agravante: o CLAUDE.md exige `pytest tests/ -v` antes de todo PR. No host de
produção, o comando era destrutivo e não sinalizado.

Duas camadas independentes, testadas aqui:
  1. `conftest.py` redireciona DB_PATH e FALHA ALTO se alguém abrir o arquivo
     de produção (origem).
  2. `Executor._posicao_e_plausivel` recusa readotar posição implausível
     (defesa em profundidade, para banco sujo por outro caminho).
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executor as ex  # noqa: E402

# ── Camada 1: isolamento do banco ──────────────────────────────


class TestIsolamentoDoBanco:
    def test_db_path_nao_aponta_para_producao(self):
        from config.runtime_settings import DB_PATH

        assert (
            "btc_data.db" not in os.path.basename(DB_PATH) or "test" in DB_PATH.lower()
        ), f"DB_PATH da suíte aponta para o banco de produção: {DB_PATH}"

    def test_abrir_o_banco_de_producao_falha_alto(self):
        """O modo de falha original era SILENCIOSO. Agora tem que gritar."""
        with pytest.raises(AssertionError, match="BANCO DE PRODUÇÃO"):
            sqlite3.connect("data/btc_data.db")

    def test_caminho_isolado_continua_funcionando(self, tmp_path):
        con = sqlite3.connect(str(tmp_path / "ok.db"))
        con.execute("CREATE TABLE t (x int)")
        con.close()


# ── Camada 2: recusa de posição implausível ────────────────────


def _exec_sim(monkeypatch, preco_mercado=64000.0):
    e = ex.Executor(simulacao=True, symbol="BTCUSDT")
    monkeypatch.setattr(e, "get_preco", lambda: preco_mercado)
    monkeypatch.setattr(e, "_monitorar", lambda: None)
    monkeypatch.setattr(ex.database, "salvar_bot_event", lambda *a, **k: None)
    return e


def _pos(**kw):
    base = {
        "tipo": "LONG",
        "entrada": 64000.0,
        "tamanho_btc": 0.001,
        "stop_atual": 63000.0,
        "target1": 65000.0,
        "target2": 66000.0,
        "parcial_feita": False,
        "abertura": "2026-07-31T10:00:00",
        "order_id": 12345,
        "stop_order_id": None,
    }
    base.update(kw)
    return base


class TestRecusaDePosicaoImplausivel:
    def test_recusa_a_posicao_exata_do_incidente(self, monkeypatch):
        """entrada 100.0 com BTC a 64.000 — o registro que existia no banco."""
        e = _exec_sim(monkeypatch)
        fantasma = _pos(
            entrada=100.0,
            stop_atual=104.16,
            target1=105.0,
            target2=110.0,
            order_id="SIM-1",
            abertura="x",
        )
        assert e.reidratar_posicao(fantasma) is False
        assert e.posicao is None, "não pode adotar"
        assert e._ativo is False, "não pode religar o monitor"

    def test_recusa_por_divergencia_de_ordem_de_magnitude(self, monkeypatch):
        e = _exec_sim(monkeypatch, preco_mercado=64000.0)
        assert e.reidratar_posicao(_pos(entrada=100.0)) is False

    def test_recusa_abertura_que_nao_e_timestamp(self, monkeypatch):
        e = _exec_sim(monkeypatch)
        assert e.reidratar_posicao(_pos(abertura="x")) is False

    def test_recusa_entrada_invalida(self, monkeypatch):
        e = _exec_sim(monkeypatch)
        for ruim in (0, -1, None, "abc"):
            assert e.reidratar_posicao(_pos(entrada=ruim)) is False

    def test_ACEITA_posicao_legitima(self, monkeypatch):
        """O risco oposto: recusar posição real deixaria uma posição VIVA sem
        monitor. Este teste é o contrapeso do anterior."""
        e = _exec_sim(monkeypatch, preco_mercado=64000.0)
        assert e.reidratar_posicao(_pos(entrada=63500.0)) is True
        assert e.posicao is not None and e._ativo is True

    def test_aceita_posicao_com_perda_grande_mas_plausivel(self, monkeypatch):
        """-30% é drawdown real, não fixture. Não pode ser recusado."""
        e = _exec_sim(monkeypatch, preco_mercado=64000.0)
        assert e.reidratar_posicao(_pos(entrada=91000.0)) is True

    def test_preco_indisponivel_nao_bloqueia(self, monkeypatch):
        """Sem preço não dá para julgar. Recusar posição real por API fora é
        pior do que aceitar uma suja — a posição real fica sem gestão."""
        e = _exec_sim(monkeypatch, preco_mercado=0.0)
        assert e.reidratar_posicao(_pos(entrada=63500.0)) is True

    def test_order_id_SIM_e_tolerado_em_simulacao(self, monkeypatch):
        """Em paper o próprio executor gera 'SIM-<epoch>'; recusá-lo quebraria
        o crash recovery legítimo do modo simulação."""
        e = _exec_sim(monkeypatch, preco_mercado=64000.0)
        assert e.reidratar_posicao(_pos(order_id="SIM-1700000000")) is True

    def test_order_id_SIM_e_recusado_em_modo_real(self, monkeypatch):
        e = ex.Executor(simulacao=False, symbol="BTCUSDT")
        monkeypatch.setattr(e, "get_preco", lambda: 64000.0)
        monkeypatch.setattr(e, "_monitorar", lambda: None)
        monkeypatch.setattr(ex.database, "salvar_bot_event", lambda *a, **k: None)
        assert e.reidratar_posicao(_pos(order_id="SIM-1")) is False

    def test_recusa_grava_bot_event_critical(self, monkeypatch):
        eventos = []
        e = _exec_sim(monkeypatch)
        monkeypatch.setattr(
            ex.database,
            "salvar_bot_event",
            lambda t, m, **k: eventos.append((t, k.get("severity"))),
        )
        e.reidratar_posicao(_pos(entrada=100.0, order_id="SIM-1", abertura="x"))
        assert eventos and eventos[0] == ("reidratacao_recusada", "CRITICAL")
