"""
Deteccao de Suportes e Resistencias + Entrada em Scale-In
==========================================================

Metodos de identificacao de suporte:
  1. Pivot Points (HiLo classico)
  2. Bollinger Band inferior como suporte dinamico
  3. VWAP como suporte/resistencia dinamica
  4. Zonas de alto volume (Volume Profile simplificado)
  5. EMA 50 e EMA 200 como suporte de tendencia

Entrada Scale-In (3 parcelas):
  Parcela 1 (40%): no primeiro toque do suporte
  Parcela 2 (40%): no segundo toque (pullback confirmado)
  Parcela 3 (20%): no rompimento + pullback (confirmacao maxima)

  Stop unico: abaixo do suporte mais forte
  Preco medio: media ponderada das 3 entradas
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import indicadores as ind

BASE_URL = "https://api.binance.com"
SYMBOL = "BTCUSDT"

# Tolerancia para considerar "proximo" ao suporte (% do preco)
TOLERANCIA_PCT = 0.005  # 0.5%

# Scale-in parcelas
PARCELA_1 = 0.40  # 40% no toque do suporte
PARCELA_2 = 0.40  # 40% no pullback
PARCELA_3 = 0.20  # 20% na confirmacao


def _klines(intervalo, limite=100):
    try:
        r = requests.get(
            f"{BASE_URL}/api/v3/klines",
            params={"symbol": SYMBOL, "interval": intervalo, "limit": limite},
            timeout=8,
        )
        k = r.json()
        return {
            "abertura": [float(x[1]) for x in k],
            "maxima": [float(x[2]) for x in k],
            "minima": [float(x[3]) for x in k],
            "fechamento": [float(x[4]) for x in k],
            "volume": [float(x[5]) for x in k],
        }
    except Exception:
        return None


def _pivot_points(maximas, minimas, fechamentos, periodo=5):
    """Encontra pontos de suporte e resistencia via pivots."""
    suportes = []
    resistencias = []

    for i in range(periodo, len(fechamentos) - 1):
        janela_min = (
            minimas[i - periodo : i + periodo + 1]
            if i + periodo < len(minimas)
            else minimas[i - periodo :]
        )
        janela_max = (
            maximas[i - periodo : i + periodo + 1]
            if i + periodo < len(maximas)
            else maximas[i - periodo :]
        )

        if minimas[i] == min(janela_min):
            suportes.append(minimas[i])
        if maximas[i] == max(janela_max):
            resistencias.append(maximas[i])

    return suportes[-5:], resistencias[-5:]  # ultimos 5


def _volume_profile(fechamentos, volumes, num_bins=20):
    """Volume Profile simplificado — encontra zonas de alto volume."""
    preco_min = min(fechamentos)
    preco_max = max(fechamentos)
    bin_size = (preco_max - preco_min) / num_bins

    if bin_size <= 0:
        return []

    bins = [0.0] * num_bins
    for i in range(len(fechamentos)):
        idx = int((fechamentos[i] - preco_min) / bin_size)
        idx = min(idx, num_bins - 1)
        bins[idx] += volumes[i]

    # Encontrar os 3 bins com mais volume (zona de valor)
    vol_media = sum(bins) / num_bins
    zonas = []
    for i in range(num_bins):
        if bins[i] > vol_media * 1.5:
            preco_zona = preco_min + (i + 0.5) * bin_size
            zonas.append(
                {
                    "preco": round(preco_zona, 2),
                    "volume": round(bins[i], 2),
                    "tipo": "suporte" if preco_zona < fechamentos[-1] else "resistencia",
                }
            )

    return sorted(zonas, key=lambda x: x["volume"], reverse=True)[:5]


def detectar_suportes(intervalo="1h"):
    """
    Detecta suportes e resistencias em multiplos metodos.

    Retorna dict com:
      suportes:      lista de precos de suporte (ordenados, mais proximo primeiro)
      resistencias:  lista de precos de resistencia
      suporte_forte: melhor suporte (confluencia de metodos)
      distancia_%:   distancia do preco atual ao suporte forte
      na_zona:       bool — preco esta proximo ao suporte
      zonas_volume:  Volume Profile zones
    """
    d = _klines(intervalo, 100)
    if not d:
        return {
            "suportes": [],
            "resistencias": [],
            "suporte_forte": 0,
            "distancia_%": 0,
            "na_zona": False,
        }

    f = d["fechamento"]
    h = d["maxima"]
    l = d["minima"]
    v = d["volume"]
    preco = f[-1]

    # ── Metodo 1: Pivot Points ────────────────────────────────
    pivots_sup, pivots_res = _pivot_points(h, l, f, 5)

    # ── Metodo 2: Bollinger inferior ──────────────────────────
    bb_upper, bb_mid, bb_lower = ind.bollinger(f, 20, 2)
    bb_sup = bb_lower[-1] if bb_lower[-1] else 0

    # ── Metodo 3: VWAP ────────────────────────────────────────
    vwap_val = ind.vwap(h, l, f, v)[-1]

    # ── Metodo 4: EMAs como suporte ──────────────────────────
    ema20 = ind.ema(f, 20)[-1]
    ema50 = ind.ema(f, 50)[-1]

    # ── Metodo 5: Volume Profile ──────────────────────────────
    zonas = _volume_profile(f, v, 20)

    # ── Consolidar suportes (abaixo do preco) ─────────────────
    todos_suportes = []

    for s in pivots_sup:
        if s < preco:
            todos_suportes.append({"preco": s, "metodo": "pivot", "peso": 2})

    if bb_sup < preco:
        todos_suportes.append({"preco": bb_sup, "metodo": "bollinger", "peso": 1})

    if vwap_val < preco:
        todos_suportes.append({"preco": vwap_val, "metodo": "vwap", "peso": 2})

    if ema20 < preco:
        todos_suportes.append({"preco": ema20, "metodo": "ema20", "peso": 1})
    if ema50 < preco:
        todos_suportes.append({"preco": ema50, "metodo": "ema50", "peso": 2})

    for z in zonas:
        if z["tipo"] == "suporte":
            todos_suportes.append({"preco": z["preco"], "metodo": "volume", "peso": 3})

    # Consolidar resistencias
    todas_resistencias = []
    for r in pivots_res:
        if r > preco:
            todas_resistencias.append({"preco": r, "metodo": "pivot", "peso": 2})

    if vwap_val > preco:
        todas_resistencias.append({"preco": vwap_val, "metodo": "vwap", "peso": 2})

    for z in zonas:
        if z["tipo"] == "resistencia":
            todas_resistencias.append({"preco": z["preco"], "metodo": "volume", "peso": 3})

    # ── Encontrar suporte forte (confluencia) ────────────────
    # Agrupar suportes proximos entre si e somar pesos
    suporte_clusters = _clusterizar(todos_suportes, preco)
    resist_clusters = _clusterizar(todas_resistencias, preco)

    # Melhor suporte = cluster com maior peso total
    suporte_forte = 0
    peso_forte = 0
    metodos_forte = []
    for cl in suporte_clusters:
        if cl["peso_total"] > peso_forte:
            suporte_forte = cl["preco_medio"]
            peso_forte = cl["peso_total"]
            metodos_forte = cl["metodos"]

    # Distancia do preco ao suporte forte
    dist_pct = (preco - suporte_forte) / preco * 100 if suporte_forte > 0 else 99
    na_zona = dist_pct <= TOLERANCIA_PCT * 100

    return {
        "preco": round(preco, 2),
        "suportes": [
            round(s["preco"], 2) for s in sorted(todos_suportes, key=lambda x: -x["preco"])
        ],
        "resistencias": [
            round(rv["preco"], 2) for rv in sorted(todas_resistencias, key=lambda x: x["preco"])
        ],
        "suporte_forte": round(suporte_forte, 2),
        "peso_forte": peso_forte,
        "metodos_forte": metodos_forte,
        "distancia_%": round(dist_pct, 2),
        "na_zona": na_zona,
        "bb_inferior": round(bb_sup, 2),
        "vwap": round(vwap_val, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "zonas_volume": zonas,
        "clusters_sup": suporte_clusters[:5],
        "clusters_res": resist_clusters[:5],
    }


def _clusterizar(niveis, preco, tolerancia=0.003):
    """Agrupa niveis proximos entre si (dentro de 0.3%)."""
    if not niveis:
        return []

    niveis.sort(key=lambda x: x["preco"])
    clusters = []
    cluster_atual = [niveis[0]]

    for i in range(1, len(niveis)):
        diff = abs(niveis[i]["preco"] - cluster_atual[-1]["preco"]) / preco
        if diff <= tolerancia:
            cluster_atual.append(niveis[i])
        else:
            clusters.append(_resumo_cluster(cluster_atual))
            cluster_atual = [niveis[i]]

    if cluster_atual:
        clusters.append(_resumo_cluster(cluster_atual))

    clusters.sort(key=lambda x: -x["peso_total"])
    return clusters


def _resumo_cluster(niveis):
    preco_medio = sum(n["preco"] for n in niveis) / len(niveis)
    peso_total = sum(n["peso"] for n in niveis)
    metodos = list(set(n["metodo"] for n in niveis))
    return {
        "preco_medio": round(preco_medio, 2),
        "peso_total": peso_total,
        "metodos": metodos,
        "confluencia": len(metodos),
    }


# ── Scale-In Manager ─────────────────────────────────────────


class ScaleIn:
    """
    Gerencia entradas em 3 parcelas progressivas.

    Uso:
      si = ScaleIn(tamanho_total_btc=0.001, suporte=65000)
      si.entrada_parcela1(preco=65050)   → compra 40%
      si.entrada_parcela2(preco=64900)   → compra 40% no pullback
      si.entrada_parcela3(preco=65200)   → compra 20% na confirmacao

      si.preco_medio  → preco medio ponderado
      si.tamanho_atual → BTC total ja comprado
    """

    def __init__(self, tamanho_total_btc, suporte):
        self.tamanho_total = tamanho_total_btc
        self.suporte = suporte
        self.entradas = []  # [{preco, tamanho, parcela}]
        self.parcela_atual = 0

    def entrada_parcela1(self, preco):
        """Primeira entrada (40%) — toque no suporte."""
        tam = round(self.tamanho_total * PARCELA_1, 6)
        self.entradas.append({"preco": preco, "tamanho": tam, "parcela": 1})
        self.parcela_atual = 1
        return tam

    def entrada_parcela2(self, preco):
        """Segunda entrada (40%) — pullback para suporte."""
        if self.parcela_atual < 1:
            return 0
        tam = round(self.tamanho_total * PARCELA_2, 6)
        self.entradas.append({"preco": preco, "tamanho": tam, "parcela": 2})
        self.parcela_atual = 2
        return tam

    def entrada_parcela3(self, preco):
        """Terceira entrada (20%) — confirmacao de bounce."""
        if self.parcela_atual < 2:
            return 0
        tam = round(self.tamanho_total * PARCELA_3, 6)
        self.entradas.append({"preco": preco, "tamanho": tam, "parcela": 3})
        self.parcela_atual = 3
        return tam

    @property
    def preco_medio(self):
        if not self.entradas:
            return 0
        custo_total = sum(e["preco"] * e["tamanho"] for e in self.entradas)
        tam_total = sum(e["tamanho"] for e in self.entradas)
        return round(custo_total / tam_total, 2) if tam_total > 0 else 0

    @property
    def tamanho_atual(self):
        return sum(e["tamanho"] for e in self.entradas)

    @property
    def completo(self):
        return self.parcela_atual >= 3

    def status(self):
        return {
            "parcela_atual": self.parcela_atual,
            "entradas": self.entradas,
            "preco_medio": self.preco_medio,
            "tamanho_atual": self.tamanho_atual,
            "tamanho_total": self.tamanho_total,
            "restante": round(self.tamanho_total - self.tamanho_atual, 6),
            "completo": self.completo,
        }


def imprimir():
    r = detectar_suportes("1h")

    verde = "\033[92m"
    vermelho = "\033[91m"
    amarelo = "\033[93m"
    cinza = "\033[90m"
    reset = "\033[0m"

    print("\n" + "=" * 58)
    print("  SUPORTES E RESISTENCIAS")
    print("=" * 58)
    print(f"  Preco Atual: ${r['preco']:,.2f}")
    print()

    # Suportes (clusters)
    print(f"  {'SUPORTES (abaixo do preco)':}")
    if r["clusters_sup"]:
        for i, cl in enumerate(r["clusters_sup"][:5]):
            dist = (r["preco"] - cl["preco_medio"]) / r["preco"] * 100
            forca = "#" * cl["peso_total"]
            metodos = "+".join(cl["metodos"])
            cor = verde if cl["confluencia"] >= 2 else amarelo
            print(
                f"    {cor}${cl['preco_medio']:>10,.2f}{reset}  "
                f"{cinza}(-{dist:.1f}%) [{forca}] {metodos}{reset}"
            )
    else:
        print(f"    {cinza}Nenhum suporte identificado{reset}")

    print()

    # Resistencias
    print(f"  {'RESISTENCIAS (acima do preco)':}")
    if r["clusters_res"]:
        for cl in r["clusters_res"][:5]:
            dist = (cl["preco_medio"] - r["preco"]) / r["preco"] * 100
            forca = "#" * cl["peso_total"]
            metodos = "+".join(cl["metodos"])
            cor = vermelho if cl["confluencia"] >= 2 else amarelo
            print(
                f"    {cor}${cl['preco_medio']:>10,.2f}{reset}  "
                f"{cinza}(+{dist:.1f}%) [{forca}] {metodos}{reset}"
            )
    else:
        print(f"    {cinza}Nenhuma resistencia identificada{reset}")

    print()

    # Suporte forte
    sf = r["suporte_forte"]
    if sf > 0:
        cor_sf = verde if r["na_zona"] else amarelo
        print(f"  Suporte Forte: {cor_sf}${sf:,.2f}{reset}  " f"({r['distancia_%']:.2f}% abaixo)")
        print(f"  Confluencia:   {'+'.join(r['metodos_forte'])} (peso {r['peso_forte']})")
        print(f"  Na zona:       {verde+'SIM'+reset if r['na_zona'] else vermelho+'NAO'+reset}")
    else:
        print(f"  {vermelho}Nenhum suporte forte identificado{reset}")

    # Zonas de volume
    if r["zonas_volume"]:
        print(f"\n  Zonas de Alto Volume:")
        for z in r["zonas_volume"][:3]:
            cor_z = verde if z["tipo"] == "suporte" else vermelho
            print(
                f"    {cor_z}${z['preco']:>10,.2f}{reset}  "
                f"{cinza}vol:{z['volume']:,.0f} ({z['tipo']}){reset}"
            )

    print("=" * 58)
    return r


if __name__ == "__main__":
    imprimir()

    # Demo scale-in
    print("\n" + "=" * 58)
    print("  DEMO SCALE-IN (3 parcelas)")
    print("=" * 58)
    si = ScaleIn(tamanho_total_btc=0.001, suporte=65000)
    si.entrada_parcela1(preco=65050)
    print(f"  Parcela 1: comprou {si.entradas[-1]['tamanho']:.6f} BTC @ ${65050:,}")
    si.entrada_parcela2(preco=64900)
    print(f"  Parcela 2: comprou {si.entradas[-1]['tamanho']:.6f} BTC @ ${64900:,}")
    si.entrada_parcela3(preco=65200)
    print(f"  Parcela 3: comprou {si.entradas[-1]['tamanho']:.6f} BTC @ ${65200:,}")
    print(f"\n  Preco Medio:  ${si.preco_medio:,}")
    print(f"  Total BTC:    {si.tamanho_atual:.6f}")
    print(f"  Completo:     {si.completo}")
    print("=" * 58)
