#!/usr/bin/env python3
"""run_lead.py — the process-group leader of one local-build run.

    run_lead.py [--timeout DURATION] -- <command> [args...]

build_local.sh records this process's pid in run.pid; `local-build.sh stop-run` sends it (and, when it can read
the group, its group) one TERM. The leader remembers that signal, forwards it, waits for the command, and then
exits 128+signal WHATEVER the command's own status. A worker that handles TERM and exits 0 is therefore still
reported as interrupted: the stop needs no receipt file and no lookup of the run's owner to reach grading — the
signal stop-run must deliver anyway is the record.

The command runs in a process group of its own, never this leader's. Every signal this leader forwards — a stop,
or the fence's TERM — goes to that group, so no TERM, INT or HUP this leader ever receives is its own: each one is
a stop. A stop cannot coalesce with the fence's broadcast and be mistaken for it. The command is started with
posix_spawn while the stop signals and the fence's SIGALRM are blocked here; the child is in its own group, with
default dispositions and those signals unblocked, before it runs a single instruction of the command. A stop that
arrives during the start is held, then forwarded once the command's pid is known.

Right after that spawn the leader publishes the worker's pid (it is the worker's pgid) and its /proc start time,
one line "<pid> <starttime>", to A770B_WORKER_PGID_FILE or, if that is unset, $A770B_DATA/logs/run.worker-pgid.
The file is removed only after this process has reaped the worker. A SIGKILL, an OOM kill or a crash leaves it,
so a later guarded signal can end the group that a kill of this leader's group does not reach.

--timeout is the caller's fence (it replaces GNU timeout here): on expiry TERM goes to the command's group; the exit
is then 124 — the same convention as timeout(1). A stop received before or during the fence's drain wins over the
fence. DURATION is seconds, with an optional s/m/h/d suffix; a zero DURATION disables the fence, as it does for
timeout(1).
"""

import ctypes
import math
import os
import signal
import sys
import tempfile


def _arm_parent_death():
    """Die with the parent named before this interpreter started.

    build_local.sh exports A770B_RUN_OWNER_PID. Reading getppid() as that
    parent arms against the user manager if the parent was already killed.
    """
    raw = os.environ.get("A770B_RUN_OWNER_PID", "")
    if raw.isdigit() and int(raw) > 1:
        parent = int(raw)
    else:
        parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent:
        print("run_lead.py: parent changed during arming", file=sys.stderr)
        return 137
    return 0

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _usage(msg):
    print(f"run_lead.py: {msg}\nusage: run_lead.py [--timeout DURATION] -- <command> [args...]", file=sys.stderr)
    return 125


def _duration(text):
    text = text.strip()
    unit = text[-1:] if text[-1:] in _UNITS and not text[-1:].isdigit() else ""
    value = float(text[: len(text) - len(unit)])
    if not math.isfinite(value) or value < 0:
        raise ValueError(text)
    return value * _UNITS[unit]


def _worker_group_path():
    path = os.environ.get("A770B_WORKER_PGID_FILE")
    if path:
        return path
    data = os.environ.get("A770B_DATA")
    if not data:
        return None
    return os.path.join(data, "logs", "run.worker-pgid")


def _proc_stat_fields(pid):
    with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
        text = fh.read()
    tail = text.rsplit(")", 1)
    if len(tail) != 2:
        raise OSError(f"unreadable stat for {pid}")
    return tail[1].split()


def _publish_worker_group(child):
    """Atomically publish the worker pgid and start time. A failed publish does not abandon the child."""
    path = _worker_group_path()
    if not path:
        return False
    try:
        fields = _proc_stat_fields(child)
        if len(fields) < 20:
            raise OSError(f"short stat for {child}")
        # fields[2] is pgrp; fields[19] is starttime (proc(5) field 22). The spawn asked for pgid == pid.
        if fields[2] != str(child):
            raise OSError(f"worker {child} is not its own process group (pgid {fields[2]})")
        payload = f"{child} {fields[19]}\n".encode("ascii")
    except (OSError, IndexError) as exc:
        print(f"run_lead.py: worker group not published: {exc}", file=sys.stderr)
        return False
    directory = os.path.dirname(path) or "."
    tmp = ""
    fd = -1
    try:
        fd, tmp = tempfile.mkstemp(prefix=".worker-pgid.", dir=directory)
        written = 0
        while written < len(payload):
            wrote = os.write(fd, payload[written:])
            if wrote <= 0:
                raise OSError("short write of the worker group file")
            written += wrote
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)               # same directory: the file appears with both fields or not at all
        tmp = ""
    except OSError as exc:
        print(f"run_lead.py: worker group not published: {exc}", file=sys.stderr)
        return False
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return True


def _clear_worker_group():
    path = _worker_group_path()
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"run_lead.py: worker group record remains: {exc}", file=sys.stderr)


def _spawns_harness_sandbox(argv):
    """This tree's sandbox_run.sh, run directly or as bash's script argument.

    `bash -c` is not that form. An inherited spawner pid must not reach any
    other worker: with SANDBOX=0 the agent would hand it to a later sandbox.
    """
    script = os.path.realpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_run.sh")
    )

    def same(path):
        if not path or path.startswith("-"):
            return False
        try:
            return os.path.realpath(path) == script
        except OSError:
            return False

    if same(argv[0]):
        return True
    return os.path.basename(argv[0]) == "bash" and len(argv) > 1 and same(argv[1])


def main(argv):
    armed = _arm_parent_death()
    if armed:
        return armed
    args = list(argv)
    fence = None
    if args[:1] == ["--timeout"]:
        if len(args) < 2:
            return _usage("--timeout needs a duration")
        try:
            fence = _duration(args[1])
        except (ValueError, IndexError):
            return _usage(f"not a duration: {args[1]!r}")
        args = args[2:]
    if args[:1] != ["--"] or len(args) < 2:
        return _usage("the command follows --")
    cmd = args[1:]

    if fence == 0:
        fence = None                            # timeout(1): a zero duration disables the timeout

    try:
        os.setpgid(0, 0)                        # this leader's own group: stop-run's group TERM reaches only it
    except OSError:
        pass

    state = {"stop": 0, "fenced": False, "child": 0}

    def forward(signum):
        child = state["child"]
        if not child:
            return                              # not started yet: the stop is held and forwarded after the start
        try:
            os.killpg(child, signum)            # the command's own group (pgid == its pid), never this leader's
        except OSError:
            try:
                os.kill(child, signum)
            except OSError:
                pass

    def on_stop(signum, _frame):
        if not state["stop"]:
            state["stop"] = signum
            forward(signum)

    def on_fence(_signum, _frame):
        state["fenced"] = True
        forward(signal.SIGTERM)

    held = (*STOP_SIGNALS, signal.SIGALRM)
    inherited = signal.pthread_sigmask(signal.SIG_BLOCK, held)
    for sig in STOP_SIGNALS:
        signal.signal(sig, on_stop)
    signal.signal(signal.SIGALRM, on_fence)
    try:
        # The sandbox arms against this pid. Its own PPID, read after it has
        # started, is the adopter when this leader has already died. Any other
        # command loses an inherited value so it cannot arm a later sandbox.
        child_env = os.environ.copy()
        child_env.pop("A770B_SPAWNER_PID", None)
        if _spawns_harness_sandbox(cmd):
            child_env["A770B_SPAWNER_PID"] = str(os.getpid())
        # Atomic in the child: its own group, default dispositions for every held signal, and a mask without them.
        child = os.posix_spawnp(cmd[0], cmd, child_env, setpgroup=0, setsigdef=held,
                                setsigmask=set(inherited) - set(held))
    except FileNotFoundError:
        print(f"run_lead.py: {cmd[0]}: not found", file=sys.stderr)
        return 127
    except OSError as exc:
        print(f"run_lead.py: {cmd[0]}: {exc}", file=sys.stderr)
        return 126
    state["child"] = child
    published = _publish_worker_group(child)   # while the stop signals are still blocked; SIGKILL after this leaves the file
    if fence is not None:
        signal.setitimer(signal.ITIMER_REAL, max(fence, 1e-6))   # a positive fence never rounds down to "no clock"
    signal.pthread_sigmask(signal.SIG_SETMASK, inherited)   # a stop held during the start is handled (forwarded) here

    while True:
        try:
            _pid, status = os.waitpid(child, 0)
            break
        except ChildProcessError:
            status = None                       # already gone: nothing of this worker is left to signal
            break
    signal.setitimer(signal.ITIMER_REAL, 0)
    if published:
        _clear_worker_group()                   # only after the reap. A SIGKILL never reaches this line.

    if status is None:
        worker = "unknown"
        rc = 125
    elif os.WIFSIGNALED(status):
        worker = f"signal {os.WTERMSIG(status)}"
        rc = 128 + os.WTERMSIG(status)
    else:
        worker = f"exit {os.WEXITSTATUS(status)}"
        rc = os.WEXITSTATUS(status)
    if state["stop"]:
        name = signal.Signals(state["stop"]).name
        print(f"▶ run_lead: {name} received — the run was stopped and is reported as interrupted "
              f"(exit {128 + state['stop']}); the worker's own status was {worker}", file=sys.stderr, flush=True)
        return 128 + state["stop"]
    if state["fenced"]:
        print(f"▶ run_lead: the caller's fence expired — exit 124; the worker's own status was {worker}",
              file=sys.stderr, flush=True)
        return 124
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
