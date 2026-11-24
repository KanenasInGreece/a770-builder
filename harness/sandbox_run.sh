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
set -euo pipefail
. "$(dirname "$0")/env.sh"
. "$(dirname "$0")/guard.sh"
WT="${1:?worktree}"; CFG="${2:?profile config}"; shift 2; [ "${1:-}" = "--" ] && shift
WT=$(guard_path_policy "$WT")
[ -d "$WT" ] || { echo "⛔ sandbox: worktree missing $WT" >&2; exit 2; }
[ -f "$CFG" ] || { echo "⛔ sandbox: config missing $CFG" >&2; exit 2; }
for t in bwrap socat uv; do command -v "$t" >/dev/null || { echo "⛔ sandbox: $t not installed" >&2; exit 2; }; done
a770b_require_opencode_v1 || exit 2
OC_BIN=$(a770b_opencode_bin)
UV=$(readlink -f "$(command -v uv)")
[ -d "$A770B_UV_CACHE" ] || { echo "⛔ sandbox: uv cache $A770B_UV_CACHE missing — run harness/warm_cache.sh once (the sandbox has no network)" >&2; exit 2; }
SOCK="$A770B_DATA/logs/llama-bridge.sock"; BRIDGE=""
if [ "${A770B_NO_BRIDGE:-0}" != 1 ]; then
  # host side of the bridge: unix socket → the model server; lives only for this run
  rm -f "$SOCK"
  case "${A770B_SERVED_ENGINE:-}" in
    sglang|vllm|strata)
      python3 "$A770B_PROJECT/harness/usage_tap.py" --listen "$SOCK" --upstream "$A770B_HOST:$A770B_PORT" --out "$A770B_DATA/logs/usage.json" 7>&- 9>&- >>"$A770B_DATA/logs/llama-bridge.log" 2>&1 & BRIDGE=$!
      ;;
    *)
      socat "UNIX-LISTEN:$SOCK,fork,unlink-early" "TCP:$A770B_HOST:$A770B_PORT" 7>&- 9>&- 2>>"$A770B_DATA/logs/llama-bridge.log" & BRIDGE=$!
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
# inside: a private per-run copy of the warm uv cache (uv must write ephemeral environments into its cache; the warm
# source stays read-only so no run can poison a later one), the loopback bridge, then the command
# 7>&- 9>&-: caller lock descriptors (seat lock & card lock) must not cross into the sandbox;
# every other inherited descriptor is closed by --new-session/--clearenv semantics only for the env, so close it explicitly.
if [ -n "${A770B_SANDBOX_ENTERED_FILE:-}" ]; then
  python3 - "$A770B_SANDBOX_ENTERED_FILE" "$HOME" "$A770B_PORT" "${ARGS[@]}" -- "$@" 7>&- 9>&- <<'PY'
import os, subprocess, sys
entered_file = sys.argv[1]
sb_home = sys.argv[2]
sb_port = sys.argv[3]
rest = sys.argv[4:]
sep = rest.index("--")
bwrap_args = rest[:sep]
cmd = rest[sep + 1:]
r_fd, w_fd = os.pipe()
os.set_inheritable(w_fd, True)
inner = (
    'mkdir -p "$HOME/.cache/uv" && cp -a "$HOME/.cache/uv-warm/." "$HOME/.cache/uv/" 2>/dev/null; '
    '[ -S /run/llama.sock ] && { socat "TCP-LISTEN:' + sb_port + ',bind=127.0.0.1,fork,reuseaddr" UNIX-CONNECT:/run/llama.sock 2>/dev/null & }; '
    f'printf "entered\\n" >&{w_fd} 2>/dev/null || true; exec {w_fd}>&-; '
    'exec "$@"'
)
proc = subprocess.Popen(
    ["bwrap", *bwrap_args, "--", "bash", "-c", inner, "_", *cmd],
    pass_fds=(w_fd,),
    close_fds=True,
)
os.close(w_fd)
chunks = []
while True:
    data = os.read(r_fd, 64)
    if not data:
        break
    chunks.append(data)
os.close(r_fd)
if b"entered" in b"".join(chunks):
    with open(entered_file, "w", encoding="utf-8") as fh:
        fh.write("entered\n")
sys.exit(proc.wait())
PY
else
  bwrap "${ARGS[@]}" -- bash -c 'mkdir -p "$HOME/.cache/uv" && cp -a "$HOME/.cache/uv-warm/." "$HOME/.cache/uv/" 2>/dev/null; [ -S /run/llama.sock ] && { socat "TCP-LISTEN:$A770B_PORT,bind=127.0.0.1,fork,reuseaddr" UNIX-CONNECT:/run/llama.sock 2>/dev/null & }; exec "$@"' _ "$@" 7>&- 9>&-
fi
