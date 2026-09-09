# SUITE-1 — the logstats mini-project

This document describes SUITE-1, the four-stage test project a model is graded against inside the kit.

One small application, "logstats" (a log-line statistics viewer), built in four stages that
depend on each other the way a real project does — design, then front end, then backend, then
a C++ optimisation of the backend's hot path — each stage its own brief
(`kit/tasks/<stage>.md`), its own run specification (`kit/tasks/<stage>.spec.json`), and its
own hidden grader (`kit/hidden/test_<stage>_hidden.py`), run in order on one seat
(`kit/seat/`, exported by `harness/run_suite.sh` as the run's working directory — every path
in a brief or spec is relative to it, not to this repository's root). `kit/suite.json` is the
machine-readable form of this file; `kit/PROFILE.md` is where a run of this suite turns into a
registry row, field by field, and what each field can honestly be compared with outside this
project.

## The five axes

Every stage is scored on up to five axes (`kit/suite.json`'s per-stage `axes` list states
which of the last two apply):

- **working** — the hidden test: pass or fail. S0's does not count toward a row's pass tally
  (see below); the other three stages' do.
- **conformance** — the compiler with warnings as errors (S3), `node --check` (S1),
  `compileall` (S2): pass or fail. S0 has none (prose has no compiler).
- **concise** — lines written against the stage's budget: a number, not pass/fail.
- **maintainable** — every stage: a 0–5 reviewer score per `kit/REVIEW-rubric.md`'s
  maintainable lines, from a reviewer that did not build the change and never sees
  `kit/hidden/`.
- **usable** — S0 and S1 only: a 0–5 reviewer score per the rubric's usable lines. S2 and S3
  have no user-facing surface, so this axis does not apply to them.

**maintainable** and **usable** never count toward a row's pass tally, at any stage: only
**working**, and, where a stage has one, **conformance**, do (see *the design note's own
hidden check and every stage's reviewer score are scored outside the pass count* in
`AGENTS.md`). The rubric is commentary a reader weighs beside the pass count, never a gate
folded into it.

## The stages, in run order

1. **S0 design** (prose) — write `design/DESIGN.md` from `product-brief.md`: the data flow,
   the three components, and the JSON interface restated with a parsing example whose keys
   match the fixed set exactly. Budget: 60 lines. **Scored outside the pass count**: S0's
   hidden test is commentary on whether the note met the bar, not a gate the row's overall
   pass tally includes — a design note is not "working code" the way a passing test suite is,
   and the interface it restates is fixed by `product-brief.md` regardless of what S0 writes.
2. **S1 front end** (JavaScript + HTML) — implement `js/format.js`'s three pure functions,
   write `js/render.js` (JSON in, HTML string out, no DOM), and wire `html/index.html`: fetch
   `stats.json`, render into `#stats`, filter by the `#level` select, keep the page
   accessible (a label, `aria-live`, heading order, a working `<select>`). Budget: 180 lines
   across `js/format.js`, `js/render.js` and `js/tests/format.test.js`.
3. **S2 backend** (Python) — implement `compute_stats` in `python/logstats/stats.py` to its
   docstring, using the seeded `parse_line`/`sanitize_component` (from Shared Memory's
   `sanitize_entity_name`, Apache-2.0), and write `python/tests/test_stats.py`. Parity with
   the committed `python/data/sample.stats.json` on `python/data/sample.log`. Budget: 120
   lines.
4. **S3 optimisation** (C++ + Python) — implement `cpp/logstats.cpp`'s `count_levels` (the
   per-line, per-level counting loop, re-done in C++) and wire `python/logstats/fast.py` to
   call it through `ctypes`, falling back to Python when the library is absent. Parity with
   S2's `by_level`/`total_lines` on well-formed input; builds with
   `-Wall -Wextra -Wpedantic -Werror`; a timing ratio recorded, never thresholded (this
   card's CPU is the seat's). Budget: 150 lines.

A stage that every row passes or every row fails is replaced at the next revision of this
suite (`kit/suite.json`'s `"suite": "SUITE-1"` names the version this row was measured on).

## The reference rung

Beside the four stages, `kit/reference/` carries one exercise per language from Aider's own
public polyglot benchmark (`kit/reference/reference.json`), chosen from each track's harder
tier: C++ `binary-search-tree`, JavaScript `forth` (through a small shim over `node:test`),
Python `pov`. Each is graded the way Aider grades it — the exercise's own public tests, run by
the language's own runner with nothing installed — plus a hidden variant with fresh inputs,
because the exercise and its tests are public and a model may already have seen them. A row
states both numbers per language side by side, next to the mini-project's own stage for that
language: the public exercise (relatable to Aider's own leaderboard) and the kit's hidden
variant (what actually counts). The pair is a contamination indicator, not two correctness
scores, and its null is stated plainly: both passing carries no information about
memorisation either way — the signal, if there is one, is a public pass beside a hidden fail.
Each exercise carries its own `LICENSE` and `.meta/config.json` (MIT, Exercism, 2021, per its
own track), unmodified, rather than an entry in `kit/SOURCES.md`.

## How a stage is run

```
A770B_HIDDEN_ROOT=<checkout>/kit/hidden local-build.sh run <seat> kit/tasks/<stage>.md --spec kit/tasks/<stage>.spec.json --profile <name>
```

where `<seat>` is `kit/seat/` inside a clone of this repository at the release's tag (see
`kit/README.md`). `harness/run_suite.sh <profile>` runs all four stages in order, `verify`s
each against its hidden grader, scores the reviewer-graded axes through
`kit/REVIEW-rubric.md`, and writes `results/<profile>-suite-<date>.json`; before each stage it
re-exports the seat fresh from `kit/seat/` with the REFERENCE solutions of every stage before it
pasted over it (`kit/hidden/solutions/<id>/`, never the model's own output), so a later stage's
brief finds the earlier stages' work already in place, as a real project would.
