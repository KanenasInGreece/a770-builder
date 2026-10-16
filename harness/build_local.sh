#!/usr/bin/env bash
# build_local.sh — dispatch a brief to the local builder through opencode, inside the sandbox.
#   build_local.sh <worktree> <brief-file-or-text>
# Env: PROFILE (required: a name in A770B_PROFILES; there is no default), LOCAL_AGENT, LOCAL_TIMEOUT (s, a caller fence; unset means no clock), SANDBOX=0 to run unconfined (testing).
# The opencode config is RENDERED per run from config/opencode.profile.template.jsonc with the server URL, the
# profile's context window and the output limit from env.sh, so the model's client and the server can never disagree.
# Rules: the target is a self-contained clone that is not a protected checkout (guard.sh); opencode runs with
# --dir <worktree> inside bubblewrap (sandbox_run.sh); every opencode call takes `< /dev/null`; judged by the deliverable.
# Exit codes: 2 refused (worktree), 3 no model server; the opencode exit code is informational only.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
# A previous run's usage file and log stamp are not this run's count.
# Removed before any exit, so a refused run cannot leave them for the capture.
# The tap writes a new usage file when it starts. The stamp is written when this run starts.
rm -f -- "$A770B_DATA/logs/usage.json" "$A770B_DATA/logs/run-since"
WT=$(guard_worktree "${1:?worktree path}"); BRIEF="${2:?brief file or task text}"
PROFILE="${PROFILE:?PROFILE is required — there is no default profile; pass a name from A770B_PROFILES}"
a770b_is_profile "$PROFILE" || { echo "⛔ PROFILE must be one of: $A770B_PROFILES" >&2; exit 2; }
CTX=$(a770b_profile_var "$PROFILE" CTX)
ENDPOINT="http://$A770B_HOST:$A770B_PORT"
curl -sf --max-time 5 -H "Authorization: Bearer $(a770b_api_key)" "$ENDPOINT/v1/models" >/dev/null || { echo "⛔ no model server answering at $ENDPOINT — start it first (local-build.sh serve <card>)" >&2; exit 3; }
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
date -u +%Y-%m-%dT%H:%M:%SZ > "$A770B_DATA/logs/run-since"
set +e
AGENT_ARGS=(); [ -n "${LOCAL_AGENT:-}" ] && AGENT_ARGS=(--agent "$LOCAL_AGENT")
PROMPT="Read Local_Documentation/briefs/brief-$stamp.md in this project and carry out the task it describes. Work only inside this project directory. Do not run git commit, push, merge or any docker/systemctl command."
RUNPID="$A770B_DATA/logs/run.pid"                              # the process-group leader, for the life of the run: local-build.sh stop-run ends exactly it
trap 'rm -f "$RUNPID"' EXIT INT TERM                          # the file must not outlive the run, even one ended by a signal
# No LOCAL_TIMEOUT means no clock: exec the sandbox (or opencode) directly. The same pid becomes the
# process-group leader, so stop-run can signal the group. A set LOCAL_TIMEOUT is a caller fence; the pid
# file then names `timeout`, which is that leader.
exec_lead(){
  exec python3 -c 'import os,sys
try:
    os.setpgid(0, 0)
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}
if [ "${SANDBOX:-1}" = 1 ]; then
  if [ -n "${LOCAL_TIMEOUT:-}" ]; then
    ( printf '%s\n' "$BASHPID" > "$RUNPID" && exec timeout "$LOCAL_TIMEOUT" bash "$(dirname "$0")/sandbox_run.sh" "$WT" "$CFG" -- \
        opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null 9>&- ) 2>&1 | tee "$LOG"
  else
    ( printf '%s\n' "$BASHPID" > "$RUNPID" && exec_lead bash "$(dirname "$0")/sandbox_run.sh" "$WT" "$CFG" -- \
        opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null 9>&- ) 2>&1 | tee "$LOG"
  fi
else
  echo "⚠ SANDBOX=0: running opencode UNCONFINED (testing only)" | tee -a "$LOG"
  if [ -n "${LOCAL_TIMEOUT:-}" ]; then
    ( cd "$WT" && printf '%s\n' "$BASHPID" > "$RUNPID" && exec timeout "$LOCAL_TIMEOUT" opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null ) 2>&1 | tee "$LOG"
  else
    ( cd "$WT" && printf '%s\n' "$BASHPID" > "$RUNPID" && exec_lead opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null ) 2>&1 | tee "$LOG"
  fi
fi
rc=${PIPESTATUS[0]}
rm -f "$RUNPID"
set -e
echo "▶ opencode exit=$rc (informational — judge by the deliverable)"
echo "▶ worktree changes before=$before after=$(safe_git "$WT" status --porcelain | wc -l):"; safe_git "$WT" status --porcelain | head -20
echo "▶ files touched during the run:"; find "$WT" -path "$WT/.git" -prune -o -type f -newer "$WT/Local_Documentation/briefs/brief-$stamp.md" -print | head -20
