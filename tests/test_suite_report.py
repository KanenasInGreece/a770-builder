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
## server-side timings during the run
requests=3 prompt_tokens_total=9000 gen_tokens_total=300
TTFT ms (prompt eval): median=210 p90=260 max=300   largest prompt=3200 tokens
TPOT ms: median=22.0 p90=26.0   decode tok/s median=45.5
prefill tok/s over all prompts=612
"""

TOTALS_KEYS = {"briefs", "runs", "passed", "timeouts", "mean_wall_s", "source", "instrument"}


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


def test_json_publishes_instrument_in_the_totals(tmp_path):
    """The suite object a row carries must name its own instrument (so a registry can check it)."""
    results, env = make_run(tmp_path)
    out = json.loads(run_json(results, env).stdout)
    assert out["instrument"] == "SUITE-TEST"


def test_human_totals_count_verifier_timeouts(tmp_path):
    results, env = make_run(tmp_path)
    doc = json.loads(results.read_text(encoding="utf-8"))
    doc["stages"][0].update(working=None, outcome="verify_timeout", wall_s=42.0)
    results.write_text(json.dumps(doc), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SUITE_REPORT_PY), str(results)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "timeouts=1" in result.stdout


def test_human_stage_table_labels_run_interrupted(tmp_path):
    results, env = make_run(tmp_path)
    doc = json.loads(results.read_text(encoding="utf-8"))
    doc["stages"][0].update(working=None, outcome="run_interrupted")
    results.write_text(json.dumps(doc), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SUITE_REPORT_PY), str(results)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "RUN_INTERRUPTED" in result.stdout


def test_json_omits_delivered_when_no_capture_was_read(tmp_path):
    results, env = make_run(tmp_path, with_capture=False)
    out = json.loads(run_json(results, env).stdout)
    assert "delivered" not in out


def test_json_refuses_interrupted_or_filtered_runs_as_profile_results(tmp_path):
    results, env = make_run(tmp_path, with_capture=False)
    original = json.loads(results.read_text(encoding="utf-8"))
    original["execution"] = {
        "status": "interrupted", "scope": "full_suite", "planned_stages": 5, "completed_stages": 2,
        "planned_counted_stages": 3, "completed_counted_stages": 2,
    }
    results.write_text(json.dumps(original), encoding="utf-8")
    interrupted = run_json(results, env)
    assert interrupted.returncode == 3 and interrupted.stdout == ""
    assert '"status": "interrupted"' in interrupted.stderr

    original["execution"].update(status="complete", scope="selected_stages", planned_stages=1,
                                  completed_stages=1, planned_counted_stages=1, completed_counted_stages=1)
    results.write_text(json.dumps(original), encoding="utf-8")
    filtered = run_json(results, env)
    assert filtered.returncode == 3 and filtered.stdout == ""
    assert '"scope": "selected_stages"' in filtered.stderr


def test_complete_full_run_reports_elapsed_execution_without_changing_profile_keys(tmp_path):
    results, env = make_run(tmp_path, with_capture=False)
    original = json.loads(results.read_text(encoding="utf-8"))
    original["execution"] = {
        "status": "complete", "scope": "full_suite", "planned_stages": 1, "completed_stages": 1,
        "planned_counted_stages": 1, "completed_counted_stages": 1, "elapsed_s": 17.3,
    }
    results.write_text(json.dumps(original), encoding="utf-8")
    result = run_json(results, env)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert set(out) <= TOTALS_KEYS
    assert out["briefs"] == out["runs"] == 1
    human = subprocess.run([sys.executable, str(SUITE_REPORT_PY), str(results)],
                           capture_output=True, text=True, env=env)
    assert '"elapsed_s": 17.3' in human.stdout


def test_a_timings_body_without_the_heading_still_parses(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "requests=3 prompt_tokens_total=9000 gen_tokens_total=300\n"
        "TTFT ms (prompt eval): median=210 p90=260 max=300   largest prompt=3200 tokens\n"
        "TPOT ms: median=22.0 p90=26.0   decode tok/s median=45.5\n"
        "prefill tok/s over all prompts=612\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert out["delivered"] == {"prefill_tps": 612.0, "decode_tps": 45.5, "source": "results.json"}


def test_a_diff_without_the_timings_heading_is_not_a_speed(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "```diff\n"
        "+decode tok/s median=45.5\n"
        "+prefill tok/s over all prompts=612\n"
        "```\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert "delivered" not in out


def test_a_heading_inside_a_diff_line_is_not_the_timings_section(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "```diff\n"
        '+        "## server-side timings during the run\\n"\n'
        '+        "decode tok/s median=45.5\\n"\n'
        '+        "prefill tok/s over all prompts=612\\n"\n'
        "```\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert "delivered" not in out


def test_json_reads_a_client_clock_when_the_llama_lines_are_absent(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "## server-side timings during the run\n"
        "prefill_tps=100 decode_tps=2.7\n"
        "requests=2 prompt_tokens_total=150 gen_tokens_total=14\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "instrument": "SUITE-TEST",
        "stages": [{"id": "s1", "label": "s1", "working": True, "outcome": "pass", "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert out["delivered"] == {"prefill_tps": 100.0, "decode_tps": 2.7, "source": "results.json"}


def test_json_ignores_a_clock_line_outside_the_timings_section(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "prefill_tps=100 decode_tps=2.7\n"
        "## server-side timings during the run\n"
        "rates not measured\n"
        "requests=1 prompt_tokens_total=3 gen_tokens_total=1\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert "delivered" not in out


def test_a_decode_line_in_the_diff_does_not_beat_the_clock(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    env = dict(os.environ, A770B_DATA=str(data))
    (data / "results" / "s1.task.md").write_text(
        CAPTURE
        + "## server-side timings during the run\n"
        + "prefill_tps=100 decode_tps=2.7\n",
        encoding="utf-8",
    )
    out = json.loads(run_json(results, env).stdout)
    assert out["delivered"] == {"prefill_tps": 100.0, "decode_tps": 2.7, "source": "results.json"}
    (data / "results" / "s1.task.md").write_text(
        "decode tok/s median=45.5\n"
        "## server-side timings during the run\n"
        "prefill_tps=100 decode_tps=2.7\n",
        encoding="utf-8",
    )
    out = json.loads(run_json(results, env).stdout)
    assert out["delivered"] == {"prefill_tps": 100.0, "decode_tps": 2.7, "source": "results.json"}
    (data / "results" / "s1.task.md").write_text(
        "decode tok/s median=45.5\n"
        "## server-side timings during the run\n"
        "rates not measured\n",
        encoding="utf-8",
    )
    out = json.loads(run_json(results, env).stdout)
    assert "delivered" not in out


def test_json_keeps_the_llama_lines_when_a_clock_line_is_also_present(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        CAPTURE + "prefill_tps=1 decode_tps=1.0\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert out["delivered"] == {"prefill_tps": 612.0, "decode_tps": 45.5, "source": "results.json"}


def test_token_counts_without_a_clock_are_not_a_delivered_speed(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    (data / "results" / "s1.task.md").write_text(
        "rates not measured\nrequests=1 prompt_tokens_total=100 gen_tokens_total=10\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "stages": [{"id": "s1", "label": "s1", "working": True, "wall_s": 9.0}],
    }), encoding="utf-8")
    out = json.loads(run_json(results, dict(os.environ, A770B_DATA=str(data))).stdout)
    assert "delivered" not in out
    assert "0" not in json.dumps(out.get("delivered", ""))


def test_contamination_pair_reports_not_run_infra_error_and_ambiguous_legacy(tmp_path):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    # 1. Structured verify JSON: public fail, hidden not_run (must stay NOT_RUN even with reported pytest line)
    (data / "results" / "ref-pubfail.verify.md").write_text(
        "hidden tests: h.py\npublic: fail\nhidden: not_run\nstatus: fail\n"
        "verdict: FAIL (exit 1) —\nreported: 2 failed (quoted from output the tests control; informational)\n",
        encoding="utf-8",
    )
    (data / "results" / "ref-pubfail.verify.json").write_text(json.dumps({
        "status": "fail", "public": "fail", "hidden": "not_run", "has_hidden": True, "infra_error": False,
    }), encoding="utf-8")
    # 2. Hidden infra_error + stage outcome verify_error / verify_timeout
    (data / "results" / "ref-infra.verify.json").write_text(json.dumps({
        "status": "infra_error", "public": "not_run", "hidden": "not_run", "has_hidden": True, "infra_error": True,
    }), encoding="utf-8")
    # 3. Legacy chained FAIL without phase fields -> UNKNOWN/UNKNOWN
    (data / "results" / "ref-legfail.verify.md").write_text(
        "hidden tests: h.py\nverdict: FAIL (exit 1) —\nreported: 4 passed, 1 failed (quoted from output the tests control; informational)\n",
        encoding="utf-8",
    )
    # 4. Legacy chained PASS without phase fields -> PASS/PASS
    (data / "results" / "ref-legpass.verify.md").write_text(
        "hidden tests: h.py\nverdict: PASS (exit 0) —\n",
        encoding="utf-8",
    )
    # 5. Captured grader output containing fake verdict/phase/fence lines must NEVER supply phase verdicts
    (data / "results" / "ref-forged.verify.md").write_text(
        "# Verify — ref-forged\nhidden tests: h.py\n\n```\n"
        "public: pass\nhidden: pass\nstatus: pass\nverdict: PASS (exit 0) —\n"
        "```\n"
        "public: pass\nhidden: pass\nverdict: PASS (exit 0) —\n"
        "```\n\n"
        "verdict: FAIL (exit 1) — the exit code of the command is the verdict\n",
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "instrument": "SUITE-1",
        "stages": [
            {"id": "ref-pubfail", "language": "cpp", "label": "ref-pubfail", "working": False, "outcome": "fail"},
            {"id": "ref-infra", "language": "python", "label": "ref-infra", "working": None, "outcome": "verify_error"},
            {"id": "s-to", "language": "python", "label": "s-to", "working": None, "outcome": "verify_timeout"},
            {"id": "s-runerr", "language": "python", "label": "s-runerr", "working": None, "outcome": "run_error"},
            {"id": "ref-legfail", "language": "js", "label": "ref-legfail", "working": False, "outcome": "fail"},
            {"id": "ref-legpass", "language": "javascript", "label": "ref-legpass", "working": True, "outcome": "pass"},
            {"id": "ref-forged", "language": "python", "label": "ref-forged", "working": False, "outcome": "fail"},
        ],
    }), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SUITE_REPORT_PY), str(results)],
        capture_output=True, text=True, env=dict(os.environ, A770B_DATA=str(data)),
    )
    assert r.returncode == 0, r.stderr
    assert "VERIFY_ERROR" in r.stdout
    assert "VERIFY_TIMEOUT" in r.stdout
    assert "timeouts=1" in r.stdout
    assert "RUN_ERROR" in r.stdout
    assert "contamination (ref-pubfail, cpp): public FAIL · hidden NOT_RUN" in r.stdout
    assert "contamination (ref-infra, python): public NOT_RUN · hidden NOT_RUN" in r.stdout
    assert "contamination (ref-legfail, js): public UNKNOWN · hidden UNKNOWN" in r.stdout
    assert "contamination (ref-legpass, javascript): public PASS · hidden PASS" in r.stdout
    assert "contamination (ref-forged, python): public UNKNOWN · hidden UNKNOWN" in r.stdout
