#!/usr/bin/env bash
# ctx_sweep.sh — prefill and decode against prompt position on the running server, with VRAM sampled during each
# prompt and the kernel log watched. Answers "does the large window come at a spillover cost". Usage: ctx_sweep.sh [sizes…]
set -uo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
# default: two points, 8k and the far end (the operator's rule: extrapolate the curve between them, not sweep every window)
URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key); SIZES=("$@"); [ "${#SIZES[@]}" -gt 0 ] || SIZES=(8000 100000)
CORPUS=$(mktemp)
# the corpus is part of the instrument (kit/corpus.py's own docstring): never fall back to a different corpus when
# A770B_CORPUS_FILE is missing — generate it, or refuse. a770b_ensure_corpus_file (harness/guard.sh) is the one
# place this rule is made, so every consumer here follows it the same way.
a770b_ensure_corpus_file || { rm -f "$CORPUS"; exit 2; }
cp "$A770B_CORPUS_FILE" "$CORPUS"
corpus_bytes=$(wc -c < "$CORPUS")
# refuse the whole sweep up front when the corpus cannot cover its largest requested point — never skip one
# point and quietly run the rest (the operator asked to compare the points against each other; a truncated
# sweep that still exits 0 hides that the far end was never measured), the same refusal depth_probe.sh makes
# for its own single target.
max_want=0
for want in "${SIZES[@]}"; do [ "$want" -gt "$max_want" ] && max_want=$want; done
max_chars=$((max_want*4))
if [ "$corpus_bytes" -lt "$max_chars" ]; then
  echo "⛔ corpus has $corpus_bytes bytes, need $max_chars for the largest target ($max_want tokens) — refusing (head -c would silently short-fill)" >&2
  rm -f "$CORPUS"
  exit 2
fi
printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' target tokens ttft_s prefill_tps decode_tps vram_max resets
for want in "${SIZES[@]}"; do
  chars=$((want*4))
  if [ "$corpus_bytes" -lt "$chars" ]; then echo "⛔ corpus has $corpus_bytes bytes, need $chars for target $want tokens — refusing (head -c would silently short-fill)" >&2; rm -f "$CORPUS" "$CORPUS.vram" "$CORPUS.body"; exit 2; fi
  TXT=$(head -c "$chars" "$CORPUS")
  python3 -c 'import json,sys; t=sys.stdin.read(); print(json.dumps({"model":"local-builder","max_tokens":96,"temperature":0,"messages":[{"role":"user","content":"Read this code and answer in one sentence: what does it do?\n\n"+t}]}))' <<<"$TXT" > "$CORPUS.body"
  b0=$(kernel_resets_since '-1min')
  vmax=0; ( while :; do u=$(gpu_used_gib); echo "$u"; sleep 2; done ) > "$CORPUS.vram" & VP=$!
  t0=$(date +%s.%N)
  R=$(curl -s --max-time "${A770B_SWEEP_MAX_TIME:-900}" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @"$CORPUS.body" "$URL/v1/chat/completions")
  t1=$(date +%s.%N); kill $VP 2>/dev/null; wait $VP 2>/dev/null
  vmax=$(sort -n "$CORPUS.vram" | tail -1)
  read -r toks pms pps dps <<<"$(python3 -c '
import json,sys
try:
    r=json.loads(sys.argv[1]); u=r.get("usage",{}); t=r.get("timings",{})
    print(u.get("prompt_tokens",0), round(t.get("prompt_ms",0)/1000,1), round(t.get("prompt_per_second",0),0), round(t.get("predicted_per_second",0),1))
except Exception as e: print(0,0,0,0)' "$R")"
  wall=$(python3 -c "print(round($t1-$t0,1))"); after=$(kernel_resets_since '-1min')
  [ "$toks" = 0 ] && echo "$want: NO ANSWER (wall ${wall}s): $(echo "$R" | head -c 160)" || printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' "$want" "$toks" "$pms" "$pps" "$dps" "${vmax}GiB" "$((after-b0))"
  curl -sf --max-time 5 "$URL/health" | grep -q '"ok"' || { echo "server not healthy after $want — stopping the sweep"; break; }
done
rm -f "$CORPUS" "$CORPUS.vram" "$CORPUS.body"
