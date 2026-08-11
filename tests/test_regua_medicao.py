"""
Testes da frente I-12 — a régua de medição para de mentir.

Medido em 2026-08-09, antes desta frente. `GET /api/backtest/BTCUSDT` servia,
24/7, para quem abrisse o dashboard:

    retorno +2,54% | Sharpe 1,04 | profit factor 1,01 | DSR 0,839
    e o frontend traduzia em "ESTRATEGIA PROMISSORA"

Corrigindo DUAS linhas — o mapeamento 1h->4h e a taxa — o MESMO motor sobre os
MESMOS dados devolve:

    retorno -45,83% | Sharpe -0,30 | profit factor 0,75 | drawdown 48,93%

48,4 pontos percentuais fabricados. O número honesto é uma perda de quase metade
do capital.

Nenhum teste aqui toca a rede nem o banco de produção.
"""

import pytest

from backtesting.alinhamento import (
    MS_1H,
    MS_4H,
    mapear_idx_fechado,
    mapear_por_intervalo,
    violacoes_de_causalidade,
)

# ══════════════════════════════════════════════════════════════════
#  1. Causalidade: nenhum candle lento aberto entra na decisão
# ══════════════════════════════════════════════════════════════════


def _serie(n, inicio=0, passo=MS_1H):
    return [inicio + i * passo for i in range(n)]


class TestAlinhamentoCausal:
    def test_o_4h_escolhido_ja_fechou(self):
        """A propriedade central: no instante da decisão (fechamento do 1h),
        o candle 4h apontado precisa ter fechado ANTES ou no mesmo instante."""
        ts1h = _serie(400)
        ts4h = _serie(100, passo=MS_4H)
        idxs = mapear_idx_fechado(ts1h, ts4h)
        assert violacoes_de_causalidade(ts1h, ts4h, idxs) == []

    def test_a_formula_antiga_viola_a_maioria_das_barras(self):
        """Prova de que o teste acima não é trivial: `i // 4` devolve o 4h que
        CONTÉM o 1h, e esse ainda não fechou.

        Numa série PERFEITAMENTE alinhada (1h[0] e 4h[0] no mesmo instante) a
        violação é de 3 em cada 4 barras: na quarta, o fechamento do 1h coincide
        com o do 4h e o dado já existe. Em série real o alinhamento não é
        perfeito — medido em BTCUSDT, 17.563 de 17.563 barras liam um 4h ainda
        aberto, porque a série 1h começa às 18:00 e as fronteiras de 4h caem em
        00/04/08/12/16/20.
        """
        ts1h = _serie(400)
        ts4h = _serie(100, passo=MS_4H)
        antigos = [min(i // 4, len(ts4h) - 1) for i in range(len(ts1h))]
        violacoes = violacoes_de_causalidade(ts1h, ts4h, antigos)
        assert len(violacoes) == pytest.approx(len(ts1h) * 0.75, abs=2)

    def test_series_com_origens_diferentes_violam_sempre(self):
        """Reproduz a condição REAL, que é pior que o desalinhamento de fase.

        Medido no banco de produção (BTCUSDT):

            1h: 2024-04-01 18:00 -> 2026-04-03 12:00   n=17.563
            4h: 2024-04-03 16:00 -> 2026-04-03 12:00   n=4.380

        A série 4h começa **46 horas depois** da 1h. `i // 4` pressupõe que as
        duas grades começam no mesmo instante e que há exatamente 4 candles 1h
        por 4h, sem buracos. Nada disso vale: o índice aponta para um candle 4h
        sistematicamente à frente, e a violação vai a 100% das barras — foi o que
        se mediu, 17.563 de 17.563.

        Minha primeira versão deste teste supôs que bastava deslocar a fase em
        2h; não basta (dá 25%). A causa é a origem diferente, não a fase.
        """
        ts1h = _serie(400)  # começa em t=0
        ts4h = _serie(100, inicio=46 * MS_1H, passo=MS_4H)  # começa 46h depois
        antigos = [min(i // 4, len(ts4h) - 1) for i in range(len(ts1h))]
        assert len(violacoes_de_causalidade(ts1h, ts4h, antigos)) == len(ts1h)

    def test_com_origens_diferentes_o_mapeamento_novo_continua_causal(self):
        """A mesma condição que quebra `i // 4` não afeta o mapeamento por
        timestamp — ele não pressupõe grade nenhuma."""
        ts1h = _serie(400)
        ts4h = _serie(100, inicio=46 * MS_1H, passo=MS_4H)
        idxs = mapear_idx_fechado(ts1h, ts4h)
        assert violacoes_de_causalidade(ts1h, ts4h, idxs) == []

    def test_primeiras_barras_devolvem_menos_um(self):
        """Antes do primeiro 4h fechar não há informação MTF. Devolver 0 ali
        seria reintroduzir look-ahead pela porta dos fundos."""
        ts1h = _serie(10)
        ts4h = _serie(5, passo=MS_4H)
        idxs = mapear_idx_fechado(ts1h, ts4h)
        # O 4h[0] fecha em t=4h; o 1h[i] decide em (i+1)h. Só a partir de i=3.
        assert idxs[0] == idxs[1] == idxs[2] == -1
        assert idxs[3] == 0

    def test_fronteira_exata_e_inclusiva(self):
        """O 4h que fecha EXATAMENTE no instante da decisão é utilizável: o dado
        existe naquele momento. Excluí-lo desperdiçaria informação legítima."""
        ts1h = _serie(8)
        ts4h = [0]
        idxs = mapear_idx_fechado(ts1h, ts4h)
        assert idxs[3] == 0  # decisão em 4h; o 4h[0] fecha em 4h

    def test_indice_e_monotonico(self):
        idxs = mapear_idx_fechado(_serie(200), _serie(50, passo=MS_4H))
        assert all(b >= a for a, b in zip(idxs, idxs[1:]))

    def test_nunca_ultrapassa_o_ultimo_candle(self):
        """Série 1h que vai muito além da 4h não pode indexar fora do vetor."""
        ts4h = _serie(3, passo=MS_4H)
        idxs = mapear_idx_fechado(_serie(500), ts4h)
        assert max(idxs) == len(ts4h) - 1

    def test_serie_lenta_vazia(self):
        assert mapear_idx_fechado(_serie(10), []) == [-1] * 10

    def test_serie_rapida_vazia(self):
        assert mapear_idx_fechado([], _serie(5, passo=MS_4H)) == []

    def test_por_intervalo_resolve_os_rotulos(self):
        ts1h, ts4h = _serie(100), _serie(25, passo=MS_4H)
        assert mapear_por_intervalo(ts1h, ts4h, "1h", "4h") == mapear_idx_fechado(ts1h, ts4h)

    def test_intervalo_desconhecido_levanta(self):
        with pytest.raises(ValueError, match="duracao conhecida"):
            mapear_por_intervalo([0], [0], "1h", "7h")

    def test_violacoes_aceita_menos_um(self):
        """-1 é 'sem dado ainda', não violação — confundir os dois faria o teste
        de causalidade falhar no começo de toda série."""
        assert violacoes_de_causalidade(_serie(5), _serie(2, passo=MS_4H), [-1] * 5) == []

    def test_violacoes_pega_indice_fora_do_vetor(self):
        assert violacoes_de_causalidade(_serie(3), [0], [99, 99, 99]) != []


class TestCausalidadeNasSeriesDoSnapshot:
    """Critério de saída de I-12, contra o substrato REAL — via snapshot.

    A primeira versão deste teste abria `data/btc_data.db` direto e foi barrada
    pelo guard do conftest da raiz (I-1), corretamente: teste não toca o banco de
    produção. O snapshot de I-11 é a fonte certa — é dado real, versionado e
    verificado por sha256.

    O snapshot só congela as séries 1h (as únicas que a pesquisa usa), então a
    contraparte 4h é derivada por reamostragem do próprio 1h: os timestamps de
    fronteira de 4h que existiriam naquele período. Isso preserva o que importa
    para o teste — o ALINHAMENTO real entre as duas grades.
    """

    @pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    def test_serie_do_snapshot_sem_violacao(self, symbol):
        from research import snapshot as snap

        try:
            ts1h_np, *_ = snap.carregar_serie(symbol, "1h")
        except (FileNotFoundError, KeyError):
            pytest.skip("sem snapshot versionado neste checkout")
        ts1h = [int(t) for t in ts1h_np]
        # Fronteiras de 4h reais dentro do período coberto pelo 1h.
        ts4h = [t for t in ts1h if t % MS_4H == 0]
        if len(ts4h) < 10:
            pytest.skip(f"poucas fronteiras 4h em {symbol}")

        idxs = mapear_idx_fechado(ts1h, ts4h)
        assert violacoes_de_causalidade(ts1h, ts4h, idxs) == []

        # E a fórmula antiga violaria — a comparação é o que dá sentido ao teste.
        antigos = [min(i // 4, len(ts4h) - 1) for i in range(len(ts1h))]
        assert violacoes_de_causalidade(
            ts1h, ts4h, antigos
        ), "a formula antiga deixou de violar — o cenario mudou, revise o teste"


# ══════════════════════════════════════════════════════════════════
#  2. Os motores usam o mapeamento causal
# ══════════════════════════════════════════════════════════════════


class TestMotoresUsamOMapeamentoCausal:
    @pytest.mark.parametrize("modulo", ["backtesting.motor_ensemble", "backtesting.otimizador"])
    def test_nao_ha_mais_divisao_por_quatro(self, modulo):
        """Guarda contra reintrodução. Compara só linhas de CÓDIGO: os
        comentários citam a fórmula antiga de propósito, para quem ler saber o
        que era."""
        import importlib
        import inspect

        mod = importlib.import_module(modulo)
        codigo = "\n".join(
            linha.split("#")[0]
            for linha in inspect.getsource(mod).splitlines()
            if not linha.strip().startswith("#")
        )
        assert "idx_1h // 4" not in codigo
        assert "mapear_idx_fechado" in codigo

    def test_walk_forward_ja_era_causal(self):
        """A correção existia desde a Etapa 1 e nunca foi propagada — este teste
        registra que ela continua lá."""
        import inspect

        from backtesting import walk_forward

        assert "_mapear_idx4_fechado" in inspect.getsource(walk_forward)


# ══════════════════════════════════════════════════════════════════
#  3. Taxa de SPOT, não de futuros
# ══════════════════════════════════════════════════════════════════


class TestTaxaSpot:
    def test_motor_ensemble_usa_taxa_spot(self):
        """0,0004 é tarifa de FUTUROS. O executor deste bot manda /api/v3/order,
        que é spot: 0,001 por lado (taker)."""
        from backtesting.motor_ensemble import TAXA

        assert TAXA == 0.001

    def test_as_duas_reguas_cobram_a_mesma_taxa(self):
        """trend_following já usava 0,001 com o comentário correto. Duas réguas
        cobrando taxas diferentes pela mesma execução medem estratégias
        diferentes — que é o defeito que I-12 inteira ataca."""
        from backtesting.motor_ensemble import TAXA as taxa_ensemble
        from backtesting.trend_following import TAXA as taxa_trend

        assert taxa_ensemble == taxa_trend

    def test_otimizador_herda_a_taxa_corrigida(self):
        from backtesting.motor_ensemble import TAXA as taxa_fonte
        from backtesting.otimizador import TAXA as taxa_otim

        assert taxa_otim == taxa_fonte == 0.001


# ══════════════════════════════════════════════════════════════════
#  4. DSR deixa de ser PSR com nome trocado
# ══════════════════════════════════════════════════════════════════


class TestDSRNaoMente:
    RETORNOS = [1.2, -0.8, 2.1, -1.5, 0.9, 1.1, -0.4, 0.7, -1.2, 1.8] * 5

    def test_sem_trials_levanta_em_vez_de_devolver_psr(self):
        """Era `sharpes_trials=None` -> benchmark SR=0 -> PSR puro, gravado sob a
        chave "dsr". O dashboard servia dsr=0,839, que não era DSR."""
        from backtesting.metricas import deflated_sharpe_ratio

        with pytest.raises(TypeError, match="exige sharpes_trials"):
            deflated_sharpe_ratio(self.RETORNOS, None)

    def test_argumento_nao_tem_mais_default(self):
        import inspect

        from backtesting.metricas import deflated_sharpe_ratio

        p = inspect.signature(deflated_sharpe_ratio).parameters["sharpes_trials"]
        assert p.default is inspect.Parameter.empty

    def test_um_unico_trial_equivale_a_psr(self):
        """Uma tentativa não tem multiplicidade a corrigir — aqui DSR == PSR é
        correto, e a diferença é que o chamador DECLAROU isso."""
        from backtesting.metricas import deflated_sharpe_ratio, probabilistic_sharpe_ratio

        assert deflated_sharpe_ratio(self.RETORNOS, [1.0]) == pytest.approx(
            probabilistic_sharpe_ratio(self.RETORNOS, 0.0)
        )

    def test_mais_trials_deflaciona_mais(self):
        """A propriedade que dá sentido ao DSR: quanto mais configurações
        testadas, mais alto o benchmark e menor a probabilidade."""
        from backtesting.metricas import deflated_sharpe_ratio

        poucos = deflated_sharpe_ratio(self.RETORNOS, [0.5, 0.8, 1.0])
        muitos = deflated_sharpe_ratio(self.RETORNOS, [0.5, 0.8, 1.0] * 200)
        assert muitos < poucos

    @pytest.mark.parametrize(
        "modulo,var",
        [
            ("backtesting.motor_ensemble", "psr"),
            ("backtesting.walk_forward", "psr"),
            ("backtesting.motor", "psr"),
        ],
    )
    def test_callers_gravam_sob_a_chave_honesta(self, modulo, var):
        import importlib
        import inspect

        fonte = inspect.getsource(importlib.import_module(modulo))
        assert f'"{var}": round(probabilistic_sharpe_ratio' in fonte
        assert '"dsr": round(deflated_sharpe_ratio' not in fonte

    def test_otimizador_continua_usando_dsr_de_verdade(self):
        """Ele é o único caller que SEMPRE teve o n_trials real — o grid inteiro.
        Ali DSR é a métrica certa e deve continuar."""
        import inspect

        from backtesting import otimizador

        assert "deflated_sharpe_ratio(best[" in inspect.getsource(otimizador)


# ══════════════════════════════════════════════════════════════════
#  5. A rota que servia número falso
# ══════════════════════════════════════════════════════════════════


class TestRotaBacktestDesligada:
    def test_desligada_por_padrao(self, monkeypatch):
        import inspect

        import dashboard

        fonte = inspect.getsource(dashboard.api_backtest)
        assert "BACKTEST_HTTP" in fonte
        assert "409" in fonte

    def test_o_payload_de_recusa_traz_os_numeros(self):
        """A recusa precisa ensinar, não só negar: quem abrir a rota tem de ver
        POR QUE o número anterior era falso."""
        import inspect

        import dashboard

        fonte = inspect.getsource(dashboard.api_backtest)
        assert "2.54" in fonte and "-45.83" in fonte

    def test_frontend_nao_promete_capital_real(self):
        """O texto de 5/5 dizia "Pode operar com capital real" — frase que, lida
        como autorização, contradiz o GATE_GO_LIVE.md (Etapa 1 REPROVADA)."""
        import os

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(raiz, "templates", "backtest.html"), encoding="utf-8") as fp:
            linhas = fp.read().splitlines()
        # Só linhas de CÓDIGO: o comentário que documenta a mudança cita as duas
        # frases antigas de propósito, para quem ler saber o que foi removido.
        codigo = "\n".join(ln for ln in linhas if not ln.strip().startswith("//"))
        assert "Pode operar com capital real" not in codigo
        assert "ESTRATEGIA PROMISSORA" not in codigo


# ══════════════════════════════════════════════════════════════════
#  6. Régua única: o backtest usa score.calcular, sem cópia
# ══════════════════════════════════════════════════════════════════


class TestReguaUnica:
    """Critério de saída de I-12: o score da régua == score.calcular.

    Aqui a igualdade é por CONSTRUÇÃO — `score_unificado` chama a função de
    produção — e é justamente esse o ponto. O teste trava a construção: se
    alguém reintroduzir uma cópia da fórmula, ele quebra.
    """

    BASE = dict(
        preco=100.0,
        ema20=98.0,
        ema50=95.0,
        rsi=55,
        atr_atual=1.0,
        atr_media=1.0,
        vol_rel=1.5,
        vwap_val=99.0,
        tend_4h="ALTA",
        adx=35,
        atr_ratio=1.0,
        ml_prob=0.7,
    )

    def test_score_bate_com_a_funcao_de_producao(self):
        import score as producao
        from backtesting.regua import classificar_regime, forca_do_regime, score_unificado

        s, d, f, comp, _ = score_unificado(**self.BASE)
        esperado = producao.calcular(
            regime_info={
                "regime_final": classificar_regime(100.0, 98.0, 95.0, 35, 1.0),
                "score": forca_do_regime(35),
            },
            fear_info={"valor": 50},
            tend_4h="ALTA",
            ml_prob=0.7,
            preco=100.0,
            ema20=98.0,
            ema50=95.0,
            rsi=55,
            vwap_val=99.0,
            vol_rel=1.5,
            atr_atual=1.0,
            atr_media=1.0,
            historico_ticks=None,
            obi=None,
        )
        assert (s, d, f) == (
            esperado["score_total"],
            esperado["decisao"],
            esperado["tamanho_fator"],
        )
        assert comp == esperado["scores"]

    def test_usa_os_pesos_de_producao_com_cvd_e_obi(self):
        """Os 15 pontos que o backtest não tinha."""
        import score as producao

        _, _, _, comp, _ = score_unificado_base()
        assert set(comp) == set(producao.PESOS)
        assert producao.PESOS["cvd"] == 7 and producao.PESOS["obi"] == 8

    def test_cvd_e_obi_entram_neutros(self):
        """Não há histórico de @aggTrade/@depth. Fingir valor seria inventar
        sinal; peso diferente seria voltar ao problema original."""
        _, _, _, comp, _ = score_unificado_base()
        assert comp["cvd"] == 50
        assert comp["obi"] == 50

    def test_avisa_o_que_nao_consegue_medir(self):
        """Um relatório não pode omitir a limitação por descuido."""
        *_, avisos = score_unificado_base()
        texto = " ".join(avisos)
        assert "cvd" in texto and "obi" in texto
        assert "timeframe" in texto

    def test_bloqueio_lateral_que_o_backtest_antigo_ignorava(self):
        """`_score_backtest` só vetava por ADX baixo e ATR extremo. Produção veta
        em LATERAL — e com ADX 10 o regime É LATERAL."""
        from backtesting.regua import score_unificado

        args = dict(self.BASE, adx=10)
        s, d, f, _, _ = score_unificado(**args)
        assert d == "AGUARDAR" and f == 0.0

    @pytest.mark.parametrize("fg", [5, 20, 85, 95])
    def test_bloqueio_por_fear_greed_extremo(self, fg):
        """F&G era componente de PESO no backtest; em produção é também um VETO.
        walk_forward passava o score convertido, então o veto nunca disparava."""
        from backtesting.regua import score_unificado

        _, d, f, _, _ = score_unificado(**dict(self.BASE, fear_greed_valor=fg))
        assert d == "AGUARDAR" and f == 0.0

    @pytest.mark.parametrize("fg", [30, 50, 70])
    def test_fear_greed_normal_nao_bloqueia(self, fg):
        from backtesting.regua import score_unificado

        _, d, _, _, _ = score_unificado(**dict(self.BASE, fear_greed_valor=fg))
        assert d != "AGUARDAR"

    def test_classificacao_reusa_os_limiares_de_regime(self):
        """Reusa as constantes de regime.py em vez de repetir os números: se
        alguém ajustar ADX_TENDENCIA lá, o backtest acompanha. Foi a divergência
        entre duas cópias que I-12 existe para eliminar."""
        import regime
        from backtesting.regua import classificar_regime

        assert classificar_regime(100, 98, 95, regime.ADX_TENDENCIA, 1.0) == "TENDENCIA_ALTA"
        assert classificar_regime(100, 98, 95, regime.ADX_TENDENCIA - 1, 1.0) == "LATERAL"
        assert classificar_regime(95, 98, 100, regime.ADX_TENDENCIA, 1.0) == "TENDENCIA_BAIXA"
        assert classificar_regime(100, 98, 95, 50, regime.ATR_EXTREMO + 0.1) == "VOLATILIDADE"

    def test_score_backtest_foi_eliminada(self):
        from backtesting import motor_ensemble

        assert not hasattr(motor_ensemble, "_score_backtest")

    @pytest.mark.parametrize(
        "modulo",
        ["backtesting.motor_ensemble", "backtesting.otimizador", "backtesting.walk_forward"],
    )
    def test_os_tres_motores_usam_a_regua_unica(self, modulo):
        import importlib
        import inspect

        fonte = inspect.getsource(importlib.import_module(modulo))
        assert "score_unificado(" in fonte

    def test_walk_forward_passa_o_valor_bruto_de_fg(self):
        """Passar o SCORE em vez do valor cru era o que impedia o veto de
        medo/ganância extremos de disparar."""
        import inspect

        from backtesting import walk_forward

        fonte = inspect.getsource(walk_forward)
        assert "fear_greed_valor=fg_valor" in fonte
        assert "fear_greed_score=fg_score" not in fonte

    def test_sem_historico_de_fg_o_default_e_neutro_nao_maximo(self):
        """O default antigo era 100 — o SCORE máximo. O backtest ganhava o
        componente inteiro de graça em todo dia sem dado."""
        import inspect

        from backtesting import walk_forward

        assert "fng_valor if fng_valor is not None else 50" in inspect.getsource(walk_forward)


def score_unificado_base():
    from backtesting.regua import score_unificado

    return score_unificado(**TestReguaUnica.BASE)
