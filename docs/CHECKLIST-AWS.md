# Checklist de Deploy na AWS

> Atualizado em 2026-10-03, na branch `feat/aws-deploy`. Prazo de entrega: **10/10/2026 23h59** (código + vídeo da Parte 2).
> Como fazer o deploy: [DEPLOY-AWS.md](DEPLOY-AWS.md).

## Aplicação

- [x] API, worker e frontend completos. Testes passando: web 132, worker 101, API (unit + integração).
- [x] **API sem os defaults do Floci.** `AWS_ENDPOINT_URL` e as chaves AWS não têm mais fallback (`localhost:4566` / `mock_key`). Sem elas, o SDK usa os endpoints reais e o instance profile. `AWS_SESSION_TOKEN` agora é suportado.
- [x] **S3 em path-style só com endpoint customizado** (Floci). Na AWS real usa virtual-hosted.
- [x] **RDS com SSL.** O `user_data` monta `DATABASE_URL` com `sslmode=require` para a API e para o worker.
- [x] **Migrations com retry.** Se o Postgres não responder no boot, a API tenta de novo a cada 5 s em background.
- [x] **Dockerfiles** da API e do web compilam no `$BUILDPLATFORM`, o que permite cross-compile rápido para `linux/amd64` a partir de um Mac ARM.

## Infraestrutura (OpenTofu)

- [x] **Parte 2 completa** em `infra/autoscaling.tf`:
  - Launch Template: `t3.micro`, AL2023, detailed monitoring, IMDSv2 com hop limit 2.
  - ALB + Target Group com health check em `/api/health`.
  - ASG de 1 a 3 instâncias, com health check `ELB` e instance refresh.
  - Duas políticas `SimpleScaling`: `> 70 %` e `< 25 %`, com período de 60 s, 1 datapoint e cooldown de 60 s.
- [x] **Security groups** em `infra/network.tf`:
  - ALB aberto na porta 80.
  - Instâncias aceitam porta 80 só vindo do ALB.
  - RDS (5432) e Redis (6379) aceitam conexões só das instâncias.
- [x] **Subnets** filtradas pelas AZs que oferecem o tipo de instância (a us-east-1e não tem `t3.micro`).
- [x] **Imagens no ECR**: `task aws:images` faz o build e o push, e o `user_data` faz o pull com retry.
- [x] **IAM**: em conta própria cria a role `estacionamento-app` (S3/SQS/DynamoDB + ECR + SSM). Na Academy, usar `TF_VAR_instance_profile_name=LabInstanceProfile`.
- [x] **Estados separados**: Floci no workspace `default`, AWS no workspace `aws`. Uma precondition bloqueia a mistura.
- [x] **Bucket S3** com sufixo do account id, e `force_destroy` na AWS.
- [x] **Senha do RDS** gerada (`random_password`), RDS sem acesso público.
- [x] **Tasks da AWS** descartam o `AWS_ENDPOINT_URL` e as chaves `mock_key` que vêm do `.env`.
- [x] `tofu validate` ok. Plan local: 6 recursos, como antes. Plan AWS: 28 recursos.

## Falta fazer (depende de pessoas ou da conta)

- [ ] **Rodar `task deploy:aws`** e validar `/api/health` e o fluxo completo pelo ALB: entrada com foto → OCR → placa no painel → pagamento → auditoria.
- [ ] **Testar a carga**: `task load:aws` (plano B: `task stress:aws`). Confirmar 1 → 2 → 3 → 1.
- [ ] **Gravar o vídeo** (3–5 min, público). Roteiro em [DEPLOY-AWS.md](DEPLOY-AWS.md#vídeo-da-elasticidade) e no `docs/GOAL.md` §5.
- [ ] **`task tf:destroy:aws`** ao terminar.
- [ ] Preencher as matrículas no `README.md` (`Item2.2`, `Item2.3`, `Item2.4`).
- [ ] Fazer push da `main` (merge do frontend) e da `feat/aws-deploy`, e abrir o PR.
- [ ] **Opcional:** não existe *delete* de sessão (o CRUD está sem o D). Avaliar `DELETE /sessions/{id}` com auditoria.

## Riscos conhecidos e aceitos

- A senha do RDS fica no state local (`infra/terraform.tfstate.d/aws/`, fora do git) e no user data do launch template. Aceitável para um trabalho acadêmico com infraestrutura efêmera.
- As instâncias ficam em subnets públicas da VPC default (com IP público), para evitar o custo de NAT Gateway. A entrada é restrita pelos security groups.
- As instâncias `t3` usam créditos `unlimited` por padrão. Uma carga longa pode gerar um custo extra pequeno.
