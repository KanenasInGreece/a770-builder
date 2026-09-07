#!/usr/bin/env bash
# selftest.sh — what the harness can prove without the card: the scripts parse, the health line informs and never
# refuses, and status reports the lock and the seat. Run: bash tests/selftest.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd); fail=0
bash -n "$here/harness/guard.sh" && bash -n "$here/skills/local-build/scripts/local-build.sh" || fail=1
export A770B_PROJECT="$here" A770B_REFUSE=/nonexistent A770B_DATA="${TMPDIR:-/tmp}/a770b-selftest"
. "$here/harness/env.sh"; . "$here/harness/guard.sh"
t=$(mktemp -d)
printf '{"status": "degraded", "version": "1"}\n' > "$t/degraded.json"
printf '{"status":"ok"}\n' > "$t/ok.json"
printf '{"version":"1"}\n' > "$t/nostatus.json"
check(){ local got; got=$(health_status "$1"); if [ "$got" = "$2" ]; then echo "ok   $2"; else echo "FAIL $1 -> '$got', expected '$2'"; fail=1; fi; }
check "file://$t/degraded.json" "degraded"
check "file://$t/ok.json" "ok"
check "file://$t/nostatus.json" "answered, no status field"
check "file://$t/missing.json" "no answer"
grep -q 'health_status "$A770B_HEALTH_URL"' "$here/harness/guard.sh" || { echo "FAIL budget_gate does not call health_status"; fail=1; }
sed -n '/health_status "$A770B_HEALTH_URL"/p' "$here/harness/guard.sh" | grep -q 'ok=0' && { echo "FAIL the health line still refuses"; fail=1; }
grep -q 'run lock:' "$here/skills/local-build/scripts/local-build.sh" && grep -q '"seat: ' "$here/skills/local-build/scripts/local-build.sh" || { echo "FAIL status does not report the lock and the seat"; fail=1; }
rm -rf "$t" "$A770B_DATA"
if [ "$fail" = 0 ]; then echo "selftest: all passed"; fi
exit "$fail"
