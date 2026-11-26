#!/usr/bin/env python3
"""Round A containment. Each test fails when its invariant is broken.

OOM runs use a test-only MemoryMax on an a770b-test-* unit. They skip when
the user systemd bus is absent. Nothing here writes the workstation log tree.
"""

import json
import os
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


def _sandbox_cmd(env, seat, cfg, command, entered=None):
    if entered is not None:
        env = dict(env, A770B_SANDBOX_ENTERED_FILE=str(entered))
    return [
        "bash", str(SANDBOX), str(seat), str(cfg), "--",
        "bash", "-c", command,
    ], env


def test_i2_parent_death_kills_sandbox_and_bridge(tmp_path):
    """Both launch branches. A background grandchild is inside the sandbox."""
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    base = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    for entered in (False, True):
        ready = seat / f"ready-{int(entered)}"
        ready.unlink(missing_ok=True)
        entered_file = tmp_path / f"entered-{int(entered)}"
        command = f'sleep 120 & echo ready > "{ready}"; sleep 120'
        argv, env = _sandbox_cmd(base, seat, cfg, command, entered_file if entered else None)
        if entered:
            env["A770B_NO_BRIDGE"] = "0"
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not ready.exists():
                if proc.poll() is not None:
                    err = proc.stderr.read() if proc.stderr else ""
                    raise AssertionError(f"sandbox exited {proc.returncode} before ready: {err}")
                time.sleep(0.1)
            assert ready.exists(), "sandbox command did not start"
            if entered:
                file_deadline = time.monotonic() + 5
                while time.monotonic() < file_deadline and not entered_file.exists():
                    time.sleep(0.05)
                assert entered_file.read_text(encoding="utf-8").strip() == "entered"
            desc = _descendants(proc.pid)
            assert desc, "sandbox produced no child"
            os.kill(proc.pid, signal.SIGKILL)
            left = _wait_gone(desc)
            assert left == [], f"sandbox processes survived the caller: {left}"
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
            _kill_ours(_descendants(proc.pid))
            proc.wait(timeout=5)


def test_i3_stop_signal_and_verify_timeout_end_the_run(tmp_path):
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    ready = seat / "lead-ready"
    command = f'sleep 120 & echo ready > "{ready}"; sleep 120'
    proc = subprocess.Popen(
        [sys.executable, str(RUN_LEAD), "--", "bash", str(SANDBOX), str(seat), str(cfg), "--", "bash", "-c", command],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not ready.exists():
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read() if proc.stderr else "leader exited")
            time.sleep(0.1)
        assert ready.exists()
        desc = _descendants(proc.pid)
        os.kill(proc.pid, signal.SIGTERM)
        rc = proc.wait(timeout=15)
        assert rc == 143, rc
        left = _wait_gone(desc)
        assert left == [], left
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
        _kill_ours(_descendants(proc.pid))
        proc.wait(timeout=5)
    ready.unlink(missing_ok=True)

    token = f"verify-token-{tmp_path.name}"
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "i3.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+seat changed\n",
        encoding="utf-8",
    )
    (results / "i3.spec.json").write_text(json.dumps({
        "verify": {"test": f"sleep 60 {token} & echo ready > '{seat}/vready'; sleep 60"},
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
case " $* " in
  *" --collect "*) exit 0 ;;
esac
ls -1 /proc/$$/fd > "{fd_run}"
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


def test_i5_script_and_command_reach_bash_unexpanded(tmp_path):
    bin_ = _tools(tmp_path)
    seat = _seat(tmp_path)
    cfg = _cfg(tmp_path)
    seen = tmp_path / "argv"
    _exe(bin_ / "systemd-run", f"""
case " $* " in
  *" --collect "*) exit 0 ;;
esac
printf '%s\\0' "$@" > "{seen}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 1
""")
    _exe(bin_ / "bwrap", """
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then shift; exec "$@"; fi
  shift
done
exit 0
""")
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}", A770B_NO_BRIDGE="1")
    proc = subprocess.run(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "printf", "%s", "a b", "$HOME", "$$"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    blob = seen.read_bytes()
    assert b"--expand-environment=no" in blob
    assert b'$HOME' in blob and b'$$' in blob
    assert b"a b" in blob
    assert b'mkdir -p "$HOME/.cache/uv"' in blob


def test_i6_scope_and_slice_have_no_limit(tmp_path):
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
    proc = subprocess.run(
        ["bash", str(SANDBOX), str(seat), str(cfg), "--", "true"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(record.read_text(encoding="utf-8"))
    if body["isolation"] != "scope":
        pytest.skip("scope was refused")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "0"], check=False)
    unit = body["unit"]
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "MemoryMax", "-p", "MemoryHigh",
         "-p", "MemorySwapMax", "-p", "CPUQuotaPerSecUSec"],
        capture_output=True, text=True,
    )
    if shown.returncode == 0:
        for line in shown.stdout.splitlines():
            if "=" not in line:
                continue
            value = line.split("=", 1)[1]
            assert value in ("infinity", "infinity ", ""), line
    slice_show = subprocess.run(
        ["systemctl", "--user", "show", "a770b-sandbox.slice", "-p", "MemoryMax"],
        capture_output=True, text=True,
    )
    if slice_show.returncode == 0 and slice_show.stdout.strip():
        assert "infinity" in slice_show.stdout


def test_i8_oom_record_grade_and_outside_kill(tmp_path):
    sys.path.insert(0, str(ROOT / "harness"))
    import sandbox_oom
    assert sandbox_oom.grade(0, "yes") == ("fail", 137, "oom")
    assert sandbox_oom.grade(1, "yes") == ("fail", 1, "oom")
    assert sandbox_oom.grade(-9, "no", signaled=True)[0] == "infra_error"
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

    marker = tmp_path / "journal-called"
    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=failed' 'Result=oom-kill'\n",
        f"printf 1 > '{marker}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    # Exit 137 and Result=oom-kill is yes, with no journal read.
    _put("scope", "a770b-sbx-t.scope", "kill")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "137"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"
    assert not marker.exists()

    # Result=oom-kill and exit 0 together is unknown, still with no journal.
    _put("scope", "a770b-sbx-t.scope", "kill")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "0"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"
    assert not marker.exists()

    # Exit 0 and Result=success is no, and the journal is not read.
    success_called = tmp_path / "success-journal"
    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=inactive' 'Result=success'\n",
        f"printf 1 > '{success_called}'\nexit 0\n",
    )}:{os.environ['PATH']}")
    _put("scope", "a770b-sbx-ok.scope", "kill")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "0"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "no"
    assert not success_called.exists()

    env = dict(os.environ, PATH=f"{_stubs(
        "printf '%s\\n' 'ActiveState=inactive' 'Result=success'\n",
        "exit 1\n",
    )}:{os.environ['PATH']}")
    _put("scope", "a770b-sbx-c.scope", "continue")
    journal = (
        "printf '%s\\n' "
        "'{\"MESSAGE\":\"The kernel OOM killer killed some processes in this unit.\","
        "\"SYSLOG_IDENTIFIER\":\"systemd\","
        "\"MESSAGE_ID\":\"fe6faa94e7774663a0da52717891d8ef\"}'\n"
    )
    _exe(tmp_path / "grade-bin" / "journalctl", journal)
    subprocess.run([sys.executable, str(OOM_PY), str(record), "0"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "yes"

    _exe(tmp_path / "grade-bin" / "journalctl", "exit 1\n")
    _put("scope", "a770b-sbx-u.scope", "continue")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "1"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    # A Started line and no OOM entry, after the short poll, is no.
    _exe(
        tmp_path / "grade-bin" / "journalctl",
        "printf '%s\\n' '{\"MESSAGE\":\"Started scope.\",\"SYSLOG_IDENTIFIER\":\"systemd\"}'\n",
    )
    _put("scope", "a770b-sbx-started.scope", "continue")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "1"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "no"

    _put("none", None, "continue")
    subprocess.run([sys.executable, str(OOM_PY), str(record), "0"], check=True, env=env)
    assert json.loads(record.read_text(encoding="utf-8"))["oom"] == "unknown"

    # Outside kill of sandbox_run.sh, through verify. Not an OOM.
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
    verify = subprocess.Popen(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "30"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        target = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and target is None and verify.poll() is None:
            for pid in _descendants(verify.pid):
                try:
                    cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
                except OSError:
                    continue
                # The grader's argv contains the script path. Match the bash that
                # is sandbox_run.sh, which is the process an outside kill hits.
                if cmd.split(b"\0")[0].endswith(b"python3") or cmd.split(b"\0")[0].endswith(b"python"):
                    continue
                if b"sandbox_run.sh" in cmd:
                    target = pid
                    break
            time.sleep(0.1)
        assert target is not None, "sandbox_run.sh did not start"
        os.kill(target, signal.SIGKILL)
        verify.wait(timeout=20)
        doc = json.loads((results / "i8.verify.json").read_text(encoding="utf-8"))
        assert doc["status"] == "infra_error", doc
        assert doc["reason"] != "oom"
    finally:
        if verify.poll() is None:
            os.kill(verify.pid, signal.SIGKILL)
            verify.wait(timeout=5)
        _kill_ours(_descendants(verify.pid))


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
        err = ""
        # The warning is already on stderr; read it after the kill.
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
    sample = host_memory.sample(cgroup_root=str(tmp_path / "no-such"))
    assert sample["sandbox_slice"] is None


def test_i11_qualify_tracks_the_server_and_continues_on_oom():
    qualify = QUALIFY.read_text(encoding="utf-8")
    suite = RUN_SUITE.read_text(encoding="utf-8")
    assert "--pid-file" in qualify
    assert "TRACKER" in qualify
    assert "SERVING_CLEARED" in qualify
    assert "serving.pid" in qualify
    assert "OOMPolicy=continue" in qualify
    assert "A770B_QUALIFY_SERVING_PID_FILE" in qualify
    assert "A770B_QUALIFY_SERVING_PID_FILE" in suite
    start = qualify.index("stop_helpers()")
    stop = qualify.index("\nstop_server()")
    helpers = qualify[start:stop]
    assert "TRACKER" in helpers
    server = qualify[qualify.index("stop_server()"):qualify.index("record_hooks()")]
    assert "SERVING_CLEARED" in server
    assert "serving.pid" in server
    assert "TRACKER" in server
    observers = qualify[qualify.index("start_observers()"):qualify.index("model_path()")]
    assert 'serve_strata.sh" pid' in observers
    assert "a770b-test" not in qualify


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
    assert "yes" in report2


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


def test_i13_oom_zero_is_137_and_the_read_is_outside_the_timeout(tmp_path):
    env, patch, seat, results = _verify_fixture(tmp_path, "near", "sleep 1")
    bin_ = tmp_path / "bin"
    _exe(bin_ / "systemctl", "#!/bin/bash\nexit 1\n")
    _exe(bin_ / "journalctl", """#!/bin/bash
sleep 12
printf '%s\\n' '{"MESSAGE":"Started scope.","SYSLOG_IDENTIFIER":"systemd"}'
""")
    started = time.monotonic()
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "10"],
        env=env, capture_output=True, text=True,
    )
    elapsed = time.monotonic() - started
    doc = json.loads((results / "near.verify.json").read_text(encoding="utf-8"))
    assert doc["status"] == "pass", (doc, proc.stdout, proc.stderr)
    assert doc["exit_code"] == 0
    assert doc["oom"]["public"] == "no"
    assert elapsed > 10

    # The seat was reset. A second patch applies to that same clean tree.
    patch = results / "zero.patch"
    patch.write_text(
        "diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-seat\n+changed\n",
        encoding="utf-8",
    )
    (results / "zero.spec.json").write_text(json.dumps({"verify": {"test": "true"}}), encoding="utf-8")
    _exe(bin_ / "journalctl", """#!/bin/bash
printf '%s\\n' '{"MESSAGE":"The kernel OOM killer killed some processes in this unit.","SYSLOG_IDENTIFIER":"systemd","MESSAGE_ID":"fe6faa94e7774663a0da52717891d8ef"}'
""")
    proc = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        env=env, capture_output=True, text=True,
    )
    doc = json.loads((results / "zero.verify.json").read_text(encoding="utf-8"))
    assert doc["status"] == "fail", doc
    assert doc["reason"] == "oom"
    assert doc["exit_code"] == 137
    assert proc.returncode == 137


def test_i1_i7_oom_scope_contains_the_hog(tmp_path):
    """Two capped runs: continue leaves the command, kill ends the scope.

    The hog allocates only after /proc/self/cgroup names a770b-test-. The
    wrapper adds the cap; production arguments are logged without it.
    """
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
                pytest.skip("systemd-run probe did not start a scope")
            assert _alive(sibling.pid) and _alive(standin.pid)
            assert _alive(os.getpid())
            text = log.read_text(encoding="utf-8")
            assert "MemoryMax" not in "\n".join(line for line in text.splitlines() if line.startswith("ORIG"))
            assert f"OOMPolicy={policy}" in text
            for line in text.splitlines():
                if line.startswith("UNIT "):
                    unit = line.split(" ", 1)[1].strip()
                    assert unit.startswith("a770b-test-")
                    if unit not in units:
                        units.append(unit)
            if policy == "continue":
                assert marker.exists(), proc.stderr
                assert proc.returncode == 0, proc.stderr
            else:
                assert not marker.exists(), "kill policy let the command continue"
                assert proc.returncode != 0
        assert len(units) == 2
        for unit in units:
            shown = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "Result"],
                capture_output=True, text=True,
            )
            journal = subprocess.run(
                ["journalctl", "--user", "-u", unit, "--no-pager"],
                capture_output=True, text=True,
            )
            assert "oom-kill" in shown.stdout or "OOM killer" in journal.stdout, (unit, shown.stdout, journal.stdout)
    finally:
        for unit in units:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
            subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
        for proc in (sibling, standin):
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
