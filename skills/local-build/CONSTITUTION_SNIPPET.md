<!-- local-build:begin -->
## Local builder seat — `/local-build` (ruled 2026-09-07)
A LOCAL model on the Arc A770 executes bounded, well-specified work you choose to delegate (a small change to named
files, tests from a specification, the read of a file your window cannot hold) and stands in when the online seats are
down or rate-limited. Skill: the `local-build` folder in YOUR OWN skill directory (script `scripts/local-build.sh`). The profiles of the two card modes (a machine serves the set `A770B_CARD_MODE` names; `local-build.sh profiles` prints it):
<!-- profiles:begin -->
Display-safe (`A770B_CARD_MODE=display`, the default): **--profile long** (default) = Qwen3.5-9B-Q4_K_M, 262,144-token window (useful to ~65k), ~37 tok/s; **--profile fast** = gemma-4-E4B-Q4_K_M, 131,072-token window (useful to ~100k), ~60 tok/s; **--profile serious** = Qwen3.8-27B-GSQ-RCO-IQ3_XXS, 131,072-token window (useful to ~32k), ~8 tok/s.
Pure-inference (`A770B_CARD_MODE=inference`, a card that draws no desktop): **--profile long** (default) = Qwen3.5-9B-Q4_K_M, 262,144-token window (useful to ~262k), ~44 tok/s; **--profile moe** = Qwen3.6-35B-A3B-UD-Q4_K_XL, 131,072-token window (useful to ~131k), ~22 tok/s; **--profile serious** = Qwen3.8-27B-GSQ-RCO-IQ3_S, 196,608-token window (useful to ~98k), ~8 tok/s.
<!-- profiles:end -->
Call:
`bash <your-skill-dir>/local-build/scripts/local-build.sh run [<seat>] <brief.md> [--spec <spec.json>] [--profile <name>]` — it starts or
switches the server, dispatches the brief through opencode inside the sandbox, captures diff + tests + timings, resets the seat.
A specification beside the brief sets, for that run, the card (the seat's standing instructions), the edit scope, extra commands,
prepared definitions, `verify.test` and hidden acceptance tests (see SKILL.md); the harness's floor is never lowered by it.
Judge the result by the capture (`~/local-ai/results/<label>.task.md`) and by `verify <label>`, which re-runs the tests in
a fresh sandbox — never by exit code. Hard rules: a STANDALONE CLONE only, never a live checkout or a linked worktree
(the script refuses both) · the VRAM cap and ubatch are the card mode's (`A770B_CARD_MODE`) — never raise them by
hand · no speculative decoding on it · every `opencode run` from a non-TTY shell takes `< /dev/null` · a run is ended with
`local-build.sh stop-run`, never by process name.
<!-- local-build:end -->
