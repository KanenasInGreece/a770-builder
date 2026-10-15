#!/usr/bin/env bash
# ctx_sweep.sh — prefill and decode against prompt position on the running server, with VRAM sampled during each
# prompt and the kernel log watched. Answers "does the large window come at a spillover cost".
#
#   ctx_sweep.sh [--bench <llama-bench results.json>] [--tokenize llama|openai] [--model NAME] [--tokenizer DIR] [sizes…]
#
# Each size is a number of REAL TOKENS, measured with the server's own tokeniser (harness/prompt_budget.py fit),
# not a character count guessed at four characters a token. That guess asked for 100,000 tokens and built a prompt
# of 132,865: on a model served at 131,072 the server rejected it, and on a wider one it cost a third more time
# than the deadline allowed. Sizing the prompt in tokens also lands the far end exactly on a depth llama-bench
# measured, so the deadline below is read off the measured curve instead of continued past its end.
#
# Every size asked for is proved against the corpus before the first point is served, so a corpus too short for
# the far end refuses the rung at once instead of after a whole short point has been paid for.
#
# The deadline for each point comes from that curve when --bench names the profile's llama-bench file
# (harness/prompt_budget.py time), because the cost of a long prompt is the area under seconds-per-token against
# depth and no single constant fits three models: 100,000 tokens is about 630 s of prefill on the 9B and about
# 1,050 s on the MoE. A770B_SWEEP_MAX_TIME overrides it; with neither, the deadline is a fixed generous one. A
# deadline the curve could not produce, because that sub-process failed or printed something that is not a number,
# is never used as one: the fallback in force is named in the line that reports it.
#
# A point that returns no answer FAILS THE SWEEP (exit 1) after the remaining points are tried. The old code let a
# timeout or a rejected prompt exit 0, so the ladder recorded a passing window rung with the far end missing and
# then filled useful_ctx from what little was left — the MoE's row read 8,000 for a model served at 131,072.
#
# A point is filed under the size the SERVER SAYS IT RECEIVED, not the size asked for: one that landed outside the
# tolerance below measured some other depth, and is reported as not measured rather than as a reading at a depth
# nothing ran.
#
# Of the two ways a point can be refused, the missing rate is asked first and before anything is computed on the
# token count: a point whose server sent no prefill or decode rate measured no rate whatever depth its prompt
# landed at, and how far that prompt sat from the size asked for decides nothing about the rate it never gave.
#
# A llama.cpp body is one JSON object: the two rates come from timings, and a missing rate stays MISSING, never
# zero. A770B_SWEEP_STREAM=1 also sends stream and stream_options.include_usage. A server-sent event stream is
# timed as its lines arrive. Prefill tok/s is prompt_tokens over the seconds to the first delta.content or
# delta.reasoning_content. Decode tok/s is completion_tokens over the seconds after that line. Token counts come
# from the last usage object. No first delta or no usage is the same failure as a missing rate. The request body
# is unchanged when A770B_SWEEP_STREAM is unset.
set -uo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
here=$(cd "$(dirname "$0")" && pwd)
BENCH=""
TOKENIZE="llama"
MODEL_NAME="local-builder"
TOKENIZER=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --bench) BENCH="${2:?path to a llama-bench results file}"; shift 2 ;;
    --tokenize) TOKENIZE="${2:?llama or openai}"; shift 2 ;;
    --model) MODEL_NAME="${2:?served model name}"; shift 2 ;;
    --tokenizer) TOKENIZER="${2:?tokenizer directory}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
case "$TOKENIZE" in
  llama|openai) ;;
  *) echo "⛔ --tokenize must be llama or openai (got '$TOKENIZE')" >&2; exit 2 ;;
esac
if [ "$TOKENIZE" = "openai" ] && [ -z "$MODEL_NAME" ]; then
  echo "⛔ --tokenize openai needs --model" >&2; exit 2
fi
if [ "$TOKENIZE" = "openai" ] && [ -z "$TOKENIZER" ]; then
  echo "⛔ --tokenize openai needs a tokenizer directory" >&2; exit 2
fi
URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key); SIZES=("${ARGS[@]}")
# default: two points, 8k and the far end (the operator's rule: extrapolate the curve between them, not sweep every window)
[ "${#SIZES[@]}" -gt 0 ] || SIZES=(8000 100000)
T=$(mktemp -d)
# the corpus is part of the instrument (kit/corpus.py's own docstring): never fall back to a different corpus when
# A770B_CORPUS_FILE is missing — generate it, or refuse. a770b_ensure_corpus_file (harness/guard.sh) is the one
# place this rule is made, so every consumer here follows it the same way.
a770b_ensure_corpus_file || { rm -rf "$T"; exit 2; }

failed=0
# the deadline of last resort, in seconds: the figure harness/prompt_budget.py itself falls back to when it has no
# measured curve. Waiting out one slow point costs one point; cutting a long prompt short costs the whole rung.
FIXED_DEADLINE=7200

# tolerance_for <size>: how far the size the server reports it received may sit from the size asked for before the
# point is refused as not measured. Five percent of the size, and never under 256 tokens. The reasoning: `fit`
# (harness/prompt_budget.py) is allowed to land up to five percent under the size asked for and refuses anything
# shorter, so a gap inside five percent is the sizing working as designed rather than an error; the 256-token floor
# covers the small sizes, where the 64-token allowance for the chat wrapper is an estimate that a model's own
# template can miss by a few dozen tokens. The error this band exists to catch is the four-characters-a-token
# class: it asked for 100,000 tokens and sent 132,865, a third out, and the point was filed as a measurement at
# 100,000 all the same.
tolerance_for(){ local tol=$(( $1 * 5 / 100 )); [ "$tol" -ge 256 ] || tol=256; printf '%s' "$tol"; }

# Every requested size is proved against the corpus BEFORE the first point is served. Sizing a prompt costs a few
# calls to the server's tokeniser; serving one costs minutes. A corpus too short for the far end used to be found
# only when that point came round, after the whole short point had already been paid for. Each prompt is built once
# here and served from these files below; a corpus too short to reach a size is refused rather than short-filled
# (the same refusal the character version made).
declare -A PROMPT_FOR EXPECTED_FOR
for want in "${SIZES[@]}"; do
  tok_arg=()
  [ -n "$TOKENIZER" ] && tok_arg=(--tokenizer "$TOKENIZER")
  if ! toks_expected=$(python3 "$here/prompt_budget.py" fit --corpus "$A770B_CORPUS_FILE" --target "$want" \
        --out "$T/prompt.$want" --overhead 64 --url "$URL" --key "$KEY" \
        --tokenize "$TOKENIZE" --model "$MODEL_NAME" "${tok_arg[@]}"); then
    echo "⛔ could not size a $want-token prompt from the corpus: refusing the sweep before any point is served" >&2
    rm -rf "$T"; exit 1
  fi
  PROMPT_FOR[$want]="$T/prompt.$want"; EXPECTED_FOR[$want]="$toks_expected"
done

# one reader for every point: a JSON object keeps the timings rule; an event stream is clocked per line
cat > "$T/read_point.py" <<'PY'
import json
import os
import subprocess
import sys
import time

raw_path = sys.argv[1]
module = sys.argv[2]
start = time.perf_counter()
rows = []
# os.read returns what has arrived. Text-mode readline pulls a whole buffer and then
# stamps every line in it at the loop, which makes a short decode look several times faster.
fd = sys.stdin.buffer.fileno()
pending = b""
with open(raw_path, "w", encoding="utf-8") as raw:
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        now = time.perf_counter()
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            text = line.decode("utf-8", "replace") + "\n"
            raw.write(text)
            rows.append((now - start, text))
    if pending:
        now = time.perf_counter()
        text = pending.decode("utf-8", "replace")
        raw.write(text)
        rows.append((now - start, text))


def ask(command, stdin_text):
    # the module decides where the answer came from; the reader only hands it the body it clocked
    done = subprocess.run([sys.executable, module, command],
                          input=stdin_text, capture_output=True, text=True)
    return done.stdout.strip()


def main():
    text = "".join(line for _, line in rows).strip()
    if not text:
        # an empty body never reaches window: the shell's deadline branch owns it
        print("NO_ANSWER")
        return
    has_data = False
    samples = []
    for at, line in rows:
        seen = line.strip()
        if not seen.startswith("data:"):
            continue
        has_data = True
        payload = seen[5:].strip()
        if payload == "" or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            samples.append([at, event])
    if not has_data:
        print(ask("window", text) or "NO_ANSWER")
        return
    rec = None
    try:
        rec = json.loads(ask("stream", json.dumps(samples)))
    except ValueError:
        rec = None
    if not isinstance(rec, dict):
        print("NO_ANSWER")
        return
    tokens = rec.get("prompt_tokens")
    if tokens is None:
        # every event an object that carries an error and no choices is the server turning the request
        # down; any other null count is a completion whose count was never reported, which is unmeasured
        if all("error" in event and "choices" not in event for _, event in samples):
            print("NO_ANSWER")
        else:
            print("UNMEASURED")
        return
    prefill = rec.get("prefill_tps")
    decode = rec.get("decode_tps")
    if prefill is None or decode is None:
        print(tokens, "MISSING", "MISSING", "MISSING")
        return
    print(tokens, round(rec.get("first_delta_s") or 0, 1), round(prefill), round(decode, 1))


try:
    main()
except Exception:
    print("NO_ANSWER")
PY
printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' target tokens ttft_s prefill_tps decode_tps vram_max resets
for want in "${SIZES[@]}"; do
  toks_expected="${EXPECTED_FOR[$want]}"
  # the per-point check, kept: the prompt proved for this point is still on disk when its turn comes
  if [ ! -s "${PROMPT_FOR[$want]}" ]; then
    echo "⛔ the $want-token prompt proved before the sweep is gone: stopping the sweep" >&2; failed=1; break
  fi
  python3 -c 'import json,os,sys; t=open(sys.argv[1],encoding="utf-8").read(); body={"model":sys.argv[2],"max_tokens":96,"temperature":0,"messages":[{"role":"user","content":"Read this code and answer in one sentence: what does it do?\n\n"+t}]}; os.environ.get("A770B_SWEEP_STREAM")=="1" and body.update(stream=True, stream_options={"include_usage":True}); print(json.dumps(body))' "${PROMPT_FOR[$want]}" "$MODEL_NAME" > "$T/body"

  # the deadline: from the measured curve when we have one, else whatever the operator set, else generous. The
  # sub-process that reads the curve can fail or print something that is not a number, and a deadline that was
  # never computed is not a deadline: both are checked, and the line below says which deadline is in force and why.
  predicted=""; deadline=""; why=""
  if [ -z "${A770B_SWEEP_MAX_TIME:-}" ]; then
    if ! estimate=$(python3 "$here/prompt_budget.py" time --tokens "$toks_expected" --gen 96 ${BENCH:+--bench "$BENCH"} 2>"$T/note"); then
      why="the deadline estimate exited non-zero"
    elif ! printf '%s' "$estimate" | grep -qE '^[0-9]+$'; then
      why="the deadline estimate was not a number ('$estimate')"
    else
      deadline="$estimate"
      predicted=$(sed -n 's/.*prefill \([0-9]*\)s.*/\1/p' "$T/note")
      echo "  $(cat "$T/note")"
    fi
  fi
  if [ -z "$deadline" ]; then
    if [ -n "${A770B_SWEEP_MAX_TIME:-}" ]; then
      deadline="$A770B_SWEEP_MAX_TIME"
      echo "  deadline ${deadline}s, set by A770B_SWEEP_MAX_TIME"
    else
      deadline="$FIXED_DEADLINE"
      echo "  deadline ${deadline}s, the fixed generous fallback: $why"
    fi
  fi

  b0=$(kernel_resets_since '-1min')
  vmax=0; ( while :; do u=$(gpu_used_gib); echo "$u"; sleep 2; done ) > "$T/vram" & VP=$!
  t0=$(date +%s.%N)
  # -sN so a streamed line is readable when it arrives, not when the body ends. The selftest's curl stub stays the client.
  read -r toks pms pps dps <<<"$(curl -sN --max-time "$deadline" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @"$T/body" "$URL/v1/chat/completions" | python3 "$T/read_point.py" "$T/raw" "$A770B_PROJECT/harness/engine_record.py")"
  t1=$(date +%s.%N); kill $VP 2>/dev/null; wait $VP 2>/dev/null
  vmax=$(sort -n "$T/vram" | tail -1)
  R=$(cat "$T/raw" 2>/dev/null || true)
  wall=$(python3 -c "print(round($t1-$t0,1))"); after=$(kernel_resets_since '-1min')
  if [ -z "$R" ]; then
    # an empty body is the deadline that we set, never a rate failure and never the server's own words
    echo "$want: NO ANSWER after ${wall}s — the ${deadline}s deadline cut the request off (asked for $toks_expected tokens)"
    failed=1
  elif [ "$toks" = "NO_ANSWER" ]; then
    # the server answered with something that is not a completion: its own words, capped
    echo "$want: NO ANSWER (wall ${wall}s): $(echo "$R" | head -c 200)"
    failed=1
  elif [ "$toks" = "UNMEASURED" ]; then
    echo "$want: NOT MEASURED: the completion had no prompt token count"
    failed=1
  else
    # the rate first, and before anything is computed on the token count: a point that reported no prefill or
    # decode rate measured no rate whatever depth its prompt landed at, and the depth sentence below would bury
    # that. Nothing is subtracted here, so a MISSING rate is refused on its own grounds.
    if [ "$pps" = "MISSING" ] || [ "$dps" = "MISSING" ]; then
      echo "$want: NOT MEASURED: the server reported no prefill or decode rate"
      failed=1
    else
      # the server reports how many tokens it received, and that is the depth this point measured. A point whose
      # prompt landed far from the size asked for measured some other depth, and filing it under the size asked for
      # would record a measurement nothing took: it is reported as not measured instead, and fails the rung.
      tol=$(tolerance_for "$want")
      off=$(( toks > want ? toks - want : want - toks ))
      if [ "$off" -gt "$tol" ]; then
        echo "$want: NOT MEASURED: the server was sent $toks tokens, $off away from the $want asked for (tolerance $tol tokens, sizing expected $toks_expected); this point measures no depth the sweep was asked for"
        failed=1
      else
        printf '%-8s %-8s %-9s %-11s %-10s %-9s %-8s\n' "$want" "$toks" "$pms" "$pps" "$dps" "${vmax}GiB" "$((after-b0))"
        # what the curve said it would cost, beside what it did. They should be close, and the prediction should sit a
        # little high; a request that runs well past its own prediction is the card telling you something changed.
        if [ -n "$predicted" ] && [ "$predicted" -gt 0 ] 2>/dev/null \
            && printf '%s' "$pms" | grep -qE '^[0-9]+(\.[0-9]+)?$'; then
          python3 -c '
import sys
pred, actual = float(sys.argv[1]), float(sys.argv[2])
ratio = actual / pred if pred else 0
note = " — ran past its own prediction, worth a look" if ratio > 1.3 else ""
print(f"  prefill predicted {pred:.0f}s from the measured curve, actually {actual:.0f}s ({ratio:.2f}x){note}")' "$predicted" "$pms"
        fi
      fi
    fi
  fi
  # any HTTP success: the body is not part of the check (an empty 200 is healthy)
  if ! curl -sf --max-time 5 -o /dev/null "$URL/health" >/dev/null; then
    echo "server not healthy after $want — stopping the sweep"; failed=1; break
  fi
done
rm -rf "$T"
[ "$failed" = 0 ] || { echo "⛔ the sweep did not measure every point it was asked for" >&2; exit 1; }
exit 0
