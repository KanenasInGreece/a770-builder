#!/usr/bin/env bash
# warm_cache.sh — pre-fill the uv cache the sandbox mounts READ-ONLY. The sandbox has no network, so every package a
# brief's test command needs (pytest, pytest-asyncio by default) must already be cached here. Run once, and again
# whenever a brief needs a package that is not yet cached. Extra packages: warm_cache.sh httpx numpy …
set -euo pipefail
. "$(dirname "$0")/env.sh"
mkdir -p "$A770B_UV_CACHE"
PKGS=(pytest pytest-asyncio "$@")
WITH=(); for p in "${PKGS[@]}"; do WITH+=(--with "$p"); done
echo "▶ warming $A770B_UV_CACHE with: ${PKGS[*]}"
UV_CACHE_DIR="$A770B_UV_CACHE" uv run "${WITH[@]}" python -c 'import sys; print("python", sys.version.split()[0])'
echo "✓ cache warm; sandboxed runs may now use: uv run ${WITH[*]} …"
