# A770_Builder

a770-builder is a harness around a local GPU: it runs a coding model in a sandbox, measures models into profiles
with the packaged tests anyone can run on their own model, and presents the result to an LLM orchestrator as a
skill it calls instead of an online seat. The models in the registry are this project's own picks, measured so far
on that one card; the harness reaches its GPU through a handful of named settings rather than anything specific to
it, and the same ladder is how another card's own rows get taken. Developed and tested on a 16 GB Intel Arc A770.

If you run Claude, OpenCode or other agentic tools locally, you probably already have the obvious problem: your
expensive agent is doing work that a much cheaper local model could do — if you knew which model was actually good
enough, how to run it safely, and what kind of work you could trust it with.

a770-builder is a Linux-only local coding-worker harness. It takes a local GPU, qualifies models by making them do
real repository work, isolates the worker, controls its resources, and verifies the artefact it produces. It isn't
another model server or chat UI; it gives your orchestrator a bounded, tested subagent capability.

Why bother? Because "this model fits in VRAM" tells you almost nothing about whether it can be a useful coding
worker. This project turns that uncertainty into something measurable. If you have spare GPU capacity and use
agents, it is a way to turn that hardware into a cheap, replaceable worker — without handing your main agent a
black box and hoping for the best.

It started out a night's project for an old Intel Arc A770. Under the hood, the worker is not just "a model running
locally." The build runs inside a bubblewrap kernel sandbox, with filesystem, network and process boundaries, so an
untrusted coding model gets a disposable seat rather than access to your workstation.

Every candidate model is profiled and qualified on actual coding tasks: what it can complete, how fast it runs, how
much memory it consumes, and where it starts to fail. The result is a capability profile, not a benchmark score.

That profile is what makes the system useful to an orchestrator: instead of asking "which model should I use?", it
can ask "which local worker is qualified for this job, within these latency and resource constraints?" The GPU will
be replaceable. The measured capability is the interface.

```text
your coding agent ── the context, the judgement, the plan
      │  local-build skill (a brief in, a capture out)
      ▼
A770 builder harness ── guard · run lock · budget gate · capture · verify
      │
      ├── bubblewrap sandbox ── opencode ── the seat (a standalone clone of your repository)
      │                          │ loopback bridge, the only network
      └── llama.cpp (Vulkan) ────┘── the qualified model on the A770
```

The repository holds a coding-worker harness with two card modes — display-safe (the tested default, under a 13 GiB
VRAM cap, for a card that also drives the desktop) and pure-inference (under a 15.3 GiB cap, for a card with nothing
else on it) — each with its own profile registry naming the qualified models (`config/profiles.json`,
`config/profiles.inference.json`). It also holds the profiling kit and its suite (`kit/`, `harness/run_suite.sh`)
that a candidate model is qualified against — `kit/PROFILE.md` says how every field of a row is measured and what
it compares with — and the `local-build` skill, the installable front door an agent hands
a brief to.

## Why we made it

A workstation full of coding agents depends on online seats that go down, rate-limit, or cost credits mid-build. Much
of what those agents are asked to do is bounded work. That work is a change to named files, tests from a
specification, or the read of one large file. It does not need the strongest model available, only one that does it
correctly in the repository's idiom. It does not need the orchestrating agent's own context spent on it either. The
card in the machine was idle. The obvious route for it, vLLM on Intel's XPU backend, is closed on this card. So the
serving line here is llama.cpp with the Vulkan backend on the distribution's own driver. That is why the project
measures rather than assumes.

Whether a 16 GB card can hold a model that actually finishes a small, well-specified change in a real repository is a
question. Only measurement answers it. From this release, a new candidate is measured on the kit inside this
repository instead of a sibling one — no row has yet been measured on the kit, and the first one will be. Only what
passes earns a place. Earlier rows in the ledger were measured on a sibling repository, and stay comparable only
with each other.

## Install

### Prerequisites

| prerequisite | what it is for here | where to get it | where this project looks |
|---|---|---|---|
| **a Linux host** | the whole harness: the sandbox is bubblewrap, which is Linux kernel namespaces; the VRAM cap reads `nvtop`; the reset watch reads the kernel log; the serving line rests on Mesa's Vulkan driver and the Xe watchdog rules. Measured on Fedora 44 only. On Windows the plausible route is the harness under WSL2 with `llama-server` running natively on Windows and reached over the loopback, but that has not been measured here and the VRAM cap would not see the card; macOS has no bubblewrap | any distribution with user namespaces enabled (the default on Fedora, Ubuntu, Debian, Arch) | `bwrap`, `nvtop`, `journalctl` on `PATH` |
| Vulkan driver for the card | the GPU backend llama.cpp runs on | your distribution's Mesa Vulkan driver (`mesa-vulkan-drivers`) and `vulkan-tools`; `vulkaninfo --summary` must list the card | `A770B_DEVICE` = the name `llama-server --list-devices` prints |
| `llama.cpp` with the Vulkan backend | serves the model (`llama-server`) | build it from source per [llama.cpp `docs/build.md`, *Vulkan*](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan) (measured here on b10805) | `A770B_LLAMA_BIN` (default `~/llama.cpp/build/bin/llama-server`) |
| `node`, `cmake`, `make`, `g++`, `ctest` | the toolchain the profiling kit's own graders build and test against, inside the sandbox, nothing else installed | your distribution's packages (Node.js, `cmake`, `make`, `gcc-c++`) | on `PATH`; `tests/kit_selftest.sh` and `local-build.sh doctor` both check the sandboxed toolchain is reachable |
| the model files of the mode's registry (`config/profiles.json`, `config/profiles.inference.json`) | the profiles of whichever card mode this machine serves | Hugging Face: [`lmstudio-community/Qwen3.5-9B-GGUF`](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF), [`ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF), [`lmstudio-community/gemma-4-E4B-it-GGUF`](https://huggingface.co/lmstudio-community/gemma-4-E4B-it-GGUF) and [`unsloth/Qwen3.6-35B-A3B-GGUF`](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF); the download lines are in [`docs/OPERATING.md`](docs/OPERATING.md#getting-llamacpp-and-the-models); every model measured on this card is in [`config/models.md`](config/models.md), and a new one enters by the procedure in `docs/OPERATING.md` | `A770B_MODELS` (default `~/LLM/tested`), `A770B_FAST_MODEL`, `A770B_SERIOUS_MODEL`, `A770B_LONG_MODEL` |
| [opencode](https://opencode.ai/docs) | the coding agent that runs inside the sandbox | its install script or package, per its docs | found on `PATH`, or `A770B_OPENCODE_BIN` |
| [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`) | the sandbox | your distribution's `bubblewrap` package | on `PATH` |
| [socat](http://www.dest-unreach.org/socat/) | the loopback bridge to the model server | your distribution's `socat` package | on `PATH` |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | runs the tests inside the sandbox from a pre-warmed, read-only cache | its install script | on `PATH`; cache at `A770B_UV_CACHE` |
| [nvtop](https://github.com/Syllo/nvtop) | the VRAM readings the cap depends on; the harness refuses to start without them unless `A770B_ALLOW_NO_NVTOP=1` | your distribution's `nvtop` package | `A770B_GPU_MATCH` = the card's name substring in `nvtop -s` |
| `git`, `curl`, `python3`, `flock`, `ss` | the harness itself | base packages (util-linux, iproute2) | on `PATH` |
| [Node.js](https://nodejs.org) | only for the `npx skills` install route | its installer or your package manager | not needed by the harness |

Each is installed and configured by its own instructions, linked in the table above; this project only needs to know
where it landed. On Fedora, for example, everything but llama.cpp, opencode, uv and the models is one line:
`sudo dnf install mesa-vulkan-drivers vulkan-tools vulkan-headers vulkan-loader-devel glslc cmake gcc-c++ bubblewrap socat nvtop git curl python3 util-linux iproute`.
This was measured and reviewed against Fedora 44 (kernel 7.1.13), Mesa 26.1.8 (Vulkan 1.4.354) on an Intel Arc A770
16 GB, llama.cpp b10805 (Vulkan), opencode 1.18.29, bubblewrap 0.12.0, socat 1.8.1.1, uv 0.12.3, nvtop 3.3.2,
git 2.55.0, Python 3.14.7, and Node 24.15.0 with the `skills` CLI 1.5.24; newer tool versions should work, but a
different llama.cpp build or model quantisation is a different measurement, and its numbers should be re-taken with
`harness/run_one.sh` before being believed.

### Steps

```bash
git clone git@github.com:KanenasInGreece/a770-builder.git ~/local-ai/A770_Builder   # 1. the project (A770B_PROJECT)
cp ~/local-ai/A770_Builder/config/builder.env.example ~/.config/a770-builder/builder.env
#    2. edit it: A770B_REFUSE = your live checkouts (required), A770B_MODELS, A770B_DEVICE/GPU_MATCH if not an A770
git clone <your target repo> ~/local-ai/seat                        # 3. the seat: a standalone clone, never a linked worktree
#    4. put the GGUFs named in the profiles into A770B_MODELS (the profiled models and the exact file of each, per mode, are listed in docs/OPERATING.md, *Getting llama.cpp and the models*; to profile a model of your own, AGENTS.md)
bash ~/local-ai/A770_Builder/harness/warm_cache.sh                  # 5. pre-fill the read-only uv cache (the sandbox has no network)
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # 6. install the skill into your agents
bash ~/.claude/skills/local-build/scripts/local-build.sh run ~/local-ai/A770_Builder/briefs/T0-smoke.md --profile long
```

The skill follows the [Agent Skills](https://agentskills.io) open standard: a folder `skills/local-build/` holding
`SKILL.md` (name and description in its front matter, instructions in the body), `scripts/`, and an optional
constitution snippet; the standard defines the skill's format, not how it is installed. Install it with the open
`skills` CLI — `npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy` (`-g` for a user-level
install under `~/.claude/skills`, `~/.codex/skills`, and the like, rather than the current project's `.claude/skills`;
`--copy` for a real copy rather than the CLI's default symlink, since a skill that carries scripts should be a copy;
`--list` browses first, and a local clone path works in place of the GitHub slug) — or by hand, copying
`skills/local-build/` into your agent's own skill directory. Tested here with the five agents below, both routes.
Whichever route: the skill is a front door; it needs this project (`harness/`, `config/`) reachable at
`A770B_PROJECT` (default `~/local-ai/A770_Builder`), a llama.cpp build with the Vulkan backend, `bubblewrap`, `uv`
and opencode on the host, and the model files in `A770B_MODELS`.

### Per agent, as tested on this workstation

| agent | skill directory | how it shows up | note |
|---|---|---|---|
| Claude Code | `~/.claude/skills/local-build` | `claude skills` lists `local-build`; invoke as `/local-build` or run the script | the whole matrix was driven this way |
| opencode | `~/.config/opencode/skills/local-build` | loaded only if allowed by name: add `"local-build": "allow"` under `permission.skill` (its policy is deny-all) | opencode is also the executor inside the sandbox |
| Gemini CLI | `~/.gemini/skills/local-build` | `gemini skills list` shows the SKILL.md under *Discovered Agent Skills* | |
| Codex CLI | `~/.codex/skills/local-build` | present in its skills directory | Codex cannot use the local model as its own brain (Responses API), it dispatches the seat |
| grok CLI | `~/.grok/skills/local-build` | present in its skills directory | grok is x.ai-bound; it dispatches the seat through the script |

`CONSTITUTION_SNIPPET.md` is optional: a short standing reminder for an agent that will use the seat repeatedly,
added to that agent's own constitution file by its operator if wanted (`SKILL.md` says how); nothing the skill
installs writes into any agent's home. The project is MIT-licensed (`LICENSE`). Day-to-day operating detail — how a
run and a `verify` work, the three profiles and their numbers, every knob, building llama.cpp with Vulkan and
fetching the models, and what each file is — lives in [`docs/OPERATING.md`](docs/OPERATING.md).

## Security

The model's process runs inside bubblewrap (a kernel-namespace sandbox) with the seat as its only writable tree, no
credentials, no other checkout, and no network access except the model server, which itself requires a key. The
guard refuses every live checkout you list, every linked worktree, symlink and agent home; the capture executes
nothing the model wrote, and `verify` re-runs a capture's tests inside a fresh sandbox when you want proof. Six
reviews have read the boundary and what sits on it, two of them by a second model family, and found and closed real
holes. All of it, with what you must still do yourself and how to report a hole, is in [`SECURITY.md`](SECURITY.md).

This project stands alone. It needs llama.cpp, opencode, bubblewrap, socat, uv and the mode's model files, and nothing else:
no database, no account, no memory system, and no network call of its own except the model server on loopback. The
profiling kit is the same way: it needs no second repository, since its own seat, corners and hidden graders all
live inside `kit/` here. It was
developed alongside the [Shared Memory](https://github.com/KanenasInGreece/Shared_Memory) framework, a sibling project
that kept the record of its decisions and reviews; none of that is needed to use it and none of it is in this repository.
If you run that framework, or any other service, on the same host: the seat never reads or writes it, the sandbox has no
route to it, and the harness knows of it only through two optional knobs, the ports the model server must never bind and
a health URL read once before a server starts, both empty by default. `SECURITY.md` lists exactly what the harness touches.

## Contributors

Credits are for people, not a claim of joint copyright on every line (the project is MIT, see
[LICENSE](LICENSE)).

| who | role |
|---|---|
| **Xenofon S. Motsenigos** ([Oratotis](https://www.youtube.com/@Oratotis)) | Author & maintainer |
