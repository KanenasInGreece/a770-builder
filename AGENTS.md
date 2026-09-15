# a770-builder

Run one local Intel Arc GPU as a coding-model seat, and measure models into profiles with the profiling kit.
Stack: a bash + Python 3 (stdlib) harness; llama.cpp in a Docker/Podman container (vulkan or sycl backend);
opencode inside a bubblewrap sandbox.

## Commands

| Action | Command |
|---|---|
| Check the machine | `bash skills/local-build/scripts/local-build.sh doctor` |
| Serve a card | `bash skills/local-build/scripts/local-build.sh serve <card>` |
| Status | `bash skills/local-build/scripts/local-build.sh status` |
| Pick a card | `bash skills/local-build/scripts/local-build.sh profiles` / `menu` |
| Profile a model | `bash harness/ladder.sh <gguf> --ctx N [--kv f16|q8_0|q4_0] [--reasoning on|off] [--reasoning-budget N] [--dry-run]` |
| Validate the registry | `python3 harness/profiles.py check --file config/profiles.inference.json` |
| Test | `uv run --with pytest python -m pytest -q tests/` |
| Self-test | `env -u A770B_CARD_MODE -u A770B_PROFILES_FILE -u A770B_CORPUS_FILE A770B_PROJECT=$PWD bash tests/selftest.sh` |

## A profile

One row of `config/profiles.json` (display-safe) or `config/profiles.inference.json` (pure-inference). The key is a
card identity (family-weight-quant-backend), never a window name. One best row per (task, card, category,
weight_class, backend). Numbers are measured, never copied; `config/models.md` is the ledger.

## The ladder

`ladder.sh` runs six rungs in order and stops at the first failure:

1. Load + VRAM (`bench_model.sh`) under the mode's cap (13.0 display / 15.3 inference).
2. Probes + 17k summary.
3. Speed (`bench_speed.sh`) at depths 0, 8k, 32k, far end.
4. Window (`ctx_sweep.sh 8000 <far end>`).
5. Depth probe (`depth_probe.sh`) — caps `useful_ctx` when it fails.
6. Task (`run_suite.sh`) — the standard suite.

A builder-class row is green on the task (suite at 80% or better, or a T1 pass), every probe passed, and no engine
reset. Speed and window are recorded, not gates.

## Submit a profile

1. Put the GGUF in `A770B_MODELS`; `doctor` must report all ok.
2. Run the ladder; fill the printed row's `__TODO__` fields by hand.
3. `python3 harness/profiles.py check`, then render the skill tables:
   `python3 harness/profiles.py render --skill skills/local-build/SKILL.md --snippet skills/local-build/CONSTITUTION_SNIPPET.md --inference-file config/profiles.inference.json`
4. Open a pull request whose description carries the numbers and the rows compared.

## Boundaries

### Always
- Work from a standalone clone, never the live checkout.
- Judge a local-build result by `verify <label>`, never by the model's own test line.
- Run the suite and selftest before merging a harness change.

### Ask first
- Changing the registry schema or the kit instrument (`kit/suite.json`).
- Bumping the version.

### Never
- Touch `VERSION`, `SKILL_VERSION`, or `release.sh` — `release.sh` is the only way a number moves.
- Copy a number into a profile you did not measure.
- Commit secrets, `.env`, keys, or the HF token.

## Load when needed
- `docs/OPERATING.md` — day-to-day running and qualifying a new model.
- `kit/PROFILE.md` — how a kit run becomes a registry row.
- `kit/SUITE.md` — the standard suite's stages.
- `config/models.md` — the ledger of every model measured.
