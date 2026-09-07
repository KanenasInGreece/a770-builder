#!/usr/bin/env bash
# guard.sh — shared guards for the builder harness (sourced, never executed). Needs env.sh sourced first.
# Canonical refusal of protected checkouts, worktree validation, verified pids, run lock, safe host-side git,
# VRAM readings that refuse to fail open, the built-in budget gate.

# guard_worktree <path> — echoes the canonical path on success; exits 2 on any refusal.
guard_worktree(){
  local wt="$1" real top r
  [ -n "$A770B_REFUSE" ] || { echo "⛔ A770B_REFUSE is empty — list the live checkouts this seat must never touch (colon-separated) in builder.env before running" >&2; exit 2; }
  [ -n "$wt" ] || { echo "⛔ empty worktree path" >&2; exit 2; }
  real=$(realpath -e -- "$wt" 2>/dev/null) || { echo "⛔ worktree does not exist: $wt" >&2; exit 2; }
  [ -d "$real/.git" ] || { echo "⛔ $real is not a self-contained git clone (needs a .git DIRECTORY; linked worktrees of a live checkout are refused)" >&2; exit 2; }
  top=$(git -c core.fsmonitor= -c core.hooksPath=/dev/null -C "$real" rev-parse --show-toplevel 2>/dev/null | xargs -r realpath -e 2>/dev/null || true)
  [ -n "$top" ] || { echo "⛔ $real is not inside a git working tree" >&2; exit 2; }
  [ "$top" = "$real" ] || { echo "⛔ $real is a subdirectory of a worktree ($top); pass the worktree root" >&2; exit 2; }
  local IFS=':'; for r in $A770B_REFUSE; do
    [ -n "$r" ] || continue; r=$(realpath -m -- "$r")
    case "$real" in "$r"|"$r"/*) echo "⛔ refusing: $wt resolves to a protected live checkout ($r)" >&2; exit 2;; esac
    case "$top"  in "$r"|"$r"/*) echo "⛔ refusing: $wt's working tree is a protected live checkout ($r)" >&2; exit 2;; esac
  done; unset IFS
  case "$real" in "$HOME"/.claude/*|"$HOME"/.grok/*|"$HOME"/.codex/*|"$HOME"/.gemini/*|"$HOME"/.config/*|"$HOME"/.shared-memory/*|"$HOME"/.ssh/*|"$A770B_PROJECT"|"$A770B_PROJECT"/*) echo "⛔ refusing: $real is an agent home, operator ground or the harness itself" >&2; exit 2;; esac
  printf '%s\n' "$real"
}

# safe_git <worktree> <args…> — host-side git against a tree the sandboxed model can write to. Every config-driven
# code path is neutralised on the command line (review finding: a model-written .git/config would otherwise run on the
# host at the next `git status`); the sandbox additionally mounts .git/config, .git/hooks and .git/info read-only.
safe_git(){ local wt="$1"; shift; GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0 \
  git -c core.fsmonitor= -c core.pager=cat -c core.hooksPath=/dev/null -c core.sshCommand=/bin/false -c credential.helper= \
      -c diff.external= -c filter.lfs.smudge= -c filter.lfs.clean= -c filter.lfs.process= -c filter.lfs.required=false \
      -c core.editor=/bin/false -c core.askPass=/bin/false -c protocol.allow=never -C "$wt" "$@"; }

# llama_pid_alive <pidfile> — prints the pid only if it is alive AND is a llama-server process.
llama_pid_alive(){
  local pf="$1" pid comm
  [ -f "$pf" ] || return 1
  pid=$(cat "$pf" 2>/dev/null); [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  [ "$comm" = "llama-server" ] || return 1
  printf '%s\n' "$pid"
}

# run_lock — take the exclusive run lock for the rest of this shell; refuses if another run holds it.
run_lock(){
  exec 9>"$A770B_DATA/logs/local-build.lock"
  flock -n 9 || { echo "⛔ another local-build run holds the lock ($A770B_DATA/logs/local-build.lock) — one run at a time on this card" >&2; exit 2; }
}

# VRAM on the builder card via nvtop. Refuses to fail open: without nvtop the caller must have set A770B_ALLOW_NO_NVTOP=1.
_nvtop_field(){ nvtop -s 2>/dev/null | python3 -c "import sys,json; d=[x for x in json.load(sys.stdin) if '$A770B_GPU_MATCH' in x['device_name']]; print(int(d[0]['$1']) if d else -1)" 2>/dev/null || echo -1; }
require_vram_readings(){
  command -v nvtop >/dev/null && [ "$(_nvtop_field mem_total)" -gt 0 ] && return 0
  [ "$A770B_ALLOW_NO_NVTOP" = 1 ] && { echo "⚠ no VRAM readings (nvtop absent or card '$A770B_GPU_MATCH' not found) — A770B_ALLOW_NO_NVTOP=1, the VRAM cap is NOT enforced" >&2; return 0; }
  echo "⛔ no VRAM readings: install nvtop and make A770B_GPU_MATCH match the builder card in 'nvtop -s' (or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop)" >&2; return 1
}
gpu_used_gib(){ local u; u=$(_nvtop_field mem_used); [ "$u" -ge 0 ] && python3 -c "print($u/2**30)" || echo 0; }
gpu_free_mb(){  local f; f=$(_nvtop_field mem_free); [ "$f" -ge 0 ] && echo $((f/1048576)) || echo 1000000; }

# budget_gate — the built-in gate: host RAM, no stray llama-server, VRAM on the builder card, optional health URL, port.
# Replaced entirely by A770B_BUDGET_GATE when that is set.
budget_gate(){
  if [ -n "$A770B_BUDGET_GATE" ]; then bash "$A770B_BUDGET_GATE" check; return $?; fi
  local ok=1 avail free
  require_vram_readings || return 1
  avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  if [ "$avail" -ge "$A770B_MIN_AVAIL_MB" ]; then printf '  %-30s %s\n' "host MemAvailable (MB)" "$avail ≥ $A770B_MIN_AVAIL_MB"; else printf '⛔ %-30s %s\n' "host MemAvailable (MB)" "$avail < $A770B_MIN_AVAIL_MB"; ok=0; fi
  if pgrep -x llama-server >/dev/null && ! llama_pid_alive "$A770B_DATA/logs/llamacpp-a770.pid" >/dev/null; then printf '⛔ %-30s %s\n' "other llama-server" "running outside this harness — one GPU process per card"; ok=0; else printf '  %-30s %s\n' "other llama-server" "none"; fi
  free=$(gpu_free_mb); if [ "$free" -ge "$A770B_MIN_VRAM_MB" ]; then printf '  %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "$free ≥ $A770B_MIN_VRAM_MB"; else printf '⛔ %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "$free < $A770B_MIN_VRAM_MB"; ok=0; fi
  if [ -n "$A770B_HEALTH_URL" ]; then if curl -s --max-time 5 "$A770B_HEALTH_URL" | grep -qE '"status": ?"ok"'; then printf '  %-30s %s\n' "health $A770B_HEALTH_URL" "ok"; else printf '⛔ %-30s %s\n' "health $A770B_HEALTH_URL" "not ok"; ok=0; fi; fi
  case " $A770B_FRAMEWORK_PORTS " in *" $A770B_PORT "*) printf '⛔ %-30s %s\n' "port $A770B_PORT" "is a protected port"; ok=0;; esac
  [ "$ok" = 1 ] || { echo "⛔ budget refused — nothing started"; return 1; }
  echo "✅ budget ok"
}
