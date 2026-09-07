# Operating the seat

Everything the README leaves out: how a run works, the two profiles, the knobs, where the models go and what the
files are. The README covers what this is, how it installs, why it exists and its security state.

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md>            # fast profile
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --serious  # serious profile
bash skills/local-build/scripts/local-build.sh serve fast|serious · status · stop
```
The run refuses anything but a git worktree root that is not the live checkout, takes a run lock, starts or switches
the server, sets the seat's `AGENTS.md` aside for the call and restores it under a trap, runs opencode inside the
sandbox with `< /dev/null`, captures the diff, the model's own pytest line and the timings without executing anything
the model wrote, and resets the seat. Judge by the capture in `~/local-ai/results/`, never by exit code.

## Profiles (measured on the A770, llama.cpp b10805 Vulkan)

| profile | model | window · KV | VRAM | decode / prefill | small task |
|---|---|---|---|---|---|
| `fast` (default) | Qwen3.5-9B Q4_K_M | 81,920 · q8_0 | 6.9 GiB | 45 tok/s / 464 tok/s | 2–3 min |
| `--serious` | Qwen3.8-27B GSQ-RCO IQ2_XS | 158,000 · q4_0 | 11.5 GiB | 8.1 tok/s / 70 tok/s | 10–25 min |

## Configure

Everything comes from one env file, loaded by `harness/env.sh` with these precedences: defaults → `<project>/config/builder.env`
→ `~/.config/a770-builder/builder.env` → the calling environment. Copy `config/builder.env.example` to one of those and
edit only what differs. The defaults are the workstation the seat was qualified on (Arc A770 as `Vulkan0`, models in
`~/LLM/tested`, data in `~/local-ai`). The installed skill copy finds the project via `A770B_PROJECT`.

## Models — where to put them, how the skill reaches them

1. Download GGUFs into `A770B_MODELS` (default `~/LLM/tested`); keep a README there saying why each earned its place.
2. Name the two profiles' files: `A770B_FAST_MODEL`, `A770B_SERIOUS_MODEL` (bare name = looked up in `A770B_MODELS`;
   absolute paths work). Set the context and KV type per profile (`A770B_*_CTX`, `A770B_*_KV`); reasoning models take
   `A770B_*_REASONING=on` plus their template kwargs in `A770B_*_EXTRA`.
3. The llama-server process on the host reads the weights and answers on `A770B_HOST:A770B_PORT` under the alias
   `local-builder`. The model's own process runs inside the sandbox and never sees `A770B_MODELS` — it only talks to the
   server over loopback. So the weights can live anywhere the server can read, including a read-only share.
4. Before trusting a new model, qualify it: `harness/run_one.sh <label> <gguf> <ctx>` runs the probes, the coding task and
   the capture; a cheap reviewer grades the capture; it lands in `A770B_MODELS` only on green.

## What is hardcoded now

Nothing that matters. Two conventions remain: the opencode alias `local-builder` (the provider files depend on it) and the
sandbox's use of `bubblewrap`, `uv` and the opencode binary from `A770B_OPENCODE_BIN`.

## What is in here

| path | role |
|---|---|
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (profiles `fast` / `--serious`), `CONSTITUTION_SNIPPET.md` (agents add it to their own constitution) |
| `render_readme.sh` | regenerates `README.html` from this file — run after every README edit; the Markdown is the source |
| `harness/serve_a770_llamacpp.sh` | the only way a server starts: budget gate, VRAM cap (13 GiB after load on a display card), `-ub 512`, model marker |
| `harness/build_local.sh` | dispatch a brief through opencode in a git worktree (never the live main checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: only the seat read-write, no credentials, no main checkout, no harness source |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation |
| `harness/guard.sh` | canonical worktree guard, verified pids, the run lock, the built-in budget gate |
| `SANDBOX-PLAN.md` · `SECURITY.md` | the sequenced security work from the adversarial reviews with its done log; what holds now and what is still open |
| `harness/capture_task.sh` | diff + new files + pytest line + server-side TTFT/TPOT distribution, then worktree reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the profile's window and a default-deny bash allow-list |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `LICENSE` | MIT |
| `briefs/` | the matrix task (`T1-…`), the cheap-reviewer prompt, the smoke brief |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a plain clone of the target repository with no link to its live checkout), results, logs and the uv cache in
`~/local-ai/`. Nothing here is a git worktree of another project.
