# Copyright 2026 Xenofon S. Motsenigos
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# MODIFIED: this file is derived from `sanitize_entity_name` in
# `shared-memory/scripts/ontology.py` (lines 226-254) of the Shared Memory
# repository (https://github.com/KanenasInGreece/Shared_Memory), renamed here
# to `sanitize_component` for the logstats project and reduced to only what
# this project needs (the entity-name noise set becomes a component noise
# set). See ../LICENSE-APACHE and ../../../NOTICE.
"""Parsing for logstats log lines: `<ISO timestamp> <LEVEL> <component> <message>`."""

import re

# Minimum component-name length after stripping. Default 2 keeps useful short
# names ("io", "db") while dropping single-character noise.
MIN_COMPONENT_NAME_LEN = 2

# Lowercased tokens that must never become a component name: content-free
# placeholders and schema words that would otherwise leak through as if they
# were a real log source.
_COMPONENT_NOISE_NAMES: frozenset[str] = frozenset({
    "true", "false", "null", "none", "nil", "n/a", "na", "tbd", "todo",
    "yes", "no", "unknown", "undefined", "nan",
    "level", "component", "message", "timestamp", "log", "logs",
})

_NUMERIC_NAME_RE = re.compile(r"^[0-9]+$")
_WHITESPACE_RE = re.compile(r"\s+")
_AXIS_DECLARATION_RE = re.compile(r"^\s*(?:level|component)\s*:", re.IGNORECASE)

# `<ISO timestamp> <LEVEL> <component> <message>`, the message taking the rest
# of the line (it may itself contain spaces).
LOG_LINE_RE = re.compile(r"^(\S+) ([A-Z]+) (\S+) (.*)$")


def sanitize_component(raw: object) -> str | None:
    """Normalise and validate one component name. Returns the cleaned name, or
    None if it must be rejected. Pure and deterministic — no I/O.

    Rejection rules: non-string / empty after strip; numeric-only; shorter
    than MIN_COMPONENT_NAME_LEN; lowercased form in the noise set; a field
    declaration (`level:` / `component:` prefix). Internal whitespace is
    collapsed to a single space; casing is preserved.
    """
    if not isinstance(raw, str):
        return None
    name = _WHITESPACE_RE.sub(" ", raw.strip())
    if not name:
        return None
    if _NUMERIC_NAME_RE.match(name):
        return None
    if len(name) < MIN_COMPONENT_NAME_LEN:
        return None
    if name.lower() in _COMPONENT_NOISE_NAMES:
        return None
    if _AXIS_DECLARATION_RE.match(name):
        return None
    return name


def parse_line(line: str) -> dict | None:
    """Parse one log line into {"timestamp": str, "level": str, "component": str,
    "message": str}, or None when the line does not match the log format or its
    component is rejected by `sanitize_component`. The timestamp is returned
    verbatim (callers that need the minute bucket take `timestamp[:16]`)."""
    m = LOG_LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    ts, level, component, message = m.groups()
    clean_component = sanitize_component(component)
    if clean_component is None:
        return None
    return {"timestamp": ts, "level": level, "component": clean_component, "message": message}
