#!/usr/bin/env python3
"""The worker group survives a SIGKILL of the leader group, and only the guarded helper ends it.

run_lead.py publishes logs/run.worker-pgid (or A770B_WORKER_PGID_FILE) after posix_spawn and removes it
only after it has reaped the worker. A SIGKILL leaves the file. signal_recorded_worker_group — the helper
build_local.sh and stop-run both call — signals that group only when the pid exists, leads its own group,
and its /proc start time still matches. The identity is checked once; a later member of the group is still
killed. The orphan watch does not hold the card lock, and it exits when build_local.sh is gone. No GPU, no
Docker, no live checkout. SANDBOX=1 is not exercised. The integration tests run the real build_local.sh and
stop-run with SANDBOX=0.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from test_review_r6_fixes import PROFILE, ROOT, _base_env, _git_seat, _lock_free, _make_exec, _with_opencode_v1

RUN_LEAD = ROOT / "harness" / "run_lead.py"
GUARD = ROOT / "harness" / "guard.sh"
BUILD_LOCAL = ROOT / "harness" / "build_local.sh"
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"


def _stat_fields(pid: int) -> list[str]:
    text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    return text.rsplit(")", 1)[1].split()


def _alive(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except FileNotFoundError:
        return False
    state = text.rsplit(")", 1)[1].split()[0]
    return state not in {"Z", "X"}


def _sleeps_of(worker: int) -> list[int]:
    """The worker itself when it is sleep, and any sleep whose parent is the worker."""
    found: list[int] = []
    if _alive(worker) and b"sleep" in Path(f"/proc/{worker}/cmdline").read_bytes():
        found.append(worker)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == worker or not _alive(pid):
            continue
        try:
            fields = _stat_fields(pid)
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (FileNotFoundError, IndexError, ProcessLookupError):
            continue
        if fields[1] == str(worker) and b"sleep" in cmd:
            found.append(pid)
    return found


def _signal(env: dict, record: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c",
         f'. "{ROOT}/harness/env.sh"; . "{GUARD}"; signal_recorded_worker_group "$1"',
         "signal_recorded_worker_group", str(record)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def _wait_until(predicate, timeout: float, detail: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(detail)


def test_sigkill_of_the_leader_group_leaves_the_worker_until_the_helper_runs(tmp_path):
    """The review's reproduce: SIGKILL the leader group, then the same helper must end the sleep.

    A copy of the record with a different start time must not be signalled.
    """
    env = _base_env(tmp_path)
    record = tmp_path / "run.worker-pgid"
    env["A770B_WORKER_PGID_FILE"] = str(record)
    proc = subprocess.Popen(
        [sys.executable, str(RUN_LEAD), "--", "bash", "-c", "sleep 300"],
        cwd=ROOT, env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    sleeps: list[int] = []
    try:
        def published_and_sleeping() -> bool:
            if not record.is_file():
                return False
            parts = record.read_text().split()
            if len(parts) != 2 or not parts[0].isdigit():
                return False
            return bool(_sleeps_of(int(parts[0])))

        _wait_until(published_and_sleeping, 5, "the leader did not publish a live sleep")
        worker_s, start = record.read_text().strip().split()
        worker = int(worker_s)
        assert _stat_fields(worker)[2] == str(worker)          # the published pid leads its own group
        assert _stat_fields(worker)[19] == start
        sleeps = _sleeps_of(worker)
        assert sleeps, "sleep was not running in the worker group"
        os.killpg(proc.pid, signal.SIGKILL)
        assert proc.wait(timeout=5) is not None
        assert record.read_text().strip() == f"{worker} {start}", "SIGKILL removed or rewrote the worker record"
        assert all(_alive(pid) for pid in sleeps), "the leader's death reached the worker group"

        mismatched = tmp_path / "mismatched-starttime"
        mismatched.write_text(f"{worker} {int(start) + 1}\n", encoding="ascii")
        refused = _signal(env, mismatched)
        assert refused.returncode != 0, refused.stderr
        assert all(_alive(pid) for pid in sleeps), "a mismatched start time was signalled: " + refused.stderr

        ended = _signal(env, record)
        assert ended.returncode == 0, ended.stderr

        def sleeps_gone() -> bool:
            return not any(_alive(pid) for pid in sleeps)

        _wait_until(sleeps_gone, 5, f"the helper left sleep running: {sleeps}")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        for pid in sleeps:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_the_leader_removes_the_default_record_only_after_reaping_the_worker(tmp_path):
    env = _base_env(tmp_path)
    assert "A770B_WORKER_PGID_FILE" not in env
    record = Path(env["A770B_DATA"]) / "logs" / "run.worker-pgid"
    proc = subprocess.Popen(
        [sys.executable, str(RUN_LEAD), "--", "bash", "-c", "sleep 0.4"],
        cwd=ROOT, env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        _wait_until(record.is_file, 5, "the default record was not logs/run.worker-pgid")
        text = record.read_text().strip()
        worker = int(text.split()[0])
        assert _alive(worker)
        rc = proc.wait(timeout=5)
        assert rc == 0, proc.stderr.read() if proc.stderr else rc
        assert not record.exists(), "the record outlived the reap"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def _term_ignored(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        if line.startswith("SigIgn:"):
            return bool(int(line.split()[1], 16) & (1 << (signal.SIGTERM - 1)))
    return False


def _pgrp_of(pid: int) -> str:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    return raw.rsplit(")", 1)[1].split()[2]


def _same_group_build_local(owner: int) -> list[int]:
    """Other live bash processes in the owner's group whose command is build_local.sh.

    With start_new_session the owner's pid is the process-group id. The orphan watch is one of these;
    the leader has left the group, and the fake opencode is not this script.
    """
    found: list[int] = []
    owner_s = str(owner)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == owner:
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii", errors="replace")
            cmd = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        tail = raw.rsplit(")", 1)[-1].split()
        if len(tail) < 3 or tail[0] in {"Z", "X"}:
            continue
        if tail[2] == owner_s and b"build_local.sh" in cmd:
            found.append(pid)
    return found


def _group_alive(pgid: int) -> bool:
    pgid_s = str(pgid)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        tail = raw.rsplit(")", 1)[-1].split()
        if len(tail) >= 3 and tail[0] not in {"Z", "X"} and tail[2] == pgid_s:
            return True
    return False


def _start_build(tmp_path: Path, opencode_body: str):
    env = _base_env(tmp_path)
    seat = _git_seat(tmp_path / "seat")
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    _make_exec(stub / "opencode", _with_opencode_v1(opencode_body))
    env.update(SANDBOX="0", PROFILE=PROFILE, PATH=f"{stub}:{env['PATH']}")
    proc = subprocess.Popen(
        ["bash", str(BUILD_LOCAL), str(seat), "brief text"],
        cwd=ROOT, env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return env, proc


def _stop_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def test_helper_kills_a_child_that_ignores_term_after_the_recorded_pid_exits(tmp_path):
    """The recorded pid is not the only member. It exits on TERM; a child in its group ignores TERM.

    The leader is SIGKILL'd first, so nothing reaps that child back into a live forwarder. The helper
    must still KILL the group. The old re-check returned when the recorded pid disappeared.
    """
    env = _base_env(tmp_path)
    record = tmp_path / "run.worker-pgid"
    child_file = tmp_path / "child.pid"
    env["A770B_WORKER_PGID_FILE"] = str(record)
    env["CHILD_PID"] = str(child_file)
    script = 'trap "" TERM; sleep 300 & echo $! > "$CHILD_PID"; trap "exit 0" TERM; wait'
    proc = subprocess.Popen(
        [sys.executable, str(RUN_LEAD), "--", "bash", "-c", script],
        cwd=ROOT, env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    child = 0
    worker = 0
    try:
        def armed() -> bool:
            if not record.is_file() or not child_file.is_file():
                return False
            parts = record.read_text().split()
            text = child_file.read_text().strip()
            if len(parts) != 2 or not parts[0].isdigit() or not text.isdigit():
                return False
            recorded, kid = int(parts[0]), int(text)
            if recorded == kid or not _alive(recorded) or not _alive(kid):
                return False
            return _term_ignored(kid) and _pgrp_of(kid) == str(recorded)

        _wait_until(armed, 5, "the worker group did not publish a child that ignores TERM")
        worker = int(record.read_text().split()[0])
        child = int(child_file.read_text().strip())
        os.killpg(proc.pid, signal.SIGKILL)
        assert proc.wait(timeout=5) is not None
        assert _alive(worker) and _alive(child), "the leader's death reached the worker group"
        assert not _term_ignored(worker)

        ended = _signal(env, record)
        assert ended.returncode == 0, ended.stderr
        _wait_until(lambda: not _alive(worker) and not _alive(child) and not _group_alive(worker),
                    5, f"the helper left the group: worker={worker} child={child} alive={_alive(child)}")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        for pid in (worker, child):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_sigkill_of_build_local_drops_the_card_lock_and_the_watch_exits(tmp_path):
    """SIGKILL build_local.sh itself. Its traps do not run. The watch must not keep the card lock, and
    it must exit once that pid is gone. SANDBOX=0, fake opencode sleeps. No GPU, no Docker.
    """
    env, proc = _start_build(tmp_path, "exec sleep 300")
    lock = Path(env["A770B_DATA"]) / "logs" / "local-build.lock"
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    record = Path(env["A770B_DATA"]) / "logs" / "run.worker-pgid"
    watch = 0
    worker = 0
    try:
        def watch_running() -> bool:
            if not (pidfile.is_file() and pidfile.read_text().strip().isdigit()):
                return False
            watches = _same_group_build_local(proc.pid)
            return len(watches) == 1 and not _lock_free(lock)

        _wait_until(watch_running, 15, "the orphan watch was not running with the card lock held")
        watch = _same_group_build_local(proc.pid)[0]
        if record.is_file():
            text = record.read_text().split()
            if text and text[0].isdigit():
                worker = int(text[0])
        os.kill(proc.pid, signal.SIGKILL)                       # not the group: the watch must notice by itself
        # The owner is a zombie until waited, so kill -0 still sees it and the watch is still alive.
        # The lock has to be free anyway: the watch was started with FD 9 closed.
        assert _wait_until_true(lambda: _lock_free(lock), 2), "the watch still holds the card lock after SIGKILL"
        assert _alive(watch), "the watch exited before the owner pid was reaped; the lock check is vacuous"
        assert proc.wait(timeout=5) is not None                 # reap the zombie so kill -0 of the owner fails
        _wait_until(lambda: not _alive(watch), 5, f"the orphan watch {watch} outlived build_local.sh")
        assert _lock_free(lock)
    finally:
        if proc.poll() is None:
            _stop_group(proc.pid)
            proc.wait(timeout=5)
        else:
            _stop_group(proc.pid)
        if worker:
            _stop_group(worker)
        if watch:
            try:
                os.kill(watch, signal.SIGKILL)
            except OSError:
                pass


def _wait_until_true(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def test_sigkill_of_the_leader_makes_build_local_return_137_and_no_worker_remains(tmp_path):
    """The production watch, not a hand call: SIGKILL the leader named by run.pid. build_local.sh must
    return 137 while the worker no longer holds the tee pipe. A watch that never starts, a wrong
    worker-pgid path, or a RUNPID re-read that never signals leaves sleep on that pipe and times out.
    """
    env, proc = _start_build(tmp_path, "exec sleep 300")
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    record = Path(env["A770B_DATA"]) / "logs" / "run.worker-pgid"
    worker = 0
    try:
        def leader_and_worker() -> bool:
            if not pidfile.is_file() or not record.is_file():
                return False
            leader_s = pidfile.read_text().strip()
            parts = record.read_text().split()
            if not leader_s.isdigit() or len(parts) != 2 or not parts[0].isdigit():
                return False
            leader, recorded = int(leader_s), int(parts[0])
            if leader == proc.pid or not _alive(leader) or not _alive(recorded):
                return False
            return bool(_sleeps_of(recorded))

        _wait_until(leader_and_worker, 15, "build_local.sh did not publish a live leader and worker")
        leader = int(pidfile.read_text().strip())
        worker = int(record.read_text().split()[0])
        assert _pgrp_of(worker) == str(worker)
        os.kill(leader, signal.SIGKILL)
        rc = proc.wait(timeout=20)
        out = (proc.stdout.read() if proc.stdout else "") + (proc.stderr.read() if proc.stderr else "")
        assert rc == 137, out
        assert not _alive(worker) and not _group_alive(worker), f"worker {worker} still in the group"
        worker = 0
    finally:
        if proc.poll() is None:
            _stop_group(proc.pid)
            proc.wait(timeout=5)
        else:
            _stop_group(proc.pid)
        if worker:
            _stop_group(worker)


def test_stop_run_kills_a_worker_that_ignores_term_and_the_run_is_143(tmp_path):
    """stop-run's real 30s wait, then the same helper. The worker ignores TERM, so the leader stays
    until the escalation. The run is reported interrupted (143). No shorter clock.
    """
    env, proc = _start_build(tmp_path, 'trap "" TERM; exec sleep 300')
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    record = Path(env["A770B_DATA"]) / "logs" / "run.worker-pgid"
    worker = 0
    try:
        def ignoring() -> bool:
            if not pidfile.is_file() or not record.is_file():
                return False
            parts = record.read_text().split()
            if len(parts) != 2 or not parts[0].isdigit():
                return False
            recorded = int(parts[0])
            return _alive(recorded) and _term_ignored(recorded) and _pgrp_of(recorded) == str(recorded)

        _wait_until(ignoring, 15, "the worker was not running with TERM ignored")
        worker = int(record.read_text().split()[0])
        started = time.monotonic()
        stop = subprocess.run(
            ["bash", str(LOCAL_BUILD), "stop-run"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=55)
        elapsed = time.monotonic() - started
        assert stop.returncode == 0, stop.stdout + stop.stderr
        assert elapsed >= 29, f"stop-run did not take the production 30s wait ({elapsed:.1f}s)"
        rc = proc.wait(timeout=20)
        out = (proc.stdout.read() if proc.stdout else "") + (proc.stderr.read() if proc.stderr else "")
        assert rc == 143, out
        assert "143" in out, out
        assert not _alive(worker) and not _group_alive(worker), f"worker group {worker} survived stop-run"
        worker = 0
    finally:
        if proc.poll() is None:
            _stop_group(proc.pid)
            proc.wait(timeout=5)
        else:
            _stop_group(proc.pid)
        if worker:
            _stop_group(worker)


def test_build_local_and_stop_run_call_the_same_helper():
    helper = "signal_recorded_worker_group"
    guard = GUARD.read_text(encoding="utf-8")
    assert f"{helper}()" in guard
    build = BUILD_LOCAL.read_text(encoding="utf-8")
    stop = LOCAL_BUILD.read_text(encoding="utf-8")
    assert helper in build
    assert "-eq 137" in build
    # stop-run's escalation is the branch after the 30s wait, not a signal by process name.
    tail = stop[stop.index("for _ in $(seq 1 30)"):stop.index("run pid $pid ended")]
    assert helper in tail
    assert "nothing escalated" not in tail
    assert "process name" in tail
