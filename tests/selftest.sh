#!/usr/bin/env bash
# selftest.sh — what the harness can prove without the card: every script parses; the run specification is refused where it must be and rendered before the floor; the health line reads the four
# kinds of answer and strips what a hostile answer could put on a terminal; the seat's dirty check hides nothing but the
# harness's own brief copies; the capture survives a run that created no file. Run: bash tests/selftest.sh
set -uo pipefail
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
# the profiles registry: config/profiles.json is the single source now that env.sh evals `harness/profiles.py env`
python3 "$here/harness/profiles.py" check >/dev/null 2>&1 && echo "ok   profiles: the registry checks" || { echo "FAIL profiles: profiles.py check failed"; fail=1; }
[ "$A770B_PROFILES" = "long fast serious" ] && [ "$A770B_DEFAULT_PROFILE" = "long" ] && echo "ok   profiles: names and default come from the registry" || { echo "FAIL profiles: A770B_PROFILES='$A770B_PROFILES' A770B_DEFAULT_PROFILE='$A770B_DEFAULT_PROFILE'"; fail=1; }
[ "$(a770b_profile_var long CTX)" = "262144" ] && printf '%s' "$(a770b_profile_var serious EXTRA)" | grep -q reasoning_effort && echo "ok   profiles: the helper reads the registry's variables" || { echo "FAIL profiles: a770b_profile_var did not read the registry"; fail=1; }
[ "$(A770B_LONG_CTX=4096 bash -c '. "$1/harness/env.sh" >/dev/null 2>&1; a770b_profile_var long CTX' _ "$here")" = "4096" ] && echo "ok   profiles: the environment wins over the registry" || { echo "FAIL profiles: A770B_LONG_CTX=4096 did not win over the registry default"; fail=1; }
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
rm -rf "$t" "$A770B_DATA"
if [ "$fail" = 0 ]; then echo "selftest: all passed"; fi
exit "$fail"
