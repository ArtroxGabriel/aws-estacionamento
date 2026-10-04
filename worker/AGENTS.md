# AGENTS.md - Worker OCR (Python)

## Project Overview
- Worker assíncrono em Python que consome mensagens da fila SQS (`ocr-processamento-fila`), descarrega a foto do S3, aplica redimensionamento e OCR (Tesseract) para extrair a placa, atualiza o status no RDS para `PARKED`, decrementa a vaga no Redis e registra auditoria `OCR_PROCESSING` no DynamoDB.

## Tech Stack
- **Linguagem / Runtime**: Python 3.14 (`uv`)
- **Bibliotecas**: `boto3`, `pytesseract`, `Pillow`, `opencv-python-headless`, `psycopg`, `redis`

## File Structure
```text
worker/
├── worker.py               # Loop principal de consumo SQS
├── ocr/
│   ├── locate.py           # Localização da placa Mercosul pela faixa azul (OpenCV)
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
- `docker build -t estacionamento-worker ./worker`: Imagem com Tesseract (ver README, seção Docker, para rodar contra o Floci com `floci.env`)

## Architecture Conventions
- Polling contínuo via SQS com `WaitTimeSeconds=20`.
- Imagem temporária tratada em memória (`BytesIO`) ou com remoção garantida após leitura.
- Após OCR com placa válida, tudo dentro de uma única transação no RDS:
  1. `UPDATE sessions SET license_plate = :license_plate, status = 'PARKED' WHERE id = :id AND status = 'PROCESSING'` (0 linhas = já processada → apaga a mensagem sem efeitos).
  2. `DECR spots:available` no Redis.
  3. `PutItem` com ação `OCR_PROCESSING` no DynamoDB.
  4. `COMMIT`. Se qualquer passo falhar: rollback, `INCR` compensatório se o `DECR` já ocorreu, e a mensagem fica na fila.
  5. `DeleteMessage` na fila SQS.
- **Placa ilegível é definitiva na 1ª entrega**: o OCR é determinístico, então reprocessar a mesma foto só atrasa o operador. A sessão vai para `FAILED` (`UPDATE ... WHERE status = 'PROCESSING'`), com auditoria `OCR_FAILED` (`reason: "unreadable plate: <motivo>"`), e a mensagem é apagada sem mexer no contador. O caixa digita a placa (`PATCH /sessions/{id}` na API). Só se o `UPDATE` falhar (RDS fora) a mensagem fica para nova tentativa. Erros de OCR (timeout, decodificação) continuam com novas tentativas e poison.
- **Motor de OCR (`OCR_ENGINE`)**: `tesseract` (padrão, Floci) ou `rekognition` (AWS, definido no `user_data`). O `HybridOcr` (`ocr/rekognition.py`) redimensiona a foto (máx. 1920 px, JPEG), chama `DetectText` e testa as linhas da mais alta para a mais baixa: vence a primeira que normaliza para placa, que é o carro em primeiro plano, não os de fundo. Erro ou nenhuma placa → pipeline Tesseract. Nas 114 fotos reais: Tesseract 42%, Rekognition 96%. Meça com `task ocr:dataset ENGINE=rekognition`.
- Mensagem que falha na 3ª entrega (`MAX_RECEIVE_COUNT` = `maxReceiveCount` da redrive policy em `infra/main.tf`) é auditada como `POISON_MESSAGE`; o SQS a move para `ocr-processamento-fila-dlq`. Os dois valores devem mudar juntos.
- Credenciais AWS estáticas são opcionais: sem `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` o boto3 usa a cadeia padrão (instance profile `LabRole` no EC2). Com credenciais temporárias da AWS Academy, defina também `AWS_SESSION_TOKEN`.
- O Worker nunca cria a chave `spots:available`: `DECR`/`INCR` rodam em Lua só se a chave existir. Sem a chave (Redis reiniciado), a API a reconstrói do RDS; um `DECR` simples a criaria como -1 → 0 e o sistema mostraria lotação falsa.
- Parada (SIGTERM/SIGINT): nunca interrompa o long poll do SQS (a requisição abandonada continua aberta no SQS, pode receber uma mensagem e escondê-la por 300 s); o loop o deixa terminar e devolve o que veio com `VisibilityTimeout=0`. Só o back-off de receive é interrompido (`_Interrupted`). A mensagem em processamento nunca é interrompida. Pare o container com `docker stop -t 30`.
- O Tesseract é chamado com `timeout` do próprio `pytesseract` (mata o subprocesso); não envolva a chamada em threads.
- OCR de foto real: a placa é localizada pela faixa azul Mercosul (azul saturado e claro, >= 10% da largura) e pelo formato (retângulo com proporção de placa, endireitado se inclinado >= 5°, com >= 5 caracteres alinhados, o que cobre placas antigas e Mercosul em carro azul); o Tesseract roda primeiro nos recortes da linha de caracteres e só lê a foto inteira se nenhum recorte der uma placa exata. A fonte da placa confunde o Tesseract (`5`→`S`, `I`→`1`/`L`, `0`→`O`); entre leituras exatas vence a mais frequente; o normalizador corrige por posição, linha a linha, sem `BRASIL`/`MERCOSUL` (nunca juntar linhas: gera placas falsas). Calibre com fotos reais no container, não só com placas sintéticas: `task ocr:local` roda o OCR em `examples/fotos` (com gabarito) e `examples/videos`.
- Com evidência Mercosul (faixa azul localizada ou `BRASIL`/`MERCOSUL` lido), só o formato Mercosul vale, inclusive nas leituras exatas: `LSN4149` é o Mercosul `LSN4I49` com o `I` lido como `1`, não uma placa antiga.
- `_binarize` aplica mediana 5×5 antes do Otsu: remove o ruído do JPEG que quebrava caracteres em foto real (`KLV-8465`). Num benchmark de 40 placas sintéticas o acerto ficou igual (38/40).

## Changelog
- 2026-10-03: Amazon Rekognition como motor principal na AWS (`OCR_ENGINE=rekognition`, Tesseract de reserva); recorte largo da linha da placa (caractere encostado na moldura). Dataset de 114 fotos reais + 300 sintéticas: 42% → 96% nas reais.
- 2026-10-03: Placa ilegível vira `FAILED` na 1ª entrega (sem 3 tentativas de 5 min); leitura exata respeita a evidência Mercosul; mediana antes da binarização. Fotos reais em `examples/`: 1/5 → 5/5.
- 2026-09-15: Criação do AGENTS.md do Worker com fluxo de OCR e auditoria.
- 2026-09-29: Efeitos colaterais transacionais com compensação, poison na última entrega, busca da placa no texto do OCR, timeout real do Tesseract e credenciais AWS opcionais + `AWS_SESSION_TOKEN`.
- 2026-09-30: Contador só é alterado se a chave existir (Lua); parada imediata durante o long poll e devolução do resto do lote.
- 2026-09-30: Dockerfile (Tesseract + tini, não-root), `floci.env`, localização da placa Mercosul (OpenCV), escala adaptativa, limiar de Otsu e correção de caracteres por posição.
