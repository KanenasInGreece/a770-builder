#!/usr/bin/env python3
"""The profiling contract: fixed switches, refusal, confirmation, and nulling."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import profiling_contract as pc

SERVE_STRATA = ROOT / "harness" / "serve_strata.sh"


def test_table_prints_as_json_for_every_engine():
    raw = subprocess.run(
        [sys.executable, str(ROOT / "harness" / "profiling_contract.py")],
        capture_output=True, text=True, check=True,
    ).stdout
    table = json.loads(raw)
    assert set(table) == {"llama.cpp", "vllm", "sglang", "strata"}
    llama = table["llama.cpp"]
    assert llama["status"] == "applied"
    assert llama["flags"] == ["--metrics", "-lv", "4"]
    assert set(llama["forbidden"]) == {
        "--no-metrics", "--metrics", "--log-disable", "-lv",
        "--log-verbosity", "--verbosity",
    }
    vllm = table["vllm"]
    assert vllm["flags"] == ["--enable-per-request-metrics"]
    assert vllm["yields"]["per_request_fields"]["consumed"] is False
    assert vllm["yields"]["metrics_aggregates"]["consumed"] is True
    sglang = table["sglang"]
    assert sglang["status"] == "deferred" and sglang["flags"] == []
    assert sglang["forbidden"] == ["--log-requests"]
    assert sglang["future_flags"] == [
        "--export-metrics-to-file", "--export-metrics-to-file-dir",
    ]
    assert sglang["yields"]["export_file"]["consumed"] is False
    strata = table["strata"]
    assert strata["flags"] == [] and strata["forbidden"] == []
    assert strata["status"] == "applied"
    assert strata["yields"]["usage"]["source"] == "completion usage object"
    assert pc.VLLM_PER_REQUEST_ECHO == "'enable_per_request_metrics': True"


def test_i1_flags_come_from_the_table_once():
    assert pc.flags("llama.cpp") == ["--metrics", "-lv", "4"]
    assert pc.flags("vllm") == ["--enable-per-request-metrics"]
    assert pc.flags("sglang") == []
    assert pc.flags("strata") == []
    assert pc.flags("llama.cpp").count("--metrics") == 1
    assert pc.flags("vllm").count("--enable-per-request-metrics") == 1


def test_refusal_matches_the_flag_and_the_equals_form_only():
    assert pc.refusal("llama.cpp", ["--temp", "0.7"]) is None
    assert pc.refusal("llama.cpp", ["--metrics-extra"]) is None
    metrics = pc.refusal("llama.cpp", ["--metrics"])
    assert metrics and "llama.cpp" in metrics and "--metrics" in metrics
    assert "--metrics" in pc.refusal("llama.cpp", ["--metrics=1"])
    assert "-lv" in pc.refusal("llama.cpp", ["-lv"])
    assert "-lv" in pc.refusal("llama.cpp", ["-lv=4"])
    assert pc.refusal("llama.cpp", ["--log-disable"])
    assert pc.refusal("llama.cpp", ["--verbosity"])
    assert pc.refusal("vllm", ["--disable-log-stats"])
    assert pc.refusal("vllm", ["--no-enable-per-request-metrics"])
    dup = pc.refusal("vllm", ["--enable-per-request-metrics"])
    assert "vllm" in dup and "--enable-per-request-metrics" in dup
    logs = pc.refusal("sglang", ["--log-requests"])
    assert "sglang" in logs and "--log-requests" in logs
    assert pc.refusal("sglang", ["--log-requests=true"])
    assert pc.refusal("sglang", ["--log_requests"])
    assert pc.refusal("sglang", ["--log_requests=true"])
    for token in (
        "--disable_log_stats",
        "--disable_log_stats=true",
        "--no_enable_per_request_metrics",
        "--no_enable_per_request_metrics=1",
        "--enable_per_request_metrics",
        "--enable_per_request_metrics=true",
    ):
        message = pc.refusal("vllm", [token])
        assert message and "vllm" in message, token
    assert pc.refusal("llama.cpp", ["--log_disable"]) is None
    assert pc.refusal("strata", ["--anything"]) is None
    # glued -lv4 is not the --flag or --flag=value form
    assert pc.refusal("llama.cpp", ["-lv4"]) is None


def test_confirm_llama_and_vllm_from_a_healthy_start():
    good = pc.confirm_llama(200, "llamacpp:prompt_tokens_total 1\n", "verbosity = 4\n")
    assert good["--metrics"] is True and good["-lv 4"] is True
    bad = pc.confirm_llama(0, "", "engine refused the model")
    assert bad["--metrics"] is False and bad["-lv 4"] is False
    assert pc.confirm_llama(200, "other", "verbosity = 3")["--metrics"] is False
    echo = "non-default args: {'enable_per_request_metrics': True}"
    # the named constant is the proof, including the quotes the log prints
    echo = "non-default args: {" + pc.VLLM_PER_REQUEST_ECHO + "}"
    assert pc.confirm_vllm(echo)["--enable-per-request-metrics"] is True
    assert pc.confirm_vllm("non-default args: {}")["--enable-per-request-metrics"] is False
    assert pc.confirm_started("sglang", "127.0.0.1", 9, "") == {}
    assert pc.confirm_started("strata", "127.0.0.1", 9, "") == {}


def test_i5_unconfirmed_verbosity_nulls_exactly_four_fields():
    row = {
        "profiling": {
            "contract": 1,
            "engine": "llama.cpp",
            "flags": ["--metrics", "-lv", "4"],
            "confirmed": {"--metrics": True, "-lv 4": False},
        },
        "requests": {
            "max_prompt_tokens": 10,
            "max_completion_tokens": 4,
            "length_stops": 2,
            "prefill_tps": 100.0,
            "decode_tps": 20.0,
            "source": "suite.json",
        },
        "speed": {"delivered": {"prefill_tps": 1, "decode_tps": 2, "source": "s"}},
    }
    pc.null_unconfirmed_verbosity(row)
    assert row["requests"]["prefill_tps"] is None
    assert row["requests"]["decode_tps"] is None
    assert row["requests"]["length_stops"] is None
    assert row["requests"]["max_prompt_tokens"] == 10
    assert row["requests"]["max_completion_tokens"] == 4
    assert row["requests"]["source"] == "suite.json"
    assert row["speed"]["delivered"] is None
    pending = {
        "profiling": {"engine": "llama.cpp", "confirmed": {"-lv 4": "pending"}},
        "requests": {"prefill_tps": 1, "decode_tps": 2, "length_stops": 3},
        "speed": {"delivered": {"prefill_tps": 1}},
    }
    pc.null_unconfirmed_verbosity(pending)
    assert pending["requests"]["prefill_tps"] == 1
    assert pending["speed"]["delivered"]["prefill_tps"] == 1
    confirmed = {
        "profiling": {"engine": "llama.cpp", "confirmed": {"-lv 4": True}},
        "requests": {"prefill_tps": 9, "decode_tps": 8, "length_stops": 1},
        "speed": {"delivered": {"decode_tps": 8}},
    }
    pc.null_unconfirmed_verbosity(confirmed)
    assert confirmed["requests"]["decode_tps"] == 8
    assert confirmed["speed"]["delivered"]["decode_tps"] == 8
    other = {
        "profiling": {"engine": "vllm", "confirmed": {"--enable-per-request-metrics": False}},
        "requests": {"prefill_tps": 3, "decode_tps": 4, "length_stops": 1},
        "speed": {"delivered": {"decode_tps": 4}},
    }
    pc.null_unconfirmed_verbosity(other)
    assert other["requests"]["prefill_tps"] == 3
    assert other["speed"]["delivered"]["decode_tps"] == 4
    absent = {"requests": {"prefill_tps": 5, "decode_tps": 6, "length_stops": 1}}
    pc.null_unconfirmed_verbosity(absent)
    assert absent["requests"]["prefill_tps"] == 5
    # A number compares equal to True and False. It is not a confirmation.
    numeric = {
        "profiling": {"engine": "llama.cpp", "confirmed": {"-lv 4": 1}},
        "requests": {"prefill_tps": 4, "decode_tps": 3, "length_stops": 1},
        "speed": {"delivered": {"decode_tps": 3}},
    }
    pc.null_unconfirmed_verbosity(numeric)
    assert numeric["requests"]["prefill_tps"] is None
    assert numeric["speed"]["delivered"] is None


def test_bash_code_keeps_a_flag_on_a_length_line():
    count = "if [ $# -gt 0 ]; then ARGV=(--metrics -lv 4); fi\n"
    items = "n=${#arr[@]}; printf %s --metrics\n"
    assert "--metrics" in pc._bash_code(count)
    assert "--metrics" in pc._bash_code(items)
    assert pc._spelling_hits(pc._bash_code(count), "--metrics") == ["--metrics"]
    assert "--metrics" not in pc._bash_code("printf ok # --metrics\n")
    assert "--metrics" not in pc._bash_code("printf ok; # --metrics\n")


def test_i3_applied_flags_are_spelled_only_in_the_contract():
    hits = pc.spellings_outside_contract(str(ROOT / "harness"))
    assert hits == [], hits


def test_strata_table_is_empty_and_an_extra_flag_is_refused():
    assert pc.flags("strata") == [] and pc.TABLE["strata"]["forbidden"] == []
    env = dict(os.environ)
    env["A770B_PROJECT"] = str(ROOT)
    result = subprocess.run(
        ["bash", str(SERVE_STRATA), "start", "prepared.json", "8192", "--draft"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Strata engine flags belong in the prepared config" in result.stderr


def test_absorb_limit_accepts_only_a_positive_integer():
    assert pc.parse_absorb_limit(None) is None
    assert pc.parse_absorb_limit("") is None
    assert pc.parse_absorb_limit("600") == 600
    for bad in ("0", "-5", "600.0", " 600 ", "abc"):
        try:
            pc.parse_absorb_limit(bad)
        except SystemExit as exc:
            assert exc.code == 2, bad
        else:
            raise AssertionError(bad)


def test_absorb_window_limit_examples():
    # salvaged sweep: targets 8000 and 100000, ttft 14.0 and 580.7, limit 600
    useful, absorb, limit = pc.absorb_window(
        262144, 100000, 14.0, 580.7, 600, True, False,
    )
    assert useful == 103133 and limit == 600
    assert absorb == {"8k": 14.0, "far_end": 580.7}
    # falling ttft must not extrapolate a negative slope
    falling, _, _ = pc.absorb_window(40000, 38976, 200.0, 50.0, 80, True, False)
    assert falling == 40000
    flat, _, _ = pc.absorb_window(40000, 38976, 100.0, 100.0, 600, True, False)
    assert flat == 40000
    over, _, _ = pc.absorb_window(40000, 38976, 700.0, 900.0, 600, True, False)
    assert over == 6857
    missing, _, _ = pc.absorb_window(40000, 38976, 14.0, None, 600, True, False)
    assert missing is None
    capped, _, _ = pc.absorb_window(262144, 100000, 14.0, 580.7, 600, False, False)
    assert capped == 8000
    unanswered, _, _ = pc.absorb_window(262144, 100000, 14.0, 580.7, 600, None, True)
    assert unanswered is None


_OLD_RULE = (
    "four-tokens-a-second",
    "four tokens a second",
    "above 4 tok/s",
    "above four tokens",
)

_NAMED_TEXT = (
    "kit/PROFILE.md",
    "AGENTS.md",
    "README.md",
    "docs/OPERATING.md",
    "skills/local-build/SKILL.md",
    "config/models.md",
    "harness/ladder.sh",
    "harness/ladder_row.py",
    "harness/ctx_sweep.sh",
    "render_readme.sh",
)


def test_i10_old_rule_wording_is_only_on_legacy_rows():
    bad = []
    for rel in _NAMED_TEXT:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if any(phrase in line for phrase in _OLD_RULE) and "legacy" not in line:
                bad.append(f"{rel}:{number}")
    assert bad == [], bad


def test_i11_the_diff_has_no_record_ids_or_private_names():
    chunks = []
    for args in (
        ["git", "diff", "main"],
        ["git", "diff", "--cached"],
        ["git", "diff"],
    ):
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        chunks.append(result.stdout.lower())
    diff = "\n".join(chunks)
    needles = (
        "xeno" + "fon",
        "claw" + "ee",
        "cl" + "oe",
        "/home" + "/",
        "fact" + ":",
        "decision" + ":",
        "retro" + ":",
    )
    found = [needle for needle in needles if needle in diff]
    assert found == [], found


def test_i6_without_a_limit_the_window_is_the_context():
    slow, _, limit = pc.absorb_window(40000, 38976, 14.0, 800.0, None, True, False)
    assert slow == 40000 and limit is None
    huge, _, _ = pc.absorb_window(40000, 38976, 9000.0, 9000.0, None, True, False)
    assert huge == 40000
    fast, _, _ = pc.absorb_window(40000, 38976, 1.0, 1.0, None, True, False)
    assert fast == slow
    no_far, _, _ = pc.absorb_window(40000, 38976, 14.0, None, None, True, False)
    assert no_far is None
    failed, _, _ = pc.absorb_window(40000, 38976, 14.0, 20.0, None, False, False)
    assert failed == 8000
