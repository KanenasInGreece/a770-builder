#!/usr/bin/env python3
"""Tests for the profiles registry: config/registry/<card>.<mode>.json + harness/profiles.py."""

import json
import tempfile
import shutil
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES_JSON = ROOT / "config" / "registry" / "a770.display.json"
PROFILES_INFERENCE_JSON = ROOT / "config" / "registry" / "a770.inference.json"
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
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["kv"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())
    assert "missing key kv" in result.stderr


def test_check_fails_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["bogus"] = 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: unknown key bogus" in result.stderr


def test_check_fails_useful_ctx_over_ctx(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["useful_ctx"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["ctx"] + 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_bad_kv(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kv"] = "q9_9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_bad_kv_v(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kv_v"] = "q9_9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kv_v must be one of f16, bf16, fp8_e4m3, q8_0, q4_0" in result.stderr


def test_check_fails_bad_mode(tmp_path):
    data = load_base()
    data["mode"] = "bogus"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: mode: must be display or inference" in result.stderr


def test_check_fails_sampling_not_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = "hot"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling must be an object" in result.stderr


def test_check_fails_sampling_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"source": "x", "bogus": 1}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling: unknown key bogus" in result.stderr


def test_check_fails_sampling_key_not_number(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"source": "x", "temperature": True}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling.temperature must be a number" in result.stderr


def test_check_fails_sampling_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"source": "x", "mode": "bogus"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling.mode must be thinking or instruct" in result.stderr


def test_check_fails_sampling_missing_source(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"temperature": 0.6}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling.source must be a non-empty string" in result.stderr


def test_check_fails_sampling_empty_source(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"source": "", "top_k": 20}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling.source must be a non-empty string" in result.stderr


def test_check_fails_sampling_temperature_without_extra_temp(tmp_path):
    """The honesty check: a sampling.temperature with no --temp in extra is refused."""
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["sampling"] = {"source": "test card", "temperature": 0.6}
    assert "--temp" not in data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["extra"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: gemma4-8b-e4b-q4km-vulkan: sampling.temperature is set but extra carries no --temp" in result.stderr


def test_check_passes_valid_sampling(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["extra"] = "--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0,
        "mode": "thinking", "source": "Qwen3.5-9B model card",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_env_temperature_top_p_from_thinking_on_shipped_display_registry():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TEMPERATURE:=0.6}"' in result.stdout.splitlines()
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TOP_P:=0.95}"' in result.stdout.splitlines()


def test_env_temperature_from_sampling(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["extra"] = "--temp 0.6"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"temperature": 0.6, "source": "test card"}
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TEMPERATURE:=0.6}"' in result.stdout.splitlines()


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
    data["profiles"]["Long1"] = data["profiles"].pop("qwen35-9b-q4km-vulkan")
    data["default"] = "Long1"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_check_fails_speed_missing_100k(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["decode_tps"]["100k"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert any(line.startswith("profiles: ") for line in result.stderr.splitlines())


def test_env_matches_expected_lines():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    assert lines[0] == ': "${A770B_PROFILES:=qwen35-9b-q4km-vulkan gemma4-8b-e4b-q4km-vulkan qwen38-27b-iq3xxs-vulkan}"'
    assert not any("A770B_DEFAULT_PROFILE" in ln for ln in lines)

    expected_long_block = [
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_MODEL:=Qwen3.5-9B-Q4_K_M.gguf}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_CTX:=262144}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_KV:=q8_0}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_KV_V:=q8_0}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TEMPERATURE:=0.6}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TOP_P:=0.95}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_OUTPUT_TOKENS:=}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_REASONING:=on}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_MODE:=on}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_EFFORT:=low}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET:=8192}"',
        "[ -n \"${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE=''",
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_PRESERVE:=true}"',
        ': "${A770B_QWEN35_9B_Q4KM_VULKAN_TIMEOUT:=1500}"',
        "[ -n \"${A770B_QWEN35_9B_Q4KM_VULKAN_EXTRA:-}\" ] || A770B_QWEN35_9B_Q4KM_VULKAN_EXTRA='--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0'",
    ]
    idx = lines.index(expected_long_block[0])
    assert lines[idx: idx + len(expected_long_block)] == expected_long_block

    expected_serious_extra = (
        '[ -n "${A770B_QWEN38_27B_IQ3XXS_VULKAN_EXTRA:-}" ] || A770B_QWEN38_27B_IQ3XXS_VULKAN_EXTRA='
        "'--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0'"
    )
    assert expected_serious_extra in lines
    expected_serious_thinking = [
        ': "${A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_MODE:=on}"',
        ': "${A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_EFFORT:=low}"',
        ': "${A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_BUDGET:=2048}"',
        "[ -n \"${A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_BUDGET_MESSAGE=''",
        ': "${A770B_QWEN38_27B_IQ3XXS_VULKAN_THINKING_PRESERVE:=true}"',
    ]
    for line in expected_serious_thinking:
        assert line in lines


def test_env_roundtrips_extra_and_survives_preset(tmp_path):
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr

    base_data = load_base()
    expected_extra = base_data["profiles"]["qwen38-27b-iq3xxs-vulkan"]["extra"]

    script = result.stdout + '\necho "$A770B_QWEN38_27B_IQ3XXS_VULKAN_EXTRA"\n'
    bash_result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert bash_result.returncode == 0, bash_result.stderr
    assert bash_result.stdout.strip() == expected_extra

    env = os.environ.copy()
    env["A770B_QWEN35_9B_Q4KM_VULKAN_CTX"] = "1"
    script2 = result.stdout + '\necho "$A770B_QWEN35_9B_Q4KM_VULKAN_CTX"\n'
    bash_result2 = subprocess.run(["bash", "-c", script2], capture_output=True, text=True, env=env)
    assert bash_result2.returncode == 0, bash_result2.stderr
    assert bash_result2.stdout.strip() == "1"


def test_env_kv_v_defaults_to_kv_and_can_be_set(tmp_path):
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_KV_V:=q8_0}"' in result.stdout.splitlines()

    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kv_v"] = "f16"
    path = write_json(tmp_path / "p.json", data)
    result2 = run("env", "--file", str(path))
    assert result2.returncode == 0, result2.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_KV_V:=f16}"' in result2.stdout.splitlines()


def test_env_output_is_valid_shell():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    check = subprocess.run(["bash", "-n"], input=result.stdout, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_card_has_three_profiles():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data["profiles"].keys()) == {"qwen35-9b-q4km-vulkan", "gemma4-8b-e4b-q4km-vulkan", "qwen38-27b-iq3xxs-vulkan"}


def test_card_carries_mode_and_registry():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "display"
    assert data["registry"] == str(PROFILES_JSON.resolve())

    result = run("card", "--file", str(PROFILES_JSON), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "display"
    assert data["registry"] == str(PROFILES_JSON.resolve())


def test_inference_registry_shape():
    data = json.loads(PROFILES_INFERENCE_JSON.read_text(encoding="utf-8"))
    assert data["mode"] == "inference"
    assert "default" not in data
    for name in data["profiles"]:
        assert NAME_RE.match(name), name


def test_card_name_fast():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "gemma4-8b-e4b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["name"] == "gemma4-8b-e4b-q4km-vulkan"


def test_card_unknown_name():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "nonexistent")
    assert result.returncode == 2
    assert "profiles: no profile nonexistent" in result.stderr


def test_card_served_overrides_without_changing_model():
    env = os.environ.copy()
    env["A770B_QWEN38_27B_IQ3XXS_VULKAN_MODEL"] = "x.gguf"
    result = run("card", "--file", str(PROFILES_JSON), "--served", env=env)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    serious = data["profiles"]["qwen38-27b-iq3xxs-vulkan"]
    assert serious["served_model"] == "x.gguf"
    assert serious["model"] == "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf"


def test_card_without_served_leaves_defaults():
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    long_prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert long_prof["served_model"] == long_prof["model"]
    assert long_prof["served_ctx"] == long_prof["ctx"]


def test_render_installed_names_only_that_cards_rows():
    result = run("render", "--installed", "--card", "a770", "--mode", "display")
    assert result.returncode == 0, result.stderr
    assert "qwen35-9b-q4km-vulkan" in result.stdout
    assert "gemma4-8b-e4b-q4km-vulkan" in result.stdout
    assert "qwen38-27b-iq3xxs-vulkan" in result.stdout
    # the inference-only rows must not leak into the display card's block
    assert "qwen38-27b-iq3s-sycl" not in result.stdout
    assert "qwen38-27b-iq3s-vulkan" not in result.stdout


def test_render_installed_prints_the_one_line_when_the_card_has_no_rows():
    result = run("render", "--installed", "--card", "b70", "--mode", "display")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no measured models for card b70 in display mode — climb one with ladder.sh <gguf> --ctx N"


def _registry_names() -> set[str]:
    names = set()
    for f in (ROOT / "config" / "registry").glob("*.json"):
        names |= set(json.loads(f.read_text(encoding="utf-8"))["profiles"])
    return names


def test_tracked_skill_and_snippet_present_no_model_list():
    """I6 — the tracked SKILL.md and CONSTITUTION_SNIPPET.md name no model and no card: they are copied into
    every agent, so they tell the caller to run local-build.sh menu instead. The snippet keeps an empty marker
    pair where the local sync pastes `render --installed` for the host's card."""
    skill = (ROOT / "skills" / "local-build" / "SKILL.md").read_text(encoding="utf-8")
    snippet = (ROOT / "skills" / "local-build" / "CONSTITUTION_SNIPPET.md").read_text(encoding="utf-8")
    card_name = re.compile(r"(?<![A-Za-z0-9_])(A770|B70|B580)(?![A-Za-z0-9_])", re.I)
    for label, text in (("SKILL.md", skill), ("CONSTITUTION_SNIPPET.md", snippet)):
        for name in _registry_names():
            assert name not in text, f"{label} names the profile {name}"
        assert not card_name.search(text), f"{label} names a card: {card_name.search(text).group(0)}"
        assert "local-build.sh menu" in text
    begin = "<!-- local-build:installed:begin -->"
    end = "<!-- local-build:installed:end -->"
    lines = snippet.splitlines()
    assert begin in lines and end in lines
    assert lines.index(end) == lines.index(begin) + 1, "the tracked installed block must stay empty"


def _temp_project(tmp_path: Path, registries: dict[str, dict] | None = None) -> Path:
    """A temp project with a copy of cards.json, README.md, config/models.md and the given registry files
    (default: copies of the tracked ones), so rendering never writes the tracked tree."""
    root = tmp_path / "proj"
    (root / "config" / "registry").mkdir(parents=True)
    for rel in ("config/cards.json", "README.md", "config/models.md"):
        shutil.copy(ROOT / rel, root / rel)
    if registries is None:
        for f in (ROOT / "config" / "registry").glob("*.json"):
            shutil.copy(f, root / "config" / "registry" / f.name)
    else:
        for name, data in registries.items():
            (root / "config" / "registry" / name).write_text(json.dumps(data), encoding="utf-8")
    return root


def test_render_catalogue_is_byte_stable_and_lists_every_card(tmp_path):
    """I7 — render --catalogue writes the table between the markers in README and config/models.md, lists every
    card's rows with card and measured_on, sorted card -> mode -> name, and is byte-stable: a render of the
    tracked registries reproduces the tracked documents exactly, and a second render changes nothing."""
    root = _temp_project(tmp_path)
    result = run("render", "--catalogue", "--root", str(root))
    assert result.returncode == 0, result.stderr
    assert (root / "README.md").read_bytes() == (ROOT / "README.md").read_bytes(), "tracked README is stale"
    assert (root / "config" / "models.md").read_bytes() == (ROOT / "config" / "models.md").read_bytes()
    first = (root / "README.md").read_bytes()
    assert run("render", "--catalogue", "--root", str(root)).returncode == 0
    assert (root / "README.md").read_bytes() == first

    text = first.decode("utf-8")
    section = text[text.index("<!-- catalogue:begin -->"):text.index("<!-- catalogue:end -->")]
    assert "| card | mode | profile | model |" in section and "| measured_on |" in section
    data_lines = [ln for ln in section.splitlines() if ln.startswith("| a770 |") or ln.startswith("| b70 |")]
    triples = [tuple(cell.strip() for cell in ln.strip("|").split("|")[:3]) for ln in data_lines]
    assert triples == sorted(triples)
    assert len(data_lines) == len(_registry_names_by_file())
    # every row carries its own measured_on in the last cell (a blank cell is a failure)
    for (card, mode, name), ln in zip(triples, data_lines):
        measured = _registry_names_by_file()[(card, mode, name)]["measured_on"]
        assert ln.rstrip().rstrip("|").rsplit("|", 1)[-1].strip() == measured, ln


def _registry_names_by_file() -> dict[tuple, dict]:
    rows = {}
    for f in (ROOT / "config" / "registry").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for name, prof in d["profiles"].items():
            rows[(d["card"], d["mode"], name)] = prof
    return rows


def test_render_catalogue_refuses_an_invalid_registry(tmp_path):
    """F8 — the catalogue is never rendered from a file check refuses, and the documents stay untouched."""
    root = _temp_project(tmp_path)
    (root / "config" / "registry" / "b70.inference.json").write_text("{ not json", encoding="utf-8")
    before = (root / "README.md").read_bytes()
    result = run("render", "--catalogue", "--root", str(root))
    assert result.returncode == 2
    assert "b70.inference.json" in result.stderr
    assert (root / "README.md").read_bytes() == before


def test_render_installed_refuses_an_invalid_registry(tmp_path):
    """F8 — render --installed validates its file the way check does."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["card"] = "b70"   # a row of another card in the a770 file
    root = _temp_project(tmp_path, {"a770.display.json": data})
    result = run("render", "--installed", "--root", str(root), "--card", "a770", "--mode", "display")
    assert result.returncode == 2
    assert "card 'b70' differs from the file's 'a770'" in result.stderr, result.stderr


def test_render_installed_never_prints_another_cards_rows(tmp_path):
    """F9 — with two cards' files present, the installed block for one names none of the other's rows."""
    a770 = load_base()
    b70 = json.loads(json.dumps(a770))
    b70["card"] = "b70"
    first_name, first = next(iter(b70["profiles"].items()))
    first = dict(first, card="b70", measured_on="Arc Pro B70, test build")
    b70["profiles"] = {"beta-9b-q4km-vulkan": first}
    if b70.get("default") is not None:
        b70["default"] = "beta-9b-q4km-vulkan"
    root = _temp_project(tmp_path, {"a770.display.json": a770, "b70.display.json": b70})
    assert run("check", "--root", str(root)).returncode == 0, run("check", "--root", str(root)).stderr
    result = run("render", "--installed", "--root", str(root), "--card", "b70", "--mode", "display")
    assert result.returncode == 0, result.stderr
    assert "beta-9b-q4km-vulkan" in result.stdout
    for name in a770["profiles"]:
        assert name not in result.stdout, f"the b70 block leaked the a770 row {name}"
    result = run("render", "--installed", "--root", str(root), "--card", "a770", "--mode", "display")
    assert "beta-9b-q4km-vulkan" not in result.stdout


def test_render_installed_refuses_a_crafted_card_or_mode():
    """F2 — the card and mode become a path, so they are refused unless they look like a card id and a mode."""
    for card, mode in (("../x", "display"), ("a/b", "display"), ("", "display"), ("a 7", "display"), ("a770", "bogus")):
        result = run("render", "--installed", "--card", card, "--mode", mode)
        assert result.returncode == 2, (card, mode, result.stdout)


def test_check_refuses_unknown_top_level_key(tmp_path):
    """An unknown key at the top level is refused like one inside a profile."""
    d = json.loads(PROFILES_JSON.read_text()); d["extra_top"] = 1
    f = tmp_path / "p.json"; f.write_text(json.dumps(d))
    r = subprocess.run([sys.executable, str(PROFILES_PY), "check", "--file", str(f)], capture_output=True, text=True)
    assert r.returncode == 2 and "unknown key extra_top" in r.stderr


def test_card_served_ctx_is_an_int(tmp_path):
    """A served window from the environment comes back as a number, like the file's."""
    env = dict(os.environ); env["A770B_QWEN35_9B_Q4KM_VULKAN_CTX"] = "131072"
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--name", "qwen35-9b-q4km-vulkan", "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["served_ctx"] == 131072


def test_card_warns_on_inverted_profiles():
    """A builder.env written for an earlier release, with fast and long's files swapped, is caught."""
    env = dict(os.environ)
    env["A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_MODEL"] = "Qwen3.5-9B-Q4_K_M.gguf"
    env["A770B_QWEN35_9B_Q4KM_VULKAN_MODEL"] = "gemma-4-E4B-it-Q4_K_M.gguf"
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    warnings = json.loads(r.stdout)["warnings"]
    assert len(warnings) == 2
    assert warnings[0].startswith("profile qwen35-9b-q4km-vulkan serves gemma4-8b-e4b-q4km-vulkan's file (gemma-4-E4B-it-Q4_K_M.gguf)")
    assert warnings[1].startswith("profile gemma4-8b-e4b-q4km-vulkan serves qwen35-9b-q4km-vulkan's file (Qwen3.5-9B-Q4_K_M.gguf)")


def test_card_no_warnings_when_clean():
    """With no A770B_*_MODEL or A770B_*_CTX overrides, the card carries no warnings."""
    env = {
        k: v for k, v in os.environ.items()
        if not (k.startswith("A770B_") and (k.endswith("_MODEL") or k.endswith("_CTX")))
    }
    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["warnings"] == []

    r = subprocess.run([sys.executable, str(PROFILES_PY), "card", "--file", str(PROFILES_JSON), "--served", "--name", "qwen35-9b-q4km-vulkan"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["warnings"] == []


# --- U2d: category, weight_class, speed.far_end, fit, suite, output_tokens, builder_class ---


def test_check_fails_missing_category(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key category" in result.stderr


def test_check_fails_bad_category(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["category"] = "small"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: category must be dense or moe" in result.stderr


def test_check_fails_missing_weight_class(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key weight_class" in result.stderr


def test_check_fails_bad_weight_class(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"] = "9"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: weight_class must look like 9b, 35b-a3b or 8b-e4b" in result.stderr


def test_check_passes_weight_class_shapes(tmp_path):
    for shape in ("9b", "12b", "27b", "35b-a3b", "8b-e4b"):
        data = load_base()
        data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"] = shape
        # long's new weight_class may now collide with fast's or serious's own (category,
        # weight_class) -- move those out of the way so this test stays about weight_class's
        # shape, not the registry's one-row-per-class rule (covered separately).
        data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = "moe"
        data["profiles"]["qwen38-27b-iq3xxs-vulkan"]["category"] = "moe"
        path = write_json(tmp_path / "p.json", data)
        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{shape}: {result.stderr}"


def test_check_fails_missing_card(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["card"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key card" in result.stderr


def test_check_fails_missing_backend(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["backend"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key backend" in result.stderr


def test_check_fails_missing_mode(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["mode"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "missing key mode" in result.stderr


def test_check_fails_uppercase_card(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["card"] = "A770"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_uppercase_backend(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["backend"] = "Vulkan"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["mode"] = "gpu"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2


def test_check_fails_row_mode_disagrees_with_file(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["mode"] = "inference"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: mode 'inference' disagrees with file mode 'display'" in result.stderr


def test_check_fails_far_end_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["far_end"] = {"tokens": 100000, "decode_tps": 11.6, "prefill_tps": 147}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.far_end must be an object with tokens, decode_tps, prefill_tps, ttft_s" in result.stderr


def test_check_fails_far_end_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["far_end"] = {
        "tokens": 100000, "decode_tps": 11.6, "prefill_tps": 147, "ttft_s": 626, "bogus": 1,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.far_end must be an object with tokens, decode_tps, prefill_tps, ttft_s" in result.stderr


def test_check_fails_far_end_bad_type(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["far_end"] = {
        "tokens": "100000", "decode_tps": 11.6, "prefill_tps": 147, "ttft_s": 626,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.far_end.tokens must be a positive int" in result.stderr


def test_check_passes_valid_far_end(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["far_end"] = {
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
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = bench
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench must be an object with tool, prompt, gen, flags, at_depth, source" in result.stderr


def test_check_fails_bench_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(bogus=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench must be an object with tool, prompt, gen, flags, at_depth, source" in result.stderr


def test_check_fails_bench_empty_string_field(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(tool="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.tool must be a non-empty string" in result.stderr


def test_check_fails_bench_bad_prompt_type(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(prompt="8192")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.prompt must be a positive int" in result.stderr


def test_check_fails_bench_at_depth_not_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(at_depth=[])
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.at_depth must be a non-empty object" in result.stderr


def test_check_fails_bench_at_depth_bad_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(at_depth={"8k": {"pp": 1, "tg": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.at_depth: key '8k' must be a digit string" in result.stderr


def test_check_fails_bench_at_depth_value_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": 1, "tg": 1, "bogus": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.at_depth.0 must be an object with pp, tg" in result.stderr


def test_check_fails_bench_at_depth_value_bad_type(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": "1", "tg": 1}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.bench.at_depth.0.pp must be a number or null" in result.stderr


def test_check_passes_bench_at_depth_null_values(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench(at_depth={"0": {"pp": None, "tg": None}})
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_valid_bench(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench()
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_delivered_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {"prefill_tps": 500.0}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.delivered must be an object with prefill_tps, decode_tps, source" in result.stderr


def test_check_fails_delivered_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json", "bogus": 1,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.delivered must be an object with prefill_tps, decode_tps, source" in result.stderr


def test_check_fails_delivered_bad_value(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {
        "prefill_tps": -1.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.delivered.prefill_tps must be a positive number" in result.stderr


def test_check_fails_delivered_empty_source(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {"prefill_tps": 500.0, "decode_tps": 30.0, "source": ""}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: speed.delivered.source must be a non-empty string" in result.stderr


def test_check_passes_valid_delivered(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_bench_and_delivered_together(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["bench"] = _valid_bench()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


# The registry has ONE home for the suite's as-delivered medians, and the pair below says which: a row that
# carries them under `suite` — the shape harness/suite_report.py --json publishes, since its `delivered` object
# rides inside the totals — is refused, and the same row with them under `speed` checks out. That is why
# harness/ladder.sh moves the object out of the totals it assigns to the printed row's `suite` and into the row's
# `speed`: a freshly measured row is meant to be pasted into the registry unedited.


def _suite_totals(**overrides) -> dict:
    """The totals harness/suite_report.py --json prints, without `delivered`."""
    totals = {"briefs": 4, "runs": 4, "passed": 3, "timeouts": 0, "mean_wall_s": 210.5,
              "source": "long-suite-20260908-120000.json"}
    totals.update(overrides)
    return totals


def test_check_fails_delivered_under_suite(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = _suite_totals(
        delivered={"prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908-120000.json"},
    )
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite: unknown key delivered" in result.stderr


def test_check_passes_the_same_row_with_delivered_under_speed(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = _suite_totals()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["speed"]["delivered"] = {
        "prefill_tps": 500.0, "decode_tps": 30.0, "source": "long-suite-20260908-120000.json",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_fit_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["fit"] = {"code": "T1: 6 tests green in 122 s", "bogus": "x"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: fit: unknown key bogus" in result.stderr


def test_check_fails_fit_empty_string(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["fit"] = {"code": ""}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: fit.code must be a non-empty string" in result.stderr


def test_check_passes_valid_fit(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["fit"] = {"code": "T1: 6 tests green in 122 s"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_suite_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {"briefs": 5, "bogus": 1}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite: unknown key bogus" in result.stderr


def test_check_fails_suite_passed_out_of_range(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {"passed": 16, "runs": 15}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.passed must be between 0 and runs" in result.stderr


def test_check_fails_suite_timeouts_out_of_range(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {"briefs": 5, "runs": 15, "passed": 12, "timeouts": 16, "source": "s"}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.timeouts must be between 0 and runs" in result.stderr


def test_check_passes_valid_suite_with_timeouts(tmp_path):
    """timeouts (harness/suite_report.py's count of "timeout"-outcome stages) is optional, and a valid count
    between 0 and runs passes."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "timeouts": 2, "mean_wall_s": 120.5, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_valid_suite(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "mean_wall_s": 120.5, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_suite_missing_key(tmp_path):
    """briefs, runs, passed and source are required in a suite object; mean_wall_s is optional."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {"briefs": 5, "runs": 15, "passed": 12}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite: missing key source" in result.stderr


def test_check_passes_valid_suite_without_mean_wall_s(tmp_path):
    """mean_wall_s stays optional when the other four suite keys are present."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "in-house suite",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_sampling_top_p_without_extra_top_p(tmp_path):
    """The widened honesty check: a sampling.top_p with no --top-p in extra is refused."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["extra"] = "--temp 0.6"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["sampling"] = {"source": "test card", "temperature": 0.6, "top_p": 0.9}
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: sampling.top_p is set but extra carries no --top-p" in result.stderr


def test_output_tokens_in_env(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["output_tokens"] = 32768
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_OUTPUT_TOKENS:=32768}"' in result.stdout.splitlines()


def test_builder_class_reflects_the_suite_on_the_inference_registry():
    # the two 27B cards clear the suite (3/3 counted); the 9B does not (1/3), so it is no longer builder-class.
    for name in ("qwen38-27b-iq3s-vulkan", "qwen38-27b-iq3s-sycl"):
        result = run("card", "--file", str(PROFILES_INFERENCE_JSON), "--name", name)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["builder_class"] is True, name
    result = run("card", "--file", str(PROFILES_INFERENCE_JSON), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is False


def test_builder_class_ignores_useful_ctx_floor():
    """The window/speed floor is gone: a row with a green task is builder_class even below the old 81,920
    window -- speed and useful_ctx are recorded facts, not gates (the task is the organizing axis)."""
    result = run("card", "--file", str(PROFILES_JSON), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["useful_ctx"] == 65536
    assert data["builder_class"] is True


def test_builder_class_false_for_weak_task_t1():
    """The shipped display fast row: useful_ctx 100000 clears the floor but task_t1 is weak."""
    result = run("card", "--file", str(PROFILES_JSON), "--name", "gemma4-8b-e4b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["useful_ctx"] >= 81920
    assert data["task_t1"].startswith("weak:")
    assert data["builder_class"] is False


def test_builder_class_true_for_suite_pass_rate(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["useful_ctx"] = 100000
    data["profiles"]["qwen35-9b-q4km-vulkan"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {"passed": 12, "runs": 15}
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is True


def test_env_inference_registry_first_line():
    result = run("env", "--file", str(PROFILES_INFERENCE_JSON))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == ': "${A770B_PROFILES:=qwen35-9b-q4km-vulkan qwen38-27b-iq3s-vulkan qwen38-27b-iq3s-sycl}"'


# --- KU8: suite.stages, suite.reviewer, suite.instrument, profile-level instrument, comparable_with ---


def test_check_fails_missing_instrument(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key instrument" in result.stderr


def test_check_fails_empty_instrument(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = ""
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: instrument must be a non-empty string" in result.stderr


def test_check_fails_bad_instrument_type(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = 1
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: instrument must be a non-empty string" in result.stderr


def test_check_fails_suite_instrument_empty(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "kit", "instrument": "",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.instrument must be a non-empty string" in result.stderr


def test_check_passes_valid_suite_instrument(tmp_path):
    data = load_base()
    for _n in data["profiles"]:
        data["profiles"][_n]["instrument"] = "SUITE-1@0.2.0"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 5, "runs": 15, "passed": 12, "source": "kit", "instrument": "SUITE-1@0.2.0",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


# --- measured_on: comparable_with is declared across machines on instrument alone otherwise ---


def test_check_fails_missing_measured_on(tmp_path):
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["measured_on"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key measured_on" in result.stderr


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
    assert data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] == data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["instrument"]
    data["profiles"]["qwen35-9b-q4km-vulkan"]["measured_on"] = "RTX 4090 24 GB, llama.cpp b10805 CUDA"
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path))
    assert result.returncode == 0, result.stderr
    data_out = json.loads(result.stdout)
    assert data_out["profiles"]["qwen35-9b-q4km-vulkan"]["comparable_with"]["profiles"] == []
    assert "qwen35-9b-q4km-vulkan" not in data_out["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["comparable_with"]["profiles"]
    assert "qwen35-9b-q4km-vulkan" not in data_out["profiles"]["qwen38-27b-iq3xxs-vulkan"]["comparable_with"]["profiles"]


# --- registry-wide rules: one best row per (category, weight_class, backend), one instrument per registry ---


def test_check_fails_duplicate_category_weight_class(tmp_path):
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["weight_class"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: gemma4-8b-e4b-q4km-vulkan: task 'code-edit' + category 'dense' + weight_class '9b' + backend 'vulkan' on card "
        "'a770' + engine 'llama.cpp' + weight_quant 'Q4_K_M' + activation_quant 'none' + ram_gb_extra 0 "
        "duplicates qwen35-9b-q4km-vulkan's -- "
        "a registry keeps one best row per task per class per backend per card, and per engine, weight quant, activation quant and ram_gb_extra"
        in result.stderr
    )


def test_check_passes_same_class_when_only_ram_gb_extra_differs(tmp_path):
    """Two rows that share the one-best key except ram_gb_extra are both kept."""
    data = load_base()
    twin = data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]
    kept = data["profiles"]["qwen35-9b-q4km-vulkan"]
    twin["category"] = kept["category"]
    twin["weight_class"] = kept["weight_class"]
    twin["ram_gb_extra"] = kept["ram_gb_extra"] + 24
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_when_two_rows_both_lack_ram_gb_extra(tmp_path):
    """A missing ram_gb_extra is "" on the one-best key, so two omissions still collide."""
    data = load_base()
    twin = data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]
    kept = data["profiles"]["qwen35-9b-q4km-vulkan"]
    twin["category"] = kept["category"]
    twin["weight_class"] = kept["weight_class"]
    del twin["ram_gb_extra"]
    del kept["ram_gb_extra"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: gemma4-8b-e4b-q4km-vulkan: task 'code-edit' + category 'dense' + weight_class '9b' + backend 'vulkan' on card "
        "'a770' + engine 'llama.cpp' + weight_quant 'Q4_K_M' + activation_quant 'none' + ram_gb_extra '' "
        "duplicates qwen35-9b-q4km-vulkan's -- "
        "a registry keeps one best row per task per class per backend per card, and per engine, weight quant, activation quant and ram_gb_extra"
        in result.stderr
    )


def test_check_passes_same_class_different_weight_quant(tmp_path):
    """Q6_K and Q6_K-QKV8 of one 27B on one backend are two rows. The weight quant is the difference."""
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["weight_class"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["quant"] = "Q6_K-QKV8"
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["checkpoint"]["weight_quant"] = "Q6_K-QKV8"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_same_class_different_backend(tmp_path):
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["weight_class"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["backend"] = "sycl"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_on_both_shipped_registries_with_card_backend_mode():
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(f.read_text(encoding="utf-8"))
        file_mode = data["mode"]
        for name, prof in data["profiles"].items():
            assert prof["card"] == "a770", f"{f}: {name}: card"
            assert prof["backend"] in {"vulkan", "sycl"}, f"{f}: {name}: backend"
            assert prof["mode"] == file_mode, f"{f}: {name}: mode"
        result = run("check", "--file", str(f))
        assert result.returncode == 0, result.stderr


def test_env_exports_card_backend_mode():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_CARD:=a770" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_BACKEND:=vulkan" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_MODE:=display" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_ENGINE:=llama.cpp" in result.stdout

    b70 = ROOT / "config" / "registry" / "b70.inference.json"
    result = run("env", "--file", str(b70))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN38_27B_UDQ6K_SYCL_ENGINE:=llama.cpp" in result.stdout
    assert "A770B_QWEN38_27B_EXL3_4BPW_VLLM_ENGINE:=vllm" in result.stdout


def test_check_fails_differing_instrument_within_registry(tmp_path):
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["instrument"] = "a different rig"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: gemma4-8b-e4b-q4km-vulkan: instrument 'a different rig' differs from qwen35-9b-q4km-vulkan's "
        "'seat: Shared_Memory@3c8e2bb' -- a registry is one instrument" in result.stderr
    )


def test_check_fails_missing_task_on_a_kit_instrument(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = "SUITE-1@1"
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["task"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key task (required on a kit instrument)" in result.stderr


def test_check_passes_a_seat_row_without_task(tmp_path):
    """A legacy seat-instrument row is not forced to invent a task it never measured."""
    data = load_base()
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["task"]
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["evidence"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_bad_task(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["task"] = "vibe-coding"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: task 'vibe-coding' is not one of:" in result.stderr


def test_check_fails_missing_evidence_on_a_kit_instrument(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = "SUITE-1@1"
    del data["profiles"]["qwen35-9b-q4km-vulkan"]["evidence"]
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: missing key evidence (required on a kit instrument)" in result.stderr


def test_check_fails_empty_evidence(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["evidence"] = "  "
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: evidence must be a non-empty string" in result.stderr


def test_check_passes_two_tasks_on_the_same_class_and_backend(tmp_path):
    """The uniqueness axis is the task: two rows identical in class/backend/card and differing ONLY by task coexist.
    Without `task` in the key they would collide, so this is the mutation guard for that axis."""
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["weight_class"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["task"] = "code-read"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_two_cards_on_the_same_task_and_class(tmp_path):
    """The card is in the uniqueness key: two rows differing ONLY by card coexist (the mutation guard for card)."""
    data = load_base()
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["category"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["category"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["weight_class"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["weight_class"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["task"] = data["profiles"]["qwen35-9b-q4km-vulkan"]["task"]
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["card"] = "b70"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_suite_instrument_mismatching_the_row(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 3, "runs": 3, "passed": 3, "source": "kit", "instrument": "SUITE-1@9.9.9",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "suite.instrument 'SUITE-1@9.9.9' differs from the row's instrument" in result.stderr


def test_shipped_rows_carry_a_task_and_evidence():
    """Every shipped row names the task it competes in and the test that qualified it (the task is the axis)."""
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(f.read_text(encoding="utf-8"))
        for name, prof in data["profiles"].items():
            assert prof["task"] in {
                "code-edit", "code-read", "debug-test", "prose-docs", "os-ops",
                "data-structured", "instruction-agentic",
            }, f"{f}: {name}: task"
            assert isinstance(prof["evidence"], str) and prof["evidence"].strip(), f"{f}: {name}: evidence"


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
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": "nope",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages must be a list" in result.stderr


def test_check_fails_stage_not_an_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": ["nope"],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0] must be an object" in result.stderr


def test_check_fails_stage_unknown_key(tmp_path):
    data = load_base()
    stage = _valid_stage(bogus=1)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0]: unknown key bogus" in result.stderr


def test_check_fails_stage_missing_key(tmp_path):
    data = load_base()
    stage = _valid_stage()
    del stage["wall_s"]
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0]: missing key wall_s" in result.stderr


def test_check_fails_stage_bad_id(tmp_path):
    data = load_base()
    stage = _valid_stage(id="")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].id must be a non-empty string" in result.stderr


def test_check_fails_stage_bad_working(tmp_path):
    data = load_base()
    stage = _valid_stage(working="yes")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].working must be a bool" in result.stderr


def test_check_fails_stage_bad_conformance(tmp_path):
    data = load_base()
    stage = _valid_stage(conformance="pass")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].conformance must be a bool or null" in result.stderr


def test_check_passes_stage_conformance_null(tmp_path):
    data = load_base()
    stage = _valid_stage(conformance=None)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_stage_bad_lines(tmp_path):
    data = load_base()
    stage = _valid_stage(lines="40")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].lines must be an int or null" in result.stderr


def test_check_fails_stage_bad_budget_lines(tmp_path):
    data = load_base()
    stage = _valid_stage(budget_lines=60.5)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].budget_lines must be an int or null" in result.stderr


def test_check_fails_stage_bad_maintainable(tmp_path):
    data = load_base()
    stage = _valid_stage(maintainable=4)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].maintainable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bool_not_maintainable(tmp_path):
    """A bool is never 0, 3 or 5, even though False == 0 in Python."""
    data = load_base()
    stage = _valid_stage(maintainable=False)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].maintainable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bad_usable(tmp_path):
    data = load_base()
    stage = _valid_stage(usable=2)
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].usable must be 0, 3, 5 or null" in result.stderr


def test_check_fails_stage_bad_wall_s(tmp_path):
    data = load_base()
    stage = _valid_stage(wall_s="12.5")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].wall_s must be a number" in result.stderr


def test_check_fails_stage_bad_axes(tmp_path):
    data = load_base()
    stage = _valid_stage(axes=["working", 1])
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].axes must be a list of strings" in result.stderr


def test_check_passes_valid_stages_list(tmp_path):
    data = load_base()
    stages = [
        _valid_stage(id="s0-design", axes=["design"], working=True),
        _valid_stage(id="s1-frontend", axes=["working", "conformance"], working=True),
        _valid_stage(id="s4-rubric", axes=["maintainable", "usable"], working=False, maintainable=5, usable=5),
    ]
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = "SUITE-1@0.2.0"
    for _n in ("gemma4-8b-e4b-q4km-vulkan", "qwen38-27b-iq3xxs-vulkan"):
        data["profiles"][_n]["instrument"] = "SUITE-1@0.2.0"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 3, "runs": 3, "passed": 2, "source": "kit", "instrument": "SUITE-1@0.2.0", "stages": stages,
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_reviewer_not_an_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "reviewer": "nope",
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.reviewer must be an object" in result.stderr


def test_check_fails_reviewer_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {"profile": "qwen35-9b-q4km-vulkan", "bogus": 1},
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.reviewer: unknown key bogus" in result.stderr


def test_check_fails_reviewer_bad_value_type(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {"profile": "qwen35-9b-q4km-vulkan", "rubric_sha256": True},
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.reviewer.rubric_sha256 must be a string or int" in result.stderr


def test_check_passes_valid_reviewer(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit",
        "reviewer": {
            "profile": "qwen38-27b-iq3xxs-vulkan", "model": "Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf", "ctx": 32768,
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
    data["profiles"]["qwen35-9b-q4km-vulkan"]["useful_ctx"] = 100000
    data["profiles"]["qwen35-9b-q4km-vulkan"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 4, "runs": 4, "passed": 3, "source": "kit",
        "stages": [
            _valid_stage(id="s0-design", axes=["maintainable", "usable"], working=True, counts_toward_pass=False),
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s3-optimise", axes=["maintainable"], working=True, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is True


def test_builder_class_false_from_stages_when_counted_ratio_low(tmp_path):
    """Same counts_toward_pass-driven tally, the other direction: two counted stages, one
    failing, is a 0.5 ratio -- below the 0.8 bar."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["useful_ctx"] = 100000
    data["profiles"]["qwen35-9b-q4km-vulkan"]["task_t1"] = "weak: not the row's own task"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 2, "runs": 2, "passed": 1, "source": "kit",
        "stages": [
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=True, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=False, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is False


def test_builder_class_false_when_suite_fails_despite_task_t1_pass(tmp_path):
    """The other bug: an old `task_t1: "pass"` used to short-circuit builder_class to True
    even when `suite` recorded failures. The suite is now the authority whenever it is
    present -- a failing suite makes the row false no matter what task_t1 says."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["useful_ctx"] = 100000
    assert data["profiles"]["qwen35-9b-q4km-vulkan"]["task_t1"] == "pass"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 4, "runs": 4, "passed": 0, "source": "kit",
        "stages": [
            _valid_stage(id="s0-design", axes=["maintainable", "usable"], working=True, counts_toward_pass=False),
            _valid_stage(id="s1-frontend", axes=["maintainable", "usable"], working=False, counts_toward_pass=True),
            _valid_stage(id="s2-backend", axes=["maintainable"], working=False, counts_toward_pass=True),
            _valid_stage(id="s3-optimise", axes=["maintainable"], working=False, counts_toward_pass=True),
        ],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["builder_class"] is False


def test_check_fails_stage_bad_counts_toward_pass(tmp_path):
    data = load_base()
    stage = _valid_stage(counts_toward_pass="yes")
    data["profiles"]["qwen35-9b-q4km-vulkan"]["suite"] = {
        "briefs": 1, "runs": 1, "passed": 1, "source": "kit", "stages": [stage],
    }
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: suite.stages[0].counts_toward_pass must be a bool" in result.stderr


def test_comparable_with_on_display_registry():
    """The shipped display registry: every row lists the other two as comparable (same instrument)."""
    result = run("card", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for name in ("qwen35-9b-q4km-vulkan", "gemma4-8b-e4b-q4km-vulkan", "qwen38-27b-iq3xxs-vulkan"):
        others = sorted(n for n in ("qwen35-9b-q4km-vulkan", "gemma4-8b-e4b-q4km-vulkan", "qwen38-27b-iq3xxs-vulkan") if n != name)
        assert sorted(data["profiles"][name]["comparable_with"]["profiles"]) == others, name
        assert data["profiles"][name]["comparable_with"]["instrument"] == data["profiles"][name]["instrument"]
        assert data["profiles"][name]["comparable_with"]["measured_on"] == data["profiles"][name]["measured_on"]


def test_comparable_with_on_inference_registry():
    """The shipped inference registry holds two rows, so each lists the one other row as comparable
    (same instrument)."""
    result = run("card", "--file", str(PROFILES_INFERENCE_JSON))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for name in ("qwen35-9b-q4km-vulkan", "qwen38-27b-iq3s-vulkan"):
        others = sorted(n for n in ("qwen35-9b-q4km-vulkan", "qwen38-27b-iq3s-vulkan") if n != name)
        assert sorted(data["profiles"][name]["comparable_with"]["profiles"]) == others, name
        assert data["profiles"][name]["comparable_with"]["instrument"] == data["profiles"][name]["instrument"]
        assert data["profiles"][name]["comparable_with"]["measured_on"] == data["profiles"][name]["measured_on"]


def test_comparable_with_excludes_differing_instrument(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["instrument"] = "SUITE-1@0.2.0"
    path = write_json(tmp_path / "p.json", data)

    result = run("card", "--file", str(path))
    assert result.returncode == 0, result.stderr
    data_out = json.loads(result.stdout)
    assert data_out["profiles"]["qwen35-9b-q4km-vulkan"]["comparable_with"]["profiles"] == []
    assert "qwen35-9b-q4km-vulkan" not in data_out["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["comparable_with"]["profiles"]
    assert "qwen35-9b-q4km-vulkan" not in data_out["profiles"]["qwen38-27b-iq3xxs-vulkan"]["comparable_with"]["profiles"]


def test_card_prints_instrument():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "qwen35-9b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["instrument"] == "seat: Shared_Memory@3c8e2bb"


# --- thinking: the server's five thinking controls (--reasoning, --reasoning-effort,
# --reasoning-budget, --reasoning-budget-message, --reasoning-preserve) as a profile field ---


def _valid_thinking(**overrides) -> dict:
    # mode "on" matches the reasoning row the tests mutate (the shipped 9B is reasoning: on since
    # 2026-09-15), so a caller testing an unrelated field (budget, effort, source, ...) does not
    # incidentally trip the mode-vs-reasoning contradiction rule; tests of that rule override mode explicitly.
    thinking = {
        "mode": "on", "effort": "low", "budget": 512, "budget_message": "budget spent, answer now",
        "preserve": True, "source": "test card",
    }
    thinking.update(overrides)
    return thinking


def test_check_fails_thinking_not_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = "hot"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking must be an object" in result.stderr


def test_check_fails_thinking_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(bogus=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking: unknown key bogus" in result.stderr


def test_check_fails_thinking_bad_mode(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(mode="maybe")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.mode must be on, off or auto" in result.stderr


def test_check_fails_thinking_bad_effort(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(effort="extreme")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.effort must be one of low, medium, high, xhigh, default" in result.stderr


def test_check_fails_thinking_effort_not_string(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(effort=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.effort must be one of low, medium, high, xhigh, default" in result.stderr


def test_check_fails_thinking_budget_not_int(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget="512")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.budget must be -1, 0 or a positive int" in result.stderr


def test_check_fails_thinking_budget_below_minus_one(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget=-2)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.budget must be -1, 0 or a positive int" in result.stderr


def test_check_passes_thinking_budget_minus_one_and_zero(tmp_path):
    for b in (-1, 0, 512):
        data = load_base()
        data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget=b)
        path = write_json(tmp_path / "p.json", data)

        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"budget={b}: {result.stderr}"


def test_check_fails_thinking_budget_message_not_string(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget_message=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.budget_message must be a non-empty string" in result.stderr


def test_check_fails_thinking_budget_message_empty(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget_message="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.budget_message must be a non-empty string" in result.stderr


def test_check_fails_thinking_preserve_not_bool(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(preserve="yes")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.preserve must be a bool" in result.stderr


def test_check_fails_thinking_source_empty(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(source="")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.source must be a non-empty string" in result.stderr


def test_check_fails_thinking_source_not_string(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(source=1)
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: thinking.source must be a non-empty string" in result.stderr


def test_check_passes_thinking_every_key_optional(tmp_path):
    """Every thinking key is optional: an empty object, and a single-key object, both pass."""
    for partial in ({}, {"mode": "auto"}, {"preserve": False}, {"source": "x"}):
        data = load_base()
        data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = partial
        path = write_json(tmp_path / "p.json", data)

        result = run("check", "--file", str(path))
        assert result.returncode == 0, f"{partial}: {result.stderr}"


def test_check_passes_valid_thinking(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking()
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_thinking_mode_on_contradicts_reasoning_off(tmp_path):
    """the E4B ships with reasoning: off -- a thinking.mode of on contradicts it."""
    data = load_base()
    assert data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["reasoning"] == "off"
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["thinking"] = _valid_thinking(mode="on")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: gemma4-8b-e4b-q4km-vulkan: thinking.mode 'on' contradicts reasoning 'off'" in result.stderr


def test_check_fails_thinking_mode_contradicts_reasoning_off(tmp_path):
    """serious ships with reasoning: on -- a thinking.mode of off contradicts it."""
    data = load_base()
    assert data["profiles"]["qwen38-27b-iq3xxs-vulkan"]["reasoning"] == "on"
    data["profiles"]["qwen38-27b-iq3xxs-vulkan"]["thinking"]["mode"] = "off"
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen38-27b-iq3xxs-vulkan: thinking.mode 'off' contradicts reasoning 'on'" in result.stderr


def test_check_passes_thinking_mode_auto_never_contradicts(tmp_path):
    """auto is compatible with either reasoning value -- it is not a fixed state."""
    for name in ("qwen35-9b-q4km-vulkan", "qwen38-27b-iq3xxs-vulkan"):
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
    data["profiles"]["gemma4-8b-e4b-q4km-vulkan"]["thinking"] = _valid_thinking(mode="off")
    path = write_json(tmp_path / "p.json", data)

    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_env_thinking_empty_when_unset():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert ': "${A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_MODE:=}"' in lines
    assert ': "${A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_EFFORT:=}"' in lines
    assert ': "${A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_BUDGET:=}"' in lines
    assert "[ -n \"${A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE:-}\" ] || A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE=''" in lines
    assert ': "${A770B_GEMMA4_8B_E4B_Q4KM_VULKAN_THINKING_PRESERVE:=}"' in lines


def test_env_thinking_set_from_registry(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(
        mode="on", budget=0, budget_message="stop thinking, it's time",
    )
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_MODE:=on}"' in lines
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_EFFORT:=low}"' in lines
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET:=0}"' in lines
    assert (
        "[ -n \"${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE:-}\" ] || "
        "A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE='stop thinking, it'\\''s time'"
    ) in lines
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_PRESERVE:=true}"' in lines


def test_env_thinking_output_is_valid_shell(tmp_path):
    """A budget_message with an embedded single quote (an apostrophe, plausible in real prose)
    must round-trip through the shell unbroken."""
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget_message="don't stop, it's fine")
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    script = result.stdout + '\necho "$A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET_MESSAGE"\n'
    bash_result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert bash_result.returncode == 0, bash_result.stderr
    assert bash_result.stdout.strip() == "don't stop, it's fine"


def test_env_thinking_negative_one_budget(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["thinking"] = _valid_thinking(budget=-1)
    path = write_json(tmp_path / "p.json", data)

    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert ': "${A770B_QWEN35_9B_Q4KM_VULKAN_THINKING_BUDGET:=-1}"' in result.stdout.splitlines()


def test_card_prints_thinking_as_stored():
    result = run("card", "--file", str(PROFILES_JSON), "--name", "qwen38-27b-iq3xxs-vulkan")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["thinking"] == {
        "mode": "on", "effort": "low", "budget": 2048, "preserve": True,
        "source": (
            "huggingface.co/Qwen/Qwen3.8-27B model card's thinking line; reasoning effort low "
            "ruled by this project for speed (formerly carried through --chat-template-kwargs, "
            "now the first-class --reasoning-effort flag); a 2048-token reasoning budget ruled "
            "2026-09-13 — unbudgeted, the 27B spent 15,415 generated tokens in one reply and never "
            "reached a tool call, so a delegated build timed out with a 0-line patch; budgeted, it "
            "delivered the gpu-hang-check fix 13/13 (verify PASS)"
        ),
    }


def test_card_no_thinking_on_unmeasured_rows():
    """the E4B carries no thinking object -- it is measured non-thinking, and card must not invent one."""
    result = run("card", "--file", str(PROFILES_JSON), "--name", "gemma4-8b-e4b-q4km-vulkan")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout).get("thinking") is None


def test_check_passes_both_shipped_registries_thinking():
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        result = run("check", "--file", str(f))
        assert result.returncode == 0, result.stderr


# Placement flags refused in a row's `extra`: nothing shipped may put a layer or an expert in host RAM. Matched
# at a word boundary followed by whitespace, "=" or the end of the string, so "-ngl=50" and "-ot=blk.*=CPU" are
# caught exactly like their space-separated forms ("-ngl 50", "-ot blk.*=CPU") — llama.cpp accepts both.
_HOST_RAM_FLAG_RE = re.compile(r"(?:^|\s)(?:-ot|--override-tensor|--cpu-moe|--n-cpu-moe|-ncmoe)(?=\s|=|$)")
_HOST_RAM_NGL_RE = re.compile(r"(?:^|\s)(?:-ngl|--n-gpu-layers)(?:\s+|=)(\d+)")


def _extra_asks_for_host_ram(extra: str) -> str | None:
    """None when `extra` places every layer and expert on the GPU; else a reason naming what it asked for."""
    m = _HOST_RAM_FLAG_RE.search(extra)
    if m:
        return f"a placement flag ({m.group(0).strip()!r})"
    m = _HOST_RAM_NGL_RE.search(extra)
    if m and int(m.group(1)) < 99:
        return f"-ngl/--n-gpu-layers below 99 ({m.group(1)})"
    return None


def test_extra_asks_for_host_ram_matcher():
    """The matcher `test_no_row_asks_for_host_ram` uses must catch the '=' forms llama.cpp accepts alongside the
    space-separated ones, and must leave an unrelated flag or a full-offload -ngl alone."""
    bad = ["-ngl=50", "-ngl 50", "--n-gpu-layers=10", "-ot=blk.*=CPU", "--cpu-moe", "--n-cpu-moe=4"]
    for extra in bad:
        assert _extra_asks_for_host_ram(extra) is not None, f"{extra!r} should be refused"
    good = ["--temp 1.0", "-ngl 99", "--no-mmap"]
    for extra in good:
        assert _extra_asks_for_host_ram(extra) is None, f"{extra!r} should be accepted"


def test_no_row_asks_for_host_ram():
    """This is a tripwire and not a gate: the harness's only host-memory check is a page-cache floor
    before the load, it cannot refuse a start whose weights will not fit in RAM, so until it can,
    nothing shipped may ask for that. It must test placement, not ram_gb_extra == 0: llama.cpp keeps
    host buffers by design (the server log's *_Host ... buffer size lines, at -lv 4, are the measure
    of that, not a sign of CPU-resident layers or experts), so every row in every registry file must
    have a non-negative ram_gb_extra and no extra string that places any layer or expert in host RAM
    (--override-tensor, -ot, --cpu-moe, --n-cpu-moe, -ncmoe, or an -ngl/--n-gpu-layers value below 99;
    see _extra_asks_for_host_ram for the matcher, which also catches the '=' forms of these flags)."""
    for fpath in (ROOT / "config" / "registry").glob("*.json"):
        data = json.loads(fpath.read_text(encoding="utf-8"))
        for name, prof in data["profiles"].items():
            ram = prof.get("ram_gb_extra")
            assert isinstance(ram, (int, float)) and not isinstance(ram, bool) and ram >= 0, \
                f"{fpath}: {name}: ram_gb_extra must be a number >= 0"
            reason = _extra_asks_for_host_ram(prof.get("extra", ""))
            assert reason is None, f"{fpath}: {name}: extra asks for host RAM: {reason}"


def test_kit_instrument_uses_kit_version_not_product_version(tmp_path):
    sys.path.insert(0, str(ROOT / "harness"))
    from profiles import kit_instrument
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"suite": "SUITE-1", "kit_version": "1"}), encoding="utf-8")
    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    assert kit_instrument(suite) == "SUITE-1@1"


def test_kit_instrument_refuses_missing_kit_version(tmp_path):
    sys.path.insert(0, str(ROOT / "harness"))
    from profiles import kit_instrument
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"suite": "SUITE-1"}), encoding="utf-8")
    try:
        kit_instrument(suite)
    except ValueError as e:
        assert "kit_version" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_shipped_kit_instrument_is_suite_at_kit_version():
    sys.path.insert(0, str(ROOT / "harness"))
    from profiles import kit_instrument
    product = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0]
    got = kit_instrument()
    assert got.startswith("SUITE-1@")
    assert not got.endswith("@" + product)


def test_check_fails_bad_placement(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["placement"] = "disk"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: placement must be host, container or remote" in result.stderr


def test_check_passes_placement_host(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["placement"] = "host"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


# --- W3a: operator_override ---


def test_check_passes_operator_override_true(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["operator_override"] = True
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_operator_override_non_bool(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["operator_override"] = "yes"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: operator_override must be true or false" in result.stderr


# --- optional engine / checkpoint / kernel (omitted = untested) ---


def _gguf_checkpoint(quant="Q4_K_M"):
    return {"format": "gguf", "weight_quant": quant, "activation_quant": "none"}


def _gpu_kernel():
    return {"path": "gpu", "xmx": "unavailable", "evidence": "llama-bench tg@0 stayed GPU-class while VRAM occupied"}


def test_check_passes_when_engine_checkpoint_kernel_are_omitted(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof.pop("engine", None)
    prof.pop("checkpoint", None)
    prof.pop("kernel", None)
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_bad_engine(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["engine"] = "ollama"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: engine must be llama.cpp, vllm, or sglang" in result.stderr


def test_check_passes_sglang_on_sglang_backend(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["engine"] = "sglang"
    prof["backend"] = "sglang"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "qwen3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_sglang_without_parsers(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["engine"] = "sglang"
    prof["backend"] = "sglang"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "tool_parser is required when backend is sglang" in result.stderr
    assert "reasoning_parser is required when backend is sglang" in result.stderr


def test_check_fails_parser_auto_by_name(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["engine"] = "sglang"
    prof["backend"] = "sglang"
    prof["tool_parser"] = "auto"
    prof["reasoning_parser"] = "auto"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "tool_parser must not be auto" in result.stderr
    assert "reasoning_parser must not be auto" in result.stderr
    assert "must match ^[a-z0-9_]+$" not in result.stderr


# --- tool_parser on vllm or sglang; reasoning_parser on sglang ---


def test_check_passes_tool_parser_on_a_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["reasoning"] == "on"
    assert prof["thinking"]["mode"] == "on"
    prof["backend"] = "vllm"
    prof["reasoning"] = "off"
    prof["thinking"]["mode"] = "off"
    prof["tool_parser"] = "qwen3_coder"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_tool_parser_on_a_non_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["backend"] == "vulkan"
    prof["tool_parser"] = "qwen3_coder"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: qwen35-9b-q4km-vulkan: tool_parser is only meaningful when backend is vllm or sglang "
        "(this row's backend is 'vulkan')" in result.stderr
    )


def test_check_fails_malformed_tool_parser(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["tool_parser"] = "bad;value"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: tool_parser must match ^[a-z0-9_]+$" in result.stderr


def test_check_passes_when_tool_parser_is_omitted(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"].pop("tool_parser", None)
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_env_exports_tool_parser(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["reasoning"] = "off"
    prof["thinking"]["mode"] = "off"
    prof["tool_parser"] = "qwen3_coder"
    path = write_json(tmp_path / "p.json", data)
    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_TOOL_PARSER:=qwen3_coder" in result.stdout


def test_env_exports_empty_tool_parser_when_absent():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_TOOL_PARSER:=}" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_REASONING_PARSER:=}" in result.stdout


def test_env_exports_reasoning_parser_and_quant_for_sglang(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "sglang"
    prof["engine"] = "sglang"
    prof["quant"] = "exl3"
    prof["checkpoint"]["weight_quant"] = "exl3"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "qwen3"
    path = write_json(tmp_path / "p.json", data)
    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_TOOL_PARSER:=qwen3_coder" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_REASONING_PARSER:=qwen3" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_QUANT:=exl3" in result.stdout


def test_check_fails_reasoning_parser_on_a_non_vllm_non_sglang_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["backend"] == "vulkan"
    prof["reasoning_parser"] = "qwen3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert (
        "profiles: qwen35-9b-q4km-vulkan: reasoning_parser is only meaningful when backend is vllm or sglang "
        "(this row's backend is 'vulkan')" in result.stderr
    )


def test_check_fails_reasoning_parser_on_vllm(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["reasoning"] == "on"
    assert prof["thinking"]["mode"] == "on"
    prof["backend"] = "vllm"
    prof["reasoning"] = "off"
    prof["thinking"]["mode"] = "off"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "qwen3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "reasoning_parser is refused when the vLLM reasoning switch is off" in result.stderr
    assert "reasoning_parser is only meaningful when backend is sglang" not in result.stderr


def test_check_passes_reasoning_parser_on_vllm_when_the_switch_is_on(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["thinking"]["mode"] == "on"
    prof["backend"] = "vllm"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "qwen3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_thinking_mode_auto_on_vllm(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["tool_parser"] = "qwen3_coder"
    prof["thinking"]["mode"] = "auto"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "thinking.mode auto is refused when backend is vllm" in result.stderr
    assert "reasoning_parser is only meaningful when backend is sglang" not in result.stderr


def test_check_fails_vllm_reasoning_on_without_a_parser(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["thinking"]["mode"] == "on"
    assert "reasoning_parser" not in prof
    prof["backend"] = "vllm"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "reasoning_parser is required when the vLLM reasoning switch is on" in result.stderr
    assert "reasoning_parser is required when backend is sglang" not in result.stderr


def test_check_fails_vllm_reasoning_parser_auto_or_malformed(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "auto"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "reasoning_parser must not be auto" in result.stderr
    assert "must match ^[a-z0-9_]+$" not in result.stderr
    assert "reasoning_parser is only meaningful when backend is sglang" not in result.stderr

    prof["reasoning_parser"] = "bad;value"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "reasoning_parser must match ^[a-z0-9_]+$" in result.stderr
    assert "reasoning_parser is only meaningful when backend is sglang" not in result.stderr


def test_check_passes_bf16_kv_on_a_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["reasoning"] = "off"
    prof["thinking"]["mode"] = "off"
    prof["kv"] = "bf16"
    prof["kv_v"] = "bf16"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_bf16_kv_on_a_non_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["backend"] == "vulkan"
    prof["kv"] = "bf16"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kv bf16 is only recorded when backend is vllm" in result.stderr


def _sglang_row(prof):
    prof["backend"] = "sglang"
    prof["engine"] = "sglang"
    prof["tool_parser"] = "qwen3_coder"
    prof["reasoning_parser"] = "qwen3"


def test_check_passes_fp8_e4m3_kv_on_sglang_when_the_flag_matches(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    _sglang_row(prof)
    prof["kv"] = "fp8_e4m3"
    prof["extra"] = prof["extra"] + " --kv-cache-dtype fp8_e4m3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_fp8_e4m3_kv_when_backend_is_not_sglang(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    assert prof["backend"] == "vulkan"
    prof["kv"] = "fp8_e4m3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kv fp8_e4m3 is only recorded when backend is sglang" in result.stderr


def test_check_fails_when_sglang_kv_disagrees_with_kv_cache_dtype(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    _sglang_row(prof)
    prof["kv"] = "f16"
    prof["extra"] = prof["extra"] + " --kv-cache-dtype fp8_e4m3"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kv 'f16' disagrees with --kv-cache-dtype fp8_e4m3" in result.stderr


def test_check_fails_vllm_image_on_a_non_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["vllm_image"] = "exl3xpu:b70-15ded2f"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "vllm_image is only meaningful when backend is vllm" in result.stderr


def test_env_exports_quant_and_image_for_a_vllm_row(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["backend"] = "vllm"
    prof["reasoning"] = "off"
    prof["thinking"]["mode"] = "off"
    prof["quant"] = "exl3"
    prof["checkpoint"]["weight_quant"] = "exl3"
    prof["vllm_image"] = "exl3xpu:b70-15ded2f"
    path = write_json(tmp_path / "p.json", data)
    result = run("env", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_QUANT:=exl3" in result.stdout
    assert "A770B_QWEN35_9B_Q4KM_VULKAN_VLLM_IMAGE:=exl3xpu:b70-15ded2f" in result.stdout


def test_env_omits_quant_unless_backend_is_vllm():
    result = run("env", "--file", str(PROFILES_JSON))
    assert result.returncode == 0, result.stderr
    assert "_QUANT:=" not in result.stdout


def test_render_prints_em_dash_when_8k_decode_is_null(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["speed"]["decode_tps"]["8k"] = None
    root = _temp_project(tmp_path, {"a770.display.json": data})
    result = run("render", "--catalogue", "--root", str(root))
    assert result.returncode == 0, result.stderr
    text = (root / "README.md").read_text(encoding="utf-8")
    section = text[text.index("<!-- catalogue:begin -->"):text.index("<!-- catalogue:end -->")]
    assert "— / " in section
    installed = run("render", "--installed", "--root", str(root), "--card", "a770", "--mode", "display")
    assert installed.returncode == 0, installed.stderr
    assert "8k decode not measured" in installed.stdout


def test_check_fails_checkpoint_not_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = "gguf"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint must be an object" in result.stderr


def test_check_fails_checkpoint_unknown_key(tmp_path):
    data = load_base()
    ck = _gguf_checkpoint()
    ck["bits"] = 4
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = ck
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint: unknown key bits" in result.stderr


def test_check_fails_checkpoint_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = {"format": "gguf", "weight_quant": "Q4_K_M"}
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint: missing key activation_quant" in result.stderr


def test_check_fails_bad_checkpoint_format(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = {
        "format": "exl2", "weight_quant": "Q4_K_M", "activation_quant": "none",
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint.format must be one of gguf, gptq, awq, safetensors" in result.stderr


def test_check_fails_empty_weight_quant(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = {
        "format": "gguf", "weight_quant": "", "activation_quant": "none",
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint.weight_quant must be a non-empty string" in result.stderr


def test_check_fails_bad_activation_quant(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = {
        "format": "gguf", "weight_quant": "Q4_K_M", "activation_quant": "w4a16",
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint.activation_quant must be one of none, fp16, bf16, int8, int4" in result.stderr


def test_check_fails_gguf_weight_quant_mismatching_quant(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = _gguf_checkpoint("Q6_K")
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "checkpoint.weight_quant 'Q6_K' differs from quant 'Q4_K_M'" in result.stderr


def test_check_passes_weight_only_gguf_checkpoint(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["engine"] = "llama.cpp"
    prof["checkpoint"] = _gguf_checkpoint(prof["quant"])
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_w4a16_without_matching_quant(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["engine"] = "vllm"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["checkpoint"] = {
        "format": "gptq", "weight_quant": "int4", "activation_quant": "fp16",
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_passes_checkpoint_with_optional_evidence(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["checkpoint"] = _gguf_checkpoint(prof["quant"])
    prof["checkpoint"]["evidence"] = "/path/to/serve.evidence.json"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_checkpoint_evidence_not_string(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["checkpoint"] = _gguf_checkpoint(prof["quant"])
    prof["checkpoint"]["evidence"] = 123
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint.evidence must be a non-empty string" in result.stderr


def test_check_fails_checkpoint_evidence_empty(tmp_path):
    data = load_base()
    prof = data["profiles"]["qwen35-9b-q4km-vulkan"]
    prof["checkpoint"] = _gguf_checkpoint(prof["quant"])
    prof["checkpoint"]["evidence"] = ""
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: checkpoint.evidence must be a non-empty string" in result.stderr


def test_check_fails_kernel_not_object(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = "gpu"
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel must be an object" in result.stderr


def test_check_fails_kernel_unknown_key(tmp_path):
    data = load_base()
    k = _gpu_kernel()
    k["dtype"] = "int4"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = k
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel: unknown key dtype" in result.stderr


def test_check_fails_kernel_missing_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = {"path": "gpu", "xmx": "unavailable"}
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel: missing key evidence" in result.stderr


def test_check_fails_bad_kernel_path(tmp_path):
    data = load_base()
    k = _gpu_kernel()
    k["path"] = "xmx"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = k
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel.path must be gpu, cpu-like or untested" in result.stderr


def test_check_fails_bad_kernel_xmx(tmp_path):
    data = load_base()
    k = _gpu_kernel()
    k["xmx"] = "maybe"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = k
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel.xmx must be used, unavailable or untested" in result.stderr


def test_check_fails_empty_kernel_evidence(tmp_path):
    data = load_base()
    k = _gpu_kernel()
    k["evidence"] = "   "
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = k
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "profiles: qwen35-9b-q4km-vulkan: kernel.evidence must be a non-empty string" in result.stderr


def test_check_passes_kernel_when_fully_specified(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["kernel"] = _gpu_kernel()
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_shipped_rows_name_weight_only_gguf_and_omit_kernel():
    """Measured rows record llama.cpp + GGUF weight-only; kernel stays omitted until measured."""
    for f in (PROFILES_JSON, PROFILES_INFERENCE_JSON):
        data = json.loads(f.read_text(encoding="utf-8"))
        for name, prof in data["profiles"].items():
            assert prof.get("engine") == "llama.cpp", f"{f}: {name}: engine"
            ck = prof.get("checkpoint")
            assert isinstance(ck, dict), f"{f}: {name}: checkpoint"
            assert ck["format"] == "gguf", f"{f}: {name}: checkpoint.format"
            assert ck["weight_quant"] == prof["quant"], f"{f}: {name}: checkpoint.weight_quant"
            assert ck["activation_quant"] == "none", f"{f}: {name}: checkpoint.activation_quant"
            assert "kernel" not in prof, f"{f}: {name}: kernel must stay omitted until measured"


# --- I4: a registry file under config/registry/ is checked on its identity, not only its keys ---


def _write_registry_file(name: str, data) -> Path:
    """Write `data` as a registry file under a temp project's config/registry/ (with a copy of the real
    config/cards.json), so the identity rules run without writing the tracked tree. Check it with
    `--root <temp project>`: `_root(path)`."""
    root = Path(tempfile.mkdtemp(prefix="registry-identity-"))
    (root / "config" / "registry").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "cards.json", root / "config" / "cards.json")
    path = root / "config" / "registry" / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _root(path: Path) -> str:
    return str(path.parents[2])


def test_check_refuses_registry_filename_card_mismatch():
    data = load_base()
    path = _write_registry_file("b70.display.json", data)
    try:
        result = run("check", "--root", _root(path), "--file", str(path))
        assert result.returncode == 2
        assert "card 'a770' disagrees with the file name 'b70'" in result.stderr
    finally:
        path.unlink(missing_ok=True)


def test_check_refuses_registry_filename_mode_mismatch():
    data = load_base()
    data["card"] = "b70"
    for name in data["profiles"]:
        data["profiles"][name]["card"] = "b70"
    path = _write_registry_file("b70.inference.json", data)
    try:
        result = run("check", "--root", _root(path), "--file", str(path))
        assert result.returncode == 2
        assert "mode 'display' disagrees with the file name 'inference'" in result.stderr
    finally:
        path.unlink(missing_ok=True)


def test_check_refuses_a_card_not_in_cards_json():
    data = load_base()
    data["card"] = "zebra"
    path = _write_registry_file("zebra.display.json", data)
    try:
        result = run("check", "--root", _root(path), "--file", str(path))
        assert result.returncode == 2
        assert "card 'zebra' is not in config/cards.json" in result.stderr
    finally:
        path.unlink(missing_ok=True)


def test_check_refuses_a_row_card_differing_from_the_files():
    data = load_base()
    data["card"] = "b70"
    path = _write_registry_file("b70.display.json", data)
    try:
        result = run("check", "--root", _root(path), "--file", str(path))
        assert result.returncode == 2
        assert "card 'a770' differs from the file's 'b70'" in result.stderr
    finally:
        path.unlink(missing_ok=True)


def test_check_refuses_a_row_mode_differing_from_the_files():
    data = load_base()
    data["card"] = "b70"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["card"] = "b70"
    data["profiles"]["qwen35-9b-q4km-vulkan"]["mode"] = "inference"
    path = _write_registry_file("b70.display.json", data)
    try:
        result = run("check", "--root", _root(path), "--file", str(path))
        assert result.returncode == 2
        assert "mode 'inference' differs from the file's 'display'" in result.stderr
    finally:
        path.unlink(missing_ok=True)


def test_check_refuses_a_duplicate_profile_name(tmp_path):
    """A duplicate key inside `profiles` is a duplicate name; json.loads keeps the last, so only a
    raw parse can see the earlier one."""
    path = tmp_path / "dup.json"
    path.write_text(
        '{"schema":1,"card":"a770","mode":"display","profiles":{"x":{"card":"a770","mode":"display"},"x":{"card":"a770","mode":"display"}}}',
        encoding="utf-8",
    )
    result = run("check", "--root", _root(path), "--file", str(path))
    assert result.returncode == 2
    assert "duplicate key 'x'" in result.stderr


def test_check_checks_every_registry_file_without_a_file_argument():
    result = run("check")
    assert result.returncode == 0, result.stderr


def test_empty_line_names_the_card_and_mode():
    result = run("empty-line", "--card", "b70", "--mode", "display")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no measured models for card b70 in display mode — climb one with ladder.sh <gguf> --ctx N"


def test_empty_line_names_the_missing_card():
    result = run("empty-line")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no builder card set (A770B_CARD) — climb one with ladder.sh <gguf> --ctx N"


def test_check_passes_measured_tasks(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["measured_tasks"] = {
        "s2-backend": {"quality": "pass", "wall_s": 900, "context_tokens": 4000, "thinking": "off"},
        "t0-slow": {"quality": "fail", "wall_s": 2, "context_tokens": None, "thinking": "on"},
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 0, result.stderr


def test_check_fails_measured_tasks_unknown_key(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["measured_tasks"] = {
        "s2-backend": {"quality": "pass", "wall_s": 900, "context_tokens": 4000, "thinking": "off", "note": "x"},
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "unknown key note" in result.stderr


def test_check_fails_measured_tasks_zero_context(tmp_path):
    data = load_base()
    data["profiles"]["qwen35-9b-q4km-vulkan"]["measured_tasks"] = {
        "s2-backend": {"quality": "pass", "wall_s": 900, "context_tokens": 0, "thinking": "off"},
    }
    path = write_json(tmp_path / "p.json", data)
    result = run("check", "--file", str(path))
    assert result.returncode == 2
    assert "context_tokens must be a positive int or null" in result.stderr


def test_check_accepts_source_that_is_the_word_local(tmp_path):
    """A row may record source as the word local. The published flashnext row is still checked on its own."""
    data = load_base()
    row = data["profiles"]["qwen35-9b-q4km-vulkan"]
    row["source"] = "https://example.com/local-build"
    path = write_json(tmp_path / "url.json", data)
    ok = run("check", "--file", str(path))
    assert ok.returncode == 0, ok.stderr

    row["source"] = "local"
    path = write_json(tmp_path / "word.json", data)
    word = run("check", "--file", str(path))
    assert word.returncode == 0, word.stderr


B70_JSON = ROOT / "config" / "registry" / "b70.inference.json"
B70_KEPT = (
    "qwen38-27b-udq6k-sycl",
    "qwen38-27b-q6k-oq8-sycl",
    "qwen38-27b-q6k-qkv8-mtp-sycl",
    "qwen38-27b-exl3-4bpw-vllm",
)
RECIPE_URL = "https://github.com/sybil-solutions/qwen38-flash-next-b70-offload"
STRATA_URL = "https://github.com/Niko1221/Strata"
SERVED_DIGEST = "24c872759256d4b0d3b9d0419ebdf6f3547e0fdbd3891fbb9f90e32ad255c94f"


def test_b70_registry_keeps_four_rows_and_records_flashnext():
    data = json.loads(B70_JSON.read_text(encoding="utf-8"))
    names = list(data["profiles"])
    for kept in B70_KEPT:
        assert kept in names
    assert "flashnext" in names
    row = data["profiles"]["flashnext"]
    assert row["source"] == RECIPE_URL
    assert row["category"] == "moe"
    assert row["weight_class"] == "180b-a6b"
    assert row["task"] == "code-edit"
    assert row["engine"] == "sglang" and row["backend"] == "sglang"
    assert row["card"] == "b70" and row["mode"] == "inference"
    assert row["kv"] == "fp8_e4m3"
    assert "--kv-cache-dtype fp8_e4m3" in row["extra"]
    assert row["ctx"] == 131072 and row["useful_ctx"] == 100000
    assert row["timeout_s"] == 1500
    assert row["vram_gib_after_load"] == 31.52
    assert row["ram_gb_extra"] == 20
    assert row["depth_probe_100k"] == "pass (3/3)"
    assert row["capability"] == {}
    assert "different image" in row["capability_source"]
    assert "different machine" in row["capability_source"]
    assert "delivered" not in row["speed"]
    assert "kernel" not in row
    assert "sampling" not in row
    assert row["speed"]["decode_tps"] == {"8k": 10.5, "32k": 10.0, "64k": 10.5, "100k": 10.3}
    assert row["speed"]["prefill_tps"] == {"8k": 1665.2, "32k": 1013.6, "64k": 1340.2, "100k": 1219.6}
    assert row["speed"]["far_end"] == {"tokens": 99887, "decode_tps": 14.2, "prefill_tps": 1088, "ttft_s": 91.8}
    assert row["suite"]["briefs"] == 3 and row["suite"]["runs"] == 3 and row["suite"]["passed"] == 3
    assert row["suite"]["timeouts"] == 0 and row["suite"]["mean_wall_s"] == 1831.0
    assert row["suite"]["instrument"] == "SUITE-1@1"
    tasks = row["measured_tasks"]
    assert tasks["s1-frontend"]["wall_s"] == 2224 and tasks["s1-frontend"]["context_tokens"] == 503734
    assert tasks["s2-backend"]["wall_s"] == 1039 and tasks["s2-backend"]["context_tokens"] == 219758
    assert tasks["s3-optimise"]["wall_s"] == 2230 and tasks["s3-optimise"]["context_tokens"] == 670707
    assert "ref-cpp-example" not in tasks
    blob = json.dumps(row)
    assert SERVED_DIGEST in blob
    assert "30260" in row["evidence"] and "7890" in row["evidence"]
    assert "62.69" in row["evidence"] and "27.31" in row["evidence"]
    assert RECIPE_URL in row["evidence"] and "Strata" in row["evidence"] and STRATA_URL in row["evidence"]
    assert "flashnext-ram24" in names
    ram24 = data["profiles"]["flashnext-ram24"]
    for copied in (
        "model", "source", "family", "architecture", "quant", "extra",
        "tool_parser", "reasoning_parser", "checkpoint", "category", "weight_class",
        "card", "backend", "engine", "placement", "mode", "capability_source",
    ):
        assert ram24[copied] == row[copied], copied
    assert ram24["kv"] == "fp8_e4m3"
    assert ram24["flash_attention"] == "off" and ram24["reasoning"] == "off"
    assert ram24["capability"] == {}
    assert "kernel" not in ram24 and "sampling" not in ram24
    assert "delivered" not in ram24["speed"]
    assert ram24["measured_on"] == "Arc Pro B70 32 GB, SGLang attached, 2026-10-17"
    assert ram24["ctx"] == 131072 and ram24["useful_ctx"] == 100000
    assert ram24["timeout_s"] == 2283
    assert ram24["vram_gib_after_load"] == 31.19
    assert ram24["ram_gb_extra"] == 24
    assert ram24["params_b"] == {"total": 180, "active": 6}
    assert ram24["depth_probe_100k"] == "pass (2/3)"
    assert ram24["instrument"] == "SUITE-1@1" and ram24["task"] == "code-edit"
    assert ram24["speed"]["decode_tps"] == {"8k": 10.7, "32k": 9.8, "64k": 10.5, "100k": 10.0}
    assert ram24["speed"]["prefill_tps"] == {"8k": 4513.3, "32k": 1137.0, "64k": 1261.5, "100k": 1207.0}
    assert ram24["speed"]["far_end"] == {"tokens": 99887, "decode_tps": 12.9, "prefill_tps": 894, "ttft_s": 111.7}
    bench = ram24["speed"]["bench"]
    assert bench["tool"] == "llama-benchy" and bench["prompt"] == 8192 and bench["gen"] == 128
    assert bench["flags"] == row["speed"]["bench"]["flags"]
    assert bench["source"].startswith("http-20261017-042235.json via http-20261017-042235.mapped.json")
    assert "prefill 521" in bench["source"] and "decode 7.5" in bench["source"]
    assert "8192 cell is the other instrument" in bench["source"]
    assert bench["at_depth"] == {
        "8192": {"pp": 4513.3, "tg": 10.7},
        "32768": {"pp": 1137.0, "tg": 9.8},
        "65536": {"pp": 1261.5, "tg": 10.5},
        "100000": {"pp": 1207.0, "tg": 10.0},
    }
    use_for = ram24["use_for"]
    for phrase in (
        "131072", "100000", "7958", "7.5", "521", "99887", "12.9", "894",
        "99789", "2/3", "10.7", "9.8", "10.5", "10.0", "4513.3", "1137.0",
        "1261.5", "1207.0", "reference exercises failed",
    ):
        assert phrase in use_for, phrase
    assert "counted" in use_for and "passed" in use_for
    evidence = ram24["evidence"]
    for phrase in (
        "ghcr.io/0xsero/sglang-exl3-xpu-flashnext",
        SERVED_DIGEST,
        "7890",
        "40g",
        "shm 8g",
        "cpuset 8-23",
        "NV tier on",
        "victim write-back on",
        "RAM cap 24",
        "48 layers",
        "8000 MoE slots",
        "n-gram tier nvme",
        "KV bits 5",
        "QSA sycl_row",
        "62.69",
        "32 GiB",
        "27.31",
        "31.19",
        "2/3",
        "not measured",
        "flashnext-suite-20261017-015712-3927775.json",
        "s1-frontend 1633",
        "s2-backend 877",
        "s3-optimise 2283",
        "s0-design passed at 475",
        "not in the counted mean",
        "reference exercises failed",
        "timeout_s is 2283",
        "longest counted wall",
        RECIPE_URL,
        "(MIT)",
        "Strata",
        STRATA_URL,
        "published speeds are not this card",
    ):
        assert phrase in evidence, phrase
    assert "30260" not in evidence
    assert ram24["fit"]["code"] == (
        "SUITE-1@1: 3 of 3 counted stages passed, mean wall 1597.7 s. "
        "All three reference exercises failed (2026-10-17, attached server)."
    )
    assert ram24["fit"]["write"] == "not measured"
    assert ram24["suite"] == {
        "briefs": 3,
        "runs": 3,
        "passed": 3,
        "timeouts": 0,
        "mean_wall_s": 1597.7,
        "source": "flashnext-suite-20261017-015712-3927775.json",
        "instrument": "SUITE-1@1",
    }
    assert ram24["measured_tasks"] == {
        "s0-design": {"quality": "pass", "wall_s": 475, "context_tokens": 58787, "thinking": "off"},
        "s1-frontend": {"quality": "pass", "wall_s": 1633, "context_tokens": 263541, "thinking": "off"},
        "s2-backend": {"quality": "pass", "wall_s": 877, "context_tokens": 123593, "thinking": "off"},
        "s3-optimise": {"quality": "pass", "wall_s": 2283, "context_tokens": 662884, "thinking": "off"},
        "ref-cpp-binary-search-tree": {"quality": "fail", "wall_s": 1222, "context_tokens": 53011, "thinking": "off"},
        "ref-javascript-forth": {"quality": "fail", "wall_s": 45, "context_tokens": None, "thinking": "off"},
        "ref-python-pov": {"quality": "fail", "wall_s": 1703, "context_tokens": 66127, "thinking": "off"},
    }
    ram_blob = json.dumps(ram24)
    for stale in ("1665.2", "1013.6", "1340.2", "1219.6", "14.2", "1088", "91.8", "1831", "1500", "3/3"):
        assert stale not in ram_blob
    for banned in (
        "d30d" + "f691",
        "/home" + "/",
        "/xfs" + "/",
        "fact" + ":",
        "decision" + ":",
        "retro" + ":",
        "46" + ".22",
        "runtime copy",
        "24" + "g",
    ):
        assert banned not in blob
        assert banned not in ram_blob
    assert row["source"] != "local"
    assert ram24["source"] != "local"
    result = run("check", "--file", str(B70_JSON))
    assert result.returncode == 0, result.stderr
