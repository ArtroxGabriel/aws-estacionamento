# AGENTS.md - Worker OCR (Python)

## Project Overview
- Worker assíncrono em Python que consome mensagens da fila RabbitMQ (`ocr-processamento-fila`), descarrega a foto do storage S3 (RustFS/MinIO), aplica redimensionamento e OCR (Tesseract) para extrair a placa, grava a placa no RDS (`plate_status` `PENDING` → `READ`) e registra auditoria `OCR_PROCESSING` no DynamoDB. O worker só enriquece a sessão: ela já nasce `PARKED`, com a vaga reservada pela API, e o ticket já vale (`docs/DECISOES.md`, D20).

## Tech Stack
- **Linguagem / Runtime**: Python 3.14 (`uv`)
- **Bibliotecas**: `boto3`, `pytesseract`, `Pillow`, `opencv-python-headless`, `psycopg`

## File Structure
```text
worker/
├── worker.py               # main(): configuração, montagem e sinais
├── processor.py            # MessageProcessor: regras de negócio de uma mensagem (sem fila)
├── consumer.py             # RabbitConsumer: conexão, ack, fila de espera, DLQ, reconexão e parada
├── ocr/
│   ├── locate.py           # Localização da placa Mercosul pela faixa azul (OpenCV)
│   ├── processor.py        # Redimensionamento e extração de placa com Tesseract
│   └── clean.py            # Normalização de caracteres da placa (Mercosul/Antiga)
├── storage/                # Conectores com S3, RDS e DynamoDB
├── pyproject.toml          # Definição de dependências via uv
└── tests/
```

## Common Commands
- `task dev:worker`: Executa o worker com as variáveis locais
- `uv run pytest`: Roda testes do processador OCR
- `uv run ruff check .` e `uv run ruff format .`: Verificação e formatação de código
- `cd infra && docker compose up -d --build worker`: Worker em container com Tesseract, com RabbitMQ, RustFS, Postgres e DynamoDB Local (ver README, seção Docker)

## Architecture Conventions
- Consumo por push no RabbitMQ (`basic_consume`, `prefetch_count=1`, `ack` manual). O transporte vive em `consumer.py`; as regras de negócio em `processor.py`, que recebe `(body, attempt)` e devolve `Result(outcome, reason)` sem conhecer o broker.
- Imagem temporária tratada em memória (`BytesIO`) ou com remoção garantida após leitura.
- **O worker nunca altera `status` nem o contador de vagas, e não usa Redis.** A única coisa que ele grava na sessão é `license_plate` e `plate_status`.
- **`plate_status = 'PENDING'` é a única condição de idempotência.** Mensagem cuja sessão não existe ou cuja placa já não está `PENDING` (`READ`, `UNREADABLE` ou `MANUAL`) é apagada sem efeitos. O `status` não é olhado: carro que já pagou ainda tem a placa gravada, como evidência.
- Após OCR com placa válida, tudo dentro de uma única transação no RDS:
  1. `UPDATE sessions SET license_plate = :license_plate, plate_status = 'READ' WHERE id = :id AND plate_status = 'PENDING'` (0 linhas = já processada ou placa digitada pelo caixa → apaga a mensagem sem efeitos; a placa digitada nunca é sobrescrita).
  2. `PutItem` com ação `OCR_PROCESSING` no DynamoDB.
  3. `COMMIT`. Se qualquer passo falhar: rollback (a placa volta a `PENDING`) e a mensagem vai para a fila de espera (nova tentativa).
  4. `basic_ack` da mensagem.
- **Placa ilegível é definitiva na 1ª entrega**: o OCR é determinístico, então reprocessar a mesma foto não muda nada. Grava `plate_status = 'UNREADABLE'` (`UPDATE ... WHERE plate_status = 'PENDING'`), com auditoria `OCR_FAILED` (`plate_status`, `reason: "unreadable plate: <motivo>"`), e a mensagem é apagada. A sessão continua `PARKED`: o motorista paga com o ticket e ninguém precisa agir (digitar a placa no caixa é opcional). Só se o `UPDATE` falhar (RDS fora) a mensagem fica para nova tentativa. Erros de OCR (timeout, decodificação) continuam com novas tentativas e poison.
- **Desistir da mensagem encerra a leitura**: na última entrega (poison), além da auditoria `POISON_MESSAGE`, a placa vai para `UNREADABLE` qualquer que tenha sido o motivo, inclusive indisponibilidade de S3/RDS, para a sessão não ficar "lendo placa" para sempre. Reprocessar pela DLQ depois disso não grava a placa; para tentar de novo, volte `plate_status` para `PENDING` no banco antes.
- **Só OCR local**: o pipeline é OpenCV + Tesseract (`ocr/`). O Amazon Rekognition, o `HybridOcr` e o `OCR_ENGINE` foram removidos no TP2 (`docs/DECISOES.md`, D1 e D10, ficam como histórico; o módulo antigo está no commit `1a63463`). O normalizador é o completo (correção por posição). Nas 114 fotos reais o Tesseract lê ~52%: o resto fica `UNREADABLE`, e o ticket vale do mesmo jeito (D20). Meça com `task ocr:dataset`.
- **Formatos aceitos (4 países do Mercosul, `docs/DECISOES.md` D8)**: Brasil `ABC1D23`/`ABC1234` (também Uruguai), Argentina `AB123CD`, e — só com o nome do país lido — Argentina antiga `ABC123` (linha inteira) e Paraguai `ABCD123`. Correções de caracteres só para o Brasil; estrangeiras só por leitura exata. O `HybridOcr` passa as linhas com o nome do país junto de cada candidata.
- **Tentativas e DLQ**: o próprio worker conta as tentativas no cabeçalho `x-attempt` (ausente = 1; `MAX_ATTEMPTS = 3` em `processor.py`). Falha transitória (S3, Postgres, DynamoDB, erro do OCR, corpo inválido) republica a mensagem em `ocr-processamento-fila-retry` (TTL 30 s, volta sozinha para a fila principal) com `x-attempt + 1`; a falha na 3ª tentativa é auditada como `POISON_MESSAGE` e a mensagem vai para `ocr-processamento-fila-dlq` com `x-failure-reason`. A publicação espera a confirmação do broker (*publisher confirms*) **antes** do `ack` da original; se ela falhar, a original não é confirmada e o broker a reentrega. As três filas vêm de `infra/rabbitmq/definitions.json`: o worker só confere que existem (declaração passiva) e sai com código 2 se faltar alguma.
- **Um endpoint por serviço**: `S3_ENDPOINT_URL` (cliente S3 em *path-style*) e `DYNAMODB_ENDPOINT_URL`; vazio = AWS real. `RABBITMQ_URL` (`amqp://`/`amqps://`, credenciais nunca vão para o log), `RABBITMQ_QUEUE_NAME` (padrão `ocr-processamento-fila`; `-retry` e `-dlq` derivam dele). `SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE` e `REDIS_URL` são ignorados.
- Credenciais AWS estáticas são opcionais: sem `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` o boto3 usa a cadeia padrão (instance profile `LabRole` no EC2). Com credenciais temporárias da AWS Academy, defina também `AWS_SESSION_TOKEN`.
- Parada (SIGTERM/SIGINT): `request_stop` marca a parada e agenda `stop_consuming` na thread da conexão (`add_callback_threadsafe`). A mensagem em processamento nunca é interrompida: termina, é confirmada e só então o consumo acaba; uma entrega que chega depois do sinal volta ao broker (`nack` com requeue). Com `prefetch=1` não há lote a devolver. Pare o container com `docker stop -t 30`; o tempo é o de uma mensagem. `heartbeat=120` s (o processamento bloqueia a thread da conexão).
- O Tesseract é chamado com `timeout` do próprio `pytesseract` (mata o subprocesso); não envolva a chamada em threads.
- OCR de foto real: a placa é localizada pela faixa azul Mercosul (azul saturado e claro, >= 10% da largura) e pelo formato (retângulo com proporção de placa, endireitado se inclinado >= 5°, com >= 5 caracteres alinhados, o que cobre placas antigas e Mercosul em carro azul); o Tesseract roda primeiro nos recortes da linha de caracteres e só lê a foto inteira se nenhum recorte der uma placa exata. A fonte da placa confunde o Tesseract (`5`→`S`, `I`→`1`/`L`, `0`→`O`); entre leituras exatas vence a mais frequente; o normalizador corrige por posição, linha a linha, sem `BRASIL`/`MERCOSUL` (nunca juntar linhas: gera placas falsas). Calibre com fotos reais no container, não só com placas sintéticas: `task ocr:local` roda o OCR em `examples/fotos` (com gabarito) e `examples/videos`.
- Com evidência Mercosul (faixa azul localizada ou `BRASIL`/`MERCOSUL` lido), só o formato Mercosul vale, inclusive nas leituras exatas: `LSN4149` é o Mercosul `LSN4I49` com o `I` lido como `1`, não uma placa antiga.
- `_binarize` aplica mediana 5×5 antes do Otsu: remove o ruído do JPEG que quebrava caracteres em foto real (`KLV-8465`). Num benchmark de 40 placas sintéticas o acerto ficou igual (38/40).

## Changelog
- 2026-11-16: Migração para o TP2: RabbitMQ no lugar do SQS (`consumer.py`, fila de espera com TTL, DLQ, `x-attempt`), regras de negócio isoladas em `processor.py`, endpoints de S3 e DynamoDB separados, Rekognition e `floci.env` removidos (`docs/PLANO-TP2-WORKER.md`).
- 2026-11-16: O worker passa a só gravar a placa (`plate_status` `PENDING` → `READ` | `UNREADABLE`): não altera `status`, não usa Redis (`storage/spots.py` e `REDIS_URL` removidos) e fecha a leitura como `UNREADABLE` ao desistir da mensagem.
- 2026-10-03: Placas dos 4 países do Mercosul (Argentina, Paraguai e Uruguai além do Brasil), com o nome do país como evidência para os formatos ambíguos.
- 2026-10-03: Amazon Rekognition como motor principal na AWS (`OCR_ENGINE=rekognition`, Tesseract de reserva); recorte largo da linha da placa (caractere encostado na moldura). Dataset de 114 fotos reais + 300 sintéticas: Tesseract 52% → Rekognition 96% nas reais.
- 2026-10-03: Placa ilegível vira `FAILED` na 1ª entrega (sem 3 tentativas de 5 min); leitura exata respeita a evidência Mercosul; mediana antes da binarização. Fotos reais em `examples/`: 1/5 → 5/5.
- 2026-09-15: Criação do AGENTS.md do Worker com fluxo de OCR e auditoria.
- 2026-09-29: Efeitos colaterais transacionais com compensação, poison na última entrega, busca da placa no texto do OCR, timeout real do Tesseract e credenciais AWS opcionais + `AWS_SESSION_TOKEN`.
- 2026-09-30: Contador só é alterado se a chave existir (Lua); parada imediata durante o long poll e devolução do resto do lote.
- 2026-09-30: Dockerfile (Tesseract + tini, não-root), `floci.env`, localização da placa Mercosul (OpenCV), escala adaptativa, limiar de Otsu e correção de caracteres por posição.
