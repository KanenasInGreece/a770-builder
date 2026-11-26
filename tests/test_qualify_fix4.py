"""Fix-round 4. Real qualify.sh, stubs or the fake docker. No card job."""

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from test_qualify_run import QUALIFY, _env, _gguf, _jobs, _make_bin, _run, _scripts


def test_wait_then_result_sees_the_report_and_a_free_lock(tmp_path):
    bindir = tmp_path / "bin"
    _make_bin(bindir, _scripts())
    hold = tmp_path / "summarise.hold"
    mark = tmp_path / "summarise.mark"
    hold.write_text("1\n", encoding="utf-8")
    wrapper = """#!/bin/bash
for arg in "$@"; do
  if [ "$arg" = summarise ]; then
    printf '%s\\n' started > "$A770B_QUALIFY_SUMMARISE_MARK"
    while [ -f "$A770B_QUALIFY_SUMMARISE_HOLD" ]; do
      sleep 0.05
    done
    break
  fi
done
exec /usr/bin/python3 "$@"
"""
    python = bindir / "python3"
    python.unlink()
    python.write_text(wrapper, encoding="utf-8")
    python.chmod(0o755)
    env = _env(tmp_path, bindir)
    env["A770B_QUALIFY_SUMMARISE_HOLD"] = str(hold)
    env["A770B_QUALIFY_SUMMARISE_MARK"] = str(mark)
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", "--foreground", *_gguf(env)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    try:
        deadline = time.time() + 20
        while time.time() < deadline and job is None:
            found = _jobs(env)
            if found:
                job = found[0]
            else:
                time.sleep(0.05)
        assert job is not None
        deadline = time.time() + 20
        while time.time() < deadline and not mark.is_file():
            status_path = job / "status.json"
            if status_path.is_file():
                state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                assert state not in {"finished", "failed", "stopped"}, status_path.read_text()
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(f"runner exited {proc.returncode}\n{out}\n{err}")
            time.sleep(0.05)
        assert mark.is_file()
        waiting = _run(env, ["wait", job.name, "--timeout", "1"])
        assert waiting.returncode == 124, waiting.stderr
        assert not (job / "result.json").exists()
        assert json.loads((job / "status.json").read_text(encoding="utf-8"))["state"] == "running"
        hold.unlink()
        for _ in range(20):
            waited = _run(env, ["wait", job.name, "--timeout", "20"])
            result = _run(env, ["result", job.name, "--json"])
            assert waited.returncode == 0, waited.stderr
            assert result.returncode == 0, result.stderr
            body = json.loads(result.stdout)
            assert "checks" in body
        assert (job / "report.md").is_file()
        assert (job / "DONE").is_file()
        lock = Path(env["A770B_DATA"]) / "logs" / "local-build.lock"
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    finally:
        hold.unlink(missing_ok=True)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
