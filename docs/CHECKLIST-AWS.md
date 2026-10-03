# Checklist: feito e falta

> Atualizado em 2026-10-03, na branch `feat/aws-deploy`. Prazo de entrega: **10/10/2026, 23h59** (link do código + link do vídeo da Parte 2).
> Deploy: [DEPLOY-AWS.md](DEPLOY-AWS.md) · Visão geral: [README](../README.md) · Contratos: [GOAL.md](GOAL.md)

## Falta fazer

### Entrega (obrigatório)
- [ ] **Gravar o vídeo da Parte 2** (3–5 min, link público no YouTube ou Drive). Roteiro: [DEPLOY-AWS.md](DEPLOY-AWS.md#vídeo-da-elasticidade) e `GOAL.md` §5.
  - Elasticidade: 1 instância → `task load:aws` (+ `task stress:aws` para chegar a 3) → alarme → 2–3 instâncias → fim da carga → volta para 1.
  - Fluxo funcional (prova os 6 serviços):
    - entrada com `examples/fotos/placa-real-mercosul-lsn4i49.jpg` → placa lida → painel → pagamento → auditoria;
    - entrada com `examples/fotos/carro-argentino-rua.jpg` → "falha no OCR" → placa digitada no caixa;
    - no console: S3, DynamoDB, SQS, RDS e EC2.
- [ ] Preencher as matrículas no `README.md` (`Item2.2`, `Item2.3`, `Item2.4`).
- [ ] Abrir o PR de `feat/aws-deploy` para a `main` e fazer o merge.
- [ ] Enviar o link do repositório e o link do vídeo.
- [ ] `task tf:destroy:aws` depois de gravar (o bootstrap pode ficar: custo ~zero).

### Deploy pelo GitHub Actions (depende do dono do repositório)
- [ ] Em *Settings → Environments*, criar o environment `aws` com a variável `AWS_ROLE_ARN = arn:aws:iam::707991310385:role/estacionamento-github-deploy`.
- [ ] Rodar *Actions → Deploy AWS → apply* uma vez para validar o workflow no GitHub. Os mesmos passos já foram validados localmente.

### Ideias futuras (só citar no vídeo/README, sem implementar)
- Trocar o Tesseract pelo Amazon Rekognition (DetectText), chamado por uma Lambda que consome a fila SQS.
- HTTPS com domínio próprio (ACM no ALB) no lugar do domínio do CloudFront.

## Feito

### Aplicação
- [x] **API (Go):**
  - entrada com foto, vagas (com recuperação anti-overbooking pelo RDS), pagamento, listagem de sessões, auditoria e health;
  - **placa digitada/corrigida no caixa** (`PATCH /sessions/{id}`);
  - **exclusão** (`DELETE /sessions/{id}`, com remoção da foto), que completa o CRUD.
- [x] **Worker (Python):**
  - SQS com long polling, OCR (OpenCV + Tesseract), efeitos transacionais e DLQ;
  - **placa ilegível vira `FAILED` na hora** (antes eram 3 tentativas, ~15 min).
- [x] **Frontend (React):**
  - painel, totem de entrada, caixa e auditoria;
  - no caixa: **informar/corrigir placa** e **excluir**, com confirmação.
- [x] **Testes:** API (unit + integração com Postgres/Redis reais), worker 105, web 144. Lint e tipos limpos.
- [x] **Pronta para a AWS:**
  - sem os defaults do Floci, credenciais pelo instance profile, RDS com SSL;
  - migrations com retry;
  - timeouts no servidor HTTP;
  - build `linux/amd64` a partir de Mac ARM.

### Infraestrutura (OpenTofu)
- [x] **Parte 2:**
  - ALB + ASG (1–3 `t3.micro`) + alarmes de CPU (`> 70 %` e `< 25 %`, 1 min, `SimpleScaling`);
  - detailed monitoring;
  - instance refresh sem downtime.
- [x] **HTTPS** com CloudFront na frente do ALB (`app_url`), sem cache e sem domínio próprio.
- [x] **Segurança:**
  - security groups mínimos;
  - RDS privado com senha gerada;
  - role IAM própria (S3/SQS/DynamoDB/ECR/SSM), ou `LabInstanceProfile` na Academy;
  - IMDSv2.
- [x] **State remoto no S3** (`infra/aws`), compartilhado entre as tasks locais e o GitHub Actions. O Floci continua com state local em `infra/`.
- [x] **GitHub Actions com OIDC** (`infra/bootstrap` + workflow *Deploy AWS*: apply/plan/destroy):
  - a role só pode ser assumida pelo environment `aws` do repositório;
  - permissões conferidas no simulador do IAM.
- [x] **Custos:**
  - 2 AZs (menos IPv4 público);
  - RDS sem backup e com gp3;
  - limpeza de imagens no ECR;
  - tags `Project=aws-estacionamento`.

### Validado na AWS real (2026-10-03)
- [x] Deploy completo; a instância fica saudável no Target Group em ~2 min.
- [x] `task smoke:aws`, pelo endereço HTTPS:
  - entrada → OCR → pagamento;
  - placa ilegível → `FAILED` em segundos → placa digitada (vaga descontada) → pagamento;
  - exclusão (vaga devolvida, foto removida do S3).
- [x] **Leitura de placas** (`task eval:aws`): **5/5**:
  - placa sintética, Mercosul real, cinza antiga real;
  - 2 carros argentinos sem placa inventada.
  - Antes das correções do OCR: 1/5.
- [x] **Carga:** 100 % de respostas 200 a ~4.200 req/s, depois de corrigir a tabela conntrack da `t3.micro` (antes: 75 % de 502/503 a 150 req/s).
- [x] **Elasticidade:** CPU a 84 % → alarme → 1 → 2 instâncias; fim da carga → 2 → 1.
- [x] Deploy por commit (tag = SHA), com os mesmos passos do workflow.

### Imagens e vídeo reais
- [x] `examples/` com fotos reais, um vídeo real, gabarito e créditos de licença.
- [x] `task ocr:local` roda o OCR localmente em fotos e vídeos (votação entre quadros).
- [x] Vídeo de câmera de mão em 640×480: nenhuma placa confirmada, porque a placa fica pequena demais. O sistema atende foto de câmera de cancela, perto e em boa resolução.

## Riscos conhecidos e aceitos
- A senha do RDS fica no state remoto (bucket privado e criptografado) e no user data do launch template. Aceitável para um trabalho acadêmico com infraestrutura efêmera.
- As instâncias ficam em subnets públicas da VPC default, para evitar o custo de NAT Gateway. A entrada é restrita pelos security groups.
- Uma sessão `FAILED` não ocupa vaga no contador até o caixa digitar a placa. Enquanto isso, o painel mostra uma vaga livre a mais.
- As instâncias `t3` usam créditos `unlimited`. Uma carga longa pode gerar um custo extra pequeno.
- O state local do bootstrap (`infra/bootstrap/terraform.tfstate`) precisa ser guardado por quem aplicou.
