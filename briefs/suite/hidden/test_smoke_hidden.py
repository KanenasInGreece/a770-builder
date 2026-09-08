from pathlib import Path
def test_smoke_file_says_ok():
    assert Path("Local_Documentation/local_build_smoke.txt").read_text().strip() == "local-build OK"
