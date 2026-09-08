#!/usr/bin/env bash
# serve_a770_llamacpp.sh — the ONE way a llama.cpp server starts on the builder card. Every knob comes from env.sh.
#   serve_a770_llamacpp.sh start <gguf (name in A770B_MODELS, or absolute)> [ctx] [extra llama-server args…]
#   serve_a770_llamacpp.sh stop | status
# Env (see config/builder.env.example): A770B_DEVICE, A770B_VK_DEVICE_SELECT, A770B_PORT, A770B_UBATCH, A770B_VRAM_CAP_GIB; per call KV_K/KV_V
# and REASONING. Flags baked in (measured on the A770 under Vulkan): -fa on, --no-mmap, -ngl 99, --parallel 1,
# quantised KV, --jinja, GGML_VK_DISABLE_COOPMAT=1 (the flag set proven on Arc under Vulkan).
# ⚠ The cap and the ubatch are the card mode's (A770B_CARD_MODE): 13.0 GiB after load on a card that also draws a
# desktop, 15.3 on one that draws nothing, -ub 512 in both, because the Xe job watchdog is what both protect.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
PIDFILE="$A770B_DATA/logs/llamacpp-a770.pid"; LOG="$A770B_DATA/logs/llamacpp-a770.log"; MARK="$A770B_DATA/logs/llamacpp-a770.model"
case "${1:-}" in
  stop)   if pid=$(llama_pid_alive "$PIDFILE"); then kill "$pid"; echo "stopped pid $pid"; else echo "not running (or pidfile stale — nothing killed)"; fi; rm -f "$PIDFILE" "$MARK"; exit 0;;
  status) if pid=$(llama_pid_alive "$PIDFILE"); then echo "running pid $pid · $(cat "$MARK" 2>/dev/null)"; curl -s -H "Authorization: Bearer $(a770b_api_key)" "http://$A770B_HOST:$A770B_PORT/v1/models" | head -c 200; echo; else echo "not running"; fi; exit 0;;
  start)  ;;
  *) echo "usage: start <gguf> [ctx] [extra…] | stop | status" >&2; exit 2;;
esac
MODEL=$(a770b_model_path "${2:?gguf}"); CTX="${3:-32768}"; shift 3 2>/dev/null || shift $#
[ -r "$MODEL" ] || { echo "⛔ model not readable: $MODEL  (A770B_MODELS=$A770B_MODELS)" >&2; exit 2; }
[ -x "$A770B_LLAMA_BIN" ] || { echo "⛔ llama-server not found at $A770B_LLAMA_BIN (set A770B_LLAMA_BIN)" >&2; exit 2; }
llama_pid_alive "$PIDFILE" >/dev/null && { echo "⛔ already running (pid $(cat "$PIDFILE")) — stop first" >&2; exit 2; }
budget_gate || exit 1
ss -ltn | grep -q ":$A770B_PORT " && { echo "⛔ port $A770B_PORT bound" >&2; exit 2; }
a770b_api_key >/dev/null || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
# --api-key-file: every completion needs the key (the rendered profile carries it); a process outside the harness cannot use the card unnoticed
MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" nohup "$A770B_LLAMA_BIN" -m "$MODEL" --alias "$A770B_ALIAS" --device "$A770B_DEVICE" --host "$A770B_HOST" --port "$A770B_PORT" --api-key-file "$A770B_API_KEY_FILE" \
  -ngl 99 -c "$CTX" -b "$A770B_BATCH" -ub "$A770B_UBATCH" --parallel 1 -fa on --no-mmap -ctk "${KV_K:-q8_0}" -ctv "${KV_V:-q8_0}" \
  --jinja --reasoning "${REASONING:-off}" --reasoning-format deepseek "$@" > "$LOG" 2>&1 9>&- &   # 9>&-: never inherit the run lock
echo $! > "$PIDFILE"; printf '%s\n' "$MODEL" > "$MARK"
echo "▶ started llama-server pid $! on $A770B_HOST:$A770B_PORT — model $(basename "$MODEL") ctx $CTX ub $A770B_UBATCH kv ${KV_K:-q8_0}/${KV_V:-q8_0} · mode $A770B_CARD_MODE · cap $A770B_VRAM_CAP_GIB GiB — log $LOG"
for _ in $(seq 1 150); do curl -sf --max-time 2 "http://$A770B_HOST:$A770B_PORT/health" 2>/dev/null | grep -q '"ok"' && break; kill -0 "$(cat "$PIDFILE")" 2>/dev/null || break; sleep 2; done
used=$(gpu_used_gib)
if python3 -c "import sys; sys.exit(0 if float('$used') > float('$A770B_VRAM_CAP_GIB') else 1)"; then kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE" "$MARK"; echo "⛔ VRAM after load ${used} GiB > cap $A770B_VRAM_CAP_GIB GiB — server STOPPED; use a smaller context, q4 KV, or, on a card that draws no desktop, A770B_CARD_MODE=inference"; exit 3; fi
echo "✓ VRAM after load: ${used} GiB ≤ cap $A770B_VRAM_CAP_GIB GiB"
