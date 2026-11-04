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
   - **Cancela de Entrada (`/cancela-entrada`):** Câmera tira foto, salva no RustFS, cria sessão `PROCESSING` no Postgres, enfileira no RabbitMQ, emite ticket em papel (com ID) e abre a cancela de entrada. O worker OCR extrai a placa com o modelo open-source, atualiza para `PARKED` e decrementa a vaga no Redis.
   - **Terminal de Pagamento / Caixa (`/pagamento`):** Motorista paga o ticket ou placa. O status vai para `PAID`. **A vaga continua retida no Redis** (o carro ainda está na vaga!). O ticket é habilitado para saída.
   - **Cancela de Saída (`/cancela-saida`):** Câmera tenta ler a placa. Se a placa tiver pagamento confirmado (`PAID`), a cancela abre imediatamente. Se a placa falhar ou estiver ilegível, o motorista insere o ID do ticket em papel. Ao validar, o status vai para `EXITED`, a cancela abre, a vaga é finalmente liberada no Redis (`INCR spots:available`) e o evento é auditado.
8. **Orquestração Única com Docker Compose:** Todos os 8 serviços sobem via `docker compose up -d` com verificação de saúde (`healthcheck`) e dependências determinísticas.
9. **Bônus Kubernetes (`k8s/`):** Manifestos YAML completos testados localmente via **k3s** com Deployments, Services (NodePort/ClusterIP), ConfigMaps e Secrets (+1 ponto extra).

```mermaid
flowchart TD
    subgraph Entrada["1. Cancela de Entrada"]
        CameraIn["Câmera Entrada"] -->|Foto| API_Entry["POST /entries"]
        API_Entry -->|1. Salva Foto| RustFS[("RustFS (S3)")]
        API_Entry -->|2. Sessão 'PROCESSING'| PG[("PostgreSQL")]
        API_Entry -->|3. Publica Tarefa| RabbitMQ{{"RabbitMQ"}}
        API_Entry -->|4. Log ENTRY| DynamoDB[("DynamoDB")]
        API_Entry -->|5. Imprime Ticket ID| TotemIn["Cancela Abre + Ticket Impresso"]
    end

    subgraph WorkerOCR["2. Worker Assíncrono (Python Open-Source)"]
        RabbitMQ -->|Consome Mensagem| Worker["Worker Python"]
        Worker -->|Baixa Foto| RustFS
        Worker -->|OpenCV Localização + Tesseract OCR| Worker
        Worker -->|Status 'PARKED' + Placa| PG
        Worker -->|DECR spots:available| Redis[("Redis")]
        Worker -->|Log OCR_PROCESSING| DynamoDB
    end

    subgraph Caixa["3. Terminal de Pagamento"]
        Motorista["Motorista"] -->|Paga Ticket/Placa| API_Pay["POST /sessions/{id}/pay"]
        API_Pay -->|Status 'PAID' (Vaga mantida)| PG
        API_Pay -->|Log PAYMENT_RECEIVED| DynamoDB
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

> [!WARNING]
> **Atendimento às Regras de Avaliação do Trabalho 2:**
> O DynamoDB é mantido para auditoria conforme exigência 1.a da especificação dos professores (*"Continuar usando o serviço de banco de dados não-relacional da AWS"*). No Docker Compose, incluiremos o `dynamodb-local` para que o projeto rode 100% autônomo e offline em qualquer máquina, permitindo apontar para a AWS real bastando exportar credenciais válidas.

---

## 3. Mudanças Propostas por Componente

### Componente 1: Infraestrutura e Orquestração Local (`docker-compose.yml` e `Taskfile.yaml`)

#### [NEW] `docker-compose.yml`
Substitui a dependência do Floci por containers oficiais e especializados:
- `rustfs`: Imagem `rustfs/rustfs:latest`, volumes persistentes, portas 9000 (API S3) e 9001 (Web Console).
- `postgres`: Imagem `postgres:16-alpine`, banco `estacionamento`, volume `pgdata`, porta 5432, `healthcheck` via `pg_isready`.
- `rabbitmq`: Imagem `rabbitmq:3-management-alpine`, portas 5672 (AMQP) e 15672 (Management), volume `rabbitmq_data`, `healthcheck` via `rabbitmq-diagnostics ping`.
- `redis`: Imagem `redis:7-alpine`, porta 6379, volume `redis_data`, `healthcheck` via `redis-cli ping`.
- `dynamodb-local`: Imagem `amazon/dynamodb-local:latest`, porta 8000, volume `dynamo_data`.
- `api`: Build `./api`, expõe 8080, `depends_on` de postgres, rabbitmq, rustfs, redis e dynamodb-local com `condition: service_healthy`.
- `worker`: Build `./worker`, consome rabbitmq, conecta ao postgres, rustfs, redis e dynamodb-local.
- `web`: Build `./web`, expõe 3000 (Nginx servindo React e roteando `/api/` para `http://api:8080/`).

#### [MODIFY] `Taskfile.yaml`
Atualizar as tasks de automação:
- `task up` / `task down` / `task logs`: Controla o `docker compose` unificado.
- `task test`: Executa testes da API Go, do Worker Python e do Frontend Web.
- `task k8s:k3s:up` / `task k8s:apply` / `task k8s:delete`: Comandos para subir e testar os manifestos no cluster local k3s.

---

### Componente 2: API Backend (`api/`)

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
// Conecta, assegura fila durável "ocr-processamento-fila" com DLQ, e publica mensagens em JSON com persistent delivery.
```

#### [MODIFY] `api/internal/repository/s3.go`
- Suporte a auto-criação do bucket `estacionamento-fotos-veiculos` no RustFS caso não exista durante a inicialização.

#### [MODIFY] `api/internal/repository/postgres.go`
- Adicionar métodos ao repositório:
  - `MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error)`
  - `FindByPlateActive(ctx context.Context, plate string) (*model.Session, error)`
  - Atualizar `CountActive` para incluir sessões com `status IN ('PROCESSING', 'PARKED', 'PAID')`.

#### [MODIFY] `api/internal/service/parking.go`
- Modificar `PayExit`: agora altera o status para `PAID`, preenche `amount_paid`, grava auditoria `PAYMENT_RECEIVED`, mas **não incrementa** o contador de vagas no Redis!
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
  - `POST /sessions/{id}/pay`: Processa o pagamento no caixa (status $\rightarrow$ `PAID`).
- Atualizar documentação e testes unitários/integrados.

---

### Componente 3: Worker de Visão Computacional (`worker/`)

#### [MODIFY] `worker/pyproject.toml`
- Adicionar `pika` para comunicação AMQP com o RabbitMQ.

#### [MODIFY] `worker/config.py`
- Adicionar `RABBITMQ_URL` (default `amqp://guest:guest@rabbitmq:5672/`).
- Fixar `OCR_ENGINE=tesseract` como padrão definitivo, removendo opções ou chamadas ao Rekognition.
- Manter `S3_BUCKET_NAME`, `DATABASE_URL`, `REDIS_URL`, `DYNAMODB_TABLE_NAME`.

#### [MODIFY] `worker/storage/clients.py`
- Remover o factory do cliente Rekognition (`rekognition_client`), mantendo apenas S3 (apontando para RustFS) e DynamoDB.

#### [MODIFY] `worker/worker.py`
- Eliminar importações e chamadas ao `HybridOcr` / `rekognition_client`.
- Fixar `ocr = extract_text` (pipeline puramente open source de `ocr/processor.py` + `ocr/locate.py`).
- Substituir o poller SQS por consumidor AMQP `pika`:
  - Conexão e canal persistentes com RabbitMQ.
  - Declaração de fila durável `ocr-processamento-fila`.
  - Configuração de prefetch (`basic_qos(prefetch_count=1)`).
  - Callback de consumo:
    1. Parseia a mensagem JSON `{"session_id", "s3_key"}`.
    2. Baixa a foto do RustFS (usando cliente S3 apontado para RustFS).
    3. Executa OCR local com Tesseract + OpenCV (e normalização Mercosul 4 países).
    4. Se identificada: atualiza status para `PARKED` no PostgreSQL, decrementa vaga no Redis (`DECR spots:available`), grava auditoria `OCR_PROCESSING` no DynamoDB, e envia `basic_ack`.
    5. Se placa ilegível: marca sessão como `FAILED` no PostgreSQL, grava auditoria `OCR_FAILED`, e envia `basic_ack` (liberando a fila para que o operador resolva no caixa).
    6. Se erro inesperado de infraestrutura: envia `basic_nack(requeue=False)` roteando para a DLQ após esgotamento.

---

### Componente 4: Frontend Web & Totens (`web/`)

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
   - **Passo 1 (Cancela de Entrada):** Acessar `/cancela-entrada`, enviar foto de veículo. Verificar que a cancela abre, ticket ID é gerado, foto é salva no RustFS, e mensagem trafega pelo RabbitMQ.
   - **Passo 2 (Worker OCR Local):** Verificar nos logs do worker que a mensagem é consumida, o modelo local OpenCV + Tesseract extrai a placa sem nenhuma chamada para a AWS, sessão passa para `PARKED` e vagas disponíveis no painel diminuem em 1.
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
