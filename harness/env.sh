#!/usr/bin/env bash
# env.sh — the ONE place every path and knob comes from (sourced by every harness script and the skill script).
# Precedence, later wins:  defaults below  →  <project>/config/builder.env  →  ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.env
#                          →  <project>/config/builder.<mode>.env  →  ${XDG_CONFIG_HOME:-~/.config}/a770-builder/builder.<mode>.env
#                          →  variables already in the environment. The mode naming the per-mode files is the mode
#                          after the two builder.env files and the environment have been read.
# Copy config/builder.env.example to one of those two places and edit. Nothing else needs touching.
A770B_PROJECT="${A770B_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck disable=SC1090 # builder.env is named at run time; the linter cannot follow it and need not
_a770b_load(){ [ -f "$1" ] && { set -a; . "$1"; set +a; }; return 0; }
_a770b_snapshot=$(export -p | grep -E '^declare -x A770B_' || true)   # values set by the caller win over files
_a770b_load "$A770B_PROJECT/config/builder.env"
_a770b_load "${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.env"
eval "$_a770b_snapshot"                                  # a mode from the calling environment names the per-mode files
: "${A770B_CARD_MODE:=display}"
case "$A770B_CARD_MODE" in display|inference) ;; *) echo "⛔ A770B_CARD_MODE must be display or inference (it is '$A770B_CARD_MODE')" >&2; return 2 2>/dev/null || exit 2;; esac
_a770b_selected_mode=$A770B_CARD_MODE
_a770b_load "$A770B_PROJECT/config/builder.$A770B_CARD_MODE.env"
_a770b_load "${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/builder.$A770B_CARD_MODE.env"
[ "$A770B_CARD_MODE" = "$_a770b_selected_mode" ] || { echo "⛔ a builder.$_a770b_selected_mode.env (in config/ or ~/.config/a770-builder/) sets A770B_CARD_MODE=$A770B_CARD_MODE: a per-mode file cannot switch the mode that selected it" >&2; return 2 2>/dev/null || exit 2; }
eval "$_a770b_snapshot"; unset _a770b_snapshot _a770b_selected_mode

# ── where things are ─────────────────────────────────────────────────────────────────────────────────────────
: "${A770B_DATA:=$HOME/local-ai}"                       # results/, logs/, seat/, seat-cache/ live here
: "${A770B_SEAT:=$A770B_DATA/seat}"                     # default worktree: a plain clone of the target repo
: "${A770B_MODELS:=$HOME/LLM/tested}"                   # where the GGUFs live (the server reads them; the sandbox never sees them)
: "${A770B_REFUSE:=}"                                   # REQUIRED: colon-separated live checkouts the seat must never touch (guard refuses to run while empty)
# flash attention off is the Gemma 4 condition on this card (with it on, prefill collapses with position and the GPU
# watchdog fires); without it the V cache must be f16, which the sliding window keeps small
# ── the profiles: config/profiles.json is the single source (harness/profiles.py env prints ${VAR:=…} defaults, so
#    builder.env and the environment still win per variable); A770B_PROFILES lists the names, A770B_DEFAULT_PROFILE the default
# the mode's two defaults: the registry it reads and its VRAM cap. 15.3 is measured on the A770 with no display (15.9
# on the card, prefill growth at or under 0.3 GiB under ub 512, a 0.3 GiB reserve); 13.0 leaves an unmeasured desktop 3 GiB.
case "$A770B_CARD_MODE" in inference) : "${A770B_PROFILES_FILE:=$A770B_PROJECT/config/profiles.inference.json}"; : "${A770B_VRAM_CAP_GIB:=15.3}";; *) : "${A770B_PROFILES_FILE:=$A770B_PROJECT/config/profiles.json}"; : "${A770B_VRAM_CAP_GIB:=13.0}";; esac
_a770b_profile_lines=$(python3 "$A770B_PROJECT/harness/profiles.py" env --file "$A770B_PROFILES_FILE") || { echo "⛔ the registry $A770B_PROFILES_FILE is invalid (python3 harness/profiles.py check says why)" >&2; return 2 2>/dev/null || exit 2; }
eval "$_a770b_profile_lines"; unset _a770b_profile_lines
: "${A770B_OUTPUT_TOKENS:=16384}"                            # opencode's per-reply output limit: a whole file goes out in one tool call, and at 4,096 a test file of two hundred lines was cut mid-JSON, so every write failed (measured 2026-09-08)
: "${A770B_HIDDEN_ROOT:=$A770B_DATA/hidden}"                  # hidden acceptance tests a run specification may name: files the model never sees, copied into the seat by verify after the patch applies
# ── the server ───────────────────────────────────────────────────────────────────────────────────────────────
: "${A770B_LLAMA_BIN:=${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}}"
: "${A770B_DEVICE:=Vulkan0}"                             # llama-server --list-devices names the cards; pick the builder card
: "${A770B_VK_DEVICE_SELECT=8086:56a0!}"                # Mesa device selector, vendor:device of the builder card with '!' = the only Vulkan device the server sees (A770 = 8086:56a0); Vulkan lists the boot card first, so an index alone drifts when the desktop moves
: "${A770B_GPU_MATCH:=DG2}"                             # substring of the card's name in `nvtop -s`, for VRAM readings and the cap
: "${A770B_PORT:=8093}";  : "${A770B_HOST:=127.0.0.1}";  : "${A770B_ALIAS:=local-builder}"
: "${A770B_UBATCH:=512}";  : "${A770B_BATCH:=2048}"     # ubatch stays 512 in both modes (raising it gained 6 percent prefill when measured)
: "${A770B_API_KEY_FILE:=${XDG_CONFIG_HOME:-$HOME/.config}/a770-builder/api.key}"   # the server's API key (one line, mode 600); created on first serve
: "${A770B_ALLOW_NO_NVTOP:=0}"                           # 1 = start without VRAM readings (NOT on a card that draws a desktop)
: "${GGML_VK_DISABLE_COOPMAT:=1}"; export GGML_VK_DISABLE_COOPMAT
# ── the budget gate (built in; set A770B_BUDGET_GATE to an external script to use that instead) ─────────────
: "${A770B_BUDGET_GATE:=}"
: "${A770B_MIN_AVAIL_MB:=20000}"; : "${A770B_MIN_VRAM_MB:=3000}"
: "${A770B_FRAMEWORK_PORTS:=}"                           # ports the seat must never bind (another service's card), space-separated
: "${A770B_HEALTH_URL:=}"                                # optional: a status URL read once before a server starts; its answer is printed, never a refusal (empty = skip)
# ── opencode + sandbox ───────────────────────────────────────────────────────────────────────────────────────
: "${A770B_OPENCODE_BIN:=}"                              # directory holding `opencode`; empty = found on PATH
: "${A770B_OPENCODE_MODELS:=$HOME/.cache/opencode/models.json}"
: "${A770B_PROFILE_TEMPLATE:=$A770B_PROJECT/config/opencode.profile.template.jsonc}"
: "${A770B_UV_CACHE:=$A770B_DATA/seat-cache/uv}"        # pre-warmed by harness/warm_cache.sh; mounted READ-ONLY, the sandbox has no network
# ── qualification (harness/run_one.sh, bench_model.sh) ──────────────────────────────────────────────────────
: "${A770B_TASK_BRIEF:=$A770B_PROJECT/briefs/T1-sanitize-entity-tests.md}"   # the coding task every candidate model gets — write your own for your repo
: "${A770B_PROBE_CORPUS:=$A770B_SEAT/**/*.py}"          # glob of source files for the long-context prefill probe
: "${A770B_TASK_TEST_FILE:=tests/test_sanitize_entity_name_matrix.py}"      # the file the task brief asks for (capture reports on it)
export A770B_PROJECT A770B_DATA A770B_SEAT A770B_MODELS A770B_REFUSE A770B_PORT A770B_HOST A770B_ALIAS A770B_GPU_MATCH A770B_API_KEY_FILE A770B_CARD_MODE A770B_PROFILES_FILE A770B_VRAM_CAP_GIB
export A770B_PROFILES A770B_DEFAULT_PROFILE
mkdir -p "$A770B_DATA/logs" "$A770B_DATA/results" 2>/dev/null || true
a770b_model_path(){ case "$1" in /*) printf '%s\n' "$1";; *) printf '%s\n' "$A770B_MODELS/$1";; esac; }
# a770b_profile_var <profile> <FIELD> — the value of A770B_<PROFILE>_<FIELD> (name upper-cased, - → _); empty if unset
a770b_profile_var(){ local n; n=$(printf '%s' "$1" | tr 'a-z-' 'A-Z_'); local v="A770B_${n}_$2"; printf '%s\n' "${!v:-}"; }
# a770b_is_profile <name> — true when the name is in A770B_PROFILES
a770b_is_profile(){ case " $A770B_PROFILES " in *" $1 "*) return 0;; *) return 1;; esac; }
# a770b_api_key — the key the server requires and the rendered profile carries. Created once, readable only by the operator.
a770b_api_key(){
  if [ ! -s "$A770B_API_KEY_FILE" ]; then
    mkdir -p "$(dirname "$A770B_API_KEY_FILE")"
    # noclobber (set -C): two first callers cannot both create a key; the loser reads the winner's
    ( umask 077; set -C; python3 -c 'import secrets; print("a770b-" + secrets.token_hex(24))' > "$A770B_API_KEY_FILE" ) 2>/dev/null || true
    [ -s "$A770B_API_KEY_FILE" ] || return 1
  fi
  chmod 600 "$A770B_API_KEY_FILE" 2>/dev/null || true       # re-asserted on every read, not only at creation
  head -n 1 "$A770B_API_KEY_FILE"
}
# a770b_render_profile <profile> <ctx> <out> [nokey] — the ONLY opencode config a sandboxed run sees, rendered by
# harness/render_profile.py from the template with the server URL, the key, the window and the output limit, and, when
# A770B_SPEC names a run specification, with that specification applied against the seat in A770B_RENDER_SEAT; the
# renderer then writes <out>.echo.json saying what it rendered. Written mode 600: it carries the key. With a fourth
# argument the key is a placeholder and no specification is applied: for runs that need no server (verify).
a770b_render_profile(){
  local profile="$1" ctx="$2" out="$3" key
  if [ -n "${4:-}" ]; then key="no-key-for-this-run"; else key=$(a770b_api_key) || { echo "⛔ cannot create the API key file $A770B_API_KEY_FILE" >&2; return 1; }; fi
  rm -f "$out" "$out.echo.json"
  # the profile's own sampling (env.sh's TEMPERATURE/TOP_P defaults, or an override): an empty value adds no argument
  local -a sampling_args=()
  local temp top_p output
  temp=$(a770b_profile_var "$profile" TEMPERATURE)
  top_p=$(a770b_profile_var "$profile" TOP_P)
  [ -n "$temp" ] && sampling_args+=(--temperature "$temp")
  [ -n "$top_p" ] && sampling_args+=(--top-p "$top_p")
  # the profile's own output_tokens (U2d) when set, else the global A770B_OUTPUT_TOKENS
  output=$(a770b_profile_var "$profile" OUTPUT_TOKENS); [ -n "$output" ] || output="$A770B_OUTPUT_TOKENS"
  if [ -n "${A770B_SPEC:-}" ] && [ -z "${4:-}" ]; then
    python3 "$A770B_PROJECT/harness/render_profile.py" render --template "$A770B_PROFILE_TEMPLATE" --out "$out" --baseurl "http://$A770B_HOST:$A770B_PORT/v1" --apikey "$key" --ctx "$ctx" --output "$output" --name "$profile" "${sampling_args[@]}" --spec "$A770B_SPEC" --seat "${A770B_RENDER_SEAT:?the seat the specification is checked against}" --echo "$out.echo.json"
  else
    python3 "$A770B_PROJECT/harness/render_profile.py" render --template "$A770B_PROFILE_TEMPLATE" --out "$out" --baseurl "http://$A770B_HOST:$A770B_PORT/v1" --apikey "$key" --ctx "$ctx" --output "$output" --name "$profile" "${sampling_args[@]}"
  fi
}
a770b_opencode_bin(){ if [ -n "$A770B_OPENCODE_BIN" ]; then printf '%s\n' "$A770B_OPENCODE_BIN"; else dirname "$(readlink -f "$(command -v opencode)")"; fi; }
