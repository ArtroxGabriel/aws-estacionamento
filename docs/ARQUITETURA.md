# Arquitetura

Como o sistema funciona hoje e o que mudou em relação à `main` (até o PR #5, do frontend). O porquê de cada mudança está em [DECISOES.md](DECISOES.md).

## Visão geral

```
                          Navegador (totem, painel, caixa, auditoria)
                                          │ HTTPS
                                          ▼
                                 ┌─────────────────┐
                                 │   CloudFront    │  HTTPS sem domínio próprio, sem cache
                                 └────────┬────────┘
                                          │ HTTP
                                 ┌────────▼────────┐
                                 │       ALB       │  health check em /api/health
                                 └────────┬────────┘
                ┌─────────────────────────┼─────────────────────────┐
                ▼                         ▼                         ▼
        ┌───────────────┐         ┌───────────────┐         ┌───────────────┐
        │ EC2 #1        │         │ EC2 #2        │         │ EC2 #3        │   Auto Scaling Group
        │  web  (nginx) │         │   (igual)     │         │   (igual)     │   1 a 3 × t3.micro
        │  api  (Go)    │         └───────────────┘         └───────────────┘   CPU > 70% por 1 min → +1
        │  worker (Py)  │                                                       CPU < 25% por 1 min → −1
        └───────┬───────┘
                │  (todas as instâncias usam os mesmos serviços gerenciados)
   ┌──────────┬─┴────────┬────────────┬──────────────┬──────────────┬─────────────┐
   ▼          ▼          ▼            ▼              ▼              ▼             ▼
  S3        SQS        RDS       ElastiCache     DynamoDB      Rekognition   CloudWatch
 fotos   fila OCR   PostgreSQL     Redis        auditoria      lê a placa    alarmes de CPU
         (+ DLQ)    (sessões)   (vagas livres)
```

As instâncias não guardam estado: sessões, vagas, fotos, fila e auditoria ficam nos serviços gerenciados. Qualquer instância atende qualquer requisição, e o Auto Scaling pode criar ou destruir instâncias à vontade.

## Fluxos

**Entrada do carro.**
1. O totem envia a foto (`POST /entries`).
2. A API grava a foto no **S3** e cria a sessão `PROCESSING` no **RDS**.
3. A API publica uma mensagem na **SQS** e grava `ENTRY` no **DynamoDB**.
4. O ticket volta na hora; a cancela não espera a leitura da placa.

**Leitura da placa (worker).**
1. O worker de qualquer instância pega a mensagem e baixa a foto do S3.
2. Ele lê a placa:
   - **Rekognition** na foto, escolhendo a placa mais alta, que é a do carro em primeiro plano;
   - sem placa, uma 2ª chamada com a foto numa moldura (close da placa);
   - ainda sem placa, o Tesseract, aceitando só leitura segura.
3. Placa lida: sessão `PARKED` no RDS, `DECR` da vaga no **Redis** e `OCR_PROCESSING` no DynamoDB.
4. Placa ilegível: sessão `FAILED` **na hora**, com `OCR_FAILED`. O operador digita a placa no caixa.
5. Uma mensagem que falha 3 vezes por problema de infraestrutura vai para a **DLQ**.

**Painel.** Consulta `GET /spots/available`, que lê do **Redis**. Se o Redis perder o valor, a API recalcula pelo RDS e regrava.

**Caixa.**
- **Pagar:** `POST /exits/{id}/pay` marca `PAID`, devolve a vaga e grava `EXIT_PAYMENT`.
- **Informar ou corrigir a placa:** `PATCH /sessions/{id}`. Uma sessão `FAILED` vira `PARKED` e ocupa a vaga. Grava `PLATE_CORRECTION`.
- **Excluir:** `DELETE /sessions/{id}` apaga a sessão e a foto do S3 e devolve a vaga se estava `PARKED`. Grava `SESSION_DELETE`.

**Placas aceitas:** os 4 países do Mercosul, com as regras da [D8](DECISOES.md#d8-placas-dos-4-países-do-mercosul).

| País | Formatos |
|---|---|
| Brasil | `ABC1D23`, `ABC1234` |
| Argentina | `AB123CD`, `ABC123` |
| Paraguai | `ABCD123` |
| Uruguai | `ABC1234` |

## Segurança

- Só o CloudFront e o ALB recebem tráfego da internet.
- As instâncias aceitam tráfego só do ALB; RDS e Redis aceitam conexão só das instâncias.
- O RDS não é público, e a senha dele é gerada pelo OpenTofu.
- As instâncias acessam S3, SQS, DynamoDB e Rekognition por uma role IAM, sem chave em código, e usam IMDSv2.
- O deploy pelo GitHub usa OIDC, sem chave salva no GitHub. A role de deploy só aceita o environment `aws` deste repositório e não consegue alterar as próprias permissões.

## Deploy

```
GitHub Actions "Deploy AWS" (ou task deploy:aws)
  → build das imagens api/worker/web → ECR (tag = commit)
  → tofu apply (state no S3, compartilhado entre local e Actions)
  → launch template muda → ASG troca as instâncias aos poucos, sem queda
  → health check pelo ALB
```

Para desenvolver localmente, o **Floci** emula S3, SQS, DynamoDB, RDS e Redis (`task bootstrap:local`), com o state do OpenTofu local em `infra/`. O worker usa só o Tesseract, porque o Floci não tem Rekognition.

## O que mudou em relação à `main`

| Área | Antes (`main`) | Agora (`feat/aws-deploy`) | Decisão |
|---|---|---|---|
| **Onde roda** | Só local, no emulador Floci | AWS real + Floci para desenvolver | — |
| **Infra (OpenTofu)** | 6 recursos: S3, SQS, DLQ, DynamoDB, RDS (público, senha fixa), Redis | + VPC/security groups, ECR, IAM, launch template, ALB, Auto Scaling (1–3), alarmes de CPU, CloudFront; RDS privado com senha gerada | [D2](DECISOES.md#d2-cloudfront-para-ter-https), [D6](DECISOES.md#d6-rede-e-custos), [D7](DECISOES.md#d7-alarmes-próprios-de-1-minuto-simple-scaling) |
| **Parte 2 (elasticidade)** | Não existia | ALB + ASG + alarmes de 1 min, testado: 1 → 2 → 1 instâncias | [D7](DECISOES.md#d7-alarmes-próprios-de-1-minuto-simple-scaling) |
| **HTTPS** | — | CloudFront (`https://...cloudfront.net`) | [D2](DECISOES.md#d2-cloudfront-para-ter-https) |
| **Deploy** | Manual, local | GitHub Actions com OIDC ou `task deploy:aws`; imagens no ECR; state no S3 | [D3](DECISOES.md#d3-deploy-por-imagens-no-ecr-e-github-actions-com-oidc) |
| **API: configuração** | Apontava sempre para o Floci, com chaves falsas | Endpoints e credenciais reais da AWS (role da instância); RDS com SSL; migrations com retry | — |
| **API: rotas** | entrada, vagas, pagamento, sessões, auditoria, health | + `PATCH /sessions/{id}` (placa digitada/corrigida) e `DELETE /sessions/{id}` (CRUD completo) | [D4](DECISOES.md#d4-placa-ilegível-vira-falha-na-hora-caixa-digita-a-placa-exclusão) |
| **Worker: leitura da placa** | Só Tesseract (52% em fotos reais) | Rekognition + moldura + Tesseract seguro (97% em fotos reais) | [D1](DECISOES.md#d1-amazon-rekognition-para-ler-a-placa), [D10](DECISOES.md#d10-política-de-leitura-moldura-e-reserva-segura) |
| **Worker: placa ilegível** | 3 tentativas com 5 min de intervalo (~15 min) até a falha | `FAILED` na hora; o caixa digita a placa | [D4](DECISOES.md#d4-placa-ilegível-vira-falha-na-hora-caixa-digita-a-placa-exclusão) |
| **Placas aceitas** | Só Brasil | 4 países do Mercosul | [D8](DECISOES.md#d8-placas-dos-4-países-do-mercosul) |
| **Caixa (web)** | Pagar | Pagar, informar/corrigir placa, excluir | [D4](DECISOES.md#d4-placa-ilegível-vira-falha-na-hora-caixa-digita-a-placa-exclusão) |
| **Estabilidade sob carga** | Não testada | Corrigido limite de conexões da `t3.micro` + keepalive nginx→API: de 75% de erros a 150 req/s para 100% OK a ~4.200 req/s | [D5](DECISOES.md#d5-ajustes-de-rede-nas-instâncias-conntrack-e-keepalive) |
| **Testes do OCR** | 0 fotos com gabarito | 1.520 fotos com gabarito (reais, Roboflow e sintéticas) + scripts de medição | [D9](DECISOES.md#d9-datasets-de-teste-e-licenças) |
| **Serviços AWS** | 6 (só no emulador) | 6 obrigatórios + Rekognition, CloudFront, ECR, CloudWatch, IAM | [tabela](DECISOES.md#serviços-aws-usados) |

## Mapa do repositório

```
api/                 API REST em Go (handler → service → repository)
worker/              Worker em Python: SQS → OCR (Rekognition/Tesseract) → RDS/Redis/DynamoDB
web/                 Frontend React + Vite (nginx em produção)
infra/               OpenTofu: recursos (módulo) + Floci local
  aws/               Root da AWS real (state remoto no S3)
  bootstrap/         Uma vez por conta: bucket do state, OIDC do GitHub, role de deploy
.github/             Workflow "Deploy AWS" (OIDC)
examples/            Fotos, vídeo e datasets com gabarito (ver examples/README.md)
scripts/             Smoke test, avaliação de OCR, download/geração de datasets
  analise-ocr/       Simulações que embasaram as decisões D1 e D10
docs/                Especificação, GOAL (contratos), ARQUITETURA, DECISOES, DEPLOY-AWS, CHECKLIST-AWS
```
