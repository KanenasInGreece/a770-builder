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
  [ ! -L "$real/.git" ] || { echo "⛔ $real/.git is a symlink — the seat's repository metadata must be its own" >&2; exit 2; }
  [ -d "$real/.git" ] || { echo "⛔ $real is not a self-contained git clone (needs a .git DIRECTORY; linked worktrees of a live checkout are refused)" >&2; exit 2; }
  top=$(git -c core.fsmonitor= -c core.hooksPath=/dev/null -C "$real" rev-parse --show-toplevel 2>/dev/null | xargs -r realpath -e 2>/dev/null || true)
  [ -n "$top" ] || { echo "⛔ $real is not inside a git working tree" >&2; exit 2; }
  gitdir=$(git -c core.fsmonitor= -c core.hooksPath=/dev/null -C "$real" rev-parse --absolute-git-dir 2>/dev/null | xargs -r realpath -e 2>/dev/null || true)
  [ "$gitdir" = "$real/.git" ] || { echo "⛔ $real's repository metadata resolves to $gitdir, not to its own .git" >&2; exit 2; }
  [ "$top" = "$real" ] || { echo "⛔ $real is a subdirectory of a worktree ($top); pass the worktree root" >&2; exit 2; }
  local IFS=':'; for r in $A770B_REFUSE; do
    [ -n "$r" ] || continue; r=$(realpath -m -- "$r")
    case "$real" in "$r"|"$r"/*) echo "⛔ refusing: $wt resolves to a protected live checkout ($r)" >&2; exit 2;; esac
    case "$top"  in "$r"|"$r"/*) echo "⛔ refusing: $wt's working tree is a protected live checkout ($r)" >&2; exit 2;; esac
  done; unset IFS
  case "$real" in "$HOME"/.claude/*|"$HOME"/.grok/*|"$HOME"/.codex/*|"$HOME"/.gemini/*|"$HOME"/.config/*|"$HOME"/.ssh/*|"$A770B_PROJECT"|"$A770B_PROJECT"/*) echo "⛔ refusing: $real is an agent home, operator ground or the harness itself" >&2; exit 2;; esac
  printf '%s\n' "$real"
}

# safe_git <worktree> <args…> — host-side git against a tree the sandboxed model can write to. Every config-driven
# code path is neutralised on the command line (review finding: a model-written .git/config would otherwise run on the
# host at the next `git status`); the sandbox additionally mounts .git/config, .git/hooks and .git/info read-only.
# Diffs go through safe_git_diff, which adds --no-ext-diff --no-textconv (an empty diff.external would itself mean "run an
# empty command", so the external-diff path is closed per call, not by config).
safe_git(){ local wt="$1"; shift; GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0 \
  git -c core.fsmonitor= -c core.pager=cat -c core.hooksPath=/dev/null -c core.sshCommand=/bin/false -c credential.helper= \
      -c filter.lfs.smudge= -c filter.lfs.clean= -c filter.lfs.process= -c filter.lfs.required=false \
      -c core.editor=/bin/false -c core.askPass=/bin/false -c protocol.allow=never -C "$wt" "$@"; }

# safe_git_diff <worktree> <args…> — a diff with every external program disabled.
safe_git_diff(){ local wt="$1"; shift; safe_git "$wt" diff --no-ext-diff --no-textconv "$@"; }

# seat_dirty <worktree> — lists everything in the seat that is not committed, INCLUDING ignored files (a model can plant
# a .venv, a CLAUDE.md or an agent settings file that the plain status never shows). Ignored files are listed one by one
# (status would collapse an ignored directory), and only the harness's own brief copies, Local_Documentation/briefs/brief-<stamp>.md,
# are exempt — by exact name, in the status output and among ignored files alike, so nothing else can hide in that directory.
seat_dirty(){ { safe_git "$1" status --porcelain --untracked-files=all 2>/dev/null | grep -vE '^\?\? Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$'; safe_git "$1" ls-files --others --ignored --exclude-standard 2>/dev/null | grep -vE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' | sed 's/^/!! /'; } || true; }

# reset_worktree <worktree> — discard everything the model did in the seat: tracked changes, untracked AND ignored files
# (-x; an operator's own ignored files in the seat go too — keep no state there), keeping only Local_Documentation/briefs.
# Every call is safe_git because the model owned the tree.
reset_worktree(){ local wt="$1"
  safe_git "$wt" checkout -- . 2>/dev/null || true
  # a briefs path that is not a real directory (a symlink the model left) goes first: git clean's exemption would keep it
  [ -L "$wt/Local_Documentation/briefs" ] && rm -f -- "$wt/Local_Documentation/briefs"
  [ -L "$wt/Local_Documentation" ] && rm -f -- "$wt/Local_Documentation"
  safe_git "$wt" clean -fdxq -e /Local_Documentation/briefs 2>/dev/null || true
  # the briefs directory keeps only the harness's own copies; anything else planted there goes with the rest
  [ -d "$wt/Local_Documentation/briefs" ] && find "$wt/Local_Documentation/briefs" -mindepth 1 -regextype posix-extended ! -regex '.*/brief-[0-9]{8}-[0-9]{6}\.md' -exec rm -rf -- {} + 2>/dev/null
  echo "worktree reset: $(seat_dirty "$wt" | wc -l) entries remain"
}

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

# run_pid_alive <pidfile> — prints the pid only if it is alive AND is the timeout process of a run of this harness (comm timeout, cmdline naming sandbox_run.sh or opencode run); a stale or foreign pid is refused, never signalled.
run_pid_alive(){
  local pf="$1" pid comm cmdline
  [ -f "$pf" ] || return 1
  pid=$(cat "$pf" 2>/dev/null); [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  [ "$comm" = "timeout" ] || return 1
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmdline" in *sandbox_run.sh*|*"opencode run"*) : ;; *) return 1;; esac
  printf '%s\n' "$pid"
}

# run_lock — take the exclusive run lock for the rest of this shell; refuses if another run holds it (one retry, so a
# status probe holding it for a moment never refuses a run).
run_lock(){
  exec 9>"$A770B_DATA/logs/local-build.lock"
  flock -n 9 || { sleep 0.3; flock -n 9; } || { echo "⛔ another local-build run holds the lock ($A770B_DATA/logs/local-build.lock) — one run at a time on this card" >&2; exit 2; }
}

# VRAM on the builder card via nvtop. Refuses to fail open: without nvtop the caller must have set A770B_ALLOW_NO_NVTOP=1.
# -1 (not just when nvtop is absent) when the number of matching devices is not exactly one — an ambiguous match never picks d[0].
_nvtop_field(){ nvtop -s 2>/dev/null | python3 -c "import sys,json; d=[x for x in json.load(sys.stdin) if '$A770B_GPU_MATCH' in x['device_name']]; print(int(d[0]['$1']) if len(d) == 1 else -1)" 2>/dev/null || echo -1; }
# gpu_match_count — the number of nvtop devices whose device_name contains A770B_GPU_MATCH; -1 without readings (nvtop absent or its JSON unreadable).
gpu_match_count(){ nvtop -s 2>/dev/null | python3 -c "import sys,json; print(len([x for x in json.load(sys.stdin) if '$A770B_GPU_MATCH' in x['device_name']]))" 2>/dev/null || echo -1; }
require_vram_readings(){
  command -v nvtop >/dev/null && [ "$(_nvtop_field mem_total)" -gt 0 ] && return 0
  [ "$A770B_ALLOW_NO_NVTOP" = 1 ] && { echo "⚠ no VRAM readings (nvtop absent or card '$A770B_GPU_MATCH' not found) — A770B_ALLOW_NO_NVTOP=1, the VRAM cap is NOT enforced" >&2; return 0; }
  echo "⛔ no VRAM readings: install nvtop and make A770B_GPU_MATCH match the builder card in 'nvtop -s' (or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop)" >&2; return 1
}
gpu_used_gib(){ local u; u=$(_nvtop_field mem_used); [ "$u" -ge 0 ] && python3 -c "print($u/2**30)" || echo 0; }
gpu_free_mb(){  local f; f=$(_nvtop_field mem_free); [ "$f" -ge 0 ] && echo $((f/1048576)) || echo 1000000; }

# kernel_resets_since <journalctl --since ARG> — count of kernel-log lines since ARG matching A770B_RESET_PATTERN
# (Intel's Xe driver wording by default; a non-Intel driver's user sets A770B_RESET_PATTERN in builder.env — see
# config/builder.env.example for how to find their own driver's wording). The ONE place this grep is made, so
# every caller (ctx_sweep.sh, bench_model.sh) reads the same, configurable pattern rather than a hard-coded one.
kernel_resets_since(){ journalctl -k --since "$1" 2>/dev/null | grep -ciE "$A770B_RESET_PATTERN" || true; }

# a770b_ensure_corpus_file — makes sure $A770B_CORPUS_FILE exists, GENERATING it via kit/corpus.py when it is
# missing (one line, the same command config/builder.env.example and kit/corpus.py's own docstring name), and
# says what it did. The corpus is part of the instrument, not a convenience: a caller that silently fell back to
# a different corpus (the seat's own source files, say) would hand a stranger a different prompt at every point on
# the curve than the one this workstation measured, so nothing here falls back — generation failure REFUSES (exit
# 2, nothing left half-written) with the exact command to run by hand.
a770b_ensure_corpus_file(){
  [ -n "${A770B_CORPUS_FILE:-}" ] || { echo "⛔ A770B_CORPUS_FILE is empty" >&2; return 2; }
  if [ -f "$A770B_CORPUS_FILE" ]; then echo "corpus: $A770B_CORPUS_FILE (already generated)"; return 0; fi
  echo "corpus: $A770B_CORPUS_FILE is missing — generating it: python3 $A770B_PROJECT/kit/corpus.py generate --out $A770B_CORPUS_FILE"
  if python3 "$A770B_PROJECT/kit/corpus.py" generate --out "$A770B_CORPUS_FILE"; then
    return 0
  else
    echo "⛔ could not generate the corpus — run by hand and fix what it reports: python3 $A770B_PROJECT/kit/corpus.py generate --out $A770B_CORPUS_FILE" >&2
    return 2
  fi
}

# health_status <url> — one GET, and the status word the answer carries; "no answer" when nothing comes back. It informs
# the operator reading the log and never refuses: another service's health says nothing about this host or this card.
health_status(){ local body word
  body=$(curl -s --max-time 5 --max-filesize 65536 "$1" 2>/dev/null) || body=""
  [ -n "$body" ] || { echo "no answer"; return 0; }
  # the first "status" field in the body, printable characters only, at most 40 of them: the answer is another service's
  # text and lands on the operator's terminal, so control characters never pass
  word=$(printf '%s' "$body" | tr -d '[:cntrl:]' | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' | cut -c1-40)
  [ -n "$word" ] && echo "$word" || echo "answered, no status field"
}

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
  if [ -n "$A770B_HEALTH_URL" ]; then printf '  %-30s %s\n' "health $A770B_HEALTH_URL" "$(health_status "$A770B_HEALTH_URL") (informational, never a refusal)"; fi
  case " $A770B_FRAMEWORK_PORTS " in *" $A770B_PORT "*) printf '⛔ %-30s %s\n' "port $A770B_PORT" "is a protected port"; ok=0;; esac
  [ "$ok" = 1 ] || { echo "⛔ budget refused — nothing started"; return 1; }
  echo "✅ budget ok"
}
