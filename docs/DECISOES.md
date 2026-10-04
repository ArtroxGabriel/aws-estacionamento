# Decisões de arquitetura

Registro do que mudou no projeto, por quê, e o que cada decisão implica. Cada item segue a mesma ordem: contexto → decisão → por quê → consequências → onde está no código.

> Branch `feat/aws-deploy`, 2026-10-03. Visão geral em [README](../README.md); estado atual em [CHECKLIST-AWS.md](CHECKLIST-AWS.md).

## Serviços AWS usados

A especificação exige 6 serviços. Os demais entraram para resolver problemas concretos, cada um explicado abaixo.

| Serviço | Obrigatório? | Para quê | Decisão |
|---|---|---|---|
| EC2 + ALB + Auto Scaling | ✅ Sim | Rodar a aplicação, com elasticidade de 1 a 3 instâncias | — |
| S3 | ✅ Sim | Fotos dos veículos | — |
| RDS (PostgreSQL) | ✅ Sim | Sessões | — |
| ElastiCache (Redis) | ✅ Sim | Contador de vagas | — |
| DynamoDB | ✅ Sim | Auditoria | — |
| SQS (+ DLQ) | ✅ Sim | Desacoplar entrada e OCR | — |
| **Rekognition** | Não | Leitura da placa (OCR) na AWS | [D1](#d1-amazon-rekognition-para-ler-a-placa), [D10](#d10-política-de-leitura-moldura-e-reserva-segura) |
| **CloudFront** | Não | HTTPS sem domínio próprio | [D2](#d2-cloudfront-para-ter-https) |
| ECR | Não | Guardar as imagens Docker | [D3](#d3-deploy-por-imagens-no-ecr-e-github-actions-com-oidc) |
| CloudWatch | Não (vem com o Auto Scaling) | Alarmes de CPU que disparam o scaling | [D7](#d7-alarmes-próprios-de-1-minuto-simple-scaling) |
| IAM | Não | Permissões das instâncias e do deploy | [D3](#d3-deploy-por-imagens-no-ecr-e-github-actions-com-oidc) |

---

## D1. Amazon Rekognition para ler a placa

**Contexto:** O worker lia a placa com Tesseract (OCR de código aberto) + OpenCV. Em 5 fotos de exemplo funcionava. Para testar de verdade, montamos um dataset de 114 fotos **reais** de carros em estacionamentos de Salvador ([examples/README.md](../examples/README.md)). Nele, o Tesseract leu só **52%** das placas. Na metade das vezes não encontrava a placa na foto, e em 10% leu a placa errada.

**Decisão:** Na AWS, o worker usa o **Amazon Rekognition** (`DetectText`) como motor principal, com o Tesseract como reserva.
- Entre os textos que o Rekognition encontra, vence a **placa mais alta na foto**, que é a do carro em primeiro plano, não a dos carros ao fundo.
- Se o Rekognition der erro ou não achar placa, o worker roda o Tesseract.
- No emulador local (Floci), que não tem Rekognition, continua só o Tesseract.
- Liga com `OCR_ENGINE=rekognition`.

**Por quê:**
- **Precisão medida, não estimada.** Nas 114 fotos reais: Tesseract 52% → Rekognition **96%** (110/114). Nas 400 sintéticas dos 4 países: 73% → **98%**.
  - Para não "decorar" o conjunto, a regra da placa mais alta foi ajustada olhando só metade das fotos reais. Na outra metade, que nunca foi usada para ajustar, também deu 96%.
  - Conferido pelo sistema no ar (foto → API → SQS → worker): 110/114.
- **Custo:** US$ 0,001 por foto, com 5 mil fotos por mês grátis no primeiro ano. Um estacionamento com 1.000 entradas por dia sairia por ~US$ 30/mês.
- **Requisito de "manipular o arquivo" continua atendido:** o worker redimensiona a foto (máx. 1920 px, JPEG) antes de enviar ao Rekognition. Na reserva com Tesseract, aplica escala, tons de cinza e binarização.
- **Alternativas descartadas:**
  - Melhorar o Tesseract: tentamos (recorte mais largo da placa, outro localizador), mas o ganho foi de 1–3 pontos.
  - Bedrock (modelo de linguagem com visão): mais caro por foto, mais lento e pode "inventar" caracteres. O Rekognition é o serviço feito para ler texto em imagem.

**Consequências:**
- O worker depende da AWS para a melhor precisão. Sem o Rekognition (Floci, ou permissão negada), ele continua funcionando com o Tesseract, só que com precisão menor.
- A role das instâncias precisa de `rekognition:DetectText`. Na AWS Academy, a `LabRole` precisa permitir o Rekognition; se não permitir, o worker cai sozinho no Tesseract.
- Mais um serviço para explicar no vídeo, como ponto positivo.

**Onde:** `worker/ocr/rekognition.py`, `worker/config.py` (`OCR_ENGINE`), `infra/autoscaling.tf` (permissão), `infra/templates/user_data.sh.tftpl`.

---

## D2. CloudFront para ter HTTPS

**Contexto:** O Load Balancer só atendia `http://`. Os navegadores atuais tentam `https://` primeiro, e o site dava *timeout* para quem o abria. Para o ALB ter HTTPS é preciso um certificado, e a AWS só emite certificado para um **domínio próprio**. O endereço `...elb.amazonaws.com` é da Amazon e não pode ter certificado.

**Decisão:** Colocar o **CloudFront** (a CDN da AWS) na frente do ALB, sem cache. Ele entrega `https://<id>.cloudfront.net` com o certificado da AWS e repassa tudo para o ALB.

**Por quê:** HTTPS sem comprar domínio. Entra no nível gratuito (1 TB e 10 milhões de requisições por mês) e leva 3 minutos para criar.

**Consequências:**
- Há dois endereços: `app_url` (HTTPS, para acessar) e `alb_url` (HTTP, usado no teste de carga do vídeo, que vai direto ao ALB e não gasta requisições do CloudFront).
- Sem cache, a cada deploy todos já recebem a versão nova.

**Onde:** `infra/cdn.tf`.

---

## D3. Deploy por imagens no ECR e GitHub Actions com OIDC

**Contexto:** Cada instância nova do Auto Scaling precisa rodar a aplicação. Compilar Go, Node e Python dentro de uma `t3.micro` levaria vários minutos por instância e atrasaria o scale-out do vídeo. O deploy também precisava ser repetível pelo time, sem chaves AWS espalhadas.

**Decisão:**
- **Imagens prontas no ECR:** a instância só faz `docker pull` e sobe em ~2 min. Cada deploy usa a tag do commit (SHA). Isso muda o launch template e faz o ASG trocar as instâncias aos poucos, sem queda.
- **GitHub Actions com OIDC** (workflow *Deploy AWS*: apply/plan/destroy). O GitHub prova sua identidade para a AWS a cada execução, sem chave de acesso salva no repositório. A role de deploy só pode ser assumida pelo environment `aws` deste repositório e não consegue alterar as próprias permissões (conferido no simulador do IAM).
- **State do OpenTofu no S3** (`infra/aws`), compartilhado entre as tasks locais e o Actions. O Floci continua com state local em `infra/`.

**Por quê:**
- Scale-out rápido.
- Deploy reproduzível.
- Segurança: nenhuma credencial de longa duração no GitHub.
- Um único "estado da verdade" da infraestrutura.

**Consequências:**
- O bootstrap (`task aws:bootstrap`: bucket do state, OIDC e role) roda uma vez por conta, por alguém com acesso de admin.
- Na AWS Academy não dá para criar IAM. Lá, usa-se só o deploy local com `LabInstanceProfile`.

**Onde:** `infra/bootstrap/`, `infra/aws/`, `.github/workflows/deploy-aws.yml`, `.github/actions/tofu-aws/`.

---

## D4. Placa ilegível vira falha na hora; caixa digita a placa; exclusão

**Contexto:**
- Quando o OCR não lia a placa, o worker devolvia a mensagem à fila e tentava de novo, 3 vezes com 5 min de intervalo. Como o OCR dá sempre o mesmo resultado para a mesma foto, isso só atrasava ~15 min o aviso ao operador.
- Um carro sem placa lida não tinha como receber a placa.
- O CRUD exigido pela especificação não tinha o "D" (excluir).

**Decisão:**
- **Placa ilegível:** a sessão vai para `FAILED` na primeira tentativa, com auditoria `OCR_FAILED`. Erros que podem passar sozinhos (S3 fora do ar, timeout) continuam com novas tentativas.
- **`PATCH /sessions/{id}`:** o caixa digita a placa (sessão `FAILED` → `PARKED`, descontando a vaga) ou corrige uma leitura errada. Auditoria `PLATE_CORRECTION`.
- **`DELETE /sessions/{id}`:** exclui a sessão e a foto do S3 e devolve a vaga se o carro estava estacionado. Auditoria `SESSION_DELETE`.

**Por quê:** É o fluxo de um estacionamento real (a leitura falha e o operador resolve). Completa o CRUD e deixa tudo registrado na auditoria.

**Consequências:** Enquanto uma sessão está `FAILED`, ela não ocupa vaga no contador. O painel mostra uma vaga livre a mais até o caixa digitar a placa.

**Onde:** `worker/worker.py`, `api/internal/service/parking.go`, `api/internal/handler/handler.go`, `web/src/pages/PaymentPage.tsx`.

---

## D5. Ajustes de rede nas instâncias (conntrack e keepalive)

**Contexto:** No teste de carga, a ~150 requisições/s, 75% das respostas eram erro 502/503. O ASG trocava a instância achando que ela estava quebrada. A causa era a tabela de conexões do kernel da `t3.micro`, limitada a 7.680 entradas. Ela enchia porque cada requisição abria uma conexão nova entre o nginx e a API.

**Decisão:**
- O nginx reaproveita conexões com a API (keepalive).
- O `user_data` aumenta a tabela para 131.072 entradas e encurta o tempo que conexões fechadas ficam nela.

**Por quê:** Sem isso, o vídeo de elasticidade mostraria instâncias caindo em vez de escalando.

**Consequências:** 100% de respostas OK a ~4.200 requisições/s; a CPU passa de 70% e o scaling dispara como esperado.

**Onde:** `web/nginx.conf.template`, `web/nginx-api-upstream.envsh`, `infra/templates/user_data.sh.tftpl`.

---

## D6. Rede e custos

**Decisão:**
- VPC default, sem NAT Gateway.
- Só 2 zonas de disponibilidade.
- RDS sem backup automático e com disco gp3.
- O ECR apaga imagens antigas.
- Tags `Project=aws-estacionamento` em tudo.
- Instâncias `t3.micro`.

**Por quê:**
- O NAT Gateway custaria ~US$ 32/mês.
- Cada IPv4 público custa US$ 0,005/h, e menos zonas significam menos IPs.
- A infra é efêmera: sobe para testar e gravar e depois é destruída.

**Consequências:**
- As instâncias têm IP público, mas só aceitam tráfego vindo do ALB (security groups).
- RDS e Redis só aceitam conexão das instâncias.
- Com tudo no ar, o custo é de ~US$ 0,10/h.

**Onde:** `infra/network.tf`, `infra/main.tf`, `infra/provider.tf`.

---

## D7. Alarmes próprios de 1 minuto (Simple Scaling)

**Contexto:** A especificação pede: CPU acima de 70% por mais de 1 minuto → +1 instância; abaixo de 25% por 1 minuto → −1.

**Decisão:**
- Dois alarmes do CloudWatch (média da CPU do grupo, período de 60 s, 1 medição) ligados a políticas *Simple Scaling* de +1/−1, com 60 s de espera.
- *Detailed monitoring* nas instâncias.

**Por quê:**
- A política automática da AWS (*target tracking*) cria alarmes que esperam 3 minutos para subir e 15 para descer, o que não atende a especificação.
- Sem detailed monitoring, a CPU só é medida de 5 em 5 minutos.

**Onde:** `infra/autoscaling.tf`.

---

## D8. Placas dos 4 países do Mercosul

**Contexto:** A especificação do professor **não fala em placas**. A regra inicial do time aceitava só os formatos **brasileiros**. Ela está no `Requirement 5` de `.kiro/specs/python-ocr-worker/requirements.md` (29/09) e no contrato do worker em `docs/GOAL.md`. Um carro argentino ou paraguaio virava "falha no OCR" e não podia nem ter a placa digitada no caixa. Como o padrão Mercosul é regional e estacionamentos do Sul e de fronteira recebem carros desses países, o time decidiu em 03/10 aceitar os 4 países.

**Decisão:**

| País | Formato | Exemplo | Quando a leitura automática aceita |
|---|---|---|---|
| Brasil (Mercosul) | LLL N L NN | `ABC1D23` | Sempre |
| Brasil (antiga) / Uruguai (Mercosul) | LLL NNNN | `ABC1234` | Sempre. Com faixa azul brasileira detectada, só se aparecer `URUGUAY` |
| Argentina (Mercosul) | LL NNN LL | `AB123CD` | Sempre: nenhuma placa brasileira começa com 2 letras e 3 dígitos |
| Argentina (antiga, preta) | LLL NNN | `ABC123` | Só com `ARGENTINA` lido na placa, e só como linha inteira |
| Paraguai (Mercosul) | LLLL NNN | `ABCD123` | Só com `PARAGUAY` lido na placa |

O caixa aceita os mesmos formatos na placa digitada.

**Por quê das condições:** os carros brasileiros são a maioria, e as condições evitam que leituras brasileiras com erro virem placas estrangeiras.
- O OCR às vezes perde o último caractere: `PJC4903` vira `PJC490`, que tem o formato argentino antigo.
- O `1` às vezes é lido como `I`: `ABC1234` vira `ABCI234`, que tem o formato paraguaio.
- As placas desses países trazem o nome do país impresso, então exigir o nome é seguro.
- As correções de caracteres confundidos (`1`→`I`, `0`→`O`) continuam só para o Brasil, porque foram calibradas na fonte da placa brasileira. Placas estrangeiras só são aceitas com leitura exata.

**Consequências:**
- Mudaram o normalizador do worker, a validação da API e a do caixa.
- O caminho do Rekognition repassa as linhas com o nome do país como contexto para cada candidata a placa.
- O dataset sintético agora tem os 4 países. Resultados em [examples/README.md](../examples/README.md).
- Para o teste de "placa ilegível", as fotos de carros argentinos deram lugar a `examples/fotos/carro-placa-coberta.jpg` (placa borrada).

**Onde:** `worker/ocr/clean.py`, `worker/ocr/rekognition.py`, `api/internal/service/parking.go`, `web/src/utils/format.ts`, `scripts/gerar_sinteticas.py`.

---

## D10. Política de leitura: moldura e reserva segura

**Contexto:** Com 1.000 recortes reais de placas do Roboflow, apareceram dois problemas.
1. **Close da placa:** quando o texto ocupa a imagem inteira, o Rekognition muitas vezes não lê nada. Em 100 recortes: 13 lidos com a proporção certa, **83** com a placa centralizada numa moldura.
2. **Reserva com Tesseract criando placas falsas:** onde o Rekognition não achava placa, o Tesseract lia ruído, e a correção de caracteres transformava esse ruído numa placa válida, porém errada (`THH8H18` → `PII7A11`). Nas 1.520 fotos de teste, a reserva recuperava 7 placas e criava 14 leituras erradas.

**Decisão:**
1. Se o Rekognition não achar placa, faz **uma 2ª chamada com a foto centralizada numa moldura** (o dobro do tamanho).
2. A reserva com Tesseract só aceita **leitura exata**, ou 1 correção quando a faixa Mercosul foi detectada. Esse é o caso típico `I`/`1` da fonte Mercosul, como a `LSN4I49`, cujo holograma também engana o Rekognition.

**Por quê** (medido com as respostas guardadas, simulando cada alternativa nas 1.520 fotos):

| Política | Acertos | Lidas erradas |
|---|---|---|
| Só Rekognition | 921 | 120 |
| + reserva Tesseract com correções (anterior) | 928 | 134 |
| + reserva só exata / 1 correção com faixa | 928 | 125 |
| **+ 2ª chamada com moldura (atual)** | **1.268** | **214** |

- **Limite de confiança do Rekognition, testado e descartado:** com 70%, os acertos nas fotos reais caem de 96% para 80%, e os erros quase não diminuem.
- **Evidência `BRASIL` da mesma placa, testada e descartada:** ficou igual ou pior.

**Consequências:**
- A moldura recupera placas em closes. Nas fotos de câmera ela quase nunca é acionada (2 de 114, ganhando 1 placa).
- Ela também traz leituras erradas: no Roboflow, ~1 errada para cada 4 recuperadas. **77% dessas erram 1 caractere** (`V`/`Y`, `W`/`N`), e o operador corrige com "Corrigir placa" no caixa. O pagamento é pelo ticket, então uma placa errada não cobra o carro de outra pessoa.
- Uma 2ª chamada custa mais US$ 0,001, só nas fotos em que a primeira falhou.
- Se o Rekognition respondeu e nenhuma placa segura foi encontrada, o resultado é **placa ilegível**: sessão `FAILED` na hora (D4), não "erro de OCR", que o worker tentaria de novo por ~15 min. Um bug nisso foi pego pelo `task smoke:aws` na AWS e corrigido, com teste.

**Onde:** `worker/ocr/rekognition.py` (`framed`, `_detect`, `_exact_fallback`).

---

## D9. Dataset de teste fora do git

**Contexto:** Para medir a leitura com fotos reais, usamos dois datasets públicos:
- as 114 fotos brasileiras do benchmark do OpenALPR (AGPL-3.0);
- recortes de placas do projeto `cafuringa/placas` do Roboflow Universe (CC BY 4.0).

**Decisão:**
- As fotos não são versionadas. `task dataset:baixar` e `task dataset:roboflow` as baixam para `examples/dataset/` (ignorado pelo git) e geram o gabarito. Cada pasta traz um `ORIGEM.md` com a fonte e a licença.
- A chave do Roboflow vem da variável de ambiente `ROBOFLOW_API_KEY`, nunca de arquivo do repositório.

**Por quê:**
- Copiar material AGPL para o repositório poderia impor essa licença ao projeto.
- São dezenas de MB.
- O export do Roboflow veio com os nomes das classes corrompidos. O mapeamento classe → caractere foi deduzido comparando as caixas com as fotos e conferido em amostras (`KNOWN_MAPS` em `scripts/baixar_roboflow.py`).

**Onde:** `scripts/baixar_dataset.py`, `scripts/gerar_sinteticas.py`, `examples/README.md`.
