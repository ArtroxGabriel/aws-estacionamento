# AGENTS.md - Infraestrutura (OpenTofu & Docker)

## Project Overview

- Gerencia a infraestrutura como código (IaC) via OpenTofu para os 6 serviços centrais da AWS (S3, SQS, DynamoDB, RDS, ElastiCache, EC2) e provê o ambiente de emulação local via Docker Compose com Floci.
- RDS (PostgreSQL) e ElastiCache (Redis) são provisionados via OpenTofu gerenciados pelo Floci localmente e pela AWS Academy em nuvem, eliminando containers dedicados de banco no Compose.
- Prepara a infraestrutura para a **Parte 2** com Application Load Balancer e Auto Scaling Group (1 a 3 réplicas).

## Tech Stack

- **IaC**: OpenTofu (latest / 1.12+)
- **Containers**: Docker Compose (`floci/floci:latest` com Docker socket para instanciar RDS PostgreSQL e ElastiCache Redis sob demanda)

## File Structure

```text
infra/
├── docker-compose.yaml     # Floci (4566, proxy RDS 5432-5440, proxy ElastiCache 6379-6399)
├── provider.tf             # Provider AWS com chaveamento para Floci + variáveis (região, tipo de instância, instance profile)
├── main.tf                 # Buckets S3, Fila SQS + DLQ, DynamoDB, RDS PostgreSQL e ElastiCache Redis
├── network.tf              # (AWS) VPC default, subnets nas AZs que oferecem o tipo de instância e security groups
├── autoscaling.tf          # (AWS) ECR, IAM, Launch Template, ALB, ASG (1–3) e alarmes de CPU
├── templates/user_data.sh.tftpl  # Boot da EC2: Docker + pull do ECR + api/worker/web
├── outputs.tf              # ARNs, URLs, URL do ALB, nome do ASG e registry ECR
├── aws/                    # Root da AWS: usa infra/ como módulo, com state remoto no S3
└── bootstrap/              # Uma vez por conta: bucket do state, OIDC do GitHub e role de deploy
```

## Common Commands

- `task tf:init`: Inicializa provedores OpenTofu
- `task tf:plan:local`: Gera plano de execução do OpenTofu apontando para o Floci
- `task tf:plan:aws`: Gera plano de execução do OpenTofu para o AWS Academy
- `task tf:apply:local`: Provisiona recursos no Floci local via OpenTofu
- `task tf:apply:aws`: Provisiona recursos na AWS Academy via OpenTofu
- `task tf:destroy:aws`: Destrói recursos na AWS Academy (preservar créditos)
- `task aws:bootstrap`: Uma vez por conta (admin): bucket do state remoto, OIDC do GitHub e role de deploy
- `task tf:init:aws`: Inicializa `infra/aws` com o state remoto (as demais tasks `*:aws` já chamam)
- `task deploy:aws`: Deploy completo (ECR → build/push das imagens `linux/amd64` → `tofu apply` completo → outputs)
- `task aws:images` + `task aws:rollout`: Publica imagens novas e recria as instâncias do ASG
- `task load:aws` / `task stress:aws`: Carga HTTP no ALB (`hey`) / CPU 100% nas instâncias via SSM, para o vídeo
- `task tf:clean`: Limpa cache e arquivos de estado locais do OpenTofu/Terraform (.terraform, .lock, .tfstate)

## Architecture Conventions

- A variável `use_localstack` (default `true`) controla se os endpoints apontam para `http://localhost:4566` ou para os serviços gerenciados da AWS.
- **Estados separados por diretório**: `infra/` guarda só o state local do Floci. A AWS é gerenciada por `infra/aws`, que chama `infra/` como módulo, com state remoto em `s3://estacionamento-tofu-state-<account_id>` e lock nativo do S3 (`use_lockfile`). Esse state é compartilhado com o GitHub Actions. Uma precondition recusa `use_localstack=false` fora do `infra/aws` (variável `aws_root`).
- **Deploy pelo GitHub Actions**: `.github/workflows/deploy-aws.yml` (`workflow_dispatch`: apply/plan/destroy) assume a role `estacionamento-github-deploy` via OIDC; só o environment `aws` do repositório pode assumi-la. As imagens são tagueadas com o SHA do commit (`image_tag`), o que muda o launch template e dispara o instance refresh.
- Recursos exclusivos da AWS (rede, ECR, IAM, EC2/ALB/ASG, senha do RDS) usam `count = local.aws_count` e não existem no Floci.
- **IAM**: `instance_profile_name` vazio cria a role `estacionamento-app` (conta própria). Na AWS Academy, onde não se cria IAM, use `TF_VAR_instance_profile_name=LabInstanceProfile`.
- **Segredos**: senha do RDS gerada por `random_password` na AWS (fica no state local e no user data do launch template); RDS sem acesso público e portas 5432/6379 abertas só para o SG das instâncias.
- **Conntrack na t3.micro**: o `user_data` sobe `nf_conntrack_max` (de 7680 para 131072) e encurta o TIME_WAIT para 30 s. Sem isso, a ~150 req/s a tabela enche, o kernel descarta SYNs e o ALB derruba a instância (testado em 2026-10-03). O nginx também usa keepalive com a API.
- **Conta AWS nova**: o primeiro ASG da conta pode falhar porque a role `AWSServiceRoleForAutoScaling` ainda não propagou. Basta rodar o apply de novo.
- **Containers e IMDS**: launch template com IMDSv2 e hop limit 2, senão os containers não obtêm as credenciais do instance profile.
- **Tasks da AWS e o `.env`**: as tasks AWS descartam `AWS_ENDPOINT_URL` do Floci e as chaves `mock_key` herdadas do `.env` antes de chamar `tofu`/`aws`.
- Fila `ocr-processamento-fila`: `visibility_timeout_seconds = 300` e `redrive_policy` para `ocr-processamento-fila-dlq` com `maxReceiveCount = 3`. O `maxReceiveCount` deve ser igual a `MAX_RECEIVE_COUNT` em `worker/worker.py` (o worker audita a mensagem como `POISON_MESSAGE` na última entrega).
- Configurações da Parte 2 (Auto Scaling):
  - CPU > 70% por > 1 min: dispara scale-out (+1 instância, máx 3).
  - CPU < 25% por > 1 min: dispara scale-in (-1 instância, mín 1).
  - Implementado com `SimpleScaling` (cooldown 60 s) + alarmes `CPUUtilization` (Average, dimensão `AutoScalingGroupName`, período 60 s, 1 datapoint) e detailed monitoring no launch template. Não trocar por target tracking: os alarmes gerenciados usam 3/15 datapoints.
  - Health check do Target Group em `/api/health` (nginx → `GET /health` da API).

## Changelog

- 2026-10-03: Deploy via GitHub Actions com OIDC (`infra/bootstrap`), state da AWS remoto no S3 em `infra/aws` (substitui o workspace `aws`) e imagens tagueadas por commit.

- 2026-10-03: Parte 2 na AWS (ECR, IAM, SGs, launch template, ALB, ASG 1–3, alarmes de CPU), workspaces separados local/AWS, bucket com sufixo do account id, senha do RDS gerada e RDS privado.

- 2026-09-29: DLQ `ocr-processamento-fila-dlq` + redrive policy (`maxReceiveCount = 3`) e visibility timeout de 300 s na fila de OCR.
- 2026-09-25: Migração da ferramenta de IaC de Terraform para OpenTofu (open-source MPL v2.0).
- 2026-09-20: Criação do AGENTS.md de Infraestrutura com parâmetros de elasticidade.
