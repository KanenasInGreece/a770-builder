# CMakeLists.txt — builds and runs the hidden variant of the binary-search-tree reference exercise.
# A second CMakeLists beside the exercise's own (kit/reference/cpp/binary-search-tree/CMakeLists.txt), so the
# public exercise's build is never touched. Links the model's edited header
# (kit/reference/cpp/binary-search-tree/binary_search_tree.h and .cpp) against the fresh hidden test
# (kit/reference/hidden/bst_hidden_test.cpp) and the exercise's own vendored Catch2
# (kit/reference/cpp/binary-search-tree/test/), the same toolchain the exercise's own build uses.
cmake_minimum_required(VERSION 3.10)
project(bst_hidden CXX)

set(EXERCISE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../cpp/binary-search-tree)
set(HIDDEN_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)

add_executable(bst_hidden
    ${HIDDEN_DIR}/bst_hidden_test.cpp
    ${EXERCISE_DIR}/binary_search_tree.cpp
    ${EXERCISE_DIR}/test/tests-main.cpp
)
target_include_directories(bst_hidden PRIVATE ${EXERCISE_DIR} ${EXERCISE_DIR}/test)

set_target_properties(bst_hidden PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED OFF
    CXX_EXTENSIONS OFF
)

if("${CMAKE_CXX_COMPILER_ID}" MATCHES "(GNU|Clang)")
    target_compile_options(bst_hidden PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

# Run the tests on every build, the way the exercise's own CMakeLists does.
add_custom_target(run_bst_hidden ALL DEPENDS bst_hidden COMMAND bst_hidden)
