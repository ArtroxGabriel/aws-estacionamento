# AGENTS.md - Worker OCR (Python)

## Project Overview
- Worker assíncrono em Python que consome mensagens da fila SQS (`ocr-processamento-fila`), descarrega a foto do S3, aplica redimensionamento e OCR (Tesseract) para extrair a placa, atualiza o status no RDS para `ESTACIONADO`, decrementa a vaga no Redis e registra auditoria `PROCESSAMENTO_OCR` no DynamoDB.

## Tech Stack
- **Linguagem / Runtime**: Python 3.14 (`uv`)
- **Bibliotecas**: `boto3`, `pytesseract`, `Pillow`, `psycopg` / `sqlalchemy`, `redis`

## File Structure
```text
worker/
├── worker.py               # Loop principal de consumo SQS
├── ocr/
│   ├── processor.py        # Redimensionamento e extração de placa com Tesseract
│   └── clean.py            # Normalização de caracteres da placa (Mercosul/Antiga)
├── storage/                # Conectores com S3, RDS, Redis e DynamoDB
├── pyproject.toml          # Definição de dependências via uv
└── tests/
```

## Common Commands
- `task dev:worker`: Executa o worker com as variáveis locais
- `uv run pytest`: Roda testes do processador OCR
- `uv run ruff check .` e `uv run ruff format .`: Verificação e formatação de código

## Architecture Conventions
- Polling contínuo via SQS com `WaitTimeSeconds=20`.
- Imagem temporária tratada em memória (`BytesIO`) ou com remoção garantida após leitura.
- Após sucesso:
  1. `UPDATE sessoes SET placa = :placa, status = 'ESTACIONADO' WHERE id = :id` no RDS.
  2. `DECR vagas:disponiveis` no Redis.
  3. `PutItem` com ação `PROCESSAMENTO_OCR` no DynamoDB.
  4. `DeleteMessage` na fila SQS.

## Changelog
- 2026-09-15: Criação do AGENTS.md do Worker com fluxo de OCR e auditoria.
