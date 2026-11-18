# OCR Worker

Worker assíncrono em Python 3.14 (gerenciado por `uv`) que consome a fila RabbitMQ
`ocr-processamento-fila`, baixa a foto do storage S3, extrai a placa com OpenCV +
Tesseract, grava a placa na sessão no Postgres (`plate_status`) e registra auditoria
no DynamoDB.

O worker só enriquece a sessão: o carro já está `PARKED`, com a vaga reservada
pela API, e o ticket já vale. Ele não altera `status` nem o contador de vagas
(não usa Redis). Ver `docs/DECISOES.md`, D20.

## Layout

```text
worker/
├── config.py               # Config_Loader (carregamento/validação de env)
├── worker.py               # main(): configuração, montagem e sinais
├── processor.py            # MessageProcessor: regras de uma mensagem (sem fila)
├── consumer.py             # RabbitConsumer: ack, fila de espera, DLQ, reconexão, parada
├── ocr/                    # Camada pura (sem I/O)
│   ├── locate.py           # Localização da placa Mercosul (OpenCV)
│   ├── processor.py        # OCR_Processor (Tesseract)
│   └── clean.py            # Plate_Normalizer (Mercosul/Antiga)
├── storage/                # Conectores de I/O
│   ├── clients.py          # Fábrica de clientes boto3
│   ├── s3_store.py         # S3_Connector
│   ├── session_repo.py     # Session_Repository (RDS)
│   └── audit.py            # Audit_Logger (DynamoDB)
├── pyproject.toml
└── tests/                  # Espelha o layout dos módulos
```

## Comandos

- `uv sync` — instala dependências (runtime + dev)
- `uv run pytest` — roda os testes
- `uv run ruff check .` / `uv run ruff format .` — lint e formatação
- `task dev:worker` — executa o worker com as variáveis locais

## Variáveis de ambiente

| Variável | Obrigatória | Observação |
|---|---|---|
| `RABBITMQ_URL` | sim | `amqp://` ou `amqps://` |
| `RABBITMQ_QUEUE_NAME` | não | Padrão `ocr-processamento-fila`; `-retry` e `-dlq` derivam dele |
| `DATABASE_URL` | sim | `postgresql://...` |
| `S3_BUCKET_NAME` | sim | |
| `S3_ENDPOINT_URL` | não | Storage local (RustFS); vazio = AWS |
| `DYNAMODB_TABLE_NAME` | sim | |
| `DYNAMODB_ENDPOINT_URL` | não | DynamoDB Local; vazio = AWS |
| `AWS_REGION` | sim | |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | não (par) | Sem elas vale a cadeia padrão do boto3 (instance profile) |

## Fila

As filas vêm de `infra/rabbitmq/definitions.json`, carregado pelo broker na subida:

- `ocr-processamento-fila`: principal (a API publica aqui, mensagens persistentes).
- `ocr-processamento-fila-retry`: espera de 30 s (TTL) e volta sozinha para a principal.
- `ocr-processamento-fila-dlq`: mensagens que o worker desistiu após 3 tentativas (veja no painel, `http://localhost:15672`, `guest`/`guest`).

O worker não cria filas: só confere que existem e sai com código 2 se faltar alguma.

## Docker

A imagem inclui o binário do Tesseract. O serviço `worker` do `infra/docker-compose.yaml`
sobe junto com Postgres, RabbitMQ, RustFS e DynamoDB Local, e só inicia depois que todos
estão saudáveis e o bucket e a tabela foram criados:

```bash
cd infra
docker compose up -d --build worker
docker compose logs -f worker
docker compose stop worker
```

O `stop` termina a mensagem em processamento, confirma (`ack`) e sai; o que o broker
ainda não entregou continua na fila. O compose já define `stop_grace_period: 30s`
(com `docker stop` direto, use `-t 30`).

O compose não tem a API ainda, e as tabelas do Postgres vêm das migrations dela
(`api/internal/repository/migrations`). Para testar só o worker: aplique as
migrations com `psql`, envie a foto ao bucket (`mc cp`), crie a sessão
(`status='PARKED'`, `plate_status='PENDING'`) e publique
`{"session_id": "<id>", "s3_key": "photos/..."}` pelo painel do RabbitMQ.

Na AWS, passe as variáveis com os valores das saídas do OpenTofu, sem
`*_ENDPOINT_URL` nem chaves estáticas (o instance profile fornece as credenciais).
