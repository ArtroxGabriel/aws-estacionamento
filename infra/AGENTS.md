# AGENTS.md - Infraestrutura (OpenTofu & Docker)

## Project Overview

- Gerencia a infraestrutura como código (IaC) via OpenTofu para os 6 serviços centrais da AWS (S3, SNS/SQS, DynamoDB, RDS, ElastiCache) e provê o ambiente de emulação local via Docker Compose com Floci.
- RDS (PostgreSQL) e ElastiCache (Redis) são provisionados via OpenTofu gerenciados pelo Floci localmente e pela AWS Academy em nuvem, eliminando containers dedicados de banco no Compose.
- Prepara a infraestrutura para a **Parte 2** com Application Load Balancer e Auto Scaling Group (1 a 3 réplicas).

## Tech Stack

- **IaC**: OpenTofu (latest / 1.12+)
- **Containers**: Docker Compose (`floci/floci:latest` com Docker socket para instanciar RDS PostgreSQL e ElastiCache Redis sob demanda)

## File Structure

```text
infra/
├── docker-compose.yaml     # Floci (4566, proxy RDS 5432-5440, proxy ElastiCache 6379-6399)
├── provider.tf             # Provider AWS com chaveamento para Floci (S3, SNS, SQS, DynamoDB, RDS, ElastiCache)
├── main.tf                 # Buckets S3, Tópico SNS, Fila SQS, DynamoDB, RDS PostgreSQL e ElastiCache Redis
├── outputs.tf              # ARNs e URLs dos recursos provisionados
└── autoscaling.tf          # (Parte 2) Launch Template, ASG, ALB e CloudWatch Alarms
```

## Common Commands

- `task tf:init`: Inicializa provedores OpenTofu
- `task tf:plan:local`: Gera plano de execução do OpenTofu apontando para o Floci
- `task tf:plan:aws`: Gera plano de execução do OpenTofu para o AWS Academy
- `task tf:apply:local`: Provisiona recursos no Floci local via OpenTofu
- `task tf:apply:aws`: Provisiona recursos na AWS Academy via OpenTofu
- `task tf:destroy:aws`: Destrói recursos na AWS Academy (preservar créditos)
- `task tf:clean`: Limpa cache e arquivos de estado locais do OpenTofu/Terraform (.terraform, .lock, .tfstate)

## Architecture Conventions

- A variável `use_localstack` (default `true`) controla se os endpoints apontam para `http://localhost:4566` ou para os serviços gerenciados da AWS.
- Configurações da Parte 2 (Auto Scaling):
  - CPU > 70% por > 1 min: dispara scale-out (+1 instância, máx 3).
  - CPU < 25% por > 1 min: dispara scale-in (-1 instância, mín 1).

## Changelog

- 2026-09-25: Migração da ferramenta de IaC de Terraform para OpenTofu (open-source MPL v2.0).
- 2026-09-20: Criação do AGENTS.md de Infraestrutura com parâmetros de elasticidade.
