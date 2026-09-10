#!/usr/bin/env bash
# kit_selftest.sh — proves K1 (kit/ is self-contained: every build or test command in it runs inside the real
# sandbox boundary, /usr read-only, no network, the warm uv cache) by running the kit's own graders through
# harness/sandbox_run.sh, never on the host directly. Four checks:
#   (1) the sandboxed toolchain: node cmake make g++ ctest are all found, and node --version prints.
#   (2) every stage's own grader (kit/suite.json's "grader": "working", which embeds the hidden pytest file
#       under tests/_hidden_<name>.py, the same convention local-build.sh verify uses) FAILS on an export of
#       the shipped kit/seat/ stub, unmodified.
#   (3) the same grader, after pasting that stage's reference solution over a fresh export that already carries
#       every earlier stage's solution, SUCCEEDS. The reference solutions live under kit/hidden/solutions/<stage>/,
#       laid out to mirror kit/seat/'s own directory shape (never in kit/seat/ itself, so a model exporting that
#       subtree cannot read them).
#   (4) each of the three reference exercises' own public grader (kit/reference/reference.json's
#       "grader": "working"), after pasting its .meta/example.* or .meta/proof.ci.js over its stub, SUCCEEDS.
# tests/selftest.sh is unchanged and stays machine-independent; this script instead skips loudly and exits 0
# when it cannot run (no bwrap, or no warm uv cache) — see K1's own check and I9 in SECURITY.md.
# Run: A770B_UV_CACHE=<warm cache> bash tests/kit_selftest.sh
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
KIT="$here/kit"
fail=0
export A770B_PROJECT="$here" A770B_REFUSE=/nonexistent

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

# copy_tracked <dest> <src> — copies into <dest> only the files git tracks under <src> (same reasoning as
# harness/run_suite.sh's own copy_tracked: never the build/cache artefacts a prior local run left behind,
# __pycache__, .pytest_cache, a built .so — those are what made the first exported seat read dirty).
copy_tracked(){
  local dest="$1" src="$2"
  if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$src" ls-files -z | tar -C "$src" --null -T - -cf - | tar -C "$dest" -xf -
  else
    cp -a "$src/." "$dest/"
  fi
}

export_seat(){ # export_seat <dest> <src> — a standalone git-init'ed, committed copy, same shape run_suite.sh's
               # export_kit_seat uses (K1: the model never sees a clone of this repository, only this export)
  local dest="$1" src="$2"
  rm -rf "$dest"; mkdir -p "$dest"
  copy_tracked "$dest" "$src"
  git init -q "$dest"
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost add -A
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost commit -q -m "kit selftest seat"
  local dirty; dirty=$(git -C "$dest" status --porcelain --ignored)
  [ -z "$dirty" ] || { echo "FAIL kit: $dest is not clean right after its own commit — something not git-tracked got in:" >&2; printf '%s\n' "$dirty" >&2; exit 2; }
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

# ── (2) and (3): the four mini-project stages. Each is checked on its OWN fresh per-stage export, the same
# mechanism harness/run_suite.sh now uses: a clean copy of kit/seat/ with the REFERENCE solutions of every stage
# before it pasted over it (kit/hidden/solutions/<id>/, laid out to mirror kit/seat/'s own directory shape, so a
# plain directory paste lands each file where the seat expects it) and committed — never a seat carried forward
# with this script's own edits piled onto it. s3's hidden grader, for instance, calls S2's own compute_stats for
# parity, so S2's solution must already be in the seat; export_stage_seat below is what puts it there.
SOLUTIONS="$KIT/hidden/solutions"
STAGE_IDS=(s0-design s1-frontend s2-backend s3-optimise)

# export_stage_seat <dest> <label> <solved-id...> — a fresh git-init'ed, committed copy of kit/seat/ at <dest>,
# with each named stage's solutions/<id>/ pasted over it in order, one commit "kit seat for <label>".
export_stage_seat(){
  local dest="$1" label="$2"; shift 2
  rm -rf -- "$dest"; mkdir -p "$dest"
  copy_tracked "$dest" "$KIT/seat"
  local sid
  for sid in "$@"; do copy_tracked "$dest" "$SOLUTIONS/$sid"; done
  git init -q "$dest"
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost add -A
  git -C "$dest" -c user.name=kit-selftest -c user.email=kit-selftest@localhost commit -q -m "kit seat for $label" >/dev/null
  local dirty; dirty=$(git -C "$dest" status --porcelain --ignored)
  [ -z "$dirty" ] || { echo "FAIL kit: $dest is not clean right after its own commit (label $label) — something not git-tracked got in:" >&2; printf '%s\n' "$dirty" >&2; exit 2; }
}

# stage <id> <grader-working-cmd> <hidden-basename>
stage(){
  local id="$1" grader="$2" hidden="$3"
  local solved=() sid
  for sid in "${STAGE_IDS[@]}"; do [ "$sid" = "$id" ] && break; solved+=("$sid"); done
  export_stage_seat "$SEAT" "$id" "${solved[@]}"
  copy_hidden "$SEAT" "$hidden"
  if sandboxed "$SEAT" "$grader" >/tmp/kit-selftest-$id-stub.log 2>&1; then
    bad "$id: the clean stub's own grader passed (it must fail) — $(tail -3 /tmp/kit-selftest-$id-stub.log | tr '\n' ' ')"
  else
    ok "$id: the clean stub fails its own grader"
  fi
  # cp -r, never -a: the stub check above may already have built an artifact (S3's make -C cpp) inside the seat;
  # -a would preserve this solution file's on-disk mtime (older than that artifact), and make would then see the
  # target as up to date and skip rebuilding it against the solution just pasted in.
  cp -r "$SOLUTIONS/$id/." "$SEAT/"
  if sandboxed "$SEAT" "$grader" >/tmp/kit-selftest-$id-solved.log 2>&1; then
    ok "$id: the reference solution (kit/hidden/solutions/$id/) makes the grader pass"
  else
    bad "$id: the reference solution did not pass its own grader: $(tail -5 /tmp/kit-selftest-$id-solved.log | tr '\n' ' ')"
  fi
  rm -f /tmp/kit-selftest-$id-stub.log /tmp/kit-selftest-$id-solved.log
}

stage s0-design \
  'uv run --with pytest python -m pytest -q tests/_hidden_test_s0_design_hidden.py' \
  test_s0_design_hidden.py

stage s1-frontend \
  'node --test js/tests/*.test.js && uv run --with pytest python -m pytest -q tests/_hidden_test_s1_frontend_hidden.py' \
  test_s1_frontend_hidden.py

stage s2-backend \
  'uv run --with pytest python -m pytest -q python/tests tests/_hidden_test_s2_backend_hidden.py' \
  test_s2_backend_hidden.py

stage s3-optimise \
  'make -C cpp && uv run --with pytest python -m pytest -q python/tests tests/_hidden_test_s3_optimise_hidden.py' \
  test_s3_optimise_hidden.py

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
