#!/usr/bin/env python3
"""Tests for `harness/profiles.py menu` — ready / also / the cold full slice.

A tiny registry in tmp_path that `check` accepts: the shipped display file plus a
second row `long-sycl` (same GGUF, different backend) and a `b580` row that must
not appear in ready or also when the sidecar's card is a770. A sidecar and a
pidfile name a live sleep process (teardown kills it). Never llama-server, never
the network, never the card.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILES_JSON = ROOT / "config" / "profiles.json"
PROFILES_PY = ROOT / "harness" / "profiles.py"

SYCL_CTX = 131072
SYCL_USEFUL_CTX = 32768
SYCL_DECODE_8K = 12.5
SYCL_USE_FOR = "The SYCL long row on the same GGUF."
SYCL_MODEL = "Qwen3.5-9B-Q4_K_M.gguf"


def load_base() -> dict:
    return json.loads(PROFILES_JSON.read_text(encoding="utf-8"))


def run(*args, env=None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROFILES_PY)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def menu_registry(tmp_path: Path) -> tuple[Path, dict]:
    """A check-accepted registry with long-sycl (same model) and a b580 row."""
    data = load_base()
    long_sycl = copy.deepcopy(data["profiles"]["long"])
    long_sycl["backend"] = "sycl"
    long_sycl["ctx"] = SYCL_CTX
    long_sycl["useful_ctx"] = SYCL_USEFUL_CTX
    long_sycl["speed"]["decode_tps"]["8k"] = SYCL_DECODE_8K
    long_sycl["use_for"] = SYCL_USE_FOR
    data["profiles"]["long-sycl"] = long_sycl

    other = copy.deepcopy(data["profiles"]["long"])
    other["card"] = "b580"
    other["category"] = "moe"
    other["weight_class"] = "4b"
    other["backend"] = "cuda"
    data["profiles"]["other"] = other

    path = write_json(tmp_path / "p.json", data)
    checked = run("check", "--file", str(path))
    assert checked.returncode == 0, checked.stderr
    return path, data


@pytest.fixture
def live_pid():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def write_sidecar(path: Path, pid: int, **overrides) -> dict:
    record = {
        "card": "a770",
        "backend": "vulkan",
        "mode": "display",
        "model": SYCL_MODEL,
        "profile": "long",
        "pid": pid,
    }
    record.update(overrides)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return record


def write_pidfile(path: Path, pid: int) -> Path:
    path.write_text(f"{pid}\n", encoding="utf-8")
    return path


def parse(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_menu_no_sidecar_is_cold_full_slice(tmp_path):
    path, data = menu_registry(tmp_path)
    result = run("menu", "--file", str(path))
    payload = parse(result)
    assert payload["state"] == "cold"
    assert payload["ready"] == []
    assert payload["also"] == []
    names = [row["name"] for row in payload["slice"]]
    assert "long" in names
    assert "long-sycl" in names
    assert names == list(data["profiles"].keys())
    assert payload["default"] == "long"
    for row in payload["slice"]:
        assert set(row) == {
            "name", "card", "backend", "mode", "model",
            "ctx", "useful_ctx", "decode_tps_8k", "use_for",
        }


def test_menu_live_vulkan_ready_and_sycl_also(tmp_path, live_pid):
    path, data = menu_registry(tmp_path)
    sidecar = tmp_path / "live-backend.json"
    pidfile = tmp_path / "llamacpp-a770.pid"
    live = write_sidecar(sidecar, live_pid)
    write_pidfile(pidfile, live_pid)

    result = run(
        "menu", "--file", str(path),
        "--sidecar", str(sidecar), "--pidfile", str(pidfile),
    )
    payload = parse(result)
    assert payload["state"] == "ready"
    assert payload["live"] == live
    ready_names = [row["name"] for row in payload["ready"]]
    also_names = [row["name"] for row in payload["also"]]
    assert "long" in ready_names
    assert "long-sycl" not in ready_names
    assert "long-sycl" in also_names
    assert "long" not in also_names
    assert "slice" not in payload
    assert payload["default"] == "long"

    also0 = payload["also"][0]
    model = data["profiles"]["long-sycl"]["model"]
    assert also0["name"] == "long-sycl"
    assert also0["reload"] == f"reload: stop the live backend, start sycl, load {model}"
    assert also0["deltas"]["ctx"] == {
        "ready": data["profiles"]["long"]["ctx"],
        "also": SYCL_CTX,
    }
    assert also0["deltas"]["useful_ctx"] == {
        "ready": data["profiles"]["long"]["useful_ctx"],
        "also": SYCL_USEFUL_CTX,
    }
    assert also0["deltas"]["decode_tps_8k"] == {
        "ready": data["profiles"]["long"]["speed"]["decode_tps"]["8k"],
        "also": SYCL_DECODE_8K,
    }
    assert also0["deltas"]["use_for"] == {
        "ready": data["profiles"]["long"]["use_for"],
        "also": SYCL_USE_FOR,
    }


def test_menu_dead_pid_is_cold(tmp_path):
    path, _data = menu_registry(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead = proc.pid
    proc.kill()
    proc.wait()
    sidecar = tmp_path / "live-backend.json"
    pidfile = tmp_path / "llamacpp-a770.pid"
    write_sidecar(sidecar, dead)
    write_pidfile(pidfile, dead)

    result = run(
        "menu", "--file", str(path),
        "--sidecar", str(sidecar), "--pidfile", str(pidfile),
    )
    payload = parse(result)
    assert payload["state"] == "cold"
    assert payload["ready"] == []
    assert payload["also"] == []
    names = [row["name"] for row in payload["slice"]]
    assert "long" in names
    assert "long-sycl" in names
    assert payload["default"] == "long"


def test_menu_deltas_absent_when_gguf_filename_differs(tmp_path, live_pid):
    path, data = menu_registry(tmp_path)
    data["profiles"]["long-sycl"]["model"] = "other-model.gguf"
    write_json(path, data)
    sidecar = tmp_path / "live-backend.json"
    pidfile = tmp_path / "llamacpp-a770.pid"
    write_sidecar(sidecar, live_pid)
    write_pidfile(pidfile, live_pid)

    result = run(
        "menu", "--file", str(path),
        "--sidecar", str(sidecar), "--pidfile", str(pidfile),
    )
    payload = parse(result)
    also_names = [row["name"] for row in payload["also"]]
    assert "long-sycl" in also_names
    also0 = next(row for row in payload["also"] if row["name"] == "long-sycl")
    assert "deltas" not in also0
    assert also0["reload"] == "reload: stop the live backend, start sycl, load other-model.gguf"


def test_menu_other_card_is_in_neither_list(tmp_path, live_pid):
    path, _data = menu_registry(tmp_path)
    sidecar = tmp_path / "live-backend.json"
    pidfile = tmp_path / "llamacpp-a770.pid"
    write_sidecar(sidecar, live_pid, card="a770")
    write_pidfile(pidfile, live_pid)

    result = run(
        "menu", "--file", str(path),
        "--sidecar", str(sidecar), "--pidfile", str(pidfile),
    )
    payload = parse(result)
    ready_names = [row["name"] for row in payload["ready"]]
    also_names = [row["name"] for row in payload["also"]]
    assert "other" not in ready_names
    assert "other" not in also_names


def test_menu_file_that_fails_check_exits_2(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["kv"]
    path = write_json(tmp_path / "bad.json", data)
    result = run("menu", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())
    assert "missing key kv" in result.stderr
