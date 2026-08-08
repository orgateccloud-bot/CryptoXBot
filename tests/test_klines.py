"""
Testes herméticos — data/klines.py (P1-5, fetch de klines consolidado).

Sem rede real: requests.get é sempre mockado. Cache module-level
(data.klines._cache) é isolado entre testes por uma fixture autouse.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.klines as klines


@pytest.fixture(autouse=True)
def _isolar_cache():
    original = dict(klines._cache)
    klines._cache.clear()
    yield
    klines._cache.clear()
    klines._cache.update(original)


def _payload(n=3, preco_base=100.0):
    return [
        [
            0,
            str(preco_base + i),
            str(preco_base + i + 1),
            str(preco_base + i - 1),
            str(preco_base + i + 0.5),
            "10.0",
        ]
        for i in range(n)
    ]


def _resp(payload):
    r = Mock()
    r.json.return_value = payload
    return r


# ── obter_klines: parse e contrato ──────────────────────────────────────────


def test_parse_retorna_as_5_series(monkeypatch):
    monkeypatch.setattr(klines.requests, "get", lambda *a, **k: _resp(_payload(3)))
    d = klines.obter_klines("BTCUSDT", "1h", 3)
    assert set(d.keys()) == {"abertura", "maxima", "minima", "fechamento", "volume"}
    assert len(d["fechamento"]) == 3


def test_valores_convertidos_para_float(monkeypatch):
    payload = [[0, "100.5", "101.0", "99.0", "100.8", "5.5"]]
    monkeypatch.setattr(klines.requests, "get", lambda *a, **k: _resp(payload))
    d = klines.obter_klines("BTCUSDT", "1h", 1)
    assert d["abertura"] == [100.5]
    assert d["maxima"] == [101.0]
    assert d["minima"] == [99.0]
    assert d["fechamento"] == [100.8]
    assert d["volume"] == [5.5]


def test_excecao_de_rede_retorna_none_sem_cache(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("sem rede")

    monkeypatch.setattr(klines.requests, "get", _boom)
    assert klines.obter_klines("BTCUSDT", "1h", 10) is None


def test_json_invalido_retorna_none(monkeypatch):
    r = Mock()
    r.json.side_effect = ValueError("json invalido")
    monkeypatch.setattr(klines.requests, "get", lambda *a, **k: r)
    assert klines.obter_klines("BTCUSDT", "1h", 10) is None


def test_usa_rest_base_url_configurado_nao_hardcode(monkeypatch):
    # Achado real da investigação: suporte.py e risco.verificar_volatilidade
    # hardcodeavam https://api.binance.com, ignorando REST_BASE_URL. O
    # módulo consolidado usa BASE_URL (setado a partir de REST_BASE_URL no
    # import) -- aqui simulamos um endpoint alternativo (ex: testnet) e
    # confirmamos que a URL de fato usada reflete isso.
    monkeypatch.setattr(klines, "BASE_URL", "https://testnet.example.com")
    capturado = {}

    def _fake_get(url, params=None, timeout=None):
        capturado["url"] = url
        return _resp(_payload(2))

    monkeypatch.setattr(klines.requests, "get", _fake_get)
    klines.obter_klines("BTCUSDT", "1h", 2)
    assert capturado["url"] == "https://testnet.example.com/api/v3/klines"


# ── cache: hit / miss / TTL / preserva stale ────────────────────────────────


def test_segunda_chamada_dentro_do_ttl_nao_bate_rede(monkeypatch):
    chamadas = []

    def _get(*a, **k):
        chamadas.append(1)
        return _resp(_payload(2))

    monkeypatch.setattr(klines.requests, "get", _get)
    d1 = klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=60)
    d2 = klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=60)
    assert len(chamadas) == 1
    assert d1 is d2  # mesmo objeto cacheado


def test_ttl_expirado_busca_de_novo(monkeypatch):
    chamadas = []

    def _get(*a, **k):
        chamadas.append(1)
        return _resp(_payload(2))

    monkeypatch.setattr(klines.requests, "get", _get)
    klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=0)
    klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=0)
    assert len(chamadas) == 2  # ttl=0 -> sempre expirado, sempre busca de novo


def test_chaves_diferentes_symbol_intervalo_limit_nao_compartilham_cache(monkeypatch):
    chamadas = []

    def _get(*a, **k):
        chamadas.append(1)
        return _resp(_payload(2))

    monkeypatch.setattr(klines.requests, "get", _get)
    klines.obter_klines("BTCUSDT", "1h", 100)
    klines.obter_klines("ETHUSDT", "1h", 100)  # symbol diferente
    klines.obter_klines("BTCUSDT", "4h", 100)  # intervalo diferente
    klines.obter_klines("BTCUSDT", "1h", 2)  # limit diferente
    assert len(chamadas) == 4  # nenhuma reaproveitou o cache de outra


def test_symbol_case_insensitive_compartilha_cache(monkeypatch):
    chamadas = []

    def _get(*a, **k):
        chamadas.append(1)
        return _resp(_payload(2))

    monkeypatch.setattr(klines.requests, "get", _get)
    klines.obter_klines("btcusdt", "1h", 100)
    klines.obter_klines("BTCUSDT", "1h", 100)
    assert len(chamadas) == 1  # mesma chave normalizada (upper)


def test_falha_apos_cache_populado_preserva_dado_antigo(monkeypatch):
    monkeypatch.setattr(klines.requests, "get", lambda *a, **k: _resp(_payload(2)))
    d1 = klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=0)

    def _boom(*a, **k):
        raise ConnectionError("sem rede")

    monkeypatch.setattr(klines.requests, "get", _boom)
    d2 = klines.obter_klines("BTCUSDT", "1h", 2, ttl_segundos=0)
    assert d2 is d1  # preserva o dado antigo em vez de descartar por falha


def test_falha_sem_cache_anterior_retorna_none(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("sem rede")

    monkeypatch.setattr(klines.requests, "get", _boom)
    assert klines.obter_klines("SOLUSDT", "1h", 2, ttl_segundos=0) is None


# ── E-7: validacao de symbol/intervalo ────────────────────────────────────────


class TestValidacaoDeSymbol:
    """Por que esta validacao e uma guarda de dinheiro e nao higiene.

    E-7 tornou `symbol` o PRIMEIRO parametro de `suporte.detectar_suportes` e de
    `regime.detectar` (antes vinha de uma constante de modulo "BTCUSDT"). Toda
    chamada posicional antiga -- `detectar_suportes("1h")` -- passaria "1h" como
    symbol. Sem esta validacao o resultado seria obter_klines("1h", "1h") ->
    HTTP 400 -> None -> o dict-vazio de fallback, e o par seguiria operando com
    suporte_forte=0: o mesmo modo de falha SILENCIOSA que E-7 existe para
    eliminar, so trocado de ativo-errado para dado-ausente.
    """

    def test_intervalo_passado_como_symbol_levanta(self):
        with pytest.raises(ValueError, match="symbol invalido"):
            klines.obter_klines("1h", "1h", 100)

    def test_mensagem_ensina_a_ordem_dos_argumentos(self):
        with pytest.raises(ValueError, match=r"obter_klines\(symbol, intervalo\)"):
            klines.obter_klines("4h", "4h")

    def test_symbol_curto_levanta(self):
        with pytest.raises(ValueError):
            klines.obter_klines("BTC", "1h")

    def test_symbol_com_separador_levanta(self):
        """A API de Spot nao aceita 'BTC/USDT' nem 'BTC-USDT'."""
        for ruim in ("BTC/USDT", "BTC-USDT", "BTC USDT"):
            with pytest.raises(ValueError):
                klines.obter_klines(ruim, "1h")

    def test_symbol_nao_string_levanta(self):
        for ruim in (None, 123456, ["BTCUSDT"]):
            with pytest.raises(ValueError):
                klines.obter_klines(ruim, "1h")

    def test_intervalo_invalido_levanta(self):
        with pytest.raises(ValueError, match="intervalo invalido"):
            klines.obter_klines("BTCUSDT", "7h")

    def test_pares_reais_passam(self, monkeypatch):
        monkeypatch.setattr(klines, "_fetch_rest", lambda sym, iv, lim: {"fechamento": [1.0]})
        klines._cache.clear()
        for par in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "1000PEPEUSDT"):
            assert klines.obter_klines(par, "1h", 10) is not None

    def test_todos_os_intervalos_da_binance_passam(self, monkeypatch):
        monkeypatch.setattr(klines, "_fetch_rest", lambda sym, iv, lim: {"fechamento": [1.0]})
        klines._cache.clear()
        for iv in klines.INTERVALOS_VALIDOS:
            assert klines.obter_klines("BTCUSDT", iv, 10) is not None

    def test_erro_de_programacao_nao_se_disfarca_de_falha_de_rede(self, monkeypatch):
        """Se o symbol invalido virasse None, a diferenca entre 'a Binance esta
        fora' e 'estou lendo o ativo errado' desapareceria do log."""
        chamou = []
        monkeypatch.setattr(klines, "_fetch_rest", lambda sym, iv, lim: chamou.append(sym))
        with pytest.raises(ValueError):
            klines.obter_klines("1h", "1h")
        assert chamou == [], "tentou ir a rede com symbol invalido"
