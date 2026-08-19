# Scorecard de módulos — 2026-08-19

> 10 auditores paralelos, 234 leituras de código real, 4 lentes por módulo
> (correção/robustez · testes · honestidade operacional · dívida). Resolução
> do instrumento: **±1 ponto** (lição medida do forense de 07/08).

## As duas réguas — leia antes de citar uma nota

Este documento mede **saúde de engenharia em paper trading**. Ele NÃO
substitui a régua do `RELATORIO_MODULOS.md` (07/08), que mede **aptidão a
capital real** por portões multiplicativos — e naquela régua a nota global
continua baixa por um único motivo que engenharia nenhuma compensa: **o
Portão 1 (edge) segue FAIL** (5 famílias reprovadas com pré-registro; funil
é o único caminho). Um 8 aqui significa "código são, testado e honesto em
paper" — não "pronto para dinheiro".

## Resumo por grupo

| Grupo | Nota | Âncora do grupo |
|---|:---:|---|
| Pesquisa e backtesting | **8.3** | hold-out de uso único como CÓDIGO (SystemExit), snapshot sha256, DSR que recusa rodar sem trials |
| Execução de ordens | **8.2** | test_executor_dia1 corta elo por elo a corrente de falha composta; oráculo de ~16k casos no monitor |
| Orquestração (main) | **8.1** | WS 24/7 com escalada 1x/episódio + rearme; travas do modo trend em 2 camadas |
| Persistência | **8.0** | migrador com idempotência PROVADA contra PG real; backup com quick_check |
| Alertas e saúde | **8.0** | rodapé do circuit breaker obrigatório por chamador; /health detecta thread morta |
| Gestão de risco | **7.9** | E-9 pinou a constante de risco com prova de invariância em 72 casos |
| Dashboard (backend) | **7.6** | degradação explícita testada em quase toda rota; mesa fail-closed |
| Frontend (Terminal RAZÃO) | **7.1** | sentinela 401 exemplar; zero testes diretos de JS |
| ML e sinais | **7.8** | validacao.py/score.py exemplares; lstm_modelo é o buraco |
| Dados de mercado | **6.3** | klines.py forte; CVD provadamente inerte; fear_greed fail-open |

**Média simples: ≈7.7/10** (régua de engenharia em paper; ±1 de resolução).

## Achados transversais, ranqueados

1. 🔴 **O freio "total" de 15% NÃO acumula entre dias** (`risco.py`,
   `_verificar_drawdown_acumulado`): compara `pnl_dia` — zerado à meia-noite
   — contra `capital_inicio_dia` nunca rebaseado. O cenário motivador do I-8
   (5 dias de −4,9% = −22% de equity) **continua sem travar o bot**; o gate
   "total" é na prática intradiário, e nenhum teste cruza virada de dia.
2. 🔴 **lstm_modelo.py (até 45% do ensemble) treina com o rótulo pré-E-10**
   (máximo de fechamentos, sem barreira de stop — o defeito que ml_filtro
   corrigiu), serve com 100 velas onde a paridade exige 300, e **não tem um
   único teste direto**.
3. 🔴 **CVD é matematicamente inerte** (`data/cvd_calculator.py`): com
   janela=50, teto de |divergence_score| = 0,069 < limiar 0,1 —
   `test_cvd_inerte.py` PROVA que o componente de 7% do score nunca sai de
   50. (Honesto: provado e pré-registrado como hipótese, não remendado.)
4. 🔴 **fear_greed.py fabrica 50 "neutro" em falha** sem log, sem evento,
   sem cache de falha (cada ciclo re-bate na API) e sem nenhum teste direto
   — e `otimizada.py` grava esse 50 no sinal como leitura real.
5. 🟡 **Vigia pula CRITICALs se o Telegram cair**: o cursor avança
   incondicionalmente mesmo quando `_enviar` falhou em todo o lote —
   eventos do intervalo nunca chegam ao operador.
6. 🟡 **Socket.IO fora do gate de token**: os emits entregam estado/preço a
   qualquer cliente conectado (mitigado pelo bind 127.0.0.1; vira furo real
   se `DASHBOARD_BIND` mudar).
7. 🟡 **Fuso misto na persistência**: SQLite grava hora local naive, caminho
   PG assume UTC — série desloca ~3h se o dormente Supabase reativar
   (inclusive no migrador, que rotula BRT como UTC).
8. 🟡 **Kill-switch com durabilidade não garantida**: `persistir_estado()`
   engole exceção — DB indisponível no instante do `travar()` + restart =
   bot destravado em silêncio.
9. 🟡 **Monitor do executor**: `pos = self.posicao` é referência (o
   comentário promete snapshot M-2); parcial não arredonda qty/2 ao step; o
   `except` do loop só imprime.
10. 🟡 **`reconciliar_boot` (22 testes) dorme atrás de flag default-off** —
    produção usa o caminho legado que confia só no DB.

## Meta-achado: o instrumento pegou um vermelho mascarado

O auditor de alertas encontrou `test_ws_resiliencia.py::test_escala_uma_
unica_vez_por_episodio` **vermelho em execução isolada** e verde na suíte
completa: o mock silenciava a aposentada `alerta_circuit_breaker`, a nova
`alerta_ws_indisponivel` rodava de verdade, e o `alerta_nao_entregue`
resultante era suprimido pelo **debounce global consumido por um teste
anterior de outro arquivo**. Ordem-dependência real, corrigida junto com
este scorecard (mocks atualizados para as funções atuais).

## Reconciliação com o forense de 07/08 (mesmos módulos, régua diferente)

Deltas de engenharia desde a auditoria que deu global 2/10 — lembrando que
lá a régua era capital-real e os líderes de melhoria foram exatamente as
frentes I-8..I-13/E-7..E-11 que aquele plano ordenou:

| Módulo | 07/08 (capital real) | Hoje (engenharia paper) | O que mudou |
|---|:---:|:---:|---|
| telegram_bot.py | 2 | 8 | canal vivo + entrega provada + rodapé honesto por chamador |
| dashboard.py | 2 | 7.6 | régua única, mesa fail-closed, 409 educativo no backtest |
| estrategias/otimizada.py | 2 | 8 | fail-closed no ensemble (E-10), invariante stop<preço na origem |
| suporte.py | 2 | 7.5 | E-7 matou o stop de BTC em ETH/SOL |
| executor.py | 3 | 8.2 | cadeia de dia-1 coberta elo a elo (I-10) |
| risco.py | 3 | 7.9 | kill-switch gate 0 + E-9; **resta o freio que não acumula** |
| database.py | 3 | 8 | dict_row com scanner de regressão; comandos auditados |
| logger.py | 3 | 6.5 | range no lugar de LIKE; win_rate honesto; escrita ainda engole erro |
| ensemble.py | 3 | 8 | symbol obrigatório (E-7), degradação em cascata explícita |
| lstm_modelo.py | 2 | 5.5 | gate de promoção existe; **rótulo velho + zero testes seguem** |
| data/cvd_calculator.py | 2 | 5 | inércia agora PROVADA e pré-registrada (era só ignorada) |
| fear_greed.py | 2 | 4.5 | **quase intocado — o pior módulo vivo do repo** |

## Veredito

Engenharia em paper: **≈7.7 — são, testado e honesto na média**, com os
quatro vermelhos acima como próxima fila de trabalho. Aptidão a capital
real: **inalterada** — o Portão 1 (edge) segue FAIL e nenhuma nota deste
documento o move; quem o move é o funil (`research/vereditos/`), micro
E-11 em coleta (medição ~25-26/08), CARRY-v2 16/11, hold-out 01/12.

*Gerado sobre commit `44b7a40` + fix do teste mascarado; suíte completa
verde (ver commit deste arquivo).*
