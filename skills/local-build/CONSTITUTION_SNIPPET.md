<!-- local-build:begin -->
## Local builder seat — `/local-build` (ruled 2026-09-07)
A LOCAL model on the Arc A770 can execute an already-ruled build brief when the online seats are down or
rate-limited. Skill: the `local-build` folder in YOUR OWN skill directory (script `scripts/local-build.sh`). Two profiles:
**fast** (default) = Qwen3.5-9B Q4_K_M, 81,920-token window, ~45 tok/s, 2–3 min per small task;
**`--serious`** = Qwen3.8-27B IQ2_XS, 158,000-token window, ~8 tok/s, 10–25 min. Call:
`bash <your-skill-dir>/local-build/scripts/local-build.sh run <worktree> <brief.md> [--serious]` — it starts or
switches the server, dispatches the brief through opencode, captures diff + tests + timings, resets the worktree.
Judge the result by the capture (`~/local-ai/results/<label>.task.md`) and a review, never by exit code.
Hard rules: a git WORKTREE only, never the live main checkout (gitguard dispatch rules apply unchanged) · the A770
is the desktop's display card — never raise its VRAM cap or ubatch · no speculative decoding on it
· every `opencode run` from a non-TTY shell takes `< /dev/null`.
<!-- local-build:end -->
