#!/usr/bin/env bash
# qualify.sh — run a qualification and return while it continues.
# start probes the card lock without holding it, then the runner takes the
# lock itself. Helpers close the lock descriptors so they cannot keep the card.
set -uo pipefail
umask 077
exec 7>&- 9>&- || true
here=$(cd "$(dirname "$0")" && pwd)
# A refused start and a dry run must not create logs or results.
export A770B_DEFER_DATA_DIRS=1
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

# shellcheck disable=SC1091
source "$here/serve_envelope.sh"

# A write follows a symlink. Refuse the path and its parent before opening it.
refuse_link() {
  local path=$1
  if [ -L "$path" ] || [ -L "$(dirname "$path")" ]; then
    printf '⛔ refusing a symlink: %s\n' "$path" >&2
    return 1
  fi
  return 0
}

plain_trunc() {
  local path=$1
  refuse_link "$path" || return 1
  : > "$path"
  chmod 600 "$path" 2>/dev/null || true
}

usage() {
  printf '%s\n' "usage: qualify.sh start --engine <llama|llama.cpp|vllm|sglang> --model <name> [--profiles-file <file>] [--ctx N] [--backend vulkan|sycl] [--tokenizer <dir>] [--kv T] [--kv-v T] [--extra <flags>] [--reasoning on|off] [--reasoning-budget N] [--stages a,b] [--refs-repeat N] [--absorb-max-s N] [--skip-window] [--no-suite] [--fit-only] [--dry-run] [--foreground] | status [job] | wait <job> [--timeout S] | result <job> [--json] | export <job> [--out <file>] [--allow-incomplete] | stop <job> | list" >&2
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
# Otherwise a second signal would re-enter the runner's trap and skip the marker.
# The same guard covers TERM and KILL: an unguarded group KILL can hit the runner.
kill_helper() {
  local pid=$1 sig=${2:-TERM} pgid self
  [ -n "$pid" ] || return 0
  case "$sig" in
    TERM|KILL) ;;
    *) sig=TERM ;;
  esac
  self=$(ps -o pgid= -p $$ 2>/dev/null | tr -d '[:space:]' || true)
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)
  if [ -n "$pgid" ] && [ "$pgid" != "$self" ] && [ "$pgid" != "$$" ]; then
    kill -"$sig" -- "-$pgid" 2>/dev/null || true
  else
    kill -"$sig" "$pid" 2>/dev/null || true
  fi
}

stop_helpers() {
  rm -f "$job/WATCH" "$job/HEARTBEAT" 2>/dev/null || true
  kill_helper "${HB:-}"
  kill_helper "${WD:-}"
  kill_helper "${VRAM_PID:-}"
  if [ -n "${MEM:-}" ]; then
    kill -TERM "$MEM" 2>/dev/null || true
  fi
  # The tracker is not the monitor. Reap it so a finished job leaves no writer.
  if [ -n "${TRACKER:-}" ]; then
    kill_helper "$TRACKER"
    wait "$TRACKER" 2>/dev/null || true
    TRACKER=""
  fi
}

release_suite_server() {
  # Stop only pairs this job appended to owned.cids. A container that is not
  # in that file was not started by this job, so it is left running.
  # The latest line for an id wins: a later start of the same id has a new
  # invocation, and stop-id refuses a mismatched one.
  local file=$job/owned.cids line cid inv seen=" " idx
  local -a entries=()
  [ "${STUB:-0}" = 1 ] && return 0
  [ -f "$file" ] && [ ! -L "$file" ] || return 0
  refuse_link "$job/phases/stop-owned.log" || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    entries+=("$line")
  done < "$file"
  for (( idx=${#entries[@]}-1; idx>=0; idx-- )); do
    cid=""
    inv=""
    read -r cid inv <<<"${entries[$idx]}"
    [ -n "$cid" ] && [ -n "$inv" ] || continue
    case "$seen" in
      *" $cid "*) continue ;;
    esac
    seen="$seen$cid "
    bash "$here/serve_compose.sh" stop-id "$cid" "$inv" >>"$job/phases/stop-owned.log" 2>&1 || true
  done
}

stop_server() {
  [ "${SERVER_STOPPED:-0}" = 1 ] && return 0
  local rc=0 cid="" engine
  # Phase 3's target ends with the server. Stop the tracker before truncating
  # so it cannot write the old pid back over the clear. Phase 5 publishes
  # through run_suite.sh after this returns.
  if [ -n "${TRACKER:-}" ]; then
    kill_helper "$TRACKER"
    wait "$TRACKER" 2>/dev/null || true
    TRACKER=""
  fi
  # A failed clear is kept. It must not skip the owned-server stop. The flag
  # is set only after that teardown, so a later call cannot skip a server
  # this attempt has not stopped yet.
  plain_trunc "$job/SERVING_CLEARED" || rc=1
  plain_trunc "$job/evidence/serving.pid" || rc=1
  [ -f "$job/cid" ] && [ ! -L "$job/cid" ] && cid=$(tr -d '[:space:]' < "$job/cid" || true)
  if [ -n "$cid" ]; then
    if refuse_link "$job/phases/stop-server.log" && refuse_link "$job/evidence/argv.log"; then
      if command -v a770b-qualify-stop >/dev/null 2>&1; then
        printf '%s\n' "stop: a770b-qualify-stop $cid" >> "$job/evidence/argv.log"
        a770b-qualify-stop "$cid" >> "$job/phases/stop-server.log" 2>&1 || true
      elif [ "${STUB:-0}" != 1 ]; then
        engine=$(pget engine 2>/dev/null || true)
        if [ "$engine" = strata ]; then
          bash "$here/serve_strata.sh" stop-id "$cid" >> "$job/phases/stop-server.log" 2>&1 || true
        else
          bash "$here/serve_compose.sh" stop-id "$cid" >> "$job/phases/stop-server.log" 2>&1 || true
        fi
      fi
    fi
  fi
  SERVER_STOPPED=1
  return "$rc"
}

record_hooks() {
  # The exit code is a small variable. The first log line is read here, and
  # only a prefix of it: a long line must not ride in the environment.
  Q_PRE="${PRE_STARTED:-0}" Q_CODE="${POST_CODE:-}" Q_LINE="${POST_LINE:-}" Q_LOG="$job/phases/post-hook.log" \
    python3 - "$job" <<'PY'
import json, os, sys
job = sys.argv[1]
body = {"pre_started": os.environ.get("Q_PRE") == "1"}
code = os.environ.get("Q_CODE", "")
line = os.environ.get("Q_LINE", "")
if not line:
    path = os.environ.get("Q_LOG", "")
    if path and not os.path.islink(path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            fd = None
        if fd is not None:
            try:
                chunk = os.read(fd, 400)
            finally:
                os.close(fd)
            line = chunk.split(b"\n", 1)[0].decode("utf-8", "replace")
if code != "":
    try:
        body["post_code"] = int(code)
    except ValueError:
        pass
    else:
        body["post_line"] = line.splitlines()[0] if line else ""
path = os.path.join(job, "evidence", "hooks.json")
payload = (json.dumps(body, indent=2) + "\n").encode()
fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
try:
    view = memoryview(payload)
    while view:
        wrote = os.write(fd, view)
        view = view[wrote:]
finally:
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
    if ! refuse_link "$job/phases/post-hook.log"; then
      POST_CODE=1
      POST_LINE="post hook log is a symlink"
    else
      "$post" "$job" >"$job/phases/post-hook.log" 2>&1
      POST_CODE=$?
      POST_LINE=""
    fi
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
  if [ -n "${CHILD:-}" ]; then
    kill_helper "$CHILD"
    local n=0
    while kill -0 "$CHILD" 2>/dev/null && [ "$n" -lt 50 ]; do
      sleep 0.1
      n=$((n + 1))
    done
    kill_helper "$CHILD" KILL
    wait "$CHILD" 2>/dev/null || true
    CHILD=
  fi
  rm -f "$job/VRAM_SAMPLE" 2>/dev/null || true
  stop_helpers
  stop_server
  release_suite_server
  record_card || true
  run_post
  record_hooks || true
  # The terminal state is the publication wait and result both read.
  # Assemble and summarise finish first. The marker and the lock release
  # come next. Only then does status.json leave "running".
  local state=${FINISH_STATE:-finished}
  local reason=${FAIL_REASON:-}
  local marker=${MARKER_NAME:-DONE}
  "${py[@]}" assemble --job "$job" >/dev/null 2>&1 || true
  if [ "$state" = failed ] && [ -n "$reason" ]; then
    "${py[@]}" summarise --job "$job" --abort-reason "$reason" >/dev/null 2>&1 || true
  else
    "${py[@]}" summarise --job "$job" >/dev/null 2>&1 || true
  fi
  release_locks
  if ! plain_trunc "$job/$marker"; then
    exit 2
  fi
  MARKER=1
  # The marker is already on disk. status_merge ignores a later writer unless
  # the patch says this is the one terminal publish.
  merge "$(python3 - "$state" "$reason" "${RUN_PID:-}" "${RUN_START:-}" <<'PY'
import json, sys
state, reason, pid, start = sys.argv[1:5]
body = {
    "state": state,
    "reason": reason or None,
    "event": "terminal " + state,
    "_allow_terminal": True,
}
if pid:
    body["pid"] = int(pid)
if start:
    body["pid_starttime"] = start
print(json.dumps(body))
PY
)"
}

on_term() {
  if [ "${IN_FINISH:-0}" = 1 ]; then
    return
  fi
  FINISH_STATE=stopped
  MARKER_NAME=STOPPED
  FAIL_REASON=${FAIL_REASON:-stopped}
  finish
  exit 4
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

record_card() {
  refuse_link "$job/evidence/card.json" || return 1
  if [ "${STUB:-0}" = 1 ]; then
    printf '%s\n' '{"containers": null, "vram_gib": null, "idle_gib": null}' | "${py[@]}" note --job "$job" --name card.json >/dev/null
    return 0
  fi
  python3 - "$job" "$here" <<'PY'
import json, os, subprocess, sys
job, here = sys.argv[1], sys.argv[2]
body = {"containers": None, "vram_gib": None, "idle_gib": None}
docker = os.environ.get("A770B_DOCKER") or "docker"
project = os.environ.get("A770B_COMPOSE_PROJECT") or ""
engine = ""
plan = os.path.join(job, "plan.json")
try:
    if os.path.isfile(plan) and not os.path.islink(plan):
        engine = json.load(open(plan, encoding="utf-8")).get("engine") or ""
except (OSError, ValueError):
    engine = ""
try:
    run = subprocess.run(
        [docker, "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True, text=True, timeout=15,
    )
    if run.returncode == 0:
        body["containers"] = len([line for line in run.stdout.splitlines() if line.strip()])
except (OSError, subprocess.TimeoutExpired):
    pass
# A managed Strata server is not a compose container. docker ps stays 0 while it runs.
if engine == "strata":
    try:
        ident = subprocess.run(
            ["bash", os.path.join(here, "serve_strata.sh"), "id"],
            capture_output=True, text=True, timeout=15,
        )
        if ident.returncode == 0 and (ident.stdout or "").strip():
            current = body["containers"] if isinstance(body["containers"], int) else 0
            body["containers"] = max(current, 1)
    except (OSError, subprocess.TimeoutExpired):
        pass
idle_path = os.path.join(job, "evidence", "idle.txt")
if os.path.isfile(idle_path) and not os.path.islink(idle_path):
    try:
        idle_text = open(idle_path, encoding="utf-8").read().strip().split()
        if idle_text and idle_text[-1] != "unavailable":
            body["idle_gib"] = float(idle_text[-1])
    except (OSError, ValueError):
        pass
script = "source " + json.dumps(os.path.join(here, "env.sh")) + " && source " + json.dumps(os.path.join(here, "guard.sh")) + " && gpu_used_gib"
try:
    used = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
    text = (used.stdout or "").strip()
    if used.returncode == 0 and text and text != "unavailable":
        body["vram_gib"] = float(text)
except (OSError, subprocess.TimeoutExpired, ValueError):
    pass
path = os.path.join(job, "evidence", "card.json")
fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
os.write(fd, (json.dumps(body, indent=2) + "\n").encode())
os.close(fd)
PY
}

# inherit=1 keeps the card lock (FD 9) for a serve child. card_lock refuses a
# fresh flock while this runner already holds the same file. Helpers pass 0
# and close both lock descriptors so they cannot keep the card after a stop.
_run_tool() {
  local inherit=$1 name=$2
  shift 2
  mkdir -p "$job/phases" "$job/evidence"
  refuse_link "$job/evidence/argv.log" || return 2
  refuse_link "$job/phases/${name}.log" || return 2
  printf '%s\n' "$name: $*" >> "$job/evidence/argv.log"
  local t0=$SECONDS
  # Background plus wait is what lets SIGTERM run before a long bench finishes.
  if [ "$inherit" = 1 ]; then
    ( exec 7>&-; "$@" >"$job/phases/${name}.log" 2>&1 ) &
  else
    ( exec 7>&- 9>&-; "$@" >"$job/phases/${name}.log" 2>&1 ) &
  fi
  CHILD=$!
  local rc=0
  wait "$CHILD" || rc=$?
  CHILD=
  printf 'phase_s %s\n' "$((SECONDS - t0))" >> "$job/phases/${name}.log"
  "${py[@]}" apply-phase --job "$job" --phase "$name" --rc "$rc" < "$job/phases/${name}.log" >/dev/null || true
  return "$rc"
}

run_tool() { _run_tool 0 "$@"; }
run_tool_locked() { _run_tool 1 "$@"; }

copy_profiles() {
  local src="" dest="$job/scratch/profiles.json" card mode candidate
  if [ -n "${PROFILE_FILE:-}" ] && [ -f "$PROFILE_FILE" ] && [ ! -L "$PROFILE_FILE" ]; then
    src=$PROFILE_FILE
  elif [ -n "${A770B_PROFILES_FILE:-}" ] && [ -f "$A770B_PROFILES_FILE" ] && [ ! -L "$A770B_PROFILES_FILE" ]; then
    src=$A770B_PROFILES_FILE
  else
    card=${A770B_CARD:-}
    mode=${A770B_CARD_MODE:-inference}
    candidate="$A770B_PROJECT/config/registry/${card}.${mode}.json"
    if [ -n "$card" ] && [ -f "$candidate" ] && [ ! -L "$candidate" ]; then
      src=$candidate
    fi
  fi
  if [ -z "$src" ]; then
    # A bare GGUF suite can run from --model. It does not invent a registry file.
    if [ "${GGUF:-0}" = 1 ]; then
      unset A770B_PROFILES_FILE
      return 0
    fi
    return 1
  fi
  refuse_link "$dest" || return 1
  cp -- "$src" "$dest" || return 1
  chmod 600 "$dest" || return 1
  export A770B_PROFILES_FILE=$dest
  # The parent shell already exported the card registry's name list. env.sh
  # will not replace a list that is already set, so a candidate file's names
  # would never become this run's profiles. The names are data, not a script.
  unset A770B_PROFILES
  A770B_PROFILES=$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); profiles=data.get("profiles") if isinstance(data, dict) else None; print(" ".join(str(k) for k in profiles) if isinstance(profiles, dict) else "")' "$dest")
  export A770B_PROFILES
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
  local stages=$1 pass=$2
  local -a args=()
  if [ "$GGUF" = 1 ]; then
    args=(--model "$(pget artifact)" --ctx "$CTX")
    [ -n "$KV" ] && args+=(--kv "$KV")
    [ -n "$KV_V" ] && args+=(--kv-v "$KV_V")
    [ -n "$EXTRA" ] && args+=(--extra "$EXTRA")
    args+=(--reasoning "$(pget reasoning)")
    local budget
    budget=$(pget reasoning_budget)
    [ -n "$budget" ] && args+=(--reasoning-budget "$budget")
    export A770B_SERVED_BACKEND
  else
    args=("$PROFILE_NAME")
  fi
  # The scratch seat is this job's. --fresh is safe and pass 2 needs it.
  args+=("$job/scratch/seat" --fresh)
  [ -n "$stages" ] && args+=(--stages "$stages")
  local -a cmd
  if command -v a770b-qualify-suite >/dev/null 2>&1; then
    cmd=(a770b-qualify-suite "${args[@]}")
  elif [ "$STUB" = 1 ]; then
    return 1
  else
    cmd=(bash "$here/run_suite.sh" "${args[@]}")
  fi
  local log="$job/phases/suite-pass-${pass}.log"
  refuse_link "$job/evidence/argv.log" || return 2
  refuse_link "$log" || return 2
  printf '%s\n' "suite: ${cmd[*]}" >> "$job/evidence/argv.log"
  printf '%s\n' "profiles-file: ${A770B_PROFILES_FILE:-}" >> "$job/evidence/argv.log"
  # The suite's cleanup stops only the containers this suite recorded.
  # Phase 5 uses the same serving-pid file the monitor already follows.
  export A770B_QUALIFY_SERVING_PID_FILE="$job/evidence/serving.pid"
  A770B_LADDER_RUN=1 A770B_PROFILES_FILE=${A770B_PROFILES_FILE:-} A770B_SEAT="$job/scratch/seat" "${cmd[@]}" >"$log" 2>&1 &
  CHILD=$!
  plain_trunc "$job/suite.pid" || return 2
  printf '%s\n' "$CHILD" > "$job/suite.pid"
  refuse_link "$job/suite.start" || return 2
  "${py[@]}" starttime "$CHILD" > "$job/suite.start" 2>/dev/null || true
  chmod 600 "$job/suite.start" 2>/dev/null || true
  local rc=0
  wait "$CHILD" || rc=$?
  CHILD=
  "${py[@]}" apply-phase --job "$job" --phase suite --rc "$rc" --log "phases/suite-pass-${pass}.log" < "$log" >/dev/null || true
  return "$rc"
}

fail_job() {
  FINISH_STATE=failed
  FAIL_REASON=$1
  MARKER_NAME=FAILED
}

prepare_serve_env() {
  local backend image_row budget
  backend=$(pget backend)
  image_row=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print((d.get("row_fields") or {}).get("vllm_image") or "")' "$job/plan.json" 2>/dev/null || true)
  if [ -n "$image_row" ]; then
    export A770B_VLLM_IMAGE=$image_row
  fi
  case "$engine" in
    llama.cpp) select_serve_envelope "$backend" ;;
    vllm) select_serve_envelope vllm ;;
    sglang) select_serve_envelope sglang ;;
  esac
  [ -n "$KV" ] && export KV_K=$KV
  [ -n "$KV_V" ] && export KV_V=$KV_V
  export REASONING=$(pget reasoning)
  export THINKING_MODE=$REASONING
  budget=$(pget reasoning_budget)
  [ -n "$budget" ] && export THINKING_BUDGET=$budget
  if [ -n "$backend" ]; then
    export A770B_SERVED_BACKEND=$backend
  fi
}

capture_cid() {
  local live="" file
  # A reused server was not started by this job. Leave its id unrecorded so
  # stop_server does not tear it down.
  if [ -f "$job/phases/serve.log" ] && [ ! -L "$job/phases/serve.log" ] && grep -q "already up (" "$job/phases/serve.log"; then
    return 0
  fi
  if [ "$engine" = strata ]; then
    live=$(bash "$here/serve_strata.sh" id 2>/dev/null || true)
  else
    file=$A770B_DATA/logs/llamacpp-a770.cid
    if [ -f "$file" ] && [ ! -L "$file" ]; then
      live=$(tr -d '[:space:]' < "$file" || true)
    fi
  fi
  [ -n "$live" ] || return 0
  plain_trunc "$job/cid" || return 1
  printf '%s\n' "$live" > "$job/cid"
}

confirm_switches() {
  # profiling.json is a receipt, not an engine log. confirm_started looks for
  # the verbosity line or the per-request echo, which the receipt does not carry.
  local log=$job/phases/serve.log cid=""
  if [ "$engine" != strata ] && [ -f "$job/cid" ] && [ ! -L "$job/cid" ]; then
    cid=$(tr -d '[:space:]' < "$job/cid" || true)
  fi
  if [ -n "$cid" ]; then
    refuse_link "$job/phases/engine.log" || return 1
    "${A770B_DOCKER:-docker}" logs "$cid" >"$job/phases/engine.log" 2>&1 || true
    log=$job/phases/engine.log
  fi
  "${py[@]}" confirm --job "$job" --host "$A770B_HOST" --port "$A770B_PORT" --log "$log" >/dev/null 2>&1 || true
}

write_engine_evidence() {
  local pointer=$A770B_DATA/logs/serve-evidence.path dest=$job/evidence/engine-evidence.json model target
  if [ -f "$pointer" ] && [ ! -L "$pointer" ]; then
    target=$(head -n 1 "$pointer" | tr -d '[:space:]' || true)
    if [ -n "$target" ] && [ -f "$target" ] && [ ! -L "$target" ]; then
      refuse_link "$dest" || return 0
      cp -- "$target" "$dest" || true
      return 0
    fi
  fi
  [ -s "$job/phases/engine.log" ] || return 0
  case "$engine" in
    llama.cpp|vllm|sglang) ;;
    *) return 0 ;;
  esac
  refuse_link "$dest" || return 0
  model=$(pget artifact)
  case "$model" in
    /*) ;;
    *) model=$A770B_MODELS/$model ;;
  esac
  python3 "$here/engine_evidence.py" log --engine "$engine" --log "$job/phases/engine.log" --model "$model" --out "$dest" >/dev/null 2>&1 || true
}

note_serve_vram() {
  # llama.cpp already prints this line. Strata and a reused container do not.
  [ -f "$job/phases/serve.log" ] && [ ! -L "$job/phases/serve.log" ] && grep -q "VRAM after load:" "$job/phases/serve.log" && return 0
  local used cap pointer target from_file
  used=$(gpu_used_gib 2>/dev/null || true)
  case "$used" in
    ''|unavailable|*[!0-9.]*) return 0 ;;
  esac
  cap=${A770B_VRAM_CAP_GIB:-}
  pointer=$A770B_DATA/logs/serve-evidence.path
  if [ -f "$pointer" ] && [ ! -L "$pointer" ]; then
    target=$(head -n 1 "$pointer" | tr -d '[:space:]' || true)
    if [ -n "$target" ] && [ -f "$target" ] && [ ! -L "$target" ]; then
      from_file=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); v=d.get("configured_vram_cap_gib"); print("" if v is None else v)' "$target" 2>/dev/null || true)
      [ -n "$from_file" ] && cap=$from_file
    fi
  fi
  [ -n "$cap" ] || return 0
  refuse_link "$job/phases/serve.log" || return 0
  printf 'VRAM after load: %s GiB ≤ configured %s GiB\n' "$used" "$cap" >> "$job/phases/serve.log"
  "${py[@]}" apply-phase --job "$job" --phase serve --rc 0 < "$job/phases/serve.log" >/dev/null || true
}

snap_metrics() {
  [ "${STUB:-0}" = 1 ] && return 0
  [ "$engine" = strata ] && return 0
  local dest=$job/evidence/metrics-$1.prom url rc=0
  refuse_link "$dest" || return 0
  url="http://$A770B_HOST:$A770B_PORT/metrics"
  # vLLM's /metrics is open. llama.cpp and SGLang require the bearer.
  if [ "$engine" = vllm ] || [ "${A770B_SERVED_BACKEND:-}" = vllm ]; then
    curl --silent --show-error --max-time 10 "$url" >"$dest" || rc=$?
  else
    a770b_curl --silent --show-error --max-time 10 "$url" >"$dest" || rc=$?
  fi
  if [ "$rc" != 0 ]; then
    rm -f -- "$dest"
  fi
}

engine_line_count() {
  local cid=""
  if [ "$engine" = strata ] || [ ! -f "$job/cid" ] || [ -L "$job/cid" ]; then
    printf '0\n'
    return 0
  fi
  cid=$(tr -d '[:space:]' < "$job/cid" || true)
  if [ -z "$cid" ]; then
    printf '0\n'
    return 0
  fi
  "${A770B_DOCKER:-docker}" logs "$cid" 2>&1 | wc -l | tr -d '[:space:]'
}

mark_offset() {
  refuse_link "$job/evidence/log-offsets.txt" || return 0
  printf '%s %s\n' "$1" "$(engine_line_count)" >> "$job/evidence/log-offsets.txt"
}

capture_slice() {
  local name=$1 cid="" start=0 end=0
  [ "$engine" = strata ] && return 0
  [ -f "$job/cid" ] && [ ! -L "$job/cid" ] || return 0
  cid=$(tr -d '[:space:]' < "$job/cid" || true)
  [ -n "$cid" ] || return 0
  refuse_link "$job/evidence/engine-$name.log" || return 0
  start=$(awk -v n="before-$name" '$1 == n { v = $2 } END { print v + 0 }' "$job/evidence/log-offsets.txt" 2>/dev/null || printf '0')
  end=$(awk -v n="after-$name" '$1 == n { v = $2 } END { print v + 0 }' "$job/evidence/log-offsets.txt" 2>/dev/null || printf '0')
  if [ "${end:-0}" -le "${start:-0}" ]; then
    : > "$job/evidence/engine-$name.log"
    return 0
  fi
  "${A770B_DOCKER:-docker}" logs "$cid" 2>&1 | awk -v s="$start" -v e="$end" 'NR > s && NR <= e' > "$job/evidence/engine-$name.log" || true
}

# Re-parse after the log slice and the metrics pair exist. run_tool already parsed once.
bracket_measure() {
  local name=$1
  shift
  mark_offset "before-$name"
  snap_metrics "before-$name" || true
  "$@"
  local rc=$?
  mark_offset "after-$name"
  snap_metrics "after-$name" || true
  capture_slice "$name"
  if [ -f "$job/phases/${name}.log" ] && [ ! -L "$job/phases/${name}.log" ]; then
    "${py[@]}" apply-phase --job "$job" --phase "$name" --rc "$rc" < "$job/phases/${name}.log" >/dev/null || true
  fi
  return "$rc"
}

start_observers() {
  [ "${STUB:-0}" = 1 ] && return 0
  plain_trunc "$job/VRAM_SAMPLE" || return 0
  refuse_link "$job/evidence/vram-samples.txt" || return 0
  refuse_link "$job/evidence/host-memory.jsonl" || return 0
  # Refuse a symlink before any sampler starts, or a bad pid file leaves VRAM running.
  plain_trunc "$job/evidence/serving.pid" || return 0
  (
    trap - TERM INT EXIT
    exec 7>&- 9>&-
    while [ -f "$job/VRAM_SAMPLE" ]; do
      used=$(gpu_used_gib 2>/dev/null || true)
      printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${used:-unavailable}" >> "$job/evidence/vram-samples.txt"
      sleep 5
    done
  ) &
  VRAM_PID=$!
  # Copy the phase's engine before the subshell. A local in the caller is
  # visible here; the tracker must not depend on that local still being set.
  track_engine=${engine:-}
  # Phase 3 identity is $job/cid: docker's pid, or the Strata pid. The loop
  # ends when stop_server signals it. It does not hold the lock fds.
  (
    trap - TERM INT EXIT
    exec 7>&- 9>&-
    last=""
    while [ ! -f "$job/SERVING_CLEARED" ]; do
      pid=""
      cid=""
      if [ -f "$job/cid" ] && [ ! -L "$job/cid" ]; then
        cid=$(tr -d '[:space:]' < "$job/cid" || true)
      fi
      if [ -n "$cid" ] && [ ! -f "$job/SERVING_CLEARED" ]; then
        if [ "$track_engine" = strata ]; then
          pid=$(bash "$here/serve_strata.sh" pid 2>/dev/null || true)
        elif printf '%s\n' "$cid" | grep -Eq '^[0-9a-f]{64}$'; then
          pid=$("$A770B_DOCKER" inspect --format '{{.State.Pid}}' "$cid" 2>/dev/null || true)
        fi
      fi
      # Rename onto the pid file. A truncate in place is empty for a reader.
      if [ ! -f "$job/SERVING_CLEARED" ] && [ "$pid" != "$last" ] \
          && printf '%s\n' "$pid" | grep -Eq '^[1-9][0-9]*$'; then
        tmp="$job/evidence/.serving.pid.$$"
        printf '%s\n' "$pid" > "$tmp"
        mv -f -- "$tmp" "$job/evidence/serving.pid"
        last=$pid
      fi
      sleep 0.5 || exit 0
    done
  ) &
  TRACKER=$!
  ( trap - TERM INT EXIT; exec 7>&- 9>&-; exec python3 -I -B "$here/host_memory.py" monitor --out "$job/evidence/host-memory.jsonl" --interval 5 --owner-pid "$$" --pid-file "$job/evidence/serving.pid" ) &
  MEM=$!
}

model_path() {
  local name=$1
  case "$name" in
    /*) printf '%s\n' "$name" ;;
    *) printf '%s\n' "$A770B_MODELS/$name" ;;
  esac
}

resolve_alias() {
  local cfg name
  if [ "$engine" = strata ]; then
    cfg=$(model_path "$(pget artifact)")
    name=$(bash "$here/serve_strata.sh" config-model "$cfg" 2>/dev/null || true)
    if [ -n "$name" ]; then
      export A770B_ALIAS=$name
      return 0
    fi
  fi
  export A770B_ALIAS=${A770B_ALIAS:-local-builder}
}

resolve_tokenizer() {
  local tok cfg dir
  tok=$(pget tokenizer)
  if [ -n "$tok" ]; then
    printf '%s\n' "$tok"
    return 0
  fi
  if [ "$engine" = strata ]; then
    cfg=$(model_path "$(pget artifact)")
    tok=$(bash "$here/serve_strata.sh" tokenizer "$cfg" 2>/dev/null || true)
    if [ -n "$tok" ]; then
      printf '%s\n' "$tok"
      return 0
    fi
  fi
  dir=$(model_path "$(pget artifact)")
  if [ -d "$dir" ] && { [ -f "$dir/tokenizer.json" ] || [ -f "$dir/tokenizer_config.json" ]; }; then
    printf '%s\n' "$dir"
    return 0
  fi
  return 1
}

record_unmeasured() {
  local name=$1 reason=$2
  mkdir -p "$job/phases" "$job/evidence"
  refuse_link "$job/evidence/argv.log" || return 2
  refuse_link "$job/phases/${name}.log" || return 2
  printf '%s\n' "$name: $reason" >> "$job/evidence/argv.log"
  printf '%s\n' "$reason" > "$job/phases/${name}.log"
  python3 -c 'import json,sys; print(json.dumps({"skipped": True, "why": {"command": "not run", "rc": 0, "log": "phases/" + sys.argv[1] + ".log", "reason": sys.argv[2]}}))' "$name" "$reason" \
    | "${py[@]}" apply-phase --job "$job" --phase "$name" --rc 0 >/dev/null
}

sample_idle() {
  local used
  refuse_link "$job/evidence/idle.txt" || return 0
  used=$(gpu_used_gib 2>/dev/null || true)
  printf '%s\n' "${used:-unavailable}" > "$job/evidence/idle.txt"
}

measure_speed() {
  local tok alias
  local -a sizes=()
  tok=$(resolve_tokenizer || true)
  alias=${A770B_ALIAS:-local-builder}
  if [ -z "$tok" ]; then
    record_unmeasured speed "no tokenizer, speed was not measured"
    return 0
  fi
  mapfile -t sizes < <(pget speed_sizes)
  # bench_http.sh treats an empty --pp as the four default sizes, which can exceed ctx.
  if [ "${#sizes[@]}" -eq 0 ] || [ -z "${sizes[0]:-}" ]; then
    record_unmeasured speed "no prompt size fits this context, speed was not measured"
    return 0
  fi
  bracket_measure speed run_tool speed bash "$here/bench_http.sh" \
    --base-url "http://$A770B_HOST:$A770B_PORT" \
    --served-model-name "$alias" \
    --tokenizer "$tok" \
    --save-result "$job/evidence/speed-bench.json" \
    --pp "${sizes[@]}"
}

measure_window() {
  local tok alias
  tok=$(resolve_tokenizer || true)
  alias=${A770B_ALIAS:-local-builder}
  if [ -z "$tok" ]; then
    record_unmeasured window "no tokenizer, the sweep was not measured"
    return 0
  fi
  case "$engine" in
    vllm|sglang|strata) export A770B_SWEEP_STREAM=1 ;;
  esac
  bracket_measure window run_tool window bash "$here/ctx_sweep.sh" --tokenize openai --model "$alias" --tokenizer "$tok" 8000 "$(pget far_end)"
}

measure_depth() {
  local tok alias
  tok=$(resolve_tokenizer || true)
  alias=${A770B_ALIAS:-local-builder}
  if [ -z "$tok" ]; then
    record_unmeasured depth "no tokenizer, the probe was not measured"
    return 0
  fi
  case "$engine" in
    vllm|sglang|strata) export A770B_SWEEP_STREAM=1 ;;
  esac
  bracket_measure depth run_tool depth bash "$here/depth_probe.sh" "$(pget far_end)" --gen "$(pget gen)" --tokenize openai --model "$alias" --tokenizer "$tok"
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
  prepare_serve_env
  if command -v a770b-qualify-gate >/dev/null 2>&1; then
    run_tool gate a770b-qualify-gate --engine "$engine" --image "$image" --backend "$(pget backend)" || {
      fail_job preflight
      return
    }
  elif [ "$STUB" = 1 ]; then
    fail_job preflight
    return
  elif [ "$engine" = strata ]; then
    local cfg
    cfg=$(model_path "$(pget artifact)")
    run_tool gate bash "$here/serve_strata.sh" config-model "$cfg" || { fail_job preflight; return; }
    refuse_link "$job/phases/gate.log" || { fail_job preflight; return; }
    bash "$here/serve_strata.sh" tokenizer "$cfg" >>"$job/phases/gate.log" 2>&1 || { fail_job preflight; return; }
    resolve_alias
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
    refuse_link "$job/phases/pre-hook.log" || { fail_job pre-hook; return; }
    if ! "$pre" "$job" >"$job/phases/pre-hook.log" 2>&1; then
      fail_job pre-hook
      return
    fi
  fi
  if pause_for serve; then
    return
  fi
  phase serve
  sample_idle
  start_observers
  if [ "$STUB" = 1 ]; then
    local -a serve_argv=()
    mapfile -t serve_argv < <(pget serve_argv)
    run_tool serve a770b-qualify-serve "${serve_argv[@]}" || { fail_job load; return; }
  elif [ "$engine" = "llama.cpp" ]; then
    local -a extra_argv=() clean=()
    local item
    mapfile -t extra_argv < <(pget extra)
    for item in "${extra_argv[@]}"; do
      [ -n "$item" ] && clean+=("$item")
    done
    if ! run_tool_locked serve bash "$here/serve_compose.sh" start "$(pget artifact)" "$CTX" "${clean[@]}"; then
      fail_job load
      return
    fi
    capture_cid
  else
    copy_profiles || { fail_job load; return; }
    if ! run_tool_locked serve bash "$A770B_PROJECT/skills/local-build/scripts/local-build.sh" serve "$PROFILE_NAME"; then
      fail_job load
      return
    fi
    capture_cid
    resolve_alias
  fi
  if [ "$STUB" != 1 ]; then
    note_serve_vram || true
    confirm_switches || true
    write_engine_evidence || true
    snap_metrics after-serve || true
  fi
  phase smoke
  run_tool smoke bash "$here/qualify_smoke.sh" "$job" || true
  phase speed
  if [ "$STUB" = 1 ]; then
    run_tool speed a770b-qualify-speed --ctx "$CTX" || true
  else
    measure_speed || true
  fi
  if [ "$(pget skip_window)" != 1 ]; then
    phase window
    if [ "$STUB" = 1 ]; then
      run_tool window a770b-qualify-sweep --to "$(pget far_end)" || true
    else
      measure_window || true
    fi
  fi
  phase depth
  if [ "$STUB" = 1 ]; then
    run_tool depth a770b-qualify-depth --gen "$(pget gen)" || true
  else
    measure_depth || true
  fi
  snap_metrics after-depth || true
  phase memory
  if [ "$STUB" = 1 ]; then
    run_tool memory a770b-qualify-memory --interval 5 --owner-pid "$$" || true
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
    plain_trunc "$job/stage-start" || { fail_job preflight; return; }
    date +%s > "$job/stage-start"
    local stages pass refs left
    stages=$(pget stages | paste -sd, -)
    run_suite_once "$stages" 1 || true
    if [ "$(pget refs_apply)" = 1 ] && [ "$(pget refs_repeat)" -gt 1 ] && [ ! -f "$job/DRIVER_ERROR" ]; then
      left=$(( $(pget refs_repeat) - 1 ))
      refs=$(pget reference_stages | paste -sd, -)
      pass=2
      while [ "$left" -gt 0 ] && [ -n "$refs" ]; do
        [ -f "$job/SUITE_SKIP" ] && break
        [ -f "$job/DRIVER_ERROR" ] && break
        run_suite_once "$refs" "$pass" || true
        pass=$((pass + 1))
        left=$((left - 1))
      done
    fi
  fi
  if [ -f "$job/DRIVER_ERROR" ]; then
    fail_job driver
  fi
}

cmd_run() {
  [ -n "${1:-}" ] || die "run needs a job directory"
  job=$1
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
  plain_trunc "$job/evidence/argv.log" || die "refusing a symlink argv log"
  plain_trunc "$job/owned.cids" || die "refusing a symlink owned-container list"
  export A770B_OWNED_CIDS_FILE=$job/owned.cids
  if ! take_card; then
    merge '{"state":"failed","reason":"lock","event":"lock refused"}'
    plain_trunc "$job/FAILED" || die "refusing a symlink marker"
    MARKER=1
    exit 2
  fi
  RUN_PID=$$
  RUN_START=$("${py[@]}" starttime "$$" || true)
  merge "{\"state\":\"running\",\"phase\":\"preflight\",\"pid\":$RUN_PID,\"pid_starttime\":\"${RUN_START}\",\"reason\":null,\"event\":\"running\"}"
  plain_trunc "$job/HEARTBEAT" || die "refusing a symlink heartbeat"
  plain_trunc "$job/WATCH" || die "refusing a symlink watch flag"
  plain_trunc "$job/cid" || die "refusing a symlink container id"
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
  local n=0 state reason
  while [ "$n" -lt 50 ]; do
    if [ -f "$job/status.json" ]; then
      state=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("state",""))' "$job/status.json" 2>/dev/null || true)
      case "$state" in
        running|finished|stopped)
          printf 'job %s\n' "$id"
          printf 'directory %s\n' "$job"
          return 0
          ;;
        failed)
          reason=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("reason") or "")' "$job/status.json" 2>/dev/null || true)
          if [ "$reason" = lock ]; then
            printf '⛔ runner refused the lock or the seat\n' >&2
            return 2
          fi
          printf 'job %s\n' "$id"
          printf 'directory %s\n' "$job"
          return 0
          ;;
      esac
    fi
    if [ -f "$job/DONE" ] || [ -f "$job/STOPPED" ] || [ -f "$job/FAILED" ]; then
      printf 'job %s\n' "$id"
      printf 'directory %s\n' "$job"
      return 0
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
  # plan creates nothing. A refusal, including Strata, returns before any
  # temp file or job directory exists. The payload stays in the shell until
  # the plan is accepted.
  local payload tmp
  if ! payload=$("${py[@]}" plan "$@"); then
    return 2
  fi
  tmp=$(mktemp)
  printf '%s\n' "$payload" > "$tmp"
  if [ -L "$A770B_DATA" ]; then
    rm -f -- "$tmp"
    die "refusing a symlink in the job path"
  fi
  if run_pid_alive "$A770B_DATA/logs/run.pid" >/dev/null 2>&1; then
    rm -f -- "$tmp"
    die "a run is in flight on this seat"
  fi
  mkdir -p "$A770B_DATA/logs" "$A770B_DATA/results"
  if ! probe_card; then
    rm -f -- "$tmp"
    die "the card lock is held"
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
    # Same argv shape as the detached runner, so stop can match this pid.
    exec bash "$here/qualify.sh" run --job "$job"
  fi
  if [ "$launch" = systemd ]; then
    local -a args=() item
    forwarded_env
    args=(systemd-run --user --collect --unit="qualify-$id" -p KillMode=mixed -p TimeoutStopSec=120 -p OOMPolicy=continue --working-directory="$A770B_PROJECT")
    for item in "${FORWARDED[@]}"; do
      [ -n "$item" ] || continue
      args+=(--setenv="$item")
    done
    args+=(-- bash "$here/qualify.sh" run --job "$job")
    refuse_link "$job/launch.log" || die "refusing a symlink launch log"
    if ! "${args[@]}" >>"$job/launch.log" 2>&1; then
      die "systemd-run failed"
    fi
  else
    refuse_link "$job/launch.log" || die "refusing a symlink launch log"
    setsid nohup bash "$here/qualify.sh" run --job "$job" >>"$job/launch.log" 2>&1 </dev/null &
  fi
  wait_handshake
}

cmd_status() {
  local id=""
  if [ $# -gt 0 ]; then
    case "$1" in
      --*) die "unknown qualify option $1" ;;
    esac
    id=$1
    shift
  fi
  [ $# -eq 0 ] || die "unknown qualify option $1"
  if [ -z "$id" ]; then
    id=$("${py[@]}" pick) || exit 2
  fi
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  "${py[@]}" status-show --job "$job"
}

cmd_wait() {
  [ $# -ge 1 ] || die "wait needs a job"
  local id=$1
  shift
  local timeout=86400
  while [ $# -gt 0 ]; do
    case "$1" in
      --timeout)
        [ $# -ge 2 ] || die "timeout needs a value"
        timeout=$2
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
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then exit 124; fi
    sleep 0.2
  done
}

cmd_result() {
  local id="" json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1; shift ;;
      --*) die "unknown qualify option $1" ;;
      *)
        [ -z "$id" ] || die "unknown qualify option $1"
        id=$1
        shift
        ;;
    esac
  done
  [ -n "$id" ] || die "result needs a job"
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  local shown state
  shown=$("${py[@]}" status-show --job "$job")
  state=$(printf '%s' "$shown" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
  if [ "$json" = 1 ]; then
    if [ ! -f "$job/result.json" ] || [ -L "$job/result.json" ]; then
      printf '⛔ no result.json\n' >&2
      exit 4
    fi
    cat "$job/result.json"
  elif [ -f "$job/report.md" ] && [ ! -L "$job/report.md" ]; then
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
  [ $# -ge 1 ] || die "export needs a job"
  local id=$1
  shift
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  "${py[@]}" export --job "$job" "$@"
}

cmd_stop() {
  [ $# -ge 1 ] || die "stop needs a job"
  local id=$1
  shift
  [ $# -eq 0 ] || die "unknown qualify option $1"
  job=$(job_path "$id") || exit 2
  [ -d "$job" ] && [ ! -L "$job" ] || die "no such job"
  local pid start launch
  pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("pid") or "")' "$job/status.json" 2>/dev/null || true)
  start=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("pid_starttime") or "")' "$job/status.json" 2>/dev/null || true)
  if ! "${py[@]}" identity --pid "$pid" --starttime "$start" --job "$job"; then
    die "runner identity does not match"
  fi
  plain_trunc "$job/STOP_REQUESTED" || die "refusing a symlink stop request"
  chmod 600 "$job/STOP_REQUESTED" || true
  if run_pid_alive "$A770B_DATA/logs/run.pid" >/dev/null 2>&1; then
    refuse_link "$job/phases/stop-run.log" || die "refusing a symlink stop log"
    bash "$A770B_PROJECT/skills/local-build/scripts/local-build.sh" stop-run >>"$job/phases/stop-run.log" 2>&1 || true
  fi
  launch=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("launch") or "")' "$job/job.json" 2>/dev/null || true)
  if [ "$launch" = systemd ]; then
    refuse_link "$job/phases/stop-run.log" || die "refusing a symlink stop log"
    systemctl --user kill -s TERM --kill-who=main "qualify-$id" >>"$job/phases/stop-run.log" 2>&1 || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  local start_s now state
  start_s=$(date +%s)
  while true; do
    state=$(${py[@]} status-show --job "$job" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
    case "$state" in
      finished|failed|lost|stopped) exit 0 ;;
    esac
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
    [ -n "${2:-}" ] || die "run needs a job directory"
    cmd_run "$2"
    ;;
  status) cmd_status "$@" ;;
  wait) cmd_wait "$@" ;;
  result) cmd_result "$@" ;;
  export) cmd_export "$@" ;;
  stop) cmd_stop "$@" ;;
  list) cmd_list ;;
  *) usage ;;
esac
