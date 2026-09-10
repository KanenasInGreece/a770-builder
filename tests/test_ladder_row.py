#!/usr/bin/env python3
"""Tests for harness/ladder_row.py — the ladder's last step, which writes its results document and prints the
registry row.

Two things are checked here.

**A real run's numbers, end to end.** `test_the_salvaged_run_produces_its_recorded_results_and_row` drives
`ladder_row.py` with the five rung outputs of one real ladder run and a fixed generation timestamp, and compares
the whole results document and the whole printed row against the expected fixtures, field by field. The run is
`ladder.sh long` of 2026-09-09 (pid 1167651, an A770 at a 262,144-token window), which was externally terminated
during rung 6, before `ladder.sh` ever reached this step; its rung outputs survived on disk and are copied into
`tests/data/` with the measuring machine's absolute paths replaced by neutral ones (the model file reads
`/models/…`, the sweep's corpus `/project/…`), consistently, so the fixtures stay internally coherent:

  * `long-rungs12-20260909-215915.json`  rungs 1-2, the load and the probes (was `long-rungs12-salvaged-…`)
  * `long-bench-20260909-222214.json`    rung 3, the standard speed rung (llama-bench)
  * `ladder-ctx-sweep-20260909.log`      rung 4, the window sweep      (was `ladder-ctx-sweep-1167651.log`)
  * `ladder-depth-probe-20260909.log`    rung 5, the graded depth probe (was `ladder-depth-probe-1167651.log`)
  * `long-suite-20260909-224236.json`    rung 6, 4 of the suite's 7 stages (the kill landed mid-stage)

The two ladder-state arguments, the failed rung and its message, are passed empty: all five rung files are
present, so this is the document the step writes when every rung handed it something. What the fixtures pin down:
`useful_ctx` 262144 — computed from the two sweep points and clamped to the served window because the graded probe
passed 3/3; the far end, 99,847 tokens at 11.5 tokens a second decoding and 159.0 prefilling after 580.7 seconds;
`vram_gib_after_load` 9.485649108886719; and the suite's as-delivered medians under `speed.delivered` and *not*
under `suite`, which `harness/profiles.py` would refuse.

**`useful_ctx` is never filled from something that was not measured.** It is the largest depth at which decode
stays above four tokens a second: the ladder reads decode time-per-token at the shallow point and at the far end
out of the window rung's own table, extends it linearly between them, and caps the result at the depth probe's
last passing depth. Every input is a measurement, so every case where a measurement is missing has to leave the
field unset rather than fill it from something else. The two that matter:

  * a depth probe the server never answered reports "not measured", not a score. The model was never asked, so
    no window has any quality behind it and `useful_ctx` is unset. A number carried over from the requested
    window, or from the sweep alone, would read as a depth whose quality had been checked.
  * a window rung that reported no far point leaves nothing to extend the curve to, and `useful_ctx` is unset
    for the same reason. A point the sweep refuses as not measured prints no row in that table.
  * a depth probe that was answered and failed caps the window at 8,000, the sweep's own shallow point — but
    only when the sweep measured a window to cap. With no usable pair of sweep points the failure leaves the
    field unset too, rather than publishing an 8,000 no rung of that run measured.

Those five cases are driven with synthetic rung logs, so they need no card, no server and no run: exactly the
inputs the ladder would hand the step, and the ladder JSON and printed row it writes out.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LADDER_ROW = ROOT / "harness" / "ladder_row.py"
DATA = ROOT / "tests" / "data"

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
DEPTH_FAILED = f"depth probe at {FAR_END}: 1/3\n"

# the fixed generation timestamp: harness/ladder.sh passes the current time here, a test passes its own
GENERATED = "2026-09-09T23:15:00"


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
        "", "", fail_rung, fail_msg, GENERATED,
    ]
    result = subprocess.run(
        [sys.executable, str(LADDER_ROW)] + argv,
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


def run_salvaged_row(out_path: Path):
    """Drive the step with the five rung outputs of the terminated run of 2026-09-09, from tests/data/.

    Paths are passed relative to the repository root, and the step is run from there, so the one path it records
    (rungs.depth_probe.log) is the same on every machine.
    """
    suite_json = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "suite_report.py"),
         "tests/data/long-suite-20260909-224236.json", "--json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    argv = [
        str(out_path), "long", "/models/Qwen3.5-9B-Q4_K_M.gguf", "262144", "q8_0", "q8_0", "off",
        "--temp 0.7 --top-p 0.8 --top-k 20 --min-p 0.0 --presence-penalty 1.5", "1500", "long",
        "SUITE-1@0.1.5", "100000", "", "",
        "tests/data/long-rungs12-20260909-215915.json", "tests/data/long-bench-20260909-222214.json",
        "tests/data/ladder-ctx-sweep-20260909.log", "tests/data/ladder-depth-probe-20260909.log",
        suite_json, "tests/data/long-suite-20260909-224236.json", "", "", GENERATED,
    ]
    result = subprocess.run(
        [sys.executable, str(LADDER_ROW)] + argv,
        cwd=ROOT, capture_output=True, text=True,
    )
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    return result, doc


def test_the_salvaged_run_produces_its_recorded_results_and_row(tmp_path):
    """One real run's five rung outputs, in: the whole results document and the whole printed row, out."""
    result, doc = run_salvaged_row(tmp_path / "ladder.json")
    assert result.returncode == 0, result.stdout + result.stderr
    row = printed_row(result.stdout)

    expected_doc = json.loads((DATA / "long-ladder-20260909-expected.json").read_text(encoding="utf-8"))
    expected_row = json.loads((DATA / "long-ladder-20260909-expected-row.json").read_text(encoding="utf-8"))
    for key in expected_doc:
        assert doc[key] == expected_doc[key], key
    assert doc == expected_doc
    for key in expected_row:
        assert row[key] == expected_row[key], key
    assert row == expected_row

    # the figures this fixture exists to hold still, named one by one
    assert doc["computed"]["useful_ctx"] == 262144 and row["useful_ctx"] == 262144
    assert doc["computed"]["vram_gib_after_load"] == 9.485649108886719
    assert doc["computed"]["speed"]["far_end"] == {
        "tokens": 99847, "decode_tps": 11.5, "prefill_tps": 159.0, "ttft_s": 580.7,
    }
    assert doc["rungs"]["depth_probe"]["score"] == 3 and doc["rungs"]["depth_probe"]["pass"] is True
    # the as-delivered medians belong under speed, never under suite: harness/profiles.py rejects a row that
    # carries them under suite, and a printed row that has to be hand-edited is the one thing it exists to avoid
    assert row["speed"]["delivered"] == {
        "prefill_tps": 178.0, "decode_tps": 35.4, "source": "long-suite-20260909-224236.json",
    }
    assert "delivered" not in row["suite"], row["suite"]
    assert doc["generated"] == GENERATED


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


def test_a_failed_probe_over_a_sweep_with_no_window_leaves_useful_ctx_unset(tmp_path):
    """The cap is a cap: with nothing measured to cap, a failed probe fills the field no more than an
    unanswered one does. 8,000 here would be a window no rung of this run measured."""
    result, doc = run_row(tmp_path, SWEEP_SHALLOW_ONLY, DEPTH_FAILED)
    assert result.returncode == 0, result.stdout + result.stderr
    probe = doc["rungs"]["depth_probe"]
    assert probe["score"] == 1 and probe["pass"] is False, probe
    assert not probe["not_measured"], probe
    assert doc["computed"]["useful_ctx"] is None, doc["computed"]
    assert printed_row(result.stdout)["useful_ctx"] is None, result.stdout


def test_a_failed_probe_over_a_measured_window_still_caps_at_the_shallow_point(tmp_path):
    """The control on the test above, and the behaviour the cap exists for: these are the same rung outputs
    that compute a window above 8,000 with a passing probe, and the failure brings the row back to 8,000."""
    result, doc = run_row(tmp_path, SWEEP_TABLE, DEPTH_FAILED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert doc["rungs"]["depth_probe"]["pass"] is False, doc["rungs"]["depth_probe"]
    assert doc["computed"]["useful_ctx"] == 8000, doc["computed"]
    assert printed_row(result.stdout)["useful_ctx"] == 8000, result.stdout
