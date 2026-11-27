#!/usr/bin/env bash
# Managed native Strata transport. Lifecycle mutations use the normal card lock.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/env.sh"; . "$here/guard.sh"
managed=(python3 "$here/strata_managed.py")
# `existing` is non-empty exactly when start could only reuse an instance, and a reused
# one belongs to whoever started it.
refuse_instance() {
  if [ -z "$existing" ]; then
    "${managed[@]}" stop "$id" || exit 3
  else
    echo "the reused Strata instance is left running" >&2
  fi
  exit 3
}
case "${1:-}" in
  status|id|pid|model|tokenizer|config-model|verify|observe|gate) exec "${managed[@]}" "$@" ;;
  stop-owned)
    card_lock
    "${managed[@]}" "$@"
    ;;
  stop|stop-id)
    card_lock
    if [ "$1" = stop-id ]; then "${managed[@]}" stop "${2:?instance id}"; else "${managed[@]}" stop; fi
    ;;
  start)
    config="${2:?prepared Strata config}"; ctx="${3:?context}"
    [ "$#" -eq 3 ] || { echo "Strata engine flags belong in the prepared config" >&2; exit 2; }
    card_lock
    case " $A770B_FRAMEWORK_PORTS " in *" $A770B_PORT "*) echo "Strata port is reserved" >&2; exit 2;; esac
    [ "$A770B_HOST" = 127.0.0.1 ] || {
      echo "managed Strata requires numeric loopback 127.0.0.1" >&2; exit 2;
    }
    python3 -c 'import math,sys; cap,total=map(float,sys.argv[1:]); sys.exit(not (math.isfinite(total) and 0 < cap <= total))' \
      "$A770B_VRAM_CAP_GIB" "$A770B_CARD_VRAM_TOTAL" || { echo "invalid Strata card cap" >&2; exit 2; }
    [ "$(gpu_match_count)" = 1 ] || { echo "managed Strata requires one uniquely measured card" >&2; exit 2; }
    existing=$("${managed[@]}" id) || {
      rc=$?
      [ "$rc" = 1 ] || exit "$rc"
      existing=""
    }
    export A770B_MIN_AVAIL_MB A770B_VRAM_CAP_GIB
    if [ -z "$existing" ]; then
      # No Compose project or server is stopped here. A failed lookup is an ambiguous state.
      containers=$("$A770B_DOCKER" ps -q \
        --filter "label=com.docker.compose.project=$A770B_COMPOSE_PROJECT" --filter 'label=com.docker.compose.service=llama') || exit 2
      [ -z "$containers" ] || { echo "stop the running builder container before starting Strata" >&2; exit 2; }
      if ! budget_gate >&2; then
        # $? is the negated if-test. PIPESTATUS keeps the gate's own status.
        rc=${PIPESTATUS[0]}
        "${managed[@]}" check-floor >/dev/null 2>&1 || true
        exit "$rc"
      fi
    fi
    "${managed[@]}" tokenizer "$config" >/dev/null || exit $?
    a770b_api_key >/dev/null || exit 2
    # A failed cap or admission must not leave the previous start's receipt behind.
    if [ -f "$A770B_DATA/logs/profiling.json" ] && [ ! -L "$A770B_DATA/logs/profiling.json" ]; then
      rm -f -- "$A770B_DATA/logs/profiling.json"
    fi
    evidence=$("${managed[@]}" start "$config" --ctx "$ctx" --timeout "${A770B_STRATA_READY_S:-300}") || exit $?
    id=$("${managed[@]}" id) || exit $?
    if ! used=$(gpu_used_gib) || ! python3 -c 'import sys; used,cap=map(float,sys.argv[1:]); sys.exit(not (0 <= used <= cap))' \
      "$used" "$A770B_VRAM_CAP_GIB"; then
      echo "owned Strata exceeded the configured VRAM cap or telemetry was unavailable (used: ${used:-unavailable} GiB)" >&2
      refuse_instance
    fi
    if ! "${managed[@]}" admit; then
      echo "managed Strata did not pass post-start admission; continuous host RAM enforcement was not recorded" >&2
      refuse_instance
    fi
    # The receipt is written only after the cap and admission have accepted this start.
    python3 - "$here" "$evidence" "$A770B_DATA" <<'PY' || true
import sys
sys.path.insert(0, sys.argv[1])
import os
import profiling_contract
evidence, data = sys.argv[2], sys.argv[3]
receipt_path = os.path.join(data, "logs", "profiling.json")
profiling_contract.write_receipt(
    receipt_path, profiling_contract.receipt("strata", {}),
)
profiling_contract.merge_receipt_file(evidence, receipt_path)
PY
    printf '%s\n' "$evidence"
    ;;
  *) echo "usage: serve_strata.sh start <config> <ctx> | stop | stop-id <id> | stop-owned <id> | status | id | pid | model | tokenizer <config> | config-model <config> | gate <config> | verify <model> --ctx <n>" >&2; exit 2 ;;
esac
