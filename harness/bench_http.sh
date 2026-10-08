#!/usr/bin/env bash
# bench_http.sh — speed against an OpenAI-compatible server that is already up.
# Calls llama-benchy with uvx. This tree does not contain that program.
#   bench_http.sh --base-url URL --served-model-name NAME --tokenizer PATH [--save-result FILE] [--dry-run]
# --tokenizer is a model directory or a Hugging Face id. It must not be the served name.
# --dry-run prints the command and does not call uvx, the network, or the GPU.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$here/env.sh"

usage(){ echo "usage: bench_http.sh --base-url URL --served-model-name NAME --tokenizer PATH [--save-result FILE] [--dry-run]" >&2; exit 2; }

base=""; served=""; tokenizer=""; save=""; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) base="${2:?--base-url needs a value}"; shift 2 ;;
    --served-model-name) served="${2:?--served-model-name needs a value}"; shift 2 ;;
    --tokenizer) tokenizer="${2:?--tokenizer needs a value}"; shift 2 ;;
    --save-result) save="${2:?--save-result needs a value}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) echo "⛔ unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$base" ] && [ -n "$served" ] && [ -n "$tokenizer" ] || usage
base="${base%/}"
if [ "$tokenizer" = "$served" ]; then
  echo "⛔ --tokenizer must be a path or a Hugging Face id, not the served name '$served'" >&2
  exit 2
fi
if [ -z "$save" ]; then
  if [ "$dry" = 1 ]; then
    save="result.json"
  else
    mkdir -p "$A770B_DATA/results"
    save="$A770B_DATA/results/http-$(date +%Y%m%d-%H%M%S).json"
  fi
fi

cmd=(uvx llama-benchy
  --base-url "$base"
  --served-model-name "$served"
  --tokenizer "$tokenizer"
  --pp 8192 32768 65536 100000
  --tg 128
  --depth 0
  --runs 3
  --concurrency 1
  --no-cache
  --skip-coherence
  --latency-mode generation
  --format json
  --save-result "$save")
flagline=$(printf '%q ' "${cmd[@]}")

if [ "$dry" = 1 ]; then
  printf '%s\n' "$flagline"
  exit 0
fi

if ! curl -sf --max-time 5 "$base/health" >/dev/null; then
  echo "⛔ $base/health did not answer" >&2
  exit 1
fi
"${cmd[@]}"
python3 "$here/bench_http_map.py" "$save" --out "${save%.json}.mapped.json" --flags "$flagline"
echo "mapped → ${save%.json}.mapped.json"
