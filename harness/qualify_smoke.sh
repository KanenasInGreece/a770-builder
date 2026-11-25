#!/usr/bin/env bash
# qualify_smoke.sh <job> — one model list, one completion, one tool call.
# The key is read from the key file into a mode 0600 curl config. The curl
# command line carries that config path and never the key.
set -uo pipefail
umask 077
job=${1:?job directory}
host=${A770B_HOST:-127.0.0.1}
port=${A770B_PORT:-9}
url="http://${host}:${port}/v1"
keyfile=${A770B_API_KEY_FILE:-}

if command -v a770b-qualify-smoke >/dev/null 2>&1; then
  a770b-qualify-smoke --key-file "${keyfile:-}" --url "$url"
  exit $?
fi

cfg="$job/scratch/curl.cfg"
argvlog="$job/evidence/curl-argv.txt"
if [ -n "$keyfile" ] && [ -f "$keyfile" ] && [ ! -L "$keyfile" ] && [ ! -L "$cfg" ]; then
  python3 - "$keyfile" "$cfg" <<'PY'
import os, sys
text = open(sys.argv[1], encoding="utf-8").read().splitlines()
key = text[0].strip() if text else ""
path = sys.argv[2]
fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
os.write(fd, ('header = "Authorization: Bearer %s"\n' % key).encode())
os.close(fd)
PY
fi
mkdir -p "$job/evidence"
if [ ! -L "$argvlog" ]; then
  {
    printf '%s\n' "curl --config $cfg $url/models"
    printf '%s\n' "curl --config $cfg $url/chat/completions"
    printf '%s\n' "curl --config $cfg $url/chat/completions"
  } >> "$argvlog"
  chmod 600 "$argvlog" 2>/dev/null || true
fi
if [ -f "$cfg" ]; then
  curl --silent --show-error --config "$cfg" "$url/models" >/dev/null 2>&1 || true
  curl --silent --show-error --config "$cfg" "$url/chat/completions" >/dev/null 2>&1 || true
fi
printf '%s\n' '{"models": false, "completion": false, "tool": false}'
