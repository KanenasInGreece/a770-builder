"""tests/test_kit_reference.py — KU4: the three reference exercises in kit/reference/.

Checks, in order:
  1. the three exercise directories exist, each with a LICENSE and a parseable .meta/config.json;
  2. kit/reference/reference.json parses and every file it names (briefs, specs, hidden graders) exists;
  3. every task specification passes `harness/render_profile.py check`;
  4. the proof solution (the exercise's own .meta/example.*) makes the public grader green, and the fresh
     hidden variant green too, each in a scratch copy so nothing is left in the working tree — skipped, with a
     clear reason, when the language's toolchain is not on PATH.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT_REF = REPO_ROOT / "kit" / "reference"
REFERENCE_JSON = KIT_REF / "reference.json"

EXERCISES = [
    ("cpp", "binary-search-tree"),
    ("javascript", "forth"),
    ("python", "pov"),
]


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


HAVE_CPP = _have("cmake") and _have("g++")
HAVE_NODE = _have("node")
HAVE_UV = _have("uv")


# ---------------------------------------------------------------------------
# 1. directories, LICENSE, .meta/config.json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language,slug", EXERCISES)
def test_exercise_directory_has_license_and_config(language, slug):
    exercise_dir = KIT_REF / language / slug
    assert exercise_dir.is_dir(), f"missing exercise directory: {exercise_dir}"

    license_path = exercise_dir / "LICENSE"
    assert license_path.is_file(), f"missing LICENSE: {license_path}"
    assert "MIT License" in license_path.read_text(encoding="utf-8")

    config_path = exercise_dir / ".meta" / "config.json"
    assert config_path.is_file(), f"missing .meta/config.json: {config_path}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "authors" in config
    assert "files" in config


def test_sources_md_exists_and_names_the_origin():
    sources = KIT_REF / "SOURCES.md"
    assert sources.is_file()
    text = sources.read_text(encoding="utf-8")
    assert "Aider-AI/polyglot-benchmark" in text
    assert "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f" in text


# ---------------------------------------------------------------------------
# 2. reference.json
# ---------------------------------------------------------------------------


def _load_reference():
    assert REFERENCE_JSON.is_file(), f"missing {REFERENCE_JSON}"
    return json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))


def test_reference_json_parses():
    data = _load_reference()
    assert data["suite"] == "SUITE-1"
    assert isinstance(data["entries"], list)
    assert len(data["entries"]) == 3


@pytest.mark.parametrize("language,slug", EXERCISES)
def test_reference_json_entry_files_exist(language, slug):
    data = _load_reference()
    entries = {e["language"]: e for e in data["entries"]}
    assert language in entries, f"no reference.json entry for language {language!r}"
    entry = entries[language]
    assert entry["slug"] == slug

    for key in ("id", "language", "slug", "track_difficulty", "brief", "spec", "grader", "hidden", "licence"):
        assert key in entry, f"{entry.get('id')}: missing key {key!r}"

    assert (REPO_ROOT / entry["brief"]).is_file(), f"missing brief: {entry['brief']}"
    assert (REPO_ROOT / entry["spec"]).is_file(), f"missing spec: {entry['spec']}"
    assert "working" in entry["grader"] and entry["grader"]["working"], f"{entry['id']}: empty grader.working"

    assert entry["hidden"], f"{entry['id']}: empty hidden list"
    for hidden_name in entry["hidden"]:
        assert (KIT_REF / "hidden" / hidden_name).is_file(), f"missing hidden grader: {hidden_name}"

    # the two additions from the adversarial read: the polyglot commit and the track licence, per entry
    assert entry.get("source_commit") == "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
    assert entry.get("track_licence"), f"{entry['id']}: missing track_licence"
    assert entry.get("contamination_note"), f"{entry['id']}: missing contamination_note"


# ---------------------------------------------------------------------------
# 3. every spec passes render_profile.py check
# ---------------------------------------------------------------------------


def test_every_spec_passes_render_profile_check():
    data = _load_reference()
    for entry in data["entries"]:
        spec_path = REPO_ROOT / entry["spec"]
        result = subprocess.run(
            [sys.executable, "harness/render_profile.py", "check", "--spec", str(spec_path), "--seat", "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{entry['id']}: render_profile.py check failed:\n{result.stdout}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# 4. the proof solutions make each grader green, public and hidden
# ---------------------------------------------------------------------------


def _copy_exercise_tree(tmp_path, *dirs):
    """Copy the named kit/reference subdirectories (relative to kit/reference/) into a scratch
    kit/reference/ under tmp_path, preserving the same relative layout the graders expect."""
    dest_root = tmp_path / "kit" / "reference"
    for rel in dirs:
        src = KIT_REF / rel
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    return tmp_path


@pytest.mark.skipif(not HAVE_CPP, reason="cmake and/or g++ not on PATH — the C++ toolchain is unavailable here")
def test_cpp_public_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "cpp/binary-search-tree")
    exercise_dir = work / "kit" / "reference" / "cpp" / "binary-search-tree"
    example = exercise_dir / ".meta" / "example.h"
    shutil.copyfile(example, exercise_dir / "binary_search_tree.h")

    build_dir = work / "build-bst"
    configure = subprocess.run(
        ["cmake", "-S", str(exercise_dir), "-B", str(build_dir), "-DEXERCISM_RUN_ALL_TESTS=1"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert configure.returncode == 0, configure.stdout + configure.stderr
    build = subprocess.run(
        ["cmake", "--build", str(build_dir)],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert "All tests passed" in build.stdout


@pytest.mark.skipif(not HAVE_CPP, reason="cmake and/or g++ not on PATH — the C++ toolchain is unavailable here")
def test_cpp_hidden_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "cpp/binary-search-tree", "hidden/cpp")
    shutil.copyfile(KIT_REF / "hidden" / "bst_hidden_test.cpp", work / "kit" / "reference" / "hidden" / "bst_hidden_test.cpp")
    exercise_dir = work / "kit" / "reference" / "cpp" / "binary-search-tree"
    example = exercise_dir / ".meta" / "example.h"
    shutil.copyfile(example, exercise_dir / "binary_search_tree.h")

    build_dir = work / "build-bst-hidden"
    configure = subprocess.run(
        ["cmake", "-S", str(work / "kit" / "reference" / "hidden" / "cpp"), "-B", str(build_dir)],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert configure.returncode == 0, configure.stdout + configure.stderr
    build = subprocess.run(["cmake", "--build", str(build_dir)], cwd=work, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    assert "All tests passed" in build.stdout


@pytest.mark.skipif(not HAVE_NODE, reason="node not on PATH")
def test_js_public_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "javascript/forth", "js")
    exercise_dir = work / "kit" / "reference" / "javascript" / "forth"
    proof = exercise_dir / ".meta" / "proof.ci.js"
    shutil.copyfile(proof, exercise_dir / "forth.js")

    result = subprocess.run(
        ["node", "--test", str(exercise_dir / "run.test.mjs")],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fail 0" in result.stdout or "fail 0" in result.stderr


@pytest.mark.skipif(not HAVE_NODE, reason="node not on PATH")
def test_js_hidden_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "javascript/forth", "js", "hidden")
    exercise_dir = work / "kit" / "reference" / "javascript" / "forth"
    proof = exercise_dir / ".meta" / "proof.ci.js"
    shutil.copyfile(proof, exercise_dir / "forth.js")

    hidden_file = work / "kit" / "reference" / "hidden" / "forth_hidden.test.mjs"
    result = subprocess.run(
        ["node", "--test", str(hidden_file)],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fail 0" in result.stdout or "fail 0" in result.stderr


@pytest.mark.skipif(not HAVE_UV, reason="uv not on PATH")
def test_python_public_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "python/pov")
    exercise_dir = work / "kit" / "reference" / "python" / "pov"
    example = exercise_dir / ".meta" / "example.py"
    shutil.copyfile(example, exercise_dir / "pov.py")

    result = subprocess.run(
        ["uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q", str(exercise_dir / "pov_test.py")],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "15 passed" in result.stdout


@pytest.mark.skipif(not HAVE_UV, reason="uv not on PATH")
def test_python_hidden_grader_green_with_proof_solution(tmp_path):
    work = _copy_exercise_tree(tmp_path, "python/pov", "hidden")
    exercise_dir = work / "kit" / "reference" / "python" / "pov"
    example = exercise_dir / ".meta" / "example.py"
    shutil.copyfile(example, exercise_dir / "pov.py")

    hidden_file = work / "kit" / "reference" / "hidden" / "test_pov_hidden.py"
    result = subprocess.run(
        ["uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q", str(hidden_file)],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "15 passed" in result.stdout


@pytest.mark.skipif(
    not (HAVE_CPP and HAVE_NODE and HAVE_UV),
    reason="cmake/g++, node and uv all required for the wrapper-style hidden graders",
)
def test_cpp_and_js_hidden_wrappers_green_with_proof_solutions(tmp_path):
    """The pytest wrappers (test_bst_hidden.py, test_forth_hidden.py) that verify.hidden names, run the way
    the harness runs them: pytest, from a seat root, on a copy of the whole kit/reference/ tree with the
    proof solutions pasted over the two stubs."""
    work = _copy_exercise_tree(
        tmp_path,
        "cpp/binary-search-tree",
        "javascript/forth",
        "js",
        "hidden",
    )
    cpp_exercise = work / "kit" / "reference" / "cpp" / "binary-search-tree"
    shutil.copyfile(cpp_exercise / ".meta" / "example.h", cpp_exercise / "binary_search_tree.h")

    js_exercise = work / "kit" / "reference" / "javascript" / "forth"
    shutil.copyfile(js_exercise / ".meta" / "proof.ci.js", js_exercise / "forth.js")

    result = subprocess.run(
        [
            "uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q",
            "kit/reference/hidden/test_bst_hidden.py",
            "kit/reference/hidden/test_forth_hidden.py",
        ],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
