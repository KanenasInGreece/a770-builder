# test_forth_hidden.py — the pytest wrapper for the hidden JavaScript variant of the forth reference
# exercise. `verify.hidden` in kit/reference/tasks/ref-javascript-forth.spec.json names this file (the harness
# copies it into tests/_hidden_<name> of the seat and runs pytest on it); the file it shells out to,
# kit/reference/hidden/forth_hidden.test.mjs, sits beside it, referenced by a path relative to the current
# working directory (the seat root, the same directory pytest is invoked from), not relative to wherever this
# wrapper itself ends up.
#
# The node subprocess here is not the model's own shell: it runs inside the harness's own grading step, so it
# is not subject to the run specification's bash_allow list (that list governs only the coding agent's own
# bash tool calls while it works the brief) — see the FINDING in kit/reference/SOURCES.md on why `node` never
# appears in a specification's bash_allow.
import shutil
import subprocess
from pathlib import Path

import pytest

HIDDEN_JS_FILE = Path("kit/reference/hidden/forth_hidden.test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_forth_hidden_passes():
    assert HIDDEN_JS_FILE.is_file(), (
        f"{HIDDEN_JS_FILE} is missing relative to the current directory ({Path.cwd()}) — "
        "this wrapper must run from the seat root"
    )
    result = subprocess.run(
        ["node", "--test", str(HIDDEN_JS_FILE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --test failed:\n{result.stdout}\n{result.stderr}"
