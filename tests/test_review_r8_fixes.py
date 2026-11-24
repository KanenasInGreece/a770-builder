#!/usr/bin/env python3
"""Offline regressions for the six findings of review round 8 (ram-profile-reporting).

1. A stop must stay distinct from the caller's fence: an operator TERM that coalesces with the fence's own TERM
   broadcast must still be recorded as a stop, and the stage must not be graded (also through run_suite).
2. serve must not reuse a container serving another profile of the same weights/backend/image, and `run` must not
   dispatch (or the suite grade) when the server actually up is not the requested profile. The selection receipt
   names the verified profile and container.
3. stop-run notifies only a positively verified build_local.sh owner that supports the USR1 protocol.
4. A stop that arrives while the worker is being started reaches the worker; the leader does not wait it out.
5. `run --timeout 0` stays unlimited, as GNU timeout treated a zero duration.
6. Strata's observed pinned host allocation survives evidence that has no host_memory_settings block, and is
   labelled as measured, apart from the configured parameters.

Every fixture is a temporary directory with fake binaries; nothing touches a GPU, a container runtime, the tracked
registry or a live checkout.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path

import pytest

import test_review_r7_fixes as r7
from test_reload import _Health, _registry_with_sycl, _write_models, live_pid  # noqa: F401 — live_pid is a fixture
from test_review_r6_fixes import (
    BUILD_LOCAL, LOCAL_BUILD, PROFILE, ROOT, RUN_SUITE_SH, _base_env, _git_seat, _make_exec, _one_stage_suite,
    _wait_for, _with_opencode_v1,
)

RUN_LEAD = ROOT / "harness" / "run_lead.py"
LADDER_ROW = ROOT / "harness" / "ladder_row.py"
sys.path.insert(0, str(ROOT / "harness"))
import host_memory  # noqa: E402
import strata_managed as managed  # noqa: E402


def _registry_snapshot() -> dict:
    reg = ROOT / "config" / "registry"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
            for p in sorted(reg.iterdir())}


@pytest.fixture(autouse=True)
def _tracked_registry_untouched():
    before = _registry_snapshot()
    yield
    assert _registry_snapshot() == before, "a test changed config/registry"


def _finish(proc: subprocess.Popen, timeout=20):
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate(timeout=5)
        return None, out
    return proc.returncode, out


def _launch(tmp_path: Path, launcher_src: str, *lead_args: str) -> subprocess.Popen:
    launcher = tmp_path / "launcher.py"
    launcher.write_text(launcher_src, encoding="utf-8")
    return subprocess.Popen([sys.executable, str(launcher), str(RUN_LEAD), *lead_args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)


# ── finding 1: a stop that lands together with the fence's broadcast is still a stop ────────────────────────────

# The leader runs unchanged; only os.killpg is wrapped. When the fence forwards its TERM, the operator's stop TERM
# is made pending at the same instant (both blocked), so the kernel delivers whatever is pending as one signal.
COALESCE_LAUNCHER = r'''
import os, runpy, signal, sys
lead = sys.argv[1]
real_killpg = os.killpg
fired = []
def killpg(pgid, sig):
    if sig == signal.SIGTERM and not fired:
        fired.append(1)
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            os.kill(os.getpid(), signal.SIGTERM)       # the operator's stop, concurrent with the fence
            return real_killpg(pgid, sig)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
    return real_killpg(pgid, sig)
os.killpg = killpg
sys.argv = sys.argv[1:]
runpy.run_path(lead, run_name="__main__")
'''


def test_a_stop_coalesced_with_the_fence_broadcast_is_reported_as_a_stop_not_a_timeout(tmp_path):
    marker = tmp_path / "worker-exit"
    worker = _make_exec(tmp_path / "worker.sh",
                        f"trap 'kill $! 2>/dev/null; echo 0 > \"{marker}\"; exit 0' TERM; sleep 30 & wait $!; exit 0")
    rc, out = _finish(_launch(tmp_path, COALESCE_LAUNCHER, "--timeout", "0.5", "--", str(worker)))
    assert rc == 143, out                        # the stop wins; 124 would let the suite grade a stopped run
    assert "fence expired" not in out, out
    assert marker.read_text().strip() == "0", out


def test_the_fence_never_signals_the_leaders_own_process_group(tmp_path):
    """The fence's TERM goes to the worker's own group, so no TERM the leader receives is its own."""
    seen = tmp_path / "killpg.log"
    launcher = r'''
import os, runpy, signal, sys
lead = sys.argv[1]
real_killpg = os.killpg
def killpg(pgid, sig):
    with open(%r, "a") as fh:
        fh.write("%%d %%d %%d\n" %% (pgid, sig, os.getpgrp()))
    return real_killpg(pgid, sig)
os.killpg = killpg
sys.argv = sys.argv[1:]
runpy.run_path(lead, run_name="__main__")
''' % str(seen)
    worker = _make_exec(tmp_path / "worker.sh", "sleep 30 & wait $!")
    rc, out = _finish(_launch(tmp_path, launcher, "--timeout", "0.3", "--", str(worker)))
    assert rc == 124, out
    calls = [tuple(int(x) for x in line.split()) for line in seen.read_text().splitlines()]
    assert calls, out
    assert all(pgid != own for pgid, _sig, own in calls), calls


# The integration: the REAL build_local.sh and the REAL stop-run, with neither the receipt nor the owner channel
# available. A sitecustomize on PYTHONPATH (active only inside run_lead.py) starts stop-run at the very moment the
# fence forwards its TERM and holds that forward until stop-run's TERM is pending, so both arrive together.
STOP_AT_FENCE_SITE = r'''
import os, signal, subprocess, sys, time
if sys.argv and os.path.basename(sys.argv[0]) == "run_lead.py" and os.environ.get("R8_STOP_AT_FENCE"):
    _cfg = os.environ["R8_STOP_AT_FENCE"].split(os.pathsep)
    _local_build, _failbin, _out = _cfg
    _real = os.killpg
    _fired = []
    def _term_pending():
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("ShdPnd:"):
                    return bool(int(line.split()[1], 16) & (1 << (signal.SIGTERM - 1)))
        return False
    def killpg(pgid, sig):
        if sig != signal.SIGTERM or _fired:
            return _real(pgid, sig)
        _fired.append(1)
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            env = dict(os.environ, PATH=_failbin + os.pathsep + os.environ["PATH"])
            env.pop("R8_STOP_AT_FENCE", None)
            subprocess.Popen(["bash", "-c", 'bash "$1" stop-run > "$2" 2>&1; echo $? > "$2.rc"', "x",
                              _local_build, _out], env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            while not _term_pending() and time.monotonic() < deadline:
                time.sleep(0.005)
            return _real(pgid, sig)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
    os.killpg = killpg
'''


def test_stop_racing_the_fence_without_receipt_or_owner_is_not_graded_by_the_suite(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(STOP_AT_FENCE_SITE, encoding="utf-8")
    failbin = r7._failbin(tmp_path, "ps_fails")
    stop_out = tmp_path / "stop-run.out"
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", "exit 0")
    marker = tmp_path / "worker-exit"
    _make_exec(stub / "opencode", _with_opencode_v1(
        f"trap 'kill $! 2>/dev/null; echo 0 > \"{marker}\"; exit 0' TERM; sleep 60 & wait $!; exit 0"))
    fake_lb = _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  env -u LOCAL_TIMEOUT LOCAL_TIMEOUT=1 SANDBOX=0 PROFILE={PROFILE} PATH="{stub}:$PATH" \\
    PYTHONPATH="{site}" R8_STOP_AT_FENCE="{LOCAL_BUILD}:{failbin}:{stop_out}" \\
    bash "{BUILD_LOCAL}" "$2" "brief" > "{tmp_path}/build.out" 2>&1
  brc=$?
  mkdir -p "$A770B_DATA/results"
  printf '# capture\\n' > "$A770B_DATA/results/fake-stage.task.md"
  printf 'diff --git a/x b/x\\n' > "$A770B_DATA/results/fake-stage.patch"
  echo "▶ capture: $A770B_DATA/results/fake-stage.task.md — captured"
  if [ "$brc" -ge 128 ] && [ "$brc" -le 159 ]; then exit "$brc"; fi
  exit 0
fi
if [ "$1" = verify ]; then touch "$A770B_DATA/verify-was-run"; exit 0; fi
exit 0''')
    env["A770B_LOCAL_BUILD"] = str(fake_lb)
    r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    build_out = (tmp_path / "build.out").read_text(encoding="utf-8", errors="replace")
    assert _wait_for(Path(f"{stop_out}.rc").exists, timeout=40), build_out
    stop_text = stop_out.read_text(encoding="utf-8", errors="replace")
    assert Path(f"{stop_out}.rc").read_text().strip() == "0", stop_text
    assert "could not persist the interruption receipt" in stop_text, stop_text
    assert "was told directly" not in stop_text, stop_text
    assert marker.read_text().strip() == "0", build_out     # the worker handled the TERM and exited 0
    assert "fence expired" not in build_out, build_out
    assert not (Path(env["A770B_DATA"]) / "verify-was-run").exists(), "a stopped run was verified"
    assert r.returncode == 4, r.stdout + r.stderr
    doc = json.loads(next(p for p in (Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")
                          if not p.name.endswith(".attachment.json")).read_text(encoding="utf-8"))
    assert doc["stages"][0]["outcome"] == "run_interrupted"
    assert doc["execution"]["completed_stage_ids"] == []


# ── finding 2: the served profile is verified before anything is dispatched ──────────────────────────────────────

RAM24 = "qwen35-9b-q4km-vulkan-ram24"
BASE = "qwen35-9b-q4km-vulkan"
QWEN = "Qwen3.5-9B-Q4_K_M.gguf"
CID_OLD = "a" * 64
CID_NEW = "b" * 64

# A compose stand-in that keeps what serve_compose.sh keeps: the pidfile, the container id and the live sidecar.
SERVE_STUB = r'''#!/usr/bin/env bash
logs="$A770B_DATA/logs"
case "$1" in
  start)
    printf 'start profile=%s\n' "${A770B_LIVE_PROFILE-}" >> "$SERVE_LOG"
    [ "${A770B_LIVE_PROFILE-}" != "${SERVE_FAIL_PROFILE-}" ] || exit 1
    n=$(cat "$SERVE_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$SERVE_COUNT"
    cid="$SERVE_CID_NEW"
    printf '%s\n' "$SERVE_LIVE_PID" > "$logs/llamacpp-a770.pid"
    printf '%s\n' "$cid" > "$logs/llamacpp-a770.cid"
    printf '%s\n' "$cid" > "$SERVE_RUNNING_CID"
    python3 "$A770B_PROJECT/harness/live_backend.py" write --path "$logs/live-backend.json" --card "$A770B_LIVE_CARD" \
      --backend "$A770B_LIVE_BACKEND" --mode "$A770B_LIVE_MODE" --model "$(basename "$2")" \
      --profile "$A770B_LIVE_PROFILE" --pid "$SERVE_LIVE_PID"
    exit 0 ;;
  stop) echo stop >> "$SERVE_LOG"; rm -f "$logs/live-backend.json" "$logs/llamacpp-a770.cid" "$SERVE_RUNNING_CID"; exit 0 ;;
  health) echo ready; exit 0 ;;
  *) echo "$1" >> "$SERVE_LOG"; exit 0 ;;
esac
'''

DOCKER_STUB = r'''#!/usr/bin/env bash
case " $* " in
  *" ps -q "*) cat "$SERVE_RUNNING_CID" 2>/dev/null; exit 0 ;;
  *" image inspect "*) echo sha256:same; exit 0 ;;
  *" inspect "*)
    case " $* " in
      *.Image*) echo sha256:same ;;
      *State.Pid*) echo "$SERVE_LIVE_PID" ;;
      *.Id*) cat "$SERVE_RUNNING_CID" 2>/dev/null ;;
      *config_files*) echo "$A770B_COMPOSE_FILE" ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
'''


def _profile_env(tmp_path: Path, live: int, *, fail_profile: str = "") -> dict:
    """A registry copy with two profiles of the same weights, backend and image (a RAM tier apart), a container that
    is up serving BASE, and a /v1/models that answers — everything the reuse path used to accept."""
    env = _base_env(tmp_path)
    registry = _registry_with_sycl(tmp_path / "profiles.json")
    data = json.loads(registry.read_text(encoding="utf-8"))
    row = json.loads(json.dumps(data["profiles"][BASE]))
    row["ram_gb_extra"] = 24
    data["profiles"][RAM24] = row
    registry.write_text(json.dumps(data), encoding="utf-8")
    _write_models(Path(env["A770B_MODELS"]), registry)
    logs = Path(env["A770B_DATA"]) / "logs"
    running = tmp_path / "running.cid"
    running.write_text(CID_OLD + "\n", encoding="ascii")
    (logs / "llamacpp-a770.cid").write_text(CID_OLD + "\n", encoding="ascii")
    (logs / "llamacpp-a770.pid").write_text(f"{live}\n", encoding="ascii")
    subprocess.run([sys.executable, str(ROOT / "harness" / "live_backend.py"), "write",
                    "--path", str(logs / "live-backend.json"), "--card", "a770", "--backend", "vulkan",
                    "--mode", "display", "--model", QWEN, "--profile", BASE, "--pid", str(live)], check=True)
    serve = _make_exec(tmp_path / "serve-stub.sh", SERVE_STUB.split("\n", 1)[1])
    docker = _make_exec(tmp_path / "docker", DOCKER_STUB.split("\n", 1)[1])
    env.update(A770B_PROFILES_FILE=str(registry), A770B_SERVE="compose", A770B_HOST="127.0.0.1",
               A770B_LLAMA_IMAGE="example.invalid/llama:test", A770B_DOCKER=str(docker),
               A770B_COMPOSE_PROJECT="seat-test", SERVE=str(serve), SERVE_LOG=str(tmp_path / "serve.log"),
               SERVE_COUNT=str(tmp_path / "serve.count"), SERVE_RUNNING_CID=str(running),
               SERVE_CID_NEW=CID_NEW, SERVE_LIVE_PID=str(live), SERVE_FAIL_PROFILE=fail_profile)
    return env


class _Server:
    def __enter__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Health)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.httpd.server_address[1]

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _serve_log(env: dict) -> list:
    path = Path(env["SERVE_LOG"])
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_serve_does_not_reuse_a_container_serving_another_profile_of_the_same_weights(tmp_path, live_pid):
    env = _profile_env(tmp_path, live_pid)
    with _Server() as port:
        env["A770B_PORT"] = str(port)
        r = subprocess.run(["bash", str(LOCAL_BUILD), "serve", RAM24], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=60)
    assert "already up" not in r.stdout, r.stdout + r.stderr
    lines = _serve_log(env)
    assert "stop" in lines, lines
    assert f"start profile={RAM24}" in lines, lines


def test_serve_still_reuses_the_container_of_the_same_profile(tmp_path, live_pid):
    env = _profile_env(tmp_path, live_pid)
    with _Server() as port:
        env["A770B_PORT"] = str(port)
        r = subprocess.run(["bash", str(LOCAL_BUILD), "serve", BASE], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already up" in r.stdout, r.stdout + r.stderr
    assert not any(line.startswith("start ") for line in _serve_log(env))


def _run_cli(tmp_path: Path, env: dict, profile: str, *extra: str):
    seat = _git_seat(tmp_path / "seat")
    brief = tmp_path / "brief.md"
    brief.write_text("# task\n", encoding="utf-8")
    stub = tmp_path / "stubbin"
    opencode_marker = tmp_path / "opencode-ran"
    _make_exec(stub / "opencode", _with_opencode_v1(f'touch "{opencode_marker}"; exit 0'))
    selection = tmp_path / "selection.json"
    run_env = dict(env, SANDBOX="0", PATH=f"{stub}:{env['PATH']}", A770B_SERVING_SELECTION_FILE=str(selection))
    r = subprocess.run(["bash", str(LOCAL_BUILD), "run", str(seat), str(brief), "--profile", profile, *extra],
                       cwd=ROOT, env=run_env, capture_output=True, text=True, timeout=120)
    return r, seat, selection, opencode_marker


def test_run_refuses_to_dispatch_when_serve_rolled_back_to_the_previous_profile(tmp_path, live_pid):
    env = _profile_env(tmp_path, live_pid, fail_profile=RAM24)
    with _Server() as port:
        env["A770B_PORT"] = str(port)
        r, seat, selection, ran = _run_cli(tmp_path, env, RAM24)
    out = r.stdout + r.stderr
    assert f"start profile={RAM24}" in _serve_log(env) and f"start profile={BASE}" in _serve_log(env), out
    assert r.returncode == 3, out
    assert not ran.exists(), "a run was dispatched to the rolled-back profile"
    assert not (seat / "Local_Documentation" / "briefs").exists(), out
    assert not (Path(env["A770B_DATA"]) / "logs" / "last-build.log.path").exists(), out
    if selection.exists():
        assert json.loads(selection.read_text(encoding="utf-8")).get("served_profile") != RAM24


def test_run_receipt_names_the_verified_profile_and_container(tmp_path, live_pid):
    env = _profile_env(tmp_path, live_pid)
    with _Server() as port:
        env["A770B_PORT"] = str(port)
        r, _seat, selection, _ran = _run_cli(tmp_path, env, RAM24)
    out = r.stdout + r.stderr
    assert "already up" not in r.stdout, out
    assert f"start profile={RAM24}" in _serve_log(env), out
    assert selection.exists(), out
    doc = json.loads(selection.read_text(encoding="utf-8"))
    assert doc["profile"] == RAM24 and doc["served_profile"] == RAM24, doc
    assert doc["cid"] == CID_NEW, doc                     # the container verified live, not the previous one
    assert doc.get("verified") is True, doc


def test_run_refuses_to_dispatch_when_the_live_container_is_not_the_recorded_one(tmp_path, live_pid):
    env = _profile_env(tmp_path, live_pid)
    # The same profile is already up, but docker reports a different container than the recorded id.
    Path(env["SERVE_RUNNING_CID"]).write_text("c" * 64 + "\n", encoding="ascii")
    with _Server() as port:
        env["A770B_PORT"] = str(port)
        r, _seat, selection, ran = _run_cli(tmp_path, env, BASE)
    out = r.stdout + r.stderr
    assert r.returncode == 3, out
    assert not ran.exists(), "a run was dispatched to a container that is not the recorded one"
    assert not selection.exists() or json.loads(selection.read_text(encoding="utf-8")).get("verified") is not True


def test_suite_does_not_grade_a_stage_whose_receipt_names_another_served_profile(tmp_path):
    env = _base_env(tmp_path)
    suite = _one_stage_suite(tmp_path)
    fake_lb = _make_exec(tmp_path / "fake-lb.sh", f'''if [ "$1" = run ]; then
  printf '{{"profile":"{PROFILE}","served_profile":"other-profile","engine":"llama.cpp","cid":"{CID_OLD}"}}\\n' \\
    > "$A770B_SERVING_SELECTION_FILE"
  mkdir -p "$A770B_DATA/results"
  printf '# capture\\n' > "$A770B_DATA/results/fake-stage.task.md"
  printf 'diff --git a/x b/x\\n' > "$A770B_DATA/results/fake-stage.patch"
  echo "▶ capture: $A770B_DATA/results/fake-stage.task.md — captured"
  exit 0
fi
if [ "$1" = verify ]; then touch "$A770B_DATA/verify-was-run"; exit 0; fi
exit 0''')
    env["A770B_LOCAL_BUILD"] = str(fake_lb)
    r = subprocess.run(["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert not (Path(env["A770B_DATA"]) / "verify-was-run").exists(), r.stdout + r.stderr
    assert r.returncode != 0, r.stdout + r.stderr
    doc = json.loads(next(p for p in (Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json")
                          if not p.name.endswith(".attachment.json")).read_text(encoding="utf-8"))
    assert doc["stages"][0]["outcome"] == "run_error", doc["stages"]
    assert doc["execution"]["completed_stage_ids"] == []


# ── finding 3: stop-run tells only a verified, protocol-supporting owner ─────────────────────────────────────────


def _farm(tmp_path: Path, owner_body: str) -> Path:
    """A project tree of symlinks to this checkout, except harness/build_local.sh, which is `owner_body`."""
    farm = tmp_path / "farm"
    (farm / "harness").mkdir(parents=True)
    for entry in ROOT.iterdir():
        if entry.name not in ("harness", ".git"):
            (farm / entry.name).symlink_to(entry)
    for entry in (ROOT / "harness").iterdir():
        if entry.name != "build_local.sh":
            (farm / "harness" / entry.name).symlink_to(entry)
    _make_exec(farm / "harness" / "build_local.sh", owner_body)
    return farm


OWNER_COMMON = r'''
RUNPID="$A770B_DATA/logs/run.pid"
( printf '%s\n' "$BASHPID" > "$RUNPID" && exec python3 "$A770B_PROJECT/harness/run_lead.py" -- \
    bash -c 'trap "exit 0" TERM; sleep 30 & wait $!' sandbox_run.sh )
echo "$?" > "$OWNER_DONE"
sleep 30
'''


@pytest.mark.parametrize("mode", ["legacy", "marker-without-handler", "handler-without-marker"])
def test_stop_run_never_signals_an_owner_it_cannot_verify_supports_the_protocol(tmp_path, mode):
    lines = []
    if mode == "marker-without-handler":
        lines.append("export A770B_RUN_OWNER_PID=$$")
    if mode == "handler-without-marker":
        lines.append("trap 'echo told > \"$OWNER_TOLD\"' USR1")
    farm = _farm(tmp_path, "\n".join(lines) + OWNER_COMMON)
    env = _base_env(tmp_path, A770B_PROJECT=str(tmp_path / "farm"))
    done, told = tmp_path / "owner.done", tmp_path / "owner.told"
    owner = subprocess.Popen(["bash", str(farm / "harness" / "build_local.sh")],
                             env=dict(env, OWNER_DONE=str(done), OWNER_TOLD=str(told)), start_new_session=True)
    try:
        pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip(), timeout=10)
        time.sleep(0.3)
        r = subprocess.run(["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _wait_for(done.exists, timeout=10)
        assert done.read_text().strip() == "143"      # the leader recorded the stop itself
        assert owner.poll() is None, "stop-run signalled an owner that does not support the USR1 protocol"
        assert not told.exists()
    finally:
        os.killpg(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)


def test_stop_run_never_signals_an_unrelated_parent_whose_arguments_name_build_local(tmp_path):
    env = _base_env(tmp_path)
    done = tmp_path / "owner.done"
    script = OWNER_COMMON.replace('"$A770B_PROJECT/harness/run_lead.py"', f'"{RUN_LEAD}"')
    wrapper = subprocess.Popen(["bash", "-c", script, "wrapper", str(BUILD_LOCAL), "x"],
                               env=dict(env, OWNER_DONE=str(done)), start_new_session=True)
    try:
        pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip(), timeout=10)
        time.sleep(0.3)
        r = subprocess.run(["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _wait_for(done.exists, timeout=10)
        assert wrapper.poll() is None, "stop-run signalled a parent that merely carries build_local.sh in its argv"
    finally:
        os.killpg(wrapper.pid, signal.SIGKILL)
        wrapper.wait(timeout=5)


def test_stop_run_tells_a_verified_owner_when_the_receipt_cannot_be_written(tmp_path):
    farm = _farm(tmp_path, "trap 'echo told > \"$OWNER_TOLD\"' USR1\nexport A770B_RUN_OWNER_PID=$$" + OWNER_COMMON)
    env = _base_env(tmp_path, A770B_PROJECT=str(farm))
    failbin = tmp_path / "failbin"
    _make_exec(failbin / "mktemp", "exit 1")
    done, told = tmp_path / "owner.done", tmp_path / "owner.told"
    owner = subprocess.Popen(["bash", str(farm / "harness" / "build_local.sh")],
                             env=dict(env, OWNER_DONE=str(done), OWNER_TOLD=str(told)), start_new_session=True)
    try:
        pidfile = Path(env["A770B_DATA"]) / "logs" / "run.pid"
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip(), timeout=10)
        time.sleep(0.3)
        r = subprocess.run(["bash", str(LOCAL_BUILD), "stop-run"], cwd=ROOT,
                           env=dict(env, PATH=f"{failbin}:{env['PATH']}"), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "was told directly" in r.stderr, r.stderr
        assert _wait_for(told.exists, timeout=10), "the verified owner was not told"
        assert _wait_for(done.exists, timeout=10) and done.read_text().strip() == "143"
        assert owner.poll() is None
    finally:
        os.killpg(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)


def test_build_local_declares_itself_to_the_leader_and_handles_usr1():
    source = BUILD_LOCAL.read_text(encoding="utf-8")
    assert "trap 'STOP_REQUESTED=1' USR1" in source
    run = source[source.index("exec_lead(){"):source.index("rc=${PIPESTATUS[0]}")]
    assert "A770B_RUN_OWNER_PID=" in run


# ── finding 4: a stop during the worker's start reaches the worker ───────────────────────────────────────────────

# Every way the leader may start its worker is wrapped so that the operator's stop lands inside the start window:
# with fork, the parent is told while the child has not yet exec'd; with posix_spawn, while the spawn is running.
EARLY_STOP_LAUNCHER = r'''
import os, runpy, signal, sys, time
lead = sys.argv[1]
real_fork = os.fork
def fork():
    pid = real_fork()
    if pid == 0:
        os.kill(os.getppid(), signal.SIGTERM)
        time.sleep(0.5)                  # hold the child before its exec, where a forwarded TERM would land
    return pid
os.fork = fork
for name in ("posix_spawn", "posix_spawnp"):
    real = getattr(os, name)
    def spawn(*a, _real=real, **k):
        os.kill(os.getpid(), signal.SIGTERM)
        return _real(*a, **k)
    setattr(os, name, spawn)
sys.argv = sys.argv[1:]
runpy.run_path(lead, run_name="__main__")
'''


def test_a_stop_during_the_workers_start_ends_the_worker_promptly(tmp_path):
    worker = _make_exec(tmp_path / "worker.sh", "exec sleep 30")
    t0 = time.monotonic()
    rc, out = _finish(_launch(tmp_path, EARLY_STOP_LAUNCHER, "--", str(worker)), timeout=15)
    assert rc == 143, out
    assert time.monotonic() - t0 < 10, "the leader waited out a worker its stop never reached"


def test_the_worker_starts_with_default_stop_dispositions_and_an_open_mask(tmp_path):
    worker = _make_exec(tmp_path / "worker.sh", f'grep -E "^(SigBlk|SigIgn|SigCgt):" /proc/$$/status > "{tmp_path}/status"')
    rc, out = _finish(_launch(tmp_path, "import runpy, sys\nsys.argv = sys.argv[1:]\n"
                                        "runpy.run_path(sys.argv[0], run_name='__main__')\n", "--", str(worker)))
    assert rc == 0, out
    fields = dict(line.split(":", 1) for line in (tmp_path / "status").read_text().splitlines())
    stop_bits = sum(1 << (s - 1) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM))
    assert int(fields["SigBlk"].strip(), 16) & stop_bits == 0, fields
    assert int(fields["SigIgn"].strip(), 16) & stop_bits == 0, fields


# ── finding 5: --timeout 0 is unlimited ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("zero", ["0", "0s", "0.0", "0m"])
def test_run_lead_zero_timeout_means_no_fence(tmp_path, zero):
    marker = tmp_path / "done"
    worker = _make_exec(tmp_path / "worker.sh", f'sleep 1.2; touch "{marker}"; exit 0')
    proc = subprocess.Popen([sys.executable, str(RUN_LEAD), "--timeout", zero, "--", str(worker)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    rc, out = _finish(proc)
    assert rc == 0, out
    assert marker.exists(), out
    assert "fence expired" not in out


def test_run_cli_timeout_zero_is_forwarded_as_unlimited(tmp_path):
    env = _base_env(tmp_path)
    seat = _git_seat(tmp_path / "seat")
    brief = tmp_path / "brief.md"
    brief.write_text("# task\n", encoding="utf-8")
    stub = tmp_path / "stubbin"
    _make_exec(stub / "curl", 'case "$*" in *http_code*) printf 200;; esac; exit 0')
    marker = tmp_path / "opencode-finished"
    _make_exec(stub / "opencode", _with_opencode_v1(f'sleep 1.2; touch "{marker}"; exit 0'))
    r = subprocess.run(["bash", str(LOCAL_BUILD), "run", str(seat), str(brief), "--profile", PROFILE,
                        "--timeout", "0", "--base-url", "http://127.0.0.1:9", "--served-model-name", "m"],
                       cwd=ROOT, env=dict(env, SANDBOX="0", PATH=f"{stub}:{env['PATH']}"),
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "timeout=0s" in r.stdout, out                  # the CLI accepted and forwarded the zero
    assert marker.exists(), "a zero --timeout ended the builder: " + out
    build_log = Path((Path(env["A770B_DATA"]) / "logs" / "last-build.log.path").read_text().strip())
    log = build_log.read_text(encoding="utf-8", errors="replace")
    assert "fence expired" not in log + out, out
    assert "opencode exit=0" in r.stdout, out


# ── finding 6: an observed pinned allocation survives evidence without host_memory_settings ──────────────────────

OBSERVED = {"value_bytes": round(27.70 * 1024 ** 3), "source": "engine.log",
            "reported_text": "mirrored in 27.70 GiB of pinned host RAM"}


def test_observe_records_the_pinned_allocation_on_evidence_that_has_no_settings_block(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "engine.log").write_text("info: mirrored in 27.70 GiB of pinned host RAM\n", encoding="utf-8")
    evidence = run / "evidence.json"
    managed.write_state(evidence, {"engine": "strata", "config": str(tmp_path / "strata.json"), "log_dir": str(run)})
    managed.observe_host_memory(evidence)
    saved = managed.read_state(evidence)
    assert "host_memory_settings" not in saved
    assert saved["observed_pinned_host_memory"]["value_bytes"] == OBSERVED["value_bytes"]


def test_ladder_row_keeps_the_observed_pinned_allocation_without_a_settings_block(tmp_path):
    from test_ladder_row import _host_sample, printed_row, run_with_conditions
    config = tmp_path / "strata.json"
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"engine": "strata", "config": str(config),
                                    "observed_pinned_host_memory": OBSERVED}), encoding="utf-8")
    host_log = tmp_path / "host-memory.jsonl"
    _host_sample(tmp_path / "host-sample.json", 30000000)
    host_log.write_text((tmp_path / "host-sample.json").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result, doc = run_with_conditions(tmp_path, evidence=evidence, gguf=str(config), engine="strata",
                                      backend="strata", host_log=host_log)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = doc["conditions"]["engine_host_memory"]
    assert engine["observed_pinned_host_memory"] == OBSERVED
    assert engine["settings"] is None                      # nothing configured was read, and none is invented
    assert "observed" in engine["observed_source"] and engine["source"] is None   # measured, and nothing configured
    assert "Strata reported pinned host RAM 27.70 GiB" in printed_row(result.stdout)["measured_on"]


def test_engine_block_labels_the_measured_value_apart_from_the_configured_settings():
    settings = {"env": {"STRATA_MIRROR_MIB": "28672"}, "args": {}}
    both = host_memory.conditions(None, None, {"settings": settings, "observed_pinned_host_memory": OBSERVED})
    engine = both["engine_host_memory"]
    assert engine["settings"] == settings and "configured" in engine["source"]
    assert engine["observed_pinned_host_memory"] == OBSERVED and "observed" in engine["observed_source"]
    only = host_memory.conditions(None, None, {"settings": None, "observed_pinned_host_memory": OBSERVED})
    engine = only["engine_host_memory"]
    assert engine["settings"] is None and engine["source"] is None
    assert engine["observed_pinned_host_memory"] == OBSERVED
    assert "Strata reported pinned host RAM 27.70 GiB" in host_memory.clause(only)
    assert "Strata config" not in host_memory.clause(only)
    # the earlier bare-settings form is still read as configured settings
    bare = host_memory.conditions(None, None, settings)["engine_host_memory"]
    assert bare["settings"] == settings and bare["observed_pinned_host_memory"] is None
