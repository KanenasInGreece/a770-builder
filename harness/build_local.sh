#!/usr/bin/env bash
# build_local.sh — dispatch a brief to the local builder through opencode, inside the sandbox.
#   build_local.sh <worktree> <brief-file-or-text>
# Env: PROFILE (fast|serious, default fast), LOCAL_AGENT, LOCAL_TIMEOUT (s), SANDBOX=0 to run unconfined (testing).
# The opencode config is RENDERED per run from config/opencode.profile.template.jsonc with the server URL, the
# profile's context window and the output limit from env.sh, so the model's client and the server can never disagree.
# Rules: the target is a self-contained clone that is not a protected checkout (guard.sh); opencode runs with
# --dir <worktree> inside bubblewrap (sandbox_run.sh); every opencode call takes `< /dev/null`; judged by the deliverable.
# Exit codes: 2 refused (worktree), 3 no model server; the opencode exit code is informational only.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
WT=$(guard_worktree "${1:?worktree path}"); BRIEF="${2:?brief file or task text}"
PROFILE="${PROFILE:-fast}"
case "$PROFILE" in fast) CTX=$A770B_FAST_CTX;; serious) CTX=$A770B_SERIOUS_CTX;; *) echo "⛔ PROFILE must be fast|serious" >&2; exit 2;; esac
ENDPOINT="http://$A770B_HOST:$A770B_PORT"
curl -sf --max-time 5 -H "Authorization: Bearer $(a770b_api_key)" "$ENDPOINT/v1/models" >/dev/null || { echo "⛔ no model server answering at $ENDPOINT — start it first (local-build.sh serve fast|serious)" >&2; exit 3; }
# render the profile config (server URL, key, window, output limit)
CFG="$A770B_DATA/logs/opencode.$PROFILE.jsonc"
a770b_render_profile "$PROFILE" "$CTX" "$CFG" || exit 2
export OPENCODE_CONFIG="$CFG"
MODEL="${LOCAL_MODEL:-local-a770/$A770B_ALIAS}"
mkdir -p "$WT/Local_Documentation/briefs"
stamp=$(date +%Y%m%d-%H%M%S)
if [ -f "$BRIEF" ]; then cp -- "$BRIEF" "$WT/Local_Documentation/briefs/brief-$stamp.md"; else printf '%s\n' "$BRIEF" > "$WT/Local_Documentation/briefs/brief-$stamp.md"; fi
LOG="$A770B_DATA/logs/build-$stamp.log"
printf '%s\n' "$LOG" > "$A770B_DATA/logs/last-build.log.path"
echo "▶ profile=$PROFILE ctx=$CTX model=$MODEL endpoint=$ENDPOINT worktree=$WT brief=Local_Documentation/briefs/brief-$stamp.md log=$LOG sandbox=${SANDBOX:-1}"
before=$(safe_git "$WT" status --porcelain | wc -l)
set +e
AGENT_ARGS=(); [ -n "${LOCAL_AGENT:-}" ] && AGENT_ARGS=(--agent "$LOCAL_AGENT")
PROMPT="Read Local_Documentation/briefs/brief-$stamp.md in this project and carry out the task it describes. Work only inside this project directory. Do not run git commit, push, merge or any docker/systemctl command."
if [ "${SANDBOX:-1}" = 1 ]; then
  ( timeout "${LOCAL_TIMEOUT:-1800}" bash "$(dirname "$0")/sandbox_run.sh" "$WT" "$CFG" -- \
      opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null 9>&- ) 2>&1 | tee "$LOG"
else
  echo "⚠ SANDBOX=0: running opencode UNCONFINED (testing only)" | tee -a "$LOG"
  ( cd "$WT" && timeout "${LOCAL_TIMEOUT:-1800}" opencode run --dir "$WT" -m "$MODEL" "${AGENT_ARGS[@]}" "$PROMPT" < /dev/null ) 2>&1 | tee "$LOG"
fi
rc=${PIPESTATUS[0]}
set -e
echo "▶ opencode exit=$rc (informational — judge by the deliverable)"
echo "▶ worktree changes before=$before after=$(safe_git "$WT" status --porcelain | wc -l):"; safe_git "$WT" status --porcelain | head -20
echo "▶ files touched during the run:"; find "$WT" -path "$WT/.git" -prune -o -type f -newer "$WT/Local_Documentation/briefs/brief-$stamp.md" -print | head -20
