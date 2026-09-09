#!/usr/bin/env bash
# ctx_sweep.sh — prefill and decode against prompt position on the running server, with VRAM sampled during each
# prompt and the kernel log watched. Answers "does the large window come at a spillover cost".
#
#   ctx_sweep.sh [--bench <llama-bench results.json>] [sizes…]
#
# Each size is a number of REAL TOKENS, measured with the server's own tokeniser (harness/prompt_budget.py fit),
# not a character count guessed at four characters a token. That guess asked for 100,000 tokens and built a prompt
# of 132,865: on a model served at 131,072 the server rejected it, and on a wider one it cost a third more time
# than the deadline allowed. Sizing the prompt in tokens also lands the far end exactly on a depth llama-bench
# measured, so the deadline below is read off the measured curve instead of continued past its end.
#
# The deadline for each point comes from that curve when --bench names the profile's llama-bench file
# (harness/prompt_budget.py time), because the cost of a long prompt is the area under seconds-per-token against
# depth and no single constant fits three models: 100,000 tokens is about 630 s of prefill on the 9B and about
# 1,050 s on the MoE. A770B_SWEEP_MAX_TIME overrides it; with neither, the deadline is a fixed generous one.
#
# A point that returns no answer FAILS THE SWEEP (exit 1) after the remaining points are tried. The old code let a
# timeout or a rejected prompt exit 0, so the ladder recorded a passing window rung with the far end missing and
# then filled useful_ctx from what little was left — the MoE's row read 8,000 for a model served at 131,072.
set -uo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
here=$(cd "$(dirname "$0")" && pwd)
BENCH=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --bench) BENCH="${2:?path to a llama-bench results file}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key); SIZES=("${ARGS[@]}")
# default: two points, 8k and the far end (the operator's rule: extrapolate the curve between them, not sweep every window)
[ "${#SIZES[@]}" -gt 0 ] || SIZES=(8000 100000)
T=$(mktemp -d)
# the corpus is part of the instrument (kit/corpus.py's own docstring): never fall back to a different corpus when
# A770B_CORPUS_FILE is missing — generate it, or refuse. a770b_ensure_corpus_file (harness/guard.sh) is the one
# place this rule is made, so every consumer here follows it the same way.
a770b_ensure_corpus_file || { rm -rf "$T"; exit 2; }

failed=0
printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' target tokens ttft_s prefill_tps decode_tps vram_max resets
for want in "${SIZES[@]}"; do
  # the prompt, cut to this many real tokens by the server's own tokeniser; a corpus too short to reach the
  # target is refused here rather than silently short-filled (the same refusal the character version made)
  if ! toks_expected=$(python3 "$here/prompt_budget.py" fit --corpus "$A770B_CORPUS_FILE" --target "$want" \
        --out "$T/prompt" --overhead 64 --url "$URL" --key "$KEY"); then
    echo "⛔ could not size a $want-token prompt — stopping the sweep" >&2; failed=1; break
  fi
  python3 -c 'import json,sys; t=open(sys.argv[1],encoding="utf-8").read(); print(json.dumps({"model":"local-builder","max_tokens":96,"temperature":0,"messages":[{"role":"user","content":"Read this code and answer in one sentence: what does it do?\n\n"+t}]}))' "$T/prompt" > "$T/body"

  # the deadline: from the measured curve when we have one, else whatever the operator set, else generous
  predicted=""
  if [ -n "${A770B_SWEEP_MAX_TIME:-}" ]; then
    deadline="$A770B_SWEEP_MAX_TIME"
    echo "  deadline ${deadline}s, set by A770B_SWEEP_MAX_TIME"
  else
    deadline=$(python3 "$here/prompt_budget.py" time --tokens "$toks_expected" --gen 96 ${BENCH:+--bench "$BENCH"} 2>"$T/note")
    predicted=$(sed -n 's/.*prefill \([0-9]*\)s.*/\1/p' "$T/note")
    echo "  $(cat "$T/note")"
  fi

  b0=$(kernel_resets_since '-1min')
  vmax=0; ( while :; do u=$(gpu_used_gib); echo "$u"; sleep 2; done ) > "$T/vram" & VP=$!
  t0=$(date +%s.%N)
  R=$(curl -s --max-time "$deadline" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @"$T/body" "$URL/v1/chat/completions")
  t1=$(date +%s.%N); kill $VP 2>/dev/null; wait $VP 2>/dev/null
  vmax=$(sort -n "$T/vram" | tail -1)
  read -r toks pms pps dps <<<"$(python3 -c '
import json,sys
try:
    r=json.loads(sys.argv[1]); u=r.get("usage",{}); t=r.get("timings",{})
    print(u.get("prompt_tokens",0), round(t.get("prompt_ms",0)/1000,1), round(t.get("prompt_per_second",0),0), round(t.get("predicted_per_second",0),1))
except Exception as e: print(0,0,0,0)' "$R")"
  wall=$(python3 -c "print(round($t1-$t0,1))"); after=$(kernel_resets_since '-1min')
  if [ "$toks" = 0 ]; then
    # say which of the two it was: the deadline we set, or the server turning the request down
    if [ -z "$R" ]; then
      echo "$want: NO ANSWER after ${wall}s — the ${deadline}s deadline cut the request off (asked for $toks_expected tokens)"
    else
      echo "$want: NO ANSWER (wall ${wall}s): $(echo "$R" | head -c 200)"
    fi
    failed=1
  else
    printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' "$want" "$toks" "$pms" "$pps" "$dps" "${vmax}GiB" "$((after-b0))"
    # what the curve said it would cost, beside what it did. They should be close, and the prediction should sit a
    # little high; a request that runs well past its own prediction is the card telling you something changed.
    if [ -n "$predicted" ] && [ "$predicted" -gt 0 ] 2>/dev/null; then
      python3 -c '
import sys
pred, actual = float(sys.argv[1]), float(sys.argv[2])
ratio = actual / pred if pred else 0
note = " — ran past its own prediction, worth a look" if ratio > 1.3 else ""
print(f"  prefill predicted {pred:.0f}s from the measured curve, actually {actual:.0f}s ({ratio:.2f}x){note}")' "$predicted" "$pms"
    fi
  fi
  curl -sf --max-time 5 "$URL/health" | grep -q '"ok"' || { echo "server not healthy after $want — stopping the sweep"; failed=1; break; }
done
rm -rf "$T"
[ "$failed" = 0 ] || { echo "⛔ the sweep did not measure every point it was asked for" >&2; exit 1; }
exit 0
