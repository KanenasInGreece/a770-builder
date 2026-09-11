#!/usr/bin/env bash
# ladder.sh — the one command a stranger runs to produce a registry row. Runs the rungs AGENTS.md's "The ladder"
# names, in the order it names them, stopping at the first failure (a later rung is wasted on a model that failed
# an earlier one):
#   1. load and VRAM at the target window  — harness/serve_a770_llamacpp.sh, wrapped inside harness/bench_model.sh
#   2. the probes and the 17k summary       — harness/bench_model.sh <label> <gguf> <ctx> [flags]
#   3. the standard speed rung              — harness/bench_speed.sh <profile>  (llama-bench, the row's own flags)
#   4. the window rung                      — harness/ctx_sweep.sh 8000 <far end>  (VRAM/kernel-log safety read;
#                                              still the source useful_ctx's linear extension is drawn from)
#   5. the depth probe, now graded          — harness/depth_probe.sh <far end>  (caps useful_ctx where it fails)
#   6. the task rung                        — harness/run_suite.sh <profile>  (or --model/--ctx — see below)
# harness/cancel_repro.sh (AGENTS.md's rung 6, "the cancel reproduction") is a one-off, once-per-model check an
# operator runs by hand; it is not part of this ladder.
#
#   ladder.sh <profile-or-gguf> [--ctx N] [--kv f16|q8_0|q4_0] [--kv-v f16|q8_0|q4_0] [--extra "<flags>"]
#             [--timeout S] [--seat <path>] [--suite <suite.json>] [--reviewer <profile>] [--fresh] [--dry-run]
#
# <profile-or-gguf> is either a name already in the registry (config/profiles.json / .inference.json — every knob
# comes from the row) or a bare GGUF (in A770B_MODELS) or an absolute path — a model with NO row yet, the case the
# ladder exists for ("no script computes useful_ctx, grades the depth probe, fills speed.bench, or writes a
# registry row" — an adversarial review). For a GGUF, --ctx is required; --kv/--kv-v/--extra/--timeout default the
# way harness/run_suite.sh's own --model flow does (q8_0/q8_0/none/1500s). Rung 3 (bench_speed.sh) needs a REGISTRY
# profile — its `harness/profiles.py card --name <profile>` lookup has no ephemeral form — so for a bare GGUF it is
# SKIPPED with a clear note; every other rung, task rung included (harness/run_suite.sh --model <gguf> --ctx <n>,
# the very capability this project added so a new GGUF can climb the task rung before it has a row), still runs.
#
# The last step is harness/ladder_row.py — a file rather than a heredoc, so a test can drive it and an operator can
# re-run it over the artefacts a terminated run left behind. It writes ONE JSON,
# $A770B_DATA/results/<label>-ladder-<date>.json, holding every rung's numbers (whatever ran,
# including a rung that failed or was skipped), then prints a REGISTRY ROW: useful_ctx computed from the ctx_sweep
# 8k/far-end decode readings by extending time-per-token linearly between them and capping by the depth probe's
# last passing depth (AGENTS.md's "four-tokens-a-second rule"); speed.decode_tps/prefill_tps filled from
# speed.bench.at_depth by depth (kit/PROFILE.md §2); vram_gib_after_load, ram_gb_extra (the host MemAvailable drop
# across the load), suite (harness/suite_report.py --json on the task rung's results file) and instrument
# (SUITE-1@<kit_version>) filled in; use_for, fit.write, capability and measured_on left as clearly marked placeholders
# IN THE ROW ITSELF (measured_on is a required field of the schema, so it is printed as __TODO__ rather than
# omitted, unlike the identity fields below) — none of these are ever derived from a measurement, they are written
# by hand from what the ladder showed (kit/PROFILE.md §2) — and a closing note lists the identity fields (source,
# family, architecture, quant, category, weight_class, params_b, capability_source, sampling, task_t1) the ladder
# never fills at all, transcribed once by hand from the GGUF's own metadata and the model's public card.
# --dry-run prints every rung's command and writes nothing.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/env.sh"; . "$here/guard.sh"
die(){ echo "⛔ $*" >&2; exit 2; }

[ $# -ge 1 ] || die "usage: ladder.sh <profile-or-gguf> [--ctx N] [--kv f16|q8_0|q4_0] [--kv-v f16|q8_0|q4_0] [--extra \"<flags>\"] [--timeout S] [--seat <path>] [--suite <suite.json>] [--reviewer <profile>] [--fresh] [--dry-run]"
SPEC="$1"; shift
CTX_ARG=""; KV_ARG=""; KV_V_ARG=""; EXTRA_ARG=""; TIMEOUT_ARG=""; SEAT=""; SUITE=""; REVIEWER=""; FRESH=0; DRYRUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ctx) CTX_ARG="${2:?window}"; shift 2 ;;
    --kv) KV_ARG="${2:?kv cache type}"; shift 2 ;;
    --kv-v) KV_V_ARG="${2:?kv_v cache type}"; shift 2 ;;
    --extra) EXTRA_ARG="${2:?extra llama-server flags}"; shift 2 ;;
    --timeout) TIMEOUT_ARG="${2:?seconds}"; shift 2 ;;
    --seat) SEAT="${2:?path}"; shift 2 ;;
    --suite) SUITE="${2:?path}"; shift 2 ;;
    --reviewer) REVIEWER="${2:?reviewer profile name}"; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    --dry-run) DRYRUN=1; shift ;;
    *) die "unknown arg $1" ;;
  esac
done

PROFILE_NAME=""
if a770b_is_profile "$SPEC"; then
  PROFILE_NAME="$SPEC"
  [ -z "$CTX_ARG" ] && [ -z "$KV_ARG" ] && [ -z "$KV_V_ARG" ] && [ -z "$EXTRA_ARG" ] \
    || die "refusing: --ctx/--kv/--kv-v/--extra override a registry profile's own served values — pass a bare GGUF instead of a profile name to set them"
  GGUF=$(a770b_model_path "$(a770b_profile_var "$PROFILE_NAME" MODEL)")
  CTX=$(a770b_profile_var "$PROFILE_NAME" CTX)
  KV=$(a770b_profile_var "$PROFILE_NAME" KV)
  KV_V=$(a770b_profile_var "$PROFILE_NAME" KV_V); KV_V="${KV_V:-$KV}"
  EXTRA=$(a770b_profile_var "$PROFILE_NAME" EXTRA)
  REAS=$(a770b_profile_var "$PROFILE_NAME" REASONING); REAS="${REAS:-off}"
  TIMEOUT="${TIMEOUT_ARG:-$(a770b_profile_var "$PROFILE_NAME" TIMEOUT)}"
  NAME="$PROFILE_NAME"
else
  GGUF=$(a770b_model_path "$SPEC")
  [ -n "$CTX_ARG" ] || die "--ctx N is required for a GGUF with no registry row ('$SPEC' is not one of: $A770B_PROFILES)"
  CTX="$CTX_ARG"; KV="${KV_ARG:-q8_0}"; KV_V="${KV_V_ARG:-$KV}"; EXTRA="${EXTRA_ARG:-}"; REAS="off"
  TIMEOUT="${TIMEOUT_ARG:-1500}"
  NAME=$(basename "$GGUF"); NAME="${NAME%.gguf}"
fi
SEAT_IS_OURS=0; [ -n "$SEAT" ] || SEAT_IS_OURS=1
SEAT="${SEAT:-$A770B_DATA/kit-seat}"
# a seat is a path, never a flag: '--seat --fresh' would otherwise make the seat the string '--fresh'
case "$SEAT" in -*) die "the seat path looks like an option ('$SEAT') — pass --seat <path>";; esac
# and the data directory a seat is replaced under has to be a real one, never empty and never the root
{ [ -n "$A770B_DATA" ] && [ "$A770B_DATA" != "/" ]; } || die "A770B_DATA is not a directory a seat may be replaced under: '$A770B_DATA'"
DATE=$(date +%Y%m%d-%H%M%S)
OUT="$A770B_DATA/results/${NAME}-ladder-${DATE}.json"

# the far end: 100000 unless the served window itself ends sooner (never sweep or probe past what the row can hold).
# The window has to hold the reply and the chat template as well as the prompt, so leave room for them: a probe sized
# to exactly CTX asks the server for a request it can only turn down.
FAR_END=100000
FAR_ROOM=$((CTX - 1024))
[ "$FAR_ROOM" -lt "$FAR_END" ] 2>/dev/null && FAR_END="$FAR_ROOM"
[ "$FAR_END" -gt 8000 ] || die "the served window ($CTX) leaves a far end of $FAR_END tokens, at or below the sweep's 8000-token shallow point — this ladder needs a window above about 9,024 tokens"
DEPTHS="0,8192,32768"
case ",$DEPTHS," in *",$FAR_END,"*) ;; *) DEPTHS="$DEPTHS,$FAR_END" ;; esac

if [ "$DRYRUN" = 1 ]; then
  FRESH_FLAG=""; { [ "$FRESH" = 1 ] || [ "$SEAT_IS_OURS" = 1 ]; } && FRESH_FLAG=" --fresh"
  echo "[dry-run] ladder for ${PROFILE_NAME:-$GGUF} — ctx=$CTX kv=$KV/$KV_V extra='$EXTRA' reasoning=$REAS timeout=${TIMEOUT}s far_end=$FAR_END"
  echo "[dry-run] rung 1/6: load — MemAvailable sampled before and after (ram_gb_extra)"
  echo "[dry-run] rung 2/6: KV_K=$KV KV_V=$KV_V REASONING=$REAS bash harness/bench_model.sh $NAME $GGUF $CTX $EXTRA"
  if [ -n "$PROFILE_NAME" ]; then
    echo "[dry-run] rung 3/6: bash harness/$(basename "$A770B_SERVE_SCRIPT") stop; bash harness/bench_speed.sh $PROFILE_NAME --depths $DEPTHS"
  else
    echo "[dry-run] rung 3/6: SKIPPED — harness/bench_speed.sh needs a registry profile ('harness/profiles.py card --name <profile>' has no ephemeral form); $GGUF has no row yet"
  fi
  echo "[dry-run] rung 4/6: bash harness/$(basename "$A770B_SERVE_SCRIPT") stop; KV_K=$KV KV_V=$KV_V REASONING=$REAS bash harness/$(basename "$A770B_SERVE_SCRIPT") start $GGUF $CTX $EXTRA; bash harness/ctx_sweep.sh --bench <rung 3's file> 8000 $FAR_END"
  echo "[dry-run]           both are sized in real tokens by the server's tokeniser, and their deadlines come from rung 3's measured curve (harness/prompt_budget.py)"
  echo "[dry-run] rung 5/6: bash harness/depth_probe.sh $FAR_END --bench <rung 3's file>  (graded: PASS/FAIL per question, exit 0 iff >=2/3, exit 3 iff the server never answered)"
  if [ -n "$PROFILE_NAME" ]; then
    echo "[dry-run] rung 6/6: bash harness/run_suite.sh $PROFILE_NAME $SEAT${SUITE:+ --suite $SUITE}${REVIEWER:+ --reviewer $REVIEWER}$FRESH_FLAG"
  else
    echo "[dry-run] rung 6/6: bash harness/run_suite.sh --model $GGUF --ctx $CTX --kv $KV --kv-v $KV_V --extra '$EXTRA' --timeout $TIMEOUT $SEAT${SUITE:+ --suite $SUITE}${REVIEWER:+ --reviewer $REVIEWER}$FRESH_FLAG"
  fi
  echo "[dry-run] writes: $OUT, then prints a REGISTRY ROW to paste (useful_ctx, speed, vram_gib_after_load, ram_gb_extra, suite, instrument computed; use_for/fit.write/capability left as placeholders)"
  echo "[dry-run] nothing written"
  exit 0
fi

mkdir -p "$A770B_DATA/results" "$A770B_DATA/logs"
LOGDIR="$A770B_DATA/logs"
FAIL_RUNG=""; FAIL_MSG=""
echo "═══ ladder: ${PROFILE_NAME:-$GGUF} — ctx $CTX kv $KV/$KV_V — $(date -Is)"

# ── rung 1+2: load and VRAM at the window, the probes and the 17k summary ──────────────────────────────────────
avail_before=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo "")
echo "▶ rung 1-2/6 — load, VRAM, probes, 17k summary (bench_model.sh)"
# shellcheck disable=SC2086  # EXTRA is a deliberate word list, expanded unquoted, exactly as local-build.sh's serve() does
if ! KV_K="$KV" KV_V="$KV_V" REASONING="$REAS" bash "$here/bench_model.sh" "$NAME" "$GGUF" "$CTX" $EXTRA; then
  FAIL_RUNG="load+probes"; FAIL_MSG="bench_model.sh failed — read $LOGDIR/llamacpp-a770.log"
fi
avail_after=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo "")
BENCH_MODEL_JSON="$A770B_DATA/results/$NAME.json"

# ── rung 3: the standard speed rung (llama-bench, the row's own flags) — needs the server DOWN ─────────────────
BENCH_SPEED_JSON=""
if [ -z "$FAIL_RUNG" ]; then
  echo "▶ rung 3/6 — standard speed (bench_speed.sh)"
  bash "$A770B_SERVE_SCRIPT" stop >/dev/null 2>&1 || true
  if [ -n "$PROFILE_NAME" ]; then
    SPEED_LOG="$LOGDIR/ladder-bench-speed-$$.log"
    if bash "$here/bench_speed.sh" "$PROFILE_NAME" --depths "$DEPTHS" 2>&1 | tee "$SPEED_LOG"; then
      BENCH_SPEED_JSON=$(grep -oE '[^ ]+-bench-[0-9]{8}-[0-9]{6}\.json' "$SPEED_LOG" | tail -1)
    else
      FAIL_RUNG="speed"; FAIL_MSG="bench_speed.sh failed — see $SPEED_LOG"
    fi
  else
    echo "⚠ skipping: bash harness/bench_speed.sh requires a registry profile name, and $GGUF has none yet — its speed.bench and the registry's decode_tps/prefill_tps four keys will be empty in this row"
  fi
fi

# ── rung 4: the window rung (ctx_sweep.sh) — needs the server UP, at the intended window ────────────────────────
CTX_SWEEP_LOG=""
if [ -z "$FAIL_RUNG" ]; then
  echo "▶ rung 4/6 — window (ctx_sweep.sh at 8000 and $FAR_END)"
  bash "$A770B_SERVE_SCRIPT" stop >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  if ! KV_K="$KV" KV_V="$KV_V" REASONING="$REAS" bash "$A770B_SERVE_SCRIPT" start "$GGUF" "$CTX" $EXTRA; then
    FAIL_RUNG="window-serve"; FAIL_MSG="the server did not come up for the window rung"
  else
    CTX_SWEEP_LOG="$LOGDIR/ladder-ctx-sweep-$$.log"
    # rung 3 has already measured how prefill slows with depth for this model on this card; the sweep sets each
    # point's deadline from that curve instead of from a constant that fits no model (harness/prompt_budget.py)
    SWEEP_ARGS=(); [ -n "$BENCH_SPEED_JSON" ] && SWEEP_ARGS=(--bench "$BENCH_SPEED_JSON")
    ( bash "$here/ctx_sweep.sh" "${SWEEP_ARGS[@]}" 8000 "$FAR_END" 2>&1 | tee "$CTX_SWEEP_LOG" ); rc=${PIPESTATUS[0]:-$?}
    [ "$rc" = 0 ] || { FAIL_RUNG="window"; FAIL_MSG="ctx_sweep.sh failed (exit $rc) — see $CTX_SWEEP_LOG"; }
  fi
fi

# ── rung 5: the depth probe, once per model and window, now graded — server still up from rung 4 ───────────────
DEPTH_LOG=""
if [ -z "$FAIL_RUNG" ]; then
  echo "▶ rung 5/6 — depth probe at $FAR_END (graded: PASS/FAIL per question)"
  DEPTH_LOG="$LOGDIR/ladder-depth-probe-$$.log"
  PROBE_ARGS=(); [ -n "$BENCH_SPEED_JSON" ] && PROBE_ARGS=(--bench "$BENCH_SPEED_JSON")
  bash "$here/depth_probe.sh" "$FAR_END" "${PROBE_ARGS[@]}" 2>&1 | tee "$DEPTH_LOG"; rc=${PIPESTATUS[0]}
  case "$rc" in
    0) ;;
    1) FAIL_RUNG="depth"; FAIL_MSG="the depth probe answered fewer than two of three correctly — see $DEPTH_LOG" ;;
    3) FAIL_RUNG="depth-no-answer"
       FAIL_MSG="the server never answered the depth probe — the prompt was turned down, or the deadline cut it off. The model was never asked, so this says nothing about its quality at depth; see $DEPTH_LOG" ;;
    *) FAIL_RUNG="depth-setup"; FAIL_MSG="the depth probe could not be set up (exit $rc) — see $DEPTH_LOG" ;;
  esac
fi

# ── rung 6: the task, under the model's own card line ────────────────────────────────────────────────────────
SUITE_RESULTS_JSON=""
if [ -z "$FAIL_RUNG" ]; then
  echo "▶ rung 6/6 — task (run_suite.sh)"
  RS_ARGS=()
  if [ -n "$PROFILE_NAME" ]; then RS_ARGS=("$PROFILE_NAME" "$SEAT")
  else RS_ARGS=(--model "$GGUF" --ctx "$CTX" --kv "$KV" --kv-v "$KV_V" --extra "$EXTRA" --timeout "$TIMEOUT" "$SEAT"); fi
  [ -n "$SUITE" ] && RS_ARGS+=(--suite "$SUITE")
  [ -n "$REVIEWER" ] && RS_ARGS+=(--reviewer "$REVIEWER")
  # a ladder run owns the kit's own scratch seat and replaces it without being asked: a leftover from an earlier
  # run stopped the task rung of all three profiles on 2026-09-09, in under a second, before any model work. A seat
  # the operator named by hand keeps run_suite.sh's guard.
  { [ "$FRESH" = 1 ] || [ "$SEAT_IS_OURS" = 1 ]; } && RS_ARGS+=(--fresh)
  RS_LOG="$LOGDIR/ladder-run-suite-$$.log"
  bash "$here/run_suite.sh" "${RS_ARGS[@]}" 2>&1 | tee "$RS_LOG"; rc=${PIPESTATUS[0]}
  SUITE_RESULTS_JSON=$(grep -oE '[^ ]+-suite-[0-9]{8}-[0-9]{6}-[0-9]+\.json' "$RS_LOG" | tail -1)
  [ "$rc" = 0 ] || { FAIL_RUNG="task"; FAIL_MSG="run_suite.sh exited $rc — see $RS_LOG"; }
fi

SUITE_JSON=""
if [ -n "$SUITE_RESULTS_JSON" ] && [ -f "$SUITE_RESULTS_JSON" ]; then
  SUITE_JSON=$(python3 "$here/suite_report.py" "$SUITE_RESULTS_JSON" --json 2>/dev/null || true)
fi

INSTRUMENT=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d.get("kit_version"); s=d.get("suite") or "SUITE-1";
raise SystemExit("kit_version missing") if not (isinstance(v,str) and v) else None
print("%s@%s" % (s, v))' "${SUITE:-$A770B_PROJECT/kit/suite.json}") || { echo "⛔ kit_version missing from suite.json" >&2; exit 1; }

# ── the last step: read every rung's own output, compute what no single rung produces (useful_ctx by the
# four-tokens-a-second rule, ram_gb_extra, the speed table), write the ONE ladder results JSON to $OUT, and print
# the REGISTRY ROW to paste. It lives in harness/ladder_row.py, not in a heredoc here, so it can be called: a test
# drives it with a real run's rung outputs, and an operator can re-run it by hand over the artefacts a terminated
# run left behind. Every argument below is this script's own, in this order; the last is the generation timestamp
# the results document is stamped with, which this ladder passes as the current time. Exits 1 if a rung failed.
python3 "$here/ladder_row.py" "$OUT" "$NAME" "$GGUF" "$CTX" "$KV" "$KV_V" "$REAS" "$EXTRA" "$TIMEOUT" "${PROFILE_NAME:-}" \
  "$INSTRUMENT" "$FAR_END" "${avail_before:-}" "${avail_after:-}" \
  "$BENCH_MODEL_JSON" "${BENCH_SPEED_JSON:-}" "${CTX_SWEEP_LOG:-}" "${DEPTH_LOG:-}" \
  "${SUITE_JSON:-}" "${SUITE_RESULTS_JSON:-}" "${FAIL_RUNG:-}" "${FAIL_MSG:-}" "$(date +%Y-%m-%dT%H:%M:%S)"
grc=$?
[ "$grc" = 0 ] && echo "✓ ladder complete" || echo "⛔ ladder incomplete — read the note above" >&2
exit "$grc"
