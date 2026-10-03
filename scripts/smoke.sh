#!/usr/bin/env bash
# Smoke test ponta a ponta contra o frontend/nginx (ALB na AWS ou local):
# health → vagas → entrada com foto → espera o OCR (PARKED) → pagamento → auditoria.
# Uso: scripts/smoke.sh <base_url> <foto_do_carro.jpg>
set -euo pipefail

BASE="${1:?uso: scripts/smoke.sh <base_url> <foto.jpg>}"
PHOTO="${2:?uso: scripts/smoke.sh <base_url> <foto.jpg>}"
API="${BASE%/}/api"
OCR_TIMEOUT="${OCR_TIMEOUT:-90}"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "1. Health"
curl -fsS "$API/health"; echo

echo "2. Vagas disponíveis"
curl -fsS "$API/spots/available"; echo

echo "3. Entrada com foto"
ENTRY=$(curl -fsS -F "photo=@${PHOTO}" "$API/entries")
echo "$ENTRY"
ID=$(echo "$ENTRY" | json 'd["id"]')

echo "4. Esperando o OCR (até ${OCR_TIMEOUT}s)"
SESSION=""
for _ in $(seq 1 $((OCR_TIMEOUT / 3))); do
  SESSION=$(curl -fsS "$API/sessions?status=PARKED" \
    | json "next((s for s in (d or []) if s['id']=='$ID'), '')")
  [ -n "$SESSION" ] && break
  sleep 3
done
if [ -z "$SESSION" ]; then
  echo "FALHA: sessão $ID não chegou a PARKED (veja a auditoria e os logs do worker)"
  curl -fsS "$API/audit" | head -c 2000; echo
  exit 1
fi
echo "$SESSION"

echo "5. Pagamento"
curl -fsS -X POST "$API/exits/$ID/pay"; echo

echo "6. Auditoria da sessão"
curl -fsS "$API/audit" | json "[(e['action'], e['timestamp']) for e in (d or []) if e.get('entity_id')=='$ID']"

echo "OK: fluxo completo funcionando em $BASE"
