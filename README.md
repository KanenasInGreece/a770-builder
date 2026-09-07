# A770_Builder

This repository is about one card, the Intel Arc A770 16 GB, used as a local code builder. It holds the two models that
earned the role by passing a real test-writing task on a live codebase, with the llama.cpp flags that keep the card stable
and the context each one gives you; and it holds `local-build`, an Agent Skills-standard skill any CLI agent can install,
which serves the chosen model, runs the brief inside a network-isolated sandbox that can only see the seat, and hands back
a capture for review instead of trusting the run.

Built and measured on 2026-09-06 and 07 on a card that also drives the desktop; reviewed adversarially twice and contained
the same day. The measured report (matrix, serving lines, method) lives beside the weights: `~/LLM/tested/README.html`.

## Why we made it

The online seats a workstation full of coding agents depends on go down, rate-limit, or cost credits in the middle of a
build. This card was already in the machine, driving the desktop, and idle. The question was whether a 16 GB Arc could
hold a model that actually finishes a small, well-specified change in a real repository, and whether an agent could hand
it that work without giving a local model the run of the host. The first was answered by measurement: every candidate
got the same brief on a live codebase, and only the ones whose tests passed under a cheap reviewer's eye kept a place. The
second was answered by the harness and the sandbox, and by two adversarial reviews of them. What is here is the result of
both, so the next card, model or build can be re-qualified the same way instead of trusted.

## Install

`llama.cpp` built with the Vulkan backend (`llama-server`), `bubblewrap` (`bwrap`), `socat`, `uv`, `python3`, `git`, `curl`,
`nvtop` (the VRAM readings the cap depends on — the harness refuses to start without them unless you accept the risk with
`A770B_ALLOW_NO_NVTOP=1`), `flock` and `ss` (util-linux / iproute2), and opencode (the executor inside the sandbox). Node is
only needed for the skills CLI route.

```bash
git clone git@github.com:KanenasInGreece/a770-builder.git ~/local-ai/A770_Builder   # 1. the project (A770B_PROJECT)
cp ~/local-ai/A770_Builder/config/builder.env.example ~/.config/a770-builder/builder.env
#    2. edit it: A770B_REFUSE = your live checkouts (required), A770B_MODELS, A770B_DEVICE/GPU_MATCH if not an A770
git clone <your target repo> ~/local-ai/seat                        # 3. the seat: a plain clone, never a linked worktree
#    4. put the GGUFs named in the profiles into A770B_MODELS (see Models)
bash ~/local-ai/A770_Builder/harness/warm_cache.sh                  # 5. pre-fill the read-only uv cache (the sandbox has no network)
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # 6. install the skill into your agents
bash ~/.claude/skills/local-build/scripts/local-build.sh run ~/local-ai/A770_Builder/briefs/T0-smoke.md
```

The skill follows the [Agent Skills](https://agentskills.io) open standard: a folder `skills/local-build/` holding
`SKILL.md` (name + description in the front matter, instructions in the body), `scripts/` and a constitution snippet.
Tested here on 2026-09-07, both routes:

**1. The open skills CLI** — detects the agents you have installed and copies the skill into each of them.
```bash
npx skills add KanenasInGreece/a770-builder --skill local-build -g --copy   # from GitHub, user-level, real copies
npx skills add /path/to/A770_Builder --skill local-build -g --copy     # from a local clone (tested)
npx skills add /path/to/A770_Builder --list                            # browse first (tested: finds local-build)
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

Every agent adds `CONSTITUTION_SNIPPET.md` to its own constitution file itself (`SKILL.md` says how); nothing writes
into another agent's home. Licence: MIT (`LICENSE`).

Operating detail (how a run works, the two profiles and their numbers, every knob, where the models go, what each file
is) lives in [`docs/OPERATING.md`](docs/OPERATING.md).

## Security

The model's process runs inside bubblewrap with the seat as its only writable tree, no credentials, no other checkout,
and no network beyond a loopback bridge to the model server. The guard refuses every live checkout you list, every linked
worktree, symlink and agent home; the capture executes nothing the model wrote. Two adversarial reviews have read the
boundary; three items remain open. All of it, with what you must still do yourself and how to report a hole, is in
[`SECURITY.md`](SECURITY.md).

## Contributors

Credits are for people and collaborators, not a claim of joint copyright on every line (the project is MIT, see
[LICENSE](LICENSE)).

| who | role |
|---|---|
| **Xenofon S. Motsenigos** ([Oratotis](https://www.youtube.com/@Oratotis)) | Author & maintainer |
| **[Claude](https://www.anthropic.com/claude)** (Anthropic) | AI collaborator — assisted with the model qualification matrix, the serving flags, the harness and sandbox, and the adversarial reviews. **Not** a code co-author for legal/git authorship purposes. |
