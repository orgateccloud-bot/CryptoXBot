"""
conftest.py da raiz — isola a suíte do estado de PRODUÇÃO.

POR QUE ISTO EXISTE (incidente de 2026-07-31)
---------------------------------------------
Não havia nenhum conftest.py no repositório. Consequência medida, não teórica:

    `pytest tests/` gravava no banco de produção.

`tests/test_executor_monitor.py` instancia um `Executor` real e chama
`_persistir_posicao()`, que escreve em `config.runtime_settings.DB_PATH` —
`data/btc_data.db`, o mesmo arquivo que o serviço BXBotWorker usa 24/7. O banco
vivo ficou com:

    risk_state['posicao:BTCUSDT'] = {"entrada": 100.0, "order_id": "SIM-1",
                                     "abertura": "x", ...}

O boot do worker readotava essa posição sem validar nada. Com BTC a ~64.000, o
monitor via preço >> target1 (105.0) e "fechava" no primeiro tick, gravando em
`sinais` PnL de +62.658% e +64.429% — que alimentaram `registrar_resultado` e o
`pnl_dia` persistido. Aconteceu **três vezes** (09/07, 19/07, 31/07). Com chave
de trading habilitada, teria enviado um SELL real de BTC que nenhuma estratégia
comprou.

Agravante: o CLAUDE.md exige `pytest tests/ -v` antes de todo PR. No host de
produção, esse comando era uma operação destrutiva não sinalizada.

`tests/test_melhorias.py::TestRetreinamentoAutomatico` chama
`main._retreinar_modelos()` DE VERDADE, sobrescrevendo `data/modelo_*.pkl` que o
worker carrega em memória, e gravando em `model_metricas`.

O QUE ESTE ARQUIVO FAZ
----------------------
Redireciona, para TODA a suíte e antes de qualquer import de teste, o banco e o
diretório de artefatos para um `tmp_path` do pytest. É `autouse` e de escopo de
sessão: não depende de nenhum teste lembrar de pedir isolamento.

Segunda camada, independente desta: `Executor._posicao_e_plausivel` recusa
readotar posição cuja entrada divirja do mercado por ordem de magnitude, ou que
carregue marcas de fixture (`order_id` "SIM-*", `abertura` que não é ISO).
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Redirecionamento NO IMPORT, não numa fixture ───────────────
# A ordem importa, e custou uma depuração: o pytest importa conftest.py ANTES
# de coletar os módulos de teste, mas fixtures — mesmo session/autouse — só
# rodam DEPOIS da coleta. Vários módulos fazem `import logger` no topo, e
# logger.py:42 faz `self.db_path = db_path or DB_PATH`, com DB_PATH resolvido
# no import. Redirecionar apenas na fixture chegava tarde: o singleton
# `logger.logger` já existia apontando para o banco de PRODUÇÃO, e
# dados_relatorio_diario abria o arquivo vivo.
_TMP_ESTADO = tempfile.mkdtemp(prefix="bxbot_teste_")
os.environ["DB_PATH"] = os.path.join(_TMP_ESTADO, "test_btc_data.db")
os.environ.setdefault("DATABASE_BACKEND", "sqlite")
os.environ.pop("DATABASE_URL", None)  # teste nunca aponta para Postgres real
# A suíte assume /api/* SEM auth. Com DASHBOARD_TOKEN no .env do operador
# (produção o tem desde 2026-08-17), o middleware do dashboard ligaria e todo
# teste de API levaria 401 — medido em 2026-08-18: 46 falhas. String vazia
# EXISTE no ambiente, então o load_dotenv(override=False) do runtime_settings
# não a substitui pelo valor do .env; dashboard lê "" e o middleware fica
# desligado. Quem testa o próprio middleware (TestApiMesa) seta
# dashboard._DASHBOARD_TOKEN por monkeypatch, sem depender do ambiente.
os.environ["DASHBOARD_TOKEN"] = ""
os.makedirs(os.path.join(_TMP_ESTADO, "data"), exist_ok=True)


def _redirecionar_para(tmp_dir):
    """Aponta DB_PATH e afins para tmp_dir, nos módulos JÁ importados e nos
    que ainda vão importar (via env var, que runtime_settings lê primeiro)."""
    db = os.path.join(tmp_dir, "test_btc_data.db")
    os.environ["DB_PATH"] = db
    os.environ.setdefault("DATABASE_BACKEND", "sqlite")
    os.environ.pop("DATABASE_URL", None)  # nunca apontar teste para Postgres real

    # runtime_settings pode já ter sido importado por um teste anterior; o valor
    # é lido em tempo de import, então reescrevemos o atributo também.
    try:
        import config.runtime_settings as rs

        rs.DB_PATH = db
        rs.DATABASE_BACKEND = "sqlite"
        rs.DATABASE_URL = ""
    except Exception:
        pass

    # database.py cacheia o caminho em módulo em algumas versões.
    try:
        import database

        for attr in ("DB_PATH", "_DB_PATH", "DB_FILE"):
            if hasattr(database, attr):
                setattr(database, attr, db)
    except Exception:
        pass

    # O SINGLETON de logger.py captura db_path na construção, que acontece no
    # import — ou seja, ANTES desta fixture, durante a coleta do pytest. Sem
    # reescrever a instância, logger.dados_relatorio_diario abre o arquivo de
    # produção direto (logger.py:85). Foi o guard de sqlite3.connect que
    # denunciou isso; sem ele, seguiria lendo produção em silêncio.
    try:
        import logger as _logmod

        for alvo in (getattr(_logmod, "logger", None), _logmod):
            if alvo is not None and hasattr(alvo, "db_path"):
                alvo.db_path = db
    except Exception:
        pass
    return db


@pytest.fixture(scope="session", autouse=True)
def _isolar_estado_de_producao():
    """Segunda passada do redirecionamento, agora sobre os módulos que a coleta
    já importou. O bloco no topo deste arquivo cobre quem importa DEPOIS; esta
    fixture reescreve os atributos de quem importou ANTES (o singleton de
    logger.py é o caso real que quebrou)."""
    db = _redirecionar_para(_TMP_ESTADO)
    yield {"tmp_dir": _TMP_ESTADO, "db": db}


@pytest.fixture(autouse=True)
def _proibir_escrita_no_banco_de_producao(monkeypatch):
    """Rede de segurança: se algum caminho ainda tentar abrir o arquivo de
    produção para escrita, o teste FALHA em voz alta em vez de corromper o banco
    vivo em silêncio — que foi exatamente o modo de falha original."""
    import sqlite3

    real_connect = sqlite3.connect
    proibidos = {os.path.abspath("data/btc_data.db")}

    def connect_guardado(database, *a, **kw):
        try:
            alvo = os.path.abspath(str(database))
        except Exception:
            alvo = ""
        if alvo in proibidos:
            raise AssertionError(
                f"TESTE TENTOU ABRIR O BANCO DE PRODUÇÃO: {alvo}\n"
                f"Use o DB_PATH isolado do conftest. Ver o incidente de 2026-07-31 "
                f"no cabeçalho de conftest.py."
            )
        return real_connect(database, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", connect_guardado)
