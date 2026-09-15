# AGENTS.md - Infraestrutura (Terraform & Docker)

## Project Overview

- Gerencia a infraestrutura como código (IaC) via Terraform para os 6 serviços centrais da AWS (S3, SNS/SQS, DynamoDB, RDS, ElastiCache) e provê o ambiente de emulação local via Docker Compose com Floci.
- RDS (PostgreSQL) e ElastiCache (Redis) são provisionados via Terraform gerenciados pelo Floci localmente e pela AWS Academy em nuvem, eliminando containers dedicados de banco no Compose.
- Prepara a infraestrutura para a **Parte 2** com Application Load Balancer e Auto Scaling Group (1 a 3 réplicas).

## Tech Stack

- **IaC**: Terraform 1.15
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

- `task tf:init`: Inicializa provedores Terraform
- `task tf:apply:local`: Provisiona recursos no Floci local
- `task tf:apply:aws`: Provisiona recursos na AWS Academy
- `task tf:destroy:aws`: Destrói recursos na AWS Academy (preservar créditos)

## Architecture Conventions

- A variável `use_localstack` (default `true`) controla se os endpoints apontam para `http://localhost:4566` ou para os serviços gerenciados da AWS.
- Configurações da Parte 2 (Auto Scaling):
  - CPU > 70% por > 1 min: dispara scale-out (+1 instância, máx 3).
  - CPU < 25% por > 1 min: dispara scale-in (-1 instância, mín 1).

## Changelog

- 2026-09-20: Criação do AGENTS.md de Infraestrutura com parâmetros de elasticidade.
