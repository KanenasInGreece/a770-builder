# Task: write the logstats design note from the product brief

You are in `kit/seat/` of a standalone clone of a770-builder at this release's tag: that
directory IS your project root (every path below is relative to it, not to the outer
repository). The task is one new file, `design/DESIGN.md`, and nothing else. Do not edit
`product-brief.md` or any file under `python/`, `cpp/`, `js/` or `html/`.

## The files

- `product-brief.md` — read it whole first (it is one page). It fixes the log format, the
  three example lines, the statistics wanted, the page's behaviour, and — verbatim, do not
  change a key — the JSON object `compute_stats` returns and its worked example.
- `README.md` — the directory layout, for orientation only.

## The change

Write `design/DESIGN.md`, at most 60 lines, covering exactly these things:

1. **Data flow**: one sentence or short list — the log file, through `compute_stats`, to the
   JSON, to the page.
2. **The three components**, named and each described in one line: the Python backend
   (`python/logstats/`), the front end (`js/` and `html/`), and the C++ optimisation
   (`cpp/`, loaded through ctypes from `python/logstats/fast.py`).
3. **The JSON interface**: restate the five keys from `product-brief.md` (`total_lines`,
   `by_level`, `by_component`, `busiest_minute`, `mean_message_length`) with one line each
   saying what each holds, and include a JSON example that actually parses (you may reuse or
   adapt the product brief's own example — the keys must match exactly, nothing added,
   nothing renamed).
4. **The function the C++ part replaces**: name it (the per-line, per-level counting loop)
   and say in one line what stays in Python instead (`by_component`, `busiest_minute`,
   `mean_message_length`).

Plain Markdown prose and one fenced JSON code block; no other files, no diagrams.

## Verify — run exactly this and print its output

```
wc -l design/DESIGN.md
```

Expected: a number at or under 60, and the file exists.

## Rules

Work only inside `kit/seat/`. No version-control command that changes state (no commit,
push, merge, branch switch). No docker, no systemctl, no network. Keep every edit on one
physical line; never re-wrap.

## Stop when

`design/DESIGN.md` exists, is at or under 60 lines, names the three components, and its JSON
example parses with the fixed five keys. Then print the line count and stop.
