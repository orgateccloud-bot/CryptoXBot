# Metodologia CARRY v2 — funding carry em janela NOVA — pré-registro

> Criado em: 2026-08-14 · Direção: usuário ("pré-registra e roda as novas
> frentes em paralelo"). Contrato curto: esta frente NÃO roda hoje — ela
> pré-registra uma medição FUTURA sobre dados que ainda não existem, porque
> é a única forma limpa de revisitar uma família cujo hold-out já foi
> consumido.

## Por que uma v2

O hold-out da METODOLOGIA_CARRY original foi usado (uso único, registro no
próprio documento) e a família reprovou. FAIL pré-registrado é final PARA
AQUELES DADOS. A única revisita legítima é sobre dados que **não existiam**
quando o teste original rodou — dados do futuro, impossíveis de overfitar.

## Janela e medição (congeladas)

- **Janela de medição:** funding rates de BTCUSDT perp (fapi) de
  **2026-08-15 00:00 UTC a 2026-11-15 00:00 UTC** (92 dias, ~276 eventos de
  funding de 8h).
- **Data da medição:** a partir de **2026-11-16**. Os dados são baixados da
  API histórica da Binance NO ATO da medição (`research/coletar_funding.py`)
  — nada é coletado nem olhado antes disso. Espiar a janela antes da data
  anula este contrato.
- **Estratégia medida:** exatamente a da METODOLOGIA_CARRY original
  (delta-neutro long spot + short perp, entra quando funding anualizado ≥
  limiar pré-registrado lá, custos idem). Nenhum parâmetro novo — v2 é a
  MESMA hipótese em dados novos, não uma hipótese nova (0 trials novos além
  do 1 desta janela).
- **Régua (a mesma da original):** carry líquido anualizado da janela ≥
  **8% a.a.** com ≥ 60% do tempo em posição. Passa → reabre o caminho da
  família (walk-forward + paper). Falha → FAIL definitivo da família também
  em regime pós-2026-08.

## O que fica agendado

Vigia não é necessário: a medição é um ato deliberado do operador em
16/11/2026 (`python research/carry_lab.py --v2` a implementar na data, ou
manualmente com a janela acima). Este documento é o lembrete e o contrato.

## Registro de medições

(nenhuma — a janela abre em 2026-08-15 e a medição só em 2026-11-16)
