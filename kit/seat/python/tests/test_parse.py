"""Idiom to copy for tests/test_stats.py (S2): plain functions, no fixtures, one assertion
block per behaviour, sys.path set the way this file sets it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logstats.parse import parse_line, sanitize_component


def test_sanitize_component_accepts_ordinary_names():
    assert sanitize_component("ingest") == "ingest"
    assert sanitize_component("  api  ") == "api"


def test_sanitize_component_rejects_noise_and_short_names():
    assert sanitize_component("true") is None
    assert sanitize_component("x") is None
    assert sanitize_component("42") is None
    assert sanitize_component("component: x") is None


def test_parse_line_returns_the_four_fields():
    got = parse_line("2026-01-01T00:00:16Z WARN api dropped job 7649")
    assert got == {
        "timestamp": "2026-01-01T00:00:16Z",
        "level": "WARN",
        "component": "api",
        "message": "dropped job 7649",
    }


def test_parse_line_rejects_a_malformed_line():
    assert parse_line("not a log line") is None
