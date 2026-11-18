#!/usr/bin/env bash
# release.sh — cut a release. The version number moves in its three places in one commit (VERSION, SKILL_VERSION in the
# skill script, the annotated tag), the commit and the tag are pushed, and a GitHub Release is published from a notes
# file that carries the measured numbers. Tagging alone is not releasing.
#   bash release.sh --notes <file.md> [--dry-run] [--major] [--minor]
# The number: the first release is 0.1.0. Each release after it adds 0.0.1; when the patch number would pass 10, the
# minor number moves by 0.1.0 instead and the patch restarts at 0 (0.2.10 → 0.3.0). A major bump never happens on its
# own: it takes --major, the maintainer's explicit word. A minor bump on demand — the next number is M.(m+1).0 — takes
# --minor, the maintainer's word; --major and --minor together are refused.
# Repeat/retry behavior: re-running at a HEAD whose tag v<VERSION> and GitHub Release are already published exits 0
# without bumping (an idempotent no-op; cutting a new version requires a new commit on main). An incomplete release at
# HEAD (unpushed commit/tag or missing GitHub Release) resumes the same version without bumping.
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
head_sha=$(git rev-parse HEAD)
origin_sha=$(git rev-parse origin/main)
gh auth status >/dev/null 2>&1 || { echo "⛔ gh is not authenticated; the release is published through it" >&2; exit 2; }

_remote_tag_sha(){
  local tag="$1" raw peeled direct
  raw=$(git ls-remote --tags origin "refs/tags/${tag}" "refs/tags/${tag}^{}" 2>/dev/null) || {
    echo "⛔ git ls-remote origin failed while checking tag $tag" >&2
    return 2
  }
  peeled=$(printf '%s\n' "$raw" | awk '$2 ~ /\^\{\}$/ {print $1; exit}')
  if [ -n "$peeled" ]; then
    printf '%s\n' "$peeled"
    return 0
  fi
  direct=$(printf '%s\n' "$raw" | awk '{print $1; exit}')
  printf '%s\n' "$direct"
}

_is_pending_release_ahead(){
  local cur_v="$1" skill_v="$2" local_sha="$3" remote_sha="$4" parent_sha subject changed tag_type
  [ "$head_sha" != "$origin_sha" ] || return 1
  case "$cur_v" in [0-9]*.[0-9]*.[0-9]*) ;; *) return 1 ;; esac
  [ "$cur_v" = "$skill_v" ] || return 1
  parent_sha=$(git rev-parse -q --verify HEAD^ 2>/dev/null || true)
  [ "$parent_sha" = "$origin_sha" ] || return 1
  [ "$local_sha" = "$head_sha" ] || return 1
  if [ -n "$remote_sha" ] && [ "$remote_sha" != "$head_sha" ]; then
    return 1
  fi
  tag_type=$(git cat-file -t "refs/tags/v${cur_v}" 2>/dev/null || true)
  [ "$tag_type" = "tag" ] || return 1
  subject=$(git log -1 --format=%s HEAD 2>/dev/null || true)
  [ "$subject" = "Release v${cur_v}: the version number moves in its three places" ] || return 1
  changed=$(git diff-tree --no-commit-id --name-only -r HEAD | LC_ALL=C sort | tr '\n' ' ')
  [ "$changed" = "VERSION ${SCRIPT} " ] || return 1
  return 0
}

_gh_repo_ok(){
  local out errf rc=0
  errf=$(mktemp)
  if out=$(gh repo view --json nameWithOwner 2>"$errf"); then
    rm -f "$errf"
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if isinstance(d, dict) and isinstance(d.get("nameWithOwner"), str) and "/" in d["nameWithOwner"] else 1)' "$out" 2>/dev/null && return 0
    echo "⛔ gh repo view returned unexpected metadata — cannot verify repository access" >&2
    return 1
  else
    rc=$?
    echo "⛔ gh repo view failed (exit $rc — API/auth/repository error, not missing release):" >&2
    cat "$errf" >&2
    rm -f "$errf"
    return 1
  fi
}

_gh_release_state(){
  local tag="$1" out errf rc=0 state=""
  _gh_repo_ok || return 1
  errf=$(mktemp)
  if out=$(gh release view "$tag" --json isDraft,tagName 2>"$errf"); then
    rm -f "$errf"
    state=$(python3 -c '
import json, sys
tag = sys.argv[1]
try:
    d = json.loads(sys.argv[2])
except Exception:
    sys.exit(1)
if not isinstance(d, dict) or d.get("tagName") != tag or not isinstance(d.get("isDraft"), bool):
    sys.exit(1)
print("draft" if d["isDraft"] else "published")
' "$tag" "$out" 2>/dev/null) || {
      echo "⛔ gh release view $tag returned unexpected JSON payload" >&2
      return 1
    }
    printf '%s\n' "$state"
    return 0
  else
    rc=$?
  fi
  if grep -qiE 'release not found' "$errf"; then
    rm -f "$errf"
    printf 'absent\n'
    return 0
  fi
  echo "⛔ gh release view $tag failed (exit $rc — API/auth error, not missing release):" >&2
  cat "$errf" >&2
  rm -f "$errf"
  return 1
}

_publish_release(){
  local tag="$1" after=""
  if gh release create "$tag" --title "$tag" --notes-file "$NOTES" >/dev/null 2>&1; then
    return 0
  fi
  after=$(_gh_release_state "$tag") || return 1
  if [ "$after" = "published" ]; then
    echo "✓ release $tag already present on GitHub after ambiguous create reply" >&2
    return 0
  elif [ "$after" = "draft" ]; then
    echo "⛔ gh release create $tag left an unpublished draft — publish or delete it before retrying" >&2
    return 1
  fi
  echo "⛔ gh release create $tag failed and release $tag is still absent on GitHub" >&2
  return 1
}

cur=$(head -1 VERSION | tr -d '[:space:]')
skill_cur=$(sed -nE 's/^SKILL_VERSION=([0-9.]+).*/\1/p' "$SCRIPT" | head -1)
local_cur_sha=$(git rev-parse -q --verify "refs/tags/v${cur}^{commit}" 2>/dev/null || true)
remote_cur_sha=$(_remote_tag_sha "v${cur}") || exit 2

if [ "$head_sha" != "$origin_sha" ]; then
  _is_pending_release_ahead "$cur" "$skill_cur" "$local_cur_sha" "$remote_cur_sha" || {
    echo "⛔ main is not in step with origin/main — push or pull first" >&2
    exit 2
  }
fi

if [ "$local_cur_sha" = "$head_sha" ] || [ "$remote_cur_sha" = "$head_sha" ]; then
  [ "$cur" = "$skill_cur" ] || { echo "⛔ HEAD already matches tag v$cur but VERSION ($cur) != SKILL_VERSION ($skill_cur)" >&2; exit 2; }
  if [ -n "$remote_cur_sha" ] && [ "$remote_cur_sha" != "$head_sha" ]; then
    echo "⛔ remote tag v$cur points to $remote_cur_sha, not HEAD ($head_sha) — refusing resume" >&2
    exit 2
  fi
  if [ -n "$local_cur_sha" ] && [ "$local_cur_sha" != "$head_sha" ]; then
    echo "⛔ local tag v$cur points to $local_cur_sha, not HEAD ($head_sha) — refusing resume" >&2
    exit 2
  fi
  rel_state=$(_gh_release_state "v${cur}") || exit 1
  if [ "$rel_state" = "published" ]; then
    if [ "$head_sha" != "$origin_sha" ] || [ "$remote_cur_sha" != "$head_sha" ]; then
      echo "⛔ release v$cur is already published on GitHub while origin/main or remote tag is behind HEAD — resolve remote refs before retrying" >&2
      exit 2
    fi
    echo "✓ release v$cur already published at HEAD ($head_sha) — nothing to bump"
    exit 0
  elif [ "$rel_state" = "draft" ]; then
    echo "⛔ release v$cur exists on GitHub as an unpublished draft — publish or delete it before retrying" >&2
    exit 1
  fi
  echo "▶ resume pending release v$cur at HEAD ($head_sha · notes $NOTES)"
  [ "$DRY" = 1 ] && exit 0
  if [ -z "$local_cur_sha" ] && [ "$remote_cur_sha" = "$head_sha" ]; then
    git fetch -q origin "refs/tags/v${cur}:refs/tags/v${cur}"
  fi
  if [ "$head_sha" != "$origin_sha" ] && [ -z "$remote_cur_sha" ]; then
    git push -q origin main "v${cur}"
  elif [ "$head_sha" != "$origin_sha" ]; then
    git push -q origin main
  elif [ -z "$remote_cur_sha" ]; then
    git push -q origin "v${cur}"
  fi
  _publish_release "v${cur}"
  echo "✓ released v$cur (resumed) — refresh every installed copy of the skill now (npx skills add … --copy, or copy skills/local-build by hand) so --version matches"
  exit 0
fi

last=$(git tag -l 'v[0-9]*' | sed 's/^v//' | LC_ALL=C sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
if [ -z "$last" ]; then
  [ "$cur" = 0.1.0 ] || { echo "⛔ nothing released yet and VERSION reads $cur — the first release is 0.1.0" >&2; exit 2; }
  next=0.1.0
else
  IFS=. read -r M m p <<<"$last"
  if [ "$MAJOR" = 1 ]; then next="$((M+1)).0.0"
  elif [ "$MINOR" = 1 ]; then next="$M.$((m+1)).0"
  elif [ "$p" -ge 10 ] && [ "$m" -ge 99 ]; then echo "⛔ $last is the last number before a major bump, which takes --major (the maintainer's word)" >&2; exit 2
  elif [ "$p" -ge 10 ]; then next="$M.$((m+1)).0"
  else next="$M.$m.$((p+1))"; fi
fi
remote_next_sha=$(_remote_tag_sha "v${next}") || exit 2
[ -z "$remote_next_sha" ] || { echo "⛔ remote tag v$next already exists on origin ($remote_next_sha) — fetch tags before releasing" >&2; exit 2; }
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
_publish_release "v$next"
echo "✓ released v$next — refresh every installed copy of the skill now (npx skills add … --copy, or copy skills/local-build by hand) so --version matches"
