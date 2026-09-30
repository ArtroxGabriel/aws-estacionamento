# OCR Worker

Worker assíncrono em Python 3.14 (gerenciado por `uv`) que consome a fila SQS
`ocr-processamento-fila`, baixa a foto do S3, extrai a placa via Tesseract OCR,
atualiza a sessão no RDS, decrementa o contador de vagas no Redis e grava
auditoria no DynamoDB.

## Layout

```text
worker/
├── config.py               # Config_Loader (carregamento/validação de env)
├── worker.py               # Poller: loop SQS, orquestração e shutdown
├── ocr/                    # Camada pura (sem I/O)
│   ├── locate.py           # Localização da placa Mercosul (OpenCV)
│   ├── processor.py        # OCR_Processor (Tesseract)
│   └── clean.py            # Plate_Normalizer (Mercosul/Antiga)
├── storage/                # Conectores de I/O
│   ├── clients.py          # Fábrica de clientes boto3
│   ├── s3_store.py         # S3_Connector
│   ├── session_repo.py     # Session_Repository (RDS)
│   ├── spots.py            # Spots_Counter (Redis)
│   └── audit.py            # Audit_Logger (DynamoDB)
├── pyproject.toml
└── tests/                  # Espelha o layout dos módulos
```

## Comandos

- `uv sync` — instala dependências (runtime + dev)
- `uv run pytest` — roda os testes
- `uv run ruff check .` / `uv run ruff format .` — lint e formatação
- `task dev:worker` — executa o worker com as variáveis locais

## Docker

A imagem inclui o binário do Tesseract. Contra o Floci local, o container
compartilha a rede do `floci_aws` (assim `localhost` é o emulador):

```bash
docker build -t estacionamento-worker ./worker
docker run -d --name ocr-worker --network container:floci_aws --env-file worker/floci.env estacionamento-worker
docker logs -f ocr-worker
docker stop ocr-worker
```

O `stop` encerra em menos de 1 s quando o worker está ocioso; uma mensagem em
processamento termina antes, e as demais do lote voltam à fila na hora.
Na AWS, passe as variáveis com os valores das saídas do OpenTofu e sem
`AWS_ENDPOINT_URL`/chaves estáticas (o instance profile fornece as credenciais).
