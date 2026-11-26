# AGENTS.md - Frontend Web (React / Vite)

## Project Overview
- Interface Web do operador e dos totens: painel (vagas disponíveis e carros no pátio), cancela de entrada (foto do veículo + ticket já válido, com a placa aparecendo quando for lida), caixa (busca por ticket ou placa, foto da entrada, tempo estacionado, valor a pagar e cobrança), cancela de saída (placa ou ticket) e trilha de auditoria do DynamoDB.
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
    ├── App.tsx               # rotas: / · /cancela-entrada · /pagamento · /cancela-saida · /auditoria · 404 (/entrada e /saida redirecionam)
    ├── components/           # Layout, SpotsCounter, StatusBadge, TicketCard, Alert, Spinner
    ├── pages/                # DashboardPage, EntryPage, PaymentPage, ExitBarrierPage, AuditPage, NotFoundPage
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
| Painel (`/`) | `GET /spots/available` (polling 5 s), `GET /sessions?status=ALL` (carros no pátio: `PARKED` e `PAID`, polling 15 s) |
| Cancela de entrada (`/cancela-entrada`) | `POST /entries` (multipart `photo`; 409 = pátio lotado), `GET /sessions?status=ALL` (enquanto `plate_status` for `PENDING`: 2 s no primeiro minuto, depois 15 s até 12 min; erros transitórios mantêm a consulta; a sessão é localizada pelo ID na lista) |
| Caixa (`/pagamento`) | `GET /sessions?status=PARKED` (polling 15 s), `GET /sessions/{id}/photo` (miniatura em `<img>`), `POST /exits/{id}/pay` |
| Cancela de saída (`/cancela-saida`) | `POST /barrier/exit/plate`, `POST /barrier/exit/ticket`; `GET /sessions?status=ALL` só para resolver um ticket digitado pelos 8 primeiros caracteres |
| Auditoria (`/auditoria`) | `GET /audit` (50 eventos mais recentes) |

**O ticket identifica o carro** (`docs/DECISOES.md`, D20). A sessão chega do `POST /entries` já `PARKED`; `plate_status` (`PENDING`, `READ`, `UNREADABLE`, `MANUAL`) diz só como foi a leitura da placa. Nenhuma tela pode tratar sessão sem placa como erro, pedir a placa antes de cobrar ou mandar o motorista procurar o operador: o texto é sempre "o ticket é válido". `PlateStatusBadge` não usa vermelho por isso.

Na Auditoria, a placa e o resultado da leitura vêm da sessão (`GET /sessions?status=ALL`), pois os eventos são imutáveis: `ENTRY` é gravado antes da leitura e `EXIT_PAYMENT` não traz placa. A entrada aparece sempre como "Estacionado", com "sem placa: <motivo>" ao lado quando a foto não deu placa. `OCR_FAILED` ("Placa não lida") e `POISON_MESSAGE` aparecem destacados, com o motivo do worker traduzido em `src/utils/audit.ts` e o filtro "Somente falhas". O link "foto" só aparece para sessões que ainda existem no banco. A trilha guarda eventos anteriores a essa mudança (`status` `PROCESSING`/`FAILED`, ação `ENTRY_FAILED`); `legacyStatusLabels` os mantém legíveis, não remova.

**Duas cancelas.** Pagar não libera a vaga: o recibo do caixa diz "Siga para a cancela de saída" e mostra `paid_at`; a vaga volta quando a cancela de saída abre (`EXITED`). A cancela de saída começa pela placa (simula a câmera) e oferece "Digitar o ticket de papel". Ela reage ao `code` do erro (`ApiError.code`): `payment_pending` → "Pague no caixa", `already_exited` → "Este veículo já saiu", 404 → placa não reconhecida (oferece o ticket) ou ticket não encontrado. O ticket aceita os 8 caracteres em destaque no ticket impresso: a página resolve o prefixo pelo `GET /sessions?status=ALL` e só segue com uma sessão única, porque a API exige o ID completo.

No Caixa, cada linha tem a miniatura da foto de entrada, **Pagar**, **Informar placa** (opcional, sessão sem placa) ou **Corrigir placa** (leitura errada) e **Excluir**, com confirmação. A placa é validada no navegador (`isValidPlate`, mesma regra da API: formatos dos 4 países do Mercosul) e enviada normalizada via `PATCH /sessions/{id}`; a exclusão usa `DELETE /sessions/{id}`. Na Auditoria, `PLATE_CORRECTION` mostra a placa nova e a anterior, e `SESSION_DELETE` o status que a sessão tinha.

As listagens da API são arrays crus e vêm como `null` quando vazias; `src/services/api.ts` normaliza para `[]`. A API não expõe `GET /sessions/{id}`. O valor a pagar vem em `amount_due` de cada sessão `PARKED` de `GET /sessions` (atualizado a cada 15 s) e aparece na coluna "Valor a pagar" e no botão de confirmar; o valor realmente cobrado está no recibo (`amount_paid` de `POST /exits/{id}/pay`). Pagamento de sessão fora de `PARKED` (já paga) retorna 409. Na Auditoria, `EXIT_PAYMENT` aparece como "Pagamento" e `EXIT_BARRIER_OPENED` como "Saída pela cancela" ("Cancela aberta pela placa" ou "pelo ticket"). A URL da foto vem de `sessionPhotoUrl` em `src/services/api.ts`.

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
- O frontend nunca calcula tarifa: os valores exibidos são o `amount_due` (antes de pagar) e o `amount_paid` (recibo) devolvidos pela API, calculados em `api/internal/service/pricing.go` (D23).
- Sem animações além do `animate-spin` do `Spinner`.
- DoD: `npm run lint`, `npm run test -- --run` e `npm run build` sem erros nem warnings.

## Changelog
- 2026-11-18: Caixa mostra o valor a pagar (`amount_due`) na linha e no botão "Confirmar pagamento de R$ ..." (D23).
- 2026-11-17: Duas cancelas (fase 4 da spec `api-rabbitmq-exit-barrier`): `ExitBarrierPage` em `/cancela-saida`, rotas `/cancela-entrada` e `/pagamento` (as antigas redirecionam), caixa sem liberar a vaga, painel com os carros no pátio, `SessionStatus` com `EXITED`, `paid_at`, `EXIT_BARRIER_OPENED` e `ApiError.code`.
- 2026-11-16: Ticket válido na emissão: `SessionStatus` só `PARKED`/`PAID`, novo `PlateStatus`, totem sem "procure o operador" e com aviso de pátio lotado, Caixa lista só `PARKED` com a foto de entrada, Auditoria com link para a foto.
- 2026-10-03: Caixa com placa digitada/corrigida e exclusão de registro; Auditoria com `PLATE_CORRECTION` e `SESSION_DELETE`.
- 2026-10-03: nginx com `upstream` + keepalive para a API (estabilidade sob carga no ALB); build no `$BUILDPLATFORM`.
- 2026-10-03: Implementação do frontend (painel, totem, saída, auditoria), cliente HTTP tipado, `usePolling`, testes com Vitest e container nginx.
- 2026-09-15: Criação do AGENTS.md do Frontend Web.
