# Operating the seat

This document is about running the seat day to day: how a run works, the three profiles and what they were measured at,
every knob, where the models go, the card's constraints and what each file is. The README covers what this is, why it
exists, how it installs and its security state; [`SECURITY.md`](../SECURITY.md) covers the boundary.

## Run it

```bash
bash skills/local-build/scripts/local-build.sh run <brief.md>                            # long profile (default), on the default seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --spec <spec.json>         # with a run specification (below)
bash skills/local-build/scripts/local-build.sh run ~/local-ai/seat <brief.md> --serious  # serious profile, on a named seat
bash skills/local-build/scripts/local-build.sh run <brief.md> --fast                     # fast profile: the reader
bash skills/local-build/scripts/local-build.sh verify <label>                            # the reviewer's proof
bash skills/local-build/scripts/local-build.sh reset                                     # a seat the skill refuses as dirty
bash skills/local-build/scripts/local-build.sh serve <profile> · profiles [--name <profile>] · status · stop · stop-run · --version · check-update
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
wrapper or interpreter is refused outright; the floor itself is defence in depth, the boundary is the sandbox. An example specification is at the end of `briefs/TEMPLATE.md`.

The test command a brief names runs in the model's own shell tool, which cuts a command at 120 seconds unless the model
asks for longer, and inside the boundary, which has no network and none of the host's environment. So a brief names the
test files its change touches, never the whole suite. Measured on a repository of about 3,800 tests: the full suite took
144 seconds inside the sandbox, the first attempt was cut at 120, and 17 tests that depend on the host's environment
failed there while passing on the host. The full suite is the merger's run on the host after review; `verify` re-runs
what the brief named.

## Profiles

The registry `config/profiles.json` is the single source of every profile's numbers; run `local-build.sh profiles`
(or `--name <profile>` for one) to see what is actually configured on this machine, `--served` layered over the
environment. Numbers below are properties of this card, build and quantisation, not of the models in general, measured
on this A770 (16 GB, also driving the desktop), llama.cpp b10805 with the Vulkan backend, on the qualification task.
`builder.env` may run the workstation's own IQ3_S card for `serious` at a different window under a larger cap; that row
is included below.

| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |
|---|---|---|---|---|---|
| long (default) | Qwen3.5-9B-Q4_K_M.gguf | 262,144 (~65k) | 10.35 GiB | 36.8 / 571 tok/s | The default: every ordinary change, tests from a specification, and a read up to about 64k. Reads exactly at 100k but takes eleven minutes to get there. |
| fast | gemma-4-E4B-it-Q4_K_M.gguf | 131,072 (~100k) | 8.1 GiB | 60 / 796 tok/s | The fast reader: a large file read cold in about four and a half minutes at 100k and precise questions about a passage deep in it. Not the profile for edits. |
| serious | Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf | 131,072 (~32k) | 12.25 GiB | 8.1 / 72 tok/s | A deliverable larger than its brief, tests written from an unfamiliar module, a change touching several files. Ten to twenty-five minutes; decode under five tokens a second by 64k, so point it at files that fit 32k. A measured card may run the IQ3_S file at a larger window through builder.env. |
| serious (this workstation, builder.env) | Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | 114,688 (~32k), needs a 14.1 cap | 13.78 GiB | 8.0 / 72 tok/s | Same use as `serious`, the quantiser's task-lossless file; ~9 min (five tests, 542 s). |

The Qwen windows are swept with `harness/ctx_sweep.sh`, prefill and decode against position with VRAM sampled and the
kernel log watched. The long profile serves its native 262,144 tokens and holds a 100k prompt with no reset and VRAM
flat within 0.3 GiB of its load, but its prefill falls under 150 tokens a second and its decode under 15 past about 64k,
so its useful window is about 64k to 72k of what it serves. The three 27B files share one speed profile, decode being
compute-bound on this card whatever the quantisation: about 8 tokens a second at 8k, 6 at 32k and under the
five-tokens-a-second floor by 64k, so the serious profile's useful window is about 32k whether it serves 131,072 or
114,688, and a brief for it points at files that fit that.

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

## Configure

All configurable paths and knobs are defined centrally and layered: the defaults in `harness/env.sh`, then
`<project>/config/builder.env`, then `~/.config/a770-builder/builder.env`, then the calling environment, later winning.
Copy `config/builder.env.example` to one of those two files and edit only what differs. The defaults are the
workstation the seat was qualified on (Arc A770 as `Vulkan0`, models in `~/LLM/tested`, data in `~/local-ai`). The
installed skill copy finds the project via `A770B_PROJECT`.

**The card.** `A770B_DEVICE` is the name from `llama-server --list-devices`; `A770B_GPU_MATCH` is the substring of that
card in `nvtop -s`, which the VRAM readings and the cap depend on. On a card that also draws the desktop keep
`A770B_VRAM_CAP_GIB` (13 of 16) and `A770B_UBATCH` (512): they keep the GPU's job watchdog quiet. The server refuses to
stay up past the cap after load. No speculative decoding on this card: draft models and MTP heads all made decode slower.

**The device pin.** `A770B_VK_DEVICE_SELECT` (default `8086:56a0!`) pins the server to the builder card by PCI vendor
and device id rather than by the Vulkan device index `A770B_DEVICE` names, because Vulkan lists the boot card first and
an index alone drifts when the desktop moves to another card. Find the id with `lspci -nn`, which prints each card's
`[vendor:device]` pair; the trailing `!` means the match must be exact, not a prefix. With the selector set,
`llama-server --list-devices` should list exactly one card, the builder's, whatever card the machine booted with as its
display device.

The cap is measured after load, and the public default of 13.0 assumes a desktop share that has never been measured on
your card. Measure it before raising the cap: run `nvtop -s`, start something that actually draws the card (a video
playing is enough), and sum every process on it that is not the server. On this workstation that came to 1.0 GiB, so
the cap here runs at 14.1 in `builder.env` rather than the public 13.0. That extra headroom is what admits the serious
profile's task-lossless file, IQ3_S, at 114,688, which loads at 13.78 GiB and peaks at 14.21 during a 32k prompt; the long profile's full native window loads
at 10.35 GiB and fits under either cap.

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

**The models.** Both profiles' files are public GGUFs on Hugging Face; the harness reads them from `A770B_MODELS`.
Fetch them with the Hugging Face CLI (no account needed for these), straight into that directory:

```bash
uvx --from huggingface_hub hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/LLM/tested            # long
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf --local-dir ~/LLM/tested   # serious
uvx --from huggingface_hub hf download ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf --local-dir ~/LLM/tested     # serious, this workstation's builder.env, under a 14.1 cap
uvx --from huggingface_hub hf download lmstudio-community/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir ~/LLM/tested       # fast
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

Models keep being released, and a new version of a family is a new model: its window, its speed on this card and its
behaviour on a real brief are all unmeasured until the harness has run it. The seat is built so that a new model enters
by measurement, in one sitting, without touching any script:

1. **Fetch the GGUF** into `A770B_MODELS` (the download lines above are the pattern) and write your qualification brief
   for your repository if you have not yet; `briefs/T1-sanitize-entity-tests.md` is the shape that works.
2. **Run the row**: `bash harness/run_one.sh <label> <file.gguf> <ctx> [extra llama-server args]`, with `KV_K`/`KV_V`
   and `REASONING` in the environment as the model needs. It starts the server under the card's rules, runs the probes
   (load time, VRAM, the sanity gate, the long prefill, a tool call), refuses to continue if the sanity gate fails, then
   dispatches the brief through opencode in the seat and writes the capture and a JSON of the numbers to
   `A770B_DATA/results/<label>.*`. Flash attention is a variable of the row, not a constant of the card: if the long
   prefill slows with position or the kernel logs an engine reset, run the row again with `-fa off` and `KV_V=f16`
   before judging the model. Watch the kernel log beside every row (`journalctl -k`) and stop the server on the first
   reset.
   For a model meant to hold large files, add three measurements with the server up: `harness/cancel_repro.sh` (a
   cancelled request must not take the card down), `harness/ctx_sweep.sh` (prefill, decode and VRAM against position)
   and `harness/depth_probe.sh` (correct answers from deep inside the prompt), and run `briefs/T2-read-a-large-file.md`
   as a second task.
3. **Grade the capture** with a reviewer that did not write it: `briefs/REVIEW-prompt.md` is the prompt, the capture is
   its only input. Run `local-build.sh verify <label>` for the proof. Green tests and PASS or PARTIAL qualify.
4. **Record it**: add the row to `config/models.md` with the measured numbers, the source repository and the caveats.
   A model that failed goes in the *kept out* paragraph with the reason, so nobody measures it twice.
5. **Give it a profile** if it earns one: it enters `config/profiles.json` with its measured card — model file, window,
   KV type, the reasoning and extra flags, the timings, and `use_for` in words. `local-build.sh profiles` and `status`
   show what is configured; `builder.env` may still override a served value per machine (a different quantisation, a
   larger cap) without touching the registry. If the new profile becomes the project's default, change `default` in
   `config/profiles.json`, run `python3 harness/profiles.py render --skill skills/local-build/SKILL.md --snippet
   skills/local-build/CONSTITUTION_SNIPPET.md` so the skill's table and the snippet pick it up, update the numbers in
   this file, and bump the version.

The expectations in the skill's table (window, speed, minutes per small task, what the model did with the repository's
idioms) are read straight off the ledger row; when the row changes, so do they.

## What is hardcoded now

Linux. The sandbox is bubblewrap, so the harness needs a kernel with unprivileged user namespaces; the VRAM readings
come from `nvtop` and the reset watch from `journalctl -k`; the serving line was measured on Mesa's Vulkan driver over
the Xe kernel driver, and the display-card rules are that driver's watchdog. Windows is not a target: the plausible
shape is the harness under WSL2 with `llama-server` native on Windows, reached over the loopback bridge, with
`A770B_ALLOW_NO_NVTOP=1` because the cap cannot read the card from inside WSL2; it has not been measured and the README
says so. macOS has no bubblewrap.

Beyond that, nothing that matters. Two conventions remain: the opencode alias `local-builder` (the profile template depends on it) and
the sandbox's use of `bubblewrap`, `socat`, `uv` and the opencode binary from `A770B_OPENCODE_BIN`.

## What is in here

| path | role |
|---|---|
| `skills/local-build/` | the agent skill: `SKILL.md`, `scripts/local-build.sh` (`run` with an optional `--spec`, `verify`, `reset`, `serve`, `status`, `stop`, `--version`, `check-update`), `CONSTITUTION_SNIPPET.md` (optional, agents add it to their own constitution) |
| `render_readme.sh` | regenerates `README.html` from `README.md`; run after every README edit, the Markdown is the source |
| `harness/serve_a770_llamacpp.sh` | the only way a server starts: budget gate, VRAM cap (13 GiB after load on a display card), `-ub 512`, the API key, model marker |
| `harness/build_local.sh` | dispatch a brief through opencode in the seat (never a live checkout), `< /dev/null`, inside the sandbox |
| `harness/sandbox_run.sh` | the bubblewrap boundary: only the seat read-write, no credentials, no other checkout, no harness source, no network except the model server |
| `harness/env.sh` · `config/builder.env.example` | every path and knob, one place; defaults = this workstation; the key and profile helpers |
| `harness/render_profile.py` | renders the opencode profile from the template (`render`), checks a run specification against the seat (`check`) and prepares its context into the brief copy (`context`); the echo of what was rendered lands beside the profile; `tests/test_render_profile.py` proves it |
| `harness/guard.sh` | canonical seat guard, verified pids, the run lock, `safe_git`, the seat reset, the built-in budget gate |
| `harness/capture_task.sh` | diff + new files + the model's pytest line + server-side TTFT/TPOT distribution, the `.patch` for `verify`, then the seat reset |
| `harness/bench_model.sh` · `run_one.sh` · `measure_overhead.sh` | the qualification row: probes → gate → task → capture; opencode opening-request cost (testing only, run by the operator) |
| `harness/cancel_repro.sh` · `ctx_sweep.sh` · `depth_probe.sh` | the long-context qualification: a cancelled request while the slot is held; prefill, decode and VRAM against position; correct answers from 85% of the way into a large prompt (run by the operator against a server that is up) |
| `config/opencode.profile.template.jsonc` | the ONLY opencode config the sandbox sees, rendered per run with the server URL, the key, the profile's window, a default-deny bash allow-list, the run specification's card, scope and additions, and a floor of deny rules rendered after every allow |
| `harness/warm_cache.sh` | pre-fills the read-only uv cache the sandbox mounts (it has no network) |
| `SECURITY.md` · `SANDBOX-PLAN.md` | the boundary as it stands; the problem, the plan and what was done |
| `VERSION` | the project's version; the installed skill carries the same number, and `local-build.sh --version` reports both, the release the checkout stands on, and warns when they differ; `check-update` asks GitHub for the latest release, only when asked, and works from a copy on a machine that has no checkout |
| `release.sh` | cuts a release: moves the number in its three places (`VERSION`, the skill's `SKILL_VERSION`, the tag) in one commit, pushes, publishes the GitHub Release from a notes file. The first release is 0.1.0; each one after adds 0.0.1, the minor number moves when the patch would pass 99, and a major bump takes `--major` |
| `LICENSE` | MIT |
| `tests/selftest.sh` | what the harness proves without the card: every script parses, the health line reads the four kinds of answer and strips a hostile one, the seat's dirty check hides nothing but the harness's own brief copies, a symlink is reported and never read, the capture survives a run that made no file. Run it after a harness edit, from a tree you have read; a builder's patch is proven by `verify`, never by running its tests on the host |
| `briefs/` | the brief template (`TEMPLATE.md`: named files, verbatim text in quoted blocks, a test command on named files, a stop condition), the qualification task (`T1-…`, a bounded edit), the reading task (`T2-…`, one large file whole, graded against `grep`), the cheap-reviewer prompt, the smoke brief |

Data stays outside this folder on purpose: models in `~/LLM/tested` and `~/LLM/next-card`; the seat (`~/local-ai/seat`,
a standalone clone of the target repository with no link to its live checkout), results (captures, patches, verify
files), logs (server, builds, the bridge, the rendered profiles) and the uv cache in `~/local-ai/`. Nothing here is a
linked worktree of another project.
