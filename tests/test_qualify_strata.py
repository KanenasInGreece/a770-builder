"""Strata qualification: export, floor stop, and the gate.

No server, container, or card job. Level Zero is mocked. A floor file never
holds this process's pid.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "harness"))

import profiling_contract
import qualify_lib as ql
import strata_managed as managed
import suite_report
from test_qualify_lib import _job, _min_row, _wrap
from test_qualify_run import QUALIFY, _env, _make_bin, _run
from test_review_r6_fixes import PROFILE, ROOT, RUN_SUITE_SH, _base_env, _make_exec


def _fn(source: str, name: str) -> str:
    text = Path(source).read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    depth = 0
    for index in range(text.index("{", start), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(name)


def _profiles(tmp_path, engine="strata"):
    path = tmp_path / f"{engine}.json"
    path.write_text(json.dumps(_wrap(_min_row(engine), "sample-row")), encoding="utf-8")
    return path


def _plan(tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    monkeypatch.delenv("A770B_OUTPUT_TOKENS", raising=False)
    return ql.build_plan(
        [
            "--engine", "strata",
            "--model", "sample-row",
            "--profiles-file", str(_profiles(tmp_path)),
            "--tokenizer", "/tmp/tokenizer-dir",
            "--stages", "s2-backend",
        ]
    )


def test_t22_strata_export_uses_pinned_ram_and_omits_flash_attention(tmp_path, monkeypatch):
    """A complete Strata job exports ram from this job's pinned observation."""
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "results").mkdir()
    monkeypatch.setenv("A770B_DATA", str(data))
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = _plan(tmp_path, monkeypatch)
    job = _job(tmp_path, plan)
    far = plan["far_end"]
    pinned = round(1.5 * 1024 ** 3)
    stale = data / "logs" / "stale-evidence.json"
    stale.write_text(json.dumps({
        "engine": "strata",
        "host_buffers_mib": 8192,
        "observed_pinned_host_memory": {"value_bytes": 9 * 1024 ** 3},
    }), encoding="utf-8")
    (data / "logs" / "serve-evidence.path").write_text(str(stale) + "\n", encoding="utf-8")
    (job / "evidence" / "engine-evidence.json").write_text(json.dumps({
        "engine": "strata",
        "host_buffers_mib": 2048,
        "observed_pinned_host_memory": {"value_bytes": pinned},
    }), encoding="utf-8")
    profiling_contract.write_receipt(
        str(job / "evidence" / "profiling-start.json"),
        profiling_contract.receipt("strata", {}),
    )
    mapped = job / "evidence" / "speed-bench.mapped.json"
    mapped.write_text(json.dumps({
        "tool": "llama-benchy",
        "prompt": 8192,
        "gen": 128,
        "flags": "default",
        "at_depth": {"8192": {"pp": 110.0, "tg": 21.0}},
    }), encoding="utf-8")
    label = "s2-backend"
    capture = data / "results" / f"{label}.task.md"
    capture.write_text(
        "## server-side timings during the run\n"
        "requests=4 prompt_tokens_total=100 gen_tokens_total=10\n"
        "max_prompt_tokens=100\n"
        "max_completion_tokens=10\n"
        "length_stops=0\n"
        "prefill_tps=40.0 decode_tps=12.0\n",
        encoding="utf-8",
    )
    suite_path = data / "results" / "suite.json"
    suite_path.write_text(json.dumps({
        "instrument": "SUITE-1@1",
        "stages": [{
            "id": "s2-backend",
            "working": True,
            "counts_toward_pass": True,
            "wall_s": 2,
            "label": label,
            "requests": 4,
            "prompt_tokens": 100,
            "gen_tokens": 10,
            "max_prompt_tokens": 100,
            "max_completion_tokens": 10,
            "length_stops": 0,
            "axes": [],
        }],
    }), encoding="utf-8")
    ql.apply_phase(str(job), "serve", (
        "sample serving alias on 127.0.0.1:9 · Strata\n"
        "VRAM after load: 8.0 GiB ≤ configured 15.0 GiB\n"
        "phase_s 3\n"
    ), 0)
    ql.apply_phase(str(job), "smoke", '{"models": true, "completion": true, "tool": true}\n', 0)
    ql.apply_phase(str(job), "speed", f"mapped → {mapped}\n", 0)
    ql.apply_phase(str(job), "window", (
        "8000 8000 1.25 110.0 21.0 4.5GiB 0\n"
        f"{far} 15000 2.5 80.0 15.0 5.0GiB 0\n"
    ), 0)
    ql.apply_phase(str(job), "depth", f"depth probe at {far}: 3/3\n", 0)
    ql.apply_phase(str(job), "suite", f"suite complete: {suite_path}\n", 0)
    ql.assemble(str(job))
    collected = json.loads((job / "evidence" / "collected.json").read_text(encoding="utf-8"))
    assert collected["ram_gb_extra"] == pytest.approx(1.5)
    result = ql.summarise(str(job))
    assert "flash_attention" not in result["statistics"] or (
        result["statistics"].get("flash_attention") in (None, "unknown")
    )
    out = tmp_path / "outside" / "row.json"
    out.parent.mkdir()
    ql.export_job(str(job), str(out), False)
    row = json.loads(out.read_text(encoding="utf-8"))
    assert "flash_attention" not in row
    assert row["ram_gb_extra"] == pytest.approx(1.5)
    assert row["engine"] == "strata"
    broken = json.loads((job / "result.json").read_text(encoding="utf-8"))
    broken["useful_ctx"] = "unknown"
    broken["statistics"]["useful_ctx"] = "unknown"
    broken["statistics"].pop("flash_attention", None)
    (job / "result.json").write_text(json.dumps(broken), encoding="utf-8")
    partial = tmp_path / "outside" / "partial.json"
    ql.export_job(str(job), str(partial), True)
    again = json.loads(partial.read_text(encoding="utf-8"))
    assert "flash_attention" not in again
    assert "flash_attention" not in ql.required_statistic_fields("strata")
    assert "flash_attention" in ql.required_statistic_fields("llama.cpp")
    assert "flash_attention" in ql.required_statistic_fields(None)
    buffers_only = job / "evidence" / "engine-evidence.json"
    buffers_only.write_text(json.dumps({
        "engine": "strata",
        "host_buffers_mib": 2048,
    }), encoding="utf-8")
    assert ql._strata_pinned_gib(str(buffers_only)) is None
    assert ql._ram_from_evidence(str(job), True) is None
    ql.assemble(str(job))
    bare = ql.summarise(str(job))
    assert bare["ram_gb_extra"] in (None, "unknown")
    assert bare["statistics"]["ram_gb_extra"] == "unknown"


def test_observe_copies_pinned_ram_and_ignores_host_buffers(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    log_dir = tmp_path / "run"
    log_dir.mkdir()
    (log_dir / "engine.log").write_text("using 1.5 GiB of pinned host RAM\n", encoding="utf-8")
    target = data / "logs" / "live.json"
    target.write_text(json.dumps({
        "engine": "strata",
        "log_dir": str(log_dir),
        "host_buffers_mib": 4096,
    }), encoding="utf-8")
    (data / "logs" / "serve-evidence.path").write_text(str(target) + "\n", encoding="utf-8")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    (job / "plan.json").write_text(json.dumps({"engine": "strata"}), encoding="utf-8")
    monkeypatch.setenv("A770B_DATA", str(data))
    monkeypatch.setenv("A770B_PROJECT", PROJECT)
    monkeypatch.setenv("A770B_DEFER_DATA_DIRS", "1")
    script = "\n".join([
        "set -u",
        _fn(os.path.join(PROJECT, "harness", "qualify.sh"), "refuse_link"),
        _fn(os.path.join(PROJECT, "harness", "qualify.sh"), "write_engine_evidence"),
        'write_engine_evidence',
    ])
    env = os.environ.copy()
    env.update(
        A770B_DATA=str(data),
        A770B_PROJECT=PROJECT,
        A770B_DEFER_DATA_DIRS="1",
        engine="strata",
        job=str(job),
        here=os.path.join(PROJECT, "harness"),
    )
    got = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    copied = json.loads((job / "evidence" / "engine-evidence.json").read_text(encoding="utf-8"))
    assert copied["observed_pinned_host_memory"]["value_bytes"] == round(1.5 * 1024 ** 3)
    assert ql._ram_from_evidence(str(job), True) == pytest.approx(1.5)
    buffers_only = job / "evidence" / "buffers-only.json"
    buffers_only.write_text(json.dumps({"host_buffers_mib": 4096}), encoding="utf-8")
    assert ql._strata_pinned_gib(str(buffers_only)) is None
    (job / "evidence" / "engine-evidence.json").write_text(
        json.dumps({"engine": "strata", "host_buffers_mib": 4096}), encoding="utf-8",
    )
    assert ql._ram_from_evidence(str(job), True) is None
    text = Path(PROJECT, "harness", "qualify.sh").read_text(encoding="utf-8")
    stop = text.index("if ! stop_server; then")
    second = text.index("write_engine_evidence", stop)
    suite = text.index("phase suite", second)
    assert stop < second < suite
    assert "STUB" in text[stop:suite]


def test_floor_hit_ignores_a_sibling_file(tmp_path):
    selected = tmp_path / "this.json"
    selected.write_text("", encoding="utf-8")
    (tmp_path / "other.json").write_text('{"phase":"launch"}\n', encoding="utf-8")
    script = "\n".join([
        "set -u",
        _fn(os.path.join(PROJECT, "harness", "qualify.sh"), "floor_hit"),
        "floor_hit",
    ])
    env = os.environ.copy()
    env["A770B_STRATA_FLOOR_FILE"] = str(selected)
    env["job"] = str(tmp_path)
    got = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert got.returncode != 0


def test_t23_launch_floor_ends_before_smoke(tmp_path):
    bindir = tmp_path / "bin"
    floor_body = textwrap.dedent(
        """\
        #!/bin/sh
        printf '%s\\n' '{"phase":"launch","available_mib":10,"floor_mib":999999,"instance":null}' > "$A770B_STRATA_FLOOR_FILE"
        exit 1
        """
    )
    gate_body = "#!/bin/sh\nexit 0\n"
    _make_bin(bindir, {
        "a770b-qualify-serve": floor_body,
        "a770b-qualify-gate": gate_body,
    })
    env = _env(tmp_path, bindir)
    env["A770B_QUALIFY_MIN_FREE_GIB"] = "0"
    env["A770B_MIN_AVAIL_MB"] = "1"
    env["A770B_CARD"] = "a770"
    env["A770B_CARD_MODE"] = "inference"
    path = tmp_path / "strata.json"
    path.write_text(json.dumps(_wrap(_min_row("strata"), "sample-row")), encoding="utf-8")
    got = _run(env, [
        "start", "--foreground", "--engine", "strata", "--model", "sample-row",
        "--profiles-file", str(path), "--tokenizer", env["A770B_TOKENIZER_DIR"],
        "--stages", "s2-backend",
    ])
    assert got.returncode == 4, got.stdout + got.stderr
    job = next((Path(env["A770B_DATA"]) / "qualify").iterdir())
    status = json.loads((job / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["reason"] == "strata_floor_stop"
    assert (job / "FAILED").is_file()
    assert not (job / "phases" / "smoke.log").exists()
    assert not (job / "evidence" / "smoke.json").exists()
    assert (job / "status.json").read_text(encoding="utf-8").find(str(os.getpid())) == -1
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    counted = next(item for item in result["checks"] if item["name"] == "counted-stages-pass")
    assert counted["result"] == "unknown"
    assert counted["reason"] == "strata floor stop"
    assert "STRATA_FLOOR_STOP" in (job / "report.md").read_text(encoding="utf-8")


def test_phase5_floor_stop_keeps_counted_reason_before_terminal_status(tmp_path, monkeypatch):
    """Summarise runs before qualify publishes the terminal reason."""
    monkeypatch.setenv("A770B_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    plan = _plan(tmp_path, monkeypatch)
    job = _job(tmp_path, plan)
    status = json.loads((job / "status.json").read_text(encoding="utf-8"))
    status["phase"] = "suite"
    status["reason"] = None
    (job / "status.json").write_text(json.dumps(status) + "\n", encoding="utf-8")
    result = ql.summarise(str(job), abort_reason="strata_floor_stop")
    counted = next(item for item in result["checks"] if item["name"] == "counted-stages-pass")
    assert counted["result"] == "unknown"
    assert counted["reason"] == "strata floor stop"
    assert "STRATA_FLOOR_STOP" in (job / "report.md").read_text(encoding="utf-8")


def test_start_and_admit_write_the_floor_file(tmp_path, monkeypatch):
    floor = tmp_path / "floor.json"
    monkeypatch.setattr(managed, "prepare_config", lambda _path: ({}, {"selector": "level_zero:0"}))
    monkeypatch.setattr(managed, "source_inputs", lambda: (tmp_path, Path(sys.executable)))
    monkeypatch.setattr(managed, "require_selected_card", lambda _selector: None)
    monkeypatch.setattr(managed, "memavailable_mib", lambda: 10)
    monkeypatch.setenv("A770B_HOST", "127.0.0.1")
    monkeypatch.setenv("A770B_PORT", "9")
    monkeypatch.setenv("A770B_MIN_AVAIL_MB", "999999")
    monkeypatch.setenv("A770B_STRATA_FLOOR_FILE", str(floor))
    with pytest.raises(ValueError, match="floor"):
        managed.start(tmp_path / "instance.json", tmp_path / "cfg.json", 1024, 1.0)
    written = json.loads(floor.read_text(encoding="utf-8"))
    assert written["phase"] == "launch"
    assert written["instance"] is None
    assert written["available_mib"] == 10
    assert written["floor_mib"] == 999999

    admit_floor = tmp_path / "admit.json"
    monkeypatch.setenv("A770B_STRATA_FLOOR_FILE", str(admit_floor))
    monkeypatch.setattr(managed, "read_state", lambda _path: {"min_ram_mib": 50000, "id": "abc"})
    monkeypatch.setattr(managed, "read_key", lambda: "key")
    monkeypatch.setattr(managed, "process_alive", lambda _state: True)
    monkeypatch.setattr(managed, "health_matches", lambda _state, _key: True)
    assert managed.admit(tmp_path / "instance.json") is False
    admitted = json.loads(admit_floor.read_text(encoding="utf-8"))
    assert admitted["phase"] == "admit"
    assert admitted["instance"] == "abc"
    assert admitted["available_mib"] == 10


def test_supervisor_writes_the_plan_floor_file(tmp_path, monkeypatch):
    terminal = tmp_path / "terminal"
    (terminal / "serve").mkdir(parents=True)
    (terminal / "serve" / "__init__.py").write_text("", encoding="utf-8")
    (terminal / "serve" / "server.py").write_text(
        "import argparse, os\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--owner-fd', type=int)\n"
        "args, _rest = p.parse_known_args()\n"
        "os.read(args.owner_fd, 1)\n",
        encoding="utf-8",
    )
    plan_floor = tmp_path / "plan-floor.json"
    other = tmp_path / "other-floor.json"
    plan_floor.write_text("", encoding="utf-8")
    other.write_text("", encoding="utf-8")
    key = tmp_path / "key"
    key.write_text("fixture-key\n", encoding="utf-8")
    monkeypatch.setenv("A770B_API_KEY_FILE", str(key))
    monkeypatch.setenv("A770B_STRATA_FLOOR_FILE", str(other))
    monkeypatch.setattr(managed, "memavailable_mib", lambda: 10)
    snapshot = tmp_path / "config.json"
    snapshot.write_text("{}", encoding="utf-8")
    state = tmp_path / "instance.json"
    plan = {
        "run": str(tmp_path / "run"),
        "snapshot": str(snapshot),
        "port": 9,
        "state": str(state),
        "selector": "level_zero:0",
        "min_ram_mib": 999999,
        "python": sys.executable,
        "terminal": str(terminal),
        "id": "owned-instance",
        "floor_file": str(plan_floor),
    }
    (tmp_path / "run").mkdir()
    plan_path = tmp_path / "launch.json"
    managed.write_state(plan_path, plan)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"1")
    os.close(write_fd)
    saved_term = signal.getsignal(signal.SIGTERM)
    saved_int = signal.getsignal(signal.SIGINT)
    try:
        managed.supervise(plan_path, read_fd)
        written = json.loads(plan_floor.read_text(encoding="utf-8"))
        assert written["phase"] == "running"
        assert written["instance"] == "owned-instance"
        assert written["available_mib"] == 10
        assert written["floor_mib"] == 999999
        assert other.read_text(encoding="utf-8") == ""
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL if saved_term is None else saved_term)
        signal.signal(signal.SIGINT, signal.SIG_DFL if saved_int is None else saved_int)


def test_builtin_budget_gate_records_a_launch_floor(tmp_path):
    """A cold start refused for low MemAvailable leaves a launch floor file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nvtop = bin_dir / "nvtop"
    nvtop.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' '[{\"device_name\":\"Fake Arc\",\"mem_used\":0,"
        "\"mem_total\":17179869184,\"mem_free\":17179869184}]'\n",
        encoding="utf-8",
    )
    nvtop.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    floor = tmp_path / "floor.json"
    key = tmp_path / "api.key"
    key.write_text("a770b-test\n", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        "A770B_PROJECT": PROJECT,
        "A770B_DATA": str(data),
        "A770B_HOST": "127.0.0.1",
        "A770B_PORT": "9",
        "A770B_VRAM_CAP_GIB": "16",
        "A770B_CARD_VRAM_TOTAL": "16",
        "A770B_GPU_MATCH": "Fake",
        "A770B_MIN_AVAIL_MB": "999999999",
        "A770B_BUDGET_GATE": "",
        "A770B_HEALTH_URL": "",
        "A770B_API_KEY_FILE": str(key),
        "A770B_DOCKER": "docker",
        "A770B_FRAMEWORK_PORTS": "",
        "A770B_SERVE": "compose",
        "A770B_COMPOSE_PROJECT": "a770-builder",
        "A770B_CARD": "",
        "A770B_STRATA_FLOOR_FILE": str(floor),
        "A770B_PROFILES_FILE": "",
        "A770B_DEFER_DATA_DIRS": "1",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    })
    (tmp_path / "xdg").mkdir()
    got = subprocess.run(
        ["bash", os.path.join(PROJECT, "harness", "serve_strata.sh"), "start", str(tmp_path / "prepared.json"), "8192"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert got.returncode != 0, got.stdout + got.stderr
    written = json.loads(floor.read_text(encoding="utf-8"))
    assert written["phase"] == "launch"
    assert isinstance(written["available_mib"], int)
    assert written["available_mib"] < written["floor_mib"]


def _suite(tmp_path, stages, reference=False):
    body = []
    for sid in stages:
        brief = tmp_path / f"{sid}.md"
        spec = tmp_path / f"{sid}.spec.json"
        brief.write_text(f"# {sid}\n", encoding="utf-8")
        spec.write_text("{}\n", encoding="utf-8")
        body.append({
            "id": sid, "language": "python", "brief": str(brief), "spec": str(spec),
            "grader": {"working": "true", "budget_lines": 50},
        })
    suite = tmp_path / "kit" / "suite.json"
    suite.parent.mkdir(parents=True)
    suite.write_text(json.dumps({"suite": "R6", "kit_version": "1", "stages": body}), encoding="utf-8")
    seat = suite.parent / "seat"
    seat.mkdir()
    (seat / "README.md").write_text("fixture kit seat\n", encoding="utf-8")
    if reference:
        ref = suite.parent / "reference"
        ref.mkdir()
        brief = ref / "ref-one.md"
        spec = ref / "ref-one.spec.json"
        brief.write_text("# ref\n", encoding="utf-8")
        spec.write_text("{}\n", encoding="utf-8")
        (ref / "reference.json").write_text(json.dumps({
            "entries": [{
                "id": "ref-one", "language": "python",
                "brief": str(brief), "spec": str(spec),
                "grader": {"working": "true", "budget_lines": 50},
            }],
        }), encoding="utf-8")
    return suite


def _counted_from_suite(tmp_path, monkeypatch, results_path):
    monkeypatch.setenv("A770B_DATA", str(results_path.parent.parent))
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    plan = _plan(tmp_path, monkeypatch)
    parent = tmp_path / "sum"
    parent.mkdir()
    job = _job(parent, plan)
    ql.apply_phase(str(job), "suite", f"suite incomplete — results: {results_path}\n", 4)
    ql.assemble(str(job))
    result = ql.summarise(str(job))
    check = next(item for item in result["checks"] if item["name"] == "counted-stages-pass")
    return check, (job / "report.md").read_text(encoding="utf-8"), result


def test_t23_suite_floor_keeps_the_first_stage_and_stops(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    suite = _suite(tmp_path, ("s1-pass", "s2-floor", "s3-later"), reference=True)
    floor = tmp_path / "floor.json"
    floor.write_text("", encoding="utf-8")
    later = tmp_path / "later"
    env["A770B_STRATA_FLOOR_FILE"] = str(floor)
    fake = _make_exec(tmp_path / "fake-lb.sh", f'''
brief=$3
case "$1" in
  run)
    case "$brief" in
      *s1-pass*)
        cap="$A770B_DATA/results/s1-pass.task.md"
        mkdir -p "$A770B_DATA/results"
        printf 'capture: %s\\n' "$cap"
        printf '%s\\n' '## server-side timings during the run' \\
          'requests=2 prompt_tokens_total=40 gen_tokens_total=8' > "$cap"
        printf '%s\\n' 'diff' > "$A770B_DATA/results/s1-pass.patch"
        sleep 1
        exit 0
        ;;
      *s2-floor*)
        printf '%s\\n' '{{"phase":"running","available_mib":10,"floor_mib":999999,"instance":null}}' > "$A770B_STRATA_FLOOR_FILE"
        exit 0
        ;;
      *)
        : > "{later}"
        exit 0
        ;;
    esac
    ;;
  verify)
    printf '%s\\n' '{{"status":"pass","public":"pass","hidden":"pass","exit_code":0}}' > "$A770B_VERIFY_OUT"
    exit 0
    ;;
esac
exit 0
''')
    env["A770B_LOCAL_BUILD"] = str(fake)
    got = subprocess.run(
        ["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert got.returncode == 4, got.stdout + got.stderr
    assert "suite incomplete" in got.stderr
    assert "suite stopped early" not in got.stderr
    assert not later.exists()
    line = next(item for item in got.stderr.splitlines() if "results:" in item)
    results = Path(line.rsplit("results:", 1)[1].strip())
    doc = json.loads(results.read_text(encoding="utf-8"))
    stages = doc["stages"]
    assert [stage["id"] for stage in stages] == ["s1-pass", "s2-floor"]
    assert stages[0]["working"] is True
    assert stages[1]["working"] is None
    assert stages[1]["outcome"] == "strata_floor_stop"
    registry = json.loads(Path(env["A770B_PROFILES_FILE"]).read_text(encoding="utf-8"))
    measured = registry["profiles"][PROFILE].get("measured_tasks") or {}
    assert "s1-pass" in measured
    assert "s2-floor" not in measured
    check, report, result = _counted_from_suite(tmp_path, monkeypatch, results)
    assert check["result"] == "unknown"
    assert check["reason"] == "strata floor stop"
    assert "STRATA_FLOOR_STOP" in report
    assert result["measured_tasks"]["s1-pass"]["quality"] == "pass"
    assert "s2-floor" not in result["measured_tasks"]
    assert suite_report.outcome_label({"outcome": "strata_floor_stop"}) == "STRATA_FLOOR_STOP"


def test_t23_another_jobs_floor_file_does_not_stop_this_run(tmp_path):
    env = _base_env(tmp_path)
    suite = _suite(tmp_path, ("s1-pass",))
    selected = tmp_path / "this-floor.json"
    selected.write_text("", encoding="utf-8")
    (tmp_path / "other-floor.json").write_text('{"phase":"launch"}\n', encoding="utf-8")
    ran = tmp_path / "ran"
    env["A770B_STRATA_FLOOR_FILE"] = str(selected)
    env["A770B_LOCAL_BUILD"] = str(_make_exec(tmp_path / "fake-lb.sh", f'''
if [ "$1" = run ]; then
  cap="$A770B_DATA/results/s1-pass.task.md"
  mkdir -p "$A770B_DATA/results"
  printf 'capture: %s\\n' "$cap"
  printf '%s\\n' '## server-side timings during the run' 'requests=1 prompt_tokens_total=8 gen_tokens_total=1' > "$cap"
  printf '%s\\n' 'diff' > "$A770B_DATA/results/s1-pass.patch"
  sleep 1
  : > "{ran}"
  exit 0
fi
if [ "$1" = verify ]; then
  printf '%s\\n' '{{"status":"pass","exit_code":0}}' > "$A770B_VERIFY_OUT"
  exit 0
fi
exit 0
'''))
    got = subprocess.run(
        ["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert "a floor stop already ended this run" not in got.stderr
    assert ran.is_file()


def test_t23_a_floor_file_already_written_exits_before_a_stage(tmp_path):
    env = _base_env(tmp_path)
    suite = _suite(tmp_path, ("s1-pass",))
    selected = tmp_path / "this-floor.json"
    selected.write_text('{"phase":"launch"}\n', encoding="utf-8")
    ran = tmp_path / "ran"
    env["A770B_STRATA_FLOOR_FILE"] = str(selected)
    env["A770B_LOCAL_BUILD"] = str(_make_exec(tmp_path / "fake-lb.sh", f': > "{ran}"; exit 0\n'))
    got = subprocess.run(
        ["bash", str(RUN_SUITE_SH), PROFILE, str(tmp_path / "exported-seat"), "--suite", str(suite)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert got.returncode == 4, got.stdout + got.stderr
    assert "a floor stop already ended this run" in got.stderr
    assert "suite incomplete" not in got.stderr
    assert not ran.exists()


def _gate_config(tmp_path, monkeypatch):
    exe = tmp_path / "engine"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    tok = tmp_path / "tok"
    tok.mkdir()
    (tok / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    (tok / "vocab.json").write_text("{}\n", encoding="utf-8")
    cfg = tmp_path / "model.json"
    cfg.write_text(json.dumps({
        "backend": "intel",
        "exe": str(exe),
        "cwd": str(tmp_path),
        "tokenizer": str(tok),
        "model_name": "sample-served",
        "parallel": 1,
        "args": [],
        "env": {},
    }), encoding="utf-8")
    monkeypatch.setenv("A770B_STRATA_ENGINE_SHA256", managed.file_digest(exe))
    monkeypatch.setenv("A770B_ONEAPI_DEVICE_SELECTOR", "level_zero:0")
    monkeypatch.setenv("A770B_CARD", "b70")
    monkeypatch.setenv("A770B_GPU_MATCH", "G31")
    return cfg, exe


def test_t24_gate_fails_for_root_python_and_card_and_names_the_digest(tmp_path, monkeypatch, capsys):
    cfg, exe = _gate_config(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("A770B_STRATA_ROOT", str(root))
    monkeypatch.setenv("A770B_STRATA_PYTHON", str(python))
    with pytest.raises(ValueError, match="absolute"):
        managed.gate(cfg)
    monkeypatch.setattr(managed, "source_inputs", lambda: (root / "terminal", Path(sys.executable)))
    monkeypatch.setattr(managed, "level_zero_pci_ids", lambda: [(0x8086, 0x56A0)])
    with pytest.raises(ValueError, match="does not select"):
        managed.gate(cfg)
    monkeypatch.setattr(managed, "level_zero_pci_ids", lambda: [(0x8086, 0xE223)])
    managed.gate(cfg)
    digest = managed.file_digest(exe)
    assert capsys.readouterr().out.strip() == f"strata@sha256:{digest}"
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    job = _job(tmp_path, _plan(tmp_path, monkeypatch))
    ql.apply_phase(str(job), "gate", f"strata@sha256:{digest}\nphase_s 2\n", 0)
    image = json.loads((job / "job.json").read_text(encoding="utf-8"))["image"]
    assert image == f"sha256:{digest}"


def test_t24_gate_preflight_names_gate_and_alias_stays_the_model(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "nvtop": "#!/bin/sh\nprintf '%s\\n' '[]'\n",
        "docker": "#!/bin/sh\nexit 0\n",
    }.items():
        path = bindir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    env = _env(tmp_path, bindir)
    env.pop("A770B_QUALIFY_STUB", None)
    env["PATH"] = f"{bindir}:{os.environ.get('PATH', '')}"
    env["A770B_QUALIFY_MIN_FREE_GIB"] = "0"
    env["A770B_MIN_AVAIL_MB"] = "1"
    env["A770B_CARD"] = "a770"
    env["A770B_CARD_MODE"] = "inference"
    env["A770B_GPU_MATCH"] = "DG2"
    env["A770B_DOCKER"] = str(bindir / "docker")
    exe = tmp_path / "engine"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    tok = Path(env["A770B_TOKENIZER_DIR"])
    (tok / "vocab.json").write_text("{}\n", encoding="utf-8")
    (tok / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    models = Path(env["A770B_MODELS"])
    cfg = models / "sample.strata.json"
    cfg.write_text(json.dumps({
        "backend": "intel",
        "exe": str(exe),
        "cwd": str(models),
        "tokenizer": str(tok),
        "model_name": "alias-model",
        "parallel": 1,
        "args": [],
        "env": {},
    }), encoding="utf-8")
    digest = managed.file_digest(exe)
    env["A770B_STRATA_ENGINE_SHA256"] = digest
    env["A770B_ONEAPI_DEVICE_SELECTOR"] = "level_zero:0"
    env["A770B_STRATA_ROOT"] = str(tmp_path / "missing-root")
    env["A770B_STRATA_PYTHON"] = sys.executable
    row = _min_row("strata")
    row["model"] = "sample.strata.json"
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps(_wrap(row, "sample-row")), encoding="utf-8")
    got = _run(env, [
        "start", "--foreground", "--engine", "strata", "--model", "sample-row",
        "--profiles-file", str(profiles), "--stages", "s2-backend",
    ], timeout=90)
    assert got.returncode == 4, got.stdout + got.stderr
    job = next((Path(env["A770B_DATA"]) / "qualify").iterdir())
    status = json.loads((job / "status.json").read_text(encoding="utf-8"))
    assert status["reason"] == "preflight"
    argv = (job / "evidence" / "argv.log").read_text(encoding="utf-8")
    assert "serve_strata.sh gate" in argv
    assert not (job / "phases" / "smoke.log").exists()
    named = subprocess.run(
        ["bash", os.path.join(PROJECT, "harness", "serve_strata.sh"), "config-model", str(cfg)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert named.returncode == 0, named.stderr
    assert named.stdout.strip() == "alias-model"
    text = Path(PROJECT, "harness", "qualify.sh").read_text(encoding="utf-8")
    assert "serve_strata.sh\" gate" in text
    assert "serve_strata.sh\" config-model" in text
    engines = text.split("--engine <", 1)[1].split(">", 1)[0]
    assert "strata" in engines.split("|")
    script = "\n".join([
        "set -u",
        _fn(os.path.join(PROJECT, "harness", "qualify.sh"), "model_path"),
        _fn(os.path.join(PROJECT, "harness", "qualify.sh"), "resolve_alias"),
        "resolve_alias",
        'printf "%s\\n" "$A770B_ALIAS"',
    ])
    alias_env = dict(env)
    alias_env.update(engine="strata", here=os.path.join(PROJECT, "harness"))
    # pget is qualify's plan reader. The extracted alias path only needs the artifact name.
    alias_env["artifact"] = "sample.strata.json"
    wrapped = "pget() { printf '%s\\n' \"$artifact\"; }\n" + script
    resolved = subprocess.run(["bash", "-c", wrapped], env=alias_env, capture_output=True, text=True, timeout=60)
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "alias-model"
