"""The trust check refuses certificates by fingerprint, not only by the root's name."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "compose" / "ca-trust-check.sh"

AAA = "D7A7A0FB5D7E2731D771E9484EBCDEF71D5F0C3E0A2948782BC83EE0EA699EF4"
SELF_TLS = "7DD78D5F4F13459A83DFF9ABBBA62EDBAF6F2D102BF257FD712F4D9F2746ED8D"
SELF_CS = "F2EDAAFF3D9B701FB4D14C9F285F8C0BAD90FC3BCC1FA07D47F4718EDABA34A5"
ROOT_FP = "86A1ECBA089C4A8D3BBE2734C612BA341D813E043CF9E8A862CD5C57A36BBE6B"
ISSUED = "42C4ACCD12C4F59597925E1A08A7A90102608543809308D50EC427E118BA1493"
CROSS = "84112972B8E59D14560AE7E7119654474AAD119990CF3722BFC1E3B91615E1FD"


def _run(tmp: Path, body: str) -> subprocess.CompletedProcess[str]:
    work = tmp / "work"
    work.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["CA_WORK"] = str(work)
    env["CA_FINGERPRINT_FILE"] = str(work / "fingerprints")
    return subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"\n{body}\n'],
        cwd=tmp,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _cert(tmp: Path, name: str, cn: str) -> Path:
    pem = tmp / name
    key = tmp / f"{name}.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", str(key), "-nodes",
            "-out", str(pem), "-subj", f"/CN={cn}", "-days", "1",
        ],
        check=True,
        capture_output=True,
    )
    return pem


def _fingerprint(pem: Path) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-fingerprint", "-sha256"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.split("=", 1)[1].replace(":", "").strip().upper()


def _body_line(pem: Path) -> str:
    for line in pem.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("-----"):
            return line
    raise AssertionError(pem)


def test_the_list_is_the_compromised_certificates_only(tmp_path: Path) -> None:
    result = _run(tmp_path, 'ca_load_fingerprints; wc -l < "$CA_FINGERPRINT_FILE"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "19"
    text = (tmp_path / "work" / "fingerprints").read_text(encoding="utf-8")
    assert ROOT_FP in text
    assert ISSUED in text
    assert CROSS in text
    assert AAA not in text
    assert SELF_TLS not in text
    assert SELF_CS not in text


def test_a_certificate_without_the_root_name_is_refused_by_fingerprint(tmp_path: Path) -> None:
    pem = _cert(tmp_path, "mid.pem", "EM DV TLS CA - G3B")
    before = _run(tmp_path, f'ca_load_fingerprints; bundle_has_root "{pem}"; printf "%s\\n" "$?"')
    assert before.stdout.strip() == "1", before.stderr
    listed = tmp_path / "work" / "fingerprints"
    listed.write_text(listed.read_text(encoding="utf-8") + _fingerprint(pem) + "\n", encoding="utf-8")
    after = _run(tmp_path, f'bundle_has_root "{pem}"; printf "%s\\n" "$?"')
    assert after.stdout.strip() == "0", after.stderr


def test_the_root_name_is_still_refused(tmp_path: Path) -> None:
    pem = _cert(tmp_path, "named.pem", "emSign ECC Root CA - G3")
    result = _run(tmp_path, f'bundle_has_root "{pem}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "0", result.stderr


def test_a_pem_header_that_cannot_be_parsed_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text(
        "-----BEGIN CERTIFICATE-----\nnot-a-cert\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, f'bundle_has_root "{bad}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "2", result.stderr


def test_a_file_that_is_not_a_certificate_is_clean(tmp_path: Path) -> None:
    note = tmp_path / "064e0aa9.1"
    note.write_text("not a certificate\n", encoding="utf-8")
    result = _run(tmp_path, f'bundle_has_root "{note}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "1", result.stderr


def test_hash_suffixes_past_zero_are_opened(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    for name in ("064e0aa9.0", "064e0aa9.1", "064e0aa9.2", "064e0aa9.10"):
        (certs / name).write_text("x\n", encoding="utf-8")
    (certs / "note.txt").write_text("x\n", encoding="utf-8")
    (certs / "libllama-common.so.0").write_text("x\n", encoding="utf-8")
    (certs / "libarrow_flight.so.2500").write_text("x\n", encoding="utf-8")
    (certs / "064e0aa9.3").symlink_to("064e0aa9.0")
    listed = tmp_path / "list"
    result = _run(
        tmp_path,
        f': > "{listed}"; ca_append_trust_files "{listed}" "{certs}"; cat "{listed}"',
    )
    assert result.returncode == 0, result.stderr
    names = {Path(line).name for line in result.stdout.splitlines() if line}
    assert names == {"064e0aa9.0", "064e0aa9.1", "064e0aa9.2", "064e0aa9.10"}


def test_strip_drops_only_the_refused_certificate(tmp_path: Path) -> None:
    keep = _cert(tmp_path, "keep.pem", "Example Keep")
    drop = _cert(tmp_path, "drop.pem", "Example Drop")
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(keep.read_text(encoding="utf-8") + drop.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "out.pem"
    result = _run(
        tmp_path,
        "\n".join(
            [
                "ca_load_fingerprints",
                f'printf "%s\\n" "{_fingerprint(drop)}" >> "$CA_FINGERPRINT_FILE"',
                f'ca_strip_refused "{bundle}" "{out}"',
                'printf "%s\\n" "$?"',
            ]
        ),
    )
    assert result.stdout.strip() == "0", result.stderr
    text = out.read_text(encoding="utf-8")
    assert _body_line(keep) in text
    assert _body_line(drop) not in text


def test_the_image_build_and_the_serve_scan_use_the_same_check() -> None:
    serve = (ROOT / "harness" / "serve_compose.sh").read_text(encoding="utf-8")
    docker = (ROOT / "compose" / "refresh-ca.Dockerfile").read_text(encoding="utf-8")
    assert "ca-trust-check.sh" in serve
    assert "ca_append_trust_files" in serve
    assert 'ca-trust-check.sh' in docker
    assert "ca_append_trust_files" in docker
    assert "-name '*.0'" not in docker
    assert '-name "*.0"' not in serve
