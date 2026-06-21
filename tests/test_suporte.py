"""
Suite hermetica para suporte.py (@Delta — QA Automation).

Cobertura:
  - detectar_suportes: estrutura do dict, chaves esperadas, robustez com
    serie sintetica e com _klines retornando None (sem rede).
  - ScaleIn: parcelas 40/40/20, preco medio ponderado (calculado a mao),
    avanco de parcela_atual, flag .completo, e chamadas fora de ordem.

Regras:
  - SEM rede / SEM banco. _klines e mockado via monkeypatch.
  - As funcoes ind.bollinger/vwap/ema sao puras (numpy, sem I/O) e rodam
    sobre a serie sintetica — nao precisam de mock.
"""

import math

import pytest

import suporte


# ──────────────────────────────────────────────────────────────
# Fixtures: serie sintetica de klines (sem rede)
# ──────────────────────────────────────────────────────────────

def _serie_sintetica(n=100):
    """
    Gera uma serie OHLCV deterministica com tendencia + ruido leve,
    longa o bastante para EMA50/Bollinger20/VWAP funcionarem.
    Replica o shape do dict retornado por suporte._klines.
    """
    fechamento = []
    maxima = []
    minima = []
    abertura = []
    volume = []
    base = 60000.0
    for i in range(n):
        # tendencia suave de alta + oscilacao senoidal -> cria pivots
        preco = base + i * 25.0 + 300.0 * math.sin(i / 4.0)
        abertura.append(preco - 5.0)
        fechamento.append(preco)
        maxima.append(preco + 40.0)
        minima.append(preco - 40.0)
        volume.append(100.0 + (i % 7) * 30.0)
    return {
        "abertura": abertura,
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": volume,
    }


@pytest.fixture
def patch_klines(monkeypatch):
    """Mocka suporte._klines para devolver serie sintetica (sem rede)."""
    serie = _serie_sintetica(100)
    monkeypatch.setattr(suporte, "_klines", lambda intervalo, limite=100: serie)
    return serie


@pytest.fixture
def patch_klines_none(monkeypatch):
    """Mocka _klines simulando falha de rede (retorno None)."""
    monkeypatch.setattr(suporte, "_klines", lambda intervalo, limite=100: None)


# ──────────────────────────────────────────────────────────────
# detectar_suportes
# ──────────────────────────────────────────────────────────────

CHAVES_COMPLETAS = {
    "preco", "suportes", "resistencias", "suporte_forte", "peso_forte",
    "metodos_forte", "distancia_%", "na_zona", "bb_inferior", "vwap",
    "ema20", "ema50", "zonas_volume", "clusters_sup", "clusters_res",
}


def test_detectar_suportes_nao_lanca_e_retorna_dict(patch_klines):
    r = suporte.detectar_suportes("1h")
    assert isinstance(r, dict)


def test_detectar_suportes_chaves_esperadas(patch_klines):
    r = suporte.detectar_suportes("1h")
    # Todas as chaves documentadas/retornadas devem existir no caminho feliz
    assert CHAVES_COMPLETAS.issubset(set(r.keys()))


def test_detectar_suportes_tipos_basicos(patch_klines):
    r = suporte.detectar_suportes("1h")
    assert isinstance(r["suportes"], list)
    assert isinstance(r["resistencias"], list)
    assert isinstance(r["zonas_volume"], list)
    # na_zona pode ser bool nativo ou numpy.bool_ (vem de comparacao numpy)
    assert bool(r["na_zona"]) in (True, False)
    assert isinstance(r["suporte_forte"], (int, float))
    assert isinstance(r["peso_forte"], (int, float))
    assert isinstance(r["metodos_forte"], list)


def test_detectar_suportes_preco_e_ultimo_fechamento(patch_klines):
    r = suporte.detectar_suportes("1h")
    esperado = round(patch_klines["fechamento"][-1], 2)
    assert r["preco"] == esperado


def test_detectar_suportes_lista_ordenada_desc(patch_klines):
    # suportes sao ordenados do mais proximo (maior preco) ao mais distante
    r = suporte.detectar_suportes("1h")
    sup = r["suportes"]
    assert sup == sorted(sup, reverse=True)


def test_detectar_suportes_resistencias_ordenada_asc(patch_klines):
    r = suporte.detectar_suportes("1h")
    res = r["resistencias"]
    assert res == sorted(res)


def test_detectar_suportes_suporte_forte_abaixo_do_preco(patch_klines):
    r = suporte.detectar_suportes("1h")
    # suporte_forte, quando identificado, fica abaixo do preco atual
    if r["suporte_forte"] > 0:
        assert r["suporte_forte"] < r["preco"]


def test_detectar_suportes_klines_none_retorna_fallback(patch_klines_none):
    # Sem dados (falha de rede simulada) -> dict de fallback, sem lancar
    r = suporte.detectar_suportes("1h")
    assert r["suportes"] == []
    assert r["resistencias"] == []
    assert r["suporte_forte"] == 0
    assert r["distancia_%"] == 0
    assert r["na_zona"] is False


def test_detectar_suportes_usa_intervalo_passado(monkeypatch):
    # Garante que o intervalo e repassado para _klines (hermetico)
    capturado = {}

    def fake_klines(intervalo, limite=100):
        capturado["intervalo"] = intervalo
        return _serie_sintetica(100)

    monkeypatch.setattr(suporte, "_klines", fake_klines)
    suporte.detectar_suportes("15m")
    assert capturado["intervalo"] == "15m"


# ──────────────────────────────────────────────────────────────
# ScaleIn — parcelas 40/40/20
# ──────────────────────────────────────────────────────────────

def test_scalein_parcela1_compra_40pct():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    tam = si.entrada_parcela1(preco=65050)
    assert tam == round(0.001 * 0.40, 6)
    assert si.parcela_atual == 1
    assert len(si.entradas) == 1


def test_scalein_parcela2_compra_40pct():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    tam = si.entrada_parcela2(preco=64900)
    assert tam == round(0.001 * 0.40, 6)
    assert si.parcela_atual == 2


def test_scalein_parcela3_compra_20pct():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    si.entrada_parcela2(preco=64900)
    tam = si.entrada_parcela3(preco=65200)
    assert tam == round(0.001 * 0.20, 6)
    assert si.parcela_atual == 3


def test_scalein_proporcoes_somam_total():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    si.entrada_parcela2(preco=64900)
    si.entrada_parcela3(preco=65200)
    # 0.0004 + 0.0004 + 0.0002 == 0.001
    assert si.tamanho_atual == pytest.approx(0.001, abs=1e-9)


def test_scalein_preco_medio_ponderado_calculado_a_mao():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)   # 0.0004 BTC
    si.entrada_parcela2(preco=64900)   # 0.0004 BTC
    si.entrada_parcela3(preco=65200)   # 0.0002 BTC

    # Calculo manual:
    #   custo = 65050*0.0004 + 64900*0.0004 + 65200*0.0002
    #         = 26.02 + 25.96 + 13.04 = 65.02
    #   tam   = 0.001
    #   medio = 65.02 / 0.001 = 65020.00
    assert si.preco_medio == 65020.00


def test_scalein_completo_apos_terceira():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    assert si.completo is False
    si.entrada_parcela1(preco=65050)
    assert si.completo is False
    si.entrada_parcela2(preco=64900)
    assert si.completo is False
    si.entrada_parcela3(preco=65200)
    assert si.completo is True


def test_scalein_parcela_atual_avanca():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    assert si.parcela_atual == 0
    si.entrada_parcela1(preco=65050)
    assert si.parcela_atual == 1
    si.entrada_parcela2(preco=64900)
    assert si.parcela_atual == 2
    si.entrada_parcela3(preco=65200)
    assert si.parcela_atual == 3


def test_scalein_fora_de_ordem_parcela2_sem_parcela1():
    # Chamar parcela2 antes da parcela1 -> retorna 0 e nao registra entrada
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    tam = si.entrada_parcela2(preco=64900)
    assert tam == 0
    assert si.entradas == []
    assert si.parcela_atual == 0


def test_scalein_fora_de_ordem_parcela3_sem_parcela2():
    # parcela3 exige parcela_atual >= 2; com so a parcela1 -> retorna 0
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    tam = si.entrada_parcela3(preco=65200)
    assert tam == 0
    assert len(si.entradas) == 1
    assert si.parcela_atual == 1


def test_scalein_preco_medio_zero_sem_entradas():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    assert si.preco_medio == 0
    assert si.tamanho_atual == 0


def test_scalein_status_estrutura():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    st = si.status()
    assert st["parcela_atual"] == 1
    assert st["tamanho_total"] == 0.001
    assert st["completo"] is False
    assert st["restante"] == round(0.001 - si.tamanho_atual, 6)
    assert isinstance(st["entradas"], list) and len(st["entradas"]) == 1


# ──────────────────────────────────────────────────────────────
# ADVERSARIAL (@Delta) — bordas, regressao e helpers internos
# ──────────────────────────────────────────────────────────────


def _serie_flat(n=30, preco=100.0, vol=10.0):
    """Serie totalmente plana: forca bin_size<=0 no volume_profile,
    std=0 nas bandas, e nenhum suporte/resistencia abaixo/acima do preco."""
    return {
        "abertura":   [preco] * n,
        "maxima":     [preco] * n,
        "minima":     [preco] * n,
        "fechamento": [preco] * n,
        "volume":     [vol] * n,
    }


@pytest.fixture
def patch_klines_flat(monkeypatch):
    serie = _serie_flat(30)
    monkeypatch.setattr(suporte, "_klines", lambda intervalo, limite=100: serie)
    return serie


@pytest.fixture
def patch_klines_curta(monkeypatch):
    """Serie curta (len < 20): bollinger/EMA50 caem no caminho de None/NaN.
    Regressao: detectar_suportes NAO pode lancar (sem NameError/IndexError)."""
    n = 15
    serie = {
        "maxima":     [100.0 + i * 5 for i in range(n)],
        "minima":     [90.0 + i * 5 for i in range(n)],
        "fechamento": [95.0 + i * 5 for i in range(n)],
        "volume":     [10.0 + i for i in range(n)],
        "abertura":   [94.0 + i * 5 for i in range(n)],
    }
    monkeypatch.setattr(suporte, "_klines", lambda intervalo, limite=100: serie)
    return serie


# ── detectar_suportes: serie plana (bordas de divisao por zero) ──

def test_detectar_suportes_flat_nao_lanca_e_chaves_completas(patch_klines_flat):
    # Regressao critica: serie degenerada nao pode quebrar a pipeline.
    r = suporte.detectar_suportes("1h")
    assert CHAVES_COMPLETAS.issubset(set(r.keys()))


def test_detectar_suportes_flat_sem_suportes_nem_resistencias(patch_klines_flat):
    r = suporte.detectar_suportes("1h")
    # Nada esta estritamente abaixo/acima do preco constante -> listas vazias.
    assert r["suportes"] == []
    assert r["resistencias"] == []
    assert r["zonas_volume"] == []


def test_detectar_suportes_flat_suporte_forte_zero_e_dist_99(patch_klines_flat):
    r = suporte.detectar_suportes("1h")
    assert r["suporte_forte"] == 0
    assert r["peso_forte"] == 0
    assert r["metodos_forte"] == []
    # sem suporte forte -> distancia sentinela 99 e fora de zona.
    assert r["distancia_%"] == 99
    assert bool(r["na_zona"]) is False


def test_detectar_suportes_flat_clusters_vazios(patch_klines_flat):
    r = suporte.detectar_suportes("1h")
    assert r["clusters_sup"] == []
    assert r["clusters_res"] == []


# ── detectar_suportes: serie curta (regressao bollinger None / ema NaN) ──

def test_detectar_suportes_curta_nao_lanca(patch_klines_curta):
    # Regressao: bollinger devolve None no final (periodo>len) e ema50 NaN.
    # O codigo usa `bb_lower[-1] if bb_lower[-1] else 0` e nao pode lancar.
    r = suporte.detectar_suportes("1h")
    assert isinstance(r, dict)
    assert CHAVES_COMPLETAS.issubset(set(r.keys()))


def test_detectar_suportes_curta_bb_inferior_zero(patch_klines_curta):
    # len(serie)=15 < periodo(20) -> bb_lower[-1] is None -> bb_sup vira 0.
    r = suporte.detectar_suportes("1h")
    assert r["bb_inferior"] == 0


def test_detectar_suportes_curta_preco_ultimo_fechamento(patch_klines_curta):
    r = suporte.detectar_suportes("1h")
    assert r["preco"] == round(patch_klines_curta["fechamento"][-1], 2)


# ── caminho feliz: relacoes finas nao cobertas ──

def test_detectar_suportes_distancia_coerente_com_suporte_forte(patch_klines):
    r = suporte.detectar_suportes("1h")
    if r["suporte_forte"] > 0:
        esperado = round((r["preco"] - r["suporte_forte"]) / r["preco"] * 100, 2)
        assert r["distancia_%"] == esperado


def test_detectar_suportes_clusters_limitados_a_cinco(patch_klines):
    r = suporte.detectar_suportes("1h")
    assert len(r["clusters_sup"]) <= 5
    assert len(r["clusters_res"]) <= 5


def test_detectar_suportes_clusters_ordenados_por_peso_desc(patch_klines):
    r = suporte.detectar_suportes("1h")
    pesos = [cl["peso_total"] for cl in r["clusters_sup"]]
    assert pesos == sorted(pesos, reverse=True)


def test_detectar_suportes_peso_forte_e_maior_cluster(patch_klines):
    r = suporte.detectar_suportes("1h")
    if r["clusters_sup"]:
        # suporte_forte vem do cluster de maior peso (primeiro apos ordenacao).
        assert r["peso_forte"] == max(cl["peso_total"] for cl in r["clusters_sup"])


def test_detectar_suportes_zonas_volume_estrutura(patch_klines):
    r = suporte.detectar_suportes("1h")
    for z in r["zonas_volume"]:
        assert set(z.keys()) == {"preco", "volume", "tipo"}
        assert z["tipo"] in ("suporte", "resistencia")


def test_detectar_suportes_zonas_volume_ordenadas_por_volume_desc(patch_klines):
    r = suporte.detectar_suportes("1h")
    vols = [z["volume"] for z in r["zonas_volume"]]
    assert vols == sorted(vols, reverse=True)


def test_detectar_suportes_na_zona_consistente_com_tolerancia(patch_klines):
    r = suporte.detectar_suportes("1h")
    if r["suporte_forte"] > 0:
        esperado = r["distancia_%"] <= suporte.TOLERANCIA_PCT * 100
        assert bool(r["na_zona"]) == bool(esperado)


def test_detectar_suportes_default_intervalo_1h(monkeypatch):
    capturado = {}

    def fake(intervalo, limite=100):
        capturado["intervalo"] = intervalo
        return _serie_sintetica(100)

    monkeypatch.setattr(suporte, "_klines", fake)
    suporte.detectar_suportes()  # sem argumento -> default "1h"
    assert capturado["intervalo"] == "1h"


def test_detectar_suportes_klines_dict_vazio_e_fallback(monkeypatch):
    # `_klines` devolvendo {} (falsy) tambem cai no fallback (not d).
    monkeypatch.setattr(suporte, "_klines", lambda i, limite=100: {})
    r = suporte.detectar_suportes("1h")
    assert r["suportes"] == []
    assert r["na_zona"] is False


# ── _volume_profile (helper interno) ──

def test_volume_profile_flat_retorna_vazio():
    # preco_max == preco_min -> bin_size <= 0 -> [].
    f = [100.0] * 30
    v = [10.0] * 30
    assert suporte._volume_profile(f, v, 20) == []


def test_volume_profile_detecta_zona_de_alto_volume():
    # Concentra volume num preco -> deve aparecer ao menos uma zona.
    f = [100.0 + (i % 5) for i in range(40)]
    v = [10.0] * 40
    v[10] = 5000.0  # pico de volume
    zonas = suporte._volume_profile(f, v, 10)
    assert len(zonas) >= 1
    # Ordenado por volume desc.
    vols = [z["volume"] for z in zonas]
    assert vols == sorted(vols, reverse=True)


def test_volume_profile_tipo_suporte_quando_abaixo_do_ultimo():
    # Pico de volume num preco baixo, ultimo fechamento alto -> tipo 'suporte'.
    f = [50.0] * 20 + [200.0]
    v = [1.0] * 20 + [1.0]
    v[0] = 9999.0
    zonas = suporte._volume_profile(f, v, 10)
    assert any(z["tipo"] == "suporte" for z in zonas)


# ── _pivot_points (helper interno) ──

def test_pivot_points_retorna_no_maximo_cinco():
    serie = _serie_sintetica(120)
    sup, res = suporte._pivot_points(serie["maxima"], serie["minima"],
                                     serie["fechamento"], 5)
    assert len(sup) <= 5
    assert len(res) <= 5


def test_pivot_points_serie_curta_nao_lanca():
    # len menor que 2*periodo -> range pode ficar vazio, mas nao deve lancar.
    sup, res = suporte._pivot_points([1, 2, 3], [1, 2, 3], [1, 2, 3], 5)
    assert sup == [] and res == []


# ── _clusterizar / _resumo_cluster (helpers internos) ──

def test_clusterizar_vazio_retorna_lista_vazia():
    assert suporte._clusterizar([], 100.0) == []


def test_clusterizar_agrupa_proximos_e_separa_distantes():
    niveis = [
        {"preco": 100.0, "metodo": "a", "peso": 1},
        {"preco": 100.2, "metodo": "b", "peso": 2},  # ~0.2% -> cluster com 100.0
        {"preco": 200.0, "metodo": "c", "peso": 5},  # distante -> proprio cluster
    ]
    cl = suporte._clusterizar(niveis, 100.0, tolerancia=0.003)
    assert len(cl) == 2
    # Ordenado por peso_total desc -> 200.0 (peso 5) primeiro.
    assert cl[0]["preco_medio"] == 200.0
    assert cl[0]["peso_total"] == 5
    # Cluster mesclado: media 100.1, peso 3, confluencia 2 metodos.
    mesclado = cl[1]
    assert mesclado["preco_medio"] == 100.1
    assert mesclado["peso_total"] == 3
    assert mesclado["confluencia"] == 2
    assert set(mesclado["metodos"]) == {"a", "b"}


def test_resumo_cluster_metricas():
    niveis = [
        {"preco": 10.0, "metodo": "x", "peso": 1},
        {"preco": 12.0, "metodo": "x", "peso": 2},
        {"preco": 14.0, "metodo": "y", "peso": 3},
    ]
    r = suporte._resumo_cluster(niveis)
    assert r["preco_medio"] == 12.0           # (10+12+14)/3
    assert r["peso_total"] == 6               # 1+2+3
    assert r["confluencia"] == 2              # metodos unicos {x,y}
    assert set(r["metodos"]) == {"x", "y"}


# ── ScaleIn: bordas adicionais ──

def test_scalein_status_completo_e_restante_zero():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    si.entrada_parcela2(preco=64900)
    si.entrada_parcela3(preco=65200)
    st = si.status()
    assert st["completo"] is True
    assert st["restante"] == pytest.approx(0.0, abs=1e-9)
    assert len(st["entradas"]) == 3


def test_scalein_completo_idempotente_apos_terceira():
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    si.entrada_parcela2(preco=64900)
    si.entrada_parcela3(preco=65200)
    # Repetir parcela3 com parcela_atual>=2 ainda registra (sem guarda de teto).
    antes = len(si.entradas)
    si.entrada_parcela3(preco=65300)
    assert len(si.entradas) == antes + 1
    assert si.completo is True


def test_scalein_entrada_registra_metadados_corretos():
    si = suporte.ScaleIn(tamanho_total_btc=0.002, suporte=65000)
    si.entrada_parcela1(preco=65000)
    e = si.entradas[-1]
    assert e["parcela"] == 1
    assert e["preco"] == 65000
    assert e["tamanho"] == round(0.002 * 0.40, 6)


def test_scalein_preco_medio_parcial_uma_entrada():
    # Com uma so entrada, preco_medio == preco da entrada.
    si = suporte.ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=64500)
    assert si.preco_medio == 64500.00


def test_scalein_tamanho_total_e_suporte_armazenados():
    si = suporte.ScaleIn(tamanho_total_btc=0.005, suporte=62000)
    assert si.tamanho_total == 0.005
    assert si.suporte == 62000


def test_scalein_zero_total_nao_quebra_preco_medio():
    # tamanho_total=0 -> entradas com tam 0 -> tam_total 0 -> preco_medio 0.
    si = suporte.ScaleIn(tamanho_total_btc=0.0, suporte=65000)
    si.entrada_parcela1(preco=65000)
    assert si.tamanho_atual == 0
    assert si.preco_medio == 0
