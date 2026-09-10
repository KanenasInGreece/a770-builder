#!/usr/bin/env python3
"""Tests for harness/suite_report.py's published contract: what `--json` puts on stdout.

`harness/ladder.sh` prints a registry row and takes the suite totals from this output, but moves the
`delivered` object out of them into the row's `speed`, because `speed.delivered` is the registry's only
home for it (harness/profiles.py — see tests/test_profiles.py's KU9 accept/reject pair). That move relies
on this output continuing to carry `delivered` inside the totals, where other callers already read it, so
this pins the producer's end: suite_report.py's own output is not what changed.

Offline: a synthetic results file and a synthetic capture file, no card, no server, no run_suite.sh.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE_REPORT_PY = ROOT / "harness" / "suite_report.py"

CAPTURE = """\
requests=3 prompt_tokens_total=9000 gen_tokens_total=300
TTFT ms (prompt eval): median=210 p90=260 max=300   largest prompt=3200 tokens
TPOT ms: median=22.0 p90=26.0   decode tok/s median=45.5
prefill tok/s over all prompts=612
"""

TOTALS_KEYS = {"briefs", "runs", "passed", "timeouts", "mean_wall_s", "source"}


def make_run(tmp_path: Path, with_capture: bool = True) -> tuple:
    """A results file and the environment suite_report.py reads its stages' captures from. The stage names
    no capture path of its own — the fallback run_suite.sh actually leaves is <A770B_DATA>/results/<label>.task.md."""
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True, exist_ok=True)
    if with_capture:
        (data / "results" / "s1.task.md").write_text(CAPTURE, encoding="utf-8")
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "instrument": "SUITE-TEST",
        "stages": [{"id": "s1", "label": "s1", "working": True, "outcome": "pass", "wall_s": 9.0}],
    }), encoding="utf-8")
    env = dict(os.environ, A770B_DATA=str(data))
    return results, env


def run_json(results: Path, env: dict) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SUITE_REPORT_PY), str(results), "--json"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_json_still_publishes_delivered_in_the_totals(tmp_path):
    results, env = make_run(tmp_path)
    result = run_json(results, env)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["delivered"] == {"prefill_tps": 612.0, "decode_tps": 45.5, "source": "results.json"}


def test_json_totals_keep_their_own_keys(tmp_path):
    results, env = make_run(tmp_path)
    out = json.loads(run_json(results, env).stdout)
    assert set(out) - {"delivered"} <= TOTALS_KEYS
    assert out["briefs"] == 1 and out["runs"] == 1 and out["passed"] == 1 and out["timeouts"] == 0


def test_json_omits_delivered_when_no_capture_was_read(tmp_path):
    results, env = make_run(tmp_path, with_capture=False)
    out = json.loads(run_json(results, env).stdout)
    assert "delivered" not in out
