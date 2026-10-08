"""bench_http_map.py keeps only the four prompt sizes at depth 0.

A depth row and a null decode must not become a registry prefill or a 0 tok/s decode.
The dry-run prints the uvx command and does not call uvx.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "harness" / "bench_http_map.py"
HTTP = ROOT / "harness" / "bench_http.sh"
LADDER = ROOT / "harness" / "ladder.sh"
LADDER_ROW = ROOT / "harness" / "ladder_row.py"


def load_map():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_http_map_under_test", MAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**over):
    row = {
        "concurrency": 1,
        "context_size": 0,
        "prompt_size": 8192,
        "response_size": 128,
        "is_context_prefill_phase": False,
        "pp_throughput": {"mean": 100.5},
        "tg_throughput": {"mean": 20.25},
    }
    row.update(over)
    return row


def test_a_depth_row_and_a_null_decode_are_not_a_prefill_or_a_zero():
    module = load_map()
    doc = {"benchmarks": [
        _row(context_size=8192, prompt_size=2048, pp_throughput={"mean": 999.0}),
        _row(prompt_size=8192, tg_throughput=None),
        _row(prompt_size=32768, is_context_prefill_phase=True, pp_throughput={"mean": 50.0}),
        _row(prompt_size=4096, pp_throughput={"mean": 7.0}),
        _row(prompt_size=65536, concurrency=4),
        _row(prompt_size=100000, response_size=32),
        _row(prompt_size=32768),
    ]}
    found = module.map_document(doc)
    assert set(found) == {"8192", "32768"}
    assert found["8192"] == {"pp": 100.5, "tg": None}
    assert found["8192"]["tg"] != 0
    assert found["32768"]["tg"] == 20.25
    assert "2048" not in found
    assert "8192" in found and found["8192"]["pp"] != 999.0


def test_mapped_file_fills_the_registry_keys_and_leaves_a_null_decode_null(tmp_path):
    mapped = {
        "instrument": "llama-benchy",
        "tool": "llama-benchy",
        "prompt": 8192,
        "gen": 128,
        "flags": "uvx llama-benchy --pp 8192 --tg 128 --depth 0",
        "at_depth": {
            "8192": {"pp": 100.5, "tg": None},
            "32768": {"pp": 80.0, "tg": 19.0},
            "65536": {"pp": 70.0, "tg": 18.0},
            "100000": {"pp": 60.0, "tg": 17.0},
        },
        "source": "raw.json",
    }
    path = tmp_path / "mapped.json"
    path.write_text(json.dumps(mapped), encoding="utf-8")
    out = tmp_path / "ladder.json"
    sweep = tmp_path / "sweep.log"
    sweep.write_text("target tokens\n", encoding="utf-8")
    depth = tmp_path / "depth.log"
    depth.write_text("depth probe at 100000: not measured — none\n", encoding="utf-8")
    argv = [
        str(out), "name", "/models/m", "131072", "q8_0", "q8_0", "off", "", "1800",
        "name", "SUITE-1@test", "100000", "", "",
        str(tmp_path / "no-bench-model.json"), str(path), str(sweep), str(depth),
        "", "", "", "", "2026-10-08T00:00:00",
    ]
    result = subprocess.run([sys.executable, str(LADDER_ROW)] + argv, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    speed = doc["computed"]["speed"]
    assert speed["prefill_tps"]["8k"] == 100.5
    assert speed["decode_tps"]["8k"] is None
    assert speed["decode_tps"]["32k"] == 19.0
    assert speed["prefill_tps"]["100k"] == 60.0
    assert speed["bench"]["tool"] == "llama-benchy"
    assert speed["bench"]["at_depth"]["8192"]["tg"] is None


def _isolated(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("A770B_")}
    env.update({
        "A770B_PROJECT": str(ROOT),
        "A770B_DATA": str(tmp_path),
        "A770B_REFUSE": "/nonexistent",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    })
    return env


def test_dry_run_prints_the_uvx_command_and_does_not_call_uvx(tmp_path):
    marker = tmp_path / "uvx-was-called"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uvx = bindir / "uvx"
    uvx.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
    uvx.chmod(0o755)
    curl = bindir / "curl"
    curl.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
    curl.chmod(0o755)
    env = _isolated(tmp_path)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(HTTP), "--dry-run", "--base-url", "http://127.0.0.1:9",
         "--served-model-name", "local-builder", "--tokenizer", "/models/Qwen"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = result.stdout
    assert "uvx" in line and "llama-benchy" in line
    assert "--depth 0" in line
    assert "--tg 128" in line
    for size in ("8192", "32768", "65536", "100000"):
        assert size in line
    assert "--exact-tg" not in line
    assert "--tokenizer /models/Qwen" in line or "--tokenizer '/models/Qwen'" in line
    assert not marker.exists()


def test_tokenizer_equal_to_the_served_name_is_refused(tmp_path):
    env = _isolated(tmp_path)
    result = subprocess.run(
        ["bash", str(HTTP), "--dry-run", "--base-url", "http://127.0.0.1:9",
         "--served-model-name", "local-builder", "--tokenizer", "local-builder"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2
    assert "uvx" not in result.stdout
    assert "served name" in result.stderr


def test_vllm_dry_run_leaves_the_server_up_and_names_the_tokenizer(tmp_path):
    models = tmp_path / "models"
    model = models / "Qwen3.8-27B-exl3-4.00bpw"
    model.mkdir(parents=True)
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    env = _isolated(tmp_path)
    env.update({
        "A770B_CARD": "b70",
        "A770B_CARD_MODE": "inference",
        "A770B_MODELS": str(models),
        "A770B_ALIAS": "local-builder",
    })
    result = subprocess.run(
        ["bash", str(LADDER), "qwen38-27b-exl3-4bpw-vllm", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    text = result.stdout
    assert "leave the server up" in text
    assert "llama-benchy" in text
    assert "bench_speed.sh" not in text
    assert str(model) in text
    rung3 = text.split("rung 4/6", 1)[0]
    assert " stop" not in rung3


def test_vllm_dry_run_stops_when_the_tokenizer_is_missing(tmp_path):
    models = tmp_path / "models"
    (models / "Qwen3.8-27B-exl3-4.00bpw").mkdir(parents=True)
    env = _isolated(tmp_path)
    env.update({
        "A770B_CARD": "b70",
        "A770B_CARD_MODE": "inference",
        "A770B_MODELS": str(models),
    })
    result = subprocess.run(
        ["bash", str(LADDER), "qwen38-27b-exl3-4bpw-vllm", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tokenizer.json" in result.stdout
    assert "llama-benchy" not in result.stdout


def test_a_log_with_no_llama_cpp_timings_does_not_print_a_zero_request_line(tmp_path):
    seat = tmp_path / "seat"
    seat.mkdir()
    subprocess.run(["git", "init", "-q", str(seat)], check=True)
    subprocess.run(
        ["git", "-C", str(seat), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = bindir / "docker.log"
    log.write_text("vllm engine ready\n", encoding="utf-8")
    docker = bindir / "docker"
    docker.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        cat "{log}" >&2
        """), encoding="utf-8")
    docker.chmod(0o755)
    blog = tmp_path / "build.log"
    blog.write_text("ok\n", encoding="utf-8")
    env = _isolated(tmp_path)
    env["A770B_DOCKER"] = str(docker)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(ROOT / "harness" / "capture_task.sh"), "notime", str(seat), str(blog)],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = tmp_path / "results" / "notime.task.md"
    body = report.read_text(encoding="utf-8")
    assert "timings unavailable (log has no llama.cpp timing lines)" in body
    assert "requests=" not in body


def test_render_readme_skips_the_attribution_block(tmp_path):
    env = _isolated(tmp_path)
    result = subprocess.run(["bash", str(ROOT / "render_readme.sh")], cwd=ROOT, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    html = (ROOT / "README.html").read_text(encoding="utf-8")
    intro = html.split("<h2>", 1)[0]
    assert "a770-builder is a harness around a local GPU" in intro
    assert "Eugene Rakhmatulin" not in intro
