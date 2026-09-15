# Especificação do Trabalho Prático 1 Desenvolvimento de Software para Nuvem

Professores: Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta

## Parte 1

Implementar uma aplicação que utilize 6 serviços da AWS a seguir: EC2, S3, RDS, Elasticache e DynamoDB/DocumentDB e SNS/SQS

A aplicação desenvolvida fica a critério da equipe, bem como a linguagem de programação utilizada para sua implementação. Sugere-se que os alunos utilizem projetos de disciplinas passadas que sejam compatíveis com os requisitos solicitados. Em geral, a aplicação deve manipular arquivos e informações, quaisquer que sejam eles. Por exemplo, um cadastro de produtos deve permitir inserir, alterar, excluir e consultar as informações dos produtos. Tradicionalmente, estas operações são conhecidas como CRUD (CREATE, READ, UPDATE e DELETE). Dentre as informações manipuladas pela aplicação, uma obrigatoriamente deve ser um arquivo binário. No exemplo proposto, todo produto possui uma imagem.

Com estas linhas gerais, seguem os requisitos para o trabalho:

1. A aplicação deve possuir uma interface Web (ou disponibilizar uma API REST para consumo de uma aplicação Mobile/Desktop) para uso de suas funcionalidades. Esta aplicação deve ser executada em uma instância na EC2;
2. As informações dos dados da aplicação devem ser gravadas em uma instância de banco de dados relacional, criada pelo serviço Amazon RDS;
3. Os arquivos binários devem ser armazenados utilizando o serviço Amazon S3;
4. O Elasticache (Redis) deve ser utilizado como camada de cache para consultas frequentes.
5. As ações do CRUD da aplicação precisam ser logadas em um banco de dados NoSQL. Pode-se usar o DynamoDB ou Amazon DocumentDB. Toda ação deve indicar o tipo da ação, os dados manipulados e a hora da ação.
6. Os arquivos recebidos devem ser manipulados (rescaling de imagem, tradução se for arquivo texto, algum cálculo se for arquivos csv, etc). O serviço que vai processar os arquivos deve estar desacoplado do Webservice que vai receber a requisição. Deve-se utilizar SNS/SQS para prover o desacoplamento.

### Getting Started

* EC2: <u>[https://aws.amazon.com/pt/ec2/getting-started/](https://aws.amazon.com/pt/ec2/getting-started/)</u>
* S3: <u>[https://aws.amazon.com/pt/s3/getting-started/?nc=sn&loc=5&dn=1](https://aws.amazon.com/pt/s3/getting-started/?nc=sn&loc=5&dn=1)</u>
* RDS: <u>[https://aws.amazon.com/pt/rds/resources/](https://aws.amazon.com/pt/rds/resources/)</u>
* DynamoDB: <u>[https://aws.amazon.com/pt/dynamodb/getting-started/](https://aws.amazon.com/pt/dynamodb/getting-started/)</u>
* DocumentDB: <u>[https://aws.amazon.com/pt/documentdb/getting-started/](https://aws.amazon.com/pt/documentdb/getting-started/)</u>
* ElastiCache: <u>[https://aws.amazon.com/pt/elasticache](https://aws.amazon.com/pt/elasticache)</u>
* SNS: <u>[https://docs.aws.amazon.com/pt_br/sns/latest/dg/welcome.html](https://docs.aws.amazon.com/pt_br/sns/latest/dg/welcome.html)</u>
* SQS: <u>[https://docs.aws.amazon.com/sqs](https://docs.aws.amazon.com/sqs)</u>

## Parte 2

A partir da aplicação desenvolvida na Parte 1, utilizar os Serviços de **LoadBalancing (ALS)** e **Auto** **Scaling Groups** para tornar a aplicação elástica.

## Configuração e Regras para Elasticidade

1. A elasticidade será conseguida pela estratégia horizontal. Inicialmente deve haver apenas uma instância da aplicação, executando em uma instância do tipo *micro ou small*.
2. Um balanceador de carga deve ser colocado à frente da(s) instância(s) da aplicação, de modo a distribuir a carga de trabalho entre as instâncias.
3. Caso a média de uso de CPU dessa instância exceda 70% por mais de um minuto, deverá ser instanciada uma nova instância para dividir a carga de trabalho da aplicação. O número de instâncias pode crescer até no máximo 3 (três).
4. Caso a média de uso da CPU do conjunto de instâncias fique abaixo de 25% por mais de um minuto, uma instância deve ser finalizada.

### Getting Started

* Auto Scalling Group: <u>[https://aws.amazon.com/pt/ec2/autoscaling/getting-started/](https://aws.amazon.com/pt/ec2/autoscaling/getting-started/)</u>

 Vídeos Tutoriais sobre Auto Scaling Group

* <u>[https://www.youtube.com/watch?v=7SfVZqOVcCI](https://www.youtube.com/watch?v=7SfVZqOVcCI)</u>

* <u>[https://www.youtube.com/watch?v=vNic2xziwlY](https://www.youtube.com/watch?v=vNic2xziwlY)</u>

## **Sugestões de aplicações**

Para quem não teve ideias de aplicações, seguem sugestões:

* Repositório de artigos científicos e TCCs. Uma plataforma onde alunos e pesquisadores cadastram trabalhos acadêmicos (título, autores, resumo, palavras-chave) e fazem upload do PDF do artigo, que é armazenado no S3. As metainformações ficam no RDS, e o ElastiCache acelera buscas frequentes (ex.: artigos mais recentes ou mais buscados por área). O processamento assíncrono, disparado via SNS/SQS, extrai o texto do PDF e gera automaticamente uma tradução do resumo para o inglês (ou outro idioma), desacoplado do serviço que recebe o upload. Toda ação de CRUD sobre os artigos é logada no DynamoDB/DocumentDB.
* Sistema de prestação de contas/reembolso de despesas. Uma aplicação para submissão de notas fiscais e planilhas de despesas (viagens, compras de laboratório, eventos). O usuário cadastra a despesa e anexa um comprovante, que pode ser uma imagem (foto do recibo) ou um CSV com múltiplos lançamentos, ambos guardados no S3. O RDS armazena os dados estruturados da despesa (valor, categoria, solicitante, status de aprovação), e o ElastiCache mantém em cache consultas comuns, como o total gasto por centro de custo. O processamento assíncrono via SNS/SQS realiza o cálculo automático dos totais quando o comprovante é um CSV, ou aplica OCR/rescaling quando é uma imagem de recibo. As ações de CRUD (criação, aprovação, edição, exclusão de despesas) são logadas no DynamoDB/DocumentDB com tipo de ação, dados alterados e horário.
* Marketplace de produtos artesanais ou usados (brechó online). Uma aplicação de compra e venda entre usuários, onde cada produto tem descrição, preço, categoria e uma ou mais fotos, armazenadas no S3, enquanto os dados estruturados (produto, vendedor, categoria, status do anúncio) ficam no RDS. O ElastiCache acelera as consultas de vitrine mais acessadas, como "mais vendidos" ou "destaques da semana". Ao publicar um anúncio, o serviço de processamento assíncrono (via SNS/SQS) redimensiona a imagem para gerar thumbnails em diferentes tamanhos e aplica uma marca d'água, sem travar a resposta da aplicação principal. Toda operação de CRUD sobre os anúncios é registrada no DynamoDB/DocumentDB.
* Sistema de aluguel/reserva de bicicletas ou patinetes compartilhados. Uma plataforma onde usuários consultam a disponibilidade de veículos em pontos de retirada e fazem reservas por um intervalo de tempo, exigindo controle de concorrência para evitar que dois usuários reservem o mesmo veículo simultaneamente (semelhante ao caso de ingressos). Cada veículo tem uma foto de estado de conservação, armazenada no S3, e os dados de reserva, usuário e veículo ficam no RDS. O ElastiCache mantém em cache a disponibilidade de veículos por estação, que é consultada com alta frequência. O processamento assíncrono via SNS/SQS trata as imagens enviadas na devolução do veículo (rescaling/compactação para armazenar o registro do estado do veículo). As ações de reserva, devolução e cadastro são logadas no DynamoDB/DocumentDB.
* Plataforma de reserva e emissão de ingressos para eventos. Uma aplicação onde organizadores cadastram eventos (nome, data, local, lote de ingressos disponíveis) e usuários compram ingressos, sendo necessário garantir que dois usuários não comprem o mesmo assento/vaga simultaneamente. Os dados de eventos, ingressos e compras ficam no RDS, enquanto o arquivo binário obrigatório pode ser o banner do evento ou o PDF/QR code do ingresso emitido, armazenados no S3. O ElastiCache mantém em cache consultas de alta frequência, como a quantidade de ingressos restantes por evento, reduzindo a pressão sobre o banco relacional nos momentos de pico de vendas. O processamento assíncrono via SNS/SQS é responsável por gerar o PDF do ingresso com QR code (ou redimensionar a imagem do evento) após a confirmação da compra, sem bloquear a resposta ao usuário. Toda ação de CRUD, é logada no DynamoDB/DocumentDB com tipo de ação, dados manipulados e horário.

## Pontuação

* Não usar Amazon RDS (-1,5 ponto);
* Não usar Amazon S3 (-1,5 ponto);
* Não usar Amazon DynamoDB e Amazon DocumentDB (-1,5 ponto);
* Não usar Elasticache (-1,5 ponto);
* Não usar Amazon Auto Scaling (-1,5);
* Não usar SNS/SQS (-1,5);
* Não implementar interface gráfica (-1,0).

## Entrega

* Deadline: 23h59 de 10 de Outubro de 2026.
* Enviar link para o git ou código (.zip).
* Enviar link de vídeo mostrando a Parte 2 funcionando.
