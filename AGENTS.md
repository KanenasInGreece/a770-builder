# a770-builder — the profiling guide

Runs one local Intel Arc GPU as a coding-model seat. A model earns a profile here by being measured on
this card, never by reputation. This file is how an agent runs the kit and adds a model.

## A profile

One row of `config/profiles.json` (display-safe) or `config/profiles.inference.json` (pure-inference): the model
file, quant, window, KV, sampling line, measured speed, and the task class it wins. The key is the card identity
(family-weight-quant-backend), never a window name. One best row per (task, card, category, weight_class, backend);
`config/models.md` is the ledger. A row's numbers are measured, never copied.

## Run the kit

`bash skills/local-build/scripts/local-build.sh doctor` must report all ok; put the GGUF in `A770B_MODELS`;
work from a standalone clone, never a live checkout. The one command:

    bash harness/ladder.sh <profile-or-gguf> [--ctx N] [--kv f16|q8_0|q4_0] [--kv-v …] [--extra "…"] [--seat <path>] [--dry-run]

It runs every rung in order, stops at the first failure, and writes one JSON plus a REGISTRY ROW to paste.

## The ladder

1. Load and VRAM (`bench_model.sh`) — does the window load under the mode's cap (13.0 display / 15.3 inference)?
2. Probes and the 17k summary — sane answers, a tool call, a coherent summary.
3. Speed (`bench_speed.sh`) — llama-bench from the row's own flags at depths 0, 8k, 32k and the far end.
4. Window (`ctx_sweep.sh 8000 <far end>`) — the 8k and far-end points, VRAM and kernel resets watched.
5. Depth probe (`depth_probe.sh`) — a planted detail answered at depth; caps `useful_ctx` when it fails.
6. Task (`run_suite.sh`) — the standard suite (design note, front end, backend, C++), pass tally.

## The bar

A builder-class row is green on its task (the suite at 80 percent or better, or a T1 pass on the sibling
instrument), every probe passed, and no engine reset. Speed and useful window are recorded, not gates. A row
that misses the bar can still be a reader or a reviewer; its `use_for` says so.

## Submit

The ladder leaves the results JSON and capture under `A770B_DATA/results/`, a ledger row in `config/models.md`,
and a registry row. Then `python3 harness/profiles.py check`, and render the skill tables:

    python3 harness/profiles.py render --skill skills/local-build/SKILL.md --snippet skills/local-build/CONSTITUTION_SNIPPET.md --inference-file config/profiles.inference.json

The submission is a pull request whose description carries the numbers and the rows compared. `release.sh` is
the only way a version number moves.
