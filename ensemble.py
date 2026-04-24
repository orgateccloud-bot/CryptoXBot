"""
Ensemble — Combina XGBoost + LSTM para previsao final
========================================================
Metodo: Media ponderada com voto de concordancia + filtro de regime

Pesos base:
  XGBoost: 55% (mais robusto em dados tabulares)
  LSTM:    45% (captura padroes sequenciais)

Ajustes por regime:
  - TENDENCIA_ALTA/BAIXA: pesos base (55/45)
  - LATERAL: XGB 70% / LSTM 30% (penaliza falsos rompimentos)
  - VOLATILIDADE: reduz threshold para 0.65 (mais conservador)

Regras:
  - Ambos concordam (> 50%): opera com confianca total
  - Apenas 1 positivo:       opera com peso reduzido
  - Ambos negativos:         nao opera

Score final:
  prob_ensemble = xgb_prob * peso_xgb + lstm_prob * peso_lstm
  concordancia  = ambos > 0.50 ou ambos < 0.50
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# FSRS — filtro adaptativo (opcional, não bloqueia se ausente)
try:
    from fsrs_trading import get_fsrs as _get_fsrs
    _FSRS_DISPONIVEL = True
except ImportError:
    _FSRS_DISPONIVEL = False

# Pesos base
PESO_XGB_BASE  = 0.55
PESO_LSTM_BASE = 0.45

# Ajustes por regime
AJUSTES_REGIME = {
    "TENDENCIA_ALTA":   {"xgb": 0.55, "lstm": 0.45, "threshold": 0.55},
    "TENDENCIA_BAIXA":  {"xgb": 0.55, "lstm": 0.45, "threshold": 0.55},
    "LATERAL":          {"xgb": 0.70, "lstm": 0.30, "threshold": 0.60},  # Penaliza LSTM em lateral
    "VOLATILIDADE":     {"xgb": 0.60, "lstm": 0.40, "threshold": 0.65},  # Mais conservador
    "INDEFINIDO":       {"xgb": 0.55, "lstm": 0.45, "threshold": 0.60},
}


def prever(regime_atual="INDEFINIDO", features_fsrs: dict | None = None):
    """
    Retorna previsao combinada XGBoost + LSTM com ajuste por regime e FSRS.

    Args:
        regime_atual:   str — regime detectado (TENDENCIA_ALTA, LATERAL, etc.)
        features_fsrs:  dict — features atuais para consulta ao FSRS (opcional)
                        Ex: {"regime": "TENDENCIA_ALTA", "rsi": 58, "dist_ema20": 0.005,
                             "cvd_score": 70, "vol_rel": 1.4}

    Retorna dict com:
      prob_ensemble:  probabilidade combinada (0-1)
      prob_xgb:       probabilidade do XGBoost
      prob_lstm:      probabilidade do LSTM
      concordancia:   bool — ambos concordam
      confianca:      ALTA | MEDIA | BAIXA
      pode_operar:    bool
      motivo:         str
      regime:         regime usado no ajuste
      pesos_aplicados: dict com pesos finais
      fator_fsrs:     float — fator de confiança adaptativo (0-1)
    """
    prob_xgb  = None
    prob_lstm = None
    msg_xgb   = ""
    msg_lstm  = ""

    # Detectar regime se nao fornecido
    if regime_atual == "INDEFINIDO":
        try:
            import regime as reg
            regime_info = reg.detectar()
            regime_atual = regime_info.get("regime_final", "INDEFINIDO")
        except Exception:
            regime_atual = "INDEFINIDO"

    # Aplicar ajustes por regime
    ajuste = AJUSTES_REGIME.get(regime_atual, AJUSTES_REGIME["INDEFINIDO"])
    peso_xgb = ajuste["xgb"]
    peso_lstm = ajuste["lstm"]
    limiar = ajuste["threshold"]

    # XGBoost
    try:
        from ml_filtro import prever as xgb_prever
        prob_xgb, msg_xgb = xgb_prever()
    except Exception as e:
        msg_xgb = str(e)

    # LSTM
    try:
        from lstm_modelo import prever as lstm_prever
        prob_lstm, msg_lstm = lstm_prever()
    except Exception as e:
        msg_lstm = str(e)

    # Calcular ensemble com pesos ajustados
    if prob_xgb is not None and prob_lstm is not None:
        prob_ensemble = prob_xgb * peso_xgb + prob_lstm * peso_lstm

        xgb_positivo  = prob_xgb  >= 0.50
        lstm_positivo = prob_lstm >= 0.50
        concordancia  = xgb_positivo == lstm_positivo

        if concordancia and prob_ensemble >= limiar:
            confianca   = "ALTA"
            pode_operar = True
            motivo = f"Ensemble {prob_ensemble*100:.1f}% — XGB e LSTM concordam (ALTA) | Regime: {regime_atual}"
        elif prob_ensemble >= limiar:
            confianca   = "MEDIA"
            pode_operar = True
            motivo = f"Ensemble {prob_ensemble*100:.1f}% — modelos divergem (MEDIA) | Regime: {regime_atual}"
        else:
            confianca   = "BAIXA"
            pode_operar = False
            motivo = f"Ensemble {prob_ensemble*100:.1f}% — abaixo do limiar {limiar*100:.0f}% | Regime: {regime_atual}"

    elif prob_xgb is not None:
        prob_ensemble = prob_xgb
        concordancia  = False
        confianca     = "MEDIA"
        pode_operar   = prob_xgb >= limiar
        motivo = f"Apenas XGBoost: {prob_xgb*100:.1f}% (LSTM indisponivel: {msg_lstm}) | Regime: {regime_atual}"

    elif prob_lstm is not None:
        prob_ensemble = prob_lstm
        concordancia  = False
        confianca     = "MEDIA"
        pode_operar   = prob_lstm >= limiar
        motivo = f"Apenas LSTM: {prob_lstm*100:.1f}% (XGBoost indisponivel: {msg_xgb}) | Regime: {regime_atual}"

    else:
        prob_ensemble = 0.5
        concordancia  = False
        confianca     = "NENHUM"
        pode_operar   = False
        motivo = f"Nenhum modelo disponivel — XGB: {msg_xgb} | LSTM: {msg_lstm}"

    # ── FSRS: ajuste adaptativo por histórico de padrões ─────────
    fator_fsrs = 0.5  # neutro (sem histórico)
    fsrs_detalhe = {}
    if _FSRS_DISPONIVEL and features_fsrs:
        try:
            _fsrs = _get_fsrs()
            # Garante que o regime está nas features
            features_fsrs.setdefault("regime", regime_atual)
            fator_fsrs = _fsrs.avaliar(features_fsrs)
            fsrs_detalhe = _fsrs.avaliar_com_detalhe(features_fsrs)

            # Aplica FSRS: ajusta prob_ensemble proporcionalmente
            # fator=0.5 (neutro) → multiplica por 0.85 (leve cautela sem histórico)
            # fator=1.0 (confiante) → multiplica por 1.0 (sem alteração)
            # fator=0.1 (padrão ruim) → multiplica por 0.73 (penaliza)
            ajuste_fsrs = 0.7 + 0.3 * fator_fsrs
            prob_ensemble_ajustado = round(prob_ensemble * ajuste_fsrs, 4)

            if fator_fsrs < 0.4:
                motivo += f" | FSRS:{fator_fsrs:.2f}(EVITAR)"
                if pode_operar and prob_ensemble_ajustado < limiar:
                    pode_operar = False
                    motivo += " → bloqueado por histórico ruim"
            elif fator_fsrs >= 0.75:
                motivo += f" | FSRS:{fator_fsrs:.2f}(CONFIAVEL)"
            else:
                motivo += f" | FSRS:{fator_fsrs:.2f}"

            prob_ensemble = prob_ensemble_ajustado
        except Exception:
            pass

    return {
        "prob_ensemble": round(prob_ensemble, 4),
        "prob_xgb":      prob_xgb,
        "prob_lstm":     prob_lstm,
        "concordancia":  concordancia,
        "confianca":     confianca,
        "pode_operar":   pode_operar,
        "motivo":        motivo,
        "regime":        regime_atual,
        "pesos_aplicados": {"xgb": peso_xgb, "lstm": peso_lstm, "threshold": limiar},
        "fator_fsrs":    fator_fsrs,
        "fsrs_detalhe":  fsrs_detalhe,
    }


def imprimir(regime_atual="INDEFINIDO"):
    r = prever(regime_atual)

    verde    = "\033[92m"
    vermelho = "\033[91m"
    amarelo  = "\033[93m"
    cinza    = "\033[90m"
    reset    = "\033[0m"

    cor_conf = {
        "ALTA":   verde,
        "MEDIA":  amarelo,
        "BAIXA":  vermelho,
        "NENHUM": cinza,
    }
    cor = cor_conf.get(r["confianca"], cinza)

    # Barra de probabilidade
    p = int(r["prob_ensemble"] * 100)
    barra_len = 30
    pos = int(p / 100 * barra_len)
    barra = "[" + "#" * pos + "-" * (barra_len - pos) + "]"

    print("\n" + "="*55)
    print("  ENSEMBLE ML (XGBoost + LSTM)")
    print("="*55)

    # Regime e pesos
    print(f"  Regime: {r['regime']}")
    print(f"  Pesos:  XGB {r['pesos_aplicados']['xgb']*100:.0f}% / LSTM {r['pesos_aplicados']['lstm']*100:.0f}%")
    print(f"  Threshold: {r['pesos_aplicados']['threshold']*100:.0f}%")
    print()

    # XGBoost
    if r["prob_xgb"] is not None:
        pxgb = int(r["prob_xgb"] * 100)
        cor_x = verde if pxgb >= 55 else vermelho
        print(f"  XGBoost ({r['pesos_aplicados']['xgb']*100:.0f}%):  {cor_x}{pxgb:3d}%{reset}")
    else:
        print(f"  XGBoost ({r['pesos_aplicados']['xgb']*100:.0f}%):  {cinza}indisponivel{reset}")

    # LSTM
    if r["prob_lstm"] is not None:
        plstm = int(r["prob_lstm"] * 100)
        cor_l = verde if plstm >= 55 else vermelho
        print(f"  LSTM    ({r['pesos_aplicados']['lstm']*100:.0f}%):  {cor_l}{plstm:3d}%{reset}")
    else:
        print(f"  LSTM    ({r['pesos_aplicados']['lstm']*100:.0f}%):  {cinza}indisponivel{reset}")

    # Ensemble
    print()
    print(f"  Ensemble:  {cor}{p:3d}%  {barra}{reset}")
    print(f"  Confianca: {cor}{r['confianca']}{reset}")
    if r["prob_xgb"] is not None and r["prob_lstm"] is not None:
        conc_txt = f"{verde}SIM{reset}" if r["concordancia"] else f"{vermelho}NAO{reset}"
        print(f"  Concordam: {conc_txt}")

    # FSRS
    ffsrs = r.get("fator_fsrs", 0.5)
    cor_fsrs = verde if ffsrs >= 0.7 else "\033[93m" if ffsrs >= 0.5 else vermelho
    fsrs_status = r.get("fsrs_detalhe", {}).get("status", "DESCONHECIDO")
    print(f"  FSRS:      {cor_fsrs}{ffsrs:.2f} ({fsrs_status}){reset}")

    print(f"  Opera:     {verde+'SIM'+reset if r['pode_operar'] else vermelho+'NAO'+reset}")
    print(f"  {r['motivo']}")
    print("="*55)
    return r


if __name__ == "__main__":
    imprimir()
