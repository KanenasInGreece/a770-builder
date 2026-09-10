#!/usr/bin/env bash
# bench_speed.sh — the standard speed instrument: llama-bench built from the profile's own registry values, the
# same flags serve_a770_llamacpp.sh would serve the row at. Touches no server: refuses outright while the harness's
# own server is up (one GPU process on the card at a time; MESA_VK_DEVICE_SELECT pins the same builder card).
#   bench_speed.sh <profile> [--depths 0,8192,32768] [--prompt 8192] [--gen 128] [--reps 3] [--dry-run]
# Writes $A770B_DATA/results/<profile>-bench-<date>.json: the raw llama-bench JSON plus a header (profile, model,
# the flags, the build id). Prints one line per depth: "depth <d>: pp<prompt> <x> tok/s · tg<gen> <y> tok/s".
# --dry-run prints the llama-bench command only — it is computed before the server-pidfile check, so a dry run never
# refuses and never touches the GPU. Path of the binary: ${A770B_LLAMA_BENCH:-$(dirname "$A770B_LLAMA_BIN")/llama-bench}.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"

usage(){ echo "usage: bench_speed.sh <profile> [--depths d0,d1,...] [--prompt N] [--gen N] [--reps N] [--dry-run]" >&2; exit 2; }

PROFILE="${1:-}"; [ -n "$PROFILE" ] || usage; shift
a770b_is_profile "$PROFILE" || { echo "⛔ no such profile: $PROFILE (A770B_PROFILES=$A770B_PROFILES)" >&2; exit 2; }

DEPTHS="0,8192,32768"; PROMPT=8192; GEN=128; REPS=3; DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --depths)  DEPTHS="${2:?--depths needs a value}"; shift 2;;
    --prompt)  PROMPT="${2:?--prompt needs a value}"; shift 2;;
    --gen)     GEN="${2:?--gen needs a value}"; shift 2;;
    --reps)    REPS="${2:?--reps needs a value}"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    *) echo "⛔ unknown argument: $1" >&2; usage;;
  esac
done

# the row's own registry values, exactly as serve_a770_llamacpp.sh would serve them: KV/KV_V/MODEL come from
# a770b_profile_var, which reads the environment (builder.env or the caller) over the registry default — the same
# precedence env.sh gives every A770B_<PROFILE>_* variable.
CARD=$(python3 "$A770B_PROJECT/harness/profiles.py" card --file "$A770B_PROFILES_FILE" --name "$PROFILE") \
  || { echo "⛔ profiles.py card failed for $PROFILE (registry: $A770B_PROFILES_FILE)" >&2; exit 2; }
CARD_FLASH_ATTENTION=$(printf '%s' "$CARD" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("flash_attention",""))')
CARD_EXTRA=$(printf '%s' "$CARD" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("extra",""))')

MODEL_NAME=$(a770b_profile_var "$PROFILE" MODEL)
MODEL=$(a770b_model_path "$MODEL_NAME")
KV=$(a770b_profile_var "$PROFILE" KV)
KV_V=$(a770b_profile_var "$PROFILE" KV_V); KV_V="${KV_V:-$KV}"
# -fa: A770B_<P>_FA if env.sh (or builder.env, or the caller) prints one, else the registry's own flash_attention
FA=$(a770b_profile_var "$PROFILE" FA); FA="${FA:-$CARD_FLASH_ATTENTION}"

BENCH_BIN="${A770B_LLAMA_BENCH:-$(dirname "$A770B_LLAMA_BIN")/llama-bench}"

# a MoE row's --n-cpu-moe N lives in its own `extra` string (the same one serve_a770_llamacpp.sh appends verbatim)
NCMOE=""
if [[ "$CARD_EXTRA" =~ (^|[[:space:]])(-ncmoe|--n-cpu-moe)[[:space:]]+([0-9]+) ]]; then NCMOE="${BASH_REMATCH[3]}"; fi

ARGS=(-m "$MODEL" -ngl 99 -fa "$FA" -ctk "$KV" -ctv "$KV_V" -b "$A770B_BATCH" -ub "$A770B_UBATCH" \
      --load-mode none -p "$PROMPT" -n "$GEN" -d "$DEPTHS" -r "$REPS" -o json)
[ -n "$NCMOE" ] && ARGS+=(--n-cpu-moe "$NCMOE")

# printed and run identically, so the printed command is what a reader can actually reproduce. Only the two paths
# are quoted (single-quoted, shell-safe); every other argument is a plain number or word, never touched by %q's
# habit of backslash-escaping harmless characters (a comma in -d 0,8192,32768 included) into noise.
sq(){ printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
CMD_DISPLAY="MESA_VK_DEVICE_SELECT=$A770B_VK_DEVICE_SELECT GGML_VK_DISABLE_COOPMAT=$GGML_VK_DISABLE_COOPMAT $(sq "$BENCH_BIN") -m $(sq "$MODEL") -ngl 99 -fa $FA -ctk $KV -ctv $KV_V -b $A770B_BATCH -ub $A770B_UBATCH --load-mode none -p $PROMPT -n $GEN -d $DEPTHS -r $REPS -o json"
[ -n "$NCMOE" ] && CMD_DISPLAY="$CMD_DISPLAY --n-cpu-moe $NCMOE"

if [ "$DRY_RUN" = 1 ]; then
  echo "$CMD_DISPLAY"
  exit 0
fi

# only past this point does the script touch the pidfile or the GPU — a dry run never refuses
if pid=$(llama_pid_alive "$A770B_DATA/logs/llamacpp-a770.pid"); then
  echo "⛔ the harness's server is up (pid $pid) — one GPU process on the card; stop it first: harness/serve_a770_llamacpp.sh stop" >&2
  exit 2
fi
[ -x "$BENCH_BIN" ] || { echo "⛔ llama-bench not found or not executable: $BENCH_BIN (set A770B_LLAMA_BENCH)" >&2; exit 2; }
[ -r "$MODEL" ] || { echo "⛔ model not readable: $MODEL (A770B_MODELS=$A770B_MODELS)" >&2; exit 2; }

echo "▶ $CMD_DISPLAY"
# The fallbacks only. llama-bench --version prints its build line and exits non-zero on this build; under set -e
# that would end the script before the benchmark ever ran, so the failure is tolerated and the git describe after
# it names the build instead. On this build that --version line is the graphics driver's platform warning, not a
# build at all, so the benchmark's own raw JSON (build_number/build_commit, read below) is preferred over both.
BUILD_ID=$(MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" "$BENCH_BIN" --version 2>&1 | head -1 || true)
[ -n "$BUILD_ID" ] || BUILD_ID=$(git -C "$(dirname "$BENCH_BIN")" describe --always --dirty 2>/dev/null || echo unknown)

RAW_FILE=$(mktemp)
trap 'rm -f "$RAW_FILE"' EXIT
MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" GGML_VK_DISABLE_COOPMAT="$GGML_VK_DISABLE_COOPMAT" \
  "$BENCH_BIN" "${ARGS[@]}" > "$RAW_FILE"

# the build that actually ran the benchmark, from the measurement's own rows (build_number/build_commit); the
# --version line and the git describe above stay as its fallbacks. The decision lives in harness/bench_build_id.py
# so a test can drive it with a synthetic raw file, without the card; it prints a line whatever it is handed.
BUILD_ID=$(python3 "$(dirname "$0")/bench_build_id.py" "$RAW_FILE" "$BUILD_ID" 2>/dev/null || printf '%s\n' "$BUILD_ID")

mkdir -p "$A770B_DATA/results"
OUT="$A770B_DATA/results/${PROFILE}-bench-$(date +%Y%m%d-%H%M%S).json"
FLAGS_STR=$(printf '%q ' "${ARGS[@]}"); FLAGS_STR="${FLAGS_STR% }"

python3 - "$OUT" "$PROFILE" "$MODEL" "$BUILD_ID" "$FLAGS_STR" "$PROMPT" "$GEN" "$DEPTHS" "$RAW_FILE" <<'PY'
import json, sys

out, profile, model, build_id, flags, prompt, gen, depths, raw_file = sys.argv[1:10]
prompt, gen = int(prompt), int(gen)
with open(raw_file) as f:
    raw = json.load(f)
rows = raw if isinstance(raw, list) else raw.get("results", [])

doc = {"profile": profile, "model": model, "build": build_id, "flags": flags, "raw": raw}
with open(out, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")

def field(row, *names):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None

for d in depths.split(","):
    depth = int(d)
    pp = tg = None
    for row in rows:
        row_depth = field(row, "n_depth", "d") or 0
        if int(row_depth) != depth:
            continue
        n_prompt = int(field(row, "n_prompt", "p") or 0)
        n_gen = int(field(row, "n_gen", "n") or 0)
        ts = field(row, "avg_ts", "t/s")
        if n_prompt == prompt and n_gen == 0 and ts is not None:
            pp = ts
        elif n_gen == gen and n_prompt == 0 and ts is not None:
            tg = ts
    pp_str = f"{pp:.1f}" if pp is not None else "-"
    tg_str = f"{tg:.1f}" if tg is not None else "-"
    print(f"depth {depth}: pp{prompt} {pp_str} tok/s · tg{gen} {tg_str} tok/s")
PY

echo "wrote $OUT"
