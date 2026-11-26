"""End-to-end qualify runs against the real harness scripts.

docker, nvtop, uvx (llama-benchy), systemd-run and opencode are fakes on PATH.
curl is the real client, talking to a loopback stand-in for the engine API.
Nothing here starts a real server, container, or card job. SANDBOX=0 is the
harness's documented unconfined arm, so the fake opencode is the process that
runs. The a770b-qualify-* whole-script stubs are not on PATH.
"""

import hashlib
import json
import os
import queue
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_qualify_lib import _min_row
from test_serve_compose import _fake_runtime, _make_bin

ROOT = Path(__file__).resolve().parents[1]
QUALIFY = ROOT / "harness" / "qualify.sh"
IMAGE_ID = "sha256:" + ("0" * 64)
DEPTH_ANSWER = (
    "xor of each byte by its 1-based position, default salt 4171, "
    "reduced modulo 65521 (the adler prime). "
    "Other definitions: alpha_kept beta_kept gamma_kept."
)


def _tokenizer(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
import sys
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Split
vocab = {"<unk>": 0}
for i in range(32, 127):
    vocab[chr(i)] = i
tok = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
tok.pre_tokenizer = Split(pattern="", behavior="isolated")
tok.save(sys.argv[1])
"""
    run = subprocess.run(
        ["uv", "run", "--with", "tokenizers", "python3", "-c", script, str(path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert run.returncode == 0, run.stderr
    assert path.is_file()


def _corpus(path: Path) -> None:
    body = (
        "def alpha_kept():\n    return 1\n"
        "def beta_kept():\n    return 2\n"
        "def gamma_kept():\n    return 3\n"
    )
    pad = "# pad line keeps the prompt inside the character window\n"
    while len(body) < 22000:
        body += pad
    path.write_text(body, encoding="utf-8")


def _patch_docker(bin_dir: Path) -> None:
    script = bin_dir / "docker"
    text = script.read_text(encoding="utf-8")
    text = text.replace("import json, os, re, sys\n", "import json, os, re, sys, time\n", 1)
    hold = (
        "    if cmd == \"up\":\n"
        "        hold = os.environ.get(\"E2E_UP_HOLD\")\n"
        "        if hold:\n"
        "            Path(hold + \".started\").write_text(\"up\\n\", encoding=\"utf-8\")\n"
        "            while os.path.exists(hold):\n"
        "                time.sleep(0.05)\n"
    )
    needle = "    if cmd == \"up\":\n"
    assert text.count(needle) == 1
    text = text.replace(needle, hold, 1)
    text = text.replace(
        'elif "{{.Image}}" in fmt:\n        print(c.get("image", ""))',
        'elif "{{.Image}}" in fmt:\n        print(' + repr(IMAGE_ID) + ")",
        1,
    )
    script.write_text(text, encoding="utf-8")


class _Engine(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    tokenizer = None

    def log_message(self, fmt, *args):
        return

    def _send(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(b'{"ok":true}')
        elif path == "/v1/models":
            self._send(b'{"data":[{"id":"local-builder"}]}')
        elif path == "/metrics":
            self._send(b"llamacpp:prompt_tokens_total 1\n")
        else:
            self._send(b'{"missing":true}', 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        messages = body.get("messages") if isinstance(body, dict) else None
        content = ""
        if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
            content = messages[-1].get("content") or ""
        if isinstance(body, dict) and body.get("tools"):
            reply = {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{\"city\":\"Athens\"}"},
                        }],
                    }
                }]
            }
        elif "Reply with one word." in content:
            reply = {"choices": [{"message": {"content": "Ready"}}]}
        else:
            tokens = len(content) if isinstance(content, str) else 0
            reply = {
                "choices": [{"message": {"content": DEPTH_ANSWER}}],
                "usage": {"prompt_tokens": tokens, "completion_tokens": 32},
                "timings": {
                    "prompt_per_second": 100.0,
                    "predicted_per_second": 20.0,
                    "prompt_ms": 80.0,
                },
            }
        self._send(json.dumps(reply).encode())


def _serve_engine():
    # The character tokenizer is one id per code point, so the stand-in counts
    # the same way without importing tokenizers into this process.
    _Engine.tokenizer = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Engine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _world(tmp_path: Path, *, port: int, engine: str):
    """PATH fakes plus the envelope the real serve scripts read from the environment."""
    bin_dir, _log = _fake_runtime(
        tmp_path,
        ps_id="ab" * 32,
        host_pid=str(os.getpid()),
        running=False,
    )
    _patch_docker(bin_dir)
    curl = bin_dir / "curl"
    if curl.exists():
        curl.unlink()
    _make_bin(
        bin_dir,
        "nvtop",
        "echo '[{\"device_name\": \"Intel Arc A770 DG2\", \"mem_total\": 17179869184, "
        "\"mem_used\": 4294967296, \"mem_free\": 12884901888}]'",
    )
    _make_bin(
        bin_dir,
        "uvx",
        """
if [ "${1:-}" = "--version" ]; then echo "uvx 0.13.4"; exit 0; fi
save=""
sizes=()
take=""
for arg in "$@"; do
  if [ "$take" = save ]; then save=$arg; take=""; continue; fi
  if [ "$take" = pp ]; then
    case "$arg" in
      --*) take="";;
      *) sizes+=("$arg"); continue;;
    esac
  fi
  if [ "$arg" = "--save-result" ]; then take=save; continue; fi
  if [ "$arg" = "--pp" ]; then take=pp; continue; fi
done
[ -n "$save" ] || exit 2
python3 - "$save" "${sizes[@]}" <<'PY'
import json, sys
path, *sizes = sys.argv[1:]
rows = []
for size in sizes:
    rows.append({
        "context_size": 0,
        "prompt_size": int(size),
        "response_size": 128,
        "concurrency": 1,
        "is_context_prefill_phase": False,
        "pp_throughput": {"mean": 120.0},
        "tg_throughput": {"mean": 40.0},
    })
open(path, "w", encoding="utf-8").write(json.dumps({"benchmarks": rows}) + "\\n")
PY
""",
    )
    marker = tmp_path / "systemd-run.log"
    _make_bin(
        bin_dir,
        "systemd-run",
        f"printf '%s\\n' \"$*\" >> {marker}\nexit 1",
    )
    opencode_dir = tmp_path / "opencode-bin"
    opencode_dir.mkdir()
    opencode = opencode_dir / "opencode"
    opencode.write_text(
        """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then echo "1.19.6"; exit 0; fi
if [ "${1:-}" = "run" ] && [ "${2:-}" = "--help" ]; then echo "usage: opencode run --dir DIR"; exit 0; fi
hold=${E2E_OPENCODE_HOLD:-}
if [ -n "$hold" ]; then
  printf '%s\\n' "$$" > "$hold.pid"
  trap '' TERM INT
  while [ -f "$hold" ]; do sleep 0.1; done
fi
solve=${E2E_S2_SOLUTION:-}
if [ -n "$solve" ]; then
  dir=""
  prev=""
  for arg in "$@"; do
    if [ "$prev" = "--dir" ]; then dir=$arg; fi
    prev=$arg
  done
  if [ -n "$dir" ] && [ -f "$dir/python/logstats/stats.py" ]; then
    cp -- "$solve" "$dir/python/logstats/stats.py"
  fi
  # A counted stage with a zero wall is not a measurement.
  sleep 1
fi
exit 0
""",
        encoding="utf-8",
    )
    opencode.chmod(opencode.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    models = tmp_path / "models"
    models.mkdir()
    (models / "sample.gguf").write_text("not a weight\n", encoding="utf-8")
    tok_dir = tmp_path / "tokenizer"
    _tokenizer(tok_dir / "tokenizer.json")
    (tok_dir / "vocab.json").write_text("{}\n", encoding="utf-8")
    _corpus(tmp_path / "corpus.py")
    card = tmp_path / "dri-card"
    render = tmp_path / "dri-render"
    card.write_text("card\n", encoding="utf-8")
    render.write_text("render\n", encoding="utf-8")
    by_path = tmp_path / "by-path"
    by_path.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env.update(
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(tmp_path / "data"),
        A770B_MODELS=str(models),
        A770B_CARD="a770",
        A770B_CARD_MODE="inference",
        A770B_GPU_MATCH="DG2",
        A770B_VRAM_CAP_GIB="15.3",
        A770B_CARD_VRAM_TOTAL="16",
        A770B_MIN_AVAIL_MB="1",
        A770B_SERVE="compose",
        A770B_HOST="127.0.0.1",
        A770B_PORT=str(port),
        A770B_API_KEY_FILE=str(tmp_path / "api.key"),
        A770B_REFUSE="/nonexistent/qualify-e2e-refuse",
        A770B_DEFER_DATA_DIRS="1",
        A770B_HEALTH_TRIES="5",
        A770B_HEALTH_PAUSE="0",
        A770B_SWEEP_MAX_TIME="30",
        A770B_PROBE_MAX_TIME="30",
        A770B_QUALIFY_KIT_PROMPT_TOKENS="1000",
        A770B_CORPUS_FILE=str(tmp_path / "corpus.py"),
        A770B_LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:full-vulkan",
        A770B_VLLM_IMAGE="vllm/vllm-openai:e2e",
        A770B_SGLANG_IMAGE="lmsysorg/sglang:e2e",
        A770B_DRM_CARD=str(card),
        A770B_DRM_RENDER=str(render),
        A770B_SGLANG_CARD_NODE=str(card.resolve()),
        A770B_SGLANG_RENDER_NODE=str(render.resolve()),
        A770B_BY_PATH_DIR=str(by_path),
        A770B_RENDER_GID="105",
        A770B_VIDEO_GID="39",
        A770B_DOCKER="docker",
        A770B_OPENCODE_BIN=str(opencode_dir),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing.env"),
        SANDBOX="0",
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
        FAKE_DOCKER_LOG_TEXT=(
            "verbosity = 4\n"
            "'enable_per_request_metrics': True\n"
            "CPU_Host model buffer size = 512.00 MiB\n"
        ),
    )
    for name in (
        "A770B_QUALIFY_STUB",
        "A770B_PROFILES_FILE",
        "A770B_ALLOW_NO_NVTOP",
        "E2E_UP_HOLD",
        "E2E_OPENCODE_HOLD",
        "E2E_S2_SOLUTION",
    ):
        env.pop(name, None)
    env["_E2E_ENGINE"] = engine
    env["_E2E_TOKENIZER"] = str(tok_dir)
    env["_E2E_MODELS"] = str(models)
    env["_E2E_SYSTEMD"] = str(marker)
    return env


def _profile(tmp_path: Path, engine: str, model: str) -> Path:
    row = _min_row(engine)
    row["model"] = model
    row["ctx"] = 16384
    row["useful_ctx"] = 16384
    path = tmp_path / f"{engine}.json"
    path.write_text(
        json.dumps({"schema": 1, "card": "a770", "mode": "inference", "profiles": {"e2e-row": row}})
        + "\n",
        encoding="utf-8",
    )
    return path


def _argv(env, *, stages: str, extra=None):
    engine = env["_E2E_ENGINE"]
    args = ["stdbuf", "-oL", "bash", str(QUALIFY), "start", "--foreground", "--engine", engine]
    if engine == "llama.cpp":
        args += [
            "--model", "sample.gguf",
            "--ctx", "16384",
            "--backend", "vulkan",
            "--tokenizer", env["_E2E_TOKENIZER"],
        ]
    else:
        model = "e2e.strata.json" if engine == "strata" else "e2e-model"
        prof = _profile(Path(env["A770B_DATA"]).parent, engine, model)
        # The profiles file must live beside the temp data, outside the project.
        args += ["--model", "e2e-row", "--profiles-file", str(prof), "--ctx", "16384"]
        if engine != "strata":
            dest = Path(env["_E2E_MODELS"]) / model
            dest.mkdir(exist_ok=True)
            tok = Path(env["_E2E_TOKENIZER"])
            (dest / "tokenizer.json").write_bytes((tok / "tokenizer.json").read_bytes())
            (dest / "tokenizer_config.json").write_text("{}\n", encoding="utf-8")
    if stages:
        args += ["--stages", stages]
    if extra:
        args += extra
    return args


def _tails(job: Path) -> str:
    parts = []
    if not job.is_dir():
        return "no job directory"
    roots = [job]
    data = job.parent.parent
    if (data / "logs").is_dir():
        roots.append(data / "logs")
    if (data / "results").is_dir():
        roots.append(data / "results")
    seen = set()
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or path in seen:
                continue
            seen.add(path)
            if path.stat().st_size > 200_000:
                continue
            if path.suffix not in {".log", ".json", ".txt", ".md"} and path.name not in {"result.json", "status.json"}:
                if "phases" not in path.parts and path.name not in {"argv.log"}:
                    continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if path.suffix == ".json" and path.name not in {
                "result.json", "serve.json", "suite.json", "status.json", "plan.json",
            }:
                continue
            try:
                label = str(path.relative_to(data))
            except ValueError:
                label = path.name
            parts.append(f"\n----- {label} -----\n{text[-4000:]}")
    return "".join(parts)[-24000:]


def _run(env, args, timeout):
    proc = subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(f"timed out\nstdout:\n{out[-4000:]}\nstderr:\n{err[-4000:]}")
    return proc.returncode, out, err


def _check(result, name):
    return next(item for item in result["checks"] if item["name"] == name)


def _assert_full(env, code, out, err):
    assert code == 0, f"exit {code}\n{out[-2000:]}\n{err[-4000:]}"
    assert "systemd-run" not in out
    marker = Path(env["_E2E_SYSTEMD"])
    assert not marker.exists() or marker.read_text(encoding="utf-8") == ""
    directory = next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("directory "))
    job = Path(directory)
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    detail = _tails(job)
    assert "another local-build run holds the card lock" not in detail
    assert _check(result, "load-ok")["result"] == "pass", detail
    assert _check(result, "switches-confirmed")["result"] == "pass", detail
    assert _check(result, "smoke-tool-call")["result"] == "pass", detail
    assert _check(result, "window-no-resets")["result"] == "pass", detail
    speed = result.get("profile_speed") or {}
    assert speed.get("prefill_tps", {}).get("8k") == 120.0, detail
    assert speed.get("decode_tps", {}).get("8k") == 40.0, detail
    assert result.get("depth_score") == 3, detail
    patches = result.get("empty_patches") or []
    assert patches and patches[0]["stage"] == "s0-design", detail
    assert "changed nothing" in (patches[0].get("evidence") or ""), detail
    stages = (result.get("suite") or {}).get("stages") or []
    assert any(stage.get("id") == "s0-design" and stage.get("outcome") == "empty_patch" for stage in stages), detail
    row_path = job.parent.parent / "row.json"
    exported = subprocess.run(
        ["bash", str(QUALIFY), "export", job.name, "--allow-incomplete", "--out", str(row_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert exported.returncode == 0, exported.stderr + detail
    row = json.loads(row_path.read_text(encoding="utf-8"))
    assert "decode_tps / speed.prefill_tps" not in (row.get("speed") or {})
    assert row["vram_gib_after_load"] == 4.0
    assert row["speed"]["prefill_tps"]["8k"] == 120.0
    assert row["speed"]["decode_tps"]["8k"] == 40.0
    todo = json.loads(Path(str(row_path) + ".todo.json").read_text(encoding="utf-8"))
    assert todo["allow_incomplete"] is True
    assert todo["missing_statistics"]
    return result


def test_llama_cpp_gguf_reaches_result_and_export(tmp_path):
    _full(tmp_path, "llama.cpp")


def _full(tmp_path, engine):
    server = None
    try:
        env = _world(tmp_path, port=9, engine=engine)
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        code, out, err = _run(env, _argv(env, stages="s0-design"), 240)
        return _assert_full(env, code, out, err)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def test_vllm_profile_reaches_result_and_export(tmp_path):
    _full(tmp_path, "vllm")


def test_sglang_profile_reaches_result_and_export(tmp_path):
    _full(tmp_path, "sglang")


def _strata_config(env) -> None:
    models = Path(env["_E2E_MODELS"])
    exe = models / "strata-engine"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    tok = Path(env["_E2E_TOKENIZER"])
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    cfg = {
        "backend": "intel",
        "exe": str(exe),
        "cwd": str(models),
        "tokenizer": str(tok),
        "model_name": "e2e-strata-served",
        "parallel": 1,
        "args": [],
        "env": {"ONEAPI_DEVICE_SELECTOR": "level_zero:0"},
    }
    (models / "e2e.strata.json").write_text(json.dumps(cfg) + "\n", encoding="utf-8")
    env["A770B_STRATA_ENGINE_SHA256"] = digest
    env["A770B_ONEAPI_DEVICE_SELECTOR"] = "level_zero:0"
    env["A770B_FRAMEWORK_PORTS"] = env["A770B_PORT"]


def test_strata_stops_at_the_reserved_port_before_a_server(tmp_path):
    env = _world(tmp_path, port=9, engine="strata")
    env["A770B_PORT"] = "9"
    env["A770B_FRAMEWORK_PORTS"] = "9"
    _strata_config(env)
    code, out, err = _run(env, _argv(env, stages="s0-design"), 90)
    assert code == 4, out + err
    directory = next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("directory "))
    job = Path(directory)
    detail = _tails(job)
    serve_log = (job / "phases" / "serve.log").read_text(encoding="utf-8", errors="replace")
    assert "Strata port is reserved" in serve_log, detail
    assert "another local-build run holds the card lock" not in detail
    assert "serve.server" not in serve_log
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    why = next(item for item in result["unmeasured"] if item.get("phase") == "serve")
    assert why.get("command")
    assert why.get("rc") not in (None, 0)
    assert why.get("log") == "phases/serve.log"
    assert "serve_strata.sh" in why["command"] or "local-build.sh" in why["command"]


def _lines_until(proc, timeout):
    found = {}
    box = queue.Queue()

    def read():
        for line in proc.stdout:
            box.put(line)
        box.put(None)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + timeout
    collected = []
    while time.time() < deadline and len(found) < 2:
        try:
            line = box.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        collected.append(line)
        if line.startswith("job "):
            found["job"] = line.split(" ", 1)[1].strip()
        elif line.startswith("directory "):
            found["directory"] = line.split(" ", 1)[1].strip()
    return found, "".join(collected)


def test_stop_during_serve_uses_the_real_runner(tmp_path):
    server = None
    hold = tmp_path / "up-hold"
    hold.write_text("1\n", encoding="utf-8")
    proc = None
    try:
        env = _world(tmp_path, port=9, engine="llama.cpp")
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        env["E2E_UP_HOLD"] = str(hold)
        proc = subprocess.Popen(
            _argv(env, stages="s0-design"),
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        found, early = _lines_until(proc, 90)
        assert "directory" in found, early
        started = Path(str(hold) + ".started")
        deadline = time.time() + 60
        while time.time() < deadline and not started.is_file():
            if proc.poll() is not None:
                err = proc.stderr.read()
                raise AssertionError(f"runner exited {proc.returncode} before compose up\n{early}\n{err}")
            time.sleep(0.1)
        assert started.is_file(), "compose up never started"
        stopped = subprocess.run(
            ["bash", str(QUALIFY), "stop", found["job"]],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert stopped.returncode == 0, stopped.stderr
        job = Path(found["directory"])
        assert (job / "STOPPED").is_file()
        serve_log = (job / "phases" / "serve.log").read_text(encoding="utf-8", errors="replace")
        assert "another local-build run holds the card lock" not in serve_log
    finally:
        hold.unlink(missing_ok=True)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        if server is not None:
            server.shutdown()
            server.server_close()


def test_stop_during_suite_calls_real_stop_run(tmp_path):
    server = None
    hold = tmp_path / "opencode-hold"
    hold.write_text("1\n", encoding="utf-8")
    proc = None
    try:
        env = _world(tmp_path, port=9, engine="llama.cpp")
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        env["E2E_OPENCODE_HOLD"] = str(hold)
        proc = subprocess.Popen(
            _argv(env, stages="s0-design", extra=["--skip-window"]),
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        found, early = _lines_until(proc, 90)
        assert "directory" in found, early
        pidfile = Path(str(hold) + ".pid")
        deadline = time.time() + 180
        while time.time() < deadline and not pidfile.is_file():
            if proc.poll() is not None:
                err = proc.stderr.read()
                job = Path(found["directory"])
                raise AssertionError(
                    f"runner exited {proc.returncode} before opencode\n{early}\n{err}\n{_tails(job)}"
                )
            time.sleep(0.2)
        assert pidfile.is_file(), _tails(Path(found["directory"]))
        stopped = subprocess.run(
            ["bash", str(QUALIFY), "stop", found["job"]],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert stopped.returncode == 0, stopped.stderr
        job = Path(found["directory"])
        assert (job / "STOPPED").is_file() or (job / "DONE").is_file()
        stop_log = (job / "phases" / "stop-run.log").read_text(encoding="utf-8", errors="replace")
        assert "ending run pid" in stop_log
        status = json.loads((job / "status.json").read_text(encoding="utf-8"))
        assert status.get("state") in {"stopped", "finished"}
    finally:
        hold.unlink(missing_ok=True)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        if server is not None:
            server.shutdown()
            server.server_close()


def _plant(env, project: str, cid: str) -> None:
    state_path = Path(env["A770B_DATA"]).parent / "docker.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["containers"].append({
        "id": cid,
        "name": f"{project}-llama-1",
        "project": project,
        "service": "llama",
        "running": True,
        "pid": "4242",
        "labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": "llama",
        },
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _still_running(env, cid: str) -> bool:
    state = json.loads((Path(env["A770B_DATA"]).parent / "docker.state.json").read_text(encoding="utf-8"))
    return any(item.get("id") == cid and item.get("running") for item in state["containers"])


def _stop_suite_spared(job: Path) -> None:
    log = job / "phases" / "stop-suite.log"
    if log.is_file():
        assert "stopped (compose project" not in log.read_text(encoding="utf-8", errors="replace")


def test_unrelated_server_survives_preflight_failure(tmp_path):
    env = _world(tmp_path, port=9, engine="llama.cpp")
    cid = "cd" * 32
    _plant(env, "a770-builder", cid)
    (Path(env["A770B_MODELS"]) / "sample.gguf").unlink()
    code, out, err = _run(env, _argv(env, stages="s0-design"), 90)
    assert code == 4, out + err
    assert _still_running(env, cid)
    directory = next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("directory "))
    _stop_suite_spared(Path(directory))


def test_unrelated_server_survives_prehook_failure(tmp_path):
    env = _world(tmp_path, port=9, engine="llama.cpp")
    cid = "ce" * 32
    _plant(env, "a770-builder", cid)
    hook = tmp_path / "pre.sh"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    env["A770B_QUALIFY_PRE"] = str(hook)
    code, out, err = _run(env, _argv(env, stages="s0-design"), 90)
    assert code == 4, out + err
    assert _still_running(env, cid)
    directory = next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("directory "))
    job = Path(directory)
    status = json.loads((job / "status.json").read_text(encoding="utf-8"))
    assert status.get("reason") == "pre-hook"
    _stop_suite_spared(job)


def test_llama_cpp_passing_stage_exports_without_allow_incomplete(tmp_path):
    server = None
    try:
        env = _world(tmp_path, port=9, engine="llama.cpp")
        server = _serve_engine()
        env["A770B_PORT"] = str(server.server_address[1])
        # verify grades inside the sandbox, which has no network. The cache is
        # this test's, filled before the run. It is not a server or a card job.
        cache = tmp_path / "uv-cache"
        cache.mkdir()
        env["A770B_UV_CACHE"] = str(cache)
        warmed = subprocess.run(
            ["bash", str(ROOT / "harness" / "warm_cache.sh")],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert warmed.returncode == 0, warmed.stdout + warmed.stderr
        env["E2E_S2_SOLUTION"] = str(
            ROOT / "kit" / "hidden" / "solutions" / "s2-backend" / "python" / "logstats" / "stats.py"
        )
        env["FAKE_DOCKER_LOG_TEXT"] = (
            "verbosity = 4\n"
            "'enable_per_request_metrics': True\n"
            "CPU_Host model buffer size = 512.00 MiB\n"
            "prompt eval time =     15880.00 ms /  1601 tokens "
            "(    9.92 ms per token,   100.80 tokens per second)\n"
            "eval time =     2245000.00 ms / 10082 tokens "
            "(  222.67 ms per token,     4.49 tokens per second)\n"
            "print_timing: id  0 | task 7 | n_gen =    100, tg =   4.49 t/s\n"
            "slot release: id  0 | task 7 | stop processing: n_tokens = 21446, truncated = 0\n"
        )
        foreign = "cf" * 32
        _plant(env, "other-work", foreign)
        code, out, err = _run(env, _argv(env, stages="s2-backend"), 300)
        assert code == 0, f"exit {code}\n{out[-2000:]}\n{err[-4000:]}"
        directory = next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("directory "))
        job = Path(directory)
        detail = _tails(job)
        result = json.loads((job / "result.json").read_text(encoding="utf-8"))
        stages = (result.get("suite") or {}).get("stages") or []
        assert stages, detail
        assert all(stage.get("working") is True for stage in stages), detail
        assert _still_running(env, foreign), detail
        _stop_suite_spared(job)
        row_path = job.parent.parent / "row-complete.json"
        exported = subprocess.run(
            ["bash", str(QUALIFY), "export", job.name, "--out", str(row_path)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert exported.returncode == 0, exported.stderr + detail
        row = json.loads(row_path.read_text(encoding="utf-8"))
        assert row["fit"]["code"]
        assert row["suite"]["passed"] == row["suite"]["runs"]
        assert row["measured_tasks"]["s2-backend"]["quality"] == "pass"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
