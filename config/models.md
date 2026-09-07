# Qualified models

This file is the ledger of every model that has been put through the seat's qualification on this card, and how it
measured. It matters because the two profiles in `builder.env` are configuration, not a promise: a model earns a profile
here first, with numbers, and a model that is not in this table has not been measured on this card, whatever its
reputation elsewhere. New releases of a model family are new models; they enter the same way.

## The bar

A model qualifies when it clears four gates on the same harness, in one run of `harness/run_one.sh`:

1. **It serves under the card's rules**: llama.cpp with the Vulkan backend, `-fa on`, `--no-mmap`, `-ngl 99`,
   `--parallel 1`, `-b 2048 -ub 512`, quantised KV, at least 80k of context and at most 13 GiB of VRAM after load on a
   16 GB card that also draws the desktop, with zero GPU engine resets.
2. **It answers**: correct greedy answers on a three-prompt sanity gate and a coherent one-sentence summary after a
   prefill of about 17k tokens of source.
3. **It is fast enough**: decode at or above 5 tok/s at long context, and tool calls that llama-server parses.
4. **It does the coding task**: the qualification brief on a real repository, run through opencode in the seat; it wrote
   the test file, ran the given test command, and a cheap reviewer reading the capture graded the run green.

A red run or a failing grade keeps a model out; a style deviation with green tests is recorded as PARTIAL and admitted
with its caveat.

## Qualified on the Intel Arc A770 16 GB (llama.cpp b10805, Vulkan, 2026-09-06/07)

| model file | source | ctx / KV | VRAM after load | decode short / after 17k | prefill (17k) | task | profile | notes |
|---|---|---|---|---|---|---|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 81,920 / q8_0 (131,072 / q4_0 also passes) | 6.9 GiB | 45.2 / 33.0 tok/s | 464 tok/s, 37 s | PASS, 11 tests first try, 121 s | **fast** (default) | followed the repository's test idiom unprompted; the only self-sufficient builder of the matrix; 6 GiB of headroom |
| `Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 158,000 / q4_0, reasoning on at effort low | 11.5 GiB | 8.1 / 6.7 tok/s | 70 tok/s, 247 s | PASS, 5 tests, 617 s | **serious** | best-written file of the matrix; at the speed floor, 10–25 min per small task |
| `Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 131,072 / q4_0, reasoning on at effort low | 12.3 GiB | 8.1 / 6.7 tok/s | 65 tok/s, 265 s | PASS, 5 tests, 1,492 s | qualified alternative | same speed as IQ2_XS for 0.75 GiB more VRAM and 30k less context |
| `gpt-oss-20b-UD-Q4_K_XL.gguf` | `unsloth/gpt-oss-20b-GGUF` | 81,920 / K q8_0 + V q4_0, `--chat-template-kwargs '{"reasoning_effort":"low"}'` | 11.7 GiB | 42.1 / 27 tok/s | 585 tok/s, 28 s | PARTIAL (green), 5 tests, 79 s | qualified alternative | fastest prefill and fastest task; improvised an import scaffold instead of the repository's idiom, which a reviewer catches; always reasons, keep the output limit generous |

## Measured and kept out

Qwen3-14B, gemma-4-12B and Ministral-3-14B failed the coding task on this card. Speculative decoding in every form
tried, a vocabulary-matched draft model and the MTP heads of the Qwen3.5 and Qwen3.8 families, made decode slower on
this card under Vulkan (the Qwen3.8 MTP head 3.7 times slower despite high acceptance), so no profile uses it. Those
weights are kept for the next Intel card, where the first thing to re-measure is exactly that.

## How a model enters

The procedure is in [`docs/OPERATING.md`](../docs/OPERATING.md#qualifying-a-new-model). In one line: put the GGUF in
`A770B_MODELS`, run `harness/run_one.sh`, have a reviewer grade the capture, add the row here with its numbers, then give
it a profile in `builder.env` and update the skill's table and the version.
