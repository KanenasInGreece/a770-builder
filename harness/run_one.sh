#!/usr/bin/env bash
# run_one.sh — one matrix row, unattended: probes → T1 coding task through opencode → capture. TESTING ONLY.
#   bash $A770B_DATA/A770_Builder/harness/run_one.sh <label> <gguf> <ctx> [extra llama-server args…]
# Env knobs: KV_K KV_V UBATCH VRAM_CAP_GIB (server), AGENTS_ASIDE=1 (set the repo's 25k-token AGENTS.md aside for the
# opencode run and restore it after), LEAN_AGENT=1 (opencode --agent local-builder), BUILD_TIMEOUT (s, default 1500).
# Leaves the server running for follow-up probes; the worktree is reset by capture_task.sh.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
set -uo pipefail
LABEL="${1:?label}"; GGUF="${2:?gguf}"; CTX="${3:?ctx}"; shift 3 || shift $#
# the seat is guarded HERE, before the AGENTS.md rename below touches it — not left to build_local.sh and
# capture_task.sh further down, which guard their own argument only after this script has already moved a file
# in the tree. A protected tree must lose nothing, not even a rename, before it is refused.
WT=$(guard_worktree "$A770B_SEAT") || exit 2; R=$A770B_DATA/results
echo "═══ ROW $LABEL — $(date -Is)"
if ! bash "$A770B_PROJECT/harness/bench_model.sh" "$LABEL" "$GGUF" "$CTX" "$@"; then echo "✗ bench failed for $LABEL"; bash "$A770B_PROJECT/harness/serve_a770_llamacpp.sh" stop; exit 1; fi
python3 -c "import json,sys; r=json.load(open('$R/$LABEL.json')); sys.exit(0 if r.get('quality_ok') else 1)" || { echo "✗ quality gate failed for $LABEL — no coding task"; exit 1; }
# coding task
[ "${AGENTS_ASIDE:-1}" = 1 ] && [ -f "$WT/AGENTS.md" ] && mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"
export LOCAL_TIMEOUT="${BUILD_TIMEOUT:-1500}" PROFILE="${PROFILE:-fast}"
if [ "${LEAN_AGENT:-1}" = 1 ]; then export LOCAL_AGENT=local-builder; fi
t0=$(date +%s)
bash "$A770B_PROJECT/harness/build_local.sh" "$WT" "$A770B_TASK_BRIEF" 2>&1 | grep -vE '^\s*$' | tail -12 | cut -c1-240
t1=$(date +%s)
[ -f "$WT/AGENTS.md.local-off" ] && mv "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"
BL=$(cat "$A770B_DATA/logs/last-build.log.path")
echo "build wall=$((t1-t0))s log=$BL"
bash "$A770B_PROJECT/harness/capture_task.sh" "$LABEL" "$WT" "$BL" 2>&1 | tail -2
python3 - "$R/$LABEL.json" "$R/$LABEL.task.md" "$((t1-t0))" <<'PY'
import json,sys,re
j,t,wall=sys.argv[1],sys.argv[2],int(sys.argv[3]); r=json.load(open(j)); md=open(t,errors='ignore').read()
r['task']={'wall_s':wall,'test_file_written':'NO TEST FILE WRITTEN' not in md,
 'pytest_line':next((l for l in md.splitlines() if re.search(r'\d+ (passed|failed|error)',l)),None),
 'server_timings':[l for l in md.splitlines() if l.startswith(('requests=','TTFT','TPOT','prefill'))]}
json.dump(r,open(j,'w'),indent=1); print("TASK:",json.dumps(r['task']))
PY
echo "═══ ROW $LABEL done — $(date -Is)"
