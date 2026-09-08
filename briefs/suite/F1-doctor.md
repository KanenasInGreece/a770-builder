# Task: `local-build.sh doctor`, and a registry card that warns when the environment inverts a profile

You are in a standalone clone of a770-builder at its current main. The task is edits in three existing files:
`harness/profiles.py`, `tests/test_profiles.py`, `skills/local-build/scripts/local-build.sh`, and two lines in
`tests/selftest.sh`. Do not create files. Do not edit any other file.

## Why

A stranger installing this repository on another Linux machine needs to know, before the first run, what their system
lacks; and a `builder.env` written for an earlier release can name `A770B_FAST_*` or `A770B_LONG_*` for the other
profile's file, which the environment-wins rule turns into a silent inversion. Both are checks, not prose.

## The files

- `harness/profiles.py` — read the `cmd_card` function and the helpers it calls (`grep -n 'def ' harness/profiles.py`, then `sed -n` the card function only).
- `tests/test_profiles.py` — read the last two tests only (`tail -30 tests/test_profiles.py`), to copy their shape.
- `skills/local-build/scripts/local-build.sh` — read lines 1–12 (the header), the `status)` and `profiles)` case arms (`grep -n '^  status)\|^  profiles)\|^  stop-run)\|^  \*) echo "usage' skills/local-build/scripts/local-build.sh`), and the `version` function.
- `tests/selftest.sh` — read the four `ok   profiles:` lines (`grep -n 'ok   profiles:' tests/selftest.sh`) and the line starting `rm -rf "$t"`.

## The change

### 1. `harness/profiles.py` — `card --served` gains a top-level `"warnings"` list

After the served values are filled in, compute warnings, a list of strings (empty when none), and add it to the
top-level object (and to a single profile's object when `--name` is given, holding only that profile's warnings):
- for every profile P whose `served_model` differs from its file `model`: if that served file equals the `model` of
  another profile Q in the registry, add exactly:
  `profile P serves Q's file (<file>): A770B_<P>_MODEL comes from the environment or builder.env and inverts the profiles; an earlier release named them the other way round`
  (with P, Q upper-cased in the variable name only, as the shell form).
- for every profile P whose `served_ctx` is greater than its file `ctx`: add exactly:
  `profile P serves <served_ctx> tokens, more than the registry's <ctx>: the card's numbers were measured at the smaller window`
Order: registry order, model warnings before ctx warnings within a profile.

### 2. `tests/test_profiles.py` — two tests

`test_card_warns_on_inverted_profiles`: with `A770B_FAST_MODEL=Qwen3.5-9B-Q4_K_M.gguf` and
`A770B_LONG_MODEL=gemma-4-E4B-it-Q4_K_M.gguf` in the environment, `card --served` has two warnings, the first starting
`profile fast serves long's file (Qwen3.5-9B-Q4_K_M.gguf)` and the second starting `profile long serves fast's file`.
`test_card_no_warnings_when_clean`: with no `A770B_*_MODEL` or `A770B_*_CTX` in the environment (strip them from a copy
of `os.environ`), `card --served` has `warnings == []`, and `card --served --name long` has `warnings == []`.

### 3. `skills/local-build/scripts/local-build.sh` — the `doctor` subcommand

A new case arm placed right after the `profiles)` arm. It prints one line per check, `ok   <what>` or `MISSING <what>: <what to do>`,
never stops at the first failure, and exits 1 when anything is MISSING, 0 otherwise. The checks, in this order:
1. `python3` on PATH (`command -v python3`), else MISSING `python3: install Python 3 (the registry, the renderer and the capture use it)`.
2. `bwrap`, `socat`, `uv`, `curl`, `git`, `flock`, `timeout` each on PATH; one line each; MISSING says `install <tool>`.
3. `"$A770B_LLAMA_BIN"` executable, else MISSING `llama-server at $A770B_LLAMA_BIN: build llama.cpp with the Vulkan backend, or set A770B_LLAMA_BIN`.
4. `nvtop` on PATH, or `A770B_ALLOW_NO_NVTOP=1`; MISSING says `nvtop: install it for VRAM readings, or set A770B_ALLOW_NO_NVTOP=1 on a card that draws no desktop`.
5. `python3 "$A770B_PROJECT/harness/profiles.py" check` exits 0, else MISSING `registry: config/profiles.json does not check (python3 harness/profiles.py check says why)`.
6. For every profile in `$A770B_PROFILES`: the served model file is readable at `a770b_model_path "$(a770b_profile_var "$p" MODEL)"`; MISSING says `model for <p>: <path> not found; download it (docs/OPERATING.md) or set A770B_<P>_MODEL`.
7. The card's warnings: run `python3 "$A770B_PROJECT/harness/profiles.py" card --file "${A770B_PROFILES_FILE:-$A770B_PROJECT/config/profiles.json}" --served` and print every string in `warnings` as `MISSING profiles: <warning>` (use `python3 -c` to read the JSON); none → `ok   profiles: the environment does not invert the registry`.
8. `[ -n "$A770B_REFUSE" ]`, else MISSING `A770B_REFUSE: list the live checkouts the seat must never touch, colon-separated, in builder.env`.
9. `"$A770B_SEAT"` is a directory holding a `.git` DIRECTORY (not a link), else MISSING `seat at $A770B_SEAT: clone the target repository there (git clone <url> $A770B_SEAT)`.
10. The selector: `MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" "$A770B_LLAMA_BIN" --list-devices 2>/dev/null | grep -c '^ *Vulkan[0-9]*:'` equals 1 when `A770B_VK_DEVICE_SELECT` is non-empty (skip the check, printing `ok   device: selector empty, not pinned`, when it is empty); MISSING says `device: the selector $A770B_VK_DEVICE_SELECT lists <n> Vulkan devices, not 1; set A770B_VK_DEVICE_SELECT to the builder card's vendor:device! from lspci -nn`. Run this check only if the binary exists (skip otherwise, no line).
11. `"$A770B_UV_CACHE"` is a directory, else MISSING `uv cache at $A770B_UV_CACHE: run bash harness/warm_cache.sh once (the sandbox has no network)`.
End with `doctor: all checks passed` or `doctor: <n> missing`. The arm takes no run lock and starts no server.
Add `doctor` to the header comment (one line: `#   doctor            what this machine lacks to run the seat, one line per check; exit 1 when anything is missing`) and to the usage line after ` | profiles [--name N]`.

### 4. `tests/selftest.sh` — two lines before `rm -rf "$t"`

With the self-test's exported `A770B_PROJECT`, `A770B_REFUSE=/nonexistent`, `A770B_DATA` and additionally
`A770B_FAST_MODEL=Qwen3.5-9B-Q4_K_M.gguf A770B_LONG_MODEL=gemma-4-E4B-it-Q4_K_M.gguf` set for the call only:
`bash "$here/skills/local-build/scripts/local-build.sh" doctor 2>&1 | grep -q "MISSING profiles: profile fast serves long's file"` →
`ok   doctor: an inverted builder.env is reported` else `FAIL doctor: the inversion was not reported`, `fail=1`.
And: the same call without those two variables prints a line starting `ok   profiles:` →
`ok   doctor: a clean environment is not warned about` else `FAIL doctor: warned on a clean environment`, `fail=1`.

## Verify — run exactly this and print its last two lines

```
uv run --with pytest python -m pytest -q tests/test_profiles.py; bash tests/selftest.sh; echo EXIT=$?
```
Expected: all tests passed, the two `ok   doctor:` lines, `selftest: all passed`, `EXIT=0`. Also
`uvx --from shellcheck-py shellcheck -S warning skills/local-build/scripts/local-build.sh tests/selftest.sh` prints nothing.

## Rules
Work only inside this directory. No version-control command that changes state. No docker, no systemctl, no network.
Start no server. Keep one-line idioms on one line. Every `doctor` line begins with `ok   ` or `MISSING `.

## Stop when
The verify prints as expected and `git status --short` names exactly the four files. Print it and stop.
