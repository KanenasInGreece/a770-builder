---
name: local-build
description: Dispatch a coding task to the LOCAL builder model on the Arc A770 (llama.cpp Vulkan, opencode seat) instead of an online LLM seat. Two profiles — fast (Qwen3.5-9B, default) and serious (Qwen3.8-27B IQ2_XS). Use when online seats are down or rate-limited, for small well-specified changes in a git worktree, never in the live main checkout.
---

# local-build — the A770 builder seat

**What it does.** Starts (or switches) the llama.cpp server on the A770, runs the brief through opencode
against it inside a git worktree, then captures the diff, the test result and the server-side timings.
Ruled the offline builder seat on 2026-09-07 after a measured model matrix. The seat is judged by
the DELIVERABLE (files changed, tests green) and the worktree's git status — never by exit code.

## Two profiles — pick by the task, know the context you have

| profile | model | context | KV | speed (measured) | typical small task | when |
|---|---|---|---|---|---|---|
| **fast** (default) | Qwen3.5-9B Q4_K_M | **81,920 tokens** (q8_0 KV) | 6.9 GiB VRAM | 45 tok/s decode · 464 tok/s prefill · 33 tok/s after a 17k prefill | ~2–3 min | every ordinary change; copies the repo's idioms; 11/11 tests green first try |
| **serious** | Qwen3.8-27B GSQ-RCO IQ2_XS | **158,000 tokens** (q4_0 KV) | 11.5 GiB VRAM | 8.1 tok/s decode · 70 tok/s prefill · 6.7 tok/s after 17k | ~10–25 min | when the fast profile fails or the change needs more reasoning; best-written output of the matrix, at the 5 tok/s floor |

The context above is the SERVER's window. opencode's own opening request costs ~5.4k tokens with the repo's
`AGENTS.md` set aside (the skill does this for the run and restores it after) — with it in place the opening
request is ~32k tokens, which is why it is set aside. Every file the model reads lands in that window; a
17k-token prefill costs the fast profile 37 s and the serious profile 4 min. Keep briefs pointed at files, not
directories.

## Incorporate the constitution snippet

`CONSTITUTION_SNIPPET.md` beside this file is a twelve-line standing-conduct block (when to use the seat, the two
profiles and their windows, the call, the four hard rules, the record ids). **Add it to your own constitution file**
(`CLAUDE.md`, `AGENTS.md` or `GEMINI.md` in your home) the first time you use this skill, between its
`<!-- local-build:begin/end -->` markers so a later version can replace it. Nothing installs it for you.

## Configuration and models

Every path and knob lives in ONE env file — copy `config/builder.env.example` from the project to
`~/.config/a770-builder/builder.env` (per user) or `<project>/config/builder.env` and edit. The installed skill copy
finds the project through `A770B_PROJECT` (default `~/local-ai/A770_Builder`). Nothing else is hardcoded.

**Models.** Put GGUF files in `A770B_MODELS` (default `~/LLM/tested`) and name the two profiles' files in
`A770B_FAST_MODEL` / `A770B_SERIOUS_MODEL` (a bare file name is looked up there; an absolute path works too). Only the
llama-server process on the host reads the weights; the model's own sandbox never sees `A770B_MODELS`, by design. To
qualify a new model before trusting it, run `harness/run_one.sh` from the project.

**Card.** `A770B_DEVICE` is the name from `llama-server --list-devices`; `A770B_GPU_MATCH` the substring of that card in
`nvtop -s`. On a card that also draws the desktop keep `A770B_VRAM_CAP_GIB` (13 of 16) and `A770B_UBATCH` (512).

## How to call it

```bash
# 1. a worktree of the target repo, NEVER the live main checkout (the script refuses it)
#    e.g. ~/local-ai/seat (a plain CLONE of your target repository — no link to a live checkout) or one you make
# 2. a brief: a Markdown file that names the files, the exact test command, and the stop condition
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md>                       # fast, on the default seat (A770B_SEAT)
bash ~/.claude/skills/local-build/scripts/local-build.sh run <worktree> <brief.md> --serious  # serious, on a given worktree
bash ~/.claude/skills/local-build/scripts/local-build.sh serve fast|serious   # start/switch the server only
bash ~/.claude/skills/local-build/scripts/local-build.sh status               # which model is up, VRAM, health
bash ~/.claude/skills/local-build/scripts/local-build.sh stop                 # free the card
```
(From another agent's install, replace `~/.claude` with that agent's skill directory; the script is identical.)

The run prints the capture path (`~/local-ai/results/<label>.task.md`) with the diff, new files, the pytest
line and the TTFT/TPOT distribution, and resets the worktree to clean. Review the capture before merging
anything; a cheap reviewer prompt lives at `~/local-ai/A770_Builder/briefs/REVIEW-prompt.md`.

## Rules the card enforces (do not override)

- **The A770 drives the desktop.** The server refuses to run past 13 GiB after load and holds `-ub 512`
  Do not raise them. One GPU process on that card at a time; the B580 is never touched.
- **No speculative decoding** on this card — draft models and MTP heads all made decode slower.
- **`< /dev/null` on every opencode call** (the script does it); without it opencode hangs after init
  
- **Seat only, inside a sandbox.** The seat must be a self-contained clone (a `.git` directory; linked worktrees of a live
  checkout are refused) and not in `A770B_REFUSE`. The model runs under a bubblewrap boundary that contains only the seat
  tree — no credentials, no other tree, no network except a loopback bridge to the model server — with `.git/config`,
  `.git/hooks` and `.git/info` read-only. Every test package the brief needs must be pre-warmed into the uv cache
  (`harness/warm_cache.sh`), because the sandbox cannot download.
- **Timeouts:** fast 1,500 s, serious 3,600 s by default (`LOCAL_TIMEOUT` env overrides).

## Brief shape that works (measured)

Name the target file and function, list the behaviours, give the exact test command, say what must not be
edited, and end with a stop condition. Point at one existing file as the idiom to copy (the fast profile
follows it; the 14B did not). Example: `~/local-ai/A770_Builder/briefs/T1-sanitize-entity-tests.md`.
