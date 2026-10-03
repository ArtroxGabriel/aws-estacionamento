# AWS Estacionamento

Sistema de gestão de estacionamento com leitura automática de placas. Usa 6 serviços da AWS (EC2, RDS, S3, ElastiCache, DynamoDB e SQS), com Load Balancer e Auto Scaling.

Trabalho Prático 1 de Desenvolvimento de Software para Nuvem (UFC).

## Equipe

| Nome | Matricula |
| -------------- | --------------- |
| Antonio Gabriel | 539628 |
| Luis Antonio | Item2.2 |
| Lucas Lopes | Item2.3 |
| Henrique Viana | Item2.4 |

## O que o sistema faz

É a automação de um estacionamento, do jeito dos estacionamentos de shopping que leem a placa:

1. **Entrada:** a câmera da cancela tira uma foto do carro. No sistema, a tela **Entrada** faz esse papel: envia a foto. A cancela abre na hora e o motorista recebe um ticket.
2. **Leitura da placa:** em segundo plano, um worker lê a placa na foto com OCR e marca o carro como estacionado. Se a placa for ilegível, a sessão fica com falha e o operador digita a placa no caixa.
3. **Painel:** mostra quantas vagas livres restam, atualizando sozinho.
4. **Caixa / Saída:** o operador acha o carro pela placa ou pelo ticket e cobra (tarifa fixa). Também pode informar ou corrigir a placa e excluir um registro.
5. **Auditoria:** histórico de tudo (entrada, leitura, pagamento, correções, exclusões, falhas).

## Arquitetura

```
 Navegador ──HTTPS──▶ CloudFront ──▶ ALB ──▶ EC2 (Auto Scaling 1–3)
                                             ├─ web     nginx + React
                                             ├─ api     Go
                                             └─ worker  Python + Tesseract
                                                   │
       ┌──────────┬───────────┬──────────────┬─────┴──────┬──────────────┐
       ▼          ▼           ▼              ▼            ▼              ▼
      S3         SQS         RDS        ElastiCache    DynamoDB      CloudWatch
     fotos    fila OCR    PostgreSQL       Redis       auditoria    alarmes de CPU
             (+ DLQ)      (sessões)    (vagas livres)               (scale out/in)
```

| Serviço | Uso |
|---|---|
| **EC2** + ALB + Auto Scaling | API, frontend e worker em containers. De 1 a 3 instâncias: CPU acima de 70% por 1 min → +1; abaixo de 25% por 1 min → −1 |
| **S3** | Fotos dos veículos (o arquivo binário exigido) |
| **RDS (PostgreSQL)** | Sessões: placa, status, entrada, saída, valor |
| **ElastiCache (Redis)** | Contador de vagas livres, consultado o tempo todo pelo painel |
| **SQS** (+ DLQ) | Desacopla a entrada da leitura da placa: a cancela não espera o OCR |
| **DynamoDB** | Auditoria de toda ação, com tipo, dados e horário |
| CloudFront | HTTPS sem domínio próprio, na frente do ALB |

**Status de uma sessão:**
- `PROCESSING` → `PARKED` (placa lida) → `PAID`
- `PROCESSING` → `FAILED` (placa ilegível) → `PARKED` (placa digitada no caixa) → `PAID`

**API:**

| Rota | O que faz |
|---|---|
| `POST /entries` | Entrada com foto |
| `GET /spots/available` | Vagas livres |
| `POST /exits/{id}/pay` | Pagamento e saída |
| `GET /sessions` | Lista sessões |
| `PATCH /sessions/{id}` | Placa digitada ou corrigida no caixa |
| `DELETE /sessions/{id}` | Exclui a sessão e a foto |
| `GET /audit` | Auditoria |
| `GET /health` | Health check do ALB |

Mais detalhes em [docs/GOAL.md](docs/GOAL.md) e nos `AGENTS.md` de cada pasta (`api/`, `worker/`, `web/`, `infra/`).

## Estrutura

```
api/       API REST em Go
worker/    Worker de OCR em Python (OpenCV + Tesseract)
web/       Frontend React + Vite + Tailwind (nginx em produção)
infra/     OpenTofu: Floci local (infra/), AWS (infra/aws), bootstrap OIDC (infra/bootstrap)
examples/  Fotos e vídeo reais para testar a leitura de placas (com gabarito)
scripts/   Smoke test, avaliação de OCR
docs/      Especificação, GOAL, deploy e checklist
```

## Rodar localmente (emulador Floci)

Pré-requisitos: Docker, [Task](https://taskfile.dev), OpenTofu, Go, Python com uv e Node. As versões estão em `mise.toml`.

```bash
task bootstrap:local   # sobe o Floci (S3, SQS, DynamoDB, RDS, Redis) e cria os recursos
task dev:api           # API em :8080
task dev:worker        # worker de OCR
task dev:web           # frontend em :5173
```

Testes: `task test:api`, `cd worker && uv run pytest`, `task test:web`.

## Deploy na AWS

Passo a passo completo em [docs/DEPLOY-AWS.md](docs/DEPLOY-AWS.md).

- **GitHub Actions:** *Actions → Deploy AWS → apply / plan / destroy*. Autentica via OIDC, sem chaves salvas no GitHub.
- **Máquina local:** `task deploy:aws`.
- **Sempre destruir ao terminar:** `task tf:destroy:aws`.

## Testar e demonstrar

| Comando | O que faz |
|---|---|
| `task smoke:aws` | Fluxo completo no ar: entrada → OCR → pagamento; placa ilegível → placa digitada → pagamento; exclusão |
| `task eval:aws` | Envia as fotos de `examples/fotos` ao sistema no ar e compara com o gabarito (resultado atual: 5/5) |
| `task ocr:local` | Roda o OCR localmente em fotos e vídeos de `examples/` |
| `task load:aws` / `task stress:aws` | Carga para o vídeo de elasticidade |

Sobre fotos e vídeos reais, veja [examples/README.md](examples/README.md). O sistema foi feito para fotos de câmera de cancela: perto e em boa resolução. Vídeo de câmera de mão em baixa resolução não serve, porque a placa fica pequena demais.

## Situação atual e o que falta

O que já foi feito e validado na AWS, e o que falta para a entrega (prazo: 10/10/2026): [docs/CHECKLIST-AWS.md](docs/CHECKLIST-AWS.md).

## Documentação

- [Especificação oficial](docs/Especificao%20Trabalho%20AWS%20-%20Desenvolvimento%20para%20Nuvem.md)
- [GOAL.md: visão geral, contratos e roteiro do vídeo](docs/GOAL.md)
- [Deploy na AWS](docs/DEPLOY-AWS.md)
- [Checklist: feito e falta](docs/CHECKLIST-AWS.md)
- [Exemplos de fotos e vídeos](examples/README.md)
