"""Validates kit/ (the SUITE-1 mini-project seat and its four stages) per the profiling-kit
cycle brief's K3 (every brief/spec/hidden file named in kit/suite.json exists, every spec
passes render_profile.py's check, every hidden name is a plain basename) and the coordinator's
2026-09-08 design-note additions: no file under kit/hidden/ is referenced from kit/seat/, and
every stage's stub fails its own hidden grader on the clean, shipped seat. The committed
kit/seat/python/data/sample.stats.json is proven against an independent stdlib
re-computation of sample.log written directly in this file, never trusted as committed.

Does not touch kit/corpus.py, kit/corpus/, kit/reference/ or harness/run_suite.sh — those are
other units' material.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / "kit"
SEAT = KIT / "seat"
HIDDEN = KIT / "hidden"
RENDER_PROFILE = REPO_ROOT / "harness" / "render_profile.py"

HIDDEN_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _suite() -> dict:
    return json.loads((KIT / "suite.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# K3: suite.json parses; every named file exists; every spec passes the check;
# every hidden name is a plain basename.
# ---------------------------------------------------------------------------

def test_suite_json_parses_and_has_the_expected_shape():
    data = _suite()
    assert data["suite"] == "SUITE-1"
    assert isinstance(data["stages"], list)
    assert len(data["stages"]) == 4
    assert [s["id"] for s in data["stages"]] == [
        "s0-design", "s1-frontend", "s2-backend", "s3-optimise",
    ]
    assert data["reference"] == []


def test_every_brief_spec_and_hidden_file_named_exists():
    data = _suite()
    for stage in data["stages"]:
        brief = REPO_ROOT / stage["brief"]
        spec = REPO_ROOT / stage["spec"]
        assert brief.is_file(), f"{stage['id']}: missing brief {brief}"
        assert spec.is_file(), f"{stage['id']}: missing spec {spec}"
        assert stage["hidden"], f"{stage['id']}: no hidden graders named"
        for h in stage["hidden"]:
            hp = HIDDEN / h
            assert hp.is_file(), f"{stage['id']}: missing hidden grader {hp}"
        rubric = REPO_ROOT / stage["grader"]["rubric"]
        assert rubric.is_file(), f"{stage['id']}: missing rubric {rubric}"


def test_every_hidden_name_is_a_plain_basename():
    data = _suite()
    for stage in data["stages"]:
        for h in stage["hidden"]:
            assert HIDDEN_BASENAME_RE.match(h), f"{stage['id']}: '{h}' is not a plain basename"
            assert "/" not in h and ".." not in h


def test_every_spec_passes_render_profile_check():
    data = _suite()
    for stage in data["stages"]:
        spec = REPO_ROOT / stage["spec"]
        proc = subprocess.run(
            [sys.executable, str(RENDER_PROFILE), "check", "--spec", str(spec), "--seat", str(SEAT)],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, f"{stage['id']}'s spec failed render_profile check: {proc.stderr}"


# ---------------------------------------------------------------------------
# Coordinator's 2026-09-08 addition: no file under kit/hidden/ is referenced from kit/seat/.
# ---------------------------------------------------------------------------

def test_no_seat_file_references_kit_hidden():
    hidden_names = sorted(p.name for p in HIDDEN.glob("*.py"))
    assert hidden_names, "kit/hidden/ has no graders to check against"
    offenders = []
    for p in SEAT.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "kit/hidden" in text or "kit_hidden" in text:
            offenders.append(f"{p}: references kit/hidden by path")
            continue
        for name in hidden_names:
            if name in text:
                offenders.append(f"{p}: references hidden grader name '{name}'")
    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# Coordinator's 2026-09-08 addition: every stage's stub FAILS its own grader on a clean seat
# (the third leg — a shipped-complete stage being a zero-work pass is the other leg, exercised
# by hand against a reference solution while building this unit, not re-run here since it
# would require shipping working reference implementations inside kit/seat/, which the stages
# themselves are supposed to produce).
# ---------------------------------------------------------------------------

def _run_hidden_against_seat(hidden_name: str) -> subprocess.CompletedProcess:
    hidden_path = HIDDEN / hidden_name
    return subprocess.run(
        ["uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q", str(hidden_path)],
        cwd=str(SEAT), capture_output=True, text=True, timeout=60,
    )


def test_stage_stubs_fail_their_own_hidden_grader_s0():
    proc = _run_hidden_against_seat("test_s0_design_hidden.py")
    assert proc.returncode != 0, "s0's hidden grader passed on the clean (stub) seat"


def test_stage_stubs_fail_their_own_hidden_grader_s1():
    proc = _run_hidden_against_seat("test_s1_frontend_hidden.py")
    assert proc.returncode != 0, "s1's hidden grader passed on the clean (stub) seat"


def test_stage_stubs_fail_their_own_hidden_grader_s2():
    proc = _run_hidden_against_seat("test_s2_backend_hidden.py")
    assert proc.returncode != 0, "s2's hidden grader passed on the clean (stub) seat"


def test_stage_stubs_fail_their_own_hidden_grader_s3():
    cpp_dir = SEAT / "cpp"
    built = cpp_dir / "liblogstats.so"
    build_ok = False
    grader_result = None
    try:
        build = subprocess.run(["make", "-C", str(cpp_dir)], capture_output=True, text=True, timeout=60)
        build_ok = build.returncode == 0
        if build_ok:
            grader_result = _run_hidden_against_seat("test_s3_optimise_hidden.py")
    finally:
        subprocess.run(["make", "-C", str(cpp_dir), "clean"], capture_output=True, text=True, timeout=30)
    assert build_ok, "the C++ stub must still build cleanly"
    assert not built.exists(), "the S3 stub build artifact was not cleaned up"
    assert grader_result is not None and grader_result.returncode != 0, (
        "s3's hidden grader passed on the clean (stub) seat"
    )


# ---------------------------------------------------------------------------
# The reference output is proven, not trusted: an independent stdlib re-computation of
# sample.log, written directly in this file (not imported from the generation-time helper
# that produced the committed files — that helper is not shipped in kit/ at all).
# ---------------------------------------------------------------------------

_LOG_RE = re.compile(r"^(\S+) ([A-Z]+) (\S+) (.*)$")
_NOISE = {
    "true", "false", "null", "none", "nil", "n/a", "na", "tbd", "todo", "yes", "no",
    "unknown", "undefined", "nan", "level", "component", "message", "timestamp", "log", "logs",
}
_NUMERIC_RE = re.compile(r"^[0-9]+$")
_AXIS_RE = re.compile(r"^\s*(?:level|component)\s*:", re.IGNORECASE)


def _sanitize_component(name: str):
    name = re.sub(r"\s+", " ", name.strip())
    if not name or _NUMERIC_RE.match(name) or len(name) < 2 or name.lower() in _NOISE:
        return None
    if _AXIS_RE.match(name):
        return None
    return name


def _reference_stats(lines):
    total = 0
    by_level, by_component, minute_counts = {}, {}, {}
    msg_lengths = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        m = _LOG_RE.match(line)
        if not m:
            continue
        ts, level, component, message = m.groups()
        clean = _sanitize_component(component)
        if clean is None:
            continue
        total += 1
        by_level[level] = by_level.get(level, 0) + 1
        by_component[clean] = by_component.get(clean, 0) + 1
        minute = ts[:16]
        minute_counts[minute] = minute_counts.get(minute, 0) + 1
        msg_lengths.append(len(message))
    busiest = None
    if minute_counts:
        best = max(minute_counts.values())
        busiest = min(m for m, c in minute_counts.items() if c == best)
    mean = round(sum(msg_lengths) / len(msg_lengths), 2) if msg_lengths else 0.0
    return {
        "total_lines": total,
        "by_level": dict(sorted(by_level.items())),
        "by_component": dict(sorted(by_component.items())),
        "busiest_minute": busiest,
        "mean_message_length": mean,
    }


def test_sample_log_is_two_thousand_lines():
    log = SEAT / "python" / "data" / "sample.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2000


def test_sample_stats_json_matches_independent_recomputation():
    log = SEAT / "python" / "data" / "sample.log"
    stats = SEAT / "python" / "data" / "sample.stats.json"
    lines = log.read_text(encoding="utf-8").splitlines()
    expected = json.loads(stats.read_text(encoding="utf-8"))
    got = _reference_stats(lines)
    assert got == expected, f"sample.stats.json disagrees with an independent recomputation\ngot={got}\nwant={expected}"
