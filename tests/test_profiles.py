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
        ': "${A770B_LONG_THINKING_MODE:=}"',
        ': "${A770B_LONG_THINKING_EFFORT:=}"',
        ': "${A770B_LONG_THINKING_BUDGET:=}"',
        "[ -n \"${A770B_LONG_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_LONG_THINKING_BUDGET_MESSAGE=''",
        ': "${A770B_LONG_THINKING_PRESERVE:=}"',
        ': "${A770B_LONG_TIMEOUT:=1500}"',
        "[ -n \"${A770B_LONG_EXTRA:-}\" ] || A770B_LONG_EXTRA=''",
    ]
    idx = lines.index(expected_long_block[0])
    assert lines[idx: idx + len(expected_long_block)] == expected_long_block

    expected_serious_extra = (
        '[ -n "${A770B_SERIOUS_EXTRA:-}" ] || A770B_SERIOUS_EXTRA='
        "'--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0'"
    )
    assert expected_serious_extra in lines
    expected_serious_thinking = [
        ': "${A770B_SERIOUS_THINKING_MODE:=on}"',
        ': "${A770B_SERIOUS_THINKING_EFFORT:=low}"',
        ': "${A770B_SERIOUS_THINKING_BUDGET:=}"',
        "[ -n \"${A770B_SERIOUS_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_SERIOUS_THINKING_BUDGET_MESSAGE=''",
        ': "${A770B_SERIOUS_THINKING_PRESERVE:=true}"',
    ]
    for line in expected_serious_thinking:
        assert line in lines


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
    """A skill file with the profiles pair but not the profiles-inference pair, plus a snippet
    with its pair: exit 2, and NOTHING written -- not even the skill file's own valid pair."""
    skill = tmp_path / "SKILL.md"
    skill_original = "before\n<!-- profiles:begin -->\nold\n<!-- profiles:end -->\nafter\n"
    skill.write_text(skill_original, encoding="utf-8")
    snippet = tmp_path / "snippet.md"
    snippet_original = "<!-- profiles:begin -->\nold snippet\n<!-- profiles:end -->\n"
    snippet.write_text(snippet_original, encoding="utf-8")

    result = run(
        "render", "--file", str(PROFILES_JSON), "--skill", str(skill), "--snippet", str(snippet),
        "--inference-file", str(PROFILES_INFERENCE_JSON),
    )
    assert result.returncode == 2
    assert f"profiles: no profiles-inference markers in {skill}" in result.stderr
    assert skill.read_text(encoding="utf-8") == skill_original
    assert snippet.read_text(encoding="utf-8") == snippet_original


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
        # long's new weight_class may now collide with fast's or serious's own (category,
        # weight_class) -- move those out of the way so this test stays about weight_class's
        # shape, not the registry's one-row-per-class rule (covered separately).
        data["profiles"]["fast"]["category"] = "moe"
        data["profiles"]["serious"]["category"] = "moe"
        path = write_json(tmp_path / "p.json", data)
        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{shape}: {result.stderr}"


def test_check_fails_missing_card(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["card"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key card" in result.stderr


def test_check_fails_missing_backend(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["backend"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key backend" in result.stderr


def test_check_fails_missing_mode(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["mode"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key mode" in result.stderr


def test_check_fails_uppercase_card(tmp_path):
    data = load_base()
    data["profiles"]["long"]["card"] = "A770"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_uppercase_backend(tmp_path):
    data = load_base()
    data["profiles"]["long"]["backend"] = "Vulkan"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["long"]["mode"] = "gpu"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_row_mode_disagrees_with_file(tmp_path):
    data = load_base()
    data["profiles"]["long"]["mode"] = "inference"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: mode 'inference' disagrees with file mode 'display'" in result.stderr


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


# --- KU9: speed.bench (llama-bench), speed.delivered (the suite's own as-delivered speed) ---


def _valid_bench(**overrides) -> dict:
    bench = {
        "tool": "llama-bench b10805",
        "prompt": 8192,
        "gen": 128,
        "flags": "-fa on -ctk q4_0 -ctv q4_0 -ub 512 -d 0,8192,32768 -o json",
        "at_depth": {"0": {"pp": 620.0, "tg": 40.0}, "8192": {"pp": 571.0, "tg": 36.8}, "32768": {"pp": 274.0, "tg": 23.1}},
        "source": "long-bench-20260908-120000.json",
    }
    bench.update(overrides)
    return bench


def test_check_fails_bench_missing_key(tmp_path):
    data = load_base()
    bench = _valid_bench()
    del bench["gen"]
    data["profiles"]["long"]["speed"]["bench"] = bench
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench must be an object with tool, prompt, gen, flags, at_depth, source" in result.stderr


def test_check_fails_bench_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(bogus=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench must be an object with tool, prompt, gen, flags, at_depth, source" in result.stderr


def test_check_fails_bench_empty_string_field(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(tool="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.tool must be a non-empty string" in result.stderr


def test_check_fails_bench_bad_prompt_type(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(prompt="8192")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.prompt must be a positive int" in result.stderr


def test_check_fails_bench_at_depth_not_object(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(at_depth=[])
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.at_depth must be a non-empty object" in result.stderr


def test_check_fails_bench_at_depth_bad_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(at_depth={"8k": {"pp": 1, "tg": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.at_depth: key '8k' must be a digit string" in result.stderr


def test_check_fails_bench_at_depth_value_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": 1, "tg": 1, "bogus": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.at_depth.0 must be an object with pp, tg" in result.stderr


def test_check_fails_bench_at_depth_value_bad_type(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": "1", "tg": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.bench.at_depth.0.pp must be a number or null" in result.stderr


def test_check_passes_bench_at_depth_null_values(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": None, "tg": None}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_valid_bench(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench()
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_delivered_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["delivered"] = {"prefill_tps": 500.0}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.delivered must be an object with prefill_tps, decode_tps, source" in result.stderr


def test_check_fails_delivered_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json", "bogus": 1,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.delivered must be an object with prefill_tps, decode_tps, source" in result.stderr


def test_check_fails_delivered_bad_value(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["delivered"] = {
        "prefill_tps": -1.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.delivered.prefill_tps must be a positive number" in result.stderr


def test_check_fails_delivered_empty_source(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["delivered"] = {"prefill_tps": 500.0, "decode_tps": 30.0, "source": ""}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: speed.delivered.source must be a non-empty string" in result.stderr


def test_check_passes_valid_delivered(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_bench_and_delivered_together(tmp_path):
    data = load_base()
    data["profiles"]["long"]["speed"]["bench"] = _valid_bench()
    data["profiles"]["long"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


# The registry has ONE home for the suite's as-delivered medians, and the pair below says which: a row that
# carries them under `suite` — the shape harness/suite_report.py --json publishes, since its `delivered` object
# rides inside the totals — is refused, and the same row with them under `speed` checks out. That is why
# harness/ladder.sh moves the object out of the totals it assigns to the printed row's `suite` and into the row's
# `speed`: a freshly measured row is meant to be pasted into config/profiles.json unedited.


def _suite_totals(**overrides) -> dict:
    """The totals harness/suite_report.py --json prints, without `delivered`."""
    totals = {"briefs": 4, "runs": 4, "passed": 3, "timeouts": 0, "mean_wall_s": 210.5,
              "source": "long-suite-20260908-120000.json"}
    totals.update(overrides)
    return totals


def test_check_fails_delivered_under_suite(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = _suite_totals(
        delivered={"prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908-120000.json"},
    )
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite: unknown key delivered" in result.stderr


def test_check_passes_the_same_row_with_delivered_under_speed(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = _suite_totals()
    data["profiles"]["long"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908-120000.json",
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


def test_check_fails_suite_timeouts_out_of_range(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {"briefs": 5, "runs": 15, "passed": 12, "timeouts": 16, "source": "s"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.timeouts must be between 0 and runs" in result.stderr


def test_check_passes_valid_suite_with_timeouts(tmp_path):
    """timeouts (harness/suite_report.py's count of "timeout"-outcome stages) is optional, and a valid count
    between 0 and runs passes."""
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "timeouts": 2, "mean_wall_s": 120.5, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


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


def test_builder_class_true_for_inference_long_serious():
    for name in ("long", "serious"):
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
    assert result.stdout.splitlines()[0] == ': "${A770B_PROFILES:=long serious}"'


# --- KU8: suite.stages, suite.reviewer, suite.instrument, profile-level instrument, comparable_with ---


def test_check_fails_missing_instrument(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["instrument"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: missing key instrument" in result.stderr


def test_check_fails_empty_instrument(tmp_path):
    data = load_base()
    data["profiles"]["long"]["instrument"] = ""
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: instrument must be a non-empty string" in result.stderr


def test_check_fails_bad_instrument_type(tmp_path):
    data = load_base()
    data["profiles"]["long"]["instrument"] = 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: instrument must be a non-empty string" in result.stderr


def test_check_fails_suite_instrument_empty(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "kit", "instrument": "",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.instrument must be a non-empty string" in result.stderr


def test_check_passes_valid_suite_instrument(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "kit", "instrument": "SUITE-1@0.2.0",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


# --- measured_on: comparable_with is declared across machines on instrument alone otherwise ---


def test_check_fails_missing_measured_on(tmp_path):
    data = load_base()
    del data["profiles"]["long"]["measured_on"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: missing key measured_on" in result.stderr


def test_check_passes_on_both_shipped_registries_with_measured_on():
    """Every shipped row (both registries) carries measured_on -- check accepts it."""
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(f.read_text(encoding="utf-8"))
        for name, prof in data["profiles"].items():
            assert prof.get("measured_on"), f"{f}: {name}: missing measured_on"


def test_comparable_with_excludes_differing_measured_on(tmp_path):
    """Two rows sharing an instrument string but measured on different cards/builds must not
    read as comparable: comparable_with matches on the pair (instrument, measured_on), not
    instrument alone."""
    data = load_base()
    assert data["profiles"]["long"]["instrument"] == data["profiles"]["fast"]["instrument"]
    data["profiles"]["long"]["measured_on"] = "RTX 4090 24 GB, llama.cpp b10805 CUDA"
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path))
    assert result.returncode == 0, result.stderr
    data_out = json.loads(result.stdout)
    assert data_out["profiles"]["long"]["comparable_with"]["profiles"] == []
    assert "long" not in data_out["profiles"]["fast"]["comparable_with"]["profiles"]
    assert "long" not in data_out["profiles"]["serious"]["comparable_with"]["profiles"]


# --- registry-wide rules: one best row per (category, weight_class, backend), one instrument per registry ---


def test_check_fails_duplicate_category_weight_class(tmp_path):
    data = load_base()
    data["profiles"]["fast"]["category"] = data["profiles"]["long"]["category"]
    data["profiles"]["fast"]["weight_class"] = data["profiles"]["long"]["weight_class"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: fast: category 'dense' + weight_class '9b' + backend 'vulkan' duplicates long's -- "
        "a registry keeps one best row per class per backend" in result.stderr
    )


def test_check_passes_same_class_different_backend(tmp_path):
    data = load_base()
    data["profiles"]["fast"]["category"] = data["profiles"]["long"]["category"]
    data["profiles"]["fast"]["weight_class"] = data["profiles"]["long"]["weight_class"]
    data["profiles"]["fast"]["backend"] = "sycl"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_on_both_shipped_registries_with_card_backend_mode():
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(f.read_text(encoding="utf-8"))
        file_mode = data["mode"]
        for name, prof in data["profiles"].items():
            assert prof["card"] == "a770", f"{f}: {name}: card"
            assert prof["backend"] == "vulkan", f"{f}: {name}: backend"
            assert prof["mode"] == file_mode, f"{f}: {name}: mode"
        result = run("check", "--file", str(f))
        assert result.returncode == 0, result.stderr


def test_env_exports_card_backend_mode():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert "A770B_LONG_CARD:=a770" in result.stdout
    assert "A770B_LONG_BACKEND:=vulkan" in result.stdout
    assert "A770B_LONG_MODE:=display" in result.stdout


def test_check_fails_differing_instrument_within_registry(tmp_path):
    data = load_base()
    data["profiles"]["fast"]["instrument"] = "a different rig"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: fast: instrument 'a different rig' differs from long's "
        "'seat: Shared_Memory@3c8e2bb' -- a registry is one instrument" in result.stderr
    )


def _valid_stage(**overrides) -> dict:
    stage = {
        "id": "s0-design", "working": True, "conformance": True, "lines": 40, "budget_lines": 60,
        "maintainable": 5, "usable": 3, "wall_s": 12.5, "axes": ["maintainable", "usable"],
        "counts_toward_pass": True,
    }
    stage.update(overrides)
    return stage


def test_check_fails_stages_not_a_list(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": "nope",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages must be a list" in result.stderr


def test_check_fails_stage_not_an_object(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": ["nope"],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0] must be an object" in result.stderr


def test_check_fails_stage_unknown_key(tmp_path):
    data = load_base()
    stage = _valid_stage(bogus=1)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0]: unknown key bogus" in result.stderr


def test_check_fails_stage_missing_key(tmp_path):
    data = load_base()
    stage = _valid_stage()
    del stage["wall_s"]
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0]: missing key wall_s" in result.stderr


def test_check_fails_stage_bad_id(tmp_path):
    data = load_base()
    stage = _valid_stage(id="")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].id must be a non-empty string" in result.stderr


def test_check_fails_stage_bad_working(tmp_path):
    data = load_base()
    stage = _valid_stage(working="yes")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].working must be a bool" in result.stderr


def test_check_fails_stage_bad_conformance(tmp_path):
    data = load_base()
    stage = _valid_stage(conformance="pass")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].conformance must be a bool or null" in result.stderr


def test_check_passes_stage_conformance_null(tmp_path):
    data = load_base()
    stage = _valid_stage(conformance=None)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_stage_bad_lines(tmp_path):
    data = load_base()
    stage = _valid_stage(lines="40")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].lines must be an int or null" in result.stderr


def test_check_fails_stage_bad_budget_lines(tmp_path):
    data = load_base()
    stage = _valid_stage(budget_lines=60.5)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].budget_lines must be an int or null" in result.stderr


def test_check_fails_stage_bad_maintainable(tmp_path):
    data = load_base()
    stage = _valid_stage(maintainable=4)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].maintainable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bool_not_maintainable(tmp_path):
    """A bool is never 0, 3 or 5, even though False == 0 in Python."""
    data = load_base()
    stage = _valid_stage(maintainable=False)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].maintainable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bad_usable(tmp_path):
    data = load_base()
    stage = _valid_stage(usable=2)
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].usable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bad_wall_s(tmp_path):
    data = load_base()
    stage = _valid_stage(wall_s="12.5")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].wall_s must be a number" in result.stderr


def test_check_fails_stage_bad_axes(tmp_path):
    data = load_base()
    stage = _valid_stage(axes=["working", 1])
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].axes must be a list of strings" in result.stderr


def test_check_passes_valid_stages_list(tmp_path):
    data = load_base()
    stages = [
        _valid_stage(id="s0-design", axes=["design"], working=True),
        _valid_stage(id="s1-frontend", axes=["working", "conformance"], working=True),
        _valid_stage(id="s4-rubric", axes=["maintainable", "usable"], working=False, maintainable=5, usable=5),
    ]
    data["profiles"]["long"]["suite"] = {
        "briefs": 3, "runs": 3, "passed": 2, "source": "kit", "instrument": "SUITE-1@0.2.0", "stages": stages,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_reviewer_not_an_object(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "reviewer": "nope",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.reviewer must be an object" in result.stderr


def test_check_fails_reviewer_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {"profile": "long", "bogus": 1},
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.reviewer: unknown key bogus" in result.stderr


def test_check_fails_reviewer_bad_value_type(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {"profile": "long", "rubric_sha256": True},
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.reviewer.rubric_sha256 must be a string or int" in result.stderr


def test_check_passes_valid_reviewer(tmp_path):
    data = load_base()
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {
            "profile": "serious", "model": "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf", "ctx": 32768,
            "sampling": "temp 0, thinking off", "rubric_sha256": "abc123",
        },
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_builder_class_tally_uses_counts_toward_pass_not_axes(tmp_path):
    """The root bug: `_builder_class` used to count only stages whose `axes` contained
    "working" -- but no stage in kit/suite.json ever puts "working" in axes (they carry
    maintainable/usable only), so a real four-stage suite.stages list (this is exactly
    kit/suite.json's shape: one commentary-only design stage plus three counted ones)
    always tallied zero counted stages under the old rule and fell through to False here
    (task_t1 is deliberately weak so the old code's other fallback can't paper over it).
    The honest rule counts a stage when its own `counts_toward_pass` is not false."""
    data = load_base()
    data["profiles"]["long"]["useful_ctx"] = 100000
    data["profiles"]["long"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["long"]["suite"] = {
        "briefs": 4, "runs": 4, "passed": 3, "source": "kit",
        "stages": [
            _valid_stage(id="s0-design", axes=["maintainable", "usable"], working=True, counts_toward_pass=False),
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s3-optimise", axes=["maintainable"], working=True, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "long")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is True


def test_builder_class_false_from_stages_when_counted_ratio_low(tmp_path):
    """Same counts_toward_pass-driven tally, the other direction: two counted stages, one
    failing, is a 0.5 ratio -- below the 0.8 bar."""
    data = load_base()
    data["profiles"]["long"]["useful_ctx"] = 100000
    data["profiles"]["long"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["long"]["suite"] = {
        "briefs": 2, "runs": 2, "passed": 1, "source": "kit",
        "stages": [
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=False, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "long")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is False


def test_builder_class_false_when_suite_fails_despite_task_t1_pass(tmp_path):
    """The other bug: an old `task_t1: "pass"` used to short-circuit builder_class to True
    even when `suite` recorded failures. The suite is now the authority whenever it is
    present -- a failing suite makes the row false no matter what task_t1 says."""
    data = load_base()
    data["profiles"]["long"]["useful_ctx"] = 100000
    assert data["profiles"]["long"]["task_t1"] == "pass"
    data["profiles"]["long"]["suite"] = {
        "briefs": 4, "runs": 4, "passed": 0, "source": "kit",
        "stages": [
            _valid_stage(id="s0-design", axes=["maintainable", "usable"], working=True, counts_toward_pass=False),
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=False, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=False, counts_toward_pass=True),
            _valid_stage(id="s3-optimise", axes=["maintainable"], working=False, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "long")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is False


def test_check_fails_stage_bad_counts_toward_pass(tmp_path):
    data = load_base()
    stage = _valid_stage(counts_toward_pass="yes")
    data["profiles"]["long"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: suite.stages[0].counts_toward_pass must be a bool" in result.stderr


def test_comparable_with_on_display_registry():
    """The shipped display registry: every row lists the other two as comparable (same instrument)."""
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for name in ("long", "fast", "serious"):
        others = sorted(n for n in ("long", "fast", "serious") if n != name)
        assert sorted(data["profiles"][name]["comparable_with"]["profiles"]) == others, name
        assert data["profiles"][name]["comparable_with"]["instrument"] == data["profiles"][name]["instrument"]
        assert data["profiles"][name]["comparable_with"]["measured_on"] == data["profiles"][name]["measured_on"]


def test_comparable_with_on_inference_registry():
    """The shipped inference registry holds two rows, so each lists the one other row as comparable
    (same instrument)."""
    result = run("card", "--file", str(PROFILES_INFERENCE_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for name in ("long", "serious"):
        others = sorted(n for n in ("long", "serious") if n != name)
        assert sorted(data["profiles"][name]["comparable_with"]["profiles"]) == others, name
        assert data["profiles"][name]["comparable_with"]["instrument"] == data["profiles"][name]["instrument"]
        assert data["profiles"][name]["comparable_with"]["measured_on"] == data["profiles"][name]["measured_on"]


def test_comparable_with_excludes_differing_instrument(tmp_path):
    data = load_base()
    data["profiles"]["long"]["instrument"] = "SUITE-1@0.2.0"
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path))
    assert result.returncode == 0, result.stderr
    data_out = json.loads(result.stdout)
    assert data_out["profiles"]["long"]["comparable_with"]["profiles"] == []
    assert "long" not in data_out["profiles"]["fast"]["comparable_with"]["profiles"]
    assert "long" not in data_out["profiles"]["serious"]["comparable_with"]["profiles"]


def test_card_prints_instrument():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "long")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["instrument"] == "seat: Shared_Memory@3c8e2bb"


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


# --- thinking: the server's five thinking controls (--reasoning, --reasoning-effort,
# --reasoning-budget, --reasoning-budget-message, --reasoning-preserve) as a profile field ---


def _valid_thinking(**overrides) -> dict:
    # mode "off" matches the shipped long profile's own reasoning: off, so a caller testing an
    # unrelated field (budget, effort, source, ...) does not incidentally trip the
    # mode-vs-reasoning contradiction rule; tests of that rule itself override mode explicitly.
    thinking = {
        "mode": "off", "effort": "low", "budget": 512, "budget_message": "budget spent, answer now",
        "preserve": True, "source": "test card",
    }
    thinking.update(overrides)
    return thinking


def test_check_fails_thinking_not_object(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = "hot"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking must be an object" in result.stderr


def test_check_fails_thinking_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(bogus=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking: unknown key bogus" in result.stderr


def test_check_fails_thinking_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(mode="maybe")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.mode must be on, off or auto" in result.stderr


def test_check_fails_thinking_bad_effort(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(effort="extreme")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.effort must be one of low, medium, high, xhigh, default" in result.stderr


def test_check_fails_thinking_effort_not_string(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(effort=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.effort must be one of low, medium, high, xhigh, default" in result.stderr


def test_check_fails_thinking_budget_not_int(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget="512")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.budget must be -1, 0 or a positive int" in result.stderr


def test_check_fails_thinking_budget_below_minus_one(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget=-2)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.budget must be -1, 0 or a positive int" in result.stderr


def test_check_passes_thinking_budget_minus_one_and_zero(tmp_path):
    for b in (-1, 0, 512):
        data = load_base()
        data["profiles"]["long"]["thinking"] = _valid_thinking(budget=b)
        path = write_json(tmp_path / "p.json", data)

        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"budget={b}: {result.stderr}"


def test_check_fails_thinking_budget_message_not_string(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget_message=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.budget_message must be a non-empty string" in result.stderr


def test_check_fails_thinking_budget_message_empty(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget_message="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.budget_message must be a non-empty string" in result.stderr


def test_check_fails_thinking_preserve_not_bool(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(preserve="yes")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.preserve must be a bool" in result.stderr


def test_check_fails_thinking_source_empty(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(source="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.source must be a non-empty string" in result.stderr


def test_check_fails_thinking_source_not_string(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(source=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.source must be a non-empty string" in result.stderr


def test_check_passes_thinking_every_key_optional(tmp_path):
    """Every thinking key is optional: an empty object, and a single-key object, both pass."""
    for partial in ({}, {"mode": "auto"}, {"preserve": False}, {"source": "x"}):
        data = load_base()
        data["profiles"]["long"]["thinking"] = partial
        path = write_json(tmp_path / "p.json", data)

        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{partial}: {result.stderr}"


def test_check_passes_valid_thinking(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking()
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_thinking_mode_contradicts_reasoning_on(tmp_path):
    """long ships with reasoning: off -- a thinking.mode of on contradicts it."""
    data = load_base()
    assert data["profiles"]["long"]["reasoning"] == "off"
    data["profiles"]["long"]["thinking"] = _valid_thinking(mode="on")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: long: thinking.mode 'on' contradicts reasoning 'off'" in result.stderr


def test_check_fails_thinking_mode_contradicts_reasoning_off(tmp_path):
    """serious ships with reasoning: on -- a thinking.mode of off contradicts it."""
    data = load_base()
    assert data["profiles"]["serious"]["reasoning"] == "on"
    data["profiles"]["serious"]["thinking"]["mode"] = "off"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: serious: thinking.mode 'off' contradicts reasoning 'on'" in result.stderr


def test_check_passes_thinking_mode_auto_never_contradicts(tmp_path):
    """auto is compatible with either reasoning value -- it is not a fixed state."""
    for name in ("long", "serious"):
        data = load_base()
        thinking = dict(data["profiles"][name].get("thinking") or {})
        thinking["mode"] = "auto"
        thinking.setdefault("source", "test")
        data["profiles"][name]["thinking"] = thinking
        path = write_json(tmp_path / "p.json", data)

        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_check_passes_thinking_mode_matches_reasoning(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(mode="off")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_env_thinking_empty_when_unset():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert ': "${A770B_LONG_THINKING_MODE:=}"' in lines
    assert ': "${A770B_LONG_THINKING_EFFORT:=}"' in lines
    assert ': "${A770B_LONG_THINKING_BUDGET:=}"' in lines
    assert "[ -n \"${A770B_LONG_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_LONG_THINKING_BUDGET_MESSAGE=''" in lines
    assert ': "${A770B_LONG_THINKING_PRESERVE:=}"' in lines


def test_env_thinking_set_from_registry(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(
        mode="off", budget=0, budget_message="stop thinking, it's time",
    )
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert ': "${A770B_LONG_THINKING_MODE:=off}"' in lines
    assert ': "${A770B_LONG_THINKING_EFFORT:=low}"' in lines
    assert ': "${A770B_LONG_THINKING_BUDGET:=0}"' in lines
    assert (
        "[ -n \"${A770B_LONG_THINKING_BUDGET_MESSAGE:-}\" ] || "
        "A770B_LONG_THINKING_BUDGET_MESSAGE='stop thinking, it'\\''s time'"
    ) in lines
    assert ': "${A770B_LONG_THINKING_PRESERVE:=true}"' in lines


def test_env_thinking_output_is_valid_shell(tmp_path):
    """A budget_message with an embedded single quote (an apostrophe, plausible in real prose)
    must round-trip through the shell unbroken."""
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget_message="don't stop, it's fine")
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    script = result.stdout + '\necho "$A770B_LONG_THINKING_BUDGET_MESSAGE"\n'
    bash_result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert bash_result.returncode == 0, bash_result.stderr
    assert bash_result.stdout.strip() == "don't stop, it's fine"


def test_env_thinking_negative_one_budget(tmp_path):
    data = load_base()
    data["profiles"]["long"]["thinking"] = _valid_thinking(budget=-1)
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_LONG_THINKING_BUDGET:=-1}"' in result.stdout.splitlines()


def test_card_prints_thinking_as_stored():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "serious")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["thinking"] == {
        "mode": "on", "effort": "low", "preserve": True,
        "source": (
            "huggingface.co/Qwen/Qwen3.8-27B model card's thinking line; reasoning effort low "
            "ruled by this project for speed (formerly carried through --chat-template-kwargs, "
            "now the first-class --reasoning-effort flag)"
        ),
    }


def test_card_no_thinking_on_unmeasured_rows():
    """long (both registries) carry no thinking object yet -- the measured
    bounded-budget arm is not a ruled row, and card must not invent one."""
    for f, names in ((PROFILES_JSON, ("long",)), (PROFILES_INFERENCE_JSON, ("long",))):
        for name in names:
            result = run("card", "--file", str(f), "--name", name)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout).get("thinking") is None, name


def test_check_passes_both_shipped_registries_thinking():
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        result = run("check", "--file", str(f))
        assert result.returncode == 0, result.stderr


def test_no_row_asks_for_host_ram():
    """This is a tripwire and not a gate: the harness's only host-memory check is a page-cache floor
    before the load, it cannot refuse a start whose weights will not fit in RAM, so until it can,
    nothing shipped may ask for that. Every row in both config/profiles.json and
    config/profiles.inference.json must have ram_gb_extra == 0 and no extra string containing
    --n-cpu-moe or -ncmoe."""
    for fpath in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(fpath.read_text(encoding="utf-8"))
        for name, prof in data["profiles"].items():
            assert prof.get("ram_gb_extra") == 0, f"{fpath}: {name}: ram_gb_extra must be 0"
            extra = prof.get("extra", "")
            assert "--n-cpu-moe" not in extra and "-ncmoe" not in extra, \
                f"{fpath}: {name}: extra must not contain --n-cpu-moe or -ncmoe"
