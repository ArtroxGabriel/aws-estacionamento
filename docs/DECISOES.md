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
| **Implementação da saída** | — | **`EXITED`, `paid_at`, saída por placa ou ticket, vaga liberada uma vez** | [D21](#d21-implementação-das-duas-cancelas) |
| **API na fila e nos endpoints** | SQS + `AWS_ENDPOINT_URL` único | **Publisher RabbitMQ com confirmação; um endpoint por serviço** | [D22](#d22-api-publicando-no-rabbitmq-e-um-endpoint-por-serviço) |

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
- Fila de espera `ocr-processamento-fila-retry` (TTL 30 s, volta sozinha para a principal) e DLQ `ocr-processamento-fila-dlq`. O worker conta as tentativas no cabeçalho `x-attempt` e manda para a DLQ na 3ª falha (`docs/PLANO-TP2-WORKER.md`). A principal também tem `x-dead-letter-routing-key` para a DLQ, como rede de segurança.
- Topologia declarada só em `infra/rabbitmq/definitions.json`, carregado pelo broker na subida. API e worker fazem declaração passiva.
- Publicação pela API em Go via driver AMQP 0-9-1 (`github.com/rabbitmq/amqp091-go`), com confirmação do broker (D22).
- Consumo pelo Worker Python via `pika`, `prefetch_count=1` e confirmação manual (`basic_ack`).

**Por quê RabbitMQ e não Kafka:**
- **Modelo de Mensageria Alinhado ao Problema:** O estacionamento precisa de uma *Fila de Trabalho* (Task Queue / Competing Consumers), onde cada carro é processado exatamente uma vez pelo worker disponível. O RabbitMQ foi concebido especificamente para esse padrão. O Kafka é um log de eventos distribuído particionado (Streaming Log), que introduz complexidade desnecessária de gerenciamento de offsets, partições e rebalanceamento de grupos de consumidores.
- **Consumo de Recursos:** O RabbitMQ em Alpine consome ~80-120 MB de RAM. O Kafka (mesmo com KRaft sem Zookeeper) exige a JVM rodando com consumo acima de 1 GB de RAM, tornando o `docker compose` pesado para máquinas modestas.
- **Interface Visual de Gestão Embutida:** O RabbitMQ Management na porta `15672` permite ao professor/avaliador acompanhar em tempo real a taxa de mensagens por segundo, mensagens enfileiradas e a DLQ diretamente no navegador.

**Consequências:**
- Dependência `github.com/rabbitmq/amqp091-go` na API Go e `pika` no Worker Python.
- Configuração de `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/`.

**Onde:** `api/internal/repository/rabbitmq.go`, `worker/consumer.py`, `infra/rabbitmq/definitions.json`, `infra/docker-compose.yaml`.

---

## D14. Continuidade do DynamoDB NoSQL com Suporte Dual (Cloud/Local)

**Contexto:** O item 1.a da especificação oficial estabelece: *"A aplicação deve continuar usando o serviço de banco de dados não-relacional da AWS (o DynamoDB ou Amazon DocumentDB) para armazenar e consultar as informações necessárias para a sua aplicação"*. Ao mesmo tempo, para que o comando `docker compose up -d` funcione em qualquer máquina offline sem exigir chaves reais da AWS, o sistema precisa de autonomia.

**Decisão:** Manter a tabela `AuditoriaEstacionamento` no DynamoDB com estratégia dual:
- Se variáveis de credenciais AWS estiverem preenchidas (`AWS_ACCESS_KEY_ID`, etc.), conecta ao Amazon DynamoDB na nuvem.
- Com `DYNAMODB_ENDPOINT_URL=http://dynamodb-local:8000` (o que o compose define), conecta ao contêiner oficial `amazon/dynamodb-local:latest`. Endpoint vazio = AWS real (D22).

**Por quê:** Atende simultaneamente à imposição formal 1.a da especificação acadêmica e ao critério de reprodutibilidade local sem dependências externas.

**Onde:** `api/internal/repository/dynamodb.go`, `worker/storage/audit.py`, `infra/docker-compose.yaml`.

---

## D15. Duas Cancelas Físicas e Desacoplamento entre Pagamento e Saída

**Contexto:** Na versão anterior, o pagamento da sessão via `POST /exits/{id}/pay` encerrava o ticket e liberava a vaga no Redis de forma síncrona. No mundo real de um estacionamento automatizado:
1. O cliente paga o ticket no totem de autoatendimento ou no caixa, e em seguida caminha até o seu carro.
2. Durante esse tempo, a vaga **continua ocupada**. Liberar a vaga no momento do pagamento causaria *overbooking* imediato na cancela de entrada.
3. Ao chegar à barreira de saída, existe uma **Cancela de Saída** física com câmera LPR. Ela tenta ler a placa do carro; se identificar que o ticket daquela placa está pago, abre a cancela automaticamente.
4. Caso a placa esteja suja, danificada ou o OCR falhe na saída, o motorista insere o ticket impresso em papel (cujo ID foi fornecido na cancela de entrada) para validar o pagamento e abrir a cancela.

**Decisão:**
1. **Cancela de Entrada (`POST /entries`):** Registra foto, reserva a vaga, emite ticket com ID único e abre a cancela de entrada. Status: `PARKED` desde a emissão (D20).
2. **Terminal de Pagamento / Caixa (`POST /exits/{id}/pay`, rota mantida do TP1; ver D21):** Registra o pagamento e altera o status para `PAID`. **A vaga continua ocupada no Redis!** O ticket/placa passa a estar autorizado a sair.
3. **Cancela de Saída com Dupla Validação:**
   - **Fluxo Principal (Câmera / Placa — `POST /barrier/exit/plate`):** Recebe a placa reconhecida pela câmera da saída (sem OCR na API; ver D21). Se existir sessão no pátio com essa placa e status `PAID`, a cancela abre!
   - **Fluxo de Contingência (Ticket em Papel — `POST /barrier/exit/ticket`):** Se a placa não for lida, o motorista informa o ID do ticket em papel. Se estiver `PAID`, a cancela abre!
   - **Efeito Físico de Saída:** Somente na validação da cancela de saída o status passa para `EXITED`, a vaga é incrementada no Redis (`INCR spots:available`) e o evento `EXIT_BARRIER_OPENED` é auditado no DynamoDB.
   - **Bloqueio de Inadimplência:** Se o ticket ou placa constar como `PARKED` (não pago), a cancela de saída recusa a abertura e instrui o motorista a realizar o pagamento no caixa.

**Por quê:** Reflete com fidelidade máxima a arquitetura de engenharia de tráfego e automação predial de estacionamentos reais, prevenindo o problema de overbooking entre o pagamento e a efetiva desocupação da vaga.

**Implementação:** ver D21.

**Onde:** `api/internal/service/parking.go`, `api/internal/handler/handler.go`, `web/src/pages/EntryPage.tsx`, `web/src/pages/ExitBarrierPage.tsx`, `web/src/pages/PaymentPage.tsx`.

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
- Com a cancela de saída (D21), o ciclo é `PARKED` $\rightarrow$ `PAID` $\rightarrow$ `EXITED`: `PAID` conta como ativa e o `INCR` foi do pagamento para a saída.
- Na última entrega de uma mensagem o worker encerra a leitura como `UNREADABLE`, mesmo que a causa tenha sido uma indisponibilidade. Reprocessar a mensagem pela DLQ depois disso não grava a placa; para tentar de novo, volte `plate_status` para `PENDING` antes.

**Onde:** `api/internal/service/parking.go`, `api/internal/repository/redis.go`, `api/internal/repository/migrations/000002_add_plate_status.up.sql`, `worker/worker.py`, `worker/storage/session_repo.py`, `web/src/pages/EntryPage.tsx`, `web/src/pages/PaymentPage.tsx`. Plano: [PLANO-FOTO-OPCIONAL.md](PLANO-FOTO-OPCIONAL.md).

---

## D21. Implementação das Duas Cancelas

**Contexto:** A D15 definiu o fluxo de duas cancelas, mas deixou pontos em aberto: o nome da rota de pagamento, como a cancela de saída recebe a placa, o que fazer com placas repetidas, onde guardar a hora do pagamento e o que acontece com as sessões `PAID` já gravadas. As escolhas estão detalhadas em `.kiro/specs/api-rabbitmq-exit-barrier/design.md` (D-A1 a D-A12).

**Decisão:**
1. **Rota e auditoria do pagamento mantidas:** `POST /exits/{id}/pay` e `EXIT_PAYMENT`. Renomear para `/sessions/{id}/pay` e `PAYMENT_RECEIVED` quebraria o frontend e os testes sem ganho.
2. **Pagar não mexe no contador.** A sessão vai para `PAID` e ganha `paid_at`; `exited_at` passa a significar "passou pela cancela de saída".
3. **A saída abre por `UPDATE ... WHERE status = 'PAID' RETURNING`.** Só a requisição que mudou a linha faz o `INCR`: pedidos simultâneos para o mesmo carro liberam a vaga uma vez e o outro recebe `already_exited`.
4. **A cancela de saída recebe a placa em JSON, sem foto e sem OCR.** Rodar OCR na requisição contraria o desacoplamento assíncrono e a D20. A câmera da saída é simulada pelo campo da placa no totem.
5. **Só placa confiável abre:** `plate_status` `READ` ou `MANUAL`, de um carro no pátio. Entre vários `PAID` com a mesma placa, abre o de pagamento mais antigo. Placa `PENDING` ou `UNREADABLE` nunca casa: o ticket é a alternativa e sempre funciona.
6. **Erros com código estável** (`payment_pending`, `already_exited`, `plate_not_found`, `session_not_found`, `invalid_*`), para o totem não depender do texto.
7. **O caixa pode corrigir a placa de um carro `PAID`**, para ele sair pela placa. Excluir um carro `PARKED` ou `PAID` devolve a vaga; um `EXITED` já devolveu.
8. **Migração `000003`:** cria `paid_at` e converte as sessões `PAID` antigas em `EXITED`, porque pela regra anterior esses carros já tinham saído e devolvido a vaga. O contador continua válido sem ser apagado.
9. **O totem aceita os 8 caracteres em destaque no ticket impresso** e resolve o ID completo pela listagem de sessões (só com uma sessão única), porque a API exige o ID inteiro.

**Por quê:** cada escolha mantém a D20 (o ticket sempre basta) e torna a liberação da vaga idempotente, que é o que impede o contador de divergir.

**Alternativas descartadas:**
- **OCR síncrono na saída:** prende a cancela ao tempo do Tesseract e falha justamente nas fotos ruins.
- **Saída por placa sem conferir `plate_status`:** uma leitura errada (ou uma placa ainda não lida) poderia abrir a cancela para outro carro.

**Verificação:** testes de sequências aleatórias de entrada, pagamento, saída e exclusão conferem que o contador sempre bate com os carros no pátio e que ninguém sai sem pagar; um teste com 20 saídas simultâneas confere a liberação única. O fluxo inteiro foi conferido no compose.

**Onde:** `api/internal/service/parking.go`, `api/internal/repository/postgres.go`, `api/internal/repository/migrations/000003_exit_barrier.up.sql`, `api/internal/handler/handler.go`, `web/src/pages/ExitBarrierPage.tsx`.

---

## D22. API Publicando no RabbitMQ e um Endpoint por Serviço

**Contexto:** No TP1 a API publicava no SQS e usava um único `AWS_ENDPOINT_URL` para S3, SQS e DynamoDB (o Floci emulava os três na mesma porta). No TP2 cada serviço é um container diferente, e o worker já tinha migrado para o RabbitMQ.

**Decisão:**
1. **Publisher RabbitMQ atrás da mesma interface `EventPublisher`.** O serviço e os handlers não mudaram.
2. **Conexão sob demanda:** abre na primeira publicação e é refeita depois de qualquer falha. A API sobe e atende com o broker fora do ar.
3. **Declaração passiva da fila**, como no worker: a topologia é do `definitions.json`.
4. **Publicação persistente, `mandatory`, com confirmação do broker e limite de 5 s.** Só conta como publicada a mensagem que o broker confirmou.
5. **`RABBITMQ_URL` vazio é falha, não um "não faz nada".** Sem fila, a sessão fica `UNREADABLE` em vez de esperar para sempre um worker que nunca vai receber a mensagem.
6. **Um endpoint por serviço:** `S3_ENDPOINT_URL` (com path-style) e `DYNAMODB_ENDPOINT_URL`, os mesmos nomes do worker. Vazio = AWS real. `AWS_ENDPOINT_URL` e `SQS_QUEUE_URL` não são mais lidos.
7. **Bucket criado na subida** quando o storage é local, como segunda garantia além do `createbuckets` do compose.
8. **Healthcheck do RabbitMQ por porta** (`check_port_connectivity`): o `ping` ficava saudável antes de a porta 5672 aceitar conexões, e a primeira entrada podia perder a leitura da placa.

**Consequências:** a fila fora do ar continua não derrubando a entrada (D20); a entrada só fica até 5 s mais lenta. O deploy do TP1 na AWS não funciona com essa imagem da API.

**Onde:** `api/internal/repository/rabbitmq.go`, `api/internal/config/config.go`, `api/cmd/api/main.go`, `infra/docker-compose.yaml`.

---

## D23. Tarifa por Tempo de Permanência, com Tolerância de 15 Minutos

**Contexto:** a tarifa era um valor fixo por sessão (`FIXED_PARKING_RATE`), igual para quem ficou 5 minutos e para quem ficou o dia todo.

**Decisão:** seguir o modelo comum dos estacionamentos brasileiros, em três faixas e sem tabela por minuto:
1. **Até 15 min (inclusive): grátis.** O pagamento continua sendo registrado, com `amount_paid` 0, porque o carro ainda precisa de uma sessão `PAID` para passar pela cancela de saída.
2. **Acima de 15 min até 1 h: preço da 1ª hora** (R$ 10,00, o antigo `FIXED_PARKING_RATE`).
3. **Depois da 1ª hora: R$ 5,00 por hora ou fração.** 1 h 01 min já cobra a 2ª hora.

A permanência é `paid_at - entered_at`, calculada quando o caixa cobra. Valores em variáveis de ambiente (`PARKING_TOLERANCE_MINUTES`, `FIXED_PARKING_RATE`, `PARKING_EXTRA_HOUR_RATE`); o cálculo é uma função pura, `CalculateFee`, em `api/internal/service/pricing.go`.

**Valor a pagar antes de confirmar:** `GET /sessions` devolve `amount_due` nas sessões `PARKED`, calculado pela mesma `CalculateFee` do pagamento, e o caixa mostra o valor na linha e no botão "Confirmar pagamento". O front não recalcula nada, então a regra existe num lugar só. O valor é o do último carregamento da lista (a cada 15 s); o recibo mostra o valor realmente cobrado, que pode ser uma faixa acima se uma fronteira foi cruzada nesse intervalo.

**Fora do escopo (de propósito):** diária com teto, tarifa noturna, mensalista e cobrança de quem demora para sair depois de pagar (não existe tolerância de saída: o valor é fechado no pagamento).

**Onde:** `api/internal/service/pricing.go`, `api/internal/service/parking.go` (`PayExit`), `api/internal/config/config.go`, `infra/docker-compose.yaml`.
