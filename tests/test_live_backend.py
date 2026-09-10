#!/usr/bin/env python3
"""Tests for harness/live_backend.py — the JSON sidecar that names which image is up.

`/health` proves a server answers; it does not prove which image. These tests drive the
sidecar module with temp paths and a short-lived python3 sleeper when a live pid is
needed. They never start llama-server, never bind a port, and never open the card.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE_BACKEND_PY = ROOT / "harness" / "live_backend.py"
SERVE_SH = ROOT / "harness" / "serve_a770_llamacpp.sh"

RECORD = {
    "card": "a770",
    "backend": "vulkan",
    "mode": "inference",
    "model": "Qwen3.5-9B-Q4_K_M.gguf",
    "profile": "long",
    "pid": 12345,
}


def _module():
    spec = importlib.util.spec_from_file_location("live_backend", LIVE_BACKEND_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live_backend = _module()


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


def _record(**overrides):
    row = dict(RECORD)
    row.update(overrides)
    return row


def test_write_then_read_with_a_live_pidfile_returns_the_same_object(tmp_path, live_pid):
    sidecar = tmp_path / "logs" / "live-backend.json"
    pidfile = tmp_path / "llamacpp-a770.pid"
    record = _record(pid=live_pid)
    live_backend.write(str(sidecar), record)
    pidfile.write_text(f"{live_pid}\n", encoding="utf-8")
    assert live_backend.read(str(sidecar), str(pidfile)) == record


def test_read_with_a_dead_pid_returns_none_and_the_sidecar_file_is_gone(tmp_path):
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
    live_backend.write(str(sidecar), _record(pid=dead))
    pidfile.write_text(f"{dead}\n", encoding="utf-8")
    assert live_backend.read(str(sidecar), str(pidfile)) is None
    assert not sidecar.exists()


def test_read_with_a_missing_pidfile_returns_none_and_the_sidecar_file_is_gone(tmp_path):
    sidecar = tmp_path / "live-backend.json"
    live_backend.write(str(sidecar), _record())
    assert live_backend.read(str(sidecar), str(tmp_path / "missing.pid")) is None
    assert not sidecar.exists()


def test_read_of_missing_sidecar_returns_none(tmp_path):
    pidfile = tmp_path / "llamacpp-a770.pid"
    pidfile.write_text("1\n", encoding="utf-8")
    assert live_backend.read(str(tmp_path / "missing.json"), str(pidfile)) is None


def test_write_with_model_containing_slash_raises_valueerror(tmp_path):
    with pytest.raises(ValueError):
        live_backend.write(str(tmp_path / "s.json"), _record(model="models/Qwen3.5-9B-Q4_K_M.gguf"))


def test_write_with_mode_gpu_raises_valueerror(tmp_path):
    with pytest.raises(ValueError):
        live_backend.write(str(tmp_path / "s.json"), _record(mode="gpu"))


def test_clear_of_a_missing_path_does_not_raise(tmp_path):
    live_backend.clear(str(tmp_path / "no-such.json"))


def test_serve_stop_removes_the_sidecar(tmp_path):
    data = tmp_path / "data"
    logs = data / "logs"
    logs.mkdir(parents=True)
    sidecar = logs / "live-backend.json"
    sidecar.write_text("{}\n", encoding="utf-8")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("A770B_")}
    env["A770B_PROJECT"] = str(ROOT)
    env["A770B_DATA"] = str(data)
    env["A770B_REFUSE"] = "/nonexistent"
    env["A770B_CARD_MODE"] = "display"
    env["XDG_CONFIG_HOME"] = str(xdg)
    result = subprocess.run(
        ["bash", str(SERVE_SH), "stop"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert not sidecar.exists()
