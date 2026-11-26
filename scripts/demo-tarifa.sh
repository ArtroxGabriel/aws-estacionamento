#!/usr/bin/env bash
# Prepara a demonstração da tarifa por tempo: registra 6 veículos pela API e volta
# o horário de entrada de cada um (no Postgres do compose), como se tivessem chegado
# há 5 min, 14 min, 16 min, 59 min, 1h10 e 3h30. Depois é só pagar no caixa
# (http://localhost:3000/pagamento) e comparar o "Valor a pagar" de cada linha.
#
# A cobrança continua sendo calculada pela API; o script só altera `entered_at`.
# Valores esperados com a tarifa padrão (tolerância 15 min, 1ª hora R$ 10, +R$ 5 por hora ou fração).
#
# Uso: scripts/demo-tarifa.sh [base_url]     (padrão: http://localhost:3000)
# Variáveis: POSTGRES_CONTAINER (padrão estacionamento_postgres), OCR_WAIT (padrão 10, em s)
set -euo pipefail

BASE="${1:-http://localhost:3000}"
API="${BASE%/}/api"
PG="${POSTGRES_CONTAINER:-estacionamento_postgres}"
OCR_WAIT="${OCR_WAIT:-10}"
cd "$(dirname "$0")/.."

# minutos atrás | valor esperado | foto
CASES=(
  "5|R\$ 0,00 (tolerância)|examples/fotos/placa-real-mercosul-lsn4i49.jpg"
  "14|R\$ 0,00 (tolerância)|examples/fotos/placa-real-antiga-klv8465.jpg"
  "16|R\$ 10,00|examples/fotos/sintetica-bra2e19.jpg"
  "59|R\$ 10,00|examples/fotos/carro-argentino-estacionamento.jpg"
  "70|R\$ 15,00|examples/fotos/carro-argentino-rua.jpg"
  "210|R\$ 25,00|examples/fotos/carro-placa-coberta.jpg"
)

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

curl -fsS "$API/health" >/dev/null || { echo "API fora do ar em $API (rode: task up)"; exit 1; }
docker exec "$PG" pg_isready -q || { echo "Postgres '$PG' não encontrado (veja POSTGRES_CONTAINER)"; exit 1; }

echo "Registrando ${#CASES[@]} veículos..."
IDS=()
for c in "${CASES[@]}"; do
  IFS='|' read -r minutes _ photo <<<"$c"
  id=$(curl -fsS -F "photo=@${photo}" "$API/entries" | json 'd["id"]')
  docker exec "$PG" psql -U app_user -d estacionamento -q \
    -c "UPDATE sessions SET entered_at = now() - interval '${minutes} minutes' WHERE id = '${id}'"
  IDS+=("$id")
done

echo "Esperando o OCR ler as placas (${OCR_WAIT}s)..."
sleep "$OCR_WAIT"

PARKED=$(curl -fsS "$API/sessions?status=PARKED")
printf '\n%-10s %-9s %-12s %s\n' "TICKET" "PLACA" "PERMANÊNCIA" "VALOR ESPERADO"
for i in "${!CASES[@]}"; do
  IFS='|' read -r minutes expected _ <<<"${CASES[$i]}"
  id="${IDS[$i]}"
  plate=$(echo "$PARKED" | json "next((s.get('license_plate','-') for s in d if s['id']=='$id'), '?')")
  printf '%-10s %-9s %-12s %s\n' "${id:0:8}" "$plate" "$((minutes / 60))h $(printf '%02d' $((minutes % 60)))min" "$expected"
done

echo
echo "Abra ${BASE%/}/pagamento e confira o 'Valor a pagar' de cada linha."
echo "Para comparar com a API: curl -s '$API/sessions?status=PARKED' | python3 -m json.tool | grep -E 'id|amount_due'"
