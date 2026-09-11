#!/usr/bin/env python3
"""Tests for the container backend (A770B_SERVE=compose): harness/serve_compose.sh and harness/compose_override.py.

They never run docker, never open the card and never bind a port. A fake `docker`, `curl`, `pgrep` and `nvtop` on
PATH record what the script asked for and answer the canned lines it reads, so the wiring rules can be checked
offline: the profile row becomes llama-server argv, the envelope is the compose file, the VRAM cap and the
live-backend sidecar stay on the host, the start path recreates behind the server entrypoint, and a bench is a
one-shot /app/llama-bench in the same image.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "harness" / "serve_compose.sh"
OVERRIDE_PY = ROOT / "harness" / "compose_override.py"
ENVELOPE = ROOT / "compose" / "a770-vulkan.yaml"
ENV_EXAMPLE = ROOT / "compose" / "a770-vulkan.env.example"
GGUF = "Qwen3.5-9B-Q4_K_M.gguf"


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _fake_runtime(tmp_path: Path, *, ps_id: str = "cid123", host_pid: str = "4242", running: bool = True,
                  down_clears: bool = True):
    """A bin dir with fake docker/curl/pgrep/nvtop; returns (bin_dir, docker_log_path).

    The fake docker is stateful: `up` marks a container up, `down` clears it (unless down_clears is False), and
    `ps -q`/`inspect` answer only while it is up, so a stop really stops and a recreate really recreates.
    """
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    state = tmp_path / "docker.state"
    if running:
        state.write_text("up", encoding="utf-8")
    down_body = f': > "{state}"; exit 0;;' if down_clears else 'exit 0;;'
    _make_bin(bin_, "docker", f'''printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0;;
  *" down"*) {down_body}
  *" up "*) printf up > "{state}"; exit 0;;
  *" ps -q "*) [ -s "{state}" ] && printf '%s\\n' "{ps_id}"; exit 0;;
  *" inspect "*) [ -s "{state}" ] && printf '%s\\n' "{host_pid}"; exit 0;;
  *) exit 0;;
esac''')
    _make_bin(bin_, "curl", 'echo \'{"ok": true}\'; exit 0')
    _make_bin(bin_, "pgrep", 'printf "%s\\n" ${FAKE_PGREP:-}')
    _make_bin(bin_, "nvtop", 'echo \'[{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}]\'')
    return bin_, log


def _env(tmp_path: Path, bin_=None, **overrides) -> dict:
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    (models / GGUF).write_text("dummy\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(tmp_path / "data"),
        A770B_REFUSE="/nonexistent",
        A770B_SERVE="compose",
        A770B_CARD_MODE="inference",
        A770B_MODELS=str(models),
        A770B_ALLOW_NO_NVTOP="1",
        A770B_MIN_AVAIL_MB="1",                 # keep the budget-gate tests hermetic on a small CI box
        A770B_API_KEY_FILE=str(tmp_path / "api.key"),
        A770B_COMPOSE_FILE=str(ENVELOPE),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "a770-vulkan.env"),
        # the envelope's image/DRM/GID values, as the gitignored env file would supply them
        A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
        A770B_DRM_CARD="/dev/dri/card0",
        A770B_DRM_RENDER="/dev/dri/renderD128",
        A770B_RENDER_GID="105",
        A770B_VIDEO_GID="39",
        A770B_DOCKER="docker",
        A770B_PORT="8093",
        FAKE_DOCKER_LOG=str(tmp_path / "docker.log"),
        FAKE_PGREP="",
    )
    if bin_ is not None:
        env["PATH"] = f"{bin_}:{env['PATH']}"
    env.update(overrides)
    return env


def _run(env, *args):
    return subprocess.run(["bash", str(SERVE), *args], capture_output=True, text=True, cwd=ROOT, env=env)


def _argv_lines(stdout: str):
    return [ln[len("argv: "):] for ln in stdout.splitlines() if ln.startswith("argv: ")]


def test_plan_builds_the_container_argv_and_touches_no_docker(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    r = _run(_env(tmp_path, bin_), "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    assert "entrypoint: /app/llama-server" in r.stdout
    argv = _argv_lines(r.stdout)
    # the binary is the ENTRYPOINT; it must not also be an argv word, or compose runs entrypoint + command and
    # execs /app/llama-server /app/llama-server …
    assert argv[0] == "-m" and "/app/llama-server" not in argv
    assert f"/models/{GGUF}" in argv
    assert "/run/a770b/api.key" in argv
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "8080"
    assert argv[argv.index("-ub") + 1] == "512"
    assert "--no-mmap" in argv
    assert not log.exists(), "plan must not invoke docker"


def test_plan_carries_the_thinking_controls_and_the_extra_words(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, THINKING_MODE="on", THINKING_EFFORT="low")
    r = _run(env, "plan", GGUF, "4096", "--temp", "1.0")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning") + 1] == "on"
    assert argv[argv.index("--reasoning-effort") + 1] == "low"
    assert argv[argv.index("--temp") + 1] == "1.0"


def test_override_is_json_with_the_server_entrypoint_and_the_argv(tmp_path):
    out = tmp_path / "ov.json"
    argv = ["-m", "/models/x.gguf", "--chat-template-kwargs", '{"reasoning_effort":"low"}']
    r = subprocess.run([sys.executable, str(OVERRIDE_PY), "--out", str(out), "--", *argv],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["services"]["llama"]["entrypoint"] == ["/app/llama-server"]
    assert doc["services"]["llama"]["command"] == argv


def test_start_recreates_never_no_recreate_and_writes_the_host_pid(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_)
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    up = [c for c in calls if " up " in f" {c} "]
    assert up, calls
    assert any("--force-recreate" in c for c in up), calls
    assert not any("--no-recreate" in c for c in calls), calls
    ov = json.loads((Path(env["A770B_DATA"]) / "logs" / "compose-argv.override.json").read_text(encoding="utf-8"))
    svc = ov["services"]["llama"]
    cmd = svc["command"]
    # entrypoint + command must be ONE invocation: the binary is the entrypoint, never the first argv word
    assert svc["entrypoint"] == ["/app/llama-server"]
    assert cmd[0] == "-m" and f"/models/{GGUF}" in cmd and "/app/llama-server" not in cmd
    pidf = Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.pid"
    assert pidf.read_text(encoding="utf-8").strip() == "4242"


def test_stop_runs_compose_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    r = _run(_env(tmp_path, bin_), "stop")
    assert r.returncode == 0, r.stderr
    assert any(" down" in f" {c} " for c in log.read_text(encoding="utf-8").splitlines())


def test_stop_reports_still_running_when_down_leaves_a_container(tmp_path):
    # a daemon that accepts `down` but leaves the container running must not be reported as a clean stop
    bin_, _ = _fake_runtime(tmp_path, down_clears=False)
    r = _run(_env(tmp_path, bin_), "stop")
    assert r.returncode == 3
    assert "still running" in r.stderr


def test_start_refuses_early_when_the_envelope_values_are_missing(tmp_path):
    # env file absent and none of the five envelope values set: refuse with the named fix, before any docker call
    bin_, log = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_LLAMA_IMAGE="", A770B_DRM_CARD="", A770B_DRM_RENDER="",
               A770B_RENDER_GID="", A770B_VIDEO_GID="")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 2
    assert "a770-vulkan.env.example" in r.stderr
    assert not log.exists()


def test_start_fails_when_the_container_reports_no_live_pid(tmp_path):
    # `docker inspect` of a dead container prints 0; an empty inspect output is the same class of failure. Neither
    # may be accepted as a live server (a pidfile of 0 makes kill -0 0 always succeed).
    for dead in ("0", ""):
        d = tmp_path / f"dead-{dead or 'empty'}"
        d.mkdir()
        bin_, _ = _fake_runtime(d, host_pid=dead)
        r = _run(_env(d, bin_), "start", GGUF, "8192")
        assert r.returncode == 3, (dead, r.stdout, r.stderr)
        assert not (Path(d) / "data" / "logs" / "llamacpp-a770.pid").exists()


def test_bench_is_a_one_shot_llama_bench_in_the_same_image(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    r = _run(_env(tmp_path, bin_), "bench", "--", "-m", "/models/x.gguf", "-ngl", "99", "-o", "json")
    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    run = [c for c in calls if " run " in f" {c} "]
    assert run, calls
    assert "--entrypoint /app/llama-bench" in run[0]
    assert "--rm" in run[0] and "--no-deps" in run[0]


def _budget_gate(env):
    return subprocess.run(["bash", "-c", '. harness/env.sh; . harness/guard.sh; budget_gate'],
                          capture_output=True, text=True, cwd=ROOT, env=env)


def test_budget_gate_compose_treats_its_own_container_as_not_foreign(tmp_path):
    bin_, _ = _fake_runtime(tmp_path, ps_id="cid", host_pid="4242")
    r = _budget_gate(_env(tmp_path, bin_, FAKE_PGREP="4242"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "other llama-server" in r.stdout and "none" in r.stdout


def test_budget_gate_compose_flags_a_foreign_llama_server(tmp_path):
    bin_, _ = _fake_runtime(tmp_path, ps_id="cid", host_pid="4242")
    r = _budget_gate(_env(tmp_path, bin_, FAKE_PGREP="9999"))
    assert r.returncode != 0
    assert "other llama-server" in r.stdout and "outside this compose project" in r.stdout


def test_envelope_sets_the_server_entrypoint_binds_loopback_and_bakes_no_model():
    text = ENVELOPE.read_text(encoding="utf-8")
    assert 'entrypoint: ["/app/llama-server"]' in text
    assert "127.0.0.1:${A770B_PORT}:8080" in text
    assert "/run/a770b/api.key" in text
    assert "${A770B_LLAMA_IMAGE}" in text
    assert "${A770B_MODEL}" not in text and "${A770B_CTX}" not in text
    # SELinux: a user-home bind mount is denied without the :z relabel suffix
    assert "${A770B_MODELS}:/models:ro,z" in text
    assert "${A770B_API_KEY_FILE}:/run/a770b/api.key:ro,z" in text


def test_envelope_devices_use_long_syntax_for_colon_bearing_paths():
    # the host node is a PCI by-path whose name contains a colon (pci-0000:0b:00.0-render); compose's short
    # `host:container` form is then ambiguous and it refuses with "confusing device mapping, please use long syntax"
    text = ENVELOPE.read_text(encoding="utf-8")
    assert 'source: "${A770B_DRM_CARD}"' in text and "target: /dev/dri/card0" in text
    assert 'source: "${A770B_DRM_RENDER}"' in text and "target: /dev/dri/renderD128" in text
    assert "${A770B_DRM_CARD}:/dev/dri" not in text
    # permissions is the cgroup access field; omitting it leaves runc's rule empty
    assert text.count('permissions: "rwm"') == 2


def test_compose_env_example_bakes_no_model_or_ctx():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "A770B_MODEL=" not in text and "A770B_CTX=" not in text
    assert "full-vulkan" in text and "getent" in text
