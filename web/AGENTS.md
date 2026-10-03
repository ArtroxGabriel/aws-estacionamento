# AGENTS.md - Frontend Web (React / Vite)

## Project Overview
- Interface Web do operador e do totem da cancela: painel de vagas disponíveis em tempo real, totem de entrada (foto do veículo + ticket provisório com acompanhamento do OCR), caixa de saída (busca, cobrança e liberação da vaga) e trilha de auditoria do DynamoDB.
- Plano completo e contratos: `docs/frontend.md`.

## Tech Stack
- **Runtime / Build**: Vite (Node 26)
- **UI**: React 19 + TypeScript (`strict`, `erasableSyntaxOnly`: sem `enum` nem parameter properties)
- **Estilização**: Tailwind CSS v4 via `@tailwindcss/vite`
- **Rotas**: `react-router` (modo declarativo: `BrowserRouter`/`Routes`)
- **HTTP**: `fetch` nativo encapsulado em `src/services/api.ts`
- **Testes**: Vitest + Testing Library (`react`, `user-event`, `jest-dom`) + jsdom
- **Lint**: oxlint (o que vem no template atual do Vite)

## File Structure
```text
web/
├── Dockerfile                # build (node:26-alpine) + nginx:alpine
├── nginx.conf.template       # SPA fallback + proxy /api/ -> ${API_UPSTREAM}
├── .env.example              # VITE_API_BASE_URL=/api
├── vite.config.ts            # plugins, proxy /api -> :8080 e config do Vitest
└── src/
    ├── main.tsx              # <BrowserRouter><App/></BrowserRouter>
    ├── App.tsx               # rotas: / · /entrada · /saida · /auditoria · 404
    ├── components/           # Layout, SpotsCounter, StatusBadge, TicketCard, Alert, Spinner
    ├── pages/                # DashboardPage, EntryPage, PaymentPage, AuditPage, NotFoundPage
    ├── hooks/usePolling.ts   # polling com setTimeout encadeado, pausa com aba oculta
    ├── services/api.ts       # ÚNICO lugar que chama fetch; ApiError e errorMessage
    ├── types/api.ts          # tipos espelhando os contratos da API
    ├── utils/                # format.ts (moeda, datas, placa, rótulos) e audit.ts
    └── test/setup.ts         # jest-dom + cleanup
```
Testes ficam ao lado do arquivo testado (`*.test.ts(x)`).

## Endpoints consumidos
| Tela | Endpoint |
|------|----------|
| Painel (`/`) | `GET /spots/available` (polling 5 s) |
| Totem (`/entrada`) | `POST /entries` (multipart `photo`), `GET /sessions/{id}` (polling 2 s enquanto `PROCESSING`, máx. 60 s) |
| Saída (`/saida`) | `GET /sessions?status=PARKED` (polling 15 s), `POST /exits/{id}/pay` |
| Auditoria (`/auditoria`) | `GET /audit?limit=100` |

Em dev o Vite faz proxy de `/api/*` para `http://localhost:8080/*`; em produção o nginx do container faz o mesmo para `API_UPSTREAM` (padrão `http://api:8080`). Mesma origem, sem CORS.

## Common Commands
- `task install:web` (`npm ci`): instala as dependências
- `task dev:web` (`npm run dev`): servidor de desenvolvimento em :5173
- `task test:web` (`npm run test -- --run`): testes
- `task lint:web` (`npm run lint`): lint
- `task build:web` (`npm run build`): `tsc -b` + bundle estático em `dist/`
- `docker build -t estacionamento-web ./web` + `docker run -p 80:80 -e API_UPSTREAM=http://<api>:8080 estacionamento-web`

## Architecture Conventions
- Páginas não chamam `fetch`; tudo passa por `src/services/api.ts`. Erros da API chegam como `{"error":"..."}` com `Content-Type: text/plain`, por isso o corpo é lido como texto e o JSON é tentado.
- Identificadores em inglês, textos da interface em pt-BR; campos JSON em `snake_case` sem camada de mapeamento.
- Estado local (`useState`), sem estado global. Toda requisição exibe carregando, erro e sucesso; botões de ação ficam `disabled` durante a requisição (evita pagamento duplo).
- O frontend nunca calcula tarifa: o valor vem de `amount_due`/`amount_paid`.
- Sem animações além do `animate-spin` do `Spinner`.
- DoD: `npm run lint`, `npm run test -- --run` e `npm run build` sem erros nem warnings.

## Changelog
- 2026-10-03: Implementação do frontend (painel, totem, saída, auditoria), cliente HTTP tipado, `usePolling`, testes com Vitest e container nginx.
- 2026-09-15: Criação do AGENTS.md do Frontend Web.
