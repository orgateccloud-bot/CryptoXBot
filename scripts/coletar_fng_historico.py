"""
Coletor do histórico de Fear & Greed (frente E-9 / I-12g)
==========================================================
`data/fng_historico.json` NÃO EXISTIA. Toda medição oficial do gate rodou com
Fear & Greed neutro fixo — ou seja, **sem o veto de sentimento que a produção
aplica de verdade**. No período medido do BTCUSDT: 730 de 730 dias sem valor.

Desde I-12g o `walk_forward.py` aborta em vez de degradar em silêncio, o que
tornou este arquivo um pré-requisito duro: sem ele não existe medição válida
para o gate, e sem medição válida a frente E-9 (re-derivar os parâmetros) não
pode nem começar.

Fonte: `https://api.alternative.me/fng/` — a MESMA que `fear_greed.py` já
consulta em produção. Pública, gratuita, sem autenticação. `limit=0` devolve o
histórico inteiro (desde 2018-02-01).

O índice é publicado no INÍCIO de cada dia UTC, então usar o valor do dia na
decisão daquele dia é causal — é o que `walk_forward._fng_do_dia` assume.

Saída (as duas versionadas, ver .gitignore):
  data/fng_historico.json           {"YYYY-MM-DD": int, ...}
  data/fng_historico.manifest.json  sha256, contagem, cobertura, data da coleta

O manifest existe pelo mesmo motivo do de I-11: sem hash, "o arquivo está lá"
não é a mesma coisa que "o arquivo é o mesmo". Verifique com `--verificar`.

Uso:
  python scripts/coletar_fng_historico.py                # coleta/atualiza
  python scripts/coletar_fng_historico.py --verificar    # só confere o sha256
  python scripts/coletar_fng_historico.py --dry-run      # mostra o que mudaria
"""

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = "https://api.alternative.me/fng/?limit=0&format=json"
CAMINHO = "data/fng_historico.json"
CAMINHO_MANIFEST = "data/fng_historico.manifest.json"


def _gravar_json_deterministico(caminho: str, obj) -> str:
    """Grava com chaves ordenadas e LF, e devolve o sha256 do que foi gravado.

    Determinismo importa: o manifest guarda o hash, e uma reordenação de
    chaves ou um CRLF fariam o mesmo conteúdo produzir hash diferente — foi
    exatamente assim que o `core.autocrlf` quebrou a verificação do snapshot
    de I-11 (ver .gitattributes).
    """
    texto = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with io.open(caminho, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _sha256_do_arquivo(caminho: str) -> str:
    with io.open(caminho, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def baixar() -> dict:
    """Histórico completo, dia UTC -> valor inteiro (0-100)."""
    r = requests.get(API_URL, timeout=60)
    r.raise_for_status()
    dados = r.json().get("data") or []
    if not dados:
        raise RuntimeError("alternative.me devolveu lista vazia")
    hist = {}
    for item in dados:
        ts = int(item["timestamp"])
        dia = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        hist[dia] = int(item["value"])
    return hist


def carregar_local() -> dict | None:
    if not os.path.exists(CAMINHO):
        return None
    with io.open(CAMINHO, encoding="utf-8") as f:
        return json.load(f)


def _resumo(hist: dict) -> dict:
    dias = sorted(hist)
    return {
        "dias": len(dias),
        "inicio": dias[0] if dias else None,
        "fim": dias[-1] if dias else None,
    }


def _buracos(hist: dict) -> list[str]:
    """Dias faltando entre o primeiro e o último — o índice é diário e o
    histórico deveria ser contíguo. Um buraco não é fatal (`_fng_do_dia` faz
    carry de até 7 dias), mas precisa ser visível."""
    dias = sorted(hist)
    if not dias:
        return []
    d = datetime.strptime(dias[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    fim = datetime.strptime(dias[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    faltando = []
    while d <= fim:
        chave = d.strftime("%Y-%m-%d")
        if chave not in hist:
            faltando.append(chave)
        d = datetime.fromtimestamp(d.timestamp() + 86400, tz=timezone.utc)
    return faltando


def verificar() -> int:
    """Confere o arquivo contra o manifest. Exit code = 0 se bate."""
    if not os.path.exists(CAMINHO) or not os.path.exists(CAMINHO_MANIFEST):
        print(f"[FNG] ausente: {CAMINHO} e/ou {CAMINHO_MANIFEST}")
        return 2
    with io.open(CAMINHO_MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    atual = _sha256_do_arquivo(CAMINHO)
    esperado = manifest.get("sha256")
    if atual != esperado:
        print("[FNG] sha256 DIVERGE do manifest")
        print(f"  esperado: {esperado}")
        print(f"  atual ...: {atual}")
        return 1
    print(f"[FNG] OK — {manifest['dias']} dias, {manifest['inicio']} -> {manifest['fim']}")
    print(f"  sha256: {atual}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta o histórico de Fear & Greed")
    parser.add_argument("--verificar", action="store_true", help="só confere o sha256")
    parser.add_argument("--dry-run", action="store_true", help="mostra o diff, nao grava")
    args = parser.parse_args()

    if args.verificar:
        return verificar()

    antes = carregar_local()
    print(f"[FNG] baixando de {API_URL} ...")
    hist = baixar()
    resumo = _resumo(hist)
    faltando = _buracos(hist)

    print(f"[FNG] {resumo['dias']} dias  ({resumo['inicio']} -> {resumo['fim']})")
    if faltando:
        print(f"[FNG] AVISO: {len(faltando)} dias sem valor no meio da serie")
        print(f"       primeiros: {faltando[:5]}")
    if antes is not None:
        novos = sorted(set(hist) - set(antes))
        mudados = sorted(d for d in set(hist) & set(antes) if hist[d] != antes[d])
        print(
            f"[FNG] local tinha {len(antes)} dias — {len(novos)} novos, "
            f"{len(mudados)} alterados"
        )
        if mudados:
            # O passado nao deveria mudar. Se mudou, o dado nao e imutavel e a
            # reproducao de qualquer medicao antiga fica em questao.
            print(f"[FNG] AVISO: valores PASSADOS mudaram: {mudados[:10]}")

    if args.dry_run:
        print("[FNG] --dry-run: nada gravado.")
        return 0

    os.makedirs(os.path.dirname(CAMINHO), exist_ok=True)
    sha = _gravar_json_deterministico(CAMINHO, hist)
    manifest = {
        "fonte": API_URL,
        "coletado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": sha,
        "dias_faltantes": len(faltando),
        **resumo,
    }
    _gravar_json_deterministico(CAMINHO_MANIFEST, manifest)
    print(f"[FNG] gravado {CAMINHO}")
    print(f"      sha256 {sha}")
    print(f"[FNG] gravado {CAMINHO_MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
