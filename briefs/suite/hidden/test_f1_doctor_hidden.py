"""Hidden acceptance for F1: the warnings field, the doctor arm, its header and usage lines."""
import json, os, subprocess, sys
from pathlib import Path
R = Path(__file__).resolve().parents[1]
def test_card_warnings_field_and_inversion_text():
    env = {k: v for k, v in os.environ.items() if not (k.startswith("A770B_") and (k.endswith("_MODEL") or k.endswith("_CTX")))}
    env["A770B_FAST_MODEL"] = "Qwen3.5-9B-Q4_K_M.gguf"
    r = subprocess.run([sys.executable, str(R / "harness/profiles.py"), "card", "--file", str(R / "config/profiles.json"), "--served"], capture_output=True, text=True, env=env)
    d = json.loads(r.stdout); w = d["warnings"]
    assert len(w) == 1 and w[0].startswith("profile fast serves long's file (Qwen3.5-9B-Q4_K_M.gguf): A770B_FAST_MODEL comes from the environment or builder.env")
def test_doctor_arm_header_usage():
    t = (R / "skills/local-build/scripts/local-build.sh").read_text()
    assert "\n  doctor)" in t and "doctor: all checks passed" in t and "MISSING profiles:" in t
    assert "#   doctor            what this machine lacks" in t and "| profiles [--name N] | doctor" in t
def test_selftest_has_two_doctor_checks():
    t = (R / "tests/selftest.sh").read_text()
    assert t.count("ok   doctor:") == 2
