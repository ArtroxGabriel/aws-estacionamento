# Plano: migração do Worker para o Trabalho 2 (RabbitMQ + storage S3 local)

> Detalha o **Componente 3** do [PLANO-TP2.md](PLANO-TP2.md). Escopo: só `worker/` e o serviço `worker` no compose.
> Branch: `feat/worker-rabbitmq-migration`.
>
> **Revisado em 2026-11-16** depois que a foto opcional entrou na `main` (PR #7, decisão D20 em [DECISOES.md](DECISOES.md), plano em `PLANO-FOTO-OPCIONAL.md`). O worker deixou de ser dono da vaga: não usa mais Redis nem altera `status`; só grava a placa (`plate_status` `PENDING` → `READ` | `UNREADABLE`). Onde o [PLANO-TP2.md](PLANO-TP2.md) ainda descreve o worker mudando a sessão para `PARKED`, marcando `FAILED` ou fazendo `DECR` no Redis, vale a D20.
>
> **Antes de implementar:** trazer a `main` para esta branch (rebase ou merge). A branch só tem este documento, então não há conflito.

## 1. Objetivo

Trocar o transporte do worker de **AWS SQS** para **RabbitMQ**, apontar os clientes S3 e DynamoDB para os containers locais e remover o Amazon Rekognition, **preservando as garantias que o worker já tem**:

- idempotência (`UPDATE ... WHERE plate_status='PENDING'`);
- placa e auditoria numa transação só (RDS → DynamoDB → commit; qualquer falha faz rollback e a placa volta a `PENDING`);
- o worker nunca altera `status` nem o contador de vagas;
- novas tentativas para falha de infraestrutura e fila de mensagens mortas (DLQ);
- ao desistir de uma mensagem, a leitura é encerrada como `UNREADABLE` (nada fica "lendo placa" para sempre);
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
| Storage | S3 (`AWS_ENDPOINT_URL` comum a todos os clientes) | S3 compatível (RustFS) via `S3_ENDPOINT_URL` |
| Auditoria | DynamoDB via `AWS_ENDPOINT_URL` | DynamoDB via `DYNAMODB_ENDPOINT_URL` (vazio = AWS real) |
| OCR | `OCR_ENGINE`: Tesseract ou Rekognition + reserva | Só o pipeline local (OpenCV + Tesseract) |
| Redis | Não usa (removido na D20) | Não usa |
| Configuração | `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE` | `RABBITMQ_URL`, `RABBITMQ_QUEUE_NAME`, `S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL` |

**Não muda:** `ocr/locate.py`, `ocr/processor.py`, `ocr/clean.py`, `parser.py`, `storage/session_repo.py`, `storage/audit.py`, `storage/s3_store.py`, a regra de negócio do processamento (o que a D20 definiu) e o contrato da mensagem (`{"session_id": "<32 hex>", "s3_key": "photos/..."}`).

## 3. Decisões que precisam ser fechadas com a equipe

| # | Decisão | Recomendação | Por quê |
|---|---|---|---|
| D-W1 | Como fazer nova tentativa | **Fila de espera com TTL** (4.2), 3 tentativas | O plano geral manda `basic_nack(requeue=False)`, que joga a mensagem na DLQ na 1ª falha. `requeue=True` reentrega na hora, em laço, enquanto o serviço está fora do ar |
| D-W2 | Quem declara a topologia | **Arquivo `definitions.json` do RabbitMQ**, montado no compose | API e worker precisam declarar a fila com argumentos idênticos; se divergirem, o broker recusa com `PRECONDITION_FAILED`. Um arquivo só elimina o risco |
| D-W3 | Storage | Tratar como "S3 compatível"; o worker não depende do servidor, só do endpoint | A especificação cita o MinIO; o professor aceitou o RustFS (D11) |
| D-W4 | Política de leitura sem Rekognition | Voltar ao normalizador completo (correção por posição, até 2 trocas) | A regra "só leitura exata" (D10) existia para a reserva do Rekognition; sem ele, ser tão restrito derruba a taxa de leitura. Com a D20 uma leitura errada não bloqueia ninguém, mas atrapalha a saída por placa; conferir a taxa de placas falsas no dataset antes de fechar |

Este plano assume as recomendações. Se a equipe decidir diferente em D-W1 ou D-W2, só a seção 4 muda.

## 4. Desenho

### 4.1 Separar transporte de processamento

Hoje o `Poller` mistura o laço do SQS com a regra de negócio. A migração divide em dois:

```
worker/
├── worker.py      main(): configuração, montagem e sinais
├── processor.py   MessageProcessor: valida → consulta sessão → baixa foto → OCR →
│                  grava a placa + auditoria. Devolve Outcome (DELETE | RETAIN | POISON).
│                  É o _handle/_process/_apply_side_effects atual, sem nada de fila.
└── consumer.py    RabbitConsumer: conexão, consumo, ack, nova tentativa, DLQ, parada.
```

O `MessageProcessor` recebe `(body, attempt)` e não conhece RabbitMQ. Depois da D20 ele já é pequeno (não há mais Redis nem compensação), então a extração é quase só mover código: `_receive_count` sai e o `attempt` chega pronto. Isso mantém os testes de regra de negócio (`test_poller_side_effects.py`, `test_poison.py`) quase intactos e isola a troca de transporte.

Mapa de resultado → ação no RabbitMQ:

| Outcome | Significado | Ação |
|---|---|---|
| `DELETE` | Placa gravada (`READ`), foto ilegível (`UNREADABLE`), sessão inexistente ou `plate_status` diferente de `PENDING` | `basic_ack` |
| `RETAIN` | Falha transitória (S3, Postgres, DynamoDB, erro do OCR) ou corpo inválido, antes da 3ª tentativa | Publica na fila de espera com `x-attempt + 1`, depois `basic_ack` |
| `POISON` | Falhou na 3ª tentativa | O processador grava a auditoria `POISON_MESSAGE` e marca `UNREADABLE` (como hoje); o consumidor publica na DLQ com o motivo, depois `basic_ack` |

O ramo "entrega além do limite" (`receive_count > MAX_RECEIVE_COUNT`, que hoje cobre o redrive atrasado do SQS) deixa de existir: quem conta e quem move para a DLQ é o próprio worker.

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

- `ocr-processamento-fila`: durável; mensagens persistentes (`delivery_mode=2`). Foi acrescentado `x-dead-letter-routing-key=ocr-processamento-fila-dlq` como rede de segurança: uma mensagem rejeitada sem requeue vai para a DLQ em vez de sumir.
- `ocr-processamento-fila-retry`: durável, `x-message-ttl=30000`, `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=ocr-processamento-fila`. Ninguém consome dela: a mensagem expira e volta para a fila principal.
- `ocr-processamento-fila-dlq`: durável, sem consumidor; inspecionada no painel (`:15672`).
- `MAX_ATTEMPTS = 3`, o mesmo valor de hoje (`MAX_RECEIVE_COUNT`). O worker lê `x-attempt` (ausente = 1).
- Publicação com *publisher confirms* antes do `ack` da original. Se o processo morrer entre os dois, a mensagem é entregue de novo; a idempotência já cobre isso.
- Enquanto a mensagem espera na fila de retry, o carro não é afetado: a sessão já está `PARKED` e o ticket vale (D20). Só a placa demora a aparecer.
- Reprocessar uma mensagem da DLQ não grava a placa, porque a sessão já está `UNREADABLE`. Para tentar de novo, voltar `plate_status` para `PENDING` no banco antes de republicar.

### 4.3 Conexão e consumo (`pika`)

- `pika.BlockingConnection` com `heartbeat=120` e `blocked_connection_timeout=60`. O pior caso de uma mensagem (3 tentativas de S3 + OCR de 10 s + Postgres + DynamoDB) fica abaixo de 60 s; o heartbeat precisa ser maior que isso, porque o processamento bloqueia a thread da conexão.
- `basic_qos(prefetch_count=1)`: uma mensagem por vez por worker; escala adicionando réplicas.
- Reconexão: laço externo com back-off exponencial até 30 s em `AMQPConnectionError` (mesma política do receive atual).
- Topologia: `queue_declare(passive=True)` na subida; se a fila não existir, sair com código 2 e mensagem clara.

### 4.4 Parada graciosa

- `SIGTERM`/`SIGINT` → `connection.add_callback_threadsafe(channel.stop_consuming)`.
- A mensagem em processamento termina e é confirmada; depois o canal e as conexões são fechados.
- Não é mais preciso "devolver o lote" (`_release`): com `prefetch=1` não há lote, e o broker reentrega sozinho o que não recebeu `ack`.
- Some o problema do long poll abandonado (`_Interrupted`, `_interruptibly`); `docker stop` passa a levar só o tempo da mensagem em andamento. Manter `tini` e `stop_grace_period: 30s`.

### 4.5 Configuração

| Variável | Obrigatória | Observação |
|---|---|---|
| `RABBITMQ_URL` | sim | `amqp://` ou `amqps://`; usuário e senha nunca vão para o log |
| `RABBITMQ_QUEUE_NAME` | não | Padrão `ocr-processamento-fila`; as filas `-retry` e `-dlq` derivam dele |
| `S3_ENDPOINT_URL` | não | Vazio = AWS |
| `S3_BUCKET_NAME` | sim | |
| `DYNAMODB_ENDPOINT_URL` | não | Vazio = DynamoDB real da AWS (exigência 1.a da especificação) |
| `DYNAMODB_TABLE_NAME` | sim | |
| `DATABASE_URL`, `AWS_REGION` | sim | |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | não (par) | Credenciais do storage local e do DynamoDB Local; `AWS_SESSION_TOKEN` continua opcional |

Removidas: `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE`. (`REDIS_URL` já saiu na `main`.)

Cliente S3 com `Config(s3={"addressing_style": "path"})`: storage local não resolve `bucket.host`.

## 5. Tarefas

### Fase 0 — Atualizar a branch
- [x] 0.1 Trazer a `main` (PR #7) para a branch e rodar `uv run pytest` em `worker/` para partir de uma base verde.

### Fase 1 — Refatoração sem mudar comportamento
- [x] 1.1 Extrair `MessageProcessor` para `worker/processor.py` (o `_handle`, `_process`, `_apply_side_effects`, `_mark_unreadable_best_effort` e as auditorias de melhor esforço), recebendo `attempt` no lugar do dicionário do SQS.
- [x] 1.2 Ajustar `test_poller_side_effects.py` e `test_poison.py` para o `MessageProcessor`. **Todos os testes continuam verdes com o SQS ainda no lugar.**

### Fase 2 — Configuração e clientes
- [x] 2.1 `config.py`: variáveis da seção 4.5; validar esquema de `RABBITMQ_URL`; redação de credenciais.
- [x] 2.2 `storage/clients.py`: endpoint por serviço (`S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL`), estilo *path* no S3; remover `sqs_client` e `rekognition_client`.
- [x] 2.3 Atualizar `test_config.py` (incluindo a propriedade "toda variável faltante é reportada").

### Fase 3 — Transporte RabbitMQ
- [x] 3.1 `pyproject.toml`: adicionar `pika`; `uv lock`.
- [x] 3.2 `worker/consumer.py`: conexão, `prefetch=1`, callback, mapa Outcome → ação, publicação na fila de espera e na DLQ com confirmação.
- [x] 3.3 Reconexão com back-off e parada graciosa (4.3 e 4.4).
- [x] 3.4 `worker.py`: `main()` monta `MessageProcessor` + `RabbitConsumer`; remover o laço do SQS (`_receive`, `_delete`, `_release`, `_Interrupted`).
- [x] 3.5 Testes com canal falso (`tests/fakes.py`), substituindo `test_shutdown.py`: ack no sucesso; fila de espera com `x-attempt` incrementado; DLQ + auditoria + `UNREADABLE` na 3ª falha; parada com mensagem em andamento; reconexão.

### Fase 4 — Remover o Rekognition
- [x] 4.1 Apagar `ocr/rekognition.py` e `tests/test_rekognition.py`; `ocr = extract_text` fixo.
- [x] 4.2 Confirmar D-W4 e ajustar os testes do normalizador se a política mudar. *Nada a ajustar: a política restritiva (só leitura exata) vivia no `HybridOcr`, que foi removido; o normalizador padrão já é o completo.*
- [x] 4.3 Remover `OCR_ENGINE` de scripts e documentos (`scripts/`, `Taskfile.yaml`). *`scripts/ocr_local.py` perdeu o `--engine` e a task `ocr:dataset` perdeu o `ENGINE`. `scripts/analise-ocr/rek_dump*.py` importam o módulo removido; o README da pasta diz como recuperá-lo do histórico. O `user_data` do Terraform (`infra/`) ainda exporta `SQS_QUEUE_URL` e `OCR_ENGINE`: o deploy na AWS do TP1 não funciona mais com esta imagem do worker (ver seção 6).*

### Fase 5 — Imagem e compose
- [x] 5.1 `Dockerfile`: copiar `processor.py` e `consumer.py` (o `COPY` lista os arquivos um a um) e trocar o comentário do `STOPSIGNAL`, que ainda fala do long poll do SQS.
- [x] 5.2 `infra/rabbitmq/definitions.json` com as 3 filas; montar no serviço `rabbitmq` (D-W2). *O arquivo também declara o vhost `/` e o usuário `guest`: o broker aborta a subida ("Please create virtual host "/" prior to importing definitions") se o vhost não estiver nas definições. O `load_definitions` entra por `infra/rabbitmq/rabbitmq.conf`.*
- [x] 5.3 Serviço `worker` no compose: `build: ../worker`, variáveis, `depends_on` com `condition: service_healthy` para `postgres`, `rabbitmq`, storage e `dynamodb-local` (**sem `redis`**), e `service_completed_successfully` para `createbuckets` e `createtables`; `stop_grace_period: 30s`; `restart: unless-stopped`.
- [x] 5.4 Apagar `worker/floci.env`.

### Fase 6 — Verificação
- [x] 6.1 `uv run pytest`, `ruff check`, `ruff format --check`.
- [x] 6.2 Suíte dentro da imagem (testes que precisam do binário do Tesseract).
- [x] 6.3 Ponta a ponta no compose: publicar uma mensagem para uma sessão `PARKED` com `plate_status=PENDING` e conferir `license_plate` gravada, `plate_status=READ`, `status` ainda `PARKED`, `spots:available` **inalterado**, auditoria `OCR_PROCESSING`, fila vazia. Enquanto a API não estiver migrada, ver a seção 6 para como criar a sessão.
- [x] 6.4 Falha transitória: parar o container do `dynamodb-local`, publicar uma mensagem, ver a placa continuar `PENDING` e a mensagem na fila de espera; religar e ver o processamento concluir sozinho, com uma única auditoria.
- [x] 6.5 DLQ: manter o storage parado por 3 tentativas e conferir a mensagem na DLQ, a auditoria `POISON_MESSAGE` e a sessão com `plate_status=UNREADABLE`, ainda `PARKED`.
- [x] 6.6 Parada: `docker compose stop worker` durante um processamento; a mensagem termina ou é reentregue, nunca se perde.
- [x] 6.7 Placa ilegível: `plate_status=UNREADABLE`, `status` ainda `PARKED`, auditoria `OCR_FAILED`, mensagem confirmada (sem nova tentativa).
- [x] 6.8 Idempotência: publicar a mesma mensagem duas vezes (uma auditoria só) e publicar para uma sessão com placa `MANUAL` (confirmada sem efeito; a placa digitada não é sobrescrita).

### Fase 7 — Documentação
- [x] 7.1 `worker/README.md` e `worker/AGENTS.md`: RabbitMQ, variáveis e comandos do compose.
- [x] 7.2 `.kiro/specs/python-ocr-worker`: requisitos 1, 9, 12, 13 e 16 (SQS → RabbitMQ), 14 e 15 (variáveis e endpoint por serviço) e o design do transporte. Os requisitos 6, 7 e 10 já foram atualizados pela D20.
- [ ] 7.3 Prints para o README do projeto: fila no painel do RabbitMQ e log do worker (evidência da rubrica).

### Resultado da verificação (2026-11-16)

Feita com `docker compose up -d --build worker` em `infra/`, contra RabbitMQ, RustFS, Postgres (migrations da API aplicadas à mão) e DynamoDB Local reais, com fotos de `examples/fotos`:

| Item | Resultado |
|---|---|
| 6.1 | `uv run pytest`: 143 passaram, 3 puladas. `ruff check` e `ruff format --check` limpos |
| 6.2 | Suíte dentro da imagem (com o binário do Tesseract): 146 passaram, 0 puladas |
| 6.3 | Foto legível → `PARKED`, `plate_status=READ`, placa `LSN4I49`, auditoria `OCR_PROCESSING`, filas vazias. O contador de vagas não foi exercitado: o compose ainda não tem a API nem o Redis |
| 6.4 | Storage parado: a tentativa 1 falhou (`S3 download failed: unavailable`), a mensagem foi para a fila de espera e a placa ficou `PENDING`; com o storage de volta, a mensagem voltou após o TTL e concluiu com uma única auditoria. Com o DynamoDB parado a própria escrita da auditoria se recuperou dentro da mesma tentativa (retentativas do `AuditLogger`) |
| 6.5 | Objeto inexistente (falha rápida): tentativas 1 e 2 na fila de espera, a 3ª na DLQ com `x-attempt=3` e `x-failure-reason`; auditorias `POISON_MESSAGE` e `OCR_FAILED`; sessão `UNREADABLE` e ainda `PARKED` |
| 6.6 | `docker stop` com 6 mensagens na fila: saiu em 1 s com código 0, 4 já processadas e 2 intactas na fila; ao religar, as 6 ficaram `READ` |
| 6.7 | Foto sem placa legível → `UNREADABLE`, `status` `PARKED`, auditoria `OCR_FAILED`, mensagem confirmada sem nova tentativa |
| 6.8 | A mesma mensagem publicada duas vezes gerou uma auditoria só; sessão `MANUAL` ficou intacta |

Com o storage inteiro parado, uma tentativa leva ~95 s (3 tentativas de download com timeout dentro do `S3Connector`); com a espera de 30 s, a mensagem chega à DLQ em alguns minutos, não em 90 s.

## 6. Dependências e riscos

| Item | Impacto | Mitigação |
|---|---|---|
| API ainda publica no SQS | Sem ponta a ponta completo até a API migrar. No compose (sem SQS) a entrada funciona, mas a API grava `plate_status=UNREADABLE` ao falhar a publicação, e o worker ignora sessões que não estão `PENDING` | Fases 1–5 e 6.1–6.2 independem da API. Em 6.3–6.8: enviar a foto ao storage (`mc cp`), criar a sessão por SQL (ou voltar `plate_status` para `PENDING`) e publicar pelo painel do RabbitMQ |
| Topologia declarada diferente pela API | `PRECONDITION_FAILED` na subida | D-W2: `definitions.json` único; API e worker só usam declaração passiva |
| Heartbeat menor que o tempo de uma mensagem | Broker derruba a conexão e reentrega (duplicata) | `heartbeat=120`; idempotência cobre a duplicata |
| Mensagem duplicada (reentrega) | Efeito duplo | Já coberto: `UPDATE ... WHERE plate_status='PENDING'` |
| Publicação na DLQ falha depois de a sessão virar `UNREADABLE` | A reentrega é confirmada como idempotente e a mensagem não chega à DLQ | Aceito: a auditoria `POISON_MESSAGE` já registra o motivo e nada fica pendente na sessão |
| Tesseract sozinho lê ~52% das fotos reais | Mais sessões sem placa | Deixou de bloquear o fluxo (D20): a sessão segue `PARKED` e o ticket paga; só a busca e a saída por placa ficam indisponíveis para essas sessões |
| Deploy na AWS do TP1 (`infra/`, `user_data`) ainda exporta `SQS_QUEUE_URL`/`OCR_ENGINE` e a API ainda publica no SQS | A imagem nova do worker não consome SQS: o deploy do TP1 com ela deixa de processar fotos | Aceito: o TP2 substitui o TP1; até a API migrar, o worker só é exercitado publicando no RabbitMQ. Quem precisar do TP1 usa as imagens antigas (commit `1a63463`) |
| Plano geral ([PLANO-TP2.md](PLANO-TP2.md)) ainda descreve o worker com `PARKED`/`FAILED`/`DECR` e `REDIS_URL` | Quem seguir o plano geral reintroduz o Redis no worker | Vale a D20 e este documento; atualizar o Componente 3 do plano geral |

## 7. Fora do escopo

- Migração da API e do frontend (Componentes 2 e 4 do plano geral).
- Contador de vagas e estados da sessão (`PAID` → `EXITED`): são da API desde a D20.
- OCR na cancela de saída.
- Manifestos Kubernetes do worker (bônus; entra depois do compose funcionando).
