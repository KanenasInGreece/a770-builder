"""Unit tests for qualify planning, status, results, and export.

These tests do not start a server, a container, or a card job.
"""

import json
import os
import re
import stat
import subprocess
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "harness"))

import profiling_contract as pc
import qualify_lib as ql


def _gguf_args(extra=None):
    args = [
        "--engine",
        "llama.cpp",
        "--model",
        "sample.gguf",
        "--ctx",
        "16384",
        "--backend",
        "vulkan",
        "--tokenizer",
        "/tmp/tokenizer-dir",
    ]
    if extra:
        args.extend(extra)
    return args


@pytest.fixture
def kit_env(monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    monkeypatch.delenv("A770B_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("A770B_SERVED_BACKEND", raising=False)
    monkeypatch.delenv("A770B_TOKENIZER_DIR", raising=False)


def test_i19_required_statistics_match_profile_md():
    text = open(os.path.join(PROJECT, "kit", "PROFILE.md"), encoding="utf-8").read().splitlines()
    start = next(i for i, line in enumerate(text) if line.startswith("## 2."))
    rows = []
    seen = False
    for line in text[start:]:
        if seen and line.startswith("#"):
            break
        if not line.startswith("|"):
            if seen:
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not seen:
            if cells[0] == "field":
                seen = True
                assert cells[1] == "who writes it"
            continue
        if set(cells[0]) <= set("-: "):
            continue
        field = re.sub(r"\s+", " ", cells[0].replace("`", "")).strip()
        rows.append((field, cells[1]))
    assert [row["field"] for row in ql.REQUIRED_STATISTICS] == [field for field, _ in rows]
    for item, (field, who) in zip(ql.REQUIRED_STATISTICS, rows):
        assert item["who"] in who.lower()
        assert ("conditional" in who.lower()) is item["conditional"]


def test_i6_unknown_option_and_engine_map(kit_env, monkeypatch):
    calls = []
    real = pc.refusal

    def spy(engine, argv):
        calls.append(engine)
        return real(engine, argv)

    monkeypatch.setattr(pc, "refusal", spy)
    with pytest.raises(ql.QualifyError) as caught:
        ql.build_plan(["--engine", "nope", "--model", "sample.gguf", "--ctx", "16384"])
    assert caught.value.code == 2
    assert calls == []
    plan = ql.build_plan(
        ["--engine", "llama", "--model", "sample.gguf", "--ctx", "16384", "--backend", "vulkan", "--tokenizer", "/tmp/tokenizer-dir"]
    )
    assert plan["engine"] == "llama.cpp"
    assert calls
    assert set(calls) == {"llama.cpp"}
    with pytest.raises(ql.QualifyError, match="unknown qualify option"):
        ql.build_plan(_gguf_args(["--not-a-flag"]))


def test_i6_forbidden_extra_and_profile_extra(kit_env, tmp_path, monkeypatch):
    flag = pc.TABLE["llama.cpp"]["flags"][0]
    with pytest.raises(ql.QualifyError, match="refuses"):
        ql.build_plan(_gguf_args(["--extra=" + flag]))
    marker = tmp_path / "created"
    assert not marker.exists()
    row = _min_row("vllm")
    row["extra"] = pc.TABLE["vllm"]["flags"][0]
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(_wrap(row, "sample-vllm")))
    with pytest.raises(ql.QualifyError, match="refuses"):
        ql.build_plan(
            [
                "--engine",
                "vllm",
                "--model",
                "sample-vllm",
                "--profiles-file",
                str(path),
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
    assert list(tmp_path.iterdir()) == [path]


def test_i12_and_i14_dry_run_argv_once(kit_env, tmp_path, capsys):
    before = set(os.listdir(tmp_path))
    code = ql.cmd_plan(["--dry-run", *_gguf_args()])
    assert code == 0
    text = capsys.readouterr().out
    assert "starts nothing" in text
    assert set(os.listdir(tmp_path)) == before
    argv = text.split("serve-argv: ", 1)[1].splitlines()[0]
    for token in pc.flags("llama.cpp"):
        if token == "4":
            continue
        assert argv.count(token) == 1
    for engine in ("sglang", "strata"):
        plan = ql.build_plan(
            [
                "--engine",
                engine,
                "--model",
                "sample-row",
                "--profiles-file",
                str(_write_profiles(tmp_path, engine)),
                "--dry-run",
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
        assert plan["profiling"]["flags"] == []
        assert pc.flags("llama.cpp")[0] not in plan["serve_argv"]


def test_i17_absorb(kit_env):
    plan = ql.build_plan(_gguf_args(["--absorb-max-s", "30"]))
    assert plan["absorb_max_s"] == 30
    with pytest.raises(SystemExit) as caught:
        ql.build_plan(_gguf_args(["--absorb-max-s", "0"]))
    assert caught.value.code == 2
    plan = ql.build_plan(_gguf_args())
    useful, _pair, limit = pc.absorb_window(
        plan["ctx"], plan["far_end"], 1.0, 2.0, plan["absorb_max_s"], True, False
    )
    assert limit is None
    assert useful == plan["ctx"]


def test_i21_window_refuses_and_fit_only_plans(kit_env, monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "20000")
    with pytest.raises(ql.QualifyError, match="usable window"):
        ql.build_plan(_gguf_args())
    plan = ql.build_plan(_gguf_args(["--fit-only"]))
    assert plan["fit_only"] is True
    assert "suite" not in plan["phases"]
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    code = ql.cmd_plan(["--dry-run", *_gguf_args()])
    assert code == 0


def _min_row(engine):
    backend = {"llama.cpp": "vulkan", "vllm": "vllm", "sglang": "sglang", "strata": "strata"}[engine]
    row = {
        "model": "sample.gguf",
        "source": "card",
        "family": "fam",
        "architecture": "arch",
        "quant": "Q4_K_M",
        "kv": "f16",
        "flash_attention": "on",
        "reasoning": "off",
        "extra": "",
        "capability_source": "card",
        "use_for": "tests",
        "depth_probe_100k": "pass (3/3)",
        "category": "dense",
        "weight_class": "9b",
        "measured_on": "test card at 16384 served",
        "card": "a770",
        "backend": backend,
        "mode": "inference",
        "ctx": 16384,
        "useful_ctx": 16384,
        "timeout_s": 60,
        "vram_gib_after_load": 8.0,
        "ram_gb_extra": 1.0,
        "params_b": {"total": 9, "active": 9},
        "speed": {
            "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
            "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
        },
        "capability": {"published": 1},
        "task": "code-edit",
        "evidence": "suite pass at 16384",
        "engine": engine,
        "instrument": "SUITE-1@1",
    }
    if engine == "strata":
        row.pop("flash_attention")
    if engine == "sglang":
        row["tool_parser"] = "qwen"
        row["reasoning_parser"] = "qwen"
        row["kv"] = "fp8_e4m3"
    if engine == "vllm":
        row["tool_parser"] = "hermes"
    return row


def _wrap(row, name):
    return {"schema": 1, "card": "a770", "mode": "inference", "profiles": {name: row}}


def _write_profiles(tmp_path, engine, name="sample-row"):
    path = tmp_path / f"{engine}.json"
    path.write_text(json.dumps(_wrap(_min_row(engine), name)))
    return path


def test_i11_profiles_file_check_before_any_plan(kit_env, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ql.QualifyError, match="failed check"):
        ql.build_plan(
            ["--engine", "sglang", "--model", "sample-row", "--profiles-file", str(bad)]
        )
    assert not (tmp_path / "qualify").exists()
    good = _write_profiles(tmp_path, "strata", "sample-row")
    plan = ql.build_plan(
        [
            "--engine",
            "strata",
            "--model",
            "sample-row",
            "--profiles-file",
            str(good),
            "--tokenizer",
            "/tmp/tokenizer-dir",
        ]
    )
    assert plan["engine"] == "strata"
    assert plan["profiling"]["flags"] == []
    with pytest.raises(ql.QualifyError, match="no kit path"):
        ql.build_plan(["--engine", "vllm", "--model", "missing-row", "--tokenizer", "/tmp/tokenizer-dir"])


def _job(tmp_path, plan):
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    (job / "evidence").mkdir()
    ql.init_job(str(job), plan, "setsid")
    return job


def _stats():
    return {row["field"]: "recorded" for row in ql.REQUIRED_STATISTICS if row["who"] == "ladder"}


def test_i15_i16_and_verdict(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    receipt = pc.receipt("llama.cpp", {pc.VERBOSITY_SWITCH: False, "--metrics": True})
    # The receipt is written by the contract helper so the flag text stays there.
    pc.write_receipt(str(job / "evidence" / "profiling-start.json"), receipt)
    collected = {
        "requests": {"prefill_tps": 10, "decode_tps": 11, "length_stops": 2},
        "delivered": {"prefill_tps": 9, "decode_tps": 8, "source": "suite"},
        "statistics": _stats(),
        "load_ok": True,
        "smoke": {"tool": True},
        "window_resets": 0,
        "depth_pass": True,
        "suite": {"completed": True, "stages": [], "references": {"passed": 1, "attempts": 1}},
        "hooks": {"pre_started": False},
        "card_free": {"containers": 0},
        "ttft_8k": 1.0,
        "ttft_far": 2.0,
        "profiling_ok": True,
        "speed": [
            {
                "depth_tokens": 8192,
                "engine_prefill": 100,
                "engine_decode": 10,
                "client_prefill": 90,
                "client_decode": 9,
                "engine_source": "server log",
            }
        ],
    }
    (job / "evidence" / "collected.json").write_text(json.dumps(collected))
    result = ql.summarise(str(job))
    assert result["verdict"] == "needs-review"
    for key in ql.NULL_DOTTED:
        assert result[key] is None
    names = {item["name"] for item in result["checks"]}
    assert "usable-window-holds-kit" in names
    assert "no-compaction-storm" in names
    switches = next(item for item in result["checks"] if item["name"] == "switches-confirmed")
    assert switches["result"] == "fail"
    receipt["confirmed"][pc.VERBOSITY_SWITCH] = True
    pc.write_receipt(str(job / "evidence" / "profiling-start.json"), receipt)
    result = ql.summarise(str(job))
    switches = next(item for item in result["checks"] if item["name"] == "switches-confirmed")
    assert switches["result"] == "pass"
    assert result["requests.prefill_tps"] == 10
    os.remove(job / "evidence" / "profiling-start.json")
    result = ql.summarise(str(job))
    switches = next(item for item in result["checks"] if item["name"] == "switches-confirmed")
    assert switches["result"] == "unknown"
    report = (job / "report.md").read_text()
    assert "needs-review" in report
    assert "at 8192 tokens" in report
    for item in result["checks"]:
        if item["result"] in ("pass", "fail"):
            assert item.get("evidence")
        assert item["result"] in ("pass", "fail", "unknown")


def test_i10_fields_stay_separate(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(
        json.dumps(
            {
                "statistics": _stats(),
                "speed": [
                    {
                        "depth_tokens": 8192,
                        "engine_prefill": 1,
                        "engine_decode": 2,
                        "client_prefill": 3,
                        "client_decode": 4,
                    }
                ],
            }
        )
    )
    result = ql.summarise(str(job))
    row = result["speed"][0]
    assert set(row) >= {"engine_prefill", "engine_decode", "client_prefill", "client_decode"}
    assert "engine_and_client" not in row
    assert row["engine_prefill"]["value"] == 1
    assert row["client_decode"]["value"] == 4


def test_i3_status_atomic(tmp_path):
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    ql.status_merge(str(job), {"state": "running", "phase": "serve", "pid": 1, "pid_starttime": "1", "trips": []})
    procs = []
    for index in range(12):
        patch = json.dumps({"trip": {"time": "t", "rule": "note", "line": f"line-{index}"}})
        procs.append(
            subprocess.Popen(
                [sys.executable, os.path.join(PROJECT, "harness", "qualify_lib.py"), "status-merge", "--job", str(job)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        procs[-1].stdin.write(patch.encode())
        procs[-1].stdin.close()
    for proc in procs:
        proc.wait(timeout=20)
        assert proc.returncode == 0
    status = json.loads((job / "status.json").read_text())
    lines = [trip["line"] for trip in status["trips"]]
    assert lines.count("line-0") == 1
    assert len(lines) == 12
    raw = (job / "status.json").read_text()
    json.loads(raw)


def test_i18_export_two_files(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    stats = _stats()
    (job / "evidence" / "collected.json").write_text(json.dumps({"statistics": stats, "profiling_ok": True}))
    ql.summarise(str(job))
    out = tmp_path / "outside" / "row.json"
    out.parent.mkdir()
    before = set(os.listdir(PROJECT))
    reg = os.path.join(PROJECT, "config", "registry")
    hashes = _hashes(reg)
    ql.export_job(str(job), str(out), False)
    assert out.is_file()
    todo = tmp_path / "outside" / "row.json.todo.json"
    assert todo.is_file()
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert _hashes(reg) == hashes
    assert not (job / "scratch" / "registry-root").exists()
    result = json.loads((job / "result.json").read_text())
    del result["statistics"]["requests"]
    (job / "result.json").write_text(json.dumps(result))
    with pytest.raises(ql.QualifyError, match="requests") as caught:
        ql.export_job(str(job), str(tmp_path / "outside" / "other.json"), False)
    assert caught.value.code == 4
    ql.export_job(str(job), str(tmp_path / "outside" / "partial.json"), True)
    todo_text = (tmp_path / "outside" / "partial.json.todo.json").read_text()
    assert "allow_incomplete" in todo_text
    assert "allow_incomplete" not in (tmp_path / "outside" / "partial.json").read_text()
    link = tmp_path / "link.json"
    link.symlink_to(out)
    with pytest.raises(ql.QualifyError, match="symlink"):
        ql.export_job(str(job), str(link), True)
    inside = os.path.join(PROJECT, "row.json")
    with pytest.raises(ql.QualifyError, match="outside"):
        ql.export_job(str(job), inside, True)
    assert set(os.listdir(PROJECT)) == before


def test_i2_lost_and_finished_readonly(tmp_path):
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    ql.status_merge(
        str(job),
        {"state": "running", "phase": "serve", "pid": 1, "pid_starttime": "not-live", "trips": []},
    )
    shown = ql.status_show(str(job))
    assert shown["state"] == "lost"
    again = (job / "status.json").read_text()
    ql.status_show(str(job))
    assert (job / "status.json").read_text() == again
    done = tmp_path / "done"
    done.mkdir(mode=0o700)
    ql.status_merge(str(done), {"state": "finished", "phase": "summarise", "pid": 1, "pid_starttime": "1", "trips": []})
    (done / "DONE").write_text("ok")
    before = (done / "status.json").read_bytes()
    shown = ql.status_show(str(done))
    assert shown["state"] == "finished"
    assert (done / "status.json").read_bytes() == before


def test_job_id_refuses_dotdot():
    with pytest.raises(ql.QualifyError):
        ql.job_dir("/tmp/data", "../escape")
    with pytest.raises(ql.QualifyError):
        ql.job_dir("/tmp/data", "a/b")


def _hashes(directory):
    found = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            found[name] = open(path, "rb").read()
    return found


def test_api_key_in_extra_refused(kit_env):
    with pytest.raises(ql.QualifyError, match="api-key"):
        ql.build_plan(_gguf_args(["--extra", "--api-key secret"]))


def test_fit_report_first_line(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args(["--fit-only"]))
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(json.dumps({"statistics": _stats()}))
    ql.summarise(str(job))
    first = (job / "report.md").read_text().splitlines()[0]
    assert first.startswith("FIT RESULT")
    assert "needs-review" in (job / "report.md").read_text()
