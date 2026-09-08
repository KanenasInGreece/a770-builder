"""Hidden grader for S0 (design). Copied by the harness to tests/_hidden_test_s0_design_hidden.py
and run with pytest, cwd = the exported seat (kit/seat/) — every path below is relative to
that. Never present in the seat at run time; not referenced from anywhere under kit/seat/.

S0 is graded on restating and justifying the FIXED JSON interface (see
kit/seat/product-brief.md): design/DESIGN.md must exist, be at most 60 lines, name the three
components, and contain a JSON example whose keys are EXACTLY the fixed set. S0 is scored
outside the pass count (commentary, per kit/SUITE.md) — this file still reports pass/fail so
a row can see whether the note met the bar, but a coordinator reading `suite.json`'s
`axes` for s0 does not fold it into the working-code tally.
"""

import json
from pathlib import Path

DESIGN = Path("design/DESIGN.md")
FIXED_KEYS = {"total_lines", "by_level", "by_component", "busiest_minute", "mean_message_length"}
COMPONENT_NAMES = ("backend", "front end", "c++")


def _read_design():
    assert DESIGN.is_file(), f"{DESIGN} does not exist"
    text = DESIGN.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) <= 60, f"{DESIGN} is {len(lines)} lines, over the 60-line budget"
    return text, lines


def test_design_file_exists_and_is_within_budget():
    _read_design()


def test_design_names_the_three_components():
    text, _ = _read_design()
    low = text.lower()
    missing = [c for c in COMPONENT_NAMES if c not in low]
    assert not missing, f"design note does not name: {missing}"


def _find_json_examples(text: str):
    """Every balanced-brace `{...}` span in `text` that parses as JSON — brace-matched, not a
    non-greedy regex, so a nested object (`"by_level": {...}` inside the outer object) does
    not truncate the outer span at its first inner `}`."""
    found = []
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        found.append(json.loads(candidate))
                    except json.JSONDecodeError:
                        pass
                    break
    return found


def test_design_contains_a_json_example_with_the_fixed_keys():
    text, _ = _read_design()
    examples = _find_json_examples(text)
    assert examples, "no parsing JSON object found in design/DESIGN.md"
    matching = [e for e in examples if isinstance(e, dict) and set(e.keys()) == FIXED_KEYS]
    assert matching, (
        f"no JSON example has exactly the fixed keys {sorted(FIXED_KEYS)}; "
        f"found key sets: {[sorted(e.keys()) for e in examples if isinstance(e, dict)]}"
    )
