# AGENTS.md - Infraestrutura (Terraform & Docker)

## Project Overview
- Gerencia a infraestrutura como código (IaC) via Terraform para os serviços da AWS e provê o ambiente de emulação local via Docker Compose (MiniStack, Postgres, Redis).
- Prepara a infraestrutura para a **Parte 2** com Application Load Balancer e Auto Scaling Group (1 a 3 réplicas).

## Tech Stack
- **IaC**: Terraform 1.15
- **Containers**: Docker Compose (`ministack:latest`, `postgres:16-alpine`, `redis:alpine`)

## File Structure
```text
infra/
├── docker-compose.yaml     # MiniStack (4566), PostgreSQL (5432), Redis (6379) com volumes persistentes
├── provider.tf             # Provider AWS com chaveamento para MiniStack e s3_use_path_style
├── main.tf                 # Buckets S3, Tópico SNS, Fila SQS e DynamoDB
├── outputs.tf              # ARNs e URLs dos recursos provisionados (S3, SNS, SQS, DynamoDB)
└── autoscaling.tf          # (Parte 2) Launch Template, ASG, ALB e CloudWatch Alarms
```

## Common Commands
- `task tf:init`: Inicializa provedores Terraform
- `task tf:apply:local`: Provisiona recursos no MiniStack local
- `task tf:apply:aws`: Provisiona recursos na AWS Academy
- `task tf:destroy:aws`: Destrói recursos na AWS Academy (preservar créditos)

## Architecture Conventions
- A variável `use_localstack` (default `true`) controla se os endpoints apontam para `http://localhost:4566` ou para os serviços gerenciados da AWS.
- Configurações da Parte 2 (Auto Scaling):
  - CPU > 70% por > 1 min: dispara scale-out (+1 instância, máx 3).
  - CPU < 25% por > 1 min: dispara scale-in (-1 instância, mín 1).

## Changelog
- 2026-09-15: Criação do AGENTS.md de Infraestrutura com parâmetros de elasticidade.
