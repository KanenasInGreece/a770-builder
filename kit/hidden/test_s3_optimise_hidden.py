"""Hidden grader for S3 (optimisation). Copied by the harness to
tests/_hidden_test_s3_optimise_hidden.py and run with pytest, cwd = the exported seat
(kit/seat/) — every path below is relative to that. Never present in the seat at run time;
not referenced from anywhere under kit/seat/.

Builds nothing itself: `kit/tasks/s3-optimise.spec.json`'s `verify.test` runs
`make -C cpp` before this file is copied in and pytest re-invoked, so `cpp/liblogstats.so`
already exists by the time these tests run. Tests `count_levels` directly through `ctypes`
(bypassing `python/logstats/fast.py` on purpose: its Python fallback is already a correct
implementation, so a Python-level-only parity check cannot tell a working C++ function from
an unimplemented one) on fixed inputs, then checks `fast.py`'s own wrapper for the same
parity plus a timing ratio recorded as a print, never a threshold.
"""

import ctypes
import json
import re
import sys
import time
from pathlib import Path

SEAT = Path.cwd()
LIB_PATH = SEAT / "cpp" / "liblogstats.so"
SAMPLE_LOG = SEAT / "python" / "data" / "sample.log"
SAMPLE_STATS = SEAT / "python" / "data" / "sample.stats.json"

sys.path.insert(0, str(SEAT / "python"))

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")  # out5[0..3], this fixed order; out5[4] = total

# Every line here fully parses (parse_line would accept it and its component would pass
# sanitize_component): count_levels only replaces the level-counting loop, not component
# sanitization (see s3-optimise.md), so its total/by_level parity with compute_stats is
# specified to hold on well-formed input only — this fixture is deliberately that, not the
# adversarial mix python/tests/test_stats.py and S2's hidden grader use.
FIXED_INPUT = "\n".join([
    "2026-02-01T10:00:05Z INFO ingest processed batch 1",
    "2026-02-01T10:00:40Z INFO ingest queued batch 2",
    "2026-02-01T10:01:10Z WARN api dropped job 9",
    "2026-02-01T10:01:59Z ERROR parser rejected chunk 4 bad header",
    "2026-02-01T10:07:00Z DEBUG cache flushed entry 7",
])


def _reference_level_counts(text):
    """Independent re-implementation of the level-counting contract, by plain string
    splitting — never calls count_levels (the seat's C++) or compute_stats (the seat's
    Python), so it cannot pass by agreeing with a bug either one shares."""
    counts = {lvl: 0 for lvl in LEVELS}
    total = 0
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[1] not in counts:
            continue
        counts[parts[1]] += 1
        total += 1
    counts["total"] = total
    return counts


FIXED_EXPECTED = _reference_level_counts(FIXED_INPUT)


def _call_count_levels(text: str):
    assert LIB_PATH.is_file(), f"{LIB_PATH} does not exist — was `make -C cpp` run first?"
    lib = ctypes.CDLL(str(LIB_PATH))
    lib.count_levels.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_long * 5)]
    lib.count_levels.restype = ctypes.c_int
    data = text.encode("utf-8")
    out5 = (ctypes.c_long * 5)()
    rc = lib.count_levels(data, len(data), ctypes.byref(out5))
    return rc, list(out5)


def test_count_levels_on_a_small_fixed_input():
    rc, out5 = _call_count_levels(FIXED_INPUT)
    assert rc == 0, f"count_levels returned {rc} (non-zero = not implemented / error)"
    got = dict(zip(LEVELS, out5[:4]))
    got["total"] = out5[4]
    assert got == FIXED_EXPECTED, f"count_levels({FIXED_INPUT!r}) = {got}, want {FIXED_EXPECTED}"


def test_count_levels_on_sample_log():
    text = SAMPLE_LOG.read_text(encoding="utf-8")
    expected_stats = json.loads(SAMPLE_STATS.read_text(encoding="utf-8"))
    rc, out5 = _call_count_levels(text)
    assert rc == 0, f"count_levels returned {rc} on sample.log"
    got_by_level = {lvl: n for lvl, n in zip(LEVELS, out5[:4]) if n}
    assert got_by_level == expected_stats["by_level"], (
        f"count_levels(sample.log) by-level = {got_by_level}, want {expected_stats['by_level']}"
    )
    assert out5[4] == expected_stats["total_lines"], (
        f"count_levels(sample.log) total = {out5[4]}, want {expected_stats['total_lines']}"
    )


def test_fast_py_wrapper_matches_compute_stats_and_records_timing():
    from logstats.fast import fast_level_counts
    from logstats.stats import compute_stats

    text = SAMPLE_LOG.read_text(encoding="utf-8")
    lines = text.splitlines()

    t0 = time.perf_counter()
    fast_result = fast_level_counts(text)
    t_fast = time.perf_counter() - t0

    t0 = time.perf_counter()
    py_result = compute_stats(lines)
    t_py = time.perf_counter() - t0

    assert fast_result["by_level"] == py_result["by_level"], (
        f"fast_level_counts by_level {fast_result['by_level']} != compute_stats by_level {py_result['by_level']}"
    )
    assert fast_result["total_lines"] == py_result["total_lines"]

    ratio = (t_py / t_fast) if t_fast > 0 else float("inf")
    print(f"S3 timing (informational, no threshold): compute_stats={t_py:.6f}s fast_level_counts={t_fast:.6f}s ratio={ratio:.2f}x")
