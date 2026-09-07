# Security

The seat runs a model that writes code you did not review yet, on the same machine as your credentials and your live
checkouts. This file says what the boundary is today, what has been checked, what is still open, and how to report a hole.

## What holds now

Two adversarial reviews by a reviewer-class model, read-only: one on the first version, a public-readiness pass on this one. What
holds now, all mutation-checked: canonical refusal of protected checkouts (`A770B_REFUSE`, required), of linked worktrees,
symlinks, subdirectories and agent homes; a run lock and pids verified before any kill; the model's process inside bubblewrap
with a private home, no credentials, no other tree, **no network except a loopback bridge to the model server** (socat over a
unix socket; the LAN, the internet and every other host service are unreachable), `.git/config`, `.git/hooks` and `.git/info`
mounted read-only so the model cannot plant config-driven code; every host-side git call neutralises config-driven code paths
(`safe_git`); the uv cache read-only and pre-warmed; the only opencode config inside is a rendered per-run profile with a
default-deny bash allow-list and no MCP, web or skills; capture executes nothing the model wrote; VRAM readings that refuse to
fail open. Still open (see below and `SANDBOX-PLAN.md`): a sandboxed `verify` command for reviewers, a checksum on the sync, an API key on the
model server, a third adversarial pass from another model family.

## What the model can and cannot reach

| surface | inside the sandbox |
|---|---|
| filesystem | the seat read-write; a private empty home; the pre-warmed uv cache read-only; nothing else of yours |
| the seat's `.git` | `config`, `hooks` and `info` mounted read-only, so no config-driven code path can be planted for the host |
| network | none, except a loopback bridge to the model server on `A770B_PORT`; no LAN, no internet, no other host service |
| tools | opencode with a rendered per-run profile: default-deny bash allow-list, no MCP, no web fetch, no skills, `.env` and key files unreadable |
| the host afterwards | the capture is taken with `safe_git`, which neutralises every config-driven git code path, and executes nothing the model wrote |

## What is still open

- a sandboxed `verify` command so a reviewer can run the model's tests inside the same boundary;
- an API key on the model server, so a process outside the sandbox cannot use the card unnoticed;
- a third adversarial pass by a model family that has not reviewed this boundary yet.

## What you must do yourself

- Set `A770B_REFUSE` to every live checkout on the machine. The guard refuses to run while it is empty, and it is the only
  thing that knows which trees are yours.
- Judge every run by its capture in `A770B_DATA/results/`, never by its exit code, and never merge a capture unread.
- Keep the seat a plain clone. A linked worktree shares `.git` with the live checkout and is refused for that reason.

## Reporting

Open a private vulnerability report through the repository's Security tab on GitHub. Say which script or mount the
finding concerns and how you reproduced it; a mutation check that demonstrates the escape is the most useful attachment.
