"""
Testes do endurecimento de segurança em config/runtime_settings.

Garante que a chave-padrão pública (`botbinance-local-dev`) nunca é usada em
produção: sem SECRET_KEY no ambiente, gera-se uma efêmera aleatória; com
SECRET_KEY definida, ela é respeitada.
"""

import importlib

import pytest

import config.runtime_settings as rs

DEFAULT = "botbinance-local-dev"


@pytest.fixture(autouse=True)
def _restaurar_runtime_settings(monkeypatch):
    # config.runtime_settings chama load_dotenv() no import/reload - sem isso,
    # um .env real no disco do dev (fora do controle do teste) vazaria para
    # dentro dos cenarios de "SECRET_KEY ausente" abaixo. Neutralizado aqui.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)
    yield
    # Após cada teste, recarrega o módulo num estado neutro (dev) para não
    # contaminar outros testes que importem config.runtime_settings.
    importlib.reload(rs)


def test_dev_usa_chave_padrao(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    importlib.reload(rs)
    assert rs.SECRET_KEY == DEFAULT


def test_producao_sem_secret_gera_efemera(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ENV", "production")
    importlib.reload(rs)
    assert rs.SECRET_KEY != DEFAULT
    # secrets.token_hex(32) -> 64 caracteres hexadecimais
    assert len(rs.SECRET_KEY) == 64
    int(rs.SECRET_KEY, 16)  # é hex válido (não lança)


def test_producao_respeita_secret_do_ambiente(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "chave-forte-do-usuario")
    importlib.reload(rs)
    assert rs.SECRET_KEY == "chave-forte-do-usuario"


def test_chaves_efemeras_diferentes_entre_reloads(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ENV", "production")
    importlib.reload(rs)
    k1 = rs.SECRET_KEY
    importlib.reload(rs)
    k2 = rs.SECRET_KEY
    assert k1 != k2  # cada inicialização gera uma chave nova
