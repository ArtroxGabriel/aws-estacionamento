#!/usr/bin/env bash
# Smoke test ponta a ponta contra o frontend/nginx (ALB na AWS ou local):
# A) health → vagas → entrada com foto → OCR (PARKED) → pagamento → auditoria.
# B) foto sem placa legível → FAILED na hora → placa digitada (PARKED) → pagamento.
# C) exclusão de uma sessão estacionada → vaga devolvida → auditoria.
# Uso: scripts/smoke.sh <base_url> <foto_do_carro.jpg> [foto_sem_placa.jpg]
set -euo pipefail

BASE="${1:?uso: scripts/smoke.sh <base_url> <foto.jpg>}"
PHOTO="${2:?uso: scripts/smoke.sh <base_url> <foto.jpg>}"
NO_PLATE_PHOTO="${3:-examples/fotos/carro-argentino-rua.jpg}"
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

spots() { curl -fsS "$API/spots/available" | json 'd["available_spots"]'; }
status_of() { curl -fsS "$API/sessions?status=ALL" | json "next((s['status'] for s in (d or []) if s['id']=='$1'), 'GONE')"; }

echo "7. Foto sem placa legível deve virar FAILED (sem novas tentativas)"
ID2=$(curl -fsS -F "photo=@${NO_PLATE_PHOTO}" "$API/entries" | json 'd["id"]')
for _ in $(seq 1 15); do [ "$(status_of "$ID2")" = "FAILED" ] && break; sleep 2; done
[ "$(status_of "$ID2")" = "FAILED" ] || { echo "FALHA: sessão $ID2 não ficou FAILED"; exit 1; }
echo "FAILED em poucos segundos"

echo "8. Operador digita a placa: FAILED -> PARKED e a vaga é descontada"
BEFORE=$(spots)
curl -fsS -X PATCH -H "Content-Type: application/json" -d '{"license_plate":"abc-1d23"}' "$API/sessions/$ID2"; echo
AFTER=$(spots)
[ "$AFTER" -eq $((BEFORE - 1)) ] || { echo "FALHA: vagas $BEFORE -> $AFTER (esperado -1)"; exit 1; }
curl -fsS -X POST "$API/exits/$ID2/pay" >/dev/null && echo "pago; vagas: $(spots)"

echo "9. Exclusão de uma sessão estacionada devolve a vaga"
ID3=$(curl -fsS -F "photo=@${PHOTO}" "$API/entries" | json 'd["id"]')
for _ in $(seq 1 15); do [ "$(status_of "$ID3")" = "PARKED" ] && break; sleep 2; done
BEFORE=$(spots)
curl -fsS -X DELETE "$API/sessions/$ID3" >/dev/null
AFTER=$(spots)
[ "$(status_of "$ID3")" = "GONE" ] || { echo "FALHA: sessão $ID3 ainda existe"; exit 1; }
[ "$AFTER" -eq $((BEFORE + 1)) ] || { echo "FALHA: vagas $BEFORE -> $AFTER (esperado +1)"; exit 1; }
curl -fsS "$API/audit" | json "[e['action'] for e in (d or []) if e.get('entity_id') in ('$ID2', '$ID3')]"

echo "OK: fluxo completo funcionando em $BASE"
