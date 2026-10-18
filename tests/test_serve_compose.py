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
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "harness" / "serve_compose.sh"
OVERRIDE_PY = ROOT / "harness" / "compose_override.py"
ENVELOPE = ROOT / "compose" / "a770-vulkan.yaml"
ENV_EXAMPLE = ROOT / "compose" / "a770-vulkan.env.example"
SYCL_ENVELOPE = ROOT / "compose" / "a770-sycl.yaml"
SYCL_ENV_EXAMPLE = ROOT / "compose" / "a770-sycl.env.example"
VLLM_ENVELOPE = ROOT / "compose" / "vllm.yaml"
VLLM_ENV_EXAMPLE = ROOT / "compose" / "vllm.env.example"
GGUF = "Qwen3.5-9B-Q4_K_M.gguf"


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _fake_runtime(tmp_path: Path, *, ps_id: str = "cid123", host_pid: str = "4242", running: bool = True,
                  down_clears: bool = True, up_fails: bool = False, exited: bool = False):
    """A bin dir with fake docker/curl/pgrep/nvtop; returns (bin_dir, docker_log_path).

    The fake docker is stateful: `up` marks a container up, `down` clears it (unless down_clears is False), and
    `ps -q` answers only while it is up, so a stop really stops and a recreate really recreates.
    `exited=True` makes `inspect` print pid 0 while `ps --all` still lists the container.
    """
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    state = tmp_path / "docker.state"
    if running:
        state.write_text("up", encoding="utf-8")
    down_body = f': > "{state}"; exit 0;;' if down_clears else 'exit 0;;'
    up_body = 'echo "docker compose up failed" >&2; exit 1;;' if up_fails else f'printf up > "{state}"; exit 0;;'
    ps_body = 'exit 0;;' if exited else f'[ -s "{state}" ] && printf \'%s\\n\' "{ps_id}"; exit 0;;'
    inspect_pid = "0" if exited else host_pid
    ps_all_body = f'[ -s "{state}" ] && printf \'%s\\n\' "{ps_id}"; exit 0;;'
    # logs succeeds only while the container state file is present, and only for
    # the id ps --all would have printed. A copy after down must not stay green.
    logs_arm = (
        f'[ -s "{state}" ] || exit 1; '
        f'case " $* " in *" logs {ps_id} "*) printf \'%s\\n\' "engine refused the model"; exit 0;; '
        f'*) exit 1;; esac;;'
    )
    _make_bin(bin_, "docker", f'''printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0;;
  *" down"*) {down_body}
  *" up "*) {up_body}
  *" ps --all -q "*|*" ps -q --all "*|*" ps "*" --all "*) {ps_all_body}
  *" ps -q "*) {ps_body}
  *" inspect "*) [ -s "{state}" ] && printf '%s\\n' "{inspect_pid}"; exit 0;;
  *" logs "*) {logs_arm}
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
        A770B_GPU_MATCH="DG2",                  # matches the fake nvtop's "Intel Arc A770 DG2"
        A770B_VRAM_CAP_GIB="15.3",              # the card knob serve's post-load cap check reads
        A770B_MIN_AVAIL_MB="1",                 # keep the budget-gate tests hermetic on a small CI box
        A770B_API_KEY_FILE=str(tmp_path / "api.key"),
        A770B_COMPOSE_FILE=str(ENVELOPE),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "a770-vulkan.env"),
        A770B_SERVED_BACKEND="vulkan",
        # the envelope's image/DRM/GID values, as the gitignored env file would supply them
        A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
        A770B_DRM_CARD="/dev/dri/card0",
        A770B_DRM_RENDER="/dev/dri/renderD128",
        A770B_RENDER_GID="105",
        A770B_VIDEO_GID="39",
        A770B_DOCKER="docker",
        A770B_PORT="7890",
        A770B_PROFILES_FILE="",
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
    # --no-mmap was removed upstream (the image rejects it); --load-mode none is the replacement
    assert "--no-mmap" not in argv
    assert argv[argv.index("--load-mode") + 1] == "none"
    assert not log.exists(), "plan must not invoke docker"


def test_plan_carries_metrics_and_verbosity_4_on_vulkan_and_sycl_never_on_vllm(tmp_path):
    # C11-W2: --metrics -lv 4 feeds the stage-counter diff and the engine-evidence log parse; both llama.cpp
    # backends this harness serves carry it, and it must never leak into the vLLM argv (a different function,
    # _build_argv_vllm, builds that one)
    bin_, _ = _fake_runtime(tmp_path)
    r_vk = _run(_env(tmp_path, bin_, A770B_SERVED_BACKEND="vulkan"), "plan", GGUF, "8192")
    assert r_vk.returncode == 0, r_vk.stderr
    argv_vk = _argv_lines(r_vk.stdout)
    assert "--metrics" in argv_vk
    assert argv_vk[argv_vk.index("-lv") + 1] == "4"

    d2 = tmp_path / "sycl"; d2.mkdir()
    bin2, _ = _fake_runtime(d2)
    r_sycl = _run(_env(d2, bin2, A770B_SERVED_BACKEND="sycl"), "plan", GGUF, "8192")
    assert r_sycl.returncode == 0, r_sycl.stderr
    argv_sycl = _argv_lines(r_sycl.stdout)
    assert "--metrics" in argv_sycl
    assert argv_sycl[argv_sycl.index("-lv") + 1] == "4"

    d3 = tmp_path / "vllm"; d3.mkdir()
    bin3, _ = _fake_runtime(d3)
    env_vllm = _env(
        d3, bin3, A770B_SERVED_BACKEND="vllm", A770B_CARD_VRAM_TOTAL="32", A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq", TOOL_PARSER="qwen3_coder",
    )
    r_vllm = _run(env_vllm, "plan", GGUF, "8192")
    assert r_vllm.returncode == 0, r_vllm.stderr
    argv_vllm = _argv_lines(r_vllm.stdout)
    assert "--metrics" not in argv_vllm and "-lv" not in argv_vllm


def test_plan_carries_the_thinking_controls_and_the_extra_words(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, THINKING_MODE="on", THINKING_EFFORT="low")
    r = _run(env, "plan", GGUF, "4096", "--temp", "1.0")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning") + 1] == "on"
    assert argv[argv.index("--reasoning-effort") + 1] == "low"
    assert argv[argv.index("--temp") + 1] == "1.0"


def test_plan_passes_the_budget_message_when_a_budget_is_set(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, THINKING_MODE="on", THINKING_BUDGET="10000")
    r = _run(env, "plan", GGUF, "4096")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning-budget") + 1] == "10000"
    # the plan prints each argv word with %q, so the spaces (and commas) in the one message word come back escaped
    assert argv[argv.index("--reasoning-budget-message") + 1].replace("\\", "") == "budget spent, answer now"


def test_plan_omits_the_budget_message_when_no_budget_is_set(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, THINKING_MODE="on")
    r = _run(env, "plan", GGUF, "4096")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert "--reasoning-budget-message" not in argv


def test_plan_honours_an_explicit_budget_message_override(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, THINKING_MODE="on", THINKING_BUDGET="10000", THINKING_BUDGET_MESSAGE="answer-now")
    r = _run(env, "plan", GGUF, "4096")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning-budget-message") + 1] == "answer-now"


def test_override_is_json_with_the_server_entrypoint_and_the_argv(tmp_path):
    out = tmp_path / "ov.json"
    argv = ["-m", "/models/x.gguf", "--chat-template-kwargs", '{"reasoning_effort":"low"}']
    r = subprocess.run([sys.executable, str(OVERRIDE_PY), "--entrypoint", "/app/llama-server", "--out", str(out), "--", *argv],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["services"]["llama"]["entrypoint"] == ["/app/llama-server"]
    assert doc["services"]["llama"]["command"] == argv

    # when --entrypoint is empty or absent, entrypoint is omitted so the envelope's entrypoint stands
    out_no_ep = tmp_path / "ov_no_ep.json"
    r2 = subprocess.run([sys.executable, str(OVERRIDE_PY), "--out", str(out_no_ep), "--", *argv],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    doc2 = json.loads(out_no_ep.read_text(encoding="utf-8"))
    assert "entrypoint" not in doc2["services"]["llama"]
    assert doc2["services"]["llama"]["command"] == argv

    out_blank = tmp_path / "ov_blank.json"
    r3 = subprocess.run([sys.executable, str(OVERRIDE_PY), "--entrypoint", "", "--out", str(out_blank), "--", *argv],
                        capture_output=True, text=True)
    assert r3.returncode == 0, r3.stderr
    doc3 = json.loads(out_blank.read_text(encoding="utf-8"))
    assert "entrypoint" not in doc3["services"]["llama"]
    assert doc3["services"]["llama"]["command"] == argv


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
    _assert_start_line_names_cap_once(r.stdout, env["A770B_VRAM_CAP_GIB"])


def test_stop_runs_compose_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    r = _run(_env(tmp_path, bin_), "stop")
    assert r.returncode == 0, r.stderr
    assert any(" down" in f" {c} " for c in log.read_text(encoding="utf-8").splitlines())


def test_stop_sglang_parses_when_the_model_dir_is_empty(tmp_path):
    """compose ps and down both parse the volume. An empty model dir is not a spec, and stop still reaches both."""
    log = tmp_path / "docker.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _make_bin(bin_, "docker", f'''case " $* " in
  *" compose "*)
    if [ -z "${{A770B_SGLANG_MODEL_DIR:-}}" ]; then exit 1; fi
    printf '%s\\n' "$*" >> "{log}"
    exit 0;;
esac
exit 0''')
    _make_bin(bin_, "curl", "echo '{}'; exit 0")
    _make_bin(bin_, "pgrep", "exit 0")
    _make_bin(bin_, "nvtop", "echo '[]'")
    yaml_before = SGLANG_YAML.read_text(encoding="utf-8")
    example_before = SGLANG_ENV_EXAMPLE.read_text(encoding="utf-8")
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "no-sglang.env"),
        A770B_SGLANG_MODEL_DIR="",
        A770B_SGLANG_MODEL_NAME="model",
    )
    r = _run(env, "stop")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(c.startswith("compose ") and " ps " in f" {c} " for c in calls), calls
    assert any(" down" in f" {c} " for c in calls), calls
    assert SGLANG_YAML.read_text(encoding="utf-8") == yaml_before
    assert SGLANG_ENV_EXAMPLE.read_text(encoding="utf-8") == example_before


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


def test_start_refuses_an_unset_or_non_numeric_vram_cap(tmp_path):
    # the cap has no default: an empty or non-finite value must refuse before the container starts, never fail
    # open on the float comparison after it is up
    for bad in ("", "abc", "nan", "inf", "15.3.3"):
        d = tmp_path / f"cap-{bad or 'empty'}"
        d.mkdir()
        bin_, log = _fake_runtime(d)
        r = _run(_env(d, bin_, A770B_VRAM_CAP_GIB=bad), "start", GGUF, "8192")
        assert r.returncode == 2, (bad, r.stdout, r.stderr)
        assert "A770B_VRAM_CAP_GIB" in r.stderr, (bad, r.stderr)


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


def test_start_reports_a_failed_up_plainly(tmp_path):
    bin_, log = _fake_runtime(tmp_path, up_fails=True)
    r = _run(_env(tmp_path, bin_), "start", GGUF, "8192")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "⛔ docker compose up failed" in r.stderr
    assert any(" up " in f" {c} " for c in log.read_text(encoding="utf-8").splitlines())


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


def test_compose_env_example_documents_the_pinned_digest_form():
    # the tracked example must make the digest the documented default, not the floating tag: a stranger who copies
    # it pins the exact bytes they pulled, and doctor's warning stops the moment the line carries @sha256:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "A770B_LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:" in text
    assert "A770B_LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:full-vulkan\n" not in text
    assert "RepoDigests" in text


def test_sycl_envelope_sets_the_server_entrypoint_and_bakes_no_model():
    text = SYCL_ENVELOPE.read_text(encoding="utf-8")
    assert 'source /opt/intel/oneapi/setvars.sh --force' in text
    assert 'exec /app/llama-server' in text
    assert "127.0.0.1:${A770B_PORT}:8080" in text
    assert "${A770B_LLAMA_IMAGE}" in text
    assert "${A770B_MODEL}" not in text and "${A770B_CTX}" not in text
    assert "${A770B_MODELS}:/models:ro,z" in text
    assert "${A770B_API_KEY_FILE}:/run/a770b/api.key:ro,z" in text


def test_sycl_envelope_uses_the_sycl_device_selector_and_hides_the_neighbour():
    text = SYCL_ENVELOPE.read_text(encoding="utf-8")
    assert "ONEAPI_DEVICE_SELECTOR" in text
    # the Vulkan-only env vars must not leak into the SYCL envelope
    assert "MESA_VK_DEVICE_SELECT" not in text and "GGML_VK_DISABLE_COOPMAT" not in text
    # long-syntax devices, only the A770's two nodes (this is what hides the neighbour card under Level Zero)
    assert 'source: "${A770B_DRM_CARD}"' in text and "target: /dev/dri/card0" in text
    assert 'source: "${A770B_DRM_RENDER}"' in text and "target: /dev/dri/renderD128" in text
    assert text.count('permissions: "rwm"') == 2


def test_sycl_env_example_names_the_sycl_image_and_bakes_no_model():
    text = SYCL_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "A770B_MODEL=" not in text and "A770B_CTX=" not in text
    assert "full-intel" in text and "getent" in text
    assert "A770B_ONEAPI_DEVICE_SELECTOR" in text


def test_plan_vllm_builds_argv_with_quantization_model_len_gpu_utilization_and_key_value(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
        TOOL_PARSER="qwen3_coder",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    assert "entrypoint: " in r.stdout
    argv = _argv_lines(r.stdout)
    assert f"/models/{GGUF}" in argv
    assert argv[argv.index("--quantization") + 1] == "gptq"
    assert argv[argv.index("--max-model-len") + 1] == "8192"
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.9"
    assert "--enable-auto-tool-choice" in argv
    assert argv[argv.index("--tool-call-parser") + 1] == "qwen3_coder"
    assert "--api-key" not in argv, "vLLM API key must not sit in the container argv"
    assert not any("a770b-" in a for a in argv)
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "8000"
    assert not log.exists(), "plan must not invoke docker"
    # the envelope carries the shim entrypoint that sources the key from /run/a770b/api.key
    envelope_text = VLLM_ENVELOPE.read_text(encoding="utf-8")
    assert 'entrypoint: ["/bin/bash", "-c", "VLLM_API_KEY=\\"$(cat /run/a770b/api.key)\\" || exit 1; export VLLM_API_KEY; source /opt/intel/oneapi/setvars.sh --force >/dev/null || exit 1; exec vllm serve \\"$@\\"", "vllm-serve"]' in envelope_text
    assert "2>/dev/null" not in envelope_text
    # and the override document generated for vllm omits the entrypoint key so the shim stands
    out_ov = tmp_path / "ov.json"
    r_ov = subprocess.run([sys.executable, str(OVERRIDE_PY), "--out", str(out_ov), "--", *argv],
                          capture_output=True, text=True)
    assert r_ov.returncode == 0, r_ov.stderr
    doc = json.loads(out_ov.read_text(encoding="utf-8"))
    assert "entrypoint" not in doc["services"]["llama"]
    assert doc["services"]["llama"]["command"] == argv
    assert "--api-key" not in doc["services"]["llama"]["command"]
    assert not any("a770b-" in str(x) for x in doc["services"]["llama"]["command"])


def test_plan_vllm_computes_gpu_memory_utilization_fraction_to_three_decimals(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="16.0",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_QUANT="gptq",
        TOOL_PARSER="qwen3_coder",
    )
    r = _run(env, "plan", GGUF, "4096")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    # The engine target stays 0.9. It is not cap/total: flooring 15.3/16 used to yield 0.956,
    # and a tighter cap must not shrink this flag.
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.9"
    assert argv[argv.index("--gpu-memory-utilization") + 1] != "1.0"
    assert argv[argv.index("--max-model-len") + 1] == "4096"


def test_dispatch_refuses_an_unknown_served_backend(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_SERVED_BACKEND="cuda")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "backend 'cuda' is not one this harness serves" in r.stderr
    assert "must be one of: vulkan, sycl, vllm, sglang" in r.stderr


def test_dispatch_refuses_an_empty_served_backend(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_SERVED_BACKEND="")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "A770B_SERVED_BACKEND is unset or empty — must be one of: vulkan, sycl, vllm, sglang" in r.stderr


def test_vllm_envelope_sets_the_server_entrypoint_and_bakes_no_model():
    text = VLLM_ENVELOPE.read_text(encoding="utf-8")
    assert 'entrypoint: ["/bin/bash", "-c", "VLLM_API_KEY=\\"$(cat /run/a770b/api.key)\\" || exit 1; export VLLM_API_KEY; source /opt/intel/oneapi/setvars.sh --force >/dev/null || exit 1; exec vllm serve \\"$@\\"", "vllm-serve"]' in text
    assert "2>/dev/null" not in text
    assert "127.0.0.1:${A770B_PORT}:8000" in text
    assert "${A770B_VLLM_IMAGE:-intel/llm-scaler-vllm@sha256:52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b}" in text
    assert "${A770B_MODEL}" not in text and "${A770B_CTX}" not in text
    assert "${A770B_MODELS}:/models:ro,z" in text
    assert "${A770B_API_KEY_FILE}:/run/a770b/api.key:ro,z" in text
    assert "${A770B_BY_PATH_DIR}:/dev/dri/by-path:ro" in text


def test_vllm_env_example_documents_filtered_by_path_dir_and_trap():
    text = VLLM_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "A770B_MODEL=" not in text and "A770B_CTX=" not in text
    assert "A770B_VLLM_IMAGE=intel/llm-scaler-vllm@sha256:52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b" in text
    assert "A770B_VLLM_IMAGE=intel/vllm" not in text
    assert "getent" in text
    assert "TRAP" in text
    assert "A770B_BY_PATH_DIR=" in text
    assert "A770B_BY_PATH_DIR=/dev/dri/by-path\n" not in text


def test_vllm_key_shim_fails_closed_when_key_unreadable():
    r = subprocess.run(
        ["/bin/sh", "-c", 'VLLM_API_KEY="$(cat /nonexistent/path/to/api.key)" || exit 1; export VLLM_API_KEY; echo "STARTED_UNAUTHENTICATED"'],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "STARTED_UNAUTHENTICATED" not in r.stdout


def test_plan_vllm_carries_the_tool_choice_flags_when_tool_parser_is_set(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
        TOOL_PARSER="qwen3_coder",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert "--enable-auto-tool-choice" in argv
    assert argv[argv.index("--tool-call-parser") + 1] == "qwen3_coder"


def test_plan_vllm_refuses_when_tool_parser_is_unset(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
    )
    env.pop("TOOL_PARSER", None)
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert (
        "⛔ the vllm backend needs a tool-call parser for the coding agent "
        "(set the row's tool_parser, or A770B_CANDIDATE_TOOL_PARSER for a candidate) "
        "— read the model's chat template" in r.stderr
    )


def test_plan_vllm_refuses_a_malformed_tool_parser(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
        TOOL_PARSER="bad;value",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "needs a tool-call parser" in r.stderr


def _vllm_plan_env(tmp_path, bin_, **overrides):
    """The env the existing vLLM plan tests set, with the reasoning pair unset."""
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
        TOOL_PARSER="qwen3_coder",
    )
    env.pop("THINKING_MODE", None)
    env.pop("REASONING", None)
    env.pop("REASONING_PARSER", None)
    env.update(overrides)
    return env


_VLLM_OFF_JSON = r'\{\"enable_thinking\":\ false\}'


def _assert_vllm_reasoning_off(argv):
    assert "--default-chat-template-kwargs" in argv
    flag = argv.index("--default-chat-template-kwargs")
    assert argv[flag + 1] == _VLLM_OFF_JSON
    assert "--reasoning-parser" not in argv
    assert "--reasoning-config" not in argv
    assert "--reasoning-budget" not in argv
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.9"
    assert argv[argv.index("--port") + 1] == "8000"


def test_plan_vllm_reasoning_off_sends_the_template_switch(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, THINKING_MODE="off", REASONING="on")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    _assert_vllm_reasoning_off(_argv_lines(r.stdout))


def test_plan_vllm_reasoning_off_from_reasoning_when_thinking_mode_is_unset(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, REASONING="off")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    _assert_vllm_reasoning_off(_argv_lines(r.stdout))


def test_plan_vllm_unset_reasoning_defaults_off(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_)
    assert "THINKING_MODE" not in env and "REASONING" not in env
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    _assert_vllm_reasoning_off(_argv_lines(r.stdout))


def test_plan_vllm_reasoning_on_names_the_parser(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, THINKING_MODE="on", REASONING_PARSER="qwen3")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning-parser") + 1] == "qwen3"
    assert "enable_thinking" not in "\n".join(argv)
    assert "--default-chat-template-kwargs" not in argv
    assert "--reasoning-config" not in argv
    assert "--reasoning-budget" not in argv
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.9"
    assert argv[argv.index("--port") + 1] == "8000"


def test_plan_vllm_reasoning_on_refuses_an_empty_parser(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, THINKING_MODE="on", REASONING_PARSER="")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode != 0
    assert "vLLM" in r.stderr
    assert "sglang" not in r.stderr


def test_plan_vllm_reasoning_on_refuses_parser_auto(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, THINKING_MODE="on", REASONING_PARSER="auto")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode != 0
    assert "vLLM" in r.stderr
    assert "sglang" not in r.stderr
    assert "--reasoning-parser" not in r.stdout


def test_plan_vllm_empty_thinking_mode_falls_through_to_reasoning_on(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(
        tmp_path, bin_, THINKING_MODE="", REASONING="on", REASONING_PARSER="qwen3",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert argv[argv.index("--reasoning-parser") + 1] == "qwen3"
    assert "enable_thinking" not in "\n".join(argv)


def test_plan_vllm_empty_thinking_mode_falls_through_to_reasoning_off(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _vllm_plan_env(tmp_path, bin_, THINKING_MODE="", REASONING="off")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    _assert_vllm_reasoning_off(_argv_lines(r.stdout))


def test_plan_vllm_reasoning_auto_exits_nonzero(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    for overrides in (
        {"THINKING_MODE": "auto"},
        {"REASONING": "auto"},
        {"THINKING_MODE": "", "REASONING": ""},
        {"THINKING_MODE": "low"},
    ):
        env = _vllm_plan_env(tmp_path, bin_, **overrides)
        r = _run(env, "plan", GGUF, "8192")
        assert r.returncode != 0, overrides
        assert "vLLM" in r.stderr
        assert "sglang" not in r.stderr
        assert "enable_thinking" not in r.stdout
        assert "--reasoning-parser" not in r.stdout


def test_plan_llama_cpp_argv_never_carries_the_tool_choice_flags(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_SERVED_BACKEND="vulkan", TOOL_PARSER="qwen3_coder")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 0, r.stderr
    argv = _argv_lines(r.stdout)
    assert "--enable-auto-tool-choice" not in argv
    assert "--tool-call-parser" not in argv


def test_plan_vllm_refuses_empty_quantization(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="",
    )
    env.pop("QUANT", None)
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "quantization must be set for the vllm backend" in r.stderr


def test_plan_vllm_preserves_nested_model_path_relative_to_models_dir(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    nested_dir = tmp_path / "models" / "Qwen" / "Qwen3-32B"
    nested_dir.mkdir(parents=True, exist_ok=True)
    env_vllm = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        A770B_QUANT="gptq",
        TOOL_PARSER="qwen3_coder",
    )
    r_vllm = _run(env_vllm, "plan", "Qwen/Qwen3-32B", "8192")
    assert r_vllm.returncode == 0, r_vllm.stderr
    argv_vllm = _argv_lines(r_vllm.stdout)
    assert argv_vllm[0] == "/models/Qwen/Qwen3-32B"

    # for llama/vulkan, nested directories still collapse to basename
    env_vulkan = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vulkan",
    )
    r_vulkan = _run(env_vulkan, "plan", "Qwen/Qwen3-32B", "8192")
    assert r_vulkan.returncode == 0, r_vulkan.stderr
    argv_vulkan = _argv_lines(r_vulkan.stdout)
    assert f"/models/{nested_dir.name}" in argv_vulkan
    assert "/models/Qwen/Qwen3-32B" not in argv_vulkan


def test_plan_vllm_refuses_vram_cap_exceeding_card_total(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_CARD_VRAM_TOTAL="16.0",
        A770B_VRAM_CAP_GIB="20.0",
        A770B_QUANT="gptq",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "A770B_VRAM_CAP_GIB must be a number no larger than A770B_CARD_VRAM_TOTAL" in r.stderr


def test_start_vllm_succeeds_with_empty_http_200_health_and_no_entrypoint(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    # vLLM /health returns an empty HTTP 200 (no body)
    _make_bin(bin_, "curl", "exit 0")
    by_path = tmp_path / "by-path"
    by_path.mkdir(exist_ok=True)
    hf_dir = tmp_path / "models" / "Qwen" / "Qwen3-32B"
    hf_dir.mkdir(parents=True, exist_ok=True)
    env = _env(
        tmp_path,
        bin_,
        A770B_SERVED_BACKEND="vllm",
        A770B_COMPOSE_FILE=str(VLLM_ENVELOPE),
        A770B_VLLM_IMAGE="intel/llm-scaler-vllm@sha256:52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b",
        A770B_BY_PATH_DIR=str(by_path),
        A770B_QUANT="gptq",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        TOOL_PARSER="qwen3_coder",
    )
    r = _run(env, "start", "Qwen/Qwen3-32B", "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "▶ started server container" in r.stdout
    _assert_start_line_names_cap_once(r.stdout, env["A770B_VRAM_CAP_GIB"], also="0.9 of the card")
    ov = json.loads((Path(env["A770B_DATA"]) / "logs" / "compose-argv.override.json").read_text(encoding="utf-8"))
    assert "entrypoint" not in ov["services"]["llama"]
    assert ov["services"]["llama"]["command"][0] == "/models/Qwen/Qwen3-32B"
    pidf = Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.pid"
    assert pidf.read_text(encoding="utf-8").strip() == "4242"


def _assert_start_line_names_cap_once(stdout: str, cap: str, also: str | None = None) -> None:
    line = next(row for row in stdout.splitlines() if row.startswith("▶ started"))
    assert line.count(f"cap {cap} GiB") == 1
    assert f"cap {cap} GiB · cap" not in line
    if also is not None:
        assert line.count(also) == 1


def _assert_logs_before_a_later_down(log: Path, ps_id: str = "cid123"):
    calls = log.read_text(encoding="utf-8").splitlines()
    logs_at = [i for i, c in enumerate(calls) if f" logs {ps_id} " in f" {c} "]
    assert logs_at, calls
    assert any(" down" in f" {c} " and i > logs_at[0] for i, c in enumerate(calls)), calls


def _assert_log_kept_before_down(log: Path, stderr: str, data: Path, ps_id: str = "cid123"):
    kept = data / "logs" / "llamacpp-a770.log"
    assert kept.is_file(), stderr
    assert "engine refused the model" in kept.read_text(encoding="utf-8")
    assert "serve log kept:" in stderr
    assert str(kept) in stderr
    _assert_logs_before_a_later_down(log, ps_id)


def test_health_failure_keeps_the_container_log_before_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 1")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="1", A770B_HEALTH_PAUSE="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "did not answer /health" in r.stderr
    assert "health: down" in r.stderr
    assert "▶ started server container" not in r.stdout
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))
    assert not (Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.pid").exists()


def test_health_loading_when_http_succeeds_without_ok(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "echo '{\"status\":\"loading\"}'; exit 0")
    r = _run(_env(tmp_path, bin_, A770B_SERVED_BACKEND="vulkan"), "health")
    assert r.returncode != 0
    assert r.stdout == "loading\n"


def test_health_ready_when_the_body_contains_ok(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    r = _run(_env(tmp_path, bin_, A770B_SERVED_BACKEND="vulkan"), "health")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "ready\n"


def test_health_down_when_curl_fails(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 1")
    r = _run(_env(tmp_path, bin_, A770B_SERVED_BACKEND="sycl"), "health")
    assert r.returncode != 0
    assert r.stdout == "down\n"


def test_health_vllm_or_sglang_http_success_is_ready(tmp_path):
    # Harness contract: HTTP success is ready and the body is not read. Not an image reading.
    bin_, _ = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 0")
    for backend in ("vllm", "sglang"):
        r = _run(_env(tmp_path, bin_, A770B_SERVED_BACKEND=backend), "health")
        assert r.returncode == 0, backend + r.stdout + r.stderr
        assert r.stdout == "ready\n"


def test_start_loading_body_fails_without_the_started_line(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "echo '{\"status\":\"loading\"}'; exit 0")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="1", A770B_HEALTH_PAUSE="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode != 0
    assert "health: loading" in r.stderr
    assert "▶ started server container" not in r.stdout
    assert "did not answer /health" in r.stderr


def test_start_ok_body_prints_health_ready_on_stderr(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="2", A770B_HEALTH_PAUSE="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "health: ready" in r.stderr
    assert "▶ started server container" in r.stdout
    _assert_start_line_names_cap_once(r.stdout, env["A770B_VRAM_CAP_GIB"])


def test_start_prints_an_unchanged_health_class_once(tmp_path):
    bin_, _ = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "echo '{\"status\":\"loading\"}'; exit 0")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="2", A770B_HEALTH_PAUSE="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode != 0
    assert r.stderr.count("health: loading") == 1
    assert "▶ started server container" not in r.stdout


def test_exited_container_log_is_kept_before_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path, exited=True)
    _make_bin(bin_, "curl", "exit 1")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="1", A770B_HEALTH_PAUSE="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "did not answer /health" in r.stderr
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))
    assert not (Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.pid").exists()


def test_symlink_at_the_log_path_is_replaced(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 1")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="1", A770B_HEALTH_PAUSE="0")
    target = tmp_path / "secret"
    target.write_text("keep me\n", encoding="utf-8")
    dest = Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.log"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(target)
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert target.read_text(encoding="utf-8") == "keep me\n"
    assert dest.is_file() and not dest.is_symlink()
    assert "engine refused the model" in dest.read_text(encoding="utf-8")
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))


def test_directory_at_the_log_path_is_not_a_kept_log(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 1")
    env = _env(tmp_path, bin_, A770B_HEALTH_TRIES="1", A770B_HEALTH_PAUSE="0")
    dest = Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.log"
    dest.parent.mkdir(parents=True)
    dest.mkdir()
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "could not be kept" in r.stderr
    assert "serve log kept:" not in r.stderr
    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert list(dest.parent.glob("llamacpp-a770.log.tmp.*")) == []
    _assert_logs_before_a_later_down(log)


def test_vram_stop_keeps_the_container_log_before_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    # 16 GiB used, above the 15.3 GiB cap this env sets. The container is healthy, then stopped.
    _make_bin(
        bin_,
        "nvtop",
        "echo '[{\"device_name\": \"Intel Arc A770 DG2\", \"mem_total\": 17179869184, "
        "\"mem_used\": 17179869184, \"mem_free\": 0}]'",
    )
    env = _env(tmp_path, bin_, A770B_MIN_VRAM_MB="0")
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "VRAM after load" in r.stderr
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))


def _assert_status_filters(log: Path):
    text = log.read_text(encoding="utf-8")
    assert "label=com.docker.compose.project=" in text
    assert "label=com.docker.compose.service=llama" in text


def test_status_names_a_running_container_when_the_pidfile_is_absent(tmp_path):
    bin_, log = _fake_runtime(tmp_path, running=True)
    _make_bin(bin_, "curl", "echo '{}'; exit 0")
    seat = tmp_path / "seat"
    seat.mkdir()
    env = _env(tmp_path, bin_, A770B_SEAT=str(seat))
    script = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
    r = subprocess.run([str(script), "status"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "server: UP · container cid123" in r.stdout
    assert "server: down" not in r.stdout
    _assert_status_filters(log)


def test_doctor_names_a_running_container_when_the_pidfile_is_absent(tmp_path):
    bin_, log = _fake_runtime(tmp_path, running=True)
    _make_bin(bin_, "curl", "echo '{}'; exit 0")
    seat = tmp_path / "seat"
    seat.mkdir()
    env = _env(tmp_path, bin_, A770B_SEAT=str(seat))
    script = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
    r = subprocess.run([str(script), "doctor"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert "ok   mode: inference, not measured (the seat's server is up)" in r.stdout
    _assert_status_filters(log)


def test_status_says_down_when_no_pidfile_and_no_container(tmp_path):
    bin_, _ = _fake_runtime(tmp_path, running=False)
    _make_bin(bin_, "curl", "echo '{}'; exit 0")
    seat = tmp_path / "seat"
    seat.mkdir()
    env = _env(tmp_path, bin_, A770B_SEAT=str(seat))
    script = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
    r = subprocess.run([str(script), "status"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "server: down" in r.stdout
    assert "server: UP" not in r.stdout


_STATUS_MODEL = "NotARegistryModel.gguf"


def _hold(argv):
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _release(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def _mark_serve(data: Path, pid: int, model: str) -> None:
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "llamacpp-a770.pid").write_text(f"{pid}\n", encoding="utf-8")
    (logs / "llamacpp-a770.model").write_text(model + "\n", encoding="utf-8")


def _status(env):
    script = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
    return subprocess.run([str(script), "status"], capture_output=True, text=True, cwd=ROOT, env=env)


def _status_env(tmp_path, bin_):
    seat = tmp_path / "seat"
    seat.mkdir()
    return _env(tmp_path, bin_, A770B_SEAT=str(seat))


def test_status_names_the_model_when_the_recorded_pid_is_llama_server(tmp_path):
    bin_, _ = _fake_runtime(tmp_path, running=False)
    _make_bin(bin_, "curl", "echo '{}'; exit 0")
    live = tmp_path / "live"
    live.mkdir()
    shutil.copy(shutil.which("sleep"), live / "llama-server")
    proc = _hold([str(live / "llama-server"), "60"])
    try:
        env = _status_env(tmp_path, bin_)
        _mark_serve(Path(env["A770B_DATA"]), proc.pid, _STATUS_MODEL)
        r = _status(env)
    finally:
        _release(proc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"server: UP · {_STATUS_MODEL} · pid {proc.pid}" in r.stdout
    assert "server: UP · container" not in r.stdout


def test_status_names_the_model_when_the_recorded_pid_is_the_container_host(tmp_path):
    proc = _hold(["sleep", "60"])
    try:
        bin_, log = _fake_runtime(tmp_path, running=True, host_pid=str(proc.pid))
        _make_bin(bin_, "curl", "echo '{}'; exit 0")
        env = _status_env(tmp_path, bin_)
        _mark_serve(Path(env["A770B_DATA"]), proc.pid, _STATUS_MODEL)
        r = _status(env)
        text = log.read_text(encoding="utf-8")
    finally:
        _release(proc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"server: UP · {_STATUS_MODEL} · pid {proc.pid}" in r.stdout
    assert "server: UP · container" not in r.stdout
    assert "inspect" in text
    assert "pgrep" not in text


def test_status_does_not_name_the_model_for_a_foreign_pid(tmp_path):
    proc = _hold(["sleep", "60"])
    try:
        bin_, _ = _fake_runtime(tmp_path, running=True, host_pid="4242")
        _make_bin(bin_, "curl", "echo '{}'; exit 0")
        env = _status_env(tmp_path, bin_)
        _mark_serve(Path(env["A770B_DATA"]), proc.pid, _STATUS_MODEL)
        r = _status(env)
    finally:
        _release(proc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "server: UP · container cid123" in r.stdout
    assert _STATUS_MODEL not in r.stdout
    assert f"pid {proc.pid}" not in r.stdout


def test_status_does_not_treat_a_zero_container_pid_as_the_serve(tmp_path):
    proc = _hold(["sleep", "60"])
    try:
        bin_, _ = _fake_runtime(tmp_path, running=True, host_pid="0")
        _make_bin(bin_, "curl", "echo '{}'; exit 0")
        env = _status_env(tmp_path, bin_)
        _mark_serve(Path(env["A770B_DATA"]), proc.pid, _STATUS_MODEL)
        r = _status(env)
    finally:
        _release(proc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "server: UP · container cid123" in r.stdout
    assert _STATUS_MODEL not in r.stdout


SGLANG_DIGEST = (
    "ghcr.io/0xsero/sglang-exl3-xpu-flashnext@sha256:"
    "24c872759256d4b0d3b9d0419ebdf6f3547e0fdbd3891fbb9f90e32ad255c94f"
)
SGLANG_YAML = ROOT / "compose" / "b70-sglang.yaml"
SGLANG_ENV_EXAMPLE = ROOT / "compose" / "b70-sglang.env.example"
SGLANG_MODEL = "Qwen3.8-Flash-Next-exl3-3.05bpw"
SGLANG_CTX = 131072
SGLANG_EXTRA = (
    "--trust-remote-code --device xpu --host 0.0.0.0 "
    "--disable-shared-experts-fusion --kv-cache-dtype fp8_e4m3 "
    "--mem-fraction-static 0.85 --chunked-prefill-size 8192 "
    f"--max-total-tokens {SGLANG_CTX} --max-running-requests 4 --dtype bfloat16 "
    "--max-mamba-cache-size 32 --cuda-graph-backend-decode full "
    "--cuda-graph-bs-decode 1 2 4"
)


def _sglang_fixture(path: Path, model_name: str) -> Path:
    doc = {
        "schema": 1,
        "card": "b70",
        "mode": "inference",
        "profiles": {
            "flashnext-fixture": {
                "model": model_name,
                "source": "https://example.com/flashnext-fixture",
                "family": "qwen",
                "architecture": "dense",
                "quant": "exl3",
                "kv": "fp8_e4m3",
                "flash_attention": "off",
                "reasoning": "off",
                "extra": SGLANG_EXTRA,
                "capability_source": "none",
                "use_for": "sglang plan fixture",
                "depth_probe_100k": "not run",
                "category": "dense",
                "weight_class": "9b",
                "measured_on": "test",
                "card": "b70",
                "backend": "sglang",
                "mode": "inference",
                "engine": "sglang",
                "tool_parser": "qwen3_coder",
                "reasoning_parser": "qwen3",
                "ctx": SGLANG_CTX,
                "useful_ctx": 8000,
                "timeout_s": 1500,
                "vram_gib_after_load": 31.9,
                "ram_gb_extra": 0,
                "params_b": {"total": 9, "active": 9},
                "speed": {
                    "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
                    "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
                },
                "capability": {},
                "instrument": "seat: test",
            }
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _sglang_nodes(tmp_path: Path) -> dict:
    dri = tmp_path / "dri"
    by = dri / "by-path"
    by.mkdir(parents=True)
    card = dri / "card1"
    render = dri / "renderD129"
    card.write_text("", encoding="utf-8")
    render.write_text("", encoding="utf-8")
    (by / "pci-0000:0b:00.0-card").symlink_to(card)
    (by / "pci-0000:0b:00.0-render").symlink_to(render)
    return {
        "A770B_DRM_CARD": str(by / "pci-0000:0b:00.0-card"),
        "A770B_DRM_RENDER": str(by / "pci-0000:0b:00.0-render"),
        "A770B_SGLANG_CARD_NODE": str(card.resolve()),
        "A770B_SGLANG_RENDER_NODE": str(render.resolve()),
        "A770B_SGLANG_IMAGE": SGLANG_DIGEST,
        "A770B_RENDER_GID": "105",
        "A770B_VIDEO_GID": "39",
    }


def _plan_sglang_from_fixture(tmp_path, env):
    """Plan through the fixture's exported parsers, quant, ctx, and extra."""
    profile = env["A770B_PROFILES_FILE"]
    model = str(Path(env["A770B_MODELS"]) / SGLANG_MODEL)
    script = f"""
set -euo pipefail
eval "$(python3 "{ROOT}/harness/profiles.py" env --file "{profile}")"
export TOOL_PARSER="$A770B_FLASHNEXT_FIXTURE_TOOL_PARSER"
export REASONING_PARSER="$A770B_FLASHNEXT_FIXTURE_REASONING_PARSER"
export QUANT="$A770B_FLASHNEXT_FIXTURE_QUANT"
# shellcheck disable=SC2086
exec bash "{SERVE}" plan "{SGLANG_MODEL}" "$A770B_FLASHNEXT_FIXTURE_CTX" $A770B_FLASHNEXT_FIXTURE_EXTRA
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=ROOT, env=env), model


def test_sglang_envelope_pins_the_trial_contract():
    text = SGLANG_YAML.read_text(encoding="utf-8")
    example = SGLANG_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "/home/" not in text
    assert "/home/" not in example
    assert SGLANG_DIGEST in text
    assert SGLANG_DIGEST in example
    assert 'entrypoint: ["python3"]' in text
    assert "127.0.0.1:${A770B_PORT}:${A770B_PORT}" in text
    assert "mem_limit: 40g" in text and "memswap_limit: 40g" in text
    assert 'cpuset: "8-23"' in text
    assert "shm_size: 8g" in text
    assert "label=disable" in text
    assert "card0" not in text and "renderD128" not in text
    assert "api.key" not in text and "--api-key" not in text
    for target in (
        "/xfs:ro",
        "/opt/trellis-serve/xpu/exl3xpu/sglang_plugin.py:ro",
        "/opt/trellis-serve/xpu/exl3xpu/nvtier.py:ro",
        "/opt/trellis-serve/xpu/exl3xpu/moe_offload.py:ro",
        "/n104/_moe_a8.so:ro",
        "/n104/qsa_row_sycl.so:ro",
        "/hc:ro",
        "/n107qsa:ro",
        "/opt/trellis-serve/xpu/exl3xpu/qsa_xpu_base.py:ro",
        "/opt/trellis-serve/xpu/exl3xpu/qsa_xpu.py:ro",
        ":/tcache",
        "/models/${A770B_SGLANG_MODEL_NAME}:ro",
    ):
        assert target in text
    for line in (
        "HOME=/tmp",
        "HF_HUB_OFFLINE=1",
        "ZE_AFFINITY_MASK=0",
        "NEOReadDebugKeys=1",
        "EnableSharedSystemUsmSupport=1",
        "EnableRecoverablePageFaults=1",
        "EXL3_MOE_SLOTS=8000",
        "EXL3_MOE_CACHE=1",
        "EXL3_NVTIER_SVM_PREFETCH=1",
        "EXL3_NVTIER_STAGE=1",
        "EXL3_NVTIER_NO_VICTIM=0",
        "EXL3_NVTIER_ASYNC=1",
        "EXL3_HC_DIR=/hc",
        "EXL3_HC_XPU=1",
        "EXL3_HC_MIX=epilogue",
        "EXL3_HC_COMBINE=torch",
        "TRITON_CACHE_DIR=/tcache",
        "EXL3_NVTIER_STAGE_PROF=0",
        "EXL3_NVTIER_STAGE_HOSTBUF=5",
        "EXL3_NVTIER_PREFILL_M=5",
        "EXL3_NVTIER_PROF_EVERY=48",
        "EXL3_NVTIER_FILL_THREADS=8",
        "EXL3_NVTIER=1",
        "EXL3_NVTIER_STORE=/xfs/qwen_experts.bin",
        "EXL3_NVTIER_RAM_GB=20",
        "EXL3_NVTIER_LAYERS=48",
        "EXL3_NGRAM_TIER=nvme",
        "SGLANG_EXL3_NGRAM_RAM_GB=1",
        "SGLANG_EXL3_KV_BITS=5",
        "EXL3_QSA_DENSE_BYTES=134217728",
        "EXL3_QSA_ROWS=64",
        "EXL3_TORCH_MEM_FRACTION=0.95",
        "TORCHINDUCTOR_COMPILE_THREADS=1",
        "EXL3_QSA_IMPL=sycl_row",
        "EXL3_QSA_SELECT=sycl",
        "EXL3_QSA_TRITON_CHECK=4",
        "EXL3_QSA_SELECT_CHECK=4",
        "EXL3_MOE_LIB=/n104/_moe_a8.so",
        "EXL3_QSA_SYCL_LIB=/n104/qsa_row_sycl.so",
        "EXL3_MODEL_PATH=/models/model",
    ):
        assert line in example


def test_plan_sglang_carries_the_trial_server_flags(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    profile = _sglang_fixture(tmp_path / "fixture.json", SGLANG_MODEL)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing-sglang.env"),
        A770B_PROFILES_FILE=str(profile),
        A770B_ALIAS="flashnext",
        A770B_PORT="30260",
        A770B_CARD_VRAM_TOTAL="32",
    )
    (Path(env["A770B_MODELS"]) / SGLANG_MODEL).mkdir()
    r, _model = _plan_sglang_from_fixture(tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "entrypoint: python3" in r.stdout
    argv = _argv_lines(r.stdout)
    assert argv[0] == "-m"
    assert argv[1] == "sglang.launch_server"
    assert "python3" not in argv
    assert "--model-path" in argv
    assert argv[argv.index("--model-path") + 1] == f"/models/{SGLANG_MODEL}"
    assert argv[argv.index("--quantization") + 1] == "exl3"
    assert argv[argv.index("--served-model-name") + 1] == "flashnext"
    assert argv[argv.index("--context-length") + 1] == str(SGLANG_CTX)
    assert argv[argv.index("--port") + 1] == "30260"
    assert argv[argv.index("--tool-call-parser") + 1] == "qwen3_coder"
    assert argv[argv.index("--reasoning-parser") + 1] == "qwen3"
    assert argv[argv.index("--max-total-tokens") + 1] == str(SGLANG_CTX)
    for flag in (
        "--trust-remote-code",
        "--disable-shared-experts-fusion",
    ):
        assert flag in argv
    assert argv[argv.index("--device") + 1] == "xpu"
    assert argv[argv.index("--kv-cache-dtype") + 1] == "fp8_e4m3"
    assert argv[argv.index("--mem-fraction-static") + 1] == "0.85"
    assert argv[argv.index("--chunked-prefill-size") + 1] == "8192"
    assert argv[argv.index("--max-running-requests") + 1] == "4"
    assert argv[argv.index("--dtype") + 1] == "bfloat16"
    assert argv[argv.index("--max-mamba-cache-size") + 1] == "32"
    assert argv[argv.index("--cuda-graph-backend-decode") + 1] == "full"
    bs = argv.index("--cuda-graph-bs-decode")
    assert argv[bs + 1:bs + 4] == ["1", "2", "4"]
    assert "--api-key" not in argv
    assert "compose:" in r.stdout
    # plan prints the compose line; it must not have executed it
    assert not log.exists()


def test_plan_sglang_refuses_auto_and_empty_parsers_before_docker(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    (tmp_path / "models").mkdir(exist_ok=True)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_QUANT="exl3",
        TOOL_PARSER="auto",
        REASONING_PARSER="qwen3",
    )
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refuses --tool-call-parser auto" in r.stderr
    assert not log.exists()

    env["TOOL_PARSER"] = "qwen3_coder"
    env["REASONING_PARSER"] = ""
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "reasoning-parser" in r.stderr
    assert not log.exists()

    env["REASONING_PARSER"] = "bad;value"
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "reasoning-parser" in r.stderr
    assert not log.exists()


def test_start_sglang_missing_env_file_names_the_example_before_docker(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "no-sglang.env"),
        A770B_QUANT="exl3",
        TOOL_PARSER="qwen3_coder",
        REASONING_PARSER="qwen3",
    )
    env.pop("A770B_SGLANG_IMAGE", None)
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no-sglang.env.example" in r.stderr
    assert not log.exists()


def test_start_sglang_refuses_novict_and_a_mismatched_node_before_docker(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    nodes = _sglang_nodes(tmp_path)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "no-sglang.env"),
        A770B_QUANT="exl3",
        TOOL_PARSER="qwen3_coder",
        REASONING_PARSER="qwen3",
        A770B_CARD_VRAM_TOTAL="32",
        NOVICT="1",
        **nodes,
    )
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "victim write-back stays on" in r.stderr
    assert not log.exists()

    env["NOVICT"] = "0"
    env["EXL3_NVTIER_NO_VICTIM"] = "1"
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 2
    assert "victim write-back stays on" in r.stderr
    assert not log.exists()

    env["EXL3_NVTIER_NO_VICTIM"] = "0"
    env["A770B_SGLANG_RENDER_NODE"] = "/dev/dri/renderD128"
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 2
    assert "by-path nodes" in r.stderr
    assert not log.exists()


def test_start_sglang_keeps_a_load_above_the_llama_cap_and_below_the_card(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 0")
    used = 20 * 2**30
    _make_bin(
        bin_, "nvtop",
        "echo '[{\"device_name\": \"Intel Arc A770 DG2\", \"mem_total\": 34359738368, "
        f"\"mem_used\": {used}, \"mem_free\": 0}}]'",
    )
    nodes = _sglang_nodes(tmp_path)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "no-sglang.env"),
        A770B_QUANT="exl3",
        TOOL_PARSER="qwen3_coder",
        REASONING_PARSER="qwen3",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_MIN_VRAM_MB="0",
        EXL3_NVTIER_NO_VICTIM="0",
        **nodes,
    )
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the card total" in r.stdout
    assert "VRAM after load" in r.stdout
    assert "STOPPED" not in r.stderr
    ov = json.loads((Path(env["A770B_DATA"]) / "logs" / "compose-argv.override.json").read_text(encoding="utf-8"))
    assert ov["services"]["llama"]["entrypoint"] == ["python3"]
    assert ov["services"]["llama"]["command"][0] == "-m"
    assert "python3" not in ov["services"]["llama"]["command"]


def test_start_sglang_stops_when_used_vram_exceeds_the_card(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    _make_bin(bin_, "curl", "exit 0")
    used = 33 * 2**30
    _make_bin(
        bin_, "nvtop",
        "echo '[{\"device_name\": \"Intel Arc A770 DG2\", \"mem_total\": 34359738368, "
        f"\"mem_used\": {used}, \"mem_free\": 0}}]'",
    )
    nodes = _sglang_nodes(tmp_path)
    env = _env(
        tmp_path, bin_,
        A770B_SERVED_BACKEND="sglang",
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "no-sglang.env"),
        A770B_QUANT="exl3",
        TOOL_PARSER="qwen3_coder",
        REASONING_PARSER="qwen3",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_MIN_VRAM_MB="0",
        EXL3_NVTIER_NO_VICTIM="0",
        **nodes,
    )
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "the card total" in r.stderr
    assert "STOPPED" in r.stderr
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))


FRAMEWORK_NAMES = ("vllm-embed", "vllm-rerank", "neo4j-memory", "postgres-vector")


def test_b70_envelope_keeps_the_measured_limits():
    text = SGLANG_YAML.read_text(encoding="utf-8")
    assert "mem_limit: 40g" in text
    assert "memswap_limit: 40g" in text
    assert "shm_size: 8g" in text
    assert 'cpuset: "8-23"' in text
    assert "${EXL3_NVTIER_RAM_GB:-20}" in text
    example = SGLANG_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "EXL3_NVTIER_RAM_GB=20" in example
    assert "64 GiB" in example
    assert "32 GiB" in example
    assert "24" + "g" not in text
    assert ":-" + "12" not in text


def _label_docker(tmp_path: Path, state: dict) -> tuple[Path, Path, Path]:
    """A docker that answers from compose labels. It never talks to a live daemon."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    state_path = tmp_path / "containers.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    script = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["FAKE_DOCKER_LOG"])
state_path = Path(os.environ["FAKE_STATE"])
with log.open("a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
state = json.loads(state_path.read_text(encoding="utf-8"))

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def split_compose(rest):
    i = 0
    project = None
    files = []
    while i < len(rest):
        a = rest[i]
        if a == "--env-file" and i + 1 < len(rest):
            i += 2
            continue
        if a == "-p" and i + 1 < len(rest):
            project = rest[i + 1]
            i += 2
            continue
        if a == "-f" and i + 1 < len(rest):
            files.append(rest[i + 1])
            i += 2
            continue
        break
    return project, files, rest[i:], i

if args[:2] == ["compose", "version"]:
    print("Docker Compose version v2.30.0")
    sys.exit(0)
if args and args[0] == "compose":
    project, files, tail, _ = split_compose(args[1:])
    cmd = tail[0] if tail else ""
    joined = ",".join(files)
    service = next((t for t in tail[1:] if not t.startswith("-")), "")
    show_all = "--all" in tail
    chosen = []
    for c in state["containers"]:
        if c["project"] != project or c["files"] != joined:
            continue
        if service and c["service"] != service:
            continue
        if not show_all and not c["running"]:
            continue
        chosen.append(c)
    if cmd == "ps":
        for c in chosen:
            print(c["id"])
        sys.exit(0)
    if cmd == "down":
        if state.get("down_exit"):
            print("compose error", file=sys.stderr)
            sys.exit(1)
        if not state.get("down_leaves"):
            for c in chosen:
                c["running"] = False
            save()
        sys.exit(0)
    if cmd == "up":
        started = False
        for c in state["containers"]:
            if c["project"] == project and c["files"] == joined:
                c["running"] = True
                c["pid"] = c.get("pid") or 4242
                started = True
        if not started:
            state["containers"].append({
                "id": "started",
                "name": "a770-builder-llama-1",
                "project": project or "",
                "service": "llama",
                "files": joined,
                "running": True,
                "pid": 4242,
                "log": "started log",
            })
        save()
        sys.exit(0)
    sys.exit(0)
if args and args[0] == "ps":
    show_all = any(a.startswith("-") and "a" in a for a in args)
    want = {}
    for a in args:
        if a.startswith("label="):
            key, value = a[len("label="):].split("=", 1)
            want[key] = value
    for c in state["containers"]:
        if not show_all and not c["running"]:
            continue
        if want.get("com.docker.compose.project") not in (None, c["project"]):
            continue
        if want.get("com.docker.compose.service") not in (None, c["service"]):
            continue
        print(c["id"])
    sys.exit(0)
if args[:2] == ["inspect", "-f"] and len(args) >= 4:
    fmt, cid = args[2], args[3]
    c = next((x for x in state["containers"] if x["id"] == cid), None)
    if c is None:
        sys.exit(1)
    if fmt == "{{.State.Pid}}":
        print(c["pid"] if c["running"] else 0)
    elif fmt == "{{.Name}}":
        print("/" + c["name"])
    elif "config_files" in fmt:
        print(c["files"])
    else:
        print("")
    sys.exit(0)
if args and args[0] == "logs" and len(args) >= 2:
    c = next((x for x in state["containers"] if x["id"] == args[1]), None)
    if c is None or not c.get("log_ok", True):
        sys.exit(1)
    print(c.get("log", ""))
    sys.exit(0)
sys.exit(0)
'''
    path = bin_ / "docker"
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    _make_bin(bin_, "curl", 'echo \'{"ok": true}\'; exit 0')
    _make_bin(bin_, "pgrep", 'printf "%s\\n" ${FAKE_PGREP:-}')
    _make_bin(bin_, "nvtop", 'echo \'[{"device_name": "Intel Arc A770 DG2", "mem_total": 17179869184, "mem_used": 0, "mem_free": 17179869184}]\'')
    return bin_, log, state_path


def _stop_world(tmp_path: Path, *, down_exit: bool = False, down_leaves: bool = False):
    override = tmp_path / "data" / "logs" / "compose-argv.override.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}\n", encoding="utf-8")
    foreign_a = tmp_path / "other-a.yaml"
    foreign_b = tmp_path / "other-b.yaml"
    foreign_a.write_text("services: {}\n", encoding="utf-8")
    foreign_b.write_text("services: {}\n", encoding="utf-8")
    ours = f"{SGLANG_YAML},{override}"
    foreign = f"{foreign_a},{foreign_b}"
    containers = [
        {"id": "both", "name": "a770-builder-llama-1", "project": "a770-builder", "service": "llama",
         "files": ours, "running": True, "pid": 4242, "log": "yaml-plus-override log"},
        {"id": "otherwt", "name": "a770-builder-llama-2", "project": "a770-builder", "service": "llama",
         "files": foreign, "running": True, "pid": 4243, "log": "other-worktree log"},
        {"id": "otherproj", "name": "someone-llama", "project": "someone-else", "service": "llama",
         "files": foreign, "running": True, "pid": 4244, "log": "other-project log"},
    ]
    for i, name in enumerate(FRAMEWORK_NAMES):
        containers.append({
            "id": f"fw{i}", "name": name, "project": "a770-builder", "service": "llama",
            "files": f"{tmp_path}/framework-{name}.yaml", "running": True, "pid": 5000 + i,
            "log": f"{name} log",
        })
    bin_, log, state_path = _label_docker(tmp_path, {"down_exit": down_exit, "down_leaves": down_leaves, "containers": containers})
    env = _env(
        tmp_path, bin_,
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_PROJECT="a770-builder",
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
        FAKE_DOCKER_LOG=str(log),
        FAKE_STATE=str(state_path),
    )
    logs = Path(env["A770B_DATA"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "llamacpp-a770.pid").write_text("99999\n", encoding="utf-8")
    (logs / "live-backend.json").write_text("{}\n", encoding="utf-8")
    return env, log, state_path, logs


def _running_ids(state_path: Path) -> set[str]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return {c["id"] for c in state["containers"] if c["running"]}


def test_stop_removes_the_override_container_and_another_worktree(tmp_path):
    env, log, state_path, logs = _stop_world(tmp_path)
    r = _run(env, "stop")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "stopped (compose project a770-builder)" in r.stdout
    assert not (logs / "llamacpp-a770.pid").exists()
    assert not (logs / "live-backend.json").exists()
    running = _running_ids(state_path)
    assert "both" not in running
    assert "otherwt" not in running
    assert "otherproj" in running
    for i in range(len(FRAMEWORK_NAMES)):
        assert f"fw{i}" in running
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not any(c.split()[0] == "rm" for c in calls if c.split())
    assert any(" logs both" in f" {c} " for c in calls)
    assert not any(" logs otherproj" in f" {c} " or " logs fw" in f" {c} " for c in calls)
    kept = logs / "llamacpp-a770.log"
    assert kept.is_file()
    kept_text = kept.read_text(encoding="utf-8")
    assert kept_text.strip() == "yaml-plus-override log"
    assert "other-worktree log" not in kept_text
    logs_at = [i for i, c in enumerate(calls) if " logs " in f" {c} "]
    downs = [i for i, c in enumerate(calls) if c.endswith(" down") or " down" in f" {c} "]
    assert logs_at and downs and logs_at[0] < downs[0]
    assert any("other-a.yaml" in c and c.endswith(" down") or ("other-a.yaml" in c and " down" in f" {c} ") for c in calls)
    assert not any("someone-else" in c for c in calls)


def test_stop_ignores_an_exited_container_whose_compose_file_is_gone(tmp_path):
    """An exited container whose compose file is gone does not make stop exit 3, and does not delete a running container of another project."""
    override = tmp_path / "data" / "logs" / "compose-argv.override.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("{}\n", encoding="utf-8")
    missing = tmp_path / "removed-worktree" / "compose.yaml"
    foreign = tmp_path / "other-project.yaml"
    foreign.write_text("services: {}\n", encoding="utf-8")
    ours = f"{SGLANG_YAML},{override}"
    containers = [
        {"id": "ghost", "name": "a770-builder-llama-old", "project": "a770-builder", "service": "llama",
         "files": str(missing), "running": False, "pid": 0, "log": "exited log"},
        {"id": "live", "name": "a770-builder-llama-1", "project": "a770-builder", "service": "llama",
         "files": ours, "running": True, "pid": 4242, "log": "yaml-plus-override log"},
        {"id": "otherproj", "name": "someone-llama", "project": "someone-else", "service": "llama",
         "files": str(foreign), "running": True, "pid": 4244, "log": "other-project log"},
        {"id": "fw0", "name": FRAMEWORK_NAMES[0], "project": "a770-builder", "service": "llama",
         "files": str(tmp_path / "framework.yaml"), "running": True, "pid": 5000, "log": "framework log"},
    ]
    bin_, log, state_path = _label_docker(tmp_path, {"containers": containers})
    env = _env(
        tmp_path, bin_,
        A770B_COMPOSE_FILE=str(SGLANG_YAML),
        A770B_COMPOSE_PROJECT="a770-builder",
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
        FAKE_DOCKER_LOG=str(log),
        FAKE_STATE=str(state_path),
    )
    logs = Path(env["A770B_DATA"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "llamacpp-a770.pid").write_text("99999\n", encoding="utf-8")
    (logs / "live-backend.json").write_text("{}\n", encoding="utf-8")
    r = _run(env, "stop")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "compose file is not on disk" not in (r.stdout + r.stderr)
    running = _running_ids(state_path)
    assert "live" not in running
    assert "otherproj" in running
    assert "fw0" in running
    assert "ghost" not in running
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not any(c.split()[0] == "rm" for c in calls if c.split())
    assert not any("removed-worktree" in c for c in calls)


def test_start_takes_down_another_worktrees_running_container(tmp_path):
    foreign = tmp_path / "other-a.yaml"
    foreign.write_text("services: {}\n", encoding="utf-8")
    elsewhere = tmp_path / "other-project.yaml"
    elsewhere.write_text("services: {}\n", encoding="utf-8")
    containers = [
        {"id": "otherwt", "name": "a770-builder-llama-2", "project": "a770-builder", "service": "llama",
         "files": str(foreign), "running": True, "pid": 4243, "log": "other-worktree log"},
        {"id": "otherproj", "name": "someone-llama", "project": "someone-else", "service": "llama",
         "files": str(elsewhere), "running": True, "pid": 4244, "log": "other-project log"},
    ]
    bin_, log, state_path = _label_docker(tmp_path, {"containers": containers})
    env = _env(
        tmp_path, bin_,
        A770B_COMPOSE_FILE=str(ENVELOPE),
        A770B_COMPOSE_PROJECT="a770-builder",
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
        FAKE_DOCKER_LOG=str(log),
        FAKE_STATE=str(state_path),
    )
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    running = _running_ids(state_path)
    assert "otherwt" not in running
    assert "otherproj" in running
    calls = log.read_text(encoding="utf-8").splitlines()
    foreign_downs = [i for i, c in enumerate(calls) if "other-a.yaml" in c and " down" in f" {c} "]
    ups = [i for i, c in enumerate(calls) if " up " in f" {c} "]
    assert foreign_downs and ups and foreign_downs[0] < ups[0]


def test_stop_keeps_the_pidfile_when_down_leaves_the_container(tmp_path):
    env, _, state_path, logs = _stop_world(tmp_path, down_leaves=True)
    r = _run(env, "stop")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stopped (compose project" not in (r.stdout + r.stderr)
    assert "still running" in r.stderr
    assert (logs / "llamacpp-a770.pid").is_file()
    assert (logs / "live-backend.json").is_file()
    assert "both" in _running_ids(state_path)
    assert "otherwt" in _running_ids(state_path)


def test_stop_keeps_the_pidfile_when_compose_errors(tmp_path):
    env, _, state_path, logs = _stop_world(tmp_path, down_exit=True)
    r = _run(env, "stop")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stopped (compose project" not in (r.stdout + r.stderr)
    assert "compose down failed" in r.stderr
    assert (logs / "llamacpp-a770.pid").is_file()
    assert (logs / "live-backend.json").is_file()
    assert "both" in _running_ids(state_path)
    assert "otherwt" in _running_ids(state_path)


def test_start_with_no_previous_container_does_not_warn_about_the_new_log(tmp_path):
    bin_, _ = _fake_runtime(tmp_path, running=False)
    r = _run(_env(tmp_path, bin_), "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not be kept" not in r.stderr
    assert "new server" not in r.stderr


def test_start_with_a_previous_container_keeps_that_log_before_down(tmp_path):
    bin_, log = _fake_runtime(tmp_path, running=True)
    env = _env(tmp_path, bin_)
    r = _run(env, "start", GGUF, "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "new server" not in r.stderr
    _assert_log_kept_before_down(log, r.stderr, Path(env["A770B_DATA"]))


def test_unattached_sglang_stage_counters_skip_diff_with_one_warning(tmp_path):
    """sglang has no prometheus counter diff. An unattached run warns once and does not call diff."""
    script = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
    data = tmp_path / "data"
    log = tmp_path / "python-argv.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    real = shutil.which("python3")
    _make_bin(
        bin_, "python3",
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case " $* " in\n'
        '  *" diff "*) exit 99;;\n'
        "esac\n"
        f'exec "{real}" "$@"\n',
    )
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    env = {
        "PATH": f"{bin_}:{os.environ.get('PATH', '')}",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "A770B_PROJECT": str(ROOT),
        "A770B_DATA": str(data),
        "A770B_REFUSE": "/nonexistent",
        "A770B_CARD_MODE": "inference",
        "A770B_SERVED_ENGINE": "sglang",
        "XDG_CONFIG_HOME": str(xdg),
        "TMPDIR": str(tmp_path),
    }
    r = subprocess.run(
        ["bash", "-c", '. "$1"; _stage_counters "$2"', "_", str(script), "sg-label"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    warnings = [ln for ln in r.stderr.splitlines() if "⚠" in ln]
    assert warnings == ["⚠ sglang has no prometheus counter diff"], warnings
    called = log.read_text(encoding="utf-8") if log.exists() else ""
    assert " diff " not in f" {called} "
    assert not (data / "results" / "sg-label.counters.json").exists()
