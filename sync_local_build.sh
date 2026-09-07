#!/usr/bin/env bash
# sync_local_build.sh — install the local-build skill (source: this project's skills/local-build) as REAL COPIES into
# every agent skill directory that already exists on this machine. Never creates an agent's home, never touches a
# directory that is not this skill (a same-named foreign skill is left alone and reported), never symlinks.
#   bash sync_local_build.sh            # install / refresh
#   bash sync_local_build.sh --check    # report only
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/skills/local-build"
[ -f "$SRC/SKILL.md" ] || { echo "⛔ source skill missing at $SRC" >&2; exit 2; }
grep -qE '^name: local-build$' "$SRC/SKILL.md" || { echo "⛔ source SKILL.md is not the local-build skill" >&2; exit 2; }
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
sum(){ (cd "$1" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12); }
SRCSUM=$(sum "$SRC")
for dst in "$HOME/.claude/skills" "$HOME/.grok/skills" "$HOME/.codex/skills" "$HOME/.gemini/skills" "$HOME/.config/opencode/skills"; do
  [ -d "$dst" ] || { printf '  %-40s %s\n' "$dst" "(no such agent skill dir — skipped)"; continue; }
  t="$dst/local-build"
  if [ -e "$t" ] && ! grep -qsE '^name: local-build$' "$t/SKILL.md"; then printf '  %-40s %s\n' "$t" "⚠ exists but is NOT this skill — left alone"; continue; fi
  if [ -d "$t" ] && [ "$(sum "$t")" = "$SRCSUM" ]; then printf '  %-40s %s\n' "$t" "up to date ($SRCSUM)"; continue; fi
  [ "$CHECK" = 1 ] && { printf '  %-40s %s\n' "$t" "would refresh → $SRCSUM"; continue; }
  rm -rf "$t"; cp -r "$SRC" "$t"; chmod +x "$t/scripts/local-build.sh"; printf '  %-40s %s\n' "$t" "installed ($SRCSUM)"
done
echo "source $SRCSUM · the constitution snippet ships inside each copy (CONSTITUTION_SNIPPET.md); agents incorporate it themselves"
