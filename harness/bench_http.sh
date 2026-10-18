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

usage(){ echo "usage: bench_http.sh --base-url URL --served-model-name NAME --tokenizer PATH [--save-result FILE] [--dry-run] [--pp size ...]" >&2; exit 2; }

base=""; served=""; tokenizer=""; save=""; dry=0; sizes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) base="${2:?--base-url needs a value}"; shift 2 ;;
    --served-model-name) served="${2:?--served-model-name needs a value}"; shift 2 ;;
    --tokenizer) tokenizer="${2:?--tokenizer needs a value}"; shift 2 ;;
    --save-result) save="${2:?--save-result needs a value}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --pp|--sizes)
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --*) break ;;
          *)
            for s in $1; do
              sizes+=("$s")
            done
            shift ;;
        esac
      done
      ;;
    [0-9]*) sizes+=("$1"); shift ;;
    *) echo "⛔ unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$base" ] && [ -n "$served" ] && [ -n "$tokenizer" ] || usage
base="${base%/}"
# Health is the origin /health. The llama-benchy base ends in /v1.
origin="${base%/v1}"
bench_base="${origin}/v1"
if [ "$tokenizer" = "$served" ]; then
  echo "⛔ --tokenizer must be a path or a Hugging Face id, not the served name '$served'" >&2
  exit 2
fi
# llama-benchy loads a file path with Tokenizer.from_file, which reads the whole file.
# A weight file is not a tokenizer. A directory, or a tokenizer.json under 64 MiB, is.
if [ -f "$tokenizer" ]; then
  tok_base=$(basename -- "$tokenizer")
  tok_size=$(stat -c %s -- "$tokenizer")
  if [ "$tok_base" != "tokenizer.json" ] || [ "$tok_size" -gt 67108864 ]; then
    echo "⛔ --tokenizer must be a model directory or tokenizer.json, not a weight file" >&2
    exit 2
  fi
fi
if [ -z "$save" ]; then
  if [ "$dry" = 1 ]; then
    save="result.json"
  else
    mkdir -p "$A770B_DATA/results"
    save="$A770B_DATA/results/http-$(date +%Y%m%d-%H%M%S).json"
  fi
fi

[ "${#sizes[@]}" -gt 0 ] || sizes=(8192 32768 65536 100000)

# llama.cpp and vLLM start with this file. The client's default bearer is the word EMPTY,
# which those servers reject. The printed line is stored beside the result, so the value stays out of it.
key=$(a770b_api_key) || { echo "⛔ cannot read the API key file $A770B_API_KEY_FILE" >&2; exit 2; }
cmd=(uvx llama-benchy
  --base-url "$bench_base"
  --served-model-name "$served"
  --model "$served"
  --tokenizer "$tokenizer"
  --api-key "$key"
  --pp "${sizes[@]}"
  --tg 128
  --depth 0
  --runs 3
  --concurrency 1
  --no-cache
  --skip-coherence
  --latency-mode generation
  --extra-body return_token_ids=false
  --format json
  --save-result "$save")
flag_cmd=()
skip_key=0
for arg in "${cmd[@]}"; do
  if [ "$skip_key" = 1 ]; then
    flag_cmd+=(from-key-file)
    skip_key=0
    continue
  fi
  flag_cmd+=("$arg")
  if [ "$arg" = "--api-key" ]; then skip_key=1; fi
done
flagline=$(printf '%q ' "${flag_cmd[@]}")
unset key skip_key

if [ "$dry" = 1 ]; then
  printf '%s\n' "$flagline"
  exit 0
fi

if ! curl -sf --max-time 5 "$origin/health" >/dev/null; then
  echo "⛔ $origin/health did not answer" >&2
  exit 1
fi
"${cmd[@]}"
python3 "$here/bench_http_map.py" "$save" --out "${save%.json}.mapped.json" --flags "$flagline"
echo "mapped → ${save%.json}.mapped.json"
