"""
Testes da frente I-11 — substrato versionado e vereditos re-deriváveis.

Os cinco FAILs pré-registrados são a única coisa que hoje impede o capital de
entrar, e até esta frente eram PROSA: números escritos em documentos, gerados por
comandos sem entrypoint, sobre uma tabela que mudava sozinha.

Medido no banco de produção em 2026-08-08:

    BTCUSDT/1h ..... 17.563 velas, primeira em 1711994400000
    ETHUSDT/1h ..... 17.520 velas, primeira em 1712149200000
    SOLUSDT/1h ..... 17.520 velas, primeira em 1712149200000

As 43 velas extras do BTC estão no INÍCIO (43 horas antes) — a tabela cresceu
para trás. Com `int(N * 0.65)`, a fronteira do hold-out caía em 2025-07-21 09:00Z
para BTC e 2025-07-22 01:00Z para ETH/SOL: dezesseis horas de diferença entre
ativos, por nenhuma razão metodológica.

Nenhum teste aqui toca a rede nem o banco de produção.
"""

import csv
import json
import os
import sqlite3

import pytest

from research import snapshot


# ══════════════════════════════════════════════════════════════════
#  1. Snapshot: imutável e verificável
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def banco_falso(tmp_path):
    """Banco com as 3 séries, de tamanhos DIFERENTES — reproduz o defeito real."""
    db = tmp_path / "k.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, intervalo TEXT, timestamp INTEGER, "
        "abertura REAL, maxima REAL, minima REAL, fechamento REAL, volume REAL)"
    )
    base = 1_700_000_000_000
    hora = 3_600_000
    linhas = []
    for sym, n, desloc in (("BTCUSDT", 120, -43), ("ETHUSDT", 100, 0), ("SOLUSDT", 100, 0)):
        for i in range(n):
            t = base + (i + desloc) * hora
            p = 100.0 + i
            linhas.append((sym, "1h", t, p, p * 1.01, p * 0.99, p, 1000.0))
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?)", linhas)
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def snap(tmp_path, banco_falso, monkeypatch):
    monkeypatch.setattr(snapshot, "DIR_SNAPSHOTS", str(tmp_path / "snapshots"))
    monkeypatch.setattr(snapshot, "DB_PATH", banco_falso)
    return snapshot.criar("teste", db_path=banco_falso)


class TestSnapshotImutavel:
    def test_cria_um_csv_por_serie_mais_manifest(self, snap):
        arquivos = sorted(os.listdir(snap))
        assert arquivos == [
            "BTCUSDT_1h.csv", "ETHUSDT_1h.csv", "SOLUSDT_1h.csv", "manifest.json",
        ]

    def test_manifest_registra_contagem_e_bordas(self, snap):
        m = snapshot.ler_manifest("teste")
        por_sym = {s["symbol"]: s for s in m["series"]}
        assert por_sym["BTCUSDT"]["n"] == 120
        assert por_sym["ETHUSDT"]["n"] == 100
        assert por_sym["BTCUSDT"]["primeiro_ts"] < por_sym["ETHUSDT"]["primeiro_ts"]

    def test_verificar_aprova_snapshot_intacto(self, snap):
        assert snapshot.verificar("teste") == []

    def test_verificar_detecta_alteracao_de_um_byte(self, snap):
        """É o ponto inteiro do sha256: substrato mexido tem de ser detectável.

        Sem isso, alguém edita um CSV (ou uma sincronização de arquivos o
        corrompe) e todos os vereditos derivados continuam parecendo válidos.
        """
        caminho = os.path.join(snap, "ETHUSDT_1h.csv")
        with open(caminho, "a", encoding="utf-8", newline="") as fp:
            fp.write("1,1,1,1,1,1\n")
        problemas = snapshot.verificar("teste")
        assert len(problemas) == 1
        assert problemas[0]["erro"] == "sha256 divergente"

    def test_verificar_detecta_arquivo_removido(self, snap):
        os.remove(os.path.join(snap, "SOLUSDT_1h.csv"))
        assert snapshot.verificar("teste")[0]["erro"] == "ausente"

    def test_recusa_sobrescrever(self, snap, banco_falso):
        """Um snapshot mutável não é um snapshot."""
        with pytest.raises(FileExistsError, match="IMUTÁVEIS|IMUTAVEIS"):
            snapshot.criar("teste", db_path=banco_falso)

    def test_carregar_serie_devolve_a_tupla_de_edge_lab(self, snap):
        ts, m, n, f, v = snapshot.carregar_serie("BTCUSDT", "1h", "teste")
        assert len(ts) == len(m) == len(n) == len(f) == len(v) == 120
        assert f[0] == 100.0

    def test_carregar_serie_recusa_snapshot_corrompido(self, snap):
        """Ler um snapshot corrompido é pior que ler a tabela viva: a tabela
        viva ao menos não finge ser imutável."""
        caminho = os.path.join(snap, "BTCUSDT_1h.csv")
        with open(caminho, "a", encoding="utf-8", newline="") as fp:
            fp.write("9,9,9,9,9,9\n")
        with pytest.raises(ValueError, match="CORROMPIDO"):
            snapshot.carregar_serie("BTCUSDT", "1h", "teste")

    def test_serie_ausente_no_snapshot_levanta_keyerror(self, snap):
        with pytest.raises(KeyError):
            snapshot.carregar_serie("DOGEUSDT", "1h", "teste")

    def test_csv_usa_lf_para_o_hash_bater_entre_sistemas(self, snap):
        """O critério de saída fala em "duas máquinas diferentes". Se o CSV
        saísse com CRLF no Windows e LF no Linux, o sha256 do MESMO dado
        divergiria e a verificação acusaria corrupção onde não há."""
        with open(os.path.join(snap, "ETHUSDT_1h.csv"), "rb") as fp:
            bruto = fp.read()
        assert b"\r\n" not in bruto

    def test_manifest_avisa_que_nao_reconstroi_o_passado(self, snap):
        """Um snapshot que se apresentasse como "os dados originais" seria pior
        que nenhum: os FAILs de julho foram medidos sobre uma série que não
        existe mais."""
        m = snapshot.ler_manifest("teste")
        assert "NAO reconstroi" in m["aviso"] or "NÃO reconstrói" in m["aviso"]


# ══════════════════════════════════════════════════════════════════
#  2. Hold-out por DATA: a fronteira parou de andar
# ══════════════════════════════════════════════════════════════════


class TestFronteiraDoHoldout:
    def test_mesma_data_para_series_de_tamanhos_diferentes(self):
        """O defeito central de I-11, em forma de teste.

        Duas séries que cobrem o mesmo período mas têm contagens diferentes
        precisam produzir a MESMA data de corte. Com fração, não produziam.
        """
        import numpy as np

        from research.edge_lab import HOLDOUT_INICIO_MS, indice_do_holdout

        hora = 3_600_000
        # A série "longa" começa 43 horas antes — exatamente o caso real do BTC.
        curta = np.array([HOLDOUT_INICIO_MS - 1000 * hora + i * hora for i in range(2000)])
        longa = np.array([HOLDOUT_INICIO_MS - 1043 * hora + i * hora for i in range(2043)])

        i_curta = indice_do_holdout(curta)
        i_longa = indice_do_holdout(longa)
        assert curta[i_curta] == longa[i_longa] == HOLDOUT_INICIO_MS
        # E o hold-out tem o MESMO número de velas nas duas.
        assert len(curta) - i_curta == len(longa) - i_longa

    def test_fracao_antiga_teria_divergido(self):
        """Prova de que o teste acima não é trivial: a fórmula antiga falha."""
        n_curta, n_longa = 2000, 2043
        assert int(n_curta * 0.65) != int(n_longa * 0.65) - 43

    def test_acrescentar_dado_no_fim_nao_move_a_fronteira(self):
        import numpy as np

        from research.edge_lab import indice_do_holdout

        from research.edge_lab import HOLDOUT_INICIO_MS

        hora = 3_600_000
        base = [HOLDOUT_INICIO_MS - 1000 * hora + i * hora for i in range(2000)]
        antes = np.array(base)
        depois = np.array(base + [base[-1] + (i + 1) * hora for i in range(500)])
        assert antes[indice_do_holdout(antes)] == depois[indice_do_holdout(depois)]

    def test_serie_que_nao_cobre_o_periodo_levanta(self):
        """Silêncio aqui produziria um hold-out vazio ou a série inteira como
        hold-out, e nos dois casos o número sairia sem sentido."""
        import numpy as np

        from research.edge_lab import HOLDOUT_INICIO_MS, indice_do_holdout

        hora = 3_600_000
        toda_antiga = np.array([HOLDOUT_INICIO_MS - (5000 - i) * hora for i in range(1000)])
        with pytest.raises(ValueError, match="fora da série|fora da serie"):
            indice_do_holdout(toda_antiga)

    def test_carry_usa_a_mesma_fronteira_do_edge(self):
        """Duas datas seriam duas metodologias."""
        from research.carry_lab import HOLDOUT_INICIO_MS as carry_ms
        from research.edge_lab import HOLDOUT_INICIO_MS as edge_ms

        assert carry_ms == edge_ms

    def test_data_congelada_e_a_pre_registrada(self):
        """Mudar esta data invalida todos os vereditos anteriores. O teste
        existe para que a mudança seja deliberada, nunca incidental."""
        from datetime import datetime, timezone

        from research.edge_lab import HOLDOUT_INICIO_MS

        d = datetime.fromtimestamp(HOLDOUT_INICIO_MS / 1000, tz=timezone.utc)
        assert d.strftime("%Y-%m-%d %H:%M:%S") == "2025-07-22 00:00:00"


# ══════════════════════════════════════════════════════════════════
#  3. A trava de uso único agora LÊ o registro que escreve
# ══════════════════════════════════════════════════════════════════


class TestTravaDeUsoUnico:
    @pytest.fixture
    def metodologia_temp(self, tmp_path, monkeypatch):
        from research import edge_lab

        arq = tmp_path / "METODOLOGIA.md"
        arq.write_text("# metodologia\n", encoding="utf-8")
        monkeypatch.setattr(edge_lab, "METODOLOGIA", str(arq))
        return arq

    def test_virgem_quando_nao_ha_registro(self, metodologia_temp):
        from research.edge_lab import holdout_ja_consumido

        assert holdout_ja_consumido("BTCUSDT") is None

    def test_detecta_consumo_anterior(self, metodologia_temp):
        from research.edge_lab import _MARCA_HOLDOUT, holdout_ja_consumido

        with open(metodologia_temp, "a", encoding="utf-8") as fp:
            fp.write(f"| 2026-07-24 | {_MARCA_HOLDOUT} (symbol=BTCUSDT, H=8) | IC=-0.01 |\n")
        assert "BTCUSDT" in holdout_ja_consumido("BTCUSDT")

    def test_consumo_de_um_par_nao_trava_outro(self, metodologia_temp):
        from research.edge_lab import _MARCA_HOLDOUT, holdout_ja_consumido

        with open(metodologia_temp, "a", encoding="utf-8") as fp:
            fp.write(f"| x | {_MARCA_HOLDOUT} (symbol=BTCUSDT) | IC=0 |\n")
        assert holdout_ja_consumido("ETHUSDT") is None

    def test_segunda_avaliacao_e_recusada(self, metodologia_temp, monkeypatch):
        """A trava antiga era SÓ o kwarg `confirmo_uso_unico`: passar True duas
        vezes funcionava, e o "registro permanente" ficava num try/except sem
        nenhum leitor. Um hold-out reavaliável não é hold-out."""
        from research import edge_lab

        with open(metodologia_temp, "a", encoding="utf-8") as fp:
            fp.write(f"| x | {edge_lab._MARCA_HOLDOUT} (symbol=BTCUSDT) | IC=-0.0123 |\n")
        with pytest.raises(RuntimeError, match="JÁ FOI CONSUMIDO|JA FOI CONSUMIDO"):
            edge_lab.avaliar_holdout("BTCUSDT", 0, 8, confirmo_uso_unico=True)

    def test_mensagem_cita_o_registro_anterior(self, metodologia_temp):
        from research import edge_lab

        with open(metodologia_temp, "a", encoding="utf-8") as fp:
            fp.write(f"| 2026-07-24 | {edge_lab._MARCA_HOLDOUT} (symbol=BTCUSDT) | IC=-0.0123 |\n")
        with pytest.raises(RuntimeError, match="IC=-0.0123"):
            edge_lab.avaliar_holdout("BTCUSDT", 0, 8, confirmo_uso_unico=True)

    def test_sem_confirmacao_continua_recusando(self, metodologia_temp):
        from research import edge_lab

        with pytest.raises(RuntimeError, match="USO ÚNICO|USO UNICO"):
            edge_lab.avaliar_holdout("BTCUSDT", 0, 8)

    def test_registro_nao_e_mais_engolido_por_try_except(self):
        """Se a gravação falhar em silêncio, a trava não existe para a próxima
        execução — e o veredito se apresenta como uso único sem ser."""
        import inspect

        from research import edge_lab

        fonte = inspect.getsource(edge_lab.avaliar_holdout)
        trecho = fonte[fonte.index("with open(edge_lab.METODOLOGIA".replace("edge_lab.", "")):] \
            if "with open(METODOLOGIA" in fonte else fonte
        assert "except Exception:\n        pass" not in trecho

    def test_carry_ganhou_trava(self):
        """carry_lab não tinha trava NENHUMA: `--holdout` era uma flag booleana,
        sem confirmação e sem registro."""
        from research import carry_lab

        assert hasattr(carry_lab, "holdout_ja_consumido")
        assert hasattr(carry_lab, "registrar_consumo_holdout")


# ══════════════════════════════════════════════════════════════════
#  4. Entrypoints que produziram veredito e não existiam
# ══════════════════════════════════════════════════════════════════


class TestEntrypoints:
    @pytest.mark.parametrize("flag", ["--perm-auc", "--economia"])
    def test_flag_existe_no_argparse(self, flag):
        """Sem entrypoint, "re-derive você mesmo" significava reescrever a
        chamada de cabeça — as duas funções não tinham NENHUM call site."""
        import inspect

        from research import edge_lab

        assert flag in inspect.getsource(edge_lab.main)

    def test_reproduzir_e_executavel_como_modulo(self):
        import research.reproduzir as rep

        assert callable(rep.derivar)
        assert callable(rep.comparar)


# ══════════════════════════════════════════════════════════════════
#  5. Comparação exige diferença 0.0
# ══════════════════════════════════════════════════════════════════


def _veredito(ic=0.02, p=0.4, sha="abc", veredito="FAIL"):
    return {
        "gerado_em": "2026-08-08T00:00:00Z",
        "snapshot": {"rotulo": "t", "sha256_por_serie": {"BTCUSDT/1h": sha}},
        "parametros": {"symbol": "BTCUSDT", "seed": 42,
                       "holdout_inicio_ms": 1753142400000, "ic_piso_economico": 0.03},
        "medicoes": {"n_amostras": 100, "melhor_feature": "rsi", "melhor_horizonte": 8,
                     "ic_max_abs": ic, "p_permutacao": p,
                     "ics": {"rsi|H8": ic, "atr_rel|H8": 0.01}},
        "veredito": veredito,
        "criterios": {},
    }


class TestComparacao:
    def test_identicos_nao_divergem(self):
        from research.reproduzir import comparar

        assert comparar(_veredito(), _veredito()) == []

    def test_tolerancia_e_zero(self):
        """O critério de saída de I-11 é literalmente "diferença 0.0" — não
        "diferença pequena". Uma tolerância frouxa esconde não-determinismo."""
        from research.reproduzir import TOLERANCIA, comparar

        assert TOLERANCIA == 0.0
        difs = comparar(_veredito(ic=0.02), _veredito(ic=0.02 + 1e-12))
        assert difs, "diferença de 1e-12 passou — a tolerância não é 0.0"

    def test_veredito_diferente_e_reportado(self):
        from research.reproduzir import comparar

        difs = comparar(_veredito(veredito="EDGE_CANDIDATO"), _veredito(veredito="FAIL"))
        assert any("VEREDITO" in d for d in difs)

    def test_substrato_mudado_e_reportado_como_causa(self):
        """Dizer "IC divergiu" quando o dado mudou mandaria alguém depurar
        estatística quando o problema é o substrato."""
        from research.reproduzir import comparar

        difs = comparar(_veredito(sha="novo", ic=0.05), _veredito(sha="velho", ic=0.02))
        assert any("SUBSTRATO" in d for d in difs)
        assert any("consequência" in d or "consequencia" in d for d in difs)

    def test_mudanca_de_parametro_pre_registrado_e_reportada(self):
        from research.reproduzir import comparar

        a = _veredito()
        b = _veredito()
        b["parametros"]["holdout_inicio_ms"] = 1
        assert any("holdout_inicio_ms" in d for d in comparar(a, b))

    def test_ic_de_qualquer_feature_da_familia_e_comparado(self):
        """"diferença 0.0 em todos os IC" — não só no vencedor."""
        from research.reproduzir import comparar

        a = _veredito()
        b = _veredito()
        b["medicoes"]["ics"]["atr_rel|H8"] = 0.0999
        difs = comparar(a, b)
        assert any("atr_rel|H8" in d for d in difs)


# ══════════════════════════════════════════════════════════════════
#  6. Coleta determinística
# ══════════════════════════════════════════════════════════════════


class TestColetaDeterministica:
    def test_db_path_nao_depende_do_cwd(self):
        """Era "data/btc_data.db" relativo: rodar de outro diretório criava um
        SQLite VAZIO ali, a coleta "funcionava", e o banco real ficava intocado."""
        from backtesting import coletar_dados

        assert os.path.isabs(coletar_dados.DB_PATH)
        assert coletar_dados.DB_PATH.endswith(os.path.join("data", "btc_data.db"))

    def test_janela_explicita_e_convertida_para_utc(self):
        from backtesting.coletar_dados import _ms

        assert _ms("2025-07-22") == 1753142400000

    def test_fim_do_dia_inclui_o_dia_inteiro(self):
        from backtesting.coletar_dados import _ms

        assert _ms("2025-07-22", fim_do_dia=True) - _ms("2025-07-22") == 86_399_999

    def test_sem_janela_e_sem_dias_levanta(self):
        from backtesting.coletar_dados import baixar_klines

        with pytest.raises(ValueError, match="exige"):
            baixar_klines("BTCUSDT", "1h")

    def test_vela_em_formacao_e_descartada(self, monkeypatch):
        """O `INSERT OR IGNORE` CONGELA a vela parcial para sempre: a próxima
        coleta encontra a chave já existente e ignora a versão correta. Havia uma
        vela permanentemente errada no fim de cada série."""
        import time as _t

        from backtesting import coletar_dados

        agora = int(_t.time() * 1000)
        hora = 3_600_000
        # k[0]=openTime, k[6]=closeTime. A última ainda não fechou.
        lote = [
            [agora - 3 * hora, "1", "1", "1", "1", "1", agora - 2 * hora],
            [agora - 2 * hora, "1", "1", "1", "1", "1", agora - 1 * hora],
            [agora - 1 * hora, "1", "1", "1", "1", "1", agora + 1 * hora],  # EM FORMAÇÃO
        ]
        chamadas = {"n": 0}

        class _Resp:
            @staticmethod
            def raise_for_status():
                pass

            @staticmethod
            def json():
                chamadas["n"] += 1
                return lote if chamadas["n"] == 1 else []

        monkeypatch.setattr(coletar_dados.requests, "get", lambda *a, **k: _Resp())
        monkeypatch.setattr(coletar_dados.time, "sleep", lambda s: None)
        r = coletar_dados.baixar_klines("BTCUSDT", "1h", dias=1)
        assert len(r) == 2
        assert all(int(k[6]) < agora for k in r)

    def test_falha_de_rede_levanta_em_vez_de_devolver_parcial(self, monkeypatch):
        """O `break` silencioso devolvia série PARCIAL como se fosse completa, e
        main() saía com código 0."""
        from backtesting import coletar_dados

        def _explode(*a, **k):
            raise ConnectionError("rede caiu")

        monkeypatch.setattr(coletar_dados.requests, "get", _explode)
        with pytest.raises(coletar_dados.ColetaIncompleta, match="INCOMPLETA"):
            coletar_dados.baixar_klines("BTCUSDT", "1h", dias=1)

    def test_main_devolve_codigo_de_erro(self):
        import inspect

        from backtesting import coletar_dados

        fonte = inspect.getsource(coletar_dados.main)
        assert "return 1" in fonte
        assert "ColetaIncompleta" in fonte

    def test_janela_fixa_e_movel_sao_exclusivas(self):
        import inspect

        from backtesting import coletar_dados

        assert "exclusivos" in inspect.getsource(coletar_dados.main)


# ══════════════════════════════════════════════════════════════════
#  7. klines: rate limit visível e stale que não decide
# ══════════════════════════════════════════════════════════════════


class TestKlinesRobusto:
    @pytest.fixture(autouse=True)
    def _cache_limpo(self):
        import data.klines as k

        k._cache.clear()
        yield
        k._cache.clear()

    def test_http_429_nao_vira_valueerror_mudo(self, monkeypatch):
        """Um 429 devolvia `{"code": -1121, ...}`, que caía no `float(x[1])` e
        virava exceção engolida. O chamador recebia None, indistinguível de "sem
        rede" — e rate limit tratado como falha de rede leva a retry, que agrava
        o rate limit."""
        import data.klines as k

        class _Resp429:
            status_code = 429

            @staticmethod
            def raise_for_status():
                raise RuntimeError("429 Too Many Requests")

            @staticmethod
            def json():  # pragma: no cover - não deve ser alcançado
                raise AssertionError("json() chamado apesar do 429")

            text = "rate limited"

        monkeypatch.setattr(k.requests, "get", lambda *a, **k_: _Resp429())
        assert k.obter_klines("BTCUSDT", "1h", 10) is None

    def test_resposta_nao_lista_e_recusada(self, monkeypatch):
        import data.klines as k

        class _Resp:
            @staticmethod
            def raise_for_status():
                pass

            @staticmethod
            def json():
                return {"code": -1121, "msg": "Invalid symbol."}

        monkeypatch.setattr(k.requests, "get", lambda *a, **k_: _Resp())
        assert k.obter_klines("BTCUSDT", "1h", 10) is None

    def test_stale_dentro_do_teto_ainda_serve(self, monkeypatch):
        """Falha transitória de rede não deve derrubar o bot."""
        import time as _t

        import data.klines as k

        k._cache[("BTCUSDT", "1h", 10)] = {
            "dados": {"fechamento": [1.0]},
            "timestamp": _t.time() - 60,
        }
        monkeypatch.setattr(k, "_fetch_rest", lambda *a: None)
        assert k.obter_klines("BTCUSDT", "1h", 10) == {"fechamento": [1.0]}

    def test_stale_alem_do_teto_devolve_none(self, monkeypatch):
        """O circuit breaker (risco.verificar_volatilidade) NÃO pode receber
        preços de horas atrás e concluir "mercado calmo" sobre um mercado em
        queda livre. Um circuit breaker alimentado por dado velho autoriza."""
        import time as _t

        import data.klines as k

        k._cache[("BTCUSDT", "1h", 10)] = {
            "dados": {"fechamento": [1.0]},
            "timestamp": _t.time() - 7200,  # 2 horas
        }
        monkeypatch.setattr(k, "_fetch_rest", lambda *a: None)
        assert k.obter_klines("BTCUSDT", "1h", 10) is None

    def test_teto_pode_ser_desligado_explicitamente(self, monkeypatch):
        import time as _t

        import data.klines as k

        k._cache[("BTCUSDT", "1h", 10)] = {
            "dados": {"fechamento": [1.0]},
            "timestamp": _t.time() - 7200,
        }
        monkeypatch.setattr(k, "_fetch_rest", lambda *a: None)
        assert k.obter_klines("BTCUSDT", "1h", 10, idade_maxima_s=None) is not None

    def test_modulo_tem_logger(self):
        """Toda falha de fetch e todo fallback aconteciam sem rastro nenhum."""
        import data.klines as k

        assert k._log.name == "botbinance"


# ══════════════════════════════════════════════════════════════════
#  8. Os labs leem o snapshot, não a tabela viva
# ══════════════════════════════════════════════════════════════════


class TestLabsLeemOSnapshot:
    def test_carregar_klines_tenta_o_snapshot_primeiro(self):
        import inspect

        from research import edge_lab

        fonte = inspect.getsource(edge_lab.carregar_klines)
        i_snap = fonte.index("carregar_serie")
        i_db = fonte.index("sqlite3.connect")
        assert i_snap < i_db, "o banco vivo voltou a ser a fonte primária"

    def test_fallback_para_o_banco_avisa(self):
        """No modo fallback o resultado não é reprodutível, e quem lê o número
        precisa saber disso."""
        import inspect

        from research import edge_lab

        fonte = inspect.getsource(edge_lab.carregar_klines)
        assert "NÃO será re-derivável" in fonte or "NAO sera re-derivavel" in fonte

    def test_snapshot_real_do_repo_esta_integro(self):
        """O snapshot versionado em data/snapshots/ tem de bater com o manifest —
        senão nenhum veredito do repositório vale."""
        try:
            problemas = snapshot.verificar()
        except FileNotFoundError:
            pytest.skip("sem snapshot versionado neste checkout")
        assert problemas == []

    def test_snapshot_real_cobre_a_fronteira_de_holdout(self):
        from research.edge_lab import HOLDOUT_INICIO_MS

        try:
            m = snapshot.ler_manifest()
        except FileNotFoundError:
            pytest.skip("sem snapshot versionado neste checkout")
        for s in m["series"]:
            assert s["primeiro_ts"] < HOLDOUT_INICIO_MS < s["ultimo_ts"], (
                f"{s['symbol']} não cobre a fronteira pré-registrada"
            )


# ══════════════════════════════════════════════════════════════════
#  9. O manifest versionado é legível e consistente
# ══════════════════════════════════════════════════════════════════


class TestManifestVersionado:
    def test_manifest_e_json_valido_com_os_campos_exigidos(self):
        try:
            base = snapshot.caminho_do_snapshot()
        except FileNotFoundError:
            pytest.skip("sem snapshot versionado neste checkout")
        with open(os.path.join(base, "manifest.json"), encoding="utf-8") as fp:
            m = json.load(fp)
        assert {"rotulo", "criado_em", "series", "aviso"} <= set(m)
        for s in m["series"]:
            assert {"symbol", "intervalo", "n", "primeiro_ts", "ultimo_ts", "sha256"} <= set(s)

    def test_contagem_do_manifest_bate_com_o_csv(self):
        try:
            base = snapshot.caminho_do_snapshot()
        except FileNotFoundError:
            pytest.skip("sem snapshot versionado neste checkout")
        m = snapshot.ler_manifest()
        for s in m["series"]:
            with open(os.path.join(base, s["arquivo"]), encoding="utf-8", newline="") as fp:
                assert sum(1 for _ in csv.DictReader(fp)) == s["n"]
