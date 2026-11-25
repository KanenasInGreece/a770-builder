#!/usr/bin/env bash
# qualify_watch.sh <jobdir> <container-id-file> — stall and driver watch.
# A run is in flight only when guard.sh run_pid_alive says so. Speed never
# stops a run, and a stage wall only logs a warning. Test hooks are programs.
set -u
umask 077
job=${1:?job directory}
cidfile=${2:?container id file}
here=$(cd "$(dirname "$0")" && pwd)
# Variables below come from env.sh. The shell follows that source.
source "$here/env.sh" || exit 2
source "$here/guard.sh"
exec 7>&- 9>&- || true

poll=${WD_POLL:-60}
dk=${WD_DOCKER:-${A770B_DOCKER:-docker}}
logs=${WD_LOGDIR:-$A770B_DATA/logs}
py=(python3 "$here/qualify_lib.py")
scan=(python3 "$here/qualify_scan.py")

is_running() {
  if [ -n "${WD_RUNNING_BIN:-}" ]; then
    "$WD_RUNNING_BIN"
    return
  fi
  run_pid_alive "${WD_RUNPID:-$A770B_DATA/logs/run.pid}" >/dev/null
}

do_stop() {
  if [ -n "${WD_STOP_BIN:-}" ]; then
    "$WD_STOP_BIN"
    return
  fi
  bash "$A770B_PROJECT/skills/local-build/scripts/local-build.sh" stop-run
}

say() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$job/watchdog.log"
}

record_trip() {
  local rule=$1
  shift
  printf '%s\n' "$*" | "${py[@]}" status-merge --job "$job" --trip-rule "$rule" >/dev/null
}

trip_stage() {
  local rule=$1
  shift
  say "TRIP: $*"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$job/watchdog-trips.log"
  record_trip "$rule" "$*"
  do_stop >> "$job/watchdog.log" 2>&1 || true
  [ "${WD_NOFINALSLEEP:-0}" = 1 ] || sleep 90
}

trip_driver() {
  local line=$1
  printf '%s\n' "$line" > "$job/DRIVER_ERROR"
  trip_stage "driver" "fresh engine/driver error: $line"
  if [ -f "$job/suite.pid" ]; then
    local pid
    pid=$(cat "$job/suite.pid" 2>/dev/null || true)
    if [ -n "$pid" ]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  fi
  printf '1\n' > "$job/SUITE_SKIP"
}

now_s() {
  if [ -n "${WD_NOWFILE:-}" ]; then
    cat "$WD_NOWFILE"
  else
    date +%s
  fi
}

jget() {
  printf '%s' "$1" | python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print("" if v is None else v)' "$2"
}

log_epoch() {
  local base stamp d8 h6
  base=$(basename "$1")
  stamp=$(printf '%s\n' "$base" | sed -n 's/^build-\([0-9]\{8\}\)-\([0-9]\{6\}\).*/\1 \2/p')
  [ -n "$stamp" ] || return 1
  d8=${stamp%% *}
  h6=${stamp#* }
  date -d "${d8:0:4}-${d8:4:2}-${d8:6:2} ${h6:0:2}:${h6:2:2}:${h6:4:2}" +%s
}

last_ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
stall=0
inflight_l=0
engine=""
polls=0
lastedit_t=$(now_s)
progress_t=$lastedit_t
prev_newest=""
lastedits=-1
warned2=""
warned4=""
tripped_log=""
prev_cid=""

while [ -e "$job/WATCH" ]; do
  [ "${WD_NOSLEEP:-0}" = 1 ] || sleep "$poll"
  polls=$((polls + 1))
  [ -z "${WD_MAX_POLLS:-}" ] || [ "$polls" -le "$WD_MAX_POLLS" ] || break
  t=$(now_s)
  if is_running >/dev/null 2>&1; then
    newest=""
    if [ -f "$logs/last-build.log.path" ]; then
      newest=$(head -n 1 "$logs/last-build.log.path" 2>/dev/null || true)
      [ -f "$newest" ] || newest=""
    fi
    if [ -z "$newest" ]; then
      newest=$(ls -t "$logs"/build-*.log 2>/dev/null | head -n 1 || true)
    fi
    if [ -n "$newest" ] && [ -n "${QUALIFY_STAGE_START:-}" ]; then
      stamp=$(log_epoch "$newest" || true)
      if [ -n "$stamp" ] && [ "$stamp" -lt "$QUALIFY_STAGE_START" ]; then
        newest=""
      fi
    fi
    if [ -n "$newest" ]; then
      if [ "$newest" != "$prev_newest" ]; then
        prev_newest=$newest
        lastedits=-1
        lastedit_t=$t
      fi
      stamp=$(log_epoch "$newest" || true)
      if [ -n "$stamp" ]; then
        w=$((t - stamp))
        if [ "$w" -ge 7200 ] && [ "$warned2" != "$newest" ]; then
          warned2=$newest
          say "WARN: stage wall over 2 h (no stop): $newest"
        fi
        if [ "$w" -ge 14400 ] && [ "$warned4" != "$newest" ]; then
          warned4=$newest
          say "WARN: stage wall over 4 h (no stop): $newest"
        fi
      fi
      clean=$(sed 's/\x1b\[[0-9;]*m//g' "$newest")
      edits=$(printf '%s\n' "$clean" | grep -c -E '^← ' || true)
      if [ "$edits" != "$lastedits" ]; then
        lastedits=$edits
        lastedit_t=$t
      fi
      rep=$(printf '%s\n' "$clean" | awk '/^[A-Z][a-z]/ && NF>=6' | sort | uniq -c | sort -rn | head -n 1 | awk '{print $1+0}')
      if [ "${rep:-0}" -ge 8 ] && [ $((t - lastedit_t)) -ge 900 ] && [ "$newest" != "$tripped_log" ]; then
        tripped_log=$newest
        trip_stage "prose" "8+ identical prose lines (x$rep), no Write/Edit for 15 min: $newest"
        stall=0
        continue
      fi
      if [ $((t - lastedit_t)) -ge 10800 ] && [ $((t - progress_t)) -lt 1800 ] && [ "$newest" != "$tripped_log" ]; then
        tripped_log=$newest
        trip_stage "backstop" "no Write/Edit for 3 h while the engine was still progressing: $newest"
        stall=0
        continue
      fi
    fi
    cid=$(cat "$cidfile" 2>/dev/null || true)
    if [ -n "$cid" ]; then
      if [ "$cid" != "$prev_cid" ]; then
        prev_cid=$cid
        inflight_l=0
        stall=0
        last_ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
      fi
      res=$("$dk" logs --timestamps --since "$last_ts" "$cid" 2>&1 | "${scan[@]}" "$last_ts")
      last_ts=$(jget "$res" last_ts)
      err=$(jget "$res" error)
      if [ -n "$err" ]; then
        trip_driver "$err"
        stall=0
        inflight_l=0
        continue
      fi
      nl=$(jget "$res" new_lines)
      clk=$(jget "$res" clock)
      [ -z "$clk" ] || engine=$clk
      zi=$(jget "$res" zero_inflight)
      mi=$(jget "$res" moving_inflight)
      la=$(jget "$res" launches)
      re=$(jget "$res" releases)
      inflight_l=$((inflight_l + la - re))
      [ "$inflight_l" -ge 0 ] || inflight_l=0
      verdict=idle
      if [ "$engine" = vllm ]; then
        if [ "${mi:-0}" -gt 0 ]; then
          verdict=moving
        elif [ "${zi:-0}" -gt 0 ]; then
          verdict=stalled
        fi
      else
        if [ "$inflight_l" -gt 0 ]; then
          if [ "${nl:-0}" -gt 0 ]; then
            verdict=moving
          else
            verdict=stalled
          fi
        fi
      fi
      case "$verdict" in
        stalled) stall=$((stall + 1)) ;;
        *) stall=0 ;;
      esac
      if [ "$verdict" = moving ]; then
        progress_t=$t
      fi
      say "poll: new-lines=${nl:-0} engine=${engine:-?} inflight=$inflight_l verdict=$verdict stall=$stall"
      if [ "$stall" -ge 10 ]; then
        trip_stage "stall" "zero-progress stall: request in flight, no engine output for 10 consecutive polls"
        stall=0
        inflight_l=0
      fi
    fi
  else
    stall=0
    inflight_l=0
    last_ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  fi
done
say "watchdog exit"
