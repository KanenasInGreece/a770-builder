#!/usr/bin/env python3
"""Tests for the profiles registry: config/profiles.json + harness/profiles.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES_JSON = ROOT / "config" / "profiles.json"
PROFILES_PY = ROOT / "harness" / "profiles.py"


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
        "**long** (default) = Qwen3.5-9B-Q4_K_M, 262,144-token window (useful to ~65k), ~37 tok/s; "
        "**--fast** = gemma-4-E4B-Q4_K_M, 131,072-token window (useful to ~100k), ~60 tok/s; "
        "**--serious** = Qwen3.8-27B-GSQ-RCO-IQ3_XXS, 131,072-token window (useful to ~32k), ~8 tok/s."
    )
    snippet_text = snippet.read_text(encoding="utf-8")
    assert expected_snippet_line in snippet_text
    assert "old snippet" not in snippet_text


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
    assert result.returncode == 0, result.stderr
    assert no_markers.read_text(encoding="utf-8") == original
    assert "no markers in" in result.stderr


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
