# AGENTS.md - API Backend (Go)

## Project Overview
- API RESTful em Go que gerencia o ciclo de vida de veículos e vagas no estacionamento.
- Implementa os endpoints:
  - `POST /entries`: Recebe foto, reserva a vaga no Redis (lotado → 409), salva no storage S3 (RustFS), cria sessão no Postgres com `status` `PARKED` e `plate_status` `PENDING`, publica na fila RabbitMQ `ocr-processamento-fila` e grava auditoria no DynamoDB.
  - `GET /spots/available`: Retorna contagem de vagas lendo diretamente da memória do Redis (`spots:available`).
  - `POST /exits/{id}/pay`: Cobra a tarifa fixa de uma sessão `PARKED`: `status` `PAID`, `paid_at` e `amount_paid`. **Não** mexe no contador (o carro continua no pátio). Auditoria `EXIT_PAYMENT`; 409 se não estiver `PARKED`.
  - `POST /barrier/exit/ticket` (`{"ticket_id"}`) e `POST /barrier/exit/plate` (`{"plate"}`): cancela de saída. Abre para uma sessão `PAID`: `status` `EXITED`, `exited_at`, `INCR spots:available` e auditoria `EXIT_BARRIER_OPENED` (`status`, `method` `ticket`|`plate`, `license_plate`). Erros com `{"error", "code"}`: `payment_pending` (409), `already_exited` (409), `session_not_found`/`plate_not_found` (404), `invalid_ticket`/`invalid_plate`/`invalid_body` (400).
  - `GET /sessions?status=PARKED|PAID|EXITED|ALL&plate=...&plate_status=...`: Lista sessões (array cru, `null` se vazio; `status` padrão `PARKED`).
  - `GET /sessions/{id}/photo`: Foto de entrada, lida do storage.
  - `PATCH /sessions/{id}`: Placa digitada/corrigida pelo caixa (`plate_status` → `MANUAL`).
  - `DELETE /sessions/{id}`: Exclui a sessão e a foto no storage.
  - `GET /audit`: 50 eventos mais recentes do DynamoDB.
  - `GET /health`: Healthcheck simples da aplicação (`{"status":"UP"}`).

## Tech Stack
- **Linguagem**: Go 1.27
- **Injeção de Dependências**: `go.uber.org/fx` (Uber Fx) com gerenciamento de ciclo de vida (`fx.Lifecycle`)
- **AWS SDK**: `aws-sdk-go-v2` (S3, DynamoDB)
- **Fila**: `github.com/rabbitmq/amqp091-go` (RabbitMQ)
- **Bancos**: PostgreSQL driver (`lib/pq`), Redis client (`go-redis/v9`)
- **Migrações**: `golang-migrate/migrate` via `embed.FS` nativo

## File Structure
```text
api/
├── Dockerfile                  # Multi-stage build estático minimalista (scratch image)
├── .dockerignore
├── cmd/
│   └── api/
│       └── main.go                     # Entrypoint e injeção de dependências
├── internal/
│   ├── config/                         # Carregamento e validação de env vars
│   │   ├── config.go
│   │   └── config_test.go
│   ├── handler/                        # Handlers HTTP REST e rotas
│   │   ├── handler.go
│   │   └── handler_test.go             # Testes unitários do transport HTTP
│   ├── model/                          # Entidades de domínio (Session)
│   │   └── session.go
│   ├── repository/                     # Adaptadores de banco e serviços AWS por tipo
│   │   ├── interfaces.go               # Contratos das portas de persistência
│   │   ├── postgres.go                 # PostgresSessionRepo (RDS)
│   │   ├── redis.go                    # RedisSpotsRepo (ElastiCache)
│   │   ├── s3.go                       # S3BlobStorage (S3)
│   │   ├── rabbitmq.go                 # RabbitMQEventPublisher (RabbitMQ)
│   │   ├── dynamodb.go                 # DynamoDBAuditLogger (DynamoDB)
│   │   ├── migrate.go                  # Migrações via embed.FS (golang-migrate)
│   │   ├── migrations/                 # Scripts SQL de migração
│   │   │   ├── 000001_create_sessions_table.{up,down}.sql
│   │   │   └── 000002_add_plate_status.{up,down}.sql
│   │   └── repository_test.go          # Testes de integração (Testcontainers Postgres & Redis)
│   └── service/                        # Regras de negócio e orquestração
│       ├── parking.go
│       └── parking_test.go             # Testes unitários de regras de negócio
├── go.mod
└── go.sum
```

## Common Commands
- `task dev:api`: Executa a API localmente (`go run ./cmd/api`)
- `task test:api`: Executa os testes unitários (`go test -v ./...`)
- `task build:api`: Compila o binário para `bin/api`
- `go test -v -run TestCreateEntry_Success ./internal/handler/...`: Teste específico

## Architecture Conventions
- **O ticket identifica o carro** (`docs/DECISOES.md`, D20 e D21): `status` é `PARKED` (desde a entrada) -> `PAID` (após a cobrança, ainda no pátio) -> `EXITED` (passou pela cancela de saída). `PARKED` e `PAID` ocupam vaga (`Session.Occupying()`). A leitura da placa fica em `plate_status`: `PENDING` -> `READ` | `UNREADABLE` (worker) ou `MANUAL` (caixa). Nada na API pode condicionar pagamento ou saída à placa. Constantes em `internal/model/session.go`.
- **Entrada (`CreateEntry`)**: a ordem é reservar a vaga → upload → `INSERT` → publicar. Falha no upload ou no `INSERT` devolve a vaga (`Increment`). Falha ao publicar **não** falha a entrada: grava `plate_status = UNREADABLE` e a auditoria `ENTRY` sai com `ocr_queued: false` e `queue_error` (o evento `ENTRY_FAILED` não é mais emitido).
- **Reserva de vaga (`SpotsRepository.Reserve`)**: script Lua que distingue vaga reservada, lotado (`false`) e chave ausente (erro). O `Decrement` não serve para isso: devolve `0` tanto ao ocupar a última vaga quanto quando já estava lotado. Com erro (chave ausente ou Redis fora), `reserveSpot` decide pelo Postgres (`total - CountActive`) e regrava o contador já contando o carro.
- **Sessões ativas (`CountActive`)**: `PARKED` e `PAID`, porque a vaga só volta na cancela de saída.
- **Cancela de saída (`openBarrier`)**: `MarkAsExited` é um `UPDATE ... WHERE status = 'PAID' RETURNING`; só a requisição que mudou a linha faz o `Increment`, então pedidos simultâneos para o mesmo carro liberam a vaga uma vez (o outro recebe `already_exited`). Se o Redis estiver fora, a saída abre mesmo assim e o contador é recalculado na próxima leitura.
- **Saída pela placa (`FindOccupyingByPlate`)**: a placa é normalizada como no caixa e só casa com sessões no pátio cuja placa é confiável (`plate_status` `READ` ou `MANUAL`), nunca `PENDING`/`UNREADABLE`. Entre várias `PAID` com a mesma placa abre a de `paid_at` mais antigo; se só houver `PARKED`, `payment_pending`. Sem OCR nem foto na rota: o ticket é sempre a alternativa.
- **Migração 000003**: coluna `paid_at`; sessões `PAID` antigas viram `EXITED` (pela regra anterior já tinham saído e devolvido a vaga), com `paid_at = exited_at`. Índice parcial em `license_plate` para os carros no pátio. O `down` volta tudo para `PAID`: depois dele, apague `spots:available`.
- **Foto (`GET /sessions/{id}/photo`)**: 404 se a sessão ou o objeto não existirem (`repository.ErrBlobNotFound`). O `Content-Type` gravado vem do cliente que fez o upload: só `image/jpeg|png|webp|gif` são devolvidos como imagem; qualquer outro vira `application/octet-stream` com `Content-Disposition: attachment`. Sempre `X-Content-Type-Options: nosniff`.
- **Migração 000002 em banco com dados**: converte `PROCESSING`/`FAILED` em `PARKED`. Essas sessões não ocupavam vaga no contador: apague a chave `spots:available` depois de migrar, que a API recalcula.
- **Logs no DynamoDB**: Criar registros com chave única contendo timestamp, ação (`ENTRY`, `EXIT_PAYMENT`, `EXIT_BARRIER_OPENED`, `PLATE_CORRECTION`, `SESSION_DELETE`) e payload resumido.
- **Redis & Anti-Overbooking**: Chave `spots:available` lida em alta frequência. Se o Redis falhar ou a chave sumir (reboot no meio do dia), **a API nunca assume o default 50**: consulta o banco relacional (`totalSpots - count(ativas)`), responde a contagem precisa e reidrata o Redis com `SET spots:available`.
- **Um endpoint por serviço**: `S3_ENDPOINT_URL` (path-style, RustFS) e `DYNAMODB_ENDPOINT_URL` (DynamoDB Local); vazio = AWS real. As chaves `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` não têm default: sem elas o SDK usa a cadeia padrão de credenciais. `AWS_SESSION_TOKEN` é aceito para credenciais temporárias. `AWS_ENDPOINT_URL` e `SQS_QUEUE_URL` não são mais lidos. Com `S3_ENDPOINT_URL` definido, a API cria o bucket na subida se ele não existir.
- **Fila (RabbitMQ)**: `RABBITMQ_URL` e `RABBITMQ_QUEUE_NAME` (padrão `ocr-processamento-fila`). O publisher conecta na primeira publicação e reconecta após falha, então a API sobe e atende com o broker fora do ar. Ele só **confere** a fila (declaração passiva): a topologia é do `infra/rabbitmq/definitions.json`. Publicação persistente, com confirmação do broker, limitada a 5 s. Falha (broker fora, fila ausente, `RABBITMQ_URL` vazio) não derruba a entrada: a sessão fica `PARKED` com `plate_status` `UNREADABLE` e a auditoria `ENTRY` registra `ocr_queued: false`. O `session_id` da mensagem tem 32 caracteres hexadecimais; o worker recusa outro formato.
- **RDS exige SSL**: na AWS use `DATABASE_URL=...?sslmode=require` (o PostgreSQL 16 do RDS recusa `sslmode=disable`).
- **Migrations no boot**: se o Postgres ainda não responder, a API sobe mesmo assim e tenta as migrations a cada 5 s em background (advisory lock do `golang-migrate` torna seguro com várias instâncias do ASG).
- **Placa digitada no caixa (`PATCH /sessions/{id}`, body `{"license_plate": "ABC1D23"}`)**: aceita os formatos dos 4 países do Mercosul: `ABC1D23`, `ABC1234`, `AB123CD`, `ABC123`, `ABCD123` (hífen/espaços são removidos; 400 se inválida; `docs/DECISOES.md` D8). Para sessão no pátio, `PARKED` ou `PAID` (um carro pago ainda pode ganhar placa para sair por ela); grava `plate_status = MANUAL` e não mexe no contador. 409 para `EXITED`. Auditoria `PLATE_CORRECTION` com `license_plate`, `previous_plate`, `previous_plate_status`, `status`.
- **Exclusão (`DELETE /sessions/{id}`)**: remove a sessão do RDS e a foto do S3 (best effort, com log se falhar), faz `INCR` se o carro estava no pátio (`PARKED` ou `PAID`) e audita `SESSION_DELETE` (`status`, `license_plate`, `s3_photo_key`). Devolve a sessão excluída (200) ou 404.
- **Contador nunca é criado nem fica negativo pela API**: `Increment`/`Decrement`/`Reserve` rodam em Lua só se a chave existir; `Decrement` e `Reserve` param em 0.
- **Padrão de Pacotes**: Nenhuma lógica de negócio dentro de `cmd/api`. Código privado mantido em `internal/` seguindo as convenções padrão do Go.

## Changelog
- 2026-11-17: Cancela de saída (fases 2 e 3 da spec): status `EXITED`, `paid_at` (migração 000003), pagamento sem `Increment`, `POST /barrier/exit/ticket` e `/plate` com códigos de erro, correção de placa e exclusão tratando `PAID` como carro no pátio.
- 2026-11-16: Fase 1 do TP2 na API (`.kiro/specs/api-rabbitmq-exit-barrier`): publica no RabbitMQ (`rabbitmq.go`, sem SQS), endpoints separados para S3 e DynamoDB, bucket criado na subida em storage local.
- 2026-11-16: Sessão `PARKED` e vaga reservada na entrada (409 se lotado), `plate_status`, migração 000002, `GET /sessions/{id}/photo`, filtro `plate_status`; `PROCESSING`/`FAILED` e `ENTRY_FAILED` removidos.
- 2026-10-03: `PATCH /sessions/{id}` (placa digitada/corrigida no caixa) e `DELETE /sessions/{id}` (CRUD completo, com remoção da foto no S3); `Decrement` seguro em Lua; CORS com PATCH/DELETE.
- 2026-10-03: Config sem defaults do Floci para endpoint/credenciais (instance profile na AWS), `AWS_SESSION_TOKEN`, retry das migrations e build do Dockerfile no `$BUILDPLATFORM` (cross-compile para `linux/amd64`).
- 2026-09-21: Reestruturação da API no padrão Go (`cmd/` e `internal/`), migrações via `golang-migrate`, TDD com 100% dos testes passando e endpoints padronizados em inglês.
- 2026-09-15: Criação inicial do AGENTS.md da API.
