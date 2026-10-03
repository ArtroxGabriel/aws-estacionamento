# AGENTS.md - API Backend (Go)

## Project Overview
- API RESTful em Go que gerencia o ciclo de vida de veículos e vagas no estacionamento.
- Implementa os endpoints:
  - `POST /entries`: Recebe foto, salva no S3, cria sessão no RDS com status `PROCESSING`, grava auditoria no DynamoDB e publica na fila SQS.
  - `GET /spots/available`: Retorna contagem de vagas lendo diretamente da memória do Redis (`spots:available`).
  - `POST /exits/{id}/pay`: Registra saída com tarifa fixa, atualiza status para `PAID` no RDS, incrementa vagas no Redis e grava auditoria no DynamoDB.
  - `GET /health`: Healthcheck simples da aplicação (`{"status":"UP"}`).

## Tech Stack
- **Linguagem**: Go 1.27
- **Injeção de Dependências**: `go.uber.org/fx` (Uber Fx) com gerenciamento de ciclo de vida (`fx.Lifecycle`)
- **AWS SDK**: `aws-sdk-go-v2` (S3, SQS, DynamoDB)
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
│   │   ├── sqs.go                      # SQSEventPublisher (SQS)
│   │   ├── dynamodb.go                 # DynamoDBAuditLogger (DynamoDB)
│   │   ├── migrate.go                  # Migrações via embed.FS (golang-migrate)
│   │   ├── migrations/                 # Scripts SQL de migração
│   │   │   ├── 000001_create_sessions_table.up.sql
│   │   │   └── 000001_create_sessions_table.down.sql
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
- **Status da Sessão**: `PROCESSING` (na entrada) -> `PARKED` (definido pelo worker após OCR) -> `PAID` (após cobrança na saída).
- **Logs no DynamoDB**: Criar registros com chave única contendo timestamp, ação (`ENTRY`, `EXIT_PAYMENT`) e payload resumido.
- **Redis & Anti-Overbooking**: Chave `spots:available` lida em alta frequência. Se o Redis falhar ou a chave sumir (reboot no meio do dia), **a API nunca assume o default 50**: consulta o banco relacional (`totalSpots - count(ativas)`), responde a contagem precisa e reidrata o Redis com `SET spots:available`.
- **AWS real x Floci**: `AWS_ENDPOINT_URL` e as chaves `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` não têm default. Sem elas o SDK usa os endpoints reais e a cadeia padrão de credenciais (instance profile na EC2); localmente o `.env`/`task dev:api` apontam para o Floci. `AWS_SESSION_TOKEN` é aceito para credenciais temporárias. Path-style no S3 só com endpoint customizado.
- **RDS exige SSL**: na AWS use `DATABASE_URL=...?sslmode=require` (o PostgreSQL 16 do RDS recusa `sslmode=disable`).
- **Migrations no boot**: se o Postgres ainda não responder, a API sobe mesmo assim e tenta as migrations a cada 5 s em background (advisory lock do `golang-migrate` torna seguro com várias instâncias do ASG).
- **Padrão de Pacotes**: Nenhuma lógica de negócio dentro de `cmd/api`. Código privado mantido em `internal/` seguindo as convenções padrão do Go.

## Changelog
- 2026-10-03: Config sem defaults do Floci para endpoint/credenciais (instance profile na AWS), `AWS_SESSION_TOKEN`, retry das migrations e build do Dockerfile no `$BUILDPLATFORM` (cross-compile para `linux/amd64`).
- 2026-09-21: Reestruturação da API no padrão Go (`cmd/` e `internal/`), migrações via `golang-migrate`, TDD com 100% dos testes passando e endpoints padronizados em inglês.
- 2026-09-15: Criação inicial do AGENTS.md da API.
