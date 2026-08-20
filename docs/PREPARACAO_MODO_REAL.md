# Preparação para o modo real — a pista, não a decolagem

> 2026-08-20 · Preparado a pedido do operador ("prepare para ativarmos o
> modo real"). **Este documento não arma nada.** Ele mede a prontidão
> técnica de hoje e deixa pronta a cerimônia para o dia em que o portão
> científico abrir. O contrato continua o de sempre:

## 0 · O contrato (inalterado e inegociável)

**Capital real está PROIBIDO pelo gate.** `GATE_GO_LIVE.md` Etapa 1 =
REPROVADA (pré-registrado, final); funil com 5 FAIL · 1 EM COLETA · 1
AGENDADA. A sequência que abre a porta é científica, não técnica:

1. Uma família do funil **SOBREVIVE** ao hold-out (próximas janelas:
   micro E-11 medição ~25-26/08 · CARRY-v2 16/11 · hold-out geral 01/12);
2. **Etapa 1** — edge com expectativa positiva, walk-forward honesto;
3. **Etapa 2** — 90 dias de paper com PF ≥ 1.3 medidos por
   `relatorio_gate.py` (exige paper OPERANDO — ver §2);
4. **Etapa 3** — micro-capital 30 dias;
5. Só então: ignição (§5), executada PELO OPERADOR.

Nenhum item abaixo antecipa, substitui ou pressiona essa sequência.

## 1 · Ignição hoje (medido em 2026-08-20 09:4x)

| Chave | Estado | Onde |
|---|---|---|
| `DRY_RUN` | `true` ✅ fechada | `.env` produção |
| `ALLOW_REAL_TRADING` | `false` ✅ fechada | `.env` produção |
| `ENV` | `production` (neutra) | `.env` |
| Flag de boot | `--simulacao` ✅ fechada | NSSM AppParameters |

4/4 como devem estar. O dashboard denuncia ignição ARMADA em vermelho
(aba 3) — verificado em produção.

## 2 · Conta e chave (medido ao vivo)

- **Chave**: autenticada · `pode_negociar_spot=True` ·
  `pode_sacar=False` · `pode_futures=False` · `restrito_por_ip=True` —
  menor privilégio correto. (Lembrete: `canTrade` da CONTA não é a
  permissão da CHAVE; a fonte é `restricoes_chave()`.)
- **Saldos**: BTC 0.02071317 (≈ $1.5k) · ETH 0.0008 · FDUSD 0.96 ·
  **USDT ≈ 0**. O bot compra pares xUSDT com USDT ⇒ **o paper está
  travado no gate "Saldo insuficiente (< $10)"** — e a Etapa 2 exige
  paper operando. Destravar é decisão do operador (converter algo de
  BTC→USDT na Binance, depositar, ou aprovar um capital fixo de
  simulação com quebra de série documentada). *O assistente não executa
  conversões nem trades — nunca.*

## 3 · Pendências técnicas NO CAMINHO DO DINHEIRO (scorecard 2026-08-19)

Bloqueantes para ignição (independentes do gate científico):

| # | Pendência | Estado |
|---|---|---|
| 1 | Freio de drawdown "total" não acumula entre dias | ✅ **RESOLVIDO** (`007b86c`, 20/08 — marca d'água de equity em risk_state, sobrevive a restart) |
| 2 | Cancel ignorado no laço maker-first (re-quote pode deixar 2 LIMIT_MAKER vivas; fill entre poll e cancel some) | aberto, sem teste |
| 3 | `RECONCILIAR_BOOT_EXCHANGE` dormente (22 testes, 0 execuções) | ligar após drill (§4) |
| 4 | Caminho real do OCO/maker nunca executado fora de mock | drill testnet (§4) |
| 5 | Kill-switch: `persistir_estado()` engole erro — trava pode ficar só em memória | aberto |
| 6 | `fear_greed` fail-open (falha vira 50 e desliga vetos de sentimento) | aberto |
| 7 | lstm_modelo rótulo pré-E-10 | ✅ **RESOLVIDO** (`a6d33f1`, 20/08 — barreira tripla + contexto 300 + primeira suíte; quebra de série em model_metricas no próximo retreino, documentada) |

Recomendação: nenhuma ignição com a coluna "aberto" não-vazia.

## 4 · Drills obrigatórios (quando o gate abrir — em paper/testnet)

1. **Testnet end-to-end**: maker-first com re-quote + fill parcial; OCO
   colocado, movido (trailing) e cancelado; stop disparado de verdade.
2. **Drill de crash com posição**: abrir posição paper → matar o worker
   → restart → posição recuperada + proteção reconciliada.
3. **`RECONCILIAR_BOOT_EXCHANGE=true` em paper por ≥1 semana** antes de
   qualquer real (é o caminho com 22 testes que hoje dorme).
4. **Prova do canal**: `testar_telegram` pela mesa + um CRITICAL
   sintético chegando ao celular (o vigia só encaminha CRITICAL).
5. **Rollback ensaiado**: cronometrar §6 — desarmar em < 60s.

## 5 · A cerimônia de ignição (dia D — executada PELO OPERADOR)

Pré-condições: §0 cumprido (gate PASS documentado) + §3 zerado + §4
executado + capital de micro definido (Etapa 3: valor pequeno, teto
diário revisado).

Ordem exata (cada passo com verificação antes do próximo):

```
1. .env:  DRY_RUN=false
2. .env:  ALLOW_REAL_TRADING=true
3. NSSM:  AppParameters -> main.py --real --intervalo 15
4. Restart BXBotWorker via scripts\restart-servico.ps1 (prova = PID)
5. Verificar: /metrics exec_estrategia_* + dashboard aba 3 (ignição
   ARMADA em vermelho é o ESPERADO agora) + primeiro ciclo no log
6. Mesa desarmada? Telegram vivo? Kill-switch testado (risco.travar)?
```

O boot faz fail-fast se faltar chave/ENV; `--modo-trend` + `--real`
continua SystemExit (estratégia reprovada não liga em real, nunca).

## 6 · Rollback (a qualquer momento, sem cerimônia)

```
1. .env: DRY_RUN=true  (ou ALLOW_REAL_TRADING=false — qualquer uma trava)
2. NSSM: AppParameters -> main.py --simulacao --intervalo 15
3. Restart worker (PID novo = prova)
4. Posição aberta? O stop/OCO está NA EXCHANGE e sobrevive; fechar
   manualmente pela Binance se desejado.
```

## 7 · O que o assistente faz e não faz

Faz: medir, testar, documentar, preparar drills, manter este checklist.
**Não faz, em nenhuma hipótese**: girar as 4 chaves, revogar FAIL
pré-registrado, executar trade/conversão/transferência, ou tratar
pressa como critério. A porta abre pela régua — e a régua é sua amiga:
é ela que garante que, quando abrir, será para valer.
