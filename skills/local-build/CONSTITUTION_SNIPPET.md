<!-- local-build:begin -->
## Local builder seat — `/local-build` (ruled 2026-09-07)
A LOCAL model on the Arc A770 executes bounded, well-specified work you choose to delegate (a small change to named
files, tests from a specification, the read of a file your window cannot hold) and stands in when the online seats are
down or rate-limited. Skill: the `local-build` folder in YOUR OWN skill directory (script `scripts/local-build.sh`). Three profiles:
**fast** (default) = Qwen3.5-9B Q4_K_M, 81,920-token window, ~45 tok/s, 2–3 min per small task;
**`--serious`** = Qwen3.8-27B IQ2_XS, 158,000-token window, ~8 tok/s, 10–25 min; **`--long`** = Gemma 4 E4B, 131,072-token
window, for reading files the fast window cannot hold, useful to ~100k. Call:
`bash <your-skill-dir>/local-build/scripts/local-build.sh run [<seat>] <brief.md> [--serious|--long]` — it starts or
switches the server, dispatches the brief through opencode inside the sandbox, captures diff + tests + timings, resets the seat.
Judge the result by the capture (`~/local-ai/results/<label>.task.md`) and by `verify <label>`, which re-runs the tests in
a fresh sandbox — never by exit code. Hard rules: a STANDALONE CLONE only, never a live checkout or a linked worktree
(the script refuses both) · the A770 is the desktop's display card — never raise its VRAM cap or ubatch · no speculative
decoding on it · every `opencode run` from a non-TTY shell takes `< /dev/null`.
<!-- local-build:end -->
