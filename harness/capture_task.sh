#!/usr/bin/env bash
# capture_task.sh — after a build run, capture what the model did for review, then reset the worktree.
#   capture_task.sh <label> <worktree> <build-log>
# Writes $A770B_DATA/results/<label>.task.md: git status, the tracked diff, every new file (the harness's own brief copy
# under Local_Documentation/briefs/ excepted; a symlink is named, never read), the pytest summary the
# model reported INSIDE the run (never re-executed here), the opencode transcript tail, and the server-side timing
# distribution; and <label>.patch, the complete change (tracked diff + every new file) that `local-build.sh verify`
# re-applies to a clean seat to run the tests inside the sandbox. Then resets the worktree to clean. All git here is safe_git: config-driven code paths neutralised,
# because the model owns the tree.
set -euo pipefail
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
LABEL="${1:?label}"; WT=$(guard_worktree "${2:?worktree}"); BLOG="${3:?build log}"
[ -f "$BLOG" ] || { echo "⛔ build log not found: $BLOG" >&2; exit 2; }
OUT="$A770B_DATA/results/$LABEL.task.md"; PATCH="$A770B_DATA/results/$LABEL.patch"; SLOG="$A770B_DATA/logs/llamacpp-a770.log"
# the complete change, untruncated, for `verify`: tracked diff, then each new file as a creation diff
{ safe_git_diff "$WT"
  safe_git "$WT" ls-files --others --exclude-standard -z | { grep -zvE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || true; } | while IFS= read -r -d '' f; do
    [ -L "$WT/$f" ] && continue                                   # a symlink is named in the report, never followed or applied
    safe_git_diff "$WT" --no-index -- /dev/null "$f" || true; done
} > "$PATCH"
# the diff is taken into a file first: piping it into head made git die of SIGPIPE on a diff over 400 lines, and under
# pipefail that aborted the capture before the reset, leaving the seat dirty while the skill reported it clean
DIFF="$A770B_DATA/logs/capture-$LABEL.diff"; safe_git_diff "$WT" > "$DIFF"
{
echo "# Task capture — $LABEL — $(date -Is)"
echo; echo "## git status (worktree)"; safe_git "$WT" status --porcelain
echo; echo "## diff (tracked, first 400 lines; the complete change is the patch)"; echo '```diff'; head -400 -- "$DIFF"; echo '```'
echo; echo "## new files"
safe_git "$WT" ls-files --others --exclude-standard -z | { grep -zvE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || true; } | while IFS= read -r -d '' f; do
  echo; if [ -L "$WT/$f" ]; then echo "### $f — SYMLINK to $(readlink -- "$WT/$f"), not read, not in the patch (the host never follows a link the model made)"; continue; fi
  echo "### $f"; echo '```'; head -200 -- "$WT/$f"; echo '```'; done
echo; echo "## ignored files the run left behind (names only; removed by the reset, never applied by verify)"
safe_git "$WT" ls-files --others --ignored --exclude-standard | grep -vE '^Local_Documentation/briefs/brief-[0-9]{8}-[0-9]{6}\.md$' || echo "none"
echo; echo "## pytest summary as reported by the model inside the run (NOT re-executed here)"
grep -E '[0-9]+ (passed|failed|error)' "$BLOG" | tail -3 || echo "no pytest summary line in the transcript"
echo; echo "## opencode transcript tail"; echo '```'; grep -vE '^\s*$' "$BLOG" | tail -30 | cut -c1-300; echo '```'
echo; echo "## server-side timings during the run"
python3 - "$SLOG" <<'PY'
import re,sys
lines=open(sys.argv[1],errors='ignore').read().splitlines()
pe=[l for l in lines if 'prompt eval time' in l]; ev=[l for l in lines if 'eval time' in l and 'prompt eval' not in l]
def parse(l):
    m=re.search(r'=\s+([\d.]+) ms /\s+(\d+) tokens \(\s*([\d.]+) ms per token,\s*([\d.]+) tokens per second',l); return tuple(float(x) for x in m.groups()) if m else None
P=[p for p in (parse(l) for l in pe) if p]; E=[e for e in (parse(l) for l in ev) if e]
n=len(E); pt=sum(p[1] for p in P); gt=sum(e[1] for e in E)
ttft=sorted(p[0] for p in P); tpot=sorted(e[2] for e in E)
q=lambda a,f: a[int(f*(len(a)-1))] if a else 0
print(f"requests={n} prompt_tokens_total={int(pt)} gen_tokens_total={int(gt)}")
print(f"TTFT ms (prompt eval): median={q(ttft,.5):.0f} p90={q(ttft,.9):.0f} max={q(ttft,1):.0f}   largest prompt={int(max((p[1] for p in P),default=0))} tokens")
print(f"TPOT ms: median={q(tpot,.5):.1f} p90={q(tpot,.9):.1f}   decode tok/s median={1000/q(tpot,.5) if q(tpot,.5) else 0:.1f}")
print(f"prefill tok/s over all prompts={pt/ (sum(p[0] for p in P)/1000) if P else 0:.0f}")
PY
} > "$OUT" 2>&1
rm -f -- "$DIFF"
echo "captured → $OUT ($(wc -l < "$OUT") lines) · patch $PATCH ($(wc -l < "$PATCH") lines)"
reset_worktree "$WT"
