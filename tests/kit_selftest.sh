#!/usr/bin/env bash
# kit_selftest.sh — proves K1 (kit/ is self-contained: every build or test command in it runs inside the real
# sandbox boundary, /usr read-only, no network, the warm uv cache) by running the kit's own graders through
# harness/sandbox_run.sh, never on the host directly. Four checks:
#   (1) the sandboxed toolchain: node cmake make g++ ctest are all found, and node --version prints.
#   (2) every stage's own grader (kit/suite.json's "grader": "working", which embeds the hidden pytest file
#       under tests/_hidden_<name>.py, the same convention local-build.sh verify uses) FAILS on an export of
#       the shipped kit/seat/ stub, unmodified.
#   (3) the same grader, after pasting that stage's reference solution over the stub, SUCCEEDS. The reference
#       solutions live under kit/hidden/solutions/<stage>/ (never in kit/seat/, so a model exporting that
#       subtree cannot read them) — each check below names the file it pasted.
#   (4) each of the three reference exercises' own public grader (kit/reference/reference.json's
#       "grader": "working"), after pasting its .meta/example.* or .meta/proof.ci.js over its stub, SUCCEEDS.
# tests/selftest.sh is unchanged and stays machine-independent; this script instead skips loudly and exits 0
# when it cannot run (no bwrap, or no warm uv cache) — see K1's own check and I9 in SECURITY.md.
# Run: A770B_UV_CACHE=<warm cache> bash tests/kit_selftest.sh
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
KIT="$here/kit"
fail=0

command -v bwrap >/dev/null 2>&1 || { echo "skip: bwrap or the warm uv cache is missing"; exit 0; }
# shellcheck disable=SC1091
. "$here/harness/env.sh"
[ -d "$A770B_UV_CACHE" ] && [ -n "$(ls -A "$A770B_UV_CACHE" 2>/dev/null)" ] || { echo "skip: bwrap or the warm uv cache is missing"; exit 0; }

t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT

# sandbox_run.sh mounts this read-only at ~/.config/opencode/local-profile.jsonc; every check here runs a
# plain shell command, never opencode itself, so its content never matters — only that the file exists.
CFG="$t/cfg.jsonc"; printf '{}\n' > "$CFG"

sandboxed(){ # sandboxed <worktree> <shell-command> — runs inside bwrap, no network, no bridge, no server
  local wt="$1" cmd="$2"
  A770B_NO_BRIDGE=1 bash "$here/harness/sandbox_run.sh" "$wt" "$CFG" -- bash -c "$cmd"
}

ok(){ echo "ok   kit: $1"; }
bad(){ echo "FAIL kit: $1"; fail=1; }

export_seat(){ # export_seat <dest> <src> — a standalone git-init'ed, committed copy, same shape run_suite.sh's
               # export_kit_seat uses (K1: the model never sees a clone of this repository, only this export)
  local dest="$1" src="$2"
  rm -rf "$dest"; mkdir -p "$dest"
  cp -a "$src/." "$dest/"
  git init -q "$dest"
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost add -A
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost commit -q -m "kit selftest seat"
}

# copy_hidden <seat> <hidden-basename> — the same tests/_hidden_<name>.py convention
# skills/local-build/scripts/local-build.sh's verify uses to carry a hidden grader in after the patch applies.
copy_hidden(){ mkdir -p "$1/tests"; cp "$KIT/hidden/$2" "$1/tests/_hidden_$2"; }

# ── (1) the sandboxed toolchain ──
SEAT="$t/seat"
export_seat "$SEAT" "$KIT/seat"
if out=$(sandboxed "$SEAT" 'command -v node cmake make g++ ctest && node --version' 2>&1); then
  ok "toolchain: node cmake make g++ ctest found — $(printf '%s' "$out" | tail -1)"
else
  bad "toolchain: not all present inside the sandbox: $out"
fi

# ── (2) and (3): the four mini-project stages, run in order on ONE seat export (as harness/run_suite.sh runs
# them: each stage's brief assumes the previous stage's work is already in place, so a later stage's solution
# is pasted and committed on top of the earlier ones, not re-exported from the pristine stub — s3's hidden
# grader, for instance, calls S2's own compute_stats for parity, so S2's solution must already be in place).
# stage <id> <grader-working-cmd> <hidden-basename> <solution-dst-in-seat> <solution-src-repo-relative...>
stage(){
  local id="$1" grader="$2" hidden="$3"; shift 3
  copy_hidden "$SEAT" "$hidden"
  if sandboxed "$SEAT" "$grader" >/tmp/kit-selftest-$id-stub.log 2>&1; then
    bad "$id: the clean stub's own grader passed (it must fail) — $(tail -3 /tmp/kit-selftest-$id-stub.log | tr '\n' ' ')"
  else
    ok "$id: the clean stub fails its own grader"
  fi
  while [ $# -gt 0 ]; do
    local dst="$1" src="$2"; shift 2
    cp "$here/$src" "$SEAT/$dst"
  done
  if sandboxed "$SEAT" "$grader" >/tmp/kit-selftest-$id-solved.log 2>&1; then
    ok "$id: the reference solution (kit/hidden/solutions/$id/) makes the grader pass"
  else
    bad "$id: the reference solution did not pass its own grader: $(tail -5 /tmp/kit-selftest-$id-solved.log | tr '\n' ' ')"
  fi
  git -C "$SEAT" -c user.name=kit-selftest -c user.email=kit-selftest@localhost add -A
  git -C "$SEAT" -c user.name=kit-selftest -c user.email=kit-selftest@localhost commit -q -m "kit selftest: $id solved" --allow-empty >/dev/null
  rm -f /tmp/kit-selftest-$id-stub.log /tmp/kit-selftest-$id-solved.log
}

stage s0-design \
  'uv run --with pytest python -m pytest -q tests/_hidden_test_s0_design_hidden.py' \
  test_s0_design_hidden.py \
  design/DESIGN.md kit/hidden/solutions/s0-design/DESIGN.md

stage s1-frontend \
  'node --test js/tests/*.test.js && uv run --with pytest python -m pytest -q tests/_hidden_test_s1_frontend_hidden.py' \
  test_s1_frontend_hidden.py \
  js/format.js kit/hidden/solutions/s1-frontend/format.js \
  js/render.js kit/hidden/solutions/s1-frontend/render.js \
  html/index.html kit/hidden/solutions/s1-frontend/index.html

stage s2-backend \
  'uv run --with pytest python -m pytest -q python/tests tests/_hidden_test_s2_backend_hidden.py' \
  test_s2_backend_hidden.py \
  python/logstats/stats.py kit/hidden/solutions/s2-backend/stats.py

stage s3-optimise \
  'make -C cpp && uv run --with pytest python -m pytest -q python/tests tests/_hidden_test_s3_optimise_hidden.py' \
  test_s3_optimise_hidden.py \
  cpp/logstats.cpp kit/hidden/solutions/s3-optimise/logstats.cpp \
  python/logstats/fast.py kit/hidden/solutions/s3-optimise/fast.py

# ── (4) the three reference exercises, each's own public grader, proof solution pasted over its stub ──
# The reference exercises' own seat is this repository's clone (kit/reference/tasks/*.md: "you are in a
# standalone clone of KanenasInGreece/a770-builder"), not an export of kit/seat/ — so this export instead
# recreates just the kit/reference/ paths the graders below name, git-init'ed the same way.
REFSEAT="$t/ref-seat"
mkdir -p "$REFSEAT/kit/reference"
for d in cpp javascript python js; do cp -a "$KIT/reference/$d" "$REFSEAT/kit/reference/$d"; done
git init -q "$REFSEAT"
git -C "$REFSEAT" -c user.name=kit-selftest -c user.email=kit-selftest@localhost add -A
git -C "$REFSEAT" -c user.name=kit-selftest -c user.email=kit-selftest@localhost commit -q -m "kit reference selftest seat"

refexercise(){ # refexercise <label> <stub-relative-path> <proof-relative-path> <grader-cmd>
  local label="$1" stub="$2" proof="$3" grader="$4"
  cp "$REFSEAT/$proof" "$REFSEAT/$stub"
  if out=$(sandboxed "$REFSEAT" "$grader" 2>&1); then
    ok "$label: $(basename "$proof") pasted over $(basename "$stub") makes the public grader pass"
  else
    bad "$label: $(basename "$proof") over $(basename "$stub") did not pass: $(printf '%s' "$out" | tail -5 | tr '\n' ' ')"
  fi
}

refexercise ref-cpp-binary-search-tree \
  kit/reference/cpp/binary-search-tree/binary_search_tree.h \
  kit/reference/cpp/binary-search-tree/.meta/example.h \
  'cmake -S kit/reference/cpp/binary-search-tree -B build-bst -DEXERCISM_RUN_ALL_TESTS=1 && cmake --build build-bst'

refexercise ref-javascript-forth \
  kit/reference/javascript/forth/forth.js \
  kit/reference/javascript/forth/.meta/proof.ci.js \
  'node --test kit/reference/javascript/forth/run.test.mjs'

refexercise ref-python-pov \
  kit/reference/python/pov/pov.py \
  kit/reference/python/pov/.meta/example.py \
  'uv run --with pytest python -m pytest -q kit/reference/python/pov/pov_test.py'

if [ "$fail" = 0 ]; then echo "kit selftest: all passed"; fi
exit "$fail"
