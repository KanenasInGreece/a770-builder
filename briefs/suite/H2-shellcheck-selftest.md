# Task: a shellcheck check added to tests/selftest.sh, skipped cleanly when no shellcheck exists

You are in a standalone clone of a770-builder on branch hygiene. The task is one insertion of six lines in
`tests/selftest.sh` and nothing else. Do not create files. Do not edit any other file.

## The files

- `tests/selftest.sh` — the harness's self-test. Read its last 12 lines only: `tail -12 tests/selftest.sh`. Every check
  prints `ok   <topic>: <what held>` on success or `FAIL <what>` and sets `fail=1`; the last three lines remove the
  temporary directories, print `selftest: all passed` when nothing failed, and exit with `$fail`.

## The change

Insert the six lines in *Text that must appear as written* immediately BEFORE the line that starts `rm -rf "$t"`, so that
the block is the last check and the three closing lines follow it unchanged. Nothing else changes.

## Text that must appear as written

```
# shellcheck over every bash file, when a shellcheck can be found: on PATH, or through uvx (shellcheck-py bundles the binary, --offline so the cache decides); inside the sandbox neither exists, and the check reports itself skipped rather than failing offline
if command -v shellcheck >/dev/null 2>&1; then SC="shellcheck"; elif command -v uvx >/dev/null 2>&1 && uvx --offline --from shellcheck-py shellcheck --version >/dev/null 2>&1; then SC="uvx --offline --from shellcheck-py shellcheck"; else SC=""; fi
if [ -n "$SC" ]; then
  if $SC -S warning "$here"/harness/*.sh "$here"/skills/local-build/scripts/*.sh "$here"/tests/*.sh "$here"/render_readme.sh "$here"/release.sh; then echo "ok   shellcheck: no finding at warning level or above"; else echo "FAIL shellcheck: findings at warning level or above, listed above"; fail=1; fi
else echo "skip shellcheck: none on PATH and none in the uv cache (install shellcheck, or once online: uvx --from shellcheck-py shellcheck --version)"; fi
```
(That is five lines of text; with the comment line it is the six-line block. `$SC` is expanded unquoted on purpose: it is a command with arguments.)

## Verify — run exactly this and print its last two lines

```
bash tests/selftest.sh; echo EXIT=$?
```

Expected: the line before the last two reads `skip shellcheck: none on PATH and none in the uv cache (install shellcheck, or once online: uvx --from shellcheck-py shellcheck --version)` (no shellcheck exists where you run), then `selftest: all passed` and `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state. No docker, no systemctl, no network.
Keep every line exactly as given; never re-wrap.

## Stop when

`grep -c 'shellcheck' tests/selftest.sh` prints 6 or more, `tail -9 tests/selftest.sh | head -1` prints the comment line
above, and the verify line prints as expected. Then print the one file you changed.
