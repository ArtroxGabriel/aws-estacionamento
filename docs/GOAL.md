# GOAL.md — Visão Geral do Sistema & Farol dos Módulos (Trabalho Prático 2)

> **Projeto:** Sistema Inteligente de Gestão de Estacionamento — Implantação com Docker, Docker Compose e Alternativas Open Source  
>
> **Disciplina:** Desenvolvimento de Software para Nuvem (UFC) — Profs. Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta  
>
> **Objetivo Deste Arquivo:** Servir como **fonte da verdade arquitetural** e **farol de revisão** para o Trabalho Prático 2, consolidando a migração dos serviços da AWS para soluções open-source (RustFS, RabbitMQ, PostgreSQL gerenciado), o fluxo físico com duas cancelas (Entrada e Saída), o terminal de pagamento independente e a orquestração via Docker Compose e Kubernetes.

---

## 1. Visão Geral do Sistema (A Jornada do Estacionamento com Duas Cancelas)

O sistema automatiza o ciclo completo de um estacionamento inteligente do mundo real. O fluxo físico divide-se claramente em:
1. **Cancela de Entrada**: Foto do veículo, reserva da vaga, emissão do ticket e abertura da cancela de entrada. **O ticket (ID da sessão) é o que identifica o carro e já vale na emissão.**
2. **Processamento em Background (Worker OCR)**: Leitura assíncrona da placa na foto. É um enriquecimento: serve de evidência para a auditoria e de atalho na saída. Nunca bloqueia o carro (ver [DECISOES.md](DECISOES.md), D20).
3. **Terminal / Totem de Pagamento (Caixa)**: Pagamento do ticket antes de ir ao carro. O pagamento **habilita** o veículo a sair, mas **não libera a vaga de imediato** nem abre cancela alguma.
4. **Cancela de Saída**: Leitura automática da placa na cancela de saída. Se a placa tiver pagamento confirmado, a cancela abre imediatamente. Se a câmera falhar ou a placa estiver ilegível/suja, o motorista insere o ID do ticket em papel para validação. Somente na abertura da cancela de saída a vaga é restituída e a sessão é encerrada (`EXITED`).

```text
========================================================================================================
1. ENTRADA (Cancela de Entrada)
========================================================================================================
[Cancela / Totem Entrada] ──(1. Foto do Carro)──> [API Go]
                                                    ├── Salva foto no RustFS (S3-compatible)
                                                    ├── Reserva a vaga no Redis (lotado -> 409, cancela fechada)
                                                    ├── Cria sessão 'PARKED' (plate_status 'PENDING') no PostgreSQL
                                                    ├── Publica evento na fila RabbitMQ (fila fora do ar não derruba a entrada)
                                                    ├── Grava auditoria no DynamoDB (ação: 'ENTRY')
                                                    └── Retorna ticket impresso (ID) e ABRE A CANCELA

[Worker OCR Python] <──(2. Consome RabbitMQ)────────┘
        ├── Baixa foto do RustFS e redimensiona
        ├── Extrai placa via Tesseract OCR (Mercosul 4 países)
        ├── Grava a placa no PostgreSQL (plate_status 'READ'), ou 'UNREADABLE' se não houver placa
        ├── Não altera o status da sessão nem o contador de vagas
        └── Grava auditoria no DynamoDB (ação: 'OCR_PROCESSING' ou 'OCR_FAILED')

========================================================================================================
2. CONSULTA EM TEMPO REAL & CAIXA DE PAGAMENTO
========================================================================================================
[Painel / Totens] ────────(Consulta Vagas)──────> [Redis] (Leitura instantânea em memória < 2ms)

[Caixa / Totem Pagamento] ──(Paga Ticket/Placa)──> [API Go]
                                                    ├── Valida valor no PostgreSQL
                                                    ├── Registra pagamento: status 'PAID' (vaga segue retida!)
                                                    └── Grava auditoria no DynamoDB (ação: 'EXIT_PAYMENT')

========================================================================================================
3. SAÍDA (Cancela de Saída com Dupla Validação)
========================================================================================================
[Cancela / Totem Saída] ──(Tentativa 1: Foto/Placa)──> [API Go]
      │                                                  ├── Busca sessão ativa pela placa
      │                                                  ├── Se status == 'PAID':
      │                                                  │     ├── Marca status = 'EXITED' (exited_at = NOW)
      │                                                  │     ├── Incrementa vagas no Redis (+1)
      │                                                  │     ├── Grava auditoria ('EXIT_BARRIER_OPENED')
      │                                                  │     └── ABRE A CANCELA DE SAÍDA!
      │
      └──(Tentativa 2: Falha na placa? Digita ID)────> [API Go]
                                                         ├── Valida se ID do ticket consta como 'PAID'
                                                         ├── Se sim: 'EXITED', incrementa Redis e ABRE CANCELA!
                                                         └── Se não pago: cancela bloqueada + alerta ao motorista
```

---

## 2. Metas e Papéis de Cada Módulo

### 2.1. Frontend Web & Simulação dos Totens (`web/` — React + Vite + TypeScript)
* **Totem da Cancela de Entrada (`/cancela-entrada`):** Simula a câmera de entrada. Envia foto via `POST /entries`, exibe o ticket (com ID único e horário), já válido, e sinaliza a abertura da cancela de entrada. A placa aparece quando for lida; se não for, nada é exigido do motorista. Pátio lotado: mensagem clara e cancela fechada.
* **Painel de Vagas em Tempo Real (`/`):** Exibe contagem de vagas em alta frequência via `GET /spots/available` sem onerar o banco relacional.
* **Caixa / Terminal de Autoatendimento (`/pagamento`):** Permite buscar sessões por ID do ticket ou placa, mostra a foto da entrada para conferência e cobra com ou sem placa lida ("Informar placa" é opcional). Processa o pagamento via `POST /exits/{id}/pay`, alterando o status para `PAID`. Informa claramente que o cliente agora possui permissão para se dirigir à cancela de saída.
* **Totem da Cancela de Saída (`/cancela-saida`):** Simula o dispositivo na barreira de saída:
  * **Modo Câmera (Placa):** Envia leitura/foto para `POST /barrier/exit/plate`. Se reconhecido e pago, aciona a animação de abertura da cancela.
  * **Modo Contingência (Ticket em Papel):** Se a placa não for identificada ou estiver ilegível, motorista insere o ID do ticket em `POST /barrier/exit/ticket` para destravar a cancela.
* **Painel de Auditoria (`/auditoria`):** Exibe o feed de logs imutáveis do DynamoDB.

### 2.2. API Backend (`api/` — Go + Uber Fx)
* **Armazenamento de Objetos (RustFS):** Substitui o S3. Usa o cliente S3 oficial da AWS com `UsePathStyle: true` apontado para o endpoint do RustFS (`http://rustfs:9000`), armazenando fotos em `photos/{session_id}_{filename}`.
* **Persistência Transacional (PostgreSQL):** Gerenciado pela equipe em container Docker. Mantém a tabela `sessions` e gerencia as transições de estado:
  $$\text{PARKED} \longrightarrow \text{PAID} \longrightarrow \text{EXITED}$$
  A leitura da placa corre em paralelo, na coluna `plate_status`: $\text{PENDING} \rightarrow \text{READ}$ ou $\text{UNREADABLE}$ (worker), ou $\text{MANUAL}$ (placa digitada no caixa). Ela nunca muda o `status`.
* **Mensageria Assíncrona (RabbitMQ):** Substitui o SNS/SQS. Publica mensagens persistentes em JSON na fila durável `ocr-processamento-fila`.
* **Auditoria NoSQL (Amazon DynamoDB):** Conforme exigência 1.a da especificação, continua utilizando DynamoDB para log imutável de todas as ações. Suporta tanto a AWS real quanto o `dynamodb-local` para desenvolvimento e correção offline.
* **Cache de Vagas Anti-Overbooking (Redis):** Mantém `spots:available`. A vaga é reservada pela API **na entrada** (script Lua atômico; lotado devolve `409`), mantida durante o pagamento (`PAID`), e **apenas incrementada na cancela de saída** (`EXITED`). Em caso de reinicialização do Redis, recalcula a partir do PostgreSQL:
  $$\text{vagas\_disponiveis} = \max(0, \text{capacidade\_total} - \text{COUNT}(\text{sessions ativas}))$$
  *(onde ativas = `status IN ('PARKED', 'PAID')`)*.
* **Foto como evidência:** `GET /sessions/{id}/photo` devolve a foto da entrada lida do armazenamento de objetos (download pela API, sem expor o storage ao navegador).

### 2.3. Worker de Visão Computacional (`worker/` — Python + OCR)
* **Consumo via RabbitMQ:** Consome mensagens da fila `ocr-processamento-fila` via protocolo AMQP (`pika`), utilizando confirmação manual (`basic_ack`).
* **Download de Imagens no RustFS:** Baixa a imagem do RustFS usando o cliente S3 (boto3) apontado para o RustFS.
* **Extração de Placa (Tesseract OCR):** Motor open-source de OCR rodando com pré-processamento Pillow/OpenCV, normalizando para placas Mercosul (Brasil, Argentina, Uruguai e Paraguai).
* **Tratamento de Falhas e Dead Letter:** Se a placa for ilegível na primeira tentativa, grava `plate_status = UNREADABLE` e confirma a mensagem. A sessão continua `PARKED` e o ticket continua valendo; o caixa pode digitar a placa se quiser. Erros de infraestrutura são reentregues; na última entrega a mensagem vai para a fila de mensagens mortas (DLQ `ocr-processamento-fila-dlq`) e a leitura também é encerrada como `UNREADABLE`.
* **Sem Redis:** o worker não toca no contador de vagas (a API reserva a vaga na entrada).

### 2.4. Orquestração & Contêineres (`docker-compose.yml`)
* Inicialização completa com um único comando: `docker compose up -d`.
* Serviços orquestrados:
  1. `rustfs`: Servidor de objetos S3-compatible em Rust (portas 9000 API e 9001 console).
  2. `postgres`: Banco de dados relacional oficial `postgres:16-alpine` (porta 5432).
  3. `rabbitmq`: Broker de mensageria `rabbitmq:3-management-alpine` (portas 5672 AMQP e 15672 UI).
  4. `redis`: Cache em memória `redis:7-alpine` (porta 6379).
  5. `dynamodb-local`: Emulador local do DynamoDB (porta 8000), configurável para DynamoDB AWS.
  6. `api`: Backend compilado em multi-stage build Go.
  7. `worker`: Worker Python com dependências Tesseract/OpenCV instaladas.
  8. `web`: Frontend React compilado servido via Nginx com proxy reverso.
* `healthcheck` em todos os serviços de infraestrutura para subida ordenada (`depends_on: condition: service_healthy`).

### 2.5. Bônus Kubernetes (`k8s/`)
* Manifestos declarativos para execução em cluster Kubernetes (Minikube / Kind / K3s):
  * `Deployments` para API, Worker, Web, Postgres, RustFS, RabbitMQ e Redis.
  * `Services` com `ClusterIP` para comunicação interna e `NodePort` para acesso externo (Web e API).
  * `ConfigMap` e `Secret` para injeção segura de credenciais e parâmetros.

---

## 3. Contratos de Dados & Estados

### 3.1. Máquina de Estados da Sessão (`sessions` no PostgreSQL)

| Estado | Significado Físico | Vaga Ocupada? | Ações Permitidas |
|---|---|---|---|
| `PARKED` | Carro passou na Cancela de Entrada e está no pátio, com ou sem placa lida. | Sim (-1 no Redis, na entrada) | Pagamento no Caixa pelo ticket, Placa digitada/corrigida (`PATCH`, opcional), Cancelamento (`DELETE`). |
| `PAID` | Cliente efetuou o pagamento no Caixa. Habilitado a sair! | Sim (ainda ocupa vaga) | Aproximação na Cancela de Saída. |
| `EXITED` | Carro cruzou a Cancela de Saída. Sessão concluída. | Liberada (+1 no Redis) | Consulta histórica/auditoria. |

**Leitura da placa (`plate_status`), independente do estado acima:**

| Valor | Significado | Quem grava |
|---|---|---|
| `PENDING` | Foto na fila, aguardando o worker. | API, na entrada |
| `READ` | Placa lida pelo OCR. | Worker |
| `UNREADABLE` | A foto não deu placa, a fila estava fora do ar ou o worker desistiu após as reentregas. | Worker ou API |
| `MANUAL` | Placa digitada ou corrigida no caixa. | API (`PATCH /sessions/{id}`) |

O worker só grava enquanto o valor é `PENDING`: uma placa digitada pelo operador nunca é sobrescrita pelo OCR.

> **Estado da implementação:** o ciclo completo `PARKED` $\rightarrow$ `PAID` $\rightarrow$ `EXITED` existe. O pagamento (`POST /exits/{id}/pay`) não devolve a vaga; ela volta na cancela de saída (`POST /barrier/exit/plate` ou `/ticket`), e `PARKED` e `PAID` contam como sessão ativa.

### 3.2. Chaves de Cache no Redis
* **Chave:** `spots:available`
* **Entrada (API, em `POST /entries`):** reserva atômica (`DECR` só se houver vaga; sem vaga, `409`). Se o upload ou o `INSERT` falharem, a vaga é devolvida.
* **Pagamento no Caixa:** Contador inalterado (carro continua fisicamente na vaga).
* **Cancela de Saída (Passagem do carro):** `INCR spots:available`
* **Auto-Recuperação:** $\text{vagas} = \max(0, \text{capacidade} - \text{COUNT}(\text{status} \in \{\text{'PARKED'}, \text{'PAID'}\}))$. A mesma conta decide a entrada quando a chave não existe.

### 3.3. Tabela DynamoDB (`AuditoriaEstacionamento`)
Ações registradas:
* `ENTRY`: Cancela de entrada aberta, ticket impresso e foto salva no RustFS.
* `OCR_PROCESSING`: Worker reconheceu a placa (`plate_status = READ`).
* `OCR_FAILED`: Worker não identificou placa válida (`plate_status = UNREADABLE`). Não exige ação: o ticket segue válido.
* `PLATE_CORRECTION`: Operador digitou ou corrigiu a placa no caixa.
* `EXIT_PAYMENT`: Pagamento efetuado com sucesso no terminal/caixa.
* `EXIT_BARRIER_OPENED`: Cancela de saída destravada (por placa ou por ticket) e vaga restituída.
* `SESSION_DELETE`: Sessão cancelada/excluída administrativamente.

### 3.4. Fila RabbitMQ (`ocr-processamento-fila`)
* **Exchange:** `estacionamento.direct` (ou default exchange)
* **Routing Key:** `ocr.processamento`
* **Queue:** `ocr-processamento-fila` (durable: true)
* **DLQ:** `ocr-processamento-fila-dlq` (associada via `x-dead-letter-exchange`)
* **Payload da Mensagem (JSON):**
  ```json
  {
    "session_id": "b1a7c4e5...",
    "s3_key": "photos/b1a7c4e5_carro.jpg"
  }
  ```

---

## 4. Tabela de Pontuação da Disciplina (TP2)

| Item da Especificação do Trabalho 2 | Peso | Implementação no Projeto |
|---|---|---|
| **Armazenamento de Arquivos com RustFS** (Substituindo MinIO/S3) | **1,5 ponto** | Container RustFS executando (0,5) + upload na entrada e download no worker funcionando (1,0). |
| **Banco NoSQL para Auditoria** (DynamoDB / DocumentDB) | **1,0 ponto** | Tabela de auditoria gravando e consultando eventos (ENTRY, OCR, PAYMENT, EXIT). |
| **Banco Relacional Gerenciado pela Equipe** (PostgreSQL) | **1,5 ponto** | Container Postgres executando (0,5) + persistência transacional de sessões pela aplicação (1,0). |
| **Fila de Mensagens com RabbitMQ** (Substituindo SNS/SQS) | **1,5 ponto** | Container RabbitMQ executando (0,5) + produção pela API e consumo pelo Worker (1,0). |
| **Dockerfiles para Backend, Worker e Frontend** | **1,5 ponto** | Imagens otimizadas multi-stage para Go API, Python Worker e React Frontend. |
| **Docker Compose Integrando Todos os Serviços** | **1,5 ponto** | Todos os serviços sobem via único `docker compose up -d` (0,75) + redes e dependências com healthchecks (0,75). |
| **Documentação no README e Evidências** | **1,5 ponto** | Passo a passo claro para execução (1,0) + evidências e prints de funcionamento (0,5). |
| **Bônus Kubernetes (Extra)** | **+1,0 ponto** | Deployments, Services (NodePort e ClusterIP), ConfigMaps e Secrets no K8s. |
| **Total Máximo Possível** | **11,0 pontos** | |

---

## 5. Checklist de Verificação Rápida

- [x] **Comando Único:** `docker compose up -d` inicia PostgreSQL, RustFS, RabbitMQ, Redis, DynamoDB Local, API, Worker e Web sem falhas.
- [x] **Fluxo da Cancela de Entrada:** Upload da foto no RustFS $\rightarrow$ vaga reservada no Redis $\rightarrow$ sessão `PARKED` no Postgres $\rightarrow$ mensagem no RabbitMQ $\rightarrow$ ticket emitido (já válido) $\rightarrow$ cancela de entrada abre.
- [x] **Worker Assíncrono:** Consome do RabbitMQ $\rightarrow$ baixa do RustFS $\rightarrow$ OCR Tesseract $\rightarrow$ grava a placa (`plate_status = READ`) ou `UNREADABLE`, sem mexer no status nem nas vagas. Com o worker parado, entrada e pagamento continuam funcionando.
- [x] **Fluxo do Caixa de Pagamento:** Consulta por ID ou placa $\rightarrow$ pagamento registrado (`PAID`) $\rightarrow$ vaga **continua** retida $\rightarrow$ ticket habilitado para saída.
- [x] **Fluxo da Cancela de Saída:**
  - Tentativa 1 (Câmera/Placa): Detecta placa paga $\rightarrow$ atualiza para `EXITED` $\rightarrow$ incrementa vaga no Redis $\rightarrow$ cancela de saída abre.
  - Tentativa 2 (Contingência Ticket Papel): Digita ID do ticket pago $\rightarrow$ cancela de saída abre e vaga é liberada.
  - Tentativa 3 (Ticket Não Pago): Cancela permanece fechada e alerta o condutor.
- [x] **Auditoria Completa:** Todas as ações registradas no DynamoDB e visualizáveis na aba `/auditoria`.
- [x] **Manifestos Kubernetes:** Pasta `k8s/` testada num k3s local (ver `k8s/README.md`).