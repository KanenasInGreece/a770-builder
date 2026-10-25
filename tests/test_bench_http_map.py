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


def test_llama_benchy_100k_is_the_100000_key_and_not_the_far_end(tmp_path):
    mapped = {
        "instrument": "llama-benchy",
        "tool": "llama-benchy",
        "prompt": 8192,
        "gen": 128,
        "flags": "uvx llama-benchy --pp 8192 32768 65536 --tg 128 --depth 0",
        "at_depth": {
            "8192": {"pp": 100.5, "tg": 21.0},
            "32768": {"pp": 80.0, "tg": 19.0},
            "65536": {"pp": 70.0, "tg": 18.0},
            "98976": {"pp": 1.0, "tg": 1.0},
        },
        "source": "raw.json",
    }
    path = tmp_path / "mapped.json"
    path.write_text(json.dumps(mapped), encoding="utf-8")
    out = tmp_path / "ladder.json"
    sweep = tmp_path / "sweep.log"
    sweep.write_text("target tokens\n", encoding="utf-8")
    depth = tmp_path / "depth.log"
    depth.write_text("depth probe at 98976: not measured — none\n", encoding="utf-8")
    argv = [
        str(out), "name", "/models/m", "100000", "q8_0", "q8_0", "off", "", "1800",
        "name", "SUITE-1@test", "98976", "", "",
        str(tmp_path / "no-bench-model.json"), str(path), str(sweep), str(depth),
        "", "", "", "", "2026-10-13T00:00:00",
    ]
    result = subprocess.run([sys.executable, str(LADDER_ROW)] + argv, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    speed = json.loads(out.read_text(encoding="utf-8"))["computed"]["speed"]
    assert speed["prefill_tps"]["100k"] is None
    assert speed["decode_tps"]["100k"] is None
    assert speed["prefill_tps"]["64k"] == 70.0


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
    uvx.write_text(
        "#!/bin/sh\n"
        'case "$1" in --version) printf "%s\\n" "uvx 0.13.5"; exit 0;; esac\n'
        f"touch {marker}\nexit 99\n",
        encoding="utf-8",
    )
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
    assert "http://127.0.0.1:9/v1" in line
    assert "--model local-builder" in line or "--model 'local-builder'" in line
    assert "return_token_ids=false" in line
    assert not marker.exists()


def test_dry_run_v1_base_is_not_doubled(tmp_path):
    env = _isolated(tmp_path)
    result = subprocess.run(
        ["bash", str(HTTP), "--dry-run", "--base-url", "http://127.0.0.1:9/v1",
         "--served-model-name", "local-builder", "--tokenizer", "/models/Qwen"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = result.stdout
    assert "llama-benchy" in line
    assert "http://127.0.0.1:9/v1" in line
    assert "/v1/v1" not in line


def test_dry_run_sends_the_key_flag_and_hides_the_value(tmp_path):
    secret = "a770b-dry-run-secret"
    keyfile = tmp_path / "api.key"
    keyfile.write_text(secret + "\n", encoding="utf-8")
    keyfile.chmod(0o600)
    env = _isolated(tmp_path)
    env["A770B_API_KEY_FILE"] = str(keyfile)
    result = subprocess.run(
        ["bash", str(HTTP), "--dry-run", "--base-url", "http://127.0.0.1:9",
         "--served-model-name", "local-builder", "--tokenizer", "/models/Qwen"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--api-key" in result.stdout
    assert "from-key-file" in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


def _dry(tmp_path, tokenizer):
    env = _isolated(tmp_path)
    return subprocess.run(
        ["bash", str(HTTP), "--dry-run", "--base-url", "http://127.0.0.1:9",
         "--served-model-name", "local-builder", "--tokenizer", str(tokenizer)],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )


def test_a_weight_file_is_not_a_tokenizer(tmp_path):
    weight = tmp_path / "model.gguf"
    weight.write_bytes(b"not-a-tokenizer")
    result = _dry(tmp_path, weight)
    assert result.returncode == 2
    assert "weight" in result.stderr
    assert "uvx" not in result.stdout


def test_an_oversize_tokenizer_symlink_is_refused(tmp_path):
    small = tmp_path / "real.json"
    small.write_text("{}", encoding="utf-8")
    ok_link = tmp_path / "ok" / "tokenizer.json"
    ok_link.parent.mkdir()
    ok_link.symlink_to(small)
    allowed = _dry(tmp_path, ok_link)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    target = tmp_path / "blob"
    with open(target, "wb") as fh:
        fh.truncate(67108865)
    link = tmp_path / "tokenizer.json"
    link.symlink_to(target)
    result = _dry(tmp_path, link)
    assert result.returncode == 2
    assert "weight" in result.stderr
    assert "uvx" not in result.stdout


def test_tokenizer_json_at_the_cap_is_allowed_and_one_byte_over_is_refused(tmp_path):
    at_cap = tmp_path / "at" / "tokenizer.json"
    at_cap.parent.mkdir()
    with open(at_cap, "wb") as fh:
        fh.truncate(67108864)
    allowed = _dry(tmp_path, at_cap)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "uvx" in allowed.stdout
    over = tmp_path / "over" / "tokenizer.json"
    over.parent.mkdir()
    with open(over, "wb") as fh:
        fh.truncate(67108865)
    refused = _dry(tmp_path, over)
    assert refused.returncode == 2
    assert "uvx" not in refused.stdout


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
    rung2 = text.split("rung 2/6", 1)[1].split("rung 3/6", 1)[0]
    assert "bench_http.sh" in rung2
    assert "bench_model.sh" not in rung2
    assert str(model) in text
    rung3 = text.split("rung 4/6", 1)[0]
    assert " stop" not in rung3


def _sglang_registry(path, model_name):
    doc = {
        "schema": 1,
        "card": "b70",
        "mode": "inference",
        "profiles": {
            "sglang-row": {
                "model": model_name,
                "source": "https://example.com/sglang-row",
                "family": "qwen",
                "architecture": "dense",
                "quant": "bf16",
                "kv": "f16",
                "flash_attention": "off",
                "reasoning": "off",
                "extra": "",
                "capability_source": "none",
                "use_for": "sglang dry-run",
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
                "ctx": 32768,
                "useful_ctx": 8000,
                "timeout_s": 1500,
                "vram_gib_after_load": 1,
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


def _sglang_dry_run(tmp_path, with_tokenizer, attached=False):
    models = tmp_path / "models"
    model = models / "sglang-model"
    model.mkdir(parents=True)
    if with_tokenizer:
        (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    registry = _sglang_registry(tmp_path / "profiles.json", "sglang-model")
    marker = tmp_path / "started-or-called"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("uvx", "docker", "curl"):
        stub = bindir / name
        if name == "uvx":
            stub.write_text(
                "#!/bin/sh\n"
                'case "$1" in --version) printf "%s\\n" "uvx 0.13.5"; exit 0;; esac\n'
                f"touch {marker}\nexit 99\n",
                encoding="utf-8",
            )
        else:
            stub.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
        stub.chmod(0o755)
    env = _isolated(tmp_path)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.update({
        "A770B_PROFILES_FILE": str(registry),
        "A770B_MODELS": str(models),
        "A770B_ALIAS": "local-builder",
        "A770B_CARD": "",
    })
    if attached:
        env["A770B_ATTACHED"] = "1"
    result = subprocess.run(
        ["bash", str(LADDER), "sglang-row", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    return result, model, marker


def test_sglang_dry_run_names_the_start(tmp_path):
    result, model, marker = _sglang_dry_run(tmp_path, with_tokenizer=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = result.stdout
    assert "bring: start" in text
    assert "serve_compose.sh start" in text
    assert "bench_model.sh" not in text
    assert "bench_http.sh" in text
    assert "this skill does not start SGLang" not in text
    assert "rung 3/6: not run" not in text
    assert "tok/s" not in text
    assert "bench_speed.sh" not in text
    assert str(model) in text
    assert not marker.exists()


def test_sglang_dry_run_names_bench_http_when_attached(tmp_path):
    result, model, marker = _sglang_dry_run(tmp_path, with_tokenizer=True, attached=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = result.stdout
    assert "bring: start" in text
    assert "bench_http.sh" in text
    assert "bench_model.sh" not in text
    assert "llama-benchy" in text
    assert "this skill does not start SGLang" not in text
    assert "rung 3/6: not run" not in text
    assert "bench_speed.sh" not in text
    assert str(model) in text
    assert not marker.exists()


def test_sglang_dry_run_stops_when_the_tokenizer_is_missing(tmp_path):
    result, model, marker = _sglang_dry_run(tmp_path, with_tokenizer=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STOPPED" in result.stdout
    assert "no tokenizer.json" in result.stdout
    assert str(model) in result.stdout
    assert "llama-benchy" not in result.stdout
    assert not marker.exists()


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


def _capture_untimed(tmp_path, blog_text, label):
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
    blog.write_text(blog_text, encoding="utf-8")
    env = _isolated(tmp_path)
    env["A770B_DOCKER"] = str(docker)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(ROOT / "harness" / "capture_task.sh"), label, str(seat), str(blog)],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "results" / f"{label}.task.md").read_text(encoding="utf-8")


def test_an_unnamed_engine_does_not_print_a_zero_request_line(tmp_path):
    body = _capture_untimed(tmp_path, "ok\n", "notime")
    assert "rates not measured" in body
    assert "context was not measured" in body
    assert "timings unavailable (log has no llama.cpp timing lines)" not in body
    assert "requests=" not in body
    assert "prompt_tokens_total=" not in body


def test_an_unnamed_engine_does_not_count_a_number_in_the_transcript(tmp_path):
    body = _capture_untimed(tmp_path, '{"usage":{"prompt_tokens":4321}}\n', "onetok")
    assert "rates not measured" in body
    assert "context was not measured" in body
    assert "prompt_tokens_total=4321" not in body
    assert "requests=" not in body


def test_render_readme_leads_with_the_attribution(tmp_path):
    env = _isolated(tmp_path)
    result = subprocess.run(["bash", str(ROOT / "render_readme.sh")], cwd=ROOT, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    html = (ROOT / "README.html").read_text(encoding="utf-8")
    title_at = html.index("<h1>")
    after_title = html[html.index("</h1>", title_at) + len("</h1>"):]
    first, description = after_title.split("a770-builder qualifies a local coding model", 1)
    assert "llama-benchy" in first
    assert "https://github.com/sybil-solutions/qwen38-flash-next-b70-offload" in first
    assert "Strata" in first
    assert "https://github.com/Niko1221/Strata" in first
    assert "d30d" + "f691" not in html
    assert description
