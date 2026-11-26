# A770_Builder

<!-- benchy-attribution:begin -->
We use [llama-benchy](https://github.com/eugr/llama-benchy) for benchmarking models, with thanks to Eugene Rakhmatulin (MIT, Copyright (c) 2026 Eugene Rakhmatulin). This project does not copy or redistribute that program. The B70 Flash-Next serve follows [qwen38-flash-next-b70-offload](https://github.com/sybil-solutions/qwen38-flash-next-b70-offload) (MIT, archived 2026-10-12), and its sparse attention follows [Strata](https://github.com/Niko1221/Strata) (MIT). This project does not copy their speeds or their image.
<!-- benchy-attribution:end -->

a770-builder qualifies a local coding model and hands an orchestrator a skill to call instead of an online seat. It serves llama.cpp, vLLM, SGLang and Strata on the cards and modes named in the registry (`config/registry/<card>.<mode>.json`). A missing rate in the catalogue stays missing.

```text
your coding agent ── the context, the judgement, the plan
      │  local-build skill (a brief in, a capture out)
      ▼
builder harness ── guard · run lock · budget gate · capture · verify
      │
      ├── bubblewrap sandbox ── opencode ── the seat (a standalone clone of the repository)
      │                          │ loopback bridge, the only network
      └── the model server ────────┘── the runtime the profile selects
```

The profile selects a container envelope or the native Strata runtime. The default container envelope is `compose/a770-vulkan.yaml`.

| backend | envelope | what it starts |
|---|---|---|
| `vulkan` | `compose/a770-vulkan.yaml` | llama.cpp, `/app/llama-server`, the full-vulkan image |
| `sycl` | `compose/a770-sycl.yaml` | llama.cpp, `/app/llama-server` after oneAPI, the full-intel image |
| `vllm` | `compose/vllm.yaml` | `vllm serve`, after oneAPI |
| `sglang` | `compose/b70-sglang.yaml` | `python3 -m sglang.launch_server` |
| `strata` | Prepared JSON config and pinned Terminal source | Strata with the Intel SYCL engine |

For a container, copy the envelope's `.env.example` to the `.env` next to it and set the image, DRM nodes and host group IDs. For the native runtime, follow the [Strata setup](docs/OPERATING.md#managed-strata). The skill starts and stops either path by profile name.

```
bash skills/local-build/scripts/local-build.sh serve <profile>
bash skills/local-build/scripts/local-build.sh stop
```

`serve` recreates the container with the profile's argv, so the registry stays the source. The yaml is the envelope: devices, group ids, mounts, the loopback port, and the entrypoint. The VRAM cap and the live-backend sidecar stay on the host. Do not start it with a bare `docker compose up`: that skips the profile argv, the budget gate, the VRAM cap, and the sidecar, and `--no-recreate` would keep the previous model. Every envelope sets `restart: "no"`, so a reboot does not bring the server back. `doctor` warns, without failing, while the selected image is still a floating tag. The speed rung is `harness/bench_http.sh` (llama-benchy, called and not packaged) against the server the load left up. It does not stop that server.

Display and inference are the two modes. Each card's VRAM cap is that host's `A770B_VRAM_CAP_GIB`. The same model may need different profiles across cards and modes. SGLang's Flash-Next measurements used an attached server. The table above describes the managed launch paths.

## How we qualify a builder

We look for the strongest configuration measured for a coding workload, card, mode and context target. We shortlist model checkpoints, then test which engine and backend support their architecture and weight format on the target card. Weight quantization affects quality and how much of the model fits on the card. Kernel precision and KV-cache quantization also affect speed, memory use and usable context. The choice can change as engines, drivers and quantized artifacts improve.

The [kit](kit/PROFILE.md) defines how to measure load, throughput, useful context and coding performance. The useful window is the served context unless the operator sets a prefill absorb limit; decode speed is recorded and does not set that window. The harness starts or attaches to the engine, enforces the configured limits on managed runs, and captures results from the context sweep and graded coding tasks. A profile records the selected artifact, runtime settings and measurements so the local-build skill can use the tested configuration.

The default way to profile a model on llama.cpp, vLLM, or SGLang is `local-build.sh qualify start`, then `qualify result` and `qualify export`. Strata is not yet supported by qualify; the manual ladder is the way to measure it. The rungs of `harness/ladder.sh` are what that command runs for those three engines, and that script is how to debug one rung rather than the default profiling step. A result holds the required statistics, the measurement switches for that engine, and whether each switch was confirmed. The runner returns facts and the verdict `needs-review`. Adding a profile stays a deliberate act.

On Arc Pro B70, Inkling 2 Mini qualified with IQ4_NL weights on llama.cpp Vulkan, with a 65,536-token configured window and a 62,949-token context sweep sample. The tested vLLM XPU build could not load the looped architecture, and the tested llama.cpp SYCL build failed in long-context prefill. Qwen3.8 Flash-Next IQ3_S qualified through Strata's Intel SYCL engine, which tiers expert weights across device and host memory. See the [model artifacts](config/models.md) and [operating guide](docs/OPERATING.md).

## The catalogue

The registry lists the exact artifacts and engines measured on each card. The catalogue is generated by `python3 harness/profiles.py render --catalogue`.

<!-- catalogue:begin -->
| card | mode | profile | model | window (useful) | VRAM at served context | decode / prefill at 8k | decode / prefill at far end | measured_on |
|---|---|---|---|---|---|---|---|---|
| a770 | display | gemma4-8b-e4b-q4km-vulkan | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB at 131,072 served | 60 / 796 tok/s at 8k | — | Arc A770 16 GB, llama.cpp b10805 Vulkan |
| a770 | display | qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB at 262,144 served | 36.8 / 571 tok/s at 8k | — | Arc A770 16 GB, llama.cpp b10805 Vulkan |
| a770 | display | qwen38-27b-iq3xxs-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB at 131,072 served | 8.1 / 72 tok/s at 8k | — | Arc A770 16 GB, llama.cpp b10805 Vulkan |
| a770 | inference | qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB at 262,144 served | 29.3 / 385.9 tok/s at 8k | 9.2 / 117.0 tok/s at 99.8k | Arc A770 16 GB, llama.cpp b10805 Vulkan |
| a770 | inference | qwen38-27b-iq3s-sycl | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 100,000 (~100k) | 14.61 GiB at 100,000 served | 9.9 / 358.4 tok/s at 8k | — | Arc A770 16 GB, llama.cpp full-intel SYCL container |
| a770 | inference | qwen38-27b-iq3s-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB at 196,608 served | 7.9 / 71 tok/s at 8k | 4.9 / 42 tok/s at 64.0k | Arc A770 16 GB, llama.cpp b10805 Vulkan |
| b70 | inference | flashnext | Qwen3.8-Flash-Next-exl3-3.05bpw | 131,072 (~100k) | 27.31 GiB at 131,072 served | 9.9 / 1418.3 tok/s at 8k | 11.0 / 891 tok/s at 99.9k | Arc Pro B70 32 GB, SGLang attached, 2026-10-21 |
| b70 | inference | flashnext-ram24 | Qwen3.8-Flash-Next-exl3-3.05bpw | 131,072 (~100k) | 31.19 GiB at 131,072 served | 10.7 / 4513.3 tok/s at 8k | 12.9 / 894 tok/s at 99.9k | Arc Pro B70 32 GB, SGLang attached, 2026-10-17 |
| b70 | inference | inkling2-mini-iq4nl-vulkan | Inkling-2-Mini-IQ4_NL.gguf | 65,536 (~62k) | 23.71 GiB at 65,536 served | 11.0 / 106.0 tok/s at 8k | 6.8 / 86.0 tok/s at 62.9k | Intel Arc Pro B70 32 GB (Battlemage G31), llama.cpp b12602 Vulkan, 2026-11-21 |
| b70 | inference | intel-qwen3-coder-30b-a3b-w4a16-vllm | Qwen3-Coder-30B-A3B-Instruct-int4-AutoRound | 101,024 (~100k) | 26.771 GiB at 101,024 served | 19.5 / 8995.5 tok/s at 8k | 20.5 / 2302 tok/s at 99.8k | Arc Pro B70, Intel LLM Scaler 1.5 CA-refreshed derivative d321efe647fb, vLLM 0.26.1.dev0+g568afb3a1.d20260907, 2026-11-18 |
| b70 | inference | qwen38-27b-exl3-4bpw-vllm | Qwen3.8-27B-exl3-4.00bpw | 100,000 (~100k) | 28.15 GiB at 100,000 served | — / 1893.1 tok/s at 8k | 27.6 / 1320.6 tok/s at 98.7k | Arc Pro B70 32 GB, vLLM 0.26.1.dev0+g568afb3a1 XPU, exl3xpu 15ded2f |
| b70 | inference | qwen38-27b-q6k-oq8-sycl | Qwen3.8-27B-Q6_K-OQ8.gguf | 100,000 (~100k) | 24.46 GiB at 100,000 served | 19.5 / 805.0 tok/s at 8k | 9.0 / 665.0 tok/s at 98.9k | Arc Pro B70 32 GB, llama.cpp full-intel SYCL container, build 10920 |
| b70 | inference | qwen38-27b-q6k-qkv8-mtp-sycl | Qwen3.8-27B-Q6_K-QKV8-MTP.gguf | 100,000 (~100k) | 24.71 GiB at 100,000 served | 19.1 / 762.9 tok/s at 8k | 8.9 / 624.0 tok/s at 98.9k | Arc Pro B70 32 GB, llama.cpp full-intel SYCL container, build 10920 |
| b70 | inference | qwen38-27b-udq6k-sycl | Qwen3.8-27B-UD-Q6_K.gguf | 100,000 (~100k) | 23.35 GiB at 100,000 served | 19.1 / 806.4 tok/s at 8k | 8.9 / 674.0 tok/s at 98.9k | Arc Pro B70 32 GB, llama.cpp full-intel SYCL container, build 10920 |
| b70 | inference | qwen38-flash-next-iq3s-strata | Qwen3.8-Flash-Next-IQ3_S.strata.json | 32,768 (~31k) | 25.64 GiB at 32,768 served | 28.7 / 1337.1 tok/s at 8k | 33.5 / 1675.0 tok/s at 31.6k | Arc Pro B70 32 GB, Strata v0.2.17 with SYCL engine 0.1.63, 2026-11-21 |
<!-- catalogue:end -->

## Why we made it

Online seats go down, rate-limit, or spend credits on bounded work: a change to named files, tests from a specification, or the read of one large file. The harness measures whether a local model does that work, on the card and the engine the registry names. A fit in VRAM is not that measurement. A pre-built llama.cpp or vLLM image is a different job: [`docs/COMPARISON.md`](docs/COMPARISON.md).

## Install

Clone the repository. Copy `config/builder.env.example` to the builder env file named in [`docs/OPERATING.md`](docs/OPERATING.md), and put the profile's weights in `A770B_MODELS`. Install the skill with `npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy`, or copy `skills/local-build/` into the agent's skill directory. The skill needs this project at `A770B_PROJECT`, plus bubblewrap, uv 0.13.4 or newer, and OpenCode 1, qualified at 1.19.6. When `A770B_OPENCODE_BIN` is empty and `PATH` is not OpenCode 1, a profile run installs that release and does not grade the profile for the mismatch. An explicit directory is used as given. OpenCode 2 is not used, because `run` has no `--dir`.

```
bash <skill-dir>/scripts/local-build.sh run <brief.md> --profile <profile>
```

Day-to-day running, the knobs, and where the weights come from are in [`docs/OPERATING.md`](docs/OPERATING.md). The project is MIT-licensed (`LICENSE`).

## Security

The worker runs in bubblewrap, with the seat as its only writable tree, no credentials, and no network except the model server on loopback, which requires a key. The guard refuses every live checkout you list, and every linked worktree, symlink, and agent home. Capture executes nothing the model wrote, and `verify` re-runs a capture's tests in a fresh sandbox. The project needs that container, OpenCode 1 (qualified at 1.19.6; a profile run installs it when `PATH` is not OpenCode 1 and `A770B_OPENCODE_BIN` is empty; an explicit directory is used as given), bubblewrap, socat, uv 0.13.4 or newer, and the profile's weights. It needs no database and no account. The seat has no route to any other service on the host. The harness knows of such a service only through two optional knobs, both empty by default: ports the model server must never bind, and a health URL read once before a start. [`SECURITY.md`](SECURITY.md) lists what the harness touches and what you still set yourself.
