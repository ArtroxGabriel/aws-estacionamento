#!/usr/bin/env bash
# Avalia a leitura de placas do sistema no ar: envia cada foto do gabarito
# como uma entrada real (POST /entries) e compara a placa que o worker gravou
# na auditoria com a esperada. Placa esperada vazia = o sistema não deve
# inventar placa (ex.: carro estrangeiro).
# Uso: scripts/eval-ocr.sh <base_url> [pasta_com_gabarito.csv]
set -euo pipefail

BASE="${1:?uso: scripts/eval-ocr.sh <base_url> [pasta]}"
DIR="${2:-examples/fotos}"
API="${BASE%/}/api"
TIMEOUT="${OCR_TIMEOUT:-60}"

total=0
acertos=0
printf "%-40s %-10s %-10s %s\n" "FOTO" "ESPERADA" "LIDA" "RESULTADO"

while IFS=, read -r arquivo esperada; do
  [ "$arquivo" = "arquivo" ] && continue
  id=$(curl -fsS -F "photo=@${DIR}/${arquivo}" "$API/entries" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

  lida="?"
  for _ in $(seq 1 $((TIMEOUT / 2))); do
    lida=$(curl -fsS "$API/audit" | python3 -c "
import json, sys
for e in json.load(sys.stdin) or []:
    if e.get('entity_id') != '$id':
        continue
    if e['action'] == 'OCR_PROCESSING':
        print(e['details'].get('license_plate', '')); break
    if e['action'] == 'OCR_FAILED':
        print('-'); break
else:
    print('?')")
    [ "$lida" != "?" ] && break
    sleep 2
  done

  # Compara sem hífen (formato antigo ABC-1234).
  norm_lida=$(echo "$lida" | tr -d '-')
  if { [ -z "$esperada" ] && [ "$lida" = "-" ]; } || [ "$norm_lida" = "$esperada" ]; then
    resultado="OK"
    acertos=$((acertos + 1))
  else
    resultado="ERRO"
  fi
  total=$((total + 1))
  printf "%-40s %-10s %-10s %s\n" "$arquivo" "${esperada:-(nenhuma)}" "$lida" "$resultado"

  # Libera a vaga das entradas que estacionaram.
  if [ "$lida" != "-" ] && [ "$lida" != "?" ]; then
    curl -fsS -X POST "$API/exits/$id/pay" >/dev/null || true
  fi
done < "${DIR}/gabarito.csv"

echo "Acertos: $acertos/$total"
