# Task: seven shellcheck findings closed and pipefail added to four scripts, in seven files

You are in a standalone clone of a770-builder at commit 9f2ae74 (branch hygiene, release v0.1.2). The task is eleven
edits in seven existing files and nothing else. Do not create files. Do not edit any file not named below.

## The files

- `harness/bench_model.sh`, `harness/sandbox_run.sh`, `harness/serve_a770_llamacpp.sh`,
  `skills/local-build/scripts/local-build.sh` — loops whose counter is never used.
- `harness/ctx_sweep.sh` — the default size list is one word today and is split by an unquoted expansion.
- `harness/env.sh` — a sourced library; a `.` of a run-time path that the linter cannot follow.
- `harness/depth_probe.sh`, `harness/cancel_repro.sh`, `tests/selftest.sh` — probes and the self-test, `set -u` only.

Read each file only around the lines named: `sed -n '<from>,<to>p' <file>`. Never print a whole file.

## The change

Each row: file, line number today, the line today (or its start), the line after. Every edit is one physical line
replaced by one physical line, except rows 6 and 8, which replace one line by two.

1. `harness/bench_model.sh` line 15 starts `for i in $(seq 1 150); do curl -sf` → starts `for _ in $(seq 1 150); do curl -sf` (only `i` becomes `_`; the rest of the line is unchanged).
2. `harness/sandbox_run.sh` line 36: `  for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK" ] && break; sleep 0.2; done` → `  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK" ] && break; sleep 0.2; done`
3. `harness/serve_a770_llamacpp.sh` line 32 starts `for i in $(seq 1 150); do curl -sf` → starts `for _ in $(seq 1 150); do curl -sf` (only `i` becomes `_`).
4. `skills/local-build/scripts/local-build.sh` line 66 starts `  for i in $(seq 1 90); do curl -sf` → starts `  for _ in $(seq 1 90); do curl -sf` (only `i` becomes `_`).
5. `harness/ctx_sweep.sh` line 6 ends `SIZES=("${@:-8000 32000 64000 100000 120000}")` → ends `SIZES=("$@"); [ "${#SIZES[@]}" -gt 0 ] || SIZES=(8000 32000 64000 100000 120000)` (the start of the line, `URL=... KEY=...`, is unchanged).
6. `harness/ctx_sweep.sh` line 9: `for want in ${SIZES[@]}; do` → `for want in "${SIZES[@]}"; do`
7. `harness/env.sh`: insert ONE new line before line 7 (the line starting `_a770b_load(){`), so that the new line 7 is exactly the directive in *Text that must appear as written*, item A, and the old line 7 becomes line 8 unchanged.
8. `skills/local-build/scripts/local-build.sh` line 64: `  # shellcheck disable=SC2086  (extra is a deliberate word list from the env)` → the TWO lines in *Text that must appear as written*, item B, in that order, each with the same two-space indent.
9. `harness/ctx_sweep.sh` line 4: `set -u` → `set -uo pipefail`
10. `harness/depth_probe.sh` line 5: `set -u` → `set -uo pipefail`
11. `harness/cancel_repro.sh` line 6: `set -u` → `set -uo pipefail`
12. `tests/selftest.sh` line 5: `set -u` → `set -uo pipefail`

(Row 8 is done after row 4 or before it; either way the `for _` edit is on the line that today reads line 66.)

## Text that must appear as written

Item A, one line, the new line 7 of `harness/env.sh`:
```
# shellcheck disable=SC1090 # builder.env is named at run time; the linter cannot follow it and need not
```

Item B, two lines replacing line 64 of `skills/local-build/scripts/local-build.sh`:
```
  # extra is a deliberate word list from the env, expanded unquoted on purpose so each word is an argument
  # shellcheck disable=SC2086
```

## Verify — run exactly this and print its last two lines

```
bash tests/selftest.sh; echo EXIT=$?
```

Expected: `selftest: all passed` and `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state (no commit, push, merge, branch switch).
No docker, no systemctl, no network. Keep every edit on one physical line; never re-wrap. Change nothing but the
characters named in each row.

## Stop when

`grep -c 'for _ in' harness/bench_model.sh harness/sandbox_run.sh harness/serve_a770_llamacpp.sh skills/local-build/scripts/local-build.sh`
prints 1 for each of the four files; `grep -c 'set -uo pipefail' harness/ctx_sweep.sh harness/depth_probe.sh harness/cancel_repro.sh tests/selftest.sh`
prints 1 for each; `grep -n 'disable=SC1090' harness/env.sh` prints line 7; `grep -n 'disable=SC2086' skills/local-build/scripts/local-build.sh`
prints one line with nothing after `SC2086`; the verify line prints as expected. Then print the list of files you changed.
