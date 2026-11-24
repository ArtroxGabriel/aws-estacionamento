# Plano de Implementação: Trabalho Prático 2 — Docker, Docker Compose & Alternativas Open Source

> **Disciplina:** Desenvolvimento de Software para Nuvem (UFC)  
> **Professores:** Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta  
> **Objetivo:** Migrar o sistema do Trabalho 1 para um ecossistema 100% containerizado com alternativas open-source (RustFS, RabbitMQ, PostgreSQL gerenciado, Redis e DynamoDB Local), retornando ao modelo open-source em Python (OpenCV + Tesseract) para leitura de placas (descartando o Amazon Rekognition), incorporando a nova dinâmica de duas cancelas físicas (Entrada e Saída) com terminal de pagamento independente e bônus Kubernetes testado localmente via k3s.

---

## 1. Visão Geral da Arquitetura & Mudanças

O Trabalho Prático 2 substitui os serviços de nuvem proprietários da AWS por serviços open-source equivalentes rodando em contêineres Docker, ao mesmo tempo em que aprimora o fluxo operacional para simular com precisão a engenharia de tráfego de um estacionamento inteligente:

1. **Substituição do Amazon S3 por RustFS:** Object storage de alta performance escrito em Rust (licença Apache 2.0), compatível com a API REST do S3.
2. **Substituição do Amazon RDS por PostgreSQL em Container:** Imagem oficial `postgres:16-alpine` com migrações SQL automáticas e volume persistente.
3. **Substituição do Amazon SNS/SQS por RabbitMQ:** Broker AMQP 0-9-1 durável (`rabbitmq:3-management-alpine`) com suporte a Dead Letter Queue (DLQ) e painel web em `http://localhost:15672`.
4. **Substituição do Modelo Proprietário AWS Rekognition pelo Pipeline Open-Source Python (OpenCV + Tesseract):** Remoção de qualquer dependência ou chamada à API do Amazon Rekognition no Worker. Retorno ao modelo de visão computacional em Python 100% local e auto-contido:
   - Localização de placa via OpenCV (`locate.py` com detecção de faixa azul Mercosul e contornos retangulares);
   - Pré-processamento e binarização adaptativa com Pillow/OpenCV (`processor.py`);
   - Extração ótica com Tesseract OCR (`pytesseract`);
   - Limpeza e normalização sintática Mercosul para os 4 países (`clean.py`).
5. **Manutenção do Amazon DynamoDB com Suporte Local:** Atendimento estrito à imposição 1.a da especificação, conectando ao DynamoDB na nuvem ou ao container `amazon/dynamodb-local` para execução 100% offline.
6. **Manutenção do Redis:** Cache em memória para contagem de vagas em tempo real (`spots:available`) com auto-recuperação anti-overbooking calculada pelo PostgreSQL.
7. **Fluxo com Duas Cancelas e Pagamento Desacoplado:**
   - **Cancela de Entrada (`/cancela-entrada`):** Câmera tira foto; a API reserva a vaga no Redis (pátio lotado → 409), salva a foto no RustFS, cria a sessão já `PARKED` com `plate_status = PENDING` no Postgres, enfileira no RabbitMQ, emite o ticket em papel (com ID) e abre a cancela. O ticket já é válido para pagar e sair (D20). O worker OCR só enriquece a sessão: lê a placa com o modelo open-source e grava `plate_status = READ` (ou `UNREADABLE`). Não muda `status` e não mexe no Redis.
   - **Terminal de Pagamento / Caixa (`/pagamento`):** Motorista paga o ticket ou placa. O status vai para `PAID`. **A vaga continua retida no Redis** (o carro ainda está na vaga!). O ticket é habilitado para saída.
   - **Cancela de Saída (`/cancela-saida`):** Câmera tenta ler a placa. Se a placa tiver pagamento confirmado (`PAID`), a cancela abre imediatamente. Se a placa falhar ou estiver ilegível, o motorista insere o ID do ticket em papel. Ao validar, o status vai para `EXITED`, a cancela abre, a vaga é finalmente liberada no Redis (`INCR spots:available`) e o evento é auditado.
8. **Orquestração Única com Docker Compose:** Todos os 8 serviços sobem via `docker compose up -d` com verificação de saúde (`healthcheck`) e dependências determinísticas.
9. **Bônus Kubernetes (`k8s/`):** Manifestos YAML completos testados localmente via **k3s** com Deployments, Services (NodePort/ClusterIP), ConfigMaps e Secrets (+1 ponto extra).

```mermaid
flowchart TD
    subgraph Entrada["1. Cancela de Entrada"]
        CameraIn["Câmera Entrada"] -->|Foto| API_Entry["POST /entries"]
        API_Entry -->|1. Salva Foto| RustFS[("RustFS (S3)")]
        API_Entry -->|0. Reserva vaga, lotado = 409| Redis[("Redis")]
        API_Entry -->|2. Sessão 'PARKED' + plate_status 'PENDING'| PG[("PostgreSQL")]
        API_Entry -->|3. Publica Tarefa| RabbitMQ{{"RabbitMQ"}}
        API_Entry -->|4. Log ENTRY| DynamoDB[("DynamoDB")]
        API_Entry -->|5. Imprime Ticket ID| TotemIn["Cancela Abre + Ticket Impresso"]
    end

    subgraph WorkerOCR["2. Worker Assíncrono (Python Open-Source)"]
        RabbitMQ -->|Consome Mensagem| Worker["Worker Python"]
        Worker -->|Baixa Foto| RustFS
        Worker -->|OpenCV Localização + Tesseract OCR| Worker
        Worker -->|plate_status 'READ' ou 'UNREADABLE' + Placa| PG
        Worker -->|Log OCR_PROCESSING / OCR_FAILED| DynamoDB
    end

    subgraph Caixa["3. Terminal de Pagamento"]
        Motorista["Motorista"] -->|Paga Ticket/Placa| API_Pay["POST /exits/{id}/pay"]
        API_Pay -->|Status 'PAID' (Vaga mantida)| PG
        API_Pay -->|Log EXIT_PAYMENT| DynamoDB
    end

    subgraph Saida["4. Cancela de Saída"]
        CameraOut["Câmera Saída"] -->|Tentativa 1: Placa| API_ExitPlate["POST /barrier/exit/plate"]
        PaperTicket["Ticket em Papel (ID)"] -->|Tentativa 2: Fallback| API_ExitTicket["POST /barrier/exit/ticket"]
        API_ExitPlate & API_ExitTicket --> CheckPaid{"Status == 'PAID'?"}
        CheckPaid -->|Sim| OpenBarrier["Status 'EXITED' + INCR Vaga no Redis + Abre Cancela!"]
        CheckPaid -->|Não Pago| BlockBarrier["Bloqueia Cancela + Alerta: Pagar no Caixa"]
    end
```

---

## 2. Revisão Requerida

> [!IMPORTANT]
> **Alterações de Contrato e Fluxo de Negócio:**
> 1. **Modelo de OCR Open Source:** O Amazon Rekognition foi abandonado. O processamento de imagens opera exclusivamente com o pipeline local em Python (OpenCV para localização geométrica/cromática e Tesseract OCR para extração de texto), eliminando qualquer dependência externa ou credencial AWS para visão computacional.
> 2. **Pagamento não libera a vaga:** No Trabalho 1, pagar a sessão liberava a vaga imediatamente. Agora, o pagamento apenas muda o status para `PAID`. A vaga só é devolvida ao Redis quando o veículo cruza fisicamente a Cancela de Saída (`EXITED`).
> 3. **Substituição da Fila:** Migração completa da API Go e do Worker Python de AWS SQS para RabbitMQ (AMQP 0-9-1).
> 4. **Substituição do Storage:** RustFS substitui MinIO/S3 (porta 9000), operando com API compatível com S3.
> 5. **Novas Rotas de Cancela:** Inclusão de `/barrier/exit/plate` e `/barrier/exit/ticket` para a automação da cancela de saída.
> 6. **Ambiente Kubernetes Local:** Utilização do **k3s** para validação rápida, leve e reproduzível dos manifestos Kubernetes.
> 7. **O ticket identifica o carro (D20, `docs/DECISOES.md`):** a sessão nasce `PARKED` com a vaga reservada pela API na entrada; `plate_status` (`PENDING`/`READ`/`UNREADABLE`/`MANUAL`) é separado de `status`. Nenhuma regra pode exigir placa lida para o carro pagar ou sair. O worker não usa Redis e não muda `status`.
> 8. **Estado desta revisão (2026-11-16):** o Componente 3 (Worker) e o serviço `worker` + topologia RabbitMQ do Componente 1 estão implementados (`docs/PLANO-TP2-WORKER.md`). A API, o frontend e o compose foram concluídos em 2026-11-17 e o Kubernetes em 2026-11-22. Ver a seção 5.

> [!WARNING]
> **Atendimento às Regras de Avaliação do Trabalho 2:**
> O DynamoDB é mantido para auditoria conforme exigência 1.a da especificação dos professores (*"Continuar usando o serviço de banco de dados não-relacional da AWS"*). No Docker Compose, incluiremos o `dynamodb-local` para que o projeto rode 100% autônomo e offline em qualquer máquina, permitindo apontar para a AWS real bastando exportar credenciais válidas.

---

## 3. Mudanças Propostas por Componente

### Componente 1: Infraestrutura e Orquestração Local (`infra/docker-compose.yaml` e `Taskfile.yaml`)

> **Estado: FEITO** (as tasks `k8s:*` também). O arquivo é `infra/docker-compose.yaml` (não há compose na raiz), com os 8 serviços mais `createbuckets` e `createtables`. O healthcheck do RabbitMQ usa `check_port_connectivity` (o `ping` ficava saudável antes da porta 5672 aceitar conexões). `task up`/`down`/`logs`/`test` existem; `task infra:up` sobe só as dependências; `task k8s:k3s:up`/`k8s:build`/`k8s:apply`/`k8s:forward`/`k8s:delete` cuidam do cluster.

#### [NEW] `infra/docker-compose.yaml` (completar)
Substitui a dependência do Floci por containers oficiais e especializados:
- `rustfs`: Imagem `rustfs/rustfs:latest`, volumes persistentes, portas 9000 (API S3) e 9001 (Web Console).
- `postgres`: Imagem `postgres:16-alpine`, banco `estacionamento`, volume `pgdata`, porta 5432, `healthcheck` via `pg_isready`.
- `rabbitmq`: Imagem `rabbitmq:3-management-alpine`, portas 5672 (AMQP) e 15672 (Management), volume `rabbitmq_data`, `healthcheck` via `rabbitmq-diagnostics ping`.
- `redis`: Imagem `redis:7-alpine`, porta 6379, volume `redis_data`, `healthcheck` via `redis-cli ping`.
- `dynamodb-local`: Imagem `amazon/dynamodb-local:latest`, porta 8000, volume `dynamo_data`.
- `api`: **[FEITO]** Build `../api`, expõe 8080, `depends_on` de postgres, rabbitmq, rustfs, redis e dynamodb-local com `condition: service_healthy`.
- `worker`: **[FEITO]** Build `../worker`, consome rabbitmq e conecta ao postgres, rustfs e dynamodb-local. **Não usa Redis** (D20).
- `web`: **[FEITO]** Build `../web`, expõe 3000 (Nginx servindo React e roteando `/api/` para `http://api:8080/`).

#### [MODIFY] `Taskfile.yaml`
Atualizar as tasks de automação:
- `task up` / `task down` / `task logs`: Controla o `docker compose` unificado.
- `task test`: Executa testes da API Go, do Worker Python e do Frontend Web.
- `task k8s:k3s:up` / `task k8s:apply` / `task k8s:delete`: Comandos para subir e testar os manifestos no cluster local k3s.

---

### Componente 2: API Backend (`api/`)

> **Estado: FEITO** (`.kiro/specs/api-rabbitmq-exit-barrier`, fases 1 a 3). Diferenças em relação ao texto abaixo: a rota de pagamento continua `POST /exits/{id}/pay` com auditoria `EXIT_PAYMENT`; `POST /barrier/exit/plate` recebe a placa em JSON, sem foto; a busca por placa se chama `FindOccupyingByPlate` e devolve uma lista; existe a coluna `paid_at`. Ver `docs/DECISOES.md`, D21 e D22.

#### [MODIFY] `api/go.mod`
- Adicionar dependência oficial AMQP: `github.com/rabbitmq/amqp091-go`.

#### [MODIFY] `api/internal/config/config.go`
- Adicionar variáveis:
  - `RabbitMQURL`: default `amqp://guest:guest@rabbitmq:5672/`.
  - `RustFSEndpoint`: default `http://rustfs:9000`.
  - `DynamoDBEndpoint`: default `http://dynamodb-local:8000` (ou vazio para AWS real).

#### [NEW] `api/internal/repository/rabbitmq.go`
Implementação de `EventPublisher` utilizando RabbitMQ:
```go
type RabbitMQEventPublisher struct {
    conn    *amqp091.Connection
    channel *amqp091.Channel
    queue   string
}
// Conecta, CONFERE (declaração passiva) que "ocr-processamento-fila" existe e publica mensagens
// em JSON com persistent delivery e publisher confirms.
// A topologia (fila, -retry, -dlq) vem de infra/rabbitmq/definitions.json; declarar a fila aqui com
// argumentos diferentes causaria PRECONDITION_FAILED.
```
Corpo da mensagem (contrato com o worker): `{"session_id": "<32 hex>", "s3_key": "photos/..."}`. O `session_id` precisa ter exatamente 32 caracteres hexadecimais, sem hífens.

#### [MODIFY] `api/internal/repository/s3.go`
- Suporte a auto-criação do bucket `estacionamento-fotos-veiculos` no RustFS caso não exista durante a inicialização.

#### [MODIFY] `api/internal/repository/postgres.go`
- Adicionar métodos ao repositório:
  - `MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error)`
  - `FindByPlateActive(ctx context.Context, plate string) (*model.Session, error)`
  - Atualizar `CountActive` para incluir sessões com `status IN ('PARKED', 'PAID')` (o status `PROCESSING` deixou de existir na D20).
  - **[DEFINIR]** Hoje a contagem de vagas sai de sessões `PARKED`; com `PAID` retendo a vaga até a saída, a migration/`CountActive` e o `INCR` precisam concordar (ver seção 5).

#### [MODIFY] `api/internal/service/parking.go`
- Modificar `PayExit`: hoje ele marca `PAID`, grava a auditoria `EXIT_PAYMENT` **e incrementa** o contador de vagas. Mudança proposta: continuar marcando `PAID` e preenchendo `amount_paid`, mas **não incrementar** o contador no Redis (o carro ainda está na vaga). **[DEFINIR]** manter o nome `EXIT_PAYMENT` ou renomear para `PAYMENT_RECEIVED` (ver seção 5).
- Implementar `ExitBarrierByPlate(ctx, plate)`:
  1. Busca sessão ativa da placa informada.
  2. Valida se o status é `PAID`. Se for `PARKED`, rejeita: *"Pagamento pendente no caixa"*.
  3. Atualiza status para `EXITED` com `exited_at`.
  4. Incrementa o contador de vagas no Redis (`spots:available`).
  5. Grava auditoria `EXIT_BARRIER_OPENED`.
  6. Retorna confirmação de liberação da cancela de saída.
- Implementar `ExitBarrierByTicket(ctx, sessionID)`:
  1. Busca sessão pelo ID do ticket.
  2. Valida se o status é `PAID`.
  3. Atualiza status para `EXITED`, incrementa Redis e grava auditoria.
  4. Retorna confirmação de liberação da cancela de saída.

#### [MODIFY] `api/internal/handler/handler.go`
- Registrar novos endpoints:
  - `POST /barrier/exit/plate`: Recebe `{ "plate": "ABC1D23" }` ou multipart com foto da cancela de saída.
  - `POST /barrier/exit/ticket`: Recebe `{ "ticket_id": "b1a7c4..." }`.
  - Pagamento: a rota existente é `POST /exits/{id}/pay` (status $\rightarrow$ `PAID`). **[DEFINIR]** manter esse caminho (o web já o usa) ou renomear para `POST /sessions/{id}/pay`.
- Atualizar documentação e testes unitários/integrados.

---

### Componente 3: Worker de Visão Computacional (`worker/`)

> **Estado: FEITO** (branch `feat/worker-rabbitmq-migration`). Detalhes, decisões e verificação em [PLANO-TP2-WORKER.md](PLANO-TP2-WORKER.md). Abaixo, o que foi implementado, que difere do rascunho original deste plano.

#### [MODIFY] `worker/pyproject.toml`
- `pika>=1.3` (AMQP).

#### [MODIFY] `worker/config.py`
- Variáveis: `RABBITMQ_URL` (`amqp://`/`amqps://`), `RABBITMQ_QUEUE_NAME` (padrão `ocr-processamento-fila`; `-retry` e `-dlq` derivam dele), `DATABASE_URL`, `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `DYNAMODB_TABLE_NAME`, `DYNAMODB_ENDPOINT_URL`, `AWS_REGION`. Endpoint vazio = AWS real.
- **Removidos:** `OCR_ENGINE` (o OCR é só OpenCV + Tesseract), `REDIS_URL`, `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`.

#### [MODIFY] `worker/storage/clients.py`
- Clientes S3 (path-style, apontando para o RustFS) e DynamoDB, cada um com seu endpoint. Rekognition e SQS removidos.

#### [MODIFY] `worker/worker.py` + [NEW] `processor.py`, `consumer.py`
- `worker.py`: só `main()` (configuração, montagem, sinais).
- `processor.py`: `MessageProcessor.handle(body, attempt)` com as regras de negócio, sem conhecer o broker. Ele parseia o JSON, baixa a foto do RustFS, roda o OCR (Tesseract + OpenCV, normalização Mercosul de 4 países) e grava no Postgres, na mesma transação, a placa e a auditoria:
  - **Legível:** `plate_status` `PENDING` → `READ` e auditoria `OCR_PROCESSING`.
  - **Ilegível:** `plate_status = UNREADABLE` e auditoria `OCR_FAILED`; a mensagem é confirmada e a sessão continua `PARKED`, pagável pelo ticket (D20).
  - O worker **não muda `status`, não usa Redis** e é idempotente (`UPDATE ... WHERE plate_status = 'PENDING'`).
- `consumer.py`: `pika.BlockingConnection`, `prefetch_count=1`, `ack` manual, publisher confirms. As filas são só conferidas (declaração passiva); se faltar alguma, o worker sai com código 2.
  - **Falha transitória** (S3, Postgres, DynamoDB, erro de OCR): republica na fila `-retry` (TTL de 30 s, volta sozinha para a principal) com `x-attempt + 1` e só então dá `ack` na original.
  - **Falha na 3ª tentativa:** auditoria `POISON_MESSAGE`, sessão encerrada como `UNREADABLE` e mensagem na `-dlq` com `x-attempt` e `x-failure-reason`.
  - Reconexão com back-off de 1 a 30 s; parada graciosa (SIGTERM termina a mensagem em andamento, dá `ack` e sai).

---

### Componente 4: Frontend Web & Totens (`web/`)

> **Estado: FEITO** (fase 4 da spec). `/entrada` e `/saida` redirecionam para as rotas novas. O totem de saída aceita os 8 caracteres em destaque no ticket.

#### [MODIFY] `web/src/App.tsx` & Navegação
Adicionar rotas dedicadas para o fluxo completo:
- `/cancela-entrada`: Interface do Totem da Cancela de Entrada (captura foto, emite ticket e simula abertura da cancela).
- `/pagamento`: Interface do Caixa / Autoatendimento (busca ticket/placa, realiza pagamento e orienta saída).
- `/cancela-saida`: Interface do Totem da Cancela de Saída (câmera com leitura de placa + fallback para digitação do ID do ticket de papel, com status visual da cancela).
- `/`: Painel Geral (status de vagas em tempo real e listagem de sessões ativas).
- `/auditoria`: Trilha de auditoria NoSQL do DynamoDB.

#### [NEW] `web/src/pages/ExitBarrierPage.tsx`
Página interativa da Cancela de Saída:
- Modo 1 (Leitura de Placa): Simula a câmera LPR da saída. Ao informar a placa, chama `POST /barrier/exit/plate`. Se pago, animação verde de *"Cancela Aberta — Boa Viagem!"*. Se não pago, alerta vermelho de *"Pagamento não localizado. Favor pagar no caixa"*.
- Modo 2 (Ticket em Papel): Quando a câmera não conseguir ler a placa, motorista clica em *"Digitar Ticket de Papel"*, insere o ID e destrava a cancela.

---

### Componente 5: Bônus Kubernetes (`k8s/` testado via k3s)

> **Estado: FEITO** (2026-11-22). Manifestos em `k8s/` (ver `k8s/README.md`), testados num k3s local (Colima): os 8 Deployments e os 2 Jobs sobem, e o fluxo entrada, OCR, pagamento e saída fechou pelo `web`. Além dos arquivos abaixo há `dynamodb-local.yaml` e `init-jobs.yaml` (`create-bucket` e `create-table`), e o `rustfs-console` também é `NodePort` (31901).

Criar diretório `k8s/` com manifestos declarativos:
- `k8s/00-namespace.yaml`: Namespace `estacionamento`.
- `k8s/01-configmap.yaml` & `k8s/02-secrets.yaml`: Credenciais e variáveis de ambiente.
- `k8s/postgres.yaml`: Deployment + Service `ClusterIP` + PersistentVolumeClaim.
- `k8s/rustfs.yaml`: Deployment + Service `ClusterIP` + PersistentVolumeClaim.
- `k8s/rabbitmq.yaml`: Deployment + Service `ClusterIP` (5672) + NodePort (31672 para painel).
- `k8s/redis.yaml`: Deployment + Service `ClusterIP`.
- `k8s/api.yaml`: Deployment da API Go + Service `ClusterIP`.
- `k8s/worker.yaml`: Deployment do Worker Python OCR.
- `k8s/web.yaml`: Deployment do Nginx/React + Service `NodePort` (porta 30080).

---

## 4. Plano de Verificação

### 4.1. Testes Automatizados
- **API Go:**
  ```bash
  cd api && go test -v ./...
  ```
  Testes unitários e de integração validando os handlers de entrada, pagamento, saída por placa, saída por ticket, e regras anti-overbooking.
- **Worker Python:**
  ```bash
  cd worker && uv run pytest -v
  ```
  Testes unitários de parsing de mensagens AMQP, integração com o pipeline local de Tesseract/OpenCV e fluxo de erro.
- **Frontend Web:**
  ```bash
  cd web && npm test
  ```
  Testes dos componentes dos totens de entrada, saída e caixa.

### 4.2. Verificação do Docker Compose (Rubrica Principal)
1. **Inicialização:**
   ```bash
   docker compose up -d --build
   docker compose ps
   ```
   Confirmar que todos os 8 contêineres sobem com status `healthy`/`running`.
2. **Acesso aos Painéis:**
   - Web: `http://localhost:3000`
   - API Health: `http://localhost:8080/health`
   - RabbitMQ Management: `http://localhost:15672` (guest/guest)
   - RustFS Console: `http://localhost:9001`
3. **Fluxo E2E Completo do Estacionamento:**
   - **Passo 1 (Cancela de Entrada):** Acessar `/cancela-entrada`, enviar foto de veículo. Verificar que a cancela abre, ticket ID é gerado, foto é salva no RustFS, a vaga é reservada (contador diminui em 1), e mensagem trafega pelo RabbitMQ. Com o pátio lotado, a entrada deve devolver 409.
   - **Passo 2 (Worker OCR Local):** Verificar nos logs do worker que a mensagem é consumida e o modelo local OpenCV + Tesseract extrai a placa sem nenhuma chamada para a AWS, e que a sessão passa a `plate_status = READ`. As vagas disponíveis já diminuíram em 1 **na entrada** (Passo 1), não nesse passo. *(O worker foi verificado sozinho; o fluxo ponta a ponta depende da API.)*
   - **Passo 3 (Caixa de Pagamento):** Acessar `/pagamento`, localizar o ticket e confirmar o pagamento. Verificar que o status vai para `PAID`, mas o mostrador de vagas **não aumenta** (carro ainda estacionado).
   - **Passo 4 (Cancela de Saída com Placa):** Acessar `/cancela-saida`, simular leitura da placa paga. Verificar que a cancela de saída abre, status vira `EXITED`, e o mostrador de vagas **aumenta em 1**.
   - **Passo 5 (Cancela de Saída com Ticket de Papel):** Fazer nova entrada e pagamento. Na saída, simular falha de placa e validar via ID do papel. Confirmar abertura e liberação de vaga.
   - **Passo 6 (Bloqueio de Não Pago):** Tentar sair com um carro sem pagar. Confirmar que a cancela de saída recusa a abertura.
   - **Passo 7 (Auditoria):** Acessar `/auditoria` e verificar a trilha completa de eventos no DynamoDB.

### 4.3. Verificação do Bônus Kubernetes com k3s
Para testes locais rápidos e confiáveis da implantação Kubernetes, utilizamos o **k3s**:
```bash
# 1. Aplicar manifestos no k3s
kubectl apply -f k8s/

# 2. Verificar pods e serviços no namespace estacionamento
kubectl get pods,svc -n estacionamento

# 3. Testar conectividade via NodePort do k3s (porta 30080 para web)
curl -I http://localhost:30080
```

---

## 5. Estado atual e decisões em aberto

### 5.1. Estado por componente (2026-11-17)

| Componente | Estado |
|---|---|
| 1. Compose / Taskfile | **Feito.** 8 serviços em `infra/docker-compose.yaml`; `task up`/`down`/`logs`/`test`. As tasks `k8s:*` (bônus) também |
| 2. API Go | **Feito e verificado** (`.kiro/specs/api-rabbitmq-exit-barrier`): RabbitMQ, endpoints próprios, `EXITED`, cancela de saída |
| 3. Worker | **Feito e verificado** (`PLANO-TP2-WORKER.md`). Prints do README feitos em 2026-11-22 |
| 4. Frontend | **Feito e verificado**: cancela de entrada, caixa, cancela de saída, painel com carros no pátio |
| 5. Kubernetes (bônus) | **Feito e verificado** em k3s: `k8s/`, tasks `k8s:*`, fluxo ponta a ponta |

A seção 4.2 foi conferida num stack novo (`docker compose -p ...` com volumes vazios): os passos 1 a 7 passaram pelo nginx do web, e a entrada com o pátio lotado devolveu 409.

### 5.2. Decisões em aberto

> **Atualização (2026-11-17):** as decisões 1 a 7 foram fechadas pela especificação `.kiro/specs/api-rabbitmq-exit-barrier/` (`design.md`, D-A1 a D-A12) e registradas em `docs/DECISOES.md` (D21 e D22): as duas cancelas ficam, o pagamento mantém `POST /exits/{id}/pay` e `EXIT_PAYMENT`, a saída por placa recebe a placa em JSON, sem OCR, as páginas novas substituem as antigas (com redirecionamento) e a API não recria o contador na subida. A 10 foi feita, exceto os `scripts/analise-ocr/rek_dump*.py`. A 9 (Kubernetes) foi feita em 2026-11-22. Continua aberta a 8 (deploy do TP1). A tabela abaixo fica como registro.

| # | O que definir | Opções e impacto |
|---|---|---|
| 1 | **Manter as duas cancelas (`PAID` retém a vaga, `EXITED` libera)?** É escopo extra, fora da rubrica de avaliação | **Cortar:** o pagamento continua liberando a vaga, a API só migra para RabbitMQ/RustFS/DynamoDB Local e o plano encolhe (itens 2, 3, 4, 7, 8 abaixo caem). **Manter:** exige novo status `EXITED`, rotas `/barrier/exit/*`, página `/cancela-saida` e mudanças no contador |
| 2 | **Contador de vagas com `PAID` retendo a vaga** (só vale se a 1 for "manter") | A reconstrução do `spots:available` a partir do Postgres, o `DELETE /sessions/{id}` (hoje faz `INCR` só se `PARKED`) e o `CountActive` precisam tratar `PAID` como ocupando vaga, e `EXITED` como livre |
| 3 | **Saída por placa com foto da cancela** | O plano manda aceitar multipart com foto, mas a regra de arquitetura proíbe OCR síncrono na entrada. Opções: (a) a saída só aceita placa digitada ou lida do banco (`plate_status` `READ`/`MANUAL`) e o ticket de papel é o fallback; (b) a saída publica uma tarefa de OCR própria. Placas repetidas entre sessões ativas também precisam de regra |
| 4 | **Nomes de contrato** | Pagamento: manter `POST /exits/{id}/pay` ou renomear para `/sessions/{id}/pay`. Auditoria: manter `EXIT_PAYMENT` ou usar `PAYMENT_RECEIVED`. Mudar quebra o web e os docs |
| 5 | **Páginas do frontend** | Criar `/cancela-entrada`, `/pagamento`, `/cancela-saida` como rotas novas ou renomear as páginas atuais (totem, caixa) |
| 6 | **Contador de vagas na subida** | O `redis` já está no compose; a `api` ainda precisa entrar com `depends_on` dele. Definir se a API recria o contador no start (hoje a chave ausente é reconstruída na primeira leitura de `GET /spots/available`) |
| 7 | **Quem é dono da topologia do RabbitMQ** | Já decidido para o worker: `infra/rabbitmq/definitions.json`, declaração passiva. A API precisa seguir a mesma regra (ver Componente 2) |
| 8 | **Deploy do TP1 na AWS** | O `user_data` ainda exporta `SQS_QUEUE_URL` e `OCR_ENGINE`, e a imagem nova do worker não consome SQS. Decidir: abandonar o deploy do TP1 (o TP2 o substitui) ou manter um segundo caminho |
| 9 | **Bônus Kubernetes** | Fazer ou não (+1 ponto). O worker já não depende de Redis. Cada manifesto precisa seguir as variáveis novas |
| 10 | **Docs a atualizar** | `docs/DECISOES.md` (D1 e D10 sobre Rekognition viram histórico; registrar a decisão do RabbitMQ), `docs/ARQUITETURA.md`, `scripts/analise-ocr/rek_dump*.py` (importam o módulo removido) |
