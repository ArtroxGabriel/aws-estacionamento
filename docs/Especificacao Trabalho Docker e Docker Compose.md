Especificação do Trabalho Prático 2 Desenvolvimento de Software para Nuvem Professores: Dr. Paulo A. L. Rego e Dr. Fernando Antonio Mota Trinta

**Objetivo:** Fazer a implantação da aplicação desenvolvida no Trabalho 1 utilizando Docker e Docker Compose.

## Imposições sobre o trabalho:

1. A aplicação deve:
a. Continuar usando o serviço de banco de dados não-relacional da AWS (o DynamoDB ou Amazon DocumentDB) para **armazenar e consultar** as informações necessárias para a sua aplicação;
b. Usar um serviço de banco de dados relacional gerenciado por você (Postgres ou MySQL) para persistir os dados antes armazenados no Amazon RDS;
c. Usar o serviço <u>MinIO</u> para armazenar os arquivos antes armazenados no S3;
d. Usar o RabbitMQ ou Kafka para substituir o SNS/SQS.
2. Usar Dockerfile para criar a imagem do backend da aplicação;
3. Usar Dockerfile para criar a imagem do frontend da aplicação;
4. Usar imagem do Postgres ou MySQL para executar o banco de dados relacional;
5. Usar imagem do MinIO para executar o serviço de armazenamento de arquivos;
6. Usaar a imagem do RabbitMQ ou Kafka para executar o serviço de fila de mensagens;
7. Usar Docker Compose para configurar a execução e iniciar os serviços de backend, frontend, banco de dados relacional, serviço de fila de mensagens e armazenamento de arquivos.
## Algumas ponderações:

1) Subir código no Github (ou outro repositório) e preparar README com os passos necessários para baixar o código, gerar as imagens e iniciar os serviços.
2) Opcionalmente: pode-se iniciar os serviços em uma máquina virtual da AWS para possibilitar a correção online.
## Pontuação

- Usar o MinIO para escrita e leitura de arquivos (1,5 pontos: 0,5 container executando e 1,0 upload/download de arquivos funcionando na aplicação);
- Usar o banco de dados NoSQL para armazenamento e consulta (1 ponto);
- Usar o banco de dados relacional gerenciado pela equipe (1,5 pontos: 0,5 para container do Postgres/MySQL executando e 1,0 para a aplicação se conectar e persistir dados);

- Usar o Rabbit ou Kafka para fila de mensagens (1,5 pontos: 0,5 container executando corretamente e 1,0 produção e consumo de mensagens funcionando).
- Usar Dockerfile corretamente para criar as imagens do backend, worker e frontend da aplicação (1,5 pontos);
- Usar Docker Compose corretamente para configurar a execução de todos os serviços (1,5 pontos: 0,75 todos os serviços sobem via um único comando e 0,75 redes e dependências configuradas corretamente);
- Documentação de uso no README do repositório (1,5 pontos: 1,0 instruções claras para executar o docker compose up e 0,5 para prints ou evidências de funcionamento).
- Atraso na entrega (-1 por dia).
- Qualidade da apresentação (slides, figuras, diagramas) e clareza na apresentação e resposta às perguntas (0 a -3).
## Pontuação extra (1 pontos):

- Subir o mesmo serviço no Kubernetes, utilizando Deployment e Services (NodePort e ClusterIP).
- Configurar variáveis de ambiente e usar credenciais/secrets no K8s de forma segura.
