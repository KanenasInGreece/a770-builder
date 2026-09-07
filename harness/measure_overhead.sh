#!/usr/bin/env bash
# measure_overhead.sh — how many prompt tokens does opencode's FIRST request cost on this repo, per configuration?
# Reads the llama-server log's prompt eval line for the request. Server must be up (local-build.sh serve fast).
# TESTING ONLY: opencode runs unconfined here with the rendered profile, on the seat, to measure the request — not to build.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
set -uo pipefail
WT=$(guard_worktree "$A770B_SEAT") || exit 2; LOG=$A770B_DATA/logs/llamacpp-a770.log
CFG=$A770B_DATA/logs/opencode.overhead.jsonc; a770b_render_profile fast "$A770B_FAST_CTX" "$CFG" || exit 2
export OPENCODE_CONFIG=$CFG
run(){ label="$1"; shift; n0=$(grep -c 'prompt eval time' "$LOG"); t0=$(date +%s)
  ( cd "$WT" && timeout 900 opencode run --dir "$WT" -m local-a770/local-builder "$@" "Reply with the single word OK and nothing else." < /dev/null >/dev/null 2>&1 )
  t1=$(date +%s); tot=$(grep 'prompt eval time' "$LOG" | tail -n +$((n0+1)) | grep -oE '/ +[0-9]+ tokens' | grep -oE '[0-9]+' | paste -sd+ | bc)
  printf '%-34s first-request prompt tokens=%-6s wall=%ss\n' "$label" "${tot:-?}" "$((t1-t0))"; }
run "V1 as-is (MCP+skills off)"
mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"; run "V2 + AGENTS.md set aside"; mv "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"
run "V3 as-is + lean agent" --agent local-builder
mv "$WT/AGENTS.md" "$WT/AGENTS.md.local-off"; run "V4 AGENTS.md aside + lean agent" --agent local-builder; mv "$WT/AGENTS.md.local-off" "$WT/AGENTS.md"
safe_git "$WT" status --porcelain | wc -l
