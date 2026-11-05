# Monorepo com backend Python e painel Next.js

Backend (`backend/`, Python com uv) e painel (`web/`, Next.js com pnpm) vivem no mesmo repositório, junto de `evals/` e `docs/`. O painel recebe o rastro ao vivo da API FastAPI por SSE, e a versão de replay é exportada como site estático para a Vercel. Um repositório só mantém PRD, ADRs, issues e código no mesmo lugar, que é o que um revisor do portfólio vai navegar.
