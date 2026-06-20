---
tags: [operacao, deploy, supabase, banco]
---

# 🐘 Deploy — Supabase (Postgres gerenciado)

> Voltar: [[00 - Home]] · Relacionado: [[Deploy Railway]] · [[Variaveis de Ambiente]] · [[Dados e Infra]]

O Supabase é o **backend de produção** do banco (Postgres). O `database.py` usa
Postgres automaticamente quando `DATABASE_URL` está setado e
`DATABASE_BACKEND ∈ {postgres, postgresql, supabase}`.

## 1. Criar o projeto Supabase
1. https://supabase.com → **New project** (escolha região próxima da do Railway p/ baixa latência).
2. Defina a senha do banco (guarde — vai na connection string).

## 2. Aplicar o schema
O schema está versionado em `supabase/migrations/001_initial_schema.sql`
(idempotente). Aplique por **um** destes caminhos:

**a) SQL Editor (mais simples)**
- Supabase → **SQL Editor** → cole o conteúdo de `001_initial_schema.sql` → **Run**.

**b) Supabase CLI**
```bash
supabase link --project-ref <ref>
supabase db push        # aplica migrations/
```

Cria as 6 tabelas: `trades`, `snapshots_mercado`, `cvd_historico`, `sinais`,
`risk_state`, `bot_events` (com índices `(symbol, timestamp DESC)`).

## 3. Connection string
Supabase → **Settings → Database → Connection string**. Use o **Transaction
pooler** (porta 6543) para apps com muitas conexões curtas:
```
postgresql://postgres.<ref>:<senha>@aws-0-<region>.pooler.supabase.com:6543/postgres
```
Esse valor vai em `DATABASE_URL` (no Railway). Defina também `DATABASE_BACKEND=postgres`.

## 4. (Opcional) Migrar dados locais SQLite → Supabase
Use o migrador idempotente:
```bash
export DATABASE_URL="postgresql://...pooler.supabase.com:6543/postgres"
python scripts/migrate_sqlite_to_supabase.py --listar      # conta linhas no SQLite
python scripts/migrate_sqlite_to_supabase.py --dry-run     # simula (não escreve)
python scripts/migrate_sqlite_to_supabase.py --confirmar   # executa (pede confirmação)
python scripts/migrate_sqlite_to_supabase.py --validar-pg  # confere contagem no Postgres
```

## 5. Validar
```bash
DATABASE_BACKEND=postgres DATABASE_URL="..." python -c "import database; database.inicializar(); print(database.backend_info()); print('healthcheck:', database.healthcheck())"
```
Deve imprimir backend `postgres` e `healthcheck: True`.

## ⚠️ Pendências conhecidas (ver [[Planejamento de Melhorias]])
- `logger.py` ainda escreve **só em SQLite** → suas tabelas de log não vão para o Supabase.
- `database.fechar_pool()` não é chamado no shutdown (conexões podem vazar em restart).
- Pooler de transação não suporta certas features de sessão; se houver erro, use o **Session pooler** (5432) ou Direct connection.
