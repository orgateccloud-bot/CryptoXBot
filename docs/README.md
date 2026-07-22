# 📚 Vault de Documentação — BinanceXBot

Esta pasta `docs/` é um **vault do [Obsidian](https://obsidian.md)**: notas Markdown
interligadas por `[[wikilinks]]`. Abra a pasta `docs/` como um cofre (vault) no
Obsidian para navegar pelo grafo de conhecimento do projeto.

## Como abrir
1. Obsidian → **Open folder as vault** → selecione `docs/`.
2. Comece por **[[00 - Home]]** (mapa de conteúdo / MOC).
3. Use o **Graph View** (Ctrl/Cmd+G) para ver as conexões entre módulos.

## Organização
```
docs/
├── 00 - Home.md                 ← comece aqui (MOC)
├── Arquitetura/
│   ├── Visao Geral.md
│   └── Fluxo de Execucao.md
├── Modulos/
│   ├── Core e Execucao.md
│   ├── ML e Sinais.md
│   ├── Dados e Infra.md
│   └── Estrategias e Backtesting.md
├── Operacao/
│   ├── Deploy Supabase.md
│   ├── Deploy VPS.md
│   └── Variaveis de Ambiente.md
├── Planejamento de Melhorias.md
└── Pontuacoes do Projeto.md
```

> Atualizado em 2026-07-22 a partir da análise da branch `chore/aposentar-cluster-async`
> (estado atual: mercado Spot, serviço 24/7 NSSM/systemd, 971 testes, locking do
> executor + reconciliação de boot + observabilidade conectada + CVaR de cauda).
