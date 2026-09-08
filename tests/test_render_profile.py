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


def write_spec(path: Path, data: dict) -> Path:
    """Write a run specification as JSON to `path` and return it."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def run_check(spec_path: Path, seat_path: Path) -> subprocess.CompletedProcess:
    """Run the `check` subcommand against a spec/seat pair."""
    cmd = [sys.executable, str(RENDERER), "check", "--spec", str(spec_path), "--seat", str(seat_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def render_with_spec(out: Path, spec_path: Path, seat_path: Path, echo: Path = None) -> subprocess.CompletedProcess:
    """Render the profile using a run specification and seat directory, optionally with --echo."""
    cmd = [sys.executable, str(RENDERER)] + COMMON_ARGS + [
        "--out", str(out), "--spec", str(spec_path), "--seat", str(seat_path),
    ]
    if echo is not None:
        cmd += ["--echo", str(echo)]
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
    assert re.search(r"__[A-Z][A-Z_]*__", text) is None, "Unreplaced placeholder found"


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
        .replace("__SAMPLING__", "")  # no --temperature/--top-p given
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


def render_with_sampling(out: Path, temperature: str = None, top_p: str = None, echo: Path = None) -> subprocess.CompletedProcess:
    """Render the profile with optional --temperature/--top-p (and optional --echo), no run specification."""
    cmd = [sys.executable, str(RENDERER)] + COMMON_ARGS + ["--out", str(out)]
    if temperature is not None:
        cmd += ["--temperature", temperature]
    if top_p is not None:
        cmd += ["--top-p", top_p]
    if echo is not None:
        cmd += ["--echo", str(echo)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_sampling_both_values_render_in_agent_block(tmp_path):
    """Test 25: --temperature and --top-p together render as one line in the agent block."""
    out = tmp_path / "out.jsonc"
    result = render_with_sampling(out, temperature="0.6", top_p="0.95")
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    text = out.read_text()
    assert '"temperature": 0.6, "top_p": 0.95,' in text
    assert "__SAMPLING__" not in text

    parsed = json.loads(strip_comments(text))
    assert parsed["agent"]["local-builder"]["temperature"] == 0.6
    assert parsed["agent"]["local-builder"]["top_p"] == 0.95


def test_sampling_temperature_only(tmp_path):
    """Test 26: --temperature alone renders just that key, with its trailing comma."""
    out = tmp_path / "out.jsonc"
    result = render_with_sampling(out, temperature="0.6")
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    text = out.read_text()
    assert '"temperature": 0.6,' in text

    parsed = json.loads(strip_comments(text))
    assert parsed["agent"]["local-builder"]["temperature"] == 0.6
    assert "top_p" not in parsed["agent"]["local-builder"]


def test_sampling_none_leaves_no_key_or_placeholder(tmp_path):
    """Test 27: with neither value given, the agent block carries no temperature/top_p key and no placeholder."""
    out = tmp_path / "out.jsonc"
    result = render_with_sampling(out)
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    text = out.read_text()
    assert "__SAMPLING__" not in text

    parsed = json.loads(strip_comments(text))
    assert "temperature" not in parsed["agent"]["local-builder"]
    assert "top_p" not in parsed["agent"]["local-builder"]


def test_sampling_bad_value_exits_2(tmp_path):
    """Test 28: an out-of-range --temperature or --top-p is refused."""
    out = tmp_path / "out.jsonc"
    result = render_with_sampling(out, temperature="3.0")
    assert result.returncode == 2
    assert "render_profile: bad --temperature/--top-p" in result.stderr

    out2 = tmp_path / "out2.jsonc"
    result2 = render_with_sampling(out2, top_p="0")
    assert result2.returncode == 2
    assert "render_profile: bad --temperature/--top-p" in result2.stderr


def test_sampling_echo_carries_values(tmp_path):
    """Test 29: the echo carries the temperature/top_p values (or null when absent)."""
    out = tmp_path / "out.jsonc"
    echo = tmp_path / "echo.json"
    result = render_with_sampling(out, temperature="0.6", top_p="0.95", echo=echo)
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    echo_data = json.loads(echo.read_text())
    assert echo_data["temperature"] == 0.6
    assert echo_data["top_p"] == 0.95

    out2 = tmp_path / "out2.jsonc"
    echo2 = tmp_path / "echo2.json"
    result2 = render_with_sampling(out2, echo=echo2)
    assert result2.returncode == 0, f"Render failed: {result2.stderr}"
    echo_data2 = json.loads(echo2.read_text())
    assert echo_data2["temperature"] is None
    assert echo_data2["top_p"] is None


def test_check_accepts_minimal_spec(tmp_path):
    """Test 7: an empty spec object passes check, printing nothing."""
    spec = write_spec(tmp_path / "spec.json", {})
    seat = tmp_path / "seat"
    seat.mkdir()

    result = run_check(spec, seat)
    assert result.returncode == 0, f"check failed: {result.stderr}"
    assert result.stdout == ""
    assert result.stderr == ""


def test_check_refuses_unknown_key(tmp_path):
    """Test 8: an unknown top-level key is refused, naming that key."""
    spec = write_spec(tmp_path / "spec.json", {"skills": []})
    seat = tmp_path / "seat"
    seat.mkdir()

    result = run_check(spec, seat)
    assert result.returncode == 2
    assert "skills" in result.stderr


def test_check_refuses_nested_unknown_key(tmp_path):
    """Test 9: an unknown key nested inside verify is refused, naming 'verify'."""
    spec = write_spec(tmp_path / "spec.json", {"verify": {"timeout": 5}})
    seat = tmp_path / "seat"
    seat.mkdir()

    result = run_check(spec, seat)
    assert result.returncode == 2
    assert "verify" in result.stderr


def test_bash_allow_renders_before_floor(tmp_path):
    """Test 10: a git-prefixed bash_allow pattern is refused; a plain one renders ahead of the floor."""
    seat = tmp_path / "seat"
    seat.mkdir()

    spec1 = write_spec(tmp_path / "spec1.json", {"bash_allow": ["git push *", "make check"]})
    out1 = tmp_path / "out1.jsonc"
    result1 = render_with_spec(out1, spec1, seat)
    assert result1.returncode == 2
    assert "bash_allow" in result1.stderr

    spec2 = write_spec(tmp_path / "spec2.json", {"bash_allow": ["make check"]})
    out2 = tmp_path / "out2.jsonc"
    result2 = render_with_spec(out2, spec2, seat)
    assert result2.returncode == 0, f"Render failed: {result2.stderr}"

    text = out2.read_text()
    bash_match = re.search(r'"bash": \{([^}]*(?:\}[^}]*)?)\n    \}', text, re.DOTALL)
    assert bash_match is not None, "Could not find bash object"
    bash_content = bash_match.group(1)

    idx_make = bash_content.index('"make check": "allow"')
    idx_commit_deny = bash_content.index('"git commit*": "deny"')
    idx_blame_allow = bash_content.index('"git blame*": "allow"')

    assert idx_make < idx_commit_deny, "bash_allow pattern must render before the floor"
    assert idx_make > idx_blame_allow, "bash_allow pattern must render after the base allow rules"


def test_bash_allow_refuses_wildcard_and_wrappers(tmp_path):
    """Test 11: a bare wildcard and shell/wrapper-prefixed patterns are all refused."""
    seat = tmp_path / "seat"
    seat.mkdir()

    patterns = [["*"], ["env X"], ["sh -c ls"], ["uv run x"], ["ls; rm"], ["a=b"], ["/bin/bash -c ls"], ["/usr/bin/env sh"], ["./bash x"], ["/usr/bin/git push"], ["git*"], ["Git push"], [" git push"], ["timeout 5 git push"], ["exec git push"], ["busybox wget x"], ["make  check"]]
    for i, p in enumerate(patterns):
        spec = write_spec(tmp_path / f"spec_{i}.json", {"bash_allow": p})
        result = run_check(spec, seat)
        assert result.returncode == 2, f"pattern {p!r} should be refused: {result.stderr}"


def test_card_from_seat_lands_in_prompt(tmp_path):
    """Test 12: a card file inside the seat becomes the rendered prompt; the echo reports it."""
    seat = tmp_path / "seat"
    seat.mkdir()
    card_text = 'Card text with "quotes" and\nnewline'
    (seat / "CARD.md").write_text(card_text, encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"card": "CARD.md"})
    out = tmp_path / "out.jsonc"
    echo = tmp_path / "echo.json"
    result = render_with_spec(out, spec, seat, echo=echo)
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    parsed = json.loads(strip_comments(out.read_text()))
    assert parsed["agent"]["local-builder"]["prompt"] == card_text

    echo_data = json.loads(echo.read_text())
    assert echo_data["card"]["chars"] == len(card_text)
    assert echo_data["card"]["source"] == "CARD.md"


def test_card_outside_seat_refused(tmp_path):
    """Test 13: a card path escaping the seat, and a card that is a symlink, are both refused."""
    seat = tmp_path / "seat"
    seat.mkdir()
    (tmp_path / "outside.md").write_text("nope", encoding="utf-8")

    spec1 = write_spec(tmp_path / "spec1.json", {"card": "../outside.md"})
    result1 = run_check(spec1, seat)
    assert result1.returncode == 2
    assert "card" in result1.stderr

    real_card = seat / "real.md"
    real_card.write_text("real card text", encoding="utf-8")
    (seat / "link.md").symlink_to(real_card)

    spec2 = write_spec(tmp_path / "spec2.json", {"card": "link.md"})
    result2 = run_check(spec2, seat)
    assert result2.returncode == 2
    assert "card" in result2.stderr


def test_card_over_limit_refused(tmp_path):
    """Test 14: inline card text over 8000 characters is refused."""
    seat = tmp_path / "seat"
    seat.mkdir()
    spec = write_spec(tmp_path / "spec.json", {"card": {"text": "x" * 8001}})

    result = run_check(spec, seat)
    assert result.returncode == 2
    assert "card" in result.stderr


def test_scope_edit_renders_deny_then_allow(tmp_path):
    """Test 15: scope.edit renders a deny-all floor followed by an allow per glob, in order."""
    seat = tmp_path / "seat"
    seat.mkdir()
    spec = write_spec(tmp_path / "spec.json", {"scope": {"edit": ["src/a.py", "tests/*.py"]}})
    out = tmp_path / "out.jsonc"
    result = render_with_spec(out, spec, seat)
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    parsed = json.loads(strip_comments(out.read_text()))
    assert parsed["permission"]["edit"] == {"*": "deny", "src/a.py": "allow", "tests/*.py": "allow"}


def test_echo_written(tmp_path):
    """Test 16: the echo file parses, carries a 64-hex-char rendered hash, and the redaction never leaks."""
    seat = tmp_path / "seat"
    seat.mkdir()
    spec = write_spec(tmp_path / "spec.json", {})
    out = tmp_path / "out.jsonc"
    echo = tmp_path / "echo.json"
    result = render_with_spec(out, spec, seat, echo=echo)
    assert result.returncode == 0, f"Render failed: {result.stderr}"

    echo_data = json.loads(echo.read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", echo_data["rendered_sha256"])

    assert "REDACTED" not in out.read_text()


def test_spec_symlink_refused(tmp_path):
    """Test 17: a spec file that is itself a symlink is refused, even when it points at a valid spec."""
    seat = tmp_path / "seat"
    seat.mkdir()
    real_spec = write_spec(tmp_path / "real_spec.json", {})
    link_spec = tmp_path / "link_spec.json"
    link_spec.symlink_to(real_spec)

    result = run_check(link_spec, seat)
    assert result.returncode == 2
    assert "spec" in result.stderr


def test_context_appends_definitions(tmp_path):
    """Test 18: context subcommand appends definitions from the specified files."""
    seat = tmp_path / "seat"
    seat.mkdir()
    a_py = seat / "a.py"
    a_py.write_text("import os\ndef alpha():\n    return 1\nclass Beta:\n    def gamma(self):\n        pass", encoding="utf-8")
    b_js = seat / "b.js"
    b_js.write_text("function delta() {\n}\nhelper() {", encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("# brief", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": ["a.py", "b.js"]}})

    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(brief)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"context failed: {result.stderr}"

    brief_text = brief.read_text()
    assert "import os" not in brief_text

    idx_a = brief_text.index("### a.py")
    idx_b = brief_text.index("### b.js")
    between = brief_text[idx_a:idx_b]
    after_b = brief_text[idx_b:]

    assert "2: def alpha():" in between
    assert "4: class Beta:" in between
    assert "5:     def gamma(self):" in between
    assert "function delta" not in between

    assert "1: function delta() {" in after_b
    assert "3: helper() {" in after_b
    assert "def alpha" not in after_b


def test_context_echo_lists_files(tmp_path):
    """Test 19: context subcommand writes the echo with the correct context entries."""
    seat = tmp_path / "seat"
    seat.mkdir()
    a_py = seat / "a.py"
    a_py.write_text("def alpha():\n    return 1\nclass Beta:\n    def gamma(self):\n        pass", encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("# brief", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": ["a.py"]}})

    echo = tmp_path / "echo.json"
    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(brief), "--echo", str(echo)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"context failed: {result.stderr}"

    echo_data = json.loads(echo.read_text())
    assert echo_data["context"] == [{"path": "a.py", "lines": 3}]


def test_context_without_key_leaves_brief(tmp_path):
    """Test 20: context subcommand with spec lacking context key leaves brief unchanged."""
    seat = tmp_path / "seat"
    seat.mkdir()
    brief = tmp_path / "brief.md"
    brief.write_text("# brief", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {})

    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(brief)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"context failed: {result.stderr}"

    brief_text = brief.read_text()
    assert brief_text == "# brief"


def test_context_refuses_outside_and_symlink(tmp_path):
    """Test 21: context subcommand refuses paths outside --seat and symlinks."""
    seat = tmp_path / "seat"
    seat.mkdir()
    a_py = seat / "a.py"
    a_py.write_text("def alpha():\n    return 1", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": ["../brief.md"]}})

    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(tmp_path / "brief.md")],
        capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "context.definitions_of" in result.stderr

    link_py = seat / "link.py"
    link_py.symlink_to(a_py)

    spec2 = write_spec(tmp_path / "spec2.json", {"context": {"definitions_of": ["link.py"]}})

    result2 = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec2), "--seat", str(seat),
         "--brief-copy", str(tmp_path / "brief.md")],
        capture_output=True, text=True
    )
    assert result2.returncode == 2
    assert "context.definitions_of" in result2.stderr


def test_context_caps_per_file(tmp_path):
    """Test 22: context subcommand caps per-file lines at 400 and truncates."""
    seat = tmp_path / "seat"
    seat.mkdir()
    a_py = seat / "a.py"
    lines = "\n".join(f"def f{i}():" for i in range(1, 451))
    a_py.write_text(lines, encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("# brief", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": ["a.py"]}})

    echo = tmp_path / "echo.json"
    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(brief), "--echo", str(echo)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"context failed: {result.stderr}"

    brief_text = brief.read_text()
    assert brief_text.count(": ") == 400
    assert "(truncated at 400 lines)" in brief_text

    echo_data = json.loads(echo.read_text())
    assert echo_data["context"] == [{"path": "a.py", "lines": 400}]


def test_context_caps_total(tmp_path):
    """Test 24: context subcommand caps the total kept lines across all files at 1200."""
    seat = tmp_path / "seat"
    seat.mkdir()
    names = ["a.py", "b.py", "c.py", "d.py"]
    for name in names:
        content = "\n".join(f"def f{i}():" for i in range(1, 351))
        (seat / name).write_text(content, encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("# brief", encoding="utf-8")

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": names}})

    echo = tmp_path / "echo.json"
    result = subprocess.run(
        [sys.executable, str(RENDERER), "context", "--spec", str(spec), "--seat", str(seat),
         "--brief-copy", str(brief), "--echo", str(echo)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"context failed: {result.stderr}"

    echo_data = json.loads(echo.read_text())
    assert echo_data["context"] == [
        {"path": "a.py", "lines": 350},
        {"path": "b.py", "lines": 350},
        {"path": "c.py", "lines": 350},
        {"path": "d.py", "lines": 150},
    ]

    brief_text = brief.read_text()
    assert "(truncated at 150 lines)" in brief_text


def test_check_validates_context_paths(tmp_path):
    """Test 23: check subcommand validates context paths."""
    seat = tmp_path / "seat"
    seat.mkdir()

    spec = write_spec(tmp_path / "spec.json", {"context": {"definitions_of": ["missing.py"]}})

    result = run_check(spec, seat)
    assert result.returncode == 2
    assert "context.definitions_of" in result.stderr
