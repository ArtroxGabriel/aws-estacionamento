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
