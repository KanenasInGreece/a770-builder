---
name: local-build
description: Dispatch a coding task to the LOCAL builder model on the Arc A770 (llama.cpp Vulkan, opencode seat) instead of an online LLM seat. Two card modes, each with its registry of profiles: display-safe (long = Qwen3.5-9B, fast = Gemma 4 E4B, serious = Qwen3.8-27B) and pure-inference (the same card with nothing else on it: long at its whole window, a moe row Qwen3.6-35B-A3B, serious at a larger window); the flag is --profile <name>. Use for bounded, well-specified work the calling agent chooses to delegate: a small change to named files, tests from a specification, the read of a file its own window cannot hold; and as the fallback when online seats are down or rate-limited. Always in a standalone clone of the target repository, never in a live checkout.
---

# local-build — the A770 builder seat

**What it does.** Starts (or switches) the llama.cpp server on the A770, runs the brief through opencode against it
inside a sandbox whose only writable tree is the seat, then captures the diff, the model's test result and the
server-side timings, and resets the seat. The seat is judged by the DELIVERABLE (the capture: files changed, tests
green) and by `verify`, never by exit code.

**When to use it.** The calling agent decides; the seat is for bounded, well-specified work that does not need that
agent's own model or context: an already-ruled change to a few named files, a test file from an invariant, the read of a
file the agent's window cannot hold. It is also the fallback when the online seats are down, rate-limited or too
expensive for the task. Not for design work, not for anything touching a live checkout.

## The profiles — pick by the card, know the window you have

Measured on this A770 (16 GB), llama.cpp b10805 Vulkan, on the qualification task. Two registries, one per card mode;
`A770B_CARD_MODE` selects which one this machine serves:

Display-safe (the default): the card also draws the desktop, cap 13 GiB after load.

<!-- profiles:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| long (default) | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB | 36.8 / 571 tok/s | The default: every ordinary change, tests from a specification, and a read up to about 64k. Reads exactly at 100k but takes eleven minutes to get there. |
| fast | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB | 60 / 796 tok/s | The fast reader: a large file read cold in about four and a half minutes at 100k and precise questions about a passage deep in it. Not the profile for edits. |
| serious | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB | 8.1 / 72 tok/s | A deliverable larger than its brief, tests written from an unfamiliar module, a change touching several files. Ten to twenty-five minutes; decode under five tokens a second by 64k, so point it at files that fit 32k. A measured card may run the IQ3_S file at a larger window through builder.env. |
<!-- profiles:end -->

Pure-inference: the card draws nothing, cap 15.3 GiB after load.

<!-- profiles-inference:begin -->
| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| long (default) | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB | 43.7 / 439 tok/s | The default: every ordinary change, tests from a specification, and a read up to its whole window at above five tokens a second; the depth probe is exact at 100k. |
| moe | Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf | 131,072 (~131k) | 14.1 GiB | 21.8 / 207 tok/s | A deliverable larger than its brief at three times the dense 27B's speed and its whole window at above ten tokens a second; needs 18 GB of host RAM for the run and a CPU that is busy; not beside another large load. |
| serious | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB | 7.9 / 71 tok/s | A deliverable larger than its brief, tests from an unfamiliar module, a change touching several files: the best-written output here, at eight tokens a second; useful to about 98k by the four-tokens-a-second rule, so a long read costs minutes per 10k tokens. |
<!-- profiles-inference:end -->

The profiles are configuration, not fixed: `status` shows the mode and the registry actually configured on this
machine, and the project's `config/models.md` is the ledger of every model qualified on this card with its numbers; a
new model enters through `docs/OPERATING.md`, *Qualifying a new model*. Every file the model reads lands in that
window, and prefill is what you pay for: a 17k-token read costs the long profile 37 s and the serious profile 4 min.
Point briefs at files, not directories. (The seat's `AGENTS.md` is set aside for the run and restored after; the
skill does that.)

## Choosing a profile

Read the card before choosing: `local-build.sh profiles`, or `--name <profile>` for one profile alone. It carries a
row's `category` (`dense` or `moe`) and weight class; `useful_ctx`, the largest depth at which decode stays above four
tokens a second by the two measured points, so a window is a capacity, not a promise of that whole depth at speed;
`speed` (decode and prefill) at 8k and at the far end of the window, set against the time you can spend; `fit`, three
short strings — `code`, `think`, `write` — each with its source, saying how the row measured on the axis the brief
needs; `sampling`, the model's own recommended line for the role, already served; and `builder_class`, whether the row
is useful to at least 81,920 tokens and passed a green task. Match these against the brief's edit scope (a change to
the files named, several files touched at once, tests written from a specification) and the token size of the files it
points at, and take the least costly profile whose card covers all three, not the one with the strongest reputation.
The registry keeps one best-measured row per category and weight class: a row a sibling beats on quality, speed and
useful window alike is pruned and the ledger says by whom, so a name on the card has already survived that. Each
profile's `use_for` says in words what it is not for, and that refusal is as much the card's content as what it is
good at; a brief that needs more than the strongest card covers should not go to the seat at all.

## How to call it

```bash
# 1. the seat: a STANDALONE CLONE of the target repository (its own .git directory), never a live checkout or a
#    linked worktree — the script refuses both. Default: A770B_SEAT (~/local-ai/seat).
# 2. a brief: a Markdown file that names the files, the exact test command, and the stop condition
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md> --profile long               # long (default), on the default seat
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md> --profile long --spec <spec.json>   # with a run specification (below)
bash ~/.claude/skills/local-build/scripts/local-build.sh run <seat> <brief.md> --profile fast        # fast, on a given seat: the reader
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md> --profile serious            # serious: a deliverable larger than its brief
bash ~/.claude/skills/local-build/scripts/local-build.sh run <brief.md> --profile moe                # inference mode: a deliverable larger than its brief, three times the dense 27B's speed
bash ~/.claude/skills/local-build/scripts/local-build.sh verify <label>                       # re-run a capture's tests in a fresh sandbox
bash ~/.claude/skills/local-build/scripts/local-build.sh serve <profile>      # start/switch the server only
bash ~/.claude/skills/local-build/scripts/local-build.sh profiles [--name <profile>]   # the card, one profile or all, with what is actually served
bash ~/.claude/skills/local-build/scripts/local-build.sh status               # which model is up, VRAM, health
bash ~/.claude/skills/local-build/scripts/local-build.sh stop                 # free the card
bash ~/.claude/skills/local-build/scripts/local-build.sh stop-run             # end the run in progress by its own pid; never kill bwrap by name
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

## The run specification — what you, the caller, decide for one run

The brief is prose. Beside it you may pass `--spec <spec.json>`, every key optional:

```json
{ "profile": "long", "timeout": 1500,
  "card": "Local_Documentation/BUILDER_CARD.md",
  "scope": { "edit": ["src/foo.py", "tests/test_foo.py"] },
  "bash_allow": ["make check"],
  "context": { "definitions_of": ["src/foo.py"] },
  "verify": { "test": "uv run --with pytest python -m pytest -q tests/test_foo.py", "hidden": ["test_foo_hidden.py"] } }
```

`card` (a file inside the seat, or `{"text": "…"}`, at most 8,000 characters) becomes the model's standing instructions
for the run: the repository's conventions, the idiom to copy. `scope.edit` limits edits to the listed paths. `bash_allow`
adds commands the profile's allow-list lacks; a bare wildcard, a path, or anything beginning with a wrapper or interpreter is
refused, and the profile's deny block is rendered after every addition. `context.definitions_of` names files whose
definitions the harness greps into the brief copy (at most 8 files, 400 lines each, 1,200 in all), so the model reads
an index instead of paging. `verify.test` is what `verify` runs when you give no `--test`; `verify.hidden` names
acceptance tests the model never saw, kept under `A770B_HIDDEN_ROOT` and copied in only after the patch applies. The
specification is checked against the seat before any server starts; the capture keeps it as `<label>.spec.json` and
an echo of what was rendered as `<label>.echo.json`. A flag on the command line wins over a key.

## Rules the card and the harness enforce (do not override)

- **The cap and the batch are the mode's.** The server refuses to run past 13.0 GiB after load in display mode, 15.3
  in inference mode, and holds `-ub 512` in both. One GPU process on that card at a time; never raise a cap by hand —
  a mode is chosen after measuring what else the card holds (`doctor` says).
- **No speculative decoding** on this card: draft models and MTP heads all made decode slower.
- **`< /dev/null` on every opencode call** (the script does it); without it opencode hangs after init.
- **Seat only, inside the sandbox.** The seat must be a standalone clone not listed in `A770B_REFUSE`. The model's
  process sees the seat, a private home and a read-only uv cache; no credentials, no other tree, no network except the
  model server. Every test package a brief needs must be pre-warmed into the uv cache (`harness/warm_cache.sh`).
- **Timeouts** are the registry's: each profile's `timeout_s` (1,500 s for long and fast, 3,600 s for serious,
  1,800 s for the inference registry's moe row); `--timeout` overrides.
- **Flash attention is per model family.** The Qwen profiles run with it on; the fast profile's Gemma runs with it off,
  because with it on every Gemma 4 measured here collapsed on prefill and reset the GPU. The profile carries the flag.

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

`CONSTITUTION_SNIPPET.md` beside this file is a short standing reminder for an agent that will use this seat repeatedly:
when to use it, the three profiles, the call, the hard rules. The skill works without it. If you want it, add it to your
own constitution file (`CLAUDE.md`, `AGENTS.md` or `GEMINI.md` in your home) between its `<!-- local-build:begin/end -->`
markers so a later version can replace it. Nothing modifies your agent configuration for you.

## Configuration

All paths and knobs are defined centrally in the builder environment (`harness/env.sh` defaults, overridden by
`config/builder.env` in the project or `~/.config/a770-builder/builder.env` per user, then
`config/builder.<mode>.env` or `~/.config/a770-builder/builder.<mode>.env` for whichever card mode
`A770B_CARD_MODE` names, then the calling environment; a per-mode file cannot switch the mode that selected it). The
installed skill finds the project through `A770B_PROJECT`. Where the models go, how to qualify a new one, the card
knobs and the API key are in the project's `docs/OPERATING.md`.
