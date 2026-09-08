"""Hidden grader for S2 (backend). Copied by the harness to
tests/_hidden_test_s2_backend_hidden.py and run with pytest, cwd = the exported seat
(kit/seat/) — every path below is relative to that. Never present in the seat at run time;
not referenced from anywhere under kit/seat/.

Parity with python/data/sample.stats.json (committed, proven independently by
tests/test_kit_suite.py) on python/data/sample.log, plus a second fixed input — generated
right here, not shared with the visible python/tests/test_stats.py idiom — whose expected
output is computed by this file's own stdlib re-implementation, never by calling the seat's
`compute_stats`.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "python"))

SAMPLE_LOG = Path("python/data/sample.log")
SAMPLE_STATS = Path("python/data/sample.stats.json")

_LOG_RE = re.compile(r"^(\S+) ([A-Z]+) (\S+) (.*)$")
_NOISE = {
    "true", "false", "null", "none", "nil", "n/a", "na", "tbd", "todo", "yes", "no",
    "unknown", "undefined", "nan", "level", "component", "message", "timestamp", "log", "logs",
}
_NUMERIC_RE = re.compile(r"^[0-9]+$")
_AXIS_RE = re.compile(r"^\s*(?:level|component)\s*:", re.IGNORECASE)


def _sanitize(name):
    name = re.sub(r"\s+", " ", name.strip())
    if not name or _NUMERIC_RE.match(name) or len(name) < 2 or name.lower() in _NOISE:
        return None
    if _AXIS_RE.match(name):
        return None
    return name


def _reference_stats(lines):
    """Independent stdlib re-implementation of the fixed contract — never imports the
    seat's own code, so it cannot pass by agreeing with a bug the seat shares."""
    total = 0
    by_level, by_component, minute_counts = {}, {}, {}
    msg_lengths = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        m = _LOG_RE.match(line)
        if not m:
            continue
        ts, level, component, message = m.groups()
        clean = _sanitize(component)
        if clean is None:
            continue
        total += 1
        by_level[level] = by_level.get(level, 0) + 1
        by_component[clean] = by_component.get(clean, 0) + 1
        minute = ts[:16]
        minute_counts[minute] = minute_counts.get(minute, 0) + 1
        msg_lengths.append(len(message))
    busiest = None
    if minute_counts:
        best = max(minute_counts.values())
        busiest = min(m for m, c in minute_counts.items() if c == best)
    mean = round(sum(msg_lengths) / len(msg_lengths), 2) if msg_lengths else 0.0
    return {
        "total_lines": total,
        "by_level": dict(sorted(by_level.items())),
        "by_component": dict(sorted(by_component.items())),
        "busiest_minute": busiest,
        "mean_message_length": mean,
    }


def _load_compute_stats():
    from logstats.stats import compute_stats  # the seat's own implementation
    return compute_stats


def test_parity_with_committed_sample_stats():
    compute_stats = _load_compute_stats()
    lines = SAMPLE_LOG.read_text(encoding="utf-8").splitlines()
    expected = json.loads(SAMPLE_STATS.read_text(encoding="utf-8"))
    got = compute_stats(lines)
    assert got == expected, f"compute_stats(sample.log) != sample.stats.json\ngot={got}\nwant={expected}"


def test_parity_on_a_second_fixed_input():
    compute_stats = _load_compute_stats()
    lines = [
        "2026-02-01T10:00:05Z INFO ingest processed batch 1",
        "2026-02-01T10:00:40Z INFO ingest queued batch 2",
        "2026-02-01T10:01:10Z WARN api dropped job 9",
        "2026-02-01T10:01:59Z ERROR parser rejected chunk 4 bad header",
        "2026-02-01T10:07:00Z DEBUG cache flushed entry 7",
        "not a log line at all",
        "2026-02-01T10:07:20Z INFO x validated task 3",  # component "x" is too short: rejected
    ]
    expected = _reference_stats(lines)
    got = compute_stats(lines)
    assert got == expected, f"compute_stats(fixed input) != reference\ngot={got}\nwant={expected}"


def test_empty_input():
    compute_stats = _load_compute_stats()
    got = compute_stats([])
    assert got["total_lines"] == 0
    assert got["busiest_minute"] is None
    assert got["mean_message_length"] == 0.0
