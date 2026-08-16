"""
Testes — MESA DE OPERAÇÕES (aba 7): canal de comando dashboard → worker
========================================================================
As propriedades que importam são as de CONTENÇÃO: a lista de comandos é
fechada, fechar posição RECUSA executor não-simulado (papel somente, por
construção), comando desconhecido é REJEITADO, e o ciclo completo
criar → consumir → marcar fica auditável na tabela `comandos`.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402


def _executor_fake(simulacao=True, posicao=None, preco=100.0):
    chamadas = []

    def fechar(preco_arg, motivo, parcial=False):
        chamadas.append((preco_arg, motivo))

    ex = SimpleNamespace(
        simulacao=simulacao,
        posicao=posicao,
        get_preco=lambda: preco,
        fechar_posicao=fechar,
        _chamadas=chamadas,
    )
    return ex


class TestCanalComandos:
    def test_ciclo_completo_criar_consumir_marcar(self):
        database.inicializar()
        cid = database.criar_comando("pausar_bot", {}, origem="teste")
        cmd = database.proximo_comando_pendente()
        assert cmd is not None and cmd["id"] == cid
        assert cmd["comando"] == "pausar_bot" and cmd["origem"] == "teste"
        database.marcar_comando(cid, "EXECUTADO", "ok")
        assert database.proximo_comando_pendente() is None
        historico = database.listar_comandos(limite=5)
        assert historico[0]["id"] == cid
        assert historico[0]["status"] == "EXECUTADO"
        assert historico[0]["resultado"] == "ok"

    def test_fila_e_fifo(self):
        database.inicializar()
        a = database.criar_comando("pausar_bot", {}, origem="t")
        b = database.criar_comando("retomar_bot", {}, origem="t")
        primeiro = database.proximo_comando_pendente()
        assert primeiro["id"] == a
        database.marcar_comando(a, "EXECUTADO", "ok")
        segundo = database.proximo_comando_pendente()
        assert segundo["id"] == b
        database.marcar_comando(b, "EXECUTADO", "ok")


class TestExecucaoDosComandos:
    def test_pausar_e_retomar_controlam_o_flag(self):
        main._mesa_pausado.clear()
        st, _ = main._executar_comando_mesa({"comando": "pausar_bot", "params": {}})
        assert st == "EXECUTADO" and main._mesa_pausado.is_set()
        st, _ = main._executar_comando_mesa({"comando": "retomar_bot", "params": {}})
        assert st == "EXECUTADO" and not main._mesa_pausado.is_set()

    def test_fechar_posicao_paper_fecha_no_preco_fresco(self, monkeypatch):
        ex = _executor_fake(simulacao=True, posicao={"entrada": 90.0}, preco=101.5)
        monkeypatch.setitem(main._estado_pares, "BTCUSDT", {"executor": ex})
        st, res = main._executar_comando_mesa(
            {"comando": "fechar_posicao_paper", "params": {"symbol": "BTCUSDT"}}
        )
        assert st == "EXECUTADO"
        assert ex._chamadas == [(101.5, "mesa_operacoes")]
        assert "101.5" in res

    def test_fechar_posicao_RECUSA_executor_real(self, monkeypatch):
        """A propriedade central da mesa: papel somente, por construção."""
        ex = _executor_fake(simulacao=False, posicao={"entrada": 90.0})
        monkeypatch.setitem(main._estado_pares, "BTCUSDT", {"executor": ex})
        st, res = main._executar_comando_mesa(
            {"comando": "fechar_posicao_paper", "params": {"symbol": "BTCUSDT"}}
        )
        assert st == "REJEITADO"
        assert ex._chamadas == [], "executor real jamais pode ser tocado pela mesa"
        assert "REAL" in res

    def test_fechar_sem_posicao_falha_sem_efeito(self, monkeypatch):
        ex = _executor_fake(simulacao=True, posicao=None)
        monkeypatch.setitem(main._estado_pares, "ETHUSDT", {"executor": ex})
        st, _ = main._executar_comando_mesa(
            {"comando": "fechar_posicao_paper", "params": {"symbol": "ETHUSDT"}}
        )
        assert st == "FALHOU" and ex._chamadas == []

    def test_fechar_sem_executor_falha(self, monkeypatch):
        monkeypatch.setattr(main, "_estado_pares", {})
        st, _ = main._executar_comando_mesa(
            {"comando": "fechar_posicao_paper", "params": {"symbol": "SOLUSDT"}}
        )
        assert st == "FALHOU"

    def test_retreinar_dispara_thread(self, monkeypatch):
        disparado = []
        monkeypatch.setattr(main, "_retreinar_modelos", lambda pares: disparado.append(pares))
        st, _ = main._executar_comando_mesa({"comando": "retreinar_ml", "params": {}})
        assert st == "EXECUTADO"
        import time

        for _ in range(50):  # thread daemon: espera curta pelo efeito
            if disparado:
                break
            time.sleep(0.05)
        assert disparado == [main.PARES_ATIVOS]

    def test_comando_desconhecido_e_REJEITADO(self):
        st, res = main._executar_comando_mesa({"comando": "ligar_modo_real", "params": {}})
        assert st == "REJEITADO"
        assert "desconhecido" in res

    def test_lista_de_comandos_nao_contem_nada_de_capital_real(self):
        """Prova estática do contrato: o CÓDIGO da mesa não conhece nenhum
        comando que toque DRY_RUN/ALLOW_REAL ou envie ordem à exchange.
        (A docstring PODE citar os nomes — é onde o contrato está escrito —
        então a varredura remove a docstring antes de olhar o código.)"""
        import ast
        import inspect
        import textwrap

        fonte = textwrap.dedent(inspect.getsource(main._executar_comando_mesa))
        fn = ast.parse(fonte).body[0]
        corpo = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body
        codigo = ast.unparse(ast.Module(body=corpo, type_ignores=[]))
        assert "ALLOW_REAL" not in codigo
        assert "DRY_RUN" not in codigo
        assert "abrir_long" not in codigo
