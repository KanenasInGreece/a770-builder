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
# proved without the card. A throwaway kit/ fixture: two stages (one that verifies PASS, one that verifies FAIL) plus
# one reference exercise (kit/reference/reference.json, appended after the stages); the fixture never touches the
# real kit/ another unit is writing. Checks the runner's own export-and-run of kit/seat/, its results JSON (one
# PASS, one FAIL, the failing id named, the reference entry present) and that suite_report.py --json reports on it.
ku3="$t/ku3"; mkdir -p "$ku3/seat" "$ku3/tasks" "$ku3/reference"
echo "fixture" > "$ku3/seat/README.md"
for st in s0-pass s1-fail ref-cpp-example; do
  echo "brief $st" > "$ku3/tasks/$st.md"
  printf '{}' > "$ku3/tasks/$st.spec.json"
done
cat > "$ku3/suite.json" <<JSON
{"suite": "SUITE-TEST", "stages": [
  {"id": "s0-pass", "language": "python", "brief": "$ku3/tasks/s0-pass.md", "spec": "$ku3/tasks/s0-pass.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 100, "rubric": null}},
  {"id": "s1-fail", "language": "python", "brief": "$ku3/tasks/s1-fail.md", "spec": "$ku3/tasks/s1-fail.spec.json",
   "grader": {"working": null, "conformance": null, "budget_lines": 100, "rubric": null}}
]}
JSON
cat > "$ku3/reference/reference.json" <<JSON
{"reference": [{"id": "ref-cpp-example", "language": "cpp", "brief": "$ku3/tasks/ref-cpp-example.md",
  "spec": "$ku3/tasks/ref-cpp-example.spec.json", "grader": {"working": null, "conformance": null, "budget_lines": 50, "rubric": null}}]}
JSON
cat > "$ku3/fake-local-build.sh" <<'FAKE'
#!/usr/bin/env bash
set -uo pipefail
case "${1:-}" in
  run)
    shift; WT="$1"; BRIEF="$2"; shift 2
    label="fake-$(basename "$BRIEF" .md)"
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
out=$(A770B_LOCAL_BUILD="$ku3/fake-local-build.sh" bash "$here/harness/run_suite.sh" long "$t/ku3-seat" --suite "$ku3/suite.json" 2>&1); rc=$?
res=$(ls -t "$A770B_DATA"/results/long-suite-*.json 2>/dev/null | head -1)
if [ "$rc" = 0 ] && [ -n "$res" ] && python3 - "$res" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ids = [x["id"] for x in d["stages"]]
assert ids == ["s0-pass", "s1-fail", "ref-cpp-example"], ids
s = {x["id"]: x for x in d["stages"]}
assert s["s0-pass"]["working"] is True, s["s0-pass"]
assert s["s1-fail"]["working"] is False, s["s1-fail"]
assert s["ref-cpp-example"]["working"] is True, s["ref-cpp-example"]
sys.exit(0)
PY
then echo "ok   suite: run_suite.sh records one PASS, one FAIL (s1-fail named) and the reference entry"
else echo "FAIL suite: run_suite.sh output/results wrong (rc=$rc res=$res)"; printf '%s\n' "$out" | tail -20; fail=1
fi
if [ -n "$res" ]; then
  jout=$(python3 "$here/harness/suite_report.py" "$res" --json 2>/dev/null)
  if printf '%s' "$jout" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
need = {"briefs", "runs", "passed", "mean_wall_s", "source"}
assert set(d.keys()) == need, d
assert d["briefs"] == 3 and d["runs"] == 3 and d["passed"] == 2, d
' 2>/dev/null; then echo "ok   suite: suite_report.py --json prints the five totals"
  else echo "FAIL suite: suite_report.py --json wrong: $jout"; fail=1
  fi
else echo "FAIL suite: no results file to report on"; fail=1
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
rm -rf "$t" "$A770B_DATA"
if [ "$fail" = 0 ]; then echo "selftest: all passed"; fi
exit "$fail"
