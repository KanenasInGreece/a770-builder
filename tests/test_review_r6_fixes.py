#!/usr/bin/env python3
"""Offline regressions for the four findings of review round 6 (ram-profile-reporting).

1. Background samplers and the suite PID watcher must not keep the card/seat locks (fds 9/7) and must not outlive
   an owner that died without running its exit trap.
2. build_local.sh's diagnostic previews must not turn a successful builder into a SIGPIPE "interrupted" exit.
3. A stop-run whose interruption receipt cannot be written still stops the run from being graded.
4. The suite PID watcher samples only the container of the profile the builder actually selected.

Every fixture is a temporary directory with fake binaries; nothing touches a GPU, a container runtime or a live checkout.
"""

import fcntl
import json
import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
BUILD_LOCAL = ROOT / "harness" / "build_local.sh"
RUN_SUITE_SH = ROOT / "harness" / "run_suite.sh"
LADDER_SH = ROOT / "harness" / "ladder.sh"
PROFILE = "qwen35-9b-q4km-vulkan"


# build_local.sh asks --version and `run --help` before the real `opencode run`. The body stays the worker.
_OPENCODE_V1_PREFLIGHT = r"""# opencode-v1-preflight
case "${1:-}" in
  --version) printf '%s\n' '1.19.6'; exit 0 ;;
esac
if [ "${1:-}" = run ] && [ "${2:-}" = --help ]; then
  printf '%s\n' '  --dir   directory to run in'
  exit 0
fi
"""


def _with_opencode_v1(body: str) -> str:
    if body.lstrip().startswith("# opencode-v1-preflight"):
        return body
    return _OPENCODE_V1_PREFLIGHT + body


def _make_exec(path: Path, body: str) -> Path:
    if path.name == "opencode":
        body = _with_opencode_v1(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _base_env(tmp_path: Path, **extra) -> dict:
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    (data / "results").mkdir(parents=True, exist_ok=True)
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    key = tmp_path / "api.key"
    key.write_text("k-test\n", encoding="utf-8")
    key.chmod(0o600)
    # A passing suite records measured_tasks in the profiles file: always a copy, never the shipped registry.
    registry = tmp_path / "a770.display.json"
    registry.write_bytes((ROOT / "config" / "registry" / "a770.display.json").read_bytes())
    env = {k: v for k, v in os.environ.items() if not k.startswith("A770B_")}
    env.update(
        HOME=str(home),
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(data),
        A770B_REFUSE=str(tmp_path / "refused"),
        A770B_MODELS=str(models),
        A770B_ALLOW_NO_NVTOP="1",
        A770B_GPU_MATCH="DG2",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_MIN_AVAIL_MB="1",
        A770B_API_KEY_FILE=str(key),
        A770B_CARD_MODE="display",
        A770B_PROFILES_FILE=str(registry),
    )
    env.update(extra)
    return env


def _one_stage_suite(tmp_path: Path, stage_ids=("s1",)) -> Path:
    stages = []
    for sid in stage_ids:
        brief = tmp_path / f"{sid}.md"
        spec = tmp_path / f"{sid}.spec.json"
        brief.write_text(f"# {sid}\n", encoding="utf-8")
        spec.write_text("{}\n", encoding="utf-8")
        stages.append({"id": sid, "language": "python", "brief": str(brief), "spec": str(spec),
                       "grader": {"working": "true", "budget_lines": 50}})
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"suite": "R6", "kit_version": "1", "stages": stages}), encoding="utf-8")
    seat = tmp_path / "seat"
    seat.mkdir(exist_ok=True)
    (seat / "README.md").write_text("fixture kit seat\n", encoding="utf-8")
    return suite


def _git_seat(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return path


def _wait_for(predicate, timeout=10.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def _lock_free(path: Path) -> bool:
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _group_members(pgid: int) -> list:
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) > 2 and fields[0] != "Z" and int(fields[2]) == pgid:
            try:
                cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                cmd = "?"
            members.append((int(entry.name), cmd))
    return members


def _kill_owner_and_check_helpers(proc, worker_pid_file: Path, locks):
    """SIGKILL the owner shell (no exit trap runs), end its blocking worker, then expect free locks and no helpers."""
    worker = int(worker_pid_file.read_text(encoding="ascii").strip())
    try:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        os.kill(worker, signal.SIGKILL)
        assert _wait_for(lambda: all(_lock_free(lock) for lock in locks), timeout=5), (
            "a helper of the dead owner still holds a harness lock: %r" % _group_members(proc.pid))
        assert _wait_for(lambda: not _group_members(proc.pid), timeout=6), (
            "helpers outlived their dead owner: %r" % _group_members(proc.pid))
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ── finding 1 ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_suite_sampler_and_pid_watcher_release_locks_and_exit_when_the_owner_dies(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    started = tmp_path / "builder-started"
    worker_pid = tmp_path / "worker.pid"
    # The fake builder drops its own inherited lock descriptors so only the suite's helpers can still hold them.
    fake_lb = _make_exec(tmp_path / "fake-lb.sh", f'''exec 7>&- 9>&-
if [ "$1" = run ]; then echo "$$" > "{worker_pid}"; touch "{started}"; exec sleep 60; fi
exit 0''')
    env["A770B_LOCAL_BUILD"] = str(fake_lb)
    proc = subprocess.Popen(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        assert _wait_for(started.exists, timeout=20), "the suite never reached the builder"
        time.sleep(1.2)  # let the watcher and the monitor each run at least one iteration with the locks held
        logs = Path(env["A770B_DATA"]) / "logs"
        locks = [logs / "local-build.lock", *sorted((logs / "seat-locks").glob("*.lock"))]
        assert len(locks) == 2 and not any(_lock_free(lock) for lock in locks)
        _kill_owner_and_check_helpers(proc, worker_pid, locks)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def test_ladder_sampler_releases_the_card_lock_and_exits_when_the_owner_dies(tmp_path):
    env = _base_env(tmp_path)
    weight = tmp_path / "weights" / "bare-weight.gguf"
    weight.parent.mkdir()
    weight.write_bytes(b"not-a-weight")
    started = tmp_path / "serve-started"
    worker_pid = tmp_path / "worker.pid"
    fake_serve = _make_exec(tmp_path / "fake-serve.sh", f'''exec 7>&- 9>&-
if [ "$1" = start ]; then echo "$$" > "{worker_pid}"; touch "{started}"; exec sleep 60; fi
exit 0''')
    docker = _make_exec(tmp_path / "fake-docker.sh", "exit 99")
    ocbin = tmp_path / "ocbin"
    _make_exec(ocbin / "opencode", "exit 0")
    env.update(A770B_SERVE_SCRIPT=str(fake_serve), A770B_DOCKER=str(docker), A770B_CARD="a770",
               XDG_CONFIG_HOME=str(tmp_path / "xdg"), A770B_OPENCODE_BIN=str(ocbin),
               PATH=f"{ocbin}:{env['PATH']}")
    env.pop("A770B_PROFILES_FILE")
    proc = subprocess.Popen(["bash", str(LADDER_SH), str(weight), "--ctx", "16384"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        assert _wait_for(started.exists, timeout=20), "the ladder never reached the load rung"
        time.sleep(1.2)
        lock = Path(env["A770B_DATA"]) / "logs" / "local-build.lock"
        assert not _lock_free(lock)
        _kill_owner_and_check_helpers(proc, worker_pid, [lock])
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def test_host_memory_monitor_exits_when_its_owner_pid_is_gone(tmp_path):
    owner = subprocess.Popen(["sleep", "60"])
    out = tmp_path / "samples.jsonl"
    monitor = subprocess.Popen(["python3", str(ROOT / "harness" / "host_memory.py"), "monitor", "--out", str(out),
                                "--interval", "0.05", "--owner-pid", str(owner.pid)])
    try:
        assert _wait_for(lambda: out.exists() and out.stat().st_size > 0, timeout=5)
        assert monitor.poll() is None
        owner.kill()
        owner.wait(timeout=5)
        assert monitor.wait(timeout=5) == 0
    finally:
        for p in (owner, monitor):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)


# ── finding 2 ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_build_local_large_diagnostic_preview_keeps_the_builder_exit_status(tmp_path):
    env = _base_env(tmp_path)
    seat = _git_seat(tmp_path / "seat")
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    # A successful builder that leaves far more than a pipe buffer of names for the head -20 previews.
    _make_exec(stub / "opencode", '''python3 - <<'PY'
import os
for i in range(6000):
    open("generated-file-with-a-deliberately-long-name-to-overflow-the-pipe-buffer-%05d.txt" % i, "w").close()
PY
exit 0''')
    env.update(SANDBOX="0", PROFILE=PROFILE, PATH=f"{stub}:{env['PATH']}")
    for _ in range(3):  # SIGPIPE is timing-dependent; a few runs make the pre-fix failure reliable
        r = subprocess.run(["bash", str(BUILD_LOCAL), str(seat), "brief text"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
        assert "opencode exit=0" in r.stdout
        assert "files touched during the run" in r.stdout
        subprocess.run(["git", "-C", str(seat), "clean", "-qfdx"], check=True)


def test_build_local_still_propagates_a_real_signal_exit(tmp_path):
    env = _base_env(tmp_path)
    seat = _git_seat(tmp_path / "seat")
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    _make_exec(stub / "opencode", "kill -TERM $$")
    env.update(SANDBOX="0", PROFILE=PROFILE, PATH=f"{stub}:{env['PATH']}")
    r = subprocess.run(["bash", str(BUILD_LOCAL), str(seat), "brief text"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 143, (r.returncode, r.stdout, r.stderr)


# ── finding 3 ────────────────────────────────────────────────────────────────────────────────────────────────────


def _fake_lb_driving_real_build(tmp_path: Path, env: dict, *, stop_run: bool) -> Path:
    """A local-build stand-in that runs the REAL build_local.sh and stop-run, then mirrors run's exit contract."""
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    # The worker handles TERM and exits 0: only an independent stop channel can tell this from a finished run.
    _make_exec(stub / "opencode", f"trap 'kill $! 2>/dev/null; echo 0 > \"{tmp_path}/worker-exit\"; exit 0' TERM; sleep 60 & wait $!; exit 0")
    failbin = tmp_path / "failbin"
    _make_exec(failbin / "mktemp", "exit 1")  # the interruption receipt cannot be written
    stopper = ""
    if stop_run:
        stopper = f'''(
    for _ in $(seq 1 200); do [ -s "$A770B_DATA/logs/run.pid" ] && break; sleep 0.05; done
    sleep 0.3
    PATH="{failbin}:$PATH" bash "{LOCAL_BUILD}" stop-run > "{tmp_path}/stop-run.out" 2>&1
    echo "$?" > "{tmp_path}/stop-run.rc"
  ) &'''
    return _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  {stopper}
  SANDBOX=0 PROFILE={PROFILE} PATH="{stub}:$PATH" bash "{BUILD_LOCAL}" "$2" "brief" > "{tmp_path}/build.out" 2>&1
  brc=$?
  wait
  mkdir -p "$A770B_DATA/results"
  printf '# capture\\n' > "$A770B_DATA/results/fake-stage.task.md"
  printf 'diff --git a/x b/x\\n' > "$A770B_DATA/results/fake-stage.patch"
  echo "▶ capture: $A770B_DATA/results/fake-stage.task.md — captured"
  if [ "$brc" -ge 128 ] && [ "$brc" -le 159 ]; then exit "$brc"; fi
  exit 0
fi
if [ "$1" = verify ]; then touch "$A770B_DATA/verify-was-run"; exit 0; fi
exit 0''')


def test_stop_run_without_receipt_still_keeps_a_clean_worker_exit_from_being_graded(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    env["A770B_LOCAL_BUILD"] = str(_fake_lb_driving_real_build(tmp_path, env, stop_run=True))
    r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    build_out = (tmp_path / "build.out").read_text(encoding="utf-8", errors="replace")
    stop_out = (tmp_path / "stop-run.out").read_text(encoding="utf-8", errors="replace")
    assert (tmp_path / "stop-run.rc").read_text().strip() == "0", stop_out
    assert "could not persist the interruption receipt" in stop_out
    assert not (Path(env["A770B_DATA"]) / "logs" / "run.interrupted").exists()
    assert (tmp_path / "worker-exit").read_text().strip() == "0", build_out  # the worker really did exit cleanly
    assert r.returncode == 4, r.stdout + r.stderr
    assert not (Path(env["A770B_DATA"]) / "verify-was-run").exists()
    results = [p for p in (Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")]
    assert len(results) == 1
    doc = json.loads(results[0].read_text(encoding="utf-8"))
    assert doc["stages"][0]["outcome"] == "run_interrupted"
    assert doc["execution"]["completed_stage_ids"] == []


def test_a_clean_build_without_stop_run_is_still_graded(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    stub = tmp_path / "stubbin"
    fake_lb = _fake_lb_driving_real_build(tmp_path, env, stop_run=False)
    _make_exec(stub / "opencode", "exit 0")
    env["A770B_LOCAL_BUILD"] = str(fake_lb)
    r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert (Path(env["A770B_DATA"]) / "verify-was-run").exists(), r.stdout + r.stderr
    doc = json.loads(next((Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")).read_text(encoding="utf-8"))
    assert doc["stages"][0]["outcome"] != "run_interrupted"


# ── finding 4 ────────────────────────────────────────────────────────────────────────────────────────────────────


def _samples(log: Path) -> list:
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def test_suite_watcher_never_attributes_the_previous_models_container_during_a_profile_switch(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path, ("s1", "s2"))
    server = tmp_path / "llama-server"
    server.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n", encoding="utf-8")
    server.chmod(0o755)
    pids = tmp_path / "pids"
    pids.mkdir()
    old_cid, new_cid = "a" * 64, "b" * 64
    docker = _make_exec(tmp_path / "docker", f'''if [ "$1" = inspect ]; then
  cid="${{@: -1}}"; [ -f "{pids}/$cid" ] && cat "{pids}/$cid" && exit 0
  exit 1
fi
exit 1''')
    # Each stage: the previous model (same engine, other profile) is still the recorded container for a while,
    # then the builder's own profile is selected and its container recorded. Only the second may be sampled.
    fake_lb = _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  python3 "{server}" >/dev/null 2>&1 & echo "$!" > "{pids}/{old_cid}"
  printf '%s\\n' "{old_cid}" > "$A770B_DATA/logs/llamacpp-a770.cid"
  sleep 2.5
  kill "$(cat "{pids}/{old_cid}")"
  python3 "{server}" >/dev/null 2>&1 & echo "$!" > "{pids}/{new_cid}"
  printf '%s\\n' "{new_cid}" > "$A770B_DATA/logs/llamacpp-a770.cid"
  if [ -n "${{A770B_SERVING_SELECTION_FILE:-}}" ]; then
    printf '{{"profile":"{PROFILE}","served_profile":"{PROFILE}","engine":"llama.cpp","cid":"{new_cid}"}}\\n' \\
      > "$A770B_SERVING_SELECTION_FILE"
  fi
  sleep 2.5
  mkdir -p "$A770B_DATA/results"
  printf '# capture\\n' > "$A770B_DATA/results/fake-$$.task.md"
  printf 'diff --git a/x b/x\\n' > "$A770B_DATA/results/fake-$$.patch"
  echo "▶ capture: $A770B_DATA/results/fake-$$.task.md — captured"
  kill "$(cat "{pids}/{new_cid}")"
  exit 0
fi
exit 0''')
    env.update(A770B_LOCAL_BUILD=str(fake_lb), A770B_DOCKER=str(docker))
    old_pids, new_pids = set(), set()
    proc = subprocess.Popen(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        # Record every PID the old and new containers had across both stages.
        while proc.poll() is None:
            for path, bucket in ((pids / old_cid, old_pids), (pids / new_cid, new_pids)):
                try:
                    bucket.add(int(path.read_text(encoding="ascii").strip()))
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        output = proc.stdout.read()
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    assert proc.returncode == 0, output
    doc = json.loads(next(p for p in (Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")
                          if not p.name.endswith(".attachment.json")).read_text(encoding="utf-8"))
    targets = [(s.get("target") or {}).get("pid") for s in _samples(Path(doc["execution"]["host_memory_log"]))]
    sampled = {t for t in targets if t}
    assert old_pids and new_pids
    assert not (sampled & old_pids), "the previous model's container was attributed to the builder"
    assert sampled & new_pids, "the selected builder container was never sampled"


def _publish_selection(tmp_path: Path, env: dict, *, engine: str = "llama.cpp") -> dict:
    """The receipt carries what _verify_served_selection proved (SERVED_VERIFIED_*), never the requested name."""
    out = tmp_path / "selection.json"
    cid = "c" * 64
    r = subprocess.run(["bash", "-c", f'. "{LOCAL_BUILD}"; A770B_SERVED_ENGINE="{engine}"; '
                        f'SERVED_VERIFIED_PROFILE="{PROFILE}"; SERVED_VERIFIED_CID="{cid if engine != "strata" else ""}"; '
                        f'SERVED_VERIFIED_PID=4242; _publish_serving_selection "{PROFILE}"'],
                       cwd=ROOT, env=dict(env, A770B_SERVING_SELECTION_FILE=str(out)),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_local_build_publishes_the_selected_profile_and_container_only_after_serve(tmp_path):
    env = _base_env(tmp_path)
    assert _publish_selection(tmp_path, env) == {
        "profile": PROFILE, "served_profile": PROFILE, "engine": "llama.cpp", "cid": "c" * 64, "pid": "4242",
        "verified": True}
    assert _publish_selection(tmp_path, env, engine="strata")["cid"] == ""
    # A reload that rolled back serves the OLD profile: verification refuses it, so no receipt and no dispatch.
    r = subprocess.run(["bash", "-c", f'. "{LOCAL_BUILD}"; A770B_SERVED_ENGINE=llama.cpp; '
                        f'A770B_SERVE_ROLLED_BACK_TO=other-profile; _verify_served_selection "{PROFILE}"'],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "rolled back to other-profile" in r.stderr, r.stdout + r.stderr
    source = LOCAL_BUILD.read_text(encoding="utf-8")
    run = source[source.index("  run)\n"):source.index("  verify)\n")]
    assert run.index('serve "$profile" || exit $?') < run.index('_verify_served_selection "$profile"')
    assert run.index('_verify_served_selection "$profile"') < run.index('_publish_serving_selection "$profile"')
    assert run.index('_publish_serving_selection "$profile"') < run.index('bash "$BUILD"')
