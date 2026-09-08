"""Hidden acceptance for D1: the selector default, the export on the launch line, the header, the two checks."""
from pathlib import Path
R = Path(__file__).resolve().parents[1]
def text(p): return (R / p).read_text()
def test_env_default_line_after_device():
    lines = text("harness/env.sh").splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(': "${A770B_DEVICE:=Vulkan0}"'))
    assert lines[i + 1].startswith(': "${A770B_VK_DEVICE_SELECT:=8086:56a0!}"')
def test_serve_launches_under_selector():
    t = text("harness/serve_a770_llamacpp.sh")
    assert 'MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" nohup "$A770B_LLAMA_BIN" -m "$MODEL"' in t
    assert "A770B_DEVICE, A770B_VK_DEVICE_SELECT, A770B_PORT" in t
def test_selftest_has_two_device_checks():
    t = text("tests/selftest.sh")
    assert t.count("ok   device:") == 2
    rm = t.index('rm -rf "$t"'); assert t.index("ok   device: the server starts under the selector") < rm
