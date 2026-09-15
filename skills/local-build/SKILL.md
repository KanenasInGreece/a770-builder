---
name: local-build
description: Dispatch a bounded coding task to a LOCAL model on an Intel Arc GPU (llama.cpp in a container, opencode in a bubblewrap sandbox) instead of an online seat. Serving is container-only (vulkan or sycl). A profile is a card identity (family-weight-quant-backend); run/serve take an explicit --profile <card> — there is no default. Use for an already-ruled change to named files, tests from a specification, or the read of a file your window cannot hold; and as the fallback when online seats are down. Always a standalone clone, never a live checkout. Measured on a 16 GB Intel Arc A770.
---

# local-build — the A770 builder seat

Dispatch a bounded coding task to a local model. `run` starts the server, runs the brief through opencode inside a
sandbox whose only writable tree is the seat, captures the diff and the model's test line, and resets the seat. Judge
by `verify`, never the exit code.

Use it when the work is bounded and does not need your own model or context. Not for design work, not for a live
checkout.

## Pick a card

`profiles` prints the measured card; `menu` is the pick-a-row list. Two card modes, `A770B_CARD_MODE` selects the
registry. The tables below are rendered from the registry — do not edit them.

Display-safe (default; the card also draws the desktop, cap 13 GiB):

<!-- profiles:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB | 36.8 / 571 tok/s | Every ordinary change, tests from a specification, and a read up to about 64k. Reads exactly at 100k but takes eleven minutes to get there. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| gemma4-8b-e4b-q4km-vulkan | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB | 60 / 796 tok/s | The fast reader: a large file read cold in about four and a half minutes at 100k and precise questions about a passage deep in it. Not the profile for edits. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| qwen38-27b-iq3xxs-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB | 8.1 / 72 tok/s | A deliverable larger than its brief, tests written from an unfamiliar module, a change touching several files. Ten to twenty-five minutes; decode under five tokens a second by 64k, so point it at files that fit 32k. A measured card may run the IQ3_S file at a larger window through builder.env. |
<!-- profiles:end -->

Pure-inference (the card draws nothing, cap 15.3 GiB):

<!-- profiles-inference:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB | 43.7 / 439 tok/s | Every ordinary bounded change, tests from a specification, and a read up to its whole window at above five tokens a second; the depth probe is exact at 100k. Not the kit's multi-stage project: SUITE-1@1 passed 1 of 3 counted stages. |
| qwen38-27b-iq3s-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB | 7.9 / 71 tok/s | A deliverable larger than its brief, tests from an unfamiliar module, a change touching several files: the best-written output here, at eight tokens a second; useful to about 98k by the four-tokens-a-second rule, so a long read costs minutes per 10k tokens. Clears the kit's builder bar (SUITE-1@1: 3 of 3 counted stages). |
| qwen38-27b-iq3s-sycl | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 100,000 (~100k) | 14.61 GiB | 9.91 / 358.35 tok/s | The faster sibling of qwen38-27b-iq3s-vulkan: the same best-written 27B at about ten tokens a second at 8k (SYCL container, q8_0 KV) instead of eight (Vulkan), with prefill about five times faster (358 vs 71 t/s at 8k), on a 100,000-token window that fits under the 15.3 GiB cap; 131,072 does not load — the SYCL server segfaults above 100,000. Clears the kit's builder bar (SUITE-1@1: 3 of 3 counted stages, all three reference exercises passed). useful_ctx is derived from THIS row's measured 8k/64k decode (the four-tokens-a-second crossing is about 155k, above the served 100k, so it is capped at the window); the depth probe is not run, so this is a first cut, not a full ladder. |
<!-- profiles-inference:end -->

`status` shows the mode and registry actually configured; `config/models.md` is the ledger.

## Call it

```bash
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md> --profile <card> [--spec <spec.json>]
bash ~/.claude/skills/local-build/scripts/local-build.sh verify <label> [--test "<cmd>"]
bash ~/.claude/skills/local-build/scripts/local-build.sh profiles | menu | status | serve <card> | stop | stop-run | reset
```

`run` prints the capture (`~/local-ai/results/<label>.task.md`) and writes `<label>.patch`. Review the capture, then
`verify <label>` re-applies the patch to a clean seat and runs the tests with no model and no key; the exit code is
the verdict and the model's pytest line is only a claim.

## The run specification

The brief is prose. `--spec <spec.json>`, every key optional:

```json
{ "profile": "<card>", "timeout": 1500,
  "card": "Local_Documentation/BUILDER_CARD.md",
  "scope": { "edit": ["src/foo.py", "tests/test_foo.py"] },
  "bash_allow": ["make check"],
  "context": { "definitions_of": ["src/foo.py"] },
  "verify": { "test": "uv run --with pytest python -m pytest -q tests/test_foo.py", "hidden": ["test_foo_hidden.py"] } }
```

`card` (a file in the seat, or `{"text": "…"}`, at most 8,000 chars) is the model's standing instructions. `scope.edit`
limits edits. `bash_allow` adds commands the profile lacks (no bare wildcard, no wrapper/interpreter first word).
`context.definitions_of` greps named files' definitions into the brief (at most 8 files, 400 lines each).
`verify.hidden` names acceptance tests the model never saw. A CLI flag wins over a key.

## Rules (enforced)

- The seat is a standalone clone; the script refuses a live checkout or a linked worktree.
- The cap and `-ub 512` are the mode's (13.0 display / 15.3 inference). Never raise a cap by hand.
- No speculative decoding on this card.
- Every opencode call takes `< /dev/null` (the script does it).
- The model sees the seat, a private home, a read-only uv cache; no credentials, no network but the server.
- The brief names the files, the exact test command, and a stop condition. Template `briefs/TEMPLATE.md`.

## Configuration

Paths and knobs live in `harness/env.sh` (defaults), overridden by `config/builder.env`, `builder.<mode>.env`, then
the environment. The skill finds the project through `A770B_PROJECT`. Models, qualification and the API key:
`docs/OPERATING.md`.
