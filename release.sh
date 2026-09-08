#!/usr/bin/env bash
# release.sh — cut a release. The version number moves in its three places in one commit (VERSION, SKILL_VERSION in the
# skill script, the annotated tag), the commit and the tag are pushed, and a GitHub Release is published from a notes
# file that carries the measured numbers. Tagging alone is not releasing.
#   bash release.sh --notes <file.md> [--dry-run] [--major] [--minor]
# The number: the first release is 0.1.0. Each release after it adds 0.0.1; when the patch number would pass 99, the
# minor number moves by 0.1.0 instead and the patch restarts at 0 (0.1.99 → 0.2.0). A major bump never happens on its
# own: it takes --major, the maintainer's explicit word. A minor bump on demand — the next number is M.(m+1).0 — takes
# --minor, the maintainer's word; --major and --minor together are refused.
set -euo pipefail
cd "$(dirname "$0")"
SCRIPT=skills/local-build/scripts/local-build.sh
NOTES=""; DRY=0; MAJOR=0; MINOR=0
while [ $# -gt 0 ]; do case "$1" in --notes) NOTES="${2:?file}"; shift;; --dry-run) DRY=1;; --major) MAJOR=1;; --minor) MINOR=1;; *) echo "usage: release.sh --notes <file.md> [--dry-run] [--major] [--minor]" >&2; exit 2;; esac; shift; done
[ "$MAJOR" = 1 ] && [ "$MINOR" = 1 ] && { echo "⛔ --major and --minor together are refused" >&2; exit 2; }
[ -f "$NOTES" ] || { echo "⛔ a notes file is required (--notes <file.md>): a release carries its measured numbers" >&2; exit 2; }
branch=$(git rev-parse --abbrev-ref HEAD); [ "$branch" = main ] || { echo "⛔ releases are cut from main (this is $branch)" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "⛔ the tree is not clean" >&2; exit 2; }
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "⛔ main is not in step with origin/main — push or pull first" >&2; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "⛔ gh is not authenticated; the release is published through it" >&2; exit 2; }
last=$(git tag -l 'v[0-9]*' | sed 's/^v//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
cur=$(head -1 VERSION)
if [ -z "$last" ]; then
  [ "$cur" = 0.1.0 ] || { echo "⛔ nothing released yet and VERSION reads $cur — the first release is 0.1.0" >&2; exit 2; }
  next=0.1.0
else
  IFS=. read -r M m p <<<"$last"
  if [ "$MAJOR" = 1 ]; then next="$((M+1)).0.0"
  elif [ "$MINOR" = 1 ]; then next="$M.$((m+1)).0"
  elif [ "$p" -ge 99 ] && [ "$m" -ge 99 ]; then echo "⛔ $last is the last number before a major bump, which takes --major (the maintainer's word)" >&2; exit 2
  elif [ "$p" -ge 99 ]; then next="$M.$((m+1)).0"
  else next="$M.$m.$((p+1))"; fi
fi
echo "▶ release v$next  (last release: ${last:-none} · VERSION $cur · notes $NOTES)"
[ "$DRY" = 1 ] && exit 0
printf '%s\n' "$next" > VERSION
sed -i -E "s/^SKILL_VERSION=[0-9.]+/SKILL_VERSION=$next/" "$SCRIPT"
grep -q "^SKILL_VERSION=$next" "$SCRIPT" || { echo "⛔ SKILL_VERSION was not updated in $SCRIPT" >&2; exit 2; }
if [ -n "$(git status --porcelain)" ]; then
  git add VERSION "$SCRIPT"
  git commit -q -m "Release v$next: the version number moves in its three places" -m "The VERSION file, the installed skill's SKILL_VERSION and the tag v$next carry the same number; the release notes carry the measured numbers."
fi
git tag -a "v$next" -F "$NOTES"
git push -q origin main "v$next"
gh release create "v$next" --title "v$next" --notes-file "$NOTES" >/dev/null
echo "✓ released v$next — refresh every installed copy of the skill now (npx skills add … --copy, or copy skills/local-build by hand) so --version matches"
