# Task: unit tests for `sanitize_entity_name` (matrix task T1, every candidate model gets this brief)

You are in a git worktree of the shared-memory-GitHub repository. The gateway's entity gate is the
function `sanitize_entity_name` in `shared-memory/scripts/ontology.py`. Read that function first.

Write a NEW test file `tests/test_sanitize_entity_name_matrix.py` (do not touch any other file) that:
1. Puts `../shared-memory/scripts` on `sys.path` the way the other files in `tests/` do (look at one).
2. Covers, each as its own test function with a descriptive name:
   a. ordinary names pass through (e.g. `EmbeddingServer`, `Reranker`) — assert the exact returned value;
   b. the schema vocabulary `Decision`, `Project`, `Domain` is rejected (the function returns `None`);
   c. axis declarations such as `Project: foo` and `Domain: bar` are rejected;
   d. leading/trailing whitespace is stripped from an accepted name — assert the exact value;
   e. an empty string or whitespace-only string is rejected.
   Read the function to learn the exact behaviour before asserting it; every assertion must compare
   against a concrete literal value, never against another call of the same function.
3. Run ONLY your file with:
   `uv run --with pytest --with pytest-asyncio python -m pytest tests/test_sanitize_entity_name_matrix.py -q`
   and make it pass. If a test fails because your expectation was wrong, fix the expectation after
   re-reading the function; never edit `ontology.py`.
4. Finish by printing the pytest summary line and the list of test names you wrote.

Rules: work only inside this project directory; do not use version control commands that change
state (no committing, pushing, merging or switching branches); no docker or systemctl.
