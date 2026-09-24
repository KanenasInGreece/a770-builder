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
                  down_clears: bool = True, up_fails: bool = False):
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
    up_body = 'echo "docker compose up failed" >&2; exit 1;;' if up_fails else f'printf up > "{state}"; exit 0;;'
    _make_bin(bin_, "docker", f'''printf '%s\\n' "$*" >> "{log}"
case " $* " in
  *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0;;
  *" down"*) {down_body}
  *" up "*) {up_body}
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
    assert "must be one of: vulkan, sycl, vllm" in r.stderr


def test_dispatch_refuses_an_empty_served_backend(tmp_path):
    bin_, log = _fake_runtime(tmp_path)
    env = _env(tmp_path, bin_, A770B_SERVED_BACKEND="")
    r = _run(env, "plan", GGUF, "8192")
    assert r.returncode == 2
    assert "A770B_SERVED_BACKEND is unset or empty — must be one of: vulkan, sycl, vllm" in r.stderr


def test_vllm_envelope_sets_the_server_entrypoint_and_bakes_no_model():
    text = VLLM_ENVELOPE.read_text(encoding="utf-8")
    assert 'entrypoint: ["/bin/bash", "-c", "VLLM_API_KEY=\\"$(cat /run/a770b/api.key)\\" || exit 1; export VLLM_API_KEY; source /opt/intel/oneapi/setvars.sh --force >/dev/null || exit 1; exec vllm serve \\"$@\\"", "vllm-serve"]' in text
    assert "2>/dev/null" not in text
    assert "127.0.0.1:${A770B_PORT}:8000" in text
    assert "${A770B_VLLM_IMAGE:-intel/vllm:0.21.0-xpu}" in text
    assert "${A770B_MODEL}" not in text and "${A770B_CTX}" not in text
    assert "${A770B_MODELS}:/models:ro,z" in text
    assert "${A770B_API_KEY_FILE}:/run/a770b/api.key:ro,z" in text
    assert "${A770B_BY_PATH_DIR}:/dev/dri/by-path:ro" in text


def test_vllm_env_example_documents_filtered_by_path_dir_and_trap():
    text = VLLM_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "A770B_MODEL=" not in text and "A770B_CTX=" not in text
    assert "0.21.0-xpu" in text and "getent" in text
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
        A770B_VLLM_IMAGE="intel/vllm:0.21.0-xpu",
        A770B_BY_PATH_DIR=str(by_path),
        A770B_QUANT="gptq",
        A770B_CARD_VRAM_TOTAL="32",
        A770B_VRAM_CAP_GIB="16.0",
        TOOL_PARSER="qwen3_coder",
    )
    r = _run(env, "start", "Qwen/Qwen3-32B", "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "▶ started server container" in r.stdout
    ov = json.loads((Path(env["A770B_DATA"]) / "logs" / "compose-argv.override.json").read_text(encoding="utf-8"))
    assert "entrypoint" not in ov["services"]["llama"]
    assert ov["services"]["llama"]["command"][0] == "/models/Qwen/Qwen3-32B"
    pidf = Path(env["A770B_DATA"]) / "logs" / "llamacpp-a770.pid"
    assert pidf.read_text(encoding="utf-8").strip() == "4242"
