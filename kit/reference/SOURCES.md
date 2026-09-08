# Sources — the three reference exercises

Origin repository: [`github.com/Aider-AI/polyglot-benchmark`](https://github.com/Aider-AI/polyglot-benchmark),
commit `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f` (cloned read-only into a scratch path for this cycle; the
polyglot repository itself carries no licence of its own and points to the per-language tracks it mirrors,
each track MIT, Exercism, 2021).

Every exercise below was copied verbatim (directory layout kept) from the commit above into
`kit/reference/<language>/<slug>/`, chosen from the polyglot set's harder tier per Aider's own difficulty
ratings (see `BRIEF-profiling-kit.md`, *What the second research settled*): C++ `binary-search-tree` (10/10),
JavaScript `forth` (8/10), Python `pov` (9/10).

## `kit/reference/cpp/binary-search-tree/`

- Track: `cpp` (Exercism C++ track), licence MIT, 2021 — `LICENSE` in this directory (the text is Exercism's
  own, copied from the JavaScript exercise's own `LICENSE` file, which the polyglot mirror already carries).
- `.meta/config.json`: authors `elyashiv`; contributors `KevinWMatthews`, `patricksjackson`; source "Josh
  Cheek" (no source URL given).
- Carries the exercise's own vendored Catch2 (`test/catch.hpp`, v2.13.6) and `test/tests-main.cpp`, so its
  `cmake … && make`-equivalent build needs no download (Aider's own note, corroborated by reading the file).
  Catch2 is Copyright (c) 2021 Two Blue Cubes Ltd, licensed under the Boost Software License, Version 1.0 —
  not MIT, and not this exercise's own `LICENSE` — per `catch.hpp`'s own header comment; the licence text is
  `test/LICENSE_1_0.txt`, beside the header, the file name `catch.hpp` itself points to.

## `kit/reference/javascript/forth/`

- Track: `javascript` (Exercism JavaScript track), licence MIT, 2021 — `LICENSE` in this directory, carried
  as-is from the polyglot mirror (this exercise already ships its own copy; the other two did not, so their
  `LICENSE` files are copies of this text, per the brief).
- `.meta/config.json`: authors `matthewmorgan`; contributors `brendanmckeown`, `slaymance`, `SleeplessByte`,
  `tejasbubane`, `tgujar`, `xarxziux`.
- `.docs/instructions.md` cites Wikipedia: <https://en.wikipedia.org/wiki/Forth_%28programming_language%29>.
- `forth.spec.js` is Exercism's own spec file: only its first test (`numbers just get pushed onto the stack`)
  is a plain `test`; every other case is `xtest`, which the Exercism JavaScript track's Jest tooling exposes
  for progressive unlocking during learning. Aider's own polyglot-benchmark harness flips every `xtest` to
  `test` before grading, so `kit/reference/js/expect-shim.mjs` maps `xtest` onto `node:test`'s own `test`
  (not `test.skip`): the shim reproduces Aider's all-enabled run, not Exercism's progressive-unlock default,
  and the proof run shows all 49 of the exercise's cases enabled (`tests/test_kit_reference.py` asserts
  `pass 49`). `kit/reference/hidden/forth_hidden.test.mjs` (25 fresh assertions, all enabled) is a separate,
  unaffected number for full coverage.

## `kit/reference/python/pov/`

- Track: `python` (Exercism Python track), licence MIT, 2021 — `LICENSE` in this directory (copied from the
  JavaScript exercise's own file, as above).
- `.meta/config.json`: authors `cmccandless`; contributors `crsmi`, `Dog`, `N-Parsons`, `tqa236`, `yawpitch`;
  source "Adaptation of exercise from 4clojure", source URL <https://github.com/oxalorg/4ever-clojure>.
- `.docs/instructions.md` cites Wikipedia: <https://en.wikipedia.org/wiki/Tree_(graph_theory)> and
  <https://en.wikipedia.org/wiki/Graph_(discrete_mathematics)> (the instructions' own link text and target are
  transposed — "wiki-graph" points at the tree article and "wiki-tree" at the graph article; carried verbatim,
  not corrected, per the brief's "copy the exercises verbatim").
- `.docs/instructions.append.md` (also carried verbatim) states the exact `ValueError` messages the tests
  require; it is not quoted into the brief (the brief quotes `.docs/instructions.md` only, per the KU4 brief),
  but sits in the exercise directory for the model to read.

## Contamination note (all three)

Both the reference grader and its canonical test data are public (Exercism's problem-specifications, MIT, via
Aider's polyglot mirror). A pass on a reference grader alone carries **no** information about memorisation. The
signal is the pairing with the hidden variant (`kit/reference/hidden/*`, fresh inputs the model never sees,
built the same way as the public grader): a reference pass with a hidden fail indicates the public shape was
solved without the general one; a pass on both carries no information about memorisation either way. Each
`kit/reference/reference.json` entry repeats this as its own `contamination_note`.

## FINDING — `bash_allow` refuses two of the three exact grader commands

`harness/render_profile.py`'s run-specification validator (`BASH_ALLOW_RE`, `BASH_ALLOW_FORBIDDEN_FIRST`) is
the template's own boundary (K4 in `BRIEF-profiling-kit.md`); nothing in this cycle's kit/reference/ unit
changes it. Tested with `python3 harness/render_profile.py check --spec <spec> --seat .`:

- **C++**: `cmake -S kit/reference/cpp/binary-search-tree -B build-bst -DEXERCISM_RUN_ALL_TESTS=1` is refused —
  `bash_allow: each pattern must match ^[A-Za-z0-9 ._/*:-]+$, at most 80 chars` (the `=` in
  `-DEXERCISM_RUN_ALL_TESTS=1` is outside the allowed charset). Only the second half of the grader,
  `cmake --build build-bst`, validates and is the one entry `kit/reference/tasks/ref-cpp-binary-search-tree.spec.json`
  carries; the configure half has no `bash_allow` entry — a coding agent working this brief cannot get bash
  permission for the exact `cmake -S … -DEXERCISM_RUN_ALL_TESTS=1` invocation Aider's own grading uses. The
  harness's own `verify.test` (run by the grading step directly, not through the agent's bash tool) is
  unaffected and was proved green on the host (see below).
- **JavaScript**: `node --test kit/reference/javascript/forth/run.test.mjs` is refused outright —
  `bash_allow: must not start with 'node'` (`node` is in `BASH_ALLOW_FORBIDDEN_FIRST`, an interpreter, per the
  boundary's own comment). `kit/reference/tasks/ref-javascript-forth.spec.json` carries no `bash_allow` entry
  at all; this is the same open question the cycle brief already raises (`BRIEF-profiling-kit.md`, *Questions
  for the reads*, #3) and is not this unit's to resolve — the template's allow-list is another family's
  boundary change (K4).
- **Python**: `uv run --with pytest python -m pytest -q kit/reference/python/pov/pov_test.py` would also be
  refused if added to `bash_allow` (`uv` is likewise in `BASH_ALLOW_FORBIDDEN_FIRST`) — but no entry is needed:
  the template's own floor already allows `uv run *`, `python3 *`, `python *` and `python3 -m pytest *`
  unconditionally, so this grader needs nothing from this unit's specifications.

Every grader was proved green on the host from a copy of the exercise with `.meta/example.*` pasted over its
stub (see `tests/test_kit_reference.py`), independent of the `bash_allow` question above, which concerns only
a live agent's own permission to invoke the grader from inside the sandbox — not whether the exercise itself is
correctly carried or whether the harness's own `verify.test` step (which runs the grader directly, not through
the agent's bash tool) can grade it.
