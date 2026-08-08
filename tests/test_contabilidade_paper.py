"""
Testes da frente E-8 — a contabilidade de paper tem de medir alguma coisa.

Estado medido no banco de producao em 2026-08-08, antes desta frente:

    log_avaliacoes .......... 7.628 linhas, TODAS em '%d/%m/%Y %H:%M:%S'
    query do relatorio ...... `timestamp LIKE '2026-08-08%'` -> 0 linhas
    log_trades .............. 0 linhas (nenhum escritor de producao)
    trades .................. 2.488.666 com trade_id NULL vs 386.124 com id
    sinais .................. 5.255 linhas, 0 com executado=1, 0 com pnl_usdt

Ou seja: 90 dias de Etapa 2 rodando sobre isso terminariam com um relatorio que
reporta zero — ou, pior, um numero errado que aprova capital real por acidente.

Nenhum teste aqui toca a rede nem o banco de producao (conftest.py da raiz
redireciona o DB e falha o teste se alguem abrir data/btc_data.db).
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import risco
from config.params_pares import get_params


@pytest.fixture(autouse=True)
def _isolar_estado_risco(monkeypatch):
    """Isola `risco._estado_risco`, que e global E PERSISTIDO.

    Sem isto, os testes deste arquivo falhavam SO na suite completa (passavam
    isolados): outro arquivo arma a trava permanente de I-8 ou deixa
    `posicoes_abertas >= 1`, e `validar_trade` passa a devolver o dict de
    BLOQUEIO — que nao tem as chaves `risco_usdt`/`stop_veio_do_sinal`/
    `limitado_por`, dai `KeyError` em vez de um assert legivel.
    Mesma fixture que tests/test_risco.py ja usava; nao seguir a convencao foi
    erro meu, e o sintoma (11 falhas na suite, 0 isoladas) e o classico de
    dependencia de ordem.
    """
    estado_original = dict(risco._estado_risco)
    carregado_original = risco._estado_carregado

    risco._estado_carregado = True  # nao toca o banco
    monkeypatch.setattr(risco.database, "salvar_risk_state", lambda *a, **k: None)
    monkeypatch.setattr(risco.database, "carregar_risk_state", lambda *a, **k: None)
    monkeypatch.setattr(risco.database, "salvar_bot_event", lambda *a, **k: None)
    monkeypatch.setattr(risco.telegram_bot, "alerta_circuit_breaker", lambda *a, **k: True)
    # Volatilidade calma e historico curto: os dois gates ficam inertes, entao o
    # que se mede aqui e sizing/contabilidade, nao os circuit breakers.
    # 0.01 = 1%, bem abaixo de VOLATILIDADE_MAXIMA (0.08 = 8%). O valor e
    # FRACAO, nao percentual: mockar 0.5 disparava "Volatilidade extrema:
    # 50.0%" e o gate 4 bloqueava tudo — foi o que causou as 11 falhas.
    monkeypatch.setattr(risco, "verificar_volatilidade", lambda *a, **k: 0.01)
    monkeypatch.setattr(risco, "obter_funding_rate", lambda *a, **k: 0.0, raising=False)

    risco._estado_risco.update(
        {
            "data_dia": str(datetime.now().date()),
            "pnl_dia": 0.0,
            "capital_inicio_dia": 1000.0,
            "bloqueado": False,
            "motivo_bloqueio": "",
            "posicoes_abertas": 0,
            "circuit_breaker_ativo": False,
            "travado": False,
            "motivo_travamento": "",
            "travado_em": None,
        }
    )
    yield
    risco._estado_risco.clear()
    risco._estado_risco.update(estado_original)
    risco._estado_carregado = carregado_original


# ══════════════════════════════════════════════════════════════════
#  1. risco_usdt deixa de ser ficcao — criterio de saida do plano
# ══════════════════════════════════════════════════════════════════


class TestRiscoReportadoEVerdadeiro:
    """Criterio de saida literal de E-8: "teste que prova risco_usdt ==
    (preco-stop)*qty com erro < 1% para os 3 pares"."""

    PARES = (("BTCUSDT", 65000.0), ("ETHUSDT", 1900.0), ("SOLUSDT", 74.0))

    @pytest.mark.parametrize("par,preco", PARES)
    def test_risco_usdt_bate_com_a_aritmetica(self, par, preco):
        stop_pct = get_params(par)["stop_pct"]
        stop = round(preco * (1 - stop_pct), 2)
        r = risco.validar_trade("COMPRA", preco, 1000.0, stop=stop, symbol=par)
        assert r["pode"] is True, f"bloqueado: {r.get('motivo')} | estado={risco._estado_risco}"
        esperado = r["tamanho_btc"] * (preco - stop)
        assert abs(r["risco_usdt"] - esperado) / esperado < 0.01

    def test_pares_com_stop_diferente_reportam_risco_diferente(self):
        """Antes os tres reportavam $3,00 — o risco do BTC, para todos.

        Medido: reportado $3/$3/$3 contra real $3/$4/$6, ou seja SOL subestimado
        em 2,00x e ETH em 1,33x. `risco_usdt` e o numero que um humano le para
        julgar exposicao; errado por 2x, ele tranquiliza no lugar de informar.
        """
        riscos = {}
        for par, preco in self.PARES:
            stop = round(preco * (1 - get_params(par)["stop_pct"]), 2)
            riscos[par] = risco.validar_trade(
                "COMPRA", preco, 1000.0, stop=stop, symbol=par
            )["risco_usdt"]
        assert len(set(riscos.values())) == 3, f"riscos colidiram: {riscos}"
        # A ordem segue os stop_pct: 1,5% < 2,0% < 3,0%
        assert riscos["BTCUSDT"] < riscos["ETHUSDT"] < riscos["SOLUSDT"]

    def test_sem_stop_cai_no_fallback_de_1_5_pct(self):
        """Retrocompatibilidade explicita: chamador que nao passa stop continua
        funcionando com o 1,5% de antes, e o retorno DIZ que foi fallback."""
        r = risco.validar_trade("COMPRA", 1900.0, 1000.0)
        assert r["stop_veio_do_sinal"] is False
        assert abs(r["stop_usado"] - 1900.0 * 0.985) < 0.01

    def test_stop_do_sinal_e_marcado_como_tal(self):
        r = risco.validar_trade("COMPRA", 1900.0, 1000.0, stop=1862.0)
        assert r["stop_veio_do_sinal"] is True
        assert r["stop_usado"] == 1862.0

    def test_stop_do_lado_errado_cai_no_fallback(self):
        """COMPRA com stop ACIMA da entrada nao pode virar distancia negativa no
        sizing. A invariante de E-7 barra o sinal antes, mas sizing e o lugar
        onde um numero absurdo se transforma em TAMANHO absurdo."""
        r = risco.validar_trade("COMPRA", 1900.0, 1000.0, stop=63521.65)
        assert r["stop_veio_do_sinal"] is False
        assert r["stop_usado"] < 1900.0

    def test_venda_com_stop_abaixo_cai_no_fallback(self):
        r = risco.validar_trade("VENDA", 1900.0, 1000.0, stop=1000.0)
        assert r["stop_veio_do_sinal"] is False
        assert r["stop_usado"] > 1900.0

    def test_stop_negativo_ou_zero_cai_no_fallback(self):
        for ruim in (0.0, -100.0):
            r = risco.validar_trade("COMPRA", 1900.0, 1000.0, stop=ruim)
            assert r["stop_veio_do_sinal"] is False


class TestLimitanteDoSizingEExposto:
    """`limitado_por` responde "o sizing veio de onde?" sem reengenharia depois.

    Medido em 2026-08-08: o teto de exposicao de 20% do capital domina em todo
    cenario real, o que significa que `kelly_do_banco()` (hoje 0,02, no proprio
    teto) NAO tem efeito nenhum sobre o tamanho — so o vol targeting tem, porque
    `mult_vol` escala o teto. Mudar essa hierarquia e mudar sizing, ou seja
    decisao de trading; expor qual restricao mandou e contabilidade.
    """

    def test_expoe_o_limitante(self):
        r = risco.validar_trade("COMPRA", 65000.0, 1000.0, stop=64025.0)
        assert r["limitado_por"] in ("teto_exposicao", "risco_por_trade")

    def test_teto_domina_no_cenario_real(self):
        r = risco.validar_trade("COMPRA", 65000.0, 1000.0, stop=64025.0)
        assert r["limitado_por"] == "teto_exposicao"

    def test_kelly_e_inerte_acima_de_0_3_pct(self):
        """Prova numerica do achado: acima de ~0,3% o fator de risco nao muda
        mais o tamanho. Se alguem mexer no teto e essa relacao mudar, este teste
        avisa em vez de a descoberta ter de ser refeita."""
        preco, cap = 65000.0, 1000.0
        stop = preco * 0.985
        tamanhos = {
            fr: risco.calcular_tamanho(cap, preco, stop, fator_risco=fr)
            for fr in (0.003, 0.005, 0.01, 0.02)
        }
        assert len(set(tamanhos.values())) == 1, f"Kelly voltou a ter efeito: {tamanhos}"
        # E abaixo do limiar ele TEM efeito — a fronteira existe de fato.
        assert risco.calcular_tamanho(cap, preco, stop, fator_risco=0.001) < min(
            tamanhos.values()
        )

    def test_limitante_indefinido_com_distancia_zero(self):
        assert risco._limitante_do_sizing(1000.0, 100.0, 100.0) == "indefinido"


# ══════════════════════════════════════════════════════════════════
#  2. Timestamp: o bug que mentia na direcao tranquilizadora
# ══════════════════════════════════════════════════════════════════


class TestTimestampISO:
    def test_analisar_produz_iso_no_campo_persistido(self, monkeypatch):
        """`timestamp` e o campo que logger.registrar_avaliacao grava."""
        from estrategias import otimizada

        datetime.strptime(_timestamp_de_analisar(monkeypatch), "%Y-%m-%d %H:%M:%S")
        assert otimizada is not None  # o import e a prova de que o modulo carrega

    def test_analisar_mantem_o_formato_br_para_exibicao(self, monkeypatch):
        r = _resultado_de_analisar(monkeypatch)
        datetime.strptime(r["timestamp_br"], "%d/%m/%Y %H:%M:%S")

    def test_os_dois_campos_descrevem_o_mesmo_instante(self, monkeypatch):
        r = _resultado_de_analisar(monkeypatch)
        iso = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        br = datetime.strptime(r["timestamp_br"], "%d/%m/%Y %H:%M:%S")
        assert iso == br

    def test_formato_br_nao_casaria_com_a_query_antiga(self):
        """Documenta por que o bug era invisivel: a query LIKE 'YYYY-MM-DD%'
        simplesmente nao encontra '08/08/2026 09:10:53', e o COUNT vira 0, e
        logger converte o None em 0.0. Nada falha; o numero so fica errado."""
        br = "08/08/2026 09:10:53"
        assert not br.startswith("2026-08-08")


def _resultado_de_analisar(monkeypatch):
    from estrategias import otimizada

    serie = [100.0 + i * 0.5 for i in range(100)]
    dados = {
        "abertura": serie[:],
        "maxima": [p * 1.002 for p in serie],
        "minima": [p * 0.998 for p in serie],
        "fechamento": serie,
        "volume": [1000.0] * 100,
    }
    monkeypatch.setattr(otimizada, "obter_klines", lambda *a, **k: dados)
    monkeypatch.setattr(otimizada.reg, "detectar", lambda s: {
        "symbol": s, "regime_final": "LATERAL", "pode_operar": False,
        "motivo": "-", "score": 40, "votos": {},
        "detalhes_tf": {"1h": {"adx": 10, "atr_ratio": 1.0, "regime": "LATERAL"}},
    })
    monkeypatch.setattr(otimizada.fg, "obter", lambda: {
        "valor": 50, "classificacao_pt": "Neutro", "pode_operar": True, "reducao_alvo": False,
    })
    monkeypatch.setattr(otimizada.sup, "detectar_suportes", lambda *a, **k: {
        "symbol": "BTCUSDT", "suportes": [], "resistencias": [], "suporte_forte": 0,
        "distancia_%": 99, "na_zona": False,
    })
    monkeypatch.setattr(otimizada.database, "salvar_sinal", lambda *a, **k: 1)
    return otimizada.analisar("BTCUSDT", ensemble_result={"prob_ensemble": 0.5})


def _timestamp_de_analisar(monkeypatch):
    return _resultado_de_analisar(monkeypatch)["timestamp"]


class TestJanelaDoDia:
    def test_range_cobre_o_dia_inteiro(self):
        from logger import _janela_do_dia

        base = datetime(2026, 8, 8, 14, 30, 0)
        hoje, inicio, fim = _janela_do_dia(base)
        assert (hoje, inicio, fim) == (
            "2026-08-08",
            "2026-08-08 00:00:00",
            "2026-08-09 00:00:00",
        )

    def test_range_atravessa_virada_de_mes(self):
        from logger import _janela_do_dia

        _, inicio, fim = _janela_do_dia(datetime(2026, 8, 31, 23, 59, 59))
        assert inicio == "2026-08-31 00:00:00"
        assert fim == "2026-09-01 00:00:00"

    def test_range_atravessa_virada_de_ano(self):
        from logger import _janela_do_dia

        _, inicio, fim = _janela_do_dia(datetime(2026, 12, 31, 12, 0, 0))
        assert fim == "2027-01-01 00:00:00"

    @pytest.mark.parametrize(
        "ts",
        [
            "2026-08-08 09:10:53",  # o formato canonico
            "2026-08-08 00:00:00",  # borda inferior, inclusiva
            "2026-08-08T09:10:53",  # separador 'T'
            "2026-08-08T09:10:53.920379",  # ISO com microssegundos (sinais.timestamp)
            "2026-08-08 23:59:59",  # borda superior
        ],
    )
    def test_range_tolera_as_variantes_iso_do_repo(self, ts):
        """O LIKE nao tolerava variacao de prefixo; o range compara ORDEM.

        As tres variantes existem de fato no banco: log_avaliacoes usa
        '%Y-%m-%d %H:%M:%S' e sinais.timestamp usa datetime.now().isoformat().
        Comparadas como texto, ' ' (0x20) < 'T' (0x54) e o prefixo de data tem
        largura fixa, entao todas caem dentro da janela correta.
        """
        from logger import _janela_do_dia

        _, inicio, fim = _janela_do_dia(datetime(2026, 8, 8, 12, 0, 0))
        assert inicio <= ts < fim

    def test_formato_br_fica_fora_do_range(self):
        """Nao e acidente: linha em formato BR NAO deve entrar com data errada.
        Ela fica invisivel ate ser normalizada pelo script — invisivel e ruim,
        mas contada no dia errado seria pior."""
        from logger import _janela_do_dia

        _, inicio, fim = _janela_do_dia(datetime(2026, 8, 8, 12, 0, 0))
        assert not (inicio <= "08/08/2026 09:10:53" < fim)


# ══════════════════════════════════════════════════════════════════
#  3. O migrador de timestamps
# ══════════════════════════════════════════════════════════════════


class TestNormalizadorDeTimestamps:
    def _script(self):
        import importlib.util
        import os

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "normalizar_timestamps", os.path.join(raiz, "scripts", "normalizar_timestamps.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("08/08/2026 09:10:53", "2026-08-08 09:10:53"),
            ("08/08/2026 09:10", "2026-08-08 09:10:00"),
            ("08/08/2026", "2026-08-08 00:00:00"),
            ("01/12/2026 23:59:59", "2026-12-01 23:59:59"),  # dia/mes, nao mes/dia
        ],
    )
    def test_converte_br_para_iso(self, entrada, esperado):
        assert self._script().para_iso(entrada) == esperado

    @pytest.mark.parametrize(
        "ja_ok",
        [
            "2026-08-08 09:10:53",
            "2026-08-08",
            "2026-08-08T09:10:53",
            "2026-04-01T05:00:32.920379",  # o formato de sinais.timestamp
        ],
    )
    def test_nao_toca_o_que_ja_e_iso(self, ja_ok):
        """As 5.255 linhas de `sinais` apareciam como "irreconheciveis" no
        primeiro dry-run porque este formato faltava na lista — falso alarme que
        teria mandado alguem cacar um problema inexistente."""
        assert self._script().para_iso(ja_ok) is None

    @pytest.mark.parametrize("lixo", ["", None, "ontem", "2026", 12345])
    def test_nao_inventa_data(self, lixo):
        assert self._script().para_iso(lixo) is None

    def test_migracao_e_idempotente(self, tmp_path):
        mod = self._script()
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE log_avaliacoes (timestamp TEXT, symbol TEXT)")
        conn.executemany(
            "INSERT INTO log_avaliacoes VALUES (?,?)",
            [("08/08/2026 09:10:53", "BTCUSDT"), ("2026-08-08 10:00:00", "ETHUSDT")],
        )
        conn.commit()
        conn.close()

        assert mod.normalizar(str(db), confirmar=True) == 1
        assert mod.normalizar(str(db), confirmar=True) == 0  # 2a passada: nada

        conn = sqlite3.connect(db)
        vals = sorted(r[0] for r in conn.execute("SELECT timestamp FROM log_avaliacoes"))
        conn.close()
        assert vals == ["2026-08-08 09:10:53", "2026-08-08 10:00:00"]

    def test_dry_run_nao_altera_nada(self, tmp_path):
        mod = self._script()
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE log_avaliacoes (timestamp TEXT, symbol TEXT)")
        conn.execute("INSERT INTO log_avaliacoes VALUES ('08/08/2026 09:10:53','BTCUSDT')")
        conn.commit()
        conn.close()

        mod.normalizar(str(db), confirmar=False)

        conn = sqlite3.connect(db)
        assert conn.execute("SELECT timestamp FROM log_avaliacoes").fetchone()[0] == (
            "08/08/2026 09:10:53"
        )
        conn.close()

    def test_tabela_ausente_nao_quebra(self, tmp_path):
        """O script varre 4 (tabela, coluna); um banco sem alguma delas nao pode
        derrubar a migracao das outras."""
        mod = self._script()
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE log_avaliacoes (timestamp TEXT)")
        conn.commit()
        conn.close()
        mod.normalizar(str(db), confirmar=True)  # nao deve levantar


# ══════════════════════════════════════════════════════════════════
#  4. O gate nao pode contar estrategia reprovada
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def db_gate(tmp_path):
    """Banco com trades fechados de DUAS sources: a primaria (perdedora) e a
    trend (vencedora). Se o filtro falhar, o gate aprova pelo motivo errado."""
    db = tmp_path / "gate.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE sinais (
               id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, tipo TEXT,
               symbol TEXT, preco REAL, preco_saida REAL, pnl_usdt REAL,
               pnl_pct REAL, barreira_tocada TEXT, executado INTEGER, source TEXT)"""
    )
    base = datetime(2026, 5, 1)
    linhas = []
    for i in range(4):  # primaria: perde
        linhas.append(
            ((base + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "COMPRA", "BTCUSDT",
             100.0, 98.0, -2.0, -2.0, "STOP", 1, "estrategia_otimizada")
        )
    for i in range(6):  # trend: ganha (REPROVADA no hold-out)
        linhas.append(
            ((base + timedelta(days=10 + i)).strftime("%Y-%m-%d %H:%M:%S"), "COMPRA", "BTCUSDT",
             100.0, 130.0, 30.0, 30.0, "TARGET", 1, "trend_live")
        )
    linhas.append(  # legado sem source: conta como primaria
        ((base - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), "COMPRA", "BTCUSDT",
         100.0, 101.0, 1.0, 1.0, "TARGET", 1, None)
    )
    conn.executemany(
        "INSERT INTO sinais (timestamp,tipo,symbol,preco,preco_saida,pnl_usdt,pnl_pct,"
        "barreira_tocada,executado,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        linhas,
    )
    conn.commit()
    return conn


class TestGateFiltraPorSource:
    def test_conta_apenas_a_primaria(self, db_gate, capsys):
        import relatorio_gate

        trades = relatorio_gate.carregar_trades_fechados(db_gate, "?")
        # 4 perdedores da primaria + 1 legado sem source = 5
        assert len(trades) == 5
        assert all(t["pnl_usdt"] <= 1.0 for t in trades)

    def test_pnl_da_trend_nao_entra(self, db_gate):
        import relatorio_gate

        trades = relatorio_gate.carregar_trades_fechados(db_gate, "?")
        total = sum(t["pnl_usdt"] for t in trades)
        # -2*4 + 1 = -7. Com a trend entrando seria -7 + 180 = +173.
        assert total == pytest.approx(-7.0)

    def test_exclusao_e_anunciada_em_voz_alta(self, db_gate, capsys):
        """Um gate que descarta linhas sem dizer quantas e indistinguivel de um
        gate sem dados."""
        import relatorio_gate

        relatorio_gate.carregar_trades_fechados(db_gate, "?")
        saida = capsys.readouterr().out
        assert "EXCLUIDOS 6" in saida
        assert "trend_live" in saida

    def test_legado_sem_source_conta_como_primaria(self, db_gate):
        """As ~5.255 linhas gravadas antes de `source` ser disciplinado vieram
        todas de estrategia_otimizada; exclui-las descartaria historico real."""
        import relatorio_gate

        trades = relatorio_gate.carregar_trades_fechados(db_gate, "?")
        assert any(t["pnl_usdt"] == 1.0 for t in trades)

    def test_source_none_desativa_o_filtro(self, db_gate):
        """Escape hatch para inspecao manual — e o default continua a primaria."""
        import relatorio_gate

        assert len(relatorio_gate.carregar_trades_fechados(db_gate, "?", source=None)) == 11

    def test_default_da_funcao_e_a_primaria(self):
        import inspect

        import relatorio_gate

        p = inspect.signature(relatorio_gate.carregar_trades_fechados).parameters["source"]
        assert p.default == relatorio_gate.SOURCE_PRIMARIA == "estrategia_otimizada"


# ══════════════════════════════════════════════════════════════════
#  5. O circuito de meta-labeling denuncia quando nao fecha
# ══════════════════════════════════════════════════════════════════


class TestVinculoDeSinalNaoFalhaEmSilencio:
    def test_marcar_executado_com_none_avisa(self, capsys):
        import database

        database.marcar_sinal_executado(None, symbol="ETHUSDT")
        saida = capsys.readouterr().out
        assert "AVISO" in saida
        assert "ETHUSDT" in saida
        assert "Etapa 2" in saida

    def test_fechamento_com_none_avisa_com_o_pnl(self, capsys):
        """Este e o ponto mais caro de perder: o PnL REAL existe, foi calculado,
        e ia para o lixo sem uma linha de log."""
        import database

        database.atualizar_sinal_fechamento(None, 105.0, 12.34, 5.6, "TARGET")
        saida = capsys.readouterr().out
        assert "12.34" in saida
        assert "TARGET" in saida

    def test_com_id_valido_grava_de_fato(self, tmp_path, monkeypatch):
        import database

        db = tmp_path / "s.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE sinais (id INTEGER PRIMARY KEY AUTOINCREMENT, executado INTEGER "
            "DEFAULT 0, executado_em TEXT, preco_saida REAL, pnl_usdt REAL, pnl_pct REAL, "
            "barreira_tocada TEXT)"
        )
        conn.execute("INSERT INTO sinais (executado) VALUES (0)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(database, "_backend", lambda: "sqlite")
        monkeypatch.setattr(database, "conectar", lambda: sqlite3.connect(db))

        database.marcar_sinal_executado(1, symbol="BTCUSDT")
        database.atualizar_sinal_fechamento(1, 105.0, 5.0, 5.0, "TARGET")

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT executado, pnl_usdt, barreira_tocada FROM sinais WHERE id=1"
        ).fetchone()
        conn.close()
        assert row == (1, 5.0, "TARGET")


# ══════════════════════════════════════════════════════════════════
#  6. ScaleIn fora do caminho vivo
# ══════════════════════════════════════════════════════════════════


class TestScaleInForaDoCaminhoVivo:
    def test_main_nao_importa_mais_scalein(self):
        import main

        assert not hasattr(main, "ScaleIn"), "ScaleIn voltou ao caminho vivo"

    def test_a_classe_continua_existindo_e_funcional(self):
        """@Zeta: nao deletar — aposentar do caminho vivo e preservar."""
        from suporte import ScaleIn

        si = ScaleIn(tamanho_total_btc=0.001, suporte=100.0)
        assert si.entrada_parcela1(101.0) == pytest.approx(0.0004)

    def test_abre_com_o_tamanho_integral(self):
        """As parcelas 2/3 eram inalcancaveis (o gate `not exec_par.posicao` fecha
        a porta depois da 1a), e o objeto nao resetado dimensionava o trade
        SEGUINTE sobre o tamanho_total do ANTERIOR."""
        import inspect

        import main

        codigo = "\n".join(
            linha.split("#")[0]
            for linha in inspect.getsource(main.loop_par).splitlines()
            if not linha.strip().startswith("#")
        )
        assert "entrada_parcela1" not in codigo
        assert "parcela = tamanho" in codigo


# ══════════════════════════════════════════════════════════════════
#  7. Preco fresco na ordem, e sinal velho descartado sem alarme falso
# ══════════════════════════════════════════════════════════════════


class TestPrecoFrescoNaOrdem:
    def test_ordem_usa_preco_mercado_nao_o_de_kline(self):
        import inspect

        import main

        codigo = "\n".join(
            linha.split("#")[0]
            for linha in inspect.getsource(main.loop_par).splitlines()
            if not linha.strip().startswith("#")
        )
        assert "exec_par.abrir_long(\n                        preco_mercado," in codigo

    def test_sinal_velho_e_descartado_nao_escalado(self):
        """Se o mercado andou mais que stop_pct durante o TTL de 30s, o trio fica
        incoerente — mas isso e SINAL VELHO, nao insumo quebrado. Tratar como
        CRITICAL geraria alarme falso justo no canal que I-9 fez funcionar."""
        import inspect

        import main

        fonte = inspect.getsource(main.loop_par)
        assert "SINAL VELHO" in fonte
        i_velho = fonte.index("desatualizado = incoerencia_de_precos")
        i_abrir = fonte.index("exec_par.abrir_long(")
        assert i_velho < i_abrir, "a checagem tem de vir ANTES de mandar a ordem"
