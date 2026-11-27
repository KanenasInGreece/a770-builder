"""Stop authority, failed stops, stop requests and suite lifecycle of a qualification.

Docker, the engine API and the suite are stand-ins. Every process signalled here was
started by these tests.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# test_qualify_lib puts harness/ on sys.path, so it is imported before qualify_lib.
from test_qualify_lib import _min_row  # noqa: F401
import qualify_lib as ql
from test_qualify_e2e import _run as _run_e2e, _serve_engine, _world
from test_qualify_run import QUALIFY, _gguf, _jobs, _one, _run, _wait_file
from test_qualify_run import box  # noqa: F401  (box is a fixture)
from test_review_r6_fixes import (PROFILE, RUN_SUITE_SH, _base_env, _make_exec,
                                  _one_stage_suite)
from test_serve_compose import GGUF, _env as _serve_env, _fake_runtime
from test_serve_compose import _run as _serve

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
ID_A, ID_B = "a" * 64, "b" * 64


def _state(job: Path) -> dict:
    return json.loads((job / "status.json").read_text(encoding="utf-8"))


def _functions(tmp_path: Path, body: str, *, engine="llama.cpp"):
    """Run the real qualify.sh functions on a job, with a recording stop script."""
    job = tmp_path / "job"
    for name in ("phases", "evidence"):
        (job / name).mkdir(parents=True, exist_ok=True)
    (job / "plan.json").write_text(json.dumps({"engine": engine}), encoding="utf-8")
    calls = tmp_path / "calls.log"
    fake = tmp_path / "fake-harness"
    _make_exec(fake / "serve_compose.sh",
               f'echo "$*" >> "{calls}"; echo stopped-output; exit "${{STOP_RC:-0}}"')
    text = Path(QUALIFY).read_text(encoding="utf-8")
    prefix = text.split("[ $# -ge 1 ] || usage", 1)[0].replace(
        'here=$(cd "$(dirname "$0")" && pwd)', f'here="{ROOT / "harness"}"', 1)
    funcs = tmp_path / "funcs.sh"
    funcs.write_text(prefix, encoding="utf-8")
    env = _base_env(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    script = f'set +e\n. "{funcs}"\nhere="{fake}"\njob="{job}"\nSTUB=0\n{body}\n'
    run = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                         text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    seen = calls.read_text().splitlines() if calls.exists() else []
    return job, seen, run.stdout


# --- 2.1 stop authority


@pytest.mark.parametrize(
    "serve_log",
    [None, "llama already up (same argv)\n", "started\n", "directory", "symlink"],
)
def test_t7_a_reused_container_is_never_stopped(tmp_path, serve_log):
    job = tmp_path / "job"
    (job / "phases").mkdir(parents=True)
    (job / "cid").write_text(ID_A + "\n")
    (job / "owned.cids").write_text("")
    log = job / "phases" / "serve.log"
    if serve_log == "directory":
        log.mkdir()
    elif serve_log == "symlink":
        log.symlink_to(tmp_path / "elsewhere")
    elif serve_log is not None:
        log.write_text(serve_log)
    _job, calls, out = _functions(
        tmp_path, 'stop_server; echo "rc=$?"; release_suite_server; echo "rc=$?"')
    assert calls == [] and out.split() == ["rc=0", "rc=0"]


def test_t7_owned_pairs_are_stopped_newest_first_with_the_latest_invocation(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "cid").write_text("c" * 64 + "\n")
    (job / "owned.cids").write_text(f"{ID_A} one\n{ID_B} two\n{ID_A} three\n")
    _job, calls, _out = _functions(tmp_path, "stop_server")
    assert calls == [f"stop-id {ID_A} three", f"stop-id {ID_B} two"]


@pytest.mark.parametrize("kind", ["symlink", "directory", "read-only"])
def test_t7_a_container_that_cannot_be_listed_is_stopped_and_start_exits_3(
        tmp_path, kind):
    if kind == "read-only" and os.geteuid() == 0:
        pytest.skip("root can append to a read-only file")
    bin_, log = _fake_runtime(tmp_path, running=False, ps_id=ID_A)
    owned = tmp_path / "owned.cids"
    target = tmp_path / "target"
    target.write_text("kept\n")
    if kind == "symlink":
        owned.symlink_to(target)
    elif kind == "directory":
        owned.mkdir()
    else:
        owned.write_text("")
        owned.chmod(0o400)
    env = _serve_env(tmp_path, bin_, A770B_OWNED_CIDS_FILE=str(owned),
                     A770B_HEALTH_TRIES="3", A770B_HEALTH_PAUSE="0",
                     XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    got = _serve(env, "start", GGUF, "8192")
    assert got.returncode == 3, got.stdout + got.stderr
    assert "cannot record container" in got.stderr
    state = json.loads((tmp_path / "docker.state.json").read_text())
    assert state["containers"] == []
    assert f"stop {ID_A}" in log.read_text()
    assert target.read_text() == "kept\n"


def test_a_refused_cid_write_fails_the_load(tmp_path):
    server = None
    try:
        env = _world(tmp_path, port=9, engine="llama.cpp")
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        hook = tmp_path / "pre.sh"
        _make_exec(hook, f'rm -f "$1/cid"; ln -s "{tmp_path}/elsewhere" "$1/cid"')
        env["A770B_QUALIFY_PRE"] = str(hook)
        job, code, text = _fit_only(env)
        assert code == 4, text
        status = _state(job)
        assert (status["state"], status["reason"]) == ("failed", "load"), status
        assert not (tmp_path / "elsewhere").exists()
        # The container this start recorded is still stopped.
        docker = json.loads((tmp_path / "docker.state.json").read_text())
        assert docker["containers"] == []
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def _fit_only(env):
    """Run the real llama.cpp runner without a suite."""
    args = ["bash", str(QUALIFY), "start", "--foreground", "--engine", "llama.cpp",
            "--model", "sample.gguf", "--ctx", "16384", "--backend", "vulkan",
            "--tokenizer", env["_E2E_TOKENIZER"], "--fit-only"]
    code, out, err = _run_e2e(env, args, 180)
    job = Path(next(line.split(" ", 1)[1] for line in out.splitlines()
                    if line.startswith("directory ")))
    return job, code, out + err[-3000:]


# A vLLM or SGLang load takes the second call site.
def test_both_capture_call_sites_fail_the_load():
    text = Path(QUALIFY).read_text(encoding="utf-8")
    assert text.count("capture_cid || { fail_job load; return; }") == 2


# --- 2.2 a failed stop is reported


def _failing_stop(box, tmp_path, body):
    """Replace the stand-in stop command. It still records its call."""
    script = Path(box["PATH"]) / "a770b-qualify-stop"
    script.unlink()
    script.write_text('#!/bin/sh\nprintf \'%s\\n\' "$0 $*" >> "$A770B_QUALIFY_ARGV"\n'
                      + body + "\n")
    script.chmod(0o755)


def _pre_hook(box, tmp_path, body):
    hook = tmp_path / "pre.sh"
    hook.write_text("#!/bin/sh\n" + body + "\n")
    hook.chmod(0o755)
    box["A770B_QUALIFY_PRE"] = str(hook)


def _stops(box) -> int:
    return Path(box["A770B_QUALIFY_ARGV"]).read_text().count("a770b-qualify-stop ")


def _assert_stop_server_failure(box, job):
    status = _state(job)
    assert status["state"] == "failed" and status["reason"] == "stop-server", status
    assert (job / "FAILED").is_file() and not (job / "DONE").exists()
    assert _run(box, ["wait", job.name, "--timeout", "20"]).returncode == 4


def test_t8_a_failing_stop_command_fails_the_job_and_runs_no_suite(box, tmp_path):
    _failing_stop(box, tmp_path, "exit 1")
    got = _run(box, ["start", "--foreground", *_gguf(box)])
    assert got.returncode == 4, got.stdout + got.stderr
    job = _one(box)
    _assert_stop_server_failure(box, job)
    assert "a770b-qualify-suite" not in Path(box["A770B_QUALIFY_ARGV"]).read_text()
    # Phase 3, then finish and its one retry.
    assert _stops(box) == 3


def test_t8_a_directory_at_the_stop_log_fails_the_job_but_still_stops(box, tmp_path):
    _pre_hook(box, tmp_path, 'mkdir "$1/phases/stop-server.log"')
    got = _run(box, ["start", "--foreground", *_gguf(box)])
    assert got.returncode == 4, got.stdout + got.stderr
    _assert_stop_server_failure(box, _one(box))
    assert _stops(box) == 1
    assert "a770b-qualify-suite" not in Path(box["A770B_QUALIFY_ARGV"]).read_text()


def test_t8_an_absent_stop_log_is_created_and_the_job_finishes(box):
    got = _run(box, ["start", "--foreground", *_gguf(box)])
    assert got.returncode == 0, got.stdout + got.stderr
    job = _one(box)
    assert _state(job)["state"] == "finished"
    assert "a770b-qualify-stop" in (job / "phases" / "stop-server.log").read_text()
    assert _stops(box) == 1


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
@pytest.mark.parametrize("name", ["stop-server", "stop-owned"])
def test_t8_an_unusable_log_does_not_skip_the_stop_command(tmp_path, name, kind):
    job = tmp_path / "job"
    (job / "phases").mkdir(parents=True)
    (job / "owned.cids").write_text(f"{ID_A} one\n")
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n")
    log = job / "phases" / f"{name}.log"
    if kind == "directory":
        log.mkdir()
    elif kind == "symlink":
        log.symlink_to(secret)
    else:
        os.mkfifo(log)
    function = "stop_server" if name == "stop-server" else "release_suite_server"
    _job, calls, out = _functions(
        tmp_path, f'{function}; echo "rc=$? stopped=${{SERVER_STOPPED:-0}}"')
    assert calls == [f"stop-id {ID_A} one"]
    assert out.split()[0] != "rc=0", out
    if name == "stop-server":
        assert "stopped=1" in out
    assert secret.read_text() == "sentinel\n"


def test_t8_a_failed_owned_stop_returns_failure(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "owned.cids").write_text(f"{ID_A} one\n{ID_B} two\n")
    _job, calls, out = _functions(
        tmp_path,
        'export STOP_RC=1; stop_server; echo "rc=$? stopped=${SERVER_STOPPED:-0}"\n'
        'release_suite_server; echo "rc=$?"')
    assert out.split() == ["rc=1", "stopped=0", "rc=1"]
    assert len(calls) == 4


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_t8_a_blocked_owned_log_ends_a_real_runner_failed(tmp_path, kind):
    server = None
    try:
        env = _world(tmp_path, port=9, engine="llama.cpp")
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        hook = tmp_path / "pre.sh"
        make = ('mkdir "$1/phases/stop-owned.log"' if kind == "directory"
                else f'ln -s "{tmp_path}/elsewhere" "$1/phases/stop-owned.log"')
        _make_exec(hook, make)
        env["A770B_QUALIFY_PRE"] = str(hook)
        job, code, text = _fit_only(env)
        assert code == 4, text
        status = _state(job)
        assert status["state"] == "failed" and status["reason"] == "stop-server", status
        waited = subprocess.run(
            ["bash", str(QUALIFY), "wait", job.name, "--timeout", "20"], env=env,
            capture_output=True, text=True, timeout=60)
        assert waited.returncode == 4
        # Phase 3's own stop worked, and the container is gone.
        assert "stopped container" in (job / "phases" / "stop-server.log").read_text()
        assert not (tmp_path / "elsewhere").exists()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def _stop_during_serve(box):
    box["A770B_QUALIFY_PAUSE_PHASE"] = "serve"
    proc = subprocess.Popen(["bash", QUALIFY, "start", *_gguf(box)], env=box,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job = None
    deadline = time.time() + 20
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "cid").exists() and (found[0] / "cid").read_text():
            job = found[0]
            break
        time.sleep(0.1)
    assert job is not None
    assert _run(box, ["stop", job.name], timeout=40).returncode == 0
    proc.communicate(timeout=20)
    return job


def test_t8_finish_retries_a_failed_stop_command_once(box, tmp_path):
    flag = tmp_path / "failed-once"
    _failing_stop(box, tmp_path,
                  f'[ -e "{flag}" ] && exit 0; : > "{flag}"; exit 1')
    job = _stop_during_serve(box)
    status = _state(job)
    assert (status["state"], status["reason"]) == ("stopped", "stopped"), status
    assert _stops(box) == 2


def test_t8_a_stopping_job_stays_stopped_and_names_the_failed_stop(box, tmp_path):
    _failing_stop(box, tmp_path, "exit 1")
    job = _stop_during_serve(box)
    status = _state(job)
    assert (status["state"], status["reason"]) == ("stopped", "stop-server"), status
    assert (job / "STOPPED").is_file() and not (job / "FAILED").exists()


# --- 2.3 and 2.4 stop requests


def test_t9_a_request_that_precedes_finish_ends_stopped(box, tmp_path):
    # The request exists and no signal arrives, as when stop-run ends the suite first.
    _pre_hook(box, tmp_path, ': > "$1/STOP_REQUESTED"')
    got = _run(box, ["start", "--foreground", *_gguf(box)])
    assert got.returncode == 4, got.stdout + got.stderr
    job = _one(box)
    assert (job / "STOPPED").is_file() and not (job / "DONE").exists()
    status = _state(job)
    assert (status["state"], status["reason"]) == ("stopped", "stopped"), status
