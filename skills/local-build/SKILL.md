---
name: local-build
description: Dispatch a bounded coding task to a LOCAL Intel Arc model (llama.cpp vulkan/sycl in a container, opencode in bwrap). Use for a ruled change to named files, tests from a spec, or a read your window cannot hold. A GGUF Q4/Q6/IQ3 label is weights-only — a slow run is often the wrong file or backend, not the wrong model. Always a standalone clone. No default profile.
---

# local-build

`run` starts or switches the server, runs the brief through opencode in a seat-only sandbox, captures diff + tests, resets. Judge by `verify`, never the exit code. Bounded work only. Not design. Not a live checkout.

`<skill-dir>` is the folder that contains this SKILL.md (not a hard-coded `~/.claude/...`). `opencode` must be on `PATH`.

## Commands

`doctor` first if this host is unknown. `status` before `run` or `serve` — it reports the live VRAM cap (`A770B_VRAM_CAP_GIB`), not a number in this file. `--profile` is required unless the spec JSON sets `"profile"`.

| Action | Command |
|---|---|
| Run | `bash <skill-dir>/scripts/local-build.sh run [<seat>] <brief.md> --profile <card> [--spec <spec.json>] [--timeout <s>]` |
| Verify | `bash <skill-dir>/scripts/local-build.sh verify <label_or_patch> [<seat>] [--test "<cmd>"] [--timeout <s>]` |
| Cards | `bash <skill-dir>/scripts/local-build.sh profiles` · `profiles --name <card>` (full `use_for`) · `menu` |
| State | `bash <skill-dir>/scripts/local-build.sh status` |
| Doctor | `bash <skill-dir>/scripts/local-build.sh doctor` |
| Server | `bash <skill-dir>/scripts/local-build.sh serve <card>` · `stop` |
| Stop run | `bash <skill-dir>/scripts/local-build.sh stop-run` |
| Reset | `bash <skill-dir>/scripts/local-build.sh reset [<seat>]` |

`run` writes `~/local-ai/results/<label>.task.md` and `<label>.patch`. Default seat is `A770B_SEAT` when `<seat>` is omitted. Smoke the default seat with the brief only — a spec that names files the seat does not have is refused.

Display-safe (`A770B_CARD_MODE=display`). Pure-inference (`A770B_CARD_MODE=inference`). Tables are rendered — do not edit them.

<!-- profiles:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k |
|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB | 36.8 / 571 tok/s |
| gemma4-8b-e4b-q4km-vulkan | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB | 60 / 796 tok/s |
| qwen38-27b-iq3xxs-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB | 8.1 / 72 tok/s |
<!-- profiles:end -->

<!-- profiles-inference:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k |
|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB | 29.3 / 385.9 tok/s |
| qwen38-27b-iq3s-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB | 7.9 / 71 tok/s |
| qwen38-27b-iq3s-sycl | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 100,000 (~100k) | 14.61 GiB | 9.91 / 358.35 tok/s |
<!-- profiles-inference:end -->

## Spec (optional)

```json
{ "profile": "<card>", "timeout": 1500,
  "card": "Local_Documentation/BUILDER_CARD.md",
  "scope": { "edit": ["src/foo.py", "tests/test_foo.py"] },
  "bash_allow": ["make check"],
  "context": { "definitions_of": ["src/foo.py"] },
  "verify": { "test": "uv run --with pytest python -m pytest -q tests/test_foo.py", "hidden": ["test_foo_hidden.py"] } }
```

`card` is standing instructions (file in the seat, or `{"text":"…"}`, ≤8000 chars). `scope.edit` limits edits. A CLI flag wins over a spec key.

## Always / Ask / Never

- **Always:** standalone clone; judge by `verify`; end a run with `stop-run`; brief names files, the exact test, and a stop (`briefs/TEMPLATE.md`).
- **Ask:** raise a cap; a second OS user on docker/render.
- **Never:** live checkout or linked worktree; raise the mode cap or `-ub 512` by hand; speculative decoding; kill `bwrap` by name.

## Load when needed

`docs/OPERATING.md` — run, knobs, logs, diagnosing a slow file/backend. `AGENTS.md` — ladder. `kit/PROFILE.md` — registry fields. `config/models.md` — ledger.
