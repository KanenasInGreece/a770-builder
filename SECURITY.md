# Security

This explains the security considerations this project makes and establishes the boundary of its responsibility.
The seat runs a model that writes code you have not
reviewed yet, on the same machine as your credentials and your live checkouts. It matters because a local model is not
trusted merely for running locally: it reads a brief you may not have written, it has a shell, and one careless mount
turns a coding seat into a way off the machine. What follows is the problem as the adversarial reviews found it, the
boundary that answers it, what was expected to be fixed and what has been done, what is still open, what you must do
yourself, and how to report a hole.

## The problem

The first version of the seat contained the model with prose and path patterns: a system prompt that asked it to stay
in its tree, and a deny-list of file patterns in the agent's configuration. The first review showed how little that is
worth. A deny pattern does not survive a relative path, so from the seat the model could read a sibling agent's `.env`
file, which holds a bearer token, or write into the hooks directory of the live checkout the seat was linked to. The
capture step ran the model's own test file on the host, as the operator, with the full environment, before anyone had
read it. The model owned its tree's `.git/config`, so a config-driven code path it wrote there would have run on the host
at the next `git status`. And the model shared the host's network, so anything it could reach, it could reach.

Each of those is a different failure of the same idea: a local model with a shell needs a kernel boundary, not advice.

## The boundary today

The model's process runs inside bubblewrap. Its writable tree is the seat, a standalone clone with its own `.git`
directory, and nothing else of yours: a private empty home, the pre-warmed uv cache mounted read-only, no credentials,
no other checkout. Inside the seat, the whole `.git` directory is mounted read-only, so no config-driven code path can be planted for
the host and nothing can be hidden there to outlive the reset. The sandbox has no network access except the model server: the network namespace
is unshared and a loopback bridge carries only that one port. The only agent configuration inside is a profile rendered
per run, with a default-deny shell allow-list, no MCP, no web fetch, no skills, and `.env` and key files unreadable.

On the host side, every git command that touches the seat goes through `safe_git`, which neutralises the config-driven
code paths on the command line: hooks, fsmonitor, pagers, filters, credential helpers, and, on every diff it takes, the
external diff and textconv programs. The capture executes nothing the model wrote; it records the diff, the new files,
the names of any ignored files the run left, the pytest line the model reported, and writes the complete change as a
patch. The reset that follows removes everything the run produced, ignored files included, keeping only the briefs
directory the harness itself writes, so nothing a run planted waits for the next one. When a reviewer wants proof rather than a claim, `verify` re-applies that patch to a clean seat
and runs the tests inside a fresh sandbox with no model, no bridge and no key; its verdict is the exit code of the test
command, never a line the tests printed, and file names taken from the patch are passed as arguments, never through a
shell. What `verify` proves is that the model's own tests pass inside the boundary; whether those tests are the right
ones is still the reviewer's reading of the capture. The model server requires an API key, kept in a file only the
operator can read, so a process outside the harness cannot use the card unnoticed. The guard refuses every live
checkout you list, every linked worktree, symlink, subdirectory and agent home, a seat whose `.git` is a link to
another repository, and refuses to run at all while that list is empty. `sandbox_run.sh` applies that same path
policy to the worktree before it bind-mounts it, so a direct call cannot hand an agent home or a live checkout to
the model as its writable tree. The run lock's descriptor is closed before the
sandbox starts, so no host file crosses the boundary with it. A symlink the model leaves in the seat is named in the
capture and never followed: the host reads nothing through a link the model made, and the patch does not carry it. The
briefs directory the harness writes keeps only the harness's own copies across a reset; anything else planted there is
reported and removed. The health line the budget gate prints from an optional URL is stripped of control characters
and cut short, so a hostile service cannot write to your terminal through it.

A run may carry a specification, a JSON file the calling agent writes, which sets the model's standing instructions,
the paths it may edit, extra commands it may run, files whose definitions are prepared for it, and how the result is
verified. The specification widens nothing beneath the profile: it is read on the host, checked against the seat
before the run lock and before any server starts, never read from inside the seat, and its card and context files must
be regular files inside the seat, so it cannot make the harness read a host file into the model's prompt. Its bash
additions are refused when they are a bare wildcard, a path, or begin with a wrapper or interpreter (a first word
that runs another command, case-folded), and the profile's deny
block, the floor, is rendered after them. That floor is defence in depth and nothing more: the profile already lets the
model run python, so a model that wants a git verb has one; what stops it is the read-only `.git` mount, the unshared
network and the reset, which are not the specification's to touch. The capture keeps the snapshot and an echo of what
was rendered, hash included, so a reviewer sees what the run was allowed as well as what it did. A specification can
only add patterns, never remove one: a pattern granted in the template is granted to every run of every project that
consumes it, whichever specification is in play. The floor's deny patterns match a whole command string, not a chain,
so a chained command is admitted by its first word alone — which is why a brief names one command per line, leaving
the sandbox itself as the boundary.

The profiling kit's own reviewer pass (`kit/REVIEW-rubric.md`'s maintainable and usable axes) puts a stage's own
patch in front of a second model, which is its own kind of exposure: the patch is content a builder model wrote,
and the reviewer reads it as data, not as instructions. `harness/run_suite.sh` guards that read the same way the
budget gate's health line is guarded: the reviewer runs as a profile the runner refuses to let resolve to the
builder's own model, at temperature 0 with no tools, and the patch is delimited between fixed markers with an
explicit instruction not to follow anything inside it; the reviewer's own reply is parsed only in the fixed
two-line shape it was asked for, so a chattier reply that tries to steer the parser back out is read as no score,
never a guessed one. The reviewer's own profile, model, context window, sampling and the rubric's own hash are
recorded beside the score, so a reader can tell which model graded a row and against which rubric.

| surface | inside the sandbox |
|---|---|
| filesystem | the seat read-write; a private empty home; the pre-warmed uv cache read-only; nothing else of yours |
| the seat's `.git` | the whole directory read-only: the model reads history and status, and can plant nothing there, neither a config-driven code path for host-side git nor a file that would outlive the reset unseen |
| network | none, except the model server; no LAN, no internet, no other service on the host |
| tools | one rendered profile: default-deny shell allow-list, no MCP, no web fetch, no skills; `.env` and key files unreadable |
| the model server | reachable with the key the rendered profile carries; the key unlocks nothing else |
| the host afterwards | the capture is taken with `safe_git` and executes nothing the model wrote; the reset removes ignored files too; `verify` runs the tests in a fresh sandbox without the key |
| a run specification | read on the host, checked before the server starts, snapshotted; card and context files inside the seat only; hidden tests under one root, copied in after the patch applies; bash additions rendered before the floor, wildcards and wrappers refused; the sandbox, the network and `.git` untouched by it |

## What it touches on your machine

The harness runs as you, outside the sandbox, and this is the complete list of what it touches. It reads the model files,
starts one `llama-server` process on the port you configure, writes under the data directory (the seat, the results, the
logs, the rendered profile and the uv cache), creates the API key file under your config directory with mode 600, and
reads the seat's git metadata through `safe_git`. It makes no network call of its own except to that server on loopback;
the model files
and the skill are fetched by you, with the commands in the install section. When a run names hidden tests, it reads
them from `A770B_HIDDEN_ROOT` and nowhere else. It needs no database, no
account and no memory system.

If you run other services on the same host, the seat is built not to meet them. Inside the sandbox the network namespace
is unshared and only the model server's port is bridged, so nothing the model does can reach a service on the host, the
LAN or the internet. Outside it, the harness knows of other services through two optional knobs, both empty by default:
`A770B_FRAMEWORK_PORTS`, ports the model server must never bind, and `A770B_HEALTH_URL`, a status URL read once, with a
plain GET, before a server starts. Nothing is written to either.

The project was developed alongside the [Shared Memory](https://github.com/KanenasInGreece/Shared_Memory) framework, a
sibling project that kept the record of its decisions and reviews and whose repository was the seat's qualification task.
Nothing of it is needed here and nothing of it is in this repository. If you run that framework, the paragraph above is
the whole of what this project does near it: it never reads or writes its store, and its gateway is at most the health
URL you choose to name.

## What was expected to be fixed

The first review left five pieces of work, sequenced in [`SANDBOX-PLAN.md`](SANDBOX-PLAN.md): a kernel boundary around
the model's process; the model's tests executed only inside that boundary, with a way for a reviewer to re-run them;
the harness source out of the model's reach; a budget gate that depends on nothing outside this project and a key on
the model server; and a second adversarial review by a model that had not written any of it.

## What has been done

Four of the five pieces are in place, and the fifth is half done. The public-readiness review that followed the first found twelve more items, among them
the read-only git metadata, the network unshare, the default-deny profile and the rendered configuration; every one was
fixed and mutation-checked the same day. The reviewer-invoked `verify` command and the API key on the server came in the
cycle after publication, and the review of that cycle closed the gaps it opened: an injectable default command in
`verify`, the run lock's descriptor reaching the sandbox, and ignored files surviving the reset. Three reviews had read the boundary, every one from the same model family; the fourth, by another family, read it
with the cycle that made the health line informational and found what the first three had not: a symlink the model
leaves in the seat was followed by the host when the capture read new files, a file planted under `.git` outlived the
reset unseen, and the briefs directory's exemption could hide a planted file. All three are closed above. The fifth and sixth reads, a code-quality read and a security read by the other
family, took the run specification: both found a path-qualified wrapper passing the bash addition's first-word check and
the renderer's placeholder guard unable to match two of its own placeholders, and the security read added a symlinked
hidden root, a hidden test placed but never run beside a caller's test command, and an echo that named the wished-for
profile rather than the one used; all closed in the same fix round with the reviewers' mutation checks run. Every
change to the boundary since the first has gone through a branch, a read-only adversarial review by a model that did
not write it, and the mutation checks re-run before merge. This cycle added `node --test *`, `node --check *`,
`g++ *` and `cmake *` to the template's own allow list, admitting the kit's JavaScript and C++ iteration commands
under the boundary review of another family; `make *` and `ctest *`, which the kit's multi-file C++ stage needs for
its configure step, go in per-task specifications instead, never the template. None of these patterns touch how a
row is actually graded: the kit's own hidden graders run through `verify`, outside the profile and its allow-list
entirely, exactly as every other hidden test does. And the seat a kit run gives the model is never a clone or
checkout of this repository: it is an export of `kit/seat/` alone, git-init'ed fresh, so the hidden graders under
`kit/hidden/` — the ones the allow-list changes above exist to run — are never part of the model's own tree at any
point in a run.

## What is still open

- A third adversarial pass, by a model family that has not yet read this boundary.
- A hidden acceptance test is anti-mistake, not anti-adversary: it is copied into the seat only after the patch has
  applied, so the model never sees it while building, but a hostile patch can print it into the verify file.
- Skills mounted into the seat from a run specification are deferred: both adversarial reads of the design found the
  copy-and-mount channel the most attackable part, and it is not built.
- The API key is readable by the model inside a build run, because the rendered profile carries it. That is accepted:
  the key unlocks only the server the sandbox already reaches; `verify` runs without it. Rotate by deleting the key
  file; the next `serve` notices the running server no longer accepts the file's key and restarts it.

## What you must do yourself

- Set `A770B_REFUSE` to every live checkout on the machine. The guard refuses to run while it is empty, and it is the
  only thing that knows which trees are yours.
- Judge every run by its capture in `A770B_DATA/results/`, never by its exit code. Run `verify` on a capture before
  merging anything from it.
- Never run a builder's test file on the host to see whether it passes: `verify` exists so the proof happens inside a
  fresh sandbox. A test file is code the model wrote; read it before it runs anywhere you can be hurt, including
  `tests/selftest.sh` in a patch that touches it.
- Keep the seat a standalone clone. A linked worktree shares `.git` with the live checkout and is refused for that reason.

## Reporting

Open a private vulnerability report through the repository's Security tab on GitHub. Say which script or mount the
finding concerns and how you reproduced it; a mutation check that demonstrates the escape is the most useful attachment.
