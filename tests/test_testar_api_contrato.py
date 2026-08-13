"""
Contrato entre testar_api.py e binance_conta.restricoes_chave()
================================================================
O defeito que motiva este arquivo (2026-08-13): `testar_api` lia os nomes
CRUS da API Binance (`enableSpotAndMarginTrading`, `enableWithdrawals`,
`ipRestrict`) de um dicionário que devolve nomes em português. `bool(None)`
= False em todos — no spot/IP o erro reprovava chave boa (fail-closed, chato
mas seguro); no SAQUE era o inverso: **uma chave que PODE sacar recebia
"[OK]"** da ferramenta cujo único propósito é barrar exatamente isso.

Foi descoberto medindo uma chave real recém-endurecida que o veredito
insistia em chamar de read-only. Estes testes travam o contrato nos dois
lados para que os nomes nunca mais divirjam em silêncio.
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import binance_conta
import testar_api


def _rodar_com(restr: dict, capsys) -> tuple[int, str]:
    with (
        # no CI nao existe .env: a guarda de credencial de diagnosticar()
        # barraria antes do mock — as chaves falsas mantem o teste hermetico
        mock.patch.object(testar_api, "API_KEY", "chave-falsa-de-teste"),
        mock.patch.object(testar_api, "API_SECRET", "segredo-falso-de-teste"),
        mock.patch.object(binance_conta, "restricoes_chave", return_value=restr),
        mock.patch.object(binance_conta, "chave_configurada", return_value=True),
        mock.patch.object(
            binance_conta,
            "ler_conta",
            return_value={
                "ok": True,
                "autenticado": True,
                "erro": None,
                "permissoes": ["SPOT"],
                "pode_operar": True,
                "saldos": {},
            },
        ),
    ):
        codigo = testar_api.diagnosticar()
    return codigo, capsys.readouterr().out


CHAVE_BOA = {
    "ok": True,
    "erro": None,
    "pode_negociar_spot": True,
    "pode_sacar": False,
    "pode_futures": False,
    "restrito_por_ip": True,
    "somente_leitura": False,
}


class TestContratoDeNomes:
    def test_chave_endurecida_e_reconhecida(self, capsys):
        """O caso real de 2026-08-13: spot+IP ativos, saque off — a versao
        com nomes errados chamava isto de read-only sem IP."""
        codigo, saida = _rodar_com(CHAVE_BOA, capsys)
        assert "PODE enviar ordens spot" in saida
        assert "nao tem restricao de IP" not in saida
        assert codigo == 0

    def test_chave_que_SACA_e_denunciada(self, capsys):
        """A direcao perigosa do defeito: pode_sacar=True tem que gritar.
        Com os nomes errados, bool(None)=False dava '[OK]' para ela."""
        restr = dict(CHAVE_BOA, pode_sacar=True)
        _, saida = _rodar_com(restr, capsys)
        assert "permite SAQUE" in saida

    def test_chave_read_only_continua_reprovada(self, capsys):
        restr = dict(CHAVE_BOA, pode_negociar_spot=False, somente_leitura=True)
        _, saida = _rodar_com(restr, capsys)
        assert "NAO pode enviar ordens spot" in saida

    def test_sem_ip_continua_avisando(self, capsys):
        restr = dict(CHAVE_BOA, restrito_por_ip=False)
        _, saida = _rodar_com(restr, capsys)
        assert "nao tem restricao de IP" in saida

    def test_nomes_lidos_existem_no_produtor(self):
        """O outro lado do contrato: se alguem renomear os campos em
        binance_conta, este teste aponta a divergencia na hora — em vez de
        o bool(None) voltar a mentir em silencio."""
        import inspect
        import re

        fonte_consumidor = inspect.getsource(testar_api)
        lidos = set(re.findall(r"restr\.get\(\"(\w+)\"\)", fonte_consumidor))
        fonte_produtor = inspect.getsource(binance_conta.restricoes_chave)
        produzidos = set(re.findall(r"\"(\w+)\":", fonte_produtor))
        faltando = lidos - produzidos
        assert not faltando, f"testar_api le campos que restricoes_chave nao produz: {faltando}"
