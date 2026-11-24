# Kubernetes (bônus do Trabalho 2)

O mesmo sistema do `infra/docker-compose.yaml`, em Deployments e Services. Testado em um k3s local.

| Arquivo | O que cria |
|---|---|
| `00-namespace.yaml` | Namespace `estacionamento` |
| `01-configmap.yaml` | `app-config` (endpoints, bucket, tabela, tarifa), `rabbitmq-config` e o script `wait-for-deps` |
| `02-secrets.yaml` | Credenciais do Postgres, do RustFS e do RabbitMQ, e as definições das filas |
| `postgres.yaml`, `rabbitmq.yaml`, `rustfs.yaml`, `dynamodb-local.yaml` | Deployment + PVC + Service `ClusterIP` |
| `redis.yaml` | Deployment + Service `ClusterIP` (sem volume: a API recalcula o contador) |
| `init-jobs.yaml` | Jobs `create-bucket` e `create-table` (o `createbuckets` e o `createtables` do compose) |
| `api.yaml`, `worker.yaml` | Deployments; a API também tem Service `ClusterIP` |
| `web.yaml` | Deployment + Service `NodePort` 30080 |

Também são `NodePort`: o painel do RabbitMQ (`rabbitmq-management`, 31672) e o console do RustFS (`rustfs-console`, 31901).

## Como o compose virou manifesto

| Compose | Kubernetes |
|---|---|
| `depends_on` com `service_healthy` | `readinessProbe` em cada dependência e o initContainer `wait-for-deps` na API e no worker |
| `service_completed_successfully` | Jobs `create-bucket` e `create-table`; o initContainer espera o bucket e a tabela existirem |
| Rede `estacionamento_network` | DNS do namespace: os Services se chamam `postgres`, `redis`, `rabbitmq`, `rustfs`, `dynamodb-local` e `api` |
| Variáveis no `environment` | `app-config` (sem segredo) e `secretKeyRef` (senhas) |
| Volumes nomeados | PersistentVolumeClaims (o k3s cria com o `local-path`) |
| `stop_grace_period: 30s` | `terminationGracePeriodSeconds: 30` no worker |

## Credenciais

Nenhuma senha fica em ConfigMap. Os pods leem os Secrets por `secretKeyRef` e montam a `DATABASE_URL` e a `RABBITMQ_URL` com `$(VAR)`, então cada senha existe em um lugar só. API e worker rodam sem root (`runAsNonRoot`, UID 10001), sem privilégios extras e sem capabilities; a API ainda tem o sistema de arquivos só para leitura.

`02-secrets.yaml` traz as credenciais de desenvolvimento do compose, para o `kubectl apply -f k8s/` funcionar de primeira. Para valores próprios, crie os Secrets antes, sem passar pelo git (e tire os Secrets de `02-secrets.yaml`):

```bash
kubectl -n estacionamento create secret generic postgres-credentials \
  --from-literal=POSTGRES_USER=app_user \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=POSTGRES_DB=estacionamento
```

O usuário e a senha do RabbitMQ também estão em `rabbitmq-definitions` (o broker lê o usuário desse arquivo). Mantenha os dois iguais, e igual ao `infra/rabbitmq/definitions.json` se mudar as filas.

## Rodar no k3s (macOS, com Colima)

Pré-requisitos: [Colima](https://github.com/abiosoft/colima), Docker CLI, `kubectl` e [Task](https://taskfile.dev).

```bash
task k8s:k3s:up    # cluster k3s (perfil "k3s" do Colima, 4 CPUs e 6 GiB)
task k8s:build     # imagens api, worker e web, direto no Docker do cluster
task k8s:apply     # aplica k8s/ e espera os 8 Deployments
task k8s:status
task k8s:forward   # web em http://localhost:3000, RabbitMQ em :15672, RustFS em :9001
```

As imagens usam `imagePullPolicy: IfNotPresent` e o k3s do Colima roda sobre o Docker, então não precisa de registry. Em um k3s instalado direto numa máquina Linux, importe as imagens com `docker save estacionamento-api | sudo k3s ctr images import -` (e o mesmo para `worker` e `web`).

Sem o `task k8s:forward`, o `NodePort` responde no IP do nó: `http://<ip-do-nó>:30080` (o k3s do Colima precisa de `--network-address`; o port-forward evita isso).

Para encerrar: `task k8s:delete` (apaga o namespace e os volumes) e `task k8s:k3s:down`.

## Conferir

```bash
kubectl -n estacionamento get pods,svc,jobs,pvc
curl -s http://localhost:3000/api/health          # {"status":"UP"}
curl -s http://localhost:3000/api/spots/available
kubectl -n estacionamento logs deploy/worker
```

O fluxo é o da seção 4.2 do [plano do TP2](../docs/PLANO-TP2.md): entrada com foto em `/cancela-entrada`, a placa lida pelo worker, o pagamento em `/pagamento` e a saída em `/cancela-saida`.

## DynamoDB real (AWS Academy)

O padrão é o DynamoDB Local. Para a auditoria ir para a AWS, deixe `DYNAMODB_ENDPOINT_URL` vazio no `app-config` e crie o Secret opcional (a API e o worker o leem quando existe):

```bash
kubectl -n estacionamento create secret generic dynamodb-aws \
  --from-literal=DYNAMODB_ACCESS_KEY_ID=... \
  --from-literal=DYNAMODB_SECRET_ACCESS_KEY=... \
  --from-literal=DYNAMODB_SESSION_TOKEN=...
kubectl -n estacionamento rollout restart deployment/api deployment/worker
```

A tabela é criada por `scripts/dynamodb-aws.sh`; o Job `create-table` só cria no DynamoDB Local, então apague-o nesse modo.
