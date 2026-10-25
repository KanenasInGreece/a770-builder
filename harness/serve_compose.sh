#!/usr/bin/env bash
# serve_compose.sh — the ONE way the containerized server starts on the builder card (A770B_SERVE=compose).
# The one way the containerized server starts; skills/local-build/scripts/local-build.sh's serve() drives it:
#   serve_compose.sh plan  <gguf> <ctx> [extra…]   print the llama-server argv and the compose invocation; no docker
#   serve_compose.sh signature <gguf> <ctx> [extra…]  print the context and the argv signature; no docker, no env file, no health wait
#   serve_compose.sh start <gguf> <ctx> [extra…]   recreate the container, wait for /health, enforce the cap, sidecar
#   serve_compose.sh stop | status | health
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
# optional gitignored env file, and the argv override once that file exists). A container started with both
# files is invisible to a yaml-only ps or down.
_compose(){
  local -a envf=() files=(-f "$A770B_COMPOSE_FILE")
  [ -f "$A770B_COMPOSE_ENV_FILE" ] && envf=(--env-file "$A770B_COMPOSE_ENV_FILE")
  [ -f "$OVERRIDE" ] && files+=(-f "$OVERRIDE")
  "$A770B_DOCKER" compose "${envf[@]}" -p "$A770B_COMPOSE_PROJECT" "${files[@]}" "$@"
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
  # A non-empty THINKING_MODE wins. Empty or unset falls through to
  # REASONING. Both unset is off. Both set and empty is refused, as is
  # auto or any other word.
  local rmode="${THINKING_MODE:-${REASONING-off}}"
  local -a reasoning_argv=()
  case "$rmode" in
    off)
      reasoning_argv+=(--default-chat-template-kwargs '{"enable_thinking": false}')
      ;;
    on)
      local reasoning_parser="${REASONING_PARSER:-}"
      if [ "$reasoning_parser" = "auto" ]; then
        echo "⛔ vLLM refuses --reasoning-parser auto: the log line that names a parser does not turn it on" >&2
        exit 2
      fi
      case "$reasoning_parser" in
        ''|*[!a-z0-9_]*)
          echo "⛔ vLLM needs --reasoning-parser set to a parser matching ^[a-z0-9_]+$ (empty and auto are refused)" >&2
          exit 2
          ;;
      esac
      reasoning_argv+=(--reasoning-parser "$reasoning_parser")
      ;;
    *)
      echo "⛔ vLLM refuses reasoning switch '${rmode}': it must be on or off (auto is refused)" >&2
      exit 2
      ;;
  esac
  # 0.9 is the engine target. Do not derive it from the llama.cpp cap: that cap is a
  # reserve for the GGUF serve, and shrinking this flag (0.875, 0.84) is a different run.
  # These variables are applied only on the serve start. A client field with no
  # engine gate would also be sent on llama.cpp rows. The client budget waits
  # for a later cycle that knows the engine at the post. The pinned image has
  # no server-wide token cap.
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
    "${reasoning_argv[@]}"
    "$@"
  )
}

# _refuse_sglang_parser <flag> <value> — empty, malformed, or the word auto.
# auto matches ^[a-z0-9_]+$, so the pattern alone would let it through. The
# log line that names a parser does not turn that parser on.
_refuse_sglang_parser(){
  local flag="$1" val="$2"
  if [ "$val" = "auto" ]; then
    echo "⛔ sglang refuses --${flag} auto: the log line that names a parser does not turn it on" >&2
    exit 2
  fi
  case "$val" in
    ''|*[!a-z0-9_]*)
      echo "⛔ sglang needs --${flag} set to a parser matching ^[a-z0-9_]+$ (empty and auto are refused)" >&2
      exit 2
      ;;
  esac
}

# _build_argv_sglang <model-in-container> <ctx> [extra…] — launch_server argv.
# The binary is the entrypoint (python3). extra carries the trial flags that
# are not the profile's model, context, name, quant, or parsers.
_build_argv_sglang(){
  local model="$1" ctx="$2"; shift 2
  local quant="${QUANT:-${A770B_QUANT:-}}"
  local tool_parser="${TOOL_PARSER:-}"
  local reasoning_parser="${REASONING_PARSER:-}"
  [ -n "$quant" ] || { echo "⛔ quantization must be set for the sglang backend (set QUANT or the profile row's quant field)" >&2; exit 2; }
  _refuse_sglang_parser tool-call-parser "$tool_parser"
  _refuse_sglang_parser reasoning-parser "$reasoning_parser"
  ARGV=(
    -m sglang.launch_server
    --model-path "$model"
    --quantization "$quant"
    --served-model-name "$A770B_ALIAS"
    --context-length "$ctx"
    --port "$A770B_PORT"
    --tool-call-parser "$tool_parser"
    --reasoning-parser "$reasoning_parser"
    "$@"
  )
}

# _value_for <NAME> — an exported value wins; otherwise the gitignored env file,
# which is the copy compose interpolates. Host paths stay in that file.
_value_for(){
  local key="$1" line val
  val="${!key:-}"
  if [ -n "$val" ]; then printf '%s\n' "$val"; return 0; fi
  [ -f "$A770B_COMPOSE_ENV_FILE" ] || return 0
  line=$(grep -E "^[[:space:]]*${key}=" "$A770B_COMPOSE_ENV_FILE" | tail -n 1 || true)
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
  printf '%s\n' "$val"
}

# Published images that still trust emSign ECC Root CA - G3. The value is the
# one compose will interpolate: an export wins, then the gitignored env file.
_refuse_old_trust() {
  case "$1" in
    *33ed2a8936e94ac893a50eed3d2958141d352198233d107e58c1c7ee3b96a9b4*|\
    *7b4905e41d2720bfccf390e78e938e8148329b3da9467d3f1b008f0efc44cd97*|\
    *52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b*|\
    *24c872759256d4b0d3b9d0419ebdf6f3547e0fdbd3891fbb9f90e32ad255c94f*|\
    *b763b3587a8fd40f7575d8a12b38071cc2ed8a88086925e6936da7727723412f*)
      echo "⛔ $1 still trusts the compromised emSign ECC Root CA - G3 root. Rebuild it with compose/refresh-ca.Dockerfile and pin that image." >&2
      exit 2
      ;;
  esac
}

# The measured row keeps the name exl3xpu:b70-15ded2f. The bytes behind that
# name are local, so a digest list cannot see them. Scan before start.
_scan_named_vllm_image() {
  case "$1" in
    exl3xpu:b70-15ded2f|exl3xpu:b70-15ded2f-ca) ;;
    *) return 0 ;;
  esac
  local found
  if ! found=$("$A770B_DOCKER" run --rm --network=none --entrypoint bash "$1" -c '
    set +e
    err=0
    for dir in /opt /usr /root /etc /app /workspace /home /srv /var /usr/local /tmp; do
      [ -d "$dir" ] || continue
      find "$dir" -type f \( -name cacert.pem -o -name ca-certificates.crt -o -name uv -o -name uvx \) > /tmp/ca-scan-list
      if [ "$?" -ne 0 ]; then err=1; fi
      while read -r f; do
        [ -n "$f" ] || continue
        grep -a -q "emSign ECC Root CA - G3" "$f"
        grc=$?
        if [ "$grc" -eq 0 ]; then printf "%s\n" "$f"
        elif [ "$grc" -ne 1 ]; then err=1; fi
      done < /tmp/ca-scan-list
    done
    rm -f /tmp/ca-scan-list
    [ "$err" -eq 0 ]
  '); then
    echo "⛔ could not scan $1 for the compromised emSign ECC Root CA - G3 root" >&2
    exit 2
  fi
  if [ -n "$found" ]; then
    echo "⛔ $1 still trusts the compromised emSign ECC Root CA - G3 root ($found). Rebuild it with compose/refresh-ca.Dockerfile and keep this name." >&2
    exit 2
  fi
}

_trust_gate() {
  _refuse_old_trust "$(_value_for A770B_LLAMA_IMAGE)"
  _refuse_old_trust "$(_value_for A770B_VLLM_IMAGE)"
  _refuse_old_trust "$(_value_for A770B_SGLANG_IMAGE)"
  _scan_named_vllm_image "$(_value_for A770B_VLLM_IMAGE)"
}

# _sglang_preflight — before any docker call. Victim write-back stays on, and
# the by-path node must be the container target (no remap onto card0).
_sglang_preflight(){
  local novict victim card render card_node render_node card_target render_target
  novict="${NOVICT:-0}"
  victim=$(_value_for EXL3_NVTIER_NO_VICTIM)
  if [ "$novict" = 1 ] || [ "$victim" = 1 ]; then
    echo "⛔ refuse: victim write-back stays on" >&2
    exit 2
  fi
  card=$(_value_for A770B_DRM_CARD)
  render=$(_value_for A770B_DRM_RENDER)
  card_node=$(_value_for A770B_SGLANG_CARD_NODE)
  render_node=$(_value_for A770B_SGLANG_RENDER_NODE)
  card_target=$(readlink -f "$card" 2>/dev/null || true)
  render_target=$(readlink -f "$render" 2>/dev/null || true)
  if [ -z "$card_node" ] || [ -z "$render_node" ] || [ "$card_target" != "$card_node" ] || [ "$render_target" != "$render_node" ]; then
    echo "⛔ refuse: by-path nodes are ${render_target:-missing} ${card_target:-missing}, not ${render_node:-unset} ${card_node:-unset}" >&2
    exit 2
  fi
  [ -n "${A770B_CARD_VRAM_TOTAL:-}" ] || { echo "⛔ A770B_CARD_VRAM_TOTAL must be set before an sglang start (set A770B_CARD)" >&2; exit 2; }
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
# _serve_log_ids — this checkout's compose ps plus any other project llama container.
# This checkout's exited container is a log to keep. Another worktree's exited container is not:
# the second loop skips an id _pid_if_running does not print.
# No id means there is no previous container. The caller stays quiet in that case.
_serve_log_ids(){
  local id seen=" "
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    seen="$seen$id "
    printf '%s\n' "$id"
  done < <(_compose ps --all -q "$CONTAINER" 2>/dev/null || true)
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    [ -n "$(_pid_if_running "$id" || true)" ] || continue
    case "$seen" in *" $id "*) continue ;; esac
    printf '%s\n' "$id"
  done < <(project_llama_ids)
}

# _keep_serve_log — compose down deletes the container log, so copy it to a
# temp file in this directory and move that file onto the durable log only
# after docker logs succeeds. The first id that copies is the durable file.
# A symlink is removed first; mv -T refuses a directory at the destination.
# No previous container is not a failed log of the server about to start.
_keep_serve_log(){
  local dest="$A770B_DATA/logs/llamacpp-a770.log" cid tmp
  local -a ids=()
  while IFS= read -r cid; do
    [ -n "$cid" ] || continue
    ids+=("$cid")
  done < <(_serve_log_ids)
  [ ${#ids[@]} -gt 0 ] || return 0
  # A failed mkdir must not abort the caller: teardown still has to run.
  mkdir -p "$A770B_DATA/logs" || {
    echo "⚠ the previous container's log could not be kept (could not create the log directory)" >&2
    return 0
  }
  for cid in "${ids[@]}"; do
    # mktemp creates the file itself. A fallback name would let `>` follow a
    # symlink planted at that predictable path.
    tmp=$(mktemp "$dest.tmp.XXXXXX") || {
      echo "⚠ the previous container's log could not be kept (could not create a temporary file)" >&2
      return 0
    }
    if "$A770B_DOCKER" logs "$cid" >"$tmp" 2>&1; then
      if [ -L "$dest" ]; then rm -f "$dest"; fi
      if mv -fT "$tmp" "$dest"; then
        echo "serve log kept: $dest" >&2
        return 0
      fi
    fi
    rm -f "$tmp"
  done
  echo "⚠ the previous container's log could not be kept" >&2
  return 0
}

# _down_other_worktree_llama — a container in this compose project whose config_files label is another
# worktree. compose down filtered by our files leaves it running. Take it down with its own files.
# Never another compose project, and never the four framework container names (project_llama_ids).
_down_other_worktree_llama(){
  local id files ours part
  local -a parts=() fargs=()
  ours="$A770B_COMPOSE_FILE"
  [ -f "$OVERRIDE" ] && ours="$ours,$OVERRIDE"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    # An exited container has no host pid. A missing compose file on that id must not refuse stop.
    [ -n "$(_pid_if_running "$id" || true)" ] || continue
    files=$("$A770B_DOCKER" inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$id" 2>/dev/null || true)
    [ -n "$files" ] || continue
    [ "$files" = "$ours" ] && continue
    fargs=()
    IFS=',' read -r -a parts <<< "$files"
    for part in "${parts[@]}"; do
      if [ ! -f "$part" ]; then
        echo "⛔ compose down failed — a project container's compose file is not on disk" >&2
        return 3
      fi
      fargs+=(-f "$part")
    done
    if [ ${#fargs[@]} -eq 0 ]; then
      echo "⛔ compose down failed — a project container has no compose file" >&2
      return 3
    fi
    if ! "$A770B_DOCKER" compose -p "$A770B_COMPOSE_PROJECT" "${fargs[@]}" down; then
      echo "⛔ compose down failed — the card is NOT free" >&2
      return 3
    fi
  done < <(project_llama_ids)
  return 0
}
# _fail_started <exit> <message> — a container that came up and then failed.
# Keep its log, then tear it down.
_fail_started(){
  local code="$1"; shift
  _keep_serve_log
  _compose down >/dev/null 2>&1 || true
  rm -f "$PIDFILE" "$MARK" "$SIDECAR"
  printf '%s\n' "$*" >&2
  exit "$code"
}

# _argv_signature <entrypoint> [argv…] — sha256 hex of the entrypoint string and the argv words.
# Empty entrypoint is an empty first field. One function for the mark and for `signature`.
_argv_signature(){
  local ep="$1"; shift
  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from compose_override import argv_signature
print(argv_signature(sys.argv[2], sys.argv[3:]))
' "$(dirname "$0")" "$ep" "$@"
}

# _health_class — one word on stdout: down, loading, or ready. Exit 0 only for ready.
# vllm and sglang: HTTP success is ready, and the body is not read. Any other backend
# is ready only when that success also contains "ok"; success without it is loading.
# A failed request is down.
_health_class(){
  local body="" rc=0
  case "${A770B_SERVED_BACKEND:-}" in
    vllm|sglang)
      if curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" >/dev/null 2>&1; then
        printf '%s\n' ready
        return 0
      fi
      printf '%s\n' down
      return 1
      ;;
    *)
      body=$(curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null) || rc=$?
      if [ "$rc" -ne 0 ]; then
        printf '%s\n' down
        return 1
      fi
      if printf '%s\n' "$body" | grep -q '"ok"'; then
        printf '%s\n' ready
        return 0
      fi
      printf '%s\n' loading
      return 1
      ;;
  esac
}

case "${1:-}" in
  stop)
    # compose interpolates the sglang volume on ps and on down. An empty host side is `: /models/…:ro`.
    if [ -z "${A770B_SGLANG_MODEL_DIR:-}" ]; then
      export A770B_SGLANG_MODEL_DIR=.
    fi
    _keep_serve_log
    # a compose error is not a stop: the pidfile and the sidecar stay while the container may still be up
    if ! _compose down >/dev/null; then
      echo "⛔ compose down failed — the card is NOT free" >&2
      exit 3
    fi
    if ! _down_other_worktree_llama; then exit 3; fi
    # a live llama-server named by the pidfile that is not this project's container is a host server from
    # before container-only serving; removing the pidfile would orphan it, so keep it and say so
    if pid=$(llama_pid_alive "$PIDFILE"); then
      echo "⛔ the pidfile names a live llama-server (pid $pid) that is not a container of $A770B_COMPOSE_PROJECT — a host server from before container-only serving; it still holds the card. Stop it yourself (kill $pid), then remove $PIDFILE" >&2
      exit 3
    fi
    if compose_project_running; then echo "⛔ the container is still running after down — the card is NOT free; check the docker daemon" >&2; exit 3; fi
    rm -f "$PIDFILE" "$MARK" "$SIDECAR"
    echo "stopped (compose project $A770B_COMPOSE_PROJECT)"
    exit 0;;
  status)
    cid=$(_compose ps -q "$CONTAINER" 2>/dev/null || true)
    if [ -n "$cid" ]; then
      echo "running container $cid · $(head -n 1 "$MARK" 2>/dev/null)"
      a770b_curl -s "http://$A770B_HOST:$A770B_PORT/v1/models" | head -c 200; echo
    else
      echo "not running"
    fi
    exit 0;;
  health)
    _health_class && exit 0
    exit 1;;
  plan|start|signature) ;;
  bench)
    shift
    DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
    [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "usage: serve_compose.sh bench [--dry-run] -- <llama-bench args…>" >&2; exit 2; }
    _trust_gate
    if [ "$DRY" = 1 ]; then
      DRY_ENVF=""; [ -f "$A770B_COMPOSE_ENV_FILE" ] && DRY_ENVF="--env-file $A770B_COMPOSE_ENV_FILE"
      printf '%s\n' "$A770B_DOCKER compose $DRY_ENVF -p $A770B_COMPOSE_PROJECT -f $A770B_COMPOSE_FILE run --rm --no-deps --entrypoint /app/llama-bench $CONTAINER $*"
      exit 0
    fi
    _compose run --rm --no-deps --entrypoint /app/llama-bench "$CONTAINER" "$@"
    exit 0;;
  *) echo "usage: serve_compose.sh plan|start|signature <gguf> [ctx] [extra…] | stop | status | health | bench [--dry-run] -- <llama-bench args…>" >&2; exit 2;;
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
  sglang)
    # python3 is the entrypoint alone. The module stays in command so
    # compose_override does not run the interpreter twice.
    ENTRYPOINT="python3"
    _build_argv_sglang "$MODEL_IN_CONTAINER" "$CTX" "$@"
    ;;
  "")
    echo "⛔ A770B_SERVED_BACKEND is unset or empty — must be one of: vulkan, sycl, vllm, sglang" >&2
    exit 2
    ;;
  *)
    echo "⛔ backend '$A770B_SERVED_BACKEND' is not one this harness serves — must be one of: vulkan, sycl, vllm, sglang" >&2
    exit 2
    ;;
esac

# signature is the context and the hash of the argv this start would build. No docker, no env file, no health wait.
if [ "$MODE" = signature ]; then
  printf '%s\n' "$CTX"
  _argv_signature "$ENTRYPOINT" "${ARGV[@]}"
  exit 0
fi

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
  elif [ "${A770B_SERVED_BACKEND:-}" = "sglang" ]; then
    _needed_vars=(A770B_SGLANG_IMAGE A770B_DRM_CARD A770B_DRM_RENDER A770B_SGLANG_CARD_NODE A770B_SGLANG_RENDER_NODE A770B_RENDER_GID A770B_VIDEO_GID)
  fi
  for _v in "${_needed_vars[@]}"; do
    [ -n "${!_v:-}" ] || { echo "⛔ A770B_SERVE=compose needs $A770B_COMPOSE_ENV_FILE (or the envelope's image/DRM/GID values in the environment) — copy ${A770B_COMPOSE_ENV_FILE}.example and edit it" >&2; exit 2; }
  done
fi
# the VRAM cap is a per-card measurement with no default: an empty or non-numeric cap must refuse BEFORE the
# container starts, not fail open on the float comparison after it is already up
python3 -c "import sys,math; v=float('$A770B_VRAM_CAP_GIB'); sys.exit(0 if (v>0 and math.isfinite(v)) else 1)" 2>/dev/null \
  || { echo "⛔ A770B_VRAM_CAP_GIB is not a positive finite number ('$A770B_VRAM_CAP_GIB') — set your card's measured VRAM cap in builder.env (raise it only after measuring what else the card holds)" >&2; exit 2; }
# sglang checks run before any compose call. A later `down` is already docker.
if [ "${A770B_SERVED_BACKEND:-}" = "sglang" ]; then
  _sglang_preflight
  # The profile model is the bind. EXL3_MODEL_PATH is that same in-container path.
  A770B_SGLANG_MODEL_NAME="$(basename "$MODEL")"
  A770B_SGLANG_MODEL_DIR="$MODEL"
  EXL3_MODEL_PATH="/models/${A770B_SGLANG_MODEL_NAME}"
  export A770B_SGLANG_MODEL_NAME A770B_SGLANG_MODEL_DIR EXL3_MODEL_PATH
fi
# a start is a recreate: take any existing container of this project down first, so the budget gate reads the card
# free (a running container's VRAM is this project's, not a foreign server) and `up --force-recreate` starts from
# the profile just built — a profile switch must not silently keep the old model.
_keep_serve_log
_compose down >/dev/null 2>&1 || true
# Another worktree's llama container is this project, so the stray-server gate counts it as ours.
# Take it down before that gate, or start can leave it running beside the new server.
if ! _down_other_worktree_llama; then exit 3; fi
budget_gate || exit 1
a770b_api_key >/dev/null || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
_ep_arg=()
[ -n "$ENTRYPOINT" ] && _ep_arg=(--entrypoint "$ENTRYPOINT")
python3 "$(dirname "$0")/compose_override.py" "${_ep_arg[@]}" --out "$OVERRIDE" -- "${ARGV[@]}"
# recreate, never --no-recreate: a profile change must replace the container
_trust_gate
if ! _compose up -d --force-recreate; then
  echo "⛔ docker compose up failed — the container did not start (docker's error is above); nothing was left running" >&2
  rm -f "$PIDFILE" "$MARK" "$SIDECAR"
  exit 1
fi
# 150 tries, 2s apart, is the wait a load is given. A test may shorten it; an unset value does not.
# The cap is the try count, not timeout_s. A class is printed only when it changes.
_health_tries="${A770B_HEALTH_TRIES:-150}"
_health_pause="${A770B_HEALTH_PAUSE:-2}"
_health_prev=""
_health_now=""
for _ in $(seq 1 "$_health_tries"); do
  _health_now=$(_health_class) || true
  if [ "$_health_now" != "$_health_prev" ]; then
    printf 'health: %s\n' "$_health_now" >&2
    _health_prev="$_health_now"
  fi
  [ "$_health_now" = ready ] && break
  # The try cap is only for a container that is still up. An id absent from
  # the running list ends the wait.
  if _health_cid=$(_compose ps -q "$CONTAINER" 2>/dev/null); then
    [ -n "$_health_cid" ] || break
  fi
  sleep "$_health_pause"
done
_health_after=$(_health_class) || true
if [ "$_health_now" != ready ] || [ "$_health_after" != ready ]; then
  _fail_started 3 "⛔ the container did not answer /health"
fi
CPID=$(_container_host_pid)
# a healthy container must report a LIVE host pid (not empty, not the 0 a dead container reports): without it the
# sidecar, the menu and the cap would all be blind, so either is a failed start, not a silent success
if [ -z "$CPID" ] || [ "$CPID" = 0 ]; then
  _fail_started 3 "⛔ the container answers /health but reports no live host pid — tearing it down"
fi
printf '%s\n' "$CPID" > "$PIDFILE"
{
  printf '%s\n' "$MODEL"
  printf '%s\n' "$CTX"
  _argv_signature "$ENTRYPOINT" "${ARGV[@]}"
} > "$MARK"
used=$(gpu_used_gib)
# llama.cpp stops at the mode cap. vLLM stops at 0.9 of the card; sglang stops at the card total.
limit="$A770B_VRAM_CAP_GIB"
limit_why="cap"
if [ "${A770B_SERVED_BACKEND:-}" = "vllm" ]; then
  limit=$(python3 -c "import sys; print(f'{0.9 * float(sys.argv[1]):.3f}')" "$A770B_CARD_VRAM_TOTAL")
  limit_why="0.9 of the card"
elif [ "${A770B_SERVED_BACKEND:-}" = "sglang" ]; then
  limit="$A770B_CARD_VRAM_TOTAL"
  limit_why="the card total"
fi
if python3 -c "import sys; sys.exit(0 if float('$used') > float('$limit') else 1)"; then
  _fail_started 3 "⛔ VRAM after load ${used} GiB > ${limit_why} ${limit} GiB — container STOPPED; use a smaller context, q4 KV, or, on a card that draws no desktop, A770B_CARD_MODE=inference"
fi
if [ -n "${A770B_LIVE_CARD:-}" ] && [ -n "${A770B_LIVE_BACKEND:-}" ] && [ -n "${A770B_LIVE_MODE:-}" ] && [ -n "${A770B_LIVE_PROFILE:-}" ] && [ -n "$CPID" ]; then
  python3 "$(dirname "$0")/live_backend.py" write --path "$SIDECAR" --card "$A770B_LIVE_CARD" --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" --model "$(basename "$MODEL")" --profile "$A770B_LIVE_PROFILE" --pid "$CPID"
fi
limit_note=""
if [ "$limit_why" != cap ] || [ "$limit" != "$A770B_VRAM_CAP_GIB" ]; then
  limit_note=" · ${limit_why} ${limit} GiB"
fi
echo "▶ started server container ($A770B_COMPOSE_PROJECT) on $A770B_HOST:$A770B_PORT — model $(basename "$MODEL") ctx $CTX ub $A770B_UBATCH kv ${KV_K:-q8_0}/${KV_V:-q8_0} · mode $A770B_CARD_MODE · cap $A770B_VRAM_CAP_GIB GiB${limit_note} — host pid $CPID"
echo "✓ VRAM after load: ${used} GiB ≤ ${limit_why} ${limit} GiB"
