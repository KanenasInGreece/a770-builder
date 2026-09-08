# test_bst_hidden.py — the pytest wrapper for the hidden C++ variant of the binary-search-tree reference
# exercise. `verify.hidden` in kit/reference/tasks/ref-cpp-binary-search-tree.spec.json names this file, and
# the harness (skills/local-build/scripts/local-build.sh verify) copies ONLY this basename into
# tests/_hidden_<name> of the seat — its companions never travel with it. So the fresh hidden test and its
# CMakeLists are embedded below verbatim, kept byte-identical to kit/reference/hidden/bst_hidden_test.cpp and
# kit/reference/hidden/cpp/CMakeLists.txt (tests/test_kit_reference.py asserts the two stay in sync), rather
# than read from kit/reference/hidden/ — a directory this wrapper must never require to exist in the model's
# tree. At test time they are materialised into a throwaway directory nested two levels under kit/reference/,
# mirroring the original kit/reference/hidden/cpp/ layout the embedded CMakeLists' own relative paths assume
# (../../cpp/binary-search-tree for the exercise, .. for the hidden test itself), built, and removed again.
#
# The cmake/build subprocess here is not the model's own shell: it runs inside the harness's own grading step,
# so it is not subject to the run specification's bash_allow list (that list governs only the coding agent's
# own bash tool calls while it works the brief).
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

BST_HIDDEN_TEST_CPP = "// bst_hidden_test.cpp \u2014 the hidden variant of the binary-search-tree reference exercise (Aider's polyglot\n// benchmark, C++ track, MIT/Exercism 2021: see kit/reference/SOURCES.md). Same public interface as the\n// exercise's own binary_search_tree_test.cpp (insert, data(), left(), right(), the iterator), but every value\n// and every insertion order here is computed by this file, not copied from the exercise's own test data, so a\n// model that has memorised the public test cannot pass this one by memorisation alone.\n//\n// Built by kit/reference/hidden/cpp/CMakeLists.txt against the exercise's own vendored Catch2\n// (kit/reference/cpp/binary-search-tree/test/) and the model's edited\n// kit/reference/cpp/binary-search-tree/binary_search_tree.h.\n\n#include \"binary_search_tree.h\"\n#ifdef EXERCISM_TEST_SUITE\n#include <catch2/catch.hpp>\n#else\n#include \"catch.hpp\"\n#endif\n#include <algorithm>\n#include <cstdint>\n#include <string>\n#include <vector>\n\ntemplate <typename T>\nusing tree_ptr = typename std::unique_ptr<binary_search_tree::binary_tree<T>>;\n\ntemplate <typename T>\nstatic void test_leaf(const tree_ptr<T> &tree, const T &data, bool has_left, bool has_right) {\n    REQUIRE(data == tree->data());\n    REQUIRE((bool)tree->left() == has_left);\n    REQUIRE((bool)tree->right() == has_right);\n}\n\ntemplate <typename T>\nstatic tree_ptr<T> make_tree(const std::vector<T> &data) {\n    if (data.empty())\n        return tree_ptr<T>(nullptr);\n\n    auto data_iter = data.begin();\n    auto tree = tree_ptr<T>(new binary_search_tree::binary_tree<T>(*data_iter));\n    ++data_iter;\n\n    for (; data_iter != data.end(); ++data_iter) {\n        tree->insert(*data_iter);\n    }\n\n    return tree;\n}\n\ntemplate <typename T>\nstatic void test_sort(const tree_ptr<T> &tree, const std::vector<T> &expected) {\n    std::vector<T> actual;\n    for (auto &x : *tree) {\n        actual.push_back(x);\n    }\n    REQUIRE(expected == actual);\n}\n\n// A small deterministic generator (a linear congruential generator, fixed seed) so the insertion\n// sequences below are computed here, at test time, rather than pasted from the public exercise.\nstatic std::vector<uint32_t> lcg_distinct(uint32_t seed, std::size_t count, uint32_t modulus) {\n    std::vector<uint32_t> seen;\n    uint64_t x = seed;\n    while (seen.size() < count) {\n        x = (x * 1103515245ULL + 12345ULL) & 0x7fffffffULL;\n        uint32_t v = static_cast<uint32_t>(x % modulus);\n        bool dup = false;\n        for (auto s : seen)\n            if (s == v) {\n                dup = true;\n                break;\n            }\n        if (!dup)\n            seen.push_back(v);\n    }\n    return seen;\n}\n\nTEST_CASE(\"hidden_data_is_retained\") {\n    auto values = lcg_distinct(7, 1, 1000);\n    auto tested = make_tree<uint32_t>({values[0]});\n    test_leaf<uint32_t>(tested, values[0], false, false);\n}\n\nTEST_CASE(\"hidden_smaller_number_at_left_node\") {\n    auto root = lcg_distinct(11, 1, 500)[0] + 500;  // force a mid-range root\n    uint32_t smaller = root - 137;\n    auto tested = make_tree<uint32_t>({root, smaller});\n\n    test_leaf<uint32_t>(tested, root, true, false);\n    test_leaf<uint32_t>(tested->left(), smaller, false, false);\n}\n\nTEST_CASE(\"hidden_same_number_at_left_node\") {\n    auto root = lcg_distinct(19, 1, 500)[0] + 500;\n    auto tested = make_tree<uint32_t>({root, root});\n\n    test_leaf<uint32_t>(tested, root, true, false);\n    test_leaf<uint32_t>(tested->left(), root, false, false);\n}\n\nTEST_CASE(\"hidden_greater_number_at_right_node\") {\n    auto root = lcg_distinct(23, 1, 500)[0] + 100;\n    uint32_t bigger = root + 251;\n    auto tested = make_tree<uint32_t>({root, bigger});\n\n    test_leaf<uint32_t>(tested, root, false, true);\n    test_leaf<uint32_t>(tested->right(), bigger, false, false);\n}\n\nTEST_CASE(\"hidden_can_create_complex_tree\") {\n    // Seven distinct values, insertion order chosen so the tree shape matches the assertions below:\n    // root, then two children, then four grandchildren, each strictly on the correct side.\n    auto root = 500u;\n    auto left = 250u, right = 750u;\n    auto ll = 125u, lr = 375u, rl = 625u, rr = 875u;\n    auto tested = make_tree<uint32_t>({root, left, right, ll, lr, rl, rr});\n\n    test_leaf<uint32_t>(tested, root, true, true);\n    test_leaf<uint32_t>(tested->left(), left, true, true);\n    test_leaf<uint32_t>(tested->right(), right, true, true);\n\n    test_leaf<uint32_t>(tested->left()->left(), ll, false, false);\n    test_leaf<uint32_t>(tested->left()->right(), lr, false, false);\n\n    test_leaf<uint32_t>(tested->right()->left(), rl, false, false);\n    test_leaf<uint32_t>(tested->right()->right(), rr, false, false);\n}\n\nTEST_CASE(\"hidden_can_sort_single_number\") {\n    auto values = lcg_distinct(29, 1, 1000);\n    test_sort(make_tree<uint32_t>({values[0]}), {values[0]});\n}\n\nTEST_CASE(\"hidden_can_sort_if_second_number_is_smaller_than_first\") {\n    test_sort(make_tree<uint32_t>({80u, 30u}), {30u, 80u});\n}\n\nTEST_CASE(\"hidden_can_sort_if_second_number_is_same_as_first\") {\n    test_sort(make_tree<uint32_t>({80u, 80u}), {80u, 80u});\n}\n\nTEST_CASE(\"hidden_can_sort_if_second_number_is_greater_than_first\") {\n    test_sort(make_tree<uint32_t>({30u, 80u}), {30u, 80u});\n}\n\nTEST_CASE(\"hidden_can_sort_complex_tree\") {\n    // A permutation of nine distinct values, unrelated to the public test's six.\n    std::vector<uint32_t> values = {40u, 15u, 60u, 5u, 25u, 50u, 70u, 1u, 90u};\n    std::vector<uint32_t> expected = values;\n    std::sort(expected.begin(), expected.end());\n    test_sort(make_tree<uint32_t>(values), expected);\n}\n\nTEST_CASE(\"hidden_can_create_complex_tree_strings\") {\n    auto tested = make_tree<std::string>({\"mango\", \"fig\", \"papaya\", \"date\", \"guava\", \"nectarine\", \"quince\"});\n\n    test_leaf<std::string>(tested, \"mango\", true, true);\n    test_leaf<std::string>(tested->left(), \"fig\", true, true);\n    test_leaf<std::string>(tested->right(), \"papaya\", true, true);\n\n    test_leaf<std::string>(tested->left()->left(), \"date\", false, false);\n    test_leaf<std::string>(tested->left()->right(), \"guava\", false, false);\n\n    test_leaf<std::string>(tested->right()->left(), \"nectarine\", false, false);\n    test_leaf<std::string>(tested->right()->right(), \"quince\", false, false);\n}\n\nTEST_CASE(\"hidden_can_sort_complex_tree_strings\") {\n    std::vector<std::string> values = {\"zebra\", \"kiwi\", \"otter\", \"walrus\", \"ibex\", \"seal\", \"yak\", \"narwhal\"};\n    std::vector<std::string> expected = values;\n    std::sort(expected.begin(), expected.end());\n    test_sort(make_tree<std::string>(values), expected);\n}\n"

BST_HIDDEN_CMAKELISTS = "# CMakeLists.txt \u2014 builds and runs the hidden variant of the binary-search-tree reference exercise.\n# A second CMakeLists beside the exercise's own (kit/reference/cpp/binary-search-tree/CMakeLists.txt), so the\n# public exercise's build is never touched. Links the model's edited header\n# (kit/reference/cpp/binary-search-tree/binary_search_tree.h and .cpp) against the fresh hidden test\n# (kit/reference/hidden/bst_hidden_test.cpp) and the exercise's own vendored Catch2\n# (kit/reference/cpp/binary-search-tree/test/), the same toolchain the exercise's own build uses.\ncmake_minimum_required(VERSION 3.10)\nproject(bst_hidden CXX)\n\nset(EXERCISE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../cpp/binary-search-tree)\nset(HIDDEN_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)\n\nadd_executable(bst_hidden\n    ${HIDDEN_DIR}/bst_hidden_test.cpp\n    ${EXERCISE_DIR}/binary_search_tree.cpp\n    ${EXERCISE_DIR}/test/tests-main.cpp\n)\ntarget_include_directories(bst_hidden PRIVATE ${EXERCISE_DIR} ${EXERCISE_DIR}/test)\n\nset_target_properties(bst_hidden PROPERTIES\n    CXX_STANDARD 17\n    CXX_STANDARD_REQUIRED OFF\n    CXX_EXTENSIONS OFF\n)\n\nif(\"${CMAKE_CXX_COMPILER_ID}\" MATCHES \"(GNU|Clang)\")\n    target_compile_options(bst_hidden PRIVATE -Wall -Wextra -Wpedantic -Werror)\nendif()\n\n# Run the tests on every build, the way the exercise's own CMakeLists does.\nadd_custom_target(run_bst_hidden ALL DEPENDS bst_hidden COMMAND bst_hidden)\n"


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@pytest.mark.skipif(
    not (_have("cmake") and _have("g++")), reason="cmake and/or g++ not on PATH — the sandbox lacks the C++ toolchain"
)
def test_bst_hidden_builds_and_passes():
    ref_root = Path("kit/reference")
    assert ref_root.is_dir(), (
        f"{ref_root} is missing relative to the current directory ({Path.cwd()}) — "
        "this wrapper must run from the seat root"
    )
    with tempfile.TemporaryDirectory(prefix=".bst-hidden-", dir=ref_root) as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "bst_hidden_test.cpp").write_text(BST_HIDDEN_TEST_CPP)
        cpp_dir = tmp_path / "cpp"
        cpp_dir.mkdir()
        (cpp_dir / "CMakeLists.txt").write_text(BST_HIDDEN_CMAKELISTS)

        build_dir = tmp_path / "build"
        configure = subprocess.run(
            ["cmake", "-S", str(cpp_dir), "-B", str(build_dir)],
            capture_output=True,
            text=True,
        )
        assert configure.returncode == 0, f"cmake configure failed:\n{configure.stdout}\n{configure.stderr}"

        build = subprocess.run(
            ["cmake", "--build", str(build_dir)],
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, (
            f"cmake build (which runs the hidden tests) failed:\n{build.stdout}\n{build.stderr}"
        )
