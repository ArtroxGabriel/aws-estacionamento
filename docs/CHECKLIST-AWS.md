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
- [x] **Estados separados**: Floci com state local em `infra/`; AWS em `infra/aws`, com state remoto no S3 (bootstrap) compartilhado com o GitHub Actions.
- [x] **Deploy pelo GitHub Actions com OIDC** (workflow *Deploy AWS*). Falta o dono do repositório criar o environment `aws` com a variável `AWS_ROLE_ARN`.
- [x] **Bucket S3** com sufixo do account id, e `force_destroy` na AWS.
- [x] **Senha do RDS** gerada (`random_password`), RDS sem acesso público.
- [x] **Tasks da AWS** descartam o `AWS_ENDPOINT_URL` e as chaves `mock_key` que vêm do `.env`.
- [x] `tofu validate` ok. Plan local: 6 recursos, como antes. Plan AWS: 28 recursos.

## Validado na AWS (2026-10-03)

- [x] `task deploy:aws`: 28 recursos criados. A instância fica saudável no Target Group em ~2 min.
- [x] Fluxo completo pelo ALB (`task smoke:aws`):
  - entrada com foto → S3 + RDS + SQS + DynamoDB;
  - o worker lê a placa `BRA2E19` em ~1 s → `PARKED` + vaga descontada no Redis;
  - pagamento → `PAID`, R$ 10;
  - auditoria `ENTRY` / `OCR_PROCESSING` / `EXIT_PAYMENT`.
- [x] **Bug achado e corrigido sob carga.** A tabela conntrack da `t3.micro` (7680 entradas) lotava, o ALB perdia a conexão e o ASG trocava a instância (75 % de respostas 502/503 a 150 req/s). Correção: sysctl no `user_data` + keepalive nginx→API. Depois disso: **100 % de 200 OK a 4.200 req/s**.
- [x] **Elasticidade:**
  - 600 conexões → CPU 84 % → alarme de alta em `ALARM` → **1 → 2 instâncias**;
  - fim da carga → alarme de baixa → **2 → 1**.
  - O `stress:aws` foi validado via SSM.
- [x] O instance refresh troca as instâncias sem downtime (sobe a nova antes de tirar a antiga).

## Leitura de placas com imagens reais (2026-10-03)

- [x] Pasta `examples/` com fotos reais, vídeo real, gabarito e créditos de licença.
- [x] `task ocr:local` (OCR local em fotos e vídeos) e `task eval:aws` (fotos enviadas ao sistema no ar).
- [x] Corrigidos no worker: `I`→`1` em placa Mercosul e ruído de JPEG. Fotos reais: **1/5 → 5/5** localmente; na AWS, as 3 placas brasileiras foram lidas certo.
- [x] Vídeo real (640×480, câmera de mão): nenhuma placa confirmada, porque as placas ficam pequenas demais. O sistema é pensado para foto de câmera de cancela.

## Falta fazer

### Entrega (obrigatório, prazo 10/10 23h59)
- [ ] **Gravar o vídeo da Parte 2** (3–5 min, link público). Roteiro: [DEPLOY-AWS.md](DEPLOY-AWS.md#vídeo-da-elasticidade). Mostrar também o fluxo com `examples/fotos/placa-real-mercosul-lsn4i49.jpg`.
- [ ] Preencher as matrículas no `README.md` (`Item2.2`, `Item2.3`, `Item2.4`).
- [ ] Abrir o PR de `feat/aws-deploy` e fazer o merge na `main`.
- [ ] Enviar o link do repositório e o link do vídeo.
- [ ] `task tf:destroy:aws` depois de gravar.

### Deploy pelo GitHub Actions
- [ ] O dono do repositório cria o environment `aws` com a variável `AWS_ROLE_ARN`.
- [ ] Rodar *Actions → Deploy AWS → apply* uma vez para validar o workflow de verdade.

### Melhorias recomendadas (opcionais, por prioridade)
- [ ] **Placa ilegível sem novas tentativas:** hoje o worker re-tenta 3× (~15 min) antes de registrar a falha, e o OCR dá sempre o mesmo resultado. Registrar `OCR_FAILED` na hora e deixar o operador digitar a placa no caixa.
- [ ] **Saída de carro sem placa lida:** permitir pagar uma sessão `PROCESSING` informando a placa manualmente.
- [ ] **CRUD completo:** `DELETE /sessions/{id}` com auditoria (a especificação fala em CRUD).
- [ ] **Futuro, só citar no vídeo/README:** Rekognition (DetectText) chamado por uma Lambda que consome a fila, no lugar do Tesseract.

## Riscos conhecidos e aceitos

- A senha do RDS fica no state local (`infra/terraform.tfstate.d/aws/`, fora do git) e no user data do launch template. Aceitável para um trabalho acadêmico com infraestrutura efêmera.
- As instâncias ficam em subnets públicas da VPC default (com IP público), para evitar o custo de NAT Gateway. A entrada é restrita pelos security groups.
- As instâncias `t3` usam créditos `unlimited` por padrão. Uma carga longa pode gerar um custo extra pequeno.
