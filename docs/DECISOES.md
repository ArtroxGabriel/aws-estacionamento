# Decisões de Arquitetura (Trabalho Prático 2)

Registro histórico e técnico das decisões tomadas no projeto, motivações, alternativas descartadas e impactos no código. Cada decisão segue o padrão estruturado: **Contexto → Decisão → Por quê → Alternativas Descartadas → Consequências → Onde está no código**.

---

## Mapeamento de Serviços: Trabalho 1 (AWS) vs Trabalho 2 (Docker / Open Source)

| Papel no Sistema | Trabalho 1 (AWS Cloud) | Trabalho 2 (Open Source & Docker Compose) | Decisão |
|---|---|---|---|
| **Armazenamento de Fotos** | Amazon S3 | **RustFS** (S3-compatible em Rust) | [D11](#d11-rustfs-em-substituição-ao-s3-e-ao-minio) |
| **Banco Transacional** | Amazon RDS (PostgreSQL) | **PostgreSQL 16** (Container gerenciado pela equipe) | [D12](#d12-postgresql-em-container-gerenciado-com-migrações-automáticas) |
| **Fila de Mensagens** | Amazon SNS + SQS | **RabbitMQ** (AMQP 0-9-1 com DLX e UI de Gestão) | [D13](#d13-rabbitmq-para-mensageria-assíncrona-em-vez-de-kafka) |
| **Banco NoSQL de Auditoria** | Amazon DynamoDB | **Amazon DynamoDB** (com suporte local via DynamoDB Local) | [D14](#d14-continuidade-do-dynamodb-nosql-com-suporte-dual-cloudlocal) |
| **Controle Físico das Cancelas** | Pagamento liberava vaga | **Duas Cancelas (Entrada e Saída) + Pagamento Desacoplado** | [D15](#d15-duas-cancelas-físicas-e-desacoplamento-entre-pagamento-e-saída) |
| **Cache de Vagas** | Amazon ElastiCache (Redis) | **Redis 7 Alpine** (Container local em memória) | [D16](#d16-manutenção-do-redis-para-contagem-de-vagas-e-anti-overbooking) |
| **Orquestração e Empacotamento** | Terraform / OpenTofu + ECR | **Dockerfiles Multi-Stage + Docker Compose Único** | [D17](#d17-dockerfiles-multi-stage-e-orquestração-com-docker-compose) |
| **Orquestração em Nuvem (Bônus)** | AWS ASG + ALB | **Kubernetes** (Deployments, Services NodePort/ClusterIP, Secrets) | [D18](#d18-preparação-do-bônus-kubernetes-k8s) |
| **Motor de OCR** | Amazon Rekognition (AWS) | **Tesseract OCR + OpenCV (Modelo Python 100% Open Source)** | [D19](#d19-retorno-ao-modelo-open-source-em-python-opencv--tesseract-e-descarte-do-amazon-rekognition) |

---

## D11. RustFS em substituição ao S3 e ao MinIO

**Contexto:** O Trabalho 1 usava o Amazon S3 para armazenar as fotos dos veículos. A especificação do Trabalho 2 sugere o uso do MinIO. No entanto, o MinIO alterou sua licença de Apache 2.0 para GNU AGPLv3, impondo restrições severas de licenciamento e telemetria, além de apresentar alto consumo de recursos.

**Decisão:** Adotar o **RustFS** (`rustfs/rustfs:latest`) como serviço de armazenamento de arquivos de alta performance.
- O RustFS é 100% compatível com a API REST do S3 (inclusive suporte a AWS Signature Version 4).
- Licenciado sob **Apache 2.0** permissivo e escrito em Rust com foco em baixa latência e consumo mínimo de memória.
- Tanto a API em Go (`aws-sdk-go-v2/service/s3`) quanto o Worker Python (`boto3`) continuam usando seus clientes S3 padrão, apenas configurando `endpoint_url = http://rustfs:9000` e `UsePathStyle = true`.

**Por quê:**
- **Zero Lock-in & Licença Permissiva:** Apache 2.0 contra as restrições da AGPLv3 do MinIO.
- **Eficiência e Velocidade:** Implementação moderna em Rust, ideal para rodar leve em contêineres Docker locais.
- **Compatibilidade Transparente:** Não exige bibliotecas proprietárias; o mesmo código do SDK da AWS conecta sem modificação nas chamadas de `PutObject`, `GetObject` e `DeleteObject`.

**Alternativas Descartadas:**
- *MinIO:* Descartado pela licença restritiva AGPLv3 e peso de inicialização.
- *LocalStack S3:* Overhead excessivo (pesado para subir apenas um bucket local).
- *Armazenamento local em disco (bind mount simples):* Descumpriria a rubrica de "usar serviço de armazenamento de objetos compatível".

**Consequências:**
- Porta 9000 exposta para a API REST S3 e porta 9001 para a interface Web de gerenciamento.
- Um bucket `estacionamento-fotos-veiculos` deve ser assegurado na inicialização.

**Onde:** `docker-compose.yml`, `api/internal/config/config.go`, `worker/storage/s3_store.py`.

---

## D12. PostgreSQL em Container Gerenciado com Migrações Automáticas

**Contexto:** No Trabalho 1, o PostgreSQL rodava como serviço gerenciado no Amazon RDS (ou emulado no Floci). O Trabalho 2 exige um banco de dados relacional gerenciado diretamente pela equipe em container Docker.

**Decisão:** Utilizar a imagem oficial `postgres:16-alpine` no Docker Compose:
- Volume persistente nomeado (`postgres_data`) para garantir integridade dos dados entre reinicializações.
- Automação de migração: A API em Go executa os scripts DDL (`api/internal/repository/migrations/`) automaticamente no momento de inicialização (Fail-Fast se o schema estiver defasado).
- Variável `DATABASE_URL=postgres://app_user:app_password@postgres:5432/estacionamento?sslmode=disable`.

**Por quê:**
- Leveza e confiabilidade da distribuição Alpine Linux.
- Eliminação de qualquer dependência externa ou configuração manual prévia para o avaliador.

**Consequências:**
- Ordem de inicialização protegida por `healthcheck` (`pg_isready -U app_user -d estacionamento`) no compose para que a API só conecte quando o banco estiver pronto.

**Onde:** `docker-compose.yml`, `api/internal/repository/migrate.go`.

---

## D13. RabbitMQ para Mensageria Assíncrona (em vez de Kafka)

**Contexto:** O Trabalho 1 utilizava Amazon SNS/SQS para desacoplar a requisição da cancela de entrada do processamento pesado de visão computacional. O Trabalho 2 exige a substituição por RabbitMQ ou Kafka.

**Decisão:** Adotar o **RabbitMQ** (`rabbitmq:3-management-alpine`):
- Fila de trabalho durável: `ocr-processamento-fila`.
- DLQ (Dead Letter Queue): `ocr-processamento-fila-dlq` configurada via `x-dead-letter-exchange` para mensagens venenosas após esgotamento de retentativas.
- Publicação pela API em Go via driver AMQP 0-9-1 (`github.com/rabbitmq/amqp091-go`).
- Consumo pelo Worker Python via `pika` com confirmação manual (`basic_ack` / `basic_nack`).

**Por quê RabbitMQ e não Kafka:**
- **Modelo de Mensageria Alinhado ao Problema:** O estacionamento precisa de uma *Fila de Trabalho* (Task Queue / Competing Consumers), onde cada carro é processado exatamente uma vez pelo worker disponível. O RabbitMQ foi concebido especificamente para esse padrão. O Kafka é um log de eventos distribuído particionado (Streaming Log), que introduz complexidade desnecessária de gerenciamento de offsets, partições e rebalanceamento de grupos de consumidores.
- **Consumo de Recursos:** O RabbitMQ em Alpine consome ~80-120 MB de RAM. O Kafka (mesmo com KRaft sem Zookeeper) exige a JVM rodando com consumo acima de 1 GB de RAM, tornando o `docker compose` pesado para máquinas modestas.
- **Interface Visual de Gestão Embutida:** O RabbitMQ Management na porta `15672` permite ao professor/avaliador acompanhar em tempo real a taxa de mensagens por segundo, mensagens enfileiradas e a DLQ diretamente no navegador.

**Consequências:**
- Dependência `github.com/rabbitmq/amqp091-go` na API Go e `pika` no Worker Python.
- Configuração de `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/`.

**Onde:** `api/internal/repository/rabbitmq.go`, `worker/worker.py`, `docker-compose.yml`.

---

## D14. Continuidade do DynamoDB NoSQL com Suporte Dual (Cloud/Local)

**Contexto:** O item 1.a da especificação oficial estabelece: *"A aplicação deve continuar usando o serviço de banco de dados não-relacional da AWS (o DynamoDB ou Amazon DocumentDB) para armazenar e consultar as informações necessárias para a sua aplicação"*. Ao mesmo tempo, para que o comando `docker compose up -d` funcione em qualquer máquina offline sem exigir chaves reais da AWS, o sistema precisa de autonomia.

**Decisão:** Manter a tabela `AuditoriaEstacionamento` no DynamoDB com estratégia dual:
- Se variáveis de credenciais AWS estiverem preenchidas (`AWS_ACCESS_KEY_ID`, etc.), conecta ao Amazon DynamoDB na nuvem.
- Se nenhuma credencial for informada ou `AWS_ENDPOINT_URL` apontar para `http://dynamodb-local:8000`, conecta ao contêiner oficial `amazon/dynamodb-local:latest`.

**Por quê:** Atende simultaneamente à imposição formal 1.a da especificação acadêmica e ao critério de reprodutibilidade local sem dependências externas.

**Onde:** `api/internal/repository/dynamodb.go`, `worker/storage/audit.py`, `docker-compose.yml`.

---

## D15. Duas Cancelas Físicas e Desacoplamento entre Pagamento e Saída

**Contexto:** Na versão anterior, o pagamento da sessão via `POST /exits/{id}/pay` encerrava o ticket e liberava a vaga no Redis de forma síncrona. No mundo real de um estacionamento automatizado:
1. O cliente paga o ticket no totem de autoatendimento ou no caixa, e em seguida caminha até o seu carro.
2. Durante esse tempo, a vaga **continua ocupada**. Liberar a vaga no momento do pagamento causaria *overbooking* imediato na cancela de entrada.
3. Ao chegar à barreira de saída, existe uma **Cancela de Saída** física com câmera LPR. Ela tenta ler a placa do carro; se identificar que o ticket daquela placa está pago, abre a cancela automaticamente.
4. Caso a placa esteja suja, danificada ou o OCR falhe na saída, o motorista insere o ticket impresso em papel (cujo ID foi fornecido na cancela de entrada) para validar o pagamento e abrir a cancela.

**Decisão:**
1. **Cancela de Entrada (`POST /entries`):** Registra foto, reserva a vaga, emite ticket com ID único e abre a cancela de entrada. Status: `PARKED` desde a emissão (D20).
2. **Terminal de Pagamento / Caixa (`POST /sessions/{id}/pay`):** Registra o pagamento e altera o status para `PAID`. **A vaga continua ocupada no Redis!** O ticket/placa passa a estar autorizado a sair.
3. **Cancela de Saída com Dupla Validação:**
   - **Fluxo Principal (Câmera / Placa — `POST /barrier/exit/plate`):** Lê a placa na cancela de saída. Se existir sessão ativa com status `PAID`, a cancela abre!
   - **Fluxo de Contingência (Ticket em Papel — `POST /barrier/exit/ticket`):** Se a placa não for lida, o motorista informa o ID do ticket em papel. Se estiver `PAID`, a cancela abre!
   - **Efeito Físico de Saída:** Somente na validação da cancela de saída o status passa para `EXITED`, a vaga é incrementada no Redis (`INCR spots:available`) e o evento `EXIT_BARRIER_OPENED` é auditado no DynamoDB.
   - **Bloqueio de Inadimplência:** Se o ticket ou placa constar como `PARKED` (não pago), a cancela de saída recusa a abertura e instrui o motorista a realizar o pagamento no caixa.

**Por quê:** Reflete com fidelidade máxima a arquitetura de engenharia de tráfego e automação predial de estacionamentos reais, prevenindo o problema de overbooking entre o pagamento e a efetiva desocupação da vaga.

**Onde:** `api/internal/service/parking.go`, `api/internal/handler/handler.go`, `web/src/pages/EntryBarrierPage.tsx`, `web/src/pages/ExitBarrierPage.tsx`, `web/src/pages/PaymentPage.tsx`.

---

## D16. Manutenção do Redis para Contagem de Vagas e Anti-Overbooking

**Contexto:** O ElastiCache era usado no Trabalho 1 para leitura de altíssima frequência da quantidade de vagas sem sobrecarregar o banco relacional.

**Decisão:** Manter o **Redis** (`redis:7-alpine`) no Docker Compose gerenciando a chave `spots:available`:
- Leitura instantânea (< 2ms) para os displays e totens.
- A vaga é reservada pela API na entrada, de forma atômica (D20); o worker não toca no contador.
- A vaga é incrementada apenas quando o veículo cruza a Cancela de Saída (`EXITED`).
- Em caso de falha ou reinicialização do Redis, a API reconcilia o valor consultando as sessões ativas no PostgreSQL:
  $$\text{vagas} = \max(0, \text{capacidade} - \text{COUNT}(\text{status} \in \{\text{'PARKED'}, \text{'PAID'}\}))$$

**Por quê:** Garante que o painel de vagas suporte milhares de consultas simultâneas sem degradar o PostgreSQL, mantendo a consistência transacional com reconciliação automática.

**Onde:** `api/internal/repository/redis.go`, `docker-compose.yml`.

---

## D17. Dockerfiles Multi-Stage e Orquestração com Docker Compose

**Decisão:**
- **`api/Dockerfile`:** Build multi-stage: `golang:1.24-alpine` para compilação estática em binário único, copiado para imagem final enxuta baseada em Alpine Linux (< 25 MB).
- **`web/Dockerfile`:** Multi-stage: compilação dos assets Vite/React com `node:22-alpine` e execução sob `nginx:alpine` (< 30 MB) configurado com reverse proxy `/api/` para o container do backend.
- **`worker/Dockerfile`:** Python com instalação dos pacotes de sistema necessários (`tesseract-ocr`, `tesseract-ocr-por`, `libgl1`, `libglib2.0-0`), isolado com `uv` ou requirements e executado com usuário não-root.
- **`docker-compose.yml`:** Arquivo na raiz do repositório contendo as 8 definições de serviço, volumes persistentes e rede bridge dedicada `estacionamento_net`, com `healthcheck` em todos os serviços de banco e mensageria para inicialização determinística via `condition: service_healthy`.

**Por quê:** Atende integralmente aos itens 2, 3, 4, 5, 6 e 7 da especificação, garantindo nota máxima nos critérios de Docker e Docker Compose.

**Onde:** `api/Dockerfile`, `web/Dockerfile`, `worker/Dockerfile`, `docker-compose.yml`.

---

## D18. Preparação do Bônus Kubernetes (`k8s/`)

**Contexto:** A especificação prevê 1 ponto extra para a implantação no Kubernetes utilizando Deployments, Services (NodePort e ClusterIP), ConfigMaps e Secrets.

**Decisão:** Criar a pasta `k8s/` contendo manifestos YAML declarativos e limpos:
- `k8s/namespace.yaml`: Namespace dedicado `estacionamento`.
- `k8s/secrets.yaml` & `k8s/configmap.yaml`: Configurações de ambiente e credenciais desacopladas.
- `k8s/postgres.yaml`, `k8s/rustfs.yaml`, `k8s/rabbitmq.yaml`, `k8s/redis.yaml`: Deployments e Services internos do tipo `ClusterIP`.
- `k8s/api.yaml` & `k8s/worker.yaml`: Deployment do backend e do consumidor assíncrono.
- `k8s/web.yaml`: Deployment do frontend com Service do tipo `NodePort` (porta 30080) para acesso externo facilitado em ambientes Minikube/Kind.

**Por quê:** Garante a pontuação extra de 1,0 ponto sem interferir na simplicidade da execução padrão com Docker Compose.

**Onde:** `k8s/`.

---

## D19. Retorno ao Modelo Open-Source em Python (OpenCV + Tesseract) e Descarte do Amazon Rekognition

**Contexto:** No Trabalho 1, para atingir 96% de acurácia no deploy em nuvem na AWS, foi adicionada a integração com o serviço proprietário Amazon Rekognition (`DetectText`) via AWS SDK (`worker/ocr/rekognition.py`), mantendo o Tesseract como fallback. Para o Trabalho 2, a premissa fundamental da disciplina é eliminar dependências de serviços pagos ou proprietários de nuvem, priorizando soluções de código aberto executando em contêineres Docker.

**Decisão:** Descartar completamente a chamada ao Amazon Rekognition no Worker e restabelecer o **pipeline de visão computacional 100% open-source em Python**:
1. **Localização de Placas via OpenCV (`locate.py`):**
   - Detecção cromática da faixa azul Mercosul (`find_mercosul_plates`) em espaço de cores HSV.
   - Detecção geométrica de bordas retangulares (`find_plate_lines`) via Canny e contornos morfológicos (para placas antigas cinzas e placas sobre carros azuis).
2. **Pré-processamento com Pillow/OpenCV (`processor.py`):**
   - Redimensionamento adaptativo, conversão para escala de cinza e binarização/limiarização (Otsu/Adaptive Thresholding) para isolar os caracteres.
3. **Extração de Caracteres com Tesseract OCR (`processor.py`):**
   - Execução do Tesseract local (`pytesseract`) com whitelisting de caracteres alfanuméricos e múltiplos modos de segmentação de página (PSM 7, 8, 13).
4. **Normalização Sintática Mercosul (`clean.py`):**
   - Validação e correção de confusões de caracteres comuns (`1` $\leftrightarrow$ `I`, `0` $\leftrightarrow$ `O`) cobrindo os 4 formatos do Mercosul (Brasil, Argentina, Uruguai e Paraguai).

**Por quê:**
- **Zero Lock-in & Independência de Nuvem:** Elimina chamadas de rede externas e a necessidade de permissões/credenciais IAM na AWS para o processamento de imagens.
- **Auto-contido no Container:** O container do worker inclui o binário do Tesseract (`tesseract-ocr` e `tesseract-ocr-por`) e as bibliotecas OpenCV headless pré-instaladas, operando com latência ultrabaixa e determinismo em qualquer máquina host.

**Consequências:**
- O worker roda com `OCR_ENGINE=tesseract` por padrão, sem tocar em nenhuma API da AWS.
- O código de `worker/ocr/rekognition.py` é removido/desativado do fluxo principal.
- Casos em que o OCR não atinja certeza imediata ficam com `plate_status = UNREADABLE`. A sessão segue `PARKED` e o ticket vale; o operador pode digitar a placa, mas isso é opcional (D20).

**Onde:** `worker/ocr/locate.py`, `worker/ocr/processor.py`, `worker/ocr/clean.py`, `worker/worker.py`, `worker/config.py`.

---

## D20. O Ticket Identifica o Carro; a Foto é Evidência e Atalho

**Substitui:** a D4 do Trabalho 1 ("placa ilegível vira `FAILED` na hora; o caixa digita a placa").

**Contexto:** A sessão nascia `PROCESSING` e só virava `PARKED` (ocupando a vaga) quando o worker lia a placa. Uma foto ilegível deixava a sessão `FAILED` e o motorista dependia de o operador digitar a placa. Na prática, o OCR era pré-requisito para o carro existir no sistema:
- Com o Tesseract acertando cerca de metade das fotos reais, metade dos carros exigia intervenção manual.
- Com o worker ou a fila fora do ar, nenhum carro era contado: o painel mostrava vagas que não existiam.
- Sessão `FAILED` não ocupava vaga no contador, embora o carro estivesse no pátio.

**Decisão:**
1. **O ticket (ID da sessão) é a identidade do carro.** `POST /entries` cria a sessão já `PARKED` e ela é pagável pelo ticket desde a emissão.
2. **A API reserva a vaga na entrada**, com um script Lua atômico no Redis (`Reserve`). Pátio lotado devolve `409` e a cancela não abre. Sem a chave (Redis reiniciado), a decisão é tomada pelo PostgreSQL e o contador é regravado. Se o upload da foto ou o `INSERT` falharem, a vaga é devolvida.
3. **A leitura da placa vira uma coluna à parte, `plate_status`:** `PENDING` $\rightarrow$ `READ` ou `UNREADABLE` (worker), ou `MANUAL` (caixa). Os status `PROCESSING` e `FAILED` deixam de existir; a migração `000002` converte os dados antigos.
4. **O worker só enriquece:** grava a placa enquanto `plate_status = 'PENDING'`, não altera `status` e não usa mais o Redis. Uma placa digitada pelo operador nunca é sobrescrita; um carro que já pagou ainda tem a placa gravada (evidência).
5. **Fila fora do ar não derruba a entrada:** a sessão é criada com `UNREADABLE` e a auditoria `ENTRY` registra `ocr_queued: false`. O evento `ENTRY_FAILED` deixa de ser emitido.
6. **A foto é servida pela API** (`GET /sessions/{id}/photo`) e aparece no caixa e na auditoria. O tipo do arquivo vem de quem fez o upload, então só imagens conhecidas são devolvidas como imagem; o resto sai como download, com `X-Content-Type-Options: nosniff`.
7. **`PATCH /sessions/{id}` continua existindo como conveniência** (busca por placa), sem mexer no contador.

**Por quê:**
- O estacionamento funciona com o OCR errando, lento ou parado. A leitura da placa passa a ser o que ela é no mundo real: um registro para auditoria e um atalho na cancela de saída.
- O contador de vagas passa a refletir os carros que entraram, não os que tiveram a placa lida.
- O worker fica mais simples: sem contador, sem compensação de `DECR`, uma única condição de idempotência.

**Consequências:**
- Contrato da API: `status` perde `PROCESSING` e `FAILED`; toda sessão traz `plate_status`. API, worker e frontend mudam juntos.
- Depois de aplicar a migração num banco com dados antigos, apague a chave `spots:available`: as sessões que eram `PROCESSING`/`FAILED` não ocupavam vaga no contador e a API o recalcula pelo banco.
- Enquanto `EXITED` não existir, o ciclo é `PARKED` $\rightarrow$ `PAID`, a vaga é devolvida no pagamento e só `PARKED` conta como ativa. Quando as rotas da cancela de saída entrarem, `PAID` passa a contar e o `INCR` vai para a saída (D15).
- Na última entrega de uma mensagem o worker encerra a leitura como `UNREADABLE`, mesmo que a causa tenha sido uma indisponibilidade. Reprocessar a mensagem pela DLQ depois disso não grava a placa; para tentar de novo, volte `plate_status` para `PENDING` antes.

**Onde:** `api/internal/service/parking.go`, `api/internal/repository/redis.go`, `api/internal/repository/migrations/000002_add_plate_status.up.sql`, `worker/worker.py`, `worker/storage/session_repo.py`, `web/src/pages/EntryPage.tsx`, `web/src/pages/PaymentPage.tsx`. Plano: [PLANO-FOTO-OPCIONAL.md](PLANO-FOTO-OPCIONAL.md).
