#!/usr/bin/env bash
# local-build.sh — the A770 builder seat, two profiles. Installed as a skill in every agent's skill dir (copies).
#   run [<worktree>] <brief.md> [--serious] [--timeout S]     (no worktree = the default seat, A770B_SEAT)
#   serve fast|serious | status | stop
# The installed copy finds the project through A770B_PROJECT: the environment, then
# ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.env, then the default ~/local-ai/A770_Builder. Every other path
# and knob comes from the project's harness/env.sh (see config/builder.env.example).
set -uo pipefail
die(){ echo "⛔ $*" >&2; exit 2; }
_cfg="${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.env"
if [ -z "${A770B_PROJECT:-}" ] && [ -f "$_cfg" ]; then A770B_PROJECT=$(sed -nE 's/^[[:space:]]*A770B_PROJECT=([^#]*).*/\1/p' "$_cfg" | tail -1 | tr -d '"' | sed "s#^~#$HOME#"); fi
A770B_PROJECT="${A770B_PROJECT:-$HOME/local-ai/A770_Builder}"; export A770B_PROJECT
[ -r "$A770B_PROJECT/harness/env.sh" ] || die "project not found at $A770B_PROJECT (set A770B_PROJECT in $_cfg or the environment)"
. "$A770B_PROJECT/harness/env.sh"; . "$A770B_PROJECT/harness/guard.sh"
SERVE="$A770B_PROJECT/harness/serve_a770_llamacpp.sh"; BUILD="$A770B_PROJECT/harness/build_local.sh"; CAPTURE="$A770B_PROJECT/harness/capture_task.sh"
PIDF="$A770B_DATA/logs/llamacpp-a770.pid"; MARK="$A770B_DATA/logs/llamacpp-a770.model"
current(){ llama_pid_alive "$PIDF" >/dev/null && cat "$MARK" 2>/dev/null || echo ""; }
profile_vars(){ # sets gguf ctx kv reasoning extra t for a profile
  case "$1" in
    fast)    gguf=$(a770b_model_path "$A770B_FAST_MODEL");    ctx=$A770B_FAST_CTX;    kv=$A770B_FAST_KV;    reasoning=$A770B_FAST_REASONING;    extra=$A770B_FAST_EXTRA;    t=$A770B_FAST_TIMEOUT ;;
    serious) gguf=$(a770b_model_path "$A770B_SERIOUS_MODEL"); ctx=$A770B_SERIOUS_CTX; kv=$A770B_SERIOUS_KV; reasoning=$A770B_SERIOUS_REASONING; extra=$A770B_SERIOUS_EXTRA; t=$A770B_SERIOUS_TIMEOUT ;;
    *) die "profile must be fast|serious" ;;
  esac
}
serve(){ local p="$1" gguf ctx kv reasoning extra t; profile_vars "$p"
  [ -r "$gguf" ] || die "model not found: $gguf — put the GGUF in A770B_MODELS ($A770B_MODELS) or set A770B_${p^^}_MODEL"
  if [ "$(current)" = "$gguf" ] && curl -sf --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" >/dev/null; then echo "✓ $p already up ($(basename "$gguf"))"; return 0; fi
  bash "$SERVE" stop >/dev/null 2>&1
  # shellcheck disable=SC2086  (extra is a deliberate word list from the env)
  KV_K=$kv KV_V=$kv REASONING=$reasoning bash "$SERVE" start "$gguf" "$ctx" $extra || die "server did not start (budget gate or VRAM cap refused — see above)"
  for i in $(seq 1 90); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | grep -q '"ok"' && break; sleep 2; done
  curl -sf "http://$A770B_HOST:$A770B_PORT/health" >/dev/null || die "server not healthy after 180 s"
  echo "✓ $p serving $(basename "$gguf") · ctx $ctx · KV $kv · $A770B_HOST:$A770B_PORT"
}
status(){ local c; c=$(current)
  if [ -n "$c" ]; then echo "server: UP · $(basename "$c") · pid $(cat "$PIDF")"; else echo "server: down"; fi
  curl -s --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | head -c 80; echo
  echo "builder card: $A770B_DEVICE ($A770B_GPU_MATCH) · VRAM used $(gpu_used_gib) GiB · cap $A770B_VRAM_CAP_GIB"
  echo "profiles: fast = $A770B_FAST_MODEL ctx $A770B_FAST_CTX · serious = $A770B_SERIOUS_MODEL ctx $A770B_SERIOUS_CTX · models in $A770B_MODELS"
  echo "project $A770B_PROJECT · data $A770B_DATA · seat $A770B_SEAT"
}
case "${1:-}" in
  serve)  run_lock; serve "${2:-fast}" ;;
  status) status ;;
  stop)   bash "$SERVE" stop ;;
  run)
    shift; profile=fast; timeout=""
    # `run <brief.md>` uses the default seat; `run <worktree> <brief.md>` names one
    if [ -f "${1:-}" ] && [ ! -d "${1:-}" ]; then WT_RAW="$A770B_SEAT"; BRIEF="$1"; shift 1; else WT_RAW="${1:?worktree or brief}"; BRIEF="${2:?brief.md}"; shift 2; fi
    while [ $# -gt 0 ]; do case "$1" in --serious) profile=serious;; --fast) profile=fast;; --timeout) timeout="$2"; shift;; *) die "unknown arg $1";; esac; shift; done
    WT=$(guard_worktree "$WT_RAW") || exit 2                       # BEFORE anything is touched (fact:2087 #3/#4)
    [ -f "$BRIEF" ] || die "brief not found: $BRIEF"
    run_lock                                                       # one run at a time on this card (fact:2087 #8)
    profile_vars "$profile"; t=${timeout:-$t}
    serve "$profile" || exit $?
    label="$profile-$(date +%Y%m%d-%H%M%S)"
    echo "▶ profile=$profile timeout=${t}s worktree=$WT brief=$BRIEF label=$label"
    aside=0
    restore(){ if [ "$aside" = 1 ] && [ -f "$WT/AGENTS.md.local-off" ]; then mv -f "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"; aside=0; fi; }
    trap restore EXIT INT TERM                                    # fact:2087 #7
    if [ -f "$WT/AGENTS.md" ]; then mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"; aside=1; fi
    PROFILE="$profile" LOCAL_AGENT=local-builder LOCAL_TIMEOUT="$t" bash "$BUILD" "$WT" "$BRIEF" 2>&1 | grep -vE '^\s*$' | tail -15 | cut -c1-240
    brc=${PIPESTATUS[0]}
    restore
    case "$brc" in 2|3) echo "⛔ build refused (exit $brc) — no capture, no reset" >&2; exit "$brc";; esac
    BL=$(cat "$A770B_DATA/logs/last-build.log.path" 2>/dev/null || true)
    [ -f "$BL" ] || die "build log path missing — capture skipped"
    bash "$CAPTURE" "$label" "$WT" "$BL" | tail -2
    echo "▶ capture: $A770B_DATA/results/$label.task.md — review it before merging; the worktree has been reset to clean." ;;
  *) echo "usage: local-build.sh run [<worktree>] <brief.md> [--serious] [--timeout S] | serve fast|serious | status | stop" >&2; exit 2 ;;
esac
