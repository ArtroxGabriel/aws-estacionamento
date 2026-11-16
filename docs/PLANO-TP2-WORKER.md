# Plano: migração do Worker para o Trabalho 2 (RabbitMQ + storage S3 local)

> Detalha o **Componente 3** do [PLANO-TP2.md](PLANO-TP2.md). Escopo: só `worker/` e o serviço `worker` no compose.
> Branch: `feat/worker-rabbitmq-migration`.

## 1. Objetivo

Trocar o transporte do worker de **AWS SQS** para **RabbitMQ**, apontar os clientes S3 e DynamoDB para os containers locais e remover o Amazon Rekognition, **preservando as garantias que o worker já tem**:

- idempotência (`UPDATE ... WHERE status='PROCESSING'`);
- efeitos colaterais numa transação só (RDS → Redis → DynamoDB → commit, com rollback e compensação);
- contador de vagas que nunca é criado pelo worker (scripts Lua);
- novas tentativas para falha de infraestrutura e fila de mensagens mortas (DLQ);
- parada graciosa sem perder mensagem.

Rubrica coberta: *"Rabbit ou Kafka para fila de mensagens: 1,0 produção e consumo funcionando"*, *"Dockerfile do worker"* e *"redes e dependências no Compose"*.

## 2. Estado atual × alvo

| Aspecto | Hoje (`main`) | Alvo |
|---|---|---|
| Fila | SQS, long polling (`receive_message`, 20 s, lote de 10) | RabbitMQ, consumo por push (`basic_consume`), `prefetch_count=1` |
| Confirmação | `delete_message` | `basic_ack` |
| Nova tentativa | Não apagar; volta após 300 s (visibility timeout) | Republicar numa fila de espera com TTL (ver 4.2) |
| Contagem de entregas | `ApproximateReceiveCount` | Cabeçalho `x-attempt` gravado pelo próprio worker |
| DLQ | Redrive policy do SQS (`maxReceiveCount=3`) | Fila `ocr-processamento-fila-dlq`, publicada pelo worker na 3ª falha |
| Storage | S3 (`AWS_ENDPOINT_URL` comum a todos os clientes) | S3 compatível (MinIO/RustFS) via `S3_ENDPOINT_URL` |
| Auditoria | DynamoDB via `AWS_ENDPOINT_URL` | DynamoDB via `DYNAMODB_ENDPOINT_URL` (vazio = AWS real) |
| OCR | `OCR_ENGINE`: Tesseract ou Rekognition + reserva | Só o pipeline local (OpenCV + Tesseract) |
| Configuração | `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE` | `RABBITMQ_URL`, `RABBITMQ_QUEUE_NAME`, `S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL` |

**Não muda:** `ocr/locate.py`, `ocr/processor.py`, `ocr/clean.py`, `storage/session_repo.py`, `storage/spots.py`, `storage/audit.py`, `storage/s3_store.py` e o contrato da mensagem (`{"session_id": "<32 hex>", "s3_key": "photos/..."}`).

## 3. Decisões que precisam ser fechadas com a equipe

| # | Decisão | Recomendação | Por quê |
|---|---|---|---|
| D-W1 | Como fazer nova tentativa | **Fila de espera com TTL** (4.2), 3 tentativas | O plano geral manda `basic_nack(requeue=False)`, que joga a mensagem na DLQ na 1ª falha. `requeue=True` reentrega na hora, em laço, enquanto o serviço está fora do ar |
| D-W2 | Quem declara a topologia | **Arquivo `definitions.json` do RabbitMQ**, montado no compose | API e worker precisam declarar a fila com argumentos idênticos; se divergirem, o broker recusa com `PRECONDITION_FAILED`. Um arquivo só elimina o risco |
| D-W3 | Storage | Tratar como "S3 compatível"; o worker não depende de ser MinIO ou RustFS | A especificação pede a imagem do **MinIO**; a troca fica só no compose |
| D-W4 | Política de leitura sem Rekognition | Voltar ao normalizador completo (correção por posição, até 2 trocas) | A regra "só leitura exata" (D10) existia para a reserva do Rekognition; sem ele, ser tão restrito derruba a taxa de leitura |

Este plano assume as recomendações. Se a equipe decidir diferente em D-W1 ou D-W2, só a seção 4 muda.

## 4. Desenho

### 4.1 Separar transporte de processamento

Hoje o `Poller` mistura o laço do SQS com a regra de negócio. A migração divide em dois:

```
worker/
├── worker.py      main(): configuração, montagem e sinais
├── processor.py   MessageProcessor: valida → consulta sessão → baixa foto → OCR →
│                  efeitos transacionais. Devolve Outcome (DELETE | RETAIN | POISON).
│                  É o _process/_apply_side_effects atual, sem nada de fila.
└── consumer.py    RabbitConsumer: conexão, consumo, ack, nova tentativa, DLQ, parada.
```

O `MessageProcessor` recebe `(body, attempt)` e não conhece RabbitMQ. Isso mantém os testes de regra de negócio (`test_poller_side_effects.py`, `test_poison.py`) quase intactos e isola a troca de transporte.

Mapa de resultado → ação no RabbitMQ:

| Outcome | Significado | Ação |
|---|---|---|
| `DELETE` | Sucesso, sessão já processada ou placa ilegível (`FAILED`) | `basic_ack` |
| `RETAIN` | Falha transitória (S3, Postgres, Redis, DynamoDB) | Publica na fila de espera com `x-attempt + 1`, depois `basic_ack` |
| `POISON` | Falhou na 3ª tentativa, ou corpo inválido na 3ª | Auditoria `POISON_MESSAGE`, publica na DLQ com o motivo, depois `basic_ack` |

### 4.2 Topologia e novas tentativas

```
API ──publish──► [ocr-processamento-fila] ──► worker
                        ▲                       │ falha transitória
                        │ expira TTL (30 s)     ▼
                 [ocr-processamento-fila-retry] ◄── publish (x-attempt = n+1)
                                                │ 3ª falha
                                                ▼
                                   [ocr-processamento-fila-dlq]
```

- `ocr-processamento-fila`: durável; mensagens persistentes (`delivery_mode=2`).
- `ocr-processamento-fila-retry`: durável, `x-message-ttl=30000`, `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=ocr-processamento-fila`. Ninguém consome dela: a mensagem expira e volta para a fila principal.
- `ocr-processamento-fila-dlq`: durável, sem consumidor; inspecionada no painel (`:15672`).
- `MAX_ATTEMPTS = 3`, o mesmo valor de hoje. O worker lê `x-attempt` (ausente = 1).
- Publicação com *publisher confirms* antes do `ack` da original. Se o processo morrer entre os dois, a mensagem é entregue de novo; a idempotência já cobre isso.

### 4.3 Conexão e consumo (`pika`)

- `pika.BlockingConnection` com `heartbeat=120` e `blocked_connection_timeout=60`. O pior caso de uma mensagem (3 tentativas de S3 + OCR de 10 s + bancos) fica abaixo de 60 s; o heartbeat precisa ser maior que isso, porque o processamento bloqueia a thread da conexão.
- `basic_qos(prefetch_count=1)`: uma mensagem por vez por worker; escala adicionando réplicas.
- Reconexão: laço externo com back-off exponencial até 30 s em `AMQPConnectionError` (mesma política do receive atual).
- Topologia: `queue_declare(passive=True)` na subida; se a fila não existir, sair com código 2 e mensagem clara.

### 4.4 Parada graciosa

- `SIGTERM`/`SIGINT` → `connection.add_callback_threadsafe(channel.stop_consuming)`.
- A mensagem em processamento termina e é confirmada; depois o canal e as conexões são fechados.
- Não é mais preciso "devolver o lote": com `prefetch=1` não há lote, e o broker reentrega sozinho o que não recebeu `ack`.
- Some o problema do long poll abandonado; `docker stop` passa a levar só o tempo da mensagem em andamento. Manter `tini` e `stop_grace_period: 30s`.

### 4.5 Configuração

| Variável | Obrigatória | Observação |
|---|---|---|
| `RABBITMQ_URL` | sim | `amqp://` ou `amqps://`; usuário e senha nunca vão para o log |
| `RABBITMQ_QUEUE_NAME` | não | Padrão `ocr-processamento-fila`; as filas `-retry` e `-dlq` derivam dele |
| `S3_ENDPOINT_URL` | não | Vazio = AWS |
| `S3_BUCKET_NAME` | sim | |
| `DYNAMODB_ENDPOINT_URL` | não | Vazio = DynamoDB real da AWS (exigência 1.a da especificação) |
| `DYNAMODB_TABLE_NAME` | sim | |
| `DATABASE_URL`, `REDIS_URL`, `AWS_REGION` | sim | `REDIS_URL` com esquema `redis://` (a API usa `host:porta`; no compose cada serviço recebe o seu) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | não (par) | Credenciais do storage local e do DynamoDB Local |

Removidas: `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE`.

Cliente S3 com `Config(s3={"addressing_style": "path"})`: storage local não resolve `bucket.host`.

## 5. Tarefas

### Fase 1 — Refatoração sem mudar comportamento
- [ ] 1.1 Extrair `MessageProcessor` para `worker/processor.py` (o `_process`, `_apply_side_effects`, compensação e marcação de `FAILED`), recebendo `attempt` no lugar do dicionário do SQS.
- [ ] 1.2 Ajustar `test_poller_side_effects.py` e `test_poison.py` para o `MessageProcessor`. **Todos os testes continuam verdes com o SQS ainda no lugar.**

### Fase 2 — Configuração e clientes
- [ ] 2.1 `config.py`: variáveis da seção 4.5; validar esquema de `RABBITMQ_URL`; redação de credenciais.
- [ ] 2.2 `storage/clients.py`: endpoint por serviço (`S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL`), estilo *path* no S3; remover `sqs_client` e `rekognition_client`.
- [ ] 2.3 Atualizar `test_config.py` (incluindo a propriedade "toda variável faltante é reportada").

### Fase 3 — Transporte RabbitMQ
- [ ] 3.1 `pyproject.toml`: adicionar `pika`; `uv lock`.
- [ ] 3.2 `worker/consumer.py`: conexão, `prefetch=1`, callback, mapa Outcome → ação, publicação na fila de espera e na DLQ com confirmação.
- [ ] 3.3 Reconexão com back-off e parada graciosa (4.3 e 4.4).
- [ ] 3.4 `worker.py`: `main()` monta `MessageProcessor` + `RabbitConsumer`; remover o laço do SQS.
- [ ] 3.5 Testes com canal falso (`tests/fakes.py`): ack no sucesso; fila de espera com `x-attempt` incrementado; DLQ + auditoria na 3ª falha; parada com mensagem em andamento; reconexão.

### Fase 4 — Remover o Rekognition
- [ ] 4.1 Apagar `ocr/rekognition.py` e `tests/test_rekognition.py`; `ocr = extract_text` fixo.
- [ ] 4.2 Confirmar D-W4 e ajustar os testes do normalizador se a política mudar.
- [ ] 4.3 Remover `OCR_ENGINE` de scripts e documentos (`scripts/`, `Taskfile.yaml`).

### Fase 5 — Imagem e compose
- [ ] 5.1 `Dockerfile`: copiar `processor.py` e `consumer.py` (o `COPY` lista os arquivos um a um).
- [ ] 5.2 `infra/rabbitmq/definitions.json` com as 3 filas; montar no serviço `rabbitmq` (D-W2).
- [ ] 5.3 Serviço `worker` no compose: `build: ../worker`, variáveis, `depends_on` com `condition: service_healthy` para `postgres`, `redis`, `rabbitmq`, storage e `dynamodb-local`, e `service_completed_successfully` para `createbuckets` e `createtables`; `stop_grace_period: 30s`; `restart: unless-stopped`.
- [ ] 5.4 Apagar `worker/floci.env`.

### Fase 6 — Verificação
- [ ] 6.1 `uv run pytest`, `ruff check`, `ruff format --check`.
- [ ] 6.2 Suíte dentro da imagem (testes que precisam do binário do Tesseract).
- [ ] 6.3 Ponta a ponta no compose: publicar uma mensagem (pela API quando ela estiver migrada, ou pelo painel do RabbitMQ) e conferir sessão `PARKED`, `spots:available` −1, auditoria `OCR_PROCESSING`, fila vazia.
- [ ] 6.4 Falha transitória: parar o container do Redis, enviar uma foto, ver a mensagem na fila de espera; religar o Redis e ver o processamento concluir sozinho, com o contador correto.
- [ ] 6.5 DLQ: manter o storage parado por 3 tentativas e conferir a mensagem na DLQ com a auditoria `POISON_MESSAGE`.
- [ ] 6.6 Parada: `docker compose stop worker` durante um processamento; a mensagem termina ou é reentregue, nunca se perde.
- [ ] 6.7 Placa ilegível: sessão `FAILED`, auditoria `OCR_FAILED`, mensagem confirmada (sem nova tentativa).

### Fase 7 — Documentação
- [ ] 7.1 `worker/README.md` e `worker/AGENTS.md`: RabbitMQ, variáveis e comandos do compose.
- [ ] 7.2 `.kiro/specs/python-ocr-worker`: requisitos 1, 9, 12, 13 e 16 (SQS → RabbitMQ) e o design do transporte.
- [ ] 7.3 Prints para o README do projeto: fila no painel do RabbitMQ e log do worker (evidência da rubrica).

## 6. Dependências e riscos

| Item | Impacto | Mitigação |
|---|---|---|
| API ainda publica no SQS | Sem ponta a ponta completo até a API migrar | Fases 1–5 e 6.1–6.2 independem da API; em 6.3 publicar pelo painel do RabbitMQ |
| Topologia declarada diferente pela API | `PRECONDITION_FAILED` na subida | D-W2: `definitions.json` único; API e worker só usam declaração passiva |
| Heartbeat menor que o tempo de uma mensagem | Broker derruba a conexão e reentrega (duplicata) | `heartbeat=120`; idempotência cobre a duplicata |
| Mensagem duplicada (reentrega) | Efeito duplo | Já coberto: `UPDATE ... WHERE status='PROCESSING'` |
| Tesseract sozinho lê ~52% das fotos reais | Mais sessões `FAILED` | Fluxo do caixa já existe; ver o plano da foto opcional (`feat/photo-optional-ticket-flow`), que remove a dependência do OCR |
| Fases 1 e 3 tocam `worker.py` inteiro | Conflito com outras branches que mexam no worker | Mesclar esta branch antes da foto opcional, ou fazer a foto opcional sobre ela |

## 7. Fora do escopo

- Migração da API e do frontend (Componentes 2 e 4 do plano geral).
- OCR na cancela de saída.
- Manifestos Kubernetes do worker (bônus; entra depois do compose funcionando).
