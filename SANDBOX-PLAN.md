# The sandbox plan

This document is about how the seat went from a model held by prose and path patterns to a model held by a kernel
boundary. It matters because the plan is the reasoning behind every mount and refusal in the harness: a reader who wants
to change the boundary, or judge a proposed change, needs to know which failure each piece answers. [`SECURITY.md`](SECURITY.md)
describes the boundary as it stands; this document describes the problem, the solution that was proposed, the fixes that
were expected, and what was done.

## The problem

The first adversarial review read the skill and the harness as an attacker would and found ten things, four of them
critical. The containment was a system prompt and a deny-list of file patterns, and a deny pattern does not survive a
relative path: from the seat the model could read another agent's bearer token, or write a hook into the live checkout
the seat was linked to, because a linked worktree shares its repository metadata with the checkout it came from. The
capture ran the model's test file on the host before any review. The refusal of the live checkout was a string
comparison a symlink walked past, and it ran after the seat's `AGENTS.md` had already been moved aside. The harness
source lay in a sibling directory the model could write, and the install script would have copied a poisoned skill into
every agent on the machine. Nothing stopped a second process from driving the card.

The cheap findings were fixed the same hour: a shared guard that canonicalises paths and refuses protected checkouts,
symlinks, subdirectories and agent homes; a run lock; a restore under a trap; a capture that quotes the model's pytest
line instead of executing the file. The rest needed a design choice each.

## The proposed solution

Put the model's process inside a kernel boundary that sees the seat and nothing else, and make everything else lean on
that. Bubblewrap was already on the host. With the boundary in place, the model's own test run is already confined; a
reviewer needs only a way to re-run those tests in a fresh boundary against the captured change. The harness source
stops being reachable the moment the seat is a standalone clone rather than a sibling worktree, so a poisoned run has
nowhere to go. The budget gate that decides whether the card may be used is copied into the harness so the seat depends
on no other repository, and the server takes a key so nothing outside the harness can use the card. Then a second
reviewer, from a model family that wrote none of it, reads the result.

## The expected fixes

1. **A kernel boundary around the model's process.** Bubblewrap around the agent's run: `/usr` and `/etc` read-only, a
   private home, only the seat writable, the network unshared except for the model server, the sandbox dying with its
   parent. Check: from inside, an agent's `.env` is not a path, a write to a hooks directory fails, the live checkout
   does not exist, and the smoke brief still passes.
2. **The model's tests run only inside that boundary.** The capture never executes model-written code; a
   reviewer-invoked `verify` re-runs the tests in a fresh sandbox against the captured change. Check: a test that touches
   the home directory is confined; the capture spawns no interpreter.
3. **The harness out of the model's reach.** The seat becomes a standalone clone in its own directory; the project
   directory does not exist inside the sandbox. Check: the harness path is absent from inside.
4. **A self-contained budget gate and a key on the server.** The gate reads host memory, stray server processes, free
   VRAM, an optional health URL and protected ports from this project's own configuration; the server starts with an API
   key from a file only the operator can read, and the rendered profile carries it. Check: a request without the key
   is refused, the skill still passes, the gate refuses when the card is busy.
5. **A second adversarial review** by a model that wrote none of it, and a written verdict on what the seat is trusted for.

## What was done

The boundary came first. `harness/sandbox_run.sh` wraps the agent's run in bubblewrap with a private home on tmpfs, the
seat as the only writable tree, the agent binary and a rendered profile read-only, a fresh private state directory per
run so no stored credential enters, uv's managed pythons read-only and a dedicated cache. The seat moved to its own
directory as a standalone clone and the linked worktree was removed from the live checkout. Probed from inside: the
agents' `.env` files, the live checkout, the model weights, SSH keys and the agent's own credentials are all absent,
writes outside the seat fail, and the smoke brief passes in twenty seconds.

The public-readiness review that followed hardened the boundary in twelve places, all fixed and mutation-checked the
same day: the network namespace is unshared and a socket bridge carries only the model server's port; `.git/config`,
`.git/hooks` and `.git/info` are read-only inside and every host-side git call goes through `safe_git`; the profile is
rendered per run from a template with a default-deny shell allow-list and the global agent configuration never enters;
the uv cache is pre-warmed and read-only; VRAM readings refuse to fail open; the list of protected checkouts is
required; every path is a knob. The budget gate was built into the harness with no dependency on any other repository.
That closed the first piece, the build side of the second, the third, and half of the fourth.

The cycle after publication closed the rest of the second and fourth pieces. The capture now writes the complete change
as a patch beside the report, and `verify` re-applies it to a clean seat and runs the tests inside a fresh sandbox with
no model, no bridge and no key; the seat is reset afterwards, ignored files included. The server starts with
`--api-key-file`, the key is created on first use with mode 600, the rendered profile carries it into a build run, and a
request without it is refused. The bridge's connection-closed messages, which had been landing in every transcript, now
go to a log of their own. The adversarial review of that cycle, by a model that wrote none of it, found and had fixed
before merge a default test command in `verify` that a model-chosen file name could have turned into a forged pass, the
run lock's file descriptor reaching into the sandbox as a writable host file, gitignored files surviving every reset
unseen, and a seat guard that followed a symlinked `.git`.

The fifth piece is open: a third adversarial pass by a model family that has not read this boundary yet, and with it the
written verdict on what the seat is trusted for. The install-script checksum from the original third piece no longer
applies to this repository: the install script is local tooling that does not ship, and the public install route copies
the skill from the repository itself.
