// bst_hidden_test.cpp — the hidden variant of the binary-search-tree reference exercise (Aider's polyglot
// benchmark, C++ track, MIT/Exercism 2021: see kit/reference/SOURCES.md). Same public interface as the
// exercise's own binary_search_tree_test.cpp (insert, data(), left(), right(), the iterator), but every value
// and every insertion order here is computed by this file, not copied from the exercise's own test data, so a
// model that has memorised the public test cannot pass this one by memorisation alone.
//
// Built by kit/reference/hidden/cpp/CMakeLists.txt against the exercise's own vendored Catch2
// (kit/reference/cpp/binary-search-tree/test/) and the model's edited
// kit/reference/cpp/binary-search-tree/binary_search_tree.h.

#include "binary_search_tree.h"
#ifdef EXERCISM_TEST_SUITE
#include <catch2/catch.hpp>
#else
#include "catch.hpp"
#endif
#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

template <typename T>
using tree_ptr = typename std::unique_ptr<binary_search_tree::binary_tree<T>>;

template <typename T>
static void test_leaf(const tree_ptr<T> &tree, const T &data, bool has_left, bool has_right) {
    REQUIRE(data == tree->data());
    REQUIRE((bool)tree->left() == has_left);
    REQUIRE((bool)tree->right() == has_right);
}

template <typename T>
static tree_ptr<T> make_tree(const std::vector<T> &data) {
    if (data.empty())
        return tree_ptr<T>(nullptr);

    auto data_iter = data.begin();
    auto tree = tree_ptr<T>(new binary_search_tree::binary_tree<T>(*data_iter));
    ++data_iter;

    for (; data_iter != data.end(); ++data_iter) {
        tree->insert(*data_iter);
    }

    return tree;
}

template <typename T>
static void test_sort(const tree_ptr<T> &tree, const std::vector<T> &expected) {
    std::vector<T> actual;
    for (auto &x : *tree) {
        actual.push_back(x);
    }
    REQUIRE(expected == actual);
}

// A small deterministic generator (a linear congruential generator, fixed seed) so the insertion
// sequences below are computed here, at test time, rather than pasted from the public exercise.
static std::vector<uint32_t> lcg_distinct(uint32_t seed, std::size_t count, uint32_t modulus) {
    std::vector<uint32_t> seen;
    uint64_t x = seed;
    while (seen.size() < count) {
        x = (x * 1103515245ULL + 12345ULL) & 0x7fffffffULL;
        uint32_t v = static_cast<uint32_t>(x % modulus);
        bool dup = false;
        for (auto s : seen)
            if (s == v) {
                dup = true;
                break;
            }
        if (!dup)
            seen.push_back(v);
    }
    return seen;
}

TEST_CASE("hidden_data_is_retained") {
    auto values = lcg_distinct(7, 1, 1000);
    auto tested = make_tree<uint32_t>({values[0]});
    test_leaf<uint32_t>(tested, values[0], false, false);
}

TEST_CASE("hidden_smaller_number_at_left_node") {
    auto root = lcg_distinct(11, 1, 500)[0] + 500;  // force a mid-range root
    uint32_t smaller = root - 137;
    auto tested = make_tree<uint32_t>({root, smaller});

    test_leaf<uint32_t>(tested, root, true, false);
    test_leaf<uint32_t>(tested->left(), smaller, false, false);
}

TEST_CASE("hidden_same_number_at_left_node") {
    auto root = lcg_distinct(19, 1, 500)[0] + 500;
    auto tested = make_tree<uint32_t>({root, root});

    test_leaf<uint32_t>(tested, root, true, false);
    test_leaf<uint32_t>(tested->left(), root, false, false);
}

TEST_CASE("hidden_greater_number_at_right_node") {
    auto root = lcg_distinct(23, 1, 500)[0] + 100;
    uint32_t bigger = root + 251;
    auto tested = make_tree<uint32_t>({root, bigger});

    test_leaf<uint32_t>(tested, root, false, true);
    test_leaf<uint32_t>(tested->right(), bigger, false, false);
}

TEST_CASE("hidden_can_create_complex_tree") {
    // Seven distinct values, insertion order chosen so the tree shape matches the assertions below:
    // root, then two children, then four grandchildren, each strictly on the correct side.
    auto root = 500u;
    auto left = 250u, right = 750u;
    auto ll = 125u, lr = 375u, rl = 625u, rr = 875u;
    auto tested = make_tree<uint32_t>({root, left, right, ll, lr, rl, rr});

    test_leaf<uint32_t>(tested, root, true, true);
    test_leaf<uint32_t>(tested->left(), left, true, true);
    test_leaf<uint32_t>(tested->right(), right, true, true);

    test_leaf<uint32_t>(tested->left()->left(), ll, false, false);
    test_leaf<uint32_t>(tested->left()->right(), lr, false, false);

    test_leaf<uint32_t>(tested->right()->left(), rl, false, false);
    test_leaf<uint32_t>(tested->right()->right(), rr, false, false);
}

TEST_CASE("hidden_can_sort_single_number") {
    auto values = lcg_distinct(29, 1, 1000);
    test_sort(make_tree<uint32_t>({values[0]}), {values[0]});
}

TEST_CASE("hidden_can_sort_if_second_number_is_smaller_than_first") {
    test_sort(make_tree<uint32_t>({80u, 30u}), {30u, 80u});
}

TEST_CASE("hidden_can_sort_if_second_number_is_same_as_first") {
    test_sort(make_tree<uint32_t>({80u, 80u}), {80u, 80u});
}

TEST_CASE("hidden_can_sort_if_second_number_is_greater_than_first") {
    test_sort(make_tree<uint32_t>({30u, 80u}), {30u, 80u});
}

TEST_CASE("hidden_can_sort_complex_tree") {
    // A permutation of nine distinct values, unrelated to the public test's six.
    std::vector<uint32_t> values = {40u, 15u, 60u, 5u, 25u, 50u, 70u, 1u, 90u};
    std::vector<uint32_t> expected = values;
    std::sort(expected.begin(), expected.end());
    test_sort(make_tree<uint32_t>(values), expected);
}

TEST_CASE("hidden_can_create_complex_tree_strings") {
    auto tested = make_tree<std::string>({"mango", "fig", "papaya", "date", "guava", "nectarine", "quince"});

    test_leaf<std::string>(tested, "mango", true, true);
    test_leaf<std::string>(tested->left(), "fig", true, true);
    test_leaf<std::string>(tested->right(), "papaya", true, true);

    test_leaf<std::string>(tested->left()->left(), "date", false, false);
    test_leaf<std::string>(tested->left()->right(), "guava", false, false);

    test_leaf<std::string>(tested->right()->left(), "nectarine", false, false);
    test_leaf<std::string>(tested->right()->right(), "quince", false, false);
}

TEST_CASE("hidden_can_sort_complex_tree_strings") {
    std::vector<std::string> values = {"zebra", "kiwi", "otter", "walrus", "ibex", "seal", "yak", "narwhal"};
    std::vector<std::string> expected = values;
    std::sort(expected.begin(), expected.end());
    test_sort(make_tree<std::string>(values), expected);
}
