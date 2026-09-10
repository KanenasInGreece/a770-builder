# Operating the seat

a770-builder is a harness around a local GPU: it runs a coding model in a sandbox, measures models into profiles
with the packaged tests a reader can run on their own model, and presents the result to an LLM orchestrator as a
skill it calls. Developed and tested on a 16 GB Intel Arc A770.

This document is about running the seat day to day: how a run works, each card mode's profiles and what they were
measured at, every knob, where the models go, the card's constraints and what each file is. The README covers what
this is, why it exists, how it installs and its security state; [`SECURITY.md`](../SECURITY.md) covers the boundary.

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile long                     # long profile (default), on the default seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile long --spec <spec.json>  # with a run specification (below)
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --profile serious  # serious profile, on a named seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --profile fast                     # fast profile: the reader
bash harness/run_suite.sh <profile>                                                      # the standard suite (kit/), stage by stage, one results file
bash skills/local-build/scripts/local-build.sh verify <label>                            # the reviewer's proof
bash skills/local-build/scripts/local-build.sh reset                                     # a seat the skill refuses as dirty
bash skills/local-build/scripts/local-build.sh serve <profile> · profiles [--name <profile>] · menu · status · stop · stop-run · --version · check-update
```

A run that must be ended early is ended with `stop-run`: the harness records the pid of the run's `timeout` process while it lives, `stop-run` sends exactly that pid one TERM after checking it is a timeout of this harness, and the run then captures what the seat did and resets the seat as an expired timeout does. Never end a run by process name: `bwrap` is also every Flatpak application on the desktop.

`run` refuses anything but the root of a standalone clone that is not a live checkout, refuses a seat that is not clean
(ignored files included, so nothing from an earlier run can pass as this one's; `reset` clears it), takes the run lock, starts or
switches the server, sets the seat's `AGENTS.md` aside for the call and restores it under a trap, runs opencode inside the
sandbox with `< /dev/null`, captures the diff, the model's own pytest line and the timings without executing anything the
model wrote, writes the complete change as `~/local-ai/results/<label>.patch`, and resets the seat. Judge by the capture
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
card that also draws the desktop) and pure-inference (for a card that draws nothing). Each mode reads its own
registry, the single source of every profile's numbers — `config/profiles.json` for display,
`config/profiles.inference.json` for inference — and `local-build.sh profiles` (or `--name <profile>` for one) shows
what is actually configured on this machine for the mode it is running in, `--served` layered over the environment.
Numbers in the tables below are properties of this card, build and quantisation, not of the models in general,
measured with llama.cpp b10805 and the Vulkan backend, on the qualification task.

A `builder.env` written for a release before the registry may name `A770B_FAST_*` or `A770B_LONG_*` for the other
profile's file: the environment wins over the registry, so such a file inverts fast and long silently. `local-build.sh
doctor` reports it, and `local-build.sh profiles` shows what is actually served beside what the registry says.

**Display-safe** (the default): measured on this A770 (16 GB, also driving the desktop).

| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| long (default) | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB | 36.8 / 571 tok/s | The default: every ordinary change, tests from a specification, and a read up to about 64k. Reads exactly at 100k but takes eleven minutes to get there. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| fast | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB | 60 / 796 tok/s | The fast reader: a large file read cold in about four and a half minutes at 100k and precise questions about a passage deep in it. Not the profile for edits. Measured with no sampling line set, at the client's default temperature (0), before this registry carried one -- not the card's own recommended line. |
| serious | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB | 8.1 / 72 tok/s | A deliverable larger than its brief, tests written from an unfamiliar module, a change touching several files. Ten to twenty-five minutes; decode under five tokens a second by 64k, so point it at files that fit 32k. A measured card may run the IQ3_S file at a larger window through builder.env. |

This workstation's own `builder.display.env` runs `serious` on the quantiser's task-lossless file instead, IQ3_S, at
114,688 under a 14.1 cap: 13.78 GiB after load, peaking at 14.21 GiB during a 32k prompt, ~9 minutes (five tests,
542 s); `local-build.sh profiles --served` shows it in place of the registry's row.

The Qwen windows are swept with `harness/ctx_sweep.sh`, prefill and decode against position with VRAM sampled and the
kernel log watched. The long profile serves its native 262,144 tokens and holds a 100k prompt with no reset and VRAM
flat within 0.3 GiB of its load; by the four-tokens-a-second rule (below) its useful window is 65,536 tokens, this
row's own registry `useful_ctx`. The three 27B files share one speed profile, decode being compute-bound on this
card whatever the quantisation: about 8 tokens a second at 8k, 6 at 32k and under four tokens a second by 64k, so
the serious profile's useful window is 32,768 whether it serves 131,072 or 114,688, and a brief for it points at
files that fit that.

The fast profile exists for the read, not the edit: files the long window cannot hold, and the "read this whole thing
and tell me" step before a brief is written. Its useful depth is about 100k tokens: at that depth it answered a probe's
planted detail exactly, while its broad recall of "three other functions" blended real names into ones that do not
exist; at 120k it misread a number. Ask it precise questions about a passage, not to recall the file: asked to index every definition of a 6,300-line
file it paged through 60% and got 34 of 40 names right and 19 lines; the long profile paged through all of it in
15 minutes and listed constants instead of definitions. Neither seat indexes a large file. Judge what it reports the
way every capture is judged. It is not the profile for multi-file shell edits: on the harness's own brief it made one
edit of three and reported all three done. Flash attention off is its condition on this card, and with it off the V
cache is f16.

The window is the server's. opencode's opening request costs about 5.4k tokens with the seat's `AGENTS.md` set aside
(the skill does this for the run and restores it after); with it in place the opening request was about 32k tokens, which
is why it is set aside. Every file the model reads lands in that window, and a 17k-token prefill costs the long profile
37 s and the serious profile 4 min. Keep briefs pointed at files, not directories.

**Pure-inference**: measured on the same card with nothing else on it.

| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| long (default) | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~262k) | 9.49 GiB | 43.7 / 439 tok/s | The default: every ordinary change, tests from a specification, and a read up to its whole window at above five tokens a second; the depth probe is exact at 100k. |
| serious | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 196,608 (~98k) | 14.62 GiB | 7.9 / 71 tok/s | A deliverable larger than its brief, tests from an unfamiliar module, a change touching several files: the best-written output here, at eight tokens a second; useful to about 98k by the four-tokens-a-second rule, so a long read costs minutes per 10k tokens. |

`useful_ctx` is the largest depth at which decode stays above four tokens a second, taken from the two measured points
(8k and the far end of the window) by extending the time per token linearly, capped at the depth probe's last
passing depth where one failed — not a window swept token by token. This is the one definition of `useful_ctx` in
this project; `harness/ladder.sh` (`AGENTS.md`, *The ladder*) computes it exactly this way. Beyond the table,
`local-build.sh profiles` (`harness/profiles.py card`)
carries the full card for each row: `speed` at 8k and the far end (decode, prefill, time to first token); `fit`, three
short strings with their source — `code` (the in-house suite or T1, tests passed and wall), `think` (a reasoning arm
measured, or the card's GPQA or AIME figure named as the card's), `write` (a reviewer-graded prose brief, or "not
measured"); `sampling`, the model's own recommended temperature, top_p, top_k, min_p and penalties, with its source —
where a row carries one at all: the display registry's `long` and `fast` rows have none, measured at the client's
default temperature (0) before this registry carried a sampling line, and their own `use_for` says so; and
`builder_class`, computed from whether the useful window reaches at least 81,920 tokens and the task is green. The
orchestrating agent reads these fields against a brief's scope, the token size of the files it names, and the time it
can spend, and takes the least costly profile whose card covers all three. `local-build.sh menu` prints which measured
models are already up and which would need a restart; when nothing is loaded it lists the whole card and marks the
default without calling it up.

The standard speed number for a row is `harness/bench_speed.sh <profile>`: llama-bench run at the row's own served
flags — `-fa`, `-ctk`/`-ctv`, `-ub`, a MoE row's `--n-cpu-moe` — at depths 0, 8k, 32k and the far end, one command a
reader can run unchanged and compare against another row's, recorded in the registry as `speed.bench`. `--dry-run`
prints that command without touching the card. It writes `$A770B_DATA/results/<profile>-bench-<date>.json` and
refuses outright while the harness's own server is up, one GPU process on the card at a time; `harness/ctx_sweep.sh`
stays the tool for what llama-bench does not watch — the VRAM peak during a prompt and the kernel log's reset count.
Its own far end is a target, not a measurement: the corpus slice it asks for is sized by four bytes a token, and
this kit's corpus is dense real source rather than prose, so that rule understates — a sweep asking for 8,000 tokens
sent 12,718. The far end actually recorded for a row is whatever token count its reply measured, never the number
typed on the command line, and a row too slow to answer inside the run's own time ceiling leaves nothing to record
at that depth at all (the 27B's own 100k attempt ran past a 3,600 s ceiling with no answer).
Beside the llama-bench number sits the standard suite's own as-delivered speed, `harness/suite_report.py` reading a
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
(the default) reads `config/profiles.json` under a 13.0 GiB cap, for a card that also draws the desktop; `inference`
reads `config/profiles.inference.json` under a 15.3 GiB cap, for a card that draws nothing else. A per-mode file,
`builder.<mode>.env` beside each `builder.env`, lets one machine keep different overrides for each mode in the load
order above — this workstation keeps `serious` at IQ3_S under a 14.1 cap only in `builder.display.env`, and drops that
override in inference mode. `status` prints the mode and the registry file on the builder-card line. `doctor` measures,
only while the seat's own server is down, what the builder card actually holds: in inference mode a reading above
0.5 GiB is reported MISSING — something else is still drawing the card; in display mode a reading at or under 0.1 GiB
gets a hint that inference mode would serve the larger registry under the larger cap. Either mode's cap is raised only
on a fresh measurement of what else the card holds, never by hand.

**The card.** `A770B_DEVICE` is the name from `llama-server --list-devices`; `A770B_GPU_MATCH` is the substring of that
card in `nvtop -s`, which the VRAM readings and the cap depend on. `A770B_UBATCH` (512) stays the same in both modes:
it keeps the GPU's job watchdog quiet. The server refuses to stay up past the mode's cap after load. No speculative
decoding on this card: draft models and MTP heads all made decode slower.

**The device pin.** `A770B_VK_DEVICE_SELECT` (default `8086:56a0!`) pins the server to the builder card by PCI vendor
and device id rather than by the Vulkan device index `A770B_DEVICE` names, because Vulkan lists the boot card first and
an index alone drifts when the desktop moves to another card. It is Mesa's Vulkan device selector, passed to the server
as `MESA_VK_DEVICE_SELECT`: find the id with `lspci -nn`, which prints each card's `[vendor:device]` pair, and the
trailing `!` makes that card the only Vulkan device the server can see. Set it in `builder.env` with single quotes,
`A770B_VK_DEVICE_SELECT='8086:56a0!'`, since an interactive shell reads a bare `!` as history expansion; an empty value
unpins. With the selector set, `llama-server --list-devices` lists exactly one card, the builder's, whatever card the
machine booted with as its display device. `local-build.sh doctor` checks this with the rest of the installation.

The cap is measured after load, in either mode. On a card that also draws the desktop, the public default of 13.0
assumes a desktop share that has never been measured on your card. Measure it before raising the cap: run `nvtop -s`,
start something that actually draws the card (a video playing is enough), and sum every process on it that is not the
server. On this workstation that came to 1.0 GiB, so the cap here runs at 14.1 in `builder.display.env` rather than
the public 13.0. That extra headroom is what admits the serious profile's task-lossless file, IQ3_S, at 114,688, which
loads at 13.78 GiB and peaks at 14.21 GiB during a 32k prompt; the long profile's full native window loads at
10.35 GiB and fits under either cap. On a card that draws nothing, the free card here reported 15.9 GiB total and
0.08 GiB used besides the server; prefill growth above the after-load reading has measured 0.04 to 0.3 GiB under
`-ub 512`, so the inference cap is 15.3: 15.9 minus a 0.3 GiB growth allowance minus a 0.3 GiB reserve. Either cap is
raised only after a fresh measurement, never by guesswork.

**Host memory.** The only host-memory check before a load is a floor on available memory, `A770B_MIN_AVAIL_MB`
(20,000 MB by default), read from the kernel's own `MemAvailable`. It is a page-cache floor for reading the weights
off disk, not a budget for weights that stay in system memory, so it cannot refuse a load whose weights will not
fit in RAM. A model that keeps part of its weights in host memory can still be started here, by four routes: the
expert-placement flag `--n-cpu-moe N` passed to `harness/serve_a770_llamacpp.sh start <gguf> <ctx>`, which hands
any argument after the window straight to the server; a profile's `A770B_<PROFILE>_EXTRA` set in the environment,
which wins over the value the registry supplies; `harness/ladder.sh <gguf> --ctx N --extra "--n-cpu-moe N"` on a
bare model file, the path for a model with no row; and serving one of the mixture-of-experts files the ledger
records. Such a server holds gigabytes of system memory beyond what it holds on the card. On a machine whose swap
is committed the kernel kills it under memory pressure, and the kill reads as a fault of the seat rather than of
the machine, so check free memory first and keep a build or another memory-heavy process off the machine while it
runs.

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

`A770B_LLAMA_BIN` defaults to `~/llama.cpp/build/bin/llama-server`; point it elsewhere if you built elsewhere.

**The kit's own toolchain.** `harness/run_suite.sh` needs nothing installed beyond what a normal development host
already has on its path: `node` (the system one; a version manager an interactive shell layers on top of it is not
what the sandbox sees), `cmake`, `make`, `g++` and `python` with the warm uv cache (`harness/warm_cache.sh`
pre-fills whatever `pytest` needs). No package is installed at run time or at grading time, and no network is used;
`local-build.sh doctor` and `tests/kit_selftest.sh` both check the sandboxed toolchain is actually reachable before
a row is trusted.

**The models.** These are the exact files each row below was measured with; a different quantisation of the same
model is a different row, and the registry's own `model` and `source` fields (`config/profiles.json`,
`config/profiles.inference.json`) are the authority when this list and they differ. Every profiled row's file is a
public GGUF on Hugging Face; the harness reads them from `A770B_MODELS`. Fetch them with the Hugging Face CLI (no
account needed for these), straight into that directory, one line per row of the registry it belongs to, in
registry order:

```bash
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested                # display: --profile long (5.6 GB)
uvx --from huggingface_hub hf download lmstudio-community/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir ~/LLM/tested        # display: --profile fast (5.3 GB)
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf --local-dir ~/LLM/tested     # display: --profile serious (10.1 GB)
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested                # inference: --profile long (5.6 GB; the same file as display's long)
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf --local-dir ~/LLM/tested       # inference: --profile serious (11.8 GB)
```

llama.cpp can also fetch a model itself: `llama-server -hf lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M` downloads into
`~/.cache/llama.cpp/` and serves it. The harness starts the server with `-m <file>`, so if you go that way set
`A770B_FAST_MODEL` (or `A770B_SERIOUS_MODEL`) to the absolute path of the cached file rather than moving it.

## Models — where to put them, how the skill reaches them

1. Put the GGUFs in `A770B_MODELS` (default `~/LLM/tested`); keep a README there saying why each earned its place.
2. Name the profiles' files: `A770B_FAST_MODEL`, `A770B_SERIOUS_MODEL`, `A770B_LONG_MODEL` (bare name = looked up in `A770B_MODELS`;
   absolute paths work). Set the context and KV type per profile (`A770B_*_CTX`, `A770B_*_KV`); reasoning models take
   `A770B_*_REASONING=on` plus their template kwargs in `A770B_*_EXTRA`.
3. The llama-server process on the host reads the weights and answers on `A770B_HOST:A770B_PORT` under the alias
   `local-builder`. The model's own process runs inside the sandbox and never sees `A770B_MODELS`; it only talks to the
   server over the bridge. So the weights can live anywhere the server can read, including a read-only share.
4. Before trusting a new model, qualify it (next section). The ledger of every model measured on this card, with its
   numbers and its profile, is [`config/models.md`](../config/models.md).

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
path — a model with no row yet, which needs `--ctx` and skips only rung 3 (`bench_speed.sh` needs a registry
profile: `harness/profiles.py card --name <profile>` has no ephemeral form). `--dry-run` prints every rung's command
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
   run by hand: **1-2** — `harness/bench_model.sh`, the load and its VRAM (`vram_gib_after_load`, and for a MoE row
   the host RAM the load costs beyond it, `ram_gb_extra`), the sanity probes and the 17k summary; **3** —
   `harness/bench_speed.sh <profile>`, llama-bench at the row's own served flags at depths 0, 8192, 32768 and the
   far end (skipped for a bare GGUF); **4** — `harness/ctx_sweep.sh 8000 <far end>`, prefill, decode and VRAM
   against position, the source `useful_ctx`'s linear extension is drawn from; **5** — `harness/depth_probe.sh <far
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
   mode's registry (`config/profiles.json` for display, `config/profiles.inference.json` for inference), then fill
   what the ladder leaves as `__TODO__` or omits entirely — `use_for` in words, `fit.write`, `capability` and
   `capability_source` — and the identity fields its own closing note lists: `source`, `family`, `architecture`,
   `quant`, `category`, `weight_class`, `params_b`, `sampling` and its source, `measured_on` (the card and build:
   e.g. `Arc A770 16 GB, llama.cpp b10805 Vulkan`), and, for a row reproducing the sibling-repository instrument,
   `task_t1` — transcribed once by hand from the GGUF's own metadata and the model's public card (`kit/PROFILE.md`
   §2 is the field-by-field reference for what fills every field). `local-build.sh profiles` and `status` show what
   is configured; `builder.<mode>.env` may still override a served value per machine (a different quantisation, a
   larger cap) without touching the registry. If the new profile becomes its mode's default, change `default` in
   that registry, run `python3 harness/profiles.py render --skill skills/local-build/SKILL.md --snippet
   skills/local-build/CONSTITUTION_SNIPPET.md --inference-file config/profiles.inference.json` so the skill's tables
   and the snippet pick it up, update the numbers in this file, and bump the version.

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

## What is in here

| path | role |
|---|---|
| `skills/` | the agent skills this project publishes for other coding agents to install; `local-build/` is the only one |
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (`run` with an optional `--spec`, `verify`, `reset`, `serve`, `status`, `stop`, `--version`, `check-update`), `CONSTITUTION_SNIPPET.md` (optional, agents add it to their own constitution) |
| `render_readme.sh` | generates the operator's local recap page, `README.html` — gitignored, never shipped; `README.md` is the document of record, written and reviewed first at every change, and the recap is regenerated from the repository's current state afterwards, never instead |
| `harness/` | the scripts that run and guard a profiling or build session: starting the model server, wrapping the coding agent in the sandbox, capturing what it did, and measuring how fast and how far it can go — every script here runs on the host, outside the sandbox |
| `harness/serve_a770_llamacpp.sh` | the only way a server starts: budget gate, VRAM cap (the mode's: 13 GiB after load on a display card, 15.3 on a free one), `-ub 512`, the API key, model marker |
| `harness/build_local.sh` | dispatch a brief through opencode in the seat (never a live checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: only the seat read-write, no credentials, no other checkout, no harness source, no network except the model server |
| `config/` | the project's configuration: the registries listing every qualified model and its measured numbers, the example environment file a new install copies, and the template opencode reads inside the sandbox |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation; the card mode, the key and profile helpers |
| `config/profiles.json` · `config/profiles.inference.json` | the two registries, one per card mode, the single source of every profile's numbers; `harness/profiles.py` validates, exports, inspects and renders them |
| `harness/render_profile.py` | renders the opencode profile from the template (`render`), checks a run specification against the seat (`check`) and prepares its context into the brief copy (`context`); the echo of what was rendered lands beside the profile; `tests/test_render_profile.py` proves it |
| `harness/guard.sh` | canonical seat guard, verified pids, the run lock, `safe_git`, the seat reset, the built-in budget gate |
| `harness/capture_task.sh` | diff + new files + the model's pytest line + server-side TTFT/TPOT distribution, the `.patch` for `verify`, then the seat reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost (testing only, run by the operator) |
| `harness/cancel_repro.sh` · `ctx_sweep.sh` · `depth_probe.sh` | the long-context qualification: a cancelled request while the slot is held; prefill, decode and VRAM against position; correct answers from 85% of the way into a large prompt (run by the operator against a server that is up) |
| `kit/` | the packaged tests run to profile each model, automated through `AGENTS.md`: `kit/seat/` (the exported working tree), `kit/tasks/` (each stage's brief and specification), `kit/hidden/` (each stage's one hidden pytest grader, and the reference solutions `run_suite.sh` pastes over a fresh export), `kit/reference/` (the three Aider polyglot exercises), `kit/corpus.py` (the deterministic long-context corpus), `kit/SUITE.md` · `kit/suite.json` (the standard suite in prose and as data), `kit/SOURCES.md` · `kit/NOTICE` (attribution) |
| `harness/run_suite.sh` | exports `kit/seat/` as its own standalone git repository, never a clone or checkout of this one, runs every stage of the standard suite and the reference exercises through the skill and `verify`, scores the reviewer-graded axes through a separate profile, and writes `results/<profile>-suite-<date>.json` |
| `harness/suite_report.py` | reads one of those results files and prints the instrument, a per-stage table, and the totals a profile's `suite` object takes (`--json` for just the totals, pasteable into the registry) |
| `tests/` | the tests that check the harness itself works, not the tests a profiled model is graded against |
| `tests/kit_selftest.sh` | proves the kit inside the real sandbox boundary, never on the host: the toolchain is found, every stage's shipped stub fails its own grader, every reference solution passes it, and every reference exercise's own public grader passes over its `.meta` solution; skips loudly without `bwrap` or the warm uv cache, so `tests/selftest.sh` stays machine-independent |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the key, the profile's window, a default-deny bash allow-list, the run specification's card, scope and additions, and a floor of deny rules rendered after every allow |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `docs/COMPARISON.md` | what this project solves next to a pre-built container of Intel GPU backends, where each one stops, and how the two fit together |
| `SECURITY.md` · `SANDBOX-PLAN.md` | the boundary as it stands; the problem, the plan and what was done |
| `VERSION` | the project's version; the installed skill carries the same number, and `local-build.sh --version` reports both, the release the checkout stands on, and warns when they differ; `check-update` asks GitHub for the latest release, only when asked, and works from a copy on a machine that has no checkout |
| `release.sh` | cuts a release: moves the number in its three places (`VERSION`, the skill's `SKILL_VERSION`, the tag) in one commit, pushes, publishes the GitHub Release from a notes file. The first release is 0.1.0; each one after adds one to the last number, and after ten of those small steps the middle number moves and the last number starts again. A major version still waits for the maintainer's word (`--major`) |
| `LICENSE` | MIT |
| `tests/selftest.sh` | what the harness proves without the card: every script parses, the health line reads the four kinds of answer and strips a hostile one, the seat's dirty check hides nothing but the harness's own brief copies, a symlink is reported and never read, the capture survives a run that made no file. Run it after a harness edit, from a tree you have read; a builder's patch is proven by `verify`, never by running its tests on the host |
| `briefs/` | the written tasks handed to a model during profiling or a build: the brief template (`TEMPLATE.md`: named files, verbatim text in quoted blocks, a test command on named files, a stop condition), the qualification task (`T1-…`, a bounded edit), the reading task (`T2-…`, one large file whole, graded against `grep`), the cheap-reviewer prompt, the smoke brief, the in-house suite (`briefs/suite/`, five bounded units of this repository with hidden graders) |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a standalone clone of the target repository with no link to its live checkout), results (captures, patches, verify
files), logs (server, builds, the bridge, the rendered profiles) and the uv cache in `~/local-ai/`. Nothing here is a
linked worktree of another project.
