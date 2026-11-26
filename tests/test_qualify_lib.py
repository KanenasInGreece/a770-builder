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
import time

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
    plan = ql.build_plan(
        [
            "--engine",
            "sglang",
            "--model",
            "sample-row",
            "--profiles-file",
            str(_write_profiles(tmp_path, "sglang")),
            "--dry-run",
            "--tokenizer",
            "/tmp/tokenizer-dir",
        ]
    )
    assert plan["profiling"]["flags"] == []
    assert pc.flags("llama.cpp")[0] not in plan["serve_argv"]
    with pytest.raises(ql.QualifyError, match="not yet supported by qualify"):
        ql.build_plan(
            [
                "--engine",
                "strata",
                "--model",
                "sample-row",
                "--profiles-file",
                str(_write_profiles(tmp_path, "strata")),
                "--dry-run",
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )


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
    good = _write_profiles(tmp_path, "sglang", "sample-row")
    plan = ql.build_plan(
        [
            "--engine",
            "sglang",
            "--model",
            "sample-row",
            "--profiles-file",
            str(good),
            "--tokenizer",
            "/tmp/tokenizer-dir",
        ]
    )
    assert plan["engine"] == "sglang"
    assert plan["profiling"]["flags"] == []
    strata = _write_profiles(tmp_path, "strata", "sample-row")
    with pytest.raises(ql.QualifyError, match="not yet supported by qualify"):
        ql.build_plan(
            [
                "--engine",
                "strata",
                "--model",
                "sample-row",
                "--profiles-file",
                str(strata),
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
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


def _measured_collected(stats):
    """Evidence a test recorded. The VRAM and the stage wall are copied from fixtures.

    ram_gb_extra is not in the arm1 capture. 6.25 is a value this test wrote into
    the evidence so the exporter has to copy it. It is not a measurement.
    """
    stage = json.loads(
        open(os.path.join(HERE, "fixtures", "qualify", "suite-result.json"), encoding="utf-8").read()
    )["stages"][0]
    return {
        "statistics": stats,
        "profiling_ok": True,
        "ttft_8k": 8.1,
        "ttft_far": 112.2,
        "depth_pass": True,
        "depth_score": 2,
        "vram_after_load": 21.77301025390625,
        "ram_gb_extra": 6.25,
        "suite": {"completed": True, "stages": [stage], "references": {}},
    }


def test_i18_export_two_files(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = ql.build_plan(_gguf_args(["--ctx", "131072"]))
    job = _job(tmp_path, plan)
    stats = _stats()
    (job / "evidence" / "collected.json").write_text(json.dumps(_measured_collected(stats)))
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
    written = json.loads(out.read_text())
    import profiles

    assert written["vram_gib_after_load"] == 21.77301025390625
    assert written["ram_gb_extra"] == 6.25
    assert written["timeout_s"] == 2111
    assert written["useful_ctx"] == plan["ctx"]
    assert written["instrument"] == profiles.kit_instrument()
    assert written["model"] == "sample.gguf"
    assert written["depth_probe_100k"] == "pass (2/3)"
    assert written["category"].startswith("__TODO__")
    assert written["vram_gib_after_load"] != 1.0
    assert written["timeout_s"] != 60
    assert "1.0" not in out.read_text().split("vram_gib_after_load", 1)[1].splitlines()[0]
    broken = json.loads((job / "result.json").read_text())
    broken["useful_ctx"] = None
    broken["vram_after_load"] = {"value": "unknown", "unit": "GiB"}
    broken["ram_gb_extra"] = "unknown"
    (job / "result.json").write_text(json.dumps(broken))
    with pytest.raises(ql.QualifyError) as structural:
        ql.export_job(str(job), str(tmp_path / "outside" / "broken.json"), False)
    assert structural.value.code == 4
    ql.export_job(str(job), str(tmp_path / "outside" / "filled.json"), True)
    filled = json.loads((tmp_path / "outside" / "filled.json").read_text())
    assert filled.get("vram_gib_after_load") != 1.0
    assert "vram_gib_after_load" not in filled or filled["vram_gib_after_load"] != 1.0
    (job / "evidence" / "collected.json").write_text(json.dumps(_measured_collected(stats)))
    ql.summarise(str(job))
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


def test_i20_docs_name_qualify_as_default():
    """README and AGENTS name qualify as the profiling step. The ladder is for one rung."""
    for name in ("README.md", "AGENTS.md"):
        text = open(os.path.join(PROJECT, name), encoding="utf-8").read()
        for needle in ("local-build.sh qualify start", "qualify result", "qualify export"):
            assert needle in text, (name, needle)
        for line in text.splitlines():
            if "ladder.sh" in line and re.search(r"default", line, re.I):
                assert re.search(r"debug|not the default", line, re.I), line


_FIGURE = re.compile(r"\d+(?:\.\d+)?\s*(?:tok/s|t/s|GiB|MiB)\b", re.I)
# Bare "at" matches ordinary English. A figure has to name a depth, a window, or a token count.
_DEPTH_CUE = re.compile(r"\b(?:served|token|tokens|context|depth|prompt|window)\b", re.I)


def test_speed_and_memory_figures_name_their_depth():
    """A speed or memory figure names the depth in the same line."""
    report = ql.render_report(
        os.path.join("job", "sample"),
        {
            "engine": "llama.cpp",
            "model": "sample.gguf",
            "ctx": 16384,
            "window": {"usable": 8192, "served_ctx": 16384, "output_limit": 8192},
            "useful_ctx": 16384,
            "useful_ctx_rule": pc.RULE_NAME,
            "speed": [
                {
                    "engine_prefill": {"value": 10, "unit": "tok/s", "depth_tokens": 8192},
                    "engine_decode": {"value": 11, "unit": "tok/s", "depth_tokens": 8192},
                    "client_prefill": {
                        "value": "unknown",
                        "unit": "tok/s",
                        "depth_tokens": 8192,
                        "reason": "not measured",
                    },
                    "client_decode": {
                        "value": "unknown",
                        "unit": "tok/s",
                        "depth_tokens": 8192,
                        "reason": "not measured",
                    },
                }
            ],
            "vram_after_load": {"value": 8.0, "unit": "GiB", "depth_tokens": 16384},
            "checks": [],
        },
    )
    blobs = [("report.md", report)]
    for name in ("README.md", "AGENTS.md"):
        blobs.append((name, open(os.path.join(PROJECT, name), encoding="utf-8").read()))
    for label, text in blobs:
        for line in text.splitlines():
            if _FIGURE.search(line):
                assert _DEPTH_CUE.search(line), (label, line)


def test_i13_diff_has_no_private_markers():
    """New tracked text keeps private names and record ids out. Patterns are assembled here."""
    diff = subprocess.check_output(["git", "diff", "main"], cwd=PROJECT, text=True, errors="replace")
    folded = diff.lower()
    patterns = [
        "xeno" + "fon",
        "cla" + "wee",
        "cl" + "oe",
        "/ho" + "me/",
        "fa" + "ct:",
        "decis" + "ion:",
        "re" + "tro:",
    ]
    found = [pat for pat in patterns if pat in folded or pat in diff]
    assert found == []


def test_i22_window_and_compaction_in_result(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(json.dumps({"statistics": _stats()}))
    ql.summarise(str(job))
    result = json.loads((job / "result.json").read_text())
    names = {item["name"]: item for item in result["checks"]}
    assert "usable-window-holds-kit" in names
    assert "no-compaction-storm" in names
    assert names["no-compaction-storm"]["result"] == "unknown"
    assert result["window"]["usable"] is not None
    assert result["window"]["served_ctx"] is not None


def _check_named(result, name):
    return next(item for item in result["checks"] if item["name"] == name)


def test_i22_compaction_pass_fail_and_storm_wins(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)

    def run(stages):
        (job / "evidence" / "collected.json").write_text(
            json.dumps({"statistics": _stats(), "suite": {"completed": True, "stages": stages}})
        )
        return _check_named(ql.summarise(str(job)), "no-compaction-storm")["result"]

    assert run([{"id": "s1", "compaction_lines": 1, "requests": 8}]) == "pass"
    assert run([{"id": "s1", "compaction_lines": 10, "requests": 4}]) == "fail"
    # A later stage with no count must not hide a storm already seen.
    assert (
        run(
            [
                {"id": "s1", "compaction_lines": 10, "requests": 4},
                {"id": "s2", "compaction_lines": None, "requests": 3},
            ]
        )
        == "fail"
    )
    assert run([{"id": "s1", "compaction_lines": "nope", "requests": 4}]) == "unknown"


def test_i21_unset_kit_budget_refuses_full_start(monkeypatch):
    monkeypatch.delenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", raising=False)
    monkeypatch.delenv("A770B_OUTPUT_TOKENS", raising=False)
    with pytest.raises(ql.QualifyError, match="kit prompt budget is unknown"):
        ql.build_plan(_gguf_args())
    plan = ql.build_plan(_gguf_args(["--no-suite"]))
    assert plan["no_suite"] is True
    assert plan["window"]["action"] == "unknown"
    assert ql.build_plan(_gguf_args(["--fit-only"]))["fit_only"] is True
    assert ql.cmd_plan(["--dry-run", *_gguf_args()]) == 0


def test_kit_budget_uses_the_selected_row_not_a_larger_stage(kit_env, tmp_path, monkeypatch):
    monkeypatch.delenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", raising=False)
    row = _min_row("vllm")
    row["measured_tasks"] = {
        "s1-frontend": {"quality": "pass", "wall_s": 10, "context_tokens": 850607, "thinking": "off"},
        "s0-design": {"quality": "pass", "wall_s": 10, "context_tokens": 850607, "thinking": "off"},
    }
    row["requests"] = {
        "max_prompt_tokens": 400,
        "max_completion_tokens": 32,
        "length_stops": 0,
        "prefill_tps": None,
        "decode_tps": None,
        "source": "suite",
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(_wrap(row, "sample-vllm")))
    plan = ql.build_plan(
        [
            "--engine",
            "vllm",
            "--model",
            "sample-vllm",
            "--profiles-file",
            str(path),
            "--tokenizer",
            "/tmp/tokenizer-dir",
            "--stages",
            "s1-frontend",
        ]
    )
    assert plan["window"]["kit_tokens"] == 400
    assert plan["window"]["kit_source"] == "requests.max_prompt_tokens"
    bare = _min_row("vllm")
    bare["measured_tasks"] = {
        "s1-frontend": {"quality": "pass", "wall_s": 10, "context_tokens": 850607, "thinking": "off"},
    }
    bare_path = tmp_path / "cumulative.json"
    bare_path.write_text(json.dumps(_wrap(bare, "sample-vllm")))
    with pytest.raises(ql.QualifyError, match="kit prompt budget is unknown"):
        ql.build_plan(
            [
                "--engine",
                "vllm",
                "--model",
                "sample-vllm",
                "--profiles-file",
                str(bare_path),
                "--tokenizer",
                "/tmp/tokenizer-dir",
                "--stages",
                "s1-frontend",
            ]
        )


def test_engine_thinking_kv_and_backend_errors(kit_env, tmp_path):
    path = _write_profiles(tmp_path, "vllm", "sample-vllm")
    with pytest.raises(ql.QualifyError, match="not the profile engine"):
        ql.build_plan(
            [
                "--engine",
                "sglang",
                "--model",
                "sample-vllm",
                "--profiles-file",
                str(path),
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
    row = _min_row("llama.cpp")
    row["reasoning"] = "on"
    row["thinking"] = {"mode": "on", "budget": 2048}
    row["backend"] = "vulkan"
    thought = tmp_path / "think.json"
    thought.write_text(json.dumps(_wrap(row, "sample-row")))
    plan = ql.build_plan(
        [
            "--engine",
            "llama.cpp",
            "--model",
            "sample-row",
            "--profiles-file",
            str(thought),
            "--tokenizer",
            "/tmp/tokenizer-dir",
        ]
    )
    assert plan["gen"] == 2368
    assert plan["reasoning"] == "on"
    with pytest.raises(ql.QualifyError, match="--kv-v"):
        ql.build_plan(
            [
                "--engine",
                "llama.cpp",
                "--model",
                "sample-row",
                "--profiles-file",
                str(thought),
                "--tokenizer",
                "/tmp/tokenizer-dir",
                "--kv-v",
                "q4_0",
            ]
        )
    broken = _min_row("llama.cpp")
    broken["extra"] = 'unclosed "'
    bad = tmp_path / "bad-extra.json"
    bad.write_text(json.dumps(_wrap(broken, "sample-row")))
    with pytest.raises(ql.QualifyError, match="could not parse"):
        ql.build_plan(
            [
                "--engine",
                "llama.cpp",
                "--model",
                "sample-row",
                "--profiles-file",
                str(bad),
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
    with pytest.raises(ql.QualifyError, match="invalid choice") as caught:
        ql.build_plan(_gguf_args(["--backend", "metal"]))
    assert "needs --engine and --model" not in str(caught.value)


def test_i4_secret_names_and_folded_api_key(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_API_KEY_RAW", "secret-raw")
    monkeypatch.setenv("A770B_HF_TOKEN_READ", "secret-token")
    monkeypatch.setenv("A770B_TOKENIZER", "/tmp/tokenizer-dir")
    monkeypatch.setenv("A770B_API_KEY_FILE", "/tmp/key")
    names = [name for name, _value in ql.iter_forward_env()]
    assert "A770B_API_KEY_RAW" not in names
    assert "A770B_HF_TOKEN_READ" not in names
    assert "A770B_TOKENIZER" in names
    assert "A770B_API_KEY_FILE" in names
    with pytest.raises(ql.QualifyError, match="api-key"):
        ql.build_plan(_gguf_args(["--extra=--api_key=secret"]))
    row = _min_row("vllm")
    row["extra"] = "--api_key=secret"
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(_wrap(row, "sample-vllm")))
    with pytest.raises(ql.QualifyError, match="api-key"):
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
    assert ql.scrub_argv(["serve", "--api_key", "secret", "--ctx", "1"]) == ["serve", "--ctx", "1"]


def test_i2_queued_without_a_pid_is_not_lost(tmp_path):
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ql.status_merge(
        str(job),
        {"state": "queued", "phase": "preflight", "pid": None, "pid_starttime": None, "started": stamp, "trips": []},
    )
    assert ql.status_show(str(job))["state"] == "queued"
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    ql.status_merge(str(job), {"started": old})
    assert ql.status_show(str(job))["state"] == "lost"


def test_i17_absorb_is_read_by_summarise(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args(["--absorb-max-s", "30", "--ctx", "131072"]))
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(
        json.dumps(
            {
                "statistics": _stats(),
                "ttft_8k": 8.1,
                "ttft_far": 112.2,
                "depth_pass": True,
            }
        )
    )
    result = ql.summarise(str(job))
    assert result["absorb_max_s"] == 30
    assert isinstance(result["useful_ctx"], int)
    assert result["useful_ctx"] <= plan["ctx"]
    assert result["useful_ctx"] < plan["ctx"]


def test_i15_export_nulls_unconfirmed_request_fields(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = ql.build_plan(_gguf_args(["--ctx", "131072"]))
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(json.dumps(_measured_collected(_stats())))
    result = ql.summarise(str(job))
    import profiling_contract as pc

    result["profiling"] = pc.receipt("llama.cpp", {"--metrics": False, pc.VERBOSITY_SWITCH: False})
    result["suite"]["requests"] = {
        "max_prompt_tokens": 15514,
        "max_completion_tokens": 7668,
        "length_stops": 2,
        "prefill_tps": 12.5,
        "decode_tps": 3.5,
        "source": "suite",
    }
    result["requests.prefill_tps"] = 12.5
    result["requests.decode_tps"] = 3.5
    result["requests.length_stops"] = 2
    result["speed.delivered"] = {"prefill_tps": 9.0, "decode_tps": 8.0, "source": "suite"}
    (job / "result.json").write_text(json.dumps(result))
    out = tmp_path / "nulls.json"
    ql.export_job(str(job), str(out), False)
    row = json.loads(out.read_text())
    assert row["requests"]["prefill_tps"] is None
    assert row["requests"]["decode_tps"] is None
    assert row["requests"]["length_stops"] is None
    assert row["speed"]["delivered"] is None
    assert row["requests"]["max_prompt_tokens"] == 15514


def test_export_collision_warning_and_card_name(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = ql.build_plan(_gguf_args(["--ctx", "131072"]))
    job = _job(tmp_path, plan)
    (job / "evidence" / "collected.json").write_text(json.dumps(_measured_collected(_stats())))
    ql.summarise(str(job))
    other = _min_row("llama.cpp")
    other["instrument"] = "seat:old"
    other["evidence"] = "seat pass at 16384"
    other["task"] = "code-read"
    registry = tmp_path / "existing.json"
    registry.write_text(json.dumps(_wrap(other, "already")))
    stored = json.loads((job / "plan.json").read_text())
    stored["profiles_file"] = str(registry)
    (job / "plan.json").write_text(json.dumps(stored))
    out = tmp_path / "collide.json"
    ql.export_job(str(job), str(out), False)
    todo = json.loads(open(str(out) + ".todo.json", encoding="utf-8").read())
    assert todo.get("warnings")
    report = (job / "report.md").read_text()
    assert "config/registry/a770.inference.json" in report


def test_render_report_uses_the_plan_card(tmp_path, monkeypatch):
    monkeypatch.delenv("A770B_CARD", raising=False)
    monkeypatch.delenv("A770B_CARD_MODE", raising=False)
    text = ql.render_report(os.path.join("job", "sample"), {"engine": "llama.cpp", "checks": []})
    assert "config/registry/<card>.inference.json" in text


def test_i8_opencode_home_when_path_is_not_v1(kit_env, tmp_path, monkeypatch):
    """OpenCode 1.19.6 under A770B_OPENCODE_HOME counts when PATH has none."""
    import shutil

    home = tmp_path / "opencode-home"
    home.mkdir()
    binary = home / "opencode"
    binary.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in --version) printf '%s\\n' '1.19.6'; exit 0 ;; esac\n"
        "case \"$1:$2\" in run:--help) printf '%s\\n' 'opencode run --dir DIR'; exit 0 ;; esac\n"
        "exit 1\n"
    )
    binary.chmod(0o755)
    uv = shutil.which("uv")
    assert uv
    path = os.pathsep.join([os.path.dirname(uv), "/usr/bin", "/bin"])
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("A770B_DEFER_DATA_DIRS", "1")
    monkeypatch.setenv("A770B_DATA", str(data))
    monkeypatch.setenv("A770B_PROJECT", PROJECT)
    monkeypatch.setenv("A770B_OPENCODE_BIN", "")
    monkeypatch.setenv("A770B_OPENCODE_HOME", str(home))
    monkeypatch.delenv("A770B_QUALIFY_OPENCODE_VERSION", raising=False)
    assert ql.opencode_version(PROJECT) == "1.19.6"
    assert not (data / "logs").exists()
    assert not (data / "results").exists()


def test_confirm_started_reads_engine_logs_and_metrics(kit_env, tmp_path, monkeypatch):
    """Real engine-log excerpts plus a local metrics body, not a card."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    monkeypatch.setenv("A770B_QUALIFY_OPENCODE_VERSION", "0-test")
    monkeypatch.setenv("A770B_DEFER_DATA_DIRS", "1")
    monkeypatch.setenv("A770B_DATA", str(tmp_path / "data"))
    fixture = os.path.join(HERE, "fixtures", "qualify")
    body = open(os.path.join(fixture, "metrics-llamacpp.prom"), "rb").read()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        plan = ql.build_plan(_gguf_args())
        job = _job(tmp_path, plan)
        log = open(os.path.join(fixture, "engine-llama.log"), encoding="utf-8").read()
        receipt = ql.confirm_server(str(job), log, host, port)
        assert receipt["confirmed"]["--metrics"] is True
        assert receipt["confirmed"][pc.VERBOSITY_SWITCH] is True
        vllm = _write_profiles(tmp_path, "vllm", "sample-vllm")
        vplan = ql.build_plan(
            [
                "--engine",
                "vllm",
                "--model",
                "sample-vllm",
                "--profiles-file",
                str(vllm),
                "--tokenizer",
                "/tmp/tokenizer-dir",
            ]
        )
        vjob = tmp_path / "vjob"
        vjob.mkdir(mode=0o700)
        (vjob / "evidence").mkdir()
        ql.init_job(str(vjob), vplan, "setsid")
        vlog = open(os.path.join(fixture, "engine-vllm.log"), encoding="utf-8").read()
        vreceipt = ql.confirm_server(str(vjob), vlog, "127.0.0.1", 9)
        assert vreceipt["confirmed"][pc.flags("vllm")[0]] is True
    finally:
        server.shutdown()
        server.server_close()


def test_window_reset_aged_out_is_not_a_new_reset(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    collected = {"window_resets": -1, "statistics": _stats(), "hooks": {"pre_started": False}}
    (job / "evidence" / "collected.json").write_text(json.dumps(collected))
    result = ql.summarise(str(job))
    check = next(item for item in result["checks"] if item["name"] == "window-no-resets")
    assert check["result"] == "pass"


def test_card_without_idle_baseline_names_that_gap(kit_env, tmp_path):
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    collected = {
        "statistics": _stats(),
        "hooks": {"pre_started": False},
        "card_free": {"containers": 0, "vram_gib": 0.4, "idle_gib": None},
    }
    (job / "evidence" / "collected.json").write_text(json.dumps(collected))
    result = ql.summarise(str(job))
    check = next(item for item in result["checks"] if item["name"] == "card-free-after")
    assert check["result"] == "unknown"
    assert check["reason"] == "no idle VRAM baseline"


def test_empty_patch_is_named_from_the_verify_evidence(kit_env, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "results").mkdir(parents=True)
    monkeypatch.setenv("A770B_DATA", str(data))
    label = "candidate-empty"
    (data / "results" / f"{label}.verify.json").write_text(
        json.dumps({"error": "patch is empty — that run changed nothing, there is nothing to verify"})
    )
    copied = ql._stage_copy({"id": "s0-design", "label": label, "outcome": "verify_error", "working": False})
    assert copied["outcome"] == "empty_patch"
    assert "changed nothing" in copied["empty_patch"]
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    collected = {
        "statistics": _stats(),
        "hooks": {"pre_started": False},
        "suite": {"completed": False, "stages": [copied]},
    }
    (job / "evidence" / "collected.json").write_text(json.dumps(collected))
    result = ql.summarise(str(job))
    assert result["empty_patches"][0]["stage"] == "s0-design"
    assert "verify.json" in result["empty_patches"][0]["evidence"] or "changed nothing" in result["empty_patches"][0]["evidence"]


def test_allow_incomplete_export_writes_nulls_and_no_illegal_speed_key(kit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_CARD", "a770")
    monkeypatch.setenv("A770B_CARD_MODE", "inference")
    plan = ql.build_plan(_gguf_args(["--fit-only"]))
    job = _job(tmp_path, plan)
    collected = {
        "load_ok": True,
        "vram_after_load": 4.0,
        "vram_cap": 15.3,
        "hooks": {"pre_started": False},
        "card_free": {"containers": 0, "vram_gib": 0.4, "idle_gib": 0.4},
    }
    (job / "evidence" / "collected.json").write_text(json.dumps(collected))
    ql.summarise(str(job))
    out = tmp_path / "row.json"
    ql.export_job(str(job), str(out), True)
    row = json.loads(out.read_text())
    assert "decode_tps / speed.prefill_tps" not in row.get("speed", {})
    assert row["vram_gib_after_load"] == 4.0
    written_todo = out.with_name(out.name + ".todo.json")
    assert written_todo.is_file()
    todo = json.loads(written_todo.read_text())
    assert todo["allow_incomplete"] is True
    assert todo["missing_statistics"]
    for key in ("useful_ctx", "ram_gb_extra"):
        if key in row:
            assert row[key] is None or isinstance(row[key], (int, float))
