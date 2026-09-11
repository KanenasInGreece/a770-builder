# Qualifying a new model — the profiling guide

This guides the profiling procedure with the included kit: how to measure a candidate model on this card and, if
it qualifies, add it to the list of models this project can run.

a770-builder is a harness around a local GPU: it runs a coding model in a sandbox, measures models into profiles
with the packaged tests this guide walks through so you can add your own, and presents the result to an LLM
orchestrator as a skill it calls. Developed and tested on a 16 GB Intel Arc A770.

This file is for the agent, or person, who has a GGUF and this harness and wants that model on the list of
profiles the orchestrating seat can propose. The models that already have profiles, and the exact file of each, are
in `docs/OPERATING.md`; this guide is for a model that is not yet on that list. It is not read by the model that
builds this repository: the harness
sets a seat's own copy of this file aside for the length of a build and restores it after. It exists for the
separate act of profiling — climbing the ladder below and deciding whether a model earns a row.

The project's offer is the ladder itself. A model does not enter the registry by reputation, a benchmark someone
else ran, or its own model card's claim: it enters by being measured on this card, every number written down, and
a row is only ever compared against other rows measured the same way. A row is a measurement on one card and one
build; on another card the same ladder produces that card's rows, and the registry may hold them beside these under
the mode that fits.

## What a profile is

`kit/PROFILE.md` is the field-by-field derivation: which rung fills a field, how its number is worked out, and
what a reader can honestly compare it with outside this project. A profile is one row of a registry, what the
orchestrating agent reads before it picks a model for a brief. Two
fields place it among rows it might be judged against: `category`, whether the model is `dense` or a mixture of
experts (`moe`), and `weight_class`, a short label such as `9b`, `12b` or `35b-a3b`. Together they keep the
registry from carrying two rows that are really the same choice twice.

The window has two numbers. `ctx` is what the model is served at — the largest window that loads under the mode's
cap, a human's own choice that rung 1 of the ladder below only confirms. `useful_ctx` is the largest depth at which
decode still holds above four tokens a second: taken from the time per token at two measured points, 8k and the far
end of the window (a target of 100k, or the window less 8k when the window is under about 110k — a target, not a
promise: `ctx_sweep.sh` sizes its corpus slice by the harness's own four-bytes-a-token rule, and this kit's corpus is
dense real source, not prose, so that rule understates — a sweep asking for 8,000 tokens sent 12,718 (measured
2026-09-08). The far end recorded for a row is whatever token count its reply actually measured, never the number
typed on the command line, and a row too slow to answer inside the run's time ceiling leaves no far-end reading to
record at all: the 27B's own 100k attempt exceeded a 3,600 s ceiling with no answer), extended linearly between
them, then capped at the depth probe's last passing depth — 8,000 tokens whenever the far end's own probe fails,
since 8k is the only other point this ladder actually probes the model's quality at. This is the one definition of
`useful_ctx` in this project: `harness/ladder.sh` computes it exactly this way, and no other threshold — a prefill
figure, a different tokens-a-second floor — stands in for it anywhere else in these documents. A row can serve a
large `ctx` and still have a much smaller `useful_ctx` once decode falls under that floor.

Decode and prefill tokens per second are recorded at both points, plus time to first token at the far end; nothing
is scored on a number pulled from the middle of the window. Quality has three short strings, each with its own
source: `code` (the suite or T1, tests passed and wall time), `think` (a reasoning arm measured on this card, or
else the model's own card's GPQA or AIME figure, named as the card's), and `write` (a reviewer-graded prose brief
where one exists, else plainly "not measured" — a missing number is never invented).

Every inference row and the display serious row carry the sampling line the model's own card recommends for the
role it fills — temperature, top_p, top_k, min_p, the penalties — and where that line was read; the display long
and fast rows were measured at the client's default and carry none until re-measured. The served flags carry it
into the server; the
same numbers are rendered into the client's own agent block too, because the client otherwise supplies its own
temperature and silently overrides the server's. A row needing more than the card's VRAM states that as RAM beyond
VRAM — a mixture-of-experts row that keeps some layers off the card costs host memory the VRAM figure does not show.

`builder_class` is not set by hand; it is computed from the row's own numbers — a useful window at or above 81,920
tokens and a green task. And `use_for` is not only what a row is good for: it also names what the row is not for,
or not to be run beside — a reading profile not meant for multi-file edits, a mixture-of-experts row that wants a
CPU that is not otherwise busy.

## What stays fixed

Some things are not re-measured for every row, settled once. Flash attention is on for every family but one: every
Gemma 4 model measured here collapsed on long prefill and reset the GPU with it on, and ran clean with it off;
llama.cpp cannot quantise the V cache without flash attention, so a Gemma row's V cache runs at f16 regardless of
K. `--no-mmap` is always set, one process holds the card at a time, and `-ub 512` holds in both card modes. No
speculative decoding is used in any form tried — a draft model and the multi-token-prediction heads of two Qwen
families both made decode slower, not faster.

The one number that changes between modes is the cap: 13.0 GiB after load on a card that also drives a desktop,
15.3 GiB on a card that draws nothing else, raised only after a fresh measurement, never by hand. Everything not
named here is a parameter of the row: quantisation, KV type, window, sampling, whether reasoning runs, and, for a
mixture of experts, how many expert layers sit on the card versus in host memory.

## Before the first rung

Read the model's own card first: its recommended sampling line per mode it offers (thinking, plain instruct,
coding) and its native context length. Decide which role — the profile a candidate would fill or replace — the row
is for before spending time on the card. Put the GGUF where the harness looks for models (`A770B_MODELS`). Profile
from a seat that is a clone of the target repository, never a live checkout. Confirm `local-build.sh doctor`
reports all ok, and confirm which card mode you are profiling for — display, under the 13.0 cap, or inference,
under the 15.3 cap — since the two modes keep separate registries and a row belongs to one of them.

From this release the ladder's task rung is measured on the kit inside this repository, `kit/`: a small,
self-contained application in Python, C++, JavaScript and HTML, with its own hidden graders and three reference
exercises, needing no second repository at all. `harness/run_suite.sh <profile>` exports `kit/seat/` — never a
live checkout of this repository and never a clone of it either, since the model must not read `harness/` — as the
run's own working tree, runs each stage of the standard suite (the design note, the front end, the backend, the
C++ optimisation) and the three reference exercises through the skill and `verify`, and writes a results file that
names its instrument. A row's task numbers carry that instrument (`instrument: SUITE-1@1` from `kit/suite.json`'s
`kit_version`, or `seat: kit/seat@<commit>`) and are compared only against rows carrying the same one; see *The
standard suite* below. The kit version moves when the tests or graders change, not when the product version does.

Every number in the ledger and the registries measured before this release carries a different instrument: the
seat was a standalone clone of the public Shared Memory repository, `https://github.com/KanenasInGreece/Shared_Memory`,
at commit `3c8e2bb` (its release v0.9.94): the qualification task `briefs/T1-sanitize-entity-tests.md` names that
repository's function `sanitize_entity_name` in `shared-memory/scripts/ontology.py` and its `tests/` idiom; the
long-context corpus for the sweep and the depth probe was that clone's Python files (`A770B_PROBE_CORPUS`, default
every `*.py` under the seat); the 17k summary prompt read the same files. Those rows stay in the ledger and the
registries, recorded as measured on the sibling repository at its commit (`seat: Shared_Memory@3c8e2bb`), and stay
comparable only with each other. The clone-the-sibling instruction below stays only as the record of how they were
taken, for a reader who wants to reproduce one of them, not as an instruction for a new row:

```
git clone https://github.com/KanenasInGreece/Shared_Memory ~/local-ai/seat && git -C ~/local-ai/seat checkout 3c8e2bb
```

The in-house suite under `briefs/suite/` is a third, separate thing again: the harness's own regression suite, run
against a clone of this repository at whatever commit each brief names, never the profiling suite —

```
git clone https://github.com/KanenasInGreece/a770-builder <a second seat> && git -C <that seat> checkout <the commit the brief names>
```

— the harness needs nothing from either repository at run time beyond what a brief names: each is the target of a
task, not a dependency of the harness. Comparability is the point of pinning an instrument: a different seat, a
different commit or a different suite version is a different instrument, and a row is only ever set beside another
row measured on the same one. A reader who profiles a model of their own against the same `kit_version`
gets numbers comparable with the ledger; a reader who only wants a row for their own repository writes their own
brief from the template and states plainly that the numbers are not comparable — the row is still theirs, and the
profile is still the interface, but it is a different instrument.

## The ladder

```
harness/ladder.sh <profile-or-gguf> [--ctx N] [--kv f16|q8_0|q4_0] [--kv-v f16|q8_0|q4_0] [--extra "<flags>"]
                  [--timeout S] [--seat <path>] [--suite <suite.json>] [--reviewer <profile>] [--fresh] [--dry-run]
```

is the one command that runs every rung below, in this order, stopping at the first failure — a later rung is
wasted on a model that failed an earlier one. `<profile-or-gguf>` is either a name already in the registry (every
knob comes from its row; passing `--ctx`/`--kv`/`--kv-v`/`--extra` alongside one is refused) or a bare GGUF or
absolute path — a model with no row yet, which needs `--ctx` and climbs every rung except rung 3 (`bench_speed.sh`
needs a registry profile; it is skipped for a bare GGUF, with a clear note in the output). It writes one JSON
holding every rung's own numbers, whatever ran, then prints a REGISTRY ROW ready to paste under `profiles.<name>`
in `config/profiles.json` or `.inference.json`. `--dry-run` prints every rung's command and writes nothing.

1. **Load and VRAM at the target window** — `harness/serve_a770_llamacpp.sh start`, wrapped inside
   `harness/bench_model.sh <label> <gguf> <ctx> [flags]`. Decides whether the window given actually loads under the
   mode's cap, and the after-load VRAM (`vram_gib_after_load`) and, for a MoE row, the host RAM the load costs
   beyond it (`ram_gb_extra`, the `MemAvailable` drop across the load).
2. **The probes and the 17k summary** — the rest of `bench_model.sh`: greedy sanity answers, a tool call, a
   coherent summary after roughly 17k tokens of prefill. Decides whether the row is sane enough for the rungs after
   it to be worth running at all.
3. **The standard speed rung** — `harness/bench_speed.sh <profile>`: llama-bench built from the row's own served
   flags (`-fa`, `-ctk`/`-ctv`, `-ub`, a MoE row's `--n-cpu-moe`), at depths 0, 8192, 32768 and the far end. Decides
   `speed.bench`: one command a reader can reproduce byte for byte and set beside another row's. Needs a registry
   profile (`harness/profiles.py card --name <profile>` has no ephemeral form); skipped for a bare GGUF.
4. **The window rung** — `harness/ctx_sweep.sh 8000 <far end>` (the far end: 100000, or the served window less 8k
   when that window is under about 110k). Decides the two measured speed points — 8k and the far end, prefill,
   decode, time to first token — `useful_ctx`'s linear extension is drawn from, and is the safety read for what
   llama-bench does not watch: VRAM sampled during each prompt and the kernel log watched for a reset. Its own
   "8000" and the far end are targets, not measurements — the corpus slice is sized by four bytes a token, and this
   kit's dense source corpus makes that rule understate, so a sweep asking for 8,000 tokens has sent more before
   now — and a row can be too slow to answer at all before the run's own time ceiling, in which case there is
   nothing to record at that depth.
5. **The depth probe, now graded** — `harness/depth_probe.sh <far end>`: a detail planted about 85 percent of the
   way into a large prompt, answered and self-graded PASS/FAIL per question (exit 0 iff at least two of three).
   Decides whether `useful_ctx`'s linear extension is trusted past 8k at all: where it fails, `useful_ctx` is
   capped at 8,000 regardless of what the arithmetic from rung 4 would otherwise say.
6. **The task rung** — `harness/run_suite.sh <profile>` (or, for a model with no row yet, `--model <gguf> --ctx
   <n> [--kv …] [--kv-v …] [--extra …] [--timeout …]` — the same path a new GGUF climbs the task rung on before a
   human has measured and pasted its row, and exactly what `--dry-run`'s rung 6 line shows for a bare GGUF). Decides
   the row's `suite` object — which stages passed, out of the stages that count toward the tally: a reference
   exercise, and any stage carrying `counts_toward_pass: false`, never does.

`harness/cancel_repro.sh` — the cancel reproduction, once per model — is **not** part of this ladder: a request
cancelled mid-flight while the slot is held must not take the card down, and it stays a one-off, once-per-model
check an operator runs by hand. Watch the kernel log across every rung above; a reset anywhere is disqualifying,
whatever the other numbers say.

**What the ladder still leaves to a human.** Everything above is measured or computed; the following is not. A
window, KV types, expert placement and flash attention are inputs the ladder takes as given, on the command line or
from an existing row, and never searches for itself; `use_for`, `fit.write` and `capability` come back in the
printed row marked `__TODO__`; the reviewer is a plain `--reviewer <profile>` choice; `fit.think` is not in the
printed row at all:

- **the largest window under the cap** — rung 1 confirms whether a given `--ctx` loads; which window to try, and a
  MoE row's own `--n-cpu-moe` count, is a human's own search, counting down from a high number until it fits.
- **the expert placement for a mixture-of-experts row** — how many expert layers sit on the card versus in host
  memory, chosen by hand and re-measured, never searched by the ladder itself.
- **flash attention on or off** — fixed per family (*What stays fixed*, above), decided once from a family's own
  behaviour, not re-measured per row.
- **the KV types** — `--kv`/`--kv-v` are given to the ladder, never derived by it.
- **which model reviews the rubric** — `--reviewer <profile>`, a human's choice of a profile that the runner
  refuses to let resolve to the builder's own model.
- **the `use_for` sentence** — written by hand from what the ladder run actually showed; never derived from a
  measurement.
- **the capability figures** — the model's or the quantiser's own published evaluation, quoted verbatim, never
  re-measured by the ladder.

Beside these, a row's identity fields — `source`, `family`, `architecture`, `quant`, `category`, `weight_class`,
`params_b`, `capability_source`, `sampling`, `measured_on` and (for a row reproducing the sibling-repository
instrument) `task_t1` — are transcribed once by hand from the GGUF's own metadata and the model's public card,
never derived from a rung; `ladder.sh`'s own closing note lists them again for the row it just measured.

## The standard suite (SUITE-1)

The kit's standard suite is one small application, "logstats", built across four stages that depend on each other
the way a real project does: a design note, a front end, a Python backend, and a C++ optimisation of its hot path.
Each stage has its own brief and its own hidden grader, and each stage's seat carries the reference solution of
every stage before it, never the model's own earlier output — so a later stage is graded on work that is actually
correct, not on what this run's own model happened to write two stages back; the stages are independent of each
other by construction. Every stage is scored on up to five axes: **working** (the hidden test, pass or fail),
**conformance** (the compiler with warnings as errors, `node --check`, `compileall`, pass or fail), **concise**
(lines against a stated budget, a number), and, by a reviewer that did not build the change, **maintainable**
(every stage) and **usable** (the design note and the front end only — the backend and the C++ stage have no
user-facing surface for that axis to grade). The design note's own hidden check and every stage's reviewer score
are scored outside the pass count: a design note is commentary on whether the fixed interface was restated and
justified, not "working code" the way a passing test suite is.

The reviewer pass runs after the builder's own server is stopped, through a separate profile the runner refuses to
let resolve to the builder's own model, with each stage's patch delimited as untrusted data and a fixed prompt at
temperature 0; the reviewer's own profile, window and sampling, and the rubric's hash, are pinned beside the score
in the results file, so a reader can tell which model graded a row and against which version of the rubric.

Beside the mini-project sits a reference rung: one exercise per language — C++, JavaScript and Python — from
Aider's own polyglot benchmark, chosen from each track's harder tier, graded the way Aider grades them (the
exercise's own public tests, run by the language's own runner with nothing installed) plus a hidden variant with
fresh inputs, because the exercise and its tests are public and a model may already have seen them. A row states
both numbers per language side by side: the public exercise, relatable to Aider's own leaderboard, and the kit's
own hidden stage, which is what actually counts. The pair is a contamination indicator, not two correctness
scores, and its null is stated plainly: both passing carries no information about memorisation either way — the
signal, if there is one, is a public pass beside a hidden fail.

The window instrument is the sweep and the depth probe run over `kit/corpus.py`'s own generated file: real source
only, no synthetic filler — the kit's own seat and reference material and this repository's own harness and
documents, concatenated deterministically with a floor stated in bytes, large enough on its own that the display
serious profile's 131,072-token window can be swept and probed all the way to its far end on any configuration. The
sweep and the probe both refuse a corpus shorter than the prompt they are asked to cut rather than silently
short-filling it; the 17k summary rung keeps its own separate knob on real project source, unchanged.

Inside the sandbox, the toolchain the kit's graders need is already there and nothing more is installed: the
system's own `node` (22 on this host — a version manager an interactive shell might carry is not what the sandbox
sees), `cmake`, `make`, `g++` and `python` with the warm uv cache (`harness/warm_cache.sh`); no network, no package
install, at run time or at grading time. Of that toolchain the model itself may only run `node`, `g++` and `cmake`
directly, because those come from the profile template's own allow list; `make` and `ctest` are granted per task
through that task's own specification, since a multi-file C++ stage needs a configure step the single-header one
does not. Every grader itself runs outside the model's own shell tool entirely, through `verify`, with no
allow-list of its own — the allow-list is for the model's own iteration, not for how a row is actually graded.

A public suite has a shelf life: SUITE-1 is revised about once a year, with its hidden inputs regenerated, so a
version that has stood a while is not assumed still uncontaminated. "Hidden" means hidden from the running model,
never from the corpus itself.

## The bar

A builder-class row is useful to at least 81,920 tokens AND has a green task — for a row measured on the kit, the
standard suite's stages that count toward the pass tally at 80 percent or better; for a row measured on the
sibling repository, T1 passed or the in-house suite at 80 percent or better — every probe in the ladder passed, and
no engine reset happened anywhere in the run. A row that misses this bar is not a builder, whatever
else is true of it — but it can still earn a place as a reader or a reviewer, and its `use_for` says so plainly.

## The comparison

The registry holds at most one best row per category and weight class, not a ranked list and not a single scalar
score: the card carries its axes, and a candidate is weighed against the row it would replace on those axes
directly. A candidate enters when it beats that row on at least one axis — quality by the suite (or T1 where the
suite was not run), speed by decode at 8k, or the useful window — without losing on the others, or when it fills an
empty category and weight class. A row dominated on every axis by a sibling is dropped, and the ledger names which
row displaced it and why. Rows that each win a different axis all stay, one row per axis at most; a class holding
only one row keeps it for as long as it clears the bar above.

The rows measured so far are the working reference for what "beats" and "dominated" mean in practice: a dense
9B-class file as the long, general-purpose row; a dense 27B-class file as the slow, best-written row, useful to
somewhere short of 100k tokens. Alongside them sit files measured and not adopted: a model that failed the coding
task outright; a dense mid-size file that passed every gate but was dominated on every axis by files already
measured, and so stayed out despite qualifying; and a mixture of experts that decoded several times faster than a
dense 27B on the same task and is a builder by every axis the comparison names, yet is not a shipped profile,
because it keeps part of its weights in host memory and the harness budgets card memory only. A row that wins its
axes is still only shipped when the harness can hold it to the rules it serves every other row by.

## The submission

A finished row leaves behind: the results JSON and capture the ladder wrote under the results directory the harness
reads (`A770B_DATA/results`); a ledger row in `config/models.md` carrying the measured numbers, the source
repository, and any caveat the run surfaced; a registry row in the mode's file (`config/profiles.json` for display,
`config/profiles.inference.json` for inference) with every field filled in — the `sampling` object with its source,
the three `fit` strings with theirs, a `suite` object when the suite was run, `category` and `weight_class` —
rather than a partial row that leaves a reader guessing what was measured. Run `python3 harness/profiles.py check`
on the file. Run the render command under *Qualifying a new model* in `docs/OPERATING.md` so the skill's tables and
its snippet pick up the new row instead of drifting from the registry. A version bump belongs to the maintainer's
own release script, never to a submission. The submission itself is a pull request whose description carries the
numbers the ladder produced and names the rows the candidate was compared against, so the reviewer can check the
comparison without re-running the ladder.
