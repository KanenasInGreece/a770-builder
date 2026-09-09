#!/usr/bin/env bash
# depth_probe.sh — quality at depth: a prompt of N tokens of real repository code with one unique function planted at
# ~85 percent of the way in, then three questions whose answers need that depth. Prints the answers, the timings, VRAM,
# and GRADES ITSELF against the planted facts: one PASS/FAIL line per question, then "depth probe at <tokens>: <n>/3".
# Q1 checks the function's described behaviour (multiplying each byte by its 1-based position, then xor-ing with the
# salt) and its default salt (4171); Q2 checks the modulus it reduces by (65521) and the comment naming it (the Adler
# prime); Q3 asks for three OTHER real definitions from the source, graded by checking that each name the reply lists
# actually appears as a "def"/"class" in the corpus text the model actually read (A770B_CORPUS_FILE, kit/corpus.py's
# own generated file — see a770b_ensure_corpus_file below), never against a fixed list.
#
#   depth_probe.sh <target_tokens> [--bench <llama-bench results.json>]
#
# The target is REAL TOKENS, measured with the server's own tokeniser, and the deadline is read off the card's own
# measured curve — see harness/prompt_budget.py for why both used to be guesses and what the guesses cost.
#
# Exit codes, because they mean different things to the ladder and used to be run together:
#   0  at least two of three right
#   1  the model answered and got fewer than two right — a real grading failure
#   2  the probe could not be set up (no corpus, no tokeniser, corpus too short)
#   3  the server never answered: the prompt was turned down, or the deadline cut it off. The model was never
#      asked, so this says nothing about its quality at depth and must not be recorded as if it did.
set -uo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
here=$(cd "$(dirname "$0")" && pwd)
URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key)
want=""; BENCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --bench) BENCH="${2:?path to a llama-bench results file}"; shift 2 ;;
    *) want="$1"; shift ;;
  esac
done
want=${want:-100000}
T=$(mktemp -d)
# the corpus is part of the instrument (kit/corpus.py's own docstring): never fall back to a different corpus when
# A770B_CORPUS_FILE is missing — generate it, or refuse. a770b_ensure_corpus_file (harness/guard.sh) is the one
# place this rule is made, so every consumer here follows it the same way.
a770b_ensure_corpus_file || { rm -rf "$T"; exit 2; }
cat > "$T/plant" <<'PLANT'


def orbital_checksum_v7(payload: bytes, salt: int = 4171) -> int:
    """Return the Kepler-weighted checksum of payload: each byte is multiplied by its 1-based position, the sum is
    xor-ed with salt, and the result is reduced modulo 65521 (the Adler prime). Used only by the perihelion exporter."""
    total = sum(b * i for i, b in enumerate(payload, start=1))
    return (total ^ salt) % 65521


PLANT
# the prompt, planted and cut to `want` real tokens by the server's own tokeniser (256 of them left for the
# questions and the chat template wrapped around it below)
if ! toks_expected=$(python3 "$here/prompt_budget.py" fit --corpus "$A770B_CORPUS_FILE" --target "$want" \
      --out "$T/prompt" --plant-file "$T/plant" --plant-at 0.85 --overhead 256 --url "$URL" --key "$KEY"); then
  echo "⛔ could not size a $want-token prompt for the depth probe" >&2; rm -rf "$T"; exit 2
fi
Q='You have just read a large body of Python source. Answer three questions, each in one or two sentences, precisely and only from the code you read:
1. What does the function orbital_checksum_v7 compute, step by step, and what is the default salt?
2. Which modulus does it reduce by, and what comment does it give for that number?
3. Name three distinct functions or classes defined in the source you read that are NOT orbital_checksum_v7, with one clause each on what they do.'
python3 - "$T/prompt" "$Q" > "$T/body" <<'PY'
import json,sys
t=open(sys.argv[1], encoding="utf-8", errors="ignore").read(); q=sys.argv[2]
print(json.dumps({"model":"local-builder","max_tokens":320,"temperature":0,"messages":[{"role":"user","content":"SOURCE:\n\n"+t+"\n\nQUESTIONS:\n"+q}]}))
PY
# the deadline: from the measured curve when we have one, else whatever the operator set, else generous
if [ -n "${A770B_PROBE_MAX_TIME:-}" ]; then
  deadline="$A770B_PROBE_MAX_TIME"
else
  deadline=$(python3 "$here/prompt_budget.py" time --tokens "$toks_expected" --gen 320 ${BENCH:+--bench "$BENCH"})
fi
b0=$(kernel_resets_since '-1min')
( while :; do gpu_used_gib; sleep 2; done ) > "$T/vram" & VP=$!
t0=$(date +%s); R=$(curl -s --max-time "$deadline" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @"$T/body" "$URL/v1/chat/completions"); t1=$(date +%s)
kill $VP 2>/dev/null; wait $VP 2>/dev/null
python3 - "$R" "$((t1-t0))" "$(sort -n "$T/vram" | tail -1)" "$(kernel_resets_since '-1min')" "$b0" "$T/prompt" "$want" "$deadline" <<'PY'
import json, re, sys

def _num(v, unit=""):
    # a VRAM sample can come back empty when nvtop is unreadable for a moment; report it rather than crashing
    try:
        return f"{float(v):.2f}{unit}"
    except (TypeError, ValueError):
        return "not measured"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


r_raw, wall, vram_max, after, before, corpus_path, want, deadline = sys.argv[1:9]
answer, refusal, answered = "", "", False
try:
    r = json.loads(r_raw)
    u, t = r.get("usage", {}), r.get("timings", {})
    print(f"prompt_tokens={u.get('prompt_tokens')} wall={wall}s prefill_tps={t.get('prompt_per_second', 0):.0f} "
          f"decode_tps={t.get('predicted_per_second', 0):.1f} vram_max={_num(vram_max, 'GiB')} resets={_int(after) - _int(before)}")
    # a content field that came back at all means the model answered, even if it answered with nothing:
    # an empty answer at depth is a quality failure to be graded 0/3, never a request that was not made
    content = r["choices"][0]["message"].get("content")
    answered = isinstance(content, str)
    answer = (content or "").strip()
    if not answered:
        refusal = (r.get("error") or {}).get("message") or "the reply carried no choices[0].message.content"
except (json.JSONDecodeError, KeyError, IndexError, TypeError):
    if not r_raw.strip():
        refusal = f"the {deadline}s deadline cut the request off after {wall}s"
    else:
        try:
            refusal = (json.loads(r_raw).get("error") or {}).get("message") or r_raw[:200]
        except ValueError:
            refusal = r_raw[:200]

# the server never answered — the model was never asked, so there is nothing to grade and nothing to conclude
# about its quality at this depth. Said plainly, and with an exit code the ladder can tell from a real 0/3.
if not answered:
    print(f"ANSWER:\n(no answer — {refusal})")
    print(f"depth probe at {want}: not measured — {refusal}")
    sys.exit(3)

print("ANSWER:\n" + (answer or "(the model answered with nothing)"))

# ── grading: the three planted facts (see the PLANT string above) ────────────────────────────────────────────
low = answer.lower()
# Q1: the described behaviour (multiply-by-position, then xor) AND the default salt
q1_ok = ("xor" in low) and any(k in low for k in ("position", "1-based", "index")) and ("4171" in answer)
# Q2: the modulus and the comment naming it
q2_ok = ("65521" in answer) and ("adler" in low)
# Q3: three OTHER real definitions — checked against the corpus text the model actually read, never a fixed list
corpus_text = open(corpus_path, encoding="utf-8", errors="ignore").read()
defs = set(re.findall(r'^\s*(?:def|class)\s+(\w+)', corpus_text, re.MULTILINE)) - {"orbital_checksum_v7"}
mentioned = sorted(d for d in defs if re.search(r'\b' + re.escape(d) + r'\b', answer))
q3_ok = len(mentioned) >= 3

checks = [
    ("Q1 (orbital_checksum_v7's behaviour and its default salt 4171)", q1_ok),
    ("Q2 (the modulus 65521 and its Adler-prime comment)", q2_ok),
    ("Q3 (three other real definitions from the source)", q3_ok),
]
n_pass = sum(1 for _, ok in checks if ok)
for label, ok in checks:
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
if mentioned:
    shown = ", ".join(mentioned[:8]) + (" …" if len(mentioned) > 8 else "")
    print(f"  Q3 matched definitions: {shown}")
print(f"depth probe at {want}: {n_pass}/3")
sys.exit(0 if n_pass >= 2 else 1)
PY
grc=$?
rm -rf "$T"
exit "$grc"
