# OCR Worker

Worker assíncrono em Python 3.14 (gerenciado por `uv`) que consome a fila SQS
`ocr-processamento-fila`, baixa a foto do S3, extrai a placa via Tesseract OCR,
grava a placa na sessão no RDS (`plate_status`) e registra auditoria no DynamoDB.

O worker só enriquece a sessão: o carro já está `PARKED`, com a vaga reservada
pela API, e o ticket já vale. Ele não altera `status` nem o contador de vagas
(não usa Redis). Ver `docs/DECISOES.md`, D20.

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
docker stop -t 30 ocr-worker
```

Use `-t 30`: um long poll do SQS em andamento termina antes de o worker sair
(até 20 s; interrompê-lo deixaria a requisição aberta no SQS, que pode entregar
uma mensagem a ela e escondê-la por 300 s). O que esse poll trouxer e o resto do
lote voltam à fila na hora; a mensagem em processamento termina antes.
Na AWS, passe as variáveis com os valores das saídas do OpenTofu e sem
`AWS_ENDPOINT_URL`/chaves estáticas (o instance profile fornece as credenciais).
