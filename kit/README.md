# kit/ — the profiling suite's seat

This folder contains the packaged tests run to profile each model, automated through AGENTS.md: the same tests
the harness ran first on its own Intel Arc A770, shipped so a reader can measure a model, or a different card, of
their own the same way.

`kit/` is the profiling ladder's task rung: a small, self-contained, multi-language project
("logstats", a log-line statistics viewer) built in four stages, the same suite for every
model profiled. It exists so a profile is not measured on Python test-writing alone — a real
project mixes Python, C++, JavaScript and HTML, and this kit gives the ladder one of each,
built as a single coherent application rather than four disconnected exercises. It is scored
on five axes per stage: working code, conformance, conciseness against a budget, and
reviewer-graded maintainability and (where it applies) usability — see `kit/SUITE.md`. Beside
the mini-project, `kit/reference/` carries one exercise per language from Aider's own public
polyglot benchmark (a reference rung, not the mini-project's own stages): a row states the
public exercise's number next to the kit's own hidden stage for the same language, a
contamination indicator whose null is that both passing carries no information — see
`kit/reference/reference.json` and `kit/SUITE.md`.

## Layout

```
kit/seat/         everything a stage's model may see and edit — the exported seat (below)
kit/tasks/        each stage's brief and run specification (outside the seat; never copied in)
kit/hidden/       each stage's ONE hidden pytest grader (outside the seat; never copied in
                   until after a run's patch applies, then only as tests/_hidden_<name>.py);
                   kit/hidden/solutions/<id>/ carries the reference solution of each stage
kit/reference/    the three reference exercises (Aider's polyglot set): tasks/, and the
                   per-language exercise directories, each with its own hidden variant
kit/suite.json    the machine-readable form of kit/SUITE.md; kit/reference/reference.json is
                   the reference rung's own machine-readable form
kit/SUITE.md      the four stages in prose: what each measures, its budget, the run order
kit/PROFILE.md    how a run of the suite turns into a registry row: which rung fills each
                   field, how its number is derived, and what it compares with outside this
                   project
kit/REVIEW-rubric.md   the reviewer's rubric for the maintainable and usable axes
kit/SOURCES.md, kit/NOTICE   attribution for every file seeded from the two sibling repositories
                   (each reference exercise carries its own LICENSE and .meta/config.json instead,
                   per Exercism's own convention — see kit/reference/reference.json)
```

## The seat

`kit/seat/` is the whole of what a stage's model sees: `product-brief.md` (read by every
stage), `README.md` (the seat's own short orientation), `design/` (S0 writes `DESIGN.md`
here), `python/logstats/` (the backend package — `parse.py` is seeded and complete,
`stats.py` and `fast.py` are stubs), `python/tests/` and `python/data/` (a committed sample
log and its reference statistics), `cpp/` (the C++ hot path, a stub, and its Makefile), and
`js/`/`html/` (the front end — `format.js` a stub, `bar-meta.js` seeded and complete,
`index.html` a seeded skeleton). `harness/run_suite.sh` exports this subtree as the seat a
run's model works in — paths in every brief and specification under `kit/tasks/` are relative
to `kit/seat/`, not to this repository's root. Nothing under `kit/hidden/` is ever part of
that exported subtree.

## How a stage is run

```
A770B_HIDDEN_ROOT=<checkout>/kit/hidden local-build.sh run <seat> kit/tasks/<stage>.md --spec kit/tasks/<stage>.spec.json --profile <name>
```

`<checkout>` is this repository's own clone (the one `kit/` lives in — not the run's seat);
`<stage>` is one of `s0-design`, `s1-frontend`, `s2-backend`, `s3-optimise`, run in that order,
since each stage's brief assumes the previous stage's work is already in place.
`harness/run_suite.sh <profile>` runs all four in order, `verify`s each, and writes
`results/<profile>-suite-<date>.json`; before each stage it re-exports the seat fresh from
`kit/seat/` with the REFERENCE solutions of every preceding stage pasted over it
(`kit/hidden/solutions/<id>/`, never the model's own output), so a stage never needs the
previous stage's model-written work to be correct for its own to run. The seat for a run is
NOT a clone of this repository (the model must not read `harness/`): it is an EXPORT of
`kit/seat/` — a copy, git-init'ed and committed fresh for every stage — for a main stage, or
of `kit/reference/`'s own `{cpp,javascript,python,js}` subtrees (`.meta/` stripped) for a
reference exercise; see `harness/run_suite.sh`'s own header comment. The kit itself is
versioned with the harness (`kit/suite.json`'s `"suite"` field).

## Findings from building this unit (2026-09-08)

- **`node` no longer needs a run specification's `bash_allow` at all**: a *pattern* whose
  first word is `node` still cannot be added through a spec's own `bash_allow` list —
  `harness/render_profile.py`'s `check` refuses it (rule 7's `BASH_ALLOW_FORBIDDEN_FIRST`,
  "no wrapper or interpreter") — but that is now moot for `node --test` and `node --check`:
  `config/opencode.profile.template.jsonc` grants both unconditionally in its own permanent
  floor (`"node --test *": "allow", "node --check *": "allow"`, alongside `"g++ *"` and
  `"cmake *"`), spliced in ahead of `__BASH_ALLOW__` on every rendered profile regardless of
  what a spec's `bash_allow` asks for. S1's model can therefore run `node --test` or
  `node --check` live in its own shell tool during a run. `uv run …` needs no grant either —
  it is already allowed by the template (`"uv run *": "allow"`), and adding it via
  `bash_allow` would itself be refused (`uv` is also in `BASH_ALLOW_FORBIDDEN_FIRST`) — so
  S2 and S3's specifications omit it.
- **`node --test <bare-directory>` fails in this sandbox's Node (v22, `/usr/bin/node`, bound
  in read-only from the host — not whatever `node` a developer's own shell resolves to)**: passing a
  directory path directly (`node --test js/tests`, with or without a trailing slash) errors
  `MODULE_NOT_FOUND` trying to `require()` the directory itself, rather than discovering test
  files under it; a quoted or unquoted glob (`node --test js/tests/*.test.js`) and no-argument
  auto-discovery from the working directory both work correctly. Every `node --test` command
  in this unit's briefs and `kit/suite.json` uses the glob form for this reason.
- **The old-brief §K3 note on hidden graders** ("copied into the seat only after the patch
  has applied, as today") already matches this unit's `verify.hidden` mechanism; no change to
  `local-build.sh verify` was needed for a JavaScript- or C++-graded stage, since every hidden
  grader here is a single Python (pytest) file that itself shells out to `node`/`make`/loads
  the built library — see `kit/hidden/*.py`'s own module docstrings.
- The exact mechanics of how `harness/run_suite.sh` invokes `local-build.sh run` with
  `kit/seat/` as the seat argument (a non-git subdirectory of the outer clone) are that unit's
  (U5's) to settle; this unit's briefs, specs and hidden graders assume paths are resolved
  relative to `kit/seat/` as stated by the coordinator's design note of 2026-09-08, and were
  verified against a hand-built reference solution copied into a scratch copy of `kit/seat/`
  (all four hidden graders passed; each also fails, as required, against the shipped stub
  seat) rather than against a live `run_suite.sh` run.
