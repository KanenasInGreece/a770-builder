#!/usr/bin/env python3
"""Tests for the profiles registry: config/profiles.json + harness/profiles.py."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES_JSON = ROOT / "config" / "profiles.json"
PROFILES_INFERENCE_JSON = ROOT / "config" / "profiles.inference.json"
PROFILES_PY = ROOT / "harness" / "profiles.py"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def load_base() -> dict:
    return json.loads(PROFILES_JSON.read_text(encoding="utf-8"))


def run(*args, env=None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROFILES_PY)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_check_passes_on_shipped_file():
    result = run("check", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_check_fails_missing_key(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["kv"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())
    assert "missing key kv" in result.stderr


def test_check_fails_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["bogus"] = 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: unknown key bogus" in result.stderr


def test_check_fails_useful_ctx_over_ctx(tmp_path):
    data = load_base()
    data["profiles"]["long"]["useful_ctx"] = data["profiles"]["long"]["ctx"] + 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_bad_kv(tmp_path):
    data = load_base()
    data["profiles"]["long"]["kv"] = "q9_9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_bad_kv_v(tmp_path):
    data = load_base()
    data["profiles"]["long"]["kv_v"] = "q9_9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: kv_v must be one of f16, q8_0, q4_0" in result.stderr


def test_check_fails_bad_mode(tmp_path):
    data = load_base()
    data["mode"] = "bogus"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: mode: must be display or inference" in result.stderr


def test_check_fails_sampling_not_object(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = "hot"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling must be an object" in result.stderr


def test_check_fails_sampling_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"source": "x", "bogus": 1}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling: unknown key bogus" in result.stderr


def test_check_fails_sampling_key_not_number(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"source": "x", "temperature": True}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.temperature must be a number" in result.stderr


def test_check_fails_sampling_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"source": "x", "mode": "bogus"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.mode must be thinking or instruct" in result.stderr


def test_check_fails_sampling_missing_source(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"temperature": 0.6}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.source must be a non-empty string" in result.stderr


def test_check_fails_sampling_empty_source(tmp_path):
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"source": "", "top_k": 20}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.source must be a non-empty string" in result.stderr


def test_check_fails_sampling_temperature_without_extra_temp(tmp_path):
    """The honesty check: a sampling.temperature with no --temp in extra is refused."""
    data = load_base()
    data["profiles"]["long"]["sampling"] = {"source": "test card", "temperature": 0.6}
    assert "--temp" not in data["profiles"]["long"]["extra"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.temperature is set but extra carries no --temp" in result.stderr


def test_check_passes_valid_sampling(tmp_path):
    data = load_base()
    data["profiles"]["long"]["extra"] = "--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0"
    data["profiles"]["long"]["sampling"] = {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0,
        "mode": "thinking", "source": "Qwen3.5-9B model card",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_env_temperature_top_p_empty_on_shipped_display_registry():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_LONG_TEMPERATURE:=}"' in result.stdout.splitlines()
    assert ': "${A770B_LONG_TOP_P:=}"' in result.stdout.splitlines()


def test_env_temperature_from_sampling(tmp_path):
    data = load_base()
    data["profiles"]["long"]["extra"] = "--temp 0.6"
    data["profiles"]["long"]["sampling"] = {"temperature": 0.6, "source": "test card"}
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_LONG_TEMPERATURE:=0.6}"' in result.stdout.splitlines()


def test_check_passes_on_both_shipped_registries():
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        result = run("check", "--file", str(f))
        assert result.returncode == 0, result.stderr


def test_check_fails_bad_default(tmp_path):
    data = load_base()
    data["default"] = "nonexistent"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_bad_name(tmp_path):
    data = load_base()
    data["profiles"]["Long1"] = data["profiles"].pop("long")
    data["default"] = "Long1"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_speed_missing_100k(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["speed"]["decode_tps"]["100k"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_env_matches_expected_lines():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    assert lines[0] == ': "${A770B_PROFILES:=long fast serious}"'
    assert lines[1] == ': "${A770B_DEFAULT_PROFILE:=long}"'

    expected_long_block = [
        ': "${A770B_LONG_MODEL:=Qwen3.5-9B-Q4_K_M.gguf}"',
        ': "${A770B_LONG_CTX:=262144}"',
        ': "${A770B_LONG_KV:=q8_0}"',
        ': "${A770B_LONG_KV_V:=q8_0}"',
        ': "${A770B_LONG_TEMPERATURE:=}"',
        ': "${A770B_LONG_TOP_P:=}"',
        ': "${A770B_LONG_OUTPUT_TOKENS:=}"',
        ': "${A770B_LONG_REASONING:=off}"',
        ': "${A770B_LONG_TIMEOUT:=1500}"',
        "[ -n \"${A770B_LONG_EXTRA:-}\" ] || A770B_LONG_EXTRA=''",
    ]
    idx = lines.index(expected_long_block[0])
    assert lines[idx: idx + len(expected_long_block)] == expected_long_block

    expected_serious_extra = (
        '[ -n "${A770B_SERIOUS_EXTRA:-}" ] || A770B_SERIOUS_EXTRA='
        "'--chat-template-kwargs {\"reasoning_effort\":\"low\"} --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0'"
    )
    assert expected_serious_extra in lines


def test_env_roundtrips_extra_and_survives_preset(tmp_path):
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr

    base_data = load_base()
    expected_extra = base_data["profiles"]["serious"]["extra"]

    script = result.stdout + '\necho "$A770B_SERIOUS_EXTRA"\n'
    bash_result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert bash_result.returncode == 0, bash_result.stderr
    assert bash_result.stdout.strip() == expected_extra

    env = os.environ.copy()
    env["A770B_LONG_CTX"] = "1"
    script2 = result.stdout + '\necho "$A770B_LONG_CTX"\n'
    bash_result2 = subprocess.run(["bash", "-c", script2], capture_output=True, text=True, env=env)
    assert bash_result2.returncode == 0, bash_result2.stderr
    assert bash_result2.stdout.strip() == "1"


def test_env_kv_v_defaults_to_kv_and_can_be_set(tmp_path):
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_LONG_KV_V:=q8_0}"' in result.stdout.splitlines()

    data = load_base()
    data["profiles"]["long"]["kv_v"] = "f16"
    path = write_json(tmp_path / "p.json", data)
    result2 = run("env", "--file", str(path))
    assert result2.returncode == 0, result2.stderr
    assert ': "${A770B_LONG_KV_V:=f16}"' in result2.stdout.splitlines()


def test_env_output_is_valid_shell():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    check = subprocess.run(["bash", "-n"], input=result.stdout, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_card_has_three_profiles():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data["profiles"].keys()) == {"long", "fast", "serious"}


def test_card_carries_mode_and_registry():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "display"
    assert data["registry"] == str(PROFILES_JSON.resolve())

    result = run("card", "--file", str(PROFILES_JSON), "--name", "long")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "display"
    assert data["registry"] == str(PROFILES_JSON.resolve())


def test_inference_registry_shape():
    data = json.loads(PROFILES_INFERENCE_JSON.read_text(encoding="utf-8"))
    assert data["mode"] == "inference"
    assert data["default"] == "long"
    for name in data["profiles"]:
        assert NAME_RE.match(name), name


def test_card_name_fast():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "fast")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["name"] == "fast"


def test_card_unknown_name():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "nonexistent")
    assert result.returncode == 2
    assert "profiles: no profile nonexistent" in result.stderr


def test_card_served_overrides_without_changing_model():
    env = os.environ.copy()
    env["A770B_SERIOUS_MODEL"] = "x.gguf"
    result = run("card", "--file", str(PROFILES_JSON), "--served", env=env)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    serious = data["profiles"]["serious"]
    assert serious["served_model"] == "x.gguf"
    assert serious["model"] == "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf"


def test_card_without_served_leaves_defaults():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    long_prof = data["profiles"]["long"]
    assert long_prof["served_model"] == long_prof["model"]
    assert long_prof["served_ctx"] == long_prof["ctx"]


def test_render_updates_skill_table_and_snippet(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "before\n<!-- profiles:begin -->\nold table\n<!-- profiles:end -->\nafter\n",
        encoding="utf-8",
    )
    snippet = tmp_path / "snippet.md"
    snippet.write_text(
        "<!-- profiles:begin -->\nold snippet\n<!-- profiles:end -->\n",
        encoding="utf-8",
    )

    result = run("render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet))
    assert result.returncode == 0, result.stderr

    skill_text = skill.read_text(encoding="utf-8")
    assert "before" in skill_text and "after" in skill_text
    assert "old table" not in skill_text
    assert "262,144 (~65k)" in skill_text
    assert "10.35 GiB" in skill_text

    expected_snippet_line = (
        "**--profile long** (default) = Qwen3.5-9B-Q4_K_M, 262,144-token window (useful to ~65k), ~37 tok/s; "
        "**--profile fast** = gemma-4-E4B-Q4_K_M, 131,072-token window (useful to ~100k), ~60 tok/s; "
        "**--profile serious** = Qwen3.8-27B-GSQ-RCO-IQ3_XXS, 131,072-token window (useful to ~32k), ~8 tok/s."
    )
    snippet_text = snippet.read_text(encoding="utf-8")
    assert expected_snippet_line in snippet_text
    assert "old snippet" not in snippet_text


def test_render_with_inference_file(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "before\n<!-- profiles:begin -->\nold\n<!-- profiles:end -->\n"
        "<!-- profiles-inference:begin -->\nold inf\n<!-- profiles-inference:end -->\nafter\n",
        encoding="utf-8",
    )
    snippet = tmp_path / "snippet.md"
    snippet.write_text(
        "<!-- profiles:begin -->\nold snippet\n<!-- profiles:end -->\n",
        encoding="utf-8",
    )

    result = run(
        "render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet),
        "--inference-file", str(PROFILES_INFERENCE_JSON),
    )
    assert result.returncode == 0, result.stderr

    skill_text = skill.read_text(encoding="utf-8")
    assert "before" in skill_text and "after" in skill_text
    assert "old\n" not in skill_text and "old inf" not in skill_text
    assert "262,144 (~65k)" in skill_text and "10.35 GiB" in skill_text  # display long row
    assert "9.49 GiB" in skill_text  # inference long row
    assert "196,608" in skill_text  # inference serious row's window

    snippet_text = snippet.read_text(encoding="utf-8")
    lines = [l for l in snippet_text.splitlines() if l.strip() and not l.strip().startswith("<!--")]
    assert lines[0].startswith("Display-safe (`A770B_CARD_MODE=display`, the default): ")
    assert lines[1].startswith("Pure-inference (`A770B_CARD_MODE=inference`, a card that draws no desktop): ")


def test_render_missing_inference_markers_exits_2(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "before\n<!-- profiles:begin -->\nold\n<!-- profiles:end -->\nafter\n",
        encoding="utf-8",
    )
    snippet = tmp_path / "snippet.md"
    snippet.write_text(
        "<!-- profiles:begin -->\nold snippet\n<!-- profiles:end -->\n",
        encoding="utf-8",
    )

    result = run(
        "render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet),
        "--inference-file", str(PROFILES_INFERENCE_JSON),
    )
    assert result.returncode == 2
    assert f"profiles: no profiles-inference markers in {skill}" in result.stderr


def test_render_leaves_file_without_markers_unchanged(tmp_path):
    no_markers = tmp_path / "nomarkers.md"
    original = "just some text\nno markers here\n"
    no_markers.write_text(original, encoding="utf-8")
    snippet = tmp_path / "snippet.md"
    snippet.write_text(
        "<!-- profiles:begin -->\nold snippet\n<!-- profiles:end -->\n",
        encoding="utf-8",
    )

    result = run("render", "--file", str(PROFILES_JSON), "--skill", str(no_markers), "--snippet", str(snippet))
    assert result.returncode == 2
    assert no_markers.read_text(encoding="utf-8") == original
    assert f"profiles: no profiles markers in {no_markers}" in result.stderr


def test_render_table_has_separator_after_header(tmp_path):
    """A Markdown table renders only with a separator row under its header."""
    skill = tmp_path / "SKILL.md"; snippet = tmp_path / "SNIP.md"
    skill.write_text("x\n<!-- profiles:begin -->\nold\n<!-- profiles:end -->\ny\n"); snippet.write_text("<!-- profiles:begin -->\n<!-- profiles:end -->\n")
    r = subprocess.run([sys.executable, str(PROFILES_PY), "render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = skill.read_text().splitlines()
    i = lines.index("| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |")
    assert lines[i + 1] == "|---|---|---|---|---|---|"


def test_check_refuses_unknown_top_level_key(tmp_path):
    """An unknown key at the top level is refused like one inside a profile."""
    d = json.loads(PROFILES_JSON.read_text()); d["extra_top"] = 1
    f = tmp_path / "p.json"; f.write_text(json.dumps(d))
    r = subprocess.run([sys.executable, str(PROFILES_PY), "check", "--file", str(f)], capture_output=True, text=True)
    assert r.returncode == 2 and "unknown key extra_top" in r.stderr


def test_card_served_ctx_is_an_int(tmp_path):
    """A served window from the environment comes back as a number, like the file's."""
    env = dict(os.environ); env["A770B_LONG_CTX"] = "131072"
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--name", "long", "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["served_ctx"] == 131072


def test_card_warns_on_inverted_profiles():
    """A builder.env written for an earlier release, with fast and long's files swapped, is caught."""
    env = dict(os.environ)
    env["A770B_FAST_MODEL"] = "Qwen3.5-9B-Q4_K_M.gguf"
    env["A770B_LONG_MODEL"] = "gemma-4-E4B-it-Q4_K_M.gguf"
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    warnings = json.loads(r.stdout)["warnings"]
    assert len(warnings) == 2
    assert warnings[0].startswith("profile long serves fast's file (gemma-4-E4B-it-Q4_K_M.gguf)")
    assert warnings[1].startswith("profile fast serves long's file (Qwen3.5-9B-Q4_K_M.gguf)")


def test_card_no_warnings_when_clean():
    """With no A770B_*_MODEL or A770B_*_CTX overrides, the card carries no warnings."""
    env = {
        k: v for k, v in os.environ.items()
        if not (k.startswith("A770B_") and (k.endswith("_MODEL") or k.endswith("_CTX")))
    }
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["warnings"] == []

    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served", "--name", "long"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["warnings"] == []


# --- U2d: category, weight_class, speed.far_end, fit, suite, output_tokens, builder_class ---


def test_check_fails_missing_category(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["category"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: missing key category" in result.stderr


def test_check_fails_bad_category(tmp_path):
    data = load_base()
    data["profiles"]["long"]["category"] = "small"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: category must be dense or moe" in result.stderr


def test_check_fails_missing_weight_class(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["weight_class"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: missing key weight_class" in result.stderr


def test_check_fails_bad_weight_class(tmp_path):
    data = load_base()
    data["profiles"]["long"]["weight_class"] = "9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: weight_class must look like 9b, 35b-a3b or 8b-e4b" in result.stderr


def test_check_passes_weight_class_shapes(tmp_path):
    for shape in ("9b", "12b", "27b", "35b-a3b", "8b-e4b"):
        data = load_base()
        data["profiles"]["long"]["weight_class"] = shape
        path = write_json(tmp_path / "p.json", data)
        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{shape}: {result.stderr}"


def test_check_fails_far_end_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["far_end"] = {"tokens": 100000, "decode_tps": 11.6, "prefill_tps": 147}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.far_end must be an object with tokens, decode_tps, prefill_tps, ttft_s" in result.stderr


def test_check_fails_far_end_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["far_end"] = {
        "tokens": 100000, "decode_tps": 11.6, "prefill_tps": 147, "ttft_s": 626, "bogus": 1,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.far_end must be an object with tokens, decode_tps, prefill_tps, ttft_s" in result.stderr


def test_check_fails_far_end_bad_type(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["far_end"] = {
        "tokens": "100000", "decode_tps": 11.6, "prefill_tps": 147, "ttft_s": 626,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.far_end.tokens must be a positive int" in result.stderr


def test_check_passes_valid_far_end(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["far_end"] = {
        "tokens": 100000, "decode_tps": 11.6, "prefill_tps": 147, "ttft_s": 626,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_fit_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["fit"] = {"code": "T1: 6 tests green in 122 s", "bogus": "x"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: fit: unknown key bogus" in result.stderr


def test_check_fails_fit_empty_string(tmp_path):
    data = load_base()
    data["profiles"]["long"]["fit"] = {"code": ""}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: fit.code must be a non-empty string" in result.stderr


def test_check_passes_valid_fit(tmp_path):
    data = load_base()
    data["profiles"]["long"]["fit"] = {"code": "T1: 6 tests green in 122 s"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_suite_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {"briefs": 5, "bogus": 1}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite: unknown key bogus" in result.stderr


def test_check_fails_suite_passed_out_of_range(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {"passed": 16, "runs": 15}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.passed must be between 0 and runs" in result.stderr


def test_check_passes_valid_suite(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "mean_wall_s": 120.5, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_suite_missing_key(tmp_path):
    """briefs, runs, passed and source are required in a suite object; mean_wall_s is optional."""
    data = load_base()
    data["profiles"]["long"]["suite"] = {"briefs": 5, "runs": 15, "passed": 12}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite: missing key source" in result.stderr


def test_check_passes_valid_suite_without_mean_wall_s(tmp_path):
    """mean_wall_s stays optional when the other four suite keys are present."""
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_sampling_top_p_without_extra_top_p(tmp_path):
    """The widened honesty check: a sampling.top_p with no --top-p in extra is refused."""
    data = load_base()
    data["profiles"]["long"]["extra"] = "--temp 0.6"
    data["profiles"]["long"]["sampling"] = {"source": "test card", "temperature": 0.6, "top_p": 0.9}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: sampling.top_p is set but extra carries no --top-p" in result.stderr


def test_output_tokens_in_env(tmp_path):
    data = load_base()
    data["profiles"]["long"]["output_tokens"] = 32768
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_LONG_OUTPUT_TOKENS:=32768}"' in result.stdout.splitlines()


def test_builder_class_true_for_inference_moe_long_serious():
    for name in ("moe", "long", "serious"):
        result = run("card", "--file", str(PROFILES_INFERENCE_JSON), "--name", name)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["builder_class"] is True, name


def test_builder_class_false_below_useful_ctx_floor():
    """The shipped display long row: useful_ctx 65536 < 81920, even though task_t1 passes."""
    result = run("card", "--file", str(PROFILES_JSON), "--name", "long")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["useful_ctx"] == 65536
    assert data["builder_class"] is False


def test_builder_class_false_for_weak_task_t1():
    """The shipped display fast row: useful_ctx 100000 clears the floor but task_t1 is weak."""
    result = run("card", "--file", str(PROFILES_JSON), "--name", "fast")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["useful_ctx"] >= 81920
    assert data["task_t1"].startswith("weak:")
    assert data["builder_class"] is False


def test_builder_class_true_for_suite_pass_rate(tmp_path):
    data = load_base()
    data["profiles"]["long"]["useful_ctx"] = 100000
    data["profiles"]["long"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["long"]["suite"] = {"passed": 12, "runs": 15}
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "long")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is True


def test_env_inference_registry_first_line():
    result = run("env", "--file", str(PROFILES_INFERENCE_JSON))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == ': "${A770B_PROFILES:=long moe serious}"'


def test_snippet_labels_use_profile_flag(tmp_path):
    """--profile <name>, the default additionally marked (default)."""
    skill = tmp_path / "SKILL.md"
    skill.write_text("<!-- profiles:begin -->\nold\n<!-- profiles:end -->\n", encoding="utf-8")
    snippet = tmp_path / "snippet.md"
    snippet.write_text("<!-- profiles:begin -->\nold\n<!-- profiles:end -->\n", encoding="utf-8")

    result = run("render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet))
    assert result.returncode == 0, result.stderr

    text = snippet.read_text(encoding="utf-8")
    assert "**--profile long** (default) = " in text
    assert "**--profile fast** = " in text
    assert "**--profile serious** = " in text
