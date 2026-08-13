"""
Diagnóstico de credenciais da Binance — CryptoXbot
====================================================
M-1: esta ferramenta dava LUZ VERDE a uma chave READ-ONLY.

Ela imprimia `permissions` de `GET /api/v3/account` e concluía
"Chaves de API validadas com sucesso!". Mas `permissions` e `canTrade` desse
endpoint são da **CONTA**, não da CHAVE — e ficaram `True` nesta conta
enquanto a chave era read-only. A armadilha está documentada em
`docs/GATE_GO_LIVE.md:196-199` e já enganou uma investigação.

Quem responde "esta chave consegue mandar ordem?" é
`GET /sapi/v1/account/apiRestrictions` → `enableSpotAndMarginTrading`, já
implementado em `binance_conta.restricoes_chave()`.

Agora o script:
  * separa o que é da CONTA do que é da CHAVE, e diz qual é qual;
  * só afirma "pode operar" com base na restrição da chave;
  * sai com código != 0 quando a chave NÃO pode operar, para que um script
    que encadeie `&&` não siga adiante achando que está tudo certo.

Uso:
  python testar_api.py
"""

import sys

from config.runtime_settings import API_KEY, API_SECRET, REST_BASE_URL


def _linha(rotulo: str, valor, ok: bool | None = None) -> str:
    marca = "" if ok is None else ("  [OK]" if ok else "  [!!]")
    return f"  {rotulo:<34} {valor}{marca}"


def diagnosticar() -> int:
    """0 se a chave pode operar spot; != 0 caso contrário."""
    if not API_KEY or not API_SECRET:
        print("[ERRO] BINANCE_API_KEY / BINANCE_API_SECRET nao definidas.")
        return 2

    print("=" * 62)
    print("  DIAGNOSTICO DE CREDENCIAIS — BINANCE SPOT")
    print("=" * 62)
    print(f"  Endpoint: {REST_BASE_URL}")

    # ── 1. A CONTA responde? ──────────────────────────────────────
    try:
        import binance_conta

        conta = binance_conta.ler_conta()
    except Exception as e:
        print(f"\n[ERRO] Nao foi possivel consultar a conta: {e}")
        return 2

    if not conta.get("ok"):
        print(f"\n[ERRO] {conta.get('erro') or 'leitura da conta falhou'}")
        return 2

    # As chaves sao as de `binance_conta.ler_conta` (saldos / permissoes /
    # pode_operar), NAO as cruas da Binance (balances / permissions /
    # canTrade): usar as cruas fazia esta secao imprimir "nenhum saldo" e "?"
    # mesmo com a conta respondendo normalmente.
    print("\n  [1] CONTA (GET /api/v3/account)")
    saldos = conta.get("saldos") or {}
    if saldos:
        for ativo, livre in list(saldos.items())[:10]:
            print(_linha(ativo, f"livre: {float(livre):,.8f}"))
    else:
        print(_linha("saldos", "nenhum ativo com saldo livre"))
    print(_linha("permissoes (da CONTA)", ", ".join(conta.get("permissoes") or ["?"])))
    print(_linha("pode_operar / canTrade (CONTA)", conta.get("pode_operar")))
    print("      ^ NAO diz se a CHAVE pode operar — ver [2].")

    # ── 2. A CHAVE pode operar? ───────────────────────────────────
    print("\n  [2] CHAVE (GET /sapi/v1/account/apiRestrictions)")
    try:
        restr = binance_conta.restricoes_chave()
    except Exception as e:
        print(f"      [ERRO] {e}")
        print("\n  VEREDITO: INDETERMINADO — sem a restricao da chave nao da")
        print("            para afirmar que ela opera. Tratando como NAO.")
        return 1

    if isinstance(restr, dict) and restr.get("erro"):
        print(f"      [ERRO] {restr['erro']}")
        print("\n  VEREDITO: INDETERMINADO — tratando como NAO.")
        return 1

    # As chaves do dicionario de restricoes_chave() sao em PORTUGUES
    # (pode_negociar_spot etc.) — este arquivo lia os nomes CRUS da API
    # (enableSpotAndMarginTrading), que nao existem no dict: bool(None)=False
    # em TODO campo. No spot/IP o erro era fail-closed (reprovava chave boa);
    # no SAQUE era o oposto e perigoso — `enableWithdrawals` ausente virava
    # False e a chave que PODE sacar ganhava "[OK]". Descoberto em 2026-08-13
    # medindo uma chave real recem-endurecida que o veredito insistia em
    # chamar de read-only.
    pode_spot = bool(restr.get("pode_negociar_spot"))
    saque = bool(restr.get("pode_sacar"))
    ip_restrito = bool(restr.get("restrito_por_ip"))

    print(_linha("pode_negociar_spot", pode_spot, ok=pode_spot))
    print(_linha("pode_futures", restr.get("pode_futures")))
    print(_linha("pode_sacar", saque, ok=not saque))
    print(_linha("restrito_por_ip", ip_restrito, ok=ip_restrito))

    # ── 3. Veredito ───────────────────────────────────────────────
    print("\n" + "=" * 62)
    if pode_spot:
        print("  VEREDITO: a chave PODE enviar ordens spot.")
    else:
        print("  VEREDITO: a chave NAO pode enviar ordens spot (read-only).")
        print("            Em paper isso e inofensivo. Para capital real,")
        print("            habilitar 'Enable Spot & Margin Trading' na Binance.")

    if saque:
        print("\n  [!!] A chave permite SAQUE. Um bot de trading nunca precisa")
        print("       disso — desabilite na Binance.")
    if not ip_restrito:
        print("\n  [!!] A chave nao tem restricao de IP. Com a maquina rodando")
        print("       24/7 num IP conhecido, restringir fecha a janela de uso")
        print("       caso a chave vaze.")
    print("=" * 62)

    # Sai != 0 quando nao pode operar: um `testar_api.py && deploy` nao pode
    # seguir adiante so porque a CONTA respondeu.
    return 0 if pode_spot else 1


if __name__ == "__main__":
    sys.exit(diagnosticar())
