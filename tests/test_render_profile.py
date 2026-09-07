#!/usr/bin/env python3
"""Tests for the opencode profile renderer."""

import json
import re
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "config" / "opencode.profile.template.jsonc"
RENDERER = Path(__file__).resolve().parents[1] / "harness" / "render_profile.py"

# Default prompt text (copied verbatim from the brief)
DEFAULT = (
    "You are a coding agent working inside a git worktree of a Python project. Use the tools to read, search (grep/glob), "
    "edit and run commands. Read the brief you are pointed at, locate code with grep before reading whole files, "
    "make the change, run the exact test command given, and report the result. Be concise; do not narrate plans."
)

# Common arguments for all tests
COMMON_ARGS = [
    "render",
    "--template", str(TEMPLATE),
    "--out", "{out}",
    "--baseurl", "http://127.0.0.1:8093/v1",
    "--apikey", "k-test",
    "--ctx", "81920",
    "--output", "4096",
    "--name", "fast",
]


def strip_comments(text: str) -> str:
    """Remove lines whose lstrip() starts with //."""
    lines = text.splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("//"))


def render_to(out: Path) -> subprocess.CompletedProcess:
    """Render the profile to the given output path."""
    cmd = [sys.executable, str(RENDERER)] + COMMON_ARGS + ["--out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_renders_valid_json(tmp_path):
    """Test 1: rendered output is valid JSON with correct values."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    text = strip_comments(out.read_text())
    parsed = json.loads(text)
    
    assert parsed["provider"]["local-a770"]["options"]["apiKey"] == "k-test"
    assert parsed["provider"]["local-a770"]["models"]["local-builder"]["limit"]["context"] == 81920


def test_no_placeholder_left(tmp_path):
    """Test 2: no placeholder patterns remain in the output."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    text = out.read_text()
    assert re.search(r"__[A-Z]+__", text) is None, "Unreplaced placeholder found"


def test_matches_plain_substitution(tmp_path):
    """Test 3: rendered output matches plain string substitution."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    text = TEMPLATE.read_text()
    expected = (
        text
        .replace("__BASEURL__", "http://127.0.0.1:8093/v1")
        .replace("__APIKEY__", "k-test")
        .replace("__CTX__", "81920")
        .replace("__OUTPUT__", "4096")
        .replace("__NAME__", "fast")
        .replace("__PROMPT__", json.dumps(DEFAULT)[1:-1])
        .replace("__EDIT_RULES__", '"*": "allow"')
        .replace("__BASH_ALLOW__", "")
    )
    
    assert out.read_text() == expected


def test_default_prompt_and_edit_rules(tmp_path):
    """Test 4: parsed JSON has correct prompt and edit rules."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    text = strip_comments(out.read_text())
    parsed = json.loads(text)
    
    assert parsed["agent"]["local-builder"]["prompt"] == DEFAULT
    assert parsed["permission"]["edit"] == {"*": "allow"}


def test_floor_renders_after_every_allow(tmp_path):
    """Test 5: bash floor (deny rules) appears after allow rules."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    text = out.read_text()
    
    # Find the bash object (from first "bash": { to next }\n    })
    bash_match = re.search(r'"bash": \{([^}]*(?:\}[^}]*)?)\n    \}', text, re.DOTALL)
    assert bash_match is not None, "Could not find bash object"
    
    bash_content = bash_match.group(1)
    
    # Find the index of the last "allow" in the bash object
    last_allow = bash_content.rfind('"allow"')
    assert last_allow != -1, "No 'allow' found in bash object"
    
    # Find the index of the first "git commit*" (floor key)
    floor_key = '"git commit*"'
    floor_index = bash_content.find(floor_key)
    assert floor_index != -1, "Floor key 'git commit*' not found"
    
    # Floor key should be after the last allow
    assert floor_index > last_allow, "Floor key should appear after all allow rules"
    
    # Verify floor deny rules are present
    assert '"git commit*": "deny"' in bash_content
    assert '"git push*": "deny"' in bash_content
    assert '"curl*": "deny"' in bash_content
    assert '"systemctl*": "deny"' in bash_content


def test_output_mode_600(tmp_path):
    """Test 6: output file has mode 0600."""
    out = tmp_path / "out.jsonc"
    result = render_to(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"
    
    mode = oct(out.stat().st_mode & 0o777)
    assert mode == "0o600", f"Expected mode 0o600, got {mode}"
