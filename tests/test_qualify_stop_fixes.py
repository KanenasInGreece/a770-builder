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


@pytest.fixture
def planted(box, tmp_path):
    """box with its own data directory, for hand-written pids.

    box's teardown signals every pid recorded under its own data directory, and these
    tests record pids it must not signal.
    """
    return dict(box, A770B_DATA=str(tmp_path / "planted"))


def _queued_job(box, tmp_path) -> Path:
    plan = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "qualify_lib.py"), "plan", *_gguf(box)],
        env=box, capture_output=True, text=True, timeout=30)
    assert plan.returncode == 0, plan.stderr
    path = tmp_path / "plan.json"
    path.write_text(plan.stdout)
    root = Path(box["A770B_DATA"]) / "qualify"
    root.mkdir(parents=True)
    job = root / json.loads(plan.stdout)["job_id"]
    job.mkdir(mode=0o700)
    made = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "qualify_lib.py"), "init-job",
         "--job", str(job), "--plan", str(path), "--launch", "setsid"],
        env=box, capture_output=True, text=True, timeout=30)
    assert made.returncode == 0, made.stderr
    assert _state(job)["state"] == "queued" and _state(job)["pid"] is None
    return job


def test_t10_a_queued_job_is_stopped_and_its_late_runner_runs_nothing(planted, tmp_path):
    job = _queued_job(planted, tmp_path)
    stopper = subprocess.Popen(["bash", QUALIFY, "stop", job.name], env=planted,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert _wait_file(job / "STOP_REQUESTED"), stopper.poll()
        # cmd_stop publishes no terminal state of its own.
        assert not (job / "STOPPED").exists()
        runner = _run(planted, ["run", "--job", str(job)])
        _out, err = stopper.communicate(timeout=30)
    finally:
        if stopper.poll() is None:
            stopper.kill()
            stopper.communicate()
    assert stopper.returncode == 0, err
    assert runner.returncode == 4, runner.stdout + runner.stderr
    assert (job / "STOPPED").is_file()
    assert _state(job)["state"] == "stopped"
    assert "phase preflight" not in (job / "events.log").read_text()
    assert "a770b-qualify-gate" not in Path(planted["A770B_QUALIFY_ARGV"]).read_text()


def test_t10_a_queued_job_with_a_pid_keeps_the_identity_check(planted, tmp_path):
    job = _queued_job(planted, tmp_path)
    ql.status_merge(str(job), {"pid": os.getpid(),
                               "pid_starttime": ql.proc_starttime(os.getpid())})
    got = _run(planted, ["stop", job.name])
    assert got.returncode == 2 and "identity does not match" in got.stderr
    assert not (job / "STOP_REQUESTED").exists()


def test_t10_an_unreadable_status_keeps_the_identity_check(planted, tmp_path):
    job = _queued_job(planted, tmp_path)
    (job / "status.json").write_text("{not json")
    got = _run(planted, ["stop", job.name])
    assert got.returncode == 2 and "identity does not match" in got.stderr
    assert not (job / "STOP_REQUESTED").exists()


# --- 2.5 and 2.6 dead runners


def _dead_pid():
    child = subprocess.Popen(["sleep", "30"])
    start = ql.proc_starttime(child.pid)
    child.kill()
    child.wait()
    return child.pid, start


def _planted(box, name, state, pid, start, marker=None) -> Path:
    job = Path(box["A770B_DATA"]) / "qualify" / name
    job.mkdir(parents=True)
    (job / "status.json").write_text(json.dumps(
        {"state": state, "pid": pid, "pid_starttime": start,
         "started": "2020-01-01T00:00:00Z", "trips": []}))
    if marker:
        (job / marker).write_text("")
    return job


def test_t11_a_marker_with_a_dead_runner_is_lost(planted):
    job = _planted(planted, "job-dead", "running", *_dead_pid(), marker="DONE")
    got = _run(planted, ["wait", job.name, "--timeout", "10"])
    assert got.returncode == 4, got.stdout + got.stderr
    status = _state(job)
    assert status["state"] == "lost"
    assert status["reason"] == "runner exited after its terminal marker"


@pytest.mark.parametrize("state,live", [("running", True), ("finished", False)])
def test_t11_a_live_runner_and_a_finished_job_are_unchanged(planted, state, live):
    pid, start = ((os.getpid(), ql.proc_starttime(os.getpid())) if live
                  else _dead_pid())
    job = _planted(planted, "job-kept", state, pid, start, marker="DONE")
    before = (job / "status.json").read_bytes()
    assert _run(planted, ["status", job.name]).returncode == 0
    assert (job / "status.json").read_bytes() == before


def test_t12_list_shows_what_status_shows(planted, tmp_path):
    dead = _planted(planted, "a-dead", "running", *_dead_pid())
    broken = _planted(planted, "b-broken", "running", *_dead_pid())
    real = tmp_path / "real-status.json"
    (broken / "status.json").rename(real)
    (broken / "status.json").symlink_to(real)
    _planted(planted, "c-done", "finished", *_dead_pid(), marker="DONE")
    listed = _run(planted, ["list"])
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == [
        "a-dead lost", "b-broken unknown", "c-done finished"]
    shown = _run(planted, ["status", dead.name])
    assert json.loads(shown.stdout)["state"] == "lost"


# --- 2.7 one suite at a time


def _suite_script(box, body):
    script = Path(box["PATH"]) / "a770b-qualify-suite"
    script.unlink()
    script.write_text('#!/bin/sh\nprintf \'%s\\n\' "$0 $*" >> "$A770B_QUALIFY_ARGV"\n'
                      + body + "\n")
    script.chmod(0o755)


@pytest.mark.parametrize("blocked", ["suite.pid", "suite.start"])
def test_t13_an_unrecordable_suite_fails_the_job_and_leaves_no_child(
        box, tmp_path, blocked):
    survived = tmp_path / "survived"
    _suite_script(box, f'sleep 4; : > "{survived}"')
    _pre_hook(box, tmp_path, f'mkdir "$1/{blocked}"')
    got = _run(box, ["start", "--foreground", "--refs-repeat", "2", *_gguf(box)])
    assert got.returncode == 4, got.stdout + got.stderr
    job = _one(box)
    status = _state(job)
    assert (status["state"], status["reason"]) == ("failed", "suite"), status
    time.sleep(5)
    assert not survived.exists()
    argv = (job / "evidence" / "argv.log").read_text()
    started = sum(line.startswith("suite: ") for line in argv.splitlines())
    # A refused clear is seen before the start. A failed write is seen after it.
    assert started == (0 if blocked == "suite.pid" else 1)
    if blocked == "suite.pid":
        assert "a770b-qualify-suite" not in Path(box["A770B_QUALIFY_ARGV"]).read_text()


def test_a_finished_suite_clears_its_recorded_pid(box):
    got = _run(box, ["start", "--foreground", *_gguf(box)])
    assert got.returncode == 0, got.stdout + got.stderr
    job = _one(box)
    assert (job / "suite.pid").read_text() == ""
    assert (job / "suite.start").read_text() == ""


# --- 2.8 and 2.9b run_suite.sh


def _suite_run(tmp_path, lb_body, **extra):
    env = _base_env(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "xdg"), **extra)
    suite = _one_stage_suite(tmp_path, ("s1-frontend",))
    ran = tmp_path / "ran"
    env["A770B_LOCAL_BUILD"] = str(_make_exec(
        tmp_path / "fake-lb.sh",
        f'if [ "$1" = run ]; then : > "{ran}"; {lb_body}\nexit 1; fi; exit 0'))
    got = subprocess.run(
        ["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"),
         "--suite", str(suite)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    return got, ran


def test_t14_an_uncleared_pid_file_stops_the_suite_before_any_stage(tmp_path):
    blocked = tmp_path / "serving.pid"
    blocked.mkdir()
    got, ran = _suite_run(tmp_path, "true",
                          A770B_QUALIFY_SERVING_PID_FILE=str(blocked))
    assert got.returncode == 2, got.stdout + got.stderr
    assert "cannot clear the serving pid file" in got.stderr
    assert not ran.exists()


def test_a_later_failed_publish_warns_once_and_does_not_end_the_stage(tmp_path):
    path = tmp_path / "serving.pid"
    got, ran = _suite_run(tmp_path, f'rm -f "{path}"; mkdir "{path}"; sleep 2',
                          A770B_QUALIFY_SERVING_PID_FILE=str(path))
    assert ran.exists()
    assert got.stderr.count("could not publish the serving pid") == 1, got.stderr
    assert "recording as run_error" in got.stderr


@pytest.mark.parametrize("target", ["listing", "broken"])
def test_t16_a_symlinked_owned_list_stops_and_removes_nothing(tmp_path, target):
    listing = tmp_path / "listing"
    if target == "listing":
        listing.write_text(f"{ID_A} one\n")
    owned = tmp_path / "caller.cids"
    owned.write_text("")
    calls = tmp_path / "stops.log"
    serve = _make_exec(tmp_path / "fake-serve.sh", f'echo "$*" >> "{calls}"')
    got, ran = _suite_run(
        tmp_path, f'rm -f "{owned}"; ln -s "{listing}" "{owned}"',
        A770B_LADDER_RUN="1", A770B_OWNED_CIDS_FILE=str(owned),
        A770B_SERVE_SCRIPT=str(serve))
    assert ran.exists(), got.stdout + got.stderr
    assert not calls.exists() or "stop-id" not in calls.read_text()
    assert owned.is_symlink()
    assert listing.exists() == (target == "listing")
    assert "owned-container list is a symlink" in got.stderr
    assert "could not stop a server" in got.stderr
