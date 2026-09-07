# Sandbox work for the local builder seat — sequenced (from the first adversarial review)

The cheap fixes (findings 3, 4, 5, 7, 8, 9) are applied in the harness. What remains needs a design choice each and is
sequenced below so every step is a small brief with its own mutation check. Principle from the review: the containment
today is prose plus path patterns; a local model with a shell needs a kernel boundary.

## Step 1 — a kernel boundary around the opencode process (finding 1, the root of 6 and 10) — DONE 2026-09-07 13:45
**Goal.** The model's shell sees the worktree and nothing else: no `../../.claude/…/.env`, no main checkout, no other
agent's home, no `~/local-ai/A770_Builder`. **Mechanism.** `bwrap` (bubblewrap, already on Fedora) wrapping the
`opencode run` line in `build_local.sh`: read-only bind of `/usr`, `/etc`, the opencode binary and its config
dir, a private `$HOME` with only the worktree bound read-write, `uv`/python caches bound read-only, `--unshare-all`
except net (the model needs `127.0.0.1:8093`), `--die-with-parent`. The linked worktree's `.git` file points at
main's `.git/worktrees/<name>`; bind that one directory read-write and the common dir read-only, or convert the
sandbox worktree to a full clone so no path into main exists at all (simpler; the clone is the seat's own tree).
**Check.** From inside the sandbox: `cat ../../.claude/<any agent>/.env` fails; writing to the hooks
directory fails; the main checkout is not a path; the smoke brief still passes.

## Step 2 — the model's tests run inside the same boundary (finding 2) — DONE with step 1 (opencode's bash is inside the boundary; capture is read-only); the reviewer-invoked `verify` command is still open
**Goal.** Nothing the model wrote executes on the host. **Mechanism.** The brief's "run the test command" happens
inside the Step 1 sandbox already (opencode's own bash). Capture stays read-only (done: it quotes the transcript's
pytest line). A separate reviewer-invoked `verify` command re-runs the tests in a fresh sandbox against the captured
diff when a human wants proof. **Check.** A brief that writes a test which touches `$HOME` is confined; the capture
never spawns python.

## Step 3 — move the harness out of the model's reach and checksum the sync (finding 6) — first half DONE by step 1 (the harness dir does not exist inside the sandbox; the seat is a clone, not a sibling worktree of main); the sync checksum is still open
**Goal.** A poisoned run cannot become fleet-wide code. **Mechanism.** With Step 1 the project dir is invisible to
the model; additionally `sync_local_build.sh` refuses to install unless the source matches a recorded checksum
(`SKILL.sha256` committed beside it, updated only by the operator), and the worktree moves to `~/local-ai/seat/`
so it is not a sibling of the source. **Check.** Edit the source without updating the checksum → sync refuses.

## Step 4 — the budget gate and the server (finding 10) — gate half DONE 2026-09-07 14:00 (built into harness/guard.sh, no dependency on any other repo; A770B_BUDGET_GATE plugs an external one in); the API key on :8093 is still open
**Goal.** No dependency on the live checkout at serve time; no unauthenticated driver of the card.
**Mechanism.** Copy `probe_budget.sh` into `harness/` (portable form: the env path and framework ports become
variables); start `llama-server` with `--api-key` from a file readable only by the operator and pass it to the
provider config's `apiKey`; keep `--host 127.0.0.1`. **Check.** A curl without the key gets 401; the skill run
still passes; the gate refuses when the framework card is busy.

## Step 5 — re-review
A second adversarial pass (different model family from the first) on the sandboxed harness, then a written verdict
on what the seat is now trusted for.

Order matters: 1 before 2 and 3 (they lean on the boundary); 4 is independent and can run in parallel with 1.
Each step: brief → build in a worktree of A770_Builder → mutation check listed above → fact → sync.

## Done log
- 2026-09-07 13:45 — `harness/sandbox_run.sh` (bubblewrap 0.12): private tmpfs $HOME, only the seat read-write, the opencode
  binary, the global policy file and the profile config (at a neutral path) read-only, a fresh private opencode state dir
  per run (auth.json never enters), uv's managed pythons read-only, a dedicated uv cache; PID/UTS/IPC unshared, net shared
  for 127.0.0.1:8093. `build_local.sh` runs opencode through it by default (`SANDBOX=0` for the unconfined testing path).
  The seat is now `~/local-ai/seat`, a plain clone of the repo with no gitdir link to the live checkout; the linked
  worktree and its branch were removed from the main checkout. Probes from inside: `.env` files, the main checkout,
  `~/LLM`, `~/.ssh`, the MCP env, opencode's auth.json and uv's credentials are all absent; writes outside the seat fail;
  the skill's smoke run passes through the sandbox in 20 s.
- 2026-09-07 14:20 — public-readiness review (12 findings) and its fixes: --unshare-net with a socat loopback bridge to the
  server only; .git/config, hooks and info read-only inside plus safe_git for every host-side call; the opencode profile
  rendered per run from a template with a default-deny bash allow-list, the global config never mounted; the uv cache
  read-only and pre-warmed (per-run private copy); VRAM readings that refuse to fail open; A770B_REFUSE required; sync
  refuses foreign skills and is checksum-idempotent; every path a knob in env.sh. Re-test found and fixed a lock-fd leak
  into the spawned server. Steps 1, 2 (build side), 3 (first half), 4a done; open: verify command, sync checksum file,
  API key on the server, third adversarial pass (another model family), a written verdict on what the seat is trusted for.
