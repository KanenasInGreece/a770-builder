# The in-house suite

This is this repository's own regression suite, distinct from the profiling kit (`kit/`, `kit/SUITE.md`): it grades
five bounded units of this repository's own history against this repository's own hidden tests, and a row measured
on it carries `seat: <this repository>@<commit>` as its instrument, never comparable with a row measured on the
kit. It has one known limit the kit does not share: the seat for a brief here is a clone of this repository at the
commit the brief names, so the same tree that carries `briefs/suite/hidden/` is what the model's seat is cloned
from — a seat that reads outside the paths a brief names could in principle read its own hidden test. The kit's
seat is an export of `kit/seat/` alone, with `kit/hidden/` never part of that export at all, which is why the kit
does not have this limit; this suite's graders stay under `briefs/suite/hidden/` until they move to the same export
model.

A profiling run needs more than one task to know what a model is good for. A single brief tells you whether a
model can follow one instruction once; it says nothing about whether the model still reads carefully on the third
file of a change, or holds a literal block in place while it edits around it, or keeps three files consistent with
each other, or reasons through a conditional check instead of pattern-matching the nearest example. This suite is
five bounded units of this repository, each shipping its own hidden grader, chosen so that between them they
discriminate on five different axes rather than one blurred average:

- **T0-smoke** is the floor: one file, one line, nothing to reason about. A model that fails this fails everything
  else for a reason that has nothing to do with capability — the harness, the seat, or the wiring is broken, and the
  rest of the suite is not worth running until this passes.
- **H2-shellcheck-selftest** asks for a block of text inserted verbatim at an exact position in a file, unwrapped and
  unparaphrased. It discriminates on instruction-following under a literal constraint: a model that reflows a line
  it was told to keep exact, or slots the block one line off from where it was told to put it, fails here even
  though the change "basically works."
- **D1-device-pin** touches three files with a single change threaded through all of them. It discriminates on
  keeping several files consistent with one another across one edit — a model that nails one file and drifts on the
  other two has not actually done the task.
- **F1-doctor** builds a CLI subcommand that walks a list of checks and reports on each independently, plus a warning
  computed from conditional logic over a registry. It discriminates on conditional reasoning and on not letting the
  first failing check stop the rest — a model that treats the checklist as a single pass/fail short-circuits it.
- **H1-shell-fixes** is eleven small edits spread across seven files. It discriminates on thoroughness under volume:
  each edit alone is trivial, so the failure mode this brief catches is a model that does most of the list well and
  quietly skips or half-does one or two edits near the end.

Each brief carries its own `.spec.json` and its own hidden test under `hidden/`; the hidden test is what actually
grades the run; the brief's own "Verify" section is what the model sees and can self-check against, which is not the
same thing as passing the hidden grader.

## How to run it

Each brief was written against a particular point in this repository's history — some name a commit, some a branch,
some just "current main" — and that point is kept exactly as the brief states it, because the hidden test was written
against the state of the repository at that point. Before running a given brief, put the seat (a clone of this
repository, never a live checkout) at the commit or branch that brief's own text names: `git clone
https://github.com/KanenasInGreece/a770-builder <seat> && git -C <seat> checkout <the commit the brief names>` —
unlike the qualification seat, which is pinned once to the public Shared Memory repository at commit `3c8e2bb`, this
one is pinned fresh, per brief, to a commit of this repository.

With the seat prepared and `A770B_HIDDEN_ROOT=<checkout>/briefs/suite/hidden` set (`<checkout>` is the path to this
repository's own clone, the one this `briefs/suite/` directory lives in — not the seat), run one brief at a time:

```
local-build.sh run <seat> briefs/suite/<brief>.md --spec briefs/suite/<brief>.spec.json --profile <profile>
```

`<brief>` is one of the five names above; `<profile>` is the profile under test. Follow each run with:

```
verify <label>
```

to re-grade it against the hidden test in a fresh sandbox — the capture's exit code is never the verdict, the
grader's re-run is. Do this for all five briefs. The row's `suite` object is one summary of the five runs together,
not one entry per brief: `briefs` (the count, 5), `runs` (how many were actually run), `passed`, `mean_wall_s` and
`source`. That summary also feeds the row's `fit.code` string: tests passed and wall, the same shape the single T1
task uses for `fit.code` when the suite is not run.

A row's suite result means something only beside another row's suite result measured the same way: compare a row on
this suite only against rows that were themselves measured on all five of these briefs, at the commits or branches
they name, under the same hidden graders.
