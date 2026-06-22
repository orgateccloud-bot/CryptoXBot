# 📦 `_legado/` — Código e Infra Aposentados (@Zeta)

> **Branch:** `chore/aposentar-cluster-async` · **Princípio @Zeta:** nunca
> deletar — arquivar preservando reversibilidade total.

## Lote 2 — Infra Docker/GCP (2026-06-22)

**Decisão:** o deploy é **único — Railway (nixpacks) + Supabase (Postgres)**.
Toda a infra de Docker e GCP do caminho antigo foi movida para `_legado/infra/`:

| Arquivado | Era |
|-----------|-----|
| `Dockerfile`, `.dockerignore`, `docker-compose*.yml`, `cloudbuild.yaml` | build/deploy via container (GCP) |
| `terraform/` | IaC do GCP (Compute Engine + Artifact Registry) |
| `deploy/` (`*.service`, `nginx.conf`, `setup.sh`, `transferir.sh`) | deploy manual VPS (Oracle/systemd) |
| `deploy.sh`, `deploy-prod.sh` | scripts de deploy GCP/manual |
| `github-workflows-deploy.yml` | era `.github/workflows/deploy.yml` (deploy GCP) |
| `PRODUCTION_README_GCP.md` | guia de produção GCP |

**Config canônica que permanece na raiz:** `railway.toml`, `Procfile`,
`.python-version` (3.11), `supabase/migrations/`. O `ci.yml` perdeu o job de
build Docker (Railway faz deploy observando o repo). Dev local usa SQLite
(sem Docker); ver `docs/Operacao/`.

> ⚠️ `_legado/infra/` contém DUAS coisas: os artefatos Docker/GCP acima **e** os
> módulos Python do cluster async (database.py, logging.py, metrics.py,
> paper_trading.py, pubsub_client.py) do Lote 1.

---

# 📦 Lote 1 — Cluster Async Aposentado

> **Data:** 2026-06-20

## Por que isto foi arquivado

O projeto tinha **duas arquiteturas coexistindo**:

1. **Produção (síncrona, em uso):** `main.py` com threads + `executor.py` + `database.py`
   (SQLite/Postgres) + `risco.py`. É o que roda no bot real.
2. **"Fase 2" (assíncrona, nunca conectada):** um cluster `asyncio` completo
   costurado por `config/di_container.py`, que **nenhum ponto de entrada de
   produção** (`main.py`, `dashboard.py`, `monitor_fluxo.py`, `health.py`)
   jamais importou.

A análise de reachability de imports confirmou que todo o conteúdo abaixo só era
alcançável a partir do próprio cluster ou de testes que o validavam — ou seja,
**código órfão do ponto de vista de produção**. Manter as duas arquiteturas
causava confusão (dois executores, dois "databases", inferência ML em stub com
predições hardcoded). Decisão registrada em `RELATORIO_MAPEAMENTO_MELHORIAS.md`
(itens C-4, C-5, P2-10).

## O que foi movido (e por quê)

| Origem | Destino | Motivo |
|--------|---------|--------|
| `core/` (event_loop, websocket_client) | `_legado/core/` | Event loop async + WS resiliente; só usados via `di_container` |
| `execution/` (order_manager, signal_executor) | `_legado/execution/` | Execução async; produção usa `executor.py` síncrono |
| `infra/database.py` | `_legado/infra/database.py` | Camada async de dados; produção usa `database.py` da raiz |
| `infra/logging.py`, `infra/metrics.py`, `infra/paper_trading.py`, `infra/pubsub_client.py` | `_legado/infra/` | Observabilidade/persistência do cluster; nenhum consumidor em produção |
| `data/stream_processor.py` | `_legado/data/stream_processor.py` | Consumido apenas por `core/websocket_client.py` (arquivado) |
| `ai/inference.py` | `_legado/ai/inference.py` | **Stub** com predições hardcoded (`[0.2,0.6,0.2]`); nunca usado pelo loop real |
| `config/di_container.py` | `_legado/config/di_container.py` | Wiring (DI) de todo o cluster async |
| `lgbm_modelo.py` | `_legado/lgbm_modelo.py` | LightGBM órfão; `lightgbm` nem está no `requirements.txt` |

**Testes que validavam exclusivamente o cluster** (movidos juntos):
`tests/test_ai.py`, `tests/test_execution.py`, `tests/test_integration.py`,
`tests/run_tests.py`, `scripts/test_core.py`, `scripts/test_chaos_websocket.py`,
`test_paper_trading.py`, `test_phase22.py`, `test_phase23.py`.

## O que permaneceu em produção (NÃO foi tocado)

- `main.py`, `executor.py`, `risco.py`, `database.py`, `logger.py`, `health.py`,
  `dashboard.py`, `monitor_fluxo.py`
- ML em uso: `ml_filtro.py` (XGBoost), `lstm_modelo.py` (MLP), `ensemble.py`,
  `fsrs_trading.py`, `score.py`, `regime.py`, `fear_greed.py`
- `data/cvd_calculator.py` (usado por `score.py`)
- `ai/ollama_client.py` (independente do cluster)
- `scripts/migrate_sqlite_to_supabase.py` (utilitário de migração)
- Testes ativos: `tests/test_data.py`, `tests/test_melhorias.py`

> `chaos_test_report.json` (raiz) é o artefato do antigo teste de caos
> (`scripts/test_chaos_websocket.py`, agora arquivado). Mantido como histórico.

## Estado: NÃO executável como está

O código aqui dentro tem imports cruzados (`from config.di_container import ...`,
`from infra.logging import ...`) que apontam para os caminhos **originais**. Como
os módulos saíram desses caminhos, o cluster **não roda a partir de `_legado/`**.
Isso é esperado: para reativar, faça o rollback (abaixo), que devolve tudo aos
lugares originais e restaura os imports.

`pytest.ini` exclui `_legado/` da coleta (`norecursedirs`), então os testes
arquivados não rodam nem quebram a suíte de produção.

## 🔄 Rollback (reversão total)

Para restaurar o cluster async ao estado anterior à aposentadoria:

```bash
# A partir da raiz do repositório:
git mv _legado/core core
git mv _legado/execution execution
git mv _legado/ai/inference.py ai/inference.py
git mv _legado/config/di_container.py config/di_container.py
git mv _legado/infra/database.py infra/database.py
git mv _legado/infra/logging.py infra/logging.py
git mv _legado/infra/metrics.py infra/metrics.py
git mv _legado/infra/paper_trading.py infra/paper_trading.py
git mv _legado/infra/pubsub_client.py infra/pubsub_client.py
git mv _legado/data/stream_processor.py data/stream_processor.py
git mv _legado/lgbm_modelo.py lgbm_modelo.py
git mv _legado/tests/test_ai.py tests/test_ai.py
git mv _legado/tests/test_execution.py tests/test_execution.py
git mv _legado/tests/test_integration.py tests/test_integration.py
git mv _legado/tests/run_tests.py tests/run_tests.py
git mv _legado/scripts/test_core.py scripts/test_core.py
git mv _legado/scripts/test_chaos_websocket.py scripts/test_chaos_websocket.py
git mv _legado/test_paper_trading.py test_paper_trading.py
git mv _legado/test_phase22.py test_phase22.py
git mv _legado/test_phase23.py test_phase23.py
```

Alternativa mais simples: `git revert` do commit de aposentadoria, ou
`git checkout <commit-anterior> -- <caminhos>`.

Após o rollback, remova `_legado/` de `pytest.ini` se quiser que os testes
arquivados voltem a rodar.
