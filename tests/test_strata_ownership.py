"""A job stops only the Strata instances it started, and leaves none behind.

The server, the engine and the card are stand-ins. Every process signalled here was
started by these tests.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import test_strata_managed as fixtures
from test_strata_managed import config, managed  # noqa: F401  (config is a fixture)
from test_qualify_stop_fixes import _functions, _queued_job, _suite_run
from test_qualify_run import QUALIFY, _run, box  # noqa: F401  (box is a fixture)
from test_review_r6_fixes import _make_exec

ROOT = Path(__file__).resolve().parents[1]
MANAGED = ROOT / "harness" / "strata_managed.py"
SERVE = ROOT / "harness" / "serve_strata.sh"

# The stand-in server records that its entry point ran.
SERVER = fixtures.SERVER.replace(
    "args = parser.parse_args()\n",
    "args = parser.parse_args()\n"
    "Path(os.environ['STRATA_HOME'], 'server-ran').touch()\n",
)

# Ignores the owner pipe and SIGTERM, and starts an engine in its own process group
# that ignores SIGTERM too, recorded the way Strata records it.
ENGINE = (
    "import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "pathlib.Path(sys.argv[1]).touch(); time.sleep(90)"
)
STUBBORN = SERVER.replace(
    """def owner():
        os.read(int(args.owner_fd), 1)
        server.shutdown()
    threading.Thread(target=owner, daemon=True).start()
""",
    f"""import signal, subprocess, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    home = Path(os.environ['STRATA_HOME'])
    ready = home / 'engine-ready'
    engine = subprocess.Popen([sys.executable, '-c', {ENGINE!r}, str(ready)],
                              start_new_session=True)
    while not ready.exists():
        time.sleep(0.01)
    def start_of(pid):
        return Path(f'/proc/{{pid}}/stat').read_text().rsplit(')', 1)[1].split()[19]
    procs = Path(os.environ['XDG_STATE_HOME'], 'strata', 'procs')
    procs.mkdir(parents=True)
    (procs / f'{{engine.pid}}.json').write_text(json.dumps(dict(
        pid=engine.pid, role='engine', token='l:' + start_of(engine.pid),
        parent=dict(pid=os.getpid(), token='l:' + start_of(os.getpid())))))
""",
)

# Runs the real main() in a process of its own, with a hook that injects at one point.
LAUNCHER = """
import os, signal, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import strata_managed as managed
terminal, hook = Path(sys.argv[2]), sys.argv[3]
managed.source_inputs = lambda: (terminal, Path(sys.executable))
managed.level_zero_pci_ids = lambda: [(0x8086, 0xe20b), (0x8086, 0xe223)]
exec(hook)
sys.argv = ["strata_managed.py", "start", sys.argv[4], "--ctx", "32768", "--timeout", "20"]
sys.exit(managed.main())
"""


def _survivors(root: Path) -> list:
    """Live processes whose command line names this test's directory."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if str(root).encode() in command:
            found.append(int(entry.name))
    return found


def _gone(root: Path, timeout=15) -> bool:
    deadline = time.monotonic() + timeout
    while _survivors(root) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _survivors(root)


@pytest.fixture
def world(config, tmp_path, monkeypatch):  # noqa: F811
    terminal = tmp_path / "terminal"
    (terminal / "serve").mkdir(parents=True)
    (terminal / "serve/server.py").write_text(SERVER)
    key = tmp_path / "api.key"
    key.write_text("fixture-key\n")
    (tmp_path / "data" / "logs").mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    for name, value in {
        "A770B_CARD": "b70", "A770B_GPU_MATCH": "G31", "A770B_HOST": "127.0.0.1",
        "A770B_MIN_AVAIL_MB": "1", "A770B_PORT": str(port),
        "A770B_API_KEY_FILE": str(key), "A770B_DATA": str(tmp_path / "data"),
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("A770B_STRATA_OWNED_FILE", "A770B_BUDGET_GATE_RECEIPT"):
        monkeypatch.delenv(name, raising=False)

    class World:
        root = tmp_path
        state = tmp_path / "data" / "logs" / "strata-instance.json"

        def start(self, hook="", timeout=40):
            return subprocess.run(
                [sys.executable, "-c", LAUNCHER, str(MANAGED.parent), str(terminal),
                 hook, str(config)], capture_output=True, text=True, timeout=timeout)

        def serve(self, body):
            (terminal / "serve/server.py").write_text(body)

        def owned(self):
            path = tmp_path / "owned.ids"
            path.write_text("")
            monkeypatch.setenv("A770B_STRATA_OWNED_FILE", str(path))
            return path

        def command(self, *args):
            return subprocess.run([sys.executable, str(MANAGED), *args],
                                  capture_output=True, text=True, timeout=90)

        def launched(self):
            return list((tmp_path / "data" / "logs").glob("strata-*/server-ran"))

    yield World()
    # Anything still here was started by this test.
    for pid in _survivors(tmp_path):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# --- T18: no supervisor, server or engine is left

BEFORE_SPAWN = """
class Popen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        signal.raise_signal(signal.SIGTERM)
        raise AssertionError("the signal did not stop the spawn")
managed.subprocess.Popen = Popen
"""
BEFORE_LAUNCH_BYTE = """
real_write = managed.write_state
def write_state(path, state):
    real_write(path, state)
    if path.name == "strata-instance.json":
        signal.raise_signal(signal.SIGTERM)
managed.write_state = write_state
"""
DURING_LOAD = """
def health(state, key):
    if (Path(state["run"]) / "server-ran").exists():
        signal.raise_signal(SIGNUM)
    return False
SIGNUM = signal.SIGTERM
managed.health_matches = health
"""
# A second signal inside the cleanup, before the wait and before the stop.
TWICE = DURING_LOAD + """
real_terminate, real_stop = subprocess.Popen.terminate, managed.stop
def terminate(self):
    signal.raise_signal(signal.SIGTERM)
    real_terminate(self)
def stop(path, expected=None):
    signal.raise_signal(signal.SIGINT)
    real_stop(path, expected)
subprocess.Popen.terminate = terminate
managed.stop = stop
"""


@pytest.mark.parametrize("hook,signum,launched", [
    (BEFORE_SPAWN, signal.SIGTERM, False),
    (BEFORE_LAUNCH_BYTE, signal.SIGTERM, False),
    # A supervisor that is never terminated must still leave on the closed gate.
    (BEFORE_LAUNCH_BYTE + "subprocess.Popen.terminate = lambda self: None\n",
     signal.SIGTERM, False),
    (DURING_LOAD, signal.SIGTERM, True),
    (DURING_LOAD + "SIGNUM = signal.SIGINT\n", signal.SIGINT, True),
    (TWICE, signal.SIGTERM, True),
], ids=["before-spawn", "before-byte", "gate-closed-first", "loading", "sigint",
        "two-signals"])
def test_t18_a_signal_during_start_leaves_nothing(world, hook, signum, launched):
    began = time.monotonic()
    got = world.start(hook)
    assert got.returncode == 128 + signum, got.stdout + got.stderr
    assert time.monotonic() - began < 30
    assert _gone(world.root), _survivors(world.root)
    assert not world.state.exists()
    assert bool(world.launched()) == launched


def test_t18_a_supervisor_wait_that_times_out_still_stops(world):
    hook = DURING_LOAD + """
real_wait = subprocess.Popen.wait
def wait(self, timeout=None):
    if timeout == managed.STOP_TIMEOUT:
        raise subprocess.TimeoutExpired("supervisor", timeout)
    return real_wait(self, timeout)
subprocess.Popen.wait = wait
subprocess.Popen.terminate = lambda self: None
"""
    got = world.start(hook)
    assert got.returncode == 128 + signal.SIGTERM, got.stdout + got.stderr
    assert _gone(world.root), _survivors(world.root)
    assert not world.state.exists()


def test_t18_a_start_killed_in_cleanup_is_stopped_from_the_owned_file(world):
    owned = world.owned()
    got = world.start(DURING_LOAD + """
subprocess.Popen.terminate = lambda self: os.kill(os.getpid(), signal.SIGKILL)
""")
    assert got.returncode == -signal.SIGKILL, got.stdout + got.stderr
    ids = owned.read_text().split()
    assert len(ids) == 1 and _survivors(world.root)
    stopped = world.command("stop-owned", ids[0])
    assert stopped.returncode == 0, stopped.stderr
    assert _gone(world.root), _survivors(world.root)
    assert not world.state.exists()


def test_t18_a_start_killed_before_the_append_leaves_no_process(world):
    owned = world.owned()
    got = world.start("""
managed.record_owned = lambda *args: os.kill(os.getpid(), signal.SIGKILL)
""")
    assert got.returncode == -signal.SIGKILL, got.stdout + got.stderr
    assert _gone(world.root), _survivors(world.root)
    assert owned.read_text() == "" and not world.launched()


def test_t18_the_supervisor_ends_a_server_that_ignores_the_owner_pipe(world):
    world.serve(STUBBORN)
    got = world.start()
    assert got.returncode == 0, got.stdout + got.stderr
    state = json.loads(world.state.read_text())
    assert managed.server_alive(state) and managed.engine_alive(state)
    assert os.getpgid(state["server_pid"]) != os.getpgid(
        next(managed.engine_processes(state))[0])
    began = time.monotonic()
    # Only the supervisor is signalled: the rest is its own work.
    assert managed.signal_and_wait(state["pid"], state["start"], signal.SIGTERM, 35)
    assert time.monotonic() - began < 30
    assert _gone(world.root, 2), _survivors(world.root)


# --- T20 and the append


def test_a_start_appends_its_id_once_and_a_reuse_appends_nothing(world):
    owned = world.owned()
    try:
        first = world.start()
        assert first.returncode == 0, first.stderr
        state = json.loads(world.state.read_text())
        assert first.stdout == str(Path(state["run"]) / "evidence.json") + "\n"
        assert owned.read_text() == state["id"] + "\n"
        again = world.start()
        assert again.returncode == 0 and again.stdout == first.stdout, again.stderr
        assert owned.read_text() == state["id"] + "\n"
    finally:
        world.command("stop")
    assert _gone(world.root)


@pytest.mark.parametrize("kind", ["symlink", "directory", "missing", "fifo",
                                  "read-only", "failed-write"])
def test_t20_a_failed_append_fails_the_start_and_leaves_nothing(world, monkeypatch,
                                                                kind):
    if kind == "read-only" and os.geteuid() == 0:
        pytest.skip("root can append to a read-only file")
    path = world.root / "owned.ids"
    target = world.root / "target"
    target.write_text("kept\n")
    hook = ""
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind != "missing":
        path.write_text("")
    if kind == "read-only":
        path.chmod(0o400)
    if kind == "failed-write":
        hook = "managed.os.write = lambda fd, data, real=os.write: " \
               "real(fd, data) if data == b'1' else 0\n"
    monkeypatch.setenv("A770B_STRATA_OWNED_FILE", str(path))
    got = world.start(hook)
    assert got.returncode == 2, got.stdout + got.stderr
    assert got.stdout == ""
    assert _gone(world.root), _survivors(world.root)
    assert not world.state.exists() and not world.launched()
    assert target.read_text() == "kept\n"


# --- 3.6 stop


def _record_engine(run: Path, server_pid, server_start, engine_pid):
    procs = run / "xdg-state" / "strata" / "procs"
    procs.mkdir(parents=True)
    managed.write_state(procs / f"{engine_pid}.json", {
        "pid": engine_pid, "role": "engine",
        "token": f"l:{managed.process_identity(engine_pid)}",
        "parent": {"pid": server_pid, "token": f"l:{server_start}"}})


@pytest.fixture
def stubborn(tmp_path):
    """Spawn processes that ignore SIGTERM, and reap them afterwards."""
    children = []

    def spawn():
        ready = tmp_path / f"ready-{len(children)}"
        child = subprocess.Popen([sys.executable, "-c", ENGINE, str(ready)],
                                 start_new_session=True)
        children.append(child)
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        return child

    yield spawn
    for child in children:
        child.kill()
        child.wait()


def test_stop_kills_a_supervisor_and_an_engine_that_ignore_term(tmp_path, monkeypatch,
                                                               stubborn):
    supervisor, engine = stubborn(), stubborn()
    path = tmp_path / "instance.json"
    # The recorded server is gone; its engine remains.
    _record_engine(tmp_path, -1, "gone", engine.pid)
    managed.write_state(path, {
        "id": "fixture", "run": str(tmp_path), "pid": supervisor.pid,
        "start": managed.process_identity(supervisor.pid),
        "server_pid": -1, "server_start": "gone"})
    monkeypatch.setattr(managed, "STOP_TIMEOUT", 1)
    managed.stop(path, "fixture")
    assert supervisor.wait(timeout=5) == -signal.SIGKILL
    assert engine.wait(timeout=5) == -signal.SIGKILL
    assert not path.exists()


@pytest.mark.parametrize("left,outcome", [
    ("nothing", None),
    ("engine", "processes remain"),
    ("other", "ownership changed"),
])
def test_stop_when_another_stopper_finished_first(tmp_path, monkeypatch, stubborn,
                                                  left, outcome):
    supervisor = stubborn()
    path = tmp_path / "instance.json"
    state = {"id": "fixture", "run": str(tmp_path), "pid": supervisor.pid,
             "start": managed.process_identity(supervisor.pid)}
    if left == "engine":
        engine = stubborn()
        state.update(server_pid=-1, server_start="gone")
        _record_engine(tmp_path, -1, "gone", engine.pid)
    managed.write_state(path, state)

    def other_stopper(*_args):
        supervisor.kill()
        supervisor.wait()
        if left == "other":
            managed.write_state(path, {**state, "id": "newer"})
        else:
            path.unlink()
        return True

    monkeypatch.setattr(managed, "signal_and_wait", other_stopper)
    if outcome is None:
        managed.stop(path, "fixture")
        assert not path.exists()
    else:
        with pytest.raises(ValueError, match=outcome):
            managed.stop(path, "fixture")


# --- T19 stop-owned


def _main(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["strata_managed.py", *args])
    return managed.main()


@pytest.mark.parametrize("args", [("stop-owned",), ("stop-owned", "")])
def test_t19_stop_owned_needs_an_id(world, monkeypatch, args):
    managed.write_state(world.state, {"id": "fixture"})
    monkeypatch.setattr(managed, "stop", lambda *a: pytest.fail("stopped"))
    assert _main(monkeypatch, *args) == 2
    assert json.loads(world.state.read_text()) == {"id": "fixture"}


def test_t19_stop_owned_leaves_another_instance_and_no_state_alone(world, monkeypatch):
    assert _main(monkeypatch, "stop-owned", "fixture") == 0
    assert not world.state.exists()
    managed.write_state(world.state, {"id": "other"})
    monkeypatch.setattr(managed, "stop", lambda *a: pytest.fail("stopped"))
    assert _main(monkeypatch, "stop-owned", "fixture") == 0
    assert json.loads(world.state.read_text()) == {"id": "other"}


def test_t19_stop_owned_reports_a_surviving_process(world, monkeypatch, stubborn):
    engine = stubborn()
    _record_engine(world.root, -1, "gone", engine.pid)
    managed.write_state(world.state, {
        "id": "fixture", "run": str(world.root), "pid": -1, "start": None,
        "server_pid": -1, "server_start": "gone"})
    monkeypatch.setattr(managed, "STOP_TIMEOUT", 0)
    monkeypatch.setattr(managed, "signal_and_wait", lambda *args: False)
    assert _main(monkeypatch, "stop-owned", "fixture") == 2
    assert world.state.exists() and engine.poll() is None


# --- T17 serve_strata.sh


def _serve_world(tmp_path, *, reused, admit_rc, used_bytes):
    bin_ = tmp_path / "bin"
    calls = tmp_path / "calls.txt"
    started = tmp_path / "started"
    if reused:
        started.write_text("")
    _make_exec(bin_ / "python3", f'''
if [[ "$1" == *strata_managed.py ]]; then
  shift
  echo "$*" >> "{calls}"
  case "$1" in
    id) [ -f "{started}" ] && {{ echo the-id; exit 0; }}; exit 1 ;;
    start) : > "{started}"; echo "{tmp_path}/evidence.json" ;;
    admit) exit {admit_rc} ;;
  esac
  exit 0
fi
exec "{shutil.which('python3')}" "$@"''')
    _make_exec(bin_ / "nvtop", "echo '" + json.dumps([{
        "device_name": "Intel Arc DG2", "mem_used": used_bytes,
        "mem_total": 17179869184, "mem_free": 17179869184 - used_bytes}]) + "'")
    _make_exec(bin_ / "docker", "exit 0")
    gate = _make_exec(tmp_path / "gate.sh", "exit 0")
    key = tmp_path / "api.key"
    key.write_text("k\n")
    (tmp_path / "xdg").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("A770B_")}
    env.update(
        PATH=f"{bin_}:{os.environ['PATH']}", A770B_PROJECT=str(ROOT),
        A770B_DATA=str(tmp_path / "data"), A770B_HOST="127.0.0.1", A770B_PORT="9",
        A770B_VRAM_CAP_GIB="12", A770B_CARD_VRAM_TOTAL="16", A770B_GPU_MATCH="DG2",
        A770B_ALLOW_NO_NVTOP="1", A770B_MIN_AVAIL_MB="1", A770B_BUDGET_GATE=str(gate),
        A770B_API_KEY_FILE=str(key), A770B_DOCKER="docker", A770B_FRAMEWORK_PORTS="",
        A770B_SERVE="compose", A770B_COMPOSE_PROJECT="a770-builder", A770B_CARD="",
        XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    return env, calls


@pytest.mark.parametrize("failure", ["vram", "admit"])
@pytest.mark.parametrize("reused", [True, False])
def test_t17_a_failed_check_stops_only_a_started_instance(tmp_path, failure, reused):
    env, calls = _serve_world(
        tmp_path, reused=reused, admit_rc=1 if failure == "admit" else 0,
        used_bytes=15 * 1024 ** 3 if failure == "vram" else 0)
    got = subprocess.run(["bash", str(SERVE), "start", str(tmp_path / "c.json"), "8192"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert got.returncode == 3, got.stdout + got.stderr
    seen = calls.read_text().splitlines()
    assert ("stop the-id" in seen) is (not reused), seen
    assert ("reused Strata instance is left running" in got.stderr) is reused


# --- T17 qualify


def _strata_stops(tmp_path, body, *, listing, rc=0):
    job = tmp_path / "job"
    job.mkdir()
    (job / "cid").write_text("observed-only\n")
    if listing is not None:
        (job / "strata-owned.ids").write_text(listing)
    calls = tmp_path / "strata-calls.log"
    _make_exec(tmp_path / "fake-harness" / "serve_strata.sh",
               f'echo "$*" >> "{calls}"; exit {rc}')
    _job, compose, out = _functions(tmp_path, body, engine="strata")
    assert compose == []
    return calls.read_text().splitlines() if calls.exists() else [], out


@pytest.mark.parametrize("function", ["stop_server", "release_suite_server"])
@pytest.mark.parametrize("listing,expected", [
    (None, []),
    ("", []),
    ("one\ntwo\none\n", ["stop-owned one", "stop-owned two"]),
    ("one\ntwo", ["stop-owned two", "stop-owned one"]),
])
def test_t17_qualify_stops_only_the_listed_instances(tmp_path, function, listing,
                                                     expected):
    calls, out = _strata_stops(tmp_path, f'{function}; echo "rc=$?"', listing=listing)
    assert calls == expected and out.split() == ["rc=0"]


@pytest.mark.parametrize("function", ["stop_server", "release_suite_server"])
def test_a_failed_instance_stop_or_a_linked_list_is_a_failed_stop(tmp_path, function):
    calls, out = _strata_stops(tmp_path, f'{function}; echo "rc=$?"', listing="one\n",
                               rc=2)
    assert calls == ["stop-owned one"] and out.split() == ["rc=1"]
    other = tmp_path / "linked"
    other.mkdir()
    (other / "list").write_text("one\n")
    calls, out = _strata_stops(
        other, f'ln -s "{other}/list" "$job/strata-owned.ids"; {function}; echo "rc=$?"',
        listing=None)
    assert calls == [] and out.split() == ["rc=1"]


def test_a_strata_runner_creates_the_owned_list_and_stops_from_it(
        box, tmp_path, config):  # noqa: F811
    box = dict(box, A770B_DATA=str(tmp_path / "planted"))
    job = _queued_job(box, tmp_path)
    plan = json.loads((job / "plan.json").read_text())
    plan.update(engine="strata", backend="strata", gguf=False, artifact=str(config))
    (job / "plan.json").write_text(json.dumps(plan))
    hook = _make_exec(tmp_path / "pre.sh", 'printf "%s\\n" "$A770B_STRATA_OWNED_FILE" '
                      '> "$1/seen"; echo planted-id >> "$A770B_STRATA_OWNED_FILE"; exit 1')
    # The fixture's nvtop must not read this host's card.
    nvtop = Path(box["PATH"]) / "nvtop"
    nvtop.unlink(missing_ok=True)
    _make_exec(nvtop, "echo '[]'")
    env = dict(box, A770B_QUALIFY_PRE=str(hook), A770B_ALLOW_NO_NVTOP="1",
               A770B_ONEAPI_DEVICE_SELECTOR="level_zero:1",
               A770B_STRATA_ENGINE_SHA256=managed.file_digest(config.parent / "strata"))
    env.pop("A770B_QUALIFY_STUB")
    for stand_in in Path(env["PATH"]).glob("a770b-qualify-*"):
        stand_in.unlink()
    got = _run(env, ["run", "--job", str(job)])
    assert got.returncode == 4, got.stdout + got.stderr
    assert (job / "seen").read_text().strip() == str(job / "strata-owned.ids")
    status = json.loads((job / "status.json").read_text())
    assert status["reason"] == "pre-hook", status
    log = (job / "phases" / "stop-server.log").read_text()
    assert "serve_strata.sh stop-owned planted-id" in log
    assert "stop-id" not in log
    assert "Strata engine logs are not scanned" in (job / "watchdog.log").read_text()


# --- 3.7 the Phase 3 tracker


@pytest.mark.parametrize("current,published", [("mine", "4343"), ("other", "")])
def test_the_tracker_publishes_only_its_own_instance(tmp_path, current, published):
    calls = tmp_path / "asked.log"
    _make_exec(tmp_path / "fake-harness" / "serve_strata.sh",
               f'echo "$1" >> "{calls}"; case "$1" in id) echo {current} ;; '
               'pid) echo 4343 ;; esac')
    body = f'''
gpu_used_gib() {{ echo 1; }}
engine=strata
echo mine > "$job/cid"
start_observers
for _ in $(seq 100); do
  [ "$(grep -c '^id$' "{calls}" 2>/dev/null)" -ge 3 ] 2>/dev/null && break
  sleep 0.1
done
echo "published=$(cat "$job/evidence/serving.pid")"
echo "asked=$(grep -c '^id$' "{calls}")"
rm -f "$job/VRAM_SAMPLE"
: > "$job/SERVING_CLEARED"
kill "$TRACKER" "$VRAM_PID" "$MEM" 2>/dev/null
wait 2>/dev/null
'''
    _job, _calls, out = _functions(tmp_path, body, engine="strata")
    lines = dict(line.split("=", 1) for line in out.splitlines())
    assert int(lines["asked"]) >= 3 and lines["published"] == published


# --- T17 run_suite.sh cleanup


def _suite(tmp_path, body, *, stop_rc=0, **extra):
    calls = tmp_path / "strata-calls.log"
    bin_ = tmp_path / "bin"
    _make_exec(bin_ / "python3", f'''
if [[ "$1" == *strata_managed.py ]]; then shift; echo "$*" >> "{calls}"; exit {stop_rc}; fi
exec "{shutil.which('python3')}" "$@"''')
    got, ran = _suite_run(tmp_path, body, PATH=f"{bin_}:{os.environ['PATH']}", **extra)
    assert ran.exists(), got.stdout + got.stderr
    return got, calls.read_text().splitlines() if calls.exists() else []


def test_t17_the_suite_stops_the_callers_listed_instances_and_keeps_the_list(tmp_path):
    owned = tmp_path / "caller.ids"
    owned.write_text("early\n")
    _got, calls = _suite(
        tmp_path, 'printf "late\\nearly\\nlate\\n" >> "$A770B_STRATA_OWNED_FILE"',
        A770B_LADDER_RUN="1", A770B_STRATA_OWNED_FILE=str(owned))
    assert calls == ["stop-owned late", "stop-owned early"]
    assert owned.read_text().split() == ["early", "late", "early", "late"]


@pytest.mark.parametrize("stop_rc", [0, 2])
def test_t17_the_suite_removes_its_own_list_and_reports_a_failed_stop(tmp_path,
                                                                      stop_rc):
    seen = tmp_path / "seen"
    got, calls = _suite(
        tmp_path,
        f'echo "$A770B_STRATA_OWNED_FILE" > "{seen}"; '
        'echo mine >> "$A770B_STRATA_OWNED_FILE"',
        stop_rc=stop_rc, A770B_LADDER_RUN="1")
    listing = Path(seen.read_text().strip())
    assert listing.parent == tmp_path / "data" / "logs" and not listing.exists()
    assert calls == ["stop-owned mine"]
    assert ("could not stop a server" in got.stderr) is bool(stop_rc)


def test_t17_the_suite_stops_nothing_from_a_linked_list(tmp_path):
    other = tmp_path / "other"
    other.write_text("theirs\n")
    owned = tmp_path / "caller.ids"
    owned.write_text("")
    got, calls = _suite(
        tmp_path, f'rm -f "{owned}"; ln -s "{other}" "{owned}"',
        A770B_LADDER_RUN="1", A770B_STRATA_OWNED_FILE=str(owned))
    assert calls == [] and owned.is_symlink()
    assert "owned-instance list is a symlink" in got.stderr


def test_a_suite_outside_a_ladder_run_hands_no_list_to_its_stages(tmp_path):
    owned = tmp_path / "caller.ids"
    owned.write_text("theirs\n")
    seen = tmp_path / "seen"
    _got, calls = _suite(tmp_path, f'echo "[${{A770B_STRATA_OWNED_FILE:-}}]" > "{seen}"',
                         A770B_STRATA_OWNED_FILE=str(owned))
    assert seen.read_text().strip() == "[]" and calls == []
