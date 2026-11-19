# Roteiro da apresentação (TP2)

Tudo roda local. Só a tabela de auditoria fica no DynamoDB da AWS Academy.

## Antes (cerca de 15 min antes)

1. **AWS Academy:** *Start Lab*, esperar a bolinha verde, *AWS Details > AWS CLI > Show*.
2. **Credenciais:** `cp infra/.env.aws.example infra/.env` e colar as 3 credenciais `DYNAMODB_*` (deixar `DYNAMODB_ENDPOINT_URL` vazio).
3. **Tabela:** `scripts/dynamodb-aws.sh` (deve imprimir "Itens na tabela: N").
4. **Começar limpo:** `cd infra && docker compose down -v && cd ..`
5. **Subir:** `task up`. Conferir a linha `>> Auditoria: DynamoDB real`.
6. **Esperar** uns 20 s e abrir http://localhost:3000.
7. **Demo da tarifa:** `scripts/demo-tarifa.sh` (6 veículos com permanências de 5 min a 3h30).

Sem credenciais, o `task up` usa o DynamoDB Local sozinho.

## Durante

| # | O que mostrar | Onde |
|---|---|---|
| 1 | Containers no ar | `docker compose -f infra/docker-compose.yaml ps` |
| 2 | Entrada com foto (ticket) | http://localhost:3000/cancela-entrada |
| 3 | Fila recebendo e consumindo; placa lida pelo worker | http://localhost:15672 (`guest`/`guest`) e `/` |
| 4 | Foto no armazenamento (RustFS) | http://localhost:9001 (`rustfsadmin`/`rustfsadmin`), bucket `estacionamento-fotos-veiculos-local` |
| 5 | Valor a pagar por faixa (R$ 0, 10, 15, 25) | http://localhost:3000/pagamento |
| 6 | Pagar e ver o recibo | `/pagamento` |
| 7 | Sair pela placa ou pelo ticket; a vaga volta | http://localhost:3000/cancela-saida |
| 8 | Auditoria (eventos) | `/auditoria` e console AWS: DynamoDB > Tables > `AuditoriaEstacionamento` > Explore items |
| 9 | Sessões no Postgres | `docker exec estacionamento_postgres psql -U app_user -d estacionamento -c "select id, status, amount_paid from sessions"` |

Regra da tarifa: até 15 min grátis; 1ª hora R$ 10; R$ 5 por hora (ou fração) a mais.

## Se algo falhar

- **502 na tela:** `docker compose -f infra/docker-compose.yaml restart web`.
- **Auditoria vazia ou com erro:** credenciais do Academy expiraram. Atualizar `infra/.env` e rodar `task up`.
- **Porta 8080 ocupada:** `docker ps` e parar o container que a usa.
- **Voltar ao banco local:** `mv infra/.env infra/.env.aws && task up`.

## Requisitos da especificação

MinIO → RustFS (aceito pelo professor) · Postgres · RabbitMQ · DynamoDB · Dockerfiles (api, worker, web) · Compose com `depends_on` e rede · README.
