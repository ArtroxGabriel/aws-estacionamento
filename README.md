# AWS Estacionamento

Sistema de gestão de estacionamento com duas cancelas, caixa de pagamento e leitura automática de placas. No Trabalho 2, roda inteiro em containers com alternativas open source: RustFS no lugar do S3, PostgreSQL, Redis, RabbitMQ no lugar do SQS e OCR com OpenCV + Tesseract. A auditoria continua no DynamoDB, como a especificação exige, com o DynamoDB Local para rodar sem a AWS.

Trabalhos Práticos 1 (AWS) e 2 (Docker Compose) de Desenvolvimento de Software para Nuvem (UFC).

## Equipe

| Nome | Matricula |
| -------------- | --------------- |
| Antonio Gabriel | 539628 |
| Luis Antonio | Item2.2 |
| Lucas Lopes | Item2.3 |
| Henrique Viana | Item2.4 |

## Rodar

Pré-requisito: Docker. Na raiz do repositório:

```bash
cd infra
docker compose up -d --build
```

Ou `task up`, com o [Task](https://taskfile.dev) instalado. O `task up` escolhe sozinho onde fica a auditoria (veja abaixo).

| Endereço | O que é |
|---|---|
| http://localhost:3000 | Sistema (painel, cancelas, caixa, auditoria) |
| http://localhost:8080/health | API |
| http://localhost:15672 | Painel do RabbitMQ (`guest` / `guest`) |
| http://localhost:9001 | Console do RustFS (`rustfsadmin` / `rustfsadmin`) |

Para parar: `docker compose down` em `infra/` (ou `task down`). Os dados ficam nos volumes. Para começar do zero, `docker compose down -v`.

### Auditoria no DynamoDB real (AWS Academy)

Por padrão a auditoria vai para o DynamoDB Local, que sobe junto no compose. A especificação pede o banco NoSQL da AWS, então dá para trocar só ele pelo DynamoDB real do AWS Academy. A aplicação continua rodando toda localmente; só a tabela de auditoria fica na AWS.

1. No Learner Lab, copie as credenciais (*AWS Details > AWS CLI > Show*).
2. `cp infra/.env.aws.example infra/.env` e preencha `DYNAMODB_ACCESS_KEY_ID`, `DYNAMODB_SECRET_ACCESS_KEY` e `DYNAMODB_SESSION_TOKEN`. Deixe `DYNAMODB_ENDPOINT_URL` vazio (o `.env` não vai para o git).
3. `scripts/dynamodb-aws.sh` cria a tabela `AuditoriaEstacionamento` (se faltar) e mostra quantos itens ela tem.
4. `task up` recria a API e o worker apontando para a AWS. O RustFS continua com as credenciais locais.

O `task up` decide sozinho: se `infra/.env` tiver `DYNAMODB_ACCESS_KEY_ID` preenchido, usa o DynamoDB real; caso contrário (ou sem o arquivo), usa o DynamoDB Local. Ele imprime `>> Auditoria: ...` dizendo qual escolheu. Para alternar:

```bash
mv infra/.env infra/.env.aws && task up   # DynamoDB Local
mv infra/.env.aws infra/.env && task up   # DynamoDB real
```

As credenciais do Academy expiram quando o lab é reiniciado: atualize o `infra/.env` e rode `task up` de novo. Com `docker compose up` direto (sem o Task) vale a mesma regra, mas sem o aviso, e é preciso rodar `docker compose restart web` se só a API for recriada.

## O que o sistema faz

1. **Cancela de entrada** (`/cancela-entrada`): a câmera tira uma foto do carro; no sistema, a tela envia a foto. A cancela abre na hora e o motorista recebe um ticket, já válido para pagar e sair. Se o pátio estiver lotado, a cancela não abre.
2. **Leitura da placa:** em segundo plano, um worker lê a placa na foto com OCR. Aceita placas dos 4 países do Mercosul. Se a placa for ilegível, nada trava: o ticket continua valendo.
3. **Painel** (`/`): vagas livres e carros no pátio, atualizando sozinho.
4. **Caixa** (`/pagamento`): o operador acha o carro pelo ticket ou pela placa e cobra pelo tempo de permanência: até 15 min é grátis, a 1ª hora custa R$ 10,00 e cada hora (ou fração) a mais, R$ 5,00. A tela mostra o tempo e o **valor a pagar** de cada carro antes de confirmar. Os valores são configuráveis no compose (`PARKING_TOLERANCE_MINUTES`, `FIXED_PARKING_RATE`, `PARKING_EXTRA_HOUR_RATE`). O carro continua ocupando a vaga até sair. O operador também pode informar ou corrigir a placa e excluir um registro.

   Para demonstrar as faixas de tarifa, rode `scripts/demo-tarifa.sh`: ele registra 6 veículos com permanências de 5 min a 3h30 (só altera o horário de entrada no Postgres; o valor continua sendo calculado pela API).
5. **Cancela de saída** (`/cancela-saida`): a câmera lê a placa; se o carro pagou, a cancela abre e a vaga volta ao painel. Se a placa não for reconhecida, o motorista digita o ticket de papel. Carro que não pagou não sai.
6. **Auditoria** (`/auditoria`): histórico de tudo (entrada, leitura, pagamento, saída, correções, exclusões, falhas).

## Arquitetura

```
 Navegador ──▶ web :3000 (nginx + React) ──/api/──▶ api :8080 (Go)
                                                     │
          ┌──────────────┬──────────────┬────────────┼──────────────┬──────────────┐
          ▼              ▼              ▼            ▼              ▼              │
       RustFS        PostgreSQL       Redis      RabbitMQ       DynamoDB           │
       fotos          sessões      vagas livres  fila OCR       auditoria          │
                         ▲                       (+ retry,          ▲              │
                         │                         + DLQ)           │              │
                         │                          │               │              │
                         └──────── worker (Python + OpenCV + Tesseract) ◀──────────┘
```

| Serviço | Uso |
|---|---|
| **RustFS** | Fotos dos veículos (API compatível com S3). Usado no lugar do MinIO, com a concordância do professor; a decisão está na [D11](docs/DECISOES.md) |
| **PostgreSQL** | Sessões: placa, status, entrada, pagamento, saída, valor |
| **Redis** | Contador de vagas livres, consultado o tempo todo pelo painel |
| **RabbitMQ** | Desacopla a entrada da leitura da placa: a cancela não espera o OCR. Fila de espera de 30 s entre tentativas e DLQ na 3ª falha |
| **DynamoDB** | Auditoria de toda ação, com tipo, dados e horário |

**Status de uma sessão:** `PARKED` (no pátio, não pago) → `PAID` (no pátio, pago) → `EXITED` (saiu pela cancela). A leitura da placa fica em outro campo, `plate_status`: `PENDING` → `READ` ou `UNREADABLE`, ou `MANUAL` (digitada no caixa).

**API:**

| Rota | O que faz |
|---|---|
| `POST /entries` | Entrada com foto (reserva a vaga; 409 se lotado) |
| `GET /spots/available` | Vagas livres |
| `POST /exits/{id}/pay` | Pagamento no caixa (a vaga continua ocupada) |
| `POST /barrier/exit/plate` | Cancela de saída pela placa |
| `POST /barrier/exit/ticket` | Cancela de saída pelo ticket |
| `GET /sessions` | Lista sessões |
| `GET /sessions/{id}/photo` | Foto de entrada |
| `PATCH /sessions/{id}` | Placa digitada ou corrigida no caixa |
| `DELETE /sessions/{id}` | Exclui a sessão e a foto |
| `GET /audit` | Auditoria |
| `GET /health` | Health check |

Mais detalhes em [docs/ARQUITETURA.md](docs/ARQUITETURA.md), [docs/DECISOES.md](docs/DECISOES.md) e nos `AGENTS.md` de cada pasta (`api/`, `worker/`, `web/`).

## Estrutura

```
api/       API REST em Go
worker/    Worker de OCR em Python (OpenCV + Tesseract)
web/       Frontend React + Vite + Tailwind (nginx em produção)
infra/     docker-compose.yaml e topologia do RabbitMQ (TP2); OpenTofu da AWS (TP1)
.kiro/     Especificações (requisitos, design, tarefas) do worker e da API/cancela de saída
examples/  Fotos, vídeo e datasets com gabarito
scripts/   Smoke test, demo da tarifa, avaliação de OCR, download/geração de datasets
docs/      Especificações, planos do TP2, arquitetura e decisões
```

## Desenvolver

Pré-requisitos: Docker, [Task](https://taskfile.dev), Go, Python com uv e Node. As versões estão em `mise.toml`.

```bash
task infra:up     # só as dependências (Postgres, Redis, RabbitMQ, RustFS, DynamoDB Local)
task dev:api      # API em :8080 (pare o container estacionamento_api antes)
task dev:worker   # worker de OCR
task dev:web      # frontend em :5173
```

Testes: `task test` (API, worker e web), ou `task test:api`, `task test:worker` e `task test:web` separados.

| Comando | O que faz |
|---|---|
| `task ocr:local` | Roda o OCR em fotos e vídeos de `examples/` |
| `task ocr:dataset` | Mede o OCR em todo o dataset com gabarito |
| `task dataset:baixar` / `task dataset:roboflow` / `task dataset:sintetico` | Baixa e gera os datasets |

## Trabalho 1 (AWS)

O Trabalho 1 rodava na AWS (EC2 com ALB e Auto Scaling, RDS, S3, ElastiCache, DynamoDB, SQS, Rekognition e CloudFront), com deploy pelo GitHub Actions. O código de infraestrutura continua em `infra/` e `infra/aws`, e o passo a passo em [docs/DEPLOY-AWS.md](docs/DEPLOY-AWS.md). As imagens atuais da API e do worker não funcionam nesse deploy, porque não usam mais o SQS.

## Documentação

- [Especificação do Trabalho 2](docs/Especificacao%20Trabalho%20Docker%20e%20Docker%20Compose.md)
- [Especificação do Trabalho 1](docs/Especificao%20Trabalho%20AWS%20-%20Desenvolvimento%20para%20Nuvem.md)
- [Plano do Trabalho 2](docs/PLANO-TP2.md) e [plano do worker](docs/PLANO-TP2-WORKER.md)
- [Arquitetura](docs/ARQUITETURA.md)
- [Roteiro enxuto da apresentação](docs/APRESENTACAO.md)
- [Decisões de arquitetura: o porquê de cada mudança](docs/DECISOES.md)
- [Especificação da API e da cancela de saída](.kiro/specs/api-rabbitmq-exit-barrier/)
- [Deploy na AWS (TP1)](docs/DEPLOY-AWS.md)
- [Exemplos de fotos e vídeos](examples/README.md)
