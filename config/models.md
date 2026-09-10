# Qualified models

This file is the ledger of every model that has been put through the seat's qualification on this card, and how it
measured. It matters because the profiles in the mode's registry are configuration, not a promise: a model earns a
profile here first, with numbers, and a model that is not in this table has not been measured on this card, whatever
its reputation elsewhere. Every row below carries an instrument AND a card (`measured_on`, the registry's own
field) — comparability needs both to match, not the instrument alone, and every row and every free-card measurement
below states both. Every row measured before this release carries the same instrument: the seat being a standalone
clone of the public Shared Memory repository at commit `3c8e2bb` (`seat: Shared_Memory@3c8e2bb`), measured on
`Arc A770 16 GB, llama.cpp b10805 Vulkan`, and stays comparable only with the other rows sharing both. From this
release a new candidate is qualified on the kit inside this repository instead (`instrument: SUITE-1@<the project's
own version>`), a different instrument again — no registry row has yet been measured on it, and the first one will
be; the free-card table below (*On the same card with nothing else on it*) already carries two measurements taken on
it, each plainly marked as such and neither a registry row: measurements, not rulings. The seat runs in one of two
card modes, and each reads its own registry. Display-safe reads
`config/profiles.json`: **long** = Qwen3.5-9B Q4_K_M, the default for every ordinary change; **serious** = Qwen3.8-27B
GSQ-RCO IQ3_XXS, for a deliverable larger than its brief; **fast** = Gemma 4 E4B Q4_K_M with flash attention off, for
the read the long window cannot hold. Pure-inference reads `config/profiles.inference.json`, measured on the same
card with nothing else on it: **long** = the same 9B at its whole native window; **serious** = the 27B's IQ3_S file
at a larger window. Both registries carry each row's `category` (`dense` or `moe`) and weight class, so an equivalent file
is ruled out rather than kept as a second row of the same class. The profile column below says which row holds which.
New releases of a model family are new models; they enter the same way.

## The bar

A model qualifies when it clears four gates on the same harness — on the sibling-repository instrument, in one run
of `harness/run_one.sh`; on the kit instrument, the same four gates in one run of `harness/ladder.sh`
(`AGENTS.md`, *The ladder*) — and every row below states which instrument and which card (`measured_on`) it
cleared them on, since a gate cleared on one is not evidence about the other:

1. **It serves under the card's rules**: llama.cpp with the Vulkan backend, `-fa on`, `--no-mmap`, `-ngl 99`,
   `--parallel 1`, `-b 2048 -ub 512`, quantised KV, at least 80k of context and at most the mode's cap after load on a
   16 GB card (13 GiB on one that also draws the desktop, 15.3 on one that draws nothing), with zero GPU engine resets.
2. **It answers**: correct greedy answers on a three-prompt sanity gate and a coherent one-sentence summary after a
   prefill of about 17k tokens of source.
3. **It is fast enough**: decode at or above 5 tok/s at long context, and tool calls that llama-server parses. The
   standard number behind this gate is `harness/bench_speed.sh <profile>` — llama-bench at the row's own served
   flags (`-fa`, `-ctk`/`-ctv`, `-ub`, a MoE row's `--n-cpu-moe`), at depths 0, 8k, 32k and the far end, one command a
   reader can reproduce and compare row to row, recorded as `speed.bench`; the standard suite's own as-delivered
   speed, `harness/suite_report.py` on a `run_suite.sh` results file, sits beside it as `speed.delivered`, labelled
   and never alone, since it is the row's speed under a real coding run rather than the flag set on its own. The far
   end itself is a target `harness/ctx_sweep.sh` sizes by four bytes a token, not a promise: this kit's corpus is
   dense real source rather than prose, so that rule understates — a sweep asking for 8,000 tokens sent 12,718 — and
   a row too slow to answer inside the run's own time ceiling leaves no far-end reading to record at all (the 27B's
   own 100k attempt ran past a 3,600 s ceiling with no answer), so the far end recorded for a row is whatever token
   count its reply actually measured, not the number asked for.
4. **It does the coding task**: on the sibling-repository instrument, the qualification brief on a real repository
   (the seat is a standalone clone of the public `https://github.com/KanenasInGreece/Shared_Memory` at commit
   `3c8e2bb`), run through opencode in the seat; it wrote the test file, ran the given test command, and a cheap
   reviewer reading the capture graded the run green. On the kit instrument, the standard suite (`kit/SUITE.md`)
   run through `harness/run_suite.sh`, graded on the stages that count toward the tally — a reference exercise, and
   the design note, never do.

A red run or a failing grade keeps a model out; a style deviation with green tests is recorded as PARTIAL and admitted
with its caveat. The task column below often names how many tests a model wrote for its own qualification brief
(`11 tests`, `5 tests`, and so on): that count is the model's own choice, not a quality score — a model that writes
fewer, sharper tests is not worse than one that pads the count, and the numbers are not comparable model to model.
What compares row to row is PASS, PARTIAL or FAIL, the wall time, and — for a row measured on the kit — the standard
suite's own stage-by-stage pass tally (`kit/PROFILE.md` §2, `suite`), never a raw test count.

## Qualified on the Intel Arc A770 16 GB (llama.cpp b10805, Vulkan, 2026-09-06/07)

Every row below: instrument `seat: Shared_Memory@3c8e2bb`, measured on `Arc A770 16 GB, llama.cpp b10805 Vulkan`.

| model file | source | ctx / KV | VRAM after load | decode short / after 17k | prefill (17k) | task | profile | notes |
|---|---|---|---|---|---|---|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 81,920 / q8_0 (131,072 / q4_0 also passes) | 6.9 GiB | 45.2 / 33.0 tok/s | 464 tok/s, 37 s | PASS, 11 tests first try, 121 s | **long** (the window before the registry) | followed the repository's test idiom unprompted; the only self-sufficient builder of the matrix; 6 GiB of headroom. Sweep 2026-09-08 (prefill and decode against position, VRAM sampled): 8k 568 / 36.8 tok/s, 32k 274 / 23.1, 64k 150 / 16.0, 71k 125 / 14.8; VRAM flat at 7.2 GiB; zero resets — the whole 81,920 window is safe. As a builder of this repository's own skill (2026-09-08): four bounded units from exact briefs in one run each, applied unchanged; a behavioural unit of nine rules not in two runs |
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 262,144 / q8_0 (native) | 10.35 GiB | 36.8 / 11.6 tok/s | 571 → 147 tok/s | — | **long** (default) | the native window, served in full; sweep run with context served at 131,072: 8k 571 / 36.8 tok/s prefill/decode, 100k 147 / 11.6; 100k prompt TTFT 626 s; VRAM peak +0.28 GiB over the 10.35 GiB load; zero resets; useful to 65,536 tokens by the four-tokens-a-second rule (this row's registry `useful_ctx`) |
| `Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 158,000 / q4_0, reasoning on at effort low | 11.5 GiB | 8.1 / 6.7 tok/s | 70 tok/s, 247 s | PASS, 5 tests, 617 s | **serious** (retired from the defaults) | best-written file of the matrix; at the speed floor, 10–25 min per small task. Sweep 2026-09-08: 8k 73 / 7.4 tok/s, 32k 58 / 5.9, 64k 40 / 4.7; VRAM 11.9 GiB; zero resets — under the 5 tok/s decode floor by 64k, so the profile is served with 158,000 tokens and useful to about 32,000; LiveCodeBench 76.57, under the 95 % line, retired from the defaults |
| `Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 131,072 / q4_0, reasoning on at effort low | 12.3 GiB | 8.1 / 6.7 tok/s | 65 tok/s, 265 s | PASS, 5 tests, 1,492 s | **serious** | same speed as IQ2_XS for 0.75 GiB more VRAM and 30k less context; LiveCodeBench v6 84.57 vs BF16 85.71, the new public default (fits the public 13.0 cap) |
| `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 114,688 / q4_0, reasoning on at effort low | 13.78 GiB | 8.0 / 6.2 / 4.9 tok/s (8k / 32k / 64k) | 72 / 58 / 42 tok/s (8k / 32k / 64k) | PASS, 5 tests, 542 s | **serious** (this workstation's `builder.display.env`) | the quantiser's task-lossless file, LiveCodeBench 85.71 = BF16; needs the 14.1 cap, peaks at 14.21 GiB during a 32k prompt; useful to about 32k |
| `gpt-oss-20b-UD-Q4_K_XL.gguf` | `unsloth/gpt-oss-20b-GGUF` | 81,920 / K q8_0 + V q4_0, `--chat-template-kwargs '{"reasoning_effort":"low"}'` | 11.7 GiB | 42.1 / 27 tok/s | 585 tok/s, 28 s | PARTIAL (green), 5 tests, 79 s | qualified alternative | fastest prefill and fastest task; improvised an import scaffold instead of the repository's idiom, which a reviewer catches; always reasons, keep the output limit generous |
| `gemma-4-E4B-it-Q4_K_M.gguf` | `lmstudio-community/gemma-4-E4B-it-GGUF` | 131,072 / f16, **`-fa off`** (its condition, see below) | 6.8 GiB at 80k, 8.1 GiB at 131k | 60 / 38 tok/s | 796 tok/s, 29 s | PASS ×2 and PARTIAL ×1 (17, 6, 6 tests; the PARTIAL one `isinstance` assertion), 74–82 s; green at 131k after one fix | **fast** | the largest window at speed on this card: cold read of 107k tokens in 273 s, exact on a planted detail at 85% depth of a 100k prompt, approximate on broad recall (asked for three other functions it blended real names into ones that do not exist), a wrong number at 120k; decode 44 → 16 tok/s from 8k to 100k; VRAM flat at 8.4 GiB (sliding window); five client-cancel rounds clean, zero resets across nine rows. Not for multi-file shell edits: on the harness's own brief it made one of three edits and reported all three done. On the reading task (T2: index every top-level definition of a 6,300-line, 100k-token file): E4B paged through 60% of it in 9 min and listed 40 names, 34 of them real definitions, 19 with the right line; the 9B paged through all of it in 15.5 min and listed 57 module-level constants instead of definitions, none right. Neither seat indexes a large file; the fast profile's value is a precise question about a passage, which the long profile cannot reach at all. |

The rows below are the campaign's, measured with nothing else on the card (`config/profiles.inference.json`). Every
row below: instrument `seat: Shared_Memory@3c8e2bb`, measured on `Arc A770 16 GB, llama.cpp b10805 Vulkan`.

| model file | source | ctx / KV | VRAM after load | decode 8k / far end | prefill 8k / far end | task | profile | notes |
|---|---|---|---|---|---|---|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | `lmstudio-community/Qwen3.5-9B-GGUF` | 262,144 / q8_0 (native) | 9.49 GiB | 43.7 / 11.6 tok/s (far end 100k) | 439 / 147 tok/s | PASS, 6 tests, 122 s, under its instruct line (0.7 / 0.8 / 20 / 0 / presence 1.5) | **long** (inference default) | useful to its whole native window; 100k prompt TTFT 626 s; the same file as the display registry's `long`, served in full on a card with nothing else on it |
| `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | `unsloth/Qwen3.6-35B-A3B-GGUF` | 131,072 / q8_0, `--n-cpu-moe 18` (18 expert layers in host RAM, 22 on the card) | 14.1 GiB, flat | 21.8 / 10.3 tok/s (far end 100k) | 207 / 80 tok/s | PASS, 10 tests, 203 s, under its instruct line | measured, not shipped | useful to its whole 131,072; 100k prompt TTFT 1,150 s; three times the dense 27B's decode; 18 of its expert layers sit in host memory, 22 on the card, which costs about 18 GB of system memory beyond the card and a CPU that is not otherwise busy; the harness has no gate for that memory, so it is not offered as a profile (*Measured and kept out*) |
| `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 196,608 / q4_0, reasoning on at effort low | 14.62 GiB, 14.82 peak at a 32k prompt | 7.9 / 4.9 tok/s (8k / 64k; 100k not run) | 71 / 42 tok/s (8k / 64k) | PASS, 10 tests, 546 s, under its card line (temperature 1.0, top_p 0.95); 5 tests, 664 s at the client's temperature 0 | **serious** (inference) | useful to about 98k by the four-tokens-a-second rule; the quantiser's task-lossless file, 100.2% of BF16 on its own card (AIME25 100, GPQA-Diamond 89.39 against 89.90, LiveCodeBench v6 85.71 = BF16); the largest IQ3_S window under the 15.3 cap |

## On the same card with nothing else on it

One row per file and window the campaign measured on the free card, adopted or not; VRAM is after load unless a peak
is named. The display registry's own after-load readings above were taken with the desktop's 0.65 to 0.9 GiB on the
card, which this table has none of. Every row below down to and including the `-ub 1024`/`-ub 2048` rows carries
instrument `seat: Shared_Memory@3c8e2bb`, measured on `Arc A770 16 GB, llama.cpp b10805 Vulkan`; the last two rows
are the exceptions, on a different instrument, named beside them.

| file, window | VRAM after load (peak) | decode / prefill at 8k | profile or verdict |
|---|---|---|---|
| Qwen3.5-9B Q4_K_M, 262,144 | 9.49 GiB | 43.7 / 439 tok/s | **long** (inference) |
| Qwen3.5-9B Q8_0, 262,144 | 12.64 GiB | 30.6 / — tok/s | measured, not adopted: 28% less decode at 8k than Q4_K_M for nothing measurable on T1 (10.3 against 11.6 at 100k); Q4_K_M keeps the row unless the suite separates them |
| gemma-4-E4B Q4_K_M, 131,072, `-fa off` | 7.33 GiB | 60 / 796 tok/s | **fast** (display registry; the same file, read on a card with nothing else on it) |
| Qwen3.6-35B-A3B UD-Q4_K_XL, 131,072, 18 expert layers in host RAM | 14.1 GiB, flat | 21.8 / 207 tok/s | measured, not adopted: the fastest builder measured on this card, but its expert layers cost about 18 GB of system memory beyond the card and the harness has no gate for that (*Measured and kept out*) |
| Qwen3.8-27B GSQ-RCO IQ3_S, 114,688 | 13.14 GiB | — | **serious** (this workstation's `builder.display.env`, under its 14.1 cap; 13.78 GiB on the display card, peaking at 14.21 during a 32k prompt) |
| Qwen3.8-27B GSQ-RCO IQ3_S, 131,072 | 13.44 GiB (14.30 on the display card) | — | measured, not adopted: superseded by the 196,608 window |
| Qwen3.8-27B GSQ-RCO IQ3_S, 163,840 | 14.03 GiB | — | measured, not adopted: superseded by the 196,608 window |
| Qwen3.8-27B GSQ-RCO IQ3_S, 196,608 | 14.62 GiB, 14.82 peak at a 32k prompt | 7.9 / 71 tok/s | **serious** (inference) |
| Qwen3.8-27B GSQ-RCO IQ3_S, 229,376 | 15.22 GiB, 15.36 peak during an 8k prompt | — | measured, not adopted: too little reserve under the 15.3 cap |
| Qwen3.8-27B, KV q4_1 (`serious`'s window) | — | 8.1 / — tok/s | measured, not adopted: compute-bound on this card whatever the KV type (q4_0 7.9, q8_0 7.8); q4_0 stays |
| Qwen3.8-27B, KV q8_0 (`serious`'s window) | — | 7.8 / — tok/s | measured, not adopted: see above |
| Qwen3.8-27B, `-ub 1024` (`serious`'s window) | 14.94 GiB | — | measured, not adopted: loads under the 15.3 cap; its row is owed to a later cycle |
| Qwen3.8-27B, `-ub 2048` (`serious`'s window) | 15.68 GiB | — | refused: exceeds the 15.3 cap |
| gemma-4-12B-it Q6_K, 81,920, K q8_0, V f16, `-fa off` | 15.19 GiB (15.28 peak) | 14.3 / 133 tok/s (8.7 / 138 at 73,728, 503 s to first token) | measured, not adopted: useful to its whole 81,920 by the four-tokens-a-second rule and clears the builder bar (five tests, 528 s, 17.3 tok/s decode and 171 prefill at a 17k prompt), but dominated on every axis by the 35B-A3B file measured on this card, itself not shipped (more window, more speed, less VRAM, a faster task), and by the 9B as a builder |
| Qwen3.6-35B-A3B UD-Q4_K_XL, 131,072, 16 expert layers in host RAM (24 on the card) | 15.01 GiB | 23.8 / 158 tok/s (prefill at 17k) | measured, not adopted: does not fit under 15.3 GiB with margin; 18 expert layers in host RAM is the row |
| Qwen3.5-9B Q4_K_M, thinking mode (the card's precise-coding line: 0.6/0.95/20/0, reasoning on, output 32,768) | — | — | measured, kept out: the probes pass on the first two prompts with about 480 characters of reasoning each; on the third (a one-line hello world) the model reasons for 13,588 characters, about 4,000 tokens and 96 s, and exhausts a 4,096-token budget with no answer — without an effort control the 9B's thinking mode over-thinks small prompts; the instruct line keeps the row |
| Qwen3.6-35B-A3B UD-Q4_K_XL, 131,072, 18 expert layers in RAM, thinking mode under the card's precise-coding line (temperature 0.6, top_p 0.95, top_k 20, min_p 0, presence 0) | 14.1 GiB | 22.3 / 154 tok/s (prefill at 17k; 51.7 ms per token after it) | measured, not adopted: probes 3 of 3 with short reasoning; T1 nine tests green in 254 s, against the instruct line's ten in 203 s — thinking costs a quarter of the wall for nothing on this task; the instruct line keeps the row |
| Qwen3.8-27B GSQ-RCO IQ3_S, 196,608, q4_0 KV, `-ub 1024` | 14.94 GiB (15.11 peak during an 8k prompt) | 7.9 / 74 tok/s | measured, not adopted: four percent more prefill than `-ub 512` (71 tok/s, 7.9 decode, 14.77 GiB peak) for 0.34 GiB more peak; ubatch stays 512 in both modes |
| Gemma 4 26B-A4B UD-Q4_K_XL, 131,072, 18 expert layers in host memory, K q8_0 / V f16, `-fa off` | 11.7 GiB (15.00 GiB peak, no reset) | 10.5 / 175 tok/s (13,028-token prompt, the project's own sweep) | measured tonight, not a registry row — **the first model measured on the kit** (`instrument: SUITE-1@0.1.5`, not `seat: Shared_Memory@3c8e2bb`; `measured_on: Arc A770 16 GB, llama.cpp b10805 Vulkan`, the same card as every other row in this table): 7.7 GB of host memory for the 18 expert layers beyond the 11.7 GiB VRAM; llama-bench at its own served flags: 13.3/186.5 tok/s decode/prefill at depth 0, 11.5/168.6 at 8,192, 9.4/109.2 at 32,768; the project's own sweep: 10.5/175 tok/s decode/prefill at a 13,028-token prompt, 5.5/82 at a 101,532-token prompt, VRAM peaking at 15.00 GiB, zero resets; the depth probe answered all three questions correctly at 101,532 tokens; the kit's suite: 3 of 3 counted stages passed (front end, backend, C++ optimisation) — the design note's own hidden check failed, scored outside the pass count, as it does for every row; of the three reference exercises, none of which ever counts toward the tally: the Python one passed, the C++ one failed, and the JavaScript one timed out after an hour. Decode still held at 5.5 tok/s at 101,532 tokens, comfortably above the four-tokens-a-second floor — so this row's window is bounded by memory near 110k (VRAM already at 15.00 GiB against the free card's 15.3 cap) rather than by speed. This is a measurement, not a ruling: it carries no registry row and is not comparable with the rows above it, which share a different instrument |
| Qwen3.5-9B Q4_K_M, 262,144, the ladder run of 2026-09-09 | 9.49 GiB | 36 / 414 tok/s | measured, not a registry row: **the second model measured on the kit** (`instrument: SUITE-1@0.1.5`, not `seat: Shared_Memory@3c8e2bb`; `measured_on: Arc A770 16 GB, llama.cpp b10805 Vulkan`, the same card as every other row in this table). It loaded in 3.3 s at 9.49 GiB and stayed there, with no kernel reset; prefill and decode by depth: 576 / 46 tok/s at the shallow end, 414 / 36 at 8k, 219 / 23 at 32k, 89 / 12 at 100k; the far end, 99,847 tokens, answered after 581 s of prefill at 159 tok/s and decoded at 11.5; the graded depth probe at 100,000 tokens passed all three questions. The kit's suite ran as a **partial** and reads as one: the run was terminated before it finished, 4 of the suite's 7 stages ran, 1 of the 3 counted stages passed, mean wall 86 s. That is not a suite score, and nothing may present it as one. This is a measurement, not a ruling: it carries no registry row, none of these numbers belong in one, and it sits beside the other row taken on the kit instrument, never beside the rows above that carry the seat instrument |

## Measured and kept out

Qwen3-14B and Ministral-3-14B failed the coding task on this card. **Gemma 4 12B** (`gemma-4-12B-it-Q4_K_M`) does not fail:
with flash attention on it collapses on long prefill and trips the GPU watchdog, like every Gemma 4 on this card; with it off,
K q8_0 and V f16, it is stable through the cancel reproduction, passes the coding task (5 tests, PASS) and parses tool
calls. On a card that also draws the desktop it only fit at 65,536 of context (13.7 GiB at 80k, over the cap), at 206 tok/s
prefill, 15 tok/s decode and 12 minutes for the task. On a card that draws nothing, at Q6_K and 81,920, it clears the
builder bar (17.3 tok/s decode, 171 prefill at a 17k prompt, five tests in 528 s; the free-card sweep of the same row is
in the table above), so the cap no longer keeps it out — but it stays out of both registries, dominated on every axis
by the 35B-A3B file measured on this card, itself not shipped, and by the 9B as a builder.

**Qwen3.6-35B-A3B** (`Qwen3.6-35B-A3B-UD-Q4_K_XL`) is a mixture of experts measured on this card at 14.1 GiB of VRAM
with 18 of its expert layers in host memory, which costs about 18 GB of system memory beyond the card. It is not
shipped as a profile. The only host-memory check the harness makes is a floor on available memory before the load,
written for a dense model's page cache, and it cannot refuse a start whose weights will not fit in RAM. On a machine
whose swap is committed the server is killed under memory pressure, and the kill looks like a fault of the seat
rather than of the machine. Keeping the file out of the registry does not add the missing check; the harness still
has no memory gate. What a row like this needs before it is offered: its own memory minimum in the budget gate,
checked before the load; a prerequisite line naming it as the one profile with a system-memory requirement; a
paragraph on the flag that trades host memory for card memory; and a rule that such a server never starts beside a
build or another memory-heavy process.

**Flash attention is a per-family condition on this card.** The Qwen line has it on. Every Gemma 4 measured with it on
collapsed on prefill with position and reset the GPU; with it off the same weights ran clean. Without flash attention
llama.cpp cannot quantise the V cache, so a Gemma row runs V at f16, which E4B's sliding window keeps small and the 12B's
full cache does not. A new family enters with both settings tried before its numbers are believed. Speculative decoding in every form
tried, a vocabulary-matched draft model and the MTP heads of the Qwen3.5 and Qwen3.8 families, made decode slower on
this card under Vulkan (the Qwen3.8 MTP head 3.7 times slower despite high acceptance), so no profile uses it. Those
weights are kept for the next Intel card, where the first thing to re-measure is exactly that.

**The sampling line.** opencode sends its own temperature when the agent's profile block sets none — 0 by default for
this seat's model id, which never matched its own Qwen rule — and that overrides whatever the server's `--temp` flag
said; every display-registry row above was measured at that client-side greedy temperature, whatever `extra` carried,
which is why its rows' notes say so. Every row now carries an optional `sampling` object naming the model's own card's
recommended line for the role its profile fills, served in `extra` on the server and rendered into the opencode agent
block's own `temperature` and `top_p` so the client no longer overrides it silently; the source is named beside the
numbers. Every inference row and the display serious row carry a sampling line; the display long and fast rows were
measured at the client's default and carry none until re-measured. The display serious row's task was measured at the
client's temperature 0, and the same file at 196,608 under this line wrote ten tests in 546 s where temperature 0 gave
five in 664 s, so the display row carries the line too.

## How a model earns the fast profile

T1 measures a bounded edit at low context and says nothing about reading. A model that is to hold a large file is
qualified twice more, with the harness's own tools: `harness/ctx_sweep.sh` for prefill and decode against position with
VRAM sampled, and `harness/depth_probe.sh` for whether it answers correctly from deep inside the prompt; and
`harness/cancel_repro.sh`, the client-cancel reproduction from the sibling framework's records, for whether a cancelled
request can take the card down. `briefs/T2-read-a-large-file.md` is the reading task, graded against `grep`.

## How a model enters

The full guide for an agent that has a GGUF and this harness and wants a model on the list is
[`AGENTS.md`](../AGENTS.md) at the repository root. In one line: put the GGUF in `A770B_MODELS`, run
`harness/ladder.sh <profile-or-gguf> --ctx N …` against the kit inside this repository (`kit/`, the ladder's task
rung from this release on — no second repository needed; `AGENTS.md`, *The ladder*, is what each rung decides and
what still needs a human), pick up the printed registry row and fill in what it marks `__TODO__` by hand, add the
row here with its numbers and its instrument (`instrument: SUITE-1@<the project's own version>`) AND its
`measured_on`, then give it a profile in the mode's registry (`config/profiles.json` or
`config/profiles.inference.json`) with its measured card and its `suite` object, render the skill's tables from it,
and move the version. A row reproducing one of the rows above instead runs `harness/run_one.sh` against the pinned
Shared Memory seat and carries `seat: Shared_Memory@3c8e2bb`, comparable only with the rows already in this ledger,
never with a kit row.
