"""Fix-round 3 regressions. No server, container, or card job is started."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[0]
sys.path.insert(0, os.path.join(HERE, "..", "harness"))
sys.path.insert(0, HERE)

import qualify_lib as ql
from test_qualify_lib import _gguf_args, _job, _min_row, _wrap
from test_review_v059_fixes import RUN_SUITE_SH, _base_env, _make_exec

# A shell command named eval. The words "eval time" and "evaluation" are not one.
_EVAL_COMMAND = re.compile(r"""(^|[^A-Za-z0-9_])eval(?:\s*\(|\s+["'$`(])""")

PROMPT_EVAL = (
    "prompt eval time =     15880.00 ms /  8192 tokens "
    "(    9.92 ms per token,   100.80 tokens per second)"
)
EVAL_TIME = (
    "eval time =      3200.00 ms /   128 tokens "
    "(   25.00 ms per token,    40.00 tokens per second)"
)


def test_qualify_sources_have_no_eval_command():
    files = sorted(Path(ROOT, "harness").glob("qualify*.sh")) + sorted(
        Path(ROOT, "harness").glob("qualify*.py")
    )
    assert len(files) >= 4
    hits = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _EVAL_COMMAND.search(line):
                hits.append(f"{path.name}:{number}:{line.strip()}")
    assert hits == []


def _plan_env(monkeypatch, tmp_path):
    monkeypatch.delenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", raising=False)
    monkeypatch.delenv("A770B_TOKENIZER_DIR", raising=False)
    monkeypatch.setenv("A770B_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(ql, "_token_counts", lambda _path, texts: [100] * len(texts))


def _profile(tmp_path, engine, name, row):
    path = tmp_path / f"{engine}.json"
    path.write_text(json.dumps(_wrap(row, name)), encoding="utf-8")
    return path


def test_build_plan_resolves_a_profile_tokenizer_without_a_flag(tmp_path, monkeypatch):
    _plan_env(monkeypatch, tmp_path)
    models = tmp_path / "models"
    model = models / "e2e-model"
    model.mkdir(parents=True)
    (model / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    for engine in ("vllm", "sglang"):
        row = _min_row(engine)
        row["model"] = "e2e-model"
        row.pop("requests", None)
        path = _profile(tmp_path, engine, "e2e-row", row)
        plan = ql.build_plan(
            [
                "--engine", engine,
                "--model", "e2e-row",
                "--profiles-file", str(path),
                "--stages", "s0-design",
            ]
        )
        assert plan["tokenizer"] == str(model)
        assert plan["window"]["kit_tokens"] == 100
        assert plan["window"]["action"] == "ok"

    tok = tmp_path / "strata-tok"
    tok.mkdir()
    (tok / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    cfg = models / "e2e.strata.json"
    cfg.write_text(json.dumps({"tokenizer": str(tok)}) + "\n", encoding="utf-8")
    row = _min_row("strata")
    row["model"] = "e2e.strata.json"
    path = _profile(tmp_path, "strata", "e2e-row", row)
    plan = ql.build_plan(
        [
            "--engine", "strata",
            "--model", "e2e-row",
            "--profiles-file", str(path),
            "--stages", "s0-design",
        ]
    )
    assert plan["tokenizer"] == str(tok)
    assert plan["window"]["kit_tokens"] == 100


def test_statistics_follow_an_exportable_suite():
    plan = {"engine": "llama.cpp", "extra": []}
    commentary = {
        "id": "s0-design",
        "counts_toward_pass": False,
        "working": True,
        "label": "note",
        "wall_s": 3,
    }
    stats = ql._statistics_from({"suite": {"stages": [commentary]}}, plan, {}, None)
    assert stats["suite"] == "unknown"
    assert stats["fit.code"] == "unknown"
    assert stats["evidence"] == "unknown"
    counted = {
        "id": "s2-backend",
        "working": True,
        "label": "lab",
        "wall_s": 2,
        "counts_toward_pass": True,
    }
    collected = {"suite": {"stages": [counted]}}
    stats = ql._statistics_from(collected, plan, {}, None)
    assert stats["suite"] == "recorded"
    assert stats["fit.code"] == "recorded"
    assert stats["evidence"] == "recorded"
    collected["statistics"] = {"suite": "kept", "fit.code": "kept", "evidence": "kept"}
    stats = ql._statistics_from(collected, plan, {}, None)
    assert stats["suite"] == "kept"
    assert stats["fit.code"] == "kept"
    assert stats["evidence"] == "kept"


def test_parse_serve_accepts_strata_and_an_already_up_container():
    strata = ql._parse_serve("✓ sample serving alias on 127.0.0.1:9 · Strata\n", 0)
    assert strata["load_ok"] is True
    reused = ql._parse_serve("✓ sample already up (same argv)\n", 0)
    assert reused["load_ok"] is True
    failed = ql._parse_serve("✓ sample serving alias on 127.0.0.1:9 · Strata\n", 1)
    assert failed["load_ok"] is False
    assert ql._parse_serve("server refused\n", 0)["load_ok"] is False


def test_log_rates_pair_decode_with_the_prompt_depth():
    prefill, decode = ql._log_rates(PROMPT_EVAL + "\n" + EVAL_TIME + "\n")
    assert prefill[8192] == pytest.approx(100.80)
    assert decode[8192] == pytest.approx(40.00)
    assert 128 not in decode
    assert ql._rate_at(decode, 8191) == pytest.approx(40.00)
    assert ql._rate_at(decode, 8193) == pytest.approx(40.00)
    assert ql._rate_at(prefill, 8192) == pytest.approx(100.80)


def _mapped(job: Path, engine: str, depths: dict) -> None:
    (job / "evidence").mkdir(parents=True)
    (job / "plan.json").write_text(json.dumps({"engine": engine}), encoding="utf-8")
    (job / "evidence" / "speed-bench.mapped.json").write_text(
        json.dumps({
            "tool": "llama-benchy",
            "prompt": 8192,
            "gen": 128,
            "at_depth": depths,
        }),
        encoding="utf-8",
    )


def test_multi_depth_speed_names_why_there_is_no_per_depth_rate(tmp_path):
    job = tmp_path / "job"
    _mapped(job, "vllm", {"8192": {"pp": 1.0, "tg": 2.0}, "32768": {"pp": 3.0, "tg": 4.0}})
    body = ql._parse_speed(str(job), "")
    assert len(body["rows"]) == 2
    assert all(
        row["engine_reason"] == "one /metrics bracket covers more than one depth"
        for row in body["rows"]
    )
    lone = tmp_path / "lone"
    _mapped(lone, "llama.cpp", {"8192": {"pp": 1.0, "tg": 2.0}})
    one = ql._parse_speed(str(lone), "")
    assert one["rows"][0]["engine_reason"] == "no per-depth rate source"


def test_merge_suite_keeps_the_first_pass_rates_and_sums_stops():
    previous = {
        "stages": [{"id": "s2-backend"}],
        "completed": True,
        "requests": {
            "length_stops": 2,
            "prefill_tps": 120.0,
            "decode_tps": 18.0,
            "max_prompt_tokens": 10,
            "max_completion_tokens": 5,
        },
        "delivered": {"prefill_tps": 120.0, "decode_tps": 18.0},
    }
    body = {
        "stages": [{"id": "ref-a"}],
        "completed": True,
        "requests": {
            "length_stops": 0,
            "prefill_tps": None,
            "decode_tps": None,
            "max_prompt_tokens": 12,
            "max_completion_tokens": 3,
        },
    }
    merged = ql._merge_suite(previous, body)
    assert merged["requests"]["length_stops"] == 2
    assert merged["requests"]["prefill_tps"] == 120.0
    assert merged["requests"]["decode_tps"] == 18.0
    assert merged["requests"]["max_prompt_tokens"] == 12
    assert merged["delivered"]["decode_tps"] == 18.0


def test_suite_why_uses_the_pass_log_and_depth_not_measured_is_a_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    monkeypatch.delenv("A770B_TOKENIZER_DIR", raising=False)
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    ql.apply_phase(str(job), "suite", "suite produced nothing\n", 1, "phases/suite-pass-1.log")
    suite = json.loads((job / "evidence" / "suite.json").read_text(encoding="utf-8"))
    assert suite["why"]["log"] == "phases/suite-pass-1.log"
    text = "depth probe at 15360: not measured — the probe timed out\n"
    parsed = ql._parse_depth(text)
    assert parsed["unanswered"] is True
    assert ql._phase_measured("depth", parsed) is False
    ql.apply_phase(str(job), "depth", text, 0)
    depth = json.loads((job / "evidence" / "depth.json").read_text(encoding="utf-8"))
    assert depth["why"]["reason"] == "the probe timed out"
    ql.assemble(str(job))
    result = ql.summarise(str(job))
    check = next(item for item in result["checks"] if item["name"] == "depth-probe")
    assert "the probe timed out" in check["reason"]
    assert any(
        item.get("phase") == "depth" and item.get("reason") == "the probe timed out"
        for item in result["unmeasured"]
    )


def test_record_unmeasured_command_is_not_run(tmp_path, monkeypatch):
    source = (ROOT / "harness" / "qualify.sh").read_text(encoding="utf-8")
    assert '"command": "not run"' in source
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    payload = subprocess.check_output(
        [
            sys.executable, "-c",
            'import json,sys; print(json.dumps({"skipped": True, "why": {"command": "not run", "rc": 0, "log": "phases/" + sys.argv[1] + ".log", "reason": sys.argv[2]}}))',
            "window",
            "no tokenizer, the sweep was not measured",
        ],
        text=True,
    )
    ql.apply_phase(str(job), "window", payload, 0)
    note = json.loads((job / "evidence" / "window.json").read_text(encoding="utf-8"))
    assert note["why"]["command"] == "not run"
    assert note["why"]["reason"] == "no tokenizer, the sweep was not measured"
    assert "command no tokenizer" not in note["why"]["reason"]


def test_summarise_names_the_abort_on_checks_that_never_ran(tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    plan = ql.build_plan(_gguf_args())
    job = _job(tmp_path, plan)
    ql.status_merge(str(job), {"state": "failed", "reason": "preflight"})
    result = ql.summarise(str(job))
    smoke = next(item for item in result["checks"] if item["name"] == "smoke-tool-call")
    assert "skipped because preflight failed" in smoke["reason"]
    assert "not run" in smoke["reason"]
    speed = next(item for item in result["unmeasured"] if item.get("phase") == "speed")
    assert speed["reason"] == "skipped because preflight failed"
    assert speed["command"] == "not run"


def test_ram_ignores_a_stale_pointer_unless_this_job_loaded(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"host_buffers_mib": 1024}), encoding="utf-8")
    (data / "logs" / "serve-evidence.path").write_text(str(other) + "\n", encoding="utf-8")
    job = tmp_path / "job"
    (job / "evidence").mkdir(parents=True)
    monkeypatch.setenv("A770B_DATA", str(data))
    assert ql._ram_from_evidence(str(job), False) is None
    assert ql._ram_from_evidence(str(job), True) == pytest.approx(1.0)


def test_compaction_count_ignores_new_files(tmp_path, monkeypatch):
    data = tmp_path / "data"
    results = data / "results"
    results.mkdir(parents=True)
    monkeypatch.setenv("A770B_DATA", str(data))
    (results / "lab.task.md").write_text(
        "## new files\n"
        "this compaction is in a new source file\n"
        "## opencode transcript tail\n"
        "the model compacted the context\n"
        "## server-side timings during the run\n"
        "compaction after the transcript is not a count\n",
        encoding="utf-8",
    )
    assert ql._compaction_count({"label": "lab"}) == 1
    (results / "bare.task.md").write_text(
        "## new files\ncompaction only in a new file\n",
        encoding="utf-8",
    )
    assert ql._compaction_count({"label": "bare"}) == 0


def test_run_suite_empty_capture_prints_no_blank_line(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "seat").mkdir()
    (tmp_path / "seat" / "README.md").write_text("fixture\n", encoding="utf-8")
    profile = tmp_path / "a770.display.json"
    profile.write_text(
        (ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    brief = tmp_path / "brief.md"
    spec = tmp_path / "spec.json"
    brief.write_text("# Task\n", encoding="utf-8")
    spec.write_text("{}\n", encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({
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
    fake = tmp_path / "fake-lb.sh"
    _make_exec(fake, 'if [ "$1" = "run" ]; then exit 1; fi; exit 0')
    run = subprocess.run(
        ["bash", str(RUN_SUITE_SH), "qwen35-9b-q4km-vulkan", str(tmp_path / "suite-seat"),
         "--suite", str(suite), "--stages", "s1-frontend"],
        capture_output=True,
        text=True,
        env=dict(env, A770B_PROFILES_FILE=str(profile), A770B_LOCAL_BUILD=str(fake)),
        cwd=ROOT,
    )
    assert run.returncode == 4, run.stdout + run.stderr
    assert not run.stderr.startswith("\n")
    assert "\n\n⚠ stage" not in run.stderr
    assert "no capture" in run.stderr
