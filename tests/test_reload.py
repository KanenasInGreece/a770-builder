#!/usr/bin/env python3
"""Tests for reload refuse / cost / rollback decisions.

Helper tests drive harness/live_backend.py. Wiring tests drive serve() in
skills/local-build/scripts/local-build.sh with A770B_DATA in a temp dir and a
stub SERVE that does not start llama-server, bind a GPU, or open the card.
Those wiring tests fail if the bash reload branch is deleted while the Python
helpers stay.
"""

import copy
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE_BACKEND_PY = ROOT / "harness" / "live_backend.py"
LOCAL_BUILD_SH = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
PROFILES_JSON = ROOT / "config" / "profiles.json"

BUSY = "reload refused: the server is busy"
REMOTE = "reload refused: remote URL cannot switch backend"
SENTENCE = (
    "reload: stop the live backend, start sycl, load Qwen3.5-9B-Q4_K_M.gguf"
)
QWEN = "Qwen3.5-9B-Q4_K_M.gguf"

STUB_SERVE = r"""#!/usr/bin/env bash
set -u
log="${SERVE_LOG:?}"
if [ "$1" = start ]; then
  printf 'start profile=%s card=%s backend=%s mode=%s\n' \
    "${A770B_LIVE_PROFILE-}" "${A770B_LIVE_CARD-}" \
    "${A770B_LIVE_BACKEND-}" "${A770B_LIVE_MODE-}" >> "$log"
else
  printf '%s\n' "$1" >> "$log"
fi
case "$1" in
  stop) exit 0 ;;
  start)
    if [ "${SERVE_FAIL_START:-0}" = 1 ]; then exit 1; fi
    exit 0
    ;;
  *) exit 2 ;;
esac
"""


def _module():
    spec = importlib.util.spec_from_file_location("live_backend", LIVE_BACKEND_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live_backend = _module()


def test_reload_blocked_when_lock_held():
    assert live_backend.reload_blocked(True, False) == BUSY


def test_reload_blocked_when_run_pid_alive():
    assert live_backend.reload_blocked(False, True) == BUSY


def test_reload_blocked_when_neither_is_none():
    assert live_backend.reload_blocked(False, False) is None


def test_reload_sentence_for_sycl_qwen():
    assert live_backend.reload_sentence("sycl", "Qwen3.5-9B-Q4_K_M.gguf") == SENTENCE


def test_remote_cannot_switch_non_loopback():
    assert live_backend.remote_cannot_switch("10.0.0.1") is True


def test_remote_cannot_switch_loopback():
    assert live_backend.remote_cannot_switch("127.0.0.1") is False


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}\n')

    def log_message(self, *_args):
        return


@pytest.fixture
def live_pid():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _chmod_x(path: Path) -> Path:
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _registry_with_sycl(path: Path) -> Path:
    data = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    row = copy.deepcopy(data["profiles"]["long"])
    row["backend"] = "sycl"
    data["profiles"]["long-sycl"] = row
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_models(models: Path, registry: Path) -> None:
    data = json.loads(registry.read_text(encoding="utf-8"))
    models.mkdir(parents=True, exist_ok=True)
    for prof in data["profiles"].values():
        (models / prof["model"]).write_text("dummy\n", encoding="utf-8")


def _serve_env(tmp_path: Path, *, fail_start: bool = False) -> dict:
    data = tmp_path / "data"
    models = tmp_path / "models"
    xdg = tmp_path / "xdg"
    logs = data / "logs"
    logs.mkdir(parents=True)
    xdg.mkdir()
    registry = _registry_with_sycl(tmp_path / "profiles.json")
    _write_models(models, registry)
    stub = tmp_path / "stub-serve.sh"
    stub.write_text(STUB_SERVE, encoding="utf-8")
    _chmod_x(stub)
    llama = tmp_path / "llama-server"
    llama.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    _chmod_x(llama)
    serve_log = tmp_path / "serve.log"
    serve_log.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("A770B_")}
    env.update(
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(data),
        A770B_MODELS=str(models),
        A770B_REFUSE="/nonexistent",
        A770B_CARD_MODE="display",
        A770B_PROFILES_FILE=str(registry),
        A770B_HOST="127.0.0.1",
        A770B_PORT="9",
        A770B_LLAMA_BIN=str(llama),
        A770B_API_KEY_FILE=str(xdg / "api.key"),
        A770B_ALLOW_NO_NVTOP="1",
        XDG_CONFIG_HOME=str(xdg),
        SERVE=str(stub),
        SERVE_LOG=str(serve_log),
        SERVE_FAIL_START="1" if fail_start else "0",
    )
    return env


def _write_live_sidecar(env: dict, pid: int) -> None:
    logs = Path(env["A770B_DATA"]) / "logs"
    live_backend.write(
        str(logs / "live-backend.json"),
        {
            "card": "a770",
            "backend": "vulkan",
            "mode": "display",
            "model": QWEN,
            "profile": "long",
            "pid": pid,
        },
    )
    (logs / "llamacpp-a770.pid").write_text(f"{pid}\n", encoding="utf-8")


def _run_serve(env: dict, profile: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LOCAL_BUILD_SH), "serve", profile],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=timeout,
    )


def _log_lines(env: dict) -> list[str]:
    path = Path(env["SERVE_LOG"])
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_serve_busy_reload_prints_busy_and_does_not_stop(tmp_path, live_pid):
    env = _serve_env(tmp_path)
    _write_live_sidecar(env, live_pid)
    run_sh = tmp_path / "sandbox_run.sh"
    run_sh.write_text("#!/usr/bin/env bash\nsleep 60\n", encoding="utf-8")
    busy = subprocess.Popen(
        ["timeout", "60", "bash", str(run_sh)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        (Path(env["A770B_DATA"]) / "logs" / "run.pid").write_text(
            f"{busy.pid}\n", encoding="utf-8"
        )
        result = _run_serve(env, "long-sycl")
    finally:
        busy.kill()
        busy.wait()
    assert BUSY in result.stdout
    assert result.returncode != 0
    lines = _log_lines(env)
    assert "stop" not in lines
    assert not any(line.startswith("start ") for line in lines)


def test_serve_remote_backend_switch_refuses_and_does_not_stop(tmp_path, live_pid):
    env = _serve_env(tmp_path)
    env["A770B_HOST"] = "10.0.0.1"
    _write_live_sidecar(env, live_pid)
    result = _run_serve(env, "long-sycl")
    assert REMOTE in result.stdout
    assert result.returncode != 0
    lines = _log_lines(env)
    assert "stop" not in lines
    assert not any(line.startswith("start ") for line in lines)


def test_serve_failed_reload_stops_before_starting_the_old_profile(tmp_path, live_pid):
    env = _serve_env(tmp_path, fail_start=True)
    _write_live_sidecar(env, live_pid)
    result = _run_serve(env, "long-sycl")
    assert "reload failed: rolling back to long" in result.stdout
    assert "rollback failed: server is down" in result.stdout
    assert result.returncode != 0
    lines = _log_lines(env)
    i_new = next(
        i for i, line in enumerate(lines)
        if line.startswith("start ") and "profile=long-sycl" in line
    )
    i_old = next(
        i for i, line in enumerate(lines)
        if line.startswith("start ") and "profile=long " in line
    )
    assert i_old > i_new
    assert any(lines[i] == "stop" for i in range(i_new + 1, i_old))
    assert "stop" in lines[i_old + 1 :]


def test_serve_exports_live_identity_on_successful_stub_start(tmp_path):
    env = _serve_env(tmp_path)
    httpd = HTTPServer(("127.0.0.1", 0), _Health)
    env["A770B_PORT"] = str(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_serve(env, "long", timeout=30)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    starts = [line for line in _log_lines(env) if line.startswith("start ")]
    assert starts
    last = starts[-1]
    assert "card=a770" in last
    assert "backend=vulkan" in last
    assert "mode=display" in last
    assert "profile=long" in last
