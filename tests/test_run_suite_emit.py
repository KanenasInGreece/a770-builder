#!/usr/bin/env python3
"""Tests for harness/run_suite.sh emit_stage: counters, serve_evidence, counts_source."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_SUITE_SH = ROOT / "harness" / "run_suite.sh"


def extract_emit_stage() -> str:
    cmd = ["awk", r"/^emit_stage\(\)\{/{p=1} p{print} p && /^PY$/{py=1} py && /^}$/{exit}", str(RUN_SUITE_SH)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def run_emit_stage(requests="", prompt_tokens="", gen_tokens="", counters_json="null", serve_evidence=""):
    emit_stage_fn = extract_emit_stage()
    script = f"""
set -euo pipefail
RESULTS_NDJSON=$(mktemp)
trap 'rm -f "$RESULTS_NDJSON"' EXIT
{emit_stage_fn}
emit_stage "s-test" "python" "lbl" "true" "0" "10" "100" "true" "null" "5" \\
  "{requests}" "{prompt_tokens}" "{gen_tokens}" "[]" "true" "[]" "pass" \\
  '{counters_json}' "{serve_evidence}"
cat "$RESULTS_NDJSON"
"""
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout.strip())


def test_emit_stage_with_counters_and_empty_log_parse():
    counters = {
        "engine": "vllm",
        "finished_requests": 3,
        "request_prompt_tokens": 300,
        "request_generation_tokens": 150,
        "prompt_tokens": 350,
        "generation_tokens": 175,
    }
    data = run_emit_stage(
        requests="",
        prompt_tokens="",
        gen_tokens="",
        counters_json=json.dumps(counters),
        serve_evidence="/tmp/test.evidence.json",
    )
    assert data["requests"] == 3
    assert data["prompt_tokens"] == 300
    assert data["gen_tokens"] == 150
    assert data["counts_source"] == "metrics"
    assert data["counters"] == counters
    assert data["serve_evidence"] == "/tmp/test.evidence.json"


def test_emit_stage_with_requests_from_log_keeps_log_source():
    counters = {
        "engine": "llama.cpp",
        "finished_requests": 2,
        "prompt_tokens": 200,
        "generation_tokens": 100,
    }
    data = run_emit_stage(
        requests="5",
        prompt_tokens="500",
        gen_tokens="250",
        counters_json=json.dumps(counters),
        serve_evidence="/tmp/test.evidence.json",
    )
    assert data["requests"] == 5
    assert data["prompt_tokens"] == 500
    assert data["gen_tokens"] == 250
    assert data["counts_source"] == "log"
    assert data["counters"] == counters
    assert data["serve_evidence"] == "/tmp/test.evidence.json"


def test_emit_stage_without_counters_defaults_log():
    data = run_emit_stage(
        requests="1",
        prompt_tokens="50",
        gen_tokens="25",
        counters_json="null",
        serve_evidence="",
    )
    assert data["requests"] == 1
    assert data["prompt_tokens"] == 50
    assert data["gen_tokens"] == 25
    assert data["counts_source"] == "log"
    assert data["counters"] is None
    assert data["serve_evidence"] is None
