# `_legado/` — código aposentado, preservado para reversibilidade

Nada aqui é importado por código de produção. Os arquivos ficam versionados em
vez de deletados para que a reversão seja um `git mv`, não uma arqueologia.

---

## `settings_template.py` — aposentado em 2026-08-07 (frente I-8)

**O que era.** Template a ser copiado para `config/settings.py`, criando um
módulo de configuração local com credenciais e endpoints.

**Por que saiu.** `config/runtime_settings.py` consultava `config/settings.py`
através de um `import` protegido por `try/except` — invisível a qualquer
varredura de dependências — e esse valor tinha **precedência sobre os defaults**.
Duas consequências, ambas medidas na auditoria de 2026-08-06:

1. `API_KEY`/`API_SECRET` podiam ser resolvidos por arquivo local não
   versionado, sem nenhum aviso no boot.
2. `REST_BASE_URL`/`WS_BASE_URL` caíam para `fapi.binance.com` / `fstream`, ou
   seja **Futures**, sobrepondo o default SPOT que o P0-1 estabeleceu justamente
   para eliminar a divergência sinal-execução. Isso ficava mascarado por duas
   linhas do `.env`: bastava removê-las para o bot passar a ler Futures
   silenciosamente enquanto executava Spot.

O template era o passo documentado que armava esse vetor — ele instruía
literalmente a criar o arquivo do fallback, e ainda ensinava
`DB_PATH='data/btc_data.db'`, o mesmo caminho do banco vivo cuja colisão já
causou três contaminações (ver o cabeçalho de `conftest.py`).

**O que fazer no lugar.** Configuração local se faz por `.env`, que
`load_dotenv()` já lê em `runtime_settings.py`. Copie `.env.example`. A
precedência agora é apenas: **variável de ambiente > default do
`runtime_settings.py`** — uma única fonte, visível, sem import escondido.

**Plano de rollback.** Se por algum motivo o fallback precisar voltar:

```bash
git mv _legado/settings_template.py config/settings_template.py
```

e restaurar em `config/runtime_settings.py` o bloco removido:

```python
try:
    from config import settings as _local_settings
except Exception:
    _local_settings = None

def _local(name, default=None):
    return getattr(_local_settings, name, default) if _local_settings else default
```

A assinatura de `_local()` foi **preservada** de propósito (hoje ela apenas
devolve o default), então as ~30 chamadas `_env("X", _local("X", padrao))`
continuam válidas e o rollback é só esse bloco. Não há diff espalhado.

**Nota de segurança.** `config/settings.py` (o arquivo gerado, não o template)
continua na máquina do desenvolvedor, é `gitignored` e **deixou de ser
consultado** — não foi apagado, para não destruir configuração local. Se ele
contém credenciais, elas agora são inertes para o bot, mas continuam no disco.

---

## `motor_otimizado.py` — aposentado em 2026-08-09 (frente I-12d)

**O que era.** Variante do backtest sem ML (regime + CVD), cujo propósito
declarado era medir a **contribuição de cada filtro** — quantos trades cada um
dos 7 filtros barrava — para orientar quais valia a pena manter.

**Por que saiu — duas razões independentes, cada uma suficiente.**

1. **Órfão confirmado.** Zero importadores em toda a árvore: nenhum módulo,
   nenhum entrypoint, nenhum teste. A única import do arquivo era ele mesmo
   puxando `backtesting.motor` para imprimir o relatório. Verificado por
   varredura de `motor_otimizado` em todos os `*.py` do repositório.

2. **O propósito era destruído pelo próprio módulo.** O filtro MTF é gate
   booleano **obrigatório** (`:189`, `:208`) e era alimentado por `idx4 = i//4`
   — o mapeamento que lê o candle 4h **ainda aberto**. Na série do projeto isso
   dá look-ahead em 100% das barras (as duas séries têm origens diferentes: a
   1h começa 46h antes da 4h). E como os 7 filtros entram em **AND**, um filtro
   que enxerga o futuro contamina a contagem de *todos* os outros: um filtro
   parece "barrar pouco" só porque o MTF vazado já deixou passar apenas as
   barras que iam subir. O número que o módulo existia para produzir nunca
   mediu o que dizia medir.

Corrigir o `idx4` (a função `mapear_idx_fechado` de `backtesting/alinhamento.py`
já existe) seria possível, mas reviveria um módulo sem chamador e sem teste
para produzir um diagnóstico que hoje ninguém consome.

**O que fazer no lugar.** Contribuição por filtro se mede no motor vivo
(`backtesting/motor_ensemble.py`) ou em `backtesting/walk_forward.py`, que já
usam `mapear_idx_fechado` e a régua unificada (`backtesting/regua.py`).

**Plano de rollback.**

```bash
git mv _legado/motor_otimizado.py backtesting/motor_otimizado.py
```

O arquivo é autocontido — só depende de `backtesting.motor` (que continua no
lugar) e de `indicadores`. Nenhum outro arquivo foi tocado por esta
aposentadoria, então o `git mv` basta. **Antes de confiar em qualquer número
dele, trocar `idx4 = i//4` (`:83`) por `mapear_idx_fechado`.**

---

## `motor_vectorbt.py` + `tests/test_motor_vectorbt.py` — aposentados em 2026-08-09 (frente I-12d)

**O que era.** Grid search vetorizado (VectorBT sobre numba, P2-2a, 2026-07-22),
aditivo a `backtesting/otimizador.py`: mesma lógica de sinal, montada em numpy
e executada por `vbt.Portfolio.from_signals`, para varrer milhares de
combinações de parâmetros sem o loop Python.

**Por que saiu — três razões.**

1. **A referência de paridade deixou de existir.** O módulo era uma
   reimplementação vetorizada de `motor_ensemble._score_backtest`, e o teste
   comparava um contra o outro elemento a elemento. A frente I-12 **eliminou**
   `_score_backtest` (as réguas passaram a usar `score.calcular` via
   `backtesting/regua.py`). Hoje `tests/test_motor_vectorbt.py:34` importa um
   nome que não existe mais: o arquivo só não quebra a suíte porque o
   `pytest.importorskip("vectorbt")` da linha 31 pula tudo antes de chegar lá.
   Um teste que nunca roda contra uma função que não existe não é cobertura.

2. **Nunca executou neste ambiente.** `vectorbt` não está em
   `requirements.txt` — só em `requirements-backtest.txt` (que saiu junto, para
   `_legado/`), deliberadamente,
   porque este projeto roda num Python global compartilhado e o pin do
   vectorbt força upgrade de numpy/pandas. Zero execuções registradas.

3. **Quebra de paridade que o teste era incapaz de detectar.** O motor legado
   dimensiona a posição por `* fator` (0,5 no score intermediário
   `OPERAR_PARCIAL`); a versão VBT usava `fator` só como **booleano**, com
   `size` escalar constante (`:304-306`, `:333`). Todo trade de meia posição
   valia o dobro. O teste de paridade de pipeline comparava apenas a
   **contagem** de trades, com tolerância de 0,5x a 2,0x — uma métrica
   estruturalmente cega a um erro de tamanho de posição.

**O que fazer no lugar.** `backtesting/otimizador.py` continua sendo o grid
search suportado. Se a velocidade voltar a ser o gargalo, o caminho é
vetorizar em numpy puro dentro dele (sem nova dependência), com o teste de
paridade comparando **PnL por trade**, não contagem.

**Plano de rollback.**

```bash
git mv _legado/motor_vectorbt.py backtesting/motor_vectorbt.py
git mv _legado/test_motor_vectorbt.py tests/test_motor_vectorbt.py
git mv _legado/requirements-backtest.txt requirements-backtest.txt
pip install -r requirements-backtest.txt   # num venv isolado, nunca no global
```

Reviver exige, **antes de qualquer medição**, três correções — nenhuma delas
foi feita, e sem elas o módulo produz números errados em silêncio:

1. Trocar `_score_backtest` por `regua.score_unificado` no módulo e no teste
   (o nome antigo não existe mais em `motor_ensemble`).
2. Trocar `idx4 = i//4` (`:229`) por `alinhamento.mapear_idx_fechado`.
3. Passar `fator` como **multiplicador de size**, não como booleano
   (`:304-306`, `:333`), e trocar o assert de paridade de contagem-de-trades
   por comparação de PnL.
