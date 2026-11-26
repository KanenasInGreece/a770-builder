#!/usr/bin/env python3
"""Round A containment. Each test fails when its invariant is broken.

OOM runs use a test-only MemoryMax on an a770b-test-* unit. They skip when
the user systemd bus is absent. Nothing here writes the workstation log tree.
"""

import json
import os
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
    env = dict(os.environ)
    for key in ("A770B_CARD_LOCK_PATH", "A770B_SEAT_LOCK_PATH", "A770B_ATTACHED", "A770B_SERVED_ENGINE"):
        env.pop(key, None)
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


def _scope_names() -> set[str]:
    proc = subprocess.run(
        ["systemctl", "--user", "list-units", "a770b-sbx-*", "--all",
         "--no-legend", "--plain", "--no-pager"],
        capture_output=True, text=True,
    )
    names = set()
    if proc.returncode != 0:
        return names
    for line in proc.stdout.splitlines():
        part = line.split()
        if part and part[0].startswith("a770b-sbx-"):
            names.add(part[0])
    return names


def _reset_scopes(names) -> None:
    for unit in names:
        subprocess.run(["systemctl", "--user", "stop", "--", unit], capture_output=True)
        subprocess.run(["systemctl", "--user", "reset-failed", "--", unit], capture_output=True)


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
    before = _scope_names()
    try:
        cases = (
            (False, {}),
            (True, {"A770B_SERVED_ENGINE": "strata"}),
        )
        for entered, extra in cases:
            ready = seat / f"ready-{int(entered)}"
            ready.unlink(missing_ok=True)
            entered_file = tmp_path / f"entered-{int(entered)}"
            record = tmp_path / f"i2-{int(entered)}.json"
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
            proc = subprocess.Popen(
                ["bash", "-c", script], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not ready.exists():
                    if proc.poll() is not None:
                        err = proc.stderr.read() if proc.stderr else ""
                        raise AssertionError(f"caller exited {proc.returncode} before ready: {err}")
                    time.sleep(0.1)
                assert ready.exists(), "sandbox command did not start"
                if entered:
                    file_deadline = time.monotonic() + 5
                    while time.monotonic() < file_deadline and not entered_file.exists():
                        time.sleep(0.05)
                    assert entered_file.read_text(encoding="utf-8").strip() == "entered"
                body = json.loads(record.read_text(encoding="utf-8"))
                if _bus():
                    assert body["isolation"] == "scope", body
                else:
                    assert body["isolation"] == "none", body
                desc = _descendants(proc.pid)
                assert any(b"sandbox_run.sh" in _cmdline(pid) for pid in desc), "sandbox_run.sh was not a child of the caller"
                if extra.get("A770B_SERVED_ENGINE") == "strata":
                    assert any(b"usage_tap.py" in _cmdline(pid) for pid in desc), "usage_tap bridge was not started"
                else:
                    assert any(b"socat" in _cmdline(pid) for pid in desc), "socat bridge was not started"
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                left = _wait_gone(desc)
                assert left == [], f"sandbox processes survived the caller: {left}"
            finally:
                if proc.poll() is None:
                    os.kill(proc.pid, signal.SIGKILL)
                _kill_ours(_descendants(proc.pid))
                proc.wait(timeout=5)
    finally:
        _reset_scopes(_scope_names() - before)


def test_i3_stop_signal_and_verify_timeout_end_the_run(tmp_path):
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    ready = seat / "lead-ready"
    token = f"i3lead{tmp_path.name}"
    command = (
        f'bash -c "exec -a {token} sleep 120" & echo ready > "{ready}"; wait'
    )
    # A parent that waits reaps the leader. stop-run treats an unreaped zombie as still alive.
    exit_file = tmp_path / "lead.exit"
    leader_file = tmp_path / "leader.pid"
    script = (
        f'python3 "{RUN_LEAD}" -- bash "{SANDBOX}" "{seat}" "{cfg}" -- bash -c {json.dumps(command)} &\n'
        f'echo $! > "{leader_file}"\n'
        "wait $!\n"
        f'echo $? > "{exit_file}"\n'
    )
    proc = subprocess.Popen(
        ["bash", "-c", script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not ready.exists():
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read() if proc.stderr else "leader exited")
            time.sleep(0.1)
        assert ready.exists()
        leader = int(leader_file.read_text(encoding="ascii").strip())
        desc = _descendants(leader)
        assert any(token.encode() in _cmdline(pid) for pid in desc), "grandchild was not alive to be stopped"
        (Path(env["A770B_DATA"]) / "logs" / "run.pid").write_text(f"{leader}\n", encoding="ascii")
        stop = subprocess.run(
            ["bash", str(LOCAL_BUILD), "stop-run"],
            env=env, capture_output=True, text=True, timeout=40,
        )
        proc.wait(timeout=15)
        rc = int(exit_file.read_text(encoding="ascii").strip())
        assert stop.returncode == 0, stop.stderr
        assert rc == 143, (rc, stop.stdout, stop.stderr)
        left = _wait_gone(desc)
        assert left == [], left
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
        _kill_ours(_descendants(proc.pid))
        proc.wait(timeout=5)
    ready.unlink(missing_ok=True)

    token = f"i3v{tmp_path.name}"
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "i3.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+seat changed\n",
        encoding="utf-8",
    )
    (results / "i3.spec.json").write_text(json.dumps({
        "verify": {"test": f"bash -c 'exec -a {token} sleep 120' & echo ready > '{seat}/vready'; wait"},
    }), encoding="utf-8")
    vready = seat / "vready"
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "8"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and verify.poll() is None and not vready.exists():
            time.sleep(0.1)
        rc = verify.wait(timeout=20)
        out = verify.stdout.read() if verify.stdout else ""
        assert rc == 124, out
        doc = json.loads((results / "i3.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "timeout"
        left = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
            except OSError:
                continue
            if token.encode() in cmd:
                left.append(pid)
        assert left == [], left
    finally:
        if verify.poll() is None:
            os.kill(verify.pid, signal.SIGKILL)
            verify.wait(timeout=5)
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            except OSError:
                continue
            if token.encode() in cmd:
                _kill_ours([pid])


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
    expected = "a b$HOME$$q'u"
    argv = ["bash", str(SANDBOX), str(seat), str(cfg), "--", "printf", "%s", "a b", "$HOME", "$$", "q'u"]
    before = _scope_names()
    try:
        proc = subprocess.run(argv, env=env, cwd=seat, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == expected, (proc.stdout, proc.stderr)
        entered = tmp_path / "entered"
        record = tmp_path / "i5.json"
        proc = subprocess.run(
            argv,
            env=dict(env, A770B_SANDBOX_ENTERED_FILE=str(entered), A770B_SANDBOX_RECORD_FILE=str(record)),
            cwd=seat, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == expected, (proc.stdout, proc.stderr)
        assert entered.read_text(encoding="utf-8").strip() == "entered"
        body = json.loads(record.read_text(encoding="utf-8"))
        if _bus():
            assert body["isolation"] == "scope", body
        else:
            assert body["isolation"] == "none", body
    finally:
        _reset_scopes(_scope_names() - before)


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
    before = _scope_names()
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
        unit = body["unit"]
        shown = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            shown = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MemoryMax",
                 "-p", "MemoryHigh", "-p", "MemorySwapMax", "-p", "TasksMax", "-p", "CPUQuotaPerSecUSec"],
                capture_output=True, text=True,
            )
            if shown.returncode == 0 and "ActiveState=active" in shown.stdout:
                break
            time.sleep(0.1)
        assert shown is not None and shown.returncode == 0, (shown.stdout if shown else "", shown.stderr if shown else "")
        fields = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        assert fields.get("ActiveState") == "active", shown.stdout
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
        slice_show = subprocess.run(
            ["systemctl", "--user", "show", "a770b-sandbox.slice", "-p", "MemoryMax", "-p", "ActiveState"],
            capture_output=True, text=True,
        )
        assert slice_show.returncode == 0, slice_show.stderr
        assert "MemoryMax=infinity" in slice_show.stdout, slice_show.stdout
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        if record.exists():
            subprocess.run([sys.executable, "-I", "-B", str(OOM_PY), str(record), "137"], check=False)
        _reset_scopes(_scope_names() - before)


def test_i8_oom_record_grade_and_outside_kill(tmp_path, monkeypatch):
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
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "i8.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    (results / "i8.spec.json").write_text(json.dumps({"verify": {"test": "sleep 60"}}), encoding="utf-8")
    before = _scope_names()
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "30"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    target = None
    bwrap_pid = None
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
        desc = _descendants(target)
        os.kill(target, signal.SIGKILL)
        verify.wait(timeout=20)
        doc = json.loads((results / "i8.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", doc
        assert doc["reason"] != "oom"
        left = _wait_gone(desc + [bwrap_pid])
        assert left == [], f"sandbox processes survived the outside kill: {left}"
        leaked = []
        for unit in _scope_names() - before:
            shown = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "ActiveState"],
                capture_output=True, text=True,
            )
            if "ActiveState=active" in shown.stdout or "ActiveState=activating" in shown.stdout:
                leaked.append(unit)
        assert leaked == [], leaked
    finally:
        if verify.poll() is None:
            os.kill(verify.pid, signal.SIGKILL)
            verify.wait(timeout=5)
        _kill_ours(_descendants(verify.pid))
        _reset_scopes(_scope_names() - before)


def test_i9_without_systemd_run_still_runs_and_dies_with_parent(tmp_path):
    bin_ = _tools(tmp_path)
    _exe(bin_ / "systemd-run", "#!/bin/bash\nexit 1\n")
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    record = tmp_path / "data" / "logs" / "none.json"
    ready = seat / "ready"
    env = _env(
        tmp_path,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_SANDBOX_RECORD_FILE=str(record),
    )
    command = f'sleep 120 & echo ready > "{ready}"; sleep 120'
    proc = subprocess.Popen(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", command],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not ready.exists():
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read() if proc.stderr else "exited")
            time.sleep(0.1)
        assert ready.exists(), "sandbox command did not start"
        desc = _descendants(proc.pid)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        err = proc.stderr.read() if proc.stderr else ""
        assert err.count("isolation=none") == 1, err
        body = json.loads(record.read_text(encoding="utf-8"))
        assert body["isolation"] == "none"
        assert body["oom"] == "unknown"
        left = _wait_gone(desc)
        assert left == [], left
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
        _kill_ours(_descendants(proc.pid))
        proc.wait(timeout=5)


def test_i10_sandbox_slice_path_is_null_when_absent(tmp_path):
    sys.path.insert(0, str(ROOT / "harness"))
    import host_memory
    uid = os.getuid()
    missing = tmp_path / "missing-root"
    missing.mkdir()
    assert host_memory.read_sandbox_slice(missing, uid) is None
    short = missing / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service" / "a770b-sandbox.slice"
    short.mkdir(parents=True)
    (short / "memory.current").write_text("0\n", encoding="ascii")
    assert host_memory.read_sandbox_slice(missing, uid) is None
    real = host_memory.sandbox_slice_dir(missing, uid)
    real.mkdir(parents=True)
    (real / "memory.current").write_text("0\n", encoding="ascii")
    (real / "memory.max").write_text("max\n", encoding="ascii")
    got = host_memory.read_sandbox_slice(missing, uid)
    assert got["memory_current"] == 0
    assert got["memory_max"] == "max"
    absent = host_memory.sample(cgroup_root=str(tmp_path / "no-such"))
    assert absent["sandbox_slice"] is None
    present_root = tmp_path / "present-root"
    present = host_memory.sandbox_slice_dir(present_root, uid)
    present.mkdir(parents=True)
    (present / "memory.current").write_text("0\n", encoding="ascii")
    (present / "memory.max").write_text("max\n", encoding="ascii")
    sample = host_memory.sample(cgroup_root=str(present_root))
    assert sample["sandbox_slice"] is not None
    assert sample["sandbox_slice"]["memory_current"] == 0


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
    _exe(bin_ / "a770b-qualify-stop", "exit 0\n")
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
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
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
    assert "\nnot recorded\n" in report or report.rstrip().endswith("not recorded")
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
    assert not journal_called.exists()
    assert fd1.read_text(encoding="utf-8").strip() == "/dev/null", fd1.read_text(encoding="utf-8")
    assert "sandbox_oom.py" in cmdline.read_text(encoding="utf-8")

    # The show sleeps inside the tool bound and outside the command wait.
    # Hidden still runs only when that sleep is added back onto the deadline.
    patch = results / "near.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    hidden = Path(env["A770B_DATA"]) / "hidden" / "hid_ok"
    hidden.write_text("print('ok')\n", encoding="utf-8")
    (results / "near.spec.json").write_text(json.dumps({
        "verify": {"test": "sleep 2", "hidden": ["hid_ok"]},
    }), encoding="utf-8")
    _exe(bin_ / "systemctl", """
case " $* " in
  *" show "*)
    sleep 3
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
    before = _scope_names()
    try:
        for entered in (False, True):
            marker = seat / f"shadow-marker-{int(entered)}"
            marker.unlink(missing_ok=True)
            record = tmp_path / f"shadow-{int(entered)}.json"
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
    finally:
        _reset_scopes(_scope_names() - before)


def _slow_python(bin_: Path) -> None:
    _exe(bin_ / "python3", """
if [ -n "${A770B_SLOW_NEEDLE:-}" ] && [[ " $* " == *" ${A770B_SLOW_NEEDLE} "* ]]; then
  printf '%s\\n' "$$" > "${A770B_SLOW_READY:?}"
  /usr/bin/sleep 2
fi
exec /usr/bin/python3 "$@"
""")


def _wait_ready(path: Path, proc: subprocess.Popen, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not path.exists():
        if proc.poll() is not None:
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"parent exited {proc.returncode} before the delayed python: {err}")
        time.sleep(0.05)
    assert path.exists(), "delayed python did not start"


def _arm_pid_gone(pid: int) -> None:
    deadline = time.monotonic() + 5
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
    before = _scope_names()
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
        try:
            _wait_ready(ready, proc)
            delayed = int(ready.read_text(encoding="ascii").strip())
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _arm_pid_gone(delayed)
            assert not marker.exists()
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)

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
        try:
            _wait_ready(ready, proc)
            delayed = int(ready.read_text(encoding="ascii").strip())
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _arm_pid_gone(delayed)
            assert not marker.exists()
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            _kill_ours(_descendants(proc.pid))

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
        try:
            _wait_ready(ready, proc, seconds=30)
            delayed = int(ready.read_text(encoding="ascii").strip())
            # The grader's parent is the command-substitution shell, not the
            # local-build process that owns the pipeline.
            parent = int(Path(f"/proc/{delayed}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[1])
            os.kill(parent, signal.SIGKILL)
            _arm_pid_gone(delayed)
            assert not marker.exists()
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            _kill_ours(_descendants(proc.pid))
    finally:
        _reset_scopes(_scope_names() - before)


def test_b3_preexec_parent_is_the_pid_from_before_the_fork(tmp_path):
    """Kill the parent while the forked child is still in preexec."""
    ready = tmp_path / "arm.ready"
    marker = tmp_path / "arm-ran"
    env, patch, seat, _results = _verify_fixture(tmp_path, "arm", f"printf ran > '{marker}'")
    env = dict(env, A770B_SLOW_ARM_READY=str(ready))
    before = _scope_names()
    proc = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "30"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_ready(ready, proc)
        delayed = int(ready.read_text(encoding="ascii").strip())
        parent = int(Path(f"/proc/{delayed}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[1])
        os.kill(parent, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(delayed):
            time.sleep(0.05)
        assert not marker.exists()
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        _kill_ours(_descendants(proc.pid))
        _reset_scopes(_scope_names() - before)


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
        "isolation": "scope", "unit": "a770b-sbx-stuck.scope", "policy": "kill", "oom": "unknown",
    }), encoding="utf-8")
    sandbox_oom.update(str(record), 0)
    text = log.read_text(encoding="utf-8")
    assert " stop " in text, text
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    log.unlink()
    _exe(bin_ / "systemctl", f"""
printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" show "*) /usr/bin/sleep 3 ;;
esac
exit 0
""")
    record.write_text(json.dumps({
        "isolation": "scope", "unit": "a770b-sbx-hang.scope", "policy": "continue", "oom": "unknown",
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
