#!/usr/bin/env bash
# selftest.sh — what the harness can prove without the card: every script parses; the health line reads the four
# kinds of answer and strips what a hostile answer could put on a terminal; the seat's dirty check hides nothing but the
# harness's own brief copies; the capture survives a run that created no file. Run: bash tests/selftest.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd); fail=0
for f in "$here"/harness/*.sh "$here"/skills/local-build/scripts/local-build.sh "$here"/release.sh "$here"/sync_local_build.sh; do
  [ -f "$f" ] || continue; bash -n "$f" || { echo "FAIL syntax: $f"; fail=1; }
done
export A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="${TMPDIR:-/tmp}/a770b-selftest-$$"
. "$here/harness/env.sh"; . "$here/harness/guard.sh"
t=$(mktemp -d)
printf '{"status": "degraded", "version": "1"}\n' > "$t/degraded.json"
printf '{"status":"ok"}\n' > "$t/ok.json"
printf '{"version":"1"}\n' > "$t/nostatus.json"
printf '{"status" : "\033[2J\033[Hfake"}\n' > "$t/hostile.json"
check(){ local got; got=$(health_status "$1"); if [ "$got" = "$2" ]; then echo "ok   health: $2"; else echo "FAIL $1 -> '$got', expected '$2'"; fail=1; fi; }
check "file://$t/degraded.json" "degraded"
check "file://$t/ok.json" "ok"
check "file://$t/nostatus.json" "answered, no status field"
check "file://$t/missing.json" "no answer"
check "file://$t/hostile.json" "[2J[Hfake"
grep -q 'health_status "$A770B_HEALTH_URL"' "$here/harness/guard.sh" && echo "ok   gate: budget_gate prints health_status" || { echo "FAIL budget_gate does not call health_status"; fail=1; }
sed -n '/health_status "$A770B_HEALTH_URL"/p' "$here/harness/guard.sh" | grep -q 'ok=0' && { echo "FAIL the health line still refuses"; fail=1; }
# a seat with the harness's own brief copy is clean; anything else planted in that directory, or a symlink, is reported and reset
r="$t/seat"; git init -q "$r"; ( cd "$r" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init )
mkdir -p "$r/Local_Documentation/briefs"; echo brief > "$r/Local_Documentation/briefs/brief-20260907-120000.md"
n=$(seat_dirty "$r" | wc -l); [ "$n" = 0 ] && echo "ok   seat: the brief copy alone reads clean" || { echo "FAIL seat with only the brief copy reads dirty ($n)"; seat_dirty "$r"; fail=1; }
echo planted > "$r/Local_Documentation/briefs/evil.py"; ln -s /etc/hostname "$r/leak"
seat_dirty "$r" | grep -q 'Local_Documentation/briefs/evil.py' && echo "ok   seat: a planted file in briefs/ is reported" || { echo "FAIL planted briefs file hidden"; fail=1; }
seat_dirty "$r" | grep -q 'leak' && echo "ok   seat: a symlink is reported" || { echo "FAIL symlink hidden"; fail=1; }
reset_worktree "$r" >/dev/null; [ ! -e "$r/Local_Documentation/briefs/evil.py" ] && [ ! -L "$r/leak" ] && [ -f "$r/Local_Documentation/briefs/brief-20260907-120000.md" ] && echo "ok   reset: planted file and symlink gone, the brief copy kept" || { echo "FAIL reset left something or removed the brief"; fail=1; }
rm -rf "$r/Local_Documentation/briefs"; mkdir -p "$t/outside"; ln -s "$t/outside" "$r/Local_Documentation/briefs"
reset_worktree "$r" >/dev/null; [ ! -L "$r/Local_Documentation/briefs" ] && [ -d "$t/outside" ] && echo "ok   reset: a symlinked briefs directory is removed, its target untouched" || { echo "FAIL a symlinked briefs directory survived the reset or its target was harmed"; fail=1; }
# the capture on a run that made no file, and on one whose only new file is a symlink: it must finish and never read through the link
mkdir -p "$A770B_DATA/results" "$A770B_DATA/logs"; : > "$A770B_DATA/logs/llamacpp-a770.log"; echo "no pytest here" > "$t/build.log"
A770B_REFUSE=/nonexistent bash "$here/harness/capture_task.sh" selftest-empty "$r" "$t/build.log" >/dev/null 2>&1 && [ -f "$A770B_DATA/results/selftest-empty.task.md" ] && echo "ok   capture: a run with no new file still writes its report" || { echo "FAIL capture aborted on an empty new-file list"; fail=1; }
echo "MARKER-$$" > "$t/secret"; ln -s "$t/secret" "$r/leak"
A770B_REFUSE=/nonexistent bash "$here/harness/capture_task.sh" selftest-link "$r" "$t/build.log" >/dev/null 2>&1
if grep -q "MARKER-$$" "$A770B_DATA/results/selftest-link.task.md" "$A770B_DATA/results/selftest-link.patch" 2>/dev/null; then echo "FAIL capture read through a symlink"; fail=1; else echo "ok   capture: a symlink is named, never read"; fi
grep -q 'run lock:' "$here/skills/local-build/scripts/local-build.sh" && grep -q '"seat: ' "$here/skills/local-build/scripts/local-build.sh" || { echo "FAIL status does not report the lock and the seat"; fail=1; }
rm -rf "$t" "$A770B_DATA"
if [ "$fail" = 0 ]; then echo "selftest: all passed"; fi
exit "$fail"
