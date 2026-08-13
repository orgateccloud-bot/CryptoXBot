"""
Vigia da primeira medição do micro_lab (frente E-11)
=====================================================
Roda diariamente pelo Task Scheduler. Quando a série do livro acumular as
barras mínimas da porção de PESQUISA (`micro_lab.MIN_BARRAS_PESQUISA`),
executa a PRIMEIRA medição oficial — o comando congelado da metodologia,
`--par BTCUSDT` — e entrega o resultado no Telegram do operador.

Duas regras que não são detalhe:

1. **Roda UMA vez.** Se `microestrutura_pesquisa_BTCUSDT.json` já existe, é
   no-op para sempre. Cada execução de `rodar_pesquisa` registra 15 sharpes
   no contador de trials que deflaciona o DSR do hold-out; re-medir todo dia
   engordaria o deflator sem produzir informação nova. Medições seguintes são
   ato deliberado do operador, não de um cron.
2. **Só a PESQUISA.** O hold-out (01/12+) é de uso único e continua atrás da
   trava própria do micro_lab — este vigia não chega perto dele.

USO:
    python research/medir_quando_pronto.py           # checa; mede se pronto
    python research/medir_quando_pronto.py --status  # só mostra a contagem

Agendamento (criado em 2026-08-13):
    schtasks /Create /TN "CryptoXbot Micro Lab" /SC DAILY /ST 08:00 ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from research import micro_lab as ml  # noqa: E402

PAR_OFICIAL = "BTCUSDT"  # o comando congelado da metodologia


def _verdito_pesquisa_path() -> str:
    return os.path.join(ml.DIR_VEREDITOS, f"microestrutura_pesquisa_{PAR_OFICIAL}.json")


def contar_barras_pesquisa() -> int:
    """Barras completas (tape+livro) na porção de PESQUISA — a régua exata
    que `rodar_pesquisa` vai usar, não um proxy."""
    tape = ml.carregar_tape_minuto(PAR_OFICIAL)
    book = ml.carregar_book_minuto(PAR_OFICIAL)
    barras = ml.construir_barras(tape, book)
    ts = barras["ts"]
    if not len(ts):
        return 0
    return int(ml.particionar(ts)[0].sum())


def _notificar(texto: str) -> bool:
    """Telegram com o canal do bot; falha de entrega não derruba o vigia —
    o veredito em research/vereditos/ é a fonte de verdade, o aviso é cortesia."""
    try:
        import telegram_bot as tb

        ok, detalhe = tb._enviar(texto, devolver_detalhe=True)
        if not ok:
            print(f"[VIGIA] Telegram nao entregue: {detalhe}")
        return bool(ok)
    except Exception as e:  # pragma: no cover - defensivo
        print(f"[VIGIA] Telegram indisponivel: {e}")
        return False


def _formatar_resultado(r: dict) -> str:
    sig = "SIM" if r["p_valor"] < 0.01 else "nao"
    return (
        "\U0001f52c CryptoXbot — PRIMEIRA MEDICAO da microestrutura (E-11)\n"
        f"Porcao de PESQUISA, {r['n_barras']} barras, par {r['symbol']}.\n\n"
        f"|IC| max: {r['ic_max_abs']:.4f}  (p={r['p_valor']:.4f}, significativo: {sig})\n"
        f"Melhor combo: {r['melhor_feature']} @ H={r['melhor_horizonte']} "
        f"(IC {r['melhor_ic']:+.4f})\n"
        f"Custo roundtrip usado: {r['custo_roundtrip']*100:.2f}%\n\n"
        "Isto NAO e o veredito da hipotese — e o retrato da pesquisa. O\n"
        "hold-out (uso unico) abre em 01/12. Detalhes em research/vereditos/."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--status", action="store_true", help="so mostra a contagem")
    args = ap.parse_args(argv)

    agora = datetime.now().isoformat(timespec="seconds")

    if os.path.exists(_verdito_pesquisa_path()):
        print(f"[VIGIA {agora}] primeira medicao JA FEITA — no-op (de proposito).")
        return 0

    n = contar_barras_pesquisa()
    alvo = ml.MIN_BARRAS_PESQUISA
    print(f"[VIGIA {agora}] barras completas na pesquisa: {n}/{alvo}")

    if args.status or n < alvo:
        return 0

    print(f"[VIGIA {agora}] alvo atingido — rodando a medicao oficial ({PAR_OFICIAL})...")
    try:
        r = ml.rodar_pesquisa(PAR_OFICIAL)
    except SystemExit as e:
        # corrida rara: a contagem passou mas o lab recusou (ex.: cobertura
        # caiu entre a checagem e a carga). Amanha o vigia tenta de novo.
        print(f"[VIGIA] micro_lab recusou: {e}")
        _notificar(
            "⚠️ CryptoXbot — o vigia do micro_lab atingiu a contagem "
            f"mas a medicao foi recusada: {e}"
        )
        return 1

    print(json.dumps(r, ensure_ascii=False, indent=2, default=float))
    _notificar(_formatar_resultado(r))
    print("[VIGIA] medicao registrada; este vigia vira no-op daqui em diante.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
