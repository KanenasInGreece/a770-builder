# test_pov_hidden.py — the hidden variant of the pov reference exercise (Aider's polyglot benchmark, Python
# track, MIT/Exercism 2021: see kit/reference/SOURCES.md). Same public interface as the exercise's own
# pov_test.py (Tree, from_pov, path_to), but every tree and every from/to pair here is fresh, written for
# this file, not copied from the exercise's own test, so a model that has memorised the public test cannot
# pass this one by memorisation alone.
#
# Loads the model's edited kit/reference/python/pov/pov.py by explicit file path, relative to the current
# working directory (the seat root, the same directory the grader command is run from — see
# kit/reference/tasks/*pov*.spec.json), rather than a bare `from pov import Tree`: this file is named in a
# specification's verify.hidden and the harness may copy it to a different directory before running pytest
# on it, so a path relative to its own location cannot be relied on.
import importlib.util
import unittest
from pathlib import Path

POV_PATH = Path("kit/reference/python/pov/pov.py")


def _load_tree_class():
    if not POV_PATH.is_file():
        raise FileNotFoundError(
            f"test_pov_hidden.py: {POV_PATH} not found relative to the current directory "
            f"({Path.cwd()}) — this test must run from the seat root"
        )
    spec = importlib.util.spec_from_file_location("pov_hidden_target", POV_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Tree


Tree = _load_tree_class()


class PovHiddenTest(unittest.TestCase):
    def assertTreeEquals(self, result, expected):
        self.assertEqual(result, expected, "{} != {}".format(result, expected))

    def test_singleton_tree_from_its_own_pov_is_unchanged(self):
        tree = Tree("root")
        expected = Tree("root")
        self.assertTreeEquals(tree.from_pov("root"), expected)

    def test_reroot_with_one_sibling(self):
        tree = Tree("mother", [Tree("me"), Tree("brother")])
        expected = Tree("me", [Tree("mother", [Tree("brother")])])
        self.assertTreeEquals(tree.from_pov("me"), expected)

    def test_reroot_with_several_siblings(self):
        tree = Tree("mother", [Tree("aunt"), Tree("me"), Tree("uncle"), Tree("cousin")])
        expected = Tree("me", [Tree("mother", [Tree("aunt"), Tree("uncle"), Tree("cousin")])])
        self.assertTreeEquals(tree.from_pov("me"), expected)

    def test_reroot_deeply_nested_target(self):
        tree = Tree(
            "gen0",
            [Tree("gen1", [Tree("gen2", [Tree("gen3", [Tree("gen4", [Tree("me")])])])])],
        )
        expected = Tree(
            "me",
            [Tree("gen4", [Tree("gen3", [Tree("gen2", [Tree("gen1", [Tree("gen0")])])])])],
        )
        self.assertTreeEquals(tree.from_pov("me"), expected)

    def test_reroot_moves_own_children_up_a_level(self):
        tree = Tree("mother", [Tree("me", [Tree("son"), Tree("daughter")])])
        expected = Tree("me", [Tree("son"), Tree("daughter"), Tree("mother")])
        self.assertTreeEquals(tree.from_pov("me"), expected)

    def test_reroot_complex_family_tree(self):
        tree = Tree(
            "great-grandmother",
            [
                Tree(
                    "grandmother",
                    [
                        Tree("me", [Tree("son"), Tree("daughter")]),
                        Tree("sister"),
                        Tree("brother"),
                    ],
                ),
                Tree("great-aunt", [Tree("cousin-a"), Tree("cousin-b")]),
            ],
        )
        expected = Tree(
            "me",
            [
                Tree("daughter"),
                Tree("son"),
                Tree(
                    "grandmother",
                    [
                        Tree("sister"),
                        Tree("brother"),
                        Tree(
                            "great-grandmother",
                            [Tree("great-aunt", [Tree("cousin-a"), Tree("cousin-b")])],
                        ),
                    ],
                ),
            ],
        )
        self.assertTreeEquals(tree.from_pov("me"), expected)

    def test_from_pov_errors_when_target_absent_singleton(self):
        tree = Tree("root")
        with self.assertRaises(ValueError) as err:
            tree.from_pov("ghost")
        self.assertEqual(type(err.exception), ValueError)
        self.assertEqual(err.exception.args[0], "Tree could not be reoriented")

    def test_from_pov_errors_when_target_absent_large_tree(self):
        tree = Tree(
            "mother",
            [Tree("me", [Tree("son"), Tree("daughter")]), Tree("sister"), Tree("brother")],
        )
        with self.assertRaises(ValueError) as err:
            tree.from_pov("ghost")
        self.assertEqual(type(err.exception), ValueError)
        self.assertEqual(err.exception.args[0], "Tree could not be reoriented")

    def test_path_to_direct_parent(self):
        tree = Tree("mother", [Tree("me"), Tree("brother")])
        expected = ["me", "mother"]
        self.assertEqual(tree.path_to("me", "mother"), expected)

    def test_path_to_sibling(self):
        tree = Tree("mother", [Tree("aunt"), Tree("me"), Tree("uncle"), Tree("cousin")])
        expected = ["me", "mother", "uncle"]
        self.assertEqual(tree.path_to("me", "uncle"), expected)

    def test_path_to_cousin(self):
        tree = Tree(
            "great-grandmother",
            [
                Tree(
                    "grandmother",
                    [
                        Tree("me", [Tree("son"), Tree("daughter")]),
                        Tree("sister"),
                        Tree("brother"),
                    ],
                ),
                Tree("great-aunt", [Tree("cousin-a"), Tree("cousin-b")]),
            ],
        )
        expected = ["me", "grandmother", "great-grandmother", "great-aunt", "cousin-b"]
        self.assertEqual(tree.path_to("me", "cousin-b"), expected)

    def test_path_to_not_via_root(self):
        tree = Tree(
            "great-grandmother",
            [Tree("grandmother", [Tree("me"), Tree("sister"), Tree("brother")])],
        )
        expected = ["me", "grandmother", "brother"]
        self.assertEqual(tree.path_to("me", "brother"), expected)

    def test_path_between_two_non_root_nodes(self):
        tree = Tree("mother", [Tree("aunt"), Tree("me"), Tree("uncle"), Tree("cousin")])
        expected = ["aunt", "mother", "cousin"]
        self.assertEqual(tree.path_to("aunt", "cousin"), expected)

    def test_path_to_errors_when_destination_absent(self):
        tree = Tree(
            "mother",
            [Tree("me", [Tree("son"), Tree("daughter")]), Tree("sister"), Tree("brother")],
        )
        with self.assertRaises(ValueError) as err:
            tree.path_to("me", "ghost")
        self.assertEqual(type(err.exception), ValueError)
        self.assertEqual(err.exception.args[0], "No path found")

    def test_path_to_errors_when_source_absent(self):
        tree = Tree(
            "mother",
            [Tree("me", [Tree("son"), Tree("daughter")]), Tree("sister"), Tree("brother")],
        )
        with self.assertRaises(ValueError) as err:
            tree.path_to("ghost", "me")
        self.assertEqual(type(err.exception), ValueError)
        self.assertEqual(err.exception.args[0], "Tree could not be reoriented")


if __name__ == "__main__":
    unittest.main()
