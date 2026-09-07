---
name: local-build
description: Dispatch a coding task to the LOCAL builder model on the Arc A770 (llama.cpp Vulkan, opencode seat) instead of an online LLM seat. Two profiles — fast (Qwen3.5-9B, default) and serious (Qwen3.8-27B IQ2_XS). Use when online seats are down or rate-limited, for small well-specified changes in a standalone clone of the target repository, never in a live checkout.
---

# local-build — the A770 builder seat

**What it does.** Starts (or switches) the llama.cpp server on the A770, runs the brief through opencode against it
inside a sandbox whose only writable tree is the seat, then captures the diff, the model's test result and the
server-side timings, and resets the seat. The seat is judged by the DELIVERABLE (the capture: files changed, tests
green) and by `verify`, never by exit code.

**When to use it.** An already-ruled, well-specified change to a few named files, when the online seats are down,
rate-limited or too expensive for the task. Not for design work, not for anything touching a live checkout.

## Two profiles — pick by the task, know the window you have

Measured on this A770 (16 GB, also driving the desktop), llama.cpp b10805 Vulkan, on the qualification task:

| profile | model | context | speed | typical small task | when |
|---|---|---|---|---|---|
| **fast** (default) | Qwen3.5-9B Q4_K_M | **81,920 tokens** | 45 tok/s decode · 464 tok/s prefill | ~2–3 min | every ordinary change; in the qualification task it followed the repository's idioms and passed its tests first try |
| **serious** | Qwen3.8-27B GSQ-RCO IQ2_XS | **158,000 tokens** | 8.1 tok/s decode · 70 tok/s prefill | ~10–25 min | when the fast profile fails or the deliverable is larger than its spec: tests from an invariant, text a reviewer will read; the best-written output of our matrix, at the speed floor. Never for mechanical edits: measured on a six-site one-keyword change, it produced the same patch as the fast profile, one comment word closer to the brief, at seven times the wall clock |

The profiles are configuration, not fixed: `status` shows what is actually configured on this machine, and the project's
`config/models.md` is the ledger of every model qualified on this card with its numbers; a new model enters through
`docs/OPERATING.md`, *Qualifying a new model*. Every file the model reads lands in that window, and prefill is what you pay for: a 17k-token read costs the fast
profile 37 s and the serious profile 4 min. Point briefs at files, not directories. (The seat's `AGENTS.md` is set aside
for the run and restored after; the skill does that.)

## How to call it

```bash
# 1. the seat: a STANDALONE CLONE of the target repository (its own .git directory), never a live checkout or a
#    linked worktree — the script refuses both. Default: A770B_SEAT (~/local-ai/seat).
# 2. a brief: a Markdown file that names the files, the exact test command, and the stop condition
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md>                       # fast, on the default seat
bash ~/.claude/skills/local-build/scripts/local-build.sh run <seat> <brief.md> --serious      # serious, on a given seat
bash ~/.claude/skills/local-build/scripts/local-build.sh verify <label>                       # re-run a capture's tests in a fresh sandbox
bash ~/.claude/skills/local-build/scripts/local-build.sh serve fast|serious   # start/switch the server only
bash ~/.claude/skills/local-build/scripts/local-build.sh status               # which model is up, VRAM, health
bash ~/.claude/skills/local-build/scripts/local-build.sh stop                 # free the card
bash ~/.claude/skills/local-build/scripts/local-build.sh reset                # discard everything uncommitted in the seat, ignored files too
bash ~/.claude/skills/local-build/scripts/local-build.sh --version            # this copy's version vs the project's; warns on mismatch
bash ~/.claude/skills/local-build/scripts/local-build.sh check-update        # asks GitHub for the latest release, on demand; nothing else calls out
```
(From another agent's install, replace `~/.claude` with that agent's skill directory; the script is identical.)

`run` prints the capture path (`~/local-ai/results/<label>.task.md`: diff, new files, the model's pytest line, the
TTFT/TPOT distribution) and writes the complete change beside it as `<label>.patch`. **Review the capture, then run
`verify <label>`**: it re-applies the patch to the clean seat, runs the tests inside the same boundary the model had
(no model, no key), and writes `<label>.verify.md` with the exit code as its verdict. The model's own pytest line is a
claim; the verify file proves the model's tests pass. Whether they are the right tests is what the review of the
capture decides. A cheap reviewer prompt for it lives at `~/local-ai/A770_Builder/briefs/REVIEW-prompt.md`.

## Rules the card and the harness enforce (do not override)

- **The A770 drives the desktop.** The server refuses to run past 13 GiB after load and holds `-ub 512`. One GPU
  process on that card at a time.
- **No speculative decoding** on this card: draft models and MTP heads all made decode slower.
- **`< /dev/null` on every opencode call** (the script does it); without it opencode hangs after init.
- **Seat only, inside the sandbox.** The seat must be a standalone clone not listed in `A770B_REFUSE`. The model's
  process sees the seat, a private home and a read-only uv cache; no credentials, no other tree, no network except the
  model server. Every test package a brief needs must be pre-warmed into the uv cache (`harness/warm_cache.sh`).
- **Timeouts:** fast 1,500 s, serious 3,600 s by default (`--timeout` overrides).

## Brief shape that works (measured)

Name the target file and function, list the behaviours, give the exact test command, say what must not be edited, and
end with a stop condition. Point at one existing file as the idiom to copy. Anything that must appear verbatim, a
docstring or a message, goes in the brief as a quoted block: the builder writes what it is given and drops what is
described. Template: `~/local-ai/A770_Builder/briefs/TEMPLATE.md`; the example that qualified the seat:
`~/local-ai/A770_Builder/briefs/T1-sanitize-entity-tests.md`. The test command names the files the change touches, never
the whole suite: it runs in the model's shell tool, which cuts a command at 120 s unless the model asks for longer, and
inside a boundary with no network and none of the host's environment (measured: a 3,800-test suite took 144 s there, and
17 host-dependent tests that pass on the host failed). The full suite is the merger's run on the host after review.

## Optional: persistent agent guidance

`CONSTITUTION_SNIPPET.md` beside this file is a twelve-line reminder for an agent that will use this seat repeatedly:
when to use it, the two profiles, the call, the hard rules. The skill works without it. If you want it, add it to your
own constitution file (`CLAUDE.md`, `AGENTS.md` or `GEMINI.md` in your home) between its `<!-- local-build:begin/end -->`
markers so a later version can replace it. Nothing modifies your agent configuration for you.

## Configuration

All paths and knobs are defined centrally in the builder environment (`harness/env.sh` defaults, overridden by
`config/builder.env` in the project or `~/.config/a770-builder/builder.env` per user, then the calling environment). The
installed skill finds the project through `A770B_PROJECT`. Where the models go, how to qualify a new one, the card
knobs and the API key are in the project's `docs/OPERATING.md`.
