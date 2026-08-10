"""
Testes — procedência dos parâmetros vivos (frente E-9)
=======================================================
`config/params_pares.py` governa o trading ao vivo. A versão que estava lá
veio de um grid de até 8.000 combinações sem out-of-sample, ordenado por um
Sharpe cego a custo, com look-ahead no MTF e F&G fixo em 100 — e o último
commit do arquivo é ANTERIOR à versão atual do otimizador. A procedência é
inauditável.

O critério de saída do E-9 pede exatamente isto: **este arquivo falha se algum
par não tiver os cinco campos de procedência**. O contrato completo:

  1. todo par declara os 5 campos (mesmo que vazios — vazio é uma resposta,
     ausente é um esquecimento);
  2. `params_confiaveis()` só devolve True com os 5 preenchidos E o DSR no
     hold-out >= 0,95;
  3. capital real é RECUSADO enquanto houver par não auditado.

Os testes NÃO exigem que os parâmetros estejam auditados hoje — não estão, e
esse é o estado honesto. Eles exigem que a ESTRUTURA que torna a auditoria
verificável exista e que a trava funcione.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import params_pares as P  # noqa: E402


class TestOsCincoCampos:
    def test_todo_par_declara_os_cinco_campos(self):
        # O critério de saída do E-9, literal.
        for par in P.PARAMS_PARES:
            proc = P.PROCEDENCIA.get(par)
            assert proc is not None, f"{par} nao tem entrada em PROCEDENCIA"
            faltando = [c for c in P.CAMPOS_PROCEDENCIA if c not in proc]
            assert not faltando, f"{par} sem os campos {faltando}"

    def test_os_cinco_campos_sao_os_do_contrato(self):
        # Trava de regressao: remover um campo do contrato afrouxaria o gate
        # sem que nenhum outro teste percebesse.
        assert set(P.CAMPOS_PROCEDENCIA) == {
            "commit_regua",
            "hash_snapshot",
            "janela",
            "n_trials",
            "dsr",
        }

    def test_procedencia_devolve_copia(self):
        # Um chamador nao pode "preencher" a procedencia por acidente mutando
        # o dict devolvido — seria a forma mais silenciosa de liberar real.
        p = P.procedencia("BTCUSDT")
        p["dsr"] = 0.99
        assert P.procedencia("BTCUSDT")["dsr"] is None


class TestConfiabilidade:
    def test_hoje_nenhum_par_e_confiavel(self):
        # Estado honesto de 2026-08-09: nenhum par foi re-derivado.
        assert P.pares_nao_auditados() == sorted(P.PARAMS_PARES)
        for par in P.PARAMS_PARES:
            assert P.params_confiaveis(par) is False

    def test_campo_faltando_reprova(self, monkeypatch):
        completo = {
            "commit_regua": "abc1234",
            "hash_snapshot": "deadbeef",
            "janela": "2024-04-01..2026-04-01",
            "n_trials": 8000,
            "dsr": 0.97,
        }
        for campo in P.CAMPOS_PROCEDENCIA:
            parcial = dict(completo)
            parcial[campo] = None
            monkeypatch.setitem(P.PROCEDENCIA, "BTCUSDT", parcial)
            assert P.params_confiaveis("BTCUSDT") is False, (
                f"{campo}=None deveria reprovar"
            )

    def test_dsr_abaixo_do_minimo_reprova(self, monkeypatch):
        # O gate nao e "tem os campos", e "tem os campos E passou". Um DSR de
        # 0,94 com os cinco campos bonitos continua sendo reprovacao.
        quase = {
            "commit_regua": "abc1234",
            "hash_snapshot": "deadbeef",
            "janela": "2024-04-01..2026-04-01",
            "n_trials": 8000,
            "dsr": P.DSR_MINIMO - 0.01,
        }
        monkeypatch.setitem(P.PROCEDENCIA, "BTCUSDT", quase)
        assert P.params_confiaveis("BTCUSDT") is False

    def test_procedencia_completa_aprova(self, monkeypatch):
        # O caminho positivo precisa existir, senao a trava seria so um
        # `return False` disfarcado e ninguem notaria.
        bom = {
            "commit_regua": "abc1234",
            "hash_snapshot": "deadbeef",
            "janela": "2024-04-01..2026-04-01",
            "n_trials": 8000,
            "dsr": 0.96,
        }
        monkeypatch.setitem(P.PROCEDENCIA, "BTCUSDT", bom)
        assert P.params_confiaveis("BTCUSDT") is True

    def test_dsr_nao_numerico_reprova(self, monkeypatch):
        monkeypatch.setitem(
            P.PROCEDENCIA,
            "BTCUSDT",
            {
                "commit_regua": "abc",
                "hash_snapshot": "def",
                "janela": "x",
                "n_trials": 10,
                "dsr": "pendente",
            },
        )
        assert P.params_confiaveis("BTCUSDT") is False


class TestTravaDeCapitalReal:
    def test_exigir_recusa_par_nao_auditado(self):
        with pytest.raises(P.ParametrosNaoAuditados, match="CAPITAL REAL BLOQUEADO"):
            P.exigir_params_auditados(["BTCUSDT"])

    def test_mensagem_diz_quais_campos_faltam(self):
        with pytest.raises(P.ParametrosNaoAuditados) as exc:
            P.exigir_params_auditados(["SOLUSDT"])
        texto = str(exc.value)
        assert "SOLUSDT" in texto
        for campo in P.CAMPOS_PROCEDENCIA:
            assert campo in texto, f"a mensagem nao diz que {campo} falta"

    def test_um_par_ruim_no_meio_ja_recusa(self, monkeypatch):
        bom = {
            "commit_regua": "abc",
            "hash_snapshot": "def",
            "janela": "x",
            "n_trials": 10,
            "dsr": 0.99,
        }
        monkeypatch.setitem(P.PROCEDENCIA, "BTCUSDT", bom)
        monkeypatch.setitem(P.PROCEDENCIA, "ETHUSDT", bom)
        # SOL segue sem procedencia
        with pytest.raises(P.ParametrosNaoAuditados, match="SOLUSDT"):
            P.exigir_params_auditados(["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    def test_todos_auditados_libera(self, monkeypatch):
        bom = {
            "commit_regua": "abc",
            "hash_snapshot": "def",
            "janela": "x",
            "n_trials": 10,
            "dsr": 0.99,
        }
        for par in P.PARAMS_PARES:
            monkeypatch.setitem(P.PROCEDENCIA, par, dict(bom))
        P.exigir_params_auditados(list(P.PARAMS_PARES))  # nao levanta

    def test_main_chama_a_trava_no_caminho_real(self):
        # A trava so vale se estiver LIGADA. Sem esta checagem, alguem poderia
        # remover a chamada de main.py e a suite seguiria verde.
        import inspect
        import re

        import main

        fonte = inspect.getsource(main)
        # tira comentarios: o proprio texto explicativo cita o nome da funcao
        sem_comentarios = "\n".join(
            linha for linha in fonte.splitlines() if not linha.lstrip().startswith("#")
        )
        assert re.search(r"exigir_params_auditados\(", sem_comentarios), (
            "main.py nao chama exigir_params_auditados — a trava de E-9 esta solta"
        )


class TestParametrosSeguemLegiveis:
    """A trava barra CAPITAL REAL, não a leitura. Backtest e paper precisam
    conseguir ler parâmetros não auditados — é com eles que se mede."""

    def test_get_params_funciona_sem_procedencia(self):
        p = P.get_params("BTCUSDT")
        assert p["stop_pct"] == 0.015
        assert p["target_pct"] == 0.050

    def test_par_desconhecido_cai_no_default(self):
        assert P.get_params("XYZUSDT") == P.PARAMS_DEFAULT

    def test_os_numeros_nao_auditaveis_ficam_registrados(self):
        # O cabecalho antigo afirmava Sharpe 3,24/1,79/2,98 e retornos de ate
        # +122%. Apagar isso seria perder o registro da afirmacao; mante-lo no
        # lugar de honra seria continuar citando. Fica num dict de historico.
        for par in P.PARAMS_PARES:
            assert par in P._HISTORICO_NAO_AUDITAVEL
        assert "3.24" in P._HISTORICO_NAO_AUDITAVEL["BTCUSDT"]

    def test_os_sharpes_nao_estao_mais_no_bloco_de_parametros(self):
        # Trava contra a reincidencia: numero de performance dentro do dict de
        # parametros e o que fazia qualquer leitor tratar 3,24 como fato.
        import inspect

        fonte = inspect.getsource(P)
        bloco = fonte.split("PARAMS_PARES = {", 1)[1].split("\nPROCEDENCIA", 1)[0]
        for proibido in ("Sharpe", "WR ", "Ret +", "DD "):
            assert proibido not in bloco, (
                f"{proibido!r} voltou para o bloco de parametros"
            )
