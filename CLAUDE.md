# Comanda

Orquestração multi-agente para atendimento ao cliente de delivery, construída como projeto de portfólio para a trilha AI Engineer do estágio GenAI do iFood (inscrição até 17/11/2026). Leia `GLOSSARY.md` antes de nomear qualquer conceito de domínio e `docs/adr/` antes de mudar arquitetura.

## Convenções

- Os agentes conversam com o cliente em português. Código, nomes de arquivo, commits e identificadores ficam em inglês. README em português com resumo em inglês no topo.
- Valores monetários são inteiros em centavos. Nenhum valor de reembolso é decidido pelo modelo (ADR-0001).
- Toda decisão de arquitetura vira um ADR em `docs/adr/`, sem perguntar ao usuário se deve registrar.
- Commits seguem Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Python: `uv`, `ruff`, `pyright`, `pytest`. Painel: `pnpm`, TypeScript estrito, ESLint.
- Funcionalidades novas seguem `/tdd`: teste falhando primeiro, uma fatia vertical por vez.

## Estrutura

- `backend/`: pacote Python `comanda` (domain, tools, agents, runtime, evals, api).
- `web/`: painel em Next.js.
- `evals/`: cenários em YAML e resultados versionados.
- `docs/`: `PRD.md`, `adr/`, `agents/` e `architecture.md`.

## Agent skills

### Issue tracker

Issues e specs ficam no GitHub Issues de `TheAndr3/comanda`, via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Rótulos padrão: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` e `docs/adr/` na raiz. See `docs/agents/domain.md`.
