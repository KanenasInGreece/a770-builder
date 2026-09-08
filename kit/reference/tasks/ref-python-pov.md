# Task: implement `from_pov` and `path_to` in `kit/reference/python/pov/pov.py`

You are in a standalone clone of KanenasInGreece/a770-builder, branch `profiling-kit`. The task is implementing
two methods in one file, `kit/reference/python/pov/pov.py`, and nothing else. Do not create files. Do not edit
any file not named below. This is Aider's polyglot-benchmark Python exercise `pov` (Exercism practice track,
MIT licence 2021 — see `kit/reference/SOURCES.md` and the `LICENSE` beside this exercise), carried into this
kit verbatim.

## The files

- `kit/reference/python/pov/pov.py` — the file to implement. The `Tree` class already has `__init__`,
  `__dict__`, `__str__`, `__lt__` and `__eq__`; `from_pov` and `path_to` are `pass` stubs.
- `kit/reference/python/pov/pov_test.py` — the exercise's own test file. Read it, never edit it: it builds
  `Tree` instances and calls `.from_pov(node)` and `.path_to(from_node, to_node)`.
- `kit/reference/python/pov/.docs/instructions.append.md` — worth reading: it says both methods must raise
  `ValueError` with the exact messages the tests check for, not just any exception.

## The exercise's instructions, verbatim

````
# Instructions

Reparent a tree on a selected node.

A [tree][wiki-tree] is a special type of [graph][wiki-graph] where all nodes are connected but there are no cycles.
That means, there is exactly one path to get from one node to another for any pair of nodes.

This exercise is all about re-orientating a tree to see things from a different point of view.
For example family trees are usually presented from the ancestor's perspective:

```text
    +------0------+
    |      |      |
  +-1-+  +-2-+  +-3-+
  |   |  |   |  |   |
  4   5  6   7  8   9
```

But there is no inherent direction in a tree.
The same information can be presented from the perspective of any other node in the tree, by pulling it up to the root and dragging its relationships along with it.
So the same tree from 6's perspective would look like:

```text
        6
        |
  +-----2-----+
  |           |
  7     +-----0-----+
        |           |
      +-1-+       +-3-+
      |   |       |   |
      4   5       8   9
```

This lets us more simply describe the paths between two nodes.
So for example the path from 6-9 (which in the first tree goes up to the root and then down to a different leaf node) can be seen to follow the path 6-2-0-3-9.

This exercise involves taking an input tree and re-orientating it from the point of view of one of the nodes.

[wiki-graph]: https://en.wikipedia.org/wiki/Tree_(graph_theory)
[wiki-tree]: https://en.wikipedia.org/wiki/Graph_(discrete_mathematics)
````

## The change

Implement `Tree.from_pov(self, from_node)`: return a new tree re-rooted at the node labelled `from_node` (the
old parent becomes a child of the node that used to be its child, and so on up to the old root); raise
`ValueError('Tree could not be reoriented')` when `from_node` is not in the tree. Implement `Tree.path_to(self,
from_node, to_node)`: return the list of labels from `from_node` to `to_node` inclusive, in order; raise
`ValueError('Tree could not be reoriented')` when `from_node` is not in the tree and `ValueError('No path
found')` when `to_node` is not in the tree.

## Text that must appear as written

```
Tree could not be reoriented
No path found
```

## Verify — run exactly this and print its last two lines

```
uv run --with pytest python -m pytest -q kit/reference/python/pov/pov_test.py
```

Expected: `15 passed in <time>s` and `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state (no commit, push, merge, branch
switch). No docker, no systemctl, no network. Keep every edit on one physical line; never re-wrap.

## Stop when

`pytest -q kit/reference/python/pov/pov_test.py` reports `15 passed` with no failures. Then print the list of
files you changed.

## A run specification beside this brief

`ref-python-pov.spec.json`, beside this file. Its `bash_allow` carries no entry either: the grader is already
covered by the template's own floor (`uv run *`, `python3 -m pytest *`), and a `bash_allow` addition starting
with `uv` would in any case be refused (see `kit/reference/SOURCES.md`).
