---
tags: [modulo, ml, sinais]
---

# 🧠 ML e Sinais

> Voltar: [[00 - Home]] · Relacionado: [[Core e Execucao]]

Pipeline de Machine Learning e geração de sinal. **ML real = XGBoost + sklearn MLP**
(não LightGBM/LSTM, apesar de nomes históricos).

> ✅ Cobertura adicionada nesta sessão: `ml_filtro` 65%, `regime` **99%**, `score` **96%**,
> `cvd_calculator`/`fsrs`/`ensemble` via `test_melhorias`. Riscos de modelo (overfitting
> MLP, scaler drift, ADX manual) permanecem — ver [[Planejamento de Melhorias]].

---

## `ensemble.py` — Combinador 🟢 Alta
- **Propósito:** XGBoost (55%) + MLP (45%), pesos ajustados por regime, fator FSRS.
- **API:** `prever(regime_atual, features_fsrs)` → `{prob_ensemble, concordancia, pode_operar, motivo}`.
- **Força:** degrada graciosamente (se um modelo falha, usa o outro; ambos falham → `pode_operar=False`).
- **Risco:** importa `regime` lazy → possível bottleneck se a API cai.

## `ml_filtro.py` — XGBoost (modelo principal) 🟡 Média
- **API:** `prever(symbol)`, `treinar(intervalo, symbol)`, `extrair_features(...)` (11 features).
- **Lib real:** `xgboost.XGBClassifier`. **Persistência:** `data/modelo_xgb_{symbol}.pkl` (+ fallback `modelo_xgb.pkl`), salvo **atomicamente** (`tmp` + `os.replace` — crash no retreino não corrompe o modelo).
- **Riscos:** split sem shuffle (correto p/ série, mas favorece padrões recentes); K-fold padrão ainda vaza futuro em série temporal (ver P0-1 em `PLANO_MODERNIZACAO.md`).

## `lstm_modelo.py` — MLP sequencial 🟡 Média
- **Lib real:** `sklearn.neural_network.MLPClassifier` + `StandardScaler` (**não é LSTM**).
- **Persistência:** `data/modelo_lstm.pkl` (modelo + scaler).
- **Riscos:** ~264-517 features para MLP pequeno → **risco de overfitting**; **scaler drift** sem retreino contínuo; mesmo timeout sem retry.

## `score.py` — Score unificado 0-100 🟢 Alta
- **API:** `calcular(...)` → `{score_total, decisao, detalhes}`; `_score_regime()` ✅ **existe** (corrigido nesta sessão).
- **Pesos:** ml 20%, regime 18%, cvd 15%, mtf 12%, ema/rsi/fear_greed 8%, vwap 5%, volume 4%, atr 2%.
- **Bloqueios absolutos:** VOLATILIDADE/LATERAL ou F&G <20 / >80 → força AGUARDAR.
- **Risco:** componente CVD novo usa `np.polyfit` em 50 ticks (ruidoso); thresholds fixos sem adaptação por regime.

## `regime.py` — Detector de regime 🟡 Média
- **API:** `detectar()` → `{regime_final, pode_operar, score, votos, detalhes_tf}` (1H/4H/1D).
- ✅ Corrigido nesta sessão: voto inclui 1D (antes calculado e descartado), 4H com peso duplo.
- **Risco:** ADX implementado à mão (não validado contra ta-lib); 3 chamadas de API sequenciais (até ~24s).

## `fear_greed.py` — Sentimento 🟢 Alta
- **API:** `obter()` → `{valor, classificacao_pt, pode_operar, reducao_alvo}`; cache 15 min; fallback neutro (50) se a API cair.

## `fsrs_trading.py` — Filtro adaptativo 🟡 Média
- **Propósito:** FSRS v4 aplicado a padrões de sinal; estabilidade sobe/desce com lucro/perda.
- **Persistência:** `data/fsrs_padroes.json`.
- **Riscos:** JSON **sem lock** (corrupção em escrita concorrente); array de pesos `W` importado mas não usado (dead code).

## `ai/ollama_client.py` — IA qualitativa (local) 🟢 Alta
- **Propósito:** análises em linguagem natural via Ollama (gemma3:4b / llama3).
- **Força:** fallbacks excelentes, timeout em 2 níveis (120s warmup / 25s), degradação graciosa. Opcional (independente do cluster aposentado).

## `data/cvd_calculator.py` — CVD vetorizado 🟢 Alta
- **API:** `calculate_cvd(ticks, window_size)` → `CVDResult` (Pydantic); `calculate_cvd_from_prices(...)`.
- **Força:** vetorização numpy, edge cases tratados. Usado por `score.py`.

---

### Resumo de maturidade
| Módulo | Nota | Risco-chave |
|---|---|---|
| ensemble.py | 🟢 Alta | timeout regime |
| score.py | 🟢 Alta | tuning do CVD novo |
| fear_greed.py | 🟢 Alta | cache em RAM |
| ollama_client.py | 🟢 Alta | latência 1ª chamada |
| cvd_calculator.py | 🟢 Alta | — |
| ml_filtro.py | 🟡 Média | timeout sem retry |
| lstm_modelo.py | 🟡 Média | overfitting + scaler drift |
| regime.py | 🟡 Média | ADX manual |
| fsrs_trading.py | 🟡 Média | JSON sem lock |
