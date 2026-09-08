# Qualified models

This file is the ledger of every model that has been put through the seat's qualification on this card, and how it
measured. It matters because the profiles in `config/profiles.json` are configuration, not a promise: a model earns a profile
here first, with numbers, and a model that is not in this table has not been measured on this card, whatever its
reputation elsewhere. Today the profiles are **long** = Qwen3.5-9B Q4_K_M, the default for every ordinary change;
**serious** = Qwen3.8-27B GSQ-RCO IQ3_XXS, for a deliverable larger than its brief; **fast** = Gemma 4 E4B Q4_K_M with
flash attention off, for the read the long window cannot hold. The profile column below says which row holds which. New releases of a model family are new models; they enter the same way.

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
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 81,920 / q8_0 (131,072 / q4_0 also passes) | 6.9 GiB | 45.2 / 33.0 tok/s | 464 tok/s, 37 s | PASS, 11 tests first try, 121 s | **long** (the window before the registry) | followed the repository's test idiom unprompted; the only self-sufficient builder of the matrix; 6 GiB of headroom. Sweep 2026-09-08 (prefill and decode against position, VRAM sampled): 8k 568 / 36.8 tok/s, 32k 274 / 23.1, 64k 150 / 16.0, 71k 125 / 14.8; VRAM flat at 7.2 GiB; zero resets — the whole 81,920 window is safe, prefill under 150 tok/s past about 64k. As a builder of this repository's own skill (2026-09-08): four bounded units from exact briefs in one run each, applied unchanged; a behavioural unit of nine rules not in two runs |
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 262,144 / q8_0 (native) | 10.35 GiB | 36.8 / 11.6 tok/s | 571 → 147 tok/s | — | **long** (default) | the native window, served in full; sweep run with context served at 131,072: 8k 571 / 36.8 tok/s prefill/decode, 100k 147 / 11.6; 100k prompt TTFT 626 s; VRAM peak +0.28 GiB over the 10.35 GiB load; zero resets; useful to about 64–72k (prefill under 150 tok/s and decode under 15 tok/s past that) |
| `Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 158,000 / q4_0, reasoning on at effort low | 11.5 GiB | 8.1 / 6.7 tok/s | 70 tok/s, 247 s | PASS, 5 tests, 617 s | **serious** (retired from the defaults) | best-written file of the matrix; at the speed floor, 10–25 min per small task. Sweep 2026-09-08: 8k 73 / 7.4 tok/s, 32k 58 / 5.9, 64k 40 / 4.7; VRAM 11.9 GiB; zero resets — under the 5 tok/s decode floor by 64k, so the profile is served with 158,000 tokens and useful to about 32,000; LiveCodeBench 76.57, under the 95 % line, retired from the defaults |
| `Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 131,072 / q4_0, reasoning on at effort low | 12.3 GiB | 8.1 / 6.7 tok/s | 65 tok/s, 265 s | PASS, 5 tests, 1,492 s | **serious** | same speed as IQ2_XS for 0.75 GiB more VRAM and 30k less context; LiveCodeBench v6 84.57 vs BF16 85.71, the new public default (fits the public 13.0 cap) |
| `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 114,688 / q4_0, reasoning on at effort low | 13.78 GiB (13.71 at 98,304, where the task ran) | 8.0 / 6.2 / 4.9 tok/s (8k / 32k / 64k) | 72 / 58 / 42 tok/s (8k / 32k / 64k) | PASS, 5 tests, 542 s | **serious** (this workstation's builder.env) | the quantiser's task-lossless file, LiveCodeBench 85.71 = BF16; needs the 14.0 cap (131,072 loads at 14.30, 114,688 at 14.01); peak 13.93 GiB; useful to about 32k |
| `gpt-oss-20b-UD-Q4_K_XL.gguf` | `unsloth/gpt-oss-20b-GGUF` | 81,920 / K q8_0 + V q4_0, `--chat-template-kwargs '{"reasoning_effort":"low"}'` | 11.7 GiB | 42.1 / 27 tok/s | 585 tok/s, 28 s | PARTIAL (green), 5 tests, 79 s | qualified alternative | fastest prefill and fastest task; improvised an import scaffold instead of the repository's idiom, which a reviewer catches; always reasons, keep the output limit generous |
| `gemma-4-E4B-it-Q4_K_M.gguf` | `lmstudio-community/gemma-4-E4B-it-GGUF` | 131,072 / f16, **`-fa off`** (its condition, see below) | 6.8 GiB at 80k, 8.1 GiB at 131k | 60 / 38 tok/s | 796 tok/s, 29 s | PASS ×2 and PARTIAL ×1 (17, 6, 6 tests; the PARTIAL one `isinstance` assertion), 74–82 s; green at 131k after one fix | **fast** | the largest window at speed on this card: cold read of 107k tokens in 273 s, exact on a planted detail at 85% depth of a 100k prompt, approximate on broad recall (asked for three other functions it blended real names into ones that do not exist), a wrong number at 120k; decode 44 → 16 tok/s from 8k to 100k; VRAM flat at 8.4 GiB (sliding window); five client-cancel rounds clean, zero resets across nine rows. Not for multi-file shell edits: on the harness's own brief it made one of three edits and reported all three done. On the reading task (T2: index every top-level definition of a 6,300-line, 100k-token file): E4B paged through 60% of it in 9 min and listed 40 names, 34 of them real definitions, 19 with the right line; the 9B paged through all of it in 15.5 min and listed 57 module-level constants instead of definitions, none right. Neither seat indexes a large file; the fast profile's value is a precise question about a passage, which the long profile cannot reach at all. |

## Measured and kept out

Qwen3-14B and Ministral-3-14B failed the coding task on this card. **Gemma 4 12B** (`gemma-4-12B-it-Q4_K_M`) does not fail:
with flash attention on it collapses on long prefill and trips the GPU watchdog, like every Gemma 4 on this card; with it off,
K q8_0 and V f16, it is stable through the cancel reproduction, passes the coding task (5 tests, PASS) and parses tool
calls, but only at 65,536 of context (13.7 GiB at 80k, over the cap), at 206 tok/s prefill, 15 tok/s decode and 12 minutes
for the task. Kept out by the context bar and by speed, not by quality.

**Flash attention is a per-family condition on this card.** The Qwen line has it on. Every Gemma 4 measured with it on
collapsed on prefill with position and reset the GPU; with it off the same weights ran clean. Without flash attention
llama.cpp cannot quantise the V cache, so a Gemma row runs V at f16, which E4B's sliding window keeps small and the 12B's
full cache does not. A new family enters with both settings tried before its numbers are believed. Speculative decoding in every form
tried, a vocabulary-matched draft model and the MTP heads of the Qwen3.5 and Qwen3.8 families, made decode slower on
this card under Vulkan (the Qwen3.8 MTP head 3.7 times slower despite high acceptance), so no profile uses it. Those
weights are kept for the next Intel card, where the first thing to re-measure is exactly that.

## How a model earns the fast profile

T1 measures a bounded edit at low context and says nothing about reading. A model that is to hold a large file is
qualified twice more, with the harness's own tools: `harness/ctx_sweep.sh` for prefill and decode against position with
VRAM sampled, and `harness/depth_probe.sh` for whether it answers correctly from deep inside the prompt; and
`harness/cancel_repro.sh`, the client-cancel reproduction from the sibling framework's records, for whether a cancelled
request can take the card down. `briefs/T2-read-a-large-file.md` is the reading task, graded against `grep`.

## How a model enters

The procedure is in [`docs/OPERATING.md`](../docs/OPERATING.md#qualifying-a-new-model). In one line: put the GGUF in
`A770B_MODELS`, run `harness/run_one.sh`, have a reviewer grade the capture, add the row here with its numbers, then give
it a profile in `config/profiles.json` with its measured card, render the skill's table from it, and move the version.
