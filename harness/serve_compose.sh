#!/usr/bin/env bash
# serve_compose.sh — the ONE way the containerized llama.cpp server starts on the builder card (A770B_SERVE=compose).
# Same interface as serve_a770_llamacpp.sh, so skills/local-build/scripts/local-build.sh's serve() drives either:
#   serve_compose.sh plan  <gguf> <ctx> [extra…]   print the llama-server argv and the compose invocation; no docker
#   serve_compose.sh start <gguf> <ctx> [extra…]   recreate the container, wait for /health, enforce the cap, sidecar
#   serve_compose.sh stop | status
#   serve_compose.sh bench [--dry-run] -- <llama-bench args…>   one-shot llama-bench in the same image (speed rung)
#
# The profile row (model, ctx, kv, extra, thinking) reaches this script through env.sh exactly as it reaches the
# host server; the container ENVELOPE (devices, groups, MESA pin, coopmat, mounts, port, entrypoint) is
# compose/a770-vulkan.yaml. The VRAM cap after load and the live-backend sidecar stay on the HOST: they watch the
# GPU, not the container's view of it. The image's entrypoint is /app/tools.sh, so the override this
# script writes sets entrypoint ["/app/llama-server"]; a bare `docker compose up` must not be a start path.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
[ "$A770B_SERVE" = compose ] || { echo "⛔ serve_compose.sh is for A770B_SERVE=compose (it is '$A770B_SERVE') — use serve_a770_llamacpp.sh" >&2; exit 2; }
PIDFILE="$A770B_DATA/logs/llamacpp-a770.pid"; MARK="$A770B_DATA/logs/llamacpp-a770.model"; SIDECAR="$A770B_DATA/logs/live-backend.json"
OVERRIDE="$A770B_DATA/logs/compose-argv.override.json"
CONTAINER="llama"                      # the compose service name; the project name namespaces it
KCONTAINER="/run/a770b/api.key"        # where the envelope mounts the key, read-only

# _compose <global opts…> <subcommand…> — the one place the compose invocation is assembled (project, envelope,
# optional gitignored env file). The override file is added by the caller with a second -f where it is needed.
_compose(){
  local -a envf=(); [ -f "$A770B_COMPOSE_ENV_FILE" ] && envf=(--env-file "$A770B_COMPOSE_ENV_FILE")
  "$A770B_DOCKER" compose "${envf[@]}" -p "$A770B_COMPOSE_PROJECT" -f "$A770B_COMPOSE_FILE" "$@"
}
# _build_argv <model-in-container> <ctx> [extra…] — the measured Vulkan flag set, mirrored from
# serve_a770_llamacpp.sh:49-51 with the container differences only: the /models path and the in-container
# host/port/key path (the envelope mounts the key). The binary is NOT an argv word here: it is the container's
# ENTRYPOINT (/app/llama-server), and compose runs entrypoint + command, so a binary in `command` too would exec
# `/app/llama-server /app/llama-server …`. The host server's own flags are otherwise unchanged.
_build_argv(){
  local model="$1" ctx="$2"; shift 2
  local -a thinking=()
  local rmode="${THINKING_MODE:-${REASONING:-off}}"
  [ -n "$rmode" ] && thinking+=(--reasoning "$rmode")
  [ -n "${THINKING_EFFORT:-}" ] && thinking+=(--reasoning-effort "$THINKING_EFFORT")
  [ -n "${THINKING_BUDGET:-}" ] && thinking+=(--reasoning-budget "$THINKING_BUDGET")
  [ -n "${THINKING_BUDGET_MESSAGE:-}" ] && thinking+=(--reasoning-budget-message "$THINKING_BUDGET_MESSAGE")
  [ "${THINKING_PRESERVE:-}" = "false" ] && thinking+=(--no-reasoning-preserve)
  ARGV=(-m "$model" --alias "$A770B_ALIAS" --host 0.0.0.0 --port 8080 --api-key-file "$KCONTAINER" \
    -ngl 99 -c "$ctx" -b "$A770B_BATCH" -ub "$A770B_UBATCH" --parallel 1 -fa on --no-mmap -ctk "${KV_K:-q8_0}" -ctv "${KV_V:-q8_0}" \
    --jinja "${thinking[@]}" --reasoning-format deepseek "$@")
}
# _container_host_pid — the host pid of the running compose container (empty when none); the sidecar and the
# pidfile name a real host process so live_backend.read's liveness check keeps working for a container.
_container_host_pid(){
  # always returns 0: the caller reads the (possibly empty) stdout, and a non-zero status here would abort the
  # caller under `set -e` before its own guard could run
  local cid p; cid=$(_compose ps -q "$CONTAINER" 2>/dev/null || true)
  [ -n "$cid" ] || return 0
  p=$("$A770B_DOCKER" inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || true)
  if [ -n "$p" ] && [ "$p" != 0 ]; then printf '%s\n' "$p"; fi   # a dead container reports pid 0, not a live server
  return 0
}

case "${1:-}" in
  stop)
    _compose down >/dev/null 2>&1 || true
    rm -f "$PIDFILE" "$MARK" "$SIDECAR"
    if compose_project_running; then echo "⛔ the container is still running after down — the card is NOT free; check the docker daemon" >&2; exit 3; fi
    echo "stopped (compose project $A770B_COMPOSE_PROJECT)"
    exit 0;;
  status)
    cid=$(_compose ps -q "$CONTAINER" 2>/dev/null || true)
    if [ -n "$cid" ]; then
      echo "running container $cid · $(cat "$MARK" 2>/dev/null)"
      curl -s -H "Authorization: Bearer $(a770b_api_key)" "http://$A770B_HOST:$A770B_PORT/v1/models" | head -c 200; echo
    else
      echo "not running"
    fi
    exit 0;;
  plan|start) ;;
  bench)
    shift
    DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
    [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "usage: serve_compose.sh bench [--dry-run] -- <llama-bench args…>" >&2; exit 2; }
    if [ "$DRY" = 1 ]; then
      DRY_ENVF=""; [ -f "$A770B_COMPOSE_ENV_FILE" ] && DRY_ENVF="--env-file $A770B_COMPOSE_ENV_FILE"
      printf '%s\n' "$A770B_DOCKER compose $DRY_ENVF -p $A770B_COMPOSE_PROJECT -f $A770B_COMPOSE_FILE run --rm --no-deps --entrypoint /app/llama-bench $CONTAINER $*"
      exit 0
    fi
    _compose run --rm --no-deps --entrypoint /app/llama-bench "$CONTAINER" "$@"
    exit 0;;
  *) echo "usage: serve_compose.sh plan|start <gguf> [ctx] [extra…] | stop | status | bench [--dry-run] -- <llama-bench args…>" >&2; exit 2;;
esac
MODE="$1"
MODEL=$(a770b_model_path "${2:?gguf}"); CTX="${3:-32768}"; shift 3 2>/dev/null || shift $#
[ -r "$MODEL" ] || { echo "⛔ model not readable: $MODEL  (A770B_MODELS=$A770B_MODELS)" >&2; exit 2; }
MODEL_IN_CONTAINER="/models/$(basename "$MODEL")"
_build_argv "$MODEL_IN_CONTAINER" "$CTX" "$@"

if [ "$MODE" = plan ]; then
  echo "entrypoint: /app/llama-server"
  printf 'argv: %q\n' "${ARGV[@]}"
  PLAN_ENVF=""; [ -f "$A770B_COMPOSE_ENV_FILE" ] && PLAN_ENVF="--env-file $A770B_COMPOSE_ENV_FILE "
  echo "compose: $A770B_DOCKER compose ${PLAN_ENVF}-p $A770B_COMPOSE_PROJECT -f $A770B_COMPOSE_FILE -f $OVERRIDE up -d --force-recreate"
  exit 0
fi

# the envelope cannot interpolate without its image/DRM/GID values: the gitignored env file supplies them, or the
# environment does; refuse early and name the fix rather than fail inside `docker compose up`
if [ ! -f "$A770B_COMPOSE_ENV_FILE" ]; then
  for _v in A770B_LLAMA_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_RENDER_GID A770B_VIDEO_GID; do
    [ -n "${!_v:-}" ] || { echo "⛔ A770B_SERVE=compose needs $A770B_COMPOSE_ENV_FILE (or the envelope's image/DRM/GID values in the environment) — copy compose/a770-vulkan.env.example and edit it" >&2; exit 2; }
  done
fi
# a start is a recreate: take any existing container of this project down first, so the budget gate reads the card
# free (a running container's VRAM is this project's, not a foreign server) and `up --force-recreate` starts from
# the profile just built — a profile switch must not silently keep the old model.
_compose down >/dev/null 2>&1 || true
budget_gate || exit 1
a770b_api_key >/dev/null || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
python3 "$(dirname "$0")/compose_override.py" --out "$OVERRIDE" -- "${ARGV[@]}"
# recreate, never --no-recreate: a profile change must replace the container
_compose -f "$OVERRIDE" up -d --force-recreate
for _ in $(seq 1 150); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | grep -q '"ok"' && break; sleep 2; done
if ! curl -sf --max-time 3 "http://$A770B_HOST:$A770B_PORT/health" >/dev/null 2>&1; then
  echo "⛔ the container did not answer /health — read: $A770B_DOCKER compose -p $A770B_COMPOSE_PROJECT logs $CONTAINER" >&2
  _compose down >/dev/null 2>&1 || true; rm -f "$PIDFILE" "$MARK" "$SIDECAR"; exit 3
fi
CPID=$(_container_host_pid)
# a healthy container must report a LIVE host pid (not empty, not the 0 a dead container reports): without it the
# sidecar, the menu and the cap would all be blind, so either is a failed start, not a silent success
if [ -z "$CPID" ] || [ "$CPID" = 0 ]; then
  echo "⛔ the container answers /health but reports no live host pid — tearing it down" >&2
  _compose down >/dev/null 2>&1 || true; rm -f "$PIDFILE" "$MARK" "$SIDECAR"; exit 3
fi
printf '%s\n' "$CPID" > "$PIDFILE"
printf '%s\n' "$MODEL" > "$MARK"
used=$(gpu_used_gib)
if python3 -c "import sys; sys.exit(0 if float('$used') > float('$A770B_VRAM_CAP_GIB') else 1)"; then
  _compose down >/dev/null 2>&1 || true; rm -f "$PIDFILE" "$MARK" "$SIDECAR"
  echo "⛔ VRAM after load ${used} GiB > cap $A770B_VRAM_CAP_GIB GiB — container STOPPED; use a smaller context, q4 KV, or, on a card that draws no desktop, A770B_CARD_MODE=inference"
  exit 3
fi
if [ -n "${A770B_LIVE_CARD:-}" ] && [ -n "${A770B_LIVE_BACKEND:-}" ] && [ -n "${A770B_LIVE_MODE:-}" ] && [ -n "${A770B_LIVE_PROFILE:-}" ] && [ -n "$CPID" ]; then
  python3 "$(dirname "$0")/live_backend.py" write --path "$SIDECAR" --card "$A770B_LIVE_CARD" --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" --model "$(basename "$MODEL")" --profile "$A770B_LIVE_PROFILE" --pid "$CPID"
fi
echo "▶ started llama-server container ($A770B_COMPOSE_PROJECT) on $A770B_HOST:$A770B_PORT — model $(basename "$MODEL") ctx $CTX ub $A770B_UBATCH kv ${KV_K:-q8_0}/${KV_V:-q8_0} · mode $A770B_CARD_MODE · cap $A770B_VRAM_CAP_GIB GiB — host pid $CPID"
echo "✓ VRAM after load: ${used} GiB ≤ cap $A770B_VRAM_CAP_GIB GiB"
