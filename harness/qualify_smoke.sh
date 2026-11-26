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
alias=${A770B_ALIAS:-local-builder}

if command -v a770b-qualify-smoke >/dev/null 2>&1; then
  a770b-qualify-smoke --key-file "${keyfile:-}" --url "$url"
  exit $?
fi

cfg="$job/scratch/curl.cfg"
argvlog="$job/evidence/curl-argv.txt"
mkdir -p "$job/evidence" "$job/scratch"
if [ -L "$argvlog" ] || [ -L "$job/evidence" ] || [ -L "$job/scratch" ]; then
  printf '⛔ refusing a symlink in the smoke path\n' >&2
  exit 2
fi
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
{
  printf '%s\n' "curl --config $cfg $url/models"
  printf '%s\n' "curl --config $cfg $url/chat/completions"
  printf '%s\n' "curl --config $cfg $url/chat/completions"
} >> "$argvlog"
chmod 600 "$argvlog" 2>/dev/null || true

body=$job/scratch/smoke-body
comp=$job/scratch/smoke-completion.json
tool=$job/scratch/smoke-tool.json
models=$job/scratch/smoke-models.json
rm -f -- "$models" "$comp" "$tool"
curl_cfg=()
if [ -f "$cfg" ] && [ ! -L "$cfg" ]; then
  curl_cfg=(--config "$cfg")
fi
curl --silent --show-error --max-time 60 "${curl_cfg[@]}" "$url/models" >"$models" 2>>"$job/phases/smoke.log" || true
python3 - "$alias" <<'PY' >"$comp.body"
import json, sys
alias = sys.argv[1]
print(json.dumps({
    "model": alias,
    "messages": [{"role": "user", "content": "Reply with one word."}],
}))
PY
curl --silent --show-error --max-time 120 "${curl_cfg[@]}" \
  -H 'Content-Type: application/json' \
  --data-binary @"$comp.body" \
  "$url/chat/completions" >"$comp" 2>>"$job/phases/smoke.log" || true
python3 - "$alias" <<'PY' >"$tool.body"
import json, sys
alias = sys.argv[1]
print(json.dumps({
    "model": alias,
    "messages": [{"role": "user", "content": "Use the tool if you need a fact."}],
    "tool_choice": "auto",
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Look up the weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }],
}))
PY
curl --silent --show-error --max-time 120 "${curl_cfg[@]}" \
  -H 'Content-Type: application/json' \
  --data-binary @"$tool.body" \
  "$url/chat/completions" >"$tool" 2>>"$job/phases/smoke.log" || true
python3 - "$job" "$models" "$comp" "$tool" <<'PY'
import json, os, sys
job, models, comp, tool = sys.argv[1:]

def load(path):
    try:
        if os.path.islink(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None

models_ok = False
body = load(models)
if isinstance(body, dict):
    data = body.get("data")
    models_ok = isinstance(data, list) and len(data) > 0

def reply(path):
    parsed = load(path)
    if not isinstance(parsed, dict):
        return False, None
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False, parsed
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return False, parsed
    content = message.get("content")
    text = content.strip() if isinstance(content, str) else ""
    return bool(text) or bool(message.get("tool_calls")), parsed

completion_ok, completion = reply(comp)
tool_ok = False
parsed = load(tool)
if isinstance(parsed, dict):
    choices = parsed.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and message.get("tool_calls"):
            tool_ok = True
print(json.dumps({"models": models_ok, "completion": completion_ok, "tool": tool_ok}))
plan = os.path.join(job, "plan.json")
engine = ""
try:
    if os.path.isfile(plan) and not os.path.islink(plan):
        engine = json.load(open(plan, encoding="utf-8")).get("engine") or ""
except (OSError, ValueError):
    engine = ""
if engine == "vllm" and isinstance(completion, dict):
    dest = os.path.join(job, "evidence", "per-request-fields.json")
    if not os.path.islink(dest) and not os.path.islink(os.path.dirname(dest)):
        fd = os.open(dest, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.write(fd, (json.dumps(completion, indent=2) + "\n").encode())
        os.close(fd)
PY
