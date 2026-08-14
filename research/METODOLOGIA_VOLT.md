# Metodologia VOLT — vol-targeting spot (gestão de exposição) — pré-registro

> **Natureza deste documento:** contrato de método, escrito ANTES de rodar
> `research/volt_lab.py` e de ver qualquer resultado. Alterar as regras
> depois de ver os números anula o valor da pesquisa. Mudanças exigem
> justificativa datada no registro ao final.
>
> Criado em: 2026-08-14 · Direção: usuário ("pré-registra e roda as novas
> frentes em paralelo").

## Hipótese (congelada)

Em cripto, períodos de volatilidade alta têm retorno médio pior por unidade
de risco (leverage effect + cascatas de liquidação). Logo, **escalar a
exposição inversamente à volatilidade realizada** ("vol-managed portfolio",
Moreira & Muir 2017) melhora o Sharpe e reduz o drawdown de uma posição
comprada, mesmo **sem alavancagem** (teto de exposição 1,0 — spot puro:
o mecanismo disponível é só DES-alavancar nos períodos ruins).

Nota de escopo: VOLT não é uma estratégia de entrada — é um OVERLAY de
dimensionamento. Se aprovada, o destino é o sizing do executor (hoje Kelly
fracionado), não uma estratégia nova de sinal.

## Substrato (imutável)

O mesmo dos demais labs: `data/snapshots/2026-08-08`, closes 1h →
série diária (close 00:00 UTC), por ativo (BTC, ETH, SOL avaliados
separadamente).

## Regras de execução simulada (congeladas)

- **Vol realizada** no dia t: desvio-padrão dos últimos w retornos diários
  (até t, inclusive), anualizado ×√365.
- **Exposição** no dia t (vale para o retorno t → t+1):
  `exp_t = min(σ_alvo / σ_realizada_t, 1.0)` — teto 1,0, sem alavancagem.
- **Custos:** turnover diário `|exp_t − exp_{t−1}|` × 0,10% (taker spot).
- Benchmark: buy-and-hold do MESMO ativo, mesma janela (exposição 1,0).

## Família de hipóteses (congelada) — 6 trials

w ∈ {10, 20, 30} dias × σ_alvo ∈ {40%, 60% a.a.} = **6 combinações**
(σ_alvo são constantes a priori, sem olhar os dados — nada de mediana da
própria janela, que espiaria o futuro). Avaliadas nos 3 ativos, mas a
UNIDADE de decisão é a combinação (w, σ_alvo); os ativos são replicações.
Cada execução de `rodar_pesquisa` soma 6 ao contador de trials
(`research/vereditos/volt_trials_count.json`).

## Split cronológico (mesma data dos demais labs)

- **Pesquisa:** até 2025-07-21 23:00 UTC (`HOLDOUT_INICIO_MS = 1753142400000`).
- **Hold-out:** 2025-07-22 → 2026-04-03. **Uso único, travado em código**
  (`volt_lab.avaliar_holdout` exige `confirmo_uso_unico`; registro
  permanente abaixo; segunda chamada recusada).

## Régua e decisão (pré-registradas)

Por combinação e por ativo, na PESQUISA, líquidas de custos:
ΔSharpe = Sharpe(vol-targeted) − Sharpe(buy-and-hold) e
ΔDD = 1 − MaxDD(vt)/MaxDD(bh) (redução relativa do drawdown).

**SOBREVIVE (candidata a hold-out):** existe combinação (w, σ_alvo) com,
em **≥ 2 dos 3 ativos**, simultaneamente:
1. ΔSharpe ≥ **+0,20**; **E**
2. ΔDD ≥ **25%** (o drawdown cai pelo menos um quarto); **E**
3. retorno anualizado líquido do vol-targeted > 0 (melhorar Sharpe
   empobrecendo até o negativo não serve ao gate).

**Escolha para o hold-out:** a combinação de maior ΔSharpe médio nos 3
ativos entre as sobreviventes — uma única. No hold-out ela precisa repetir
as três condições em ≥ 2 dos 3 ativos. Passou → integra o caminho do gate
como overlay de sizing (Etapa 2 mede em paper). Falhou → FAIL final.

**FAIL da pesquisa:** nenhuma combinação sobrevive.

## Limitações declaradas

- Overlay não cria edge de entrada; ele multiplica o que existir. Aprovar
  VOLT não reabre a Etapa 1 sozinho — reduz o risco de cauda de quem passar.
- Teto 1,0 significa que em vol baixa o overlay fica idêntico ao
  buy-and-hold; o teste mede essencialmente o corte nas caudas.

## Registro de mudanças

- 2026-08-14 (ANTES da primeira medição; achados da revisão adversarial de
  2 céticos independentes): (a) corrigido off-by-one na fatia do hold-out
  (`ini − w − 1` → `ini − w`): o 1º retorno avaliado agora é 22→23/07,
  dentro da janela pré-registrada e igual ao MOMO; (b) `max_drawdown`
  (importada do momo_lab) corrigida para incluir o pico inicial 1,0 — DD é
  RÉGUA neste lab, então a correção precede qualquer número; (c)
  `avaliar_holdout` recusa rodar se o snapshot atual divergir do da
  pesquisa; (d) mesma semântica de timestamp do MOMO (close da barra com
  open 00:00 ≈ 01:00 UTC).

## Registro de usos do hold-out

(nenhum)
