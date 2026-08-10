# `tests/integration/` — suíte contra o Binance Spot Testnet

> **Esta suíte envia ordens de verdade.** Contra o testnet, com moedas de
> brinquedo, mas ordens reais num livro real. Por isso está desligada por
> padrão e só roda com opt-in explícito.

Existe para cumprir o critério de saída da frente **I-10** (cadeia de dia-1 do
executor, `docs/RELATORIO_MODULOS.md`): nenhum dos caminhos de execução real do
bot jamais rodou — dez métodos bifurcam por `if self.simulacao` e o grafo que
receberá capital tem **zero execuções**.

---

## Como rodar

1. Crie uma chave de API no testnet: <https://testnet.binance.vision/>
   (login com GitHub; a conta vem com saldo fictício).
2. Exporte as variáveis — note que são **próprias**, nunca as de produção:

```bash
export BINANCE_TESTNET_API_KEY=...
export BINANCE_TESTNET_API_SECRET=...
export RODAR_INTEGRACAO_TESTNET=1
```

3. Rode só este diretório:

```bash
pytest tests/integration/ -v
```

Sem as três variáveis, os testes são pulados com o motivo impresso — a suíte
normal (`pytest tests/`) continua verde e hermética.

### Variáveis opcionais

| Variável | Default | Para quê |
|---|---|---|
| `INTEGRACAO_PAR` | `BTCUSDT` | par usado nos testes |
| `INTEGRACAO_NOTIONAL` | `15` | tamanho de cada ordem em USDT (o testnet exige ≥ 10) |
| `INTEGRACAO_CICLOS` | `10` | ciclos do teste de invariante; **o gate oficial do I-10 pede `50`** |

Para o run que vale como critério de saída:

```bash
INTEGRACAO_CICLOS=50 pytest tests/integration/ -v
```

---

## As três travas

Qualquer uma que falhe impede a execução:

1. **`RODAR_INTEGRACAO_TESTNET=1`** — ter credencial no ambiente não basta;
   tem de haver intenção.
2. **Credenciais próprias** (`BINANCE_TESTNET_*`) — nunca reaproveitamos
   `BINANCE_API_KEY`, para que uma chave de produção presente no ambiente não
   seja usada aqui por acidente.
3. **Endpoint hardcoded** em `conftest.py` (`URL_TESTNET`), não lido de
   `config/runtime_settings.py`. Um `.env` apontando para `api.binance.com`
   não tem como vazar para dentro destes testes. Há um `assert "testnet" in
   URL_TESTNET` no import — se alguém trocar a constante, a suíte se recusa a
   rodar em vez de mandar ordem com dinheiro real.

Cada teste limpa o que criou (cancela ordens abertas, zera a posição) no
teardown, **inclusive se falhar no meio**.

---

## O que está aqui e o que está no hermético

O critério do I-10 lista 8 cenários. Eles se dividem por uma razão técnica,
não por conveniência.

### Só a exchange real prova — `tests/integration/test_dia1_testnet.py`

| # | Cenário | Por que precisa do testnet |
|---|---|---|
| 1 | fill com comissão em ativo-base → stop **aceito** | só a Binance debitando a taxa em BTC de verdade prova que o `STOP_LOSS_LIMIT` passa no filtro de saldo |
| 4 | stop executado fora do bot → detectado | exercita o ciclo de vida real da ordem |
| 8 | crash e restart → `reconciliar_boot` converge | precisa de estado real na exchange para convergir contra |
| — | invariante: 0 posições sem stop após N ciclos | métrica agregada do I-10 |

### Injeção de falha — `tests/test_executor_dia1.py` (hermético)

| # | Cenário |
|---|---|
| 2 | stop rejeitado → `abrir_long` devolve False, sem posição órfã |
| 3 | 503 na entrada → nenhuma ordem duplicada |
| 5 | SELL rejeitado → monitor sobrevive e retenta |
| 6 | exceção em `salvar_sinal` → PnL contabilizado exatamente 1× |
| 7 | cancelamento que falha → ids **não** são zerados |

Não dá para pedir ao testnet que devolva 503 no momento certo, nem que rejeite
um SELL sob demanda. Forçar isso exigiria um proxy de injeção de falhas — mais
infraestrutura para provar o que o teste hermético já prova de forma
determinística e sem flakiness. O que o testnet acrescenta é o que o mock não
consegue: o comportamento real dos filtros, da comissão e do ciclo de vida da
ordem.

---

## Limites conhecidos

- **O testnet não é a produção.** Liquidez, latência e comportamento de
  rejeição diferem. Um verde aqui prova que o caminho de código funciona contra
  uma Binance de verdade — não que a estratégia funciona.
- **Não substitui a Etapa 2 do gate** (`docs/GATE_GO_LIVE.md`): 90 dias de
  paper trading com ≥ 30 trades fechados continuam sendo pré-requisito para
  capital real, independentemente desta suíte.
- O teste de invariante fecha as posições a mercado, então **cada ciclo paga
  spread + taxa** no saldo fictício. Com `INTEGRACAO_CICLOS=50` e notional de
  15 USDT isso consome uma fração pequena, mas a conta de testnet precisa ter
  saldo.
