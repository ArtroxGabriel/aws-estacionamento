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
| Totem (`/entrada`) | `POST /entries` (multipart `photo`), `GET /sessions?status=ALL` (enquanto `PROCESSING`: 2 s no primeiro minuto, depois 15 s até 12 min; erros transitórios mantêm a consulta; a sessão é localizada pelo ID na lista) |
| Saída (`/saida`) | `GET /sessions?status=PARKED` + `GET /sessions?status=FAILED` (polling 15 s), `POST /exits/{id}/pay` |
| Auditoria (`/auditoria`) | `GET /audit` (50 eventos mais recentes) |

Na Auditoria, a placa e o resultado do OCR vêm da sessão (`GET /sessions?status=ALL`), pois os eventos são imutáveis: `ENTRY` guarda `PROCESSING` e `EXIT_PAYMENT` não traz placa. Falhas (`OCR_FAILED`, `POISON_MESSAGE`, `ENTRY_FAILED`) aparecem destacadas, com o motivo do worker traduzido em `src/utils/audit.ts` e o filtro "Somente falhas".

No Caixa, cada linha tem **Pagar e liberar**, **Informar placa** (sessão `FAILED`, OCR não leu) ou **Corrigir placa** (leitura errada) e **Excluir**, com confirmação. A placa é validada no navegador (`isValidPlate`, mesma regra da API: `ABC1D23` ou `ABC1234`) e enviada normalizada via `PATCH /sessions/{id}`; a exclusão usa `DELETE /sessions/{id}`. Na Auditoria, `PLATE_CORRECTION` mostra a placa nova e a anterior, e `SESSION_DELETE` o status que a sessão tinha.

As listagens da API são arrays crus e vêm como `null` quando vazias; `src/services/api.ts` normaliza para `[]`. A API não expõe `GET /sessions/{id}` nem o valor a pagar antes da cobrança: o valor aparece no recibo (`amount_paid` de `POST /exits/{id}/pay`). Pagamento de sessão fora de `PARKED`/`FAILED` retorna 409.

Em dev o Vite faz proxy de `/api/*` para `http://localhost:8080/*`; em produção o nginx do container faz o mesmo para `API_UPSTREAM` (padrão `http://api:8080`). Mesma origem, sem CORS. O `nginx-api-upstream.envsh` extrai `host:porta` de `API_UPSTREAM` para um bloco `upstream` com keepalive. Sem keepalive, cada request abre um TCP novo, e sob carga isso lota a tabela conntrack do Docker.

## Common Commands
- `task install:web` (`npm ci`): instala as dependências
- `task dev:web` (`npm run dev`): servidor de desenvolvimento em :5173
- `task test:web` (`npm run test -- --run`): testes
- `task lint:web` (`npm run lint`): lint
- `task build:web` (`npm run build`): `tsc -b` + bundle estático em `dist/`
- `docker build -t estacionamento-web ./web` + `docker run -p 80:80 -e API_UPSTREAM=http://<api>:8080 estacionamento-web`

## Architecture Conventions
- Páginas não chamam `fetch`; tudo passa por `src/services/api.ts`. Erros da API chegam como `{"error":"..."}`; o corpo é lido como texto e o JSON é tentado sem depender do `Content-Type` (respostas fora da API, como o 404 padrão do Go ou do nginx, são texto puro).
- Identificadores em inglês, textos da interface em pt-BR; campos JSON em `snake_case` sem camada de mapeamento.
- Estado local (`useState`), sem estado global. Toda requisição exibe carregando, erro e sucesso; botões de ação ficam `disabled` durante a requisição (evita pagamento duplo).
- O frontend nunca calcula tarifa: o valor exibido é o `amount_paid` devolvido pela API.
- Sem animações além do `animate-spin` do `Spinner`.
- DoD: `npm run lint`, `npm run test -- --run` e `npm run build` sem erros nem warnings.

## Changelog
- 2026-10-03: Caixa com placa digitada/corrigida e exclusão de registro; Auditoria com `PLATE_CORRECTION` e `SESSION_DELETE`.
- 2026-10-03: nginx com `upstream` + keepalive para a API (estabilidade sob carga no ALB); build no `$BUILDPLATFORM`.
- 2026-10-03: Implementação do frontend (painel, totem, saída, auditoria), cliente HTTP tipado, `usePolling`, testes com Vitest e container nginx.
- 2026-09-15: Criação do AGENTS.md do Frontend Web.
