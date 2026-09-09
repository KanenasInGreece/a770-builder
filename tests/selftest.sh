#!/usr/bin/env bash
# selftest.sh — what the harness can prove without the card: every script parses; the run specification is refused where it must be and rendered before the floor; the health line reads the four
# kinds of answer and strips what a hostile answer could put on a terminal; the seat's dirty check hides nothing but the
# harness's own brief copies; the capture survives a run that created no file. Run: bash tests/selftest.sh
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd); fail=0
for f in "$here"/harness/*.sh "$here"/skills/local-build/scripts/local-build.sh "$here"/release.sh "$here"/sync_local_build.sh; do
  [ -f "$f" ] || continue; bash -n "$f" || { echo "FAIL syntax: $f"; fail=1; }
done
t=$(mktemp -d)
# isolation (I11): this machine's own ~/.config/a770-builder/builder(.<mode>).env must never reach the selftest's
# baseline, whatever mode it selects and whatever the calling shell already exported — clear every exported A770B_*
# this process carries, then set exactly the baseline the checks below assume: display mode, an empty XDG_CONFIG_HOME.
for v in $(compgen -A export A770B_); do
  case $v in A770B_PROJECT|A770B_REFUSE|A770B_DATA) ;; *) unset "$v";; esac
done
mkdir -p "$t/xdg"
export A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="${TMPDIR:-/tmp}/a770b-selftest-$$" A770B_CARD_MODE=display XDG_CONFIG_HOME="$t/xdg"
. "$here/harness/env.sh"; . "$here/harness/guard.sh"
# A770B_KEEP / _iso — every I1-I10 check below that switches the mode or the registry re-sources env.sh in its own
# subshell; _iso clears whatever this process already has exported (this baseline's A770B_CARD_MODE included) except
# the names in A770B_KEEP, so each subshell resolves fresh from the files and exports it sets itself — nothing here,
# and nothing inherited from the calling shell, leaks into a check it was not given to.
A770B_KEEP="A770B_PROJECT A770B_REFUSE A770B_DATA A770B_GPU_MATCH"
# A770B_PROFILES_FILE and A770B_VRAM_CAP_GIB are no longer exported by env.sh (they must not cross a process
# boundary, so a script that sources env.sh once in one mode and again in another, in a child process, resolves the
# child's own registry and cap). Being plain shell variables now, they are not caught by `compgen -A export` and
# survive a `(...)` subshell's fork from this baseline like any other local variable, so _iso unsets them by name too.
_iso(){ local v; for v in $(compgen -A export A770B_); do case " $A770B_KEEP " in *" $v "*) ;; *) unset "$v";; esac; done; unset A770B_PROFILES_FILE A770B_VRAM_CAP_GIB; }
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
# a tracked change longer than the 400 lines the report shows must not abort the capture (it did: head closed the pipe, git died, pipefail stopped the script before the reset)
seq 1 600 > "$r/big.txt"; ( cd "$r" && git add big.txt && git -c user.name=t -c user.email=t@t commit -q -m big ); seq 1 600 | sed 's/$/ changed/' > "$r/big.txt"
A770B_REFUSE=/nonexistent bash "$here/harness/capture_task.sh" selftest-big "$r" "$t/build.log" >/dev/null 2>&1; bc=$?
if [ "$bc" = 0 ] && grep -q '## server-side timings' "$A770B_DATA/results/selftest-big.task.md" && [ "$(seat_dirty "$r" | wc -l)" = 0 ]; then echo "ok   capture: a change over 400 lines is captured whole and the seat is reset"; else echo "FAIL capture aborted on a long diff (exit $bc) or left the seat dirty"; fail=1; fi
echo "MARKER-$$" > "$t/secret"; ln -s "$t/secret" "$r/leak"
A770B_REFUSE=/nonexistent bash "$here/harness/capture_task.sh" selftest-link "$r" "$t/build.log" >/dev/null 2>&1
if grep -q "MARKER-$$" "$A770B_DATA/results/selftest-link.task.md" "$A770B_DATA/results/selftest-link.patch" 2>/dev/null; then echo "FAIL capture read through a symlink"; fail=1; else echo "ok   capture: a symlink is named, never read"; fi
grep -q 'run lock:' "$here/skills/local-build/scripts/local-build.sh" && grep -q '"seat: ' "$here/skills/local-build/scripts/local-build.sh" || { echo "FAIL status does not report the lock and the seat"; fail=1; }
# the run specification: the renderer refuses what the floor must not admit (I2), renders the floor after every addition
# (I1, I9), the build script accepts the long profile (I10), and the capture keeps the snapshot and the echo (I8)
RP="$here/harness/render_profile.py"; TPL="$here/config/opencode.profile.template.jsonc"; sd="$t/specseat"; mkdir -p "$sd"; echo "card text" > "$sd/card.md"
spec_refused(){ printf '%s' "$2" > "$t/spec.json"; if python3 "$RP" check --spec "$t/spec.json" --seat "$sd" 2>/dev/null; then echo "FAIL spec: $1 was admitted"; fail=1; else echo "ok   spec: $1 is refused"; fi; }
spec_refused "an unknown key" '{"skills":[]}'
spec_refused "a nested unknown key" '{"verify":{"timeout":5}}'
spec_refused "a git pattern" '{"bash_allow":["git push *"]}'
spec_refused "a bare wildcard" '{"bash_allow":["*"]}'
spec_refused "a card outside the seat" '{"card":"../spec.json"}'
spec_refused "a card over the limit" "{\"card\":{\"text\":\"$(head -c 8001 /dev/zero | tr '\0' x)\"}}"
ln -s "$t/spec.json" "$t/spec-link.json"; printf '{}' > "$t/spec.json"
if python3 "$RP" check --spec "$t/spec-link.json" --seat "$sd" 2>/dev/null; then echo "FAIL spec: a symlinked specification was admitted"; fail=1; else echo "ok   spec: a symlinked specification is refused"; fi
printf '{"bash_allow":["make check"],"card":"card.md","scope":{"edit":["a.py"]}}' > "$t/spec.json"
if python3 "$RP" render --template "$TPL" --out "$t/p.jsonc" --baseurl u --apikey k-selftest --ctx 1 --output 1 --name fast --spec "$t/spec.json" --seat "$sd" --echo "$t/p.echo.json" 2>/dev/null; then
  python3 - "$t/p.jsonc" "$t/p.echo.json" <<'PY' || fail=1
import json,sys,re
t=open(sys.argv[1]).read(); b=t[t.index('"bash": {'):]; e=json.load(open(sys.argv[2]))
ok=True
if not (b.index('"make check": "allow"') < b.index('"git commit*": "deny"')): print("FAIL floor: the caller's pattern is not before the floor"); ok=False
last_allow=b.rfind('"allow"'); first_floor=b.index('"git commit*": "deny"')
if not (last_allow < first_floor): print("FAIL floor: an allow renders after the floor"); ok=False
d=json.loads("\n".join(l for l in t.splitlines() if not l.lstrip().startswith("//")))
if d["permission"]["edit"]!={"*":"deny","a.py":"allow"}: print("FAIL scope: edit rules wrong"); ok=False
if d["agent"]["local-builder"]["prompt"].strip()!="card text": print("FAIL card: prompt is not the card"); ok=False
if "k-selftest" in json.dumps(e): print("FAIL echo: the key leaked into the echo"); ok=False
if len(e.get("rendered_sha256",""))!=64: print("FAIL echo: no rendered hash"); ok=False
print("ok   spec: the caller's pattern renders before the floor, the floor after every allow, the card and the scope land, the echo carries the hash and not the key" if ok else "FAIL spec render")
sys.exit(0 if ok else 1)
PY
else echo "FAIL spec: a valid specification was refused"; fail=1; fi
grep -q 'a770b_is_profile "\$PROFILE"' "$here/harness/build_local.sh" && echo "ok   build: the long profile is accepted" || { echo "FAIL build_local.sh rejects the long profile"; fail=1; }
printf '%s\n' "$t/p.echo.json" > "$A770B_DATA/logs/last-build.echo.path"
A770B_REFUSE=/nonexistent A770B_SPEC="$t/spec.json" bash "$here/harness/capture_task.sh" selftest-spec "$r" "$t/build.log" >/dev/null 2>&1
if [ -f "$A770B_DATA/results/selftest-spec.spec.json" ] && [ -f "$A770B_DATA/results/selftest-spec.echo.json" ] && grep -q '## run specification' "$A770B_DATA/results/selftest-spec.task.md" && grep -q 'rendered_sha256' "$A770B_DATA/results/selftest-spec.task.md"; then echo "ok   capture: the specification and its echo are kept beside the patch and printed"; else echo "FAIL capture did not keep or print the specification"; fail=1; fi
# the linter over every bash file, when a shellcheck can be found: on PATH, or through uvx (shellcheck-py bundles the binary, --offline so the cache decides); inside the sandbox neither exists, and the check reports itself skipped rather than failing offline
if command -v shellcheck >/dev/null 2>&1; then SC="shellcheck"; elif command -v uvx >/dev/null 2>&1 && uvx --offline --from shellcheck-py shellcheck --version >/dev/null 2>&1; then SC="uvx --offline --from shellcheck-py shellcheck"; else SC=""; fi
if [ -n "$SC" ]; then
  if $SC -S warning "$here"/harness/*.sh "$here"/skills/local-build/scripts/*.sh "$here"/tests/*.sh "$here"/render_readme.sh "$here"/release.sh; then echo "ok   shellcheck: no finding at warning level or above"; else echo "FAIL shellcheck: findings at warning level or above, listed above"; fail=1; fi
else echo "skip shellcheck: none on PATH and none in the uv cache (install shellcheck, or once online: uvx --from shellcheck-py shellcheck --version)"; fi
# KU3 — harness/run_suite.sh: a fake local-build.sh (A770B_LOCAL_BUILD) stands in for the real one, so the runner is
# proved without the card. A throwaway kit/ fixture: three stages (one that verifies PASS, one that verifies FAIL,
# and one commentary-only stage — "counts_toward_pass": false, always PASS, standing in for S0 — that must NOT
# move the totals) plus one reference exercise (kit/reference/reference.json, appended after the stages); the
# fixture never touches the real kit/ another unit is writing. Checks the runner's own per-stage export of
# kit/seat/ (kit/hidden/solutions/<id>/ pasted for every preceding stage — s0-pass's solution file must be in the
# seat by the time s1-fail runs, s1-fail's own solution by the time s2-note runs, and ref-cpp-example's export
# must carry neither, being a plain export — the fake local-build.sh's run case `ls`s the seat it was given so
# this can be checked), its results JSON (one PASS, one FAIL, the failing id named, the commentary stage present
# but excluded, the reference entry present, each stage's "seat_state" naming what was pasted) and that
# suite_report.py --json reports on it.
ku3="$t/ku3"; mkdir -p "$ku3/seat" "$ku3/tasks" "$ku3/reference" "$ku3/hidden/solutions/s0-pass" "$ku3/hidden/solutions/s1-fail" "$ku3/seat-ls"
echo "fixture" > "$ku3/seat/README.md"
echo "s0-pass solved" > "$ku3/hidden/solutions/s0-pass/SOLVED-s0-pass.marker"
echo "s1-fail solved" > "$ku3/hidden/solutions/s1-fail/SOLVED-s1-fail.marker"
for st in s0-pass s1-fail s2-note ref-cpp-example; do
  echo "brief $st" > "$ku3/tasks/$st.md"
  printf '{}' > "$ku3/tasks/$st.spec.json"
done
cat > "$ku3/suite.json" <<JSON
{"suite": "SUITE-TEST", "stages": [
  {"id": "s0-pass", "language": "python", "brief": "$ku3/tasks/s0-pass.md", "spec": "$ku3/tasks/s0-pass.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 100, "rubric": null}},
  {"id": "s1-fail", "language": "python", "brief": "$ku3/tasks/s1-fail.md", "spec": "$ku3/tasks/s1-fail.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 100, "rubric": null}},
  {"id": "s2-note", "language": "prose", "brief": "$ku3/tasks/s2-note.md", "spec": "$ku3/tasks/s2-note.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 60, "rubric": null}, "counts_toward_pass": false}
]}
JSON
cat > "$ku3/reference/reference.json" <<JSON
{"entries": [{"id": "ref-cpp-example", "language": "cpp", "brief": "$ku3/tasks/ref-cpp-example.md",
  "spec": "$ku3/tasks/ref-cpp-example.spec.json", "grader": {"working": null, "conformance": null, "budget_lines": 50, "rubric": null}}]}
JSON
rm -f "$ku3/argv.log"
cat > "$ku3/fake-local-build.sh" <<'FAKE'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$KU3_ARGV_LOG"
case "${1:-}" in
  run)
    shift; WT="$1"; BRIEF="$2"; shift 2
    label="fake-$(basename "$BRIEF" .md)"
    ls "$WT" > "$KU3_SEAT_LS_DIR/$label.ls" 2>/dev/null || true
    mkdir -p "$A770B_DATA/results"
    { echo "# fake capture"; echo "requests=2 prompt_tokens_total=100 gen_tokens_total=40"; } > "$A770B_DATA/results/$label.task.md"
    { echo "--- a/x.py"; echo "+++ b/x.py"; echo "+line one"; echo "+line two"; } > "$A770B_DATA/results/$label.patch"
    echo "▶ capture: $A770B_DATA/results/$label.task.md — review it before merging; the worktree has been reset to clean."
    exit 0 ;;
  verify)
    shift; label="$1"; shift
    case "$label" in *fail*) rc=1; verdict="FAIL (exit 1)" ;; *) rc=0; verdict="PASS (exit 0)" ;; esac
    mkdir -p "$A770B_DATA/results"; echo "verdict: $verdict" > "$A770B_DATA/results/$label.verify.md"
    exit "$rc" ;;
  stop|serve) exit 0 ;;
  *) exit 2 ;;
esac
FAKE
chmod +x "$ku3/fake-local-build.sh"
rm -f "$A770B_DATA"/results/long-suite-*.json
out=$(A770B_LOCAL_BUILD="$ku3/fake-local-build.sh" KU3_ARGV_LOG="$ku3/argv.log" KU3_SEAT_LS_DIR="$ku3/seat-ls" bash "$here/harness/run_suite.sh" long "$t/ku3-seat" --suite "$ku3/suite.json" 2>&1); rc=$?
res=$(ls -t "$A770B_DATA"/results/long-suite-*.json 2>/dev/null | head -1)
if [ "$rc" = 0 ] && [ -n "$res" ] && python3 - "$res" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ids = [x["id"] for x in d["stages"]]
assert ids == ["s0-pass", "s1-fail", "s2-note", "ref-cpp-example"], ids
s = {x["id"]: x for x in d["stages"]}
assert s["s0-pass"]["working"] is True, s["s0-pass"]
assert s["s1-fail"]["working"] is False, s["s1-fail"]
assert s["s2-note"]["working"] is True and s["s2-note"]["counts_toward_pass"] is False, s["s2-note"]
assert s["ref-cpp-example"]["working"] is True, s["ref-cpp-example"]
assert s["s0-pass"]["seat_state"] == [], s["s0-pass"]
assert s["s1-fail"]["seat_state"] == ["s0-pass"], s["s1-fail"]
assert s["s2-note"]["seat_state"] == ["s0-pass", "s1-fail"], s["s2-note"]
assert s["ref-cpp-example"]["seat_state"] == [], s["ref-cpp-example"]
sys.exit(0)
PY
then echo "ok   suite: run_suite.sh records one PASS, one FAIL (s1-fail named), one excluded commentary stage (s2-note), the reference entry, and each stage's seat_state"
else echo "FAIL suite: run_suite.sh output/results wrong (rc=$rc res=$res)"; printf '%s\n' "$out" | tail -20; fail=1
fi
# the per-stage seat export: s1-fail's seat carried s0-pass's solution file (stage two saw stage one's solved
# work), s2-note's carried both, and ref-cpp-example (a reference exercise) is a plain export carrying neither —
# the fake local-build.sh's run case recorded an `ls` of the seat it was handed for each stage under $ku3/seat-ls.
if grep -q '^SOLVED-s0-pass\.marker$' "$ku3/seat-ls/fake-s0-pass.ls" 2>/dev/null; then
  echo "FAIL suite: s0-pass's own (first) seat already carried a solution file"; fail=1
elif ! grep -q '^SOLVED-s0-pass\.marker$' "$ku3/seat-ls/fake-s1-fail.ls" 2>/dev/null; then
  echo "FAIL suite: s1-fail's seat did not carry s0-pass's solution file — $ku3/seat-ls/fake-s1-fail.ls"; fail=1
elif ! { grep -q '^SOLVED-s0-pass\.marker$' "$ku3/seat-ls/fake-s2-note.ls" 2>/dev/null && grep -q '^SOLVED-s1-fail\.marker$' "$ku3/seat-ls/fake-s2-note.ls" 2>/dev/null; }; then
  echo "FAIL suite: s2-note's seat did not carry both preceding stages' solution files — $ku3/seat-ls/fake-s2-note.ls"; fail=1
elif grep -qE '^SOLVED-(s0-pass|s1-fail)\.marker$' "$ku3/seat-ls/fake-ref-cpp-example.ls" 2>/dev/null; then
  echo "FAIL suite: ref-cpp-example's seat (a reference exercise) carried a main-suite solution file — it must be a plain export"; fail=1
else
  echo "ok   suite: the stage-two seat saw the stage-one solution file (and stage three both, and the reference exercise neither)"
fi
# Defect 3 (export_reference_seat half): $t/ku3-seat is whatever the LAST stage of the run above left in place —
# ref-cpp-example, a reference exercise, so export_reference_seat's own commit — and the fake local-build.sh never
# touches or resets it, so it still reads exactly as exported. It must carry the build-artefact .gitignore.
if [ -f "$t/ku3-seat/.gitignore" ] && grep -qx 'CMakeFiles/' "$t/ku3-seat/.gitignore" && grep -qx '__pycache__/' "$t/ku3-seat/.gitignore"; then
  echo "ok   suite: export_reference_seat's export (ref-cpp-example) carries the build-artefact .gitignore"
else
  echo "FAIL suite: $t/ku3-seat has no .gitignore, or it is missing an expected pattern"; fail=1
fi
# the fake local-build.sh's own argv, recorded per call: a "run ... --spec ... --profile long" line and a
# "verify fake-<id>" line per stage, in order, and no reviewer step (stop/serve) since --reviewer was not given
if [ -f "$ku3/argv.log" ] && python3 - "$ku3/argv.log" <<'PY'
import re, sys
lines = [l for l in open(sys.argv[1]).read().splitlines() if l.strip()]
ids = ["s0-pass", "s1-fail", "s2-note", "ref-cpp-example"]
assert len(lines) == 2 * len(ids), lines
run_re = re.compile(r"^run \S+ \S+ --spec \S+ --profile long$")
for i, id_ in enumerate(ids):
    run_line, verify_line = lines[2 * i], lines[2 * i + 1]
    assert run_re.match(run_line), (id_, run_line)
    assert verify_line.startswith(f"verify fake-{id_} "), (id_, verify_line)
assert not any(l.split()[0] in ("stop", "serve") for l in lines), "reviewer step present with no --reviewer"
sys.exit(0)
PY
then echo "ok   suite: the fake local-build.sh's argv shows run --spec --profile then verify <label>, in order, no reviewer step"
else echo "FAIL suite: the recorded argv did not match (see $ku3/argv.log)"; [ -f "$ku3/argv.log" ] && cat "$ku3/argv.log"; fail=1
fi
# KU3b — Defect 1: --stages must filter kit/reference/reference.json's own entries exactly as it filters
# kit/suite.json's stages (an exact id, or its prefix before the first "-"). Before the fix, --stages left the
# reference exercises entirely unfiltered, so a request for one ordinary stage silently pulled in all three of
# them too — an hour of card time nobody asked for. Same ku3 fixture, reused: a filter naming only the main
# stage s1-fail must run s1-fail alone (not ref-cpp-example), a filter naming only the reference exercise
# ref-cpp-example must run it alone (no main stage matches it), and both must be named in a line printed before
# the first stage starts.
rm -f "$ku3/argv.log"
outF=$(A770B_LOCAL_BUILD="$ku3/fake-local-build.sh" KU3_ARGV_LOG="$ku3/argv.log" KU3_SEAT_LS_DIR="$ku3/seat-ls" bash "$here/harness/run_suite.sh" long "$t/ku3-seat-b" --suite "$ku3/suite.json" --stages s1-fail 2>&1); rcF=$?
if [ "$rcF" = 0 ] && printf '%s\n' "$outF" | grep -q -- '--stages s1-fail selects: s1-fail' \
  && [ -f "$ku3/argv.log" ] && [ "$(grep -c '^run ' "$ku3/argv.log")" = 1 ] \
  && grep -q "$ku3/tasks/s1-fail.md" "$ku3/argv.log" && ! grep -q 'ref-cpp-example' "$ku3/argv.log"
then echo "ok   suite: --stages s1-fail filters the reference exercises exactly as the main stages (s1-fail alone ran, ref-cpp-example did not) and announces the selection before the first stage starts"
else echo "FAIL suite: --stages s1-fail did not select exactly s1-fail (see $ku3/argv.log, output below)"; printf '%s\n' "$outF" | tail -10; fail=1
fi
rm -f "$ku3/argv.log"
outF2=$(A770B_LOCAL_BUILD="$ku3/fake-local-build.sh" KU3_ARGV_LOG="$ku3/argv.log" KU3_SEAT_LS_DIR="$ku3/seat-ls" bash "$here/harness/run_suite.sh" long "$t/ku3-seat-c" --suite "$ku3/suite.json" --stages ref-cpp-example 2>&1); rcF2=$?
if [ "$rcF2" = 0 ] && printf '%s\n' "$outF2" | grep -q -- '--stages ref-cpp-example selects: ref-cpp-example' \
  && [ -f "$ku3/argv.log" ] && [ "$(grep -c '^run ' "$ku3/argv.log")" = 1 ] && grep -q 'ref-cpp-example' "$ku3/argv.log"
then echo "ok   suite: --stages also selects a reference exercise on its own (ref-cpp-example alone, no main stage matched it)"
else echo "FAIL suite: --stages ref-cpp-example did not select exactly the reference exercise (see $ku3/argv.log, output below)"; printf '%s\n' "$outF2" | tail -10; fail=1
fi
if [ -n "$res" ]; then
  # four stages are in the results (s0-pass, s1-fail, s2-note, ref-cpp-example) but s2-note carries
  # "counts_toward_pass": false, so the totals below must read as if it were never run: briefs=3, runs=3,
  # passed=2 (s0-pass and ref-cpp-example), exactly as if only the three counted stages existed.
  jout=$(python3 "$here/harness/suite_report.py" "$res" --json 2>/dev/null)
  if printf '%s' "$jout" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
need = {"briefs", "runs", "passed", "timeouts", "mean_wall_s", "source"}
assert set(d.keys()) == need, d
assert d["briefs"] == 3 and d["runs"] == 3 and d["passed"] == 2 and d["timeouts"] == 0, d
' 2>/dev/null; then echo "ok   suite: suite_report.py --json excludes the counts_toward_pass:false stage from all totals (timeouts=0, none of this fixture's stages timed out)"
  else echo "FAIL suite: suite_report.py --json wrong: $jout"; fail=1
  fi
else echo "FAIL suite: no results file to report on"; fail=1
fi
# KU10 — Defect 2: a capture that genuinely carries no server-side timings line at all (the run's wall clock
# reached the profile's own timeout before capture_task.sh's own SLOG parse ever had anything to report) must be
# recorded with requests/prompt_tokens/gen_tokens = null, never 0, and the stage's own "outcome" must read
# "timeout", not a plain failure — so a reader can tell a slow model from a wrong one — carried through into
# suite_report.py's table (TIMEOUT, not FAIL) and its --json (a "timeouts" count). A770B_LONG_TIMEOUT=1 shrinks
# the profile's own timeout window to one second so a two-second fake run trips it without an hour of card time.
# This fixture's own fresh kit/seat/ export (export_kit_seat, one main stage, never touched by the fake run) also
# proves the other half of Defect 3: the .gitignore lands there too, not only from export_reference_seat above.
kut="$t/kutimeout"; mkdir -p "$kut/tasks" "$kut/seat"
echo "fixture" > "$kut/seat/README.md"
echo "brief" > "$kut/tasks/t0-slow.md"; printf '{}' > "$kut/tasks/t0-slow.spec.json"
cat > "$kut/suite.json" <<JSON
{"suite": "SUITE-TIMEOUT", "stages": [
  {"id": "t0-slow", "language": "python", "brief": "$kut/tasks/t0-slow.md", "spec": "$kut/tasks/t0-slow.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 100, "rubric": null}}
]}
JSON
cat > "$kut/fake-local-build.sh" <<'FAKE'
#!/usr/bin/env bash
set -uo pipefail
case "${1:-}" in
  run)
    shift; WT="$1"; BRIEF="$2"; shift 2
    sleep 2   # longer than A770B_LONG_TIMEOUT=1 below, so this stage's wall_s trips the timeout comparison
    label="fake-$(basename "$BRIEF" .md)"
    mkdir -p "$A770B_DATA/results"
    echo "# fake capture — no server-side timings line at all (the run never got that far)" > "$A770B_DATA/results/$label.task.md"
    { echo "--- a/x.py"; echo "+++ b/x.py"; echo "+line one"; } > "$A770B_DATA/results/$label.patch"
    echo "▶ capture: $A770B_DATA/results/$label.task.md — review it before merging; the worktree has been reset to clean."
    exit 0 ;;
  verify) exit 1 ;;   # the stage never finished — verify fails, same as any incomplete work
  stop|serve) exit 0 ;;
  *) exit 2 ;;
esac
FAKE
chmod +x "$kut/fake-local-build.sh"
rm -f "$A770B_DATA"/results/long-suite-*.json
outT=$(A770B_LOCAL_BUILD="$kut/fake-local-build.sh" A770B_LONG_TIMEOUT=1 bash "$here/harness/run_suite.sh" long "$t/kutimeout-seat" --suite "$kut/suite.json" 2>&1); rcT=$?
resT=$(ls -t "$A770B_DATA"/results/long-suite-*.json 2>/dev/null | head -1)
if [ "$rcT" = 0 ] && [ -n "$resT" ] && python3 - "$resT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
s = d["stages"][0]
assert s["id"] == "t0-slow", s
assert s["requests"] is None and s["prompt_tokens"] is None and s["gen_tokens"] is None, s
assert s["outcome"] == "timeout", s
assert s["working"] is False, s
sys.exit(0)
PY
then
  tbl=$(python3 "$here/harness/suite_report.py" "$resT" 2>&1)
  jsonT=$(python3 "$here/harness/suite_report.py" "$resT" --json 2>/dev/null)
  if printf '%s\n' "$tbl" | grep -qE '^t0-slow +TIMEOUT ' && printf '%s' "$jsonT" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
assert d["timeouts"] == 1, d
' 2>/dev/null
  then echo "ok   suite: a capture with no timings line at all records requests/tokens as null (never 0) and the stage outcome as timeout; suite_report.py shows TIMEOUT in its table and counts it in --json"
  else echo "FAIL suite: suite_report.py did not show the timeout distinction — table: $tbl / json: $jsonT"; fail=1
  fi
else
  echo "FAIL suite: run_suite.sh did not record null requests/tokens + a timeout outcome for a captureless-timings stage (rc=$rcT res=$resT)"; printf '%s\n' "$outT" | tail -20; fail=1
fi
# Defect 3 (export_kit_seat half): the main-suite export above (kutimeout-seat) must carry the same .gitignore.
if [ -f "$t/kutimeout-seat/.gitignore" ] && grep -qx 'CMakeFiles/' "$t/kutimeout-seat/.gitignore" && grep -qx '__pycache__/' "$t/kutimeout-seat/.gitignore"; then
  echo "ok   suite: export_kit_seat's export also carries the build-artefact .gitignore"
else
  echo "FAIL suite: $t/kutimeout-seat has no .gitignore, or it is missing an expected pattern"; fail=1
fi
# Defect 3, the part that must NOT change: seat_dirty (guard.sh) never hides an ignored file on principle (a model
# could plant a disguised .venv or config file the same way) — planting build-artefact-shaped files that the new
# .gitignore matches must still show up there — while capture_task.sh's own "new files"/patch loop, respecting
# that same .gitignore, must not see them as the model's edits. Both checked on the real (unmodified)
# seat_dirty and capture_task.sh, over the real seat export_kit_seat just produced above — never a re-typed copy.
mkdir -p "$t/kutimeout-seat/build-bst/CMakeFiles" "$t/kutimeout-seat/python/logstats/__pycache__"
echo "generated" > "$t/kutimeout-seat/build-bst/CMakeFiles/foo.txt"
echo "generated" > "$t/kutimeout-seat/build-bst/CMakeCache.txt"
: > "$t/kutimeout-seat/python/logstats/__pycache__/stats.cpython-314.pyc"
echo ".o" > "$t/kutimeout-seat/thing.o"
dseat=$(seat_dirty "$t/kutimeout-seat")
if printf '%s\n' "$dseat" | grep -q 'CMakeFiles/foo.txt' && printf '%s\n' "$dseat" | grep -q 'thing.o'; then
  echo "ok   suite: seat_dirty still reports the planted build artefacts even though the seat's own .gitignore now covers them (it never hides ignored files — see its own header comment)"
else
  echo "FAIL suite: seat_dirty hid a planted build artefact it must still report:"; printf '%s\n' "$dseat"; fail=1
fi
A770B_REFUSE=/nonexistent bash "$here/harness/capture_task.sh" ku10-gitignore "$t/kutimeout-seat" "$t/build.log" >/dev/null 2>&1
cap="$A770B_DATA/results/ku10-gitignore.task.md"; patch="$A770B_DATA/results/ku10-gitignore.patch"
# capture_task.sh's "## new files" section (git ls-files --others --exclude-standard, per-file "### <path>" dumps)
# must come back empty — none of the planted artefacts are untracked-and-not-ignored any more — and neither must
# the patch (built from the same listing); the SEPARATE "## ignored files ... left behind" section is expected
# and correct to still name them (informational only, by capture_task.sh's own header comment: "removed by the
# reset, never applied by verify") — that section is not what this defect was ever about.
newfiles=$(sed -n '/^## new files$/,/^## ignored files/p' "$cap" 2>/dev/null | sed '1d;$d')
if [ -f "$cap" ] && [ -z "$(printf '%s' "$newfiles" | tr -d '[:space:]')" ] \
  && ! grep -qE '(CMakeFiles/foo\.txt|CMakeCache\.txt|\bthing\.o\b|stats\.cpython)' "$patch" 2>/dev/null \
  && grep -q 'build-bst/CMakeFiles/foo.txt' "$cap"
then echo "ok   suite: capture_task.sh, respecting the same .gitignore, never lists or diffs in the planted build artefacts as the model's own new files (they still show, informationally, under ignored files left behind)"
else echo "FAIL suite: capture_task.sh's new-files section or patch included a gitignored build artefact — new files section: [$newfiles]"; fail=1
fi
[ "$(seat_dirty "$t/kutimeout-seat" | wc -l)" = 0 ] && echo "ok   suite: capture_task.sh's reset leaves the seat clean again, build artefacts included" || { echo "FAIL suite: seat left dirty after capture's reset"; fail=1; }
# KU9 — suite_report.py's delivered speed: a fake capture file (the shape harness/capture_task.sh's server-side
# timings section actually writes) beside a results JSON whose only per-stage capture record is its label, proving
# the fallback path (<A770B_DATA>/results/<label>.task.md) and the two lines' parse.
ku9="$t/ku9"; mkdir -p "$ku9" "$A770B_DATA/results"
cat > "$A770B_DATA/results/ku9-stage.task.md" <<'CAP'
requests=3 prompt_tokens_total=9000 gen_tokens_total=300
TTFT ms (prompt eval): median=210 p90=260 max=300   largest prompt=3200 tokens
TPOT ms: median=22.0 p90=26.0   decode tok/s median=45.5
prefill tok/s over all prompts=612
CAP
cat > "$ku9/results.json" <<'JSON'
{"instrument": "SUITE-TEST", "stages": [{"id": "ku9-stage", "label": "ku9-stage", "working": true, "wall_s": 9.0}]}
JSON
sout=$(python3 "$here/harness/suite_report.py" "$ku9/results.json" 2>&1)
jout9=$(python3 "$here/harness/suite_report.py" "$ku9/results.json" --json 2>/dev/null)
if printf '%s\n' "$sout" | grep -q 'delivered (ku9-stage): prefill 612.0 tok/s · decode 45.5 tok/s' \
  && printf '%s\n' "$sout" | grep -q 'delivered: prefill 612.0 tok/s · decode 45.5 tok/s' \
  && printf '%s' "$jout9" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
assert d["delivered"] == {"prefill_tps": 612.0, "decode_tps": 45.5, "source": "results.json"}, d
' 2>/dev/null
then echo "ok   selftest: suite_report.py reads a capture (fallen back to <label>.task.md) and reports delivered speed, in the table and --json alike"
else echo "FAIL selftest: suite_report.py delivered speed did not parse — stdout: $sout"; fail=1
fi
# the profiles registry: config/profiles.json is the single source now that env.sh evals `harness/profiles.py env`
python3 "$here/harness/profiles.py" check >/dev/null 2>&1 && echo "ok   profiles: the registry checks" || { echo "FAIL profiles: profiles.py check failed"; fail=1; }
[ "$A770B_PROFILES" = "long fast serious" ] && [ "$A770B_DEFAULT_PROFILE" = "long" ] && echo "ok   profiles: names and default come from the registry" || { echo "FAIL profiles: A770B_PROFILES='$A770B_PROFILES' A770B_DEFAULT_PROFILE='$A770B_DEFAULT_PROFILE'"; fail=1; }
[ "$(a770b_profile_var long CTX)" = "262144" ] && printf '%s' "$(a770b_profile_var serious EXTRA)" | grep -q reasoning_effort && echo "ok   profiles: the helper reads the registry's variables" || { echo "FAIL profiles: a770b_profile_var did not read the registry"; fail=1; }
[ "$( (export A770B_LONG_CTX=4096; . "$here/harness/env.sh" >/dev/null 2>&1; a770b_profile_var long CTX) )" = "4096" ] && echo "ok   profiles: the environment wins over the registry" || { echo "FAIL profiles: A770B_LONG_CTX=4096 did not win over the registry default"; fail=1; }
# ── the card mode (I1-I4, I5a, I7b, I8-I10): a temporary registry/override tree under $t so nothing here reads the
#    machine's own ~/.config/a770-builder/builder(.<mode>).env; every check that switches the mode, the registry file
#    or the PATH runs in its own subshell (see _iso above), so nothing — PATH included — leaks into this shell.
( _iso; export XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1 && [ "$A770B_CARD_MODE" = display ] ) \
  && echo "ok   mode: I1 unset defaults to display" || { echo "FAIL mode: I1 unset did not default to display"; fail=1; }
( _iso; export A770B_CARD_MODE=inference XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1 && [ "$A770B_CARD_MODE" = inference ] ) \
  && echo "ok   mode: I1 inference is accepted" || { echo "FAIL mode: I1 inference was not accepted"; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=x XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" ) 2>&1 ); rc=$?
{ [ "$rc" = 2 ] && printf '%s' "$out" | grep -q "must be display or inference (it is 'x')"; } \
  && echo "ok   mode: I1 an invalid mode refuses (exit 2) and names it" || { echo "FAIL mode: I1 x -> rc=$rc: $out"; fail=1; }
( _iso; export XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1
  case "$A770B_PROFILES_FILE" in *config/profiles.json) : ;; *) exit 1;; esac
  [ "$A770B_VRAM_CAP_GIB" = 13.0 ] && [ "$A770B_PROFILES" = "long fast serious" ] && [ "$A770B_DEFAULT_PROFILE" = long ]
) && echo "ok   mode: I2 the v0.1.4 defaults hold with the mode unset" || { echo "FAIL mode: I2 the display defaults did not all hold"; fail=1; }
( _iso; export A770B_CARD_MODE=inference XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1
  case "$A770B_PROFILES_FILE" in *config/profiles.inference.json) : ;; *) exit 1;; esac
  [ "$A770B_VRAM_CAP_GIB" = 15.3 ]
) && echo "ok   mode: I3 inference defaults to its own registry and a 15.3 cap" || { echo "FAIL mode: I3 inference defaults did not hold"; fail=1; }
( _iso; export A770B_CARD_MODE=inference A770B_VRAM_CAP_GIB=14.9 XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1; [ "$A770B_VRAM_CAP_GIB" = 14.9 ] ) \
  && echo "ok   mode: I3 the environment's cap wins over the inference default" || { echo "FAIL mode: I3 A770B_VRAM_CAP_GIB=14.9 did not win"; fail=1; }
( _iso; export A770B_CARD_MODE=inference A770B_PROFILES_FILE="$here/config/profiles.json" XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1; [ "$A770B_PROFILES_FILE" = "$here/config/profiles.json" ] ) \
  && echo "ok   mode: I3 the environment's registry file wins over the inference default" || { echo "FAIL mode: I3 A770B_PROFILES_FILE override did not win"; fail=1; }
# A770B_PROFILES_FILE and A770B_VRAM_CAP_GIB must not be exported: a process that sources env.sh once in display mode,
# then a CHILD process that switches to inference mode and sources env.sh again, must resolve that child's own
# registry and cap fresh, not inherit the parent's already-computed display values across the process boundary
( _iso; export XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1
  bash -c 'export A770B_CARD_MODE=inference; . "$A770B_PROJECT/harness/env.sh" >/dev/null 2>&1
    case "$A770B_PROFILES_FILE" in *config/profiles.inference.json) : ;; *) exit 1;; esac
    [ "$A770B_VRAM_CAP_GIB" = 15.3 ]'
) && echo "ok   mode: a second source in another mode takes that mode's registry and cap" \
  || { echo "FAIL mode: a second source in another mode did not take that mode's registry/cap"; fail=1; }
i4a="$t/i4a/xdg"; mkdir -p "$i4a/a770-builder"
printf 'A770B_CARD_MODE=inference\nA770B_VRAM_CAP_GIB=15.0\n' > "$i4a/a770-builder/builder.env"
printf 'A770B_VRAM_CAP_GIB=14.9\n' > "$i4a/a770-builder/builder.inference.env"
( _iso; export XDG_CONFIG_HOME="$i4a"; . "$here/harness/env.sh" >/dev/null 2>&1; [ "$A770B_VRAM_CAP_GIB" = 14.9 ] ) \
  && echo "ok   mode: I4 the per-mode override file wins over builder.env" || { echo "FAIL mode: I4 the per-mode file did not win over builder.env"; fail=1; }
( _iso; export A770B_VRAM_CAP_GIB=14.8 XDG_CONFIG_HOME="$i4a"; . "$here/harness/env.sh" >/dev/null 2>&1; [ "$A770B_VRAM_CAP_GIB" = 14.8 ] ) \
  && echo "ok   mode: I4 the calling environment wins over the per-mode file" || { echo "FAIL mode: I4 A770B_VRAM_CAP_GIB=14.8 did not win"; fail=1; }
i4c="$t/i4c/xdg"; mkdir -p "$i4c/a770-builder"
printf 'A770B_CARD_MODE=inference\n' > "$i4c/a770-builder/builder.env"
printf 'A770B_VRAM_CAP_GIB=14.7\n' > "$i4c/a770-builder/builder.display.env"
( _iso; export A770B_CARD_MODE=display XDG_CONFIG_HOME="$i4c"; . "$here/harness/env.sh" >/dev/null 2>&1; [ "$A770B_CARD_MODE" = display ] && [ "$A770B_VRAM_CAP_GIB" = 14.7 ] ) \
  && echo "ok   mode: I4 the environment's mode names the per-mode file that lands" || { echo "FAIL mode: I4 builder.display.env did not land"; fail=1; }
i4d="$t/i4d/xdg"; mkdir -p "$i4d/a770-builder"
printf 'A770B_CARD_MODE=display\n' > "$i4d/a770-builder/builder.inference.env"
out=$( ( _iso; export A770B_CARD_MODE=inference XDG_CONFIG_HOME="$i4d"; . "$here/harness/env.sh" ) 2>&1 ); rc=$?
{ [ "$rc" = 2 ] && printf '%s' "$out" | grep -q "cannot switch the mode"; } \
  && echo "ok   mode: I4 a per-mode file naming the other mode is refused" || { echo "FAIL mode: I4 rc=$rc: $out"; fail=1; }
[ "$(a770b_profile_var long KV_V)" = q8_0 ] \
  && echo "ok   mode: I5a a770b_profile_var long KV_V defaults to kv under the display baseline" \
  || { echo "FAIL mode: I5a a770b_profile_var long KV_V='$(a770b_profile_var long KV_V)'"; fail=1; }
out=$(bash "$here/skills/local-build/scripts/local-build.sh" status 2>&1)
printf '%s' "$out" | grep -q '· mode display · registry ' \
  && echo "ok   mode: I8 status prints the mode and registry in display" || { echo "FAIL mode: I8 status (display): $out"; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=inference; bash "$here/skills/local-build/scripts/local-build.sh" status ) 2>&1 )
printf '%s' "$out" | grep -q '· mode inference · registry ' \
  && echo "ok   mode: I8 status prints the mode and registry in inference" || { echo "FAIL mode: I8 status (inference): $out"; fail=1; }
out=$( ( bash "$here/skills/local-build/scripts/local-build.sh" run /nonexistent-seat /nonexistent-brief.md --profile fast ) 2>&1 ); rc=$?
{ [ "$rc" = 2 ] && printf '%s' "$out" | grep -q "worktree does not exist: /nonexistent-seat"; } \
  && echo "ok   mode: I7b --profile fast is accepted, the run fails at the guard, naming the seat" || { echo "FAIL mode: I7b fast rc=$rc: $out"; fail=1; }
out=$( ( bash "$here/skills/local-build/scripts/local-build.sh" run /nonexistent-seat /nonexistent-brief.md --profile nosuch ) 2>&1 )
printf '%s' "$out" | grep -q "profile must be one of:" \
  && echo "ok   mode: I7b --profile nosuch is refused before the guard" || { echo "FAIL mode: I7b nosuch: $out"; fail=1; }
out=$( ( bash "$here/skills/local-build/scripts/local-build.sh" run /nonexistent-seat /nonexistent-brief.md --fast ) 2>&1 )
printf '%s' "$out" | grep -q "unknown arg --fast" \
  && echo "ok   mode: I7b the removed --fast arm is an unknown argument" || { echo "FAIL mode: I7b --fast: $out"; fail=1; }
grep -q 'mode \$A770B_CARD_MODE · cap \$A770B_VRAM_CAP_GIB GiB' "$here/harness/serve_a770_llamacpp.sh" \
  && grep -q 'A770B_CARD_MODE=inference' "$here/harness/serve_a770_llamacpp.sh" \
  && echo "ok   mode: I10 the server's start and refusal lines name the mode and cap" \
  || { echo "FAIL mode: I10 serve_a770_llamacpp.sh does not name the mode/cap as expected"; fail=1; }
grep -q 'the server died during load' "$here/harness/serve_a770_llamacpp.sh" \
  && echo "ok   harness: serve refuses when the pid dies during load" \
  || { echo "FAIL harness: serve_a770_llamacpp.sh does not name the death message"; fail=1; }
grep -q '4096' "$here/harness/bench_model.sh" && grep -q 'REASONING' "$here/harness/bench_model.sh" \
  && echo "ok   harness: bench_model's probe gate reads REASONING for its 4096 budget" \
  || { echo "FAIL harness: bench_model.sh does not read REASONING for a 4096 gate"; fail=1; }
( _iso; export A770B_LONG_OUTPUT_TOKENS=4242 XDG_CONFIG_HOME="$t/xdg"; . "$here/harness/env.sh" >/dev/null 2>&1
  a770b_render_profile long 4096 "$t/harness-output.jsonc" nokey >/dev/null 2>&1
  grep -q '"output": 4242' "$t/harness-output.jsonc"
) && echo "ok   harness: a770b_render_profile carries the profile's own output_tokens" \
  || { echo "FAIL harness: a770b_render_profile did not carry A770B_LONG_OUTPUT_TOKENS=4242"; fail=1; }
# I9 — doctor's card: and mode: lines; a fake nvtop prints the JSON file $NVTOP_FAKE names, in 'nvtop -s' shape
mkdir -p "$t/bin"
cat > "$t/bin/nvtop" <<'NVEOF'
#!/bin/sh
cat "$NVTOP_FAKE"
NVEOF
chmod +x "$t/bin/nvtop"
mkfake(){ # mkfake <path> <mem_used_bytes...> — one nvtop device per argument, its name containing A770B_GPU_MATCH
  python3 - "$1" "$A770B_GPU_MATCH" "${@:2}" <<'PYEOF'
import json, sys
path, match, *used = sys.argv[1:]
total = 17179869184
devs = [{"device_name": f"Intel {match} Graphics {i}", "mem_total": str(total), "mem_used": u,
         "mem_free": str(total - int(u)), "processes": []} for i, u in enumerate(used)]
json.dump(devs, open(path, "w"))
PYEOF
}
mkfake "$t/nvtop-1g.json" 1073741824
mkfake "$t/nvtop-005g.json" 52428800
mkfake "$t/nvtop-015g.json" 161061273
mkfake "$t/nvtop-2dev.json" 1073741824 1073741824
# case f needs every tool the doctor path uses (python3 above all — env.sh dies without it) but no nvtop; nvtop can
# share a directory with those tools (it does on this host), so a symlink farm of everything but nvtop is the only
# PATH that is both nvtop-free and still lets env.sh, guard.sh and doctor itself run
mkdir -p "$t/bin-no-nvtop"
IFS=':' read -ra _path_dirs <<< "$PATH"
for _d in "${_path_dirs[@]}"; do
  [ -d "$_d" ] || continue
  for _f in "$_d"/*; do
    [ -x "$_f" ] && [ ! -d "$_f" ] || continue
    _b=$(basename "$_f")
    [ "$_b" = nvtop ] && continue
    [ -e "$t/bin-no-nvtop/$_b" ] || ln -s "$_f" "$t/bin-no-nvtop/$_b" 2>/dev/null
  done
done
path_no_nvtop="$t/bin-no-nvtop"
out=$( ( _iso; export A770B_CARD_MODE=inference PATH="$t/bin:$PATH" NVTOP_FAKE="$t/nvtop-1g.json" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 ); rc=$?
{ [ "$rc" = 1 ] && printf '%s' "$out" | grep -q "MISSING mode: inference, but the builder card holds"; } \
  && echo "ok   mode: I9a inference over 0.5 GiB with the server down is MISSING (doctor exits 1)" \
  || { echo "FAIL mode: I9a rc=$rc"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=inference PATH="$t/bin:$PATH" NVTOP_FAKE="$t/nvtop-005g.json" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
printf '%s' "$out" | grep -q "ok   mode: inference; the builder card holds" \
  && echo "ok   mode: I9b inference under 0.5 GiB with the server down is ok" \
  || { echo "FAIL mode: I9b"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=display PATH="$t/bin:$PATH" NVTOP_FAKE="$t/nvtop-005g.json" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
printf '%s' "$out" | grep -q "no desktop measured on it" \
  && echo "ok   mode: I9c display at 0.05 GiB hints that inference mode would give more room" \
  || { echo "FAIL mode: I9c"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=display PATH="$t/bin:$PATH" NVTOP_FAKE="$t/nvtop-015g.json" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
{ printf '%s' "$out" | grep -q "ok   mode: display" && ! printf '%s' "$out" | grep -q "no desktop measured"; } \
  && echo "ok   mode: I9d display at 0.15 GiB gives no hint" \
  || { echo "FAIL mode: I9d"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=display PATH="$t/bin:$PATH" NVTOP_FAKE="$t/nvtop-2dev.json" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
{ printf '%s' "$out" | grep -q "MISSING card: 2 nvtop devices match" \
  && printf '%s' "$out" | grep -q "not measured (no VRAM readings or more than one matching card)" \
  && ! printf '%s' "$out" | grep -q "no desktop measured"; } \
  && echo "ok   mode: I9e two matching devices are MISSING card, and mode reads not measured" \
  || { echo "FAIL mode: I9e"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=display PATH="$path_no_nvtop" A770B_ALLOW_NO_NVTOP=1
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
{ printf '%s' "$out" | grep -q "ok   card: not measured" && printf '%s' "$out" | grep -q "ok   mode: display, not measured" \
  && ! printf '%s' "$out" | grep -q "MISSING card" && ! printf '%s' "$out" | grep -q "MISSING mode"; } \
  && echo "ok   mode: I9f no nvtop with A770B_ALLOW_NO_NVTOP=1 is not measured, never MISSING" \
  || { echo "FAIL mode: I9f"; printf '%s\n' "$out" | tail -20; fail=1; }
out=$( ( _iso; export A770B_CARD_MODE=display PATH="$path_no_nvtop" A770B_ALLOW_NO_NVTOP=0
  bash "$here/skills/local-build/scripts/local-build.sh" doctor ) 2>&1 )
printf '%s' "$out" | grep -q "MISSING card: no VRAM readings (nvtop absent or its output unreadable); install nvtop, or set A770B_ALLOW_NO_NVTOP=1" \
  && echo "ok   mode: I9g no nvtop without A770B_ALLOW_NO_NVTOP is MISSING card" \
  || { echo "FAIL mode: I9g"; printf '%s\n' "$out" | tail -20; fail=1; }
# the stop-run gate: run_pid_alive is the proof a pid is ours before any signal is sent, proved here without the card
RUNPID="$A770B_DATA/logs/run.pid"
rm -f "$RUNPID"
pid=$(run_pid_alive "$RUNPID" 2>/dev/null); rc=$?
if [ "$rc" != 0 ] && [ -z "$pid" ]; then echo "ok   gate: no pidfile, no pid"; else echo "FAIL gate: a missing pidfile did not refuse cleanly (rc=$rc pid='$pid')"; fail=1; fi
sleep 30 & sp=$!
printf '%s\n' "$sp" > "$RUNPID"
pid=$(run_pid_alive "$RUNPID" 2>/dev/null)
if [ -z "$pid" ]; then echo "ok   gate: a foreign pid is refused"; else echo "FAIL gate: a foreign (sleep) pid was accepted"; fail=1; fi
kill "$sp" 2>/dev/null
timeout 30 bash -c 'sleep 30' & tp=$!
printf '%s\n' "$tp" > "$RUNPID"
pid=$(run_pid_alive "$RUNPID" 2>/dev/null)
if [ -z "$pid" ]; then echo "ok   gate: a timeout that is not a run is refused"; else echo "FAIL gate: a timeout not naming sandbox_run.sh or opencode run was accepted"; fail=1; fi
kill "$tp" 2>/dev/null
timeout 30 bash -c 'true sandbox_run.sh; sleep 30' & rp=$!
printf '%s\n' "$rp" > "$RUNPID"
pid=$(run_pid_alive "$RUNPID" 2>/dev/null)
if [ "$pid" = "$rp" ] && bash "$here/skills/local-build/scripts/local-build.sh" stop-run >/dev/null 2>&1; then
  for _ in $(seq 1 5); do kill -0 "$rp" 2>/dev/null || break; sleep 1; done
  if kill -0 "$rp" 2>/dev/null; then echo "FAIL gate: stop-run exited 0 but the run pid is still alive"; fail=1; kill "$rp" 2>/dev/null
  else echo "ok   gate: stop-run ends exactly the recorded run"; fi
else echo "FAIL gate: run_pid_alive refused the recorded run, or stop-run did not exit 0"; fail=1; kill "$rp" 2>/dev/null
fi
rm -f "$RUNPID"
[ "$A770B_VK_DEVICE_SELECT" = "8086:56a0!" ] && echo "ok   device: the builder card is pinned by PCI id by default" || { echo "FAIL device: A770B_VK_DEVICE_SELECT default is '$A770B_VK_DEVICE_SELECT'"; fail=1; }
grep -q '^MESA_VK_DEVICE_SELECT="\$A770B_VK_DEVICE_SELECT" nohup "\$A770B_LLAMA_BIN"' "$here/harness/serve_a770_llamacpp.sh" && echo "ok   device: the server starts under the selector" || { echo "FAIL device: serve_a770_llamacpp.sh does not export the selector to the server"; fail=1; }
out=$(A770B_FAST_MODEL=Qwen3.5-9B-Q4_K_M.gguf A770B_LONG_MODEL=gemma-4-E4B-it-Q4_K_M.gguf bash "$here/skills/local-build/scripts/local-build.sh" doctor 2>&1); printf '%s\n' "$out" | grep -q "MISSING profiles: profile fast serves long's file" && echo "ok   doctor: an inverted builder.env is reported" || { echo "FAIL doctor: the inversion was not reported"; fail=1; }
out=$(bash "$here/skills/local-build/scripts/local-build.sh" doctor 2>&1); printf '%s\n' "$out" | grep -q "^ok   profiles:" && echo "ok   doctor: a clean environment is not warned about" || { echo "FAIL doctor: warned on a clean environment"; fail=1; }
# ── depth_probe.sh grades itself (item 1): the two shared-helper fixes another builder landed (commit 637c4a1) —
# a770b_ensure_corpus_file (never a silent seat-glob fallback) and kernel_resets_since (A770B_RESET_PATTERN, not a
# hard-coded Intel string) — plus the grading itself: PASS/FAIL per question and a final "n/3" line, exit 0 iff >=2/3.
grep -q 'a770b_ensure_corpus_file' "$here/harness/depth_probe.sh" && ! grep -q 'seat glob' "$here/harness/depth_probe.sh" \
  && echo "ok   depth_probe: generates/refuses the corpus via a770b_ensure_corpus_file, no silent seat-glob fallback" \
  || { echo "FAIL depth_probe: does not use a770b_ensure_corpus_file (or still falls back to a seat glob)"; fail=1; }
grep -q 'kernel_resets_since' "$here/harness/depth_probe.sh" && ! grep -q "engine reset|timedout" "$here/harness/depth_probe.sh" \
  && echo "ok   depth_probe: reads kernel resets through kernel_resets_since (A770B_RESET_PATTERN), not a hard-coded grep" \
  || { echo "FAIL depth_probe: does not use kernel_resets_since (or still hard-codes the Intel reset pattern)"; fail=1; }
dp_corpus="$t/dp-corpus.py"
{ for i in 1 2 3 4 5 6; do printf 'def helper_%d():\n    return %d\n\n' "$i" "$i"; done; head -c 4000 /dev/zero | tr '\0' '#'; } > "$dp_corpus"
mkdir -p "$t/dpbin"
cat > "$t/dpbin/curl" <<'DPCURL'
#!/bin/sh
# a real completion takes many seconds, giving depth_probe.sh's background VRAM sampler time to write at least one
# reading before it is killed; this fake must not answer instantly or that sampler file reads empty (float('') dies)
sleep 2.5
cat "$DP_FAKE_ANSWER_FILE"
DPCURL
chmod +x "$t/dpbin/curl"
python3 -c 'import json,sys; json.dump({"usage":{"prompt_tokens":1234},"timings":{"prompt_per_second":500,"predicted_per_second":20},"choices":[{"message":{"content":"orbital_checksum_v7 multiplies each byte by its 1-based position, sums them, and xors the sum with the salt (default 4171), then reduces the result modulo 65521, described as the Adler prime. Other real definitions include helper_1, helper_2, and helper_3."}}]}, open(sys.argv[1],"w"))' "$t/dp-good.json"
python3 -c 'import json,sys; json.dump({"usage":{},"timings":{},"choices":[{"message":{"content":"I could not determine the exact behaviour of the function or any other definitions in the source."}}]}, open(sys.argv[1],"w"))' "$t/dp-bad.json"
A770B_CORPUS_FILE="$dp_corpus" A770B_REFUSE=/nonexistent PATH="$t/dpbin:$PATH" DP_FAKE_ANSWER_FILE="$t/dp-good.json" \
  bash "$here/harness/depth_probe.sh" 1000 > "$t/dp-good.out" 2>&1; dp_good_rc=$?
if [ "$dp_good_rc" = 0 ] && grep -q '^PASS: Q1' "$t/dp-good.out" && grep -q '^PASS: Q2' "$t/dp-good.out" \
  && grep -q '^PASS: Q3' "$t/dp-good.out" && grep -q 'depth probe at 1000: 3/3' "$t/dp-good.out" \
  && grep -q '^ANSWER:' "$t/dp-good.out"
then echo "ok   depth_probe: a fully-correct reply grades PASS on all three questions (3/3) and exits 0, the raw answer still printed"
else echo "FAIL depth_probe: good-answer grading did not pass as expected (rc=$dp_good_rc)"; cat "$t/dp-good.out"; fail=1
fi
A770B_CORPUS_FILE="$dp_corpus" A770B_REFUSE=/nonexistent PATH="$t/dpbin:$PATH" DP_FAKE_ANSWER_FILE="$t/dp-bad.json" \
  bash "$here/harness/depth_probe.sh" 1000 > "$t/dp-bad.out" 2>&1; dp_bad_rc=$?
if [ "$dp_bad_rc" = 1 ] && grep -q '^FAIL: Q1' "$t/dp-bad.out" && grep -q '^FAIL: Q2' "$t/dp-bad.out" \
  && grep -q '^FAIL: Q3' "$t/dp-bad.out" && grep -qE 'depth probe at 1000: [01]/3' "$t/dp-bad.out"
then echo "ok   depth_probe: a reply with none of the planted facts grades FAIL on all three questions and exits 1"
else echo "FAIL depth_probe: bad-answer grading did not fail as expected (rc=$dp_bad_rc)"; cat "$t/dp-bad.out"; fail=1
fi

# ── harness/ladder.sh (item 2): the one command that produces a row — --dry-run prints all six rungs and writes nothing
lad_out=$(A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="$t/ladder-data" bash "$here/harness/ladder.sh" long --dry-run 2>&1)
if printf '%s\n' "$lad_out" | grep -q 'rung 1/6' && printf '%s\n' "$lad_out" | grep -q 'bench_model.sh' \
  && printf '%s\n' "$lad_out" | grep -q 'bench_speed.sh' && printf '%s\n' "$lad_out" | grep -q 'ctx_sweep.sh' \
  && printf '%s\n' "$lad_out" | grep -q 'depth_probe.sh' && printf '%s\n' "$lad_out" | grep -q 'run_suite.sh' \
  && printf '%s\n' "$lad_out" | grep -q 'nothing written' \
  && { [ ! -d "$t/ladder-data/results" ] || [ -z "$(ls -A "$t/ladder-data/results" 2>/dev/null)" ]; }
then echo "ok   ladder: --dry-run names all six rungs (load, probes, speed, window, depth probe, task) and writes nothing"
else echo "FAIL ladder: --dry-run output missing a rung or wrote something"; printf '%s\n' "$lad_out"; fail=1
fi
lad_model_out=$(A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="$t/ladder-data2" bash "$here/harness/ladder.sh" /home/xenofon/LLM/tested/Qwen3.5-9B-Q4_K_M.gguf --ctx 8192 --dry-run 2>&1)
if printf '%s\n' "$lad_model_out" | grep -q 'SKIPPED' && printf '%s\n' "$lad_model_out" | grep -q -- '--model .*--ctx 8192'
then echo "ok   ladder: a bare GGUF with no registry row skips the bench_speed.sh rung (it needs a registry name) and still drives run_suite.sh --model for the task rung"
else echo "FAIL ladder: the no-row GGUF path did not skip bench_speed.sh or did not drive run_suite.sh --model"; printf '%s\n' "$lad_model_out"; fail=1
fi

# ── run_suite.sh --model/--ctx (item 3): a GGUF with no registry row can still climb the task rung
rs_model_out=$(A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="$t/rs-model-data" bash "$here/harness/run_suite.sh" --model /home/xenofon/LLM/tested/Qwen3.5-9B-Q4_K_M.gguf --ctx 8192 "$t/rs-model-seat" --dry-run 2>&1)
if printf '%s\n' "$rs_model_out" | grep -q "ephemeral profile 'candidate'" && printf '%s\n' "$rs_model_out" | grep -q -- '--profile candidate' \
  && printf '%s\n' "$rs_model_out" | grep -q 'nothing written'
then echo "ok   run_suite: --model/--ctx builds an ephemeral 'candidate' profile and drives every stage with it, with no registry row"
else echo "FAIL run_suite: --model/--ctx did not build the candidate profile as expected"; printf '%s\n' "$rs_model_out"; fail=1
fi
rs_combo_out=$(A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="$t/rs-combo-data" bash "$here/harness/run_suite.sh" long --model /home/xenofon/LLM/tested/Qwen3.5-9B-Q4_K_M.gguf --ctx 8192 --dry-run 2>&1); rs_combo_rc=$?
if [ "$rs_combo_rc" = 2 ] && printf '%s\n' "$rs_combo_out" | grep -q 'mutually exclusive'
then echo "ok   run_suite: a registry profile name together with --model is refused (mutually exclusive)"
else echo "FAIL run_suite: the profile-name + --model combination was not refused (rc=$rs_combo_rc)"; printf '%s\n' "$rs_combo_out"; fail=1
fi

# ── kit/REVIEW-rubric.md (item 4): asks for exactly what run_suite.sh's own parser accepts
if grep -qx 'maintainable: <0|3|5>' "$here/kit/REVIEW-rubric.md" && grep -qx 'usable: <0|3|5>' "$here/kit/REVIEW-rubric.md" \
  && grep -q 'EXACTLY two lines' "$here/kit/REVIEW-rubric.md" && grep -qi 'recorded as .null.' "$here/kit/REVIEW-rubric.md" \
  && ! grep -q 'must start with the model name' "$here/kit/REVIEW-rubric.md"
then echo "ok   rubric: kit/REVIEW-rubric.md asks for exactly the two lines run_suite.sh's parser accepts, and says anything else is null"
else echo "FAIL rubric: kit/REVIEW-rubric.md does not match run_suite.sh's parser"; fail=1
fi

# ── harness/suite_report.py (item 5): the contamination pair (public exercise result beside its hidden variant),
# read from a reference stage's own <label>.verify.md, per language — never leaked into --json (no field for it there)
cr="$t/contam"; mkdir -p "$cr/results"
cat > "$cr/results/ref-cpp-x.verify.md" <<'EOF'
hidden tests: test_bst_hidden.py
verdict: FAIL (exit 1) — the exit code of the command is the verdict
reported: 5 passed, 1 failed in 0.42s (quoted from output the tests control; informational)
EOF
cat > "$cr/results.json" <<JSON
{"instrument": "SUITE-1", "stages": [{"id": "ref-cpp-x", "language": "cpp", "label": "ref-cpp-x", "working": false}]}
JSON
cr_out=$(A770B_DATA="$cr" python3 "$here/harness/suite_report.py" "$cr/results.json" 2>&1)
cr_json=$(A770B_DATA="$cr" python3 "$here/harness/suite_report.py" "$cr/results.json" --json 2>/dev/null)
if printf '%s\n' "$cr_out" | grep -q 'contamination (ref-cpp-x, cpp): public PASS · hidden FAIL' \
  && ! printf '%s' "$cr_json" | grep -q contamination
then echo "ok   suite_report: prints the contamination pair (public PASS, hidden FAIL) per reference stage's language, never in --json"
else echo "FAIL suite_report: contamination pair not reported as expected"; printf '%s\n' "$cr_out"; echo "$cr_json"; fail=1
fi
rm -rf "$t" "$A770B_DATA"
if [ "$fail" = 0 ]; then echo "selftest: all passed"; fi
exit "$fail"
