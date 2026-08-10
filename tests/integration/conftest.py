"""
Guardas da suite de integracao (I-10i) — Binance Spot Testnet
==============================================================
Esta suite envia ORDENS DE VERDADE. Contra o testnet, com moedas de brinquedo,
mas ordens reais num livro real. Por isso ela e desligada por default e tem
tres travas independentes; qualquer uma que falhe impede a execucao:

  1. `RODAR_INTEGRACAO_TESTNET=1` explicito — nao basta ter credencial no
     ambiente, tem de haver intencao.
  2. `BINANCE_TESTNET_API_KEY` e `BINANCE_TESTNET_API_SECRET` presentes. Sao
     variaveis PROPRIAS: nunca reaproveitamos `BINANCE_API_KEY`, para que uma
     credencial de producao no ambiente nao seja usada aqui por acidente.
  3. O endpoint e HARDCODED aqui (`URL_TESTNET`), nao lido de
     `config/runtime_settings.py`. Um `.env` mal configurado apontando para
     `api.binance.com` nao tem como vazar para dentro destes testes.

A trava 3 e o motivo de o endereco nao ser configuravel. Se um dia precisar
ser, a checagem `"testnet" in URL` tem de continuar existindo.
"""

import os

import pytest

URL_TESTNET = "https://testnet.binance.vision"

# Cinto e suspensorio: se alguem trocar a constante acima por um endereco de
# producao, a suite se recusa a rodar em vez de mandar ordem com dinheiro real.
assert "testnet" in URL_TESTNET, "endpoint da suite de integracao NAO e testnet"

_LIGADA = os.getenv("RODAR_INTEGRACAO_TESTNET", "").strip().lower() in {"1", "true", "yes", "on"}
_CHAVE = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
_SEGREDO = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()


def motivo_do_skip() -> str | None:
    """Devolve o motivo de pular, ou None se a suite pode rodar."""
    if not _LIGADA:
        return (
            "suite de integracao desligada — exporte RODAR_INTEGRACAO_TESTNET=1 "
            "(ver tests/integration/LEIA-ME.md)"
        )
    if not _CHAVE or not _SEGREDO:
        return (
            "faltam BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET "
            "(chaves PROPRIAS de testnet, nunca as de producao)"
        )
    return None


@pytest.fixture(scope="session")
def credenciais_testnet():
    return {"url": URL_TESTNET, "chave": _CHAVE, "segredo": _SEGREDO}
