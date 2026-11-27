#!/usr/bin/env bash
# serve_compose.sh — the ONE way the containerized server starts on the builder card (A770B_SERVE=compose).
# The one way the containerized server starts; skills/local-build/scripts/local-build.sh's serve() drives it:
#   serve_compose.sh plan  <gguf> <ctx> [extra…]   print the llama-server argv and the compose invocation; no docker
#   serve_compose.sh signature <gguf> <ctx> [extra…]  print the context and the argv signature; no docker, no env file, no health wait
#   serve_compose.sh start <gguf> <ctx> [extra…]   recreate the container, wait for /health, enforce the cap, sidecar
#   serve_compose.sh stop | stop-id <full-container-id> | status | health
#   serve_compose.sh gate                                   the trust check start and bench run; no server
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
CIDFILE="$A770B_DATA/logs/llamacpp-a770.cid"
INVFILE="$A770B_DATA/logs/llamacpp-a770.invocation"
EVPATHFILE="$A770B_DATA/logs/serve-evidence.path"
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
# _apply_contract <engine> [extra…] — refuse a forbidden extra, then print the applied flags, one per line.
# The words come from profiling_contract.py. A duplicate of an applied flag is refused there too.
_apply_contract(){
  local harness engine
  harness=$(dirname "$0")
  engine="$1"
  shift
  python3 - "$harness" "$engine" "$@" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import profiling_contract
message = profiling_contract.refusal(sys.argv[2], sys.argv[3:])
if message:
    print("⛔ " + message, file=sys.stderr)
    raise SystemExit(2)
words = profiling_contract.flags(sys.argv[2])
if words:
    sys.stdout.write("\n".join(words) + "\n")
PY
}

# _write_profiling_receipt <engine> — after a healthy start, record which switches took effect.
# The receipt is merged into the serve evidence later, because that file does not exist yet.
# The log goes to a file: a heredoc already owns this python's stdin.
_write_profiling_receipt(){
  local harness engine cid logfile rc receipt
  harness=$(dirname "$0")
  engine="$1"
  cid="${CREATED_CID:-}"
  mkdir -p "$A770B_DATA/logs"
  logfile="$A770B_DATA/logs/profiling-start.log"
  receipt="$A770B_DATA/logs/profiling.json"
  # `: >` follows a symlink. Leave a planted link in place and do not remove it.
  if [ -L "$logfile" ] || [ -L "$receipt" ]; then
    echo "⛔ profiling receipt path is a symlink" >&2
    return 2
  fi
  : >"$logfile"
  if [ -n "$cid" ]; then
    "$A770B_DOCKER" logs "$cid" >"$logfile" 2>&1 || : >"$logfile"
  fi
  set +e
  python3 - "$harness" "$engine" "$A770B_HOST" "$A770B_PORT" \
      "${A770B_API_KEY_FILE:-}" "$A770B_DATA/logs/profiling.json" "$logfile" 0.4 <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import profiling_contract
engine, host, port, key_file, out, log_path, timeout_s = sys.argv[2:9]
try:
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        log_text = handle.read()
except OSError:
    log_text = ""
try:
    timeout = float(timeout_s)
except ValueError:
    timeout = 0.4
confirmed = profiling_contract.confirm_started(
    engine, host, port, log_text, key_file or None, timeout=timeout,
)
profiling_contract.write_receipt(
    out, profiling_contract.receipt(engine, confirmed),
)
PY
  rc=$?
  set -e
  rm -f -- "$logfile"
  return "$rc"
}

# _build_argv_llama <model-in-container> <ctx> [extra…] — the measured Vulkan flag set, with the container differences
# only: the /models path and the in-container host/port/key path (the envelope mounts the key). The binary is NOT
# an argv word here: it is the container's ENTRYPOINT (/app/llama-server), and compose runs entrypoint + command,
# so a binary in `command` too would exec `/app/llama-server /app/llama-server …`.
_build_argv_llama(){
  local model="$1" ctx="$2"; shift 2
  local -a thinking=() meas=()
  local meas_text
  meas_text=$(_apply_contract llama.cpp "$@")
  if [ -n "$meas_text" ]; then
    mapfile -t meas <<< "$meas_text"
  fi
  local rmode="${THINKING_MODE:-${REASONING:-off}}"
  [ -n "$rmode" ] && thinking+=(--reasoning "$rmode")
  [ -n "${THINKING_EFFORT:-}" ] && thinking+=(--reasoning-effort "$THINKING_EFFORT")
  [ -n "${THINKING_BUDGET:-}" ] && thinking+=(--reasoning-budget "$THINKING_BUDGET")
  # pass the budget message whenever a budget is active (defaulting to the one string A770B_BUDGET_MESSAGE), so the
  # truncation marker llama-server injects is deterministic and bench_model.sh / depth_probe.sh can grep for it
  [ -n "${THINKING_BUDGET:-}" ] && [ -n "${THINKING_BUDGET_MESSAGE:-$A770B_BUDGET_MESSAGE}" ] && thinking+=(--reasoning-budget-message "${THINKING_BUDGET_MESSAGE:-$A770B_BUDGET_MESSAGE}")
  [ "${THINKING_PRESERVE:-}" = "false" ] && thinking+=(--no-reasoning-preserve)
  # Measurement switches come from the profiling contract, before the caller's extra words.
  # Both llama.cpp backends this harness serves (vulkan, sycl) take that list.
  ARGV=(-m "$model" --alias "$A770B_ALIAS" --host 0.0.0.0 --port 8080 --api-key-file "$KCONTAINER" \
    -ngl 99 -c "$ctx" -b "$A770B_BATCH" -ub "$A770B_UBATCH" --parallel 1 -fa on --load-mode none -ctk "${KV_K:-q8_0}" -ctv "${KV_V:-q8_0}" \
    "${meas[@]}" --jinja "${thinking[@]}" --reasoning-format deepseek "$@")
}

# _build_argv_vllm <model-in-container> <ctx> [extra…] — the vLLM flag set.
_build_argv_vllm(){
  local model="$1" ctx="$2"; shift 2
  local -a meas=()
  local meas_text
  meas_text=$(_apply_contract vllm "$@")
  if [ -n "$meas_text" ]; then
    mapfile -t meas <<< "$meas_text"
  fi
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
    "${meas[@]}"
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
  local -a meas=()
  local meas_text
  meas_text=$(_apply_contract sglang "$@")
  if [ -n "$meas_text" ]; then
    mapfile -t meas <<< "$meas_text"
  fi
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
    "${meas[@]}"
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

# Scan the id serve will start. The measured name is not the only tag that
# can point at old bytes, so the name is not what decides whether to scan.
_scan_image() {
  local id="$1" shown="$2" found check
  check=$(CDPATH= cd -- "$(dirname "$0")/../compose" && pwd)/ca-trust-check.sh
  if [ ! -f "$check" ]; then
    echo "⛔ the trust check script is missing" >&2
    exit 2
  fi
  if ! found=$("$A770B_DOCKER" run --rm --network=none --pull=never \
      -v "$check:/tmp/ca-trust-check.sh:ro,z" --entrypoint bash "$id" -c '
    set +e
    err=0
    . /tmp/ca-trust-check.sh || err=1
    if find /etc/ssl/certs /usr/share/ca-certificates -name "emSign_ECC_Root_CA_-_G3*" 2>/dev/null | grep -q .; then
      echo "emSign_ECC_Root_CA_-_G3"
    fi
    if find /etc/ssl/certs -type l -lname "*emSign_ECC_Root_CA_-_G3*" 2>/dev/null | grep -q .; then
      echo "emSign_ECC_Root_CA_-_G3-link"
    fi
    if [ -f /etc/ca-certificates.conf ] && grep -q "emSign_ECC_Root_CA_-_G3" /etc/ca-certificates.conf; then
      echo "/etc/ca-certificates.conf"
    fi
    if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
      bundle_has_root /etc/ssl/certs/ca-certificates.crt
      rc=$?
      if [ "$rc" -eq 0 ]; then echo "/etc/ssl/certs/ca-certificates.crt"
      elif [ "$rc" -eq 2 ]; then err=1
      fi
    fi
    for dir in /*; do
      base=$(basename "$dir")
      case "$base" in proc|sys|dev|run) continue;; esac
      [ -d "$dir" ] || continue
      : > /tmp/ca-scan-list
      : > /tmp/ca-scan-embedded
      ca_append_trust_files /tmp/ca-scan-list "$dir" || err=1
      ca_append_embedded_files /tmp/ca-scan-embedded "$dir" || err=1
      while read -r f; do
        [ -n "$f" ] || continue
        bundle_has_root "$f"
        rc=$?
        if [ "$rc" -eq 0 ]; then printf "%s\n" "$f"
        elif [ "$rc" -eq 2 ]; then err=1
        fi
      done < /tmp/ca-scan-list
      while read -r f; do
        [ -n "$f" ] || continue
        ca_embedded_refused "$f"
        rc=$?
        if [ "$rc" -eq 0 ]; then printf "%s\n" "$f"
        elif [ "$rc" -eq 2 ]; then err=1
        fi
      done < /tmp/ca-scan-embedded
    done
    rm -f /tmp/ca-scan-list /tmp/ca-scan-embedded
    [ "$err" -eq 0 ]
  '); then
    echo "⛔ could not scan $shown for the compromised emSign ECC Root CA - G3 root" >&2
    exit 2
  fi
  if [ -n "$found" ]; then
    echo "⛔ $shown still trusts the compromised emSign ECC Root CA - G3 root ($found). Rebuild it with compose/refresh-ca.Dockerfile and keep this name." >&2
    exit 2
  fi
}

# One id for the scan and for compose. An empty inspect is a refusal: a real
# docker prints an id or fails, and a blank success must not keep the tag.
_image_id() {
  local name="$1" id
  if ! id=$("$A770B_DOCKER" image inspect -f '{{.Id}}' "$name" 2>/dev/null); then
    echo "⛔ could not resolve $name to an image id" >&2
    return 1
  fi
  case "$id" in
    sha256:*) printf '%s\n' "$id" ;;
    *) echo "⛔ $name resolved to '${id:-empty}', not an image id" >&2; return 1 ;;
  esac
}
# The configured name can be a tag of a refused image. Refuse the resolved id
# and every repo digest, which is where those bytes are named.
_refuse_resolved_bytes() {
  local name="$1" id digests
  id=$(_image_id "$name") || exit 2
  _refuse_old_trust "$id"
  digests=$("$A770B_DOCKER" image inspect -f '{{range .RepoDigests}}{{.}} {{end}}' "$name" 2>/dev/null || true)
  _refuse_old_trust "$digests"
  printf '%s\n' "$id"
}
_pin_compose_image() {
  local var="$1" name id digests chosen=""
  name=$(_value_for "$var")
  [ -n "$name" ] || return 0
  if [ -n "${_resolved_id:-}" ]; then
    id=$_resolved_id
  else
    id=$(_image_id "$name") || exit 2
  fi
  case "$name" in
    *@sha256:*)
      # Existing repository@sha256:<manifest> pin stays unchanged.
      export "$var=$name"
      return 0
      ;;
    sha256:*)
      # Explicit image-id reference stays unchanged.
      export "$var=$name"
      return 0
      ;;
  esac
  # Tagged reference: reuse a matching RepoDigest on the same repository when it resolves to the scanned id;
  # otherwise pass the scanned id directly (local derivative / refresh-ca without a remote manifest digest).
  digests=$("$A770B_DOCKER" image inspect -f '{{range .RepoDigests}}{{.}}{{"\n"}}{{end}}' "$name" 2>/dev/null || true)
  if [ -n "$digests" ]; then
    local cand cand_id
    while IFS= read -r cand; do
      [ -n "$cand" ] || continue
      python3 -c '
import sys
name, cand = sys.argv[1], sys.argv[2]
if "@sha256:" not in cand:
    sys.exit(1)
cand_repo = cand.split("@", 1)[0]
ref_no_digest = name.split("@", 1)[0]
last_slash = ref_no_digest.rfind("/")
last_colon = ref_no_digest.rfind(":")
want_repo = ref_no_digest[:last_colon] if last_colon > last_slash else ref_no_digest
def norm(r):
    parts = r.split("/")
    if len(parts) == 1:
        return "docker.io/library/" + r
    first = parts[0]
    if "." not in first and ":" not in first and first != "localhost":
        return "docker.io/" + r
    return r
sys.exit(0 if (cand_repo == want_repo or norm(cand_repo) == norm(want_repo)) else 1)
' "$name" "$cand" || continue
      cand_id=$("$A770B_DOCKER" image inspect -f '{{.Id}}' "$cand" 2>/dev/null || true)
      if [ "$cand_id" = "$id" ]; then
        chosen="$cand"
        break
      fi
    done <<< "$digests"
  fi
  if [ -n "$chosen" ]; then
    export "$var=$chosen"
  else
    export "$var=$id"
  fi
}

_trust_gate() {
  local name id var
  _resolved_id=""
  _refuse_old_trust "$(_value_for A770B_LLAMA_IMAGE)"
  _refuse_old_trust "$(_value_for A770B_VLLM_IMAGE)"
  _refuse_old_trust "$(_value_for A770B_SGLANG_IMAGE)"
  case "${A770B_SERVED_BACKEND:-}" in
    vllm) var=A770B_VLLM_IMAGE ;;
    sglang) var=A770B_SGLANG_IMAGE ;;
    *) var=A770B_LLAMA_IMAGE ;;
  esac
  name=$(_value_for "$var")
  if [ -z "$name" ]; then
    echo "⛔ $var is not set, so serve will not start the compose file's default tag" >&2
    exit 2
  fi
  id=$(_refuse_resolved_bytes "$name") || exit 2
  _resolved_id=$id
  _scan_image "$id" "$name"
  _pin_compose_image "$var"
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

# _container_host_pid [cid] — the host pid of the running compose container (empty when none); the sidecar and the
# pidfile name a real host process so live_backend.read's liveness check keeps working for a container.
_container_host_pid(){
  # always returns 0: the caller reads the (possibly empty) stdout, and a non-zero status here would abort the
  # caller under `set -e` before its own guard could run
  local cid="${1:-${CREATED_CID:-}}" p
  [ -n "$cid" ] || cid=$(_compose ps -q "$CONTAINER" 2>/dev/null || true)
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
# _keep_log_for_id <cid> — copy one container's log atomically to llamacpp-a770.log.
_keep_log_for_id(){
  local cid="$1" dest="$A770B_DATA/logs/llamacpp-a770.log" tmp
  [ -n "$cid" ] || return 1
  mkdir -p "$A770B_DATA/logs" || return 1
  tmp=$(mktemp "$dest.tmp.XXXXXX") || return 1
  if "$A770B_DOCKER" logs "$cid" >"$tmp" 2>&1; then
    if [ -L "$dest" ]; then rm -f "$dest"; fi
    if mv -fT "$tmp" "$dest"; then
      echo "serve log kept: $dest" >&2
      return 0
    fi
  fi
  rm -f "$tmp"
  return 1
}

_clear_ownership_files(){
  rm -f "$CIDFILE" "$INVFILE" "$PIDFILE" "$MARK" "$SIDECAR"
  if [ -f "$EVPATHFILE" ] && [ ! -L "$EVPATHFILE" ]; then
    rm -f -- "$EVPATHFILE"
  fi
}

# _docker_inspect_state <cid> — distinguish an existing container (exit 0, prints full Id),
# confirmed absence (exit 1), and daemon/lookup failure (exit 2).
# Never trusts a filtered listing on an ambiguous inspect error: anything short of an explicit
# "no such object/container" error preserves ownership records as uncertain (exit 2).
_docker_inspect_state(){
  local cid="$1" errf out
  [ -n "$cid" ] || return 1
  errf=$(mktemp)
  if out=$("$A770B_DOCKER" inspect -f '{{.Id}}' "$cid" 2>"$errf"); then
    rm -f "$errf"
    out=$(printf '%s\n' "$out" | head -n 1 | tr -d '[:space:]')
    if [ -n "$out" ]; then
      printf '%s\n' "$out"
      return 0
    fi
    return 2
  fi
  if grep -qiE 'no such (object|container)' "$errf"; then
    rm -f "$errf"
    return 1
  fi
  cat "$errf" >&2
  rm -f "$errf"
  return 2
}

# _inspect_container_label <cid> <label_key> — print one label value (empty when unset).
# Returns 0 on inspect success, 1 on confirmed absence, 2 on daemon/lookup error.
_inspect_container_label(){
  local cid="$1" key="$2" errf out
  [ -n "$cid" ] || return 1
  errf=$(mktemp)
  if out=$("$A770B_DOCKER" inspect -f "{{index .Config.Labels \"$key\"}}" "$cid" 2>"$errf"); then
    rm -f "$errf"
    out=$(printf '%s\n' "$out" | head -n 1)
    [ "$out" = "<no value>" ] && out=""
    printf '%s\n' "$out"
    return 0
  fi
  if grep -qiE 'no such (object|container)' "$errf"; then
    rm -f "$errf"
    return 1
  fi
  cat "$errf" >&2
  rm -f "$errf"
  return 2
}

# _verify_container_ownership <cid> <expected_inv> — verify <cid> is the exact full ID and carries
# matching project, service, and builder.invocation labels.
# Returns: 0 = verified owner, 1 = confirmed absent, 2 = daemon/lookup error, 3 = ownership mismatch.
_verify_container_ownership(){
  local cid="$1" expected_inv="$2" full st=0 proj svc inv
  full=$(_docker_inspect_state "$cid") || st=$?
  [ "$st" -eq 0 ] || return "$st"
  if [ "$full" != "$cid" ]; then
    echo "⛔ container id mismatch: inspected '$full', expected '$cid'" >&2
    return 3
  fi
  st=0
  proj=$(_inspect_container_label "$cid" "com.docker.compose.project") || st=$?
  [ "$st" -eq 0 ] || return "$st"
  svc=$(_inspect_container_label "$cid" "com.docker.compose.service") || st=$?
  [ "$st" -eq 0 ] || return "$st"
  inv=$(_inspect_container_label "$cid" "builder.invocation") || st=$?
  [ "$st" -eq 0 ] || return "$st"
  if [ "$proj" != "$A770B_COMPOSE_PROJECT" ] || [ "$svc" != "$CONTAINER" ] || [ "$inv" != "$expected_inv" ]; then
    echo "⛔ container $cid ownership mismatch (project='$proj' service='$svc' invocation='$inv', expected project='$A770B_COMPOSE_PROJECT' service='$CONTAINER' invocation='$expected_inv')" >&2
    return 3
  fi
  return 0
}

# _invocation_cid <inv_id> — recover ONLY the full container ID created with builder.invocation=<inv_id>.
# Exit 0 (prints full ID) on unique verified match, 1 when absent, 2 on lookup/daemon error or ambiguity.
_invocation_cid(){
  local inv_id="$1" out cand full count=0 st=0
  out=$("$A770B_DOCKER" ps -aq --no-trunc \
    --filter "label=com.docker.compose.project=${A770B_COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=${CONTAINER}" \
    --filter "label=builder.invocation=${inv_id}" 2>/dev/null) || return 2
  cand=""
  while IFS= read -r line; do
    line=$(printf '%s' "$line" | tr -d '[:space:]')
    [ -n "$line" ] || continue
    cand="$line"
    count=$((count + 1))
  done <<< "$out"
  [ "$count" -eq 0 ] && return 1
  [ "$count" -eq 1 ] || return 2
  full=$(_docker_inspect_state "$cand") || st=$?
  [ "$st" -eq 0 ] || return "$st"
  st=0
  _verify_container_ownership "$full" "$inv_id" >/dev/null || st=$?
  if [ "$st" -eq 1 ]; then
    return 1
  elif [ "$st" -ne 0 ]; then
    return 2
  fi
  printf '%s\n' "$full"
  return 0
}

# _stop_exact_container <cid> — stop and remove ONLY the given container ID (never project-level compose down).
# Returns 0 only if stop+rm succeed and inspect confirms <cid> is absent.
_stop_exact_container(){
  local cid="$1" clean_ok=1 st=0
  [ -n "$cid" ] || return 1
  if ! "$A770B_DOCKER" stop "$cid" >/dev/null 2>&1; then clean_ok=0; fi
  if ! "$A770B_DOCKER" rm -f "$cid" >/dev/null 2>&1; then clean_ok=0; fi
  _docker_inspect_state "$cid" >/dev/null 2>&1 || st=$?
  [ "$st" -eq 1 ] || clean_ok=0
  [ "$clean_ok" = 1 ]
}

# _fail_started <exit> <message> — a container that came up and then failed.
# Keep its log, then tear down ONLY CREATED_CID (never project-wide compose down).
_fail_started(){
  local code="$1" cid="${CREATED_CID:-}" cleaned=0; shift
  if [ -n "$cid" ]; then
    _keep_log_for_id "$cid" || _keep_serve_log
    if _stop_exact_container "$cid"; then
      cleaned=1
    fi
  else
    _keep_serve_log
  fi
  if [ "$cleaned" = 1 ]; then
    _clear_ownership_files
  else
    echo "⛔ failed container ${cid:-unknown} could not be torn down cleanly — ownership files ($CIDFILE, $INVFILE, $PIDFILE, $MARK, $SIDECAR) retained; the card may NOT be free" >&2
  fi
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
    card_lock
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
    _clear_ownership_files
    echo "stopped (compose project $A770B_COMPOSE_PROJECT)"
    exit 0;;
  stop-id)
    _target_cid="${2:-}"
    _expected_inv="${3:-}"
    [ -n "$_target_cid" ] || { echo "usage: serve_compose.sh stop-id <full-container-id> [invocation-id]" >&2; exit 2; }
    case "$_target_cid" in
      *[!0-9a-f]*|'')
        echo "⛔ refusing stop-id '$_target_cid': must be a verified 64-character hex container id" >&2
        exit 2 ;;
    esac
    if [ "${#_target_cid}" -ne 64 ]; then
      echo "⛔ refusing stop-id '$_target_cid': must be a verified 64-character hex container id" >&2
      exit 2
    fi
    card_lock
    _recorded_cid=$(cat "$CIDFILE" 2>/dev/null | tr -d '[:space:]' || true)
    _recorded_inv=$(cat "$INVFILE" 2>/dev/null | tr -d '[:space:]' || true)
    if [ -z "$_recorded_cid" ] || [ "$_recorded_cid" != "$_target_cid" ]; then
      _st=0
      _docker_inspect_state "$_target_cid" >/dev/null || _st=$?
      if [ "$_st" -eq 1 ]; then
        exit 0
      fi
      if [ "$_st" -eq 0 ] && [ -n "$_expected_inv" ]; then
        _own_rc=0
        _verify_container_ownership "$_target_cid" "$_expected_inv" || _own_rc=$?
        if [ "$_own_rc" -eq 1 ]; then
          exit 0
        elif [ "$_own_rc" -eq 0 ]; then
          _keep_log_for_id "$_target_cid" || true
          if ! _stop_exact_container "$_target_cid"; then
            echo "⛔ failed to stop/remove container $_target_cid — ownership files ($CIDFILE, $INVFILE, $PIDFILE, $MARK, $SIDECAR) retained; the card may NOT be free" >&2
            exit 3
          fi
          echo "stopped container $_target_cid"
          exit 0
        fi
      fi
      echo "⛔ refusing stop-id $_target_cid: does not match recorded container id (${_recorded_cid:-none}) — ownership files retained" >&2
      exit 3
    fi
    if [ -z "$_recorded_inv" ] || { [ -n "$_expected_inv" ] && [ "$_recorded_inv" != "$_expected_inv" ]; }; then
      echo "⛔ refusing stop-id $_target_cid: missing or mismatched invocation identity ($INVFILE) — ownership files retained" >&2
      exit 3
    fi
    _own_rc=0
    _verify_container_ownership "$_target_cid" "$_recorded_inv" || _own_rc=$?
    if [ "$_own_rc" -eq 1 ]; then
      _clear_ownership_files
      exit 0
    elif [ "$_own_rc" -eq 2 ]; then
      echo "⛔ cannot inspect container $_target_cid (docker daemon error) — ownership files ($CIDFILE, $INVFILE, $PIDFILE, $MARK, $SIDECAR) retained; the card may NOT be free" >&2
      exit 3
    elif [ "$_own_rc" -ne 0 ]; then
      echo "⛔ refusing stop-id $_target_cid: container failed ownership verification — ownership files retained" >&2
      exit 3
    fi
    _keep_log_for_id "$_target_cid" || true
    if ! _stop_exact_container "$_target_cid"; then
      echo "⛔ failed to stop/remove container $_target_cid — ownership files ($CIDFILE, $INVFILE, $PIDFILE, $MARK, $SIDECAR) retained; the card may NOT be free" >&2
      exit 3
    fi
    _clear_ownership_files
    echo "stopped container $_target_cid"
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
  gate)
    _trust_gate
    case "${A770B_SERVED_BACKEND:-}" in
      vllm) printf '%s\n' "${A770B_VLLM_IMAGE:-}" ;;
      sglang) printf '%s\n' "${A770B_SGLANG_IMAGE:-}" ;;
      *) printf '%s\n' "${A770B_LLAMA_IMAGE:-}" ;;
    esac
    exit 0;;
  plan|start|signature) ;;
  bench)
    shift
    DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
    [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "usage: serve_compose.sh bench [--dry-run] -- <llama-bench args…>" >&2; exit 2; }
    [ "$DRY" = 1 ] || card_lock
    _trust_gate
    if [ "$DRY" = 1 ]; then
      DRY_ENVF=""; [ -f "$A770B_COMPOSE_ENV_FILE" ] && DRY_ENVF="--env-file $A770B_COMPOSE_ENV_FILE"
      printf '%s\n' "$A770B_DOCKER compose $DRY_ENVF -p $A770B_COMPOSE_PROJECT -f $A770B_COMPOSE_FILE run --rm --no-deps --entrypoint /app/llama-bench $CONTAINER $*"
      exit 0
    fi
    _compose run --rm --no-deps --entrypoint /app/llama-bench "$CONTAINER" "$@"
    exit 0;;
  *) echo "usage: serve_compose.sh plan|start|signature <gguf> [ctx] [extra…] | gate | stop | stop-id <cid> | status | health | bench [--dry-run] -- <llama-bench args…>" >&2; exit 2;;
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
# a start is a recreate: take the exclusive card lock first, then take any existing container of this project down, so
# the budget gate reads the card free (a running container's VRAM is this project's, not a foreign server) and
# `up --force-recreate` starts from the profile just built — a profile switch must not silently keep the old model.
card_lock
_strata_rc=0
python3 "$(dirname "$0")/strata_managed.py" id >/dev/null || _strata_rc=$?
[ "$_strata_rc" = 1 ] || { echo "⛔ stop managed Strata before starting a builder container" >&2; exit 3; }
_keep_serve_log
if ! _compose down >/dev/null; then
  echo "⛔ compose down failed — the card is NOT free; prior ownership files retained" >&2
  exit 3
fi
# Another worktree's llama container is this project, so the stray-server gate counts it as ours.
# Take it down before that gate, or start can leave it running beside the new server.
if ! _down_other_worktree_llama; then exit 3; fi
if [ -f "$EVPATHFILE" ] && [ ! -L "$EVPATHFILE" ]; then
  rm -f -- "$EVPATHFILE"
fi
# A previous start's confirmation must not be copied onto this start's evidence.
if [ -f "$A770B_DATA/logs/profiling.json" ] && [ ! -L "$A770B_DATA/logs/profiling.json" ]; then
  rm -f -- "$A770B_DATA/logs/profiling.json"
fi
budget_gate || exit 1
a770b_api_key >/dev/null || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
_ep_arg=()
[ -n "$ENTRYPOINT" ] && _ep_arg=(--entrypoint "$ENTRYPOINT")
_inv_id=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
python3 "$(dirname "$0")/compose_override.py" --pull-policy never --label "builder.invocation=${_inv_id}" "${_ep_arg[@]}" --out "$OVERRIDE" -- "${ARGV[@]}"
# recreate, never --no-recreate: a profile change must replace the container
_trust_gate
CREATED_CID=""
_up_rc=0
_compose up -d --force-recreate || _up_rc=$?
_cid_rc=0
CREATED_CID=$(_invocation_cid "$_inv_id") || _cid_rc=$?
if [ "$_up_rc" -ne 0 ]; then
  if [ "$_cid_rc" -eq 0 ] && [ -n "$CREATED_CID" ]; then
    mkdir -p "$A770B_DATA/logs"
    printf '%s\n' "$CREATED_CID" > "$CIDFILE"
    printf '%s\n' "$_inv_id" > "$INVFILE"
    _keep_log_for_id "$CREATED_CID" || true
    if _stop_exact_container "$CREATED_CID"; then
      _clear_ownership_files
      echo "⛔ docker compose up failed — partially created container $CREATED_CID was stopped and removed" >&2
      exit 1
    fi
    if [ -n "${A770B_OWNED_CIDS_FILE:-}" ] && [ -f "$A770B_OWNED_CIDS_FILE" ] && [ ! -L "$A770B_OWNED_CIDS_FILE" ]; then
      printf '%s %s\n' "$CREATED_CID" "$_inv_id" >> "$A770B_OWNED_CIDS_FILE"
    fi
    echo "⛔ docker compose up failed and partially created container $CREATED_CID could not be torn down cleanly — ownership files ($CIDFILE, $INVFILE, $PIDFILE, $MARK, $SIDECAR) retained; the card may NOT be free" >&2
    exit 3
  elif [ "$_cid_rc" -eq 2 ]; then
    echo "⛔ docker compose up failed and container ownership lookup failed — retaining any ownership files" >&2
    exit 3
  else
    _prev_cid=$(cat "$CIDFILE" 2>/dev/null | tr -d '[:space:]' || true)
    if [ -n "$_prev_cid" ]; then
      _prev_st=0
      _docker_inspect_state "$_prev_cid" >/dev/null 2>&1 || _prev_st=$?
      if [ "$_prev_st" -ne 1 ]; then
        echo "⛔ docker compose up failed and prior container $_prev_cid may still exist — retaining ownership files" >&2
        exit 3
      fi
    fi
    echo "⛔ docker compose up failed — the container did not start (docker's error is above); nothing was left running" >&2
    _clear_ownership_files
    exit 1
  fi
fi
if [ "$_cid_rc" -ne 0 ] || [ -z "$CREATED_CID" ]; then
  echo "⛔ docker compose up succeeded without positive container ownership for invocation $_inv_id — refusing to adopt an unrelated or missing container" >&2
  exit 3
fi
mkdir -p "$A770B_DATA/logs"
printf '%s\n' "$CREATED_CID" > "$CIDFILE"
printf '%s\n' "$_inv_id" > "$INVFILE"
# The caller stops only what its list names, so a container that cannot be listed is
# stopped here.
if [ -n "${A770B_OWNED_CIDS_FILE:-}" ]; then
  if [ ! -f "$A770B_OWNED_CIDS_FILE" ] || [ -L "$A770B_OWNED_CIDS_FILE" ] \
      || ! { printf '%s %s\n' "$CREATED_CID" "$_inv_id" >> "$A770B_OWNED_CIDS_FILE"; } 2>/dev/null; then
    _fail_started 3 "⛔ cannot record container $CREATED_CID in $A770B_OWNED_CIDS_FILE"
  fi
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
  # The try cap is only for this invocation's container while it is still up.
  _run_state=$("$A770B_DOCKER" inspect -f '{{.State.Running}}' "$CREATED_CID" 2>/dev/null || true)
  if [ "$_run_state" = "false" ]; then
    break
  fi
  _ist=0
  _docker_inspect_state "$CREATED_CID" >/dev/null 2>&1 || _ist=$?
  [ "$_ist" -eq 1 ] && break
  sleep "$_health_pause"
done
_health_after=$(_health_class) || true
if [ "$_health_now" != ready ] || [ "$_health_after" != ready ]; then
  _fail_started 3 "⛔ the container did not answer /health"
fi
_ist=0
_docker_inspect_state "$CREATED_CID" >/dev/null 2>&1 || _ist=$?
if [ "$_ist" -ne 0 ]; then
  _fail_started 3 "⛔ owned container $CREATED_CID disappeared or could not be inspected after /health"
fi
CPID=$(_container_host_pid "$CREATED_CID")
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
if used=$(gpu_used_gib); then
  if python3 -c "import sys; sys.exit(0 if float('$used') > float('$limit') else 1)"; then
    _fail_started 3 "⛔ VRAM after load ${used} GiB > ${limit_why} ${limit} GiB — container STOPPED; use a smaller context, q4 KV, or, on a card that draws no desktop, A770B_CARD_MODE=inference"
  fi
  vram_note="✓ VRAM after load: ${used} GiB ≤ ${limit_why} ${limit} GiB"
elif [ "${A770B_ALLOW_NO_NVTOP:-0}" = 1 ]; then
  vram_note="⚠ VRAM after load: unavailable (A770B_ALLOW_NO_NVTOP=1, the VRAM cap is NOT enforced)"
else
  _fail_started 3 "⛔ VRAM telemetry unavailable after load (nvtop absent or card '$A770B_GPU_MATCH' missing/ambiguous) — container STOPPED; set A770B_ALLOW_NO_NVTOP=1 only on a headless/unmetered card"
fi
if [ -n "${A770B_LIVE_CARD:-}" ] && [ -n "${A770B_LIVE_BACKEND:-}" ] && [ -n "${A770B_LIVE_MODE:-}" ] && [ -n "${A770B_LIVE_PROFILE:-}" ] && [ -n "$CPID" ]; then
  python3 "$(dirname "$0")/live_backend.py" write --path "$SIDECAR" --card "$A770B_LIVE_CARD" --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" --model "$(basename "$MODEL")" --profile "$A770B_LIVE_PROFILE" --pid "$CPID"
fi
limit_note=""
if [ "$limit_why" != cap ] || [ "$limit" != "$A770B_VRAM_CAP_GIB" ]; then
  limit_note=" · ${limit_why} ${limit} GiB"
fi
# Confirmation runs once the server is healthy and the cap has accepted it.
# A failed read records false and does not fail the start. pending is unused:
# both proofs are on the log or on /metrics at this moment.
_profiling_engine=""
case "${A770B_SERVED_BACKEND:-}" in
  vulkan|sycl) _profiling_engine="llama.cpp" ;;
  vllm|sglang) _profiling_engine="${A770B_SERVED_BACKEND}" ;;
esac
if [ -n "$_profiling_engine" ]; then
  _write_profiling_receipt "$_profiling_engine" || true
fi
echo "▶ started server container ($A770B_COMPOSE_PROJECT) on $A770B_HOST:$A770B_PORT — model $(basename "$MODEL") ctx $CTX ub $A770B_UBATCH kv ${KV_K:-q8_0}/${KV_V:-q8_0} · mode $A770B_CARD_MODE · cap $A770B_VRAM_CAP_GIB GiB${limit_note} — host pid $CPID"
echo "$vram_note"
