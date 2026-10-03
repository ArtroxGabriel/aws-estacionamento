# Deploy na AWS

Sobe os 6 serviços na AWS real com ALB + Auto Scaling (Parte 2) usando OpenTofu.

## Pré-requisitos

- `tofu`, `task`, `aws` (CLI v2), Docker com `buildx` e, para o vídeo, `hey`.
  - macOS: `brew install opentofu go-task awscli docker-buildx hey`.
- Credenciais AWS válidas em `~/.aws/credentials` (ou no ambiente). Confira com `aws sts get-caller-identity`.
- **AWS Academy:** o laboratório precisa estar iniciado. Copie as credenciais do "AWS Details" (elas expiram em 4 h) e use o instance profile do laboratório:
  ```bash
  export TF_VAR_instance_profile_name=LabInstanceProfile
  ```
  Em conta própria, não defina essa variável: o OpenTofu cria a role IAM `estacionamento-app`.

## Deploy

```bash
task deploy:aws
```

O comando faz, em ordem:

1. `tofu init` e seleciona o workspace `aws` (o estado local do Floci fica no `default`).
2. Cria os repositórios ECR.
3. Faz build das imagens `api`, `worker` e `web` para `linux/amd64` e o push delas.
4. Roda o `tofu apply` completo, que **pede confirmação**. Leva de 10 a 15 min, principalmente por causa do RDS e do ElastiCache.
5. Mostra os outputs, incluindo `alb_url`.

Depois do apply, a instância ainda leva ~2–3 min para instalar o Docker, baixar as imagens e passar no health check. Teste:

```bash
curl "$(cd infra && tofu output -raw alb_url)/api/health"   # {"status":"UP"}
```

Abra o `alb_url` no navegador para usar o painel, o totem de entrada, o caixa e a auditoria.

Para testar o fluxo inteiro pela linha de comando (entrada com foto → OCR → `PARKED` → pagamento → auditoria):

```bash
task smoke:aws PHOTO=caminho/para/foto-do-carro.jpg
```

## Atualizar o código

```bash
task aws:images    # rebuild + push das imagens
task aws:rollout   # recria as instâncias do ASG, 50% por vez
```

## Vídeo da elasticidade

1. No console, mostre o ALB, o ASG com 1 instância e o Target Group `healthy`.
2. Gere carga pelo ALB:
   ```bash
   task load:aws DURATION=8m CONCURRENCY=300
   ```
   - Se a CPU não passar de 70 %, use o plano B, que ocupa 100 % da CPU das instâncias atuais via SSM:
     ```bash
     task stress:aws DURATION=300
     ```
   - Depois que uma instância nova entrar, rode o `stress:aws` de novo para levar o grupo a 3 instâncias.
3. Mostre o alarme `estacionamento-cpu-alta` em `ALARM` e o ASG subindo para 2 e depois 3 instâncias, todas `healthy` no Target Group.
4. Pare a carga. Mostre o alarme `estacionamento-cpu-baixa` em `ALARM` e o ASG voltando para 1 instância. O cooldown é de 60 s entre cada passo.

## Debug

- **Acessar a instância:** console EC2 → *Connect* → *Session Manager* (a role própria já inclui o SSM).
- **Logs dos containers:**
  ```bash
  sudo docker logs api      # também: worker, web
  ```
- **Log do boot:**
  ```bash
  sudo cat /var/log/cloud-init-output.log
  ```

## Destruir (sempre ao terminar)

```bash
task tf:destroy:aws
```

O bucket S3 e os repositórios ECR são apagados mesmo com conteúdo dentro.

## Custos

Os custos principais são estes:

| Recurso | Custo aproximado |
|---|---|
| ALB | ~US$ 0,03/h |
| RDS `db.t3.micro` | ~US$ 0,02/h |
| ElastiCache `cache.t3.micro` | ~US$ 0,02/h |
| Cada EC2 `t3.micro` | ~US$ 0,01/h |

Sem NAT Gateway. Destrua tudo quando não estiver usando.
