#!/usr/bin/env bash
# build_local.sh — dispatch a brief to the local builder through opencode, inside the sandbox.
#   build_local.sh <worktree> <brief-file-or-text>
# Env: PROFILE (required: a name in A770B_PROFILES; there is no default), LOCAL_AGENT, LOCAL_TIMEOUT (s, a caller fence; unset or 0 means no clock), SANDBOX=0 to run unconfined (testing).
# The opencode config is RENDERED per run from config/opencode.profile.template.jsonc with the server URL, the
# profile's context window and the output limit from env.sh, so the model's client and the server can never disagree.
# Rules: the target is a self-contained clone that is not a protected checkout (guard.sh); opencode runs with
# --dir <worktree> inside bubblewrap (sandbox_run.sh); every opencode call takes `< /dev/null`; judged by the deliverable.
# Exit codes: 2 refused (worktree), 3 no model server; the opencode exit code is informational only.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
_SANDBOX_OOM="$A770B_DATA/logs/sandbox-oom.json"
_clear_stale_capture_markers_if_unlocked(){
  local lock
  mkdir -p "$A770B_DATA/logs"
  lock=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$A770B_DATA/logs/local-build.lock")
  if _inherited_lock_valid 9 "$lock" "${A770B_CARD_LOCK_PATH:-}"; then
    rm -f -- "$A770B_DATA/logs/usage.json" "$A770B_DATA/logs/run-since" "$_SANDBOX_OOM"
    return 0
  fi
  (
    exec 8>>"$lock"
    flock -n 8 || exit 1
    rm -f -- "$A770B_DATA/logs/usage.json" "$A770B_DATA/logs/run-since" "$_SANDBOX_OOM"
  ) 2>/dev/null || true
}
_clear_stale_capture_markers_if_unlocked
WT=$(guard_worktree "${1:?worktree path}"); BRIEF="${2:?brief file or task text}"
PROFILE="${PROFILE:?PROFILE is required — there is no default profile; pass a name from A770B_PROFILES}"
a770b_is_profile "$PROFILE" || { echo "⛔ PROFILE must be one of: $A770B_PROFILES" >&2; exit 2; }
card_lock; seat_lock "$WT"
# A previous run's usage file and log stamp are not this run's count.
# Removed only after locks are held so a refused concurrent caller cannot clear an active run's capture.
# The tap writes a new usage file when it starts. The stamp is written when this run starts.
rm -f -- "$A770B_DATA/logs/usage.json" "$A770B_DATA/logs/run-since" "$_SANDBOX_OOM"
CTX=$(a770b_profile_var "$PROFILE" CTX)
ENDPOINT="http://$A770B_HOST:$A770B_PORT"
if [ "${A770B_SERVED_ENGINE:-}" = strata ] && [ "${A770B_ATTACHED:-}" != 1 ]; then
  bash "$A770B_PROJECT/harness/serve_strata.sh" verify "$A770B_ALIAS" --ctx "$CTX" \
    || { echo "⛔ the managed Strata launch no longer matches this run" >&2; exit 3; }
elif [ "${A770B_SERVED_ENGINE:-}" = strata ]; then
  # Recheck the endpoint in the verified auth mode before rendering the client.
  strata_verdict=$(python3 "$A770B_PROJECT/harness/strata_attach.py" attach --base-url "$ENDPOINT" --model "$A770B_ALIAS" --ctx "$CTX" --auth "${A770B_STRATA_AUTH:?the Strata attach records its auth mode}") \
    || { echo "⛔ the attached Strata endpoint at $ENDPOINT no longer passes the attach check: $strata_verdict" >&2; exit 3; }
else
  a770b_curl -sf --max-time 5 "$ENDPOINT/v1/models" >/dev/null || { echo "⛔ no model server answering at $ENDPOINT — start it first (local-build.sh serve <card>)" >&2; exit 3; }
fi
# render the profile config (server URL, key, window, output limit)
CFG="$A770B_DATA/logs/opencode.$PROFILE.jsonc"
export A770B_RENDER_SEAT="$WT"; a770b_render_profile "$PROFILE" "$CTX" "$CFG" || exit 2
export OPENCODE_CONFIG="$CFG"
MODEL="${LOCAL_MODEL:-local-a770/$A770B_ALIAS}"
mkdir -p "$WT/Local_Documentation/briefs"
stamp=$(date +%Y%m%d-%H%M%S)
if [ -f "$BRIEF" ]; then cp -- "$BRIEF" "$WT/Local_Documentation/briefs/brief-$stamp.md"; else printf '%s\n' "$BRIEF" > "$WT/Local_Documentation/briefs/brief-$stamp.md"; fi
# the run specification, if any: the prepared context lands in the brief copy, the echo beside the rendered profile
if [ -n "${A770B_SPEC:-}" ]; then
  python3 "$A770B_PROJECT/harness/render_profile.py" context --spec "$A770B_SPEC" --seat "$WT" --brief-copy "$WT/Local_Documentation/briefs/brief-$stamp.md" --echo "$CFG.echo.json" || exit 2
  printf '%s\n' "$CFG.echo.json" > "$A770B_DATA/logs/last-build.echo.path"
else
  rm -f "$A770B_DATA/logs/last-build.echo.path"
fi
LOG="$A770B_DATA/logs/build-$stamp.log"
printf '%s\n' "$LOG" > "$A770B_DATA/logs/last-build.log.path"
echo "▶ profile=$PROFILE ctx=$CTX model=$MODEL endpoint=$ENDPOINT worktree=$WT brief=Local_Documentation/briefs/brief-$stamp.md log=$LOG sandbox=${SANDBOX:-1}"
before=$(safe_git "$WT" status --porcelain | wc -l)
# The container log before this instant is not this run. Capture reads the stamp.
# Whole seconds would keep a line written earlier in that same second.
date -u +%Y-%m-%dT%H:%M:%S.%NZ > "$A770B_DATA/logs/run-since"
set +e
AGENT_ARGS=(); [ -n "${LOCAL_AGENT:-}" ] && AGENT_ARGS=(--agent "$LOCAL_AGENT")
PROMPT="Read Local_Documentation/briefs/brief-$stamp.md in this project and carry out the task it describes. Work only inside this project directory. Do not run git commit, push, merge or any docker/systemctl command."
RUNPID="$A770B_DATA/logs/run.pid"                              # the process-group leader, for the life of the run: local-build.sh stop-run ends exactly it
export A770B_WORKER_PGID_FILE="$A770B_DATA/logs/run.worker-pgid"  # the worker's own group, published by the leader after spawn
_leader_proc_gone(){
  local leader
  [ -f "$RUNPID" ] || return 1
  leader=$(tr -d '[:space:]' < "$RUNPID" 2>/dev/null || true)
  [[ "$leader" =~ ^[1-9][0-9]*$ ]] || return 1
  [ ! -d "/proc/$leader" ]
}
_orphan_watch_pid=""
_stop_orphan_watch(){
  local w="${_orphan_watch_pid:-}"
  [ -n "$w" ] || return 0
  _orphan_watch_pid=""
  kill "$w" 2>/dev/null || true
  wait "$w" 2>/dev/null || true
}
# While the pipeline is blocked on the worker's end of the tee pipe, a leader that has already died cannot
# forward anything. Signal only while run.pid is still that dead pid, so a later run's record is not this watch's.
# This shell's pid is the owner, captured by the caller before the background start. The watch is not the run:
# it does not inherit the card lock (FD 9), the seat lock (FD 7), or this shell's stdout, and it exits when
# that owner pid is gone. A SIGKILL of this shell does not run the EXIT trap; without this, the watch would
# keep those locks and the caller's pipe open.
_orphan_watch_loop(){
  local owner="${1:-}" leader now
  [[ "$owner" =~ ^[1-9][0-9]*$ ]] || exit 0
  while true; do
    kill -0 "$owner" 2>/dev/null || exit 0                     # this shell is gone: do not touch a later run's record
    leader=$(tr -d '[:space:]' < "$RUNPID" 2>/dev/null || true)
    if [[ "$leader" =~ ^[1-9][0-9]*$ ]] && [ ! -d "/proc/$leader" ] && [ -f "$A770B_WORKER_PGID_FILE" ]; then
      now=$(tr -d '[:space:]' < "$RUNPID" 2>/dev/null || true)
      if [ "$now" = "$leader" ]; then
        signal_recorded_worker_group "$A770B_WORKER_PGID_FILE" || true
      fi
    fi
    sleep 0.25 || true
  done
}
trap 'rm -f "$RUNPID"; _stop_orphan_watch' EXIT INT TERM      # run.pid must not outlive the run; the watch must not either
# stop-run's owner channel: USR1 to this shell, sent before the TERM to the leader. It does not depend on the
# run.interrupted receipt, so a stopped worker that handles TERM and exits 0 is still reported as interrupted.
# This shell declares itself to the leader (A770B_RUN_OWNER_PID in the leader's environment); stop-run sends USR1
# only to a parent that is this script, is declared by the leader, and has a USR1 handler installed — never to a
# legacy owner (no handler: USR1 would kill it) or an unrelated parent whose arguments merely name this file.
STOP_REQUESTED=0
trap 'STOP_REQUESTED=1' USR1
# The run's process-group leader is harness/run_lead.py, exec'd into the subshell whose pid run.pid records.
# It is the stop record that needs nothing else: on stop-run's TERM it forwards the signal, waits, and exits
# 143 whatever the worker's own status, so neither the run.interrupted receipt nor the USR1 owner channel has
# to succeed for a stopped run to stay ungraded. A set LOCAL_TIMEOUT is a caller fence the leader also keeps
# (exit 124, timeout(1)'s convention); no LOCAL_TIMEOUT, or LOCAL_TIMEOUT=0, means no clock.
# The worker is a second process group. The leader publishes it in A770B_WORKER_PGID_FILE and removes that
# file only after reaping the worker. If this pipeline returns 137, or the leader is already dead while the
# file remains, the guarded helper ends that group so the tee pipe can close. A live leader still forwards.
exec_lead(){
  local fence=()
  [ -z "${LOCAL_TIMEOUT:-}" ] || fence=(--timeout "$LOCAL_TIMEOUT")   # 0 is no clock, as for timeout(1)
  export A770B_RUN_OWNER_PID="$$"                                # this shell, the leader's parent: the owner stop-run may tell
  # -I -B: ignore PYTHONPATH and the user site, write no bytecode. The absolute
  # script and the -- / --timeout delimiter are what run_lead_pid matches,
  # on this form and on the legacy form without these two flags.
  exec python3 -I -B "$(realpath -e -- "$A770B_PROJECT/harness/run_lead.py")" "${fence[@]}" -- "$@"
}
# A previous leader killed before it could reap leaves the worker file beside a dead run.pid. End that group
# before this run's leader replaces the file. A live leader is left to forward its own signals.
if _leader_proc_gone && [ -f "$A770B_WORKER_PGID_FILE" ]; then
  signal_recorded_worker_group "$A770B_WORKER_PGID_FILE" || true
  rm -f -- "$A770B_WORKER_PGID_FILE"                       # acted on, or not our group; this run's leader publishes a new file
fi
# Both arms run opencode. Refuse a binary that is not OpenCode 1 before either of them starts.
A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1 || exit 2
_orphan_watch_loop "$$" 7>&- 9>&- >/dev/null 2>>"$LOG" &
_orphan_watch_pid=$!
# tee is still running after a SIGKILL of this shell, for as long as the worker holds the pipe.
# It must not keep FD 7 or FD 9, or the card lock outlives this shell with the worker.
if [ "${SANDBOX:-1}" = 1 ]; then
  # The helper reads this after the pipeline returns, outside the leader's fence.
  export A770B_SANDBOX_RECORD_FILE="$_SANDBOX_OOM"
  ( printf '%s\n' "$BASHPID" > "$RUNPID" && exec_lead bash "$(dirname "$0")/sandbox_run.sh" "$WT" "$CFG" -- \
      opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null 7>&- 9>&- ) 2>&1 | tee "$LOG" 7>&- 9>&-
else
  echo "⚠ SANDBOX=0: running opencode UNCONFINED (testing only)" | tee -a "$LOG" 7>&- 9>&-
  ( cd "$WT" && printf '%s\n' "$BASHPID" > "$RUNPID" && exec_lead "$(a770b_opencode_bin)/opencode" run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null 7>&- 9>&- ) 2>&1 | tee "$LOG" 7>&- 9>&-
fi
rc=${PIPESTATUS[0]}
# 137: the leader was SIGKILL'd and could not forward. The other arm: it is already dead and the worker file
# is still there (the leader removes the file only after its own reap). Signal that group before the OOM
# helper, which can wait on the scope. The helper no-ops when the recorded identity does not match.
if [ "$rc" -eq 137 ] || { _leader_proc_gone && [ -f "$A770B_WORKER_PGID_FILE" ]; }; then
  signal_recorded_worker_group "$A770B_WORKER_PGID_FILE" || true
fi
# Continue policy is graded from the journal, so the leader's code is enough.
# A helper failure must not replace the builder's own exit status. Lock fds stay here.
if [ "${SANDBOX:-1}" = 1 ] && [ -f "$_SANDBOX_OOM" ]; then
  python3 -I -B "$A770B_PROJECT/harness/sandbox_oom.py" "$_SANDBOX_OOM" "$rc" 7>&- 9>&- || true
fi
_stop_orphan_watch
rm -f "$RUNPID"
if [ "$STOP_REQUESTED" = 1 ] && ! { [ "$rc" -ge 128 ] 2>/dev/null && [ "$rc" -le 159 ] 2>/dev/null; }; then
  echo "▶ local-build.sh stop-run ended this run (opencode exit=$rc after the stop request) — reported as interrupted (143)"
  rc=143
fi
set -e
echo "▶ opencode exit=$rc (informational — judge by the deliverable)"
# The previews are diagnostics. `sed -n 1,20p` reads all its input, so a long listing cannot SIGPIPE its producer
# under pipefail, and `|| true` keeps any preview failure from replacing the builder's own exit status ($rc).
echo "▶ worktree changes before=$before after=$(safe_git "$WT" status --porcelain | wc -l):"; { safe_git "$WT" status --porcelain | sed -n '1,20p'; } || true
echo "▶ files touched during the run:"; { find "$WT" -path "$WT/.git" -prune -o -type f -newer "$WT/Local_Documentation/briefs/brief-$stamp.md" -print | sed -n '1,20p'; } || true
if [ "$rc" -ge 128 ] 2>/dev/null && [ "$rc" -le 159 ] 2>/dev/null; then
  exit "$rc"
fi
