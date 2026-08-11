"""
Parâmetros por Par — CryptoXbot
================================

    ┌───────────────────────────────────────────────────────────────────┐
    │  ESTES PARÂMETROS NÃO SÃO AUDITÁVEIS E NÃO AUTORIZAM CAPITAL REAL │
    └───────────────────────────────────────────────────────────────────┘

Frente E-9. O arquivo governa o trading ao vivo, mas a sua procedência não
resiste a exame:

* Veio de um grid de até 8.000 combinações **sem out-of-sample**. O vencedor
  de 8.000 sorteios sobre uma série única tem Sharpe alto por construção — é
  o máximo de uma amostra, não uma estimativa.
* A ordenação usava um Sharpe calculado sobre retorno **bruto** de taxa e de
  sizing, anualizado a 252 sobre retornos **por trade**, com piso de 5 trades.
* A régua tinha look-ahead no filtro MTF (`idx4 = i//4`, corrigido em I-12b) e
  Fear & Greed fixo em 100 (corrigido em I-12g).
* O último commit do arquivo (`5991003`, 2026-06-22) é **anterior** à versão
  atual do otimizador. Não há como reproduzir o que o gerou.

Os números que este cabeçalho exibia — Sharpe 3,24 / 1,79 / 2,98 e retornos
+57,6% / +35,9% / +122,0% — estão preservados abaixo em `_HISTORICO_NAO_AUDITAVEL`
apenas como registro do que foi afirmado. **É proibido citá-los como evidência
em qualquer documento de decisão.** Eles contradizem tudo que foi medido desde
então com régua auditável:

    par        cabeçalho antigo    medido (I-12, régua unificada)
    BTCUSDT         +57,6%          -59,10%   Sharpe -3,31   PSR 0,00
    ETHUSDT         +35,9%          -57,73%   Sharpe -1,01   PSR 0,08

E a Etapa 1 do gate (`docs/GATE_GO_LIVE.md`) reprovou em 4 de 5 critérios.

────────────────────────────────────────────────────────────────────────────
OS CINCO CAMPOS DE PROCEDÊNCIA

Nenhum conjunto de parâmetros entra em produção sem os cinco campos de
`PROCEDENCIA` preenchidos. Enquanto qualquer um for None, `params_confiaveis()`
devolve False e `exigir_params_auditados()` recusa o modo real.

    commit_regua    commit da régua de medição usada (backtesting/regua.py)
    hash_snapshot   sha256 do manifest do snapshot (data/snapshots/*/manifest.json)
    janela          período medido, incluindo o hold-out
    n_trials        número REAL de combinações testadas (entra no DSR)
    dsr             Deflated Sharpe Ratio no hold-out; o gate exige >= 0,95

`tests/test_params_procedencia.py` falha se algum par perder um desses campos.
────────────────────────────────────────────────────────────────────────────
"""

# Registro do que o cabeçalho antigo afirmava. Mantido para que a afirmação
# não desapareça do histórico — NÃO é evidência de nada.
_HISTORICO_NAO_AUDITAVEL = {
    "BTCUSDT": "grid 144 combinações: 185 trades, WR 33.5%, Ret +57.6%, DD 8.98%, Sharpe 3.24",
    "ETHUSDT": "grid 8000 combinações: 282 trades, WR 30.9%, Ret +35.9%, DD 14.4%, Sharpe 1.79",
    "SOLUSDT": "grid 8000 combinações: 343 trades, WR 47.5%, Ret +122.0%, DD 13.4%, Sharpe 2.98",
}

# Os cinco campos que um conjunto de parâmetros precisa ter para ser auditável.
CAMPOS_PROCEDENCIA = ("commit_regua", "hash_snapshot", "janela", "n_trials", "dsr")

# Estado atual: NENHUM par foi re-derivado. Todos os cinco campos None é a
# resposta honesta — não é "faltou preencher", é "não existe medição que
# sustente estes números".
_SEM_PROCEDENCIA = dict.fromkeys(CAMPOS_PROCEDENCIA)

PARAMS_PARES = {
    "BTCUSDT": {
        "stop_pct": 0.015,  # 1.5%
        "target_pct": 0.050,  # 5.0%
        "rsi_min": 42,
        "rsi_max": 60,
        "score_operar": 60,
        "score_cheio": 70,
    },
    "ETHUSDT": {
        "stop_pct": 0.020,  # 2.0%
        "target_pct": 0.060,  # 6.0%
        "rsi_min": 38,
        "rsi_max": 62,
        "score_operar": 50,
        "score_cheio": 65,
    },
    "SOLUSDT": {
        "stop_pct": 0.030,  # 3.0%  (SOL mais volátil, stop maior)
        "target_pct": 0.050,  # 5.0%
        "rsi_min": 38,
        "rsi_max": 60,
        "score_operar": 50,
        "score_cheio": 65,
    },
}

PROCEDENCIA = {par: dict(_SEM_PROCEDENCIA) for par in PARAMS_PARES}

# Parâmetros default (fallback para pares não configurados)
PARAMS_DEFAULT = PARAMS_PARES["BTCUSDT"]

# DSR mínimo no hold-out para um conjunto de parâmetros valer para o gate.
DSR_MINIMO = 0.95


def get_params(symbol: str) -> dict:
    """Retorna os parâmetros do par. Fallback para BTC se não configurado.

    Não checa procedência de propósito: o backtest e o paper trading PRECISAM
    conseguir ler parâmetros não auditados — é justamente com eles que se mede.
    Quem barra o capital real é `exigir_params_auditados()`.
    """
    return PARAMS_PARES.get(symbol.upper(), PARAMS_DEFAULT)


def procedencia(symbol: str) -> dict:
    """Os cinco campos de procedência do par (valores None = não auditado)."""
    return dict(PROCEDENCIA.get(symbol.upper(), _SEM_PROCEDENCIA))


def params_confiaveis(symbol: str) -> bool:
    """True só se os cinco campos estão preenchidos E o DSR passa do mínimo."""
    p = procedencia(symbol)
    if any(p.get(campo) in (None, "") for campo in CAMPOS_PROCEDENCIA):
        return False
    try:
        return float(p["dsr"]) >= DSR_MINIMO
    except (TypeError, ValueError):
        return False


def pares_nao_auditados() -> list[str]:
    """Pares que ainda não têm procedência completa — hoje, todos."""
    return sorted(par for par in PARAMS_PARES if not params_confiaveis(par))


def exigir_params_auditados(symbols) -> None:
    """Levanta se algum par for operar com capital real sem procedência.

    Chamado no caminho de trading real (main.py). É a trava que dá sentido
    prático ao cabeçalho: sem os cinco campos, o arquivo não entra em produção.
    Paper trading e backtest não passam por aqui.
    """
    faltando = [s for s in symbols if not params_confiaveis(s)]
    if not faltando:
        return
    detalhe = "\n".join(
        f"    {s}: " + ", ".join(f"{c}={procedencia(s).get(c)!r}" for c in CAMPOS_PROCEDENCIA)
        for s in faltando
    )
    raise ParametrosNaoAuditados(
        "CAPITAL REAL BLOQUEADO (E-9): parametros sem procedencia auditavel.\n"
        f"{detalhe}\n"
        "  Os cinco campos de PROCEDENCIA precisam estar preenchidos e o DSR no\n"
        f"  hold-out precisa ser >= {DSR_MINIMO}. Ver o cabecalho de\n"
        "  config/params_pares.py e a frente E-9 em docs/RELATORIO_MODULOS.md."
    )


class ParametrosNaoAuditados(RuntimeError):
    """Parâmetros sem os cinco campos de procedência tentaram ir a real."""
