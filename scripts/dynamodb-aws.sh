#!/usr/bin/env bash
# Cria (se faltar) a tabela de auditoria no DynamoDB real e mostra quantos itens ela tem.
# Usa as credenciais de infra/.env (modelo: infra/.env.aws.example) e o aws-cli em container.
#
# Uso: scripts/dynamodb-aws.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="infra/.env"
[ -f "$ENV_FILE" ] || { echo "Falta $ENV_FILE (copie de infra/.env.aws.example)"; exit 1; }

set -a; . "$ENV_FILE"; set +a
: "${DYNAMODB_ACCESS_KEY_ID:?preencha DYNAMODB_ACCESS_KEY_ID em $ENV_FILE}"
: "${DYNAMODB_SECRET_ACCESS_KEY:?preencha DYNAMODB_SECRET_ACCESS_KEY em $ENV_FILE}"
: "${DYNAMODB_SESSION_TOKEN:?preencha DYNAMODB_SESSION_TOKEN em $ENV_FILE}"

TABLE="${DYNAMODB_TABLE_NAME:-AuditoriaEstacionamento}"
REGION="${AWS_REGION:-us-east-1}"

aws() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID="$DYNAMODB_ACCESS_KEY_ID" \
    -e AWS_SECRET_ACCESS_KEY="$DYNAMODB_SECRET_ACCESS_KEY" \
    -e AWS_SESSION_TOKEN="$DYNAMODB_SESSION_TOKEN" \
    -e AWS_DEFAULT_REGION="$REGION" \
    amazon/aws-cli "$@"
}

if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  echo "Tabela $TABLE já existe em $REGION."
else
  echo "Criando a tabela $TABLE em $REGION..."
  aws dynamodb create-table --table-name "$TABLE" \
    --attribute-definitions AttributeName=id,AttributeType=S \
    --key-schema AttributeName=id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE"
  echo "Tabela criada."
fi
echo "Itens na tabela: $(aws dynamodb scan --table-name "$TABLE" --select COUNT --query Count --output text)"
