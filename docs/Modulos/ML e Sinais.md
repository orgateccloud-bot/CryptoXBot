---
tags: [modulo, ml, sinais]
atualizado: 2026-07-22
---

# 🧠 ML e Sinais

> Voltar: [[00 - Home]] · Relacionado: [[Core e Execucao]]

Pipeline de Machine Learning e geração de sinal. **ML real = XGBoost + sklearn MLP**
(não LightGBM/LSTM, apesar de nomes históricos).

> ✅ **P0-1 (2026-07-09):** treino de `ml_filtro`/`lstm_modelo` usa **Purged &
> Embargoed CV** (`validacao.py`) — o K-fold padrão que vazava futuro em
> série temporal (risco citado numa versão anterior deste vault) **foi
> corrigido**, não é mais um risco aberto. AUC honesto (mean±std da purged
> CV) salvo no pickle do modelo.
> ✅ **P1-4:** guard-rail de drift no retreino automático (`validacao.
> detectar_drift`, tabela `model_metricas`) — compara o AUC do modelo novo
> contra a média histórica antes de promover.
> ✅ **FSRS aposentado (2026-07-21):** `fsrs_trading.py` foi **removido** —
> nunca ativava no caminho ao vivo (`hasattr(ens_mod, "symbol")` era sempre
> `False`, então `features_fsrs` era sempre `None`); branch morto desde
> sempre, não uma regressão. `ensemble.py` foi simplificado (sem
> `fator_fsrs`/`fsrs_detalhe`).

---

## `ensemble.py` — Combinador 🟢 Alta
- **Propósito:** XGBoost (55%) + MLP (45%), pesos ajustados por regime.
- **API:** `prever(regime_atual)` → `{prob_ensemble, concordancia, pode_operar, motivo}`.
- **Força:** degrada graciosamente (se um modelo falha, usa o outro; ambos falham → `pode_operar=False`).
- **Risco:** importa `regime` lazy → possível bottleneck se a API cai.

## `ml_filtro.py` — XGBoost (modelo principal) 🟢 Alta
- **API:** `prever(symbol)`, `treinar(intervalo, symbol)`, `extrair_features(...)` (11 features).
- **Lib real:** `xgboost.XGBClassifier`. **Persistência:** `data/modelo_xgb_{symbol}.pkl` (+ fallback `modelo_xgb.pkl`), salvo **atomicamente** (`tmp` + `os.replace` — crash no retreino não corrompe o modelo).
- ✅ **Purged & Embargoed CV** (P0-1) — treino/holdout purgados (gap = janela de horizonte), sem vazamento de futuro.
- ✅ **Guard-rail de drift** (P1-4) — `verificar_drift_e_registrar()` compara o AUC do modelo recém-treinado contra a média histórica (`model_metricas`) antes de promover; grava `bot_event` se detectar degradação.
- **Riscos remanescentes:** split sem shuffle (correto p/ série, mas favorece padrões recentes).

## `lstm_modelo.py` — MLP sequencial 🟡 Média
- **Lib real:** `sklearn.neural_network.MLPClassifier` + `StandardScaler` (**não é LSTM**).
- **Persistência:** `data/modelo_lstm.pkl` (modelo + scaler).
- ✅ Mesmo guard-rail de drift do `ml_filtro.py` (P1-4).
- **Riscos:** ~264-517 features para MLP pequeno → **risco de overfitting**; **scaler drift** sem retreino contínuo; mesmo timeout sem retry.

## `score.py` — Score unificado 0-100 🟢 Alta
- **API:** `calcular(...)` → `{score_total, decisao, detalhes}`.
- **Pesos (após P1-1, OBI):** ml 20%, regime 18%, **obi 8%** (novo),
  **cvd 7%** (caiu de 15% para abrir espaço ao OBI), mtf 12%, ema/rsi/
  fear_greed 8%, vwap 5%, volume 4%, atr 2%.
- **Bloqueios absolutos:** VOLATILIDADE/LATERAL ou F&G <20 / >80 → força AGUARDAR.
- **Risco:** componente CVD usa `np.polyfit` em 50 ticks (ruidoso); thresholds fixos sem adaptação por regime.

## `regime.py` — Detector de regime 🟡 Média
- **API:** `detectar()` → `{regime_final, pode_operar, score, votos, detalhes_tf}` (1H/4H/1D).
- ✅ Migrado para `data/klines.py` (P1-5) — cache TTL compartilhado, elimina fetch duplicado.
- **Risco:** ADX implementado à mão (não validado contra ta-lib); 3 chamadas de API sequenciais (até ~24s, mitigado pelo cache TTL entre chamadas próximas).

## `fear_greed.py` — Sentimento 🟢 Alta
- **API:** `obter()` → `{valor, classificacao_pt, pode_operar, reducao_alvo}`; cache 15 min; fallback neutro (50) se a API cair.

## `data/cvd_calculator.py` + OBI — Fluxo de ordens 🟢 Alta
- **Propósito:** CVD (Cumulative Volume Delta) vetorizado via `@aggTrade` +
  **OBI** (Order Book Imbalance, P1-1) via WebSocket `@depth`, suavizado
  contra spoofing.
- **API:** `calculate_cvd(ticks, window_size)` → `CVDResult` (Pydantic);
  `calculate_cvd_from_prices(...)`; `main.obter_obi_suavizado()`.
- **Força:** vetorização numpy, edge cases tratados. Usados por `score.py`
  (componentes `cvd` e `obi`).
- **Nota histórica:** o bug de parsing do `@aggTrade` (`data["a"]`, não
  `data["t"]`) que zerava o CVD foi corrigido antes desta rodada — mantido
  aqui como lembrete de por que o teste de regressão existe.

## `ai/ollama_client.py` — IA qualitativa (local) 🟢 Alta
- **Propósito:** análises em linguagem natural via Ollama (gemma3:4b / llama3).
- **Força:** fallbacks excelentes, timeout em 2 níveis (120s warmup / 25s), degradação graciosa. Opcional (independente do cluster aposentado).

## Meta-labeling — instrumentação pronta, treino pendente (P1-3 / P2-4)
- **Propósito:** rotular cada trade fechado com a barreira tocada
  (STOP/TARGET/TARGET_PARCIAL/MANUAL) + PnL, para eventualmente treinar um
  meta-modelo (triple-barrier) que filtra sinais de baixa qualidade.
- **Onde vive:** colunas `preco_saida`/`pnl_usdt`/`pnl_pct`/`barreira_tocada`
  na tabela `sinais` (`database.py`); `executor.fechar_posicao()` classifica
  a barreira pelo `motivo` exato (não pelo sinal do PnL) e chama
  `database.atualizar_sinal_fechamento()`; `sinal_id` percorre
  `otimizada.py → main.py → executor.abrir_long`.
- **Status:** instrumentação completa e testada; o treino do meta-modelo em
  si está **deferido** — auditoria 2026-07-22 encontrou o SQLite local
  órfão (schema pré-P1-3) e a base real (Supabase) não verificável a partir
  deste ambiente. Ver `PLANO_MODERNIZACAO.md` (seção "P2-4 — verificação
  pendente") para o threshold decidido (~200-500 trades) e a query pronta
  para rodar no Supabase.

---

### Resumo de maturidade
| Módulo | Nota | Risco-chave |
|---|---|---|
| ensemble.py | 🟢 Alta | timeout regime |
| score.py | 🟢 Alta | tuning do CVD/OBI |
| fear_greed.py | 🟢 Alta | cache em RAM |
| ollama_client.py | 🟢 Alta | latência 1ª chamada |
| data/cvd_calculator.py + OBI | 🟢 Alta | — |
| ml_filtro.py | 🟢 Alta | split sem shuffle |
| lstm_modelo.py | 🟡 Média | overfitting + scaler drift |
| regime.py | 🟡 Média | ADX manual |
