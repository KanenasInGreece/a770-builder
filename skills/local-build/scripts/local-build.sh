#!/usr/bin/env bash
# local-build.sh — the A770 builder seat, three profiles. Installed as a skill in every agent's skill dir (copies).
#   run [<worktree>] <brief.md> [--spec <spec.json>] [--profile <name>] [--timeout S]     (no worktree = the default seat, A770B_SEAT)
#   verify <label|patch> [<worktree>] [--test "<cmd>"] [--timeout S]   re-run a capture's tests inside a fresh sandbox
#   reset [<worktree>]                                          discard everything in the seat that is not committed (ignored files too)
#   serve <profile> | status | profiles [--name N] | stop | stop-run (end the run in progress by its own pid) | version (--version)
#   profiles [--name N]   the registry card (config/profiles.json) with the served values, for a caller choosing a profile
#   doctor            what this machine lacks to run the seat, one line per check; exit 1 when anything is missing
#   The mode: A770B_CARD_MODE=display (the default, the tested set under a 13.0 cap) or inference (a card that draws no desktop: its own registry under 15.3)
#   check-update      ask GitHub for the latest release and compare it with this copy (on demand only; nothing else ever calls out)
# The installed copy finds the project through A770B_PROJECT: the environment, then
# ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.env, then the default ~/local-ai/A770_Builder. Every other path
# and knob comes from the project's harness/env.sh (see config/builder.env.example).
set -uo pipefail
SKILL_VERSION=0.1.5            # the version of THIS installed copy; the project's VERSION file must match (see `version`)
die(){ echo "⛔ $*" >&2; exit 2; }
_cfg="${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.env"
if [ -z "${A770B_PROJECT:-}" ] && [ -f "$_cfg" ]; then A770B_PROJECT=$(sed -nE 's/^[[:space:]]*A770B_PROJECT=([^#]*).*/\1/p' "$_cfg" | tail -1 | tr -d '"' | sed "s#^~#$HOME#"); fi
A770B_PROJECT="${A770B_PROJECT:-$HOME/local-ai/A770_Builder}"; export A770B_PROJECT
version(){ local pv sha rel n
  echo "local-build skill release $SKILL_VERSION · installed at $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [ -r "$A770B_PROJECT/VERSION" ]; then pv=$(head -1 "$A770B_PROJECT/VERSION"); sha=$(git -c core.hooksPath=/dev/null -C "$A770B_PROJECT" rev-parse --short HEAD 2>/dev/null || true)
    # the release this checkout stands on: the nearest tag, and how many commits it has moved since
    rel=$(git -c core.hooksPath=/dev/null -C "$A770B_PROJECT" describe --tags --match 'v[0-9]*' --long 2>/dev/null || true)
    case "$rel" in "") rel="no release tag reachable";; *-0-g*) rel="release ${rel%%-*}";; *) n=${rel#*-}; n=${n%%-*}; rel="$n commit(s) after release ${rel%%-*}";; esac
    echo "a770-builder project $pv${sha:+ ($sha)} · $rel · $A770B_PROJECT"
    [ "$pv" = "$SKILL_VERSION" ] || echo "⚠ installed skill $SKILL_VERSION ≠ project $pv — reinstall the skill from the project (npx skills add … --copy, or copy skills/local-build by hand)" >&2
  else echo "a770-builder project: not found at $A770B_PROJECT (set A770B_PROJECT in $_cfg or the environment)" >&2; fi
}
# check_update — one conditional request, only when asked. Compares this copy's release number with the VERSION file on the
# repository's main branch, which release.sh moves only at a release, so it reads as the latest release. Works without the
# project checkout: an installed copy on a machine that has only the skill can still ask. Exit 0 up to date, 1 newer
# release available, 2 could not ask.
check_update(){ local url latest pv
  url="${A770B_UPDATE_URL:-https://raw.githubusercontent.com/KanenasInGreece/a770-builder/main/VERSION}"
  latest=$(curl -fsS --max-time 5 "$url" 2>/dev/null | head -1 | tr -d '[:space:]') || true
  case "$latest" in [0-9]*.[0-9]*.[0-9]*) ;; *) echo "⚠ could not read the latest release from $url (offline, or the URL moved)" >&2; return 2;; esac
  [ -r "$A770B_PROJECT/VERSION" ] && pv=$(head -1 "$A770B_PROJECT/VERSION") || pv=""
  if [ "$(printf '%s\n%s\n' "$latest" "$SKILL_VERSION" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" = "$SKILL_VERSION" ]; then
    echo "✓ this copy is release $SKILL_VERSION, the latest on GitHub${pv:+ · project checkout $pv}"; return 0
  fi
  echo "↑ release $latest is out; this copy is $SKILL_VERSION${pv:+ · project checkout $pv}"
  echo "  update: git -C ${A770B_PROJECT} pull  (the project), then  npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy  (this copy; or copy skills/local-build by hand)"
  return 1
}
case "${1:-}" in version|--version|-V) version; exit 0;; check-update) check_update; exit $?;; esac
[ -r "$A770B_PROJECT/harness/env.sh" ] || die "project not found at $A770B_PROJECT (set A770B_PROJECT in $_cfg or the environment)"
. "$A770B_PROJECT/harness/env.sh"; . "$A770B_PROJECT/harness/guard.sh"
SERVE="$A770B_PROJECT/harness/serve_a770_llamacpp.sh"; BUILD="$A770B_PROJECT/harness/build_local.sh"; CAPTURE="$A770B_PROJECT/harness/capture_task.sh"
PIDF="$A770B_DATA/logs/llamacpp-a770.pid"; MARK="$A770B_DATA/logs/llamacpp-a770.model"
current(){ llama_pid_alive "$PIDF" >/dev/null && cat "$MARK" 2>/dev/null || echo ""; }
profile_vars(){ # sets gguf ctx kv kv_v reasoning extra t for a profile named in A770B_PROFILES
  a770b_is_profile "$1" || die "profile must be one of: $A770B_PROFILES"
  local m; m=$(a770b_profile_var "$1" MODEL); [ -n "$m" ] || die "profile $1 has no MODEL set (A770B_$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')_MODEL is empty)"
  gguf=$(a770b_model_path "$m"); ctx=$(a770b_profile_var "$1" CTX); kv=$(a770b_profile_var "$1" KV)
  kv_v=$(a770b_profile_var "$1" KV_V)
  reasoning=$(a770b_profile_var "$1" REASONING); extra=$(a770b_profile_var "$1" EXTRA); t=$(a770b_profile_var "$1" TIMEOUT)
}
serve(){ local p="$1" gguf ctx kv kv_v reasoning extra t; profile_vars "$p"
  [ -r "$gguf" ] || die "model not found: $gguf — put the GGUF in A770B_MODELS ($A770B_MODELS) or set A770B_${p^^}_MODEL"
  if [ "$(current)" = "$gguf" ] && curl -sf --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" >/dev/null; then
    if curl -sf --max-time 3 -H "Authorization: Bearer $(a770b_api_key)" "http://$A770B_HOST:$A770B_PORT/v1/models" >/dev/null; then echo "✓ $p already up ($(basename "$gguf"))"; return 0; fi
    echo "↻ the running server does not accept the key in $A770B_API_KEY_FILE (rotated?) — restarting it"
  fi
  bash "$SERVE" stop >/dev/null 2>&1
  # extra is a deliberate word list from the env, expanded unquoted on purpose so each word is an argument
  # shellcheck disable=SC2086
  KV_K=$kv KV_V=${kv_v:-$kv} REASONING=$reasoning bash "$SERVE" start "$gguf" "$ctx" $extra || die "server did not start (budget gate or VRAM cap refused — see above)"
  for _ in $(seq 1 90); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | grep -q '"ok"' && break; sleep 2; done
  curl -sf "http://$A770B_HOST:$A770B_PORT/health" >/dev/null || die "server not healthy after 180 s"
  echo "✓ $p serving $(basename "$gguf") · ctx $ctx · KV $kv/${kv_v:-$kv} · $A770B_HOST:$A770B_PORT"
}
status(){ local c; c=$(current); version 2>&1
  if [ -n "$c" ]; then echo "server: UP · $(basename "$c") · pid $(cat "$PIDF")"; else echo "server: down"; fi
  curl -s --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | head -c 80; echo
  echo "builder card: $A770B_DEVICE ($A770B_GPU_MATCH) · mode $A770B_CARD_MODE · registry $A770B_PROFILES_FILE · VRAM used $(gpu_used_gib) GiB · cap $A770B_VRAM_CAP_GIB"
  printf 'profiles:'; for p in $A770B_PROFILES; do printf ' %s%s = %s ctx %s ·' "$p" "$([ "$p" = "$A770B_DEFAULT_PROFILE" ] && echo ' (default)')" "$(a770b_profile_var "$p" MODEL)" "$(a770b_profile_var "$p" CTX)"; done; echo " models in $A770B_MODELS"
  echo "project $A770B_PROJECT · data $A770B_DATA · seat $A770B_SEAT"
  if ! : 9>>"$A770B_DATA/logs/local-build.lock" 2>/dev/null; then echo "run lock: unknown (cannot open $A770B_DATA/logs/local-build.lock)"; elif ( flock -n 9 ) 9>>"$A770B_DATA/logs/local-build.lock"; then echo "run lock: free"; else echo "run lock: HELD — a run, verify, serve or reset is in progress; wait for it"; fi
  if pid=$(run_pid_alive "$A770B_DATA/logs/run.pid"); then echo "run: pid $pid in progress since $(date -r "$A770B_DATA/logs/run.pid" +%H:%M:%S 2>/dev/null || echo "a moment ago") — local-build.sh stop-run ends exactly it"; else echo "run: none"; fi
  local n; if [ -d "$A770B_SEAT/.git" ]; then n=$(seat_dirty "$A770B_SEAT" | wc -l); if [ "$n" = 0 ]; then echo "seat: $A770B_SEAT clean"; else echo "seat: $A770B_SEAT has $n uncommitted or ignored entries — local-build.sh reset before a run"; fi; else echo "seat: $A770B_SEAT is not a git clone"; fi
}
# doctor — what this machine lacks to run the seat, one line per check; never stops at the first MISSING. No run lock, no server.
doctor(){
  local missing=0 t p m mp n warn_list w u hint
  command -v python3 >/dev/null 2>&1 && echo "ok   python3: on PATH" || { echo "MISSING python3: install Python 3 (the registry, the renderer and the capture use it)"; missing=$((missing+1)); }
  for t in bwrap socat uv curl git flock timeout; do
    command -v "$t" >/dev/null 2>&1 && echo "ok   $t: on PATH" || { echo "MISSING $t: install $t"; missing=$((missing+1)); }
  done
  [ -x "$A770B_LLAMA_BIN" ] && echo "ok   llama-server at $A770B_LLAMA_BIN" || { echo "MISSING llama-server at $A770B_LLAMA_BIN: build llama.cpp with the Vulkan backend, or set A770B_LLAMA_BIN"; missing=$((missing+1)); }
  if command -v nvtop >/dev/null 2>&1 || [ "$A770B_ALLOW_NO_NVTOP" = 1 ]; then echo "ok   nvtop: on PATH or A770B_ALLOW_NO_NVTOP=1"; else echo "MISSING nvtop: install it for VRAM readings, or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop"; missing=$((missing+1)); fi
  n=$(gpu_match_count)
  case "$n" in
    1) echo "ok   card: exactly one nvtop device matches A770B_GPU_MATCH=$A770B_GPU_MATCH" ;;
    -1)
      if [ "$A770B_ALLOW_NO_NVTOP" = 1 ]; then
        echo "ok   card: not measured (no VRAM readings)"
      else
        echo "MISSING card: no VRAM readings (nvtop absent or its output unreadable); install nvtop, or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop"; missing=$((missing+1))
      fi ;;
    *) echo "MISSING card: $n nvtop devices match A770B_GPU_MATCH=$A770B_GPU_MATCH, not 1; set it to a substring naming the builder card alone in 'nvtop -s'"; missing=$((missing+1)) ;;
  esac
  if llama_pid_alive "$PIDF" >/dev/null; then
    echo "ok   mode: $A770B_CARD_MODE, not measured (the seat's server is up)"
  elif [ "$n" != 1 ]; then
    echo "ok   mode: $A770B_CARD_MODE, not measured (no VRAM readings or more than one matching card)"
  else
    u=$(python3 -c "print(f'{$(gpu_used_gib):.2f}')")
    case "$A770B_CARD_MODE" in
      inference)
        if python3 -c "import sys; sys.exit(0 if float('$u') > 0.5 else 1)"; then
          echo "MISSING mode: inference, but the builder card holds $u GiB with the seat's server down — something else draws it (a desktop?); set A770B_CARD_MODE=display, or free the card"; missing=$((missing+1))
        else
          echo "ok   mode: inference; the builder card holds $u GiB with the seat's server down"
        fi ;;
      *)
        hint=""
        python3 -c "import sys; sys.exit(0 if float('$u') <= 0.1 else 1)" && hint=" — no desktop measured on it; A770B_CARD_MODE=inference would serve the inference registry under the inference cap (15.3)"
        echo "ok   mode: display; the builder card holds $u GiB with the seat's server down$hint" ;;
    esac
  fi
  if python3 "$A770B_PROJECT/harness/profiles.py" check --file "$A770B_PROFILES_FILE" >/dev/null 2>&1; then echo "ok   registry: $(basename "$A770B_PROFILES_FILE") checks"; else echo "MISSING registry: $(basename "$A770B_PROFILES_FILE") does not check (python3 harness/profiles.py check says why)"; missing=$((missing+1)); fi
  for p in $A770B_PROFILES; do
    m=$(a770b_profile_var "$p" MODEL); mp=$(a770b_model_path "$m")
    if [ -r "$mp" ]; then echo "ok   model for $p: $mp"; else echo "MISSING model for $p: $mp not found; download it (docs/OPERATING.md) or set A770B_$(printf '%s' "$p" | tr 'a-z-' 'A-Z_')_MODEL"; missing=$((missing+1)); fi
  done
  warn_list=$(python3 "$A770B_PROJECT/harness/profiles.py" card --file "$A770B_PROFILES_FILE" --served 2>/dev/null | python3 -c 'import json,sys; [print(w) for w in json.load(sys.stdin).get("warnings", [])]' 2>/dev/null)
  if [ -n "$warn_list" ]; then
    while IFS= read -r w; do echo "MISSING profiles: $w"; missing=$((missing+1)); done <<<"$warn_list"
  else
    echo "ok   profiles: the environment does not invert the registry"
  fi
  [ -n "$A770B_REFUSE" ] && echo "ok   A770B_REFUSE: set" || { echo "MISSING A770B_REFUSE: list the live checkouts the seat must never touch, colon-separated, in builder.env"; missing=$((missing+1)); }
  if [ -d "$A770B_SEAT" ] && [ -d "$A770B_SEAT/.git" ] && [ ! -L "$A770B_SEAT/.git" ]; then echo "ok   seat at $A770B_SEAT"; else echo "MISSING seat at $A770B_SEAT: clone the target repository there (git clone <url> $A770B_SEAT)"; missing=$((missing+1)); fi
  if [ -x "$A770B_LLAMA_BIN" ]; then
    if [ -z "$A770B_VK_DEVICE_SELECT" ]; then
      echo "ok   device: selector empty, not pinned"
    else
      n=$(MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" "$A770B_LLAMA_BIN" --list-devices 2>/dev/null | grep -c '^ *Vulkan[0-9]*:')
      if [ "$n" = 1 ]; then echo "ok   device: selector $A770B_VK_DEVICE_SELECT pins exactly one Vulkan device"; else echo "MISSING device: the selector $A770B_VK_DEVICE_SELECT lists $n Vulkan devices, not 1; set A770B_VK_DEVICE_SELECT to the builder card's vendor:device! from lspci -nn"; missing=$((missing+1)); fi
    fi
  fi
  [ -d "$A770B_UV_CACHE" ] && echo "ok   uv cache at $A770B_UV_CACHE" || { echo "MISSING uv cache at $A770B_UV_CACHE: run bash harness/warm_cache.sh once (the sandbox has no network)"; missing=$((missing+1)); }
  if [ "$missing" = 0 ]; then echo "ok   doctor: all checks passed"; return 0; else echo "MISSING doctor: $missing missing"; return 1; fi
}
case "${1:-}" in
  serve)  run_lock; serve "${2:-$A770B_DEFAULT_PROFILE}" ;;
  reset)  WT=$(guard_worktree "${2:-$A770B_SEAT}") || exit 2; run_lock; reset_worktree "$WT" ;;
  status) status ;;
  profiles) shift; python3 "$A770B_PROJECT/harness/profiles.py" card --file "$A770B_PROFILES_FILE" --served "$@" ;;
  doctor) doctor ;;
  stop)   bash "$SERVE" stop ;;
  stop-run)
    # end exactly the run in progress, through its own pid: one TERM to the timeout process ends its whole process
    # group (measured on this host), and the run then unwinds as a timeout does — the capture is written, the seat reset.
    # Never by process name: bwrap is also every Flatpak app on the desktop.
    RUNPID="$A770B_DATA/logs/run.pid"
    pid=$(run_pid_alive "$RUNPID") || die "no run to stop: $RUNPID names no live timeout process of this harness (a finished run removes the file; a stale or foreign pid is refused, never signalled)"
    echo "▶ ending run pid $pid (timeout → sandbox → opencode): one TERM, then up to 30 s for the capture and the reset to follow"
    kill -TERM "$pid" 2>/dev/null || die "could not signal pid $pid"
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && die "run pid $pid is still alive after 30 s — nothing escalated; read the build log before ending anything by hand, and never by process name"
    echo "✓ run pid $pid ended; the local-build.sh run that owns it is now capturing what the seat did and resetting the seat" ;;
  run)
    shift; profile=$A770B_DEFAULT_PROFILE; timeout=""; spec=""; profile_set=0
    # `run <brief.md>` uses the default seat; `run <worktree> <brief.md>` names one
    if [ -f "${1:-}" ] && [ ! -d "${1:-}" ]; then WT_RAW="$A770B_SEAT"; BRIEF="$1"; shift 1; else WT_RAW="${1:?worktree or brief}"; BRIEF="${2:?brief.md}"; shift 2; fi
    while [ $# -gt 0 ]; do case "$1" in --profile) profile="${2:?profile name}"; a770b_is_profile "$profile" || die "profile must be one of: $A770B_PROFILES (got '$profile')"; profile_set=1; shift;; --timeout) timeout="${2:?seconds}"; shift;; --spec) spec="${2:?spec.json}"; shift;; *) die "unknown arg $1";; esac; shift; done
    WT=$(guard_worktree "$WT_RAW") || exit 2                       # BEFORE anything is touched
    [ -f "$BRIEF" ] || die "brief not found: $BRIEF"
    # the run specification: checked against the seat BEFORE the run lock and before any server starts, then snapshotted
    # so nothing re-reads the caller's file later; a flag on the command line wins over a key in the specification
    SPEC_SNAP=""
    if [ -n "$spec" ]; then
      [ -f "$spec" ] && [ ! -L "$spec" ] || die "specification not found, or a symlink: $spec"
      mkdir -p "$A770B_DATA/logs"; SPEC_SNAP="$A770B_DATA/logs/spec-$(date +%Y%m%d-%H%M%S)-$$.json"
      ( umask 077; cp -- "$spec" "$SPEC_SNAP" )                     # the snapshot is what is checked and what is used: no file changes between the two
      python3 "$A770B_PROJECT/harness/render_profile.py" check --spec "$SPEC_SNAP" --seat "$WT" || { rm -f -- "$SPEC_SNAP"; die "specification refused: $spec"; }
      read -r spec_profile spec_timeout <<<"$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("profile") or "-", d.get("timeout") or "-")' "$SPEC_SNAP")"
      [ "$profile_set" = 1 ] || [ "$spec_profile" = "-" ] || profile="$spec_profile"
      [ -n "$timeout" ] || [ "$spec_timeout" = "-" ] || timeout="$spec_timeout"
    fi
    run_lock                                                       # one run at a time on this card
    dirty=$(seat_dirty "$WT"); [ -z "$dirty" ] || { printf '%s\n' "$dirty" | head -5 >&2; die "seat $WT is not clean (ignored files count) — nothing from a previous run may pass as this one's; run: local-build.sh reset $WT"; }
    profile_vars "$profile"; t=${timeout:-$t}
    serve "$profile" || exit $?
    label="$profile-$(date +%Y%m%d-%H%M%S)"
    echo "▶ profile=$profile timeout=${t}s worktree=$WT brief=$BRIEF label=$label"
    aside=0
    restore(){ if [ "$aside" = 1 ] && [ -f "$WT/AGENTS.md.local-off" ]; then mv -f "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"; aside=0; fi; }
    trap restore EXIT INT TERM                                    # restored even on crash or timeout
    if [ -f "$WT/AGENTS.md" ]; then mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"; aside=1; fi
    A770B_SPEC="$SPEC_SNAP" PROFILE="$profile" LOCAL_AGENT=local-builder LOCAL_TIMEOUT="$t" bash "$BUILD" "$WT" "$BRIEF" 2>&1 | grep -vE '^\s*$' | tail -15 | cut -c1-240
    brc=${PIPESTATUS[0]}
    restore
    case "$brc" in 2|3) echo "⛔ build refused (exit $brc) — no capture, no reset" >&2; exit "$brc";; esac
    BL=$(cat "$A770B_DATA/logs/last-build.log.path" 2>/dev/null || true)
    [ -f "$BL" ] || die "build log path missing — capture skipped"
    A770B_SPEC="$SPEC_SNAP" bash "$CAPTURE" "$label" "$WT" "$BL" | tail -2; crc=${PIPESTATUS[0]}
    [ "$crc" = 0 ] || { echo "⛔ the capture failed (exit $crc) and the seat was NOT reset: read $A770B_DATA/results/$label.task.md, then run: local-build.sh reset $WT" >&2; exit "$crc"; }
    echo "▶ capture: $A770B_DATA/results/$label.task.md — review it before merging; the worktree has been reset to clean." ;;
  verify)
    # Re-apply what a run produced (<label>.patch beside the capture) to a CLEAN seat and run the tests inside the same
    # boundary the model had — no server, no bridge, no key. The verdict is the EXIT CODE of the test command; the pytest
    # line is quoted for the reader but decides nothing (the patch controls what the tests print). If the run had a
    # specification, <label>.spec.json beside the patch supplies the test command when --test is absent, and its hidden
    # acceptance tests — files the model never saw, kept under A770B_HIDDEN_ROOT — are copied into the seat AFTER the
    # patch applies and run with the rest; the reset removes them.
    shift; SRC="${1:?capture label or .patch path}"; shift; WT_RAW="$A770B_SEAT"; TEST=""; timeout=""
    while [ $# -gt 0 ]; do case "$1" in --test) TEST="${2:?command}"; shift;; --timeout) timeout="${2:?seconds}"; shift;; *) if [ -d "$1" ]; then WT_RAW="$1"; else die "unknown arg $1"; fi;; esac; shift; done
    if [ -f "$SRC" ]; then PATCH=$(realpath -e -- "$SRC"); else SRC=$(basename "$SRC"); PATCH="$A770B_DATA/results/${SRC%.task.md}"; PATCH="${PATCH%.patch}.patch"; fi
    [ -f "$PATCH" ] || die "patch not found: $PATCH (every capture writes <label>.patch beside <label>.task.md)"
    [ -s "$PATCH" ] || die "patch is empty — that run changed nothing, there is nothing to verify"
    SPECF="${PATCH%.patch}.spec.json"; HIDDEN=(); HROOT_RAW="${A770B_HIDDEN_ROOT:-$A770B_DATA/hidden}"
    if [ -f "$SPECF" ]; then
      # the hidden root is a real directory, never a link, and every file in it is checked at its resolved path
      [ -d "$HROOT_RAW" ] && [ ! -L "$HROOT_RAW" ] || die "A770B_HIDDEN_ROOT is not a real directory (a symlink is refused): $HROOT_RAW"
      HROOT=$(realpath -e -- "$HROOT_RAW")
      [ -n "$TEST" ] || TEST=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print((d.get("verify") or {}).get("test") or "")' "$SPECF")
      while IFS= read -r h; do
        [ -n "$h" ] || continue
        case "$h" in *[!A-Za-z0-9._-]*|.*|-*) die "hidden test name is not a plain basename: '$h'";; esac
        [ -f "$HROOT/$h" ] && [ ! -L "$HROOT/$h" ] || die "hidden test not found under $HROOT, or a symlink: $h"
        [ "$(stat -c %s -- "$HROOT/$h")" -le 65536 ] || die "hidden test over 64 KiB: $h"
        HIDDEN+=("$h")
      done < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(x) for x in ((d.get("verify") or {}).get("hidden") or [])]' "$SPECF")
    fi
    WT=$(guard_worktree "$WT_RAW") || exit 2
    run_lock                                                       # the seat is exclusive, like the card
    dirty=$(seat_dirty "$WT"); [ -z "$dirty" ] || { printf '%s\n' "$dirty" | head -5 >&2; die "seat $WT is not clean (ignored files count) — it is reset after every run; run: local-build.sh reset $WT"; }
    files=()
    if [ -z "$TEST" ]; then
      # the default: pytest on every tests/*.py file the patch touches — file names pass as SEPARATE argv words and must be
      # plain (a model-chosen name is untrusted input; a shell metacharacter in it is refused, never interpreted)
      while IFS= read -r f; do
        case "$f" in *[!A-Za-z0-9._/-]*|*..*|/*) die "unsafe test path in the patch: '$f' — pass --test '<command>' if you still want to run it";; esac
        files+=("$f")
      done < <(grep -oE '^\+\+\+ b/tests?/[^[:space:]]+\.py$' "$PATCH" | sed 's#^+++ b/##' | sort -u)
      [ ${#files[@]} -gt 0 ] || [ ${#HIDDEN[@]} -gt 0 ] || die "the patch adds or changes no tests/*.py file — pass --test '<command to run inside the sandbox>'"
    fi
    safe_git "$WT" apply --check "$PATCH" || die "patch does not apply cleanly to $WT"
    trap 'reset_worktree "$WT" >/dev/null' EXIT; trap 'reset_worktree "$WT" >/dev/null; exit 130' INT TERM   # clean again whatever happens
    safe_git "$WT" apply "$PATCH" || die "patch failed to apply"
    if [ ${#HIDDEN[@]} -gt 0 ]; then
      # the destination directory is the seat's own tests/, never a link the patch or the model planted
      mkdir -p "$WT/tests"; [ -d "$WT/tests" ] && [ ! -L "$WT/tests" ] && [ "$(realpath -e -- "$WT/tests")" = "$WT/tests" ] || die "the patched seat's tests/ is a symlink or missing — hidden tests refused"
    fi
    for h in "${HIDDEN[@]}"; do
      [ ! -e "$WT/tests/_hidden_$h" ] && [ ! -L "$WT/tests/_hidden_$h" ] || die "the patched seat already has tests/_hidden_$h — refused, a hidden test must not be overwritten"
      cp -P -- "$HROOT/$h" "$WT/tests/_hidden_$h"; chmod 644 "$WT/tests/_hidden_$h"; files+=("tests/_hidden_$h")
    done
    if [ -n "$TEST" ] && [ ${#HIDDEN[@]} -gt 0 ]; then
      # the caller's command first, then the hidden tests by pytest; the names reach pytest as argv words, never through the shell string
      CMD=(bash -c "$TEST"' && exec uv run --with pytest --with pytest-asyncio python -m pytest -q "$@"' _ "${files[@]}")
    elif [ -n "$TEST" ]; then CMD=(bash -c "$TEST"); else CMD=(uv run --with pytest --with pytest-asyncio python -m pytest -q "${files[@]}"); fi
    label=$(basename "${PATCH%.patch}"); OUT="$A770B_DATA/results/$label.verify.md"
    CFG="$A770B_DATA/logs/opencode.verify.jsonc"; a770b_render_profile verify "$(a770b_profile_var "$A770B_DEFAULT_PROFILE" CTX)" "$CFG" nokey || exit 2
    t=${timeout:-$(a770b_profile_var "$A770B_DEFAULT_PROFILE" TIMEOUT)}
    echo "▶ verify $label · seat $WT · inside the sandbox: ${CMD[*]}"
    { echo "# Verify — $label — $(date -Is)"; echo; echo "patch: $PATCH"; echo "applied to: $WT ($(seat_dirty "$WT" | wc -l) entries)"; echo "hidden tests: ${HIDDEN[*]:-none}"; echo "command, inside the sandbox (no model, no bridge, no key): ${CMD[*]}"; echo; echo '```'; } > "$OUT"
    ( A770B_NO_BRIDGE=1 timeout "$t" bash "$A770B_PROJECT/harness/sandbox_run.sh" "$WT" "$CFG" -- "${CMD[@]}" < /dev/null 9>&- ) 2>&1 | tail -80 | cut -c1-300 | tee -a "$OUT"
    vrc=${PIPESTATUS[0]}
    summary=$(grep -E '[0-9]+ (passed|failed|error)' "$OUT" | tail -1)
    case "$vrc" in 0) verdict="PASS (exit 0)";; 124) verdict="TIMEOUT after ${t}s";; *) verdict="FAIL (exit $vrc)";; esac
    { echo '```'; echo; echo "verdict: $verdict — the exit code of the command is the verdict"; echo "reported: ${summary:-no pytest summary line} (quoted from output the tests control; informational)"; } >> "$OUT"
    reset_worktree "$WT"; trap - EXIT INT TERM
    echo "▶ verify: $verdict · reported: ${summary:-no pytest summary line} · $OUT"
    exit "$vrc" ;;
  *) echo "usage: local-build.sh run [<worktree>] <brief.md> [--spec <spec.json>] [--profile <name>] [--timeout S] | verify <label|patch> [<worktree>] [--test \"<cmd>\"] | reset [<worktree>] | serve <profile> | status | profiles [--name N] | doctor | stop | stop-run | version | check-update" >&2; exit 2 ;;
esac
