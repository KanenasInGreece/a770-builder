# logstats — design note

## Data flow

The log file is read line by line, parsed and aggregated by the Python **backend**
(`compute_stats`), which returns one JSON object; the **front end** fetches that JSON and
renders it into the page, with a level filter.

## The three components

- **Backend** (`python/logstats/`): `parse.py` (line parsing and component sanitisation,
  already complete) and `stats.py`'s `compute_stats`, which aggregates the parsed lines into
  the JSON below.
- **Front end** (`js/` and `html/`): `format.js` (pure formatting helpers), `render.js`
  (turns the JSON into an HTML fragment) and `index.html` (fetches the JSON, wires the level
  filter, injects the rendered fragment).
- **C++ optimisation** (`cpp/`, loaded through ctypes from `python/logstats/fast.py`): a
  drop-in re-implementation of the per-line, per-level counting loop, called instead of
  looping in Python when the compiled library is present.

## The JSON interface

- `"total_lines"` — integer, count of lines that parsed successfully.
- `"by_level"` — object, count per level, only the levels that occur.
- `"by_component"` — object, count per component, only the components that occur.
- `"busiest_minute"` — string (`"YYYY-MM-DDTHH:MM"`) or `null` when there are no lines.
- `"mean_message_length"` — number, rounded to 2 decimal places.

```json
{
  "total_lines": 3,
  "by_level": {"DEBUG": 1, "ERROR": 1, "WARN": 1},
  "by_component": {"api": 1, "ingest": 1, "parser": 1},
  "busiest_minute": "2026-01-01T00:00",
  "mean_message_length": 24.0
}
```

## The function the C++ part replaces

The per-line, per-level counting loop inside `compute_stats` (producing `by_level` and
`total_lines`) moves to C++; `by_component`, `busiest_minute` and `mean_message_length` stay
in Python.
