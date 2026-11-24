#!/usr/bin/env python3
"""Offline regressions for the three findings left open after review round 7 (ram-profile-reporting).

1. stop-run: with the interruption receipt unwritable AND the run's owner undiscoverable (no ppid, ppid 1, or a
   foreign parent), a worker that handles TERM and exits 0 must still not be graded. The run's process-group
   leader (harness/run_lead.py) carries the stop itself, so the intent needs neither a file nor a process lookup.
2. The cold-candidate cgroup-sample test must run against an isolated registry copy, never config/registry.
3. A770B_SERVING_PID must not let a process outside the selected managed container be attributed to the builder.

Every fixture is a temporary directory with fake binaries; nothing touches a GPU, a container runtime or a live checkout.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_review_r6_fixes import (  # noqa: E402 — shared offline fixtures
    BUILD_LOCAL, LOCAL_BUILD, PROFILE, ROOT, RUN_SUITE_SH, _base_env, _make_exec, _one_stage_suite, _wait_for,
    _with_opencode_v1,
)

RUN_LEAD = ROOT / "harness" / "run_lead.py"
GUARD_SH = ROOT / "harness" / "guard.sh"
REAL_PS = shutil.which("ps") or "/usr/bin/ps"


def _registry_snapshot() -> dict:
    """Every entry of config/registry, hidden ones included, by name and content digest."""
    reg = ROOT / "config" / "registry"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
            for p in sorted(reg.iterdir())}


# ── finding 1: the run leader ────────────────────────────────────────────────────────────────────────────────────


def _lead(tmp_path: Path, worker_body: str, *lead_args: str) -> subprocess.Popen:
    worker = _make_exec(tmp_path / "worker.sh", worker_body)
    return subprocess.Popen([sys.executable, str(RUN_LEAD), *lead_args, "--", str(worker)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)


def _finish(proc: subprocess.Popen, timeout=15):
    try:
        out, _ = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    return proc.returncode, out


def test_run_lead_reports_a_stop_even_when_the_worker_handles_term_and_exits_0(tmp_path):
    marker = tmp_path / "worker-exit"
    proc = _lead(tmp_path, f'''trap 'kill $! 2>/dev/null; echo 0 > "{marker}"; exit 0' TERM
touch "{tmp_path}/started"; sleep 30 & wait $!; exit 0''')
    assert _wait_for((tmp_path / "started").exists, timeout=10)
    os.kill(proc.pid, signal.SIGTERM)          # the leader alone, as stop-run does when it cannot read the group
    rc, out = _finish(proc)
    assert marker.read_text().strip() == "0", out   # the worker really did exit cleanly
    assert rc == 143, out
    assert "stop" in out.lower()


@pytest.mark.parametrize("body,expected", [("exit 0", 0), ("exit 3", 3), ("kill -TERM $$", 143), ("kill -KILL $$", 137)])
def test_run_lead_otherwise_passes_the_worker_status_through(tmp_path, body, expected):
    rc, out = _finish(_lead(tmp_path, body))
    assert rc == expected, out


def test_run_lead_is_a_caller_fence_with_timeouts_exit_convention(tmp_path):
    t0 = time.monotonic()
    rc, out = _finish(_lead(tmp_path, "trap 'exit 0' TERM; sleep 30 & wait $!; exit 0", "--timeout", "1"))
    assert rc == 124, out                      # a fence expiry is not a stop: GNU timeout's 124, whatever the worker did
    assert time.monotonic() - t0 < 10
    rc, out = _finish(_lead(tmp_path, "exit 5", "--timeout", "30"))
    assert rc == 5, out


def test_run_lead_refuses_a_malformed_command_line(tmp_path):
    for args in ([], ["--timeout"], ["--timeout", "x", "--", "true"], ["true"]):
        r = subprocess.run([sys.executable, str(RUN_LEAD), *args], capture_output=True, text=True, timeout=10)
        assert r.returncode == 125, (args, r.stdout, r.stderr)
    r = subprocess.run([sys.executable, str(RUN_LEAD), "--", str(tmp_path / "no-such-binary")],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 127


def _run_pid_alive(env: dict, pidfile: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{ROOT}/harness/env.sh"; . "{GUARD_SH}"; run_pid_alive "{pidfile}"'],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def test_run_pid_alive_accepts_only_this_projects_run_lead(tmp_path):
    # build_local.sh execs `python3`, so the leader's comm is python3; the impostor is a byte-identical copy elsewhere.
    env = _base_env(tmp_path)
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    impostor_dir = tmp_path / "elsewhere" / "harness"
    impostor_dir.mkdir(parents=True)
    impostor = impostor_dir / "run_lead.py"
    impostor.write_bytes(RUN_LEAD.read_bytes())
    ours = subprocess.Popen(["python3", str(RUN_LEAD), "--", "bash", "-c", "sleep 30", "sandbox_run.sh"],
                            start_new_session=True)
    theirs = subprocess.Popen(["python3", str(impostor), "--", "bash", "-c", "sleep 30", "sandbox_run.sh"],
                              start_new_session=True)
    try:
        time.sleep(0.3)
        pidfile.write_text(f"{ours.pid}\n", encoding="ascii")
        r = _run_pid_alive(env, pidfile)
        assert r.returncode == 0 and r.stdout.strip() == str(ours.pid), r.stdout + r.stderr
        pidfile.write_text(f"{theirs.pid}\n", encoding="ascii")
        r = _run_pid_alive(env, pidfile)
        assert r.returncode != 0 and not r.stdout.strip(), r.stdout + r.stderr
    finally:
        for p in (ours, theirs):
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)


def _failbin(tmp_path: Path, owner_lookup: str, foreign_pid: int = 0) -> Path:
    """mktemp always fails (no receipt); ps cannot name the run's owner in the way `owner_lookup` says."""
    failbin = tmp_path / "failbin"
    _make_exec(failbin / "mktemp", "exit 1")
    if owner_lookup == "ps_fails":
        _make_exec(failbin / "ps", "exit 1")
    else:
        ppid = "1" if owner_lookup == "orphaned" else str(foreign_pid)
        _make_exec(failbin / "ps", f'case "$*" in *ppid=*) echo "{ppid}"; exit 0;; esac\nexec "{REAL_PS}" "$@"')
    return failbin


def test_stop_run_refuses_a_leader_that_cannot_carry_the_stop_when_no_channel_records_it(tmp_path):
    env = _base_env(tmp_path)
    pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
    failbin = _failbin(tmp_path, "ps_fails")
    legacy = subprocess.Popen(["timeout", "30", "bash", "-c", "true sandbox_run.sh; sleep 30"], start_new_session=True)
    try:
        time.sleep(0.2)
        pidfile.write_text(f"{legacy.pid}\n", encoding="ascii")
        r = subprocess.run(["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT,
                           env=dict(env, PATH=f"{failbin}:{env['PATH']}"), capture_output=True, text=True, timeout=60)
        assert r.returncode != 0, r.stdout + r.stderr
        assert "nothing was signalled" in r.stderr
        assert legacy.poll() is None, "a stop nobody could record was still sent"
    finally:
        os.killpg(legacy.pid, signal.SIGKILL)
        legacy.wait(timeout=5)


def _fake_lb(tmp_path: Path, failbin: Path, fence: str) -> Path:
    """A local-build stand-in: the REAL build_local.sh and the REAL stop-run, then run's exit contract."""
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    marker = tmp_path / "worker-exit"
    _make_exec(stub / "opencode", _with_opencode_v1(
        f"trap 'kill $! 2>/dev/null; echo 0 > \"{marker}\"; exit 0' TERM; sleep 60 & wait $!; exit 0"))
    fence_env = f'LOCAL_TIMEOUT="{fence}"' if fence else ""
    return _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  (
    for _ in $(seq 1 200); do [ -s "$A770B_DATA/logs/run.pid" ] && break; sleep 0.05; done
    sleep 0.5
    PATH="{failbin}:$PATH" bash "{LOCAL_BUILD}" stop-run > "{tmp_path}/stop-run.out" 2>&1
    echo "$?" > "{tmp_path}/stop-run.rc"
  ) &
  env -u LOCAL_TIMEOUT {fence_env} SANDBOX=0 PROFILE={PROFILE} PATH="{stub}:$PATH" bash "{BUILD_LOCAL}" "$2" "brief" > "{tmp_path}/build.out" 2>&1
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


@pytest.mark.parametrize("fence", ["", "60"], ids=["no-clock", "caller-fence"])
@pytest.mark.parametrize("owner_lookup", ["ps_fails", "orphaned", "foreign"])
def test_stop_run_without_receipt_or_owner_still_keeps_a_clean_worker_exit_from_being_graded(tmp_path, owner_lookup, fence):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    foreign = subprocess.Popen(["sleep", "60"])  # a live parent that is not build_local.sh: must never be signalled
    try:
        failbin = _failbin(tmp_path, owner_lookup, foreign.pid)
        env["A770B_LOCAL_BUILD"] = str(_fake_lb(tmp_path, failbin, fence))
        r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        build_out = (tmp_path / "build.out").read_text(encoding="utf-8", errors="replace")
        stop_out = (tmp_path / "stop-run.out").read_text(encoding="utf-8", errors="replace")
        assert (tmp_path / "stop-run.rc").read_text().strip() == "0", stop_out
        assert "could not persist the interruption receipt" in stop_out, stop_out
        assert "was told directly" not in stop_out, stop_out   # no owner was found to tell
        assert not (Path(env["A770B_DATA"]) / "logs" / "run.interrupted").exists()
        assert (tmp_path / "worker-exit").read_text().strip() == "0", build_out  # the worker handled TERM, exited 0
        assert foreign.poll() is None, "stop-run signalled a process that is not the run's owner"
        assert r.returncode == 4, r.stdout + r.stderr
        assert not (Path(env["A770B_DATA"]) / "verify-was-run").exists()
        results = list((Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json"))
        assert len(results) == 1
        doc = json.loads(results[0].read_text(encoding="utf-8"))
        assert doc["stages"][0]["outcome"] == "run_interrupted"
        assert doc["execution"]["completed_stage_ids"] == []
    finally:
        foreign.kill()
        foreign.wait(timeout=5)


def test_build_local_makes_run_lead_the_process_group_leader_in_every_branch():
    source = BUILD_LOCAL.read_text(encoding="utf-8")
    assert "exec timeout" not in source
    run = source[source.index("exec_lead(){"):source.index("rc=${PIPESTATUS[0]}")]
    assert run.count("&& exec_lead ") == 2          # the sandboxed and the SANDBOX=0 branch
    assert '$A770B_PROJECT/harness/run_lead.py' in run
    assert '--timeout "$LOCAL_TIMEOUT"' in run


# ── finding 2: the cold-candidate test never touches the tracked registry ────────────────────────────────────────


def test_candidate_cgroup_sample_test_leaves_the_tracked_registry_byte_identical(tmp_path):
    import test_review_v059_fixes as v059
    before = _registry_snapshot()
    v059.test_candidate_cold_generation_gets_verified_serving_cgroup_sample(tmp_path)
    assert _registry_snapshot() == before
    assert not (ROOT / "config" / "registry" / ".a770.display.json.lock").exists()


# ── finding 3: A770B_SERVING_PID cannot move the sample off the selected container ───────────────────────────────


def _serving_pid_suite(tmp_path: Path, *, override: str) -> tuple:
    """One stage whose selected container's main pid is `selected` (with a child server); A770B_SERVING_PID names
    either a stray llama-server outside that container or the selected server's own child."""
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    server = tmp_path / "llama-server"
    server.write_text('''#!/usr/bin/env python3
import subprocess, sys, time
if len(sys.argv) > 2 and sys.argv[1] == "parent":
    child = subprocess.Popen([sys.argv[0], "child"])
    open(sys.argv[2], "w").write(str(child.pid))
time.sleep(30)
''', encoding="utf-8")
    server.chmod(0o755)
    pids = tmp_path / "pids"
    pids.mkdir()
    cid = "d" * 64
    selected = subprocess.Popen([str(server), "parent", str(pids / "child")])
    stray = subprocess.Popen([str(server)])  # a live llama-server that is NOT the selected container's
    try:
        assert _wait_for(lambda: (pids / "child").exists() and (pids / "child").read_text().strip(), timeout=10)
        child = int((pids / "child").read_text())
        (pids / "selected").write_text(str(selected.pid))
        docker = _make_exec(tmp_path / "docker", f'if [ "$1" = inspect ]; then cat "{pids}/selected"; exit 0; fi\nexit 1')
        fake_lb = _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  printf '%s\\n' "{cid}" > "$A770B_DATA/logs/llamacpp-a770.cid"
  printf '{{"profile":"{PROFILE}","served_profile":"{PROFILE}","engine":"llama.cpp","cid":"{cid}"}}\\n' \\
    > "$A770B_SERVING_SELECTION_FILE"
  sleep 2.5
  mkdir -p "$A770B_DATA/results"
  printf '# capture\\n' > "$A770B_DATA/results/fake-$$.task.md"
  printf 'diff --git a/x b/x\\n' > "$A770B_DATA/results/fake-$$.patch"
  echo "▶ capture: $A770B_DATA/results/fake-$$.task.md — captured"
  exit 0
fi
exit 0''')
        env.update(A770B_LOCAL_BUILD=str(fake_lb), A770B_DOCKER=str(docker),
                   A770B_SERVING_PID=str({"stray": stray.pid, "child": child}[override]))
        r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, r.stdout + r.stderr
        doc = json.loads(next(p for p in (Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")
                              if not p.name.endswith(".attachment.json")).read_text(encoding="utf-8"))
        samples = [json.loads(line) for line in Path(doc["execution"]["host_memory_log"]).read_text().splitlines()
                   if line.strip()]
        sampled = {(s.get("target") or {}).get("pid") for s in samples} - {None}
        return r, sampled, stray.pid, selected.pid, child
    finally:
        for p in (stray, selected):
            p.kill()
            p.wait(timeout=5)
        try:
            os.kill(int((pids / "child").read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_serving_pid_override_outside_the_selected_container_is_never_attributed(tmp_path):
    r, sampled, stray, selected, _child = _serving_pid_suite(tmp_path, override="stray")
    assert stray not in sampled, "A770B_SERVING_PID attributed a process outside the selected container"
    assert selected not in sampled, "a refused override silently fell back to another pid"
    assert "A770B_SERVING_PID" in r.stderr  # the operator is told why the override was not used


def test_serving_pid_override_naming_a_process_inside_the_selected_container_is_sampled(tmp_path):
    _r, sampled, stray, selected, child = _serving_pid_suite(tmp_path, override="child")
    assert child in sampled, "an override inside the selected container was not used"
    assert stray not in sampled and selected not in sampled
