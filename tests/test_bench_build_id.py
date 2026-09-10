#!/usr/bin/env python3
"""Tests for harness/bench_build_id.py — the build id a speed measurement records as `speed.bench.tool`.

`harness/bench_speed.sh` used to take that id from the first line of `llama-bench --version`, which on this
Vulkan build is the graphics driver's platform warning; the benchmark's own raw JSON carries `build_number`
and `build_commit` instead, and those are what the row must name. The decision lives in a module of its own
precisely so it can be checked here: these tests drive it with a synthetic raw file and need no card, no
llama-bench and no server.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_BUILD_ID_PY = ROOT / "harness" / "bench_build_id.py"


def _module():
    spec = importlib.util.spec_from_file_location("bench_build_id", BENCH_BUILD_ID_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bench_build_id = _module()


def _raw_row(**overrides) -> dict:
    """One row of llama-bench's `-o json` output, trimmed to the fields read here."""
    row = {
        "build_commit": "86b351fd6",
        "build_number": 10805,
        "model_filename": "Qwen3.5-9B-Q4_K_M.gguf",
        "n_prompt": 8192,
        "n_gen": 0,
        "n_depth": 0,
        "avg_ts": 620.5,
    }
    row.update(overrides)
    return row


def write_raw(path: Path, raw) -> Path:
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def run_cli(*args) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(BENCH_BUILD_ID_PY)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


# --- the raw JSON names the build ---


def test_raw_with_build_fields_names_that_build():
    raw = [_raw_row(), _raw_row(n_prompt=0, n_gen=128, avg_ts=37.2)]
    assert bench_build_id.build_id_from_raw(raw, "the driver's warning") == "b10805 (86b351fd6)"


def test_raw_results_object_shape_is_read_too():
    raw = {"results": [_raw_row()]}
    assert bench_build_id.build_id_from_raw(raw, "the driver's warning") == "b10805 (86b351fd6)"


def test_build_number_already_prefixed_is_not_prefixed_twice():
    raw = [_raw_row(build_number="b10805")]
    assert bench_build_id.build_id_from_raw(raw, "") == "b10805 (86b351fd6)"


def test_file_with_build_fields_names_that_build(tmp_path):
    path = write_raw(tmp_path / "raw.json", [_raw_row()])
    assert bench_build_id.build_id_from_file(str(path), "the driver's warning") == "b10805 (86b351fd6)"


def test_cli_prints_the_build_the_raw_file_names(tmp_path):
    path = write_raw(tmp_path / "raw.json", [_raw_row()])
    result = run_cli(str(path), "the driver's warning")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "b10805 (86b351fd6)"


# --- no build fields: the caller's own fallback (--version, then the git describe) stands ---


def test_raw_without_build_fields_falls_back(tmp_path):
    row = _raw_row()
    del row["build_commit"]
    del row["build_number"]
    path = write_raw(tmp_path / "raw.json", [row])
    assert bench_build_id.build_id_from_file(str(path), "llama-bench b9999") == "llama-bench b9999"


def test_empty_build_fields_fall_back(tmp_path):
    path = write_raw(tmp_path / "raw.json", [_raw_row(build_commit="", build_number=None)])
    assert bench_build_id.build_id_from_file(str(path), "llama-bench b9999") == "llama-bench b9999"


def test_unreadable_or_unparseable_raw_falls_back(tmp_path):
    bad = tmp_path / "raw.json"
    bad.write_text("not json at all", encoding="utf-8")
    assert bench_build_id.build_id_from_file(str(bad), "llama-bench b9999") == "llama-bench b9999"
    assert bench_build_id.build_id_from_file(str(tmp_path / "missing.json"), "llama-bench b9999") == "llama-bench b9999"


def test_no_build_fields_and_no_fallback_is_unknown(tmp_path):
    path = write_raw(tmp_path / "raw.json", [])
    assert bench_build_id.build_id_from_file(str(path), "") == "unknown"


def test_cli_never_fails_on_a_missing_file(tmp_path):
    result = run_cli(str(tmp_path / "missing.json"), "llama-bench b9999")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "llama-bench b9999"
