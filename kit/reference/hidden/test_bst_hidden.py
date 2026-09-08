# test_bst_hidden.py — the pytest wrapper for the hidden C++ variant of the binary-search-tree reference
# exercise. `verify.hidden` in kit/reference/tasks/ref-cpp-binary-search-tree.spec.json names this file (the
# harness copies it into tests/_hidden_<name> of the seat and runs pytest on it); the C++ files it shells out
# to — kit/reference/hidden/bst_hidden_test.cpp and kit/reference/hidden/cpp/CMakeLists.txt — sit beside it,
# referenced by a path relative to the current working directory (the seat root, the same directory pytest is
# invoked from), not relative to wherever this wrapper itself ends up.
#
# The pytest tool subprocess here is not the model's own shell: it runs inside the harness's own grading
# step, so it is not subject to the run specification's bash_allow list (that list governs only the coding
# agent's own bash tool calls while it works the brief).
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HIDDEN_CPP_DIR = Path("kit/reference/hidden/cpp")


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@pytest.mark.skipif(
    not (_have("cmake") and _have("g++")), reason="cmake and/or g++ not on PATH — the sandbox lacks the C++ toolchain"
)
def test_bst_hidden_builds_and_passes():
    assert HIDDEN_CPP_DIR.is_dir(), (
        f"{HIDDEN_CPP_DIR} is missing relative to the current directory ({Path.cwd()}) — "
        "this wrapper must run from the seat root"
    )
    with tempfile.TemporaryDirectory(prefix="bst-hidden-build-") as tmp:
        build_dir = Path(tmp) / "build"
        configure = subprocess.run(
            ["cmake", "-S", str(HIDDEN_CPP_DIR), "-B", str(build_dir)],
            capture_output=True,
            text=True,
        )
        assert configure.returncode == 0, f"cmake configure failed:\n{configure.stdout}\n{configure.stderr}"

        build = subprocess.run(
            ["cmake", "--build", str(build_dir)],
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, f"cmake build (which runs the hidden tests) failed:\n{build.stdout}\n{build.stderr}"
