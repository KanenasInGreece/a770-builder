#!/usr/bin/env python3
"""Tests for kit/corpus.py: the deterministic long-context corpus, concatenated from real source
only (no synthetic filler). Runs entirely offline against files already in this clone; no server,
no GPU, no network."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PY = ROOT / "kit" / "corpus.py"

# small enough that harness/, skills/, tests/, docs/ alone reach it on a bare clone (no kit/seat or
# kit/reference needed), so these tests do not depend on the other builders' corners existing yet
SMALL_FLOOR = 4096


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CORPUS_PY), *args], capture_output=True, text=True, cwd=ROOT
    )


def test_generate_twice_is_byte_identical(tmp_path):
    out1, out2 = tmp_path / "a.py", tmp_path / "b.py"
    r1 = run("generate", "--out", str(out1), "--min-bytes", str(SMALL_FLOOR))
    r2 = run("generate", "--out", str(out2), "--min-bytes", str(SMALL_FLOOR))
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert out1.read_bytes() == out2.read_bytes()


def test_generate_meets_the_requested_byte_floor(tmp_path):
    out = tmp_path / "large.py"
    r = run("generate", "--out", str(out), "--min-bytes", str(SMALL_FLOOR))
    assert r.returncode == 0, r.stderr
    assert out.stat().st_size >= SMALL_FLOOR


def test_generate_carries_file_marker_lines(tmp_path):
    out = tmp_path / "large.py"
    r = run("generate", "--out", str(out), "--min-bytes", str(SMALL_FLOOR))
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8", errors="replace")
    markers = [line for line in text.splitlines() if line.startswith("### FILE ")]
    assert len(markers) >= 2
    # every marker names a real file relative to the repository root
    for line in markers:
        rel = line[len("### FILE ") :]
        assert (ROOT / rel).is_file(), rel


def test_generate_refuses_when_the_floor_exceeds_available_source(tmp_path):
    out = tmp_path / "large.py"
    unreachable = 10**12  # no repository reaches a terabyte of source
    r = run("generate", "--out", str(out), "--min-bytes", str(unreachable))
    assert r.returncode == 2
    assert not out.exists()
    assert "kit/seat" in r.stderr or "kit/reference" in r.stderr or str(unreachable) in r.stderr


def test_check_passes_at_the_shipped_default():
    r = run("check")
    assert r.returncode == 0, r.stderr
    assert "sha256" in r.stdout
    assert "bytes" in r.stdout


def test_check_fails_when_the_floor_is_unreachable():
    r = run("check", "--min-bytes", str(10**12))
    assert r.returncode == 2
    assert r.stderr.strip() != ""


def test_index_reports_only_python_definitions_in_file_order(tmp_path):
    # a small hand-built corpus: one .py segment (two top-level defs) and one .md segment (no defs,
    # and never indexed even though it contains a line that LOOKS like a def)
    corpus = tmp_path / "mini.py"
    corpus.write_text(
        "### FILE kit/seat/python/one.py\n"
        "def alpha():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Beta:\n"
        "    def method(self):\n"
        "        return 2\n"
        "### FILE docs/note.md\n"
        "not python: `def not_a_real_def():` inside a code span\n",
        encoding="utf-8",
    )
    r = run("index", str(corpus))
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines == ["2: alpha", "6: Beta"]
    # ascending line order, and nothing from the .md segment leaked in
    nums = [int(ln.split(":", 1)[0]) for ln in lines]
    assert nums == sorted(nums)
    assert "not_a_real_def" not in r.stdout


def test_the_floor_is_only_a_threshold_not_a_target_size(tmp_path):
    # generate() always concatenates the WHOLE fixed, sorted file list (no seed, no filler, no
    # trimming to size); --min-bytes only gates acceptance, so two reachable floors on the same
    # source tree produce byte-identical output.
    out_a = tmp_path / "a.py"
    out_b = tmp_path / "b.py"
    run("generate", "--out", str(out_a), "--min-bytes", str(SMALL_FLOOR))
    run("generate", "--out", str(out_b), "--min-bytes", str(SMALL_FLOOR * 2))
    assert out_a.read_bytes() == out_b.read_bytes()
