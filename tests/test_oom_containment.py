#!/usr/bin/env python3
"""Sandbox and OOM containment tests keep A770B_DATA under tmp_path; when the user
systemd manager is available, some default tests also create transient user scopes
under a770b-sandbox.slice and write unit events to the user journal. Real kernel OOM
tests use a test-only MemoryMax on an a770b-test-* scope and require A770B_OOM_TESTS=1
because they write kernel OOM log entries and raise desktop notifications.
"""

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "harness" / "sandbox_run.sh"
OOM_PY = ROOT / "harness" / "sandbox_oom.py"
HOST_MEMORY = ROOT / "harness" / "host_memory.py"
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
RUN_LEAD = ROOT / "harness" / "run_lead.py"
CAPTURE = ROOT / "harness" / "capture_task.sh"
BUILD = ROOT / "harness" / "build_local.sh"
QUALIFY = ROOT / "harness" / "qualify.sh"
RUN_SUITE = ROOT / "harness" / "run_suite.sh"

_OPENCODE = """#!/bin/bash
case "${1:-}" in
  --version) printf '%s\\n' '1.19.6'; exit 0 ;;
esac
if [ "${1:-}" = run ] && [ "${2:-}" = --help ]; then
  printf '%s\\n' '  --dir   directory to run in'
  exit 0
fi
exit 0
"""


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body.startswith("#!") else "#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _env(tmp: Path, **extra) -> dict:
    data = tmp / "data"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    (data / "results").mkdir(parents=True, exist_ok=True)
    (data / "hidden").mkdir(parents=True, exist_ok=True)
    scratch = tmp / "tmp"
    scratch.mkdir(exist_ok=True)
    # An inherited harness path is an operator's file. Drop every A770B_* name
    # before this fixture's own values, so a test cannot unlink or replace it.
    env = {key: value for key, value in os.environ.items() if not key.startswith("A770B_")}
    env["TMPDIR"] = str(scratch)
    env.update(
        HOME=str(tmp / "home"),
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(data),
        A770B_REFUSE=str(tmp / "refused"),
        A770B_MODELS=str(tmp / "models"),
        A770B_UV_CACHE=str(tmp / "uv"),
        A770B_PROFILES_FILE="",
        A770B_ALLOW_NO_NVTOP="1",
        PATH=os.environ.get("PATH", "/usr/bin:/bin"),
    )
    (tmp / "home").mkdir(exist_ok=True)
    (tmp / "models").mkdir(exist_ok=True)
    (tmp / "uv").mkdir(exist_ok=True)
    (tmp / "refused").mkdir(exist_ok=True)
    env.update(extra)
    return env


def test_env_drops_inherited_harness_paths(tmp_path, monkeypatch):
    """A path inherited from the operator stays outside this fixture."""
    outside = tmp_path.parent / f"{tmp_path.name}-operator"
    outside.mkdir()
    try:
        verify_out = outside / "verify.json"
        worker = outside / "worker-pgid"
        record = outside / "sandbox-oom.json"
        verify_out.write_text('{"status":"keep"}\n', encoding="utf-8")
        worker.write_text("1 2\n", encoding="utf-8")
        record.write_text('{"oom":"keep"}\n', encoding="utf-8")
        monkeypatch.setenv("A770B_VERIFY_OUT", str(verify_out))
        monkeypatch.setenv("A770B_WORKER_PGID_FILE", str(worker))
        monkeypatch.setenv("A770B_SANDBOX_RECORD_FILE", str(record))
        monkeypatch.setenv("A770B_CARD_LOCK_PATH", str(outside / "card.lock"))
        monkeypatch.setenv("TMPDIR", str(outside))
        env = _env(tmp_path)
        leaked = [key for key in env if key.startswith("A770B_") and env[key].startswith(str(outside))]
        assert leaked == [], leaked
        assert "A770B_VERIFY_OUT" not in env
        assert "A770B_WORKER_PGID_FILE" not in env
        assert "A770B_SANDBOX_RECORD_FILE" not in env
        assert "A770B_CARD_LOCK_PATH" not in env
        assert env["TMPDIR"] == str(tmp_path / "tmp")
        assert env["A770B_DATA"] == str(tmp_path / "data")
        assert env["A770B_PROJECT"] == str(ROOT)
        own = tmp_path / "own-verify.json"
        replaced = _env(tmp_path, A770B_VERIFY_OUT=str(own), TMPDIR=str(tmp_path / "custom-tmp"))
        assert replaced["A770B_VERIFY_OUT"] == str(own)
        assert replaced["TMPDIR"] == str(tmp_path / "custom-tmp")
        assert verify_out.read_text(encoding="utf-8") == '{"status":"keep"}\n'
        assert worker.read_text(encoding="utf-8") == "1 2\n"
        assert record.read_text(encoding="utf-8") == '{"oom":"keep"}\n'
    finally:
        shutil.rmtree(outside)


def _seat(tmp: Path) -> Path:
    seat = tmp / "seat"
    seat.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=seat, check=True)
    (seat / "README").write_text("seat\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=seat, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=seat, check=True)
    return seat


def _tools(tmp: Path) -> Path:
    bin_ = tmp / "bin"
    _exe(bin_ / "opencode", _OPENCODE)
    _exe(bin_ / "uv", "#!/bin/bash\nprintf '%s\\n' 'uv 0.13.4 (test)'\n")
    return bin_


def _cfg(tmp: Path) -> Path:
    cfg = tmp / "cfg.jsonc"
    cfg.write_text("{}\n", encoding="utf-8")
    return cfg


def _bus() -> bool:
    proc = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True)
    return proc.returncode == 0


def _descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(pid)
    out: list[int] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        for child in children.get(cur, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                stack.append(child)
    return out


def _ppid(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    return int(stat.rsplit(")", 1)[1].split()[1])


def _proc_fds(pid: int) -> set[str]:
    root = Path(f"/proc/{pid}/fd")
    try:
        return {entry.name for entry in root.iterdir()}
    except OSError:
        return set()


def _fd_target(pid: int, number: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/fd/{number}")
    except OSError:
        return None


def _lock_available(path: str) -> bool:
    """True when this process can take an exclusive flock. The lock is released."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        state = stat.rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state not in ("Z", "X")


def _wait_gone(pids: list[int], seconds: float = 5.0) -> list[int]:
    deadline = time.monotonic() + seconds
    left = list(pids)
    while time.monotonic() < deadline and left:
        left = [pid for pid in left if _alive(pid)]
        if left:
            time.sleep(0.1)
    return left


def _kill_ours(pids: list[int]) -> None:
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _cmdline(pid: int) -> bytes:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return b""


def _argv0(pid: int) -> bytes:
    return _cmdline(pid).split(b"\0", 1)[0]


# Captured before any test stubs systemctl on PATH. Queries and cleanup call
# this binary, so a stub earlier in PATH cannot hide or widen a unit.
REAL_SYSTEMCTL = shutil.which("systemctl")
# sandbox_run.sh names a770b-sbx-<its pid>-<random>.scope. Anything else in a
# record is not this test's unit and must not be stopped.
_SCOPE_UNIT = re.compile(r"^a770b-sbx-([1-9][0-9]*)-([0-9]+)\.scope$")


def _note_owned(root: int, extra: list[int] | None = None) -> list[int]:
    """Record pids before a signal. Cleanup uses this list and does not rescan."""
    found = [root, *_descendants(root)]
    if extra:
        found.extend(extra)
    owned: list[int] = []
    for pid in found:
        if pid > 0 and pid not in owned:
            owned.append(pid)
    return owned


def _reap_owned(proc: subprocess.Popen, owned: list[int]) -> None:
    """Signal the saved identities. An empty list does not search for others."""
    if proc.poll() is None and not owned:
        owned = _note_owned(proc.pid)
    elif proc.poll() is None and proc.pid not in owned:
        owned = [proc.pid, *owned]
    _kill_ours(owned)
    if proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait(timeout=5)


def _scope_unit(record: Path) -> str | None:
    """Unit named by this test's record, or None when the record has no scope.

    A name that is not a sandbox scope fails the read. It is not a stop target.
    """
    try:
        raw = record.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError(f"could not read sandbox record {record}: {exc}") from exc
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise AssertionError(f"sandbox record {record} is not json: {exc}") from exc
    if not isinstance(body, dict):
        raise AssertionError(f"sandbox record {record} is not a record")
    unit = body.get("unit")
    if unit is None or unit == "":
        return None
    if not isinstance(unit, str) or _SCOPE_UNIT.fullmatch(unit) is None:
        raise AssertionError(
            f"sandbox record {record} does not name this test's scope: {unit!r}"
        )
    return unit


def _require_recorded_scope(record: Path, root: int) -> str | None:
    """The record's scope must be a live sandbox_run.sh under root."""
    unit = _scope_unit(record)
    if unit is None:
        return None
    match = _SCOPE_UNIT.fullmatch(unit)
    assert match is not None
    embedded = int(match.group(1))
    if embedded != root and embedded not in _descendants(root):
        raise AssertionError(f"{unit} is not owned by {root}")
    if b"sandbox_run.sh" not in _cmdline(embedded):
        raise AssertionError(f"{unit} is not this test's sandbox")
    return unit


def _stop_scope(unit: str | None) -> None:
    """Stop one scope this test already named. Never asks systemd for others."""
    if unit is None:
        return
    if _SCOPE_UNIT.fullmatch(unit) is None:
        raise AssertionError(f"refusing to stop a unit this test does not own: {unit!r}")
    if REAL_SYSTEMCTL is None:
        raise AssertionError(f"could not stop {unit}: systemctl is not on PATH")
    subprocess.run(
        [REAL_SYSTEMCTL, "--user", "stop", "--", unit],
        capture_output=True, text=True,
    )
    subprocess.run(
        [REAL_SYSTEMCTL, "--user", "reset-failed", "--", unit],
        capture_output=True, text=True,
    )


def _stop_records(records: list[Path]) -> None:
    units: list[str] = []
    for record in records:
        if not record.exists():
            continue
        unit = _scope_unit(record)
        if unit is not None and unit not in units:
            units.append(unit)
    for unit in units:
        _stop_scope(unit)


def _scopes_in_logs(logs: Path) -> list[str]:
    """Scopes named by verify records still in this test's own log directory."""
    if not logs.is_dir():
        return []
    units: list[str] = []
    for path in logs.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("verify-oom-") and path.name.endswith(".json"):
            unit = _scope_unit(path)
            if unit is not None and unit not in units:
                units.append(unit)
    return units


def _stop_log_scopes(logs: Path, saved: list[str] | None = None) -> None:
    """Stop scopes this test saved, plus any record still in its log directory."""
    units = list(saved or [])
    for unit in _scopes_in_logs(logs):
        if unit not in units:
            units.append(unit)
    for unit in units:
        _stop_scope(unit)


# Terminal ActiveState values. deactivating is still changing; active is a leak.
_FINISHED_SCOPE_STATES = frozenset({"inactive", "failed"})


def _scope_is_finished(state: str) -> bool:
    """True when the named scope is no longer running or changing."""
    return state in _FINISHED_SCOPE_STATES


def _observe_scope(unit: str) -> str:
    """ActiveState of one named scope. A failed query fails; it does not search.

    -p belongs before --. After --, systemd reads -p and ActiveState as unit names.
    """
    if _SCOPE_UNIT.fullmatch(unit) is None:
        raise AssertionError(f"refusing to observe a unit this test does not own: {unit!r}")
    if REAL_SYSTEMCTL is None:
        raise AssertionError(f"could not observe {unit}: systemctl is not on PATH")
    shown = subprocess.run(
        [REAL_SYSTEMCTL, "--user", "show", "-p", "ActiveState", "--", unit],
        capture_output=True, text=True,
    )
    if shown.returncode != 0 or "ActiveState=" not in shown.stdout:
        detail = (shown.stderr or shown.stdout or "").strip()
        raise AssertionError(f"could not observe {unit}: {detail or 'no ActiveState'}")
    for line in shown.stdout.splitlines():
        if line.startswith("ActiveState="):
            state = line.split("=", 1)[1].strip()
            if not state:
                raise AssertionError(f"could not observe {unit}: empty ActiveState")
            return state
    raise AssertionError(f"could not observe {unit}: no ActiveState")


def _wait_sleeper(root: int, ready: Path, token: str, proc: subprocess.Popen,
                  seconds: float = 20) -> int:
    """Host pid under root whose argv0 is token. A script that only mentions it does not count.

    The sandbox pid namespace makes an in-sandbox $! unusable on the host.
    A timeout fails. The search never leaves root's descendants.
    """
    deadline = time.monotonic() + seconds
    want = token.encode()
    while time.monotonic() < deadline:
        if proc.poll() is not None and not ready.exists():
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(
                f"exited {proc.returncode} before the sleeper was ready: {err}"
            )
        if ready.exists():
            found = [pid for pid in _descendants(root) if _alive(pid) and _argv0(pid) == want]
            if len(found) > 1:
                raise AssertionError(f"more than one sleeper named {token}: {found}")
            if len(found) == 1:
                return found[0]
        time.sleep(0.05)
    raise AssertionError(f"sleeper {token} did not appear under {root}")


def _sleeper_command(token: str, ready: Path) -> str:
    """Background exec -a sleeper. The ready file appears by rename."""
    launch = json.dumps(f"exec -a {token} sleep 120")
    return (
        f"bash -c {launch} & "
        f'printf "%s\\n" ready > "{ready}.tmp" && mv -f "{ready}.tmp" "{ready}"; '
        "wait"
    )


def _sandbox_cmd(env, seat, cfg, command, entered=None):
    if entered is not None:
        env = dict(env, A770B_SANDBOX_ENTERED_FILE=str(entered))
    return [
        "bash", str(SANDBOX), str(seat), str(cfg), "--",
        "bash", "-c", command,
    ], env


def test_i2_parent_death_kills_sandbox_and_bridge(tmp_path):
    """Kill the shell that started run_lead, not sandbox_run.sh itself.

    One case is the direct branch with the socat bridge. The other is the
    entered-file branch with usage_tap. A grandchild sleeps inside the sandbox.
    """
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    base = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    records: list[Path] = []
    try:
        cases = (
            (False, {}, False),
            (True, {"A770B_SERVED_ENGINE": "strata"}, False),
            (False, {}, True),
            (True, {}, True),
        )
        stub = bin_ / "systemd-run"
        for entered, extra, fallback in cases:
            if stub.exists():
                stub.unlink()
            if fallback:
                _exe(stub, "#!/bin/bash\nexit 1\n")
            ready = seat / f"ready-{int(entered)}-{int(fallback)}"
            ready.unlink(missing_ok=True)
            entered_file = tmp_path / f"entered-{int(entered)}-{int(fallback)}"
            record = tmp_path / f"i2-{int(entered)}-{int(fallback)}.json"
            records.append(record)
            command = f'sleep 120 & echo ready > "{ready}"; sleep 120'
            env = dict(base, A770B_SANDBOX_RECORD_FILE=str(record), **extra)
            if entered:
                env["A770B_SANDBOX_ENTERED_FILE"] = str(entered_file)
            # $$ is the outer shell. The subshell execs python, so that shell
            # stays the parent. A last-command exec in this shell would make
            # $$ the interpreter's own pid.
            script = (
                "(\n"
                "export A770B_RUN_OWNER_PID=$$\n"
                f'exec python3 "{RUN_LEAD}" -- bash "{SANDBOX}" "{seat}" "{cfg}" -- '
                f"bash -c {json.dumps(command)}\n"
                ")\n"
                "wait\n"
            )
            err_path = tmp_path / f"i2-err-{int(entered)}-{int(fallback)}"
            err_fh = err_path.open("w", encoding="utf-8")
            proc = subprocess.Popen(
                ["bash", "-c", script], env=env,
                stdout=subprocess.DEVNULL, stderr=err_fh, text=True,
            )
            owned: list[int] = []
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not ready.exists():
                    if proc.poll() is not None:
                        err_fh.close()
                        err = err_path.read_text(encoding="utf-8", errors="replace")
                        raise AssertionError(f"caller exited {proc.returncode} before ready: {err}")
                    time.sleep(0.1)
                assert ready.exists(), "sandbox command did not start"
                if entered:
                    file_deadline = time.monotonic() + 5
                    while time.monotonic() < file_deadline and not entered_file.exists():
                        time.sleep(0.05)
                    assert entered_file.read_text(encoding="utf-8").strip() == "entered"
                body = json.loads(record.read_text(encoding="utf-8"))
                if fallback:
                    assert body["isolation"] == "none", body
                    assert _scope_unit(record) is None
                elif _bus():
                    assert body["isolation"] == "scope", body
                else:
                    assert body["isolation"] == "none", body
                _require_recorded_scope(record, proc.pid)
                owned = _note_owned(proc.pid)
                assert any(b"sandbox_run.sh" in _cmdline(pid) for pid in owned), "sandbox_run.sh was not a child of the caller"
                if extra.get("A770B_SERVED_ENGINE") == "strata":
                    assert any(b"usage_tap.py" in _cmdline(pid) for pid in owned), "usage_tap bridge was not started"
                else:
                    assert any(b"socat" in _cmdline(pid) for pid in owned), "socat bridge was not started"
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                err_fh.close()
                # A survivor can keep the write end. The death check must not wait on it.
                left = _wait_gone(owned)
                assert left == [], f"sandbox processes survived the caller: {left}"
                if fallback:
                    err = err_path.read_text(encoding="utf-8", errors="replace")
                    assert err.count("isolation=none") == 1, err
            finally:
                if not err_fh.closed:
                    err_fh.close()
                _reap_owned(proc, owned)
    finally:
        _stop_records(records)


def test_i3_stop_signal_and_verify_timeout_end_the_run(tmp_path):
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    ready = seat / "lead-ready"
    token = f"i3lead{tmp_path.name}"
    command = _sleeper_command(token, ready)
    # A parent that waits reaps the leader. stop-run treats an unreaped zombie as still alive.
    exit_file = tmp_path / "lead.exit"
    leader_file = tmp_path / "leader.pid"
    record = tmp_path / "i3-stop.json"
    script = (
        f'python3 "{RUN_LEAD}" -- bash "{SANDBOX}" "{seat}" "{cfg}" -- bash -c {json.dumps(command)} &\n'
        f'echo $! > "{leader_file}"\n'
        "wait $!\n"
        f'echo $? > "{exit_file}"\n'
    )
    proc = subprocess.Popen(
        ["bash", "-c", script],
        env=dict(env, A770B_SANDBOX_RECORD_FILE=str(record)),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    owned: list[int] = []
    try:
        sleeper = _wait_sleeper(proc.pid, ready, token, proc)
        assert ready.exists()
        assert _alive(sleeper) and _argv0(sleeper) == token.encode()
        _require_recorded_scope(record, proc.pid)
        leader = int(leader_file.read_text(encoding="ascii").strip())
        owned = _note_owned(proc.pid, [sleeper, leader])
        (Path(env["A770B_DATA"]) / "logs" / "run.pid").write_text(f"{leader}\n", encoding="ascii")
        stop = subprocess.run(
            ["bash", str(LOCAL_BUILD), "stop-run"],
            env=env, capture_output=True, text=True, timeout=40,
        )
        proc.wait(timeout=15)
        rc = int(exit_file.read_text(encoding="ascii").strip())
        assert stop.returncode == 0, stop.stderr
        assert rc == 143, (rc, stop.stdout, stop.stderr)
        left = _wait_gone([sleeper])
        assert left == [], left
    finally:
        _reap_owned(proc, owned)
        _stop_records([record])
    ready.unlink(missing_ok=True)

    token = f"i3v{tmp_path.name}"
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "i3.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+seat changed\n",
        encoding="utf-8",
    )
    vready = seat / "vready"
    (results / "i3.spec.json").write_text(json.dumps({
        "verify": {"test": _sleeper_command(token, vready)},
    }), encoding="utf-8")
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "8"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    owned = []
    logs = Path(env["A770B_DATA"]) / "logs"
    units: list[str] = []
    try:
        sleeper = _wait_sleeper(verify.pid, vready, token, verify)
        assert vready.exists()
        assert _alive(sleeper) and _argv0(sleeper) == token.encode()
        owned = _note_owned(verify.pid, [sleeper])
        for unit in _scopes_in_logs(logs):
            embedded = int(_SCOPE_UNIT.fullmatch(unit).group(1))
            assert embedded in owned, unit
            units.append(unit)
        assert not _bus() or units, "verify did not record its scope"
        rc = verify.wait(timeout=20)
        out = verify.stdout.read() if verify.stdout else ""
        assert rc == 124, out
        doc = json.loads((results / "i3.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "timeout"
        left = _wait_gone([sleeper])
        assert left == [], left
    finally:
        _reap_owned(verify, owned)
        _stop_log_scopes(logs, units)


def test_i4_entered_file_and_lock_fds_stay_out(tmp_path):
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    fd_bwrap = tmp_path / "bwrap.fds"
    fd_run = tmp_path / "systemd.fds"
    fd_socat = tmp_path / "socat.fds"
    _exe(bin_ / "bwrap", f"""
ls -1 /proc/$$/fd > "{fd_bwrap}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    _exe(bin_ / "systemd-run", f"""
ls -1 /proc/$$/fd >> "{fd_run}"
printf '\\n' >> "{fd_run}"
case " $* " in
  *" --collect "*) exit 0 ;;
esac
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 1
""")
    _exe(bin_ / "socat", f"""
path=$(printf '%s\\n' "$1" | sed -n 's/^UNIX-LISTEN:\\([^,]*\\).*/\\1/p')
python3 -c 'import os,socket,sys
p, dest = sys.argv[1], sys.argv[2]
s=socket.socket(socket.AF_UNIX)
if os.path.exists(p): os.unlink(p)
s.bind(p); s.listen(1)
open(dest,"w",encoding="utf-8").write("\\n".join(os.listdir(f"/proc/{{os.getpid()}}/fd")))
' "$path" "{fd_socat}"
sleep 30
""")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    entered = tmp_path / "entered"
    script = (
        f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
        f'card_lock; seat_lock "{seat}"; '
        f'bash "{SANDBOX}" "{seat}" "{cfg}" -- true'
    )
    proc = subprocess.run(["bash", "-c", script], env=dict(env, A770B_SANDBOX_ENTERED_FILE=str(entered)),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert entered.read_text(encoding="utf-8").strip() == "entered"
    for path in (fd_bwrap, fd_run):
        fds = set(path.read_text(encoding="utf-8").split())
        assert "7" not in fds and "9" not in fds, path
    fd_bwrap.unlink()
    fd_run.unlink()
    env.pop("A770B_NO_BRIDGE", None)
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    for path in (fd_bwrap, fd_run, fd_socat):
        fds = set(path.read_text(encoding="utf-8").split())
        assert "7" not in fds and "9" not in fds, (path, fds)
    fd_tap = tmp_path / "tap.fds"
    _exe(bin_ / "python3", f"""
if [[ " $* " == *usage_tap.py* ]]; then
  ls -1 /proc/$$/fd > "{fd_tap}"
fi
exec /usr/bin/python3 "$@"
""")
    proc = subprocess.run(
        ["bash", "-c", script],
        env=dict(env, A770B_SERVED_ENGINE="strata"),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    tap_fds = set(fd_tap.read_text(encoding="utf-8").split())
    assert "7" not in tap_fds and "9" not in tap_fds, tap_fds


def test_i5_script_and_command_reach_bash_unexpanded(tmp_path):
    """Real bwrap and, when the bus is up, real systemd-run. Neither is stubbed."""
    if shutil.which("bwrap") is None or shutil.which("setpriv") is None:
        pytest.skip("bwrap and setpriv are required; this test does not stub them")
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    # A unit separator keeps each argument. Concatenated "%s" output can match by accident.
    argv = ["bash", str(SANDBOX), str(seat), str(cfg), "--", "printf", "%s\x1f", "a b", "$HOME", "$$", "q'u"]
    direct = tmp_path / "i5-direct.json"
    record = tmp_path / "i5.json"
    try:
        proc = subprocess.run(
            argv, env=dict(env, A770B_SANDBOX_RECORD_FILE=str(direct)),
            cwd=seat, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split("\x1f") == ["a b", "$HOME", "$$", "q'u", ""], (proc.stdout, proc.stderr)
        entered = tmp_path / "entered"
        proc = subprocess.run(
            argv,
            env=dict(env, A770B_SANDBOX_ENTERED_FILE=str(entered), A770B_SANDBOX_RECORD_FILE=str(record)),
            cwd=seat, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split("\x1f") == ["a b", "$HOME", "$$", "q'u", ""], (proc.stdout, proc.stderr)
        assert entered.read_text(encoding="utf-8").strip() == "entered"
        body = json.loads(record.read_text(encoding="utf-8"))
        if _bus():
            assert body["isolation"] == "scope", body
        else:
            assert body["isolation"] == "none", body
        _scope_unit(record)
        _scope_unit(direct)
    finally:
        _stop_records([direct, record])


def test_i6_scope_and_slice_have_no_limit(tmp_path):
    """Read the scope while it is still active. An unloaded unit reports infinity."""
    text = SANDBOX.read_text(encoding="utf-8")
    qualify = QUALIFY.read_text(encoding="utf-8")
    for word in ("MemoryMax", "MemoryHigh", "MemorySwapMax", "TasksMax", "CPUQuota"):
        assert word not in text
        assert word not in qualify
    if not _bus():
        pytest.skip("no user systemd bus for the live scope read")
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    record = tmp_path / "data" / "logs" / "live.json"
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1",
               A770B_SANDBOX_RECORD_FILE=str(record))
    proc = subprocess.Popen(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "sleep", "30"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        body = None
        while time.monotonic() < deadline and body is None:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(f"sandbox exited {proc.returncode} before the scope was read: {err}")
            if record.exists():
                body = json.loads(record.read_text(encoding="utf-8"))
            else:
                time.sleep(0.05)
        assert body is not None, "sandbox record was not written"
        assert body["isolation"] == "scope", body
        unit = _require_recorded_scope(record, proc.pid)
        assert unit is not None, body
        shown = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            shown = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "Slice",
                 "-p", "ControlGroup", "-p", "MemoryMax", "-p", "MemoryHigh",
                 "-p", "MemorySwapMax", "-p", "TasksMax", "-p", "CPUQuotaPerSecUSec"],
                capture_output=True, text=True,
            )
            if shown.returncode == 0 and "ActiveState=active" in shown.stdout:
                break
            time.sleep(0.1)
        assert shown is not None and shown.returncode == 0, (shown.stdout if shown else "", shown.stderr if shown else "")
        fields = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        assert fields.get("ActiveState") == "active", shown.stdout
        assert fields.get("Slice") == "a770b-sandbox.slice", shown.stdout
        assert "a770b-sandbox.slice" in fields.get("ControlGroup", ""), shown.stdout
        for key in ("MemoryMax", "MemoryHigh", "MemorySwapMax", "CPUQuotaPerSecUSec"):
            assert fields.get(key) == "infinity", (key, shown.stdout)
        # The user manager's DefaultTasksMax is inherited. A scope cap would be lower.
        manager = subprocess.run(
            ["systemctl", "--user", "show", "--property=TasksMax", "--property=DefaultTasksMax"],
            capture_output=True, text=True,
        )
        assert manager.returncode == 0 and "=" in manager.stdout, manager.stderr
        inherited = {line.split("=", 1)[1] for line in manager.stdout.splitlines() if "=" in line}
        assert fields.get("TasksMax") == "infinity" or fields.get("TasksMax") in inherited, (
            fields.get("TasksMax"), inherited,
        )
        slice_show = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            slice_show = subprocess.run(
                ["systemctl", "--user", "show", "a770b-sandbox.slice", "-p", "ActiveState",
                 "-p", "MemoryMax", "-p", "MemoryHigh", "-p", "MemorySwapMax",
                 "-p", "TasksMax", "-p", "CPUQuotaPerSecUSec"],
                capture_output=True, text=True,
            )
            if slice_show.returncode == 0 and "ActiveState=active" in slice_show.stdout:
                break
            time.sleep(0.1)
        assert slice_show is not None and slice_show.returncode == 0, (
            slice_show.stdout if slice_show else "", slice_show.stderr if slice_show else "",
        )
        slice_fields = dict(line.split("=", 1) for line in slice_show.stdout.splitlines() if "=" in line)
        assert slice_fields.get("ActiveState") == "active", slice_show.stdout
        for key in ("MemoryMax", "MemoryHigh", "MemorySwapMax", "CPUQuotaPerSecUSec"):
            assert slice_fields.get(key) == "infinity", (key, slice_show.stdout)
        assert slice_fields.get("TasksMax") == "infinity" or slice_fields.get("TasksMax") in inherited, (
            slice_fields.get("TasksMax"), inherited, slice_show.stdout,
        )
    finally:
        owned = _note_owned(proc.pid) if proc.poll() is None else []
        _reap_owned(proc, owned)
        if record.exists():
            subprocess.run([sys.executable, "-I", "-B", str(OOM_PY), str(record), "137"], check=False)
        _stop_records([record])


def test_i8_oom_record_grade_and_outside_kill(tmp_path, monkeypatch):
    real_path = os.environ.get("PATH", "/usr/bin:/bin")
    sys.path.insert(0, str(ROOT / "harness"))
    import sandbox_oom
    monkeypatch.setattr(sandbox_oom, "_SETTLE_S", 0.4)
    monkeypatch.setattr(sandbox_oom, "_JOURNAL_S", 0.4)
    monkeypatch.setattr(sandbox_oom, "_TOOL_TIMEOUT_S", 0.8)
    assert sandbox_oom.grade(0, "yes") == ("fail", 137, "oom")
    assert sandbox_oom.grade(1, "yes") == ("fail", 1, "oom")
    assert sandbox_oom.grade(-9, "no", signaled=True) == ("infra_error", 137, None)
    assert sandbox_oom.grade(0, "no", timed_out=True) == ("timeout", 124, None)
    code, signaled = sandbox_oom.normalize_status(-9)
    assert (code, signaled) == (137, True)

    def _stubs(systemctl_body, journal_body):
        bin_ = tmp_path / "grade-bin"
        _exe(bin_ / "systemctl", systemctl_body)
        _exe(bin_ / "journalctl", journal_body)
        return bin_

    record = tmp_path / "rec.json"

    def _put(isolation, unit, policy):
        record.write_text(json.dumps({
            "isolation": isolation, "unit": unit, "policy": policy, "oom": "unknown",
        }), encoding="utf-8")

    def _grade(exit_code, env):
        sandbox_oom.update(str(record), exit_code)

    marker = tmp_path / "journal-called"
    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=failed' 'Result=oom-kill'\n",
        f"printf 1 > '{marker}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    # Result=oom-kill is yes at any exit, including 0 and 137. The journal stays unread.
    monkeypatch.setenv("PATH", env["PATH"])
    _put("scope", "a770b-sbx-t.scope", "kill")
    _grade(137, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not marker.exists()
    _put("scope", "a770b-sbx-t.scope", "kill")
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not marker.exists()

    # Exit 0 and any other result is no, and the journal is not read.
    success_called = tmp_path / "success-journal"
    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=inactive' 'Result=success'\n",
        f"printf 1 > '{success_called}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    monkeypatch.setenv("PATH", env["PATH"])
    _put("scope", "a770b-sbx-ok.scope", "kill")
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "no"
    assert not success_called.exists()

    # An empty show is not a settled success. Exit 0 must not become oom=no.
    empty_journal = tmp_path / "empty-journal"
    env = dict(os.environ, PATH=f"{_stubs(
        "exit 0\n",
        f"printf 1 > '{empty_journal}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    monkeypatch.setenv("PATH", env["PATH"])
    _put("scope", "a770b-sbx-empty.scope", "kill")
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"
    # activating is unsettled. Exit 0 must not become oom=no, and the journal stays unread.
    activating_journal = tmp_path / "activating-journal"
    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=activating' 'Result=success'\n",
        f"printf 1 > '{activating_journal}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    monkeypatch.setenv("PATH", env["PATH"])
    _put("scope", "a770b-sbx-activating.scope", "kill")
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"
    assert not activating_journal.exists()

    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=inactive' 'Result=success'\n",
        "exit 1\n",
    )}:{os.environ['PATH']}")
    monkeypatch.setenv("PATH", env["PATH"])
    _put("scope", "a770b-sbx-c.scope", "continue")
    journal = (
        "printf '%s\\n' "
        "'{\"MESSAGE\":\"The kernel OOM killer killed some processes in this unit.\","
        "\"SYSLOG_IDENTIFIER\":\"systemd\","
        "\"MESSAGE_ID\":\"fe6faa94e7774663a0da52717891d8ef\"}'\n"
    )
    _exe(tmp_path / "grade-bin" / "journalctl", journal)
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"

    _exe(tmp_path / "grade-bin" / "journalctl", "exit 1\n")
    _put("scope", "a770b-sbx-u.scope", "continue")
    _grade(1, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    # A Started line and no OOM entry, after the short poll, is no.
    _exe(
        tmp_path / "grade-bin" / "journalctl",
        "printf '%s\\n' '{\"MESSAGE\":\"Started scope.\",\"SYSLOG_IDENTIFIER\":\"systemd\"}'\n",
    )
    _put("scope", "a770b-sbx-started.scope", "continue")
    _grade(1, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "no"

    _put("none", None, "continue")
    _grade(0, env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    # Outside kill of sandbox_run.sh, after bwrap is in the tree. Not an OOM.
    # The grader stubs stay off this PATH. The leak query uses the real binary.
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{real_path}")
    resolved = shutil.which("systemctl", path=env["PATH"])
    assert resolved == REAL_SYSTEMCTL, resolved
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "i8.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    (results / "i8.spec.json").write_text(json.dumps({"verify": {"test": "sleep 60"}}), encoding="utf-8")
    logs = Path(env["A770B_DATA"]) / "logs"
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "30"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    target = None
    bwrap_pid = None
    owned: list[int] = []
    unit = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and verify.poll() is None:
            target = None
            bwrap_pid = None
            for pid in _descendants(verify.pid):
                cmd = _cmdline(pid)
                parts = cmd.split(b"\0")
                if not parts or parts[0].endswith(b"python3") or parts[0].endswith(b"python"):
                    continue
                if b"sandbox_run.sh" in cmd and target is None:
                    target = pid
                if parts[0].endswith(b"bwrap") or b"/bwrap" in parts[0]:
                    bwrap_pid = pid
            if target is not None and bwrap_pid is not None:
                break
            time.sleep(0.05)
        assert target is not None and bwrap_pid is not None, "bwrap was not inside sandbox_run.sh"
        records = [
            path for path in logs.iterdir()
            if path.is_file() and path.name.startswith("verify-oom-") and path.name.endswith(".json")
        ]
        assert len(records) == 1, records
        unit = _require_recorded_scope(records[0], verify.pid)
        if _bus():
            assert unit is not None and int(_SCOPE_UNIT.fullmatch(unit).group(1)) == target
        owned = _note_owned(verify.pid, [target, bwrap_pid])
        desc = [pid for pid in owned if pid == target or pid in _descendants(target) or pid == bwrap_pid]
        os.kill(target, signal.SIGKILL)
        verify.wait(timeout=20)
        doc = json.loads((results / "i8.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", doc
        assert doc["reason"] != "oom"
        left = _wait_gone(desc)
        assert left == [], f"sandbox processes survived the outside kill: {left}"
        if unit is not None:
            state = _observe_scope(unit)
            deadline = time.monotonic() + 5
            while not _scope_is_finished(state) and time.monotonic() < deadline:
                time.sleep(0.05)
                state = _observe_scope(unit)
            assert _scope_is_finished(state), state
    finally:
        _reap_owned(verify, owned)
        _stop_log_scopes(logs, [unit] if unit else None)


def test_i9_without_systemd_run_still_runs_and_dies_with_parent(tmp_path):
    """Caller death with systemd-run refused, on the shell path and the entered path."""
    bin_ = _tools(tmp_path)
    _exe(bin_ / "systemd-run", "#!/bin/bash\nexit 1\n")
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    base = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    records: list[Path] = []
    proc: subprocess.Popen | None = None
    owned: list[int] = []
    try:
        for entered in (False, True):
            record = tmp_path / "data" / "logs" / f"none-{int(entered)}.json"
            records.append(record)
            ready = seat / f"ready-{int(entered)}"
            ready.unlink(missing_ok=True)
            entered_file = tmp_path / f"entered-{int(entered)}"
            env = dict(base, A770B_SANDBOX_RECORD_FILE=str(record))
            if entered:
                env["A770B_SANDBOX_ENTERED_FILE"] = str(entered_file)
            command = f'sleep 120 & echo ready > "{ready}"; sleep 120'
            err_path = tmp_path / f"i9-err-{int(entered)}"
            err_fh = err_path.open("w", encoding="utf-8")
            proc = subprocess.Popen(
                ["bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", command],
                env=env, stdout=subprocess.DEVNULL, stderr=err_fh, text=True,
            )
            owned = []
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not ready.exists():
                    if proc.poll() is not None:
                        err_fh.close()
                        raise AssertionError(err_path.read_text(encoding="utf-8", errors="replace") or "exited")
                    time.sleep(0.1)
                assert ready.exists(), "sandbox command did not start"
                if entered:
                    file_deadline = time.monotonic() + 5
                    while time.monotonic() < file_deadline and not entered_file.exists():
                        time.sleep(0.05)
                    assert entered_file.read_text(encoding="utf-8").strip() == "entered"
                owned = _note_owned(proc.pid)
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                err_fh.close()
                left = _wait_gone(owned)
                assert left == [], left
                err = err_path.read_text(encoding="utf-8", errors="replace")
                assert err.count("isolation=none") == 1, err
                body = json.loads(record.read_text(encoding="utf-8"))
                assert body["isolation"] == "none"
                assert body["oom"] == "unknown"
                assert _scope_unit(record) is None
            finally:
                if not err_fh.closed:
                    err_fh.close()
                _reap_owned(proc, owned)
                owned = []
    finally:
        if proc is not None and owned:
            _reap_owned(proc, owned)
        _stop_records(records)


def test_i10_sandbox_slice_path_is_null_when_absent(tmp_path):
    sys.path.insert(0, str(ROOT / "harness"))
    import host_memory

    def literal(root, who):
        # The hierarchy systemd uses. Not sandbox_slice_dir(), which is under test.
        return (Path(root) / "user.slice" / f"user-{who}.slice" / f"user@{who}.service"
                / "a770b.slice" / "a770b-sandbox.slice")

    uid = 424242 if os.getuid() != 424242 else 424243
    missing = tmp_path / "missing-root"
    missing.mkdir()
    assert host_memory.read_sandbox_slice(missing, uid) is None
    short = missing / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service" / "a770b-sandbox.slice"
    short.mkdir(parents=True)
    (short / "memory.current").write_text("0\n", encoding="ascii")
    assert host_memory.read_sandbox_slice(missing, uid) is None
    # A caller-uid directory must not satisfy a read that was given another uid.
    decoy = literal(missing, os.getuid())
    decoy.mkdir(parents=True)
    (decoy / "memory.current").write_text("99\n", encoding="ascii")
    assert host_memory.read_sandbox_slice(missing, uid) is None
    real = literal(missing, uid)
    real.mkdir(parents=True)
    (real / "memory.current").write_text("0\n", encoding="ascii")
    (real / "memory.max").write_text("max\n", encoding="ascii")
    got = host_memory.read_sandbox_slice(missing, uid)
    assert got["memory_current"] == 0
    assert got["memory_max"] == "max"
    assert got["source"] == str(real)
    absent = host_memory.sample(cgroup_root=str(tmp_path / "no-such"))
    assert absent["sandbox_slice"] is None
    present_root = tmp_path / "present-root"
    present = literal(present_root, os.getuid())
    present.mkdir(parents=True)
    (present / "memory.current").write_text("0\n", encoding="ascii")
    (present / "memory.max").write_text("max\n", encoding="ascii")
    other = literal(present_root, uid)
    other.mkdir(parents=True)
    (other / "memory.current").write_text("99\n", encoding="ascii")
    sample = host_memory.sample(cgroup_root=str(present_root))
    assert sample["sandbox_slice"] is not None
    assert sample["sandbox_slice"]["memory_current"] == 0
    assert sample["sandbox_slice"]["source"] == str(present)


def test_i11_qualify_tracks_the_server_and_continues_on_oom(tmp_path):
    """Run the tracker, the pid-file rename, the symlink refusal, and suite cleanup."""
    bin_ = _tools(tmp_path)
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    sleep_log = tmp_path / "sleep.pids"
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("5151\n", encoding="ascii")
    _exe(bin_ / "sleep", f"""
printf '%s %s\\n' "$$" "$PPID" >> "{sleep_log}"
if [ "${{A770B_SLEEP_HOLD:-}}" = 1 ]; then exec /usr/bin/sleep 30; fi
exec /usr/bin/sleep "$@"
""")
    _exe(bin_ / "docker", f"""
if [ "${{1:-}}" = inspect ]; then cat "{docker_pid}"; fi
exit 0
""")
    nvtop_log = tmp_path / "nvtop.log"
    _exe(bin_ / "nvtop", f"printf '%s\\n' \"$*\" >> \"{nvtop_log}\"\nexit 0\n")
    stop_log = tmp_path / "qualify-stop.log"
    _exe(bin_ / "a770b-qualify-stop", f"printf '%s\\n' \"$*\" >> \"{stop_log}\"\nexit 0\n")
    prefix = QUALIFY.read_text(encoding="utf-8").split("[ $# -ge 1 ] || usage", 1)[0]
    prefix = prefix.replace(
        'here=$(cd "$(dirname "$0")" && pwd)',
        f'here="{ROOT / "harness"}"',
        1,
    )
    funcs = tmp_path / "qualify-funcs.sh"
    funcs.write_text(prefix, encoding="utf-8")
    script = f"""
set -m
set +e
. "{funcs}"
# The sampler must not reach the real gpu_used_gib, which runs nvtop.
gpu_used_gib() {{ printf '%s\\n' 0; }}
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
mkdir -p "$job/evidence" "$job/phases"
export A770B_SLEEP_HOLD=1
start_observers
printf 'OUT TRACKER=%s\\n' "${{TRACKER:-}}"
deadline=$((SECONDS + 5))
child=""
while [ "$SECONDS" -lt "$deadline" ]; do
  child=$(awk -v p="$TRACKER" '$2 == p {{ print $1; exit }}' "{sleep_log}" 2>/dev/null || true)
  [ -n "$child" ] && break
  /usr/bin/sleep 0.05
done
printf 'OUT CHILD=%s\\n' "$child"
stop_helpers
/usr/bin/sleep 0.15
left=0
if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then left=1; fi
printf 'OUT CHILD_ALIVE=%s\\n' "$left"
unset VRAM_PID TRACKER MEM A770B_SLEEP_HOLD
start_observers
inode1=$(stat -c %i "$job/evidence/serving.pid")
printf '%s\\n' 'abababababababababababababababababababababababababababababababab' > "$job/cid"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
inode2=$(stat -c %i "$job/evidence/serving.pid")
printf 'OUT PID1=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf 'OUT INODE1=%s\\n' "$inode1"
printf 'OUT INODE2=%s\\n' "$inode2"
printf '%s\\n' 6161 > "{docker_pid}"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 6161 ]; then break; fi
  /usr/bin/sleep 0.05
done
inode3=$(stat -c %i "$job/evidence/serving.pid")
printf 'OUT PID2=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf 'OUT INODE3=%s\\n' "$inode3"
stop_helpers
# Symlink refusal is before the sampler. A following VRAM pid means the check lost.
unset VRAM_PID TRACKER MEM
job="{tmp_path / "job-m5"}"
mkdir -p "$job/evidence"
ln -s "{secret}" "$job/evidence/serving.pid"
start_observers
printf 'OUT M5_VRAM=%s\\n' "${{VRAM_PID-}}"
printf 'OUT M5_SECRET=%s\\n' "$(cat "{secret}")"
stop_helpers
unset TRACKER VRAM_PID MEM SERVER_STOPPED
job="{tmp_path / "job-stop"}"
mkdir -p "$job/evidence" "$job/phases"
ln -s "{secret}" "$job/evidence/serving.pid"
stop_server
printf 'OUT STOP_RC=%s\\n' "$?"
printf 'OUT STOP_SECRET=%s\\n' "$(cat "{secret}")"
"""
    # A configured match makes the real gpu_used_gib call nvtop. The override
    # must be what keeps this test off that path. The stub records a call.
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_GPU_MATCH="red-gpu")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=40)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    assert fields.get("CHILD"), proc.stdout + proc.stderr
    assert fields.get("CHILD_ALIVE") == "0", proc.stdout + proc.stderr
    assert fields.get("PID1") == "5151", proc.stdout + proc.stderr
    assert fields.get("PID2") == "6161", proc.stdout + proc.stderr
    assert fields["INODE1"] != fields["INODE2"], fields
    assert fields["INODE2"] != fields["INODE3"], fields
    assert fields.get("M5_VRAM", "x") == "", fields
    assert fields.get("M5_SECRET") == "sentinel", fields
    assert fields.get("STOP_RC") == "1", fields
    assert fields.get("STOP_SECRET") == "sentinel", fields
    assert not nvtop_log.exists(), nvtop_log.read_text(encoding="utf-8") if nvtop_log.exists() else ""
    live_job = tmp_path / "job-live"
    strata_job = tmp_path / "job-strata"
    phase_job = tmp_path / "job-phase5"
    strata_here = tmp_path / "strata-here"
    strata_here.mkdir()
    (strata_here / "host_memory.py").symlink_to(HOST_MEMORY)
    # The tracker publishes the pid only while `id` still names the job's instance.
    _exe(strata_here / "serve_strata.sh",
         'case "${1:-}" in id) echo owned ;; pid) echo 4343 ;; esac\nexit 0\n')
    hold_file = tmp_path / "phase5-hold.pid"
    marker = tmp_path / "phase5-error"
    (tmp_path / "wait_target.py").write_text(
        "import json, sys, time\n"
        "path, pid, timeout = sys.argv[1:]\n"
        "pid = int(pid)\n"
        "deadline = time.monotonic() + float(timeout)\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        lines = open(path, encoding='utf-8').read().splitlines()\n"
        "    except OSError:\n"
        "        lines = []\n"
        "    for index, line in enumerate(lines):\n"
        "        try:\n"
        "            body = json.loads(line)\n"
        "        except ValueError:\n"
        "            continue\n"
        "        target = body.get('target')\n"
        "        if isinstance(target, dict) and target.get('pid') == pid:\n"
        "            print(index)\n"
        "            raise SystemExit(0)\n"
        "    time.sleep(0.05)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    (tmp_path / "wait_clear.py").write_text(
        "import json, sys, time\n"
        "path, after, timeout = sys.argv[1:]\n"
        "after = int(after)\n"
        "deadline = time.monotonic() + float(timeout)\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        lines = open(path, encoding='utf-8').read().splitlines()\n"
        "    except OSError:\n"
        "        lines = []\n"
        "    for line in lines[after + 1:]:\n"
        "        try:\n"
        "            body = json.loads(line)\n"
        "        except ValueError:\n"
        "            continue\n"
        "        if body.get('target') is None:\n"
        "            raise SystemExit(0)\n"
        "    time.sleep(0.05)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    # The suite command is stubbed. Publication is the real run_suite.sh function,
    # so deleting its qualify-file write leaves the monitor without a target.
    _exe(bin_ / "a770b-qualify-suite", f"""
expected="{phase_job / "evidence" / "serving.pid"}"
got="${{A770B_QUALIFY_SERVING_PID_FILE:-}}"
if [ "$got" != "$expected" ]; then
  printf 'export:%s\\n' "$got" > "{marker}"
  exit 1
fi
eval "$(awk '/^suite_publish_serving_pid()/,/^}}/' "{RUN_SUITE}")"
HOST_PID_FILE="{tmp_path / "suite-host.pid"}"
/usr/bin/sleep 90 &
hold=$!
printf '%s\\n' "$hold" > "{hold_file}"
cleanup() {{
  if [ -n "${{hold:-}}" ] && kill -0 "$hold" 2>/dev/null; then
    kill "$hold" 2>/dev/null || true
    wait "$hold" 2>/dev/null || true
  fi
}}
trap cleanup EXIT
suite_publish_serving_pid "$hold" || {{ printf 'publish failed\\n' > "{marker}"; exit 1; }}
if ! idx=$(python3 "{tmp_path / "wait_target.py"}" "{phase_job / "evidence" / "host-memory.jsonl"}" "$hold" 12); then
  printf 'no target sample\\n' > "{marker}"
  exit 1
fi
suite_publish_serving_pid "" || {{ printf 'clear failed\\n' > "{marker}"; exit 1; }}
if ! python3 "{tmp_path / "wait_clear.py"}" "{phase_job / "evidence" / "host-memory.jsonl"}" "$idx" 12; then
  printf 'no clear sample\\n' > "{marker}"
  exit 1
fi
exit 0
""")
    named = [
        tmp_path / "live-tracker.pid", tmp_path / "live-child.pid", tmp_path / "live-mem.pid",
        tmp_path / "live-vram.pid", tmp_path / "strata-tracker.pid", tmp_path / "strata-mem.pid",
        tmp_path / "strata-vram.pid", tmp_path / "phase-tracker.pid", tmp_path / "phase-mem.pid",
        tmp_path / "phase-vram.pid", hold_file,
    ]
    more = f"""
set -m
set +e
. "{funcs}"
gpu_used_gib() {{ printf '%s\\n' 0; }}
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
orig_here=$here
cleanup_named() {{
  local mem="${{MEM:-}}" vram="${{VRAM_PID:-}}"
  stop_helpers
  [ -n "$mem" ] && wait "$mem" 2>/dev/null || true
  [ -n "$vram" ] && wait "$vram" 2>/dev/null || true
}}
trap cleanup_named EXIT
printf '%s\\n' 5151 > "{docker_pid}"
export A770B_SLEEP_HOLD=1
engine=llama
job="{live_job}"
mkdir -p "$job/evidence" "$job/phases"
cid='{"cd" * 32}'
printf '%s\\n' "$cid" > "$job/cid"
start_observers
printf '%s\\n' "${{TRACKER:-}}" > "{tmp_path / "live-tracker.pid"}"
printf '%s\\n' "${{MEM:-}}" > "{tmp_path / "live-mem.pid"}"
printf '%s\\n' "${{VRAM_PID:-}}" > "{tmp_path / "live-vram.pid"}"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  tr '\\000' '\\n' < "/proc/$MEM/cmdline" > "{tmp_path / "mem.argv"}" 2>/dev/null || true
  if grep -qx -- '--pid-file' "{tmp_path / "mem.argv"}"; then break; fi
  /usr/bin/sleep 0.05
done
child=""
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  child=$(awk -v p="$TRACKER" '$2 == p {{ print $1; exit }}' "{sleep_log}" 2>/dev/null || true)
  [ -n "$child" ] && break
  /usr/bin/sleep 0.05
done
printf '%s\\n' "$child" > "{tmp_path / "live-child.pid"}"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT LIVE_BEFORE=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
saved_tracker=$TRACKER
stop_server
tracker_gone=0
child_gone=0
deadline=$((SECONDS + 3))
while [ "$SECONDS" -lt "$deadline" ]; do
  tracker_gone=1
  child_gone=1
  if [ -n "$saved_tracker" ] && kill -0 "$saved_tracker" 2>/dev/null; then tracker_gone=0; fi
  if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then child_gone=0; fi
  [ "$tracker_gone" = 1 ] && [ "$child_gone" = 1 ] && break
  /usr/bin/sleep 0.05
done
printf 'OUT LIVE_TRACKER_GONE=%s\\n' "$tracker_gone"
printf 'OUT LIVE_CHILD_GONE=%s\\n' "$child_gone"
if [ -f "$job/SERVING_CLEARED" ] && [ ! -L "$job/SERVING_CLEARED" ]; then printf 'OUT LIVE_CLEARED=1\\n'
else printf 'OUT LIVE_CLEARED=0\\n'; fi
if [ -f "$job/evidence/serving.pid" ] && [ ! -L "$job/evidence/serving.pid" ] && [ ! -s "$job/evidence/serving.pid" ]; then
  printf 'OUT LIVE_PID_EMPTY=1\\n'
else printf 'OUT LIVE_PID_EMPTY=0\\n'; fi
printf 'OUT LIVE_STOP=%s\\n' "$(tr -d '[:space:]' < "{stop_log}" 2>/dev/null || true)"
# stop_server leaves the monitor and the VRAM sampler. Reap every helper this job started
# after the gone-check, including a tracker the stop failed to signal.
kill_helper "$saved_tracker"
kill -TERM "$child" 2>/dev/null || true
wait "$saved_tracker" 2>/dev/null || true
wait "$child" 2>/dev/null || true
kill -TERM "$MEM" 2>/dev/null || true
kill_helper "$VRAM_PID"
wait "$MEM" 2>/dev/null || true
wait "$VRAM_PID" 2>/dev/null || true
unset A770B_SLEEP_HOLD TRACKER VRAM_PID MEM SERVER_STOPPED
engine=strata
here="{strata_here}"
job="{strata_job}"
mkdir -p "$job/evidence" "$job/phases"
printf '%s\\n' owned > "$job/cid"
start_observers
printf '%s\\n' "${{TRACKER:-}}" > "{tmp_path / "strata-tracker.pid"}"
printf '%s\\n' "${{MEM:-}}" > "{tmp_path / "strata-mem.pid"}"
printf '%s\\n' "${{VRAM_PID:-}}" > "{tmp_path / "strata-vram.pid"}"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 4343 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT STRATA=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
stop_helpers
wait "$MEM" 2>/dev/null || true
wait "$VRAM_PID" 2>/dev/null || true
unset TRACKER VRAM_PID MEM SERVER_STOPPED
here=$orig_here
engine=llama
job="{phase_job}"
mkdir -p "$job/evidence" "$job/phases" "$job/scratch/seat"
start_observers
printf '%s\\n' "${{TRACKER:-}}" > "{tmp_path / "phase-tracker.pid"}"
printf '%s\\n' "${{MEM:-}}" > "{tmp_path / "phase-mem.pid"}"
printf '%s\\n' "${{VRAM_PID:-}}" > "{tmp_path / "phase-vram.pid"}"
stop_server
unset SERVER_STOPPED
GGUF=0
PROFILE_NAME=candidate
KV=
KV_V=
EXTRA=
CTX=8192
STUB=0
run_suite_once "" 1
printf 'OUT SUITE_RC=%s\\n' "$?"
"""
    try:
        proc = subprocess.run(["bash", "-c", more], env=env, capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        more_fields = {}
        for line in proc.stdout.splitlines():
            if line.startswith("OUT ") and "=" in line:
                key, value = line[4:].split("=", 1)
                more_fields[key] = value
        assert more_fields.get("LIVE_BEFORE") == "5151", proc.stdout + proc.stderr
        assert more_fields.get("LIVE_TRACKER_GONE") == "1", more_fields
        assert more_fields.get("LIVE_CHILD_GONE") == "1", more_fields
        assert more_fields.get("LIVE_CLEARED") == "1", more_fields
        assert more_fields.get("LIVE_PID_EMPTY") == "1", more_fields
        assert more_fields.get("LIVE_STOP") == "cd" * 32, more_fields
        mem_argv = (tmp_path / "mem.argv").read_text(encoding="utf-8").splitlines()
        assert "host_memory.py" in " ".join(mem_argv), mem_argv
        pid_at = mem_argv.index("--pid-file")
        assert mem_argv[pid_at + 1] == str(live_job / "evidence" / "serving.pid"), mem_argv
        assert more_fields.get("STRATA") == "4343", proc.stdout + proc.stderr
        assert more_fields.get("SUITE_RC") == "0", (
            more_fields, marker.read_text(encoding="utf-8") if marker.exists() else "", proc.stdout, proc.stderr,
        )
        phase_lines = [
            json.loads(line)
            for line in (phase_job / "evidence" / "host-memory.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        hold = int(hold_file.read_text(encoding="ascii").strip())
        seen_at = None
        for index, sample in enumerate(phase_lines):
            target = sample.get("target")
            if isinstance(target, dict) and target.get("pid") == hold:
                seen_at = index
                break
        assert seen_at is not None, phase_lines
        assert any(sample.get("target") is None for sample in phase_lines[seen_at + 1:]), phase_lines
    finally:
        for path in named:
            if not path.is_file():
                continue
            text = path.read_text(encoding="ascii", errors="replace").strip()
            if text.isdigit() and int(text) > 1 and _alive(int(text)):
                _kill_ours([int(text)])
    pidfile = tmp_path / "suite-serving.pid"
    pidfile.write_text("999\n", encoding="ascii")
    suite_env = dict(env, A770B_QUALIFY_SERVING_PID_FILE=str(pidfile))
    dry = subprocess.run(
        ["bash", str(RUN_SUITE), "--base-url", "http://127.0.0.1:9/v1",
         "--served-model-name", "qwen-candidate", "--engine", "strata", "--ctx", "8192",
         str(tmp_path / "seat"), "--stages", "s1-frontend", "--dry-run"],
        env=suite_env, cwd=ROOT, capture_output=True, text=True,
    )
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert not pidfile.exists()

def _sandbox_oom_section(report: str) -> str:
    """Body of the sandbox oom heading, and nothing the capture wrote above it."""
    marker = "## sandbox oom"
    _head, sep, tail = report.partition(marker)
    if not sep:
        raise AssertionError("capture wrote no sandbox oom section")
    body, _sep, _rest = tail.partition("\n## ")
    return body


def test_sandbox_oom_section_ignores_the_same_words_elsewhere():
    """The transcript can contain the words. Only the sandbox oom section counts."""
    report = (
        "# Task capture — sample — 2026-11-27T00:00:00+00:00\n"
        "\n## opencode transcript tail\n```\nnot recorded\n```\n"
        "\n## server-side timings during the run\nrates not measured\n"
        "\n## sandbox oom\nyes\n"
    )
    section = _sandbox_oom_section(report)
    assert section.strip() == "yes"
    assert "not recorded" not in section


def test_i12_stale_record_is_removed_and_absence_is_not_recorded(tmp_path):
    env = _env(tmp_path)
    logs = Path(env["A770B_DATA"]) / "logs"
    stale = logs / "sandbox-oom.json"
    usage = logs / "usage.json"
    stamp = logs / "run-since"
    stale.write_text('{"oom":"yes"}\n', encoding="utf-8")
    usage.write_text("{}\n", encoding="utf-8")
    stamp.write_text("old\n", encoding="utf-8")
    proc = subprocess.run(["bash", str(BUILD)], env=env, cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode != 0
    assert not stale.exists()
    assert not usage.exists()
    # A holder of the card lock keeps the stale record. The refused caller exits
    # on missing arguments before it can take the lock and remove the file.
    holder = subprocess.Popen(
        ["bash", "-c", f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; card_lock; sleep 3'],
        env=env, cwd=ROOT,
    )
    try:
        lock_file = logs / "local-build.lock"
        held = False
        for _ in range(40):
            if lock_file.exists():
                probe = subprocess.run(
                    ["bash", "-c", f'flock -n 9 9>>"{lock_file}"'],
                    env=env,
                )
                if probe.returncode != 0:
                    held = True
                    break
            time.sleep(0.05)
        assert held, "card lock was not held"
        stale.write_text('{"oom":"yes"}\n', encoding="utf-8")
        usage.write_text("{}\n", encoding="utf-8")
        stamp.write_text("held\n", encoding="utf-8")
        busy = subprocess.run(["bash", str(BUILD)], env=env, cwd=ROOT, capture_output=True, text=True)
        assert busy.returncode != 0
        assert stale.is_file() and "yes" in stale.read_text(encoding="utf-8")
        assert usage.is_file()
    finally:
        holder.terminate()
        holder.wait(timeout=5)
    stale.unlink(missing_ok=True)
    usage.unlink(missing_ok=True)
    stamp.unlink(missing_ok=True)
    seat = _seat(tmp_path)
    blog = tmp_path / "build.log"
    blog.write_text("no pytest here\n", encoding="utf-8")
    cap = subprocess.run(
        ["bash", str(CAPTURE), "i12", str(seat), str(blog)],
        env=env, cwd=ROOT, capture_output=True, text=True,
    )
    assert cap.returncode == 0, cap.stderr
    report = (Path(env["A770B_DATA"]) / "results" / "i12.task.md").read_text(encoding="utf-8")
    section = _sandbox_oom_section(report)
    assert any(line == "not recorded" for line in section.splitlines()), section
    stale.write_text('{"oom":"yes"}\n', encoding="utf-8")
    cap2 = subprocess.run(
        ["bash", str(CAPTURE), "i12b", str(seat), str(blog)],
        env=env, cwd=ROOT, capture_output=True, text=True,
    )
    assert cap2.returncode == 0, cap2.stderr
    report2 = (Path(env["A770B_DATA"]) / "results" / "i12b.task.md").read_text(encoding="utf-8")
    section = report2.split("## sandbox oom", 1)[1]
    assert "yes" in section.split("##", 1)[0]



def _verify_fixture(tmp_path, name, test):
    bin_ = _tools(tmp_path)
    _exe(bin_ / "bwrap", """
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    seat = _seat(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / f"{name}.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    (results / f"{name}.spec.json").write_text(json.dumps({"verify": {"test": test}}), encoding="utf-8")
    return env, patch, seat, results



def _scope_exec_stub(path: Path) -> None:
    """Probe succeeds. The real invocation execs the command and starts no unit."""
    _exe(path, """
case " $* " in
  *" --collect "*) exit 0 ;;
esac
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 1
""")


def test_i13_oom_zero_is_137_and_the_read_is_outside_the_timeout(tmp_path):
    env, patch, seat, results = _verify_fixture(tmp_path, "zero", "true")
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    fd1 = tmp_path / "oom-fd1"
    cmdline = tmp_path / "oom-cmd"
    journal_called = tmp_path / "journal-called"
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" show "*)
    readlink /proc/$PPID/fd/1 > "{fd1}" || true
    tr '\\0' ' ' < /proc/$PPID/cmdline > "{cmdline}" || true
    printf '%s\\n' 'ActiveState=failed' 'Result=oom-kill'
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", f"printf 1 > '{journal_called}'\nexit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True,
    )
    doc = json.loads((results / "zero.verify.json").read_text(encoding="utf-8"))
    assert doc["status"] == "fail", doc
    assert doc["reason"] == "oom"
    assert doc["exit_code"] == 137
    assert proc.returncode == 137
    report = (results / "zero.verify.md").read_text(encoding="utf-8")
    assert "verdict: FAIL (exit 137) — recorded reason: oom" in report
    assert "the exit code of the command is the verdict" not in report
    assert not journal_called.exists()
    assert fd1.read_text(encoding="utf-8").strip() == "/dev/null", fd1.read_text(encoding="utf-8")
    assert "sandbox_oom.py" in cmdline.read_text(encoding="utf-8")

    # The public command is instant. The show sleeps inside the tool bound.
    # Hidden still runs only when that sleep is added back onto the deadline.
    patch = results / "near.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    hidden = Path(env["A770B_DATA"]) / "hidden" / "hid_ok"
    hidden.write_text("print('ok')\n", encoding="utf-8")
    (results / "near.spec.json").write_text(json.dumps({
        "verify": {"test": "true", "hidden": ["hid_ok"]},
    }), encoding="utf-8")
    _exe(bin_ / "systemctl", """
case " $* " in
  *" show "*)
    /usr/bin/sleep 4
    printf '%s\\n' 'ActiveState=inactive' 'Result=success'
    ;;
esac
exit 0
""")
    started = time.monotonic()
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "4"],
        env=env, capture_output=True, text=True,
    )
    elapsed = time.monotonic() - started
    doc = json.loads((results / "near.verify.json").read_text(encoding="utf-8"))
    assert elapsed > 4, elapsed
    assert doc["public"] == "pass", (doc, proc.stdout, proc.stderr)
    assert doc["hidden"] == "pass", (doc, proc.stdout, proc.stderr)
    assert doc["status"] == "pass", doc
    assert doc["oom"]["public"] == "no"
    assert doc["exit_code"] == 0
    near_report = (results / "near.verify.md").read_text(encoding="utf-8")
    assert "the exit code of the command is the verdict" in near_report
    assert "recorded reason:" not in near_report


def test_i1_i7_oom_scope_contains_the_hog(tmp_path):
    """Two capped runs: continue leaves the command, kill ends the scope.

    The hog allocates only after /proc/self/cgroup names a770b-test-. The
    wrapper adds the cap; production arguments are logged without it.
    Opt-in: each run is a real kernel OOM and raises a desktop notification.
    """
    if os.environ.get("A770B_OOM_TESTS") != "1":
        pytest.skip(
            "real kernel OOM tests are opt-in and raise a desktop notification; "
            "set A770B_OOM_TESTS=1 to enable them"
        )
    if not _bus():
        pytest.skip("no user systemd bus")
    bin_ = _tools(tmp_path)
    log = tmp_path / "systemd-run.log"
    _exe(bin_ / "systemd-run", f"""
printf 'ORIG %s\\n' "$*" >> "{log}"
case " $* " in
  *" --collect "*) exec /usr/bin/systemd-run "$@" ;;
esac
args=()
unit=""
for a in "$@"; do
  case "$a" in
    --unit=a770b-sbx-*) unit="a770b-test-${{a#--unit=a770b-sbx-}}"; args+=("--unit=$unit") ;;
    *) args+=("$a") ;;
  esac
done
final=()
added=0
for a in "${{args[@]}}"; do
  if [ "$a" = -- ] && [ "$added" = 0 ]; then
    final+=(-p MemoryMax=100M -p MemorySwapMax=0)
    added=1
  fi
  final+=("$a")
done
printf 'UNIT %s\\n' "$unit" >> "{log}"
exec /usr/bin/systemd-run "${{final[@]}}"
""")
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    hog = seat / "hog.py"
    hog.write_text(
        "import os, sys\n"
        "cg = open('/proc/self/cgroup', encoding='ascii', errors='replace').read()\n"
        "if 'a770b-test-' not in cg:\n"
        "    sys.exit(2)\n"
        "chunks = []\n"
        "for _ in range(32):\n"
        "    chunks.append(b'x' * (8 * 1024 * 1024))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    sibling = subprocess.Popen(["sleep", "180"])
    standin = subprocess.Popen(["sleep", "180"])
    units = []
    try:
        for policy, bridge in (("continue", "0"), ("kill", "1")):
            marker = seat / f"continued-{policy}"
            marker.unlink(missing_ok=True)
            record = tmp_path / "data" / "logs" / f"{policy}.json"
            run_env = dict(env, A770B_NO_BRIDGE=bridge, A770B_SANDBOX_RECORD_FILE=str(record))
            command = f'python3 "{hog}" || true; echo continued > "{marker}"'
            proc = subprocess.run(
                ["bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", command],
                env=run_env, capture_output=True, text=True,
            )
            body = json.loads(record.read_text(encoding="utf-8"))
            if body.get("isolation") != "scope":
                pytest.fail(f"user bus is up but isolation was not scope: {body}")
            assert _alive(sibling.pid) and _alive(standin.pid)
            assert _alive(os.getpid())
            text = log.read_text(encoding="utf-8")
            assert "MemoryMax" not in "\n".join(line for line in text.splitlines() if line.startswith("ORIG"))
            assert f"OOMPolicy={policy}" in text
            unit = ""
            for line in text.splitlines():
                if line.startswith("UNIT "):
                    unit = line.split(" ", 1)[1].strip()
            assert unit.startswith("a770b-test-"), text
            if unit not in units:
                units.append(unit)
            named = unit if unit.endswith(".scope") else unit + ".scope"
            body["unit"] = named
            record.write_text(json.dumps(body) + "\n", encoding="utf-8")
            graded = subprocess.run(
                [sys.executable, "-I", "-B", str(OOM_PY), str(record), str(proc.returncode)],
                capture_output=True, text=True,
            )
            assert graded.returncode == 0, graded.stderr
            assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
            if policy == "continue":
                assert marker.exists(), proc.stderr
                assert proc.returncode == 0, proc.stderr
            else:
                assert not marker.exists(), "kill policy let the command continue"
                assert proc.returncode != 0
        assert len(units) == 2
    finally:
        for unit in units:
            named = unit if unit.endswith(".scope") else unit + ".scope"
            subprocess.run(["systemctl", "--user", "stop", "--", named], capture_output=True)
            subprocess.run(["systemctl", "--user", "reset-failed", "--", named], capture_output=True)
        for proc in (sibling, standin):
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


def test_shadow_modules_do_not_replace_stdlib(tmp_path):
    """python -I, with the caller's cwd inside the seat that plants the shadows."""
    if shutil.which("bwrap") is None or shutil.which("setpriv") is None:
        pytest.skip("bwrap and setpriv are required; this test does not stub them")
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    poison = "import sys\nsys.stderr.write('shadow\\n')\nraise SystemExit(86)\n"
    for name in ("ctypes.py", "os.py", "subprocess.py"):
        (seat / name).write_text(poison, encoding="utf-8")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    records: list[Path] = []
    try:
        for entered in (False, True):
            marker = seat / f"shadow-marker-{int(entered)}"
            marker.unlink(missing_ok=True)
            record = tmp_path / f"shadow-{int(entered)}.json"
            records.append(record)
            run_env = dict(env, A770B_SANDBOX_RECORD_FILE=str(record))
            if entered:
                run_env["A770B_SANDBOX_ENTERED_FILE"] = str(tmp_path / f"shadow-entered-{int(entered)}")
            proc = subprocess.run(
                ["bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c",
                 f"printf ok > '{marker}'"],
                env=run_env, cwd=seat, capture_output=True, text=True,
            )
            assert proc.returncode == 0, proc.stderr
            assert "shadow" not in proc.stderr
            assert marker.read_text(encoding="utf-8") == "ok"
            if entered:
                assert Path(run_env["A770B_SANDBOX_ENTERED_FILE"]).read_text(encoding="utf-8").strip() == "entered"
            _scope_unit(record)
    finally:
        _stop_records(records)


def _ready_publish(assignment: str) -> str:
    """Publish a ready file by rename so a reader never sees an empty file."""
    return f"""
{assignment}
tmp=$(mktemp "$(dirname "$ready")/ready.XXXXXX") || exit 1
printf '%s\\n' "$$" > "$tmp"
mv -f "$tmp" "$ready"
"""


def _slow_python(bin_: Path) -> None:
    publish = _ready_publish('ready="${A770B_SLOW_READY:?}"')
    _exe(bin_ / "python3", f"""
if [ -n "${{A770B_SLOW_NEEDLE:-}}" ] && [[ " $* " == *" ${{A770B_SLOW_NEEDLE}} "* ]]; then
{publish}
  /usr/bin/sleep 2
fi
exec /usr/bin/python3 "$@"
""")


def _wait_ready(path: Path, proc: subprocess.Popen, seconds: float = 20) -> int:
    """Return the published pid. An empty file is not ready, and a timeout fails."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None and not path.exists():
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"parent exited {proc.returncode} before the delayed python: {err}")
        if path.exists():
            text = path.read_text(encoding="ascii", errors="replace").strip()
            if text.isdigit():
                return int(text)
        time.sleep(0.05)
    raise AssertionError("delayed python did not publish a pid")


def _arm_pid_gone(pid: int, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid), f"delayed python {pid} kept running after its parent died"


def test_b3_slow_start_does_not_arm_the_reaper(tmp_path):
    """Kill the original parent while python is still starting. The command must not run."""
    bin_ = _tools(tmp_path)
    _slow_python(bin_)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    base = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    saved_units: list[str] = []
    try:
        ready = tmp_path / "lead.ready"
        marker = tmp_path / "lead-ran"
        env = dict(base, A770B_SLOW_NEEDLE=str(RUN_LEAD), A770B_SLOW_READY=str(ready))
        script = (
            "(\n"
            "export A770B_RUN_OWNER_PID=$$\n"
            f'exec python3 "{RUN_LEAD}" -- bash -c \'printf ran > "{marker}"\'\n'
            ")\n"
            "wait\n"
        )
        proc = subprocess.Popen(
            ["bash", "-c", script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        owned: list[int] = []
        try:
            delayed = _wait_ready(ready, proc)
            owned = [proc.pid, delayed]
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _arm_pid_gone(delayed)
            assert not marker.exists()
        finally:
            _reap_owned(proc, owned)

        ready = tmp_path / "entered.ready"
        marker = seat / "entered-ran"
        entered = tmp_path / "b3-entered"
        record = tmp_path / "b3.json"
        env = dict(
            base,
            A770B_SLOW_NEEDLE=str(entered),
            A770B_SLOW_READY=str(ready),
            A770B_SANDBOX_ENTERED_FILE=str(entered),
            A770B_SANDBOX_RECORD_FILE=str(record),
        )
        proc = subprocess.Popen(
            ["bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", f'printf ran > "{marker}"'],
            env=env, cwd=seat, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        owned = []
        try:
            delayed = _wait_ready(ready, proc)
            unit = _require_recorded_scope(record, proc.pid)
            if unit is not None:
                saved_units.append(unit)
            owned = _note_owned(proc.pid, [delayed])
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _arm_pid_gone(delayed)
            assert not marker.exists()
        finally:
            _reap_owned(proc, owned)
            _stop_records([record])

        ready = tmp_path / "verify.ready"
        marker = seat / "verify-ran"
        env = dict(base, A770B_SLOW_NEEDLE=str(SANDBOX), A770B_SLOW_READY=str(ready))
        results = Path(env["A770B_DATA"]) / "results"
        patch = results / "b3.patch"
        patch.write_text(
            "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
            encoding="utf-8",
        )
        (results / "b3.spec.json").write_text(
            json.dumps({"verify": {"test": f"printf ran > '{marker}'"}}), encoding="utf-8",
        )
        proc = subprocess.Popen(
            ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "30"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        owned = []
        try:
            delayed = _wait_ready(ready, proc, seconds=30)
            owned = _note_owned(proc.pid, [delayed])
            logs = Path(env["A770B_DATA"]) / "logs"
            for unit in _scopes_in_logs(logs):
                saved_units.append(unit)
            # Kill the outer verify process, not the grader's parent if those differ.
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _arm_pid_gone(delayed)
            assert not marker.exists()
            left = _wait_gone([pid for pid in owned if pid != proc.pid])
            assert left == [], f"verify descendants survived the owner: {left}"
        finally:
            _reap_owned(proc, owned)
            _stop_log_scopes(Path(env["A770B_DATA"]) / "logs", saved_units)
    finally:
        _stop_log_scopes(Path(base["A770B_DATA"]) / "logs", saved_units)


def _arm_delay_copy(dest: Path) -> Path:
    """Production grader plus a pause at the start of its preexec function.

    The pause is not in the shipped script. It only gives the test a window
    in which to kill the parent before the production check runs.
    """
    text = LOCAL_BUILD.read_text(encoding="utf-8")
    needle = "def _arm_child():\n"
    assert text.count(needle) == 1, "grader preexec function moved"
    injected = (
        "def _arm_child():\n"
        "    _ready = os.environ.get('A770B_ARM_DELAY_READY', '')\n"
        "    if _ready:\n"
        "        with open(_ready, 'w', encoding='ascii') as _fh:\n"
        "            _fh.write(str(os.getpid()))\n"
        "        time.sleep(2)\n"
    )
    dest.write_text(text.replace(needle, injected, 1), encoding="utf-8")
    dest.chmod(0o755)
    return dest


def test_b3_verify_owner_death_reaps_descendants_and_the_seat_lock(tmp_path):
    """SIGKILL the outer verify pid. Its descendants and the seat lock must go too."""
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    token = f"vown{tmp_path.name}"
    ready = seat / "owner-ready"
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "owner.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+seat changed\n",
        encoding="utf-8",
    )
    (results / "owner.spec.json").write_text(json.dumps({
        "verify": {"test": _sleeper_command(token, ready)},
    }), encoding="utf-8")
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "60"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    logs = Path(env["A770B_DATA"]) / "logs"
    owned: list[int] = []
    units: list[str] = []
    try:
        sleeper = _wait_sleeper(verify.pid, ready, token, verify, seconds=30)
        owned = _note_owned(verify.pid, [sleeper])
        for unit in _scopes_in_logs(logs):
            embedded = int(_SCOPE_UNIT.fullmatch(unit).group(1))
            assert embedded in owned, unit
            units.append(unit)
        lock = _fd_target(verify.pid, 7)
        assert lock, "verify did not hold the seat lock"
        assert not _lock_available(lock), "seat lock was not held before the kill"
        held = [
            pid for pid in owned
            if pid != verify.pid and ({"7", "9"} & _proc_fds(pid))
        ]
        os.kill(verify.pid, signal.SIGKILL)
        verify.wait(timeout=5)
        left = _wait_gone([pid for pid in owned if pid != verify.pid])
        assert left == [], f"descendants survived verify: {left}"
        assert _lock_available(lock), "seat lock stayed held after verify died"
        assert held == [], f"a descendant held lock fd 7 or 9: {held}"
    finally:
        _reap_owned(verify, owned)
        _stop_log_scopes(logs, units)


def test_b3_preexec_parent_is_the_pid_from_before_the_fork(tmp_path):
    """Kill the grader while its child is still in preexec. Production has no delay hook."""
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")
    env, patch, seat, _results = _verify_fixture(tmp_path, "hook", "true")
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=inactive' 'Result=success' ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    logs = Path(env["A770B_DATA"]) / "logs"
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=dict(env, A770B_SLOW_ARM_READY=str(sentinel)),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    _stop_log_scopes(logs)

    ready = tmp_path / "arm.ready"
    marker = tmp_path / "arm-ran"
    delay_root = tmp_path / "delay"
    delay_root.mkdir()
    env, patch, seat, _results = _verify_fixture(delay_root, "arm", f"printf ran > '{marker}'")
    script = _arm_delay_copy(delay_root / "local-build-arm-delay.sh")
    logs = Path(env["A770B_DATA"]) / "logs"
    proc = subprocess.Popen(
        ["bash", str(script), "verify", str(patch), str(seat), "--timeout", "30"],
        env=dict(env, A770B_ARM_DELAY_READY=str(ready)),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    owned: list[int] = []
    try:
        delayed = _wait_ready(ready, proc)
        owned = _note_owned(proc.pid, [delayed])
        os.kill(_ppid(delayed), signal.SIGKILL)
        _arm_pid_gone(delayed)
        assert not marker.exists()
    finally:
        _reap_owned(proc, owned)
        _stop_log_scopes(logs)


def _delay_first_sandbox_bash(bin_: Path, ready: Path) -> None:
    """PATH bash that pauses once, before the real interpreter reads PPID."""
    publish = _ready_publish(f'ready="{ready}"')
    _exe(bin_ / "bash", f"""
if [ "${{1:-}}" = "{SANDBOX}" ] && [ -z "${{A770B_BASH_DELAY_DONE:-}}" ]; then
  export A770B_BASH_DELAY_DONE=1
{publish}
  /usr/bin/sleep 2
fi
exec /usr/bin/bash "$@"
""")


def test_b3_worker_checks_the_pid_its_leader_passed(tmp_path):
    """Kill the leader while sandbox_run is still starting. A direct call passes no pid."""
    bin_ = _tools(tmp_path)
    ready = tmp_path / "worker.ready"
    _delay_first_sandbox_bash(bin_, ready)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    marker = seat / "worker-ran"
    record = tmp_path / "worker.json"
    direct_record = tmp_path / "direct.json"
    nested_record = tmp_path / "nested.json"
    env = _env(
        tmp_path,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_NO_BRIDGE="1",
        A770B_SANDBOX_RECORD_FILE=str(record),
    )
    command = f'printf ran > "{marker}"'
    script = (
        "(\n"
        "export A770B_RUN_OWNER_PID=$$\n"
        f'exec python3 "{RUN_LEAD}" -- bash "{SANDBOX}" --parent "$BASHPID" "{seat}" "{cfg}" -- '
        f"bash -c {json.dumps(command)}\n"
        ")\n"
        "wait\n"
    )
    proc = subprocess.Popen(
        ["bash", "-c", script], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    owned: list[int] = []
    try:
        delayed = _wait_ready(ready, proc)
        leader = _ppid(delayed)
        owned = _note_owned(proc.pid, [delayed, leader])
        os.kill(leader, signal.SIGKILL)
        _arm_pid_gone(delayed)
        proc.wait(timeout=5)
        err = proc.stderr.read() if proc.stderr else ""
        assert "parent changed during arming" in err, err
        assert not marker.exists()
    finally:
        _reap_owned(proc, owned)
        _stop_records([record])
    direct = subprocess.run(
        ["/usr/bin/bash", str(SANDBOX), str(seat), str(cfg), "--", "true"],
        env=dict(env, A770B_SANDBOX_RECORD_FILE=str(direct_record)),
        capture_output=True, text=True,
    )
    nested = subprocess.run(
        ["/usr/bin/bash", "-c", f'/usr/bin/bash "{SANDBOX}" "{seat}" "{cfg}" -- true'],
        env=dict(env, A770B_SANDBOX_RECORD_FILE=str(nested_record)),
        capture_output=True, text=True,
    )
    try:
        assert direct.returncode == 0, direct.stderr
        assert nested.returncode == 0, nested.stderr
    finally:
        _stop_records([direct_record, nested_record])


def _confine_harness_outputs(env, tmp_path):
    """Keep this test off an inherited verify handoff or worker receipt."""
    env.pop("A770B_VERIFY_OUT", None)
    env["A770B_WORKER_PGID_FILE"] = str(tmp_path / "worker-pgid")
    return env


def _private_lead_env(tmp_path: Path) -> dict:
    """Drop inherited harness paths before a test starts the real leader."""
    scratch = tmp_path / "lead-tmp"
    data = tmp_path / "lead-data"
    scratch.mkdir(exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("A770B_")}
    env["TMPDIR"] = str(scratch)
    env["A770B_DATA"] = str(data)
    return env


def test_non_sandbox_worker_launch_keeps_an_outside_receipt(tmp_path, monkeypatch):
    """A worker that is not the sandbox must not replace an inherited receipt."""
    outside = tmp_path.parent / f"{tmp_path.name}-operator-receipt"
    outside.mkdir()
    receipt = outside / "run.worker-pgid"
    receipt.write_text("keep\n", encoding="ascii")
    data = outside / "data"
    (data / "logs").mkdir(parents=True)
    data_receipt = data / "logs" / "run.worker-pgid"
    data_receipt.write_text("keep-data\n", encoding="ascii")
    monkeypatch.setenv("A770B_WORKER_PGID_FILE", str(receipt))
    monkeypatch.setenv("A770B_DATA", str(data))
    monkeypatch.setenv("TMPDIR", str(outside))
    try:
        env = _private_lead_env(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(RUN_LEAD), "--", sys.executable, "-c", "raise SystemExit(0)"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        proc = subprocess.run(
            [sys.executable, str(RUN_LEAD), "--", "bash", "-c", "exit 0"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        impostor = tmp_path / "sandbox_run.sh"
        impostor.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        impostor.chmod(0o755)
        proc = subprocess.run(
            [sys.executable, str(RUN_LEAD), "--", "bash", str(impostor)],
            env=env, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert receipt.read_text(encoding="ascii") == "keep\n"
        assert data_receipt.read_text(encoding="ascii") == "keep-data\n"
        monkeypatch.delenv("A770B_WORKER_PGID_FILE", raising=False)
        data_receipt.write_text("keep-data\n", encoding="ascii")
        data_env = _private_lead_env(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(RUN_LEAD), "--", "bash", "-c", "exit 0"],
            env=data_env, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert data_receipt.read_text(encoding="ascii") == "keep-data\n"
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_parent_argument_is_a_positive_decimal_integer():
    """Anything else is usage, before a worktree is required."""
    for value in ("", "0", "01", "-1", "1a", " 1"):
        args = ["/usr/bin/bash", str(SANDBOX), "--parent"]
        if value != "":
            args.append(value)
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        assert proc.returncode == 2, (value, proc.returncode, proc.stderr)
        assert "positive decimal integer" in proc.stderr, proc.stderr


def test_parent_argument_mismatch_exits_137_and_a_match_runs(tmp_path):
    """The passed pid is compared with $PPID after arming. No process table is consulted."""
    marker = tmp_path / "ran"
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    bin_ = _tools(tmp_path)
    _exe(bin_ / "bwrap", """
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", "exit 0\n")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    stranger = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        refused = subprocess.run(
            ["/usr/bin/bash", str(SANDBOX), "--parent", str(stranger.pid), str(seat), str(cfg), "--",
             "bash", "-c", f'printf ran > "{marker}"'],
            env=env, capture_output=True, text=True, check=False,
        )
        assert refused.returncode == 137, refused.stderr
        assert "parent changed during arming" in refused.stderr, refused.stderr
        assert not marker.exists()
        accepted = subprocess.run(
            ["/usr/bin/bash", str(SANDBOX), "--parent", str(os.getpid()), str(seat), str(cfg), "--",
             "bash", "-c", f'printf ran > "{marker}"'],
            env=env, capture_output=True, text=True, check=False,
        )
        assert accepted.returncode == 0, accepted.stderr
        assert marker.read_text(encoding="utf-8") == "ran"
    finally:
        if stranger.poll() is None:
            stranger.kill()
        stranger.wait(timeout=5)


def test_supplied_parent_is_checked_when_arming_is_already_marked(tmp_path):
    """Exporting this pid as already armed must not discard a supplied --parent."""
    marker = tmp_path / "ran"
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    bin_ = _tools(tmp_path)
    _exe(bin_ / "bwrap", """
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", "exit 0\n")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    env.pop("A770B_PDEATH_PARENT", None)
    env.pop("A770B_PDEATH_ARMED_PID", None)
    command = f'printf ran > "{marker}"'
    wrapper = (
        "unset A770B_PDEATH_PARENT\n"
        "export A770B_PDEATH_ARMED_PID=$$\n"
        f'exec /usr/bin/bash "{SANDBOX}" --parent "$1" "{seat}" "{cfg}" -- '
        f"bash -c {json.dumps(command)}\n"
    )
    stranger = subprocess.Popen(
        ["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        refused = subprocess.run(
            ["/usr/bin/bash", "-c", wrapper, "wrapper", str(stranger.pid)],
            env=env, capture_output=True, text=True, check=False,
        )
        assert refused.returncode == 137, refused.stderr
        assert "parent changed during arming" in refused.stderr, refused.stderr
        assert not marker.exists()
        marker.write_text("stale\n", encoding="utf-8")
        accepted = subprocess.run(
            ["/usr/bin/bash", "-c", wrapper, "wrapper", str(os.getpid())],
            env=env, capture_output=True, text=True, check=False,
        )
        assert accepted.returncode == 0, accepted.stderr
        assert marker.read_text(encoding="utf-8") == "ran"
    finally:
        if stranger.poll() is None:
            stranger.kill()
        stranger.wait(timeout=5)


def test_inherited_spawner_pid_has_no_effect(tmp_path):
    """A dead inherited pid used to abort a direct sandbox. The variable is ignored."""
    dead = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dead_pid = dead.pid
    dead.kill()
    dead.wait(timeout=5)
    assert not _alive(dead_pid)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    bin_ = _tools(tmp_path)
    _exe(bin_ / "bwrap", """
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", "exit 0\n")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    env["A770B_SPAWNER_PID"] = str(dead_pid)
    marker = tmp_path / "ran"
    proc = subprocess.run(
        ["/usr/bin/bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", f'printf ran > "{marker}"'],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "parent changed during arming" not in proc.stderr
    assert marker.read_text(encoding="utf-8") == "ran"


def test_build_local_passes_the_leader_pid_as_parent(tmp_path):
    """The real build_local.sh passes --parent from the subshell that becomes the leader."""
    bin_ = _tools(tmp_path)
    seen = tmp_path / "seen"
    _exe(bin_ / "curl", "exit 0\n")
    _exe(bin_ / "bash", f"""
case "${{1:-}}" in
  *sandbox_run.sh)
    tr '\\0' '\\n' < /proc/"$PPID"/cmdline > "{seen}/cmdline"
    printf '%s\\n' "$PPID" > "{seen}/ppid"
    printf '%s\\n' "$@" > "{seen}/argv"
    exit 0
    ;;
esac
exec /usr/bin/bash "$@"
""")
    seen.mkdir()
    registry = tmp_path / "registry.json"
    registry.write_bytes((ROOT / "config" / "registry" / "a770.display.json").read_bytes())
    seat = _seat(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text("# task\n", encoding="utf-8")
    env = _env(
        tmp_path,
        PATH=f"{bin_}:{os.environ['PATH']}",
        PROFILE="qwen35-9b-q4km-vulkan",
        A770B_PROFILES_FILE=str(registry),
        A770B_CARD_MODE="display",
        SANDBOX="1",
    )
    proc = subprocess.run(
        ["bash", str(BUILD), str(seat), str(brief)],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = (seen / "argv").read_text(encoding="utf-8").splitlines()
    assert argv[1] == "--parent", argv
    parent = argv[2]
    assert parent.isdigit() and int(parent) > 0
    assert parent == (seen / "ppid").read_text(encoding="ascii").strip()
    leader = (seen / "cmdline").read_text(encoding="utf-8")
    assert "run_lead.py" in leader, leader


def test_b2_abandoned_scope_is_stopped_and_tools_are_bounded(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "harness"))
    import sandbox_oom
    monkeypatch.setattr(sandbox_oom, "_SETTLE_S", 0.4)
    monkeypatch.setattr(sandbox_oom, "_JOURNAL_S", 0.2)
    monkeypatch.setattr(sandbox_oom, "_TOOL_TIMEOUT_S", 0.4)
    log = tmp_path / "ctl.log"
    bin_ = tmp_path / "b2-bin"
    _exe(bin_ / "systemctl", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*)
    if grep -q ' stop ' "{log}"; then
      printf '%s\\n' 'ActiveState=inactive' 'Result=success'
    else
      printf '%s\\n' 'ActiveState=active' 'Result=success'
    fi
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "b2.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope", "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    sandbox_oom.update(str(record), 0)
    text = log.read_text(encoding="utf-8")
    assert " stop " in text, text
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    log.unlink()
    _exe(bin_ / "systemctl", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*) exec /usr/bin/sleep 3 ;;
esac
exit 0
""")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-43-8.scope", "policy": "continue", "oom": "unknown",
    }), encoding="utf-8")
    started = time.monotonic()
    sandbox_oom.update(str(record), 1)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, elapsed
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"


def test_b4_verify_records_are_unique(tmp_path):
    env, patch, seat, results = _verify_fixture(tmp_path, "b4", "true")
    bin_ = tmp_path / "bin"
    seen = tmp_path / "records.log"
    _exe(bin_ / "systemd-run", f"""
case " $* " in
  *" --collect "*) exit 0 ;;
esac
printf '%s\\n' "$A770B_SANDBOX_RECORD_FILE" >> "{seen}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 1
""")
    hidden = Path(env["A770B_DATA"]) / "hidden" / "hid_b4"
    hidden.write_text("print('ok')\n", encoding="utf-8")
    (results / "b4.spec.json").write_text(json.dumps({
        "verify": {"test": "true", "hidden": ["hid_b4"]},
    }), encoding="utf-8")
    _exe(bin_ / "systemctl", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=inactive' 'Result=success' ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True,
    )
    doc = json.loads((results / "b4.verify.json").read_text(encoding="utf-8"))
    assert doc["public"] == "pass" and doc["hidden"] == "pass", (doc, proc.stdout, proc.stderr)
    paths = [line for line in seen.read_text(encoding="utf-8").splitlines() if line]
    assert len(paths) == 2, paths
    assert len(set(paths)) == 2, paths
    for path in paths:
        name = Path(path).name
        assert name.startswith("verify-oom-") and name.endswith(".json"), name
        assert name not in ("verify-oom-public.json", "verify-oom-hidden.json")
        assert not Path(path).exists()
    logs = Path(env["A770B_DATA"]) / "logs"
    assert list(logs.glob("verify-oom-*.json")) == []


def test_m1_signal_before_oom_helper_and_lock_fds_closed(tmp_path):
    text = BUILD.read_text(encoding="utf-8")
    start = text.index("rc=${PIPESTATUS[0]}")
    end = text.index("_stop_orphan_watch\n", start) + len("_stop_orphan_watch\n")
    snippet = text[start:end]
    order = tmp_path / "order"
    fds = tmp_path / "fds"
    record = tmp_path / "sandbox-oom.json"
    record.write_text("{}\n", encoding="utf-8")
    bin_ = tmp_path / "m1-bin"
    _exe(bin_ / "python3", f"""
if [[ " $* " == *sandbox_oom.py* ]]; then
  printf '%s\\n' python >> "{order}"
  ls -1 /proc/$$/fd > "{fds}"
  exit 0
fi
exec /usr/bin/python3 "$@"
""")
    script = f"""
set +e
signal_recorded_worker_group() {{ printf '%s\\n' signal >> "{order}"; }}
_leader_proc_gone() {{ return 1; }}
_stop_orphan_watch() {{ :; }}
export A770B_PROJECT="{ROOT}"
export A770B_WORKER_PGID_FILE="{tmp_path / "worker"}"
export SANDBOX=1
export _SANDBOX_OOM="{record}"
export PATH="{bin_}:{os.environ['PATH']}"
exec 7>"{tmp_path / "lock7"}" 9>"{tmp_path / "lock9"}"
set -o pipefail
(exit 137) | :
{snippet}
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    lines = order.read_text(encoding="utf-8").splitlines()
    assert lines.index("signal") < lines.index("python"), lines
    opened = set(fds.read_text(encoding="utf-8").split())
    assert "7" not in opened and "9" not in opened, opened


def test_m3_corrupt_record_is_not_a_traceback(tmp_path):
    env = _env(tmp_path)
    seat = _seat(tmp_path)
    blog = tmp_path / "build.log"
    blog.write_text("no pytest here\n", encoding="utf-8")
    record = Path(env["A770B_DATA"]) / "logs" / "sandbox-oom.json"
    for name, body in (("brace", "{"), ("list", "[]"), ("null", "null"), ("str", '"x"')):
        record.write_text(body, encoding="utf-8")
        cap = subprocess.run(
            ["bash", str(CAPTURE), name, str(seat), str(blog)],
            env=env, cwd=ROOT, capture_output=True, text=True,
        )
        assert cap.returncode == 0, cap.stderr
        report = (Path(env["A770B_DATA"]) / "results" / f"{name}.task.md").read_text(encoding="utf-8")
        assert "Traceback" not in report
        section = report.split("## sandbox oom", 1)[1].split("##", 1)[0]
        assert "not recorded" in section, report


def test_m6_setup_oom_before_entered_is_infra(tmp_path):
    env, patch, seat, results = _verify_fixture(tmp_path, "m6", "true")
    bin_ = tmp_path / "bin"
    _exe(bin_ / "bwrap", "exit 0\n")
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=failed' 'Result=oom-kill' ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True,
    )
    doc = json.loads((results / "m6.verify.json").read_text(encoding="utf-8"))
    assert doc["status"] == "infra_error", (doc, proc.stdout, proc.stderr)
    assert doc["reason"] != "oom"
    assert doc["oom"]["public"] == "yes"


def test_stop_record_rejects_a_unit_the_test_does_not_own(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"unit": "ssh.service"}), encoding="utf-8")
    try:
        _stop_records([record])
    except AssertionError as exc:
        assert "does not name this test's scope" in str(exc)
    else:
        raise AssertionError(f"stopped a unit this test does not own: {calls}")
    assert calls == []


def test_stop_record_stops_only_the_named_scope(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "list-units" in cmd:
            raise AssertionError("cleanup enumerated units")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"unit": "a770b-sbx-42-7.scope"}), encoding="utf-8")
    empty = tmp_path / "none.json"
    empty.write_text(json.dumps({"unit": None}), encoding="utf-8")
    _stop_records([record, empty, tmp_path / "missing.json"])
    stopped = [cmd for cmd in calls if "stop" in cmd]
    assert stopped == [[REAL_SYSTEMCTL, "--user", "stop", "--", "a770b-sbx-42-7.scope"]]
    assert all("list-units" not in cmd for cmd in calls)


def test_failed_scope_query_does_not_widen(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "list-units" in cmd:
            raise AssertionError("a failed query enumerated units")
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(AssertionError, match="could not observe"):
        _observe_scope("a770b-sbx-42-7.scope")
    assert calls == [[
        REAL_SYSTEMCTL, "--user", "show", "-p", "ActiveState", "--", "a770b-sbx-42-7.scope",
    ]]


def test_scope_is_finished_rejects_a_unit_still_changing():
    """active and activating are not the only unfinished states."""
    assert _scope_is_finished("inactive")
    assert _scope_is_finished("failed")
    for state in ("active", "activating", "deactivating", "reloading", "maintenance"):
        assert not _scope_is_finished(state), state


def test_observe_scope_uses_the_real_systemctl_binary(monkeypatch, tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    _exe(stub / "systemctl", "#!/bin/bash\nprintf '%s\\n' 'ActiveState=active'\n")
    monkeypatch.setenv("PATH", f"{stub}:/usr/bin:/bin")
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, "ActiveState=inactive\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _observe_scope("a770b-sbx-9-1.scope") == "inactive"
    assert seen == [REAL_SYSTEMCTL]


def _qualify_funcs(tmp_path: Path) -> Path:
    prefix = QUALIFY.read_text(encoding="utf-8").split("[ $# -ge 1 ] || usage", 1)[0]
    prefix = prefix.replace(
        'here=$(cd "$(dirname "$0")" && pwd)',
        f'here="{ROOT / "harness"}"',
        1,
    )
    funcs = tmp_path / "qualify-funcs.sh"
    funcs.write_text(prefix, encoding="utf-8")
    return funcs


def _observer_script(tmp_path: Path, body: str) -> str:
    """Source the real observer and always stop the helpers it starts."""
    funcs = _qualify_funcs(tmp_path)
    return f"""
set -m
set +e
. "{funcs}"
gpu_used_gib() {{ printf '%s\\n' 0; }}
trap 'stop_helpers' EXIT
{body}
"""


def _run_observer(tmp_path: Path, body: str, bin_: Path) -> dict[str, str]:
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_GPU_MATCH="red-gpu")
    proc = subprocess.run(
        ["bash", "-c", _observer_script(tmp_path, body)],
        env=env, capture_output=True, text=True, timeout=40,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    return fields


def _observer_bin(tmp_path: Path, docker_pid: Path) -> Path:
    bin_ = _tools(tmp_path)
    _exe(bin_ / "docker", f"""
if [ "${{1:-}}" = inspect ]; then cat "{docker_pid}"; fi
exit 0
""")
    _exe(bin_ / "nvtop", "exit 0\n")
    _exe(bin_ / "a770b-qualify-stop", f"printf '%s\\n' \"$*\" >> \"{tmp_path}/qualify-stop.log\"\nexit 0\n")
    return bin_


def test_non_numeric_serving_pid_is_never_published(tmp_path):
    """Docker's reply is published only when it is a positive decimal integer."""
    count = tmp_path / "inspect-n"
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    bin_ = _observer_bin(tmp_path, tmp_path / "unused.pid")
    _exe(bin_ / "docker", f"""
if [ "${{1:-}}" != inspect ]; then exit 0; fi
n=0
[ -f "{count}" ] && n=$(cat "{count}")
n=$((n + 1))
printf '%s\\n' "$n" > "{count}"
case "$n" in
  1) printf '%s\\n' '12abc' ;;
  2) printf '%s\\n' '12 34' ;;
  3) printf '%s\\n' '0' ;;
  4) printf '%s\\n' '0123' ;;
  *) printf '%s\\n' '5151' ;;
esac
exit 0
""")
    cid = "ab" * 32
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
start_observers
printf '%s\\n' '{cid}' > "$job/cid"
bad=0
seen=""
deadline=$((SECONDS + 8))
while [ "$SECONDS" -lt "$deadline" ]; do
  cur=$(cat "$job/evidence/serving.pid" 2>/dev/null || true)
  case "$cur" in
    ''|5151) ;;
    *) bad=1; seen=$cur ;;
  esac
  if [ "$cur" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT BAD=%s\\n' "$bad"
printf 'OUT SEEN=%s\\n' "$seen"
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf 'OUT INSPECTS=%s\\n' "$(cat "{count}" 2>/dev/null || echo 0)"
""", bin_)
    assert int(fields.get("INSPECTS", "0")) >= 5, fields
    assert fields.get("BAD") == "0", fields
    assert fields.get("PID") == "5151", fields
    assert fields.get("SEEN", "") == "", fields


def test_serving_pid_is_not_written_through_a_planted_temp_name(tmp_path):
    """The publication temp is created exclusively, not opened at a predictable path."""
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("5151\n", encoding="ascii")
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    bin_ = _observer_bin(tmp_path, docker_pid)
    cid = "ab" * 32
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
start_observers
# $$ in the tracker subshell is this shell, which is the predictable temp name.
ln -sfn "{secret}" "$job/evidence/.serving.pid.$$"
printf '%s\\n' '{cid}' > "$job/cid"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf 'OUT SECRET=%s\\n' "$(cat "{secret}")"
printf 'OUT LINK=%s\\n' "$([ -L "$job/evidence/.serving.pid.$$" ] && echo 1 || echo 0)"
""", bin_)
    assert fields.get("PID") == "5151", fields
    assert fields.get("SECRET") == "sentinel", fields
    assert fields.get("LINK") == "1", fields


def test_serving_pid_retries_the_same_pid_when_rename_fails(tmp_path):
    """last advances only after the rename, so one failed mv does not skip that pid."""
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("5151\n", encoding="ascii")
    real_mv = shutil.which("mv")
    assert real_mv
    flag = tmp_path / "mv-failed"
    bin_ = _observer_bin(tmp_path, docker_pid)
    _exe(bin_ / "mv", f"""
dest="${{@: -1}}"
case "$dest" in
  */evidence/serving.pid)
    if [ ! -f "{flag}" ]; then : > "{flag}"; exit 1; fi
    ;;
esac
exec "{real_mv}" "$@"
""")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    cid = "cd" * 32
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
start_observers
printf '%s\\n' '{cid}' > "$job/cid"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid" 2>/dev/null)" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid" 2>/dev/null)"
printf 'OUT MV_FAILED=%s\\n' "$([ -f "{flag}" ] && echo 1 || echo 0)"
""", bin_)
    assert fields.get("MV_FAILED") == "1", fields
    assert fields.get("PID") == "5151", fields


def test_serving_pid_is_not_published_into_a_directory(tmp_path):
    """A directory at serving.pid is a failed rename. The same pid is published once it is a file again."""
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("5151\n", encoding="ascii")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    bin_ = _observer_bin(tmp_path, docker_pid)
    cid = "ab" * 32
    pidfile = job / "evidence" / "serving.pid"
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
start_observers
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ -f "$job/evidence/serving.pid" ]; then break; fi
  /usr/bin/sleep 0.05
done
rm -f -- "$job/evidence/serving.pid"
mkdir -- "$job/evidence/serving.pid"
printf '%s\\n' '{cid}' > "$job/cid"
/usr/bin/sleep 1.5
if [ -d "$job/evidence/serving.pid" ]; then printf 'OUT DIR=1\\n'; else printf 'OUT DIR=0\\n'; fi
inside=$(find "$job/evidence/serving.pid" -mindepth 1 -print 2>/dev/null | wc -l | tr -d '[:space:]')
printf 'OUT INSIDE=%s\\n' "$inside"
rmdir -- "$job/evidence/serving.pid" 2>/dev/null || true
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ -f "$job/evidence/serving.pid" ] && [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid" 2>/dev/null)"
""", bin_)
    assert fields.get("DIR") == "1", fields
    assert fields.get("INSIDE") == "0", fields
    assert fields.get("PID") == "5151", fields
    assert pidfile.is_file()
    assert not pidfile.is_dir()


def test_serving_pid_is_empty_when_the_server_is_gone(tmp_path):
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("5151\n", encoding="ascii")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    bin_ = _observer_bin(tmp_path, docker_pid)
    cid = "ef" * 32
    pidfile = job / "evidence" / "serving.pid"
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
start_observers
printf '%s\\n' '{cid}' > "$job/cid"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 5151 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
: > "{docker_pid}"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ ! -s "$job/evidence/serving.pid" ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT EMPTY=%s\\n' "$([ ! -s "$job/evidence/serving.pid" ] && echo 1 || echo 0)"
printf 'OUT LEFT=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
""", bin_)
    assert fields.get("PID") == "5151", fields
    assert fields.get("EMPTY") == "1", fields
    assert fields.get("LEFT") == "", fields
    assert pidfile.read_bytes() == b""


def test_reused_server_is_observed_and_not_recorded_as_owned(tmp_path):
    """cmd_run's empty cid file is not ownership. An already-up server is still sampled."""
    docker_pid = tmp_path / "docker.pid"
    docker_pid.write_text("4242\n", encoding="ascii")
    live = "12" * 32
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    bin_ = _observer_bin(tmp_path, docker_pid)
    fields = _run_observer(tmp_path, f"""
export A770B_DOCKER="{bin_ / "docker"}"
export STUB=0
engine=llama
job="{job}"
mkdir -p "$A770B_DATA/logs" "$job/phases"
printf '%s\\n' '{live}' > "$A770B_DATA/logs/llamacpp-a770.cid"
# cmd_run truncates this file before the serve phase. An empty file is not an id.
plain_trunc "$job/cid"
start_observers
deadline=$((SECONDS + 2))
while [ "$SECONDS" -lt "$deadline" ]; do /usr/bin/sleep 0.05; done
printf 'OUT QUIET=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf '✓ llama already up (same argv)\\n' > "$job/phases/serve.log"
deadline=$((SECONDS + 5))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(tr -d '[:space:]' < "$job/evidence/serving.pid")" = 4242 ]; then break; fi
  /usr/bin/sleep 0.05
done
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
printf 'OUT CID=%s\\n' "$(tr -d '[:space:]' < "$job/cid")"
stop_server
printf 'OUT STOP=%s\\n' "$(cat "{tmp_path}/qualify-stop.log" 2>/dev/null || true)"
""", bin_)
    assert fields.get("QUIET") == "", fields
    assert fields.get("PID") == "4242", fields
    assert fields.get("CID", "x") == "", fields
    assert fields.get("STOP", "") == "", fields
    assert (job / "cid").is_file()
    assert not (job / "cid").is_symlink()


def _end_named(pid: int) -> None:
    """Signal one process this test started, then its recorded worker if that file names one."""
    if pid > 1 and _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.05)


def _end_worker_record(data: Path) -> None:
    record = data / "logs" / "run.worker-pgid"
    if not record.is_file() or record.is_symlink():
        return
    parts = record.read_text(encoding="ascii", errors="replace").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return
    pid = int(parts[0])
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        start = stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return
    if start != parts[1]:
        return
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.05)


def _exec_lead_text() -> str:
    source = BUILD.read_text(encoding="utf-8")
    start = source.index("exec_lead(){")
    end = source.index("\n}\n", start)
    return source[start:end + 2]


def _argv(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def test_exec_lead_starts_isolated_and_stop_run_accepts_both_forms(tmp_path):
    """-I -B is how the leader is started. stop-run accepts that form and the legacy one."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "sitecustomize.py").write_text(
        "import sys\nsys.stderr.write('shadow\\n')\nraise SystemExit(86)\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, PYTHONPATH=str(shadow))
    fn = _exec_lead_text()
    # $$ inside the subshell is the outer shell, which stays alive across exec.
    isolated = subprocess.run(
        ["bash", "-c", f'export A770B_PROJECT="{ROOT}"\nunset LOCAL_TIMEOUT\n{fn}\n( exec_lead true )\n'],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert isolated.returncode == 0, isolated.stderr
    assert "shadow" not in isolated.stderr
    assert "python3 -I -B" in fn

    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    leader_file = tmp_path / "leader"
    starter = f"""
export A770B_PROJECT="{ROOT}"
unset LOCAL_TIMEOUT
{fn}
(
  printf '%s\\n' "$BASHPID" > "{pidfile}"
  exec_lead bash -c 'sleep 60' sandbox_run.sh
) &
leader=$!
printf '%s\\n' "$leader" > "{leader_file}"
wait "$leader"
"""
    proc = subprocess.Popen(
        ["bash", "-c", starter], env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    leader = 0
    legacy = None
    legacy_pid = 0
    data = Path(env["A770B_DATA"])
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if leader_file.exists():
                text = leader_file.read_text(encoding="ascii").strip()
                if text.isdigit() and _alive(int(text)):
                    try:
                        argv = _argv(int(text))
                    except OSError:
                        argv = []
                    if (
                        len(argv) >= 5
                        and argv[1:3] == ["-I", "-B"]
                        and os.path.realpath(argv[3]) == os.path.realpath(RUN_LEAD)
                        and argv[4] == "--"
                        and "sandbox_run.sh" in argv
                    ):
                        leader = int(text)
                        break
            time.sleep(0.05)
        assert leader, "isolated leader did not publish -I -B"
        stopped = subprocess.run(
            ["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=40,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert f"ending run pid {leader}" in stopped.stdout
        proc.wait(timeout=10)
        err = proc.stderr.read() if proc.stderr else ""
        assert proc.returncode == 143, err
        assert not _alive(leader)

        legacy_env = dict(env)
        legacy_env.pop("PYTHONPATH", None)
        legacy_file = tmp_path / "legacy"
        legacy_script = f"""
(
  printf '%s\\n' "$BASHPID" > "{pidfile}"
  exec python3 "{RUN_LEAD}" -- bash -c 'sleep 60' sandbox_run.sh
) &
legacy=$!
printf '%s\\n' "$legacy" > "{legacy_file}"
wait "$legacy"
"""
        legacy = subprocess.Popen(
            ["bash", "-c", legacy_script], env=legacy_env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        legacy_pid = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if legacy_file.exists():
                text = legacy_file.read_text(encoding="ascii").strip()
                if text.isdigit() and _alive(int(text)):
                    argv = _argv(int(text))
                    if len(argv) >= 3 and argv[1:3] == [str(RUN_LEAD), "--"] and "-I" not in argv:
                        legacy_pid = int(text)
                        break
            time.sleep(0.05)
        assert legacy_pid, "legacy leader did not stay up"
        stopped = subprocess.run(
            ["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT, env=legacy_env,
            capture_output=True, text=True, timeout=40,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert f"ending run pid {legacy_pid}" in stopped.stdout
        legacy.wait(timeout=10)
        assert legacy.returncode == 143
        assert not _alive(legacy_pid)
        legacy = None
    finally:
        if leader:
            _end_named(leader)
        if legacy_pid:
            _end_named(legacy_pid)
        _end_worker_record(data)
        if proc.poll() is None:
            _end_named(proc.pid)
            proc.wait(timeout=5)
        if legacy is not None and legacy.poll() is None:
            _end_named(legacy.pid)
            _end_worker_record(data)
            legacy.wait(timeout=5)


def test_run_lead_pid_rejects_anything_but_the_two_forms(tmp_path):
    env = _env(tmp_path)
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    impostor_dir = tmp_path / "elsewhere"
    impostor_dir.mkdir()
    impostor = impostor_dir / "run_lead.py"
    impostor.write_bytes(RUN_LEAD.read_bytes())
    forms = {
        "only-I": ["-I", str(RUN_LEAD), "--"],
        "swapped": ["-B", "-I", str(RUN_LEAD), "--"],
        "extra": ["-I", "-B", "-s", str(RUN_LEAD), "--"],
        "impostor": ["-I", "-B", str(impostor), "--"],
    }
    for name, flags in forms.items():
        proc = subprocess.Popen(
            ["python3", *flags, "bash", "-c", "sleep 60", "sandbox_run.sh"],
            env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not _alive(proc.pid):
                time.sleep(0.05)
            assert _alive(proc.pid), name
            pidfile.write_text(f"{proc.pid}\n", encoding="ascii")
            stopped = subprocess.run(
                ["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=30,
            )
            assert stopped.returncode != 0, (name, stopped.stdout, stopped.stderr)
            assert "no run to stop" in stopped.stderr, (name, stopped.stderr)
            assert _alive(proc.pid), name
        finally:
            if proc.poll() is None:
                _end_named(proc.pid)
                _end_worker_record(Path(env["A770B_DATA"]))
                proc.wait(timeout=5)


def test_run_phase_grades_an_entered_command_once():
    text = LOCAL_BUILD.read_text(encoding="utf-8")
    start = text.index("def run_phase(")
    end = text.index("\ninv_override", start)
    body = text[start:end]
    assert body.count("sandbox_oom.grade(") == 1


def test_capture_comment_limits_a_missing_record_to_an_early_stop():
    text = CAPTURE.read_text(encoding="utf-8")
    start = text.index("## sandbox oom")
    window = text[start:start + 400]
    assert "early stop" in window
    assert "No file on stop-run" not in text


def test_reap_uses_the_saved_pid_after_the_parent_is_gone(tmp_path):
    child_file = tmp_path / "child"
    script = f'sleep 60 & printf "%s\\n" "$!" > "{child_file}.tmp" && mv -f "{child_file}.tmp" "{child_file}"; wait'
    proc = subprocess.Popen(
        ["bash", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    owned: list[int] = []
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_file.exists():
            if proc.poll() is not None:
                raise AssertionError("parent exited before the child pid was published")
            time.sleep(0.05)
        assert child_file.exists()
        child = int(child_file.read_text(encoding="ascii").strip())
        owned = [proc.pid, child]
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        assert _alive(child), "child died with the parent, so the saved identity was not needed"
        assert child not in _descendants(proc.pid)
        _reap_owned(proc, owned)
        assert _wait_gone([child], seconds=2) == []
    finally:
        _reap_owned(proc, owned)


def test_sleeper_identity_is_argv0_not_a_script_that_mentions_the_token(tmp_path):
    token = f"zzsleeper{tmp_path.name}"
    ready = tmp_path / "ready"
    # A following command keeps this shell alive, so its command line still
    # mentions the token while argv0 stays bash.
    decoy = json.dumps(f"sleep 30; echo {token}")
    real = json.dumps(f"exec -a {token} sleep 30")
    script = (
        f"bash -c {decoy} & bash -c {real} & "
        f'printf "%s\\n" ready > "{ready}.tmp" && mv -f "{ready}.tmp" "{ready}"; wait'
    )
    proc = subprocess.Popen(
        ["bash", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    owned: list[int] = []
    try:
        pid = _wait_sleeper(proc.pid, ready, token, proc)
        owned = _note_owned(proc.pid, [pid])
        assert pid != proc.pid
        assert _argv0(pid) == token.encode()
        assert token.encode() in _cmdline(proc.pid)
        assert _argv0(proc.pid) != token.encode()
        decoys = [item for item in owned if item != pid and token.encode() in _cmdline(item)]
        assert decoys, "the script that only mentions the token was not running"
    finally:
        _reap_owned(proc, owned)


def test_ready_publish_is_never_empty(tmp_path):
    body = _ready_publish('ready="${A770B_SLOW_READY:?}"')
    assert 'mv -f "$tmp" "$ready"' in body
    assert '> "$ready"' not in body
    ready = tmp_path / "ready"
    script = _ready_publish(f'ready="{ready}"') + "\nexit 0\n"
    seen_empty = False
    proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            text = ready.read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        if text == "":
            seen_empty = True
            break
    err = proc.stderr.read() if proc.stderr else ""
    assert proc.wait(timeout=5) == 0, err
    assert not seen_empty
    assert ready.read_text(encoding="ascii").strip().isdigit()


def test_wait_ready_rejects_an_empty_file(tmp_path):
    ready = tmp_path / "ready"
    ready.write_text("", encoding="ascii")
    proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with pytest.raises(AssertionError, match="did not publish a pid"):
            _wait_ready(ready, proc, seconds=0.3)
    finally:
        _reap_owned(proc, [proc.pid])


def test_arm_pid_gone_fails_when_the_process_is_still_alive():
    proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with pytest.raises(AssertionError, match="kept running"):
            _arm_pid_gone(proc.pid, seconds=0.2)
    finally:
        _reap_owned(proc, [proc.pid])


def _oom_mod(monkeypatch):
    sys.path.insert(0, str(ROOT / "harness"))
    import sandbox_oom
    monkeypatch.setattr(sandbox_oom, "_SETTLE_S", 0.3)
    monkeypatch.setattr(sandbox_oom, "_JOURNAL_S", 0.2)
    monkeypatch.setattr(sandbox_oom, "_TOOL_TIMEOUT_S", 0.4)
    return sandbox_oom


def _grade_bin(tmp_path, name, systemctl_body, journal_body="exit 1\n"):
    bin_ = tmp_path / name
    _exe(bin_ / "systemctl", systemctl_body)
    _exe(bin_ / "journalctl", journal_body)
    return bin_


def test_unsettled_oom_kill_is_yes_under_kill_policy(tmp_path, monkeypatch):
    """Result=oom-kill is yes while the scope is still deactivating.

    Any other unsettled result stays unknown and does not read the journal.
    """
    oom = _oom_mod(monkeypatch)
    journal = tmp_path / "journal-called"
    bin_ = _grade_bin(
        tmp_path, "unset-bin",
        "printf '%s\\n' 'ActiveState=deactivating' 'Result=oom-kill'\n",
        f"printf 1 > '{journal}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "yes"
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not journal.exists()

    journal_other = tmp_path / "journal-other"
    bin_other = _grade_bin(
        tmp_path, "other-bin",
        "printf '%s\\n' 'ActiveState=deactivating' 'Result=success'\n",
        f"printf 1 > '{journal_other}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", f"{bin_other}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-8.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "unknown"
    assert not journal_other.exists()

    journal_continue = tmp_path / "journal-continue"
    bin_continue = _grade_bin(
        tmp_path, "continue-bin",
        "printf '%s\\n' 'ActiveState=deactivating' 'Result=oom-kill'\n",
        f"printf 1 > '{journal_continue}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", f"{bin_continue}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-9.scope",
        "policy": "continue", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "unknown"
    assert not journal_continue.exists()


def test_cleanup_refuses_a_unit_the_sandbox_did_not_generate(tmp_path, monkeypatch, capsys):
    oom = _oom_mod(monkeypatch)
    log = tmp_path / "ctl.log"
    bin_ = _grade_bin(tmp_path, "refuse-bin", f"""
printf '%s\\n' "$*" >> "{log}"
printf '%s\\n' 'ActiveState=active' 'Result=success'
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    for unit in ("ssh.service", "a770b-sbx-42-7.service", "a770b-sbx-stuck.scope", "a770b-test-not.scope"):
        log.write_text("", encoding="utf-8")
        record.write_text(json.dumps({
            "isolation": "scope", "unit": unit, "policy": "kill", "oom": "unknown",
        }), encoding="utf-8")
        oom.update(str(record), 0)
        text = log.read_text(encoding="utf-8")
        assert " stop " not in f" {text} ", (unit, text)
        assert "reset-failed" not in text, (unit, text)
    err = capsys.readouterr().err
    assert "refusing to stop or reset ssh.service" in err
    assert "refusing to stop or reset a770b-sbx-42-7.service" in err


def test_opt_in_test_scope_is_still_stopped(tmp_path, monkeypatch, capsys):
    oom = _oom_mod(monkeypatch)
    log = tmp_path / "ctl.log"
    bin_ = _grade_bin(tmp_path, "optin-bin", f"""
printf '%s\\n' "$*" >> "{log}"
printf '%s\\n' 'ActiveState=active' 'Result=success'
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-test-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    oom.update(str(record), 0)
    text = log.read_text(encoding="utf-8")
    assert " stop " in f" {text} ", text
    assert "reset-failed" in text, text
    assert "refusing" not in capsys.readouterr().err


def test_failed_show_still_stops_an_owned_scope(tmp_path, monkeypatch):
    oom = _oom_mod(monkeypatch)
    log = tmp_path / "ctl.log"
    bin_ = _grade_bin(tmp_path, "show-bin", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*) exit 1 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    oom.update(str(record), 1)
    text = log.read_text(encoding="utf-8")
    assert " stop " in f" {text} ", text


def test_stop_failure_and_timeout_are_reported(tmp_path, monkeypatch, capsys):
    oom = _oom_mod(monkeypatch)
    bin_ = _grade_bin(tmp_path, "fail-bin", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=active' 'Result=success' ;;
  *" stop "*) printf '%s\\n' 'stop refused' >&2; exit 1 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    oom.update(str(record), 0)
    err = capsys.readouterr().err
    assert "stop of a770b-sbx-42-7.scope failed: stop refused" in err

    bin_reset = _grade_bin(tmp_path, "reset-bin", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=failed' 'Result=success' ;;
  *" reset-failed "*) printf '%s\\n' 'reset refused' >&2; exit 1 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_reset}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-8.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    oom.update(str(record), 0)
    err = capsys.readouterr().err
    assert "reset-failed of a770b-sbx-42-8.scope failed: reset refused" in err

    bin_hang = _grade_bin(tmp_path, "hang-bin", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=deactivating' 'Result=success' ;;
  *" stop "*) exec /usr/bin/sleep 30 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_hang}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-9.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    started = time.monotonic()
    oom.update(str(record), 1)
    assert time.monotonic() - started < 2.0
    err = capsys.readouterr().err
    assert "stop of a770b-sbx-42-9.scope timed out" in err


def test_settled_inactive_scope_is_not_reset(tmp_path, monkeypatch, capsys):
    """A clean inactive scope is already unloaded. reset-failed is not run."""
    oom = _oom_mod(monkeypatch)
    log = tmp_path / "ctl.log"
    bin_ = _grade_bin(tmp_path, "inactive-bin", f"""
printf '%s\\n' "$*" >> "{log}"
printf '%s\\n' 'ActiveState=inactive' 'Result=success'
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "no"
    text = log.read_text(encoding="utf-8")
    assert " show " in f" {text} ", text
    assert "reset-failed" not in text
    assert "reset-failed" not in capsys.readouterr().err


def test_not_loaded_reply_is_not_a_cleanup_failure(tmp_path, monkeypatch, capsys):
    """systemctl saying the unit is not loaded is an unloaded scope, not a refusal."""
    oom = _oom_mod(monkeypatch)
    bin_ = _grade_bin(tmp_path, "gone-bin", """
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=failed' 'Result=success' ;;
  *" reset-failed "*) printf '%s\\n' 'Unit a770b-sbx-42-7.scope not loaded.' >&2; exit 1 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "no"
    err = capsys.readouterr().err
    assert "reset-failed of" not in err
    assert "not loaded" not in err

    bin_stop = _grade_bin(tmp_path, "gone-stop", """
case " $* " in
  *" show "*) exit 1 ;;
  *" stop "*) printf '%s\\n' 'Unit a770b-sbx-42-8.scope not loaded.' >&2; exit 1 ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_stop}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-8.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    oom.update(str(record), 1)
    err = capsys.readouterr().err
    assert "stop of" not in err
    assert "not loaded" not in err


def test_observed_oom_kill_is_written_before_cleanup(tmp_path, monkeypatch):
    """A later show must not drop Result=oom-kill, and the record is written first."""
    oom = _oom_mod(monkeypatch)
    journal = tmp_path / "journal-called"
    order = tmp_path / "order"
    record = tmp_path / "rec.json"
    count = tmp_path / "shows"
    bin_ = _grade_bin(tmp_path, "flip-bin", f"""
case " $* " in
  *" show "*)
    n=0
    [ -f "{count}" ] && n=$(cat "{count}")
    n=$((n + 1))
    printf '%s' "$n" > "{count}"
    if [ "$n" -eq 1 ]; then
      printf '%s\\n' 'ActiveState=deactivating' 'Result=oom-kill'
    else
      exit 1
    fi
    ;;
  *" stop "*|*" reset-failed "*)
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("oom",""))' "{record}" > "{order}"
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", f"printf 1 > '{journal}'\nexit 0\n")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "yes"
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not journal.exists()
    assert order.read_text(encoding="utf-8").strip() == "yes"

    count.unlink()
    order.unlink()
    changed = tmp_path / "changed"
    bin_other = _grade_bin(tmp_path, "other-result", f"""
case " $* " in
  *" show "*)
    n=0
    [ -f "{count}" ] && n=$(cat "{count}")
    n=$((n + 1))
    printf '%s' "$n" > "{count}"
    if [ "$n" -eq 1 ]; then
      printf '%s\\n' 'ActiveState=deactivating' 'Result=oom-kill'
    else
      printf '%s\\n' 'ActiveState=inactive' 'Result=success'
    fi
    ;;
  *" reset-failed "*) printf 1 > "{changed}" ;;
esac
exit 0
""")
    _exe(bin_other / "journalctl", f"printf 1 > '{journal}'\nexit 0\n")
    monkeypatch.setenv("PATH", f"{bin_other}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-8.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.update(str(record), 0) == "yes"
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not journal.exists()
    assert not changed.exists()


def test_cleanup_still_runs_when_decide_or_write_raises(tmp_path, monkeypatch):
    """Stop runs after a decision that raises, and after a write that raises."""
    oom = _oom_mod(monkeypatch)
    decided = tmp_path / "decided"
    log = tmp_path / "ctl.log"
    early = tmp_path / "early"

    def explode(*_args):
        decided.write_text("yes", encoding="ascii")
        raise RuntimeError("decide failed")

    monkeypatch.setattr(oom, "_decide", explode)
    bin_ = _grade_bin(tmp_path, "raise-bin", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=active' 'Result=success' ;;
  *" stop "*)
    if [ ! -f "{decided}" ]; then printf '%s\\n' early > "{early}"; fi
    ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-7.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="decide failed"):
        oom.update(str(record), 0)
    assert " stop " in f" {log.read_text(encoding='utf-8')} "
    assert not early.exists()

    wrote = tmp_path / "wrote"
    log.write_text("", encoding="utf-8")
    early.unlink(missing_ok=True)
    monkeypatch.undo()
    oom = _oom_mod(monkeypatch)

    def fail_write(_path, body):
        wrote.write_text(body["oom"], encoding="ascii")
        raise OSError("disk")

    monkeypatch.setattr(oom, "_write_record", fail_write)
    bin_write = _grade_bin(tmp_path, "write-bin", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*) printf '%s\\n' 'ActiveState=active' 'Result=oom-kill' ;;
  *" stop "*)
    if [ ! -f "{wrote}" ]; then printf '%s\\n' early > "{early}"; fi
    ;;
esac
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_write}:{os.environ['PATH']}")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-8.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    with pytest.raises(OSError, match="disk"):
        oom.update(str(record), 0)
    assert wrote.read_text(encoding="ascii") == "yes"
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"
    assert " stop " in f" {log.read_text(encoding='utf-8')} "
    assert not early.exists()
    assert oom.main(["sandbox_oom.py", str(record), "0"]) == 1


def test_cleanup_runs_when_the_settle_read_raises(tmp_path, monkeypatch):
    """A settle that raises still stops the scope. It used to skip the finally."""
    oom = _oom_mod(monkeypatch)
    log = tmp_path / "ctl.log"

    def explode(_unit):
        raise RuntimeError("settle failed")

    monkeypatch.setattr(oom, "_wait_settled", explode)
    bin_ = _grade_bin(tmp_path, "settle-bin", f"""
printf '%s\\n' "$*" >> "{log}"
exit 0
""")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-42-9.scope",
        "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="settle failed"):
        oom.update(str(record), 0)
    assert " stop " in f" {log.read_text(encoding='utf-8')} "


def test_unpublished_oom_yes_is_an_infrastructure_error(tmp_path):
    """A positive OOM decision whose record write fails does not grade as pass."""
    marker = tmp_path / "ran"
    env, patch, seat, results = _verify_fixture(
        tmp_path, "publish", f"printf ran > '{marker}'",
    )
    _confine_harness_outputs(env, tmp_path)
    logs = Path(env["A770B_DATA"]) / "logs"
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" show "*)
    chmod 444 "{logs}"/verify-oom-*.json 2>/dev/null || true
    printf '%s\\n' 'ActiveState=inactive' 'Result=oom-kill'
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True, check=False,
    )
    try:
        assert marker.read_text(encoding="utf-8") == "ran", proc.stderr
        doc = json.loads((results / "publish.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", doc
        assert doc["public"] != "pass", doc
        assert doc.get("reason") != "oom", doc
        assert proc.returncode == 2, proc.returncode
        assert doc["exit_code"] == 2, doc
        assert "could not publish the record" in proc.stderr, proc.stderr
        report = (results / "publish.verify.md").read_text(encoding="utf-8")
        assert "the oom record was not published" in report, report
        assert "the exit code of the command is the verdict" not in report
    finally:
        _stop_log_scopes(logs)


def test_unusable_oom_record_exits_nonzero(tmp_path, monkeypatch):
    """A missing, malformed, or non-object record is not unknown with exit 0.

    A record the helper itself writes as unknown (isolation=none) still is.
    """
    oom = _oom_mod(monkeypatch)
    missing = tmp_path / "missing.json"
    assert oom.main(["sandbox_oom.py", str(missing), "0"]) == 1
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert oom.main(["sandbox_oom.py", str(malformed), "0"]) == 1
    listed = tmp_path / "list.json"
    listed.write_text("[]\n", encoding="utf-8")
    assert oom.main(["sandbox_oom.py", str(listed), "0"]) == 1
    none = tmp_path / "none.json"
    none.write_text(json.dumps({
        "isolation": "none", "unit": None, "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    assert oom.main(["sandbox_oom.py", str(none), "0"]) == 0
    assert json.loads(none.read_text(encoding="utf-8"))["oom"] == "unknown"


def test_stale_oom_yes_is_not_reported_when_publication_fails(tmp_path):
    """A helper that cannot rewrite the record must not leave its old yes."""
    env, patch, seat, results = _verify_fixture(tmp_path, "stale", "true")
    _confine_harness_outputs(env, tmp_path)
    logs = Path(env["A770B_DATA"]) / "logs"
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" show "*)
    for f in "{logs}"/verify-oom-*.json; do
      [ -f "$f" ] || continue
      printf '%s\\n' '{{"oom":"yes"}}' > "$f"
      chmod 444 "$f"
    done
    exit 1
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True, check=False,
    )
    try:
        doc = json.loads((results / "stale.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", doc
        assert doc["public"] != "pass", doc
        assert doc["oom"]["public"] == "unknown", doc
        assert doc.get("reason") != "oom", doc
        assert proc.returncode == 2, proc.returncode
    finally:
        _stop_log_scopes(logs)


def test_read_back_of_a_non_object_record_is_not_a_pass(tmp_path):
    """A helper that exits 0 still fails verify when the record is no longer an object."""
    env, patch, seat, results = _verify_fixture(tmp_path, "reread", "true")
    _confine_harness_outputs(env, tmp_path)
    logs = Path(env["A770B_DATA"]) / "logs"
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" show "*) exit 1 ;;
  *" stop "*|*" reset-failed "*)
    for f in "{logs}"/verify-oom-*.json; do
      [ -e "$f" ] || continue
      printf '%s\\n' '[]' > "$f"
    done
    exit 0
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True, check=False,
    )
    try:
        doc = json.loads((results / "reread.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", (doc, proc.stderr)
        assert doc["public"] != "pass", doc
        assert doc["oom"]["public"] == "unknown", doc
        assert proc.returncode == 2, proc.returncode
        report = (results / "reread.verify.md").read_text(encoding="utf-8")
        assert "the oom record was not published" in report, report
    finally:
        _stop_log_scopes(logs)


def test_verify_shows_a_failed_stop_on_its_stderr(tmp_path):
    """The helper's stop refusal reaches verify. Its stdout stays discarded."""
    env, patch, seat, results = _verify_fixture(tmp_path, "stop-err", "true")
    _confine_harness_outputs(env, tmp_path)
    bin_ = tmp_path / "bin"
    _scope_exec_stub(bin_ / "systemd-run")
    fd2 = tmp_path / "helper-fd2"
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" show "*) exit 1 ;;
  *" stop "*)
    readlink /proc/$PPID/fd/2 > "{fd2}" || true
    printf '%s\\n' 'stop refused' >&2
    exit 1
    ;;
esac
exit 0
""")
    _exe(bin_ / "journalctl", "exit 1\n")
    logs = Path(env["A770B_DATA"]) / "logs"
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True, check=False,
    )
    try:
        assert "sandbox oom: stop of a770b-sbx-" in proc.stderr
        assert "failed: stop refused" in proc.stderr
        assert "stop refused" not in proc.stdout
        helper_err = fd2.read_text(encoding="utf-8").strip()
        assert helper_err != "/dev/null", helper_err
        doc = json.loads((results / "stop-err.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "pass", (doc, proc.stderr)
    finally:
        _stop_log_scopes(logs)


def _end_session(proc: subprocess.Popen, pids: list[int]) -> None:
    """Kill this test's session and the pids its stubs published. No scan."""
    for pid in pids:
        if pid > 0 and _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _published_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def test_probe_and_reset_are_bounded_and_the_model_command_is_not(tmp_path):
    """The throwaway probe and its reset-failed return. The model command does not sit under timeout."""
    if shutil.which("bwrap") is None or shutil.which("setpriv") is None:
        pytest.skip("bwrap and setpriv are required; this test does not stub them")
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    probe_parent = tmp_path / "probe.parent"
    probe_pid = tmp_path / "probe.pid"
    _exe(bin_ / "systemd-run", f"""
ps -o comm= -p "$PPID" | tr -d '[:space:]' > "{probe_parent}"
printf '%s\\n' "$$" > "{probe_pid}"
exec /usr/bin/sleep 30
""")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    err = tmp_path / "probe.err"
    proc = subprocess.Popen(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "true"],
        env=env, stdout=subprocess.DEVNULL, stderr=err.open("w"),
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        _end_session(proc, [_published_pid(probe_pid)])
    assert probe_parent.read_text(encoding="ascii").strip() == "timeout", probe_parent.read_text(encoding="ascii")
    assert not timed_out, "systemd-run probe was not bounded"
    assert not _alive(_published_pid(probe_pid))
    assert proc.returncode == 0, err.read_text(encoding="utf-8", errors="replace")
    assert "isolation=none" in err.read_text(encoding="utf-8", errors="replace")

    reset_parent = tmp_path / "reset.parent"
    reset_pid = tmp_path / "reset.pid"
    model_parent = tmp_path / "model.parent"
    _exe(bin_ / "systemd-run", f"""
case " $* " in
  *" --collect "*)
    ps -o comm= -p "$PPID" | tr -d '[:space:]' > "{probe_parent}"
    exit 0
    ;;
esac
ps -o comm= -p "$PPID" | tr -d '[:space:]' > "{model_parent}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 1
""")
    _exe(bin_ / "systemctl", f"""
case " $* " in
  *" reset-failed "*)
    ps -o comm= -p "$PPID" | tr -d '[:space:]' > "{reset_parent}"
    printf '%s\\n' "$$" > "{reset_pid}"
    exec /usr/bin/sleep 30
    ;;
esac
exit 0
""")
    err2 = tmp_path / "reset.err"
    proc = subprocess.Popen(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "true"],
        env=env, stdout=subprocess.DEVNULL, stderr=err2.open("w"),
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        _end_session(proc, [_published_pid(reset_pid)])
    assert probe_parent.read_text(encoding="ascii").strip() == "timeout"
    assert not timed_out, "reset-failed of the probe was not bounded"
    assert reset_parent.read_text(encoding="ascii").strip() == "timeout"
    assert not _alive(_published_pid(reset_pid))
    model = model_parent.read_text(encoding="ascii").strip()
    assert model and model != "timeout", model
    assert proc.returncode == 0, err2.read_text(encoding="utf-8", errors="replace")


def test_stop_server_tears_down_when_the_clear_fails(tmp_path):
    """A refused clear still stops the owned server, and the flag is set after that stop."""
    bin_ = _tools(tmp_path)
    job = tmp_path / "job-clear"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    (job / "cid").write_text("abc123\n", encoding="ascii")
    os.symlink(secret, job / "evidence" / "serving.pid")
    during = tmp_path / "during"
    got = tmp_path / "stopped"
    prefix = QUALIFY.read_text(encoding="utf-8").split("[ $# -ge 1 ] || usage", 1)[0]
    prefix = prefix.replace(
        'here=$(cd "$(dirname "$0")" && pwd)',
        f'here="{ROOT / "harness"}"',
        1,
    )
    funcs = tmp_path / "qualify-funcs.sh"
    funcs.write_text(prefix, encoding="utf-8")
    script = f"""
set +e
. "{funcs}"
a770b-qualify-stop() {{
  if [ ! -f "{during}" ]; then
    printf '%s\\n' "${{SERVER_STOPPED:-unset}}" > "{during}"
  fi
  printf '%s\\n' "$1" >> "{got}"
}}
export PATH="{bin_}:{os.environ['PATH']}"
job="{job}"
unset TRACKER SERVER_STOPPED
stop_server
printf 'OUT RC=%s\\n' "$?"
printf 'OUT FLAG=%s\\n' "${{SERVER_STOPPED:-unset}}"
stop_server
printf 'OUT RC2=%s\\n' "$?"
printf 'OUT SECRET=%s\\n' "$(cat "{secret}")"
"""
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    assert got.is_file(), proc.stdout + proc.stderr
    stopped = [line for line in got.read_text(encoding="ascii").splitlines() if line]
    assert stopped == ["abc123"], proc.stdout + proc.stderr
    assert during.read_text(encoding="ascii").strip() != "1", during.read_text(encoding="ascii")
    assert fields.get("RC") == "1", fields
    assert fields.get("FLAG") == "1", fields
    assert fields.get("RC2") == "0", fields
    assert fields.get("SECRET") == "sentinel", fields


def test_stop_server_tears_down_when_the_first_clear_fails(tmp_path):
    """A refused SERVING_CLEARED still stops the owned server and returns failure."""
    bin_ = _tools(tmp_path)
    job = tmp_path / "job-clear-first"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    (job / "cid").write_text("abc123\n", encoding="ascii")
    (job / "evidence" / "serving.pid").write_text("999\n", encoding="ascii")
    os.symlink(secret, job / "SERVING_CLEARED")
    during = tmp_path / "during"
    got = tmp_path / "stopped"
    funcs = _qualify_funcs(tmp_path)
    script = f"""
set +e
. "{funcs}"
a770b-qualify-stop() {{
  if [ ! -f "{during}" ]; then
    printf '%s\\n' "${{SERVER_STOPPED:-unset}}" > "{during}"
  fi
  printf '%s\\n' "$1" >> "{got}"
}}
export PATH="{bin_}:{os.environ['PATH']}"
job="{job}"
unset TRACKER SERVER_STOPPED
stop_server
printf 'OUT RC=%s\\n' "$?"
printf 'OUT FLAG=%s\\n' "${{SERVER_STOPPED:-unset}}"
printf 'OUT PID=%s\\n' "$(tr -d '[:space:]' < "$job/evidence/serving.pid")"
stop_server
printf 'OUT RC2=%s\\n' "$?"
printf 'OUT SECRET=%s\\n' "$(cat "{secret}")"
"""
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    assert got.is_file(), proc.stdout + proc.stderr
    stopped = [line for line in got.read_text(encoding="ascii").splitlines() if line]
    assert stopped == ["abc123"], proc.stdout + proc.stderr
    assert during.read_text(encoding="ascii").strip() != "1", during.read_text(encoding="ascii")
    assert fields.get("RC") == "1", fields
    assert fields.get("FLAG") == "1", fields
    assert fields.get("PID") == "", fields
    assert fields.get("RC2") == "0", fields
    assert fields.get("SECRET") == "sentinel", fields


def test_stop_server_stops_when_the_log_is_a_symlink(tmp_path):
    """A refused stop log does not skip the owned server, and the stop returns failure."""
    bin_ = _tools(tmp_path)
    job = tmp_path / "job-log"
    (job / "evidence").mkdir(parents=True)
    (job / "phases").mkdir()
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    (job / "cid").write_text("abc123\n", encoding="ascii")
    os.symlink(secret, job / "phases" / "stop-server.log")
    os.symlink(secret, job / "evidence" / "argv.log")
    got = tmp_path / "stopped"
    funcs = _qualify_funcs(tmp_path)
    script = f"""
set +e
. "{funcs}"
a770b-qualify-stop() {{ printf '%s\\n' "$1" >> "{got}"; }}
export PATH="{bin_}:{os.environ['PATH']}"
job="{job}"
unset TRACKER SERVER_STOPPED
stop_server
printf 'OUT RC=%s\\n' "$?"
printf 'OUT FLAG=%s\\n' "${{SERVER_STOPPED:-unset}}"
stop_server
printf 'OUT RC2=%s\\n' "$?"
printf 'OUT SECRET=%s\\n' "$(cat "{secret}")"
"""
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    assert got.is_file(), proc.stdout + proc.stderr
    stopped = [line for line in got.read_text(encoding="ascii").splitlines() if line]
    assert stopped == ["abc123"], proc.stdout + proc.stderr
    assert fields.get("RC") == "1", fields
    assert fields.get("FLAG") == "1", fields
    assert fields.get("RC2") == "0", fields
    assert fields.get("SECRET") == "sentinel", fields


def test_plain_trunc_returns_failure_when_the_write_fails(tmp_path):
    """The truncation's own status is the function status. chmod does not hide a failed write."""
    job = tmp_path / "job-trunc"
    (job / "evidence").mkdir(parents=True)
    target = job / "evidence" / "serving.pid"
    target.mkdir()
    good = job / "evidence" / "argv.log"
    good.write_text("keep\n", encoding="utf-8")
    funcs = _qualify_funcs(tmp_path)
    script = f"""
set +e
. "{funcs}"
plain_trunc "{target}"
printf 'OUT DIR=%s\\n' "$?"
plain_trunc "{good}"
printf 'OUT FILE=%s\\n' "$?"
"""
    env = _env(tmp_path)
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = {}
    for line in proc.stdout.splitlines():
        if line.startswith("OUT ") and "=" in line:
            key, value = line[4:].split("=", 1)
            fields[key] = value
    assert fields.get("DIR") == "1", fields
    assert fields.get("FILE") == "0", fields
    assert target.is_dir()
    assert good.read_text(encoding="utf-8") == ""


def _suite_publish_text() -> str:
    source = RUN_SUITE.read_text(encoding="utf-8")
    start = source.index("suite_publish_serving_pid(){")
    end = source.index("\n}\n", start)
    return source[start:end + 2]


def test_suite_publish_does_not_write_through_a_predictable_temp(tmp_path):
    """Both published paths use an exclusive temp. A planted <path>.tmp.$$ symlink is not the write."""
    host = tmp_path / "host" / "serving.pid"
    qual = tmp_path / "qual" / "serving.pid"
    host.parent.mkdir()
    qual.parent.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("sentinel\n", encoding="utf-8")
    fn = _suite_publish_text()
    script = f"""
set +e
{fn}
HOST_PID_FILE="{host}"
export A770B_QUALIFY_SERVING_PID_FILE="{qual}"
ln -s "{secret}" "${{HOST_PID_FILE}}.tmp.$$"
ln -s "{secret}" "${{A770B_QUALIFY_SERVING_PID_FILE}}.tmp.$$"
suite_publish_serving_pid 4242
printf 'OUT RC=%s\\n' "$?"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OUT RC=0" in proc.stdout, proc.stdout + proc.stderr
    assert secret.read_text(encoding="utf-8") == "sentinel\n"
    assert host.read_text(encoding="ascii") == "4242\n"
    assert qual.read_text(encoding="ascii") == "4242\n"
    assert host.is_file() and not host.is_symlink()
    assert qual.is_file() and not qual.is_symlink()


def test_suite_publish_returns_failure_when_the_write_fails(tmp_path):
    """A temp that cannot be written is not renamed over either published path."""
    host_dir = tmp_path / "host"
    qual_dir = tmp_path / "qual"
    host_dir.mkdir()
    qual_dir.mkdir()
    host = host_dir / "serving.pid"
    qual = qual_dir / "serving.pid"
    host.write_text("old\n", encoding="ascii")
    qual.write_text("oldq\n", encoding="ascii")
    bin_ = tmp_path / "bin"
    _exe(bin_ / "mktemp", f"""
out=$(/usr/bin/mktemp "$@") || exit $?
case "$out" in
  {qual_dir}/*) chmod 000 "$out" || exit 1 ;;
esac
printf '%s\\n' "$out"
""")
    fn = _suite_publish_text()
    script = f"""
set +e
{fn}
HOST_PID_FILE="{host}"
export A770B_QUALIFY_SERVING_PID_FILE="{qual}"
suite_publish_serving_pid 4242
printf 'OUT RC=%s\\n' "$?"
"""
    env = os.environ.copy()
    env["PATH"] = f"{bin_}:{env.get('PATH', '/usr/bin:/bin')}"
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OUT RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert host.read_text(encoding="ascii") == "4242\n"
    assert qual.read_text(encoding="ascii") == "oldq\n"


def test_suite_publish_updates_the_second_path_when_the_first_write_fails(tmp_path):
    """A failed host publish still updates or clears the qualify path, and the call fails."""
    host_dir = tmp_path / "host"
    qual_dir = tmp_path / "qual"
    host_dir.mkdir()
    qual_dir.mkdir()
    host = host_dir / "serving.pid"
    qual = qual_dir / "serving.pid"
    host.write_text("old\n", encoding="ascii")
    qual.write_text("oldq\n", encoding="ascii")
    bin_ = tmp_path / "bin"
    _exe(bin_ / "mktemp", f"""
out=$(/usr/bin/mktemp "$@") || exit $?
case "$out" in
  {host_dir}/*) chmod 000 "$out" || exit 1 ;;
esac
printf '%s\\n' "$out"
""")
    fn = _suite_publish_text()
    script = f"""
set +e
{fn}
HOST_PID_FILE="{host}"
export A770B_QUALIFY_SERVING_PID_FILE="{qual}"
suite_publish_serving_pid 4242
printf 'OUT RC=%s\\n' "$?"
printf 'OUT QUAL=%s\\n' "$(cat -- "{qual}")"
suite_publish_serving_pid ""
printf 'OUT CLEAR=%s\\n' "$?"
"""
    env = os.environ.copy()
    env["PATH"] = f"{bin_}:{env.get('PATH', '/usr/bin:/bin')}"
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OUT RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert "OUT QUAL=4242" in proc.stdout, proc.stdout + proc.stderr
    assert host.read_text(encoding="ascii") == "old\n"
    assert "OUT CLEAR=1" in proc.stdout, proc.stdout + proc.stderr
    assert qual.read_text(encoding="ascii") == ""
    assert host.read_text(encoding="ascii") == "old\n"


def test_suite_publish_refuses_a_directory_target(tmp_path):
    """mv -T leaves a directory at either published path in place and returns failure."""
    host = tmp_path / "host.pid"
    qual = tmp_path / "qual.pid"
    host.mkdir()
    fn = _suite_publish_text()
    first = f"""
set +e
{fn}
HOST_PID_FILE="{host}"
unset A770B_QUALIFY_SERVING_PID_FILE
suite_publish_serving_pid 4242
printf 'OUT RC=%s\\n' "$?"
"""
    proc = subprocess.run(["bash", "-c", first], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OUT RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert host.is_dir()
    assert list(host.iterdir()) == []
    host_file = tmp_path / "host-file.pid"
    qual.mkdir()
    second = f"""
set +e
{fn}
HOST_PID_FILE="{host_file}"
export A770B_QUALIFY_SERVING_PID_FILE="{qual}"
suite_publish_serving_pid 4242
printf 'OUT RC=%s\\n' "$?"
"""
    proc = subprocess.run(["bash", "-c", second], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OUT RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert host_file.read_text(encoding="ascii") == "4242\n"
    assert qual.is_dir()
    assert list(qual.iterdir()) == []
