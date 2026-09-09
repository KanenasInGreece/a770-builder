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
# Writes ONE JSON, $A770B_DATA/results/<label>-ladder-<date>.json, holding every rung's numbers (whatever ran,
# including a rung that failed or was skipped), then prints a REGISTRY ROW: useful_ctx computed from the ctx_sweep
# 8k/far-end decode readings by extending time-per-token linearly between them and capping by the depth probe's
# last passing depth (AGENTS.md's "four-tokens-a-second rule"); speed.decode_tps/prefill_tps filled from
# speed.bench.at_depth by depth (kit/PROFILE.md §2); vram_gib_after_load, ram_gb_extra (the host MemAvailable drop
# across the load), suite (harness/suite_report.py --json on the task rung's results file) and instrument
# (SUITE-1@<VERSION>) filled in; use_for, fit.write and capability left as clearly marked placeholders — those are
# never derived from a measurement, they are written by hand from what the ladder showed (kit/PROFILE.md §2) — and
# a closing note lists the identity fields (source, family, architecture, quant, category, weight_class, params_b,
# measured_on,
# capability_source, sampling, task_t1) the ladder never fills, transcribed once by hand from the GGUF's own
# metadata and the model's public card.
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
SEAT="${SEAT:-$A770B_DATA/kit-seat}"
DATE=$(date +%Y%m%d-%H%M%S)
OUT="$A770B_DATA/results/${NAME}-ladder-${DATE}.json"

# the far end: 100000 unless the served window itself ends sooner (never sweep or probe past what the row can hold)
FAR_END=100000
[ "$CTX" -lt "$FAR_END" ] 2>/dev/null && FAR_END="$CTX"
DEPTHS="0,8192,32768"
case ",$DEPTHS," in *",$FAR_END,"*) ;; *) DEPTHS="$DEPTHS,$FAR_END" ;; esac

if [ "$DRYRUN" = 1 ]; then
  FRESH_FLAG=""; [ "$FRESH" = 1 ] && FRESH_FLAG=" --fresh"
  echo "[dry-run] ladder for ${PROFILE_NAME:-$GGUF} — ctx=$CTX kv=$KV/$KV_V extra='$EXTRA' reasoning=$REAS timeout=${TIMEOUT}s far_end=$FAR_END"
  echo "[dry-run] rung 1/6: load — MemAvailable sampled before and after (ram_gb_extra)"
  echo "[dry-run] rung 2/6: KV_K=$KV KV_V=$KV_V REASONING=$REAS bash harness/bench_model.sh $NAME $GGUF $CTX $EXTRA"
  if [ -n "$PROFILE_NAME" ]; then
    echo "[dry-run] rung 3/6: bash harness/serve_a770_llamacpp.sh stop; bash harness/bench_speed.sh $PROFILE_NAME --depths $DEPTHS"
  else
    echo "[dry-run] rung 3/6: SKIPPED — harness/bench_speed.sh needs a registry profile ('harness/profiles.py card --name <profile>' has no ephemeral form); $GGUF has no row yet"
  fi
  echo "[dry-run] rung 4/6: bash harness/serve_a770_llamacpp.sh stop; KV_K=$KV KV_V=$KV_V REASONING=$REAS bash harness/serve_a770_llamacpp.sh start $GGUF $CTX $EXTRA; bash harness/ctx_sweep.sh 8000 $FAR_END"
  echo "[dry-run] rung 5/6: bash harness/depth_probe.sh $FAR_END  (now graded: PASS/FAIL per question, exit 0 iff >=2/3)"
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
  bash "$here/serve_a770_llamacpp.sh" stop >/dev/null 2>&1 || true
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
  bash "$here/serve_a770_llamacpp.sh" stop >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  if ! KV_K="$KV" KV_V="$KV_V" REASONING="$REAS" bash "$here/serve_a770_llamacpp.sh" start "$GGUF" "$CTX" $EXTRA; then
    FAIL_RUNG="window-serve"; FAIL_MSG="the server did not come up for the window rung"
  else
    CTX_SWEEP_LOG="$LOGDIR/ladder-ctx-sweep-$$.log"
    ( bash "$here/ctx_sweep.sh" 8000 "$FAR_END" 2>&1 | tee "$CTX_SWEEP_LOG" ); rc=${PIPESTATUS[0]:-$?}
    [ "$rc" = 0 ] || { FAIL_RUNG="window"; FAIL_MSG="ctx_sweep.sh failed (exit $rc) — see $CTX_SWEEP_LOG"; }
  fi
fi

# ── rung 5: the depth probe, once per model and window, now graded — server still up from rung 4 ───────────────
DEPTH_LOG=""
if [ -z "$FAIL_RUNG" ]; then
  echo "▶ rung 5/6 — depth probe at $FAR_END (graded: PASS/FAIL per question)"
  DEPTH_LOG="$LOGDIR/ladder-depth-probe-$$.log"
  bash "$here/depth_probe.sh" "$FAR_END" 2>&1 | tee "$DEPTH_LOG"; rc=${PIPESTATUS[0]}
  [ "$rc" = 0 ] || { FAIL_RUNG="depth"; FAIL_MSG="the depth probe answered fewer than two of three correctly — see $DEPTH_LOG"; }
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
  [ "$FRESH" = 1 ] && RS_ARGS+=(--fresh)
  RS_LOG="$LOGDIR/ladder-run-suite-$$.log"
  bash "$here/run_suite.sh" "${RS_ARGS[@]}" 2>&1 | tee "$RS_LOG"; rc=${PIPESTATUS[0]}
  SUITE_RESULTS_JSON=$(grep -oE '[^ ]+-suite-[0-9]{8}-[0-9]{6}-[0-9]+\.json' "$RS_LOG" | tail -1)
  [ "$rc" = 0 ] || { FAIL_RUNG="task"; FAIL_MSG="run_suite.sh exited $rc — see $RS_LOG"; }
fi

SUITE_JSON=""
if [ -n "$SUITE_RESULTS_JSON" ] && [ -f "$SUITE_RESULTS_JSON" ]; then
  SUITE_JSON=$(python3 "$here/suite_report.py" "$SUITE_RESULTS_JSON" --json 2>/dev/null || true)
fi

VERSION_STR=$(head -1 "$A770B_PROJECT/VERSION" 2>/dev/null || echo unknown)
INSTRUMENT="SUITE-1@$VERSION_STR"

python3 - "$OUT" "$NAME" "$GGUF" "$CTX" "$KV" "$KV_V" "$REAS" "$EXTRA" "$TIMEOUT" "${PROFILE_NAME:-}" \
  "$INSTRUMENT" "$FAR_END" "${avail_before:-}" "${avail_after:-}" \
  "$BENCH_MODEL_JSON" "${BENCH_SPEED_JSON:-}" "${CTX_SWEEP_LOG:-}" "${DEPTH_LOG:-}" \
  "${SUITE_JSON:-}" "${SUITE_RESULTS_JSON:-}" "${FAIL_RUNG:-}" "${FAIL_MSG:-}" <<'PY'
import json, os, re, sys

(out_path, name, gguf, ctx_s, kv, kv_v, reasoning, extra, timeout_s, profile_name,
 instrument, far_end_s, avail_before_s, avail_after_s,
 bench_model_path, bench_speed_path, ctx_sweep_log, depth_log,
 suite_json_s, suite_results_path, fail_rung, fail_msg) = sys.argv[1:23]

ctx = int(ctx_s)
far_end = int(far_end_s)


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def read_text(path):
    if not path or not os.path.isfile(path):
        return ""
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


bench_model = load_json(bench_model_path)
bench_speed = load_json(bench_speed_path) if bench_speed_path else None

# ram_gb_extra: the host MemAvailable drop across the load (rung 1), in GB
ram_gb_extra = None
try:
    ram_gb_extra = round((int(avail_before_s) - int(avail_after_s)) / 1024.0, 2)
except (ValueError, TypeError):
    pass

vram_gib_after_load = (bench_model or {}).get("vram_gib_after_load")

# ── ctx_sweep.sh's own table: "target tokens ttft_s prefill_tps decode_tps vram_max resets" ────────────────────
sweep_points = {}
sweep_text = read_text(ctx_sweep_log)
for m in re.finditer(
    r'^(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)GiB\s+(-?\d+)\s*$', sweep_text, re.MULTILINE
):
    target, toks, ttft, pp, tg, vmax, resets = m.groups()
    sweep_points[int(target)] = {
        "tokens": int(toks), "ttft_s": float(ttft), "prefill_tps": float(pp),
        "decode_tps": float(tg), "vram_max_gib": float(vmax), "resets": int(resets),
    }
p8, pfar = sweep_points.get(8000), sweep_points.get(far_end)

# ── the depth probe's own verdict line: "depth probe at <tokens>: <n>/3" ────────────────────────────────────
depth_score = None
depth_text = read_text(depth_log)
m = re.search(r'depth probe at \d+: (\d)/3', depth_text)
if m:
    depth_score = int(m.group(1))
depth_pass = None if depth_score is None else depth_score >= 2

# ── useful_ctx: the four-tokens-a-second rule (AGENTS.md) — decode time-per-token at 8k and the far end,
# extended linearly between them, capped by the depth probe's last passing depth (8k when the far probe failed,
# since 8k is the only other point this ladder actually probed the model's quality at) ──────────────────────
useful_ctx = None
if p8 and pfar and p8["decode_tps"] > 0 and pfar["decode_tps"] > 0:
    t1, t2 = 1.0 / p8["decode_tps"], 1.0 / pfar["decode_tps"]
    if far_end == 8000 or t2 <= t1:
        useful_ctx = far_end if t2 <= 0.25 else 8000
    else:
        slope = (t2 - t1) / (far_end - 8000)
        d_star = 8000 + (0.25 - t1) / slope
        useful_ctx = max(0, int(d_star))
if depth_pass is False:
    cap = 8000
    useful_ctx = cap if useful_ctx is None else min(useful_ctx, cap)
if useful_ctx is not None:
    useful_ctx = max(0, min(useful_ctx, ctx))

# ── speed.bench: llama-bench's own at_depth, re-derived from bench_speed.sh's raw output the same way it built it
def bench_at_depth(bench):
    if not bench:
        return None
    raw = bench.get("raw")
    rows = raw if isinstance(raw, list) else (raw or {}).get("results", [])

    def field(row, *names):
        for n in names:
            if n in row and row[n] is not None:
                return row[n]
        return None

    depths = sorted({int(field(row, "n_depth", "d") or 0) for row in rows})
    at_depth = {}
    for d in depths:
        pp = tg = None
        for row in rows:
            if int(field(row, "n_depth", "d") or 0) != d:
                continue
            ts = field(row, "avg_ts", "t/s")
            if int(field(row, "n_gen", "n") or 0) == 0 and ts is not None:
                pp = ts
            elif int(field(row, "n_prompt", "p") or 0) == 0 and ts is not None:
                tg = ts
        at_depth[str(d)] = {"pp": pp, "tg": tg}
    return at_depth


speed = {
    "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
    "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
}
at_depth = bench_at_depth(bench_speed)
if at_depth:
    def pick(field_key, depth):
        v = at_depth.get(str(depth))
        return v.get(field_key) if v else None

    for reg_key, bench_field in (("decode_tps", "tg"), ("prefill_tps", "pp")):
        speed[reg_key]["8k"] = pick(bench_field, 8192)
        speed[reg_key]["32k"] = pick(bench_field, 32768)
        speed[reg_key]["64k"] = pick(bench_field, 65536)
        hundred_k_depth = far_end if far_end not in (8192, 32768, 65536) else 100000
        speed[reg_key]["100k"] = pick(bench_field, hundred_k_depth)

    m_flags = re.search(r'-p\s+(\d+)', bench_speed.get("flags") or "")
    n_flags = re.search(r'-n\s+(\d+)', bench_speed.get("flags") or "")
    bench_out = {
        "tool": bench_speed.get("build") or "unknown",
        "prompt": int(m_flags.group(1)) if m_flags else 0,
        "gen": int(n_flags.group(1)) if n_flags else 0,
        "flags": bench_speed.get("flags") or "",
        "at_depth": at_depth,
        "source": os.path.basename(bench_speed_path),
    }
    if bench_out["prompt"] > 0 and bench_out["gen"] > 0 and bench_out["at_depth"]:
        speed["bench"] = bench_out

if pfar:
    speed["far_end"] = {
        "tokens": pfar["tokens"], "decode_tps": pfar["decode_tps"],
        "prefill_tps": pfar["prefill_tps"], "ttft_s": pfar["ttft_s"],
    }

suite = json.loads(suite_json_s) if suite_json_s else None
if suite is not None and suite_results_path:
    suite["source"] = os.path.basename(suite_results_path)

flash_attention = "off" if re.search(r'(?:^|\s)-fa\s+off(?:\s|$)', extra) else "on"

# ── the ladder JSON: every rung's own numbers, whatever ran ─────────────────────────────────────────────────
ladder_doc = {
    "instrument": instrument,
    "generated": None,
    "input": {
        "profile": profile_name or None, "model": gguf, "ctx": ctx, "kv": kv, "kv_v": kv_v,
        "reasoning": reasoning, "extra": extra, "timeout_s": int(timeout_s) if timeout_s.isdigit() else timeout_s,
        "far_end": far_end,
    },
    "rungs": {
        "load_and_probes": bench_model,
        "standard_speed": bench_speed,
        "window_sweep": sweep_points or None,
        "depth_probe": {"log": depth_log or None, "score": depth_score, "pass": depth_pass},
        "task_suite": suite,
    },
    "computed": {
        "useful_ctx": useful_ctx, "ram_gb_extra": ram_gb_extra, "vram_gib_after_load": vram_gib_after_load,
        "speed": speed,
    },
    "stopped_at_rung": fail_rung or None,
    "failure": fail_msg or None,
}
import datetime
ladder_doc["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
with open(out_path, "w") as f:
    json.dump(ladder_doc, f, indent=2)
    f.write("\n")
print(f"\n▶ ladder results: {out_path}")

row = {
    "model": os.path.basename(gguf), "ctx": ctx, "kv": kv, "kv_v": kv_v,
    "flash_attention": flash_attention, "reasoning": reasoning, "extra": extra,
    "timeout_s": int(timeout_s) if timeout_s.isdigit() else timeout_s,
    "vram_gib_after_load": vram_gib_after_load, "ram_gb_extra": ram_gb_extra,
    "useful_ctx": useful_ctx, "speed": speed, "instrument": instrument,
    "use_for": "__TODO__ — write by hand from what this ladder run showed (kit/PROFILE.md: never derived from a measurement)",
    "fit": {
        "code": (
            f"SUITE-1: {suite['passed']}/{suite['runs']} stages passed"
            + (f", mean wall {suite['mean_wall_s']}s" if suite.get("mean_wall_s") is not None else "")
            + " (this ladder run)"
        ) if suite and suite.get("runs") else "not measured",
        "write": "__TODO__ — a reviewer-graded prose brief, or state 'not measured'",
    },
    "capability": "__TODO__ — the model's or the quantiser's own published evaluation, quoted verbatim, never re-measured here",
}
if suite is not None:
    row["suite"] = suite
if far_end >= 90000 and depth_score is not None:
    row["depth_probe_100k"] = f"{'pass' if depth_pass else 'fail'} ({depth_score}/3)"

print("\n── REGISTRY ROW — paste under profiles.<name> in config/profiles.json (or .inference.json) ──")
print(json.dumps(row, indent=2))
print(
    "\nNot filled above — transcribe once by hand from the GGUF's own metadata and the model's public card "
    "(kit/PROFILE.md §2, \"identity fields, not measurements\"): source, family, architecture, quant, category, "
    "weight_class, params_b, capability_source, sampling, and measured_on (the card and the serving build)."
)
if not (far_end >= 90000 and depth_score is not None):
    note = f"depth probe ran at {far_end} tokens (not ~100k)"
    if depth_score is not None:
        note += f": {depth_score}/3 ({'pass' if depth_pass else 'fail'})"
    print(f"depth_probe_100k: not filled — {note}; record it by hand if that is close enough to call the 100k rung.")
print("task_t1: not filled — superseded by suite for a row measured on the kit (kit/PROFILE.md §2).")
print("fit.think: not filled — a reasoning arm measured on this card, or else the model card's own GPQA/AIME figure, named as the card's.")

if fail_rung:
    print(f"\n⛔ ladder stopped at rung '{fail_rung}': {fail_msg}", file=sys.stderr)
    sys.exit(1)
PY
grc=$?
[ "$grc" = 0 ] && echo "✓ ladder complete" || echo "⛔ ladder incomplete — read the note above" >&2
exit "$grc"
