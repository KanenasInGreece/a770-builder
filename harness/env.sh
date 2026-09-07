#!/usr/bin/env bash
# env.sh — the ONE place every path and knob comes from (sourced by every harness script and the skill script).
# Precedence, later wins:  defaults below  →  <project>/config/builder.env  →  ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.env
#                          →  variables already in the environment.
# Copy config/builder.env.example to one of those two places and edit. Nothing else needs touching.
A770B_PROJECT="${A770B_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_a770b_load(){ [ -f "$1" ] && { set -a; . "$1"; set +a; }; return 0; }
_a770b_snapshot=$(export -p | grep -E '^declare -x A770B_' || true)   # values set by the caller win over files
_a770b_load "$A770B_PROJECT/config/builder.env"
_a770b_load "${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.env"
eval "$_a770b_snapshot"; unset _a770b_snapshot

# ── where things are ─────────────────────────────────────────────────────────────────────────────────────────
: "${A770B_DATA:=$HOME/local-ai}"                       # results/, logs/, seat/, seat-cache/ live here
: "${A770B_SEAT:=$A770B_DATA/seat}"                     # default worktree: a plain clone of the target repo
: "${A770B_MODELS:=$HOME/LLM/tested}"                   # where the GGUFs live (the server reads them; the sandbox never sees them)
: "${A770B_REFUSE:=}"                                   # REQUIRED: colon-separated live checkouts the seat must never touch (guard refuses to run while empty)
# ── the two profiles ─────────────────────────────────────────────────────────────────────────────────────────
: "${A770B_FAST_MODEL:=Qwen3.5-9B-Q4_K_M.gguf}";            : "${A770B_FAST_CTX:=81920}";    : "${A770B_FAST_KV:=q8_0}"
: "${A770B_FAST_REASONING:=off}";                            : "${A770B_FAST_EXTRA:=}"
: "${A770B_SERIOUS_MODEL:=Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf}"; : "${A770B_SERIOUS_CTX:=158000}"; : "${A770B_SERIOUS_KV:=q4_0}"
: "${A770B_SERIOUS_REASONING:=on}"
: "${A770B_SERIOUS_EXTRA:=--chat-template-kwargs {\"reasoning_effort\":\"low\"} --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0}"
: "${A770B_FAST_TIMEOUT:=1500}";                             : "${A770B_SERIOUS_TIMEOUT:=3600}"
: "${A770B_OUTPUT_TOKENS:=4096}"                             # opencode's per-reply output limit for both profiles
# ── the server ───────────────────────────────────────────────────────────────────────────────────────────────
: "${A770B_LLAMA_BIN:=${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}}"
: "${A770B_DEVICE:=Vulkan0}"                             # llama-server --list-devices names the cards; pick the builder card
: "${A770B_GPU_MATCH:=DG2}"                             # substring of the card's name in `nvtop -s`, for VRAM readings and the cap
: "${A770B_PORT:=8093}";  : "${A770B_HOST:=127.0.0.1}";  : "${A770B_ALIAS:=local-builder}"
: "${A770B_UBATCH:=512}";  : "${A770B_BATCH:=2048}";     : "${A770B_VRAM_CAP_GIB:=13.0}"
: "${A770B_ALLOW_NO_NVTOP:=0}"                           # 1 = start without VRAM readings (NOT on a card that draws a desktop)
: "${GGML_VK_DISABLE_COOPMAT:=1}"; export GGML_VK_DISABLE_COOPMAT
# ── the budget gate (built in; set A770B_BUDGET_GATE to an external script to use that instead) ─────────────
: "${A770B_BUDGET_GATE:=}"
: "${A770B_MIN_AVAIL_MB:=20000}"; : "${A770B_MIN_VRAM_MB:=3000}"
: "${A770B_FRAMEWORK_PORTS:=}"                           # ports the seat must never bind (another service's card), space-separated
: "${A770B_HEALTH_URL:=}"                                # optional: a URL that must answer ok before a server starts (empty = skip)
# ── opencode + sandbox ───────────────────────────────────────────────────────────────────────────────────────
: "${A770B_OPENCODE_BIN:=}"                              # directory holding `opencode`; empty = found on PATH
: "${A770B_OPENCODE_MODELS:=$HOME/.cache/opencode/models.json}"
: "${A770B_PROFILE_TEMPLATE:=$A770B_PROJECT/config/opencode.profile.template.jsonc}"
: "${A770B_UV_CACHE:=$A770B_DATA/seat-cache/uv}"        # pre-warmed by harness/warm_cache.sh; mounted READ-ONLY, the sandbox has no network
# ── qualification (harness/run_one.sh, bench_model.sh) ──────────────────────────────────────────────────────
: "${A770B_TASK_BRIEF:=$A770B_PROJECT/briefs/T1-sanitize-entity-tests.md}"   # the coding task every candidate model gets — write your own for your repo
: "${A770B_PROBE_CORPUS:=$A770B_SEAT/shared-memory/scripts/*.py}"          # glob of source files for the long-context prefill probe
: "${A770B_TASK_TEST_FILE:=tests/test_sanitize_entity_name_matrix.py}"      # the file the task brief asks for (capture reports on it)
export A770B_PROJECT A770B_DATA A770B_SEAT A770B_MODELS A770B_REFUSE A770B_PORT A770B_HOST A770B_ALIAS
mkdir -p "$A770B_DATA/logs" "$A770B_DATA/results" 2>/dev/null || true
a770b_model_path(){ case "$1" in /*) printf '%s\n' "$1";; *) printf '%s\n' "$A770B_MODELS/$1";; esac; }
a770b_opencode_bin(){ if [ -n "$A770B_OPENCODE_BIN" ]; then printf '%s\n' "$A770B_OPENCODE_BIN"; else dirname "$(readlink -f "$(command -v opencode)")"; fi; }
