"""Hidden grader for S1 (front end). Copied by the harness to
tests/_hidden_test_s1_frontend_hidden.py and run with pytest, cwd = the exported seat
(kit/seat/) — every path below is relative to that. Never present in the seat at run time;
not referenced from anywhere under kit/seat/.

Shells out to `node` (fresh inputs, embedded below — different from js/tests/format.test.js's
trivial cases, so a model cannot pass by hard-coding the visible test's values) and uses
`html.parser` on html/index.html for the structural/accessibility check.
"""

import html.parser
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SEAT = Path.cwd()
FORMAT_JS = (SEAT / "js" / "format.js").resolve()
RENDER_JS = (SEAT / "js" / "render.js").resolve()
INDEX_HTML = SEAT / "html" / "index.html"

_NODE_SCRIPT = """
import {{ formatCount, formatMean, busiestMinuteLabel }} from "{format_uri}";
import {{ renderStats }} from "{render_uri}";

function fail(msg) {{ console.error("FAIL: " + msg); process.exit(1); }}
function eq(got, want, msg) {{ if (got !== want) fail(`${{msg}}: got ${{JSON.stringify(got)}} want ${{JSON.stringify(want)}}`); }}

// fresh inputs, not the ones in js/tests/format.test.js
eq(formatCount(1000000), "1,000,000", "formatCount(1000000)");
eq(formatCount(42), "42", "formatCount(42)");
eq(formatMean(7.5), "7.50 chars avg", "formatMean(7.5)");
eq(busiestMinuteLabel("2026-03-04T09:08"), "2026-03-04 09:08", "busiestMinuteLabel");

const stats = {{
  total_lines: 314,
  by_level: {{ INFO: 200, WARN: 114 }},
  by_component: {{ api: 200, cache: 114 }},
  busiest_minute: "2026-05-06T07:08",
  mean_message_length: 12.5,
}};
const out = renderStats(stats);
if (typeof out !== "string") fail("renderStats did not return a string");
if (!out.includes(formatCount(314))) fail("renderStats output missing formatCount(total_lines)");
if (!out.includes(formatMean(12.5))) fail("renderStats output missing formatMean(mean_message_length)");
if (!out.includes(busiestMinuteLabel("2026-05-06T07:08"))) fail("renderStats output missing busiestMinuteLabel(busiest_minute)");

console.log("OK");
"""


def _run_node_checks():
    assert FORMAT_JS.is_file(), f"{FORMAT_JS} does not exist"
    assert RENDER_JS.is_file(), f"{RENDER_JS} does not exist"
    script = _NODE_SCRIPT.format(format_uri=FORMAT_JS.as_uri(), render_uri=RENDER_JS.as_uri())
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(script)
        tmp_path = f.name
    try:
        proc = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=30)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node check failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK" in proc.stdout


def test_format_and_render_on_fresh_inputs():
    _run_node_checks()


class _PageChecker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_lang = None
        self.label_for_level = False
        self.select_options = []
        self.in_select = False
        self.aria_live_ids = []
        self.headings = []
        self._current_tag_stack = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self._current_tag_stack.append(tag)
        if tag == "html":
            self.html_lang = d.get("lang")
        elif tag == "label" and d.get("for") == "level":
            self.label_for_level = True
        elif tag == "select" and d.get("id") == "level":
            self.in_select = True
        elif tag == "option" and self.in_select:
            self.select_options.append(d.get("value"))
        elif "aria-live" in d:
            self.aria_live_ids.append(d.get("id"))
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag == "select":
            self.in_select = False
        if self._current_tag_stack and self._current_tag_stack[-1] == tag:
            self._current_tag_stack.pop()


def _parse_page():
    assert INDEX_HTML.is_file(), f"{INDEX_HTML} does not exist"
    p = _PageChecker()
    p.feed(INDEX_HTML.read_text(encoding="utf-8"))
    return p


def test_page_has_lang():
    p = _parse_page()
    assert p.html_lang, "<html> is missing a lang attribute"


def test_page_has_label_for_level():
    p = _parse_page()
    assert p.label_for_level, '<label for="level"> not found'


def test_page_select_has_options():
    p = _parse_page()
    assert len(p.select_options) >= 2, f"#level select has too few options: {p.select_options}"


def test_page_has_aria_live_region():
    p = _parse_page()
    assert p.aria_live_ids, "no element with aria-live found"


def test_page_heading_order_not_skipped():
    p = _parse_page()
    assert p.headings, "no heading elements found"
    for a, b in zip(p.headings, p.headings[1:]):
        assert b <= a + 1, f"heading order skips a level: {p.headings}"
