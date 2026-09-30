# AGENTS.md - Worker OCR (Python)

## Project Overview
- Worker assíncrono em Python que consome mensagens da fila SQS (`ocr-processamento-fila`), descarrega a foto do S3, aplica redimensionamento e OCR (Tesseract) para extrair a placa, atualiza o status no RDS para `PARKED`, decrementa a vaga no Redis e registra auditoria `OCR_PROCESSING` no DynamoDB.

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
- Após OCR com placa válida, tudo dentro de uma única transação no RDS:
  1. `UPDATE sessions SET license_plate = :license_plate, status = 'PARKED' WHERE id = :id AND status = 'PROCESSING'` (0 linhas = já processada → apaga a mensagem sem efeitos).
  2. `DECR spots:available` no Redis.
  3. `PutItem` com ação `OCR_PROCESSING` no DynamoDB.
  4. `COMMIT`. Se qualquer passo falhar: rollback, `INCR` compensatório se o `DECR` já ocorreu, e a mensagem fica na fila.
  5. `DeleteMessage` na fila SQS.
- Mensagem que falha na 3ª entrega (`MAX_RECEIVE_COUNT` = `maxReceiveCount` da redrive policy em `infra/main.tf`) é auditada como `POISON_MESSAGE`; o SQS a move para `ocr-processamento-fila-dlq`. Os dois valores devem mudar juntos.
- Credenciais AWS estáticas são opcionais: sem `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` o boto3 usa a cadeia padrão (instance profile `LabRole` no EC2). Com credenciais temporárias da AWS Academy, defina também `AWS_SESSION_TOKEN`.
- O Tesseract é chamado com `timeout` do próprio `pytesseract` (mata o subprocesso); não envolva a chamada em threads.

## Changelog
- 2026-09-15: Criação do AGENTS.md do Worker com fluxo de OCR e auditoria.
- 2026-09-29: Efeitos colaterais transacionais com compensação, poison na última entrega, busca da placa no texto do OCR, timeout real do Tesseract e credenciais AWS opcionais + `AWS_SESSION_TOKEN`.
