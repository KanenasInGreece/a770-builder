# tests/

This folder holds the tests that check the harness itself works, not the tests a profiled model is graded against.
`selftest.sh` proves the harness's own scripts behave correctly without needing the card; `kit_selftest.sh` proves
the profiling kit's tasks and graders work inside the real sandbox boundary; the Python files are the project's own
pytest suite, run on the host.
