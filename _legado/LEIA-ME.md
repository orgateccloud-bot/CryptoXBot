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
