"""
Testes — vigia da primeira medição do micro_lab
================================================
O vigia roda por cron durante semanas sem ninguém olhar. As propriedades que
importam são as de NÃO-ação: não medir antes da hora, não medir duas vezes
(inflaria o deflator de trials do DSR), não chegar perto do hold-out, e não
morrer se o Telegram falhar.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import medir_quando_pronto as vigia
from research import micro_lab as ml


def _preparar(tmp_path, monkeypatch, barras=0, ja_medido=False):
    vered = tmp_path / "vereditos"
    vered.mkdir()
    monkeypatch.setattr(ml, "DIR_VEREDITOS", str(vered))
    if ja_medido:
        (vered / "microestrutura_pesquisa_BTCUSDT.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(vigia, "contar_barras_pesquisa", lambda: barras)
    enviadas = []
    monkeypatch.setattr(vigia, "_notificar", lambda t: enviadas.append(t) or True)
    return vered, enviadas


RESULTADO = {
    "symbol": "BTCUSDT",
    "n_barras": 320,
    "ic_max_abs": 0.041,
    "p_valor": 0.006,
    "melhor_feature": "ofi_book",
    "melhor_horizonte": 4,
    "melhor_ic": 0.041,
    "custo_roundtrip": 0.002,
}


class TestNaoAgir:
    def test_antes_da_hora_nao_mede(self, tmp_path, monkeypatch):
        _preparar(tmp_path, monkeypatch, barras=120)
        chamado = []
        monkeypatch.setattr(ml, "rodar_pesquisa", lambda p: chamado.append(p))
        assert vigia.main([]) == 0
        assert not chamado, "mediu com a coleta incompleta"

    def test_ja_medido_e_noop_mesmo_com_barras(self, tmp_path, monkeypatch):
        """A regra anti-inflacao de trials: verdict existente = no-op eterno."""
        _preparar(tmp_path, monkeypatch, barras=9999, ja_medido=True)
        chamado = []
        monkeypatch.setattr(ml, "rodar_pesquisa", lambda p: chamado.append(p))
        assert vigia.main([]) == 0
        assert not chamado, "re-mediu e inflaria o deflator do DSR"

    def test_status_nunca_mede(self, tmp_path, monkeypatch):
        _preparar(tmp_path, monkeypatch, barras=9999)
        chamado = []
        monkeypatch.setattr(ml, "rodar_pesquisa", lambda p: chamado.append(p))
        assert vigia.main(["--status"]) == 0
        assert not chamado

    def test_nunca_toca_no_holdout(self):
        """O vigia so conhece rodar_pesquisa — nem importa avaliar_holdout."""
        import inspect

        fonte = inspect.getsource(vigia)
        assert "avaliar_holdout" not in fonte
        assert "confirmo_uso_unico" not in fonte


class TestAgir:
    def test_com_barras_mede_e_notifica(self, tmp_path, monkeypatch):
        _, enviadas = _preparar(tmp_path, monkeypatch, barras=300)
        monkeypatch.setattr(ml, "rodar_pesquisa", lambda p: dict(RESULTADO, symbol=p))
        assert vigia.main([]) == 0
        assert len(enviadas) == 1
        assert "PRIMEIRA MEDICAO" in enviadas[0]
        assert "ofi_book" in enviadas[0]
        assert "hold-out" in enviadas[0].lower() or "01/12" in enviadas[0]

    def test_recusa_do_lab_avisa_e_sai_1(self, tmp_path, monkeypatch):
        """Corrida contagem-vs-carga: o fail-closed do lab prevalece e o
        operador fica sabendo; amanha o cron tenta de novo."""
        _, enviadas = _preparar(tmp_path, monkeypatch, barras=300)

        def recusa(p):
            raise SystemExit("[MICRO] Coleta insuficiente para medir")

        monkeypatch.setattr(ml, "rodar_pesquisa", recusa)
        assert vigia.main([]) == 1
        assert len(enviadas) == 1
        assert "recusada" in enviadas[0]

    def test_telegram_morto_nao_derruba_a_medicao(self, tmp_path, monkeypatch):
        """O veredito em disco e a fonte de verdade; o aviso e cortesia."""
        vered, _ = _preparar(tmp_path, monkeypatch, barras=300)
        monkeypatch.setattr(vigia, "_notificar", lambda t: False)
        monkeypatch.setattr(ml, "rodar_pesquisa", lambda p: dict(RESULTADO, symbol=p))
        assert vigia.main([]) == 0


class TestFormato:
    def test_mensagem_diz_o_que_NAO_e(self):
        """A mensagem que chega no celular nao pode parecer veredito de
        hipotese — pesquisa aprovada ainda nao e edge."""
        texto = vigia._formatar_resultado(RESULTADO)
        assert "NAO e o veredito" in texto
        assert "01/12" in texto
