"""run_suite.sh counts a stage's added lines from its patch. An empty patch must count as 0.

`grep -c` prints 0 and exits 1 when nothing matches, so `$(grep -c ... || echo 0)` read "0\\n0" and the
arithmetic that follows was a syntax error. In a non-interactive bash that error aborted the whole stage
loop: the stage went unrecorded, the stages after it never ran, and the suite still said it completed.
These tests run the block exactly as run_suite.sh carries it, in bash, with no card and no server.
"""
import re
import subprocess
from pathlib import Path

RUN_SUITE = Path(__file__).resolve().parent.parent / "harness" / "run_suite.sh"


def _line_count_block() -> str:
    text = RUN_SUITE.read_text(encoding="utf-8")
    m = re.search(r'^  local lines=0 added hdrs\n  if \[ -f "\$patchfile" \]; then\n.*?^  fi\n', text, re.S | re.M)
    assert m, "the patch line-count block moved; update this test to follow it"
    return m.group(0).replace("  local ", "  ")


def _count(patch: Path) -> subprocess.CompletedProcess:
    script = (
        "set -uo pipefail\n"
        f"patchfile={str(patch)!r}\n"
        "for _ in 1; do\n" + _line_count_block() + "done\n"
        'echo "lines=$lines"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_empty_patch_counts_zero_and_does_not_abort(tmp_path):
    patch = tmp_path / "empty.patch"
    patch.write_text("", encoding="utf-8")
    r = _count(patch)
    assert r.returncode == 0, r.stderr
    assert "syntax error" not in r.stderr
    assert r.stdout.strip() == "lines=0"


def test_patch_counts_added_lines_without_headers(tmp_path):
    patch = tmp_path / "one.patch"
    patch.write_text(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n",
        encoding="utf-8",
    )
    r = _count(patch)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "lines=2"
