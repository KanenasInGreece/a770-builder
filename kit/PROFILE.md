# kit/PROFILE.md — from the tests to the profile

A row in the registry (`config/profiles.json`, `config/profiles.inference.json`) is not a claim; it is a
transcription of what a rung of the ladder in `AGENTS.md` actually measured. This document says, field by field,
which rung fills a field, how the number in it is derived, and what a reader can honestly set it beside outside
this project — so a row here is directly comparable with an online test wherever one exists, and plainly marked
"not comparable" where none does. `kit/SUITE.md` describes the suite itself; this document describes what a run
of it, and of the rest of the ladder, turns into a row.

## 1. What the orchestrator is handed

The orchestrating agent never re-runs the ladder before picking a model for a brief: it reads the card
(`local-build.sh profiles --name <profile>`, backed by `harness/profiles.py card`), a plain JSON object, against
the brief's scope, the size of the files it names, and the time it can spend. The card is data the calling agent
reads, not code it executes.

The kit's own designated first row is the inference `serious` profile: its grading against `kit/SUITE.md` is what
re-judges the design set by the earlier, sibling-repository instrument, so it is the row this document reproduces
throughout, abridged to its fields with one gloss each, real values, unedited:

```json
{
  "model": "Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf",
  "source": "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF",
  "family": "qwen3.8",
  "quant": "IQ3_S",
  "category": "dense",
  "weight_class": "27b",
  "ctx": 196608,
  "useful_ctx": 98304,
  "kv": "q4_0", "flash_attention": "on", "reasoning": "on",
  "vram_gib_after_load": 14.62, "ram_gb_extra": 0,
  "depth_probe_100k": "not run", "task_t1": "pass",
  "instrument": "seat: Shared_Memory@3c8e2bb",
  "capability": {"LiveCodeBench_v6": 85.71, "GPQA_Diamond": 89.39, "AIME25": 100},
  "use_for": "A deliverable larger than its brief … at eight tokens a second; useful to about 98k …"
}
```

one gloss per field shown: `model` the served GGUF's bare file name · `source` where it came from · `family` the
model family label · `quant` the quantisation scheme · `category` dense or moe · `weight_class` a short label for
the parameter count · `ctx` the window served · `useful_ctx` the window that actually holds above 4 tok/s decode ·
`kv`/`flash_attention`/`reasoning` what was actually served · `vram_gib_after_load`/`ram_gb_extra` what the load
cost · `depth_probe_100k`/`task_t1` pass/fail of two rungs · `instrument` what the row was measured on ·
`capability` the model's own public benchmark numbers · `use_for` what the ladder showed the row is good for.
Added by `card`, never stored in the file: `"builder_class": true` (`useful_ctx` clears 81,920 and `task_t1`
reads pass) and `"comparable_with": ["long", "moe"]` (the other two inference rows share this row's exact
`instrument` string today).

Besides the fields shown above, every row also carries `architecture` (a one-line prose description — attention
shape, KV head count, block count), `params_b` (total/active parameters in billions), `extra` (the exact server
flags), `sampling`, `timeout_s`, `speed`, `capability_source` and `fit`; §2 covers each in turn. `model`,
`source`, `family`, `architecture`, `quant`, `category`, `weight_class` and `params_b` are identity fields, not
measurements: transcribed once, by hand, from the GGUF's own metadata and the model's public card, never
re-derived by a rung — they exist so the registry never carries two rows that are really the same choice twice.

## 2. From the tests to the fields

| field | what fills it | how the number is derived | the public figure it compares with |
|---|---|---|---|
| `ctx` | Rung 1, the load (`harness/serve_a770_llamacpp.sh start <gguf> <ctx>`) | the largest window that actually loads under the mode's cap (13.0 GiB display, 15.3 GiB inference), found by hand, counting a MoE row's `--n-cpu-moe` down until it fits | none — a property of this card and cap |
| `useful_ctx` | Rung 3 (`bench_speed.sh`) plus the safety sweep (`ctx_sweep.sh`) and Rung 4 (`depth_probe.sh`) | the four-tokens-a-second rule: decode time-per-token at 8k and the far end, extended linearly between them, capped wherever the depth probe actually failed to answer from deep in the prompt | none — this card's own figure; the method is stated so a reader can judge it |
| `kv`, `kv_v` | Rung 1's served flags (`-ctk`/`-ctv`) | the K/V cache quantisation actually served; `kv_v` defaults to `kv` when a row carries none | none |
| `flash_attention` | fixed per family, not re-measured per row (*What stays fixed*, `AGENTS.md`) | on for every family measured so far but one: every Gemma 4 collapsed on long prefill with it on and ran clean off; recorded as what was actually served | none |
| `reasoning` | the model's own card's recommended mode, read once before the first rung | on/off as served in `extra`; a reasoning row gets a larger sanity-gate budget so its answer lands in the reply content, not spent unseen | none |
| `extra` | the exact server flags actually served: the sampling line, a MoE row's `--n-cpu-moe`, a reasoning row's `--chat-template-kwargs` | copied verbatim from what was served — `bench_speed.sh` builds its own llama-bench command from these same values | the flag set itself is what must match for a llama-bench comparison to hold; a different `extra` is a different row |
| `sampling` | the model's own card, read once before the first rung | temperature/top_p/top_k/min_p/penalties transcribed from the card's recommended line for the role the row fills, `source` naming where; rendered into the server's `extra` and the client's own agent block, so a client default cannot silently override it | the model card's own recommended sampling line — quoted, never re-measured |
| `output_tokens` | an operating knob, not a rung | the opencode per-reply output cap, picked generous enough that a whole file goes out in one tool call — a row author's choice | none |
| `timeout_s` | the ladder's own observed wall times (Rung 2's summary, Rung 5's task wall) | the wall-clock budget a run is wrapped in, set with headroom over what the row was actually seen to take | none |
| `vram_gib_after_load` | Rung 1's serve line | the after-load VRAM reading, taken once per row | none directly — it must sit under the mode's cap |
| `ram_gb_extra` | Rung 1, a MoE row only | host RAM the row's CPU-resident expert layers cost beyond VRAM, measured empirically; 0 for a dense row | none |
| `speed.decode_tps` / `speed.prefill_tps` | Rung 3 (`bench_speed.sh`), filled by hand from `speed.bench.at_depth` | `8k` from `at_depth["8192"]`, `32k` from `["32768"]`, `64k` from `["65536"]`, `100k` from `["100000"]` (or whichever depth the row's own far end used, on a window under ~110k); `decode_tps.<k>` from that depth's `tg`, `prefill_tps.<k>` from its `pp` — a human transcription, never computed by `profiles.py` | any llama-bench result for the same GGUF, backend and flags. Worked instance: llama-bench on this row read 8.8 tok/s decode at depth 0, 7.9 at 8,192, 6.2 at 32,768 (prefill 73.0 / 64.5 / 47.4 tok/s at those depths) — agreeing with this project's own sweep of the same file within a few percent, the two instruments cross-checking each other |
| `speed.bench` | Rung 3 (`bench_speed.sh <profile>`) directly | llama-bench run at the row's own served flags at depths 0, 8,192, 32,768 and the far end; `tool`, `prompt`/`gen`, `flags` (the reproducible command line), `at_depth`, `source` recorded verbatim | any llama-bench result for the same GGUF, backend and flags — the standard, reproducible number; a different quantisation or backend is a different row |
| `speed.delivered` | `harness/suite_report.py` reading a `run_suite.sh` results file | each stage's own server-side timing lines from its capture, MEDIANED across stages | not comparable — what the row delivered inside a real coding run, contention included, never what the flag set can do alone; recorded beside `speed.bench`, never instead of it |
| `speed.far_end` | Rung 3 / the safety sweep | `tokens` is whatever the reply actually measured, never the number typed on the command line (a row too slow to answer inside the run's ceiling leaves nothing to record); `decode_tps`/`prefill_tps`/`ttft_s` read at that point | none directly — the anchor `useful_ctx`'s linear extension is drawn from |
| `depth_probe_100k` | Rung 4 (`depth_probe.sh <tokens>`) | pass/fail/"not run" on whether a detail planted ~85% deep in a large prompt is actually recalled from that depth | none |
| `task_t1` | Rung 5 on the sibling-repository instrument (`run_one.sh`, T1) | pass/fail from the qualification brief run through opencode in that seat; superseded by `suite` for a row measured on the kit | none — this project's own bounded task |
| `suite` | Rung 5 on the kit (`run_suite.sh <profile>`), summarised by `suite_report.py --json` | `briefs`/`runs`/`passed`/`timeouts`/`mean_wall_s`/`source` from the results file's totals (a stage carrying `counts_toward_pass: false` excluded entirely, so the design stage's own grade never enters the tally); `stages` one entry per stage; `reviewer` pins the reviewer's own profile, model, ctx, sampling and the rubric's hash | none directly — `capability`, below, is the field that reaches outside this project |
| `fit` | three short strings, each independently sourced | `code` from the suite's stage pass count and wall time (or T1's, when the suite was not run); `think` from a reasoning arm measured on this card, or else the model's own card's GPQA/AIME figure, named as the card's; `write` from a reviewer-graded prose brief, or plainly "not measured" — never invented | `code`: none; `think`: the model card's own figure, where quoted rather than measured here; `write`: none, or the rubric's own 0–5 scale |
| `capability`, `capability_source` | the model's or the quantiser's own published evaluation | numbers quoted verbatim from what `capability_source` names, never re-measured on this card | directly comparable with the maker's model card, and with the quantiser's own evaluation where one exists |
| `instrument` | computed per run (`run_suite.sh`: `<suite id>@<project version>`, e.g. `SUITE-1@0.2.0`) or hand-recorded for the earlier rows (`seat: Shared_Memory@3c8e2bb`) | names what the row was actually measured on | none — it is the comparability key `comparable_with` reads, not itself a comparison |
| `builder_class` | computed by `card`, never stored | `useful_ctx >= 81,920` AND a green task — `task_t1` passing, or, when `suite.stages` is present, the stages whose `axes` include `working` passing at 80% or better (the design stage and the rubric axes never count) | none |
| `comparable_with` | computed by `card`, never stored | every other row in the same registry whose `instrument` string matches this row's exactly | none — it names which other rows this one may sit beside |
| `use_for` | written by hand from what the ladder actually showed | states what the row is good for and, as often, what it is not for or not to be run beside | none |

## 3. The tests, one by one

**S0 design** asks for `design/DESIGN.md`: the data flow, the three components, and the fixed five-key JSON
interface restated with a parsing example, at or under 60 lines. The model sees `product-brief.md` and the seat's
own `README.md`; it may run nothing (prose has no compiler). It is graded by one hidden pytest file checking the
restated interface matches exactly, with no separate conformance check; its budget is 60 lines. It carries
`maintainable` and `usable`, both outside the pass count — a floor is a missing or renamed key; a ceiling is an
exact, concise restatement. No public counterpart: this brief is kit-specific.

**S1 front end** asks for `js/format.js`'s three functions, a new `js/render.js`, and `html/index.html` wired to
fetch, filter and announce re-renders. The model sees `product-brief.md`, the stubbed files with their JSDoc
contracts, and the seeded test idiom; it may run `node --test`/`node --check` (granted unconditionally by the
profile template). It is graded by `node --test` plus one hidden pytest file, conformance by `node --check` on
both JS files, budget 180 lines across three files. It carries `maintainable` and `usable`. A floor is DOM access
inside `render.js` or a dead `<select>`; a ceiling is all three functions correct with an accessible page. No
public counterpart of its own, though the kit's C++ reference exercise sits beside it in the run order for a
different language.

**S2 backend** asks for `compute_stats` in `python/logstats/stats.py` and its own test file. The model sees the
function's full docstring (the contract it builds against), `parse.py` (used, not reimplemented), the seeded test
idiom, and the committed sample log and its reference statistics; it may run `uv run --with pytest …` (granted
unconditionally). It is graded by pytest over `python/tests` plus one hidden file, conformance by `compileall`,
budget 120 lines. It carries `maintainable` only — no user-facing surface. A floor is a busiest-minute tie broken
the wrong way or a component `sanitize_component` should have rejected leaking into the totals; a ceiling is exact
parity with the committed reference JSON plus the edge cases. No public counterpart.

**S3 optimisation** asks for `cpp/logstats.cpp`'s `count_levels` and the `ctypes` wiring in
`python/logstats/fast.py`. The model sees the file's own top-of-file ABI comment (the contract), the complete
Makefile, and S2's `compute_stats` for parity; it may run `g++`/`cmake` (template-granted) and `make`/`ctest`
(granted per this stage's own specification, since a multi-file C++ build needs a configure step). It is graded by
`make -C cpp` (warnings as errors) plus pytest and one hidden file, budget 150 lines. It carries `maintainable`
only. A floor is a warning treated as an error, or a `by_level`/`total_lines` mismatch against S2; a ceiling is a
clean build and full translation with the Python fallback left intact. No public counterpart.

**ref-cpp-binary-search-tree** asks for a templated `binary_tree<T>` in one header: a constructor, `insert`,
`data()`/`left()`/`right()`, and an in-order const iterator. The model sees the exercise's own public test file
(never edited) and its instructions verbatim; it may run `cmake` (template-granted). What grades it, publicly, is
the exercise's own CMake build running its test binary as a build step (69 assertions, 12 cases); privately, a
hidden pytest wraps a hidden C++ variant with fresh values. Public pass with hidden fail is the contamination
signal; both passing carries no information either way. Public counterpart: Aider's own polyglot-benchmark C++
exercise `binary-search-tree` (Exercism practice track, MIT, 2021).

**ref-javascript-forth** asks for a small stack-based Forth evaluator: arithmetic, stack words, and custom word
definitions. The model sees the exercise's own spec file (never edited), run through a shim mapping `xtest` onto
`node:test`'s own `test` (reproducing Aider's own all-enabled grading, 49 cases, not Exercism's progressive
unlock); it may run `node --test` (template-granted). Publicly graded by that 49-case run; privately, a hidden
pytest wraps fresh programs. Public counterpart: Aider's polyglot-benchmark JavaScript exercise `forth`.

**ref-python-pov** asks for `Tree.from_pov` and `Tree.path_to` (reparenting a tree on a node), raising exact
`ValueError` text. The model sees the exercise's own test file (never edited) and a docs note on the exact error
strings; it may run `uv run --with pytest …` (template-granted). Publicly graded by that file (15 cases); privately,
a hidden pytest wraps fresh trees. Public counterpart: Aider's polyglot-benchmark Python exercise `pov`.

## 4. How to compare with online tests, honestly

**llama-bench**: `speed.bench` is reproducible byte for byte — the same GGUF, the same backend (llama.cpp,
Vulkan), the same flags and the same depths give a comparable `pp`/`tg` pair anywhere they are run. A different
quantisation, a different backend, or a different flag in `extra` is not a faster or slower reading of the same
row; it is a different row, and belongs beside this one, never in place of it.

**Aider's polyglot benchmark**: the reference rung's public grader is exactly the exercise Aider grades, run the
same way (its own runner, nothing installed) — but it is one exercise per language here against Aider's own tens
to hundreds, with a project arc the reference rung has none of. Read it as a spot check of relatability and a
contamination indicator, never as a leaderboard position; Aider's own leaderboard shape (pass@2, two attempts) also
differs from the seat's single attempt here.

**Model cards**: `capability` is the maker's or the quantiser's own number, on their own harness, quoted here
verbatim — never re-measured on this card. It is directly comparable with the card it was quoted from, and with a
second quantiser's evaluation of the same weights where one exists, but not with a number this project measured
itself.

What is **not** comparable with anything outside this project: `speed.delivered` (a real run's contention, not a
clean flag set); the four stages of the standard suite (`kit/SUITE.md` has no public counterpart at all); and the
reviewer's rubric scores (one rubric, one reviewer profile, pinned per run — a different reviewer is a different
instrument).

## 5. Reading a row

Read the card in §1 plainly: this row builds under its own served line at 8.8 tokens a second at the mouth of the
window and 6.2 by 32k, holds a useful 98,304 tokens by the four-tokens-a-second rule, clears `builder_class` on
`task_t1` alone (no `suite` object yet — this is the sibling-repository instrument, not a kit measurement), and its
`capability`/`capability_source` say its LiveCodeBench and AIME numbers match its BF16 original on the quantiser's
own card. Its `use_for` follows from exactly that: a deliverable larger than its brief, at eight tokens a second,
useful to about 98k before the read gets slow — never a claim invented past what the fields above actually hold.

A green field-by-field card is not the whole story, and reading a row means reading a stage's own grade before
trusting it. The kit's own standard suite has now run once, end to end, on the card: against a 9B-class row, it
graded every one of the four stages FAIL. The honest reading of a result like that is not "close" or "nearly
working" — it is a row that does not clear the standard suite's bar as things stand, and a card's `use_for` must
say exactly that, never dressed up as a partial pass because the other rungs read well. A row is only ever as
strong as its worst rung, and the suite exists precisely so that rung cannot be skipped or softened in the write-up.
