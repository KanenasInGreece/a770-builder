# Operating the seat

a770-builder is a harness around a local GPU: it runs a coding model in a sandbox, measures models into profiles
with the packaged tests a reader can run on their own model, and presents the result to an LLM orchestrator as a
skill it calls. Developed and tested on a 16 GB Intel Arc A770.

This document is about running the seat day to day: how a run works, each card mode's profiles and what they were
measured at, every knob, where the models go, the card's constraints and what each file is. The README covers what
this is, why it exists, how it installs and its security state; [`SECURITY.md`](../SECURITY.md) covers the boundary.

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile qwen38-27b-iq3s-sycl            # the profile you chose, on the default seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile qwen35-9b-q4km-vulkan --spec <spec.json>  # with a run specification (below)
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --profile qwen38-27b-iq3xxs-vulkan  # a deliverable larger than its brief, on a named seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile gemma4-8b-e4b-q4km-vulkan                     # the reader
bash harness/run_suite.sh <profile>                                                      # the standard suite (kit/), stage by stage, one results file
bash skills/local-build/scripts/local-build.sh verify <label>                            # the reviewer's proof
bash skills/local-build/scripts/local-build.sh reset                                     # a seat the skill refuses as dirty
bash skills/local-build/scripts/local-build.sh serve <profile> · profiles [--name <profile>] · menu · status · stop · stop-run · --version · check-update
```

A run with no `--timeout` and no spec `timeout` has no clock. It runs until it finishes. An explicit `--timeout` or a spec `timeout` is a caller fence for that one run; `--timeout 0` means no clock, as it does for GNU `timeout`. The profile's `timeout_s` is recorded headroom, not a clock the skill wraps the run in.

`stop-run` ends the run in progress. The pid file names the process-group leader, `harness/run_lead.py`, which starts the sandbox and also keeps the caller fence when one is set (exit 124, `timeout`'s convention). `stop-run` checks that pid is this harness's leader, writes the `run.interrupted` receipt, tells the leader's `build_local.sh` owner directly (USR1), then sends one TERM to the leader, and to its group when the pid leads one. USR1 goes only to an owner verified to support it: a `bash` process whose script is this project's `harness/build_local.sh`, declared by the leader's own environment (`A770B_RUN_OWNER_PID`), with a USR1 handler installed. A legacy owner (USR1 would kill it) or a parent whose arguments merely name `build_local.sh` is never signalled. The run is two process groups: the leader group, and the worker group. The worker (the sandbox, or opencode when `SANDBOX=0`) is started in a group of its own, with the stop signals held and default dispositions restored before it runs, so the leader never receives its own forwarded TERM: a stop that lands together with the fence's TERM is still a stop, and a stop during the worker's start still reaches the worker. As soon as that worker exists, the leader publishes its pid — which is its process-group id — and its `/proc` start time in `logs/run.worker-pgid` (or `A770B_WORKER_PGID_FILE`). It removes that file only after it has reaped the worker. A SIGKILL, an OOM kill or an interpreter crash leaves the file in place; the worker would otherwise keep holding the capture pipe, and a kill of the leader group does not reach it. There are windows in which no record can end that orphan: a SIGKILL between posix_spawnp and the publish, a failed publish, and reuse of the leader's pid while the worker is still running (the watch only checks that the pid in run.pid is absent from /proc, not that pid's start time). Those windows are not closed. A guarded helper signals that recorded group only when the pid still exists, its process-group id is itself, and its start time matches the file; otherwise it does nothing, and it never signals by process name. If the leader's pipeline returns 137, or the leader is already dead while the worker file remains, `build_local.sh` uses that helper (TERM, then KILL if the same group is still alive) so the pipe can unblock, then continues the capture path. `stop-run`'s own TERM still goes only to a live leader, and a live `run_lead.py` leader is what forwards it to the worker. If that leader is still alive after 30 s, `stop-run` signals the recorded worker group with the same helper instead of stopping there. The run then captures what the seat did and resets the seat. The leader itself records the stop: it forwards the TERM, waits for the worker and exits 143 whatever the worker's own status, so a stopped stage is reported as interrupted and never graded even when the receipt could not be written, the owner could not be found, and the worker exited 0 on the TERM. If none of the three can carry the stop (no receipt, no owner, a leader that is not `run_lead.py`), `stop-run` refuses and signals nothing. Never end a run by process name: `bwrap` is also every Flatpak application on the desktop. Every stop and fence integration test runs with `SANDBOX=0`. The production `SANDBOX=1` path — TERM to the worker group, then `sandbox_run.sh`, then `bwrap --new-session --die-with-parent` — is not exercised end to end.

On the Strata path and the unmarked-keep path, `served_profile` means launch-equivalent to the requested profile. Strata records the requested name; the managed launch is re-checked (alias and context) before dispatch, and there is no separate sidecar name to copy. An empty or stale sidecar is rewritten with the requested profile only after the model, the context and the argv signature match. A future RAM tier that is a compose or cgroup setting outside that argv signature must be added to the signature, or two profiles that differ only by that setting would be kept and recorded as the same launch.

`run` refuses anything but the root of a standalone clone that is not a live checkout (including ancestors/descendants of protected paths), refuses a seat that is not clean
(ignored files included, so nothing from an earlier run can pass as this one's; `reset` clears it), takes the card lock (`local-build.lock`, FD 9) before the canonical per-seat lock (FD 7), starts or
switches the server (or attaches with `--base-url http://host:port[/v1] --served-model-name <name>`, plain HTTP without userinfo/extra path/query/fragment), sets the seat's `AGENTS.md` aside for the call and restores it under a trap, runs opencode inside the
sandbox with `< /dev/null` (closing inherited lock descriptors across sandbox and background helper boundaries; `stop-run` remains lock-free), captures the diff, the model's own pytest line and the timings without executing anything the
model wrote, writes the complete change as `~/local-ai/results/<label>.patch`, and resets the seat. `harness/run_suite.sh --fresh` replaces an existing nonempty seat only when external disposable-seat provenance recorded under `$A770B_DATA/logs/disposable-seats/` confirms it; if migrating from an old unmarked seat directory, move it aside or pass a new `<seat>` path rather than passing `--fresh` over an ordinary directory. Judge by the capture
in `~/local-ai/results/`, never by exit code.

The reset after every run removes every uncommitted file in the seat, ignored ones included, and keeps only
`Local_Documentation/briefs/`, where the harness copies each brief. Keep no state of your own in the seat: a hand-made
`.env` or `.venv` there does not survive a run, by design.

`verify <label>` is how a reviewer turns the model's claim into proof. It re-applies the patch to the clean seat, runs
the tests inside a fresh sandbox with no model, no bridge and no key, writes `<label>.verify.md` with the command, the
output and a verdict taken from the command's exit code, and resets the seat whatever happens. By default it runs pytest
on every `tests/*.py` file the patch touches, passing the names as arguments and refusing any name that is not plain;
`--test "<command>"` runs something else instead, inside the same boundary; the verdict is the exit status of that
whole command, so give it the test runner alone, never followed by an `echo` or anything else that would end it with
zero. It refuses an empty patch, a seat that is not clean (ignored files included), and a patch that does not apply.

**The run specification.** A brief is prose the harness cannot enforce. Beside it the caller may pass a small JSON
file, `--spec <spec.json>`, that sets for that one run what the calling agent has decided: `card`, a file inside the seat
or inline text that becomes the model's standing instructions for the run (the repository's conventions, the idiom to
copy); `scope.edit`, the paths the model may edit, everything else refused; `bash_allow`, extra commands the profile
lets it run (a test runner the allow-list lacks); `context.definitions_of`, files whose definitions the harness greps
and appends to the brief copy, so the model reads an index instead of paging; `verify.test`, the command `verify` runs
when `--test` is not given; and `verify.hidden`, acceptance tests the model never saw, kept under `A770B_HIDDEN_ROOT`
and copied into the seat only after the patch has applied. `profile` and `timeout` may be set too; a flag on the
command line wins. Every key is optional and an unknown one is refused. The specification is checked against the seat
before the run lock is taken and before any server starts, then snapshotted, so nothing re-reads the caller's file
later; the capture keeps the snapshot as `<label>.spec.json` and an echo of what was actually rendered, with the
profile's hash, as `<label>.echo.json`, and prints the echo in its *run specification* section. What a specification
cannot do is lower the floor: the sandbox, the unshared network, the read-only `.git`, the secret-file denials and the
deny block at the end of the profile's bash rules (git state verbs, docker, systemctl, sudo, package installs, network
clients) are rendered after everything the caller adds, and a bash pattern that is a bare wildcard, a path, or begins with a
wrapper or interpreter is refused outright; the floor itself is defence in depth, the boundary is the sandbox. An example specification is at the end of `briefs/TEMPLATE.md`. For the profiling kit's own tasks, `make` and `ctest` are added per task through `bash_allow`, while `node`, `g++` and `cmake` come from the template itself.

The test command a brief names runs in the model's own shell tool, which cuts a command at 120 seconds unless the model
asks for longer, and inside the boundary, which has no network and none of the host's environment. So a brief names the
test files its change touches, never the whole suite. Measured on a repository of about 3,800 tests: the full suite took
144 seconds inside the sandbox, the first attempt was cut at 120, and 17 tests that depend on the host's environment
failed there while passing on the host. The full suite is the merger's run on the host after review; `verify` re-runs
what the brief named.

## Profiles

`kit/PROFILE.md` is the field-by-field derivation of a row: which rung fills a field, how its number is worked
out, and what it can honestly be compared with outside this project.

The seat runs in one of two card modes, chosen by `A770B_CARD_MODE`: display-safe (the default, the tested set, for a
card that also draws the desktop) and pure-inference (for a card that draws nothing). Each card and mode reads its
own registry, the single source of every profile's numbers — `config/registry/<card>.<mode>.json`, resolved from
`A770B_CARD` and `A770B_CARD_MODE` — and `local-build.sh profiles` (or `--name <profile>` for one) shows
what is actually configured on this machine for the mode it is running in, `--served` layered over the environment.
A card with no registry file is not an error: it shows one line ("no measured models for card … — climb one with
ladder.sh") until its first row is pasted. Numbers in the tables below are properties of this card, build and
quantisation, not of the models in general, measured with llama.cpp b10805 and the Vulkan backend, on the
qualification task.

A `builder.env` (or the environment) that names one card's `A770B_<CARD>_MODEL` as another card's file inverts the two
cards silently — the environment wins over the registry. `local-build.sh doctor` reports it, and `local-build.sh
profiles` shows what is actually served beside what the registry says.

**Display-safe** (the default): measured on this A770 (16 GB, also driving the desktop).

| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB at 262,144 served | 36.8 / 571 tok/s at 8k | Every ordinary change, tests from a specification, and a read up to about 64k. Reads exactly at 100k but takes eleven minutes to get there. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| gemma4-8b-e4b-q4km-vulkan | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB at 131,072 served | 60 / 796 tok/s at 8k | The fast reader: a large file read cold in about four and a half minutes at 100k and precise questions about a passage deep in it. Not the profile for edits. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| qwen38-27b-iq3xxs-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB at 131,072 served | 8.1 / 72 tok/s at 8k | A deliverable larger than its brief, tests written from an unfamiliar module, a change touching several files. Ten to twenty-five minutes; decode under five tokens a second by 64k, so point it at files that fit 32k. A measured card may run the IQ3_S file at a larger window through builder.env. |

This workstation's own `builder.display.env` runs `qwen38-27b-iq3xxs-vulkan` on the quantiser's task-lossless file instead, IQ3_S, at
114,688 under a 14.1 cap: 13.78 GiB after load, peaking at 14.21 GiB during a 32k prompt, ~9 minutes (five tests,
542 s); `local-build.sh profiles --served` shows it in place of the registry's row.

The Qwen windows are swept with `harness/ctx_sweep.sh`, prefill and decode against position with VRAM sampled and the
kernel log watched. The 9B serves its native 262,144 tokens and holds a 100k prompt with no reset and VRAM
flat within 0.3 GiB of its load; by the legacy four-tokens-a-second rule (below) its useful window is 65,536 tokens, this
row's own registry `useful_ctx`. The three 27B files share one speed profile, decode being compute-bound on this
card whatever the quantisation: about 8 tokens a second at 8k, 6 at 32k and below 4 tok/s by 64k. By the
legacy four-tokens-a-second rule (below) the 27B's useful window is 32,768 tokens, this row's own registry
`useful_ctx`, whether it serves 131,072 or 114,688, and a brief for it points at files that fit that.

The E4B exists for the read, not the edit: files the 9B's window cannot hold, and the "read this whole thing
and tell me" step before a brief is written. Its useful depth is about 100k tokens: at that depth it answered a probe's
planted detail exactly, while its broad recall of "three other functions" blended real names into ones that do not
exist; at 120k it misread a number. Ask it precise questions about a passage, not to recall the file: asked to index every definition of a 6,300-line
file it paged through 60% and got 34 of 40 names right and 19 lines; the 9B paged through all of it in
15 minutes and listed constants instead of definitions. Neither seat indexes a large file. Judge what it reports the
way every capture is judged. It is not the profile for multi-file shell edits: on the harness's own brief it made one
edit of three and reported all three done. Flash attention off is its condition on this card, and with it off the V
cache is f16.

The window is the server's. opencode's opening request costs about 5.4k tokens with the seat's `AGENTS.md` set aside
(the skill does this for the run and restores it after); with it in place the opening request was about 32k tokens, which
is why it is set aside. Every file the model reads lands in that window, and a 17k-token prefill costs the 9B
37 s and the 27B 4 min. Keep briefs pointed at files, not directories.

**Pure-inference**: measured on the same card with nothing else on it.

| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| qwen35-9b-q4km-vulkan | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB at 262,144 served | 43.7 / 439 tok/s | Every ordinary change, tests from a specification, and a read up to its whole window at above five tokens a second; the depth probe is exact at 100k. |
| qwen38-27b-iq3s-vulkan | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB at 196,608 served | 7.9 / 71 tok/s at 8k | A deliverable larger than its brief, tests from an unfamiliar module, a change touching several files: the best-written output here, at eight tokens a second; useful to about 98k by the legacy four-tokens-a-second rule, so a long read costs minutes per 10k tokens. |
| qwen38-27b-iq3s-sycl | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 100,000 (~100k) | 14.61 GiB at 100,000 served | 9.91 / 358 tok/s at 8k | The faster sibling of qwen38-27b-iq3s-vulkan: the same 27B served in the SYCL container (`A770B_SERVE=compose`, `compose/a770-sycl.yaml`), ~25% faster decode and ~5x faster prefill at the same depths; 131,072 does not load (the SYCL server segfaults above 100,000). |

`useful_ctx` is the served context (`prefill-absorb-v1`). A failed depth probe still caps a measured window at
8,000, and an unanswered probe leaves the field unset. Decode speed is recorded and does not set the window.
`A770B_ABSORB_MAX_S`, when it is a positive number of seconds, limits the window to the deepest depth whose
measured time to first token stays inside that limit. This is the definition of `useful_ctx`; `harness/ladder.sh`
(`AGENTS.md`, *The ladder*) computes it. A row with no `useful_ctx_rule` keeps the legacy decode rule it was
measured under. Beyond the table,
`local-build.sh profiles` (`harness/profiles.py card`)
carries the full card for each row: `speed` at 8k and the far end (decode, prefill, time to first token); `fit`, three
short strings with their source — `code` (the in-house suite or T1, tests passed and wall), `think` (a reasoning arm
measured, or the card's GPQA or AIME figure named as the card's), `write` (a reviewer-graded prose brief, or "not
measured"); `sampling`, the model's own recommended temperature, top_p, top_k, min_p and penalties, with its source —
where a row carries one at all: the display registry's `qwen35-9b-q4km-vulkan` and `gemma4-8b-e4b-q4km-vulkan` rows have none, measured at the client's
default temperature (0) before this registry carried a sampling line, and their own `use_for` says so; and
`builder_class`, computed from whether the row's task test passes (a green task); the useful window and speed are
recorded, not gates. The
orchestrating agent reads these fields against a brief's scope, the token size of the files it names, and the time it
can spend, and takes the least costly profile whose card covers all three. `local-build.sh menu` prints which measured
models are already up and which would need a restart; when nothing is loaded it lists the whole card and marks the
default without calling it up.

The standard speed number for a row is `harness/bench_http.sh`: llama-benchy, called and not packaged, against the
server the load rung left up, at prompt sizes 8192, 32768, 65536 and 100000. The ladder does not stop that server
to take the number. Window and depth deadlines stay the unmeasured cap, and tokens are counted from the tokenizer
directory beside the weights. `--dry-run` prints the command without touching the card. `harness/ctx_sweep.sh`
stays the tool for what that speed call does not watch — the VRAM peak during a prompt and the kernel log's reset count.
Its own far end is a target, not a measurement: the corpus slice it asks for is sized by four bytes a token, and
this kit's corpus is dense real source rather than prose, so that rule understates — a sweep asking for 8,000 tokens
sent 12,718. The far end actually recorded for a row is whatever token count its reply measured, never the number
typed on the command line, and a row too slow to answer inside the run's own time ceiling leaves nothing to record
at that depth at all (the 27B's own 100k attempt ran past a 3,600 s ceiling with no answer).
Beside that speed number sits the standard suite's own as-delivered speed, `harness/suite_report.py` reading a
`run_suite.sh` results file's per-stage capture, recorded as `speed.delivered` — labelled, never given alone, since
it answers a different question: not what the row can do at that flag set in isolation, but what it delivered inside
a real coding run.

## Configure

All configurable paths and knobs are defined centrally and layered: the defaults in `harness/env.sh`, then
`<project>/config/builder.env`, then `~/.config/a770-builder/builder.env`, then `<project>/config/builder.<mode>.env`,
then `~/.config/a770-builder/builder.<mode>.env`, then the calling environment, later winning; the mode that names the
two per-mode files is the mode after the two plain `builder.env` files and the environment have been read, so a
per-mode file cannot switch the mode that selected it. Copy `config/builder.env.example` to one of the plain files and
edit only what differs, and a per-mode file for whichever override one mode needs and the other does not. The defaults
are the workstation the seat was qualified on (Arc A770 as `Vulkan0`, models in `~/LLM/tested`, data in `~/local-ai`).
The installed skill copy finds the project via `A770B_PROJECT`.

**The card mode.** `A770B_CARD_MODE` selects which of the two configurations this machine serves: unset or `display`
(the default) reads `config/registry/<card>.display.json` under a 13.0 GiB cap, for a card that also draws the
desktop; `inference` reads `config/registry/<card>.inference.json` under a 15.3 GiB cap, for a card that draws
nothing else. A per-mode file,
`builder.<mode>.env` beside each `builder.env`, lets one machine keep different overrides for each mode in the load
order above — this workstation keeps `qwen38-27b-iq3xxs-vulkan` at IQ3_S under a 14.1 cap only in `builder.display.env`, and drops that
override in inference mode. `status` prints the mode and the registry file on the builder-card line. `doctor` measures,
only while the seat's own server is down, what the builder card actually holds: in inference mode a reading above
0.5 GiB is reported MISSING — something else is still drawing the card; in display mode a reading at or under 0.1 GiB
gets a hint that inference mode would serve the larger registry under the larger cap. Either mode's cap is raised only
on a fresh measurement of what else the card holds, never by hand.

**The builder card.** `A770B_CARD` (no default — `doctor` refuses while unset) names the ONE builder card on this host (e.g. `b70`, `a770`). Physical capabilities and selectors are described in `config/cards.json`: PCI IDs, total VRAM, GPU match patterns, architecture generation, XMX native precision formats, and device selectors. Setting `A770B_CARD` supplies default values for `A770B_VK_DEVICE_SELECT`, `A770B_GPU_MATCH`, and `A770B_CARD_VRAM_TOTAL` via `harness/env.sh`, while explicit environment or `builder.env` overrides still win. `A770B_CARD_ROLE` (optional) overrides a card's role in `cards.json` — a community B580 builder sets `A770B_CARD_ROLE=builder` to use a card this project filed as an encoder. The VRAM cap (`A770B_VRAM_CAP_GIB`) remains an empirical runtime measurement in `builder.env`/`builder.<mode>.env` with no default.

**The card.** `A770B_DEVICE` is the name from `llama-server --list-devices`; `A770B_GPU_MATCH` is the substring of that
card in `nvtop -s`, which the VRAM readings and the cap depend on (supplied from `config/cards.json` by `A770B_CARD`). `A770B_UBATCH` (512) stays the same in both modes:
it keeps the GPU's job watchdog quiet. The server refuses to stay up past the mode's cap after load.

**Speculative decoding.** Qualify each method for the exact checkpoint, engine build, backend and card. Record
the draft method and settings, hardware and memory limits, and the workload. Compare against speculation off
when the same configuration supports it. Measure prefill, decode at representative depths, peak VRAM and host RAM,
stability, useful context, and kit results. When a pack requires speculation, qualify the required setting and
record it as part of that configuration. Each published profile uses its measured settings.

**The device pin.** `A770B_VK_DEVICE_SELECT` (no default — `doctor` refuses while unset; supplied by `A770B_CARD` from `config/cards.json`) pins the server to the builder
card by PCI vendor and device id rather than by the Vulkan device index `A770B_DEVICE` names, because Vulkan lists the
boot card first and an index alone drifts when the desktop moves to another card. It is Mesa's Vulkan device selector,
passed to the server as `MESA_VK_DEVICE_SELECT`: find the id with `lspci -nn`, which prints each card's `[vendor:device]`
pair, and the trailing `!` makes that card the only Vulkan device the server can see. Set it in `builder.env` with
single quotes, `A770B_VK_DEVICE_SELECT='8086:e223!'` (the Arc Pro B70; the A770 was `8086:56a0`), since an interactive
shell reads a bare `!` as history expansion. With the selector set, `llama-server --list-devices` lists exactly one
card, the builder's, whatever card the machine booted with as its display device. `local-build.sh doctor` checks this
with the rest of the installation.

The cap is measured after load, in either mode, and has no default — `doctor` refuses while `A770B_VRAM_CAP_GIB` is
unset. On a card that also draws the desktop, a shared card's cap must account for a desktop share that has never been
measured on your card. Measure it before setting or raising the cap: run `nvtop -s`, start something that actually
draws the card (a video playing is enough), and sum every process on it that is not the server. On the A770 this came
to 1.0 GiB, so the cap there ran at 14.1 in `builder.display.env` rather than a display default. That extra headroom is
what admitted the 27B's task-lossless file, IQ3_S, at 114,688, which
loads at 13.78 GiB and peaks at 14.21 GiB during a 32k prompt; the 9B's full native window loads at
10.35 GiB at 262,144 served and fits under either cap. On a card that draws nothing, the free card here reported 15.9 GiB total and
0.08 GiB used besides the server; prefill growth above the after-load reading has measured 0.04 to 0.3 GiB under
`-ub 512`, so the inference cap is 15.3: 15.9 minus a 0.3 GiB growth allowance minus a 0.3 GiB reserve. Either cap is
raised only after a fresh measurement, never by guesswork.

**Host memory.** The only host-memory check before a load is a floor on available memory, `A770B_MIN_AVAIL_MB`
(20,000 MB by default), read from the kernel's own `MemAvailable`. It is a page-cache floor for reading the weights
off disk, not a budget for weights that stay in system memory, so it cannot refuse a load whose weights will not
fit in RAM. A model that keeps part of its weights in host memory can still be started here, by four routes: the
expert-placement flag `--n-cpu-moe N` passed to `harness/serve_compose.sh start <gguf> <ctx>`, which hands
any argument after the window straight to the server; a profile's `A770B_<PROFILE>_EXTRA` set in the environment,
which wins over the value the registry supplies; `harness/ladder.sh <gguf> --ctx N --extra "--n-cpu-moe N"` on a
bare model file, the path for a model with no row; and serving one of the mixture-of-experts files the ledger
records. Such a server holds gigabytes of system memory beyond what it holds on the card. On a machine whose swap
is committed the kernel kills it under memory pressure, and the kill reads as a fault of the seat rather than of
the machine, so check free memory first and keep a build or another memory-heavy process off the machine while it
runs.

The ladder records host conditions under `conditions` in its results JSON: the configured MemAvailable floor, VRAM
cap and card mode; continuous timestamped `/proc/meminfo` RAM and swap samples across every rung; and the serving
process's cgroup v2 limits and sampled charge when its PID is known. The collector's cgroup is kept separate and is
never reported as the server's limit. In a kit run of a managed server, `A770B_SERVING_PID` is a manual pointer to
the real server process inside the selected container (for a server below a container init). It never replaces the
selected container's identity: it is sampled only when it is that container's main process or one of its
descendants, and matches the engine. Any other value is refused with a warning, and no serving process is
sampled while it is set. With `--base-url` it is ignored; the endpoint's listener is sampled. A cgroup charge can
include sibling processes in the same serving cgroup.
Strata's configured host-memory flags are recorded separately from observed host counters. These counters do not
derive the model's RAM requirement or replace `ram_gb_extra`, which comes from the engine's own logs. The full JSONL
receipt is named in the results JSON. The printed `measured_on` placeholder carries a concise summary to copy.

**The key.** The server starts with `--api-key-file A770B_API_KEY_FILE` (default `~/.config/a770-builder/api.key`). The
file is created on the first serve, one line, mode 600; the rendered profile carries the key into the sandbox, and every
harness script that talks to the server reads it from the file. Delete the file to rotate: the next `serve` creates a
new key, notices the running server no longer accepts it, and restarts the server.

**Hidden tests.** `A770B_HIDDEN_ROOT` (default `$A770B_DATA/hidden`) is the only place a specification's
`verify.hidden` names may resolve to: plain basenames, regular files, at most 64 KiB each. They are copied into the seat
as `tests/_hidden_<name>` after the patch applies, refused if the patched seat already has that path, run with the rest,
and removed by the reset. A hidden test guards against a model that wrote tests to its own reading of the brief; it
does not guard against a hostile patch, which can print it into the verify file.

**Other services on the host.** `A770B_FRAMEWORK_PORTS` lists ports the model server must never bind. `A770B_HEALTH_URL`,
when set, is read with one GET before a server starts, and the gate prints the status word the answer carries, "no
answer", or "answered, no status field", as information: another service's health says nothing about this host or this card, so the built-in gate never
refuses on it. Both knobs are empty by default and neither is written to. Anyone who wants a hard dependency on another
service has the external gate: a script named by `A770B_BUDGET_GATE` replaces the built-in gate entirely. `status`
reports whether the run lock is held and whether the seat is clean, so two sessions sharing a seat see each other before
a run; the convention is one seat per consuming project, and harness work on a clone of its own.

## Getting llama.cpp and the models

**llama.cpp with the Vulkan backend.** The seat was measured on build b10805. Build instructions live in the llama.cpp
repository, [`docs/build.md`, section *Vulkan*](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan):
install the Vulkan SDK or your distribution's `vulkan-headers`/`vulkan-loader` and `glslc`, then

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp && cd ~/llama.cpp
cmake -B build -DGGML_VULKAN=ON && cmake --build build --config Release -j
./build/bin/llama-server --list-devices        # the builder card's name goes into A770B_DEVICE
```

`A770B_LLAMA_BIN` is unused now: the server runs in a container, and the host llama.cpp build was removed.

**The kit's own toolchain.** `harness/run_suite.sh` needs nothing installed beyond what a normal development host
already has on its path: `node` (the system one; a version manager an interactive shell layers on top of it is not
what the sandbox sees), `cmake`, `make`, `g++` and `python` with the warm uv cache (`harness/warm_cache.sh`
pre-fills `pytest` and `pytest-asyncio`). No package is installed at run time or at grading time, and no network is
used. `local-build.sh doctor` checks that the exact offline pytest command resolves and that the configured
OpenCode binary is executable; `tests/kit_selftest.sh` checks the sandboxed build toolchain before a row is trusted.

**The models.** These are the exact files each row below was measured with; a different quantisation of the same
model is a different row, and the registry's own `model` and `source` fields (`config/registry/<card>.<mode>.json`)
are the authority when this list and they differ. A GGUF quant label is the
weight encoding (`checkpoint.weight_quant`); activations stay in the engine's working precision unless the
checkpoint itself quantises them. Another user's encoding of the same model (GPTQ, AWQ, a different GGUF) is a
different checkpoint and may need a different kernel on this card. Every profiled row's file is a
public GGUF on Hugging Face; the harness reads them from `A770B_MODELS`. Fetch them with the Hugging Face CLI (no
account needed for these), straight into that directory, one line per row of the registry it belongs to, in
registry order:

```bash
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested                # display: --profile qwen35-9b-q4km-vulkan (5.6 GB)
uvx --from huggingface_hub hf download lmstudio-community/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir ~/LLM/tested        # display: --profile gemma4-8b-e4b-q4km-vulkan (5.3 GB)
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf --local-dir ~/LLM/tested     # display: --profile qwen38-27b-iq3xxs-vulkan (10.1 GB)
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested                # inference: --profile qwen35-9b-q4km-vulkan (5.6 GB; the same file as display's qwen35-9b-q4km-vulkan)
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf --local-dir ~/LLM/tested       # inference: --profile qwen38-27b-iq3s-vulkan (11.8 GB)
```

llama.cpp can also fetch a model itself: `llama-server -hf lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M` downloads into
`~/.cache/llama.cpp/` and serves it. The harness starts the server with `-m <file>`, so if you go that way set
the card's `A770B_<CARD>_MODEL` (e.g. `A770B_QWEN35_9B_Q4KM_VULKAN_MODEL`) to the absolute path of the cached file rather than moving it.

## Models — where to put them, how the skill reaches them

1. Put the GGUFs in `A770B_MODELS` (default `~/LLM/tested`); keep a README there saying why each earned its place.
2. Name the profiles' files with each card's stem, `A770B_<CARD>_MODEL` (e.g. `A770B_QWEN35_9B_Q4KM_VULKAN_MODEL`): a bare
   name is looked up in `A770B_MODELS`, an absolute path works. Set the context and KV type per card (`A770B_<CARD>_CTX`,
   `A770B_<CARD>_KV`); reasoning models take `A770B_<CARD>_REASONING=on` plus their sampling line in `A770B_<CARD>_EXTRA`.
3. The llama-server process on the host reads the weights and answers on `A770B_HOST:A770B_PORT` under the alias
   `local-builder`. The model's own process runs inside the sandbox and never sees `A770B_MODELS`; it only talks to the
   server over the bridge. So the weights can live anywhere the server can read, including a read-only share.
4. Before trusting a new model, qualify it (next section). The ledger of every model measured, on every card, with its
   numbers and its profile, is [`config/models.md`](../config/models.md).

## Engine, checkpoint, kernel — what the logs say

A slow run is often the **wrong file** or the **wrong backend**, not the wrong model. Read the engine log before
you drop a family. Generic strings from NVIDIA write-ups (`FlashInfer`, `CUTLASS`, `Marlin`, `falling back to CPU`)
are not what these containers print. The lines below are what llama.cpp and vLLM actually emit on Intel Arc
(A770/Xe1 and B70/Xe2). `kit/PROFILE.md` §2 `kernel.evidence` is where a measured line is transcribed onto a row;
omit the field until you have one.

`capture_task.sh` currently keeps timings and `n_gen` only. The banners live on **llama-bench stderr** and in
`docker logs` of the serve container. Grep those, not the capture.

### llama.cpp — which backend, which card

Cheap, no model load (one-shot in the same image as serve; `serve_compose.sh bench` is llama-bench, not this):

```bash
docker compose --env-file compose/a770-vulkan.env -f compose/a770-vulkan.yaml \
  run --rm --no-deps --entrypoint /app/llama-cli llama --list-devices
docker compose --env-file compose/a770-sycl.env -f compose/a770-sycl.yaml \
  run --rm --no-deps --entrypoint /app/llama-cli llama --list-devices
# llama-bench prints the capability banner even when it then fails to find a default GGUF:
docker compose --env-file compose/a770-vulkan.env -f compose/a770-vulkan.yaml \
  run --rm --no-deps --entrypoint /app/llama-bench llama
```

Measured on this workstation's B70 (2026-09-22, no weights loaded):

```
Available devices:
  Vulkan0: Intel(R) Graphics (BMG G31) (32656 MiB, … free)
  SYCL0:   Intel(R) Arc(TM) Pro B70 Graphics (32656 MiB, … free)
```

On the A770 the same flag named `Vulkan0` / the DG2 part. If this list is empty or names the desktop card, stop —
the envelope's DRM nodes are wrong.

llama-bench (not llama-server at verbosity 3) prints the **device capability** line. Vulkan, B70, same day:

```
ggml_vulkan: Found 1 Vulkan devices:
ggml_vulkan: 0 = Intel(R) Graphics (BMG G31) (Intel open-source Mesa driver) | uma: 0 | fp16: 1 | bf16: 1 | fp4: 0 | warp size: 32 | shared memory: 49152 | int dot: 1 | matrix cores: none
load_backend: loaded Vulkan backend from /app/libggml-vulkan.so
load_backend: loaded CPU backend from /app/libggml-cpu-haswell.so
```

A770 Vulkan is the same shape with `A770 Graphics (DG2)` and also `matrix cores: none` / `int dot: 1`. NVIDIA
boxes log `matrix cores: NV_coopmat2` here; we never have. `int dot: 1` is DP4a **present**, not "this GGUF used
DP4a". `fp4: 0` means the device does not expose native fp4 — a GGUF `Q4_*` is still weight-only.

SYCL prints **no** `matrix cores:` line. What we get:

```
load_backend: loaded SYCL backend from /app/libggml-sycl.so
load_backend: loaded CPU backend from /app/libggml-cpu-haswell.so
```

The CPU backend **always** loads. That is not CPU decode. llama-bench JSON is the SYCL proof the GPU ran:
`backends: "SYCL"`, `gpu_info: "Intel(R) Arc(TM) Pro B70 Graphics"`, `n_gpu_layers: 99`,
`model_type: "qwen35 27B Q6_K"` (the **weight** encoding of this file).

llama-server at the harness default (`verbosity = 3`) does **not** repeat the `ggml_vulkan:` banner. It does print
`model has unused tensor …` (this **file** has heads the engine is not using — Qwen `blk.64.nextn.*` is MTP sitting
idle) and `eval time = … tokens per second`. Do not wait for `offloading all layers to GPU` or
`Warning: falling back to CPU`: those strings are not in our logs. Partial offload shows up as `offloaded N/M
layers to GPU` with N < M in the server log, as `n_cpu_moe` on a MoE row, and as VRAM that does not match the
file size — not as `ram_gb_extra`, which is the engine's own host buffers (the log's `*_Host … buffer size` lines,
at `-lv 4`) and stays small even at full offload.

`GGML_VK_DISABLE_COOPMAT=1` is still the harness default (A770 freeze). Mesa already reports `matrix cores: none`
on both A770 and B70, so unsetting the flag is not a promised XMX path.

### vLLM — kernel name is in the log (Xe2 / B70 only)

vLLM's Intel XPU line does **not** run on the A770 (DG2 / Xe1). On the B70 it **does** name the kernel. Hunt:

| Line | Meaning here |
|---|---|
| `Using XPUwNa16LinearKernel` / `int4_gemm_w4a16` | XPU W4A16 kernel for **this** checkpoint |
| `Using gptq_gemm` / AutoRound W4A16 | the Intel recipe that actually ran (~24 t/s at 100k on the B70) |
| `Please switch to gptq_marlin` | CUDA-centric warning. Marlin is NVIDIA-only. Do **not** chase that flag or a Marlin file |
| `gptq_marlin_repack` / missing `vllm._C` | this checkpoint selected a CUDA kernel that is not on XPU — wrong file or wrong `--quantization` |
| `Failed to import from vllm._C` | expected on the XPU image (no CUDA `_C`) |
| GGUF | closed on XPU. A slow GGUF is not a vLLM candidate |

vLLM will tell you W4A16 vs a missing kernel. llama.cpp GGUF will not. "Download W4A4 because of a native-support
warning" is NVIDIA advice; on this card AutoRound **W4A16** is the working vLLM checkpoint, not a consolation prize.

### The performance signal (both engines)

llama.cpp does not log shader names (`mul_mat_vec_q6_k.comp`, …) at our verbosity. The honest kernel miss is
**decode in a CPU-like band while VRAM is occupied**. Same Qwen3.8-27B UD-Q6_K GGUF on this B70: Vulkan ~5.45 t/s
at 893 tokens with ~23 GiB used; SYCL 21.2 t/s after load. That is not `useful_ctx` (a slow
kernel is not the useful-window rule).

A CPU-only A/B (`llama-server -ngl 0`, or vLLM `--device cpu` if the image has it) is decisive on a **small**
file. Do not `-ngl 0` a 21 GiB 27B on this host as a screening tool. Prefer: same file Vulkan vs SYCL, then
another user's encoding of the same Hugging Face id (GPTQ/AWQ/AutoRound) on the engine that has a kernel.

Prefill vs decode are already in `slot print_timing` / llama-bench `pp`/`tg`. Do not copy A770 9B numbers
(~37–44 t/s) onto a 27B, or NVIDIA ms/token bands onto this card.

### What to do when it is slow

1. Confirm the device list and the banner (`Vulkan0`/`SYCL0`, `ggml_vulkan:` / `load_backend: loaded SYCL`).
2. Confirm the weights are on the GPU (VRAM after load, `n_gpu_layers`, `offloaded N/M layers to GPU` in the log
   — N < M is partial offload; `ram_gb_extra` is the engine's own host buffers, not a placement signal).
3. Same GGUF, other llama.cpp backend — if they disagree, it is the **kernel/backend**, not the model.
4. Other checkpoint of the same model on the engine that named a kernel (vLLM AutoRound W4A16 vs GGUF K-quant).
5. Only then spend a ladder, or write the family off. IQ2 vs IQ3 LiveCodeBench is a real **file-quality** cliff;
   Vulkan 5 t/s on Q6_K is not.

## Qualifying a new model

Models keep being released, and a new version of a family is a new model: its window, its speed on this card, its
sampling line and its behaviour on a real brief are all unmeasured until the harness has run it. The seat is built so
that a new model enters by measurement, in one sitting, through one command:

```
harness/ladder.sh <profile-or-gguf> [--ctx N] [--kv f16|q8_0|q4_0] [--kv-v f16|q8_0|q4_0] [--extra "<flags>"]
                  [--timeout S] [--seat <path>] [--suite <suite.json>] [--reviewer <profile>] [--fresh] [--dry-run]
```

`harness/ladder.sh` is the sitting: it runs every rung below, in order, stopping at the first failure (a later rung
is wasted on a model that failed an earlier one), writes one JSON holding every rung's own numbers, and prints a
REGISTRY ROW ready to paste under `profiles.<name>`. `<profile-or-gguf>` is either a name already in the registry
(every knob comes from its row; `--ctx`/`--kv`/`--kv-v`/`--extra` alongside one is refused) or a bare GGUF/absolute
path — a model with no row yet, which needs `--ctx` and skips only rung 3 (the speed rung runs only when given a
registry profile name; a bare GGUF has no row yet). `--dry-run` prints every rung's command
and writes nothing. `AGENTS.md`, *The ladder*, is the full reference for every flag, what each rung decides, and
what the ladder still leaves to a human (`use_for`, `fit.write`, `capability`, the reviewer choice, and every
identity field).

1. **Fetch the GGUF** into `A770B_MODELS` (the download lines above are the pattern). The ladder's task rung (6) is
   now the kit inside this repository (`kit/`): put a clone of this repository at the release's tag where
   `A770B_PROJECT` points — rung 6 runs `harness/run_suite.sh <profile>` against it, no second repository, no brief
   of your own to write. `A770B_CORPUS_FILE` (`kit/corpus.py`'s own generated file) is what the sweep and the depth
   probe read by default. A row measured on the kit at a different release's tag, or against a revised suite
   version, is a different instrument, comparable only against other rows measured on that same one. To reproduce
   one of the rows measured before this release instead — comparable only with each other, never with a kit row —
   pin the seat the way they were pinned: `git clone https://github.com/KanenasInGreece/Shared_Memory ~/local-ai/seat
   && git -C ~/local-ai/seat checkout 3c8e2bb`, with `A770B_PROBE_CORPUS` and `A770B_TASK_BRIEF` pointing the sweep,
   the depth probe and the qualification task at that seat and `briefs/T1-sanitize-entity-tests.md`, and pass
   `--seat ~/local-ai/seat` to the ladder.
2. **Run the ladder**: `bash harness/ladder.sh <profile-or-gguf> [--ctx <n>] [--kv …] [--kv-v …] [--extra …]`, with
   the model's own card's sampling flags folded into `--extra`. Rung by rung it runs what used to be six commands
   run by hand: **1-2** — `harness/bench_model.sh`, the load and its VRAM (`vram_gib_after_load`; `ram_gb_extra` —
   the engine's own host buffers, transcribed by hand from the server log's `*_Host … buffer size` lines at `-lv
   4`, not computed here — shows as `offloaded N/M layers to GPU` with N < M when offload is partial), the sanity
   probes and the 17k summary; **3** —
   `harness/bench_http.sh`, llama-benchy at prompt sizes 8192, 32768, 65536 and 100000 against the server left up
   (skipped for a bare GGUF); **4** — `harness/ctx_sweep.sh 8000 <far end>`, prefill, decode and VRAM
   against position, the measured time to first token a prefill limit reads; **5** — `harness/depth_probe.sh <far
   end>`, now graded PASS/FAIL per question, capping `useful_ctx` at 8,000 where it fails; **6** —
   `harness/run_suite.sh <profile>` (or `--model <gguf> --ctx <n> [--kv …] [--kv-v …] [--extra …] [--timeout …]` for
   a model with no row yet): starts the server under the mode's cap and `-ub 512`, exports `kit/seat/` fresh per
   stage with every earlier stage's reference solution already pasted in, dispatches each stage's brief through
   opencode in that export and `verify`s it against its hidden grader, scores the reviewer-graded axes through a
   separate profile that is never the builder, and writes `results/<profile>-suite-<date>.json` naming the row's
   instrument. Flash attention is a variable of the row, not a constant of the card: if the long prefill slows with
   position or the kernel logs an engine reset, run the ladder again with `--extra "-fa off"` and `--kv-v f16`
   before judging the model. Watch the kernel log beside every run (`journalctl -k`); a reset anywhere is
   disqualifying, whatever the other numbers say — the ladder itself does not stop the server for you on one.
   For a model meant to hold large files, rungs 3-5 already give speed at 8k and the far end and the depth probe for
   the model and window, both against `kit/corpus.py`'s own generated corpus by default; run the cancel reproduction
   once, by hand and outside the ladder (`harness/cancel_repro.sh`, a cancelled request must not take the card
   down), and `briefs/T2-read-a-large-file.md` as a second task if you want one beyond the suite. To reproduce a row
   on the earlier instrument instead of the ladder, `bash harness/run_one.sh <label> <file.gguf> <ctx> [extra
   llama-server args]` runs the probes and the single task against the pinned Shared Memory seat directly, refusing
   to continue if the sanity gate fails and writing its capture and numbers to `A770B_DATA/results/<label>.*`; the
   in-house suite (`briefs/suite/`, five bounded units of this repository with hidden graders, not the profiling
   suite) runs beside it for a builder-class candidate on that instrument.
3. **Grade the capture** with a reviewer that did not write it: `briefs/REVIEW-prompt.md` is the prompt, the capture is
   its only input. Run `local-build.sh verify <label>` for the proof. Green tests and PASS or PARTIAL qualify.
4. **Record it**: add the row to `config/models.md` with the measured numbers, the source repository and the caveats.
   A model that failed goes in the *kept out* paragraph with the reason, so nobody measures it twice.
5. **Give it a profile** if it earns one: paste the ladder's printed REGISTRY ROW under `profiles.<name>` in the
   card's own registry (`config/registry/<card>.<mode>.json` — a new card's first row creates that file, with its
   top-level `card` and `mode` keys), then fill
   what the ladder leaves as `__TODO__` or omits entirely — `use_for` in words, `fit.write`, `capability` and
   `capability_source` — and the identity fields its own closing note lists: `source`, `family`, `architecture`,
   `quant`, `category`, `weight_class`, `params_b`, `sampling` and its source, `measured_on` (the card and build:
   e.g. `Arc A770 16 GB, llama.cpp b10805 Vulkan`), and, for a row reproducing the sibling-repository instrument,
   `task_t1` — transcribed once by hand from the GGUF's own metadata and the model's public card (`kit/PROFILE.md`
   §2 is the field-by-field reference for what fills every field). Optional beside those: `engine`, `checkpoint`
   (format / weight_quant / activation_quant; a GGUF K-quant is weights-only, `activation_quant: none`) and
   `kernel` (omit until the GPU vs CPU-like path is observed). Omitted means untested, not "the same as `quant`". `local-build.sh profiles` and `status` show what
   is configured; `builder.<mode>.env` may still override a served value per machine (a different quantisation, a
   larger cap) without touching the registry. Run `python3 harness/profiles.py render --catalogue` so README and
   `config/models.md` pick the new row up, update the numbers in this file, and bump the version.

The expectations in the skill's tables (window, speed, minutes per small task, what the model did with the
repository's idioms) are read straight off the ledger row; when the row changes, so do they. The full guide for an
agent that has a GGUF and this harness and wants a model on the list — what a profile is, the ladder as commands in
order, the bar, and the submission — is [`AGENTS.md`](../AGENTS.md) at the repository root.

## What is hardcoded now

Linux. The sandbox is bubblewrap, so the harness needs a kernel with unprivileged user namespaces; the VRAM readings
come from `nvtop` and the reset watch from `journalctl -k`; the serving line was measured on Mesa's Vulkan driver over
the Xe kernel driver, and the display-card rules are that driver's watchdog. Windows is not a target: the plausible
shape is the harness under WSL2 with `llama-server` native on Windows, reached over the loopback bridge, with
`A770B_ALLOW_NO_NVTOP=1` because the cap cannot read the card from inside WSL2; it has not been measured and the README
says so. macOS has no bubblewrap.

Two pieces are Vulkan-specific, both harmless on another Vulkan card and simply unused on a non-Vulkan backend:
`MESA_VK_DEVICE_SELECT` (the pin Mesa's Vulkan loader reads) and `GGML_VK_DISABLE_COOPMAT` (the flag set proven on
Arc under Vulkan). The `-ub 512` ceiling is this card's own watchdog rationale — Intel's Xe kernel driver resets the
GPU past it on a display card; another card's reason for a batch ceiling, if any, is its own and needs its own
measurement. The reset watch reads the kernel log for a configurable pattern, `A770B_RESET_PATTERN`
(`harness/guard.sh`'s `kernel_resets_since`), Intel's Xe driver wording (`engine reset|timedout`) by default;
another driver words a reset differently, and its own user sets `A770B_RESET_PATTERN` in `builder.env` to match it
(`config/builder.env.example` says how to find the wording) — nobody has yet, so every reading in this document is
still against the Xe driver's own words.

Beyond that, nothing that matters. Two conventions remain: the opencode alias `local-builder` (the profile template depends on it) and
the sandbox's use of `bubblewrap`, `socat`, `uv` and the opencode binary from `A770B_OPENCODE_BIN`.

## An already-running SGLang server

`bench_http.sh`, `prompt_budget.py fit`, and `depth_probe.sh` can measure a server that is already up. Pass its base URL. fit counts from the tokenizer directory and does not post /tokenize. Health for that URL is any successful `GET /health`. The tokenizer is a model directory or a Hugging Face id, not the served name.

The skill starts SGLang from the profile's tool_parser and reasoning_parser and the compose/b70-sglang.yaml envelope, and still does not package the image. An sglang ladder starts from `compose/b70-sglang.yaml` and takes speed from llama-benchy.

```text
bash harness/bench_http.sh --base-url http://127.0.0.1:PORT --served-model-name NAME --tokenizer /path/to/model --dry-run
python3 harness/prompt_budget.py fit --corpus "$A770B_CORPUS_FILE" --target 8192 --out prompt.txt --url http://127.0.0.1:PORT --tokenize openai --model NAME --tokenizer /path/to/model
bash harness/depth_probe.sh 8192 --base-url http://127.0.0.1:PORT --tokenize openai --model NAME --tokenizer /path/to/model
```

## Inkling 2 Mini on Arc Pro B70

Use the [ggml-org IQ4_NL GGUF](https://huggingface.co/ggml-org/Inkling-2-Mini-GGUF/tree/db61c5b492a6a034d986328d28274af0a439aa6c)
and the [model tokenizer](https://huggingface.co/thinkingmachines/Inkling-2-Mini/tree/ed40e7da625f594fc9c4990f5f66be24873c22d7).
The tested Vulkan build was llama.cpp b12602 at commit `418fe97b5`. Its amd64 image was
`ghcr.io/ggml-org/llama.cpp@sha256:b5e3a86d6b52792f8845fcd03366b5e04bd6460c7c936660365ca3fc835f930c`.
The trial used a derivative with the compromised certificates removed from the vendored pip bundle.
The container trust scan stayed enabled. Its server wrapper unsets `GGML_VK_DISABLE_COOPMAT`
before executing the original server so the setting applies only to this test image.

To reproduce the derivative, save this as `Dockerfile.inkling` and build it with the
repository root as the build context. The repository trust scan still checks the full
resulting image when the harness starts it.

```dockerfile
FROM ghcr.io/ggml-org/llama.cpp@sha256:b5e3a86d6b52792f8845fcd03366b5e04bd6460c7c936660365ca3fc835f930c
USER root
COPY compose/ca-trust-check.sh /tmp/ca-trust-check.sh
RUN set -eu; . /tmp/ca-trust-check.sh; \
    pem=/usr/lib/python3/dist-packages/pip/_vendor/certifi/cacert.pem; \
    ca_strip_refused "$pem" /tmp/clean-ca.pem; \
    cat /tmp/clean-ca.pem > "$pem"; \
    rm -f /tmp/clean-ca.pem /tmp/ca-trust-check.sh
RUN mv /app/llama-server /app/llama-server-real; \
    printf '%s\n' '#!/bin/sh' 'unset GGML_VK_DISABLE_COOPMAT' \
      'exec /app/llama-server-real "$@"' > /app/llama-server; \
    chmod 755 /app/llama-server
```

```bash
docker build --network none -f Dockerfile.inkling -t inkling-vulkan:b12602 .
```

Put the GGUF in the model directory and put the pinned `tokenizer.json` in a dedicated
directory. Set `A770B_TOKENIZER_DIR` for the benchmark and ladder to use that tokenizer.
Point `A770B_LLAMA_IMAGE` at the prepared image and select the B70 inference registry.
Run the named profile with the skill.

```bash
export A770B_CARD=b70 A770B_CARD_MODE=inference
export A770B_LLAMA_IMAGE=inkling-vulkan:b12602
export A770B_TOKENIZER_DIR=/path/to/inkling-tokenizer
export A770B_PROFILES_FILE="$PWD/config/registry/b70.inference.json"
bash skills/local-build/scripts/local-build.sh serve inkling2-mini-iq4nl-vulkan
```

The profile allocates 65,536 tokens with q8_0 KV and a 2,048-token reasoning budget.
The published vLLM recipe validates the looped architecture on NVIDIA hardware.
The installed Intel XPU vLLM image tested here did not register that architecture.

## Managed Strata

The native path runs the pinned [Strata v0.2.17 terminal
source](https://github.com/Niko1221/Strata/tree/12d491da4a6904eb49aaa6f2f63ec4b1a17582e3/terminal)
with an operator-prepared Python runtime and Intel engine. Prepare the executable, its runtime libraries,
the model pack and shards, the tokenizer, and the JSON run config before using the harness. Start uses
`python -s -m serve.server --engine strata --offline` from the pinned terminal checkout. The harness
records the source revision and SHA-256 of the engine, tokenizer and config beside separate server and
engine logs. It gives the launched terminal its own settings, profiles and state directories and
disables saved presets and acquisition. Record the model artifacts and checkpoint or revision with the
measurement notes.

The run config supplies `backend` (`intel` or `sycl`), `exe`, `cwd`, `tokenizer`, `model_name` and
an engine `args` array. Use absolute paths for the executable, working directory, and tokenizer. The
tokenizer directory needs both `vocab.json` for Strata and `tokenizer.json` for harness probes. Place
context, KV, reserve and host-memory parameters in the config. Keep replicas, layer splitting, vision,
MCP, and before-load commands disabled. The pinned Intel guide calls for `--spec 2` or higher with native
IQ packs. The managed path preserves that setting. Pin each separate draft asset by absolute path and SHA-256 in `draft_asset_sha256`.
This covers `--draft-model`, `--mtp-draft-vocab` and `--mtp`. For an MTP directory, pin its
`draft_vocab.bin`. The `dflash2` mode requires `--draft-model`.

Set the measured card-mode cap and host `MemAvailable` floor in the builder environment. The native path
matches the card in `config/cards.json` to both the unique nvtop reading and the selected Level Zero PCI ID.
Override `A770B_ONEAPI_DEVICE_SELECTOR` to that single indexed device and clear ambient
Level Zero, Unified Runtime and SYCL device filters. Match the selector in the config if it sets one. On the B70, nvtop reports G31 and
Level Zero reports PCI ID `8086:e223`. The budget gate applies before loading. The harness checks the VRAM cap after loading and on reuse. The owner checks the
host memory floor during serving. Use a Linux memory scope when the trial needs a hard per-process limit.

```bash
export A770B_STRATA_ROOT=/path/to/clean/Strata-v0.2.17
export A770B_STRATA_PYTHON=/path/to/prepared/venv/bin/python
export A770B_STRATA_ENGINE_SHA256=ENGINE_SHA256
export A770B_STRATA_CONFIG=/path/to/prepared/strata.json
export A770B_STRATA_CTX=32768
export A770B_HOST=127.0.0.1
export A770B_GPU_MATCH=G31
export A770B_ONEAPI_DEVICE_SELECTOR=level_zero:1  # use the index verified on this host

bash skills/local-build/scripts/local-build.sh serve strata-candidate
bash skills/local-build/scripts/local-build.sh status
bash skills/local-build/scripts/local-build.sh bench strata-candidate --pp 8192
bash skills/local-build/scripts/local-build.sh run /path/to/brief.md --profile strata-candidate
bash skills/local-build/scripts/local-build.sh stop
bash harness/ladder.sh strata-candidate
```

`strata-candidate` is an environment-only candidate. A measured registry profile uses `engine: strata`,
`backend: strata` and a `model` path to the prepared config. Strata identifies the served model by
`model_name` in the config. The ladder loads once, uses the same HTTP benchmark, context and depth
tools as other engines, then runs the ordinary kit path. Stop an existing builder server before starting
the ladder. The ladder cleans up its own invocation and refuses `--reviewer`, which would switch
servers between stages. The start checks readiness, loaded identity, context, authentication and a
launch attestation. It reuses a matching owned launch. Stop uses the recorded supervisor identity and
the pinned terminal's owner guard to close its server and engine.

### Arc Pro B70 and Qwen3.8-Flash-Next IQ3_S

Use the [Intel Arc guide](https://github.com/Niko1221/Strata/blob/v0.2.17/docs/INTEL_ARC.md)
and the [ISTA-DASLab IQ3_S checkpoint](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/ed59f92082b1e93c0e96d60a8b11aab089b52f09/IQ3_S).
The two GGUF shards occupy 77.9 GiB on disk. Place them on a local SSD and prepare
the native pack with the pinned Terminal. The measured run used the signed
SYCL engine 0.1.63 and the `expert-profile-sycl.bin` from that Terminal revision.

The run config uses the transformer GGUF for `--native`, the per-layer embedding GGUF for
`--ple-gguf`, and the prepared directory for `--pack`. Pass the profile above as `--expert-profile`.
The other measured arguments are
`--expert-cache auto --stream-experts --spec 2 --prefill 4096 --max-context 32768 --kv int8 --ple-io mmap --vram-reserve-mib 5120`.
Set `parallel` to 1 in the JSON. The config's engine environment uses
`STRATA_VERIFY_DEVICE_PLAN=1`, `STRATA_VERIFY_NO_HOST=1`, and
`STRATA_MIRROR_MIB=28672`. Use the single indexed Level Zero selector verified
for your host. Keep the existing 28 GiB card cap and a 10 GiB host
`MemAvailable` floor (`A770B_MIN_AVAIL_MB=10240`). The trial ran in a systemd user scope with
`MemoryMax=36G` and `MemorySwapMax=0`.

The engine reported 10,087 resident experts and 14,489 experts mirrored in
27.70 GiB of pinned host RAM. The measured load read 25.64 GiB of GPU memory at 32,768 served. 
The native suffix draft path used `--spec 2` without an external draft head.
Three llama-benchy trials averaged 1,337 tok/s prefill and 28.7 tok/s decode at 8,192
prompt tokens and 128 generated tokens. The context sweep reached 31,632 input tokens at
33.5 tok/s decode, 18.9 seconds to first token and zero GPU resets. The 31,522-token
depth probe passed at 2/3.

For the named B70 profile, place the prepared JSON at
`$A770B_MODELS/Qwen3.8-Flash-Next-IQ3_S.strata.json` and use
`local-build.sh serve qwen38-flash-next-iq3s-strata`.

## An externally managed Strata candidate

- **Identity is explicit.** Pass `--engine strata` next to `--base-url` and `--served-model-name`. The harness never
  decides a server is Strata, llama.cpp or vLLM from compatible metrics. The vLLM, SGLang and llama.cpp attach paths
  keep their own contract unchanged.
- **Pinned contract (Strata v0.2.16).** `strata_attach.py attach` reads `/health` (`service` must be `"strata"`,
  `loaded` true, `model`, a positive `max_context`) and `/v1/models`: the requested id must be one row, itself or
  through `alias_of`, whose `status.value` is `loaded`, whose `architecture.output_modalities` names `text` (an image
  row names `["image"]` and is refused), and whose `meta.n_ctx` is positive. `/health` must have that model (or its
  alias target) loaded. Both answers need a successful HTTP status; a valid-looking body on an error status is
  refused. The caller's `--ctx` is refused when it exceeds the usable window, the smaller of `max_context` and
  `meta.n_ctx`. The check sends GETs only.
- **Loopback is `127.0.0.1`.** `localhost`, `[::1]` and any other host are refused before any request: the sandbox
  listener, the rendered client URL and the usage tap's upstream all use this one spelling. The checks use Python's
  `http.client`, so environment proxies and curl configuration never apply.
- **Auth.** The skill and kit first request `/v1/models` anonymously. They send the key from `A770B_API_KEY_FILE`
  only if the confirmed Strata endpoint requires authentication. For direct checks, `strata_attach.py attach` and
  `probe` also accept `--auth key` or `--auth none`. They read `/health` anonymously and keep credentials off their
  command lines. The existing profile renderer still receives `--apikey` in key mode. In unkeyed mode,
  OpenCode sends its placeholder bearer, which Strata v0.2.16 ignores when it has no configured key. The harness
  skips key-file creation and still applies the run specification and model ID.
- **Caller.** The run uses the existing sandboxed OpenCode OpenAI-compatible Chat Completions provider. Its bridge is
  `usage_tap.py`. The capture is labelled `engine: strata (attached)`. A stream that carries no usage is recorded as
  unmeasured, never as zero. The coordinator verified OpenCode 1.18.35 through the ordinary `run` and `verify`, inside
  Bubblewrap, against a deterministic fixture endpoint, and no key file was created. That is a fixture run, not a
  live Strata measurement. A run uses OpenCode 1.19.6. When `A770B_OPENCODE_BIN` is empty and `PATH` is not that release, the run installs it and does not grade the profile for the mismatch. An explicit directory is used as given. A binary that is not 1.x, or whose `run --help` has no `--dir`, is refused.
- **Probe before long prompts.** Run `strata_attach.py probe` once against the live endpoint. `--tokenizer DIR` and
  `--checkpoint REF` (for example `repo@revision`) are both required. The probe counts the local tokenizer first,
  through `prompt_budget.py`'s path, so that path's one `uv` re-exec for `tokenizers` happens before any traffic.
  It then runs the attach checks and, in key mode, checks that a request without the key is refused. A failure
  there ends the probe before any POST. Next comes the tool smoke. The tool call must carry its own id, name
  `read_file` on `NOTE.txt`, end with `tool_calls` and `[DONE]`, and report usage. The answer to the tool result
  must complete with `stop`, carry the probe's random marker, and report usage. The probe disconnects after it
  sees generation progress and records whether a fresh request returns a usable HTTP 200 completion. It can only
  account for stream events it has received. Backend cancellation and slot release need separate evidence. The probe also compares API
  `prompt_tokens` with the local count. The bare prompt may exceed it by up to `--count-slack`, and the tool
  definitions must add tokens. The evidence records the tokenizer's
  checkpoint and `tokenizer.json` SHA-256, the usage, and the response *shapes*. It keeps no returned text or tool
  arguments. Run `ctx_sweep.sh` / `depth_probe.sh` with `--tokenize openai --tokenizer DIR` only after the probe passes.
```text
python3 harness/strata_attach.py attach --base-url http://127.0.0.1:PORT/v1 --model NAME --ctx N [--auth auto|key|none]
python3 harness/strata_attach.py probe --base-url http://127.0.0.1:PORT/v1 --model NAME [--auth auto|key|none] \
  --tokenizer /path/to/checkpoint --checkpoint REPO@REV --evidence "$A770B_DATA/results/strata-probe.json"
bash harness/run_suite.sh --base-url http://127.0.0.1:PORT/v1 --served-model-name NAME --engine strata \
  --ctx N --stages s1-frontend
```

## What is in here

| path | role |
|---|---|
| `skills/` | the agent skills this project publishes for other coding agents to install; `local-build/` is the only one |
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (`run` with an optional `--spec`, `verify`, `reset`, `serve`, `status`, `stop`, `--version`, `check-update`), `CONSTITUTION_SNIPPET.md` (optional, agents add it to their own constitution) |
| `render_readme.sh` | generates the operator's local recap page, `README.html` — gitignored, never shipped; `README.md` is the document of record, written and reviewed first at every change, and the recap is regenerated from the repository's current state afterwards, never instead |
| `harness/` | the scripts that run and guard a profiling or build session: starting the model server, wrapping the coding agent in the sandbox, capturing what it did, and measuring how fast and how far it can go — every script here runs on the host, outside the sandbox |
| `harness/serve_compose.sh` | the container way a server starts (`A770B_SERVE=compose`, the only value): builds the same llama-server argv from the registry, writes it as a compose override, and recreates the container (`up -d --force-recreate`); enforces the VRAM cap and writes the live-backend sidecar on the host; `plan` prints the argv without touching docker, and `bench` runs a one-shot `/app/llama-bench` in the same image |
| `compose/a770-vulkan.yaml` | the container ENVELOPE for `A770B_SERVE=compose`: official llama.cpp `full-vulkan` image (not `server-vulkan`: that image has no `llama-bench`), the `/app/llama-server` entrypoint (the image's own is `tools.sh`, and compose appends `command:` to it), the two PCI DRM nodes, the host group ids, the MESA pin, the read-only GGUF and API-key mounts, and a loopback port. It bakes no model or context — the argv comes from the registry at serve time. A bare `docker compose up` is not the skill's start path (it skips the profile argv, the budget gate, the VRAM cap and the sidecar); `serve` recreates so a profile change takes effect. Take the server down before a bench one-shot. Passthrough unmeasured until a host brings it up. Pin the image by digest after the first pull; `doctor` warns, without failing, while the reference is still the floating tag. |
| `compose/vllm.yaml` | the container ENVELOPE for vLLM backend serving (`A770B_SERVED_BACKEND=vllm`): `intel/llm-scaler-vllm:0.26.0-b2` pinned by digest, binds internal port 8000 to `A770B_PORT`, engine memory fraction 0.9 of the card; sibling `compose/vllm.env.example` |
| `harness/build_local.sh` | dispatch a brief through opencode in the seat (never a live checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: path policy before the bind, then only the seat read-write, no credentials, no other checkout, no harness source, no network except the model server |
| `config/` | the project's configuration: the registries listing every qualified model and its measured numbers, the example environment file a new install copies, and the template opencode reads inside the sandbox |
| `config/cards.json` | physical capability registry for workstation cards (`b70`, `a770`, `b580`): PCI IDs, total VRAM, GPU match strings, generation, XMX native precision formats, and device selectors |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation; the card mode, the key and profile helpers |
| `config/registry/<card>.<mode>.json` | one registry file per card and mode, the single source of every profile's numbers for that card; `harness/profiles.py` validates, exports, inspects and renders them |
| `harness/render_profile.py` | renders the opencode profile from the template (`render`), checks a run specification against the seat (`check`) and prepares its context into the brief copy (`context`); the echo of what was rendered lands beside the profile; `tests/test_render_profile.py` proves it |
| `harness/guard.sh` | canonical seat guard, verified pids, the run lock, `safe_git`, the seat reset, the built-in budget gate |
| `harness/capture_task.sh` | diff + new files + the model's pytest line + server-side TTFT/TPOT distribution, the `.patch` for `verify`, then the seat reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost (testing only, run by the operator) |
| `harness/cancel_repro.sh` · `ctx_sweep.sh` · `depth_probe.sh` | the long-context qualification: a cancelled request while the slot is held; prefill, decode and VRAM against position; correct answers from 85% of the way into a large prompt (run by the operator against a server that is up) |
| `kit/` | the packaged tests run to profile each model, automated through `AGENTS.md`: `kit/seat/` (the exported working tree), `kit/tasks/` (each stage's brief and specification), `kit/hidden/` (each stage's one hidden pytest grader, and the reference solutions `run_suite.sh` pastes over a fresh export), `kit/reference/` (the three Aider polyglot exercises), `kit/corpus.py` (the deterministic long-context corpus), `kit/SUITE.md` · `kit/suite.json` (the standard suite in prose and as data; `kit_version` is the instrument number, not the product version), `kit/SOURCES.md` · `kit/NOTICE` (attribution) |
| `harness/run_suite.sh` | exports `kit/seat/` as its own standalone git repository, never a clone or checkout of this one, runs every stage of the standard suite and the reference exercises through the skill and `verify`, scores the reviewer-graded axes through a separate profile, and writes `results/<profile>-suite-<date>.json` |
| `harness/suite_report.py` | reads one of those results files and prints the instrument, a per-stage table, and the totals a profile's `suite` object takes (`--json` for just the totals, pasteable into the registry) |
| `tests/` | the tests that check the harness itself works, not the tests a profiled model is graded against |
| `tests/kit_selftest.sh` | proves the kit inside the real sandbox boundary, never on the host: the toolchain is found, every stage's shipped stub fails its own grader, every reference solution passes it, and every reference exercise's own public grader passes over its `.meta` solution; skips loudly without `bwrap` or the warm uv cache, so `tests/selftest.sh` stays machine-independent |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the key, the profile's window, a default-deny bash allow-list, the run specification's card, scope and additions, and a floor of deny rules rendered after every allow |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `docs/COMPARISON.md` | what this project solves next to a pre-built container of Intel GPU backends, where each one stops, and how the two fit together |
| `SECURITY.md` · `SANDBOX-PLAN.md` | the boundary as it stands; the problem, the plan and what was done |
| `VERSION` | the project's version; the installed skill carries the same number, and `local-build.sh --version` reports both, the release the checkout stands on, and warns when they differ; `check-update` asks GitHub for the latest release, only when asked, and works from a copy on a machine that has no checkout |
| `release.sh` | cuts a release: moves the number in its three places (`VERSION`, the skill's `SKILL_VERSION`, the tag) in one commit, pushes, publishes the GitHub Release from a notes file. The first release is 0.1.0; each one after adds one to the last number, and after ten of those small steps the middle number moves and the last number starts again. A major version still waits for the maintainer's word (`--major`). Re-running on a `HEAD` whose release is already published exits 0 without bumping (idempotent retry; a new bump requires a new commit on `main`); an incomplete release at `HEAD` resumes the same version |
| `LICENSE` | MIT |
| `tests/selftest.sh` | what the harness proves without the card: every script parses, the health line reads the four kinds of answer and strips a hostile one, the seat's dirty check hides nothing but the harness's own brief copies, a symlink is reported and never read, the capture survives a run that made no file. Run it after a harness edit, from a tree you have read; a builder's patch is proven by `verify`, never by running its tests on the host |
| `briefs/` | the written tasks handed to a model during profiling or a build: the brief template (`TEMPLATE.md`: named files, verbatim text in quoted blocks, a test command on named files, a stop condition), the qualification task (`T1-…`, a bounded edit), the reading task (`T2-…`, one large file whole, graded against `grep`), the cheap-reviewer prompt, the smoke brief, the in-house suite (`briefs/suite/`, five bounded units of this repository with hidden graders) |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a standalone clone of the target repository with no link to its live checkout), results (captures, patches, verify
files), logs (server, builds, the bridge, the rendered profiles) and the uv cache in `~/local-ai/`. Nothing here is a
linked worktree of another project.
