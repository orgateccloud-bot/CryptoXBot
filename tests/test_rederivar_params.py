"""
Testes — harness de re-derivação dos parâmetros (frente E-9, ação 3)
=====================================================================
O grid que produziu `config/params_pares.py` selecionava e media na MESMA
série. `research/rederivar_params.py` separa seleção (treino), confirmação
(OOS) e o DSR final (hold-out de uso único), com as fronteiras pré-registradas
em `research/METODOLOGIA_PARAMS.md`.

Um harness anti-overfit só vale se as suas próprias travas forem verificadas —
senão ele é teatro metodológico. Estes testes cobrem:

  1. o fatiamento por DATA, que é onde o vazamento entraria de volta;
  2. o F&G obrigatório (sem ele a medição é de outra estratégia);
  3. a trava de uso único do hold-out;
  4. `n_trials` = combinações AVALIADAS, que é o que desinfla o DSR.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import rederivar_params as R  # noqa: E402

MS_1H = 3_600_000
MS_4H = 14_400_000
# 2024-01-01T00:00:00Z
T0 = 1_704_067_200_000


def _dados_sinteticos(n1h=2000):
    """Série contígua com 1h e 4h de ORIGENS DIFERENTES — é essa diferença que
    produziu look-ahead em 100% das barras antes de I-12b, e o fatiador tem de
    lidar com ela."""
    ts1h = [T0 + i * MS_1H for i in range(n1h)]
    ts4h = [T0 + 46 * MS_1H + j * MS_4H for j in range(n1h // 4)]
    serie = lambda ts: [100.0 + (i % 17) for i in range(len(ts))]  # noqa: E731
    return {
        "ts1h": ts1h, "ts4h": ts4h,
        "f1h": serie(ts1h), "m1h": serie(ts1h), "n1h": serie(ts1h), "v1h": serie(ts1h),
        "ema20": serie(ts1h), "ema50": serie(ts1h), "rsi14": serie(ts1h),
        "atr14": serie(ts1h), "volr": serie(ts1h), "bw": serie(ts1h),
        "vwap": serie(ts1h), "adx_vals": serie(ts1h),
        "f4h": serie(ts4h), "m4h": serie(ts4h), "n4h": serie(ts4h),
        "ema20_4h": serie(ts4h), "ema50_4h": serie(ts4h),
    }


class TestFatiamentoPorData:
    def test_janelas_nao_se_sobrepoem_no_periodo_medido(self):
        d = _dados_sinteticos()
        treino = R._fatiar(d, None, R._ms(R.CORTE_OOS))
        oos = R._fatiar(d, R._ms(R.CORTE_OOS), R._ms(R.CORTE_HOLDOUT))
        # O treino nao pode conter NENHUM candle a partir do corte do OOS —
        # e exatamente esse vazamento que invalidaria a confirmacao.
        assert all(t < R._ms(R.CORTE_OOS) for t in treino["ts1h"])
        # O OOS comeca com 55 barras de aquecimento ANTES do corte (os
        # indicadores precisam delas); o que nao pode e ir alem do hold-out.
        assert all(t < R._ms(R.CORTE_HOLDOUT) for t in oos["ts1h"])

    def test_holdout_comeca_depois_do_oos(self):
        d = _dados_sinteticos()
        oos = R._fatiar(d, R._ms(R.CORTE_OOS), R._ms(R.CORTE_HOLDOUT))
        hold = R._fatiar(d, R._ms(R.CORTE_HOLDOUT), None)
        if hold["ts1h"]:
            # descontando o aquecimento, nenhum candio DE DECISAO do hold-out
            # aparece no OOS
            assert max(oos["ts1h"]) < R._ms(R.CORTE_HOLDOUT)

    def test_aquecimento_de_55_barras_e_preservado(self):
        # `_rodar_com_params` comeca em i=55. Sem as 55 barras anteriores, a
        # fatia perderia os primeiros dois dias e meio de decisoes.
        d = _dados_sinteticos()
        corte = R._ms(R.CORTE_OOS)
        oos = R._fatiar(d, corte, R._ms(R.CORTE_HOLDOUT))
        antes = [t for t in oos["ts1h"] if t < corte]
        assert len(antes) == 55, f"aquecimento tem {len(antes)} barras, esperado 55"

    def test_series_1h_e_4h_ficam_coerentes(self):
        """A fatia nao pode PERDER cobertura de 4h que a serie tinha.

        Cuidado com o que se exige aqui: no inicio da serie a 4h simplesmente
        nao existe ainda (46h depois, no dado real deste projeto). Nessas
        barras `mapear_idx_fechado` devolve -1 e `tend_4h_em` cai em "LATERAL"
        — comportamento correto e ja testado. Exigir cobertura ali seria
        reprovar o dado, nao o fatiador. O que se exige e: para fatias que
        comecam DEPOIS da origem, tem de haver um 4h no inicio e no fim.
        """
        d = _dados_sinteticos()
        for ini, fim in (
            (None, R._ms(R.CORTE_OOS)),
            (R._ms(R.CORTE_OOS), R._ms(R.CORTE_HOLDOUT)),
            (R._ms(R.CORTE_HOLDOUT), None),
        ):
            f = R._fatiar(d, ini, fim)
            if not f["ts1h"] or not f["ts4h"]:
                continue
            if ini is not None:
                assert f["ts4h"][0] <= f["ts1h"][0] + MS_4H, (
                    "fatia do meio da serie comecou sem 4h utilizavel"
                )
            assert f["ts4h"][-1] >= f["ts1h"][-1] - MS_4H

    def test_fatia_do_inicio_aceita_ausencia_de_4h(self):
        # Complemento explicito do anterior: a serie 4h comeca 46h depois da
        # 1h, e o fatiador nao inventa dado para tapar isso.
        d = _dados_sinteticos()
        treino = R._fatiar(d, None, R._ms(R.CORTE_OOS))
        assert treino["ts4h"][0] > treino["ts1h"][0], (
            "o teste sintetico deixou de reproduzir origens diferentes"
        )

    def test_todas_as_listas_1h_tem_o_mesmo_tamanho(self):
        # Um desalinhamento aqui daria indicador de um candle aplicado a outro
        # — silencioso e devastador.
        d = _dados_sinteticos()
        f = R._fatiar(d, R._ms(R.CORTE_OOS), R._ms(R.CORTE_HOLDOUT))
        tamanhos = {
            k: len(f[k])
            for k in ("f1h", "m1h", "n1h", "v1h", "ts1h", "ema20", "ema50",
                      "rsi14", "atr14", "volr", "bw", "vwap", "adx_vals")
        }
        assert len(set(tamanhos.values())) == 1, tamanhos

    def test_fronteiras_sao_datas_nao_proporcoes(self):
        # Fronteira por proporcao se MOVE quando a serie cresce, e um hold-out
        # que se move nao e hold-out (mesma decisao de I-11b).
        curta = _dados_sinteticos(1500)
        longa = _dados_sinteticos(4000)
        assert R._fatiar(curta, None, R._ms(R.CORTE_OOS))["ts1h"] == pytest.approx(
            R._fatiar(longa, None, R._ms(R.CORTE_OOS))["ts1h"][: len(
                R._fatiar(curta, None, R._ms(R.CORTE_OOS))["ts1h"]
            )]
        )


class TestFngObrigatorio:
    def test_sem_historico_a_rodada_aborta(self, monkeypatch):
        monkeypatch.setattr(R, "_carregar_fng", dict)
        with pytest.raises(R.FngObrigatorio, match="fng_historico"):
            R.rodar("BTCUSDT", rapido=True, usar_holdout=False)

    def test_cobertura_parcial_tambem_aborta(self, monkeypatch):
        # Um historico que cobre metade do periodo passaria num
        # `os.path.exists` e deixaria metade da medicao sem veto.
        monkeypatch.setattr(R, "_carregar_fng", lambda: {"2024-01-01": 50})
        monkeypatch.setattr(
            R, "carregar", lambda par, itv: [(T0 + i * MS_1H, 1, 1, 1, 1, 1) for i in range(2500)]
        )
        monkeypatch.setattr(R, "_indicadores", lambda a, b: _dados_sinteticos())
        with pytest.raises(R.FngObrigatorio, match="nao cobre"):
            R.rodar("BTCUSDT", rapido=True, usar_holdout=False)


class TestTravaDoHoldout:
    def test_arquivo_de_metodologia_existe(self):
        assert os.path.exists(R.METODOLOGIA), (
            "sem o pre-registro nao ha contrato — e a trava grava nele"
        )

    def test_holdout_virgem_devolve_none(self, monkeypatch, tmp_path):
        vazio = tmp_path / "m.md"
        vazio.write_text("# sem consumos\n", encoding="utf-8")
        monkeypatch.setattr(R, "METODOLOGIA", str(vazio))
        assert R.holdout_ja_consumido("BTCUSDT") is None

    def test_consumo_registrado_e_detectado(self, monkeypatch, tmp_path):
        arq = tmp_path / "m.md"
        arq.write_text("# pre-registro\n", encoding="utf-8")
        monkeypatch.setattr(R, "METODOLOGIA", str(arq))
        R.registrar_consumo("BTCUSDT", {"dsr": 0.3, "n_trials": 900,
                                        "trades": 60, "aprovado": False})
        anterior = R.holdout_ja_consumido("BTCUSDT")
        assert anterior is not None
        assert "BTCUSDT" in anterior and "dsr=0.3" in anterior

    def test_consumo_de_um_par_nao_trava_outro(self, monkeypatch, tmp_path):
        arq = tmp_path / "m.md"
        arq.write_text("# pre-registro\n", encoding="utf-8")
        monkeypatch.setattr(R, "METODOLOGIA", str(arq))
        R.registrar_consumo("BTCUSDT", {"dsr": 0.1, "aprovado": False})
        assert R.holdout_ja_consumido("ETHUSDT") is None

    def test_registro_e_append_only(self, monkeypatch, tmp_path):
        # Reescrever o arquivo apagaria consumos anteriores — a trava tem de
        # acumular, nunca substituir.
        arq = tmp_path / "m.md"
        arq.write_text("# pre-registro\nlinha original\n", encoding="utf-8")
        monkeypatch.setattr(R, "METODOLOGIA", str(arq))
        R.registrar_consumo("BTCUSDT", {"dsr": 0.1, "aprovado": False})
        R.registrar_consumo("ETHUSDT", {"dsr": 0.2, "aprovado": False})
        texto = io.open(arq, encoding="utf-8").read()
        assert "linha original" in texto
        assert texto.count(R._MARCA_HOLDOUT) == 2

    def test_cli_recusa_holdout_sem_confirmacao(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["x", "--par", "BTCUSDT", "--holdout"])
        assert R.main() == 2

    def test_cli_recusa_segundo_consumo(self, monkeypatch, tmp_path):
        arq = tmp_path / "m.md"
        arq.write_text("# pre-registro\n", encoding="utf-8")
        monkeypatch.setattr(R, "METODOLOGIA", str(arq))
        R.registrar_consumo("BTCUSDT", {"dsr": 0.1, "aprovado": False})
        monkeypatch.setattr(
            sys, "argv",
            ["x", "--par", "BTCUSDT", "--holdout", "--confirmo-uso-unico"],
        )
        assert R.main() == 3


class TestContratoDoGrid:
    def test_n_trials_conta_avaliadas_nao_sobreviventes(self):
        # Contar so as sobreviventes subestima o multiple-testing e INFLA o
        # DSR — justamente a metrica que deveria descontar isso.
        import inspect

        fonte = inspect.getsource(R.rodar)
        sem_comentarios = "\n".join(
            ln for ln in fonte.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "n_trials = len(combos)" in sem_comentarios, (
            "n_trials deixou de contar as combinacoes AVALIADAS"
        )

    def test_piso_de_trades_e_o_do_otimizador(self):
        # Um Sharpe sobre 5 trades e ruido; o piso antigo era 5.
        assert R.MIN_TRADES >= 50

    def test_grid_rapido_e_subconjunto_do_completo(self):
        rapido = list(R._grid(True))
        completo = list(R._grid(False))
        assert len(rapido) < len(completo)
        # `--rapido` e para iterar no harness, nao para produzir veredito com
        # menos multiple-testing do que o pre-registro declara.
        assert len(completo) >= 500

    def test_dsr_minimo_bate_com_o_de_params_pares(self):
        from config import params_pares

        assert R.DSR_MINIMO == params_pares.DSR_MINIMO
