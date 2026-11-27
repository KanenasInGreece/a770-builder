#!/usr/bin/env bash
# sandbox_run.sh — run a command inside a bubblewrap boundary that sees ONLY the seat worktree, with NO network
# except a loopback route to the model server.
#   sandbox_run.sh <worktree> <profile-config.jsonc> -- <command…>
# Inside: /usr,/etc read-only; a private empty $HOME on tmpfs with exactly these mounts:
#   <worktree>                          read-write   (the only writable tree) — EXCEPT its whole .git directory,
#                                                    mounted read-only: the model reads history and status but can plant
#                                                    nothing there, neither config-driven code for host-side git nor a
#                                                    file that would survive the reset unseen (git clean never enters .git)
#   <opencode binary dir>               read-only
#   <profile-config.jsonc>              read-only at ~/.config/opencode/local-profile.jsonc (the ONLY opencode config inside;
#                                                    the global config is never mounted — it may hold provider keys)
#   $A770B_OPENCODE_MODELS              read-only    (so no models.dev fetch is needed; fetch disabled anyway)
#   ~/.local/share/opencode             PRIVATE tmpfs (fresh session db per run; auth.json never enters)
#   uv + ~/.local/share/uv/python       read-only    (managed pythons only — never the whole uv dir, it holds credentials)
#   $A770B_UV_CACHE                     read-ONLY    (pre-warmed by harness/warm_cache.sh; a run cannot poison later runs)
# NETWORK: --unshare-net. The sandbox has loopback only; a bridge forwards 127.0.0.1:$A770B_PORT inside to the host's
# model server through a unix socket, so the model reaches the server and nothing else — no LAN, no internet, no other
# loopback service on the host. PID/UTS/IPC unshared; the sandbox dies with its parent. When the host recorded
# SGLang, vLLM or Strata, the host usage tap forwards the response and records token counts in
# $A770B_DATA/logs/usage.json. llama.cpp keeps the socat bridge, and
# that count stays in the container log. Bridge errors go to $A770B_DATA/logs/llama-bridge.log, so a closed
# connection never lands in the transcript. A770B_NO_BRIDGE=1 runs without any bridge (verify: tests only, no model, no key).
# The worktree is judged by guard_path_policy before any existence check and before it is bind-mounted.
# This script stays in the caller's cgroup. Only bwrap and its descendants move into a
# scope under a770b-sandbox.slice. The scope has no memory, CPU, or task limit. Parent
# death is armed on each process because the scope is outside the caller's cgroup.
set -euo pipefail
# Caller lock fds. Close one only when it is open: closing an absent fd prints
# an error and would hide a later path refusal.
if ( : >&7 ) 2>/dev/null; then exec 7>&-; fi
if ( : >&9 ) 2>/dev/null; then exec 9>&-; fi
# The death signal is not inherited across fork. Re-exec once under setpriv.
# The guard is this pid and is unset after the check, so a nested call arms too.
# A770B_SPAWNER_PID is the leader's pid, passed before this process started.
# PPID read after that leader died is the adopter, so a gone spawner is still
# the parent to arm against. A live spawner that is not our parent was inherited
# from an ancestor; arming against it aborts a healthy run. A direct call passes
# nothing and uses PPID. The pid is unset on every path below, including when
# setpriv is not re-executed, so a later command cannot inherit it.
_arm_against_spawner() {
  local pid="${1:-}" stat state
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  if [ "$pid" = "$PPID" ]; then
    return 0
  fi
  if [ ! -d "/proc/$pid" ]; then
    return 0
  fi
  # The process can exit after the directory check. A gone spawner is the
  # parent to arm against. A live one whose stat cannot be read is not.
  stat=$(cat "/proc/$pid/stat" 2>/dev/null) || {
    if [ ! -d "/proc/$pid" ]; then
      return 0
    fi
    return 1
  }
  state=${stat##*)}
  state=${state# }
  state=${state%% *}
  [ "$state" = "Z" ] || [ "$state" = "X" ]
}
if [ "${A770B_PDEATH_ARMED_PID:-}" != "$$" ] && command -v setpriv >/dev/null 2>&1; then
  if _arm_against_spawner "${A770B_SPAWNER_PID:-}"; then
    export A770B_PDEATH_PARENT="${A770B_SPAWNER_PID}"
  else
    export A770B_PDEATH_PARENT="${PPID}"
  fi
  export A770B_PDEATH_ARMED_PID="$$"
  exec setpriv --pdeathsig KILL -- /usr/bin/bash "$0" "$@"
fi
# This process was not re-executed under setpriv. Consume a spawner pid the
# branch above did not already copy, then drop the variable.
if [ -z "${A770B_PDEATH_PARENT:-}" ] && _arm_against_spawner "${A770B_SPAWNER_PID:-}"; then
  export A770B_PDEATH_PARENT="${A770B_SPAWNER_PID}"
fi
unset A770B_SPAWNER_PID || true
if [ -n "${A770B_PDEATH_PARENT:-}" ] && [ "$PPID" != "$A770B_PDEATH_PARENT" ]; then
  echo "⛔ sandbox: parent changed during arming" >&2
  exit 137
fi
unset A770B_PDEATH_PARENT A770B_PDEATH_ARMED_PID || true
. "$(dirname "$0")/env.sh"
. "$(dirname "$0")/guard.sh"
WT="${1:?worktree}"; CFG="${2:?profile config}"; shift 2; [ "${1:-}" = "--" ] && shift
WT=$(guard_path_policy "$WT")
[ -d "$WT" ] || { echo "⛔ sandbox: worktree missing $WT" >&2; exit 2; }
[ -f "$CFG" ] || { echo "⛔ sandbox: config missing $CFG" >&2; exit 2; }
for t in bwrap socat uv setpriv; do command -v "$t" >/dev/null || { echo "⛔ sandbox: $t not installed" >&2; exit 2; }; done
A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1 || exit 2
OC_BIN=$(a770b_opencode_bin)
UV=$(readlink -f "$(command -v uv)")
[ -d "$A770B_UV_CACHE" ] || { echo "⛔ sandbox: uv cache $A770B_UV_CACHE missing — run harness/warm_cache.sh once (the sandbox has no network)" >&2; exit 2; }
SOCK="$A770B_DATA/logs/llama-bridge.sock"; BRIDGE=""
if [ "${A770B_NO_BRIDGE:-0}" != 1 ]; then
  # Host side of the bridge. setpriv execs in place, so the bridge's parent is
  # this shell. SIGKILL skips the EXIT trap; the death signal does not.
  rm -f "$SOCK"
  case "${A770B_SERVED_ENGINE:-}" in
    sglang|vllm|strata)
      setpriv --pdeathsig KILL -- bash -c 'if [ "$PPID" != "$1" ]; then echo "⛔ sandbox: parent changed during arming" >&2; exit 137; fi; shift; exec "$@"' _ "$$" \
        python3 -I -B "$A770B_PROJECT/harness/usage_tap.py" --listen "$SOCK" --upstream "$A770B_HOST:$A770B_PORT" --out "$A770B_DATA/logs/usage.json" \
        7>&- 9>&- >>"$A770B_DATA/logs/llama-bridge.log" 2>&1 & BRIDGE=$!
      ;;
    *)
      setpriv --pdeathsig KILL -- bash -c 'if [ "$PPID" != "$1" ]; then echo "⛔ sandbox: parent changed during arming" >&2; exit 137; fi; shift; exec "$@"' _ "$$" \
        socat "UNIX-LISTEN:$SOCK,fork,unlink-early" "TCP:$A770B_HOST:$A770B_PORT" \
        7>&- 9>&- 2>>"$A770B_DATA/logs/llama-bridge.log" & BRIDGE=$!
      ;;
  esac
  trap 'kill $BRIDGE 2>/dev/null; rm -f "$SOCK"' EXIT
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK" ] && break; sleep 0.2; done
  if [ ! -S "$SOCK" ]; then echo "⛔ sandbox: bridge socket did not appear" >&2; exit 2; fi
fi
ARGS=(
  --ro-bind /usr /usr --ro-bind /etc /etc
  --symlink usr/lib64 /lib64 --symlink usr/lib /lib --symlink usr/bin /bin --symlink usr/sbin /sbin
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run
  --tmpfs "$HOME"
  --bind "$WT" "$WT"
  --ro-bind "$WT/.git" "$WT/.git"
  --ro-bind "$OC_BIN" "$OC_BIN"
  --ro-bind "$CFG" "$HOME/.config/opencode/local-profile.jsonc"
  --dir "$HOME/.local/share/opencode" --dir "$HOME/.cache/opencode"
  --ro-bind "$UV" "$HOME/.local/bin/uv"
  --ro-bind "$A770B_UV_CACHE" "$HOME/.cache/uv-warm"
  --unshare-pid --unshare-uts --unshare-ipc --unshare-net --die-with-parent --new-session
  --clearenv
  --setenv HOME "$HOME" --setenv USER "$USER" --setenv TERM dumb --setenv LANG C.UTF-8
  --setenv PATH "$HOME/.local/bin:$OC_BIN:/usr/local/bin:/usr/bin:/bin"
  --setenv OPENCODE_CONFIG "$HOME/.config/opencode/local-profile.jsonc" --setenv OPENCODE_DISABLE_MODELS_FETCH 1 --setenv OPENCODE_DISABLE_AUTOUPDATE 1
  --setenv UV_CACHE_DIR "$HOME/.cache/uv" --setenv UV_PYTHON_INSTALL_DIR "$HOME/.local/share/uv/python" --setenv UV_OFFLINE 1 --setenv UV_NO_SYNC 1
  --setenv A770B_PORT "$A770B_PORT"
  --chdir "$WT"
)
[ -n "$BRIDGE" ] && ARGS+=(--bind "$SOCK" /run/llama.sock)
[ -f "$A770B_OPENCODE_MODELS" ] && ARGS+=(--ro-bind "$A770B_OPENCODE_MODELS" "$A770B_OPENCODE_MODELS")
[ -d "$HOME/.local/share/uv/python" ] && ARGS+=(--ro-bind "$HOME/.local/share/uv/python" "$HOME/.local/share/uv/python")
# verify (no bridge) kills the whole scope. build continues so the offending
# process is the one that dies and the command can still report the failure.
policy=continue
[ "${A770B_NO_BRIDGE:-0}" = 1 ] && policy=kill
unit="a770b-sbx-$$-$RANDOM.scope"
probe="a770b-sbx-probe-$$-$RANDOM.scope"
isolation=scope
# --collect is only on the probe. The real unit stays readable after oom-kill.
# No bus, or a refusal: still arm bwrap and still raise oom_score_adj.
# The bound covers this probe and its reset only. The model command is not wrapped.
if ! /usr/bin/timeout -k 1 5 systemd-run --user --scope --quiet --collect \
     --expand-environment=no --unit="$probe" --slice=a770b-sandbox.slice \
     -p OOMPolicy=continue -- /bin/true 7>&- 9>&-
then
  echo "⚠ sandbox: systemd-run refused; isolation=none" >&2
  isolation=none
  unit=""
else
  /usr/bin/timeout -k 1 5 systemctl --user reset-failed "$probe" 2>/dev/null || true
fi
if [ -n "${A770B_SANDBOX_RECORD_FILE:-}" ]; then
  python3 -I -B - "$A770B_SANDBOX_RECORD_FILE" "$isolation" "$unit" "$policy" <<'PY'
import json, os, sys
path, isolation, unit, policy = sys.argv[1:5]
body = {"isolation": isolation, "unit": unit or None, "policy": policy, "oom": "unknown"}
flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
fd = os.open(path, flags, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(body, fh)
    fh.write("\n")
PY
fi
export A770B_SANDBOX_ISOLATION="$isolation" A770B_SANDBOX_UNIT="$unit" A770B_SANDBOX_POLICY="$policy"
# $1 is the parent pid captured before this bash replaced setpriv. oom_score_adj
# is victim preference, not a limit. A failed write warns and the command runs.
SCOPE_WRAP='if [ "$PPID" != "$1" ]; then echo "⛔ sandbox: parent changed during arming" >&2; exit 137; fi; shift; if ! printf 800 >/proc/self/oom_score_adj; then echo "⚠ sandbox: could not raise oom_score_adj" >&2; fi; exec "$@"'
# bash -c cannot re-exec $0 (it is "_"). This interpreter arms, checks the
# parent, then execs the inner script. The signal is kept across that exec.
ARM_PY='import ctypes, os, sys
parent = os.getppid()
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, 9, 0, 0, 0) != 0:
    sys.stderr.write("sandbox: prctl failed\n")
    sys.exit(137)
if os.getppid() != parent:
    sys.stderr.write("sandbox: parent changed during arming\n")
    sys.exit(137)
script = sys.argv[1]
os.execvp("bash", ["bash", "-c", script, *sys.argv[2:]])
'
INNER='mkdir -p "$HOME/.cache/uv" && cp -a "$HOME/.cache/uv-warm/." "$HOME/.cache/uv/" 2>/dev/null; [ -S /run/llama.sock ] && { setpriv --pdeathsig KILL -- socat "TCP-LISTEN:$A770B_PORT,bind=127.0.0.1,fork,reuseaddr" UNIX-CONNECT:/run/llama.sock 2>/dev/null & }; exec "$@"'
# 7>&- 9>&-: caller lock descriptors must not cross into the sandbox.
# rc is captured so a killed scope still returns the command status under set -e.
_run_scoped() {
  local rc=0
  if [ "$isolation" = scope ]; then
    setpriv --pdeathsig KILL -- systemd-run --user --scope --quiet --expand-environment=no \
      --unit="$unit" --slice=a770b-sandbox.slice -p "OOMPolicy=$policy" -- \
      bash -c "$SCOPE_WRAP" _ "$$" bwrap "$@" 7>&- 9>&- || rc=$?
  else
    setpriv --pdeathsig KILL -- bash -c "$SCOPE_WRAP" _ "$$" bwrap "$@" 7>&- 9>&- || rc=$?
  fi
  return "$rc"
}
if [ -n "${A770B_SANDBOX_ENTERED_FILE:-}" ]; then
  # $$ is this shell, captured before Python starts. getppid() after startup
  # can already be the user manager if this process was killed in between.
  python3 -I -B - "$$" "$A770B_SANDBOX_ENTERED_FILE" "$HOME" "$A770B_PORT" "${ARGS[@]}" -- "$@" 7>&- 9>&- <<'PY'
import ctypes, os, subprocess, sys
expected_parent = int(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
# PR_SET_PDEATHSIG is 1 and SIGKILL is 9. The user manager is a subreaper
# here, so a later getppid() of pid 1 is the wrong check.
if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != expected_parent:
    sys.stderr.write("sandbox: parent changed during arming\n")
    sys.exit(137)
entered_file = sys.argv[2]
sb_home = sys.argv[3]
sb_port = sys.argv[4]
rest = sys.argv[5:]
sep = rest.index("--")
bwrap_args = rest[:sep]
cmd = rest[sep + 1:]
r_fd, w_fd = os.pipe()
os.set_inheritable(w_fd, True)
inner = (
    'mkdir -p "$HOME/.cache/uv" && cp -a "$HOME/.cache/uv-warm/." "$HOME/.cache/uv/" 2>/dev/null; '
    '[ -S /run/llama.sock ] && { setpriv --pdeathsig KILL -- socat "TCP-LISTEN:' + sb_port + ',bind=127.0.0.1,fork,reuseaddr" UNIX-CONNECT:/run/llama.sock 2>/dev/null & }; '
    f'printf "entered\\n" >&{w_fd} 2>/dev/null || true; exec {w_fd}>&-; '
    'exec "$@"'
)
arm = (
    "import ctypes, os, sys\n"
    "parent = os.getppid()\n"
    "libc = ctypes.CDLL(None, use_errno=True)\n"
    "if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent:\n"
    "    sys.stderr.write('sandbox: parent changed during arming\\n')\n"
    "    sys.exit(137)\n"
    "script = sys.argv[1]\n"
    "os.execvp('bash', ['bash', '-c', script, *sys.argv[2:]])\n"
)
scope = (
    'if [ "$PPID" != "$1" ]; then echo "sandbox: parent changed during arming" >&2; exit 137; fi; '
    'shift; if ! printf 800 >/proc/self/oom_score_adj; then '
    'echo "sandbox: could not raise oom_score_adj" >&2; fi; exec "$@"'
)
argv = ["bwrap", *bwrap_args, "--", "python3", "-I", "-B", "-c", arm, inner, "_", *cmd]
me = str(os.getpid())
if os.environ.get("A770B_SANDBOX_ISOLATION") == "scope":
    argv = [
        "setpriv", "--pdeathsig", "KILL", "--",
        "systemd-run", "--user", "--scope", "--quiet", "--expand-environment=no",
        "--unit=" + os.environ.get("A770B_SANDBOX_UNIT", ""),
        "--slice=a770b-sandbox.slice", "-p", "OOMPolicy=" + os.environ.get("A770B_SANDBOX_POLICY", "kill"),
        "--", "bash", "-c", scope, "_", me, *argv,
    ]
else:
    argv = ["setpriv", "--pdeathsig", "KILL", "--", "bash", "-c", scope, "_", me, *argv]
proc = subprocess.Popen(argv, pass_fds=(w_fd,), close_fds=True)
os.close(w_fd)
# The marker is published on the first read. The supervisor can hold the
# write end until it exits, so waiting for EOF would hide a live entry.
chunks = []
seen = False
while True:
    data = os.read(r_fd, 64)
    if not data:
        break
    chunks.append(data)
    if not seen and b"entered" in b"".join(chunks):
        seen = True
        with open(entered_file, "w", encoding="utf-8") as fh:
            fh.write("entered\n")
os.close(r_fd)
rc = proc.wait()
# sys.exit of a negative wait status is the 8-bit trap (247 for -9). Publish 128+N.
if isinstance(rc, int) and rc < 0:
    rc = 128 + (-rc)
sys.exit(rc)
PY
else
  rc=0
  _run_scoped "${ARGS[@]}" -- python3 -I -B -c "$ARM_PY" "$INNER" _ "$@" || rc=$?
  exit "$rc"
fi
