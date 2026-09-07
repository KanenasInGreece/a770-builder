# A770_Builder

This repository is about one card, the Intel Arc A770 16 GB, used as a local code builder. A local model is not trusted
here because it runs locally: it is treated as an untrusted coding worker, given a disposable seat, kept from everything
else on the machine, qualified on a real coding task, and judged by the artefact it hands back, never by its exit code.

It holds two things:

1. **`local-build`**, the installable skill in the [Agent Skills](https://agentskills.io) format. Any compatible coding
   agent can install it and hand the seat a brief.
2. **The A770 builder runtime** the skill calls: the harness, the sandbox, the serving lines, and the two models that
   earned their place by passing a real test-writing task on a live codebase.

Installing the skill does not install llama.cpp, the models, the sandbox or the runtime; the skill is the front door to
this project, which must be present on the machine.

```text
your coding agent
      │  local-build skill (a brief in, a capture out)
      ▼
A770 builder harness ── guard · run lock · budget gate · capture · verify
      │
      ├── bubblewrap sandbox ── opencode ── the seat (a standalone clone of your repository)
      │                          │ loopback bridge, the only network
      └── llama.cpp (Vulkan) ────┘── the qualified model on the A770
```

Built and measured on 2026-09-06 and 07 on a card that also drives the desktop; reviewed adversarially twice and contained
the same day. The measured report (matrix, serving lines, method) lives beside the weights: `~/LLM/tested/README.html`.

## Why we made it

The online seats a workstation full of coding agents depends on go down, rate-limit, or cost credits in the middle of a
build. This card was already in the machine, driving the desktop, and idle. The question was whether a 16 GB Arc could
hold a model that actually finishes a small, well-specified change in a real repository, and whether an agent could hand
it that work without giving a local model the run of the host. The first was answered by measurement: every candidate
got the same brief on a live codebase, and only the ones whose tests passed under a cheap reviewer's eye kept a place. The
second was answered by the harness and the sandbox, and by three adversarial reviews of them, all from one model family so far. A run does not end in an exit
code but in a capture, and a reviewer can re-run its tests inside a fresh sandbox with `verify` before merging anything.
What is here is the result of all of that, so the next card, model or build can be re-qualified the same way instead of
trusted.

## Install

### Prerequisites

Each is installed and configured by its own instructions, linked here; this project only needs to know where it landed.

| prerequisite | what it is for here | where to get it | where this project looks |
|---|---|---|---|
| Vulkan driver for the card | the GPU backend llama.cpp runs on | your distribution's Mesa Vulkan driver (`mesa-vulkan-drivers`) and `vulkan-tools`; `vulkaninfo --summary` must list the card | `A770B_DEVICE` = the name `llama-server --list-devices` prints |
| `llama.cpp` with the Vulkan backend | serves the model (`llama-server`) | build it from source per [llama.cpp `docs/build.md`, *Vulkan*](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan) (measured here on b10805) | `A770B_LLAMA_BIN` (default `~/llama.cpp/build/bin/llama-server`) |
| the two GGUF models | the fast and serious profiles | Hugging Face: [`lmstudio-community/Qwen3.5-9B-GGUF`](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF) and [`ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF); the download lines are in [`docs/OPERATING.md`](docs/OPERATING.md#getting-llamacpp-and-the-models); every model measured on this card is in [`config/models.md`](config/models.md), and a new one enters by the procedure in `docs/OPERATING.md` | `A770B_MODELS` (default `~/LLM/tested`), `A770B_FAST_MODEL`, `A770B_SERIOUS_MODEL` |
| [opencode](https://opencode.ai/docs) | the coding agent that runs inside the sandbox | its install script or package, per its docs | found on `PATH`, or `A770B_OPENCODE_BIN` |
| [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`) | the sandbox | your distribution's `bubblewrap` package | on `PATH` |
| [socat](http://www.dest-unreach.org/socat/) | the loopback bridge to the model server | your distribution's `socat` package | on `PATH` |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | runs the tests inside the sandbox from a pre-warmed, read-only cache | its install script | on `PATH`; cache at `A770B_UV_CACHE` |
| [nvtop](https://github.com/Syllo/nvtop) | the VRAM readings the cap depends on; the harness refuses to start without them unless `A770B_ALLOW_NO_NVTOP=1` | your distribution's `nvtop` package | `A770B_GPU_MATCH` = the card's name substring in `nvtop -s` |
| `git`, `curl`, `python3`, `flock`, `ss` | the harness itself | base packages (util-linux, iproute2) | on `PATH` |
| [Node.js](https://nodejs.org) | only for the `npx skills` install route | its installer or your package manager | not needed by the harness |

On Fedora, for example, everything but llama.cpp, opencode, uv and the models is one line:
`sudo dnf install mesa-vulkan-drivers vulkan-tools vulkan-headers vulkan-loader-devel glslc cmake gcc-c++ bubblewrap socat nvtop git curl python3 util-linux iproute`.

**Versions this was measured and reviewed with** (2026-09-07): Fedora 44, kernel 7.1.13, Mesa 26.1.8 (Vulkan 1.4.341) on
the Intel Arc A770 16 GB; llama.cpp b10805 (Vulkan); opencode 1.18.29; bubblewrap 0.12.0; socat 1.8.1.1; uv 0.12.3;
nvtop 3.3.2; git 2.55.0; Python 3.14.7; Node 24.15.0 with `skills` CLI 1.5.24. Newer versions of the tools should work;
a different llama.cpp build or model quantisation is a different measurement, and its numbers should be re-taken with
`harness/run_one.sh` before being believed. The project and the installed skill carry the same version number
(`VERSION`, `local-build.sh --version`), so an installed copy can tell when the project moved on.

### Steps

```bash
git clone git@github.com:KanenasInGreece/a770-builder.git ~/local-ai/A770_Builder   # 1. the project (A770B_PROJECT)
cp ~/local-ai/A770_Builder/config/builder.env.example ~/.config/a770-builder/builder.env
#    2. edit it: A770B_REFUSE = your live checkouts (required), A770B_MODELS, A770B_DEVICE/GPU_MATCH if not an A770
git clone <your target repo> ~/local-ai/seat                        # 3. the seat: a standalone clone, never a linked worktree
#    4. put the GGUFs named in the profiles into A770B_MODELS (docs/OPERATING.md says where to get them and llama.cpp)
bash ~/local-ai/A770_Builder/harness/warm_cache.sh                  # 5. pre-fill the read-only uv cache (the sandbox has no network)
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # 6. install the skill into your agents
bash ~/.claude/skills/local-build/scripts/local-build.sh run ~/local-ai/A770_Builder/briefs/T0-smoke.md
```

The skill follows the [Agent Skills](https://agentskills.io) open standard: a folder `skills/local-build/` holding
`SKILL.md` (name + description in the front matter, instructions in the body), `scripts/` and an optional constitution
snippet. The standard defines the skill's format, not how it is installed; install it with the open `skills` CLI or by
copying the folder into any Agent Skills-compatible agent. Tested here on 2026-09-07 with the five agents below, both routes:

**1. The open skills CLI** — detects the agents you have installed and copies the skill into each of them.
```bash
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # from GitHub, user-level, real copies
npx skills add KanenasInGreece/a770-builder --list                          # browse first (tested: finds local-build in the public repository)
npx skills add /path/to/A770_Builder --skill local-build -g --copy          # from a local clone (tested)
```
`-g` installs under your home (`~/.claude/skills`, `~/.codex/skills`, …); without it the CLI installs into the current
project's `.claude/skills`. `--copy` matters: the default is a symlink, and a skill that carries scripts should be a copy.

**2. By hand** — copy `skills/local-build/` into your agent's skill directory.

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

`CONSTITUTION_SNIPPET.md` is optional: a short standing reminder for an agent that will use the seat repeatedly, which
the agent's operator adds to its own constitution file if wanted (`SKILL.md` says how). Nothing writes into any agent's
home. Licence: MIT (`LICENSE`).

Operating detail (how a run and a `verify` work, the two profiles and their numbers, every knob, how to build llama.cpp
with Vulkan and fetch the models, what each file is) lives in [`docs/OPERATING.md`](docs/OPERATING.md).

## Security

The model's process runs inside bubblewrap with the seat as its only writable tree, no credentials, no other checkout,
and no network access except the model server, which itself requires a key. The guard refuses every live checkout you
list, every linked worktree, symlink and agent home; the capture executes nothing the model wrote, and `verify` re-runs
a capture's tests inside a fresh sandbox when you want proof. Three adversarial reviews have read the boundary, all from one model
family; a reader from another family is still owed. All of it, with what you must still do yourself and how to report a hole, is in
[`SECURITY.md`](SECURITY.md).

This project stands alone. It needs llama.cpp, opencode, bubblewrap, socat, uv and the two model files, and nothing else:
no database, no account, no memory system, and no network call of its own except the model server on loopback. It was
developed alongside the [Shared Memory](https://github.com/KanenasInGreece/Shared_Memory) framework, a sibling project
that kept the record of its decisions and reviews; none of that is needed to use it and none of it is in this repository.
If you run that framework, or any other service, on the same host: the seat never reads or writes it, the sandbox has no
route to it, and the harness knows of it only through two optional knobs, the ports the model server must never bind and
a health URL read once before a server starts, both empty by default. `SECURITY.md` lists exactly what the harness touches.

## Contributors

Credits are for people and collaborators, not a claim of joint copyright on every line (the project is MIT, see
[LICENSE](LICENSE)).

| who | role |
|---|---|
| **Xenofon S. Motsenigos** ([Oratotis](https://www.youtube.com/@Oratotis)) | Author & maintainer |
| **[Claude](https://www.anthropic.com/claude)** (Anthropic) | AI collaborator — assisted with the model qualification matrix, the serving flags, the harness and sandbox, and the adversarial reviews. **Not** a code co-author for legal/git authorship purposes. |
