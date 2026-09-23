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
weight_class, backend, engine, checkpoint weight quant, checkpoint activation quant). Numbers are measured,
never copied; `config/models.md` is the ledger.

A quant label on a GGUF is the **weight** encoding. Activations stay in the engine's working precision unless
the checkpoint itself quantises them (`checkpoint.activation_quant`; `none` on every shipped GGUF row). Looking
at `Q4_K_M` and assuming 4-bit local math is a mistake — that needs a compatible kernel on this card, which
`kernel` records only when observed. `kernel.path` and `kernel.xmx` stay `untested` until `kernel.evidence`
is a log line from this card. A speed number does not name the kernel. vLLM prints that line more often than
llama.cpp. Omitted `engine` / `checkpoint` / `kernel` means untested, not "same as `quant`". A different
checkpoint of the same model is a different row when the engine, the weight quant, or the activation quant
differs, even when the kernel is the same.

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

## Profiling a card the registry does not know

When qualifying models on a card that has no rows in the registry, do not measure blindly. Follow the extrapolation heuristic:

1. **Anchor**: Pick the closest known profile row (same `family` + `weight_class`, from any card). Its numbers are a baseline comparison, never copied.
2. **Densify by VRAM delta**: Compare the new card's measured VRAM cap to the anchor card. Use the anchor's `vram_gib_after_load` to gauge headroom; if more VRAM is available, choose a denser quantization.
3. **Prefer native dtypes**: Target the hardware's native tensor formats. On Xe2 (Battlemage), XMX runs native `int4`, `int8`, `fp16`, and `bf16`. Sub-4-bit formats like `int3` and `int2` require software dequantization that costs execution time — try native `int4` first. Native int4 math is not a GGUF `Q4_*` file: that file is typically weight-only, and activations stay at the engine's working precision. Confirm a kernel exists for that checkpoint on this card (or try another user's encoding of the same model) before spending a ladder; Intel Arc is not a regular NVIDIA-style optimisation target.
4. **Read the logs before you drop the family** (`docs/OPERATING.md`, *Engine, checkpoint, kernel*). Confirm `Vulkan0`/`SYCL0` from `llama-cli --list-devices`; on Vulkan, llama-bench's `ggml_vulkan: … matrix cores:` line (capability, not the shader — Mesa on A770 and B70 reports `none` / `int dot: 1`); on SYCL, llama-bench JSON `backends` + `n_gpu_layers`. llama.cpp will not print `falling back to CPU` or a FlashInfer/Marlin kernel name. vLLM on Xe2/B70 will name `XPUwNa16LinearKernel` / `gptq_gemm`; `switch to gptq_marlin` is a CUDA warning, not a file to fetch. Same GGUF Vulkan vs SYCL far apart, or vLLM W4A16 vs GGUF K-quant, is the **file/backend**, not the model.
5. **Run ONE approximation test**: Run a single benchmark of the candidate quant using `serve_compose.sh bench`:
   `bash harness/serve_compose.sh bench -- -m /models/<model> -ngl 99 -c <ctx> -n 128`
6. **Classify**:
   - **Compute-bound**: If decode tok/s stays flat across quantizations (e.g. Q4 vs Q6), higher precision is essentially free — densify to the larger quant for quality.
   - **Bandwidth-bound**: If decode speed drops proportionally with larger weights, memory bandwidth is the bottleneck — stay at the faster quant and record the ceiling.
   - **Kernel miss**: decode in a CPU-like band while VRAM is occupied (same Q6_K: Vulkan ~5 t/s vs SYCL ~21 t/s). Try the other backend or another user's checkpoint before a ladder.
7. **Suggest next tests**: Use the classification to pick the candidate for the full ladder (`harness/ladder.sh`). Only the full ladder measurement ships a profile row.

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
