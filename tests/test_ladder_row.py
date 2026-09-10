#!/usr/bin/env python3
"""Tests for the `useful_ctx` harness/ladder.sh computes and prints in a registry row.

`useful_ctx` is the largest depth at which decode stays above four tokens a second: the ladder reads decode
time-per-token at the shallow point and at the far end out of the window rung's own table, extends it linearly
between them, and caps the result at the depth probe's last passing depth. Every input is a measurement, so
every case where a measurement is missing has to leave the field unset rather than fill it from something else.

The two that matter here:

  * a depth probe the server never answered reports "not measured", not a score. The model was never asked, so
    no window has any quality behind it and `useful_ctx` is unset. A number carried over from the requested
    window, or from the sweep alone, would read as a depth whose quality had been checked.
  * a window rung that reported no far point leaves nothing to extend the curve to, and `useful_ctx` is unset
    for the same reason. A point the sweep refuses as not measured prints no row in that table.

The computation is the block of Python harness/ladder.sh runs after its six rungs, which takes every rung's
output as its arguments. These tests lift that block out of the script and drive it with synthetic rung logs,
so they need no card, no server and no run: exactly the inputs the ladder would hand it, and the ladder JSON
and printed row it writes out.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LADDER_SH = ROOT / "harness" / "ladder.sh"

FAR_END = 38976
CTX = 40000

# harness/ctx_sweep.sh's own table, as it prints it: a shallow point and a far point, both answered
SWEEP_TABLE = """\
target   tokens   ttft_s    prefill_tps decode_tps vram_max  resets
8000     7993     14.0      571.0       36.8       10.60GiB  0
38976    38970    265.0     147.0       11.6       10.90GiB  0
"""
SWEEP_SHALLOW_ONLY = "\n".join(SWEEP_TABLE.splitlines()[:2]) + "\n"

# harness/depth_probe.sh's own verdict lines. The wording is the probe's, quoted so the ladder is driven with
# what it really reads.
DEPTH_PASSED = f"depth probe at {FAR_END}: 3/3\n"
DEPTH_NEVER_ANSWERED = (
    f"depth probe at {FAR_END}: not measured — the 7200s deadline cut the request off after 7201s\n"
)


def row_computation(tmp_path: Path) -> Path:
    """The Python block harness/ladder.sh runs to compute the row, written out as a file to drive."""
    lines = LADDER_SH.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip().endswith("<<'PY'")]
    assert len(starts) == 1, f"harness/ladder.sh no longer has exactly one PY block: {starts}"
    ends = [i for i, line in enumerate(lines) if line.rstrip("\n") == "PY" and i > starts[0]]
    assert ends, "harness/ladder.sh has no terminator for its PY block"
    block = "".join(lines[starts[0] + 1:ends[0]])
    assert "useful_ctx" in block, "the block lifted out of harness/ladder.sh does not compute useful_ctx"
    path = tmp_path / "row_computation.py"
    path.write_text(block, encoding="utf-8")
    return path


def run_row(tmp_path: Path, sweep_table: str, depth_log: str, fail_rung: str = "",
            fail_msg: str = "") -> tuple:
    """Drive the computation with one set of rung outputs; return its result and the ladder JSON it wrote."""
    sweep_path = tmp_path / "ctx-sweep.log"
    sweep_path.write_text(sweep_table, encoding="utf-8")
    depth_path = tmp_path / "depth-probe.log"
    depth_path.write_text(depth_log, encoding="utf-8")
    out_path = tmp_path / "ladder.json"
    argv = [
        str(out_path), "long", "/models/a-model.gguf", str(CTX), "q8_0", "q8_0", "0", "-fa on", "1800",
        "long", "SUITE-1@test", str(FAR_END), "64000", "52000",
        str(tmp_path / "no-bench-model.json"), "", str(sweep_path), str(depth_path),
        "", "", fail_rung, fail_msg,
    ]
    result = subprocess.run(
        [sys.executable, str(row_computation(tmp_path))] + argv,
        capture_output=True, text=True,
    )
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    return result, doc


def printed_row(stdout: str) -> dict:
    """The registry row the computation prints, which is what an operator pastes into the registry."""
    start = stdout.index("{")
    depth = 0
    for i, ch in enumerate(stdout[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(stdout[start:i + 1])
    raise AssertionError(f"no registry row in:\n{stdout}")


def test_a_probe_the_server_never_answered_leaves_useful_ctx_unset(tmp_path):
    result, doc = run_row(
        tmp_path, SWEEP_TABLE, DEPTH_NEVER_ANSWERED,
        fail_rung="depth-no-answer", fail_msg="the server never answered the depth probe",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    probe = doc["rungs"]["depth_probe"]
    assert probe["score"] is None and probe["pass"] is None, probe
    assert probe["not_measured"], probe
    assert doc["computed"]["useful_ctx"] is None, doc["computed"]
    assert printed_row(result.stdout)["useful_ctx"] is None, result.stdout


def test_the_same_rungs_with_an_answered_probe_do_compute_a_useful_ctx(tmp_path):
    """The control on the test above: these rung outputs reach the computation and fill the field."""
    result, doc = run_row(tmp_path, SWEEP_TABLE, DEPTH_PASSED)
    assert result.returncode == 0, result.stdout + result.stderr
    useful = doc["computed"]["useful_ctx"]
    assert isinstance(useful, int) and 8000 < useful <= CTX, doc["computed"]
    assert printed_row(result.stdout)["useful_ctx"] == useful, result.stdout


def test_a_window_rung_that_reported_no_far_point_leaves_useful_ctx_unset(tmp_path):
    """A point the sweep refuses as not measured prints no row, and there is no curve to extend."""
    result, doc = run_row(tmp_path, SWEEP_SHALLOW_ONLY, DEPTH_PASSED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert doc["rungs"]["window_sweep"] is not None, doc["rungs"]
    assert str(FAR_END) not in doc["rungs"]["window_sweep"], doc["rungs"]["window_sweep"]
    assert doc["computed"]["useful_ctx"] is None, doc["computed"]
    assert printed_row(result.stdout)["useful_ctx"] is None, result.stdout
