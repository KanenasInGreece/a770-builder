#!/usr/bin/env bash
# qualify.sh — run a qualification and return while it continues.
# start probes the card lock without holding it, then the runner takes the
# lock itself. Helpers close the lock descriptors so they cannot keep the card.
set -uo pipefail
umask 077
exec 7>&- 9>&- || true
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$here/env.sh" || exit 2
# shellcheck disable=SC1091
source "$here/guard.sh"
: "${A770B_DATA:?}"
: "${A770B_PROJECT:?}"
: "${A770B_MODELS:=}"
: "${A770B_HOST:=127.0.0.1}"
: "${A770B_PORT:=9}"
: "${A770B_API_KEY_FILE:=}"
: "${A770B_LLAMA_IMAGE:=}"
: "${A770B_VLLM_IMAGE:=}"
: "${A770B_SGLANG_IMAGE:=}"
: "${A770B_PROFILES_FILE:=}"
: "${A770B_CARD:=}"
: "${A770B_QUALIFY_PRE:=}"
: "${A770B_QUALIFY_POST:=}"
: "${A770B_QUALIFY_STUB:=}"
: "${A770B_QUALIFY_PAUSE_PHASE:=}"
: "${A770B_QUALIFY_HOLD_S:=}"
: "${A770B_QUALIFY_HEARTBEAT_S:=30}"
py=(python3 "$here/qualify_lib.py")

die() { printf '⛔ %s\n' "$*" >&2; exit 2; }

usage() {
  printf '%s\n' "usage: qualify.sh start --engine <llama|llama.cpp|vllm|sglang|strata> --model <name> [--profiles-file <file>] [--ctx N] [--backend vulkan|sycl] [--tokenizer <dir>] [--kv T] [--kv-v T] [--extra <flags>] [--reasoning on|off] [--reasoning-budget N] [--stages a,b] [--refs-repeat N] [--absorb-max-s N] [--skip-window] [--no-suite] [--fit-only] [--dry-run] [--foreground] | status [job] | wait <job> [--timeout S] | result <job> [--json] | export <job> [--out <file>] [--allow-incomplete] | stop <job> | list" >&2
  exit 2
}

job_path() {
  "${py[@]}" job-dir "$1"
}

merge() {
  printf '%s' "$1" | "${py[@]}" status-merge --job "$job" >/dev/null
}

pget() {
  python3 - "$job/plan.json" "$1" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
cur = plan
for part in sys.argv[2].split("."):
    cur = cur[part]
if isinstance(cur, bool):
    print("1" if cur else "0")
elif isinstance(cur, list):
    print("\n".join(str(item) for item in cur))
elif cur is None:
    print("")
else:
    print(cur)
PY
}

release_locks() {
  exec 9>&- 2>/dev/null || true
  exec 7>&- 2>/dev/null || true
}

# A helper's group is signalled only when it is not the runner's group.
# Otherwise a second TERM would re-enter the runner's trap and skip the marker.
kill_helper() {
  local pid=$1 pgid self
  [ -n "$pid" ] || return 0
  self=$(ps -o pgid= -p $$ 2>/dev/null | tr -d '[:space:]' || true)
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)
  if [ -n "$pgid" ] && [ "$pgid" != "$self" ] && [ "$pgid" != "$$" ]; then
    kill -TERM -- "-$pgid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
}

stop_helpers() {
  rm -f "$job/WATCH" "$job/HEARTBEAT" 2>/dev/null || true
  kill_helper "${HB:-}"
  kill_helper "${WD:-}"
  if [ -n "${MEM:-}" ]; then
    kill -TERM "$MEM" 2>/dev/null || true
  fi
}

stop_server() {
  [ "${SERVER_STOPPED:-0}" = 1 ] && return 0
  SERVER_STOPPED=1
  local cid=""
  [ -f "$job/cid" ] && cid=$(tr -d '[:space:]' < "$job/cid" || true)
  [ -n "$cid" ] || return 0
  if command -v a770b-qualify-stop >/dev/null 2>&1; then
    printf '%s\n' "stop: a770b-qualify-stop $cid" >> "$job/evidence/argv.log"
    a770b-qualify-stop "$cid" >> "$job/phases/stop-server.log" 2>&1 || true
  elif [ "${STUB:-0}" != 1 ]; then
    bash "$here/serve_compose.sh" stop-id "$cid" >> "$job/phases/stop-server.log" 2>&1 || true
  fi
}

record_hooks() {
  Q_PRE="${PRE_STARTED:-0}" Q_CODE="${POST_CODE:-}" Q_LINE="${POST_LINE:-}" \
    python3 - "$job" <<'PY'
import json, os, sys
job = sys.argv[1]
body = {"pre_started": os.environ.get("Q_PRE") == "1"}
code = os.environ.get("Q_CODE", "")
if code != "":
    body["post_code"] = int(code)
    body["post_line"] = os.environ.get("Q_LINE", "").splitlines()[0] if os.environ.get("Q_LINE") else ""
path = os.path.join(job, "evidence", "hooks.json")
fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
os.write(fd, (json.dumps(body, indent=2) + "\n").encode())
os.close(fd)
PY
}

run_post() {
  [ "${POST_DONE:-0}" = 1 ] && return 0
  [ "${PRE_STARTED:-0}" = 1 ] || return 0
  POST_DONE=1
  local post=${A770B_QUALIFY_POST:-}
  POST_CODE=0
  POST_LINE=""
  if [ -n "$post" ] && [ -x "$post" ] && [ ! -L "$post" ]; then
    local out
    out=$("$post" "$job" 2>&1 | head -n 1)
    POST_CODE=$?
    POST_LINE=$out
  else
    POST_CODE=1
    POST_LINE="post hook missing"
  fi
}

finish() {
  [ "${IN_FINISH:-0}" = 1 ] && return 0
  IN_FINISH=1
  # A second TERM must not run the trap again: that exit would skip the marker.
  trap '' TERM INT
  [ -n "${CHILD:-}" ] && kill -TERM "$CHILD" 2>/dev/null || true
  stop_helpers
  stop_server
  run_post
  record_hooks || true
  "${py[@]}" assemble --job "$job" >/dev/null 2>&1 || true
  "${py[@]}" summarise --job "$job" >/dev/null 2>&1 || true
  local state=${FINISH_STATE:-finished}
  local reason=${FAIL_REASON:-}
  local marker=${MARKER_NAME:-DONE}
  merge "$(python3 - "$state" "$reason" "${RUN_PID:-}" "${RUN_START:-}" <<'PY'
import json, sys
state, reason, pid, start = sys.argv[1:5]
body = {"state": state, "reason": reason or None, "event": "terminal " + state}
if pid:
    body["pid"] = int(pid)
if start:
    body["pid_starttime"] = start
print(json.dumps(body))
PY
)"
  : > "$job/$marker"
  chmod 600 "$job/$marker" 2>/dev/null || true
  MARKER=1
  release_locks
}

on_term() {
  if [ "${IN_FINISH:-0}" = 1 ]; then
    return
  fi
  FINISH_STATE=stopped
  MARKER_NAME=STOPPED
  FAIL_REASON=${FAIL_REASON:-stopped}
  finish
  exit 0
}

on_exit() {
  local rc=$?
  if [ "${MARKER:-0}" = 1 ]; then
    release_locks
    return
  fi
  if [ "${INSIDE_RUN:-0}" = 1 ]; then
    FINISH_STATE=${FINISH_STATE:-failed}
    MARKER_NAME=${MARKER_NAME:-FAILED}
    finish
  fi
  return "$rc"
}

probe_card() {
  python3 - <<'PY'
import fcntl, os, sys
root = os.environ["A770B_DATA"]
logs = os.path.join(root, "logs")
os.makedirs(logs, exist_ok=True)
path = os.path.realpath(os.path.join(logs, "local-build.lock"))
fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(2)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
finally:
    os.close(fd)
PY
}

take_card() {
  local lock
  mkdir -p "$A770B_DATA/logs"
  lock=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$A770B_DATA/logs/local-build.lock")
  exec 9>"$lock" || return 1
  flock -n 9 || return 1
  export A770B_CARD_LOCK_PATH=$lock
}

take_seat() {
  local seat=$1 lock
  mkdir -p "$A770B_DATA/logs/seat-locks" "$seat"
  lock=$(python3 -c 'import hashlib,os,sys; c=os.path.realpath(sys.argv[1]); print(os.path.join(sys.argv[2], "logs", "seat-locks", hashlib.sha256(c.encode()).hexdigest()+".lock"))' "$seat" "$A770B_DATA")
  exec 7>"$lock" || return 1
  flock -n 7 || return 1
  export A770B_SEAT=$seat
  export A770B_SEAT_LOCK_PATH=$lock
}

heartbeat_loop() {
  local parent=$1
  while [ -e "$job/HEARTBEAT" ]; do
    kill -0 "$parent" 2>/dev/null || exit 0
    printf '%s\n' "{\"heartbeat\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" | "${py[@]}" status-merge --job "$job" >/dev/null || true
    sleep "${A770B_QUALIFY_HEARTBEAT_S:-30}" || true
  done
}

phase() {
  merge "{\"phase\":\"$1\",\"event\":\"phase $1\"}"
}

run_tool() {
  local name=$1
  shift
  mkdir -p "$job/phases" "$job/evidence"
  printf '%s\n' "$name: $*" >> "$job/evidence/argv.log"
  local out rc=0
  out=$("$@" 2>>"$job/phases/${name}.log") || rc=$?
  printf '%s\n' "$out" >> "$job/phases/${name}.log"
  printf '%s' "$out" | "${py[@]}" apply-phase --job "$job" --phase "$name" --rc "$rc" >/dev/null || true
  return "$rc"
}

copy_profiles() {
  local src="" dest="$job/scratch/profiles.json"
  if [ -n "${PROFILE_FILE:-}" ] && [ -f "$PROFILE_FILE" ] && [ ! -L "$PROFILE_FILE" ]; then
    src=$PROFILE_FILE
  elif [ -n "${A770B_PROFILES_FILE:-}" ] && [ -f "$A770B_PROFILES_FILE" ] && [ ! -L "$A770B_PROFILES_FILE" ]; then
    src=$A770B_PROFILES_FILE
  else
    src=$(find "$A770B_PROJECT/config/registry" -maxdepth 1 -type f -name '*.json' | head -n 1)
  fi
  [ -n "$src" ] || return 1
  cp -- "$src" "$dest" || return 1
  chmod 600 "$dest" || return 1
  export A770B_PROFILES_FILE=$dest
}

image_for() {
  case "$1" in
    llama.cpp) printf '%s\n' "${A770B_LLAMA_IMAGE:-}" ;;
    vllm) printf '%s\n' "${A770B_VLLM_IMAGE:-}" ;;
    sglang) printf '%s\n' "${A770B_SGLANG_IMAGE:-}" ;;
    *) printf '%s\n' "admission" ;;
  esac
}

pause_for() {
  [ "${A770B_QUALIFY_PAUSE_PHASE:-}" = "$1" ] || return 1
  phase "$1"
  if [ "$1" = serve ]; then
    printf '%s\n' "stubcid" > "$job/cid"
    chmod 600 "$job/cid" || true
  fi
  if [ "$1" = suite ]; then
    cat > "$job/scratch/sandbox_run.sh" <<'EOF'
#!/bin/bash
sleep 3600
EOF
    chmod 700 "$job/scratch/sandbox_run.sh"
    bash "$job/scratch/sandbox_run.sh" &
    echo $! > "$A770B_DATA/logs/run.pid"
    echo $! > "$job/suite.pid"
  fi
  while [ ! -f "$job/STOP_REQUESTED" ]; do
    sleep 0.2
  done
  FINISH_STATE=stopped
  MARKER_NAME=STOPPED
  FAIL_REASON=stopped
  return 0
}

run_suite_once() {
  local stages=$1
  local -a args=()
  if [ "$GGUF" = 1 ]; then
    args=(--model "$MODEL" --ctx "$CTX")
    [ -n "$KV" ] && args+=(--kv "$KV")
    [ -n "$KV_V" ] && args+=(--kv-v "$KV_V")
    [ -n "$EXTRA" ] && args+=(--extra "$EXTRA")
  else
    args=("$PROFILE_NAME")
  fi
  args+=("$job/scratch/seat")
  [ -n "$stages" ] && args+=(--stages "$stages")
  local -a cmd
  if command -v a770b-qualify-suite >/dev/null 2>&1; then
    cmd=(a770b-qualify-suite "${args[@]}")
  elif [ "$STUB" = 1 ]; then
    return 1
  else
    cmd=(bash "$here/run_suite.sh" "${args[@]}")
  fi
  printf '%s\n' "suite: ${cmd[*]}" >> "$job/evidence/argv.log"
  printf '%s\n' "profiles-file: $A770B_PROFILES_FILE" >> "$job/evidence/argv.log"
  A770B_PROFILES_FILE=$A770B_PROFILES_FILE A770B_SEAT="$job/scratch/seat" "${cmd[@]}" >"$job/phases/suite.log" 2>&1 &
  CHILD=$!
  printf '%s\n' "$CHILD" > "$job/suite.pid"
  local rc=0
  wait "$CHILD" || rc=$?
  CHILD=
  "${py[@]}" apply-phase --job "$job" --phase suite --rc "$rc" < "$job/phases/suite.log" >/dev/null || true
  return "$rc"
}

fail_job() {
  FINISH_STATE=failed
  FAIL_REASON=$1
  MARKER_NAME=FAILED
}

run_phases() {
  local engine model image rc=0
  engine=$(pget engine)
  model=$(pget model)
  GGUF=$(pget gguf)
  CTX=$(pget ctx)
  MODEL=$model
  KV=$(pget kv)
  KV_V=$(pget kv_v)
  EXTRA=$(pget extra | paste -sd ' ' -)
  PROFILE_NAME=$(pget profile)
  PROFILE_FILE=$(pget profiles_file)
  STUB=0
  if [ "${A770B_QUALIFY_STUB:-}" = 1 ] || command -v a770b-qualify-serve >/dev/null 2>&1; then
    STUB=1
  fi
  phase preflight
  if ! "${py[@]}" preflight --job "$job"; then
    fail_job preflight
    return
  fi
  image=$(image_for "$engine")
  if command -v a770b-qualify-gate >/dev/null 2>&1; then
    run_tool gate a770b-qualify-gate --engine "$engine" --image "$image" --backend "$(pget backend)" || {
      fail_job preflight
      return
    }
  elif [ "$STUB" = 1 ]; then
    fail_job preflight
    return
  elif [ "$engine" = strata ]; then
    run_tool gate bash "$here/serve_strata.sh" admit || { fail_job preflight; return; }
  else
    run_tool gate bash "$here/serve_compose.sh" gate || { fail_job preflight; return; }
  fi
  phase pre-hook
  local pre=${A770B_QUALIFY_PRE:-}
  if [ -n "$pre" ]; then
    if [ -L "$pre" ] || [ ! -x "$pre" ]; then
      fail_job pre-hook
      return
    fi
    PRE_STARTED=1
    if ! "$pre" "$job" >"$job/phases/pre-hook.log" 2>&1; then
      fail_job pre-hook
      return
    fi
  fi
  if pause_for serve; then
    return
  fi
  phase serve
  if [ "$STUB" = 1 ]; then
    local -a serve_argv=()
    mapfile -t serve_argv < <(pget serve_argv)
    run_tool serve a770b-qualify-serve "${serve_argv[@]}" || { fail_job load; return; }
  elif [ "$engine" = "llama.cpp" ]; then
    local -a extra_argv=()
    mapfile -t extra_argv < <(pget extra)
    bash "$here/serve_compose.sh" start "$model" "$CTX" "${extra_argv[@]}" >"$job/phases/serve.log" 2>&1 || { fail_job load; return; }
    [ -f "$A770B_DATA/logs/llamacpp-a770.cid" ] && cp -- "$A770B_DATA/logs/llamacpp-a770.cid" "$job/cid"
  else
    bash "$A770B_PROJECT/skills/local-build/scripts/local-build.sh" serve "$PROFILE_NAME" >"$job/phases/serve.log" 2>&1 || { fail_job load; return; }
  fi
  if [ "$STUB" != 1 ]; then
    "${py[@]}" confirm --job "$job" --host "$A770B_HOST" --port "$A770B_PORT" --log "$job/phases/serve.log" >/dev/null 2>&1 || true
  fi
  phase smoke
  run_tool smoke bash "$here/qualify_smoke.sh" "$job" || true
  phase speed
  if [ "$STUB" = 1 ]; then
    run_tool speed a770b-qualify-speed --ctx "$CTX" || true
  else
    run_tool speed bash "$here/bench_http.sh" --base-url "http://$A770B_HOST:$A770B_PORT/v1" --served-model-name "$model" --tokenizer "$(pget tokenizer)" --pp "$(pget speed_sizes | paste -sd ' ' -)" || true
  fi
  if [ "$(pget skip_window)" != 1 ]; then
    phase window
    if [ "$STUB" = 1 ]; then
      run_tool window a770b-qualify-sweep --to "$(pget far_end)" || true
    else
      run_tool window bash "$here/ctx_sweep.sh" --tokenizer "$(pget tokenizer)" 8000 "$(pget far_end)" || true
    fi
  fi
  phase depth
  if [ "$STUB" = 1 ]; then
    run_tool depth a770b-qualify-depth --gen "$(pget gen)" || true
  else
    run_tool depth bash "$here/depth_probe.sh" "$(pget far_end)" --gen "$(pget gen)" --tokenizer "$(pget tokenizer)" || true
  fi
  phase memory
  if [ "$STUB" = 1 ]; then
    run_tool memory a770b-qualify-memory --interval 5 --owner-pid "$$" || true
  else
    ( exec 7>&- 9>&-; exec python3 "$here/host_memory.py" monitor --out "$job/evidence/host-memory.jsonl" --interval 5 --owner-pid "$$" ) &
    MEM=$!
  fi
  phase stop-server
  stop_server
  if [ "$(pget fit_only)" = 1 ] || [ "$(pget no_suite)" = 1 ]; then
    :
  elif pause_for suite; then
    return
  else
    phase suite
    copy_profiles || { fail_job preflight; return; }
    take_seat "$job/scratch/seat" || { fail_job lock; return; }
    local stages
    stages=$(pget stages | paste -sd, -)
    run_suite_once "$stages" || true
    if [ "$(pget refs_apply)" = 1 ] && [ "$(pget refs_repeat)" -gt 1 ] && [ ! -f "$job/DRIVER_ERROR" ]; then
      local left refs
      left=$(( $(pget refs_repeat) - 1 ))
      refs=$(pget reference_ids | paste -sd, -)
      while [ "$left" -gt 0 ]; do
        [ -f "$job/SUITE_SKIP" ] && break
        [ -f "$job/DRIVER_ERROR" ] && break
        run_suite_once "$refs" || true
        left=$((left - 1))
      done
    fi
  fi
  if [ -f "$job/DRIVER_ERROR" ]; then
    fail_job driver
  fi
}

cmd_run() {
  job=${1:?job directory}
  [ -d "$job" ] && [ ! -L "$job" ] || die "job directory is missing or a symlink"
  INSIDE_RUN=1
  FINISH_STATE=finished
  MARKER_NAME=DONE
  FAIL_REASON=
  PRE_STARTED=0
  POST_DONE=0
  MARKER=0
  IN_FINISH=0
  STUB=0
  set -m
  trap on_term TERM INT
  trap on_exit EXIT
  mkdir -p "$job/evidence" "$job/phases" "$job/scratch"
  : > "$job/evidence/argv.log"
  chmod 600 "$job/evidence/argv.log" || true
  if ! take_card; then
    merge '{"state":"failed","reason":"lock","event":"lock refused"}'
    : > "$job/FAILED"
    chmod 600 "$job/FAILED" || true
    MARKER=1
    exit 2
  fi
  RUN_PID=$$
  RUN_START=$("${py[@]}" starttime "$$" || true)
  merge "{\"state\":\"running\",\"phase\":\"preflight\",\"pid\":$RUN_PID,\"pid_starttime\":\"${RUN_START}\",\"reason\":null,\"event\":\"running\"}"
  : > "$job/HEARTBEAT"
  : > "$job/WATCH"
  : > "$job/cid"
  if [ "${A770B_QUALIFY_STUB:-}" = 1 ] || command -v a770b-qualify-serve >/dev/null 2>&1; then
    STUB=1
    # The helper must be executable, and every file inside the job is mode 0600.
    nodocker=$A770B_DATA/qualify-nodocker
    if [ -L "$nodocker" ]; then
      echo "qualify: runtime helper is a symlink" >&2
      exit 2
    fi
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$nodocker"
    chmod 700 "$nodocker"
    export WD_DOCKER=$nodocker
  fi
  # Helpers drop the runner's traps. A copied trap would finish the job twice.
  ( trap - TERM INT EXIT; exec 7>&- 9>&-; heartbeat_loop "$$" ) &
  HB=$!
  ( trap - TERM INT EXIT; exec 7>&- 9>&-; QUALIFY_WATCH_PARENT=$$ exec bash "$here/qualify_watch.sh" "$job" "$job/cid" ) &
  WD=$!
  if [ -n "${A770B_QUALIFY_HOLD_S:-}" ]; then
    sleep "$A770B_QUALIFY_HOLD_S" &
    CHILD=$!
    wait "$CHILD" || true
    CHILD=
  fi
  run_phases
  finish
  if [ "$FINISH_STATE" = finished ]; then
    exit 0
  fi
  exit 4
}

wait_handshake() {
  local n=0 state
  while [ "$n" -lt 50 ]; do
    if [ -f "$job/status.json" ]; then
      state=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("state",""))' "$job/status.json" 2>/dev/null || true)
      if [ "$state" = running ]; then
        printf 'job %s\n' "$id"
        printf 'directory %s\n' "$job"
        return 0
      fi
      if [ "$state" = failed ]; then
        printf '⛔ runner refused the lock or the seat\n' >&2
        return 2
      fi
    fi
    n=$((n + 1))
    sleep 0.2
  done
  printf '⛔ runner handshake timed out\n' >&2
  return 2
}

forwarded_env() {
  mapfile -d '' -t FORWARDED < <("${py[@]}" forward-env || true)
}

use_systemd() {
  command -v systemd-run >/dev/null 2>&1 || return 1
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --user show-environment >/dev/null 2>&1 || return 1
  return 0
}

cmd_start() {
  local dry=0 fore=0 arg
  for arg in "$@"; do
    [ "$arg" = --dry-run ] && dry=1
    [ "$arg" = --foreground ] && fore=1
  done
  if [ "$dry" = 1 ]; then
    "${py[@]}" plan "$@"
    return $?
  fi
  local tmp
  tmp=$(mktemp)
  if ! "${py[@]}" plan --emit "$tmp" "$@"; then
    rm -f -- "$tmp"
    return 2
  fi
  if ! probe_card; then
    rm -f -- "$tmp"
    die "the card lock is held"
  fi
  if run_pid_alive "$A770B_DATA/logs/run.pid" >/dev/null 2>&1; then
    rm -f -- "$tmp"
    die "a run is in flight on this seat"
  fi
  local root id launch
  root=$A770B_DATA/qualify
  if [ -L "$A770B_DATA" ] || [ -L "$root" ]; then
    rm -f -- "$tmp"
    die "refusing a symlink in the job path"
  fi
  id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["job_id"])' "$tmp")
  mkdir -p -m 0700 "$root"
  job=$root/$id
  if [ -e "$job" ]; then
    id=$(printf '%s-%04x' "${id:0:59}" "$RANDOM")
    job=$root/$id
    python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p, encoding="utf-8")); d["job_id"]=sys.argv[2]; json.dump(d, open(p,"w"), indent=2)' "$tmp" "$id"
  fi
  if [ -L "$job" ]; then
    rm -f -- "$tmp"
    die "refusing a symlink job directory"
  fi
  mkdir -m 0700 "$job"
  if [ "$fore" = 1 ]; then
    launch=foreground
  elif use_systemd; then
    launch=systemd
  else
    launch=setsid
  fi
  "${py[@]}" init-job --job "$job" --plan "$tmp" --launch "$launch"
  rm -f -- "$tmp"
  chmod 700 "$job" "$job/evidence" "$job/scratch" "$job/phases" 2>/dev/null || true
  if [ "$fore" = 1 ]; then
    printf 'job %s\n' "$id"
    printf 'directory %s\n' "$job"
    cmd_run "$job"
    return $?
  fi
  if [ "$launch" = systemd ]; then
    local -a args=() item
    forwarded_env
    args=(systemd-run --user --collect --unit="qualify-$id" -p KillMode=mixed -p TimeoutStopSec=120 --working-directory="$A770B_PROJECT")
    for item in "${FORWARDED[@]}"; do
      [ -n "$item" ] || continue
      args+=(--setenv="$item")
    done
    args+=(-- bash "$here/qualify.sh" run --job "$job")
    if ! "${args[@]}" >>"$job/launch.log" 2>&1; then
      die "systemd-run failed"
    fi
  else
    setsid nohup bash "$here/qualify.sh" run --job "$job" >>"$job/launch.log" 2>&1 </dev/null &
  fi
  wait_handshake
}

cmd_status() {
  local id=${1:-}
  if [ -z "$id" ]; then
    id=$("${py[@]}" pick) || exit 2
  fi
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  "${py[@]}" status-show --job "$job"
}

cmd_wait() {
  local id=${1:?job}
  shift
  local timeout=86400
  while [ $# -gt 0 ]; do
    case "$1" in
      --timeout)
        timeout=${2:?seconds}
        shift 2
        ;;
      *) die "unknown qualify option $1" ;;
    esac
  done
  case "$timeout" in
    ''|*[!0-9]*) die "timeout must be a positive integer" ;;
  esac
  [ "$timeout" -ge 1 ] || die "timeout must be a positive integer"
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  local start now state
  start=$(date +%s)
  while true; do
    state=$(${py[@]} status-show --job "$job" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
    case "$state" in
      finished) exit 0 ;;
      failed|lost|stopped) exit 4 ;;
    esac
    if [ -f "$job/DONE" ]; then exit 0; fi
    if [ -f "$job/FAILED" ] || [ -f "$job/STOPPED" ]; then exit 4; fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then exit 124; fi
    sleep 0.2
  done
}

cmd_result() {
  local id="" json=0 arg
  for arg in "$@"; do
    case "$arg" in
      --json) json=1 ;;
      *) id=$arg ;;
    esac
  done
  [ -n "$id" ] || die "result needs a job"
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  local shown state
  shown=$("${py[@]}" status-show --job "$job")
  state=$(printf '%s' "$shown" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
  if [ "$json" = 1 ] && [ -f "$job/result.json" ]; then
    cat "$job/result.json"
  elif [ -f "$job/report.md" ]; then
    cat "$job/report.md"
  else
    printf '%s\n' "$shown"
  fi
  case "$state" in
    finished) exit 0 ;;
    failed|lost|stopped) exit 4 ;;
    *) exit 3 ;;
  esac
}

cmd_export() {
  local id=${1:?job}
  shift
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  "${py[@]}" export --job "$job" "$@"
}

cmd_stop() {
  local id=${1:?job}
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  local pid start launch
  pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("pid") or "")' "$job/status.json" 2>/dev/null || true)
  start=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("pid_starttime") or "")' "$job/status.json" 2>/dev/null || true)
  if ! "${py[@]}" identity --pid "$pid" --starttime "$start"; then
    die "runner identity does not match"
  fi
  : > "$job/STOP_REQUESTED"
  chmod 600 "$job/STOP_REQUESTED" || true
  if run_pid_alive "$A770B_DATA/logs/run.pid" >/dev/null 2>&1; then
    bash "$A770B_PROJECT/skills/local-build/scripts/local-build.sh" stop-run >>"$job/phases/stop-run.log" 2>&1 || true
  fi
  launch=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("launch") or "")' "$job/job.json" 2>/dev/null || true)
  if [ "$launch" = systemd ]; then
    systemctl --user kill -s TERM --kill-who=main "qualify-$id" >>"$job/phases/stop-run.log" 2>&1 || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  local start_s now
  start_s=$(date +%s)
  while true; do
    if [ -f "$job/STOPPED" ] || [ -f "$job/DONE" ] || [ -f "$job/FAILED" ]; then
      exit 0
    fi
    now=$(date +%s)
    if [ $((now - start_s)) -ge 120 ]; then
      die "stop timed out"
    fi
    sleep 0.2
  done
}

cmd_list() {
  local root=$A770B_DATA/qualify name id state
  [ -d "$root" ] && [ ! -L "$root" ] || return 0
  for name in "$root"/*; do
    [ -d "$name" ] || continue
    id=$(basename "$name")
    job=$(job_path "$id" 2>/dev/null) || continue
    state=$(python3 -c 'import json,sys,os; p=os.path.join(sys.argv[1],"status.json"); print(json.load(open(p)).get("state","") if os.path.isfile(p) else "")' "$job" 2>/dev/null || true)
    printf '%s %s\n' "$id" "$state"
  done
}

[ $# -ge 1 ] || usage
cmd=$1
shift
case "$cmd" in
  start) cmd_start "$@" ;;
  run)
    [ "${1:-}" = --job ] || die "run needs --job"
    cmd_run "${2:?job directory}"
    ;;
  status) cmd_status "${1:-}" ;;
  wait) cmd_wait "$@" ;;
  result) cmd_result "$@" ;;
  export) cmd_export "$@" ;;
  stop) cmd_stop "$@" ;;
  list) cmd_list ;;
  *) usage ;;
esac
