# AGENTS.md - API Backend (Go)

## Project Overview
- API RESTful em Go que gerencia o ciclo de vida de veículos e vagas no estacionamento.
- Implementa os endpoints:
  - `POST /entradas`: Recebe foto, salva no S3, cria sessão no RDS com status `PROCESSANDO`, grava auditoria no DynamoDB e publica no SNS.
  - `GET /vagas/disponiveis`: Retorna mapa e contagem de vagas lendo diretamente da memória do Redis.
  - `POST /saidas/:id/pagar`: Calcula permanência/tarifa no RDS, atualiza status para `PAGO`, incrementa vagas no Redis e grava auditoria no DynamoDB.

## Tech Stack
- **Linguagem**: Go 1.27
- **AWS SDK**: `aws-sdk-go-v2` (S3, SNS, DynamoDB)
- **Bancos**: PostgreSQL driver (`pgx` ou `lib/pq`), Redis client (`go-redis/v9`)

## File Structure
```text
api/
├── main.go                 # Inicialização de rotas e injeção de dependências
├── internal/
│   ├── handler/            # Handlers HTTP (entradas, vagas, saidas)
│   ├── service/            # Regras de negócio, cálculo de tarifas e orquestração
│   ├── repository/         # Acesso ao RDS, ElastiCache, DynamoDB e S3
│   └── model/              # Estruturas de dados (Veiculo, Sessao, LogAuditoria)
└── go.mod
```

## Common Commands
- `task dev:api`: Executa a API localmente
- `go test ./...`: Roda os testes unitários e de integração
- `go test -v -run TestEntradaHandler ./internal/handler/...`: Teste específico

## Architecture Conventions
- **Status da Sessão**: `PROCESSANDO` (na entrada) -> `ESTACIONADO` (definido pelo worker após OCR) -> `PAGO` (após cobrança na saída).
- **Logs no DynamoDB**: Criar registros com chave única contendo timestamp, ação (`ENTRADA`, `SAIDA_PAGAMENTO`) e payload resumido.
- **Redis**: Chave `vagas:disponiveis` operada via comandos atômicos (`INCR`, `DECR`, `GET`).

## Changelog
- 2026-09-15: Criação do AGENTS.md da API com endpoints e ciclo de vida detalhado.
