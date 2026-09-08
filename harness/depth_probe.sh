#!/usr/bin/env bash
# depth_probe.sh — quality at depth: a prompt of ~N tokens of real repository code with one unique function planted at
# ~85 percent of the way in, then three questions whose answers need that depth. Prints the answers, the timings and
# VRAM. Usage: depth_probe.sh <target_tokens>
set -uo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
URL="http://$A770B_HOST:$A770B_PORT"; KEY=$(a770b_api_key); want=${1:-100000}
T=$(mktemp -d)
if [ -n "${A770B_CORPUS_FILE:-}" ] && [ -f "$A770B_CORPUS_FILE" ]; then
  cp "$A770B_CORPUS_FILE" "$T/corpus"; echo "corpus: kit generator ($A770B_CORPUS_FILE)"
else
  find "$A770B_SEAT" -name '*.py' -size +2k | sort | head -400 | xargs cat 2>/dev/null | tr -cd '\11\12\15\40-\176' > "$T/corpus"; echo "corpus: seat glob ($A770B_SEAT/**/*.py, today's fallback — kit/corpus.py has not generated $A770B_CORPUS_FILE)"
fi
chars=$((want*4)); pos=$((chars*85/100))
corpus_bytes=$(wc -c < "$T/corpus")
[ "$corpus_bytes" -ge "$chars" ] || { echo "⛔ corpus has $corpus_bytes bytes, need $chars for target $want tokens — refusing (head -c would silently short-fill)" >&2; rm -rf "$T"; exit 2; }
PLANT='

def orbital_checksum_v7(payload: bytes, salt: int = 4171) -> int:
    """Return the Kepler-weighted checksum of payload: each byte is multiplied by its 1-based position, the sum is
    xor-ed with salt, and the result is reduced modulo 65521 (the Adler prime). Used only by the perihelion exporter."""
    total = sum(b * i for i, b in enumerate(payload, start=1))
    return (total ^ salt) % 65521

'
{ head -c "$pos" "$T/corpus"; printf '%s' "$PLANT"; tail -c +"$((pos+1))" "$T/corpus" | head -c "$((chars-pos))"; } > "$T/prompt"
Q='You have just read a large body of Python source. Answer three questions, each in one or two sentences, precisely and only from the code you read:
1. What does the function orbital_checksum_v7 compute, step by step, and what is the default salt?
2. Which modulus does it reduce by, and what comment does it give for that number?
3. Name three distinct functions or classes defined in the source you read that are NOT orbital_checksum_v7, with one clause each on what they do.'
python3 - "$T/prompt" "$Q" > "$T/body" <<'PY'
import json,sys
t=open(sys.argv[1]).read(); q=sys.argv[2]
print(json.dumps({"model":"local-builder","max_tokens":320,"temperature":0,"messages":[{"role":"user","content":"SOURCE:\n\n"+t+"\n\nQUESTIONS:\n"+q}]}))
PY
b0=$(journalctl -k --since '-1min' 2>/dev/null | grep -ciE 'engine reset|timedout')
( while :; do gpu_used_gib; sleep 2; done ) > "$T/vram" & VP=$!
t0=$(date +%s); R=$(curl -s --max-time 1200 -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @"$T/body" "$URL/v1/chat/completions"); t1=$(date +%s)
kill $VP 2>/dev/null; wait $VP 2>/dev/null
python3 - "$R" "$((t1-t0))" "$(sort -n "$T/vram" | tail -1)" "$(journalctl -k --since '-1min' 2>/dev/null | grep -ciE 'engine reset|timedout')" "$b0" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); u=r.get("usage",{}); t=r.get("timings",{})
print(f"prompt_tokens={u.get('prompt_tokens')} wall={sys.argv[2]}s prefill_tps={t.get('prompt_per_second',0):.0f} decode_tps={t.get('predicted_per_second',0):.1f} vram_max={float(sys.argv[3]):.2f}GiB resets={int(sys.argv[4])-int(sys.argv[5])}")
print("ANSWER:\n"+r["choices"][0]["message"]["content"].strip())
PY
rm -rf "$T"
