# Product brief: logstats — a log-line statistics viewer

Fresh, MIT, this repository. This is the ONE brief every stage of this project reads; each
stage's own task brief (outside this directory) narrows it to that stage's file(s). The
interface below is FIXED — no stage renegotiates it; S0 is graded on restating it correctly,
not on inventing an alternative.

## The users

An operator watching one service's log file who wants, at a glance and with no query
language: how many lines came in per level and per component, when things were busiest, and
whether messages are running long — then wants to narrow the view to one level.

## The log format

One log file, one event per line, in this exact shape:

```
<ISO timestamp> <LEVEL> <component> <message>
```

Three example lines:

```
2026-01-01T00:00:16Z WARN api dropped job 7649
2026-01-01T00:01:20Z DEBUG ingest processed segment 1234
2026-01-01T00:07:02Z ERROR parser rejected batch 42 malformed header
```

`<LEVEL>` is one of `DEBUG`, `INFO`, `WARN`, `ERROR`. `<component>` is a short lowercase
name (a source, e.g. `ingest`, `api`); a line whose component fails validation
(`python/logstats/parse.py`'s `sanitize_component`) is skipped, not counted anywhere.
`<message>` is free text and may itself contain spaces — it is everything after the third
space-separated field.

## The statistics wanted

Per-level counts, per-component counts, the busiest minute (the one-minute bucket with the
most lines), and the mean message length in characters.

## The interface: the JSON the backend returns

The backend is one function, `compute_stats(lines) -> dict`
(`python/logstats/stats.py`), whose returned dict has exactly these keys — no more, no
fewer, no renaming:

- `"total_lines"` — integer, the count of lines that parsed successfully.
- `"by_level"` — object, count per level, only the levels that occur.
- `"by_component"` — object, count per component, only the components that occur.
- `"busiest_minute"` — string (`"YYYY-MM-DDTHH:MM"`) or `null` when there are no lines.
- `"mean_message_length"` — number, rounded to 2 decimal places.

Example, for the three lines above:

```json
{
  "total_lines": 3,
  "by_level": {"DEBUG": 1, "ERROR": 1, "WARN": 1},
  "by_component": {"api": 1, "ingest": 1, "parser": 1},
  "busiest_minute": "2026-01-01T00:00",
  "mean_message_length": 24.0
}
```

## The page's behaviour

The page fetches this JSON (as `stats.json`, beside the page) once on load and renders: the
total line count, the per-level counts, the per-component counts, the busiest minute, and the
mean message length. A `<select id="level">` filters the rendered per-component breakdown to
lines counted for one level; an "All" option shows the unfiltered totals. The page is usable
with a screen reader: every control is labelled, the results region announces when it
updates, and heading order is not skipped.

## The C++ part

One function is the hot path worth re-doing in C++: the per-line, per-level counting loop
over the raw file text (not the per-component breakdown, not the busiest-minute or
mean-message-length arithmetic — those stay in Python). Its C ABI, its five-long output layout
and the parity it must hold with `compute_stats`'s `by_level`/`total_lines` are specified in
`../tasks/s3-optimise.md`.
