#!/usr/bin/env python3
"""bench_build_id.py — name the server build a speed measurement was actually taken on.

`harness/bench_speed.sh` records this string as the row's `speed.bench.tool`, the field the registry
documents as "the build id llama-bench itself reports" (harness/profiles.py). It used to be the first
line of `llama-bench --version`, which on this Vulkan build is the graphics driver's platform warning:
every row recorded a warning where it should name a build.

The measurement's own output already carries the answer — every row of the raw JSON llama-bench writes
holds `build_number` and `build_commit`, the build that ran the benchmark. Those are preferred here and
formatted the way llama.cpp names a build, `b10805 (86b351fd6)`. The `--version` line the script read
before, and the git describe after it, stay as the fallback for a build whose JSON carries neither.

This lives in a file of its own, beside the script, because the decision is the thing worth testing and
the script cannot be run without the card: a test drives `build_id_from_raw` (or this CLI) with a
synthetic raw file and needs no GPU, no llama-bench and no server.

Usage: bench_build_id.py <raw.json> [fallback]
Prints one line, always: the build named by the raw JSON, else the fallback, else "unknown". Never fails
— bench_speed.sh calls it under `set -e` after a benchmark that has already run, and an unreadable or
oddly-shaped raw file must cost the caller its build id, never the measurement.
"""

import json
import sys


def rows_of(raw):
    """The rows of a llama-bench raw document: `-o json` writes a list, older/other shapes a
    {"results": [...]} object. Anything else has no rows."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [r for r in raw.get("results") or [] if isinstance(r, dict)]
    return []


def _clean(v):
    """A row field as a non-empty string, or "" — llama-bench writes build_number as an int."""
    if v is None or isinstance(v, bool):
        return ""
    return str(v).strip()


def build_id_from_raw(raw, fallback=""):
    """The build id named by a llama-bench raw document: "b<number> (<commit>)" from the first row that
    carries either field, else the fallback, else "unknown"."""
    for row in rows_of(raw):
        number = _clean(row.get("build_number"))
        commit = _clean(row.get("build_commit"))
        if number and not number.startswith("b"):
            number = "b" + number
        if number and commit:
            return f"{number} ({commit})"
        if number:
            return number
        if commit:
            return commit
    return fallback.strip() or "unknown"


def build_id_from_file(path, fallback=""):
    """As build_id_from_raw, reading the raw file. A missing or unparseable file falls back."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return fallback.strip() or "unknown"
    return build_id_from_raw(raw, fallback)


def main(argv):
    path = argv[1] if len(argv) > 1 else ""
    fallback = argv[2] if len(argv) > 2 else ""
    print(build_id_from_file(path, fallback))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
