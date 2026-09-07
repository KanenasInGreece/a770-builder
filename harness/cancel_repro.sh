#!/usr/bin/env bash
# cancel_repro.sh — the deterministic reproduction from the clawee record: request A (a ~3k-token prompt, max_tokens 400)
# holds the single slot; 4 s later request B, the same prompt, is cancelled by its client after 5 s while queued. On
# Gemma 4 with flash attention on that killed the card every time. Runs N rounds against the harness's server, then
# reports device-lost lines, kernel resets, and whether the server still answers. Usage: cancel_repro.sh [rounds]
set -u
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
N=${1:-5}; URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key)
# a ~3k-token prompt from the seat's own source
TXT=$(head -c 11000 "$A770B_SEAT/shared-memory/scripts/ontology.py" 2>/dev/null || head -c 11000 "$A770B_PROJECT/harness/guard.sh")
BODY=$(python3 -c 'import json,sys; t=sys.stdin.read(); print(json.dumps({"model":"local-builder","max_tokens":400,"temperature":0,"messages":[{"role":"user","content":"Summarise this file in three sentences:\n\n"+t}]}))' <<<"$TXT")
before=$(journalctl -k --since '-1min' 2>/dev/null | grep -ciE 'engine reset|timedout')
ok=0; lost=0
for i in $(seq 1 "$N"); do
  t0=$(date +%s)
  curl -s -o /dev/null -w "A%{http_code} " -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d "$BODY" "$URL/v1/chat/completions" &
  sleep 4
  curl -s -o /dev/null --max-time 5 -w "B%{http_code}(cancelled) " -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d "$BODY" "$URL/v1/chat/completions"
  wait
  sleep 2
  if curl -sf --max-time 5 "$URL/health" | grep -q '"ok"'; then ok=$((ok+1)); echo "round $i: server ok after $(( $(date +%s)-t0 )) s"; else lost=$((lost+1)); echo "round $i: SERVER NOT HEALTHY"; fi
  journalctl -k --since '-30s' 2>/dev/null | grep -iE 'engine reset|timedout' | tail -1 | cut -c1-120
done
after=$(journalctl -k --since '-10min' 2>/dev/null | grep -ciE 'engine reset|timedout')
echo "RESULT rounds=$N healthy_after=$ok unhealthy=$lost kernel_resets_during=$((after-before)) device_lost_lines=$(grep -c 'device lost' "$A770B_DATA/logs/llamacpp-a770.log")"
