#!/usr/bin/env bash
# capture_task.sh — after a build run, capture what the model did for review, then reset the worktree.
#   capture_task.sh <label> <worktree> <build-log>
# Writes $A770B_DATA/results/<label>.task.md: git status, the tracked diff, every new file (the harness's own brief copy
# under Local_Documentation/briefs/ excepted; a symlink is named, never read), the pytest summary the
# model reported INSIDE the run (never re-executed here), the opencode transcript tail, and the server-side timing
# distribution; and <label>.patch, the complete change (tracked diff + every new file) that `local-build.sh verify`
# re-applies to a clean seat to run the tests inside the sandbox. Then resets the worktree to clean. All git here is safe_git: config-driven code paths neutralised,
# because the model owns the tree.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
LABEL="${1:?label}"; WT=$(guard_worktree "${2:?worktree}"); BLOG="${3:?build log}"
[ -f "$BLOG" ] || { echo "⛔ build log not found: $BLOG" >&2; exit 2; }
OUT="$A770B_DATA/results/$LABEL.task.md"; PATCH="$A770B_DATA/results/$LABEL.patch"; TMPLOG=$(mktemp)
trap 'rm -f -- "$TMPLOG" "$TMPLOG.none"' EXIT
# The engine is the one the host recorded. sglang and vllm read the usage file.
# llama.cpp reads the container log. No recorded engine is unmeasured.
# True when this project's container is running, publishes this run's port,
# and that port is on this host: loopback, or the published address itself.
container_is_attached_server(){
  local container="$1" line host
  host="${A770B_HOST:-}"
  line=$("$A770B_DOCKER" inspect --format '{{.State.Running}} {{json .NetworkSettings.Ports}}' "$container" 2>/dev/null) || return 1
  case "$line" in
    true\ *) ;;
    *) return 1 ;;
  esac
  printf '%s\n' "$line" | grep -q "\"HostPort\":\"${A770B_PORT}\"" || return 1
  case "${host,,}" in
    127.0.0.1|localhost|::1) return 0 ;;
  esac
  # One binding. A port on this machine and an address on another binding is not this server.
  # Docker prints HostIp before HostPort in each binding.
  printf '%s\n' "$line" | grep -F -q "{\"HostIp\":\"${host}\",\"HostPort\":\"${A770B_PORT}\"}"
}
capture_timings(){
  local usage="$A770B_DATA/logs/usage.json"
  local engine="${A770B_SERVED_ENGINE:-}"
  local container="${A770B_COMPOSE_PROJECT}-llama-1"
  local since_file="$A770B_DATA/logs/run-since"
  local since=""
  case "$engine" in
    sglang|vllm)
      python3 "$A770B_PROJECT/harness/engine_record.py" capture --usage "$usage"
      ;;
    llama.cpp)
      # An attached server is this container only when the container is running,
      # publishes this run's port, and that port is on this host. The stamp is
      # written when the build starts. Without it the log is an older run.
      if [ -f "$since_file" ]; then
        since=$(tr -d '[:space:]' < "$since_file" || true)
      fi
      if { [ "${A770B_ATTACHED:-}" = 1 ] && ! container_is_attached_server "$container"; } || [ -z "$since" ]; then
        python3 "$A770B_PROJECT/harness/engine_record.py" capture --unmeasured
      else
        # llama.cpp writes on the container's stderr; docker logs sends that to this process's stderr.
        # Do not pass a colour-stripping flag: this docker does not accept one, and discarding
        # stderr made a refused flag look like an empty log (C9 s1-frontend, 2026-09-19).
        # The build log is not a count. An empty second file keeps the transcript unread.
        "$A770B_DOCKER" logs --since "$since" "$container" > "$TMPLOG" 2>&1 || true
        : > "$TMPLOG.none"
        python3 "$A770B_PROJECT/harness/engine_record.py" capture "$TMPLOG" "$TMPLOG.none"
      fi
      ;;
    *)
      python3 "$A770B_PROJECT/harness/engine_record.py" capture --unmeasured
      ;;
  esac
}
# the complete change, untruncated, for `verify`: tracked diff, then each new file as a creation diff
{ safe_git_diff "$WT"
  safe_git "$WT" ls-files --others --exclude-standard -z | { grep -zvE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || true; } | while IFS= read -r -d '' f; do
    [ -L "$WT/$f" ] && continue                                   # a symlink is named in the report, never followed or applied
    safe_git_diff "$WT" --no-index -- /dev/null "$f" || true; done
} > "$PATCH"
# the diff is taken into a file first: piping it into head made git die of SIGPIPE on a diff over 400 lines, and under
# pipefail that aborted the capture before the reset, leaving the seat dirty while the skill reported it clean
DIFF="$A770B_DATA/logs/capture-$LABEL.diff"; safe_git_diff "$WT" > "$DIFF"
{
echo "# Task capture — $LABEL — $(date -Is)"
echo; echo "## git status (worktree)"; safe_git "$WT" status --porcelain
echo; echo "## diff (tracked, first 400 lines; the complete change is the patch)"; echo '```diff'; head -400 -- "$DIFF"; echo '```'
echo; echo "## new files"
safe_git "$WT" ls-files --others --exclude-standard -z | { grep -zvE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || true; } | while IFS= read -r -d '' f; do
  echo; if [ -L "$WT/$f" ]; then echo "### $f — SYMLINK to $(readlink -- "$WT/$f"), not read, not in the patch (the host never follows a link the model made)"; continue; fi
  echo "### $f"; echo '```'; head -200 -- "$WT/$f"; echo '```'; done
echo; echo "## ignored files the run left behind (names only; removed by the reset, never applied by verify)"
safe_git "$WT" ls-files --others --ignored --exclude-standard | grep -vE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || echo "none"
echo; echo "## pytest summary as reported by the model inside the run (UNVERIFIED, proven only by verify, NOT re-executed here)"
grep -E '[0-9]+ (passed|failed|error)' "$BLOG" | tail -3 || echo "no pytest summary line in the transcript"
echo; echo "## run specification"
if [ -n "${A770B_SPEC:-}" ] && [ -f "$A770B_SPEC" ]; then
  ( umask 077; cp -- "$A770B_SPEC" "$A770B_DATA/results/$LABEL.spec.json" )
  ECHO=$(cat "$A770B_DATA/logs/last-build.echo.path" 2>/dev/null || true)
  if [ -n "$ECHO" ] && [ -f "$ECHO" ]; then ( umask 077; cp -- "$ECHO" "$A770B_DATA/results/$LABEL.echo.json" ); echo '```json'; cat -- "$ECHO"; echo '```'; else echo "no echo file: the renderer wrote none"; fi
  echo "specification kept as $A770B_DATA/results/$LABEL.spec.json; the echo, what was actually rendered, as $A770B_DATA/results/$LABEL.echo.json"
else
  echo "none: the run had no specification, so the profile is the template's defaults"
fi
echo; echo "## opencode transcript tail"; echo '```'; grep -vE '^\s*$' "$BLOG" | tail -30 | cut -c1-300; echo '```'
echo; echo "## server-side timings during the run"
capture_timings
} > "$OUT" 2>&1
rm -f -- "$TMPLOG"
rm -f -- "$DIFF"
echo "captured → $OUT ($(wc -l < "$OUT") lines) · patch $PATCH ($(wc -l < "$PATCH") lines)"
reset_worktree "$WT"
