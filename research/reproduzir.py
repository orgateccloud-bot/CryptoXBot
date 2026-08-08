"""
Re-derivação de vereditos a partir do snapshot congelado (I-11).
=================================================================

## O problema que este comando resolve

Os cinco FAILs pré-registrados são a única coisa que hoje impede o capital de
entrar. Até I-11 eles eram PROSA: números escritos em documentos, produzidos por
comandos que ninguém tinha guardado, sobre uma tabela que mudava sozinha.

A pergunta "será que o mercado mudou?" sempre chega — normalmente vinda de quem
está com pressa de ligar o capital. Um FAIL que não pode ser re-derivado não
sobrevive a essa pergunta.

Este módulo produz um veredito **verificável**: verifica o sha256 do substrato,
re-roda as medições, e compara com o registro anterior exigindo diferença 0.0.

## Como usar

    python -m research.reproduzir                      # roda e grava o registro
    python -m research.reproduzir --comparar           # compara com o registro
    python -m research.reproduzir --rotulo 2026-08-08  # snapshot específico

O critério de saída de I-11 é: `--comparar` retorna diferença 0.0 em todos os
ICs e em todos os vereditos, em duas máquinas diferentes.

## Uma honestidade necessária

Este comando NÃO re-deriva os vereditos ORIGINAIS de julho. A série sobre a qual
eles foram medidos não existe mais: a tabela `klines` cresceu de forma
não-append (BTCUSDT/1h passou de 17.520 para 17.563 velas, as 43 novas no
INÍCIO) e nunca houve um snapshot. O que este comando faz é fixar o substrato de
hoje e tornar re-derivável tudo daqui em diante.

Os números que ele produz na primeira execução são NOVOS. Registrá-los como se
fossem "a confirmação dos FAILs anteriores" seria exatamente o tipo de conforto
falso que I-11 existe para eliminar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import snapshot  # noqa: E402
from research.edge_lab import (  # noqa: E402
    FEATURE_NAMES,
    HOLDOUT_INICIO_MS,
    IC_PISO_ECONOMICO,
    rodar_pesquisa,
)

DIR_REGISTROS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vereditos")
TOLERANCIA = 0.0  # diferença 0.0, literal — é o critério de saída de I-11


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derivar(symbol: str = "BTCUSDT", seed: int = 42, rotulo: str | None = None) -> dict:
    """Re-deriva o veredito de edge para `symbol` sobre o snapshot.

    Aborta se o snapshot não bate com o manifest: um veredito derivado de
    substrato corrompido é pior que nenhum veredito, porque parece válido.
    """
    problemas = snapshot.verificar(rotulo)
    if problemas:
        raise SystemExit(
            "SNAPSHOT COMPROMETIDO — nenhuma re-derivação é válida:\n"
            + "\n".join(f"  {p}" for p in problemas)
        )
    manifest = snapshot.ler_manifest(rotulo)

    r = rodar_pesquisa(symbol, com_xgb=False, seed=seed)
    perm = r["perm"]
    fi = perm["melhor_feature_idx"]
    H = perm["melhor_horizonte"]
    ic_max = float(perm["obs_max"])

    # Mesma regra de decisão pré-registrada em METODOLOGIA.md: sobreviver ao
    # teste de permutação E superar o piso econômico. Reproduzida aqui como
    # CÓDIGO para que o veredito seja um valor comparável, não uma leitura.
    sobreviveu_permutacao = bool(perm["p"] < 0.05)
    supera_piso = bool(ic_max >= IC_PISO_ECONOMICO)
    veredito = "EDGE_CANDIDATO" if (sobreviveu_permutacao and supera_piso) else "FAIL"

    return {
        "gerado_em": _agora(),
        "snapshot": {
            "rotulo": manifest["rotulo"],
            "sha256_por_serie": {
                f"{s['symbol']}/{s['intervalo']}": s["sha256"] for s in manifest["series"]
            },
        },
        "parametros": {
            "symbol": symbol,
            "seed": seed,
            "holdout_inicio_ms": HOLDOUT_INICIO_MS,
            "ic_piso_economico": IC_PISO_ECONOMICO,
        },
        "medicoes": {
            "n_amostras": int(r["n_amostras"]),
            "melhor_feature": FEATURE_NAMES[fi],
            "melhor_horizonte": int(H),
            "ic_max_abs": ic_max,
            "p_permutacao": float(perm["p"]),
            # ICs de TODA a família — é o que permite dizer "diferença 0.0 em
            # todos os IC", e não só no vencedor.
            "ics": {
                f"{FEATURE_NAMES[i]}|H{h}": float(v) for (i, h), v in perm["ics_obs"].items()
            },
        },
        "veredito": veredito,
        "criterios": {
            "sobreviveu_permutacao": sobreviveu_permutacao,
            "supera_piso_economico": supera_piso,
        },
    }


def caminho_registro(symbol: str, rotulo: str) -> str:
    return os.path.join(DIR_REGISTROS, f"{rotulo}_{symbol}.json")


def gravar(resultado: dict) -> str:
    os.makedirs(DIR_REGISTROS, exist_ok=True)
    caminho = caminho_registro(
        resultado["parametros"]["symbol"], resultado["snapshot"]["rotulo"]
    )
    with open(caminho, "w", encoding="utf-8", newline="") as fp:
        json.dump(resultado, fp, indent=2, ensure_ascii=False, sort_keys=True)
        fp.write("\n")
    return caminho


def comparar(atual: dict, anterior: dict) -> list[str]:
    """Diferenças entre duas derivações. Lista vazia = reprodução exata.

    Compara o substrato ANTES das medições: se o sha256 mudou, qualquer
    diferença nos números é esperada e a comparação não significa nada — dizer
    "IC divergiu" nesse caso mandaria alguém depurar estatística quando o
    problema é o dado.
    """
    difs = []

    sha_a = atual["snapshot"]["sha256_por_serie"]
    sha_b = anterior["snapshot"]["sha256_por_serie"]
    if sha_a != sha_b:
        for chave in sorted(set(sha_a) | set(sha_b)):
            if sha_a.get(chave) != sha_b.get(chave):
                difs.append(
                    f"SUBSTRATO {chave}: {str(sha_b.get(chave))[:16]}... -> "
                    f"{str(sha_a.get(chave))[:16]}..."
                )
        difs.append(
            "  (o substrato mudou; diferenças numéricas abaixo são consequência disso, "
            "não falha de reprodutibilidade)"
        )

    for chave in ("holdout_inicio_ms", "seed", "ic_piso_economico"):
        if atual["parametros"].get(chave) != anterior["parametros"].get(chave):
            difs.append(
                f"PARAMETRO {chave}: {anterior['parametros'].get(chave)} -> "
                f"{atual['parametros'].get(chave)}"
            )

    if atual["veredito"] != anterior["veredito"]:
        difs.append(f"VEREDITO: {anterior['veredito']} -> {atual['veredito']}")

    ma, mb = atual["medicoes"], anterior["medicoes"]
    for chave in ("n_amostras", "melhor_feature", "melhor_horizonte"):
        if ma.get(chave) != mb.get(chave):
            difs.append(f"MEDICAO {chave}: {mb.get(chave)} -> {ma.get(chave)}")
    for chave in ("ic_max_abs", "p_permutacao"):
        d = abs(float(ma.get(chave, 0)) - float(mb.get(chave, 0)))
        if d > TOLERANCIA:
            difs.append(f"MEDICAO {chave}: diferença {d:.10f} (tolerância {TOLERANCIA})")

    for chave in sorted(set(ma.get("ics", {})) | set(mb.get("ics", {}))):
        va, vb = ma.get("ics", {}).get(chave), mb.get("ics", {}).get(chave)
        if va is None or vb is None:
            difs.append(f"IC {chave}: ausente em uma das derivações")
        elif abs(va - vb) > TOLERANCIA:
            difs.append(f"IC {chave}: diferença {abs(va - vb):.10f}")
    return difs


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Re-deriva vereditos do snapshot (I-11)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rotulo", default=None, help="snapshot (default: mais recente)")
    ap.add_argument("--comparar", action="store_true",
                    help="compara com o registro anterior e exige diferença 0.0")
    args = ap.parse_args()

    resultado = derivar(args.symbol, args.seed, args.rotulo)
    m = resultado["medicoes"]

    print("=" * 70)
    print(f"  RE-DERIVAÇÃO — {args.symbol} | snapshot {resultado['snapshot']['rotulo']}")
    print("=" * 70)
    print(f"  n amostras ......... {m['n_amostras']}")
    print(f"  melhor feature ..... {m['melhor_feature']} (H={m['melhor_horizonte']})")
    print(f"  |IC| máx ........... {m['ic_max_abs']:+.6f}  (piso {IC_PISO_ECONOMICO})")
    print(f"  p (permutação) ..... {m['p_permutacao']:.6f}")
    print(f"  VEREDITO ........... {resultado['veredito']}")

    caminho = caminho_registro(args.symbol, resultado["snapshot"]["rotulo"])
    if args.comparar:
        if not os.path.exists(caminho):
            print(f"\n  Sem registro anterior em {caminho} — nada a comparar.")
            print("  Rode sem --comparar primeiro para criar a linha de base.")
            return 2
        with open(caminho, encoding="utf-8") as fp:
            anterior = json.load(fp)
        difs = comparar(resultado, anterior)
        if not difs:
            print(f"\n  REPRODUÇÃO EXATA — diferença 0.0 contra {os.path.basename(caminho)}")
            print(f"  (linha de base gerada em {anterior['gerado_em']})")
            return 0
        print("\n  DIVERGÊNCIA:")
        for d in difs:
            print(f"    {d}")
        return 1

    gravado = gravar(resultado)
    print(f"\n  Registro gravado: {gravado}")
    print("  Para verificar reprodutibilidade (aqui ou em outra máquina):")
    print(f"    python -m research.reproduzir --symbol {args.symbol} --comparar")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
