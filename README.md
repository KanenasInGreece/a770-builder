# A770_Builder

This repository is about one card, the Intel Arc A770 16 GB, used as a local code builder. It holds the two models that
earned the role by passing a real test-writing task on a live codebase, with the llama.cpp flags that keep the card stable
and the context each one gives you; and it holds `local-build`, an Agent Skills-standard skill any CLI agent can install,
which serves the chosen model, runs the brief inside a network-isolated sandbox that can only see the seat, and hands back
a capture for review instead of trusting the run.

Built and measured on 2026-09-06 and 07 on a card that also drives the desktop; reviewed adversarially twice and contained
the same day. The measured report (matrix, serving lines, method) lives beside the weights: `~/LLM/tested/README.html`.

## Prerequisites

`llama.cpp` built with the Vulkan backend (`llama-server`), `bubblewrap` (`bwrap`), `socat`, `uv`, `python3`, `git`, `curl`,
`nvtop` (the VRAM readings the cap depends on — the harness refuses to start without them unless you accept the risk with
`A770B_ALLOW_NO_NVTOP=1`), `flock` and `ss` (util-linux / iproute2), and opencode (the executor inside the sandbox). Node is
only needed for the skills CLI route.

## First run (six steps)

```bash
git clone git@github.com:KanenasInGreece/a770-builder.git ~/local-ai/A770_Builder   # 1. the project (A770B_PROJECT)
cp ~/local-ai/A770_Builder/config/builder.env.example ~/.config/a770-builder/builder.env
#    2. edit it: A770B_REFUSE = your live checkouts (required), A770B_MODELS, A770B_DEVICE/GPU_MATCH if not an A770
git clone <your target repo> ~/local-ai/seat                        # 3. the seat: a plain clone, never a linked worktree
#    4. put the GGUFs named in the profiles into A770B_MODELS (see Models)
bash ~/local-ai/A770_Builder/harness/warm_cache.sh                  # 5. pre-fill the read-only uv cache (the sandbox has no network)
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # 6. install the skill into your agents (or: bash sync_local_build.sh)
bash ~/.claude/skills/local-build/scripts/local-build.sh run ~/local-ai/A770_Builder/briefs/T0-smoke.md
```

## Install — how an agent acquires this skill

The skill follows the [Agent Skills](https://agentskills.io) open standard: a folder `skills/local-build/` holding
`SKILL.md` (name + description in the front matter, instructions in the body), `scripts/` and a constitution snippet.
Tested here on 2026-09-07, all three routes:

**1. The open skills CLI** — detects the agents you have installed and copies the skill into each of them.
```bash
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # from GitHub, user-level, real copies
npx skills add /path/to/A770_Builder --skill local-build -g --copy     # from a local clone (tested)
npx skills add /path/to/A770_Builder --list                            # browse first (tested: finds local-build)
```
`-g` installs under your home (`~/.claude/skills`, `~/.codex/skills`, …); without it the CLI installs into the current
project's `.claude/skills`. `--copy` matters: the default is a symlink, and a skill that carries scripts should be a copy.

**2. The project's own installer** — the same thing without Node, for the five agents on this workstation:
```bash
bash sync_local_build.sh          # real copies into ~/.claude, ~/.grok, ~/.codex, ~/.gemini, ~/.config/opencode skills dirs
```

**3. By hand** — copy `skills/local-build/` into your agent's skill directory.

Whichever route: the skill is a front door; it needs this project (`harness/`, `config/`) reachable at `A770B_PROJECT`
(default `~/local-ai/A770_Builder`, see *Configure*), a llama.cpp build with the Vulkan backend, `bubblewrap`, `uv` and
opencode on the host, and the model files in `A770B_MODELS`.

### Per agent, as tested on this workstation

| agent | skill directory | how it shows up | note |
|---|---|---|---|
| Claude Code | `~/.claude/skills/local-build` | `claude skills` lists `local-build`; invoke as `/local-build` or run the script | the whole matrix was driven this way |
| opencode | `~/.config/opencode/skills/local-build` | loaded only if allowed by name: add `"local-build": "allow"` under `permission.skill` (its policy is deny-all) | opencode is also the executor inside the sandbox |
| Gemini CLI | `~/.gemini/skills/local-build` | `gemini skills list` shows the SKILL.md under *Discovered Agent Skills* | |
| Codex CLI | `~/.codex/skills/local-build` | present in its skills directory | Codex cannot use the local model as its own brain (Responses API), it dispatches the seat |
| grok CLI | `~/.grok/skills/local-build` | present in its skills directory | grok is x.ai-bound; it dispatches the seat through the script |

Every agent adds `CONSTITUTION_SNIPPET.md` to its own constitution file itself (`SKILL.md` says how); nothing writes
into another agent's home. Licence: MIT (`LICENSE`).

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md>            # fast profile
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --serious  # serious profile
bash skills/local-build/scripts/local-build.sh serve fast|serious · status · stop
bash sync_local_build.sh                                                               # after editing the skill
```
The run refuses anything but a git worktree root that is not the live checkout, takes a run lock, starts or switches
the server, sets the seat's `AGENTS.md` aside for the call and restores it under a trap, runs opencode inside the
sandbox with `< /dev/null`, captures the diff, the model's own pytest line and the timings without executing anything
the model wrote, and resets the seat. Judge by the capture in `~/local-ai/results/`, never by exit code.

## What is in here

| path | role |
|---|---|
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (profiles `fast` / `--serious`), `CONSTITUTION_SNIPPET.md` (agents add it to their own constitution) |
| `render_readme.sh` | regenerates `README.html` from this file — run after every README edit; the Markdown is the source |
| `sync_local_build.sh` | installs the skill as real copies into every agent's skill directory (Claude Code, grok, Codex, Gemini, opencode) |
| `harness/serve_a770_llamacpp.sh` | the only way a server starts: budget gate, VRAM cap (13 GiB after load on a display card), `-ub 512`, model marker |
| `harness/build_local.sh` | dispatch a brief through opencode in a git worktree (never the live main checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: only the seat read-write, no credentials, no main checkout, no harness source |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation |
| `harness/guard.sh` | canonical worktree guard, verified pids, the run lock, the built-in budget gate |
| `SANDBOX-PLAN.md` | the sequenced security work from the adversarial review, with a done log |
| `harness/capture_task.sh` | diff + new files + pytest line + server-side TTFT/TPOT distribution, then worktree reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the profile's window and a default-deny bash allow-list |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `LICENSE` | MIT |
| `briefs/` | the matrix task (`T1-…`), the cheap-reviewer prompt, the smoke brief |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a plain clone of the target repository with no link to its live checkout), results, logs and the uv cache in
`~/local-ai/`. Nothing here is a git worktree of another project.

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

## Security state

Two adversarial reviews by a reviewer-class model, read-only: one on the first version, a public-readiness pass on this one. What
holds now, all mutation-checked: canonical refusal of protected checkouts (`A770B_REFUSE`, required), of linked worktrees,
symlinks, subdirectories and agent homes; a run lock and pids verified before any kill; the model's process inside bubblewrap
with a private home, no credentials, no other tree, **no network except a loopback bridge to the model server** (socat over a
unix socket; the LAN, the internet and every other host service are unreachable), `.git/config`, `.git/hooks` and `.git/info`
mounted read-only so the model cannot plant config-driven code; every host-side git call neutralises config-driven code paths
(`safe_git`); the uv cache read-only and pre-warmed; the only opencode config inside is a rendered per-run profile with a
default-deny bash allow-list and no MCP, web or skills; capture executes nothing the model wrote; VRAM readings that refuse to
fail open. Still open (`SANDBOX-PLAN.md`): a sandboxed `verify` command for reviewers, a checksum on the sync, an API key on the
model server, a third adversarial pass from another model family.
