#!/usr/bin/env bash
# local-build.sh — the A770 builder seat, serving the profiles of the card mode's own registry. Installed as a skill in every agent's skill dir (copies).
#   run [<worktree>] <brief.md> [--spec <spec.json>] [--profile <name>] [--timeout S] [--base-url http://host:port[/v1] --served-model-name NAME [--engine strata]]     (no worktree = the default seat, A770B_SEAT)
#   verify <label|patch> [<worktree>] [--test "<cmd>"] [--timeout S]   re-run a capture's tests inside a fresh sandbox
#   reset [<worktree>]                                          discard everything in the seat that is not committed (ignored files too)
#   serve <profile> | bench <profile> [--pp <sizes>] [--dry-run] | status | profiles [--name N] | menu | stop | stop-run (end the run in progress by its own pid) | version (--version)
#   qualify start|status|wait|result|export|stop|list   a qualification that continues after its caller exits
#   profiles [--name N]   the registry card (config/registry/<card>.<mode>.json) with the served values, for a caller choosing a profile
#   menu              ready / also / the cold full slice as JSON, for a caller picking a row
#   doctor            what this machine lacks to run the seat, one line per check; exit 1 when anything is missing
#   The mode: A770B_CARD_MODE=display (the default, the tested set under a 13.0 cap) or inference (a card that draws no desktop: its own registry under 15.3)
#   check-update      ask GitHub for the latest release and compare it with this copy (on demand only; nothing else ever calls out)
# The installed copy finds the project through A770B_PROJECT: the environment, then
# ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.env, then the default ~/local-ai/A770_Builder. Every other path
# and knob comes from the project's harness/env.sh (see config/builder.env.example).
set -uo pipefail
SKILL_VERSION=0.6.8            # the version of THIS installed copy; the project's VERSION file must match (see `version`)
die(){ echo "⛔ $*" >&2; exit 2; }
_cfg="${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.env"
# The project this copy belongs to, in order: the environment; THIS script's own checkout when it sits inside one
# (so running a branch's copy tests that branch — an installed skill copy is not inside a checkout and skips this);
# builder.env; the default. An explicit A770B_PROJECT always wins.
if [ -z "${A770B_PROJECT:-}" ]; then
  _self="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd || true)"
  if [ -n "$_self" ] && [ -r "$_self/harness/env.sh" ]; then A770B_PROJECT="$_self"
  elif [ -f "$_cfg" ]; then A770B_PROJECT=$(sed -nE 's/^[[:space:]]*A770B_PROJECT=([^#]*).*/\1/p' "$_cfg" | tail -1 | tr -d '"' | sed "s#^~#$HOME#")
  fi
fi
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
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in version|--version|-V) version; exit 0;; check-update) check_update; exit $?;; esac
fi
[ -r "$A770B_PROJECT/harness/env.sh" ] || die "project not found at $A770B_PROJECT (set A770B_PROJECT in $_cfg or the environment)"
. "$A770B_PROJECT/harness/env.sh" || { [ "${1:-}" = "doctor" ] || exit 2; }; . "$A770B_PROJECT/harness/guard.sh"
# shellcheck disable=SC1091
. "$A770B_PROJECT/harness/serve_envelope.sh"
# shellcheck source=profile_engine.sh
. "$(dirname "${BASH_SOURCE[0]}")/profile_engine.sh"
if [ "${1:-}" = "doctor" ]; then
  SERVE="${SERVE:-${A770B_SERVE_SCRIPT:-$A770B_PROJECT/harness/serve_compose.sh}}"
else
  SERVE="${SERVE:-${A770B_SERVE_SCRIPT:?the project at $A770B_PROJECT has no A770B_SERVE_SCRIPT in its env.sh — that checkout predates A770B_SERVE; run this copy from its own checkout or set A770B_PROJECT to it}}"
fi
BUILD="$A770B_PROJECT/harness/build_local.sh"; CAPTURE="$A770B_PROJECT/harness/capture_task.sh"
PIDF="$A770B_DATA/logs/llamacpp-a770.pid"; MARK="$A770B_DATA/logs/llamacpp-a770.model"
# _recorded_serve_pid <pidfile> — print the pid when it is alive and it is llama-server
# or this project's container host pid. A foreign process does not count. Pid 0 is not
# a process: kill -0 0 signals the caller. Do not search for a language runtime.
_recorded_serve_pid(){
  local pf="$1" pid comm cid host
  [ -f "$pf" ] || return 1
  pid=$(cat "$pf" 2>/dev/null || true)
  case "$pid" in
    ''|*[!0-9]*|0) return 1 ;;
  esac
  kill -0 "$pid" 2>/dev/null || return 1
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  if [ "$comm" = "llama-server" ]; then
    printf '%s\n' "$pid"
    return 0
  fi
  cid=$(_compose_container_id)
  [ -n "$cid" ] || return 1
  host=$("$A770B_DOCKER" inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || true)
  [ -n "$host" ] && [ "$host" != 0 ] && [ "$pid" = "$host" ] || return 1
  printf '%s\n' "$pid"
}
current(){ _recorded_serve_pid "$PIDF" >/dev/null && head -n 1 "$MARK" 2>/dev/null || echo ""; }
# The pidfile is a live llama-server. A vLLM container's host pid is not that name, and a failed start
# removes the pidfile while the container can still be running. The project label is this seat's container.
_compose_container_id(){
  local out
  [ "${A770B_SERVE:-}" = compose ] || return 0
  local lines
  out=$("$A770B_DOCKER" ps -q \
    --filter "label=com.docker.compose.project=${A770B_COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=llama" 2>/dev/null) || return 2
  lines=$(printf '%s\n' "$out" | grep -c '[^[:space:]]' || true)
  [ "$lines" -le 1 ] || return 2
  printf '%s\n' "$out" | head -n 1
}
# the one line for a card with no measured rows, produced in ONE place (profiles.py empty-line) and quoted verbatim
empty_registry_line(){ python3 "$A770B_PROJECT/harness/profiles.py" empty-line --card "${A770B_CARD:-}" --mode "$A770B_CARD_MODE"; }
# refuse a profile name that is not in this card's registry with env.sh's profile_refusal: it names this card and its
# own profiles, or quotes the one line when this card has none — never another card's rows.
require_profile(){ a770b_is_profile "$1" && return 0; die "$(profile_refusal "$1")"; }
profile_vars(){ # sets gguf ctx kv kv_v reasoning extra t and the thinking_* fields for a profile named in A770B_PROFILES
  require_profile "$1"
  local m; m=$(a770b_profile_var "$1" MODEL); [ -n "$m" ] || die "profile $1 has no MODEL set (A770B_$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')_MODEL is empty)"
  gguf=$(a770b_model_path "$m"); ctx=$(a770b_profile_var "$1" CTX); kv=$(a770b_profile_var "$1" KV)
  kv_v=$(a770b_profile_var "$1" KV_V)
  reasoning=$(a770b_profile_var "$1" REASONING); extra=$(a770b_profile_var "$1" EXTRA); t=$(a770b_profile_var "$1" TIMEOUT)
  # the row's optional `thinking` object (harness/profiles.py): empty when the row sets none, like every field above
  thinking_mode=$(a770b_profile_var "$1" THINKING_MODE); thinking_effort=$(a770b_profile_var "$1" THINKING_EFFORT)
  thinking_budget=$(a770b_profile_var "$1" THINKING_BUDGET); thinking_budget_message=$(a770b_profile_var "$1" THINKING_BUDGET_MESSAGE)
  thinking_preserve=$(a770b_profile_var "$1" THINKING_PRESERVE)
}
_export_live(){ # A770B_LIVE_* for the sidecar write on start; empty values mean the sidecar is not written
  A770B_LIVE_CARD="$(a770b_profile_var "$1" CARD)"
  A770B_LIVE_BACKEND="$(a770b_profile_var "$1" BACKEND)"
  A770B_LIVE_MODE="$(a770b_profile_var "$1" MODE)"
  A770B_LIVE_PROFILE="$1"
  export A770B_LIVE_CARD A770B_LIVE_BACKEND A770B_LIVE_MODE A770B_LIVE_PROFILE
}
_envelope_for_backend(){ # the row's backend selects the container envelope. An unknown backend is refused, never
  # defaulted onto the Vulkan envelope (a silent wrong-backend serve).
  local be; be=$(a770b_profile_var "$1" BACKEND)
  if [ -z "$be" ]; then
    A770B_SERVED_BACKEND="vulkan"
    export A770B_SERVED_BACKEND
    return 0
  fi
  select_serve_envelope "$be"
}
_envelope_from_sidecar(){ # the running container's backend selects the envelope, so `stop` ends it whatever it is
  local be; be=$(python3 "$A770B_PROJECT/harness/live_backend.py" read --path "$A770B_DATA/logs/live-backend.json" --pidfile "$PIDF" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("backend") or "")' 2>/dev/null) || be=""
  case "$be" in vulkan|sycl|vllm|sglang) select_serve_envelope "$be";; esac
  return 0
}
_reload_lock_held(){ # true when another process holds the run lock; this shell's own fd 9 does not count
  if { true >&9; } 2>/dev/null; then return 1; fi
  if ( flock -n 8 ) 8>>"$A770B_DATA/logs/local-build.lock" 2>/dev/null; then return 1; fi
  return 0
}
_clear_serve_evidence_pointer(){
  local ev="$A770B_DATA/logs/serve-evidence.path"
  if [ -f "$ev" ] && [ ! -L "$ev" ]; then
    rm -f -- "$ev"
  fi
}
_write_serve_evidence(){ # <profile> <backend> <model-path> — best-effort (E4): pipe the compose service's own
  # container log into engine_evidence.py log, so every successful serve leaves evidence beside the run's other
  # results, and record its path for a caller (the stage-counter diff, the ladder) to pick up. docker, python or
  # an unreadable log failing here must never fail the serve that already succeeded — warn once and carry on.
  local profile="$1" model="$3" engine cid container out logtext
  _clear_serve_evidence_pointer
  engine="${A770B_SERVED_ENGINE:-}"
  if [ -z "$engine" ]; then
    echo "⚠ A770B_SERVED_ENGINE is empty — skipping serve evidence for $profile" >&2
    return 0
  fi
  cid=$(cat "$A770B_DATA/logs/llamacpp-a770.cid" 2>/dev/null | tr -d '[:space:]' || true)
  container="${cid:-${A770B_COMPOSE_PROJECT}-llama-1}"           # prefer the recorded container id from start
  mkdir -p "$A770B_DATA/results" "$A770B_DATA/logs"
  out="$A770B_DATA/results/serve-$profile-$(date +%Y%m%d-%H%M%S).evidence.json"
  # docker's own exit status is checked BEFORE python3 ever runs (rather than piping straight into it): python3
  # reads only its stdin and cannot see a failed upstream, so a straight pipe would still write a near-empty
  # evidence file — and its own success would make the pipeline look like it worked without pipefail's help.
  if logtext=$("$A770B_DOCKER" logs "$container" 2>&1) \
      && printf '%s\n' "$logtext" | python3 "$A770B_PROJECT/harness/engine_evidence.py" \
           log --engine "$engine" --log - --model "$model" --out "$out"
  then
    python3 - "$A770B_PROJECT/harness" "$out" "$A770B_DATA/logs/profiling.json" <<'PY' || true
import sys
sys.path.insert(0, sys.argv[1])
import profiling_contract
profiling_contract.merge_receipt_file(sys.argv[2], sys.argv[3])
PY
    printf '%s\n' "$out" > "$A770B_DATA/logs/serve-evidence.path"
  else
    _clear_serve_evidence_pointer
    echo "⚠ could not write serve evidence for $profile (the container log or engine_evidence.py failed) — continuing" >&2
  fi
}
_metrics_snapshot(){ # <out-file> — best-effort GET of /metrics; a failure writes nothing and warns. a770b_curl sends the key.
  local out="$1" url="http://$A770B_HOST:$A770B_PORT/metrics" body rc=1
  # Strata usage comes from the request stream. An attached endpoint can also have its own auth mode.
  [ "${A770B_SERVED_ENGINE:-}" = strata ] && return 0
  if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
    body=$(curl -sf --max-time 5 "$url" 2>/dev/null) && rc=0
  else
    body=$(a770b_curl -sf --max-time 5 "$url" 2>/dev/null) && rc=0
  fi
  if [ "$rc" = 0 ] && [ -n "$body" ]; then
    printf '%s\n' "$body" > "$out"
  else
    echo "⚠ could not snapshot /metrics to $(basename "$out") — continuing" >&2
  fi
}
_stage_counters(){ # <label> — engine_evidence.py diff over <label>.metrics-before.prom/.metrics-after.prom, into
  # <label>.counters.json; best-effort like the snapshot it reads (E4) — a failure here warns and leaves no
  # counters.json (engine_evidence.py diff itself already tolerates a missing before/after file with a note).
  local label="$1" engine
  engine="${A770B_SERVED_ENGINE:-}"
  # An attached server's /metrics counts every client. This run's count is the tap or the container log.
  if [ "${A770B_ATTACHED:-}" = 1 ]; then
    return 0
  fi
  # engine_evidence.py diff does not accept sglang. Calling it is only the generic failure warning.
  [ "$engine" = strata ] && return 0
  if [ "$engine" = sglang ]; then
    echo "⚠ sglang has no prometheus counter diff" >&2
    return 0
  fi
  if [ -z "$engine" ]; then
    echo "⚠ A770B_SERVED_ENGINE is empty — skipping stage counters for $label" >&2
    return 0
  fi
  if ! python3 "$A770B_PROJECT/harness/engine_evidence.py" diff --engine "$engine" \
      "$A770B_DATA/results/$label.metrics-before.prom" "$A770B_DATA/results/$label.metrics-after.prom" \
      --out "$A770B_DATA/results/$label.counters.json"
  then
    echo "⚠ could not compute stage counters for $label — continuing" >&2
  fi
}
_serve_start(){ # start; the serve script owns the ready wait. 1 = start failed
  # extra is a deliberate word list from the env, expanded unquoted on purpose so each word is an argument
  # shellcheck disable=SC2086
  local quant; quant=$(a770b_profile_var "$p" QUANT)
  local tool_parser; tool_parser=$(a770b_profile_var "$p" TOOL_PARSER)
  local reasoning_parser; reasoning_parser=$(a770b_profile_var "$p" REASONING_PARSER)
  # This start only. A vLLM row may name an image the pinned compose file does not hold.
  local vllm_image; vllm_image=$(a770b_profile_var "$p" VLLM_IMAGE)
  (
    if [ -n "$vllm_image" ]; then export A770B_VLLM_IMAGE="$vllm_image"; fi
    QUANT=$quant TOOL_PARSER=$tool_parser REASONING_PARSER=$reasoning_parser \
      KV_K=$kv KV_V=${kv_v:-$kv} REASONING=$reasoning \
      THINKING_MODE=$thinking_mode THINKING_EFFORT=$thinking_effort THINKING_BUDGET=$thinking_budget \
      THINKING_BUDGET_MESSAGE=$thinking_budget_message THINKING_PRESERVE=$thinking_preserve \
      bash "$SERVE" start "$gguf" "$ctx" $extra
  ) || return 1
  _write_serve_evidence "$p" "${A770B_SERVED_BACKEND:-}" "$gguf"
  local thinking_desc="mode ${thinking_mode:-$reasoning}"
  [ -n "$thinking_effort" ] && thinking_desc="$thinking_desc · effort $thinking_effort"
  [ -n "$thinking_budget" ] && thinking_desc="$thinking_desc · budget $thinking_budget"
  [ "$thinking_preserve" = "false" ] && thinking_desc="$thinking_desc · preserve off" || thinking_desc="$thinking_desc · preserve on"
  echo "✓ $p serving $(basename "$gguf") · ctx $ctx · KV $kv/${kv_v:-$kv} · thinking $thinking_desc · $A770B_HOST:$A770B_PORT"
  _override_warn "$p"
  return 0
}
_serve_start_or_die(){
  local rc=0; _serve_start || rc=$?
  [ "$rc" = 0 ] && return 0
  die "server did not start — see the error above (a budget-gate or VRAM-cap refusal, or the server's own failure)"
}
# The container's image id has to be the id serve would start, and that image
# has to pass the same trust gate a start runs. A retag of the name does not
# keep the previous bytes. A refused digest does not stay up because its id matches.
_running_image_matches(){
  local cid running wanted pinned vllm_image
  cid=$(_compose_container_id) || return 1
  [ -n "$cid" ] || return 1
  running=$("$A770B_DOCKER" inspect -f '{{.Image}}' "$cid" 2>/dev/null) || return 1
  [ -n "$running" ] || return 1
  if [ "${A770B_SERVED_BACKEND:-}" = vllm ]; then
    vllm_image=$(a770b_profile_var "$p" VLLM_IMAGE 2>/dev/null || true)
    [ -n "$vllm_image" ] && export A770B_VLLM_IMAGE="$vllm_image"
  fi
  pinned=$(bash "$A770B_PROJECT/harness/serve_compose.sh" gate) || return 1
  wanted=$("$A770B_DOCKER" image inspect -f '{{.Id}}' "$pinned" 2>/dev/null) || return 1
  case "$wanted" in
    sha256:*) ;;
    *) return 1 ;;
  esac
  [ "$running" = "$wanted" ]
}
# The sidecar does not name this pid. Keep the container when the mark is this model,
# this context, and this argv signature, the override file hashes the same, the compose
# file is this envelope, and /health is ready. A missing mark line or a missing override
# does not keep. A ladder start writes the pidfile and the mark, and writes the sidecar
# only when A770B_LIVE_* is set. On that match the sidecar below is rewritten with the
# requested profile: the name then means this launch, not an identity read off the server.
# An empty live field or a failed write fails the keep, because the verifier refuses a
# server this function would otherwise accept.
# A RAM cap or cgroup that is not part of the argv signature is not part of the match.
_serve_keep_unmarked(){
  local pid mark_model mark_ctx mark_hash cid files part found=0
  local quant tool_parser reasoning_parser sig_out sig_ctx sig_hash ov ov_hash
  pid=$(_recorded_serve_pid "$PIDF") || return 1
  [ -f "$MARK" ] || return 1
  mark_model=""; mark_ctx=""; mark_hash=""
  {
    IFS= read -r mark_model || true
    IFS= read -r mark_ctx || true
    IFS= read -r mark_hash || true
  } < "$MARK"
  [ -n "$mark_model" ] && [ "$(basename -- "$mark_model")" = "$req_model" ] || return 1
  [ -n "$mark_ctx" ] && [ "$mark_ctx" = "$ctx" ] || return 1
  [ -n "$mark_hash" ] || return 1
  quant=$(a770b_profile_var "$p" QUANT)
  tool_parser=$(a770b_profile_var "$p" TOOL_PARSER)
  reasoning_parser=$(a770b_profile_var "$p" REASONING_PARSER)
  # extra is a deliberate word list, expanded unquoted on purpose so each word is an argument
  # shellcheck disable=SC2086
  sig_out=$(
    QUANT=$quant TOOL_PARSER=$tool_parser REASONING_PARSER=$reasoning_parser \
      KV_K=$kv KV_V=${kv_v:-$kv} REASONING=$reasoning \
      THINKING_MODE=$thinking_mode THINKING_EFFORT=$thinking_effort THINKING_BUDGET=$thinking_budget \
      THINKING_BUDGET_MESSAGE=$thinking_budget_message THINKING_PRESERVE=$thinking_preserve \
      bash "$A770B_PROJECT/harness/serve_compose.sh" signature "$gguf" "$ctx" $extra
  ) || return 1
  sig_ctx=$(printf '%s\n' "$sig_out" | sed -n '1p')
  sig_hash=$(printf '%s\n' "$sig_out" | sed -n '2p')
  [ -n "$sig_ctx" ] && [ -n "$sig_hash" ] || return 1
  [ -z "$(printf '%s\n' "$sig_out" | sed -n '3p')" ] || return 1
  [ "$mark_ctx" = "$sig_ctx" ] && [ "$mark_hash" = "$sig_hash" ] || return 1
  ov="$A770B_DATA/logs/compose-argv.override.json"
  [ -f "$ov" ] || return 1
  ov_hash=$(python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from compose_override import llama_service_signature
try:
    doc = json.load(open(sys.argv[2], encoding="utf-8"))
    print(llama_service_signature(doc["services"]["llama"]))
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    sys.exit(1)
' "$A770B_PROJECT/harness" "$ov") || return 1
  [ "$ov_hash" = "$sig_hash" ] || return 1
  cid=$(_compose_container_id) || return 1
  [ -n "$cid" ] || return 1
  files=$("$A770B_DOCKER" inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$cid" 2>/dev/null || true)
  [ -n "$files" ] || return 1
  while IFS= read -r part; do
    [ "$part" = "$A770B_COMPOSE_FILE" ] && found=1
  done < <(printf '%s\n' "$files" | tr ',' '\n')
  [ "$found" = 1 ] || return 1
  health_word=$(bash "$SERVE" health 2>/dev/null) || true
  [ "$health_word" = ready ] || return 1
  if ! a770b_curl -sf --max-time 3 "http://$A770B_HOST:$A770B_PORT/v1/models" >/dev/null; then
    echo "↻ the running server does not accept the key in $A770B_API_KEY_FILE (rotated?) — restarting it"
    return 1
  fi
  if ! _running_image_matches; then
    echo "↻ the running server is not the pinned image — restarting it"
    return 1
  fi
  if [ -z "${A770B_LIVE_CARD:-}" ] || [ -z "${A770B_LIVE_BACKEND:-}" ] || [ -z "${A770B_LIVE_MODE:-}" ] || [ -z "${A770B_LIVE_PROFILE:-}" ]; then
    echo "↻ no card, mode or backend to record the live identity — restarting it"
    return 1
  fi
  python3 "$LIVE_PY" write --path "$A770B_DATA/logs/live-backend.json" \
    --card "$A770B_LIVE_CARD" --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" \
    --model "$req_model" --profile "$A770B_LIVE_PROFILE" --pid "$pid" || return 1
  echo "✓ $p already up ($(basename -- "$gguf"))"
  _override_warn "$p"
  return 0
}
_override_warn(){ # a card the operator marked operator_override: say so, so it is never a surprise
  local p="$1" v; v=$(a770b_profile_var "$p" OPERATOR_OVERRIDE)
  case "${v,,}" in true|1|yes) echo "⚠ $p is presented by operator override — set on the card (operator_override) or in A770B_${p^^}_OPERATOR_OVERRIDE; unset it to choose from all cards";; esac
}
_strata_serve(){
  local evidence
  [ -z "$extra" ] || { echo "Strata engine flags belong in the prepared config" >&2; return 2; }
  evidence=$(bash "$A770B_PROJECT/harness/serve_strata.sh" start "$gguf" "$ctx") || return $?
  A770B_ALIAS=$(bash "$A770B_PROJECT/harness/serve_strata.sh" model) || return $?
  export A770B_ALIAS A770B_STRATA_AUTH=key A770B_SERVED_BACKEND=strata
  printf '%s\n' "$evidence" > "$A770B_DATA/logs/serve-evidence.path"
  echo "✓ $p serving $A770B_ALIAS on $A770B_HOST:$A770B_PORT · Strata"
}
# _verify_served_selection <profile> — before anything is dispatched, refuse a rollback to another profile.
# Compose path: the live sidecar (pid-checked by live_backend.py) names <profile>, and the container now
# running is the one recorded at start (full id) whose host pid the sidecar names.
# Strata path: there is no sidecar name to copy. SERVED_VERIFIED_PROFILE is the requested profile, meaning
# the managed launch is launch-equivalent to it; build_local.sh re-verifies the alias and the context.
# Unmarked-keep path: the sidecar has just been rewritten with the requested profile after a model,
# context and argv-signature match, so the name checked here is that launch, not a separate observation.
# Sets SERVED_VERIFIED_PROFILE, SERVED_VERIFIED_CID and SERVED_VERIFIED_PID; 1 (with the reason on stderr) otherwise.
_verify_served_selection(){
  local p="$1" live_json live_profile live_pid short full recorded host
  SERVED_VERIFIED_PROFILE=""; SERVED_VERIFIED_CID=""; SERVED_VERIFIED_PID=""
  if [ -n "${A770B_SERVE_ROLLED_BACK_TO:-}" ] && [ "$A770B_SERVE_ROLLED_BACK_TO" != "$p" ]; then
    echo "the reload of $p failed and the server rolled back to $A770B_SERVE_ROLLED_BACK_TO" >&2; return 1
  fi
  if [ "${A770B_SERVED_ENGINE:-}" = strata ]; then
    # No sidecar names this profile. The requested name is recorded as launch-equivalent; build_local.sh
    # re-verifies the managed launch (alias and context) before dispatch.
    SERVED_VERIFIED_PROFILE="$p"; SERVED_VERIFIED_PID=$(bash "$A770B_PROJECT/harness/serve_strata.sh" pid 2>/dev/null || true)
    return 0
  fi
  live_json=$(python3 "$A770B_PROJECT/harness/live_backend.py" read --path "$A770B_DATA/logs/live-backend.json" --pidfile "$PIDF" 2>/dev/null) || live_json=""
  [ -n "$live_json" ] && [ "$live_json" != null ] || { echo "no live server identity (sidecar) names the server that is up" >&2; return 1; }
  IFS=$'\t' read -r live_profile live_pid < <(printf '%s' "$live_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["profile"], d["pid"], sep="\t")') \
    || { echo "the live server identity could not be read" >&2; return 1; }
  [ "$live_profile" = "$p" ] || { echo "the server that is up is profile $live_profile, not $p" >&2; return 1; }
  short=$(_compose_container_id) || { echo "the running container could not be read" >&2; return 1; }
  [ -n "$short" ] || { echo "no container of this project is running" >&2; return 1; }
  full=$("$A770B_DOCKER" inspect -f '{{.Id}}' "$short" 2>/dev/null | tr -d '[:space:]') || full=""
  full=${full#sha256:}
  [[ "$full" =~ ^[0-9a-f]{64}$ ]] || { echo "the running container's id could not be read" >&2; return 1; }
  recorded=$(tr -d '[:space:]' < "$A770B_DATA/logs/llamacpp-a770.cid" 2>/dev/null || true)
  [ "$recorded" = "$full" ] || { echo "the running container ${full:0:12} is not the one recorded at start (${recorded:0:12})" >&2; return 1; }
  host=$("$A770B_DOCKER" inspect -f '{{.State.Pid}}' "$full" 2>/dev/null | tr -d '[:space:]') || host=""
  [ -n "$host" ] && [ "$host" = "$live_pid" ] || { echo "the running container's host pid (${host:-unknown}) is not the server the sidecar names ($live_pid)" >&2; return 1; }
  SERVED_VERIFIED_PROFILE="$live_profile"; SERVED_VERIFIED_CID="$full"; SERVED_VERIFIED_PID="$live_pid"
}
# _publish_serving_selection <profile> — for a caller sampling this run's server (run_suite.sh sets
# A770B_SERVING_SELECTION_FILE): written only after _verify_served_selection. served_profile is the name that
# check accepted. On the compose path that name was read from the sidecar. On the Strata path and the
# unmarked-keep path it is the requested profile and means launch-equivalent to that profile (see
# _verify_served_selection), not a name copied from a separate identity. The container id and host pid are
# what the check verified.
_publish_serving_selection(){
  local f="${A770B_SERVING_SELECTION_FILE:-}" tmp
  [ -n "$f" ] || return 0
  tmp="$f.tmp.$$"
  if python3 -c 'import json, sys
keys = ("profile", "served_profile", "engine", "cid", "pid")
doc = dict(zip(keys, sys.argv[1:6]))
doc["verified"] = True
with open(sys.argv[6], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
    fh.write("\n")' \
      "$1" "$SERVED_VERIFIED_PROFILE" "${A770B_SERVED_ENGINE:-}" "$SERVED_VERIFIED_CID" "$SERVED_VERIFIED_PID" "$tmp" 2>/dev/null \
      && mv -f -- "$tmp" "$f"; then
    return 0
  fi
  rm -f -- "$tmp" 2>/dev/null || true
  echo "⚠ could not record the serving selection for $1 — the kit run will not sample this server's cgroup" >&2
}
serve(){ local p="$1" gguf ctx kv kv_v reasoning extra t
  A770B_SERVE_ROLLED_BACK_TO=""                                   # set below only when a reload rolls back to the old profile
  local thinking_mode thinking_effort thinking_budget thinking_budget_message thinking_preserve
  local live_json live_backend live_model live_profile req_backend req_model
  local lock_held run_alive msg old_profile LIVE_PY health_word skip_unmarked_keep=0 cid crc=0
  local -a rb_args
  profile_vars "$p"
  A770B_SERVED_ENGINE=$(_profile_engine "$p") || exit $?
  export A770B_SERVED_ENGINE
  # Refuse a forbidden measurement flag before any docker or card command.
  if [ -n "$extra" ]; then
    # extra is a deliberate word list, expanded unquoted on purpose so each word is an argument
    # shellcheck disable=SC2086
    python3 - "$A770B_PROJECT/harness" "$A770B_SERVED_ENGINE" $extra <<'PY' || return $?
import sys
sys.path.insert(0, sys.argv[1])
import profiling_contract
if sys.argv[2] == "strata":
    if len(sys.argv) > 3:
        print("⛔ Strata engine flags belong in the prepared config", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(0)
message = profiling_contract.refusal(sys.argv[2], sys.argv[3:])
if message:
    print("⛔ " + message, file=sys.stderr)
    raise SystemExit(2)
PY
  fi
  if [ "$A770B_SERVED_ENGINE" = strata ]; then _strata_serve; return $?; fi
  local strata_rc=0
  bash "$A770B_PROJECT/harness/serve_strata.sh" id >/dev/null || strata_rc=$?
  [ "$strata_rc" = 1 ] || { echo "stop managed Strata before starting another builder engine" >&2; return 2; }
  _envelope_for_backend "$p"
  if [ "$A770B_SERVED_ENGINE" = "vllm" ] || [ "$A770B_SERVED_ENGINE" = "sglang" ]; then
    [ -r "$gguf" ] || die "model not found: $gguf — put the model file or directory in A770B_MODELS ($A770B_MODELS) or set A770B_${p^^}_MODEL"
  else
    [ -r "$gguf" ] || die "model not found: $gguf — put the GGUF in A770B_MODELS ($A770B_MODELS) or set A770B_${p^^}_MODEL"
  fi
  _export_live "$p"
  LIVE_PY="$A770B_PROJECT/harness/live_backend.py"
  req_backend=$(a770b_profile_var "$p" BACKEND)
  export A770B_SERVED_BACKEND="${req_backend:-vulkan}"
  req_model=$(basename "$gguf")
  # read deletes the sidecar when the pidfile is missing. Do not call it then, and do not stop a container
  # that is already this project's when there is no pidfile to name it.
  live_json=""
  if [ -f "$PIDF" ]; then
    live_json=$(python3 "$LIVE_PY" read --path "$A770B_DATA/logs/live-backend.json" --pidfile "$PIDF" 2>/dev/null) || live_json=""
  elif [ -n "$(_compose_container_id)" ]; then
    echo "serve refused: the container is running and the pidfile is missing"
    return 1
  fi
  if [ -n "$live_json" ]; then
    live_backend=$(printf '%s' "$live_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["backend"])')
    live_model=$(printf '%s' "$live_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model"])')
    live_profile=$(printf '%s' "$live_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["profile"])')
    # The profile is part of the identity: two rows can share the weights, the backend and the image and still
    # differ in their serve settings (a RAM tier). Another profile's container is never reused as this one.
    if [ "$live_backend" = "$req_backend" ] && [ "$live_model" = "$req_model" ] && [ "$live_profile" != "$p" ]; then
      echo "↻ the running server is profile ${live_profile:-unknown}, not $p — restarting it"
    fi
    if [ "$live_backend" = "$req_backend" ] && [ "$live_model" = "$req_model" ] && [ "$live_profile" = "$p" ]; then
      health_word=$(bash "$SERVE" health 2>/dev/null) || true
      if [ "$health_word" = ready ]; then
        if a770b_curl -sf --max-time 3 "http://$A770B_HOST:$A770B_PORT/v1/models" >/dev/null; then
          cid=$(_compose_container_id)
          crc=$?
          if [ "$crc" -ne 0 ]; then
            echo "↻ the running container could not be read — restarting it"
            skip_unmarked_keep=1
          elif [ -z "$cid" ]; then
            echo "↻ the running server is not this project's container — restarting it"
            skip_unmarked_keep=1
          elif _running_image_matches; then
            echo "✓ $p already up ($(basename "$gguf"))"; _override_warn "$p"; return 0
          else
            echo "↻ the running server is not the pinned image — restarting it"
            skip_unmarked_keep=1
          fi
        else
          echo "↻ the running server does not accept the key in $A770B_API_KEY_FILE (rotated?) — restarting it"
          # One restart line, then stop and start. Do not fall through into the unmarked keep.
          skip_unmarked_keep=1
        fi
      else
        echo "serve refused: $A770B_HOST:$A770B_PORT did not answer /health"
        return 1
      fi
    else
      if [ "$live_backend" != "$req_backend" ] && python3 "$LIVE_PY" remote-cannot-switch --host "$A770B_HOST"; then
        echo "reload refused: remote URL cannot switch backend"
        return 1
      fi
      lock_held=0; run_alive=0
      if _reload_lock_held; then lock_held=1; fi
      if run_pid_alive "$A770B_DATA/logs/run.pid" >/dev/null; then run_alive=1; fi
      rb_args=(); [ "$lock_held" = 1 ] && rb_args+=(--lock-held); [ "$run_alive" = 1 ] && rb_args+=(--run-pid-alive)
      msg=$(python3 "$LIVE_PY" reload-blocked "${rb_args[@]}")
      if [ -n "$msg" ]; then echo "$msg"; return 1; fi
      python3 "$LIVE_PY" reload-sentence --backend "$req_backend" --model "$req_model"
      old_profile=$live_profile
      a770b_is_profile "$old_profile" && _envelope_for_backend "$old_profile"   # stop the OLD server on ITS envelope
      bash "$SERVE" stop >/dev/null 2>&1
      _envelope_for_backend "$p"                                                # then start the new one on its own
      if _serve_start; then return 0; fi
      echo "reload failed: rolling back to $old_profile"
      bash "$SERVE" stop >/dev/null 2>&1
      if ! a770b_is_profile "$old_profile"; then echo "rollback failed: server is down"; return 1; fi
      p=$old_profile
      A770B_SERVE_ROLLED_BACK_TO=$old_profile
      profile_vars "$p"
      A770B_SERVED_ENGINE=$(_profile_engine "$p") || exit $?
      export A770B_SERVED_ENGINE
      _envelope_for_backend "$p"
      if [ ! -r "$gguf" ]; then echo "rollback failed: server is down"; return 1; fi
      _export_live "$p"
      if _serve_start; then return 0; fi
      bash "$SERVE" stop >/dev/null 2>&1
      echo "rollback failed: server is down"
      return 1
    fi
  fi
  # Empty or stale sidecar. The container from a direct serve_compose.sh start can
  # already be this profile; stopping it starts a second load.
  # A key-rotation restart has already said so. Do not keep, and do not say it again.
  if [ "$skip_unmarked_keep" != 1 ]; then
    if _serve_keep_unmarked; then return 0; fi
  fi
  bash "$SERVE" stop >/dev/null 2>&1
  _serve_start_or_die
}
# bench_profile <profile> [--pp <sizes>] [--dry-run] — serve that profile, then llama-benchy. Not bench_model.sh.
bench_profile(){
  local p dry=0
  local -a pp=() http_args=()
  p="${1:?}"
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --pp)
        shift
        while [ $# -gt 0 ]; do
          case "$1" in
            --*) break ;;
            *) pp+=("$1"); shift ;;
          esac
        done
        [ "${#pp[@]}" -gt 0 ] || die "--pp needs sizes"
        ;;
      *) die "unknown arg $1" ;;
    esac
  done
  profile_vars "$p"
  # The ladder passes the directory that holds tokenizer.json. The weight file itself
  # is not that directory: llama-benchy would read the whole file into RAM.
  local tok_dir eng
  eng=$(_profile_engine "$p") || return $?
  if [ "$eng" = strata ]; then
    tok_dir=$(bash "$A770B_PROJECT/harness/serve_strata.sh" tokenizer "$gguf") || return $?
    A770B_ALIAS=$(bash "$A770B_PROJECT/harness/serve_strata.sh" config-model "$gguf") || return $?
  elif [ -n "${A770B_TOKENIZER_DIR:-}" ]; then tok_dir="$A770B_TOKENIZER_DIR"
  elif [ -d "$gguf" ]; then tok_dir="$gguf"; else tok_dir=$(dirname -- "$gguf"); fi
  if [ ! -f "$tok_dir/tokenizer.json" ] && [ ! -f "$tok_dir/tokenizer_config.json" ]; then
    die "bench refused: $gguf has no tokenizer.json or tokenizer_config.json"
  fi
  if [ "$dry" != 1 ]; then
    run_lock
    serve "$p" || return $?
  fi
  http_args=(--base-url "http://$A770B_HOST:$A770B_PORT" --served-model-name "$A770B_ALIAS" --tokenizer "$tok_dir")
  [ "$dry" = 1 ] && http_args+=(--dry-run)
  [ "${#pp[@]}" -gt 0 ] && http_args+=(--pp "${pp[@]}")
  bash "$A770B_PROJECT/harness/bench_http.sh" "${http_args[@]}"
}
# _attach_running_server <base-url> <served-name> [<engine> <ctx>] — use a server that is already up. Does not start or stop one.
# Requires a plain http://host:port or http://host:port/v1 URL; health is the origin /health.
# The explicit engine argument accepts strata. Validate its v0.2.16 health and model endpoints, context and auth
# mode through strata_attach.py. Strata uses 127.0.0.1.
_write_attachment_metadata(){
  [ -n "${A770B_ATTACHMENT_METADATA_FILE:-}" ] || return 0
  python3 - "$A770B_ATTACHMENT_METADATA_FILE" "${A770B_SERVED_ENGINE:-}" "${A770B_SERVED_BACKEND:-}" <<'PY'
import json, os, sys, tempfile
path = sys.argv[1]
parent = os.path.dirname(path) or "."
os.makedirs(parent, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".attachment.", dir=parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"engine": sys.argv[2] or None, "backend": sys.argv[3] or None}, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
}
_attach_running_server(){
  local url="$1" name="$2" engine="${3:-}" ctx="${4:-}" origin code ev_logs ev_base parsed
  [ -n "$url" ] && [ -n "$name" ] || die "--base-url and --served-model-name are required together"
  case "$engine" in
    ''|strata) ;;
    *) die "--engine accepts strata only (got '$engine'); llama.cpp, vllm and sglang are named by the attached server itself" ;;
  esac
  unset A770B_STRATA_AUTH
  parsed=$(python3 -c '
import sys
from urllib.parse import urlsplit
raw_in = sys.argv[1]
if "?" in raw_in or "#" in raw_in:
    sys.exit(1)
raw = raw_in[:-1] if raw_in.endswith("/") else raw_in
try:
    u = urlsplit(raw)
except ValueError:
    sys.exit(1)
if u.scheme != "http":
    sys.exit(1)
if u.username is not None or u.password is not None or "@" in (u.netloc or ""):
    sys.exit(1)
if u.query or u.fragment:
    sys.exit(1)
if u.path not in ("", "/v1"):
    sys.exit(1)
host = u.hostname
try:
    port = u.port
except ValueError:
    sys.exit(1)
if not host or port is None or not (1 <= port <= 65535):
    sys.exit(1)
print(f"http://{u.netloc}\t{host}\t{port}")
' "$url" 2>/dev/null) || die "--base-url must be a plain http://host:port or http://host:port/v1 URL (no https, userinfo, extra path, query, or fragment): $1"
  IFS=$'\t' read -r origin A770B_HOST A770B_PORT <<<"$parsed"
  if [ "$engine" = strata ]; then
    # One spelling: the sandbox listener binds 127.0.0.1, the rendered client URL and the usage tap's upstream are
    # built from this host, and none of them is shown to take a bracketed IPv6 or a resolved name.
    [ "$A770B_HOST" = 127.0.0.1 ] || die "Strata attach accepts only http://127.0.0.1:<port>[/v1] (got host $A770B_HOST)"
    case "$ctx" in ''|*[!0-9]*|0*) die "Strata attach needs the caller's window as a positive integer (got '${ctx}')" ;; esac
    export A770B_HOST A770B_PORT A770B_ALIAS="$name"
    local verdict="" _ok auth usable
    verdict=$(python3 "$A770B_PROJECT/harness/strata_attach.py" attach --base-url "$origin" --model "$name" --ctx "$ctx") \
      || die "Strata candidate refused at $origin: ${verdict:-the attach check failed}"
    read -r _ok auth usable <<<"$verdict"
    export A770B_STRATA_AUTH="$auth" A770B_SERVED_ENGINE=strata A770B_SERVED_BACKEND=strata
    _write_attachment_metadata || echo "⚠ could not publish verified attachment engine metadata" >&2
    _clear_serve_evidence_pointer
    export A770B_ATTACHED=1
    echo "▶ attached $A770B_HOST:$A770B_PORT model $A770B_ALIAS · engine strata (explicit, externally managed; auth $auth; ctx $ctx ≤ usable $usable) — not starting or stopping a server"
    return 0
  fi
  export A770B_HOST A770B_PORT A770B_ALIAS="$name"
  code=$(curl -sS -o /dev/null --max-time 5 -w '%{http_code}' "$origin/health" 2>/dev/null || true)
  case "$code" in
    2*) ;;
    *) die "server not healthy at $origin/health (HTTP ${code:-none})" ;;
  esac
  # The engine is the one this server names. An unknown or missing name is empty,
  # and the capture then does not read a container log.
  local models_body="" owned=""
  A770B_SERVED_ENGINE=""
  A770B_SERVED_BACKEND=""
  if [ -s "$A770B_API_KEY_FILE" ]; then
    models_body=$(a770b_curl -sS --max-time 5 "$origin/v1/models" 2>/dev/null || true)
    owned=$(printf '%s' "$models_body" | python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
rows = data.get("data") if isinstance(data, dict) else None
if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
    raise SystemExit(0)
name = rows[0].get("owned_by")
if isinstance(name, str):
    sys.stdout.write(name)
')
    case "$owned" in
      sglang) A770B_SERVED_ENGINE=sglang; A770B_SERVED_BACKEND=sglang ;;
      vllm) A770B_SERVED_ENGINE=vllm; A770B_SERVED_BACKEND=vllm ;;
      llama.cpp|llamacpp|llama-cpp) A770B_SERVED_ENGINE=llama.cpp ;;
    esac
  fi
  export A770B_SERVED_ENGINE
  _write_attachment_metadata || echo "⚠ could not publish verified attachment engine metadata" >&2
  if [ -z "$A770B_SERVED_ENGINE" ]; then
    echo "served engine was not named; this capture will not read a container log" >&2
  fi
  ev_logs="$A770B_DATA/logs"
  ev_base="serve-evidence.path"
  if [ -f "$ev_logs/$ev_base" ] && [ ! -L "$ev_logs/$ev_base" ]; then
    rm -f -- "$ev_logs/$ev_base"
  fi
  export A770B_ATTACHED=1
  echo "▶ attached $A770B_HOST:$A770B_PORT model $A770B_ALIAS (not starting a server)"
}
status(){ local c cid strata_rc=0; c=$(current); version 2>&1
  bash "$A770B_PROJECT/harness/serve_strata.sh" status || strata_rc=$?
  if [ "$strata_rc" = 0 ]; then :
  elif [ "$strata_rc" != 1 ]; then echo "server: managed Strata ownership could not be read"
  elif [ -n "$c" ]; then echo "server: UP · $(basename "$c") · pid $(cat "$PIDF")"
  else
    cid=$(_compose_container_id)
    if [ -n "$cid" ]; then echo "server: UP · container ${cid}"
    else echo "server: down"; fi
  fi
  curl -s --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | head -c 80; echo
  local _vram_s; _vram_s=$(gpu_used_gib 2>/dev/null) || _vram_s="unavailable"
  if [ "$_vram_s" = "unavailable" ] || [ -z "$_vram_s" ]; then
    echo "builder card: $A770B_DEVICE ($A770B_GPU_MATCH) · mode $A770B_CARD_MODE · registry $A770B_PROFILES_FILE · VRAM used unavailable · cap $A770B_VRAM_CAP_GIB"
  else
    echo "builder card: $A770B_DEVICE ($A770B_GPU_MATCH) · mode $A770B_CARD_MODE · registry $A770B_PROFILES_FILE · VRAM used $_vram_s GiB · cap $A770B_VRAM_CAP_GIB"
  fi
  if [ -n "$A770B_PROFILES" ]; then printf 'profiles:'; for p in $A770B_PROFILES; do printf ' %s = %s ctx %s ·' "$p" "$(a770b_profile_var "$p" MODEL)" "$(a770b_profile_var "$p" CTX)"; done; echo " models in $A770B_MODELS"; else empty_registry_line; fi
  echo "project $A770B_PROJECT · data $A770B_DATA · seat $A770B_SEAT"
  if ! : 9>>"$A770B_DATA/logs/local-build.lock" 2>/dev/null; then echo "run lock: unknown (cannot open $A770B_DATA/logs/local-build.lock)"; elif ( flock -n 9 ) 9>>"$A770B_DATA/logs/local-build.lock"; then echo "run lock: free"; else echo "run lock: HELD — a run, verify, serve or reset is in progress; wait for it"; fi
  if pid=$(run_pid_alive "$A770B_DATA/logs/run.pid"); then echo "run: pid $pid in progress since $(date -r "$A770B_DATA/logs/run.pid" +%H:%M:%S 2>/dev/null || echo "a moment ago") — local-build.sh stop-run ends exactly it"; else echo "run: none"; fi
  local n; if [ -d "$A770B_SEAT/.git" ]; then n=$(seat_dirty "$A770B_SEAT" | wc -l); if [ "$n" = 0 ]; then echo "seat: $A770B_SEAT clean"; else echo "seat: $A770B_SEAT has $n uncommitted or ignored entries — local-build.sh reset before a run"; fi; else echo "seat: $A770B_SEAT is not a git clone"; fi
}
# doctor — what this machine lacks to run the seat, one line per check; never stops at the first MISSING. No run lock, no server.
doctor(){
  local missing=0 t p m mp n warn_list w u hint _cap_default _vsel_n _envfile_ok _v _img _oc_bin
  command -v python3 >/dev/null 2>&1 && echo "ok   python3: on PATH" || { echo "MISSING python3: install Python 3 (the registry, the renderer and the capture use it)"; missing=$((missing+1)); }
  for t in bwrap socat uv curl git flock timeout; do
    command -v "$t" >/dev/null 2>&1 && echo "ok   $t: on PATH" || { echo "MISSING $t: install $t"; missing=$((missing+1)); }
  done
  if [ -d "$A770B_UV_CACHE" ] && command -v uv >/dev/null 2>&1; then
    if uv_run_no_project --with pytest --with pytest-asyncio python -c 'import pytest, pytest_asyncio' >/dev/null 2>&1; then
      echo "ok   offline grader: pytest + pytest-asyncio resolve from the warm cache"
    else
      echo "MISSING offline grader: pytest + pytest-asyncio are not resolvable from $A770B_UV_CACHE; run bash harness/warm_cache.sh"
      missing=$((missing+1))
    fi
  else
    echo "MISSING offline grader: uv cache is absent at $A770B_UV_CACHE; run bash harness/warm_cache.sh"
    missing=$((missing+1))
  fi
  _oc_bin=$(a770b_opencode_bin 2>/dev/null || true)
  if [ -n "$_oc_bin" ] && [ -d "$_oc_bin" ] && [ -x "$_oc_bin/opencode" ] && a770b_require_opencode_v1 >/dev/null 2>&1; then
    echo "ok   opencode: $_oc_bin/opencode"
  elif [ -z "${A770B_OPENCODE_BIN:-}" ] && [ -x "$A770B_OPENCODE_HOME/opencode" ] && A770B_OPENCODE_BIN="$A770B_OPENCODE_HOME" a770b_require_opencode_v1 >/dev/null 2>&1; then
    echo "ok   opencode: $A770B_OPENCODE_HOME/opencode"
  elif [ -z "${A770B_OPENCODE_BIN:-}" ] && a770b_opencode_pin_supported; then
    echo "ok   opencode: a profile run installs OpenCode 1.19.6 when PATH is not OpenCode 1"
  elif [ -n "$_oc_bin" ] && [ -x "$_oc_bin/opencode" ]; then
    echo "MISSING opencode: OpenCode 1 is required; 1.19.6 is the qualified release; set A770B_OPENCODE_BIN to that binary's directory"
    missing=$((missing+1))
  else
    echo "MISSING opencode: expected executable at \${A770B_OPENCODE_BIN:-PATH}/opencode; set A770B_OPENCODE_BIN to its directory"
    missing=$((missing+1))
  fi
  if [ "$A770B_SERVE" = compose ]; then
    # compose mode: the server is the container, so a host llama-server is NOT required; the
    # container runtime and its compose plugin are. The envelope and its gitignored env file are checked too.
    if command -v "$A770B_DOCKER" >/dev/null 2>&1 && "$A770B_DOCKER" compose version >/dev/null 2>&1; then
      echo "ok   $A770B_DOCKER compose: on PATH (A770B_SERVE=compose — no host llama-server needed)"
    else
      echo "MISSING $A770B_DOCKER compose: A770B_SERVE=compose needs the container runtime and its compose plugin on PATH"; missing=$((missing+1))
    fi
    [ -f "$A770B_COMPOSE_FILE" ] && echo "ok   compose envelope: $A770B_COMPOSE_FILE" || { echo "MISSING compose envelope: $A770B_COMPOSE_FILE not found"; missing=$((missing+1)); }
    # the envelope cannot interpolate without its image/DRM/GID values: the gitignored env file supplies them, or
    # they are set in the environment (builder.env is exported). Either way, a missing value is a MISSING check.
    local _img_var="A770B_LLAMA_IMAGE"
    local -a _needed_vars=(A770B_LLAMA_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_RENDER_GID A770B_VIDEO_GID)
    case "${A770B_SERVED_BACKEND:-}" in
      vllm)
        _img_var="A770B_VLLM_IMAGE"
        _needed_vars=(A770B_VLLM_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_BY_PATH_DIR A770B_RENDER_GID A770B_VIDEO_GID)
        ;;
      sglang)
        _img_var="A770B_SGLANG_IMAGE"
        _needed_vars=(A770B_SGLANG_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_SGLANG_CARD_NODE A770B_SGLANG_RENDER_NODE A770B_RENDER_GID A770B_VIDEO_GID)
        ;;
      *)
        case "$A770B_COMPOSE_FILE" in
          */vllm.yaml|vllm.yaml)
            _img_var="A770B_VLLM_IMAGE"
            _needed_vars=(A770B_VLLM_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_BY_PATH_DIR A770B_RENDER_GID A770B_VIDEO_GID)
            ;;
          */b70-sglang.yaml|b70-sglang.yaml)
            _img_var="A770B_SGLANG_IMAGE"
            _needed_vars=(A770B_SGLANG_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_SGLANG_CARD_NODE A770B_SGLANG_RENDER_NODE A770B_RENDER_GID A770B_VIDEO_GID)
            ;;
        esac
        ;;
    esac
    _envfile_ok=1; for _v in "${_needed_vars[@]}"; do [ -n "${!_v:-}" ] || _envfile_ok=0; done
    if [ -f "$A770B_COMPOSE_ENV_FILE" ]; then echo "ok   compose env: $A770B_COMPOSE_ENV_FILE"
    elif [ "$_envfile_ok" = 1 ]; then echo "ok   compose env: the envelope's image/DRM/GID values are set in the environment"
    else echo "MISSING compose env: $A770B_COMPOSE_ENV_FILE not found and the envelope's image/DRM/GID values are not all set — copy ${A770B_COMPOSE_ENV_FILE}.example"; missing=$((missing+1)); fi
    # A floating tag can be moved by upstream at any time; the plan pins the exact bytes by digest after the first
    # pull. Warn, never fail: the first pull legitimately starts from the tag, and a digest silences this. The
    # environment wins over the gitignored env file, so read the value the envelope will actually interpolate.
    _img="${!_img_var:-}"
    if [ -z "$_img" ] && [ -f "$A770B_COMPOSE_ENV_FILE" ]; then
      _img=$(grep -E "^[[:space:]]*${_img_var}=" "$A770B_COMPOSE_ENV_FILE" | tail -n 1 | cut -d= -f2-)
    fi
    _img="${_img%\"}"; _img="${_img#\"}"; _img="${_img%\'}"; _img="${_img#\'}"
    case "$_img" in
       *33ed2a8936e94ac893a50eed3d2958141d352198233d107e58c1c7ee3b96a9b4*|\
       *7b4905e41d2720bfccf390e78e938e8148329b3da9467d3f1b008f0efc44cd97*|\
       *52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b*|\
       *24c872759256d4b0d3b9d0419ebdf6f3547e0fdbd3891fbb9f90e32ad255c94f*|\
       *b763b3587a8fd40f7575d8a12b38071cc2ed8a88086925e6936da7727723412f*)
         echo "WARN image: ${_img_var}=$_img still trusts the compromised emSign ECC Root CA - G3 root. Rebuild it with compose/refresh-ca.Dockerfile and pin that image. serve refuses this name (warning only — doctor still passes)";;
       *@sha256:*) : ;;   # pinned: name@sha256:<digest> names exactly one image, so nothing to warn about
       "") : ;;           # unset is already a MISSING above; do not report the same gap twice
       *) echo "WARN image: ${_img_var}=$_img is a floating tag, not a digest — pull it once, then pin the exact bytes (docker inspect --format='{{index .RepoDigests 0}}' $_img) and set ${_img_var}=ghcr.io/ggml-org/llama.cpp@sha256:<digest> in $A770B_COMPOSE_ENV_FILE; see ${A770B_COMPOSE_ENV_FILE}.example (warning only — doctor still passes)";;
    esac
  fi
  if command -v nvtop >/dev/null 2>&1 || [ "$A770B_ALLOW_NO_NVTOP" = 1 ]; then echo "ok   nvtop: on PATH or A770B_ALLOW_NO_NVTOP=1"; else echo "MISSING nvtop: install it for VRAM readings, or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop"; missing=$((missing+1)); fi
  if [ -z "$A770B_GPU_MATCH" ]; then
    # an unset GPU_MATCH must never be matched: '' is a substring of every device name, so on a one-card machine it
    # would read as "exactly one match" and pass — a false green. The MISSING input line for A770B_GPU_MATCH names
    # the fix; here the card and mode lines just say "not measured".
    n=""
    echo "ok   card: not measured (A770B_GPU_MATCH unset)"
  else
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
  fi
  if llama_pid_alive "$PIDF" >/dev/null || [ -n "$(_compose_container_id)" ] ||
      bash "$A770B_PROJECT/harness/serve_strata.sh" id >/dev/null 2>&1; then
    echo "ok   mode: $A770B_CARD_MODE, not measured (the seat's server is up)"
  elif [ "$n" != 1 ]; then
    echo "ok   mode: $A770B_CARD_MODE, not measured (no VRAM readings or more than one matching card)"
  else
    local _raw_u=""
    if _raw_u=$(gpu_used_gib 2>/dev/null) && u=$(python3 -c "import sys; print(f'{float(sys.argv[1]):.2f}')" "$_raw_u" 2>/dev/null); then
      case "$A770B_CARD_MODE" in
        inference)
          if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0.5 else 1)" "$u"; then
            echo "MISSING mode: inference, but the builder card holds $u GiB with the seat's server down — something else draws it (a desktop?); set A770B_CARD_MODE=display, or free the card"; missing=$((missing+1))
          else
            echo "ok   mode: inference; the builder card holds $u GiB with the seat's server down"
          fi ;;
        *)
          hint=""
          python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= 0.1 else 1)" "$u" && hint=" — no desktop measured on it; A770B_CARD_MODE=inference would serve the inference registry under its measured cap"
          echo "ok   mode: display; the builder card holds $u GiB with the seat's server down$hint" ;;
      esac
    elif [ "${A770B_ALLOW_NO_NVTOP:-0}" = 1 ]; then
      echo "ok   mode: $A770B_CARD_MODE, not measured (VRAM reading unavailable)"
    else
      echo "MISSING mode: VRAM reading unavailable on $A770B_GPU_MATCH"; missing=$((missing+1))
    fi
  fi
  if [ -z "$A770B_PROFILES_FILE" ] || [ ! -f "$A770B_PROFILES_FILE" ]; then
    echo "MISSING registry: $(empty_registry_line)"; missing=$((missing+1))
  elif python3 "$A770B_PROJECT/harness/profiles.py" check --file "$A770B_PROFILES_FILE" >/dev/null 2>&1; then
    echo "ok   registry: $(basename "$A770B_PROFILES_FILE") checks"
  else
    echo "MISSING registry: $(basename "$A770B_PROFILES_FILE") does not check (python3 harness/profiles.py check says why)"; missing=$((missing+1))
  fi
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
  # card inputs: A770B_VK_DEVICE_SELECT, A770B_GPU_MATCH and A770B_VRAM_CAP_GIB carry NO default — they name THIS
  # machine's builder card, and an unset value is a MISSING input, never a fallback to a card this project once
  # shipped (no card identity is the default). A770B_UBATCH stays a fixed default: 512 keeps GPU jobs under the
  # Intel Xe watchdog on any Intel card. One line per knob: the value in force, or the MISSING it needs.
  _a770b_required_input(){ local var="$1" label="$2" hint="$3" val
    val="${!var}"
    if [ -n "$val" ]; then echo "ok   input $var=$val — $label"; else echo "MISSING input $var is unset — $label; $hint"; missing=$((missing+1)); fi
  }
  _a770b_doctor_input(){ local var="$1" default="$2" label="$3" val
    val="${!var}"
    if [ "$val" = "$default" ]; then echo "ok   input $var=$val — $label (this project's default)"
    else echo "ok   input $var=$val — $label (your own override)"; fi
  }
  local _cards_file="${A770B_CARDS_FILE:-$A770B_PROJECT/config/cards.json}"
  local _known_cards=""
  if [ -f "$_cards_file" ]; then
    _known_cards=$(python3 -c 'import json,sys; cards=json.load(open(sys.argv[1])).get("cards",{}); print(", ".join(sorted(k for k, v in cards.items() if v.get("role", "builder") == "builder")))' "$_cards_file" 2>/dev/null || true)
  fi
  if [ -z "${A770B_CARD:-}" ]; then
    echo "MISSING input A770B_CARD is unset — the builder card identity; set it in builder.env to one of: ${_known_cards:-b70, a770}"
    missing=$((missing+1))
  elif [ -n "$_known_cards" ] && ! python3 -c 'import json,sys; cards=json.load(open(sys.argv[1])).get("cards",{}); role=sys.argv[3] or cards.get(sys.argv[2],{}).get("role","builder"); sys.exit(0 if sys.argv[2] in cards and role == "builder" else 1)' "$_cards_file" "$A770B_CARD" "${A770B_CARD_ROLE:-}" 2>/dev/null; then
    echo "MISSING input A770B_CARD=$A770B_CARD is unknown — set it in builder.env to one of: $_known_cards"
    missing=$((missing+1))
  else
    echo "ok   input A770B_CARD=$A770B_CARD — the builder card identity"
  fi
  _a770b_required_input A770B_VK_DEVICE_SELECT "the Vulkan selector pinning the builder card" "set it to your card's vendor:device! from 'lspci -nn' (e.g. 8086:e223! for the Arc Pro B70)"
  _a770b_required_input A770B_GPU_MATCH "nvtop device-name substring naming the builder card alone" "set it from 'nvtop -s' (e.g. 'G31' or 'Arc Pro B70')"
  _a770b_required_input A770B_VRAM_CAP_GIB "the VRAM cap for mode $A770B_CARD_MODE" "measure it on your card, then set it (raise only after measuring what else the card holds)"
  _a770b_doctor_input A770B_UBATCH "512" "ubatch, kept under the GPU reset watchdog"

  [ -d "$A770B_UV_CACHE" ] && echo "ok   uv cache at $A770B_UV_CACHE" || { echo "MISSING uv cache at $A770B_UV_CACHE: run bash harness/warm_cache.sh once (the sandbox has no network)"; missing=$((missing+1)); }
  if [ "$missing" = 0 ]; then echo "ok   doctor: all checks passed"; return 0; else echo "MISSING doctor: $missing missing"; return 1; fi
}
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
case "${1:-}" in
  serve)  card_lock; [ -n "${2:-}" ] || { if [ -n "$A770B_PROFILES" ]; then die "serve needs a profile name (there is no default profile) — one of: $A770B_PROFILES"; else die "$(empty_registry_line)"; fi; }; serve "$2" ;;
  bench)  [ -n "${2:-}" ] || { if [ -n "$A770B_PROFILES" ]; then die "serve needs a profile name (there is no default profile) — one of: $A770B_PROFILES"; else die "$(empty_registry_line)"; fi; }; case "$2" in --*) die "unknown arg $2";; esac; shift; bench_profile "$@" ;;
  reset)  WT=$(guard_worktree "${2:-$A770B_SEAT}") || exit 2; seat_lock "$WT"; reset_worktree "$WT" ;;
  status) status ;;
  profiles) if [ -n "$A770B_PROFILES" ]; then shift; python3 "$A770B_PROJECT/harness/profiles.py" card --file "$A770B_PROFILES_FILE" --served "$@"; else empty_registry_line; fi ;;
  menu) if [ -n "$A770B_PROFILES" ]; then python3 "$A770B_PROJECT/harness/profiles.py" menu --file "$A770B_PROFILES_FILE" --sidecar "$A770B_DATA/logs/live-backend.json" --pidfile "$A770B_DATA/logs/llamacpp-a770.pid"; else python3 "$A770B_PROJECT/harness/profiles.py" menu --card "${A770B_CARD:-}" --mode "$A770B_CARD_MODE"; fi ;;
  doctor) doctor ;;
  qualify) shift; exec bash "$A770B_PROJECT/harness/qualify.sh" "$@" ;;
  stop)
    card_lock
    strata_rc=0
    bash "$A770B_PROJECT/harness/serve_strata.sh" id >/dev/null || strata_rc=$?
    case "$strata_rc" in
      0) bash "$A770B_PROJECT/harness/serve_strata.sh" stop && _clear_serve_evidence_pointer ;;
      1) _envelope_from_sidecar; _clear_serve_evidence_pointer; bash "$SERVE" stop ;;
      *) die "managed Strata ownership could not be read" ;;
    esac ;;
  stop-run)
    # end exactly the run in progress, through its own pid. The pid file names the process-group leader,
    # harness/run_lead.py (which also keeps a caller fence). One TERM to that leader (and to its group, when
    # the pid leads one) ends the run — the capture is written, the seat reset. A live run_lead.py leader
    # forwards that TERM to the worker group. The worker group is a second group, recorded in
    # logs/run.worker-pgid; it is signalled only by signal_recorded_worker_group, and only if this leader
    # is still alive after the wait below. Never by process name: bwrap is also every Flatpak app on the desktop.
    RUNPID="$A770B_DATA/logs/run.pid"
    pid=$(run_pid_alive "$RUNPID") || die "no run to stop: $RUNPID names no live run leader of this harness (a finished run removes the file; a stale or foreign pid is refused, never signalled)"
    mkdir -p "$A770B_DATA/logs"
    _interrupt_tmp=""
    _interrupt_tmp=$(mktemp "$A770B_DATA/logs/.run-interrupted.XXXXXX" 2>/dev/null) || _interrupt_tmp=""
    if [ -n "$_interrupt_tmp" ]; then
      if ! printf '%s\n' "$pid" > "$_interrupt_tmp" || ! mv -f -- "$_interrupt_tmp" "$A770B_DATA/logs/run.interrupted"; then
        rm -f -- "$_interrupt_tmp" 2>/dev/null || true
        _interrupt_tmp=""
      fi
    fi
    # The owner channel: build_local.sh, the leader's parent, marks the run stopped on USR1 whatever the
    # receipt did, so a worker that handles TERM and exits 0 is still not graded. USR1 goes only to an owner
    # run_owner_pid verifies (this project's build_local.sh, declared by the leader, with its USR1 handler
    # installed): a legacy owner or an unrelated parent would be killed by it, so neither is ever signalled.
    _owner=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)
    _owner_told=0
    if run_owner_pid "$pid" "$_owner"; then
      kill -USR1 "$_owner" 2>/dev/null && _owner_told=1
    fi
    # The third channel, and the one that needs neither a file nor a lookup: a run_lead.py leader exits 128+signal
    # on this TERM whatever the worker does. A leader that is not run_lead.py with no other channel would let a
    # worker that handles TERM and exits 0 be graded, so that one stop is refused before anything is signalled.
    if [ -z "$_interrupt_tmp" ]; then
      if [ "$_owner_told" = 1 ]; then
        echo "⚠ could not persist the interruption receipt; the run's owner (build_local.sh pid $_owner) was told directly, signaling the verified run" >&2
      elif run_lead_pid "$pid"; then
        echo "⚠ could not persist the interruption receipt and found no verified build_local.sh owner to tell; the run's leader (run_lead.py pid $pid) records the stop itself, signaling the verified run" >&2
      else
        die "stop-run refused: the interruption receipt could not be written, no verified build_local.sh owner was found, and run pid $pid is not a run_lead.py leader that records a stop itself — a worker that exits 0 on TERM would be graded, so nothing was signalled. Make $A770B_DATA/logs writable and run stop-run again"
      fi
    fi
    echo "▶ ending run pid $pid (the leader group): one TERM, then up to 30 s for the capture and the reset to follow"
    _pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)
    if [ -n "$_pgid" ] && [ "$_pgid" = "$pid" ]; then
      kill -TERM -- "-$pid" 2>/dev/null || die "could not signal pid $pid"
    else
      kill -TERM "$pid" 2>/dev/null || die "could not signal pid $pid"
    fi
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
      # The leader is still alive, so it did not reap the worker. Signal the group the leader published
      # (TERM, then KILL if that same group is still there). A mismatch signals nothing. Not by process name.
      _worker_pgid="${A770B_WORKER_PGID_FILE:-$A770B_DATA/logs/run.worker-pgid}"
      signal_recorded_worker_group "$_worker_pgid" || true
      for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
    fi
    if kill -0 "$pid" 2>/dev/null; then
      die "run pid $pid is still alive after 30 s. The recorded worker group (${A770B_WORKER_PGID_FILE:-$A770B_DATA/logs/run.worker-pgid}) was signalled only if its pid still existed, led its own group, and its start time matched; nothing was signalled by process name. Read the build log before ending anything by hand"
    fi
    echo "✓ run pid $pid ended; the local-build.sh run that owns it is now capturing what the seat did and resetting the seat" ;;
  run)
    unset A770B_ATTACHED A770B_STRATA_AUTH
    shift; profile=""; timeout=""; spec=""; profile_set=0; base_url=""; served_name=""; engine=""
    # `run <brief.md>` uses the default seat; `run <worktree> <brief.md>` names one
    if [ -f "${1:-}" ] && [ ! -d "${1:-}" ]; then WT_RAW="$A770B_SEAT"; BRIEF="$1"; shift 1; else WT_RAW="${1:?worktree or brief}"; BRIEF="${2:?brief.md}"; shift 2; fi
    while [ $# -gt 0 ]; do case "$1" in --profile) profile="${2:?profile name}"; require_profile "$profile"; profile_set=1; shift;; --timeout) timeout="${2:?seconds}"; shift;; --spec) spec="${2:?spec.json}"; shift;; --base-url) base_url="${2:?base url}"; shift;; --served-model-name) served_name="${2:?served model name}"; shift;; --engine) engine="${2:?engine}"; shift;; *) die "unknown arg $1";; esac; shift; done
    if [ -n "$base_url" ] || [ -n "$served_name" ]; then
      [ -n "$base_url" ] && [ -n "$served_name" ] || die "--base-url and --served-model-name are required together"
    fi
    # An explicit engine describes the attachment. A named profile selects a managed engine.
    [ -z "$engine" ] || [ -n "$base_url" ] || die "--engine needs --base-url: --engine strata attaches to an externally managed endpoint and never starts a server"
    WT=$(guard_worktree "$WT_RAW") || exit 2                       # BEFORE anything is touched
    mkdir -p "$A770B_DATA/logs"
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
    [ -n "$profile" ] || { if [ -n "$A770B_PROFILES" ]; then die "no profile: pass --profile <name> (there is no default profile) or set \"profile\" in the specification — one of: $A770B_PROFILES"; else die "$(empty_registry_line)"; fi; }
    # A spec with no fit is a measurement and is allowed. A spec with fit must clear the profile's
    # measured_tasks entry for fit.task. The refusal names the failed check. This is before the run lock
    # and before any server starts.
    if [ -n "$SPEC_SNAP" ]; then
      fit_line=$(python3 - "$SPEC_SNAP" "$profile" "${A770B_PROFILES_FILE:-}" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["A770B_PROJECT"], "harness"))
from render_profile import fit_failed_check
spec_path, profile, profiles_file = sys.argv[1:4]
spec = json.load(open(spec_path, encoding="utf-8"))
fit = spec.get("fit") if isinstance(spec, dict) else None
if not isinstance(fit, dict):
    sys.exit(0)
prof = None
if profiles_file:
    try:
        data = json.load(open(profiles_file, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if isinstance(profiles, dict) and isinstance(profiles.get(profile), dict):
        prof = profiles[profile]
name = fit_failed_check(fit, prof)
if name:
    print("fit: %s: failed check %s" % (fit.get("task"), name))
    sys.exit(2)
PY
) || { rm -f -- "$SPEC_SNAP"; die "${fit_line:-fit refused}"; }
    fi
    A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1 || exit 2
    card_lock                                                      # card lock before seat lock (single lock order)
    seat_lock "$WT"                                                # exclusive seat lock on canonical worktree path
    dirty=$(seat_dirty "$WT"); [ -z "$dirty" ] || { printf '%s\n' "$dirty" | head -5 >&2; die "seat $WT is not clean (ignored files count) — nothing from a previous run may pass as this one's; run: local-build.sh reset $WT"; }
    profile_vars "$profile"
    if [ -n "$base_url" ]; then
      _attach_running_server "$base_url" "$served_name" "$engine" "$ctx"   # exports host, port, alias and engine; does not call serve or stop
    else
      serve "$profile" || exit $?
      # Nothing is dispatched (or graded) unless the server that is up is verifiably this profile.
      if ! _verify_served_selection "$profile"; then
        [ -z "${A770B_SERVING_SELECTION_FILE:-}" ] || rm -f -- "$A770B_SERVING_SELECTION_FILE"
        echo "⛔ run refused: the server that is up is not verifiably $profile — nothing was dispatched" >&2
        exit 3
      fi
      _publish_serving_selection "$profile"
    fi
    label="$profile-$(date +%Y%m%d-%H%M%S)"
    # The profile's timeout_s is recorded headroom, not a clock. No --timeout and no spec timeout
    # means no clock. An explicit --timeout or spec timeout is a caller fence for this run only.
    if [ -n "$timeout" ]; then
      echo "▶ profile=$profile timeout=${timeout}s worktree=$WT brief=$BRIEF label=$label"
      fence_env=(LOCAL_TIMEOUT="$timeout")
    else
      echo "▶ profile=$profile timeout=none worktree=$WT brief=$BRIEF label=$label"
      fence_env=()
    fi
    mkdir -p "$A770B_DATA/results"
    _metrics_snapshot "$A770B_DATA/results/$label.metrics-before.prom"
    aside=0
    restore(){ if [ "$aside" = 1 ] && [ -f "$WT/AGENTS.md.local-off" ]; then mv -f "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"; aside=0; fi; }
    trap restore EXIT INT TERM                                    # restored even on crash or timeout
    if [ -f "$WT/AGENTS.md" ]; then mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"; aside=1; fi
    env -u LOCAL_TIMEOUT "${fence_env[@]}" A770B_SPEC="$SPEC_SNAP" PROFILE="$profile" LOCAL_AGENT=local-builder bash "$BUILD" "$WT" "$BRIEF" 2>&1 | grep -vE '^\s*$' | tail -15 | cut -c1-240
    brc=${PIPESTATUS[0]}
    restore
    case "$brc" in 2|3) echo "⛔ build refused (exit $brc) — no capture, no reset" >&2; exit "$brc";; esac
    BL=$(cat "$A770B_DATA/logs/last-build.log.path" 2>/dev/null || true)
    [ -f "$BL" ] || die "build log path missing — capture skipped"
    _metrics_snapshot "$A770B_DATA/results/$label.metrics-after.prom"
    _stage_counters "$label"
    A770B_SPEC="$SPEC_SNAP" bash "$CAPTURE" "$label" "$WT" "$BL" | tail -2; crc=${PIPESTATUS[0]}
    [ "$crc" = 0 ] || { echo "⛔ the capture failed (exit $crc) and the seat was NOT reset: read $A770B_DATA/results/$label.task.md, then run: local-build.sh reset $WT" >&2; exit "$crc"; }
    echo "▶ capture: $A770B_DATA/results/$label.task.md — review it before merging; the worktree has been reset to clean."
    if [ "$brc" -ge 128 ] 2>/dev/null && [ "$brc" -le 159 ] 2>/dev/null; then
      echo "⚠ builder process ended by signal (exit $brc); preserving capture without treating it as a completed run" >&2
      exit "$brc"
    fi ;;
  verify)
    # Re-apply what a run produced (<label>.patch beside the capture) to a CLEAN seat and run public + hidden phases
    # inside the same boundary the model had — no server, no bridge, no key. Emits explicit phase status to both
    # <label>.verify.md and <label>.verify.json (plus A770B_VERIFY_OUT when set) so infra_error / timeout / not_run
    # are never masked as model fail.
    _verify_published=0
    _verify_infra_attempted=0
    VJSON=""
    label=""
    timeout=""
    _verify_publish_json(){
      local status="$1" code="$2" pub_s="$3" hid_s="$4" is_infra="$5" is_to="$6" err_msg="${7:-}"
      mkdir -p "$A770B_DATA/results" 2>/dev/null || true
      python3 - "$VJSON" "${A770B_VERIFY_OUT:-}" "$label" "$status" "$code" "$pub_s" "$hid_s" "$is_infra" "$is_to" "$err_msg" "${timeout:-}" <<'PY'
import json, os, sys, tempfile
vjson_path, handoff_path, label, status, code_s, pub_s, hid_s, is_infra, is_to, err_msg, timeout_s = sys.argv[1:12]
doc = {
    "label": label or None,
    "status": status,
    "exit_code": int(code_s),
    "public": pub_s,
    "hidden": hid_s,
    "infra_error": (is_infra == "1"),
    "timed_out": (is_to == "1"),
    "timeout_s": int(timeout_s) if timeout_s.isdigit() and int(timeout_s) > 0 else None,
}
if err_msg:
    doc["error"] = err_msg
payload = json.dumps(doc, indent=2) + "\n"
errors = []
for dest in (vjson_path, handoff_path):
    if not dest:
        continue
    try:
        d = os.path.dirname(os.path.abspath(dest))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".verify-", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, dest)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:
        errors.append(f"{dest}: {exc}")
if errors:
    sys.stderr.write("⛔ verify publish failed: " + "; ".join(errors) + "\n")
    sys.exit(1)
PY
    }
    _verify_infra_die(){
      local msg="$1"
      _verify_infra_attempted=1
      if _verify_publish_json "infra_error" 2 "not_run" "not_run" 1 0 "$msg"; then
        _verify_published=1
      fi
      die "$msg"
    }
    shift; SRC="${1:?capture label or .patch path}"; shift; WT_RAW="$A770B_SEAT"; TEST=""
    while [ $# -gt 0 ]; do case "$1" in --test) TEST="${2:?command}"; shift;; --timeout) timeout="${2:?seconds}"; shift;; *) if [ -d "$1" ]; then WT_RAW="$1"; else _verify_infra_die "unknown arg $1"; fi;; esac; shift; done
    if [ -f "$SRC" ]; then
      PATCH=$(realpath -e -- "$SRC")
      label=$(basename "${PATCH%.patch}")
    else
      _src_base=$(basename "$SRC")
      _src_stem="${_src_base%.task.md}"
      label="${_src_stem%.patch}"
      PATCH="$A770B_DATA/results/$label.patch"
    fi
    OUT="$A770B_DATA/results/$label.verify.md"
    VJSON="$A770B_DATA/results/$label.verify.json"
    # Remove any stale verification files for this label/handoff before checks or grading start.
    rm -f -- "$VJSON" "$OUT" 2>/dev/null || true
    if [ -n "${A770B_VERIFY_OUT:-}" ]; then
      rm -f -- "$A770B_VERIFY_OUT" 2>/dev/null || true
    fi
    [ -f "$PATCH" ] || _verify_infra_die "patch not found: $PATCH (every capture writes <label>.patch beside <label>.task.md)"
    [ -s "$PATCH" ] || _verify_infra_die "patch is empty — that run changed nothing, there is nothing to verify"
    if [ -n "$timeout" ]; then
      case "$timeout" in
        ''|*[!0-9]*|0) _verify_infra_die "--timeout must be a positive integer (got '$timeout')" ;;
      esac
    fi
    WT=$(guard_worktree "$WT_RAW") || _verify_infra_die "worktree refused: $WT_RAW"
    _verify_abort_trap(){
      local rc=$?
      if [ "$_verify_published" != 1 ] && [ "$_verify_infra_attempted" != 1 ] && [ "$rc" -ne 0 ]; then
        _verify_publish_json "infra_error" "$rc" "not_run" "not_run" 1 0 "verify aborted before grading (exit $rc)" || true
      fi
    }
    trap '_verify_abort_trap' EXIT
    seat_lock "$WT"                                                # exclusive lock on canonical seat; inherits if caller holds it
    SPECF="${PATCH%.patch}.spec.json"; HIDDEN=(); HROOT_RAW="${A770B_HIDDEN_ROOT:-$A770B_DATA/hidden}"
    if [ -f "$SPECF" ]; then
      # the hidden root is a real directory, never a link, and every file in it is checked at its resolved path
      [ -d "$HROOT_RAW" ] && [ ! -L "$HROOT_RAW" ] || _verify_infra_die "A770B_HIDDEN_ROOT is not a real directory (a symlink is refused): $HROOT_RAW"
      HROOT=$(realpath -e -- "$HROOT_RAW")
      [ -n "$TEST" ] || TEST=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print((d.get("verify") or {}).get("test") or "")' "$SPECF")
      while IFS= read -r h; do
        [ -n "$h" ] || continue
        case "$h" in *[!A-Za-z0-9._-]*|.*|-*) _verify_infra_die "hidden test name is not a plain basename: '$h'";; esac
        [ -f "$HROOT/$h" ] && [ ! -L "$HROOT/$h" ] || _verify_infra_die "hidden test not found under $HROOT, or a symlink: $h"
        [ "$(stat -c %s -- "$HROOT/$h")" -le 65536 ] || _verify_infra_die "hidden test over 64 KiB: $h"
        HIDDEN+=("$h")
      done < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(x) for x in ((d.get("verify") or {}).get("hidden") or [])]' "$SPECF")
    fi
    dirty=$(seat_dirty "$WT"); [ -z "$dirty" ] || { printf '%s\n' "$dirty" | head -5 >&2; _verify_infra_die "seat $WT is not clean (ignored files count) — it is reset after every run; run: local-build.sh reset $WT"; }
    pub_files=(); hid_files=(); files=()
    if [ -z "$TEST" ]; then
      # the default: pytest on every tests/*.py file the patch touches — file names pass as SEPARATE argv words and must be
      # plain (a model-chosen name is untrusted input; a shell metacharacter in it is refused, never interpreted)
      while IFS= read -r f; do
        case "$f" in *[!A-Za-z0-9._/-]*|*..*|/*) _verify_infra_die "unsafe test path in the patch: '$f' — pass --test '<command>' if you still want to run it";; esac
        pub_files+=("$f"); files+=("$f")
      done < <(grep -oE '^\+\+\+ b/tests?/[^[:space:]]+\.py$' "$PATCH" | sed 's#^+++ b/##' | sort -u)
      [ ${#pub_files[@]} -gt 0 ] || [ ${#HIDDEN[@]} -gt 0 ] || _verify_infra_die "the patch adds or changes no tests/*.py file — pass --test '<command to run inside the sandbox>'"
    fi
    safe_git "$WT" apply --check "$PATCH" || _verify_infra_die "patch does not apply cleanly to $WT"
    trap 'reset_worktree "$WT" >/dev/null; _verify_abort_trap' EXIT; trap 'reset_worktree "$WT" >/dev/null; exit 130' INT TERM   # clean again whatever happens
    safe_git "$WT" apply "$PATCH" || _verify_infra_die "patch failed to apply"
    if [ ${#HIDDEN[@]} -gt 0 ]; then
      # the destination directory is the seat's own tests/, never a link the patch or the model planted
      mkdir -p "$WT/tests"; [ -d "$WT/tests" ] && [ ! -L "$WT/tests" ] && [ "$(realpath -e -- "$WT/tests")" = "$WT/tests" ] || _verify_infra_die "the patched seat's tests/ is a symlink or missing — hidden tests refused"
    fi
    for h in "${HIDDEN[@]}"; do
      [ ! -e "$WT/tests/_hidden_$h" ] && [ ! -L "$WT/tests/_hidden_$h" ] || _verify_infra_die "the patched seat already has tests/_hidden_$h — refused, a hidden test must not be overwritten"
      cp -P -- "$HROOT/$h" "$WT/tests/_hidden_$h"; chmod 644 "$WT/tests/_hidden_$h"; hid_files+=("tests/_hidden_$h"); files+=("tests/_hidden_$h")
    done
    mkdir -p "$A770B_DATA/results" "$A770B_DATA/logs"
    CFG="$A770B_DATA/logs/opencode.verify.jsonc"; a770b_render_profile verify 32768 "$CFG" nokey || _verify_infra_die "could not render verify sandbox config"
    PUB_CMD=(); HID_CMD=()
    if [ -n "$TEST" ]; then
      PUB_CMD=(bash -c "$TEST")
    elif [ ${#pub_files[@]} -gt 0 ]; then
      PUB_CMD=(uv run --with pytest --with pytest-asyncio python -m pytest -q "${pub_files[@]}")
    fi
    if [ ${#hid_files[@]} -gt 0 ]; then
      HID_CMD=(uv run --with pytest --with pytest-asyncio python -m pytest -q "${hid_files[@]}")
    fi
    if [ ${#PUB_CMD[@]} -gt 0 ] && [ ${#HID_CMD[@]} -gt 0 ]; then
      CMD_DESC="${PUB_CMD[*]} && ${HID_CMD[*]}"
    elif [ ${#PUB_CMD[@]} -gt 0 ]; then
      CMD_DESC="${PUB_CMD[*]}"
    else
      CMD_DESC="${HID_CMD[*]}"
    fi
    # An omitted --timeout is not a clock, the same rule run already uses.
    # A number here is only the caller's fence; host TimeoutExpired is distinguished from grader exit 124.
    if [ -n "$timeout" ]; then
      echo "▶ verify $label · timeout=${timeout}s · seat $WT · inside the sandbox: $CMD_DESC"
    else
      echo "▶ verify $label · timeout=none · seat $WT · inside the sandbox: $CMD_DESC"
    fi
    { echo "# Verify — $label — $(date -Is)"; echo; echo "patch: $PATCH"; echo "applied to: $WT ($(seat_dirty "$WT" | wc -l) entries)"; echo "hidden tests: ${HIDDEN[*]:-none}"; echo "command, inside the sandbox (no model, no bridge, no key): $CMD_DESC"; echo; echo '```'; } > "$OUT"
    VRAW=$(mktemp "$A770B_DATA/logs/verify-raw-XXXXXX.log")
    PUB_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${PUB_CMD[@]}")
    HID_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${HID_CMD[@]}")
    v_py_rc=0
    # exec replaces the substitution shell, so the grader's parent is this
    # verify process. A SIGKILL of verify then kills the grader. FDs 7 and 9
    # are the seat and card locks; the grader must not keep them.
    v_py_out=$(
      if ( : >&7 ) 2>/dev/null; then exec 7>&-; fi
      if ( : >&9 ) 2>/dev/null; then exec 9>&-; fi
      exec python3 -I -B - "$$" "$A770B_PROJECT/harness/sandbox_run.sh" "$WT" "$CFG" "$VRAW" "$VJSON" "${A770B_VERIFY_OUT:-}" "$label" "${timeout:-}" "$PUB_JSON" "$HID_JSON" "$A770B_DATA/logs" <<'PY'
import ctypes, json, os, signal, subprocess, sys, tempfile, time

# The shell passes its pid. getppid() after startup can already be the
# user manager if that shell was killed while Python was starting.
expected_parent = int(sys.argv[1])
sandbox_sh, wt, cfg, raw_path, json_path, handoff_path, label, timeout_s, pub_json, hid_json, log_dir = sys.argv[2:13]
sys.path.insert(0, os.path.dirname(os.path.abspath(sandbox_sh)))
import sandbox_oom

_libc = ctypes.CDLL(None, use_errno=True)
if _libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != expected_parent:
    sys.stderr.write("verify: parent changed during arming\n")
    sys.exit(137)

pub_cmd = json.loads(pub_json)
hid_cmd = json.loads(hid_json)
has_public = len(pub_cmd) > 0
has_hidden = len(hid_cmd) > 0
timeout_val = int(timeout_s) if timeout_s.isdigit() else None
deadline = (time.monotonic() + timeout_val) if timeout_val is not None else None
oom_py = os.path.join(os.path.dirname(os.path.abspath(sandbox_sh)), "sandbox_oom.py")


# Captured before Popen forks. The child compares against this, not a
# getppid() it would treat as the original parent after a reparent.
_owner_pid = os.getpid()


def _arm_child():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != _owner_pid:
        os._exit(137)


def _entered(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as ef:
            return "entered" in ef.read()
    except OSError:
        return False


def _record_oom(record_path, rc):
    # After proc.wait has returned. This call is not inside the timed wait.
    # Its wall time is added back so a hidden phase is not charged for it.
    # A non-zero helper means the decided value was not published. The caller
    # must not grade from the previous or truncated record.
    global deadline
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", oom_py, record_path, str(rc)],
            check=False,
            stdout=subprocess.DEVNULL,
        )
        try:
            with open(record_path, encoding="utf-8") as fh:
                body = json.load(fh)
            if not isinstance(body, dict):
                oom = "unknown"
            else:
                oom = body.get("oom", "unknown")
        except (OSError, ValueError):
            oom = "unknown"
        return oom, proc.returncode == 0
    finally:
        if deadline is not None:
            deadline += time.monotonic() - started


def run_phase(cmd_args, raw_fh):
    if deadline is not None:
        rem = deadline - time.monotonic()
        if rem <= 0:
            return "not_run", 124, "timeout", None, None
    else:
        rem = None
    fd, entered_file = tempfile.mkstemp(prefix="verify-entered-", dir=log_dir)
    os.close(fd)
    os.unlink(entered_file)
    rec_fd, record_path = tempfile.mkstemp(prefix="verify-oom-", suffix=".json", dir=log_dir)
    os.close(rec_fd)
    try:
        os.unlink(record_path)
    except OSError:
        pass
    env = dict(os.environ)
    # The grader's own preexec arms this child. The sandbox takes no parent
    # argument here: its parent is this process.
    env["A770B_NO_BRIDGE"] = "1"
    env["A770B_SANDBOX_ENTERED_FILE"] = entered_file
    env["A770B_SANDBOX_RECORD_FILE"] = record_path
    proc = subprocess.Popen(
        ["bash", sandbox_sh, wt, cfg, "--", *cmd_args],
        stdin=subprocess.DEVNULL,
        stdout=raw_fh,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        close_fds=True,
        preexec_fn=_arm_child,
    )
    try:
        try:
            rc = proc.wait(timeout=rem)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
            proc.wait()
            rc = 124
        # The deadline was only the wait above. Do not consult it again.
        entered = _entered(entered_file)
        norm, signaled = sandbox_oom.normalize_status(rc if not timed_out else 124)
        oom, published = _record_oom(record_path, norm)
        if timed_out:
            return ("timeout" if entered else "not_run"), 124, "timeout", oom, None
        if signaled:
            return "not_run", norm, "infra_error", oom, None
        # Setup that dies before "entered" is the harness, not the model.
        if not entered:
            return "not_run", (norm if norm != 0 else 2), "infra_error", oom, None
        # The command did run. A record that was not published is not a pass
        # and not an OOM grade taken from whatever bytes were left behind.
        if not published:
            code = norm if isinstance(norm, int) and not isinstance(norm, bool) and norm != 0 else 2
            return "not_run", code, "infra_error", oom, None
        status, code, reason = sandbox_oom.grade(norm, oom)
        return status, code, None, oom, reason
    finally:
        for path in (entered_file, record_path):
            try:
                os.unlink(path)
            except OSError:
                pass


inv_override = None
with open(raw_path, "w", encoding="utf-8", errors="replace") as raw_fh:
    if has_public:
        pub_state, pub_rc, pub_inv, pub_oom, pub_reason = run_phase(pub_cmd, raw_fh)
        if pub_inv is not None:
            inv_override = pub_inv
    else:
        pub_state, pub_rc, pub_inv, pub_oom, pub_reason = "none", 0, None, None, None

    if has_hidden:
        if (not has_public or pub_state == "pass") and inv_override is None:
            hid_state, hid_rc, hid_inv, hid_oom, hid_reason = run_phase(hid_cmd, raw_fh)
            if hid_inv is not None:
                inv_override = hid_inv
        else:
            hid_state, hid_rc, hid_inv, hid_oom, hid_reason = "not_run", pub_rc, None, None, None
    else:
        hid_state, hid_rc, hid_inv, hid_oom, hid_reason = "none", 0, None, None, None

if inv_override is not None:
    status = inv_override
    vrc = hid_rc if (has_hidden and hid_inv is not None) else pub_rc
elif has_public and pub_state != "pass":
    status = pub_state
    vrc = pub_rc
elif has_hidden:
    status = hid_state
    vrc = hid_rc
else:
    status = pub_state
    vrc = pub_rc

infra_error = (status == "infra_error")
timed_out = (status == "timeout")
reason = None
if status == "fail" and (pub_reason == "oom" or hid_reason == "oom"):
    reason = "oom"

doc = {
    "label": label,
    "status": status,
    "exit_code": vrc,
    "public": pub_state,
    "hidden": hid_state,
    "infra_error": infra_error,
    "timed_out": timed_out,
    "timeout_s": timeout_val,
    "has_public": has_public,
    "has_hidden": has_hidden,
    "oom": {"public": pub_oom, "hidden": hid_oom},
    "reason": reason,
}
payload = json.dumps(doc, indent=2) + "\n"
staged = []
try:
    for dest in (json_path, handoff_path):
        if not dest:
            continue
        d = os.path.dirname(os.path.abspath(dest))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".verify-", suffix=".json", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        staged.append((tmp, dest))
    replaced = []
    for tmp, dest in staged:
        try:
            os.replace(tmp, dest)
            replaced.append(dest)
        except Exception:
            for prev in replaced:
                try:
                    os.unlink(prev)
                except OSError:
                    pass
            raise
except Exception:
    for tmp, _ in staged:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    raise
print(f"{status} {pub_state} {hid_state} {1 if infra_error else 0} {1 if timed_out else 0} {vrc}")
PY
) || v_py_rc=$?
    v_status="" pub_state="" hid_state="" v_infra="" v_timed="" vrc="" v_extra=""
    v_fields_ok=0
    if [ "$v_py_rc" -eq 0 ] && [ -n "$v_py_out" ]; then
      case "$v_py_out" in
        *$'\n'*) ;;
        *)
          IFS=' ' read -r v_status pub_state hid_state v_infra v_timed vrc v_extra <<<"$v_py_out"
          case "$v_status" in
            pass|fail|timeout|infra_error)
              case "$pub_state" in
                pass|fail|timeout|not_run|none)
                  case "$hid_state" in
                    pass|fail|timeout|not_run|none)
                      case "$v_infra:$v_timed" in
                        [01]:[01])
                          if [ -z "$v_extra" ] && [ -n "$vrc" ]; then
                            case "$vrc" in
                              *[!0-9]*) ;;
                              *) v_fields_ok=1 ;;
                            esac
                          fi
                          ;;
                      esac
                      ;;
                  esac
                  ;;
              esac
              ;;
          esac
          ;;
      esac
    fi
    if [ "$v_fields_ok" != 1 ]; then
      rm -f -- "$VJSON" 2>/dev/null || true
      if [ -n "${A770B_VERIFY_OUT:-}" ]; then
        rm -f -- "$A770B_VERIFY_OUT" 2>/dev/null || true
      fi
      if [ -f "$VRAW" ]; then
        tail -80 "$VRAW" | cut -c1-300 >> "$OUT" 2>/dev/null || true
        rm -f -- "$VRAW"
      fi
      { echo '```'; echo; echo "public: not_run"; echo "hidden: not_run"; echo "status: infra_error"; echo "verdict: INFRA_ERROR (exit 2) — verify publication failed (exit $v_py_rc)"; } >> "$OUT" 2>/dev/null || true
      _verify_infra_die "verify grading or publication failed (exit $v_py_rc)"
    fi
    _verify_published=1
    if [ -f "$VRAW" ]; then
      if ! tail -80 "$VRAW" | cut -c1-300 | tee -a "$OUT"; then
        rm -f -- "$VRAW"
        _verify_published=0
        rm -f -- "$VJSON" 2>/dev/null || true
        if [ -n "${A770B_VERIFY_OUT:-}" ]; then
          rm -f -- "$A770B_VERIFY_OUT" 2>/dev/null || true
        fi
        _verify_infra_die "could not write verify report: $OUT"
      fi
      rm -f -- "$VRAW"
    fi
    summary=$(grep -E '[0-9]+ (passed|failed|error)' "$OUT" | tail -1)
    case "$v_status" in
      pass) verdict="PASS (exit 0)";;
      timeout) verdict="TIMEOUT after ${timeout}s";;
      infra_error) verdict="INFRA_ERROR (exit $vrc)";;
      *)
        if [ "$vrc" = 124 ]; then verdict="FAIL (exit 124)"
        else verdict="FAIL (exit $vrc)"; fi;;
    esac
    # The JSON reason is the record. An entered command can exit 0 and still
    # be fail/oom/137; the sentence has to say that reason, not the raw exit.
    v_reason=$(python3 -I -B -c 'import json,sys
try:
    body = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
if not isinstance(body, dict):
    raise SystemExit(1)
reason = body.get("reason")
if reason is None:
    print("")
elif isinstance(reason, str):
    print(reason)
else:
    raise SystemExit(1)
' "$VJSON" 2>/dev/null) || v_reason=""
    if [ -n "$v_reason" ]; then
      v_note="recorded reason: $v_reason"
    else
      v_note="the exit code of the command is the verdict"
    fi
    if ! { echo '```'; echo; echo "public: $pub_state"; echo "hidden: $hid_state"; echo "status: $v_status"; echo "verdict: $verdict — $v_note"; echo "reported: ${summary:-no pytest summary line} (quoted from output the tests control; informational)"; } >> "$OUT"; then
      _verify_published=0
      rm -f -- "$VJSON" 2>/dev/null || true
      if [ -n "${A770B_VERIFY_OUT:-}" ]; then
        rm -f -- "$A770B_VERIFY_OUT" 2>/dev/null || true
      fi
      _verify_infra_die "could not finalize verify report: $OUT"
    fi
    reset_worktree "$WT"; trap - EXIT INT TERM
    echo "▶ verify: $verdict (public=$pub_state hidden=$hid_state) · reported: ${summary:-no pytest summary line} · $OUT"
    exit "$vrc" ;;
  *) echo "usage: local-build.sh run [<worktree>] <brief.md> [--spec <spec.json>] [--profile <name>] [--timeout S] [--base-url http://host:port[/v1] --served-model-name NAME [--engine strata]] | verify <label|patch> [<worktree>] [--test \"<cmd>\"] [--timeout S] | reset [<worktree>] | serve <profile> | bench <profile> [--pp <sizes>] [--dry-run] | status | profiles [--name N] | menu | doctor | stop | stop-run | qualify start|status|wait|result|export|stop|list | version | check-update" >&2; exit 2 ;;
esac
fi
