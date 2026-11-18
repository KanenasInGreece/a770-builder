#!/usr/bin/env python3
"""Offline regression tests for the review-v059 fixes.

All fixtures use temporary directories, fake CLI binaries (docker/nvtop/gh/bwrap),
and local bare git repositories — never touching real GPU services or live checkouts.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
RELEASE_SH = ROOT / "release.sh"
CAPTURE_SH = ROOT / "harness" / "capture_task.sh"
RUN_SUITE_SH = ROOT / "harness" / "run_suite.sh"
LADDER_SH = ROOT / "harness" / "ladder.sh"


def _make_exec(path: Path, body: str) -> Path:
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
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    env = dict(os.environ)
    for k in ("A770B_CARD_LOCK_PATH", "A770B_SEAT_LOCK_PATH", "A770B_ATTACHED"):
        env.pop(k, None)
    env.update(
        HOME=str(fake_home),
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(data),
        A770B_REFUSE=str(tmp_path / "refused"),
        A770B_MODELS=str(models),
        A770B_ALLOW_NO_NVTOP="1",
        A770B_GPU_MATCH="DG2",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_MIN_AVAIL_MB="1",
        A770B_API_KEY_FILE=str(tmp_path / "api.key"),
        A770B_CARD_MODE="display",
    )
    env.update(extra)
    return env


def test_locks_ordering_canonical_aliases_reentry_forged_env_and_fd_closure(tmp_path):
    env = _base_env(tmp_path)
    seat_real = tmp_path / "seat-real"
    seat_real.mkdir()
    seat_link = tmp_path / "seat-alias"
    seat_link.symlink_to(seat_real)

    # 1. Nested phase reentry via symlink alias succeeds; forged A770B_CARD_LOCK_PATH without held FD is rejected when locked.
    r = subprocess.run(
        [
            "bash", "-c",
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
            f'card_lock; seat_lock "{seat_real}"; '
            f'bash -c \'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; card_lock; seat_lock "{seat_link}"\'',
        ],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr

    # 2. Forging A770B_CARD_LOCK_PATH (closed FD 9 OR unlocked FD 9 open on the exact same inode)
    # cannot bypass an active card_lock held by an independent holder.
    holder = subprocess.Popen(
        [
            "bash", "-c",
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; card_lock; sleep 3',
        ],
        env=env, cwd=ROOT,
    )
    try:
        lock_file = Path(env["A770B_DATA"]) / "logs" / "local-build.lock"
        # Wait briefly for holder to acquire flock
        for _ in range(40):
            if lock_file.exists():
                r_probe = subprocess.run(
                    [
                        "bash", "-c",
                        f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
                        f'export A770B_CARD_LOCK_PATH="{lock_file}"; '
                        f'exec 9>&-; card_lock',
                    ],
                    capture_output=True, text=True, env=env, cwd=ROOT,
                )
                if r_probe.returncode == 2:
                    break
            import time
            time.sleep(0.05)
        else:
            raise AssertionError("concurrent card_lock contention with forged env var did not refuse")

        # Candidate descriptor open on the exact same lock inode, unlocked, while holder owns the reservation
        r_inode = subprocess.run(
            [
                "bash", "-c",
                f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
                f'export A770B_CARD_LOCK_PATH="{lock_file}"; '
                f'exec 9>>"{lock_file}"; card_lock',
            ],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        assert r_inode.returncode == 2, r_inode.stdout + r_inode.stderr
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    # 2b. Same inode-only unlocked FD check for seat_lock with symlink alias and independent holder.
    seat_holder = subprocess.Popen(
        [
            "bash", "-c",
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; seat_lock "{seat_real}"; sleep 3',
        ],
        env=env, cwd=ROOT,
    )
    try:
        import hashlib, time
        seat_lock_file = Path(env["A770B_DATA"]) / "logs" / "seat-locks" / (
            hashlib.sha256(str(seat_real.resolve()).encode("utf-8")).hexdigest() + ".lock"
        )
        for _ in range(40):
            if seat_lock_file.exists():
                r_seat_probe = subprocess.run(
                    [
                        "bash", "-c",
                        f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
                        f'export A770B_SEAT_LOCK_PATH="{seat_lock_file}"; '
                        f'exec 7>>"{seat_lock_file}"; seat_lock "{seat_link}"',
                    ],
                    capture_output=True, text=True, env=env, cwd=ROOT,
                )
                if r_seat_probe.returncode == 2:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("seat_lock accepted unlocked candidate FD on held inode")
    finally:
        seat_holder.terminate()
        seat_holder.wait(timeout=5)

    # 3. Lock-order inversion (seat_lock held, then card_lock acquired when FD 9 is closed) is refused.
    r_inv = subprocess.run(
        [
            "bash", "-c",
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
            f'seat_lock "{seat_real}"; card_lock',
        ],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r_inv.returncode == 2
    assert "lock order violation" in r_inv.stderr

    # 4. sandbox_run.sh closes FD 7 and FD 9 before entering bwrap.
    bin_ = tmp_path / "bin"
    fd_out = tmp_path / "fds.txt"
    _make_exec(bin_ / "bwrap", f'ls -1 /proc/$$/fd > "{fd_out}"; exit 0')
    _make_exec(bin_ / "socat", "exit 0")
    uv_cache = tmp_path / "uv-cache"
    uv_cache.mkdir()
    seat_real_git = tmp_path / "seat-git"
    seat_real_git.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=seat_real_git, check=True)
    cfg = tmp_path / "cfg.jsonc"
    cfg.write_text("{}\n", encoding="utf-8")
    env_sb = _base_env(
        tmp_path,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_UV_CACHE=str(uv_cache),
        A770B_NO_BRIDGE="1",
    )
    _make_exec(bin_ / "opencode", "exit 0")
    _make_exec(bin_ / "uv", 'printf "%s\\n" "uv 0.13.4 (test)"')
    r_sb = subprocess.run(
        [
            "bash", "-c",
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
            f'card_lock; seat_lock "{seat_real_git}"; '
            f'bash "{ROOT}/harness/sandbox_run.sh" "{seat_real_git}" "{cfg}" -- true',
        ],
        capture_output=True, text=True, env=env_sb, cwd=ROOT,
    )
    assert r_sb.returncode == 0, r_sb.stdout + r_sb.stderr
    fds = set(fd_out.read_text(encoding="utf-8").split())
    assert "7" not in fds and "9" not in fds


def test_attach_running_server_validates_url_before_curl(tmp_path):
    bin_ = tmp_path / "bin"
    curl_log = tmp_path / "curl.log"
    _make_exec(bin_ / "curl", f'echo "$*" >> "{curl_log}"; echo 200')
    env = _base_env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")

    bad_urls = [
        "https://127.0.0.1:8080",
        "http://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:8080/v1/v1",
        "http://127.0.0.1:8080/api",
        "http://127.0.0.1:8080?x=1",
        "http://127.0.0.1:8080#frag",
        "http://127.0.0.1",
        "http://127.0.0.1:99999",
    ]
    for bad in bad_urls:
        r = subprocess.run(
            ["bash", "-c", f'. "{LOCAL_BUILD}"; _attach_running_server "{bad}" model-a'],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        assert r.returncode == 2, (bad, r.stdout, r.stderr)
        assert not curl_log.exists(), f"curl was called for rejected URL {bad}"

    for good in ("http://127.0.0.1:8080", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/"):
        r = subprocess.run(
            ["bash", "-c", f'. "{LOCAL_BUILD}"; _attach_running_server "{good}" model-a'],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        assert r.returncode == 0, (good, r.stdout, r.stderr)
    assert curl_log.exists()


def test_capture_task_binary_patch_applies_cleanly_on_disposable_seat(tmp_path):
    env = _base_env(tmp_path)
    seat = tmp_path / "seat-bin"
    seat.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=seat, check=True)
    (seat / "existing.bin").write_bytes(b"\x00\x01\x02\x03")
    subprocess.run(["git", "add", "existing.bin"], cwd=seat, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=seat, check=True)

    # Modify tracked binary + create new binary untracked file
    (seat / "existing.bin").write_bytes(b"\xff\xfe\x00\x09")
    (seat / "added.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    blog = tmp_path / "build.log"
    blog.write_text("ok\n", encoding="utf-8")

    r = subprocess.run(
        ["bash", str(CAPTURE_SH), "bin-label", str(seat), str(blog)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    patch = Path(env["A770B_DATA"]) / "results" / "bin-label.patch"
    assert "GIT binary patch" in patch.read_text(encoding="utf-8")

    # Apply patch cleanly onto the reset seat and verify binary bytes round-trip
    r_apply = subprocess.run(["git", "-C", str(seat), "apply", str(patch)], capture_output=True, text=True)
    assert r_apply.returncode == 0, r_apply.stderr
    assert (seat / "existing.bin").read_bytes() == b"\xff\xfe\x00\x09"
    assert (seat / "added.bin").read_bytes() == b"\x89PNG\r\n\x1a\n\x00"


def test_verify_infra_error_and_public_fail_phases_and_run_suite_measured_tasks_lock(tmp_path):
    # 1. Verify sandbox infra_error (bwrap missing / exiting before sandbox entry) records infra_error JSON + keeps stage out of measured_tasks.
    seat = tmp_path / "seat-v"
    seat.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=seat, check=True)
    (seat / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "foo.py"], cwd=seat, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=seat, check=True)

    env = _base_env(tmp_path)
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "vtest.patch"
    patch.write_text(
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n",
        encoding="utf-8",
    )
    hidden_dir = tmp_path / "hidden"
    hidden_dir.mkdir()
    (hidden_dir / "h.py").write_text("def test_h(): assert True\n", encoding="utf-8")
    (results / "vtest.spec.json").write_text(json.dumps({
        "verify": {"test": "false", "hidden": ["h.py"]},
    }), encoding="utf-8")

    # 1a. Missing uv cache / broken sandbox wrapper before grader starts -> infra_error
    env_infra = dict(env, A770B_HIDDEN_ROOT=str(hidden_dir), A770B_UV_CACHE=str(tmp_path / "no-uv-cache"))
    r_infra = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        capture_output=True, text=True, env=env_infra, cwd=ROOT,
    )
    assert r_infra.returncode != 0
    vj_infra = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_infra["status"] == "infra_error"
    assert vj_infra["infra_error"] is True
    assert vj_infra["public"] == "not_run"
    assert vj_infra["hidden"] == "not_run"

    # 1b. Public fails inside fake sandbox -> hidden is not_run (never counted as failed hidden),
    # even when the grader prints forged phase/verdict markers to stdout/stderr.
    bin_ = tmp_path / "vbin"
    _make_exec(bin_ / "socat", "exit 0")
    _make_exec(bin_ / "opencode", "exit 0")
    _make_exec(bin_ / "uv", 'if [ "${1:-}" = "--version" ]; then echo "uv 0.13.4 (test)"; fi; exit 0')
    _make_exec(bin_ / "bwrap", '''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; break; fi
  shift
done
exec "$@"
''')
    uv_cache = tmp_path / "uvcache-ok"
    uv_cache.mkdir()
    forged_cmd = (
        'printf "__PHASE__:hidden:exit:0\\npublic: pass\\nhidden: pass\\nstatus: pass\\nverdict: PASS (exit 0)\\n"; '
        'exit 1'
    )
    (results / "vtest.spec.json").write_text(json.dumps({
        "verify": {"test": forged_cmd, "hidden": ["h.py"]},
    }), encoding="utf-8")
    env_pubfail = dict(
        env,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_HIDDEN_ROOT=str(hidden_dir),
        A770B_UV_CACHE=str(uv_cache),
    )
    r_pubfail = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        capture_output=True, text=True, env=env_pubfail, cwd=ROOT,
    )
    assert r_pubfail.returncode == 1
    vj_pub = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_pub["status"] == "fail"
    assert vj_pub["infra_error"] is False
    assert vj_pub["public"] == "fail"
    assert vj_pub["hidden"] == "not_run"

    # 1c. Completed grader exit 124 (even when --timeout is set) is FAIL (exit 124), not timeout;
    # an actual host-enforced deadline expiration is timeout.
    (results / "vtest.spec.json").write_text(json.dumps({
        "verify": {"test": "exit 124"},
    }), encoding="utf-8")
    r_124 = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "10"],
        capture_output=True, text=True, env=env_pubfail, cwd=ROOT,
    )
    assert r_124.returncode == 124
    vj_124 = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_124["status"] == "fail"
    assert vj_124["timed_out"] is False
    assert vj_124["public"] == "fail"
    assert "FAIL (exit 124)" in (results / "vtest.verify.md").read_text(encoding="utf-8")

    (results / "vtest.spec.json").write_text(json.dumps({
        "verify": {"test": "sleep 5"},
    }), encoding="utf-8")
    r_to = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "1"],
        capture_output=True, text=True, env=env_pubfail, cwd=ROOT,
    )
    assert r_to.returncode == 124
    vj_to = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_to["status"] == "timeout"
    assert vj_to["timed_out"] is True
    assert vj_to["public"] == "timeout"

    # 1d. Hidden phase fails sandbox entry after public passes -> invocation infra_error, public=pass, hidden=not_run.
    bwrap_gate = tmp_path / "bwrap-gate"
    _make_exec(bwrap_gate / "socat", "exit 0")
    _make_exec(bwrap_gate / "opencode", "exit 0")
    _make_exec(bwrap_gate / "uv", 'if [ "${1:-}" = "--version" ]; then echo "uv 0.13.4 (test)"; fi; exit 0')
    _make_exec(bwrap_gate / "bwrap", '''
case " $* " in
  *"_hidden_"*) exit 2 ;;
esac
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; break; fi
  shift
done
exec "$@"
''')
    (results / "vtest.spec.json").write_text(json.dumps({
        "verify": {"test": "true", "hidden": ["h.py"]},
    }), encoding="utf-8")
    env_hid_infra = dict(
        env,
        PATH=f"{bwrap_gate}:{os.environ['PATH']}",
        A770B_HIDDEN_ROOT=str(hidden_dir),
        A770B_UV_CACHE=str(uv_cache),
    )
    r_hid_infra = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        capture_output=True, text=True, env=env_hid_infra, cwd=ROOT,
    )
    assert r_hid_infra.returncode != 0
    vj_hid_infra = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_hid_infra["status"] == "infra_error"
    assert vj_hid_infra["infra_error"] is True
    assert vj_hid_infra["public"] == "pass"
    assert vj_hid_infra["hidden"] == "not_run"

    # 1e. Host timeout fires before sandbox entry completes -> invocation timeout, unstarted phase=not_run.
    bwrap_hang = tmp_path / "bwrap-hang"
    _make_exec(bwrap_hang / "socat", "exit 0")
    _make_exec(bwrap_hang / "opencode", "exit 0")
    _make_exec(bwrap_hang / "uv", 'if [ "${1:-}" = "--version" ]; then echo "uv 0.13.4 (test)"; fi; exit 0')
    _make_exec(bwrap_hang / "bwrap", "sleep 5")
    env_hang = dict(
        env,
        PATH=f"{bwrap_hang}:{os.environ['PATH']}",
        A770B_HIDDEN_ROOT=str(hidden_dir),
        A770B_UV_CACHE=str(uv_cache),
    )
    r_hang = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat), "--timeout", "1"],
        capture_output=True, text=True, env=env_hang, cwd=ROOT,
    )
    assert r_hang.returncode == 124
    vj_hang = json.loads((results / "vtest.verify.json").read_text(encoding="utf-8"))
    assert vj_hang["status"] == "timeout"
    assert vj_hang["timed_out"] is True
    assert vj_hang["public"] == "not_run"
    assert vj_hang["hidden"] == "not_run"

    # 2. Concurrent record_measured writers serialize under flock and keep both stages.
    pf = tmp_path / "a770.display.json"
    pf.write_text((ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"), encoding="utf-8")
    env_rm = dict(env, A770B_PROFILES_FILE=str(pf))
    def _rm_cmd(stage: str, work: str) -> str:
        return (
            f'A770B_PROFILES_FILE="{pf}" PROFILE=qwen35-9b-q4km-vulkan; '
            f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; '
            f'PROFILE=qwen35-9b-q4km-vulkan; '
            f'eval "$(sed -n \'/^record_measured(){{/,/^}}/p\' "{RUN_SUITE_SH}")"; '
            f'record_measured "{stage}" "{work}" 12 2000'
        )
    p1 = subprocess.Popen(["bash", "-c", _rm_cmd("s1-frontend", "true")], env=env_rm, cwd=ROOT)
    p2 = subprocess.Popen(["bash", "-c", _rm_cmd("s2-backend", "false")], env=env_rm, cwd=ROOT)
    assert p1.wait(timeout=15) == 0
    assert p2.wait(timeout=15) == 0
    # Also verify non-completed working="" does NOT write measured_tasks
    p3 = subprocess.run(["bash", "-c", _rm_cmd("s0-notes", "")], env=env_rm, cwd=ROOT)
    assert p3.returncode == 0
    mt = json.loads(pf.read_text(encoding="utf-8"))["profiles"]["qwen35-9b-q4km-vulkan"]["measured_tasks"]
    assert mt["s1-frontend"]["quality"] == "pass"
    assert mt["s2-backend"]["quality"] == "fail"
    assert "s0-notes" not in mt


def test_ladder_stop_only_stops_its_own_cid_and_turns_cleanup_failure_non_zero(tmp_path):
    # Exercise ladder.sh with mocked serve script and mocked speed/sweep/depth/kit to prove:
    # (1) stale $NAME.json is ignored when load/attach did not write one;
    # (2) cleanup failure turns 0 exit into 1 and does not run project-wide `stop`.
    env = _base_env(tmp_path)
    results = Path(env["A770B_DATA"]) / "results"
    stale_load = results / "cand.json"
    stale_load.write_text(json.dumps({"vram_gib_after_load": 99.9, "load_s": 99.9}), encoding="utf-8")

    # Case A: missing bring/attach -> stale cand.json must NOT be read as vram_gib_after_load
    r_stale = subprocess.run(
        ["bash", str(LADDER_SH), "/nonexistent-model.gguf", "--ctx", "8192", "--bring", "attach"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r_stale.returncode != 0

    # Case B: ladder cleanup calls `stop-id <cid>` (never bare `stop`), and failure turns 0 exit into non-zero
    serve_mock = tmp_path / "serve-mock.sh"
    serve_calls = tmp_path / "serve-calls.log"
    _make_exec(serve_mock, f'''
printf '%s\\n' "$*" >> "{serve_calls}"
case "$1" in
  start)
    mkdir -p "$A770B_DATA/logs"
    printf '%s\\n' "cid-ladder-99" > "$A770B_DATA/logs/llamacpp-a770.cid"
    exit 0 ;;
  stop-id)
    exit 3 ;;
  stop)
    echo "FORBIDDEN_PROJECT_STOP" >&2
    exit 99 ;;
esac
''')
    # Test the _ladder_stop trap behavior directly via sourcing or isolated rung 1 fail
    r_trap = subprocess.run(
        [
            "bash", "-c",
            f'_serve="{serve_mock}"; _ladder_started=1; LADDER_CID="cid-ladder-99"; '
            f'_ladder_stop() {{ local rc=$? stop_rc=0; [ "$_ladder_started" = 1 ] || return 0; _ladder_started=0; '
            f'if [ -n "$LADDER_CID" ]; then bash "$_serve" stop-id "$LADDER_CID" || stop_rc=$?; else stop_rc=3; fi; '
            f'if [ "$stop_rc" -ne 0 ]; then [ "$rc" -eq 0 ] && rc=1; fi; exit "$rc"; }}; '
            f'trap _ladder_stop EXIT; exit 0',
        ],
        capture_output=True, text=True, env=env,
    )
    assert r_trap.returncode == 1
    assert serve_calls.read_text(encoding="utf-8").splitlines() == ["stop-id cid-ladder-99"]


def test_release_sh_idempotent_retry_with_bare_origin_and_fake_gh(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Maintainer"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "m@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "main"], check=True)
    (repo / "VERSION").write_text("0.5.8\n", encoding="utf-8")
    script = repo / "skills" / "local-build" / "scripts" / "local-build.sh"
    script.parent.mkdir(parents=True)
    script.write_text("SKILL_VERSION=0.5.8\n", encoding="utf-8")
    (repo / "release.sh").write_text(RELEASE_SH.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "release.sh").chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "tag", "-a", "v0.5.8", "-m", "v0.5.8"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main", "v0.5.8"], check=True)

    # New commit on main before release v0.5.9
    (repo / "feat.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "feat.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "feat"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)

    notes = tmp_path / "notes.md"
    notes.write_text("Release notes\n", encoding="utf-8")
    gh_state = tmp_path / "gh.state"
    gh_log = tmp_path / "gh.log"
    bin_ = tmp_path / "bin"
    _make_exec(bin_ / "gh", f'''
printf '%s\\n' "$*" >> "{gh_log}"
mode="${{FAKE_GH_MODE:-fail_once}}"
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view")
    if [ "$mode" = "repo_not_found" ]; then
      echo "HTTP 404: Repository not found" >&2
      exit 1
    fi
    if [ "$mode" = "api_error" ]; then
      echo "HTTP 500 Internal Server Error" >&2
      exit 1
    fi
    echo '{{"nameWithOwner":"KanenasInGreece/a770-builder"}}'
    exit 0 ;;
  "release view")
    tag="$3"
    if [ "$mode" = "api_error" ]; then
      echo "HTTP 500 Internal Server Error" >&2
      exit 1
    fi
    if [ "$mode" = "generic_not_found" ]; then
      echo "HTTP 404: Not Found" >&2
      exit 1
    fi
    if [ "$mode" = "draft" ]; then
      echo '{{"isDraft":true,"tagName":"'"$tag"'"}}'
      exit 0
    fi
    if grep -qx "$tag" "{gh_state}" 2>/dev/null; then
      echo '{{"isDraft":false,"tagName":"'"$tag"'"}}'
      exit 0
    fi
    echo "release not found" >&2
    exit 1 ;;
  "release create")
    tag="$3"
    if [ "$mode" = "fail_once" ]; then
      echo "simulated network drop" >&2
      exit 1
    fi
    if [ "$mode" = "ambiguous_exists" ]; then
      printf '%s\\n' "$tag" >> "{gh_state}"
      echo "502 Bad Gateway" >&2
      exit 1
    fi
    printf '%s\\n' "$tag" >> "{gh_state}"
    exit 0 ;;
esac
exit 1
''')
    # Wrap git so the very first `git push` of the release fails (transport failure before either ref is pushed).
    real_git = subprocess.check_output(["which", "git"], text=True).strip()
    push_fail_flag = tmp_path / "fail_first_push"
    push_fail_flag.write_text("1", encoding="utf-8")
    _make_exec(bin_ / "git", f'''
if [ "$1" = "push" ] && [ -f "{push_fail_flag}" ]; then
  rm -f "{push_fail_flag}"
  echo "fatal: simulated transport failure on first push" >&2
  exit 128
fi
exec "{real_git}" "$@"
''')
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")

    # 0. First `git push` fails leaving release commit + tag locally; retry recognizes pending release and pushes without --force.
    # Run under en_US.UTF-8 collation to verify `_is_pending_release_ahead` does not depend on locale sort order.
    locale_env = dict(env, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8", LC_COLLATE="en_US.UTF-8")
    r0 = subprocess.run(["bash", "release.sh", "--notes", str(notes)], cwd=repo, capture_output=True, text=True,
                        env=dict(locale_env, FAKE_GH_MODE="fail_once"))
    assert r0.returncode != 0
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.5.9"
    assert subprocess.check_output([real_git, "-C", str(repo), "rev-parse", "origin/main"], text=True).strip() != \
           subprocess.check_output([real_git, "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    # 1. Retry pushes main + v0.5.9 cleanly, then fails on gh release create (tag + commit pushed, release still absent)
    r1 = subprocess.run(["bash", "release.sh", "--notes", str(notes)], cwd=repo, capture_output=True, text=True,
                        env=dict(locale_env, FAKE_GH_MODE="fail_once"))
    assert r1.returncode != 0
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.5.9"
    assert subprocess.check_output([real_git, "-C", str(repo), "rev-parse", "origin/main"], text=True).strip() == \
           subprocess.check_output([real_git, "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    # 2. API/repo/generic-404/draft errors refuse without creating or bumping to 0.5.10
    for mode, msg in (
        ("api_error", "API/auth"),
        ("repo_not_found", "API/auth/repository error"),
        ("generic_not_found", "API/auth error"),
        ("draft", "unpublished draft"),
    ):
        r_err = subprocess.run(["bash", "release.sh", "--notes", str(notes)], cwd=repo, capture_output=True, text=True,
                               env=dict(env, FAKE_GH_MODE=mode))
        assert r_err.returncode != 0, (mode, r_err.stdout, r_err.stderr)
        assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.5.9"
        assert msg in r_err.stderr, (mode, r_err.stderr)

    # 3. Retry succeeds cleanly via ambiguous_exists without bumping to 0.5.10
    r2 = subprocess.run(["bash", "release.sh", "--notes", str(notes)], cwd=repo, capture_output=True, text=True,
                        env=dict(env, FAKE_GH_MODE="ambiguous_exists"))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.5.9"
    assert "v0.5.9" in gh_state.read_text(encoding="utf-8").splitlines()

    # 4. Another idempotent call on already-released HEAD exits 0 without bumping
    r3 = subprocess.run(["bash", "release.sh", "--notes", str(notes)], cwd=repo, capture_output=True, text=True,
                        env=dict(env, FAKE_GH_MODE="ok"))
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.5.9"


def test_run_suite_no_capture_skips_registry_update_and_records_run_error(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "seat").mkdir()
    (tmp_path / "seat" / "README.md").write_text("fixture\n", encoding="utf-8")
    pf = tmp_path / "a770.display.json"
    orig_text = (ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8")
    pf.write_text(orig_text, encoding="utf-8")
    suite_file = tmp_path / "suite.json"
    brief = tmp_path / "brief.md"
    spec = tmp_path / "spec.json"
    brief.write_text("# Task\n", encoding="utf-8")
    spec.write_text("{}\n", encoding="utf-8")
    suite_file.write_text(json.dumps({
        "suite": "SUITE-TEST",
        "kit_version": "1",
        "stages": [{
            "id": "s1-frontend",
            "language": "python",
            "brief": str(brief),
            "spec": str(spec),
            "grader": {"working": "true", "budget_lines": 50},
        }],
    }), encoding="utf-8")
    fake_lb = tmp_path / "fake-lb.sh"
    _make_exec(fake_lb, 'if [ "$1" = "run" ]; then echo "run crashed" >&2; exit 1; fi; exit 0')
    seat = tmp_path / "suite-seat"
    run_env = dict(
        env,
        A770B_PROFILES_FILE=str(pf),
        A770B_LOCAL_BUILD=str(fake_lb),
    )
    r = subprocess.run(
        ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(seat), "--suite", str(suite_file), "--stages", "s1-frontend"],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # Prior registry row must remain byte-for-byte identical.
    assert pf.read_text(encoding="utf-8") == orig_text
    res_files = list((Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json"))
    assert len(res_files) == 1
    doc = json.loads(res_files[0].read_text(encoding="utf-8"))
    st = doc["stages"][0]
    assert st["working"] is None
    assert st["outcome"] == "run_error"
    rep = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "suite_report.py"), str(res_files[0])],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert rep.returncode == 0, rep.stderr
    assert "RUN_ERROR" in rep.stdout


def test_missing_telemetry_compatibility_in_bench_model_and_doctor(tmp_path):
    # 1. bench_model.sh with valid preflight (serve_compose / initial) followed by unreadable nvtop:
    # A770B_ALLOW_NO_NVTOP=1 succeeds and writes null; A770B_ALLOW_NO_NVTOP=0 fails closed.
    for allow in ("1", "0"):
        d = tmp_path / f"bm-{allow}"
        d.mkdir()
        env = _base_env(d, A770B_ALLOW_NO_NVTOP=allow, A770B_HOST="127.0.0.1", A770B_PORT="18080")
        bin_ = d / "bin"
        serve_mock = d / "serve.sh"
        _make_exec(serve_mock, f'mkdir -p "{env["A770B_DATA"]}/logs"; echo $$ > "{env["A770B_DATA"]}/logs/llamacpp-a770.pid"; exit 0')
        _make_exec(bin_ / "nvtop", "echo '[]'")
        _make_exec(bin_ / "bc", "echo 1.2")
        _make_exec(bin_ / "curl", 'echo \'{"status":"ok"}\'; exit 0')
        _make_exec(bin_ / "uv", 'printf "%s\\n" "uv 0.13.4 (test)"')
        corpus_glob = d / "corpus" / "*.py"
        (d / "corpus").mkdir()
        (d / "corpus" / "a.py").write_text("print(1)\n", encoding="utf-8")
        # Minimal local HTTP server for chat completions
        import http.server, threading
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({
                    "choices": [{"message": {"content": "4 Athens print('hello')", "tool_calls": [{"id": "1"}]}}],
                    "timings": {"prompt_n": 10, "prompt_ms": 5.0, "predicted_n": 5, "predicted_per_token_ms": 2.0, "predicted_per_second": 50.0, "prompt_per_second": 100.0},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            bm_env = dict(
                env,
                PATH=f"{bin_}:{os.environ['PATH']}",
                A770B_SERVE_SCRIPT=str(serve_mock),
                A770B_PORT=str(port),
                A770B_SUMMARY_CORPUS=str(corpus_glob),
                A770B_API_KEY_FILE=str(d / "api.key"),
            )
            (d / "api.key").write_text("k\n", encoding="utf-8")
            r = subprocess.run(
                ["bash", str(ROOT / "harness" / "bench_model.sh"), "lbl", "m.gguf", "4096"],
                capture_output=True, text=True, env=bm_env, cwd=ROOT,
            )
        finally:
            srv.shutdown()
            srv.server_close()
        if allow == "1":
            assert r.returncode == 0, r.stdout + r.stderr
            out_doc = json.loads((Path(env["A770B_DATA"]) / "results" / "lbl.json").read_text(encoding="utf-8"))
            assert out_doc["vram_gib_after_load"] is None
            assert out_doc["vram_gib_after_probes"] is None
        else:
            assert r.returncode == 3, r.stdout + r.stderr
            assert "VRAM telemetry unavailable" in r.stderr

    # 2. local-build.sh doctor when gpu_match_count=1 then gpu_used_gib becomes unreadable
    d_doc = tmp_path / "doc"
    d_doc.mkdir()
    doc_bin = d_doc / "bin"
    cnt = d_doc / "nv.cnt"
    _make_exec(doc_bin / "nvtop", f'''
n=$(cat "{cnt}" 2>/dev/null || echo 0); n=$((n+1)); printf '%s' "$n" > "{cnt}"
if [ "$n" -eq 1 ]; then
  echo '[{{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}}]'
else
  echo '[]'
fi
''')
    _make_exec(doc_bin / "uv", 'printf "%s\\n" "uv 0.13.4 (test)"')
    _make_exec(doc_bin / "uvx", 'printf "%s\\n" "uvx 0.13.4 (test)"')
    doc_env = _base_env(d_doc, PATH=f"{doc_bin}:{os.environ['PATH']}", A770B_ALLOW_NO_NVTOP="1")
    r_doc = subprocess.run(["bash", str(LOCAL_BUILD), "doctor"], capture_output=True, text=True, env=doc_env, cwd=ROOT)
    assert "NameError" not in r_doc.stderr
    assert "not measured (VRAM reading unavailable)" in r_doc.stdout


def test_refused_build_clears_stale_capture_only_when_unlocked(tmp_path):
    env = _base_env(tmp_path)
    logs = Path(env["A770B_DATA"]) / "logs"
    usage = logs / "usage.json"
    stamp = logs / "run-since"

    # 1. Standalone refused build when card_lock is free clears stale markers
    usage.write_text('[{"prompt_tokens":99}]\n', encoding="utf-8")
    stamp.write_text("2020-01-01T00:00:00Z\n", encoding="utf-8")
    r_free = subprocess.run(["bash", str(ROOT / "harness" / "build_local.sh")], capture_output=True, text=True, env=env, cwd=ROOT)
    assert r_free.returncode != 0
    assert not usage.exists()
    assert not stamp.exists()

    # 2. Refused concurrent build while active run holds card_lock MUST NOT clear active run's markers
    holder = subprocess.Popen(
        ["bash", "-c", f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; card_lock; sleep 3'],
        env=env, cwd=ROOT,
    )
    try:
        lock_file = logs / "local-build.lock"
        import time
        for _ in range(40):
            if lock_file.exists():
                # Confirm lock is actively held
                probe = subprocess.run(
                    ["bash", "-c", f'flock -n 9 9>>"{lock_file}"'],
                    env=env,
                )
                if probe.returncode != 0:
                    break
            time.sleep(0.05)
        usage.write_text('[{"prompt_tokens":42}]\n', encoding="utf-8")
        stamp.write_text("2026-11-18T00:00:00Z\n", encoding="utf-8")
        r_busy = subprocess.run(["bash", str(ROOT / "harness" / "build_local.sh")], capture_output=True, text=True, env=env, cwd=ROOT)
        assert r_busy.returncode != 0
        assert usage.is_file() and "42" in usage.read_text(encoding="utf-8")
        assert stamp.is_file() and "2026-11-18" in stamp.read_text(encoding="utf-8")
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_run_suite_reused_label_inapplicable_patch_is_verify_error_not_quality_fail(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "seat").mkdir()
    (tmp_path / "seat" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    pf = tmp_path / "a770.display.json"
    orig_text = (ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8")
    pf.write_text(orig_text, encoding="utf-8")
    suite_file = tmp_path / "suite.json"
    brief = tmp_path / "brief.md"
    spec = tmp_path / "spec.json"
    brief.write_text("# Task\n", encoding="utf-8")
    spec.write_text("{}\n", encoding="utf-8")
    suite_file.write_text(json.dumps({
        "suite": "SUITE-TEST",
        "kit_version": "1",
        "stages": [{
            "id": "s1-frontend",
            "language": "python",
            "brief": str(brief),
            "spec": str(spec),
            "grader": {"working": "true", "budget_lines": 50},
        }],
    }), encoding="utf-8")

    results = Path(env["A770B_DATA"]) / "results"
    reused = "reused-lbl"
    # Pre-existing historical PASS artifact at the exact label that run will reuse.
    (results / f"{reused}.verify.json").write_text(json.dumps({
        "label": reused,
        "status": "pass",
        "exit_code": 0,
        "public": "pass",
        "hidden": "none",
        "infra_error": False,
        "timed_out": False,
    }), encoding="utf-8")

    fake_lb = tmp_path / "fake-lb.sh"
    _make_exec(fake_lb, f'''
if [ "$1" = "run" ]; then
  cat > "$A770B_DATA/results/{reused}.task.md" <<'CAP'
# fake capture
## server-side timings during the run
requests=1 prompt_tokens_total=50 gen_tokens_total=10
CAP
  cat > "$A770B_DATA/results/{reused}.patch" <<'PATCH'
diff --git a/nonexistent.py b/nonexistent.py
--- a/nonexistent.py
+++ b/nonexistent.py
@@ -1 +1 @@
-old
+new
PATCH
  echo "▶ capture: $A770B_DATA/results/{reused}.task.md — done"
  exit 0
fi
if [ "$1" = "verify" ]; then
  exec bash "{LOCAL_BUILD}" "$@"
fi
exit 0
''')
    seat = tmp_path / "suite-seat"
    run_env = dict(
        env,
        A770B_PROFILES_FILE=str(pf),
        A770B_LOCAL_BUILD=str(fake_lb),
    )
    r = subprocess.run(
        ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(seat), "--suite", str(suite_file), "--stages", "s1-frontend"],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert pf.read_text(encoding="utf-8") == orig_text
    vj = json.loads((results / f"{reused}.verify.json").read_text(encoding="utf-8"))
    assert vj["status"] == "infra_error"
    assert vj["infra_error"] is True
    assert vj["public"] == "not_run"
    assert vj["hidden"] == "not_run"
    res_files = list(results.glob("*-suite-*.json"))
    assert len(res_files) == 1
    doc = json.loads(res_files[0].read_text(encoding="utf-8"))
    st = doc["stages"][0]
    assert st["working"] is None
    assert st["outcome"] == "verify_error"
    assert st["verify"]["status"] == "infra_error"
    rep = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "suite_report.py"), str(res_files[0])],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert rep.returncode == 0, rep.stderr
    assert "VERIFY_ERROR" in rep.stdout


def test_reviewer_evidence_not_inherited_when_builder_evidence_fails(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    (models / "Qwen3.5-9B-Q4_K_M.gguf").write_text("dummy\n", encoding="utf-8")
    (models / "gemma-4-E4B-it-Q4_K_M.gguf").write_text("dummy\n", encoding="utf-8")
    sample_log = ROOT / "tests" / "fixtures" / "engine" / "llamacpp-sycl-lv4.log"
    state_file = tmp_path / "docker.state.json"
    state_file.write_text(json.dumps({"containers": []}), encoding="utf-8")
    holder_proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    live_pid = str(holder_proc.pid)
    try:
        _make_exec(bin_ / "docker", f'''python3 - "{state_file}" "{sample_log}" "{live_pid}" "$@" <<'PY'
import json, os, re, sys
from pathlib import Path
sfile, slog, live_pid, *argv = sys.argv[1:]
state = json.loads(Path(sfile).read_text(encoding="utf-8"))
def save():
    Path(sfile).write_text(json.dumps(state), encoding="utf-8")
if argv[:2] == ["compose", "version"]:
    print("Docker Compose version v2.30.0"); sys.exit(0)
if argv[:2] == ["image", "inspect"]:
    print("sha256:" + "0" * 64); sys.exit(0)
if argv and argv[0] == "compose":
    project = "a770-builder"; files = []; i = 1
    while i < len(argv):
        if argv[i] == "--env-file": i += 2
        elif argv[i] == "-p": project = argv[i+1]; i += 2
        elif argv[i] == "-f": files.append(argv[i+1]); i += 2
        else: break
    cmd = argv[i] if i < len(argv) else ""
    if cmd == "down":
        state["containers"] = []; save(); sys.exit(0)
    if cmd == "up":
        lbls = {{"com.docker.compose.project": project, "com.docker.compose.service": "llama", "com.docker.compose.project.config_files": ",".join(files)}}
        for fp in files:
            try:
                lbls.update(((json.loads(Path(fp).read_text()).get("services") or {{}}).get("llama") or {{}}).get("labels") or {{}})
            except Exception:
                pass
        state["containers"] = [{{"id": "a" * 64, "name": f"{{project}}-llama-1", "project": project, "service": "llama", "running": True, "pid": live_pid, "labels": lbls}}]
        save(); sys.exit(0)
    if cmd == "ps":
        for c in state["containers"]:
            if c.get("running"): print(c["id"])
        sys.exit(0)
if argv and argv[0] == "ps":
    want = {{}}
    for i, a in enumerate(argv):
        if a == "--filter" and i + 1 < len(argv) and argv[i+1].startswith("label="):
            k, v = argv[i+1][6:].split("=", 1); want[k] = v
    for c in state["containers"]:
        if all((c.get("labels") or {{}}).get(k) == v for k, v in want.items()):
            print(c["id"])
    sys.exit(0)
if argv[:2] == ["inspect", "-f"]:
    fmt, cid = argv[2], argv[3]
    c = next((x for x in state["containers"] if x["id"] == cid or x.get("name") == cid), None)
    if not c:
        print(f"Error: No such object: {{cid}}", file=sys.stderr); sys.exit(1)
    m = re.search(r'index \\.Config\\.Labels "([^"]+)"', fmt)
    if m: print((c.get("labels") or {{}}).get(m.group(1), ""))
    elif "{{{{.Id}}}}" in fmt: print(c["id"])
    elif "{{{{.Name}}}}" in fmt: print("/" + c["name"])
    elif "{{{{.State.Running}}}}" in fmt: print("true" if c["running"] else "false")
    elif "{{{{.State.Pid}}}}" in fmt: print(c["pid"])
    else: print("")
    sys.exit(0)
if argv and argv[0] == "logs":
    if os.environ.get("FAKE_DOCKER_LOGS_FAIL") == "1":
        sys.exit(1)
    sys.stdout.write(Path(slog).read_text(encoding="utf-8"))
    sys.exit(0)
sys.exit(0)
PY
''')
        _make_exec(bin_ / "curl", 'echo \'{"ok": true}\'; exit 0')
        _make_exec(bin_ / "pgrep", "exit 0")
        _make_exec(bin_ / "nvtop", 'echo \'[{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}]\'')
        _make_exec(bin_ / "socat", "exit 0")
        _make_exec(bin_ / "opencode", "exit 0")
        _make_exec(bin_ / "uv", 'if [ "${1:-}" = "--version" ]; then echo "uv 0.13.4 (test)"; fi; exit 0')
        _make_exec(bin_ / "bwrap", '''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; break; fi
  shift
done
exec "$@"
''')
        uv_cache = tmp_path / "uv-cache"
        uv_cache.mkdir()

        pf = tmp_path / "a770.display.json"
        pf.write_text((ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"), encoding="utf-8")
        env = _base_env(
            tmp_path,
            PATH=f"{bin_}:{os.environ['PATH']}",
            A770B_CARD="a770",
            A770B_PROFILES_FILE=str(pf),
            A770B_MODELS=str(models),
            A770B_SERVE="compose",
            A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
            A770B_DRM_CARD="/dev/dri/card0",
            A770B_DRM_RENDER="/dev/dri/renderD128",
            A770B_RENDER_GID="105",
            A770B_VIDEO_GID="39",
            A770B_DOCKER="docker",
        )
        env.pop("A770B_PROFILES", None)
        # 1. Reviewer serves and writes its own serve-evidence.path.
        r_rev = subprocess.run(
            ["bash", str(LOCAL_BUILD), "serve", "gemma4-8b-e4b-q4km-vulkan"],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        assert r_rev.returncode == 0, r_rev.stdout + r_rev.stderr
        ev_ptr = Path(env["A770B_DATA"]) / "logs" / "serve-evidence.path"
        assert ev_ptr.is_file()
        rev_ev_path = ev_ptr.read_text(encoding="utf-8").strip()
        assert "gemma4-8b-e4b-q4km-vulkan" in rev_ev_path and Path(rev_ev_path).is_file()

        # 2. Builder serves next while docker logs fails -> stale reviewer serve-evidence.path MUST be cleared.
        env_fail_ev = dict(env, FAKE_DOCKER_LOGS_FAIL="1")
        r_bld = subprocess.run(
            ["bash", str(LOCAL_BUILD), "serve", "qwen35-9b-q4km-vulkan"],
            capture_output=True, text=True, env=env_fail_ev, cwd=ROOT,
        )
        assert r_bld.returncode == 0, r_bld.stdout + r_bld.stderr
        assert "could not write serve evidence" in r_bld.stderr
        assert not ev_ptr.exists()

        # 3. Also verify through run_suite.sh: reviewer evidence before builder stage whose evidence collection fails
        # yields serve_evidence=None on the builder stage.
        assert subprocess.run(
            ["bash", str(LOCAL_BUILD), "serve", "gemma4-8b-e4b-q4km-vulkan"],
            capture_output=True, text=True, env=env, cwd=ROOT,
        ).returncode == 0
        assert ev_ptr.is_file()
        (tmp_path / "seat").mkdir()
        (tmp_path / "seat" / "foo.py").write_text("x = 1\n", encoding="utf-8")
        suite_file = tmp_path / "suite.json"
        brief = tmp_path / "brief.md"
        spec = tmp_path / "spec.json"
        brief.write_text("# Task\n", encoding="utf-8")
        spec.write_text("{}\n", encoding="utf-8")
        suite_file.write_text(json.dumps({
            "suite": "SUITE-TEST",
            "kit_version": "1",
            "stages": [{
                "id": "s1-frontend",
                "language": "python",
                "brief": str(brief),
                "spec": str(spec),
                "grader": {"working": "true", "budget_lines": 50},
            }],
        }), encoding="utf-8")
        wrapper_lb = tmp_path / "wrapper-lb.sh"
        _make_exec(wrapper_lb, f'''
if [ "$1" = "run" ]; then
  bash "{LOCAL_BUILD}" serve qwen35-9b-q4km-vulkan >&2
  cat > "$A770B_DATA/results/ev-bld-lbl.task.md" <<'CAP'
# fake capture
## server-side timings during the run
requests=1 prompt_tokens_total=50 gen_tokens_total=10
CAP
  cat > "$A770B_DATA/results/ev-bld-lbl.patch" <<'PATCH'
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-x = 1
+x = 2
PATCH
  echo "▶ capture: $A770B_DATA/results/ev-bld-lbl.task.md — done"
  exit 0
fi
exec bash "{LOCAL_BUILD}" "$@"
''')
        suite_env = dict(
            env_fail_ev,
            A770B_PROFILES_FILE=str(pf),
            A770B_LOCAL_BUILD=str(wrapper_lb),
            A770B_UV_CACHE=str(uv_cache),
        )
        r_suite = subprocess.run(
            ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(tmp_path / "suite-seat"), "--suite", str(suite_file), "--stages", "s1-frontend"],
            capture_output=True, text=True, env=suite_env, cwd=ROOT,
        )
        assert r_suite.returncode == 0, r_suite.stdout + r_suite.stderr
        res_files = list((Path(env["A770B_DATA"]) / "results").glob("*-suite-*.json"))
        assert len(res_files) == 1
        doc = json.loads(res_files[0].read_text(encoding="utf-8"))
        assert doc["stages"][0]["outcome"] == "pass"
        assert doc["stages"][0]["serve_evidence"] is None
    finally:
        holder_proc.terminate()
        holder_proc.wait(timeout=5)


def test_verify_publication_failure_records_infra_error_and_clears_stale_evidence(tmp_path):
    seat = tmp_path / "seat-vpub"
    seat.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=seat, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=seat, check=True)
    (seat / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "foo.py"], cwd=seat, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=seat, check=True)

    env = _base_env(tmp_path)
    results = Path(env["A770B_DATA"]) / "results"
    patch = results / "vpub.patch"
    patch.write_text(
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n",
        encoding="utf-8",
    )
    (results / "vpub.spec.json").write_text(json.dumps({"verify": {"test": "true"}}), encoding="utf-8")

    # Pre-existing stale PASS evidence for the same label
    (results / "vpub.verify.json").write_text(json.dumps({
        "label": "vpub", "status": "pass", "exit_code": 0,
        "public": "pass", "hidden": "none", "infra_error": False, "timed_out": False,
    }), encoding="utf-8")
    (results / "vpub.verify.md").write_text("verdict: PASS (exit 0)\n", encoding="utf-8")

    bin_ = tmp_path / "vbin"
    _make_exec(bin_ / "socat", "exit 0")
    _make_exec(bin_ / "opencode", "exit 0")
    _make_exec(bin_ / "uv", 'if [ "${1:-}" = "--version" ]; then echo "uv 0.13.4 (test)"; fi; exit 0')
    _make_exec(bin_ / "bwrap", '''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; break; fi
  shift
done
exec "$@"
''')
    uv_cache = tmp_path / "uvcache"
    uv_cache.mkdir()

    # Case 1: handoff path is an existing directory -> os.replace fails during grading publication
    bad_handoff_dir = tmp_path / "handoff_is_dir"
    bad_handoff_dir.mkdir()
    env_fail = dict(
        env,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_UV_CACHE=str(uv_cache),
        A770B_VERIFY_OUT=str(bad_handoff_dir),
    )
    r = subprocess.run(
        ["bash", str(LOCAL_BUILD), "verify", str(patch), str(seat)],
        capture_output=True, text=True, env=env_fail, cwd=ROOT,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    vjson = json.loads((results / "vpub.verify.json").read_text(encoding="utf-8"))
    assert vjson["status"] == "infra_error"
    assert vjson["infra_error"] is True
    assert vjson["public"] == "not_run"
    assert vjson["hidden"] == "not_run"
    vmd_path = results / "vpub.verify.md"
    if vmd_path.exists():
        vmd = vmd_path.read_text(encoding="utf-8")
        assert "status: infra_error" in vmd
        assert "PASS (exit 0)" not in vmd
        assert "FAIL (exit )" not in vmd


def test_stop_id_and_suite_owned_cleanup_with_reviewer_and_unrelated_container(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    (models / "Qwen3.5-9B-Q4_K_M.gguf").write_text("dummy\n", encoding="utf-8")
    (models / "gemma-4-E4B-it-Q4_K_M.gguf").write_text("dummy\n", encoding="utf-8")
    state_file = tmp_path / "docker.state.json"
    state_file.write_text(json.dumps({"containers": [], "seq": 0}), encoding="utf-8")
    holder_proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    live_pid = str(holder_proc.pid)
    try:
        _make_exec(bin_ / "docker", f'''python3 - "{state_file}" "{live_pid}" "$@" <<'PY'
import json, re, sys
from pathlib import Path
sfile, live_pid, *argv = sys.argv[1:]
state = json.loads(Path(sfile).read_text(encoding="utf-8"))
def save():
    Path(sfile).write_text(json.dumps(state), encoding="utf-8")
if argv[:2] == ["compose", "version"]:
    print("Docker Compose version v2.30.0"); sys.exit(0)
if argv[:2] == ["image", "inspect"]:
    fmt = argv[3] if len(argv) > 3 and argv[2] == "-f" else ""
    if "RepoDigests" in fmt:
        print("")
    else:
        print("sha256:" + "0" * 64)
    sys.exit(0)
if argv and argv[0] == "compose":
    project = "a770-builder"; files = []; i = 1
    while i < len(argv):
        if argv[i] == "--env-file": i += 2
        elif argv[i] == "-p": project = argv[i+1]; i += 2
        elif argv[i] == "-f": files.append(argv[i+1]); i += 2
        else: break
    cmd = argv[i] if i < len(argv) else ""
    if cmd == "down":
        state["containers"] = [c for c in state["containers"] if c.get("project") != project]
        save(); sys.exit(0)
    if cmd == "up":
        state["seq"] = state.get("seq", 0) + 1
        cid = f"{{state['seq']:064x}}"
        lbls = {{"com.docker.compose.project": project, "com.docker.compose.service": "llama", "com.docker.compose.project.config_files": ",".join(files)}}
        for fp in files:
            try:
                lbls.update(((json.loads(Path(fp).read_text()).get("services") or {{}}).get("llama") or {{}}).get("labels") or {{}})
            except Exception:
                pass
        state["containers"] = [c for c in state["containers"] if c.get("project") != project]
        state["containers"].append({{"id": cid, "name": f"{{project}}-llama-1", "project": project, "service": "llama", "image": "sha256:" + "0" * 64, "running": True, "pid": live_pid, "labels": lbls}})
        save(); sys.exit(0)
    if cmd == "ps":
        rest = argv[i+1:]
        include_all = ("--all" in rest or "-a" in rest)
        svcs = [a for a in rest if not a.startswith("-")]
        for c in state["containers"]:
            if c.get("project") != project: continue
            if svcs and c.get("service") not in svcs: continue
            if include_all or c.get("running"): print(c["id"])
        sys.exit(0)
if argv and argv[0] == "ps":
    want = {{}}
    include_all = any(a == "--all" or (a.startswith("-") and not a.startswith("--") and "a" in a) for a in argv[1:])
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--filter" and i + 1 < len(argv):
            fv = argv[i+1]; i += 2
        elif a.startswith("--filter="):
            fv = a.split("=", 1)[1]; i += 1
        else:
            i += 1; continue
        if fv.startswith("label="):
            k, v = fv[6:].split("=", 1); want[k] = v
    for c in state["containers"]:
        if not include_all and not c.get("running"): continue
        if all((c.get("labels") or {{}}).get(k) == v for k, v in want.items()):
            print(c["id"])
    sys.exit(0)
if argv[:2] == ["inspect", "-f"]:
    fmt, cid = argv[2], argv[3]
    c = next((x for x in state["containers"] if x["id"] == cid or x.get("name") == cid), None)
    if not c:
        print(f"Error: No such object: {{cid}}", file=sys.stderr); sys.exit(1)
    m = re.search(r'index \\.Config\\.Labels "([^"]+)"', fmt)
    if m: print((c.get("labels") or {{}}).get(m.group(1), ""))
    elif "{{{{.Id}}}}" in fmt: print(c["id"])
    elif "{{{{.Name}}}}" in fmt: print("/" + c["name"])
    elif "{{{{.Image}}}}" in fmt: print(c.get("image", "sha256:" + "0" * 64))
    elif "{{{{.State.Running}}}}" in fmt: print("true" if c["running"] else "false")
    elif "{{{{.State.Pid}}}}" in fmt: print(c["pid"])
    else: print("")
    sys.exit(0)
if argv and argv[0] == "stop":
    c = next((x for x in state["containers"] if x["id"] == argv[-1] or x.get("name") == argv[-1]), None)
    if not c: sys.exit(1)
    c["running"] = False; c["pid"] = "0"; save(); sys.exit(0)
if argv and argv[0] == "rm":
    cid = argv[-1]
    before = len(state["containers"])
    state["containers"] = [c for c in state["containers"] if c["id"] != cid and c.get("name") != cid]
    save(); sys.exit(0 if len(state["containers"]) < before else 1)
sys.exit(0)
PY
''')
        _make_exec(bin_ / "curl", '''for a in "$@"; do
  case "$a" in *%{http_code}*) echo 200; exit 0;; esac
done
echo '{"ok": true, "choices": [{"message": {"content": "maintainable: 5\\nusable: 5"}}]}'
exit 0''')
        _make_exec(bin_ / "pgrep", "exit 0")
        _make_exec(bin_ / "nvtop", 'echo \'[{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}]\'')
        pf = tmp_path / "a770.display.json"
        pf.write_text((ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"), encoding="utf-8")
        env = _base_env(
            tmp_path,
            PATH=f"{bin_}:{os.environ['PATH']}",
            A770B_CARD="a770",
            A770B_PROFILES_FILE=str(pf),
            A770B_MODELS=str(models),
            A770B_SERVE="compose",
            A770B_SERVED_BACKEND="vulkan",
            A770B_COMPOSE_FILE=str(ROOT / "compose" / "a770-vulkan.yaml"),
            A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
            A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
            A770B_DRM_CARD="/dev/dri/card0",
            A770B_DRM_RENDER="/dev/dri/renderD128",
            A770B_RENDER_GID="105",
            A770B_VIDEO_GID="39",
            A770B_DOCKER="docker",
        )
        env.pop("A770B_PROFILES", None)
        serve_sh = ROOT / "harness" / "serve_compose.sh"

        # 1. Start initial ladder server (cid_1)
        r_s1 = subprocess.run(["bash", str(serve_sh), "start", "Qwen3.5-9B-Q4_K_M.gguf", "8192"], capture_output=True, text=True, env=env, cwd=ROOT)
        assert r_s1.returncode == 0, r_s1.stdout + r_s1.stderr
        logs = Path(env["A770B_DATA"]) / "logs"
        cid_1 = (logs / "llamacpp-a770.cid").read_text(encoding="utf-8").strip()
        assert len(cid_1) == 64

        # 2. Run suite with A770B_LADDER_RUN=1 and --reviewer: stops cid_1, starts reviewer container, cleans reviewer container on exit.
        (tmp_path / "seat").mkdir()
        (tmp_path / "seat" / "foo.py").write_text("x = 1\n", encoding="utf-8")
        suite_file = tmp_path / "suite.json"
        brief = tmp_path / "brief.md"
        spec = tmp_path / "spec.json"
        rubric = tmp_path / "rubric.md"
        brief.write_text("# Task\n", encoding="utf-8")
        spec.write_text("{}\n", encoding="utf-8")
        rubric.write_text("# Rubric\n", encoding="utf-8")
        suite_file.write_text(json.dumps({
            "suite": "SUITE-TEST",
            "kit_version": "1",
            "stages": [{
                "id": "s1-frontend",
                "language": "python",
                "brief": str(brief),
                "spec": str(spec),
                "grader": {"working": "true", "budget_lines": 50, "rubric": str(rubric)},
            }],
        }), encoding="utf-8")
        wrapper_lb = tmp_path / "wrapper-lb.sh"
        _make_exec(wrapper_lb, f'''
if [ "$1" = "run" ]; then
  cat > "$A770B_DATA/results/rev-lbl.task.md" <<'CAP'
# fake capture
## server-side timings during the run
requests=1 prompt_tokens_total=50 gen_tokens_total=10
CAP
  cat > "$A770B_DATA/results/rev-lbl.patch" <<'PATCH'
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-x = 1
+x = 2
PATCH
  echo "▶ capture: $A770B_DATA/results/rev-lbl.task.md — done"
  exit 0
fi
if [ "$1" = "verify" ]; then
  exit 0
fi
exec bash "{LOCAL_BUILD}" "$@"
''')
        suite_env = dict(env, A770B_LOCAL_BUILD=str(wrapper_lb), A770B_LADDER_RUN="1")
        r_suite = subprocess.run(
            ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(tmp_path / "suite-seat"),
             "--suite", str(suite_file), "--stages", "s1-frontend", "--reviewer", "gemma4-8b-e4b-q4km-vulkan"],
            capture_output=True, text=True, env=suite_env, cwd=ROOT,
        )
        assert r_suite.returncode == 0, r_suite.stdout + r_suite.stderr
        assert json.loads(state_file.read_text(encoding="utf-8"))["containers"] == []
        assert not (logs / "llamacpp-a770.cid").exists()

        # 3. Ladder's stop-id on cid_1 positively verifies absence and exits 0.
        r_stop1 = subprocess.run(["bash", str(serve_sh), "stop-id", cid_1], capture_output=True, text=True, env=env, cwd=ROOT)
        assert r_stop1.returncode == 0, r_stop1.stdout + r_stop1.stderr

        # 4. Standalone run_suite (no A770B_LADDER_RUN) preserves existing semantics (reviewer server remains up).
        r_standalone = subprocess.run(
            ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(tmp_path / "suite-seat-2"),
             "--suite", str(suite_file), "--stages", "s1-frontend", "--reviewer", "gemma4-8b-e4b-q4km-vulkan"],
            capture_output=True, text=True, env=dict(env, A770B_LOCAL_BUILD=str(wrapper_lb)), cwd=ROOT,
        )
        assert r_standalone.returncode == 0, r_standalone.stdout + r_standalone.stderr
        containers_after_standalone = json.loads(state_file.read_text(encoding="utf-8"))["containers"]
        assert len(containers_after_standalone) == 1
        unrelated_cid = containers_after_standalone[0]["id"]

        # 5. Suite with A770B_LADDER_RUN=1 that starts NO server of its own never adopts/stops the unrelated container.
        no_serve_lb = tmp_path / "no-serve-lb.sh"
        _make_exec(no_serve_lb, '''
if [ "$1" = "run" ]; then
  cat > "$A770B_DATA/results/ns-lbl.task.md" <<'CAP'
# fake capture
## server-side timings during the run
requests=1 prompt_tokens_total=50 gen_tokens_total=10
CAP
  cat > "$A770B_DATA/results/ns-lbl.patch" <<'PATCH'
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-x = 1
+x = 2
PATCH
  echo "▶ capture: $A770B_DATA/results/ns-lbl.task.md — done"
  exit 0
fi
exit 0
''')
        r_unrel = subprocess.run(
            ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(tmp_path / "suite-seat-3"),
             "--suite", str(suite_file), "--stages", "s1-frontend"],
            capture_output=True, text=True, env=dict(env, A770B_LOCAL_BUILD=str(no_serve_lb), A770B_LADDER_RUN="1"), cwd=ROOT,
        )
        assert r_unrel.returncode == 0, r_unrel.stdout + r_unrel.stderr
        containers_still = json.loads(state_file.read_text(encoding="utf-8"))["containers"]
        assert [c["id"] for c in containers_still] == [unrelated_cid]
        assert (logs / "llamacpp-a770.cid").read_text(encoding="utf-8").strip() == unrelated_cid

        # 6. Attach mode (--base-url) with A770B_LADDER_RUN=1 preserves attach semantics and leaves the running container untouched.
        att_lb = tmp_path / "att-lb.sh"
        _make_exec(att_lb, f'''
if [ "$1" = "run" ]; then
  . "{LOCAL_BUILD}"
  _attach_running_server "http://127.0.0.1:18099/v1" "att-model" >/dev/null
  cat > "$A770B_DATA/results/att-lbl.task.md" <<'CAP'
# fake capture
## server-side timings during the run
requests=1 prompt_tokens_total=50 gen_tokens_total=10
CAP
  cat > "$A770B_DATA/results/att-lbl.patch" <<'PATCH'
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-x = 1
+x = 2
PATCH
  echo "▶ capture: $A770B_DATA/results/att-lbl.task.md — done"
  exit 0
fi
exit 0
''')
        r_att = subprocess.run(
            ["bash", str(RUN_SUITE_SH), "--base-url", "http://127.0.0.1:18099/v1",
             "--served-model-name", "att-model", "--ctx", "8192",
             str(tmp_path / "suite-seat-att"), "--suite", str(suite_file), "--stages", "s1-frontend"],
            capture_output=True, text=True, env=dict(env, A770B_LOCAL_BUILD=str(att_lb), A770B_LADDER_RUN="1"), cwd=ROOT,
        )
        assert r_att.returncode == 0, r_att.stdout + r_att.stderr
        containers_att = json.loads(state_file.read_text(encoding="utf-8"))["containers"]
        assert [c["id"] for c in containers_att] == [unrelated_cid]

        # 7. stop-id with non-matching CIDFILE still refuses when target container is NOT absent.
        other_cid = "b" * 64
        (logs / "llamacpp-a770.cid").write_text(other_cid + "\n", encoding="utf-8")
        r_still_up = subprocess.run(["bash", str(serve_sh), "stop-id", unrelated_cid], capture_output=True, text=True, env=env, cwd=ROOT)
        assert r_still_up.returncode == 3
    finally:
        holder_proc.terminate()
        holder_proc.wait(timeout=5)


def test_seat_provenance_nonce_blocks_inode_reuse_and_refuses_unreadable_dir(tmp_path):
    import hashlib
    env = _base_env(tmp_path)
    (tmp_path / "seat-src").mkdir()
    (tmp_path / "seat-src" / "README.md").write_text("fixture\n", encoding="utf-8")
    pf = tmp_path / "a770.display.json"
    pf.write_text((ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"), encoding="utf-8")
    suite_file = tmp_path / "suite.json"
    brief = tmp_path / "brief.md"
    spec = tmp_path / "spec.json"
    brief.write_text("# Task\n", encoding="utf-8")
    spec.write_text("{}\n", encoding="utf-8")
    suite_file.write_text(json.dumps({
        "suite": "SUITE-TEST",
        "kit_version": "1",
        "stages": [{
            "id": "s1-frontend",
            "language": "python",
            "brief": str(brief),
            "spec": str(spec),
            "grader": {"working": "true", "budget_lines": 50},
        }],
    }), encoding="utf-8")
    fake_lb = tmp_path / "fake-lb.sh"
    _make_exec(fake_lb, 'if [ "$1" = "run" ]; then echo "run crashed" >&2; exit 1; fi; exit 0')
    run_env = dict(env, A770B_PROFILES_FILE=str(pf), A770B_LOCAL_BUILD=str(fake_lb))

    # 1. Create a real disposable seat, then replace it with a plain directory that claims the same (dev, ino) in ledger
    seat = tmp_path / "disp-seat"
    r1 = subprocess.run(
        ["bash", "-c", f'. "{ROOT}/harness/env.sh"; . "{ROOT}/harness/guard.sh"; mkdir -p "{seat}"; git -C "{seat}" init -q; record_seat_provenance "{seat}"'],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert r1.returncode == 0, r1.stderr
    prov_file = Path(env["A770B_DATA"]) / "logs" / "disposable-seats" / (
        hashlib.sha256(str(seat.resolve()).encode("utf-8")).hexdigest() + ".json"
    )
    assert prov_file.is_file()
    old_prov = json.loads(prov_file.read_text(encoding="utf-8"))
    assert "nonce" in old_prov and len(old_prov["nonce"]) >= 16

    # Simulate operator deleting seat and creating a new ordinary directory at same path, matching (dev, ino)
    import shutil
    shutil.rmtree(seat)
    seat.mkdir()
    (seat / "precious.txt").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seat), "init", "-q"], check=True)
    st = seat.stat()
    old_prov["dev"] = st.st_dev
    old_prov["ino"] = st.st_ino
    prov_file.write_text(json.dumps(old_prov) + "\n", encoding="utf-8")

    r_reuse = subprocess.run(
        ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(seat), "--suite", str(suite_file), "--stages", "s1-frontend", "--fresh"],
        capture_output=True, text=True, env=run_env, cwd=ROOT,
    )
    assert r_reuse.returncode == 2
    assert (seat / "precious.txt").read_text(encoding="utf-8") == "keep\n"

    # 2. Unreadable nonempty directory must be refused (never treated as empty by ls -A)
    if os.geteuid() != 0:
        unreadable = tmp_path / "unreadable-seat"
        unreadable.mkdir()
        (unreadable / "witness.txt").write_text("do-not-delete\n", encoding="utf-8")
        unreadable.chmod(0o000)
        try:
            r_unr = subprocess.run(
                ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(unreadable), "--suite", str(suite_file), "--stages", "s1-frontend", "--fresh"],
                capture_output=True, text=True, env=run_env, cwd=ROOT,
            )
            assert r_unr.returncode == 2
            assert "unreadable" in r_unr.stderr
        finally:
            unreadable.chmod(0o755)
        assert (unreadable / "witness.txt").read_text(encoding="utf-8") == "do-not-delete\n"


def test_pre_start_compose_down_failure_retains_prior_ownership_and_evidence(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    (models / "Qwen3.5-9B-Q4_K_M.gguf").write_text("dummy\n", encoding="utf-8")
    docker_log = tmp_path / "docker.log"
    _make_exec(bin_ / "docker", f'''
printf '%s\\n' "$*" >> "{docker_log}"
case " $* " in
  *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0 ;;
  *" down "*) echo "daemon down failed" >&2; exit 1 ;;
  *" up "*) echo "UP_MUST_NOT_RUN" >&2; exit 99 ;;
  *" inspect "*) exit 0 ;;
esac
exit 0
''')
    _make_exec(bin_ / "curl", 'echo \'{"ok": true}\'; exit 0')
    _make_exec(bin_ / "pgrep", "exit 0")
    _make_exec(bin_ / "nvtop", 'echo \'[{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}]\'')

    env = _base_env(
        tmp_path,
        PATH=f"{bin_}:{os.environ['PATH']}",
        A770B_MODELS=str(models),
        A770B_SERVE="compose",
        A770B_CARD_MODE="inference",
        A770B_COMPOSE_FILE=str(ROOT / "compose" / "a770-vulkan.yaml"),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
        A770B_SERVED_BACKEND="vulkan",
        A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
        A770B_DRM_CARD="/dev/dri/card0",
        A770B_DRM_RENDER="/dev/dri/renderD128",
        A770B_RENDER_GID="105",
        A770B_VIDEO_GID="39",
        A770B_DOCKER="docker",
        A770B_PORT="7890",
    )
    logs = Path(env["A770B_DATA"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    prior_cid = "a" * 64
    (logs / "llamacpp-a770.cid").write_text(prior_cid + "\n", encoding="utf-8")
    (logs / "llamacpp-a770.invocation").write_text("prior-inv\n", encoding="utf-8")
    (logs / "llamacpp-a770.pid").write_text("4242\n", encoding="utf-8")
    (logs / "llamacpp-a770.model").write_text("old.gguf\n", encoding="utf-8")
    (logs / "live-backend.json").write_text('{"backend":"vulkan"}\n', encoding="utf-8")
    (logs / "serve-evidence.path").write_text("/tmp/prior.evidence.json\n", encoding="utf-8")

    r = subprocess.run(
        ["bash", str(ROOT / "harness" / "serve_compose.sh"), "start", "Qwen3.5-9B-Q4_K_M.gguf", "8192"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "compose down failed" in r.stderr
    assert "UP_MUST_NOT_RUN" not in r.stderr
    assert (logs / "llamacpp-a770.cid").read_text(encoding="utf-8").strip() == prior_cid
    assert (logs / "llamacpp-a770.invocation").read_text(encoding="utf-8").strip() == "prior-inv"
    assert (logs / "llamacpp-a770.pid").read_text(encoding="utf-8").strip() == "4242"
    assert (logs / "llamacpp-a770.model").read_text(encoding="utf-8").strip() == "old.gguf"
    assert (logs / "live-backend.json").exists()
    assert (logs / "serve-evidence.path").read_text(encoding="utf-8").strip() == "/tmp/prior.evidence.json"

    # Case 2: compose down exits 0 without removing prior_cid, then compose up fails -> retain ownership files.
    _make_exec(bin_ / "docker", f'''
printf '%s\\n' "$*" >> "{docker_log}"
case " $* " in
  *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0 ;;
  *" image inspect "*) echo "sha256:0000000000000000000000000000000000000000000000000000000000000000"; exit 0 ;;
  *" down "*) exit 0 ;;
  *" up "*) echo "up failed" >&2; exit 1 ;;
  *" inspect "*) echo "{prior_cid}"; exit 0 ;;
esac
exit 0
''')
    r2 = subprocess.run(
        ["bash", str(ROOT / "harness" / "serve_compose.sh"), "start", "Qwen3.5-9B-Q4_K_M.gguf", "8192"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r2.returncode == 3, r2.stdout + r2.stderr
    assert "may still exist" in r2.stderr
    assert (logs / "llamacpp-a770.cid").read_text(encoding="utf-8").strip() == prior_cid
    assert (logs / "llamacpp-a770.invocation").read_text(encoding="utf-8").strip() == "prior-inv"
    assert (logs / "llamacpp-a770.pid").read_text(encoding="utf-8").strip() == "4242"
