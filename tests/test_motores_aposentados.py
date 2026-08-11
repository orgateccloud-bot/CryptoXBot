"""
Testes — aposentadoria de motores e portao do motor.py (frente I-12d)
======================================================================
Dois grupos:

1. APOSENTADORIA (@Zeta). `motor_otimizado.py` e `motor_vectorbt.py` foram
   movidos para `_legado/`. O risco de uma aposentadoria e a ressurreicao
   silenciosa: alguem re-adiciona um import e o modulo volta a rodar com os
   defeitos que motivaram a saida (look-ahead por `i//4`, paridade de sizing
   quebrada, referencia a uma funcao que nao existe mais). Estes testes falham
   se isso acontecer.

2. PORTAO DO motor.py. Nao e orfao — `main.py --backtest` chama — mas oito das
   entradas de `score.calcular` sao constantes hardcoded, duas delas
   permanentemente altistas. Mesma decisao da rota `/api/backtest` (I-12a): a
   medicao nao roda sem opt-in explicito.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APOSENTADOS = ("motor_otimizado", "motor_vectorbt")


class TestAposentadoria:
    def test_arquivos_estao_em_legado_e_nao_em_backtesting(self):
        for nome in APOSENTADOS:
            assert os.path.exists(
                os.path.join(RAIZ, "_legado", f"{nome}.py")
            ), f"{nome}.py sumiu de _legado/ — aposentar e MOVER, nunca deletar"
            assert not os.path.exists(
                os.path.join(RAIZ, "backtesting", f"{nome}.py")
            ), f"{nome}.py voltou para backtesting/ sem passar pelo LEIA-ME"
        assert os.path.exists(os.path.join(RAIZ, "_legado", "test_motor_vectorbt.py"))

    def test_nenhum_codigo_vivo_importa_os_aposentados(self):
        # Varre a arvore viva (fora de _legado/, .venv/ e caches) atras de
        # IMPORTS dos modulos aposentados. Mencao em comentario ou docstring
        # (nota historica) e legitima; o que ressuscita o modulo e um import.
        ignorar = {".venv", "_legado", "__pycache__", ".git", "scratch_vbt"}
        este_arquivo = os.path.abspath(__file__)
        ofensores = []
        for raiz, dirs, arquivos in os.walk(RAIZ):
            dirs[:] = [d for d in dirs if d not in ignorar]
            for arq in arquivos:
                if not arq.endswith(".py"):
                    continue
                caminho = os.path.join(raiz, arq)
                if os.path.abspath(caminho) == este_arquivo:
                    continue  # o proprio scanner cita os nomes que procura
                with open(caminho, encoding="utf-8", errors="ignore") as f:
                    for n_linha, linha in enumerate(f, 1):
                        limpa = linha.lstrip()
                        if limpa.startswith("#"):
                            continue
                        if not (limpa.startswith("import ") or limpa.startswith("from ")):
                            continue
                        for nome in APOSENTADOS:
                            if nome in limpa:
                                rel = os.path.relpath(caminho, RAIZ)
                                ofensores.append(f"{rel}:{n_linha}: {limpa.strip()}")
        assert not ofensores, "codigo vivo importando modulo aposentado:\n" + "\n".join(ofensores)

    def test_leia_me_documenta_os_dois_com_rollback(self):
        with open(os.path.join(RAIZ, "_legado", "LEIA-ME.md"), encoding="utf-8") as f:
            leia_me = f.read()
        for nome in APOSENTADOS:
            assert f"{nome}.py" in leia_me, f"{nome} aposentado sem entrada no LEIA-ME"
            # o rollback tem de ser executavel, nao uma promessa
            assert (
                f"git mv _legado/{nome}.py" in leia_me
            ), f"{nome} sem comando de rollback no LEIA-ME"


class TestPortaoDoMotorComMocks:
    def test_rodar_backtest_recusa_por_padrao(self, monkeypatch):
        from backtesting.motor import MedicaoComMocks, rodar_backtest

        monkeypatch.delenv("BACKTEST_MOCKS", raising=False)
        with pytest.raises(MedicaoComMocks, match="hardcoded"):
            rodar_backtest("1h", 1000.0)

    @pytest.mark.parametrize("valor", ["0", "", "nao", "false", "off"])
    def test_valores_que_nao_liberam(self, monkeypatch, valor):
        from backtesting.motor import MedicaoComMocks, rodar_backtest

        monkeypatch.setenv("BACKTEST_MOCKS", valor)
        with pytest.raises(MedicaoComMocks):
            rodar_backtest("1h", 1000.0)

    def test_recusa_acontece_antes_de_tocar_o_banco(self, monkeypatch):
        # A recusa nao pode depender de haver dados: se `carregar_klines`
        # rodasse antes, um ambiente sem banco daria "dados insuficientes" em
        # vez do bloqueio, e o motivo real ficaria escondido.
        import backtesting.motor as motor

        monkeypatch.delenv("BACKTEST_MOCKS", raising=False)

        def explodir(*a, **k):
            raise AssertionError("carregar_klines foi chamado antes do portao")

        monkeypatch.setattr(motor, "carregar_klines", explodir)
        with pytest.raises(motor.MedicaoComMocks):
            motor.rodar_backtest("1h", 1000.0)

    def test_mensagem_aponta_a_medicao_valida(self, monkeypatch):
        from backtesting.motor import MedicaoComMocks, rodar_backtest

        monkeypatch.delenv("BACKTEST_MOCKS", raising=False)
        with pytest.raises(MedicaoComMocks) as exc:
            rodar_backtest("1h", 1000.0)
        # nao basta recusar: tem de dizer o que rodar no lugar
        assert "walk_forward" in str(exc.value)

    def test_opt_in_programatico_passa_do_portao(self, monkeypatch):
        # `permitir_mocks=True` nao pode ser barrado — e o caminho consciente.
        # Para isolar do banco, o carregar_klines devolve serie vazia e a
        # funcao sai por "dados insuficientes", nao pelo portao.
        import backtesting.motor as motor

        monkeypatch.delenv("BACKTEST_MOCKS", raising=False)
        monkeypatch.setattr(motor, "carregar_klines", lambda intervalo: [])
        assert motor.rodar_backtest("1h", 1000.0, permitir_mocks=True) is None

    def test_taxa_e_de_spot_nao_de_futuros(self):
        # O bot executa /api/v3/order (SPOT). 0.0004 e tarifa de futuros e
        # sozinha ja moveu o resultado do motor_ensemble em dezenas de pontos.
        from backtesting.motor import TAXA_BINANCE

        assert TAXA_BINANCE == 0.001


class TestPortaoNosEntrypoints:
    """O portao so vale se os dois entrypoints saírem com codigo != 0 — um
    script que encadeia `&&` nao pode seguir como se a medicao tivesse valido."""

    def _rodar(self, args):
        env = dict(os.environ)
        env.pop("BACKTEST_MOCKS", None)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, *args],
            cwd=RAIZ,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_cli_do_motor_sai_com_codigo_2(self):
        p = self._rodar(["backtesting/motor.py", "--intervalo", "1h"])
        assert p.returncode == 2, f"stdout={p.stdout[-600:]} stderr={p.stderr[-600:]}"
        assert "MEDICAO BLOQUEADA" in p.stdout
        assert "VEREDITO" not in p.stdout, "imprimiu veredito apesar do bloqueio"

    def test_main_backtest_sai_com_codigo_2(self):
        p = self._rodar(["main.py", "--backtest", "1h"])
        assert p.returncode == 2, f"stdout={p.stdout[-600:]} stderr={p.stderr[-600:]}"
        assert "MEDICAO BLOQUEADA" in p.stdout
        assert "VEREDITO" not in p.stdout
