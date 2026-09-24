#!/usr/bin/env bash
# serve_compose.sh — the ONE way the containerized server starts on the builder card (A770B_SERVE=compose).
# The one way the containerized server starts; skills/local-build/scripts/local-build.sh's serve() drives it:
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
[ "$A770B_SERVE" = compose ] || { echo "⛔ serve_compose.sh is for A770B_SERVE=compose (it is '$A770B_SERVE')" >&2; exit 2; }
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
# _build_argv_llama <model-in-container> <ctx> [extra…] — the measured Vulkan flag set, with the container differences
# only: the /models path and the in-container host/port/key path (the envelope mounts the key). The binary is NOT
# an argv word here: it is the container's ENTRYPOINT (/app/llama-server), and compose runs entrypoint + command,
# so a binary in `command` too would exec `/app/llama-server /app/llama-server …`.
_build_argv_llama(){
  local model="$1" ctx="$2"; shift 2
  local -a thinking=()
  local rmode="${THINKING_MODE:-${REASONING:-off}}"
  [ -n "$rmode" ] && thinking+=(--reasoning "$rmode")
  [ -n "${THINKING_EFFORT:-}" ] && thinking+=(--reasoning-effort "$THINKING_EFFORT")
  [ -n "${THINKING_BUDGET:-}" ] && thinking+=(--reasoning-budget "$THINKING_BUDGET")
  # pass the budget message whenever a budget is active (defaulting to the one string A770B_BUDGET_MESSAGE), so the
  # truncation marker llama-server injects is deterministic and bench_model.sh / depth_probe.sh can grep for it
  [ -n "${THINKING_BUDGET:-}" ] && [ -n "${THINKING_BUDGET_MESSAGE:-$A770B_BUDGET_MESSAGE}" ] && thinking+=(--reasoning-budget-message "${THINKING_BUDGET_MESSAGE:-$A770B_BUDGET_MESSAGE}")
  [ "${THINKING_PRESERVE:-}" = "false" ] && thinking+=(--no-reasoning-preserve)
  # --metrics -lv 4: /metrics for the stage-counter diff (C11), and verbosity 4 for the engine-evidence log lines
  # (host buffer sizes, tensor counts, the sampler block, …) — both llama.cpp backends this harness serves
  # (vulkan, sycl), never the vLLM argv (_build_argv_vllm, above, is a separate function)
  ARGV=(-m "$model" --alias "$A770B_ALIAS" --host 0.0.0.0 --port 8080 --api-key-file "$KCONTAINER" \
    -ngl 99 -c "$ctx" -b "$A770B_BATCH" -ub "$A770B_UBATCH" --parallel 1 -fa on --load-mode none -ctk "${KV_K:-q8_0}" -ctv "${KV_V:-q8_0}" \
    --metrics -lv 4 --jinja "${thinking[@]}" --reasoning-format deepseek "$@")
}

# _build_argv_vllm <model-in-container> <ctx> [extra…] — the vLLM flag set.
_build_argv_vllm(){
  local model="$1" ctx="$2"; shift 2
  local quant="${QUANT:-${A770B_QUANT:-}}"
  [ -n "$quant" ] || { echo "⛔ quantization must be set for the vllm backend (e.g. awq, gptq, auto-round) — set QUANT or the profile row's quant field" >&2; exit 2; }
  [ -n "${A770B_VRAM_CAP_GIB:-}" ] || { echo "⛔ A770B_VRAM_CAP_GIB must be set before a vLLM start" >&2; exit 2; }
  [ -n "${A770B_CARD_VRAM_TOTAL:-}" ] || { echo "⛔ A770B_CARD_VRAM_TOTAL must be set before a vLLM start (set A770B_CARD)" >&2; exit 2; }
  python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)" "$A770B_VRAM_CAP_GIB" "$A770B_CARD_VRAM_TOTAL" 2>/dev/null \
    || { echo "⛔ A770B_VRAM_CAP_GIB must be a number no larger than A770B_CARD_VRAM_TOTAL" >&2; exit 2; }
  # the parser is a property of the model's chat template (qwen3_coder for the XML tool format this
  # image's Qwen3.8 checkpoints emit, qwen3_xml, hermes for JSON, …) — never a constant here. opencode
  # sends tool_choice: "auto", which vLLM refuses without both flags, so an unset or malformed parser
  # refuses the start rather than land a bad value in a container argv.
  local tool_parser="${TOOL_PARSER:-}"
  case "$tool_parser" in
    ''|*[!a-z0-9_]*)
      echo "⛔ the vllm backend needs a tool-call parser for the coding agent (set the row's tool_parser, or A770B_CANDIDATE_TOOL_PARSER for a candidate) — read the model's chat template" >&2
      exit 2
      ;;
  esac
  # 0.9 is the engine target. Do not derive it from the llama.cpp cap: that cap is a
  # reserve for the GGUF serve, and shrinking this flag (0.875, 0.84) is a different run.
  local frac=0.9
  ARGV=(
    "$model"
    --served-model-name "$A770B_ALIAS"
    --quantization "$quant"
    --max-model-len "$ctx"
    --gpu-memory-utilization "$frac"
    --host 0.0.0.0
    --port 8000
    --enable-auto-tool-choice
    --tool-call-parser "$tool_parser"
    "$@"
  )
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
    # a live llama-server named by the pidfile that is not this project's container is a host server from
    # before container-only serving; removing the pidfile would orphan it, so keep it and say so
    if pid=$(llama_pid_alive "$PIDFILE"); then
      echo "⛔ the pidfile names a live llama-server (pid $pid) that is not a container of $A770B_COMPOSE_PROJECT — a host server from before container-only serving; it still holds the card. Stop it yourself (kill $pid), then remove $PIDFILE" >&2
      exit 3
    fi
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
if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
  _rel="${MODEL#"${A770B_MODELS%/}"/}"
  MODEL_IN_CONTAINER="/models/$_rel"
else
  MODEL_IN_CONTAINER="/models/$(basename "$MODEL")"
fi

ENTRYPOINT=""
case "${A770B_SERVED_BACKEND:-}" in
  vulkan)
    ENTRYPOINT="/app/llama-server"
    _build_argv_llama "$MODEL_IN_CONTAINER" "$CTX" "$@"
    ;;
  sycl)
    # Leave the envelope entrypoint in place. It sources oneAPI, then execs llama-server.
    # Overriding it with the bare binary drops that runtime on an image that does not bake it.
    ENTRYPOINT=""
    _build_argv_llama "$MODEL_IN_CONTAINER" "$CTX" "$@"
    ;;
  vllm)
    ENTRYPOINT=""
    _build_argv_vllm "$MODEL_IN_CONTAINER" "$CTX" "$@"
    ;;
  "")
    echo "⛔ A770B_SERVED_BACKEND is unset or empty — must be one of: vulkan, sycl, vllm" >&2
    exit 2
    ;;
  *)
    echo "⛔ backend '$A770B_SERVED_BACKEND' is not one this harness serves — must be one of: vulkan, sycl, vllm" >&2
    exit 2
    ;;
esac

if [ "$MODE" = plan ]; then
  echo "entrypoint: $ENTRYPOINT"
  printf 'argv: %q\n' "${ARGV[@]}"
  PLAN_ENVF=""; [ -f "$A770B_COMPOSE_ENV_FILE" ] && PLAN_ENVF="--env-file $A770B_COMPOSE_ENV_FILE "
  echo "compose: $A770B_DOCKER compose ${PLAN_ENVF}-p $A770B_COMPOSE_PROJECT -f $A770B_COMPOSE_FILE -f $OVERRIDE up -d --force-recreate"
  exit 0
fi

# the envelope cannot interpolate without its image/DRM/GID values: the gitignored env file supplies them, or the
# environment does; refuse early and name the fix rather than fail inside `docker compose up`
if [ ! -f "$A770B_COMPOSE_ENV_FILE" ]; then
  _needed_vars=(A770B_LLAMA_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_RENDER_GID A770B_VIDEO_GID)
  if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
    _needed_vars=(A770B_VLLM_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_BY_PATH_DIR A770B_RENDER_GID A770B_VIDEO_GID)
  fi
  for _v in "${_needed_vars[@]}"; do
    [ -n "${!_v:-}" ] || { echo "⛔ A770B_SERVE=compose needs $A770B_COMPOSE_ENV_FILE (or the envelope's image/DRM/GID values in the environment) — copy ${A770B_COMPOSE_ENV_FILE}.example and edit it" >&2; exit 2; }
  done
fi
# the VRAM cap is a per-card measurement with no default: an empty or non-numeric cap must refuse BEFORE the
# container starts, not fail open on the float comparison after it is already up
python3 -c "import sys,math; v=float('$A770B_VRAM_CAP_GIB'); sys.exit(0 if (v>0 and math.isfinite(v)) else 1)" 2>/dev/null \
  || { echo "⛔ A770B_VRAM_CAP_GIB is not a positive finite number ('$A770B_VRAM_CAP_GIB') — set your card's measured VRAM cap in builder.env (raise it only after measuring what else the card holds)" >&2; exit 2; }
# a start is a recreate: take any existing container of this project down first, so the budget gate reads the card
# free (a running container's VRAM is this project's, not a foreign server) and `up --force-recreate` starts from
# the profile just built — a profile switch must not silently keep the old model.
_compose down >/dev/null 2>&1 || true
budget_gate || exit 1
a770b_api_key >/dev/null || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
_ep_arg=()
[ -n "$ENTRYPOINT" ] && _ep_arg=(--entrypoint "$ENTRYPOINT")
python3 "$(dirname "$0")/compose_override.py" "${_ep_arg[@]}" --out "$OVERRIDE" -- "${ARGV[@]}"
# recreate, never --no-recreate: a profile change must replace the container
if ! _compose -f "$OVERRIDE" up -d --force-recreate; then
  echo "⛔ docker compose up failed — the container did not start (docker's error is above); nothing was left running" >&2
  rm -f "$PIDFILE" "$MARK" "$SIDECAR"
  exit 1
fi
if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
  for _ in $(seq 1 150); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" >/dev/null 2>&1 && break; sleep 2; done
else
  for _ in $(seq 1 150); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | grep -q '"ok"' && break; sleep 2; done
fi
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
# llama.cpp stays inside the measured cap. vLLM's target is 0.9 of the card, which on this
# B70 is above that cap; stopping it at the cap would cancel the target.
limit="$A770B_VRAM_CAP_GIB"
limit_why="cap"
if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
  limit=$(python3 -c "import sys; print(f'{0.9 * float(sys.argv[1]):.3f}')" "$A770B_CARD_VRAM_TOTAL")
  limit_why="0.9 of the card"
fi
if python3 -c "import sys; sys.exit(0 if float('$used') > float('$limit') else 1)"; then
  _compose down >/dev/null 2>&1 || true; rm -f "$PIDFILE" "$MARK" "$SIDECAR"
  echo "⛔ VRAM after load ${used} GiB > ${limit_why} ${limit} GiB — container STOPPED; use a smaller context, q4 KV, or, on a card that draws no desktop, A770B_CARD_MODE=inference"
  exit 3
fi
if [ -n "${A770B_LIVE_CARD:-}" ] && [ -n "${A770B_LIVE_BACKEND:-}" ] && [ -n "${A770B_LIVE_MODE:-}" ] && [ -n "${A770B_LIVE_PROFILE:-}" ] && [ -n "$CPID" ]; then
  python3 "$(dirname "$0")/live_backend.py" write --path "$SIDECAR" --card "$A770B_LIVE_CARD" --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" --model "$(basename "$MODEL")" --profile "$A770B_LIVE_PROFILE" --pid "$CPID"
fi
echo "▶ started server container ($A770B_COMPOSE_PROJECT) on $A770B_HOST:$A770B_PORT — model $(basename "$MODEL") ctx $CTX ub $A770B_UBATCH kv ${KV_K:-q8_0}/${KV_V:-q8_0} · mode $A770B_CARD_MODE · cap $A770B_VRAM_CAP_GIB GiB · ${limit_why} ${limit} GiB — host pid $CPID"
echo "✓ VRAM after load: ${used} GiB ≤ ${limit_why} ${limit} GiB"
