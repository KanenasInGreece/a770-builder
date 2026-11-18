#!/usr/bin/env bash
# guard.sh — shared guards for the builder harness (sourced, never executed). Needs env.sh sourced first.
# Canonical refusal of protected checkouts, worktree validation, verified pids, run lock, safe host-side git,
# VRAM readings that refuse to fail open, the built-in budget gate.

# guard_path_policy <path> — THE PATH POLICY, and its only home: the trees this harness must never remove, replace
# or write over. It judges a path ALONE — whether or not it exists, and whether or not it is a git tree — which is
# what lets it run BEFORE the destructive operation rather than after it. That ordering is the point: run_suite.sh's
# --fresh removal and both of its seat exports delete the seat path outright, and until this function existed the
# only check was guard_worktree, which cannot run that early (it also proves the path is a self-contained clone —
# false by construction for a seat about to be created, and for the kit seat, which is an export, not a clone).
# guard_worktree now calls this and keeps its clone checks on top, so both callers enforce one rule.
# Echoes the resolved path on success; exits 2 on any refusal.
# _paths_overlap <canon_a> <canon_b> — true when two canonical paths are identical or either is an ancestor of the other.
_paths_overlap(){
  local a="$1" b="$2"
  [ "$a" = "$b" ] && return 0
  [ "$a" = "/" ] || [ "$b" = "/" ] && return 0
  case "$a" in "$b"/*) return 0 ;; esac
  case "$b" in "$a"/*) return 0 ;; esac
  return 1
}

# _path_is_inside <canon_path> <canon_parent> — true when canon_path equals or is inside canon_parent.
_path_is_inside(){
  local p="$1" parent="$2"
  [ "$p" = "$parent" ] && return 0
  [ "$parent" = "/" ] && return 0
  case "$p" in "$parent"/*) return 0 ;; esac
  return 1
}

guard_path_policy(){
  local p="$1" real r r_real prot prot_real
  [ -n "$A770B_REFUSE" ] || { echo "⛔ A770B_REFUSE is empty — list the live checkouts this seat must never touch (colon-separated) in builder.env before running" >&2; exit 2; }
  [ -n "$p" ] || { echo "⛔ empty path" >&2; exit 2; }
  # -m, not -e: a path that does not exist yet is still judged (every component that DOES exist is still resolved
  # through its symlinks, so a link planted where the seat will be cannot point the removal somewhere else)
  real=$(realpath -m -- "$p" 2>/dev/null) || { echo "⛔ cannot resolve path: $p" >&2; exit 2; }
  local IFS=':'; for r in $A770B_REFUSE; do
    [ -n "$r" ] || continue
    r_real=$(realpath -m -- "$r" 2>/dev/null) || continue
    if _path_is_inside "$real" "$r_real"; then
      echo "⛔ refusing: $p resolves to a protected live checkout ($r_real)" >&2; exit 2
    elif _path_is_inside "$r_real" "$real"; then
      echo "⛔ refusing: $p is an ancestor of a protected live checkout ($r_real)" >&2; exit 2
    fi
  done; unset IFS
  # the agent homes, the operator's configuration and keys, and the harness's own project directory.
  # Canonical overlap in either direction is refused so an ancestor (e.g. $HOME or /) can never wipe a protected tree.
  for prot in \
    "$HOME/.claude" "$HOME/.grok" "$HOME/.codex" "$HOME/.gemini" \
    "$HOME/.config" "$HOME/.ssh" "$A770B_PROJECT" \
    "${A770B_DATA:+$A770B_DATA/logs}" "${A770B_DATA:+$A770B_DATA/results}" \
    "${A770B_DATA:+$A770B_DATA/hidden}" "${A770B_DATA:+$A770B_DATA/seat-cache}" \
    "${A770B_HIDDEN_ROOT:-}"
  do
    [ -n "$prot" ] || continue
    prot_real=$(realpath -m -- "$prot" 2>/dev/null) || continue
    if _path_is_inside "$real" "$prot_real"; then
      echo "⛔ refusing: $real is an agent home, operator ground, harness state or the harness itself ($prot_real)" >&2; exit 2
    elif _path_is_inside "$prot_real" "$real"; then
      echo "⛔ refusing: $real is an ancestor of an agent home, operator ground, harness state or the harness itself ($prot_real)" >&2; exit 2
    fi
  done
  printf '%s\n' "$real"
}

# guard_worktree <path> — echoes the canonical path on success; exits 2 on any refusal.
guard_worktree(){
  local wt="$1" real top
  [ -n "$A770B_REFUSE" ] || { echo "⛔ A770B_REFUSE is empty — list the live checkouts this seat must never touch (colon-separated) in builder.env before running" >&2; exit 2; }
  [ -n "$wt" ] || { echo "⛔ empty worktree path" >&2; exit 2; }
  real=$(realpath -e -- "$wt" 2>/dev/null) || { echo "⛔ worktree does not exist: $wt" >&2; exit 2; }
  # the path policy first, in its one home; everything below it is the clone check this function adds on top
  guard_path_policy "$real" >/dev/null
  [ ! -L "$real/.git" ] || { echo "⛔ $real/.git is a symlink — the seat's repository metadata must be its own" >&2; exit 2; }
  [ -d "$real/.git" ] || { echo "⛔ $real is not a self-contained git clone (needs a .git DIRECTORY; linked worktrees of a live checkout are refused)" >&2; exit 2; }
  top=$(git -c core.fsmonitor= -c core.hooksPath=/dev/null -C "$real" rev-parse --show-toplevel 2>/dev/null | xargs -r realpath -e 2>/dev/null || true)
  [ -n "$top" ] || { echo "⛔ $real is not inside a git working tree" >&2; exit 2; }
  gitdir=$(git -c core.fsmonitor= -c core.hooksPath=/dev/null -C "$real" rev-parse --absolute-git-dir 2>/dev/null | xargs -r realpath -e 2>/dev/null || true)
  [ "$gitdir" = "$real/.git" ] || { echo "⛔ $real's repository metadata resolves to $gitdir, not to its own .git" >&2; exit 2; }
  [ "$top" = "$real" ] || { echo "⛔ $real is a subdirectory of a worktree ($top); pass the worktree root" >&2; exit 2; }
  # $top needs no separate policy pass: the line above has just proved it is $real, which the policy already judged.
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

# run_pid_alive <pidfile> — prints the pid only if it is alive AND is this harness's run leader.
# With a caller fence the leader's comm is timeout. With no clock it is the sandbox (bash sandbox_run.sh)
# or unconfined opencode. The cmdline must name sandbox_run.sh or opencode run. A stale or foreign pid is refused.
run_pid_alive(){
  local pf="$1" pid comm cmdline
  [ -f "$pf" ] || return 1
  pid=$(cat "$pf" 2>/dev/null); [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmdline" in *sandbox_run.sh*|*"opencode run"*) : ;; *) return 1;; esac
  case "$comm" in timeout|bash|opencode|sandbox_run.sh) : ;; *) return 1;; esac
  printf '%s\n' "$pid"
}

# Lock ordering (single order across the entire harness): card_lock (FD 9) MUST be acquired before seat_lock (FD 7).
# Inherited locks are trusted only when A770B_*_LOCK_PATH matches the canonical lock path, FD 9/7 points to the same
# (st_dev, st_ino) as the lock file, and a non-blocking flock probe confirms the lock is actively held on that inode.
_inherited_lock_valid(){
  local fd="$1" lock="$2" env_path="${3:-}"
  [ -n "$env_path" ] || return 1
  python3 -c '
import fcntl, os, sys
fd = int(sys.argv[1])
target = os.path.realpath(sys.argv[2])
env_path = os.path.realpath(sys.argv[3])
if target != env_path:
    sys.exit(1)
try:
    st_fd = os.fstat(fd)
    st_target = os.stat(target)
except OSError:
    sys.exit(1)
if (st_fd.st_dev, st_fd.st_ino) != (st_target.st_dev, st_target.st_ino):
    sys.exit(1)
probe = os.open(target, os.O_RDONLY)
try:
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        fcntl.flock(probe, fcntl.LOCK_UN)
        sys.exit(1)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
    sys.exit(0)
finally:
    os.close(probe)
' "$fd" "$lock" "$env_path" 2>/dev/null
}

# card_lock — take the exclusive card lock on FD 9 for the rest of this shell (inherits across phases in the same run).
card_lock(){
  local lock
  mkdir -p "$A770B_DATA/logs"
  lock=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$A770B_DATA/logs/local-build.lock")
  if _inherited_lock_valid 9 "$lock" "${A770B_CARD_LOCK_PATH:-}"; then
    export A770B_CARD_LOCK_PATH="$lock"
    return 0
  fi
  if [ -n "${A770B_SEAT_LOCK_PATH:-}" ] && _inherited_lock_valid 7 "$A770B_SEAT_LOCK_PATH" "$A770B_SEAT_LOCK_PATH"; then
    echo "⛔ lock order violation: seat lock ($A770B_SEAT_LOCK_PATH) is held without card lock — acquire card_lock before seat_lock" >&2
    exit 2
  fi
  exec 9>"$lock"
  flock -n 9 || { sleep 0.3; flock -n 9; } || { echo "⛔ another local-build run holds the card lock ($lock) — one run at a time on this card" >&2; exit 2; }
  export A770B_CARD_LOCK_PATH="$lock"
}
run_lock(){ card_lock "$@"; }

# seat_lock <seat-dir> — take the exclusive seat lock on FD 7 after resolving canonical seat path (always after card_lock when both are needed).
seat_lock(){
  local raw="${1:-}" canon lock
  [ -n "$raw" ] || { echo "⛔ seat_lock requires a seat path" >&2; exit 2; }
  mkdir -p "$A770B_DATA/logs/seat-locks"
  IFS=$'\t' read -r canon lock < <(python3 -c '
import hashlib, os, sys
canon = os.path.realpath(sys.argv[1])
h = hashlib.sha256(canon.encode("utf-8")).hexdigest()
print(canon + "\t" + os.path.realpath(os.path.join(sys.argv[2], "logs", "seat-locks", h + ".lock")))
' "$raw" "$A770B_DATA")
  if _inherited_lock_valid 7 "$lock" "${A770B_SEAT_LOCK_PATH:-}"; then
    export A770B_SEAT_LOCK_PATH="$lock"
    return 0
  fi
  if [ -n "${A770B_SEAT_LOCK_PATH:-}" ] && _inherited_lock_valid 7 "$A770B_SEAT_LOCK_PATH" "$A770B_SEAT_LOCK_PATH"; then
    echo "⛔ another seat lock is already held in this process ($A770B_SEAT_LOCK_PATH) — cannot also lock $canon" >&2
    exit 2
  fi
  exec 7>"$lock"
  flock -n 7 || { sleep 0.3; flock -n 7; } || { echo "⛔ another harness run holds the seat lock for $canon ($lock)" >&2; exit 2; }
  export A770B_SEAT_LOCK_PATH="$lock"
}

# Seat provenance (stored outside the writable seat under $A770B_DATA/logs/disposable-seats/ and paired with a
# per-instance nonce inside $seat/.git/a770-seat-nonce so inode reuse on a recreated directory is rejected).
seat_provenance_file(){
  python3 -c '
import hashlib, os, sys
canon = os.path.realpath(sys.argv[1])
print(os.path.join(sys.argv[2], "logs", "disposable-seats", hashlib.sha256(canon.encode("utf-8")).hexdigest() + ".json"))
' "$1" "$A770B_DATA"
}

clear_seat_provenance(){
  local seat="${1:-}"
  [ -n "$seat" ] || return 0
  python3 -c '
import hashlib, os, sys
seat, data_dir = sys.argv[1], sys.argv[2]
canon = os.path.realpath(seat)
prov = os.path.join(data_dir, "logs", "disposable-seats", hashlib.sha256(canon.encode("utf-8")).hexdigest() + ".json")
try:
    os.unlink(prov)
except OSError:
    pass
' "$seat" "$A770B_DATA"
}

record_seat_provenance(){
  local seat="$1"
  [ -n "$seat" ] || return 1
  python3 -c '
import hashlib, json, os, secrets, sys, tempfile, time
seat, data_dir = sys.argv[1], sys.argv[2]
if os.path.islink(seat):
    sys.exit(1)
canon = os.path.realpath(seat)
st = os.stat(canon)
if not os.path.isdir(canon):
    sys.exit(1)
git_dir = os.path.join(canon, ".git")
if os.path.islink(git_dir) or (os.path.exists(git_dir) and not os.path.isdir(git_dir)):
    sys.exit(1)
os.makedirs(git_dir, exist_ok=True)
nonce_path = os.path.join(git_dir, "a770-seat-nonce")
if os.path.islink(nonce_path):
    sys.exit(1)
nonce = secrets.token_hex(16)
fd_n, tmp_n = tempfile.mkstemp(prefix=".seat-nonce-", dir=git_dir)
try:
    with os.fdopen(fd_n, "w", encoding="utf-8") as fn:
        fn.write(nonce + "\n")
    os.replace(tmp_n, nonce_path)
except Exception:
    try: os.unlink(tmp_n)
    except OSError: pass
    raise
dest_dir = os.path.join(data_dir, "logs", "disposable-seats")
os.makedirs(dest_dir, exist_ok=True)
dest = os.path.join(dest_dir, hashlib.sha256(canon.encode("utf-8")).hexdigest() + ".json")
if os.path.islink(dest):
    os.unlink(dest)
payload = {
    "owner": "a770-builder/run_suite",
    "seat": canon,
    "dev": st.st_dev,
    "ino": st.st_ino,
    "nonce": nonce,
    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
fd, tmp = tempfile.mkstemp(prefix=".seat-prov-", dir=dest_dir)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dest)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
' "$seat" "$A770B_DATA"
}

verify_seat_provenance(){
  local seat="$1"
  [ -n "$seat" ] || return 1
  [ ! -L "$seat" ] || return 1
  python3 -c '
import hashlib, hmac, json, os, sys
seat, data_dir = sys.argv[1], sys.argv[2]
if os.path.islink(seat) or not os.path.isdir(seat):
    sys.exit(1)
canon = os.path.realpath(seat)
st = os.stat(canon)
git_dir = os.path.join(canon, ".git")
if os.path.islink(git_dir) or not os.path.isdir(git_dir):
    sys.exit(1)
nonce_path = os.path.join(git_dir, "a770-seat-nonce")
if os.path.islink(nonce_path) or not os.path.isfile(nonce_path):
    sys.exit(1)
try:
    with open(nonce_path, encoding="utf-8") as fn:
        seat_nonce = fn.read().strip()
except Exception:
    sys.exit(1)
prov = os.path.join(data_dir, "logs", "disposable-seats", hashlib.sha256(canon.encode("utf-8")).hexdigest() + ".json")
if os.path.islink(prov) or not os.path.isfile(prov):
    sys.exit(1)
try:
    with open(prov, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(1)
ledger_nonce = data.get("nonce") if isinstance(data, dict) else None
ok = (
    isinstance(data, dict)
    and data.get("owner") == "a770-builder/run_suite"
    and data.get("seat") == canon
    and data.get("dev") == st.st_dev
    and data.get("ino") == st.st_ino
    and isinstance(ledger_nonce, str)
    and len(ledger_nonce) >= 16
    and bool(seat_nonce)
    and hmac.compare_digest(seat_nonce, ledger_nonce)
)
sys.exit(0 if ok else 1)
' "$seat" "$A770B_DATA"
}

# VRAM on the builder card via nvtop. Refuses to fail open: without nvtop the caller must have set A770B_ALLOW_NO_NVTOP=1.
# -1 (not just when nvtop is absent) when the number of matching devices is not exactly one or A770B_GPU_MATCH is empty — an ambiguous match never picks d[0].
_nvtop_field(){
  [ -n "${A770B_GPU_MATCH:-}" ] || { echo -1; return 0; }
  nvtop -s 2>/dev/null | python3 -c "import sys,json; m=sys.argv[1]; f=sys.argv[2]; d=[x for x in json.load(sys.stdin) if m in x.get('device_name','')]; print(int(d[0][f]) if len(d) == 1 else -1)" "$A770B_GPU_MATCH" "$1" 2>/dev/null || echo -1
}
# gpu_match_count — the number of nvtop devices whose device_name contains A770B_GPU_MATCH; -1 without readings (nvtop absent, match empty, or JSON unreadable).
gpu_match_count(){
  [ -n "${A770B_GPU_MATCH:-}" ] || { echo -1; return 0; }
  nvtop -s 2>/dev/null | python3 -c "import sys,json; m=sys.argv[1]; print(len([x for x in json.load(sys.stdin) if m in x.get('device_name','')]))" "$A770B_GPU_MATCH" 2>/dev/null || echo -1
}
require_vram_readings(){
  [ -n "${A770B_GPU_MATCH:-}" ] && command -v nvtop >/dev/null && [ "$(_nvtop_field mem_total)" -gt 0 ] && return 0
  [ "$A770B_ALLOW_NO_NVTOP" = 1 ] && { echo "⚠ no VRAM readings (nvtop absent or card '$A770B_GPU_MATCH' not found) — A770B_ALLOW_NO_NVTOP=1, the VRAM cap is NOT enforced" >&2; return 0; }
  echo "⛔ no VRAM readings: install nvtop and make A770B_GPU_MATCH match the builder card in 'nvtop -s' (or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop)" >&2; return 1
}
gpu_used_gib(){
  local u
  u=$(_nvtop_field mem_used)
  case "$u" in
    ''|*[!0-9]*) echo "unavailable"; return 1 ;;
    *) python3 -c "print($u/2**30)" ;;
  esac
}
gpu_free_mb(){
  local f
  f=$(_nvtop_field mem_free)
  case "$f" in
    ''|*[!0-9]*) echo "unavailable"; return 1 ;;
    *) echo $((f/1048576)) ;;
  esac
}

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

# project_llama_ids — containers in this compose project whose service is llama, including exited ones
# (`docker ps -aq`). The four framework names are never selected, even when they carry the same project label.
# A container whose compose files are another worktree is still this project, so it is listed here;
# compose ps filtered by this checkout's files does not see it. A caller that needs a live container
# skips an id when _pid_if_running prints nothing.
project_llama_ids(){
  command -v "$A770B_DOCKER" >/dev/null 2>&1 || return 0
  local id name
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    name=$("$A770B_DOCKER" inspect -f '{{.Name}}' "$id" 2>/dev/null || true)
    name=${name#/}
    case "$name" in
      vllm-embed|vllm-rerank|neo4j-memory|postgres-vector) continue ;;
    esac
    printf '%s\n' "$id"
  done < <("$A770B_DOCKER" ps -aq \
      --filter "label=com.docker.compose.project=${A770B_COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=llama" 2>/dev/null || true)
  return 0
}

# _pid_if_running <id> — the host pid, or nothing when the container is stopped (pid 0) or inspect fails.
_pid_if_running(){
  local p
  p=$("$A770B_DOCKER" inspect -f '{{.State.Pid}}' "$1" 2>/dev/null || true)
  if [ -n "$p" ] && [ "$p" != 0 ]; then printf '%s\n' "$p"; fi
}

# compose_project_pids — the host pids of the compose project's running containers, one per line (empty when none,
# when docker is absent, or when the envelope does not exist). A container shares the host PID namespace, so its
# llama-server shows up in pgrep; these pids are what budget_gate subtracts to tell its OWN container from a foreign
# host server (pgrep and a pidfile alone lie for a container). The file set is the envelope plus
# logs/compose-argv.override.json when that file exists, and any other project llama container the file set misses.
compose_project_pids(){
  command -v "$A770B_DOCKER" >/dev/null 2>&1 || return 0
  [ -f "$A770B_COMPOSE_FILE" ] || return 0
  local id seen=" " ov="$A770B_DATA/logs/compose-argv.override.json"
  local -a files=(-f "$A770B_COMPOSE_FILE")
  [ -f "$ov" ] && files+=(-f "$ov")
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    seen="$seen$id "
    _pid_if_running "$id"
  done < <("$A770B_DOCKER" compose -p "$A770B_COMPOSE_PROJECT" "${files[@]}" ps -q 2>/dev/null || true)
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    case "$seen" in *" $id "*) continue ;; esac
    _pid_if_running "$id"
  done < <(project_llama_ids)
  return 0
}
# compose_project_running — true when the project has at least one running container.
compose_project_running(){
  [ -n "$(compose_project_pids)" ]
}

# budget_gate — the built-in gate: host RAM, no stray llama-server, VRAM on the builder card, optional health URL, port.
# Replaced entirely by A770B_BUDGET_GATE when that is set.
budget_gate(){
  if [ -n "$A770B_BUDGET_GATE" ]; then bash "$A770B_BUDGET_GATE" check; return $?; fi
  local ok=1 avail free
  require_vram_readings || return 1
  avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  if [ "$avail" -ge "$A770B_MIN_AVAIL_MB" ]; then printf '  %-30s %s\n' "host MemAvailable (MB)" "$avail ≥ $A770B_MIN_AVAIL_MB"; else printf '⛔ %-30s %s\n' "host MemAvailable (MB)" "$avail < $A770B_MIN_AVAIL_MB"; ok=0; fi
  if [ "$A770B_SERVE" = compose ]; then
    # a llama-server that is not one of THIS project's containers is foreign; this project's own container is ours
    local ours foreign=0 p
    ours=" $(compose_project_pids | tr '\n' ' ') "
    for p in $(pgrep -x llama-server 2>/dev/null || true); do
      case "$ours" in *" $p "*) ;; *) foreign=1;; esac
    done
    if [ "$foreign" = 1 ]; then printf '⛔ %-30s %s\n' "other llama-server" "running outside this compose project — one GPU process per card"; ok=0; else printf '  %-30s %s\n' "other llama-server" "none"; fi
  elif pgrep -x llama-server >/dev/null && ! llama_pid_alive "$A770B_DATA/logs/llamacpp-a770.pid" >/dev/null; then printf '⛔ %-30s %s\n' "other llama-server" "running outside this harness — one GPU process per card"; ok=0; else printf '  %-30s %s\n' "other llama-server" "none"; fi
  if free=$(gpu_free_mb); then
    if [ "$free" -ge "$A770B_MIN_VRAM_MB" ]; then printf '  %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "$free ≥ $A770B_MIN_VRAM_MB"; else printf '⛔ %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "$free < $A770B_MIN_VRAM_MB"; ok=0; fi
  elif [ "${A770B_ALLOW_NO_NVTOP:-0}" = 1 ]; then
    printf '  %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "unavailable (A770B_ALLOW_NO_NVTOP=1)"
  else
    printf '⛔ %-30s %s\n' "VRAM free on $A770B_GPU_MATCH (MB)" "unavailable"; ok=0
  fi
  if [ -n "$A770B_HEALTH_URL" ]; then printf '  %-30s %s\n' "health $A770B_HEALTH_URL" "$(health_status "$A770B_HEALTH_URL") (informational, never a refusal)"; fi
  case " $A770B_FRAMEWORK_PORTS " in *" $A770B_PORT "*) printf '⛔ %-30s %s\n' "port $A770B_PORT" "is a protected port"; ok=0;; esac
  [ "$ok" = 1 ] || { echo "⛔ budget refused — nothing started"; return 1; }
  echo "✅ budget ok"
}
