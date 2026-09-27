# GOAL.md — Visão Geral do Sistema & Farol dos Módulos

> **Projeto:** Sistema Inteligente de Gestão de Estacionamento em Nuvem  
>
> **Disciplina:** Desenvolvimento de Software para Nuvem (UFC) — Profs. Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta  
>
> **Objetivo Deste Arquivo:** Servir como **fonte da verdade arquitetural** e **farol de revisão** para avaliar e alinhar as PRs de cada módulo desenvolvidas pela equipe.

---

## 1. Visão Geral do Sistema (A Jornada do Estacionamento)

O sistema automatiza o ciclo completo de um estacionamento inteligente, desde a chegada do veículo na cancela até a sua saída e pagamento, integrando **6 serviços centrais da AWS** com desacoplamento assíncrono e elasticidade horizontal.

```text
[Cancela / Totem] ──(1. Foto do Carro)──> [API Go]
                                            ├── Salva foto no S3
                                            ├── Cria sessão 'PROCESSING' no RDS
                                            ├── Envia evento direto na Fila SQS
                                            ├── Grava auditoria no DynamoDB
                                            └── Retorna ticket provisório (Cancela abre)

[Worker OCR Python] <──(2. Consome SQS)─────┘
        ├── Baixa foto do S3 e redimensiona
        ├── Extrai placa via Tesseract OCR
        ├── Atualiza sessão no RDS ('PARKED' + Placa)
        ├── Decrementa vagas no Redis (ElastiCache)
        └── Grava auditoria no DynamoDB

[Painel / Totem Web] ──(3. Consulta Vagas)──> [Redis ElastiCache] (Leitura ultrarrápida)

[Checkout / Saída] ──(4. Pagar / Liberar)──> [API Go]
                                            ├── Atualiza sessão no RDS ('PAID')
                                            ├── Incrementa vagas no Redis
                                            └── Grava auditoria no DynamoDB
```

---

## 2. Metas e Papéis de Cada Módulo

### 2.1. Cancela & Frontend (`web/` — React + Vite + TypeScript)
* **Propósito no Mundo Real:** É a interface do operador e o totem físico da cancela.
* **Responsabilidades:**
  1. **Totem de Entrada:** Simula a câmera da cancela. Permite capturar/submeter a foto frontal do veículo e enviar para a API via `POST /entries`. Exibe imediatamente o ticket preliminar gerado.
  2. **Display de Vagas em Tempo Real:** Consulta `GET /spots/available` em alta frequência para exibir aos motoristas se o estacionamento está lotado ou quantas vagas restam (sem sobrecarregar o banco relacional).
  3. **Terminal de Saída / Caixa:** Permite ao operador localizar a sessão (por ID ou placa) e processar o pagamento via `POST /exits/{id}/pay`, liberando a cancela de saída.
  4. **Painel de Auditoria (Opcional/Bônus):** Exibição do histórico de eventos gravados.

### 2.2. API Backend (`api/` — Go + Uber Fx)
* **Propósito no Mundo Real:** É o orquestrador transacional de alta performance e baixo overhead que recebe os eventos da cancela e do caixa.
* **Diretriz de Segurança (KISS):** 
  * Por ser um projeto acadêmico focado em computação em nuvem, **não há camadas de autenticação/autorização** (sem API Key, JWT ou validação de origem). 
  * Os endpoints são públicos e abertos. PRs que tentem introduzir autenticação complexa devem ser recusadas (YAGNI).
* **Responsabilidades:**
  1. **Ingestão na Cancela (`POST /entries`):**
     * Recebe a imagem binária da foto da frente do veículo.
     * Faz upload no **Amazon S3** (`photos/{session_id}_{filename}`).
     * Cria registro transacional preliminar no **Amazon RDS** (PostgreSQL) com `status = 'PROCESSING'` e placa nula.
     * Publica mensagem diretamente na fila **Amazon SQS** (`ocr-processamento-fila`).
     * Grava log de auditoria no **Amazon DynamoDB** com ação `ENTRY`.
     * Retorna `201 Created` com o ticket preliminar para liberar a cancela sem esperar pelo OCR.
  2. **Consulta Rápida de Vagas com Auto-Recuperação Anti-Overbooking (`GET /spots/available`):**
     * **Caminho Feliz (Cache Hit):** Lê diretamente do **Amazon ElastiCache** (Redis) na chave `spots:available`. Resposta ultrarrápida em < 2ms, sem onerar o banco relacional.
     * **Cenário de Queda/Reboot do Redis (Cache Miss / Falha):** Se o Redis cair e voltar no meio do dia, a chave terá sumido. **A API nunca assume o valor padrão de 50**, pois isso geraria sobrelotação (overbooking) se já existirem carros estacionados. Apenas quando o Redis falhar/retornar nulo, a API consulta o **Amazon RDS**:
       $$\text{vagas\_disponiveis} = \max(0, \text{capacidade\_total} - \text{COUNT}(\text{sessions ativas}))$$
       *(onde sessões ativas são as que possuem `status IN ('PROCESSING', 'PARKED')`)*.
     * **Reidratação Automática:** A API salva o valor real recalculado no Redis (`SET spots:available`) para que as requisições seguintes voltem a ser atendidas instantaneamente da memória.
  3. **Pagamento e Saída (`POST /exits/{id}/pay`):**
     * Localiza a sessão no **RDS**, atualiza `status = 'PAID'`, calcula tarifa e preenche `exited_at`.
     * Incrementa atomicamente o contador no **Redis** (`INCR spots:available`).
     * Grava log de auditoria no **DynamoDB** com ação `EXIT_PAYMENT`.
  4. **Healthcheck (`GET /health`):**
     * Retorna `200 OK` (`{"status":"UP"}`) para o Target Group do Load Balancer monitorar a saúde da instância.

### 2.3. Worker de Visão Computacional (`worker/` — Python + OCR)
* **Propósito no Mundo Real:** É o agente autônomo inteligente em segundo plano que processa as fotos dos carros para identificar quem entrou.
* **Mecanismo de Fila: SQS Direto via Long Polling:**
  * O AWS SQS opera por *pull*. Usamos `WaitTimeSeconds=20` no `receive_message`. A conexão fica aberta e recebe a mensagem no milissegundo em que a API publica, com **0% de consumo de CPU** quando ocioso.
  * **Simplificação Máxima (Sem SNS):** A API publica direto na fila SQS (`ocr-processamento-fila`). O worker recebe o JSON diretamente no `Body` da mensagem SQS, **sem envelopes intermediários do SNS**.
  * Atende com folga ao requisito da rubrica de desacoplamento assíncrono por mensageria (**Amazon SNS/SQS**).
* **Responsabilidades:**
  1. **Consumo Desacoplado:** Faz polling contínuo (Long Polling 20s) na fila **Amazon SQS** (`ocr-processamento-fila`).
  2. **Tratamento de Imagem:** Baixa a foto original do **Amazon S3**, aplica pré-processamento (rescaling, escala de cinza, limiarização via Pillow/OpenCV) para evidenciar a área da placa.
  3. **Extração de Placa (OCR):** Roda o Tesseract OCR na imagem tratada e normaliza os caracteres para padrão Mercosul (`ABC1D23`) ou antigo (`ABC-1234`).
  4. **Confirmação da Vaga:**
     * Atualiza a sessão no **RDS** (`UPDATE sessions SET license_plate = :plate, status = 'PARKED' WHERE id = :id`).
     * Decrementa atomicamente as vagas no **Redis** (`DECR spots:available`).
     * Grava log de auditoria no **DynamoDB** com ação `OCR_PROCESSING` e a placa identificada.
     * Remove a mensagem processada do **SQS** (`DeleteMessage`).

### 2.4. Infraestrutura, Custos & Elasticidade (`infra/` — OpenTofu)
* **Propósito no Mundo Real:** Provisiona toda a infraestrutura em nuvem de forma reproduzível e resiliente, garantindo que o sistema suporte picos de entrada no estacionamento dentro do budget de estudante ($50).
* **Guarda-Corpo de Custos (Budget $50 AWS Academy):**
  * **Tamanhos Estritamente Econômicos:** Instâncias EC2 `t2.micro` ou `t3.micro`, RDS `db.t3.micro` Single-AZ, ElastiCache `cache.t3.micro` nó único.
  * **DynamoDB & SQS:** Modo sob demanda / Pay-Per-Request (custo fixo zero).
  * **Sem Recursos Caros:** Proibido adicionar NAT Gateways, Multi-AZ, Load Balancers redundantes ou discos provisionados IOPS (usar gp2/gp3 padrão de 20GB).
  * **Infraestrutura Efêmera:** Desenvolvimento é 100% no emulador local (Floci). Na AWS real, os recursos só sobem para testes e gravação do vídeo, devendo ser destruídos logo após (`task tf:destroy:aws`).
* **Responsabilidades:**
  1. **Provisionamento dos 6 Serviços AWS:**
     * **S3:** Bucket de armazenamento de fotos dos veículos.
     * **RDS:** PostgreSQL gerenciado para persistência transacional (`sessions`).
     * **ElastiCache:** Cluster Redis para contagem de vagas em memória.
     * **DynamoDB:** Tabela `AuditoriaEstacionamento` para rastreamento imutável de ações.
     * **SQS:** Fila `ocr-processamento-fila` para desacoplamento direto.
     * **EC2:** Hospedagem da aplicação sob Load Balancer.
  2. **Dual-Mode de Execução:**
     * `use_localstack = true`: Roda 100% local via Docker Compose com Floci (S3, SQS, DynamoDB, RDS e ElastiCache).
     * `use_localstack = false`: Provisiona os recursos reais na AWS Academy (usando `LabRole`).
  3. **Elasticidade Horizontal (Parte 2 do Trabalho):**
     * **ALB (Application Load Balancer):** Distribui o tráfego de entrada entre as instâncias da API.
     * **ASG (Auto Scaling Group):** Mantém entre 1 (mínimo) e 3 (máximo) instâncias EC2 (`t2.micro` ou `t3.micro`).
     * **Alarmes CloudWatch:**
       * CPU > 70% por > 1 minuto: dispara Scale-Out (+1 instância).
       * CPU < 25% por > 1 minuto: dispara Scale-In (-1 instância).

---

## 3. Contratos de Dados (Evitando Desalinhamento entre PRs)

Qualquer PR que inventar nomes diferentes deve ser ajustada para seguir estes padrões:

### 3.1. Tabela RDS (`sessions`)
| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `VARCHAR(64)` (PK) | Hash hexadecimal de 32 caracteres da sessão |
| `license_plate`| `VARCHAR(16)` (NULL) | Placa identificada pelo OCR (nula na entrada) |
| `status` | `VARCHAR(20)` | `'PROCESSING'` -> `'PARKED'` -> `'PAID'` |
| `s3_photo_key` | `TEXT` | Caminho no S3: `photos/{session_id}_{filename}` |
| `entered_at` | `TIMESTAMP WITH TZ` | Horário de passagem pela cancela |
| `exited_at` | `TIMESTAMP WITH TZ` (NULL) | Horário de pagamento e liberação da cancela |
| `amount_paid` | `NUMERIC(10,2)` (NULL) | Valor cobrado na saída |

### 3.2. Chaves de Cache no Redis & Auto-Recuperação
* **Chave:** `spots:available`
* **Entrada (Worker após OCR):** `DECR spots:available`
* **Saída (API após pagar):** `INCR spots:available`
* **Consulta (Totem/Web):** `GET spots:available`
* **Regra Anti-Overbooking:** Se `GET spots:available` falhar ou a chave não existir, a API consulta o RDS (`totalSpots - count(sessions ativas)`), responde a contagem real e reidrata o Redis com `SET spots:available`. É terminantemente proibido assumir 50 cegamente em caso de falha de cache.

### 3.3. Tabela DynamoDB (`AuditoriaEstacionamento`)
* **Chave:** `id` (`<session_id>#<timestamp_nano>`)
* **Ações oficiais:**
  * `ENTRY`: Registrado pela API ao receber o carro na cancela.
  * `OCR_PROCESSING`: Registrado pelo Worker Python ao ler a placa.
  * `EXIT_PAYMENT`: Registrado pela API ao receber o pagamento.

### 3.4. Mensageria SQS Direta
* **Fila SQS:** `ocr-processamento-fila`
* **Payload da mensagem (direto no Body do SQS, sem envelope):**
  ```json
  {
    "session_id": "hex_32_caracteres",
    "s3_key": "photos/session_id_foto.jpg"
  }
  ```

---

## 4. Tabela de Penalidades da Disciplina (Checklist Anti-Zero)

| Item da Especificação | Penalidade se Faltar na PR / Entrega |
|---|---|
| **Amazon RDS** | **-1,5 ponto** |
| **Amazon S3** | **-1,5 ponto** |
| **Amazon DynamoDB / DocumentDB** | **-1,5 ponto** |
| **Amazon ElastiCache (Redis)** | **-1,5 ponto** |
| **Amazon SNS / SQS** | **-1,5 ponto** |
| **Amazon Auto Scaling + ALB** | **-1,5 ponto** |
| **Interface Gráfica (Web)** | **-1,0 ponto** |
| **Vídeo da Elasticidade (Parte 2)** | **Item Obrigatório de Entrega** |

---

## 5. Roteiro do Vídeo de Comprovação (Parte 2)

O vídeo é item mandatório de entrega (deadline: **10/10/2026 às 23h59**):
1. **Mostrar Topologia Inicial:** Console da AWS com ALB ativo, ASG com 1 instância saudável e Target Group respondendo em `/health`.
2. **Aplicar Carga de CPU:** Gerar tráfego contra o ALB (ex.: via `k6`, `hey`, `ab`, ou script de stress) até a média de CPU das instâncias ultrapassar **70%**.
3. **Comprovar Scale-Out:**
   * Exibir o alarme do CloudWatch mudando para estado `ALARM` após 1 minuto de CPU > 70%.
   * Exibir o ASG iniciando novas instâncias até atingir 2 e 3 instâncias.
   * Exibir as novas instâncias ficando saudáveis no Target Group do ALB.
4. **Comprovar Scale-In:**
   * Cessar a carga e aguardar a CPU média cair abaixo de **25%** por 1 minuto.
   * Exibir o CloudWatch disparando o alarme de scale-in e o ASG terminando as instâncias extras até voltar para 1.
5. **Gravação:** Vídeo de 3 a 5 minutos, áudio claro, hospedado no YouTube ou Google Drive com permissão pública de visualização.

---

## 6. Checklist de Revisão Rápida para PRs

Antes de aprovar qualquer PR dos colegas de equipe:

- [ ] **1. Contrato Respeitado:** A PR usa `sessions` (com `status` `PROCESSING`/`PARKED`/`PAID`), a chave Redis `spots:available` e a tabela DynamoDB `AuditoriaEstacionamento`?
- [ ] **2. Desacoplamento Preservado:** A API nunca executa OCR diretamente de forma síncrona? O upload dispara evento via SNS/SQS?
- [ ] **3. Auditoria Registrada:** Toda alteração de estado no sistema gera um item correspondente no DynamoDB?
- [ ] **4. Compatibilidade Local:** O comando `task bootstrap:local` continua funcionando perfeitamente com o Floci?
- [ ] **5. Sem Segredos no Código:** Credenciais e endpoints são lidos exclusivamente via variáveis de ambiente?
- [ ] **6. Testes Incluídos:** Foram adicionados testes unitários ou de integração cobrindo a funcionalidade alterada?
