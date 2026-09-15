# AGENTS.md

## Project Overview
- **Objetivo**: Sistema de Gestão de Vagas de Estacionamento integrando 6 serviços centrais da AWS (EC2, S3, RDS, ElastiCache, DynamoDB, SNS/SQS) com auto-recuperação e elasticidade horizontal (ALB + Auto Scaling Group de 1 a 3 instâncias).
- **Contexto**: Trabalho Prático 1 da disciplina de Desenvolvimento de Software para Nuvem (UFC - Profs. Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta).
- **Estágio**: Fase inicial de implementação com suporte a emulação local via MiniStack/Docker Compose e provisionamento real na AWS Academy.

## Mapeamento de Serviços AWS
1. **EC2**: Instâncias (`t2.micro`/`t3.micro`) executando a API e o painel Web servido via container Docker, sob Application Load Balancer e Auto Scaling Group (1 a 3 réplicas).
2. **Amazon RDS**: PostgreSQL (`db.t3.micro`, Single-AZ) para persistência transacional de vagas, sessões de permanência (`PROCESSANDO`, `ESTACIONADO`, `PAGO`) e cálculo de tarifas.
3. **Amazon S3**: Bucket para arquivos binários (fotos dos veículos capturadas na entrada).
4. **Amazon ElastiCache**: Cluster Redis nó único (`cache.t3.micro`) mantendo em memória o mapa de vagas disponíveis para leituras de alta frequência e baixa latência.
5. **Amazon DynamoDB**: Tabela em modo *Pay-Per-Request* para trilha de auditoria e log imutável de todas as ações de CRUD (`ENTRADA`, `PROCESSAMENTO_OCR`, `SAIDA_PAGAMENTO`).
6. **Amazon SNS + SQS**: Desacoplamento assíncrono: API publica evento no SNS (`novo-veiculo-topico`), repassado para o SQS (`ocr-processamento-fila`); o worker Python consome a fila para redimensionar a foto e extrair a placa via OCR (Tesseract).

## Pipeline da Solução
```text
[Totem/Web: React + Vite] ──> POST /entradas (com foto)
    ├── 1. API (Go) salva a imagem original no S3
    ├── 2. Registra sessão preliminar no RDS (status: "PROCESSANDO")
    ├── 3. Emite evento no SNS ──> Fila SQS
    ├── 4. Grava log de auditoria no DynamoDB (ação: "ENTRADA")
    └── 5. Retorna ticket provisório ao usuário

[Worker Assíncrono: Python]
    ├── Consome mensagens da fila SQS
    ├── Baixa a foto do S3, redimensiona a imagem e roda o OCR (Tesseract)
    ├── Atualiza a sessão no RDS com a placa identificada e status "ESTACIONADO"
    ├── Decrementa o contador de vagas no ElastiCache (Redis)
    └── Grava log de auditoria da placa processada no DynamoDB (ação: "PROCESSAMENTO_OCR")

[Consulta de Vagas] ──> GET /vagas/disponiveis
    └── Responde instantaneamente direto da memória do ElastiCache

[Saída/Pagamento] ──> POST /saidas/:id/pagar
    ├── Calcula valor com base no tempo de permanência no RDS
    ├── Atualiza registro para "PAGO" e libera a vaga no RDS
    ├── Incrementa a contagem de vagas no ElastiCache
    └── Grava log de auditoria da finalização no DynamoDB (ação: "SAIDA_PAGAMENTO")
```

## Tech Stack
- **Orquestração e Ferramentas**: Taskfile (`task 3.53`), mise (Go 1.27, Python 3.14, Node 26, Terraform 1.15, UV 0.12)
- **API Backend**: Go 1.27 (REST, AWS SDK v2, drivers Postgres e Redis)
- **Worker Assíncrono**: Python 3.14 + `uv` (Boto3, Tesseract OCR, Pillow/OpenCV, driver PostgreSQL)
- **Frontend**: React + Vite (Node 26)
- **Infraestrutura**: Terraform 1.15 + Docker Compose (MiniStack 4566, PostgreSQL 16 Alpine 5432, Redis Alpine 6379)

## File Structure
```text
.
├── Taskfile.yaml           # Automação de tarefas (infra, dev, build)
├── mise.toml               # Configuração de versões das ferramentas
├── api/                    # API REST em Go
├── worker/                 # Worker assíncrono OCR em Python
├── web/                    # Frontend React + Vite
├── infra/                  # Terraform e Docker Compose
└── docs/                   # Especificações da disciplina
```

- `api/`: API REST responsável pelas rotas `/entradas`, `/vagas/disponiveis` e `/saidas/:id/pagar`.
- `worker/`: Worker consumidor da fila SQS para processar a foto no S3, rodar OCR e atualizar o RDS/Redis.
- `web/`: Interface para o operador e totem de entrada.
- `infra/`: Definições IaC (Terraform) e ambiente local (Docker Compose com MiniStack).

## Common Commands
- **Ambiente Local Completo**:
  - `task bootstrap:local`: Sobe containers e provisiona recursos no MiniStack
  - `task infra:up` / `task infra:down` / `task infra:logs`
- **Terraform**:
  - `task tf:init`: Inicializa diretório Terraform
  - `task tf:apply:local`: Provisiona no MiniStack (`use_localstack=true`)
  - `task tf:apply:aws`: Provisiona recursos na AWS Academy
  - `task tf:destroy:aws`: Destrói recursos na AWS Academy
- **Execução dos Serviços**:
  - `task dev:api`: Executa a API Go
  - `task dev:worker`: Executa o Worker Python
  - `task dev:web`: Inicia servidor local Vite
  - `task build:web`: Gera bundle estático do frontend

## Environment / Setup
Configurar variáveis locais no `.env`:
- `AWS_ENDPOINT_URL=http://localhost:4566`
- `DATABASE_URL=postgres://app_user:app_password@localhost:5432/estacionamento?sslmode=disable`
- `REDIS_URL=localhost:6379`
- `AWS_DEFAULT_REGION=us-east-1`

## Architecture Conventions
- **Desacoplamento Assíncrono**: O upload e entrada do veículo **nunca** executam OCR de forma síncrona. A API grava no S3, publica no SNS e libera a requisição HTTP.
- **Auditoria Imutável**: Toda alteração de estado registra evento no DynamoDB (`ENTRADA`, `PROCESSAMENTO_OCR`, `SAIDA_PAGAMENTO`).
- **Cache de Alta Frequência**: A consulta de vagas disponíveis bate exclusivamente no ElastiCache (Redis).
- **Consistência de Contadores**: O decremento ocorre no término do OCR e o incremento ocorre na liberação da vaga após pagamento.

## Known Gotchas
- **Créditos AWS Academy**: Sempre executar `task tf:destroy:aws` ao encerrar os testes em nuvem.
- **Ordem de Inicialização**: Executar `task bootstrap:local` antes de rodar API ou Worker localmente.

## Changelog
- 2026-09-15: Criação do AGENTS.md raiz com pipeline detalhado dos 6 serviços AWS.
