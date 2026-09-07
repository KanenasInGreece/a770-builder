# Operating the seat

This document is about running the seat day to day: how a run works, the two profiles and what they were measured at,
every knob, where the models go, the card's constraints and what each file is. The README covers what this is, why it
exists, how it installs and its security state; [`SECURITY.md`](../SECURITY.md) covers the boundary.

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run <brief.md>                            # fast profile, on the default seat
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --serious  # serious profile, on a named seat
bash skills/local-build/scripts/local-build.sh verify <label>                            # the reviewer's proof
bash skills/local-build/scripts/local-build.sh reset                                     # a seat the skill refuses as dirty
bash skills/local-build/scripts/local-build.sh serve fast|serious · status · stop · --version
```

`run` refuses anything but the root of a standalone clone that is not a live checkout, refuses a seat that is not clean
(ignored files included, so nothing from an earlier run can pass as this one's; `reset` clears it), takes the run lock, starts or
switches the server, sets the seat's `AGENTS.md` aside for the call and restores it under a trap, runs opencode inside the
sandbox with `< /dev/null`, captures the diff, the model's own pytest line and the timings without executing anything the
model wrote, writes the complete change as `~/local-ai/results/<label>.patch`, and resets the seat. Judge by the capture
in `~/local-ai/results/`, never by exit code.

The reset after every run removes every uncommitted file in the seat, ignored ones included, and keeps only
`Local_Documentation/briefs/`, where the harness copies each brief. Keep no state of your own in the seat: a hand-made
`.env` or `.venv` there does not survive a run, by design.

`verify <label>` is how a reviewer turns the model's claim into proof. It re-applies the patch to the clean seat, runs
the tests inside a fresh sandbox with no model, no bridge and no key, writes `<label>.verify.md` with the command, the
output and a verdict taken from the command's exit code, and resets the seat whatever happens. By default it runs pytest
on every `tests/*.py` file the patch touches, passing the names as arguments and refusing any name that is not plain;
`--test "<command>"` runs something else instead, inside the same boundary. It refuses an empty patch, a seat that is
not clean (ignored files included), and a patch that does not apply.

The test command a brief names runs in the model's own shell tool, which cuts a command at 120 seconds unless the model
asks for longer, and inside the boundary, which has no network and none of the host's environment. So a brief names the
test files its change touches, never the whole suite. Measured on a repository of about 3,800 tests: the full suite took
144 seconds inside the sandbox, the first attempt was cut at 120, and 17 tests that depend on the host's environment
failed there while passing on the host. The full suite is the merger's run on the host after review; `verify` re-runs
what the brief named.

## Profiles

Measured on this A770 (16 GB, also driving the desktop), llama.cpp b10805 with the Vulkan backend, on the qualification
task. Numbers are properties of this card, build and quantisation, not of the models in general.

| profile | model | window · KV | VRAM | decode / prefill | small task |
|---|---|---|---|---|---|
| `fast` (default) | Qwen3.5-9B Q4_K_M | 81,920 · q8_0 | 6.9 GiB | 45 tok/s / 464 tok/s | 2–3 min |
| `--serious` | Qwen3.8-27B GSQ-RCO IQ2_XS | 158,000 · q4_0 | 11.5 GiB | 8.1 tok/s / 70 tok/s | 10–25 min |

The window is the server's. opencode's opening request costs about 5.4k tokens with the seat's `AGENTS.md` set aside
(the skill does this for the run and restores it after); with it in place the opening request was about 32k tokens, which
is why it is set aside. Every file the model reads lands in that window, and a 17k-token prefill costs the fast profile
37 s and the serious profile 4 min. Keep briefs pointed at files, not directories.

## Configure

All configurable paths and knobs are defined centrally and layered: the defaults in `harness/env.sh`, then
`<project>/config/builder.env`, then `~/.config/a770-builder/builder.env`, then the calling environment, later winning.
Copy `config/builder.env.example` to one of those two files and edit only what differs. The defaults are the
workstation the seat was qualified on (Arc A770 as `Vulkan0`, models in `~/LLM/tested`, data in `~/local-ai`). The
installed skill copy finds the project via `A770B_PROJECT`.

**The card.** `A770B_DEVICE` is the name from `llama-server --list-devices`; `A770B_GPU_MATCH` is the substring of that
card in `nvtop -s`, which the VRAM readings and the cap depend on. On a card that also draws the desktop keep
`A770B_VRAM_CAP_GIB` (13 of 16) and `A770B_UBATCH` (512): they keep the GPU's job watchdog quiet. The server refuses to
stay up past the cap after load. No speculative decoding on this card: draft models and MTP heads all made decode slower.

**The key.** The server starts with `--api-key-file A770B_API_KEY_FILE` (default `~/.config/a770-builder/api.key`). The
file is created on the first serve, one line, mode 600; the rendered profile carries the key into the sandbox, and every
harness script that talks to the server reads it from the file. Delete the file to rotate: the next `serve` creates a
new key, notices the running server no longer accepts it, and restarts the server.

**Other services on the host.** `A770B_FRAMEWORK_PORTS` lists ports the model server must never bind. `A770B_HEALTH_URL`,
when set, is read with one GET before a server starts, and the built-in gate refuses unless the answer carries
`"status": "ok"`. Both are empty by default and neither is written to. A different or stricter check belongs in an
external gate script named by `A770B_BUDGET_GATE`, which replaces the built-in gate entirely.

## Getting llama.cpp and the models

**llama.cpp with the Vulkan backend.** The seat was measured on build b10805. Build instructions live in the llama.cpp
repository, [`docs/build.md`, section *Vulkan*](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan):
install the Vulkan SDK or your distribution's `vulkan-headers`/`vulkan-loader` and `glslc`, then

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp && cd ~/llama.cpp
cmake -B build -DGGML_VULKAN=ON && cmake --build build --config Release -j
./build/bin/llama-server --list-devices        # the builder card's name goes into A770B_DEVICE
```

`A770B_LLAMA_BIN` defaults to `~/llama.cpp/build/bin/llama-server`; point it elsewhere if you built elsewhere.

**The models.** Both profiles' files are public GGUFs on Hugging Face; the harness reads them from `A770B_MODELS`.
Fetch them with the Hugging Face CLI (no account needed for these), straight into that directory:

```bash
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested            # fast
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf --local-dir ~/LLM/tested   # serious
```

llama.cpp can also fetch a model itself: `llama-server -hf lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M` downloads into
`~/.cache/llama.cpp/` and serves it. The harness starts the server with `-m <file>`, so if you go that way set
`A770B_FAST_MODEL` (or `A770B_SERIOUS_MODEL`) to the absolute path of the cached file rather than moving it.

## Models — where to put them, how the skill reaches them

1. Put the GGUFs in `A770B_MODELS` (default `~/LLM/tested`); keep a README there saying why each earned its place.
2. Name the two profiles' files: `A770B_FAST_MODEL`, `A770B_SERIOUS_MODEL` (bare name = looked up in `A770B_MODELS`;
   absolute paths work). Set the context and KV type per profile (`A770B_*_CTX`, `A770B_*_KV`); reasoning models take
   `A770B_*_REASONING=on` plus their template kwargs in `A770B_*_EXTRA`.
3. The llama-server process on the host reads the weights and answers on `A770B_HOST:A770B_PORT` under the alias
   `local-builder`. The model's own process runs inside the sandbox and never sees `A770B_MODELS`; it only talks to the
   server over the bridge. So the weights can live anywhere the server can read, including a read-only share.
4. Before trusting a new model, qualify it (next section). The ledger of every model measured on this card, with its
   numbers and its profile, is [`config/models.md`](../config/models.md).

## Qualifying a new model

Models keep being released, and a new version of a family is a new model: its window, its speed on this card and its
behaviour on a real brief are all unmeasured until the harness has run it. The seat is built so that a new model enters
by measurement, in one sitting, without touching any script:

1. **Fetch the GGUF** into `A770B_MODELS` (the download lines above are the pattern) and write your qualification brief
   for your repository if you have not yet; `briefs/T1-sanitize-entity-tests.md` is the shape that works.
2. **Run the row**: `bash harness/run_one.sh <label> <file.gguf> <ctx> [extra llama-server args]`, with `KV_K`/`KV_V`
   and `REASONING` in the environment as the model needs. It starts the server under the card's rules, runs the probes
   (load time, VRAM, the sanity gate, the long prefill, a tool call), refuses to continue if the sanity gate fails, then
   dispatches the brief through opencode in the seat and writes the capture and a JSON of the numbers to
   `A770B_DATA/results/<label>.*`.
3. **Grade the capture** with a reviewer that did not write it: `briefs/REVIEW-prompt.md` is the prompt, the capture is
   its only input. Run `local-build.sh verify <label>` for the proof. Green tests and PASS or PARTIAL qualify.
4. **Record it**: add the row to `config/models.md` with the measured numbers, the source repository and the caveats.
   A model that failed goes in the *kept out* paragraph with the reason, so nobody measures it twice.
5. **Give it a profile** if it earns one: set `A770B_FAST_MODEL` or `A770B_SERIOUS_MODEL` with its `_CTX`, `_KV`,
   `_REASONING` and `_EXTRA` in `builder.env`. The skill reads those at every call and `local-build.sh status` shows what
   is configured. If the new profile becomes the project's default, change the defaults in `harness/env.sh`, the table in
   `skills/local-build/SKILL.md` and the numbers in this file, and bump the version.

The expectations in the skill's table (window, speed, minutes per small task, what the model did with the repository's
idioms) are read straight off the ledger row; when the row changes, so do they.

## What is hardcoded now

Nothing that matters. Two conventions remain: the opencode alias `local-builder` (the profile template depends on it) and
the sandbox's use of `bubblewrap`, `socat`, `uv` and the opencode binary from `A770B_OPENCODE_BIN`.

## What is in here

| path | role |
|---|---|
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (`run`, `verify`, `reset`, `serve`, `status`, `stop`, `--version`), `CONSTITUTION_SNIPPET.md` (optional, agents add it to their own constitution) |
| `render_readme.sh` | regenerates `README.html` from `README.md`; run after every README edit, the Markdown is the source |
| `harness/serve_a770_llamacpp.sh` | the only way a server starts: budget gate, VRAM cap (13 GiB after load on a display card), `-ub 512`, the API key, model marker |
| `harness/build_local.sh` | dispatch a brief through opencode in the seat (never a live checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: only the seat read-write, no credentials, no other checkout, no harness source, no network except the model server |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation; the key and profile helpers |
| `harness/guard.sh` | canonical seat guard, verified pids, the run lock, `safe_git`, the seat reset, the built-in budget gate |
| `harness/capture_task.sh` | diff + new files + the model's pytest line + server-side TTFT/TPOT distribution, the `.patch` for `verify`, then the seat reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost (testing only, run by the operator) |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the key, the profile's window and a default-deny bash allow-list |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `SECURITY.md` · `SANDBOX-PLAN.md` | the boundary as it stands; the problem, the plan and what was done |
| `VERSION` | the project's version; the installed skill carries the same number and `local-build.sh --version` warns when they differ. Releases are tags of this number |
| `LICENSE` | MIT |
| `briefs/` | the qualification task (`T1-…`), the cheap-reviewer prompt, the smoke brief |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a standalone clone of the target repository with no link to its live checkout), results (captures, patches, verify
files), logs (server, builds, the bridge, the rendered profiles) and the uv cache in `~/local-ai/`. Nothing here is a
linked worktree of another project.
