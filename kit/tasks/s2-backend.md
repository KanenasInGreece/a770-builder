# Task: implement `compute_stats` and its tests

You are in `kit/seat/` of a standalone clone of a770-builder at this release's tag: that
directory IS your project root (every path below is relative to it). The task is two files —
`python/logstats/stats.py` and a new `python/tests/test_stats.py` — and nothing else. Do not
edit `python/logstats/parse.py`, `python/logstats/__init__.py`, any file under `python/data/`,
or any file under `js/`, `html/`, `cpp/` or `design/`.

## The files

- `python/logstats/stats.py` — `compute_stats`'s signature and its full docstring (the five
  JSON keys, their exact meaning, tie-breaking and rounding rules) are already written; only
  the body is a stub (`raise NotImplementedError`). Read the docstring, not
  `product-brief.md`, for the exact contract — they agree, but the docstring is the one you
  implement against.
- `python/logstats/parse.py` — `parse_line` (use it; do not reimplement its regex) and
  `sanitize_component` (already applied inside `parse_line`, you do not call it directly).
- `python/tests/test_parse.py` — the idiom to copy: `sys.path` set the way this file sets it,
  one assertion block per behaviour, no fixtures.
- `python/data/sample.log` — 2,000 generated log lines, deterministic, already committed.
- `python/data/sample.stats.json` — the reference `compute_stats` output for `sample.log`,
  already committed.

## The change

1. Implement `compute_stats` in `python/logstats/stats.py` exactly to its docstring: call
   `parse_line` on every input line, skip a line it rejects (`None`), and aggregate
   `total_lines`, `by_level`, `by_component` (sorted keys, only levels/components that occur),
   `busiest_minute` (earliest-bucket tie-break, `None` when there are no parsed lines) and
   `mean_message_length` (rounded to 2 decimals, `0.0` when there are no parsed lines).
2. Write `python/tests/test_stats.py`, copying `test_parse.py`'s idiom, with at least these
   cases, each its own test function:
   a. `compute_stats` on `python/data/sample.log`'s lines equals
      `python/data/sample.stats.json` exactly (load the JSON, compare the dict);
   b. an empty input (`compute_stats([])`) returns `total_lines == 0`,
      `busiest_minute is None`, `mean_message_length == 0.0`;
   c. one line whose component `sanitize_component` rejects (e.g. `component: x` as the
      component field) is skipped and does not appear in `by_component` or count toward
      `total_lines`.

## Verify — run exactly this and print its last two lines

```
uv run --with pytest python -m pytest -q python/tests
```

Expected: a pytest summary line with 0 failed, and `EXIT=0`.

## Rules

Work only inside `kit/seat/`. No version-control command that changes state (no commit,
push, merge, branch switch). No docker, no systemctl, no network. Keep every edit on one
physical line; never re-wrap.

## Stop when

`uv run --with pytest python -m pytest -q python/tests` passes with 0 failures, including the
new `test_stats.py`. Then print the pytest summary line and the list of files you changed.
