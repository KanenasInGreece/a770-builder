"""Hidden acceptance for H1: the exact lines the brief named, present and absent. Runs from the seat root."""
from pathlib import Path
R = Path(__file__).resolve().parents[1]
def text(p): return (R / p).read_text()
def test_unused_counters_renamed():
    for p, n in [("harness/bench_model.sh", 150), ("harness/serve_a770_llamacpp.sh", 150), ("skills/local-build/scripts/local-build.sh", 90)]:
        t = text(p); assert f"for _ in $(seq 1 {n}); do" in t and f"for i in $(seq 1 {n}); do" not in t, p
    t = text("harness/sandbox_run.sh"); assert "for _ in 1 2 3 4 5 6 7 8 9 10; do" in t and "for i in 1 2 3" not in t
def test_ctx_sweep_sizes_array():
    t = text("harness/ctx_sweep.sh")
    assert 'SIZES=("$@"); [ "${#SIZES[@]}" -gt 0 ] || SIZES=(8000 32000 64000 100000 120000)' in t
    assert 'for want in "${SIZES[@]}"; do' in t and 'for want in ${SIZES[@]}; do' not in t and '${@:-8000' not in t
def test_env_directive_on_line_7():
    lines = text("harness/env.sh").splitlines()
    assert lines[6] == "# shellcheck disable=SC1090 # builder.env is named at run time; the linter cannot follow it and need not"
    assert lines[7].startswith("_a770b_load(){")
def test_local_build_directive_bare():
    lines = text("skills/local-build/scripts/local-build.sh").splitlines()
    i = lines.index("  # shellcheck disable=SC2086")
    assert lines[i-1] == "  # extra is a deliberate word list from the env, expanded unquoted on purpose so each word is an argument"
    assert not any("disable=SC2086  (" in l for l in lines)
def test_pipefail_on_four():
    for p in ["harness/ctx_sweep.sh", "harness/depth_probe.sh", "harness/cancel_repro.sh", "tests/selftest.sh"]:
        t = text(p); assert "set -uo pipefail\n" in t and "\nset -u\n" not in t, p
