---
name: local-build
description: Use when delegating a bounded coding task to a LOCAL Intel Arc model — a ruled change to named files, tests from a specification, a read your window cannot hold, or when online seats are down. Do not use for design, a live checkout, or a second OS account on this host.
---

# local-build

`<skill-dir>` is the folder that contains this SKILL.md. `opencode` must be on `PATH`. **One operator** on this host (the uid that owns Docker and the card).

## Commands

`doctor` first if this host is unknown. `status` before `run` or `serve`. `--profile` is required unless the spec sets `"profile"`.

| Action | Command |
|---|---|
| Run | `bash <skill-dir>/scripts/local-build.sh run [<seat>] <brief.md> --profile <card> [--spec <spec.json>] [--timeout <s>]` |
| Verify | `bash <skill-dir>/scripts/local-build.sh verify <label_or_patch> [<seat>] [--test "<cmd>"] [--timeout <s>]` |
| Cards | `bash <skill-dir>/scripts/local-build.sh profiles` · `profiles --name <card>` · `menu` |
| State | `bash <skill-dir>/scripts/local-build.sh status` |
| Doctor | `bash <skill-dir>/scripts/local-build.sh doctor` |
| Server | `bash <skill-dir>/scripts/local-build.sh serve <card>` · `stop` |
| Stop run | `bash <skill-dir>/scripts/local-build.sh stop-run` |
| Reset | `bash <skill-dir>/scripts/local-build.sh reset [<seat>]` |
| Version | `bash <skill-dir>/scripts/local-build.sh --version` · `check-update` |

## Patterns

Copy the command. The line under it is what that command does.

**A — Dispatch.**

```
bash <skill-dir>/scripts/local-build.sh run <brief.md> --profile <profile>
```

→ `~/local-ai/results/<label>.task.md` and `<label>.patch`. Judge by `verify`, never the exit code. Default seat is `A770B_SEAT`. Smoke that seat with the brief only.

**B — Confirm.**

```
bash <skill-dir>/scripts/local-build.sh verify <label>
```

→ re-applies the patch on a clean seat with no model.

`status` before `run` or `serve` — live cap is `A770B_VRAM_CAP_GIB`, not a number here.

profiles | menu | status

## Pick a card

No learned router. Read the brief (edit scope, file size, time you can spend), then choose from `local-build.sh menu`, which lists only the models measured on this machine's card: each row's `use_for`, useful window and speed say what it is for. A smaller, faster model for ordinary edits, tests and reads under its window; a larger one when the deliverable is larger than the brief or spans several files. `profiles --name` is the full `use_for` (a card that fails a dimension says so there). A GGUF Q4/Q6/IQ3 label is the **weight** encoding — a slow run is often the wrong file or backend, not the wrong family.

Display-safe (`A770B_CARD_MODE=display`). Pure-inference (`A770B_CARD_MODE=inference`). Run `local-build.sh menu` for this machine's measured models — the tables live there, not in this file.

## Spec (optional)

```json
{ "profile": "<card>", "timeout": 1500,
  "card": "Local_Documentation/BUILDER_CARD.md",
  "scope": { "edit": ["src/foo.py", "tests/test_foo.py"] },
  "bash_allow": ["make check"],
  "context": { "definitions_of": ["src/foo.py"] },
  "verify": { "test": "uv run --with pytest python -m pytest -q tests/test_foo.py", "hidden": ["test_foo_hidden.py"] } }
```

`card` is standing instructions (file in the seat, or `{"text":"…"}`, ≤8000 chars). A CLI flag wins over a spec key.

## Always / Ask / Never

- **Always:** standalone clone; judge by `verify`; end a run with `stop-run`; brief names files, the exact test, and a stop (`briefs/TEMPLATE.md`). One operator on this host.
- **Ask:** raise a cap.
- **Never:** live checkout or linked worktree; raise the mode cap or `-ub 512` by hand; speculative decoding; kill `bwrap` by name; install this skill for another OS account or put a service user in `docker`.

## Load when needed

`docs/OPERATING.md` — run, knobs, logs. `AGENTS.md` — ladder. `kit/PROFILE.md` — registry fields. `config/models.md` — ledger.
