"""Hidden acceptance for H2: the shellcheck block sits last in tests/selftest.sh, before the cleanup, verbatim."""
from pathlib import Path
R = Path(__file__).resolve().parents[1]
def test_block_placed_and_verbatim():
    lines = (R / "tests/selftest.sh").read_text().splitlines()
    rm = next(i for i, l in enumerate(lines) if l.startswith('rm -rf "$t"'))
    block = lines[rm-5:rm]
    assert block[0].startswith("# the linter over every bash file, when a shellcheck can be found") or block[0].startswith("# shellcheck over every bash file")
    assert block[1].startswith('if command -v shellcheck >/dev/null 2>&1; then SC="shellcheck"; elif command -v uvx') and 'uvx --offline --from shellcheck-py shellcheck' in block[1]
    assert block[2] == 'if [ -n "$SC" ]; then'
    assert block[3].startswith('  if $SC -S warning "$here"/harness/*.sh') and 'fail=1' in block[3]
    assert block[4].startswith('else echo "skip shellcheck: none on PATH and none in the uv cache') and block[4].endswith('; fi')
    assert lines[rm+1].startswith('if [ "$fail" = 0 ]') and lines[rm+2] == 'exit "$fail"'
