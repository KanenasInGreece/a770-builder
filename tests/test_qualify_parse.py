"""Real harness text, parsed by the real apply_phase. No card and no server."""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "qualify")
sys.path.insert(0, os.path.join(PROJECT, "harness"))

import qualify_lib as ql


def _job(tmp_path, ctx=131072):
    plan = ql.build_plan(
        [
            "--engine",
            "llama.cpp",
            "--model",
            "sample.gguf",
            "--ctx",
            str(ctx),
            "--backend",
            "vulkan",
            "--tokenizer",
            "/tmp/tokenizer-dir",
            "--dry-run",
        ]
    )
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    (job / "evidence").mkdir()
    ql.init_job(str(job), plan, "setsid")
    return job


def test_window_depth_speed_and_suite_parse_fixture_text(tmp_path, monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    job = _job(tmp_path)
    window = open(os.path.join(FIXTURES, "window.log"), encoding="utf-8").read()
    ql.apply_phase(str(job), "window", window, 0)
    parsed = json.loads((job / "evidence" / "window.json").read_text())
    assert parsed["ttft_8k"] == 8.1
    assert parsed["ttft_far"] == 112.2
    assert parsed["resets"] == 0
    assert parsed["points"]["8000"]["vram_max_gib"] == 21.77301025390625

    depth = open(os.path.join(FIXTURES, "depth.log"), encoding="utf-8").read()
    ql.apply_phase(str(job), "depth", depth, 0)
    probed = json.loads((job / "evidence" / "depth.json").read_text())
    assert probed["score"] == 2
    assert probed["passed"] is True
    assert probed["depth_tokens"] == 100000

    unanswered = open(os.path.join(FIXTURES, "depth-unanswered.log"), encoding="utf-8").read()
    ql.apply_phase(str(job), "depth", unanswered, 0)
    missing = json.loads((job / "evidence" / "depth.json").read_text())
    assert missing["passed"] is None
    assert missing["unanswered"] is True

    mapped = job / "evidence" / "speed-mapped.json"
    shutil.copy(os.path.join(FIXTURES, "speed-mapped.json"), mapped)
    text = "mapped → " + str(mapped) + "\n"
    ql.apply_phase(str(job), "speed", text, 0)
    speed = json.loads((job / "evidence" / "speed.json").read_text())
    by_depth = {row["depth_tokens"]: row for row in speed["rows"]}
    assert by_depth[8192]["client_prefill"] == 1768.7024842723774
    assert by_depth[100000]["client_decode"] == 11.947831158598161
    assert speed["profile_speed"]["prefill_tps"]["32k"] == 1302.3227247986867

    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    ql.apply_phase(str(job), "speed", "mapped → " + str(outside) + "\n", 0)
    refused = json.loads((job / "evidence" / "speed.json").read_text())
    assert refused.get("rows", []) == []

    suite_path = job / "evidence" / "suite-result.json"
    shutil.copy(os.path.join(FIXTURES, "suite-result.json"), suite_path)
    complete = open(os.path.join(FIXTURES, "suite-complete.txt"), encoding="utf-8").read()
    line = complete.replace("suite-result.json", str(suite_path))
    ql.apply_phase(str(job), "suite", line, 0)
    suite = json.loads((job / "evidence" / "suite.json").read_text())
    assert suite["attempts"] == 1
    assert suite["stages"][0]["id"] == "s1-frontend"
    assert suite["stages"][0]["wall_s"] == 2111
    assert suite["stages"][0]["requests"] == 9
    assert suite["stages"][0]["prompt_tokens"] == 15514
    assert "compaction_lines" not in suite["stages"][0]
    assert suite["requests"]["max_prompt_tokens"] == 15514
    # A second pass adds a reference and keeps the counted stage.
    second = {
        "instrument": "SUITE-1@1",
        "stages": [],
        "references": {"passed": 1, "attempts": 1},
    }
    second_path = job / "evidence" / "suite-pass-2.json"
    second_path.write_text(json.dumps(second))
    ql.apply_phase(str(job), "suite", "suite complete: " + str(second_path) + "\n", 0)
    merged = json.loads((job / "evidence" / "suite.json").read_text())
    assert merged["attempts"] == 2
    assert merged["stages"][0]["id"] == "s1-frontend"
    assert merged["references"]["passed"] == 1
    assert merged["references"]["attempts"] == 1

    # A stub JSON line still wins over prose.
    ql.apply_phase(str(job), "window", 'note\n{"resets": 3, "ttft_8k": 1.5}\n', 0)
    stub = json.loads((job / "evidence" / "window.json").read_text())
    assert stub["resets"] == 3
    assert stub["ttft_8k"] == 1.5


def test_fixture_text_has_no_host_path_or_key(tmp_path):
    blob = []
    for name in os.listdir(FIXTURES):
        path = os.path.join(FIXTURES, name)
        if os.path.isfile(path):
            blob.append(open(path, encoding="utf-8", errors="replace").read())
    text = "\n".join(blob)
    # The marker is assembled so this test file itself stays out of the private-path scan.
    assert ("/ho" + "me/") not in text
    assert ("api-" + "key") not in text.lower()
    assert ("author" + "ization") not in text.lower()
