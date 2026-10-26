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
PUBLISHED = {
    ROOT_FP,
    "3094025792039A68AF6E553686410BDAB7C0D4A36B9084CBA64202E35D6EBBDD",
    "7137FA71AD46BA686F746D1014AC57E8E6C6E9AF4AA2F73C0C44A5CF27DEBB59",
    ISSUED,
    "0164D7CEAE5BB909C272AF151FF44B1177AE9B47D9424B94F1D9558C30934D19",
    "E7568FE6E12331F4FD7DE61DA4C7559D0F8EB192A218D4741A3BA75184333838",
    "432EC25F0D28DE9006D97BF752F17BBB2CD41F669933FCB072D9A1148D0261F3",
    "98B896625C0C230ADEEC3EB2CF6AAD5001ECF9156A68644E82374CE5833BC25B",
    "CB6E15165AC802E9B3F993F16C25D41F4E81C21EBDE71AE1B012AC00BF9C7C3F",
    "9E39DE2881273ACC225A149C72BA3E0F51424963669AB633EE1DC7DF5DB067B4",
    "4B2460A466F95FD40113BCDCCA2CEB29B3B093921DE45474DA7DF48874C630B1",
    "45A883F349AEDFD5F3A649B5498FFB121712BA8B448F3F2A91DBC5A766FCDDBE",
    "62B7F9868263F72F6911FC78CEAF72C1979158B7368BF648683BB2AA39B02E72",
    "579480AEA166290BD3C643589E795FC0D0E54E86505D180F1DFF99EFD3199512",
    "FD9D5C2DBBD4995AB69B8D50C439D9527B00529199C2FD01B2174EB58F07021F",
    "5F1DED4DD590AF74631290CF073B122A2AAC6A1FA479778A0BD43579BD832699",
    "7485B51C0ED192878FA1D2F948FB529AE1842CC5086C4B67F7BE5B2039DE9489",
    "A496E23AC0CAB8155FC9B117B97EAE368EFCBB9041AEC8804D5A308D2D01C2D8",
    CROSS,
}


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


def _body_line(pem: Path) -> str:
    for line in pem.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("-----"):
            return line
    raise AssertionError(pem)


def test_the_list_is_the_compromised_certificates_only(tmp_path: Path) -> None:
    result = _run(tmp_path, 'ca_load_fingerprints; cat "$CA_FINGERPRINT_FILE"')
    assert result.returncode == 0, result.stderr
    digests = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    assert digests == PUBLISHED
    assert AAA not in digests
    assert SELF_TLS not in digests
    assert SELF_CS not in digests


def test_a_planted_fingerprint_file_is_not_the_list(tmp_path: Path) -> None:
    planted = tmp_path / "fingerprints"
    planted.write_text(AAA + "\n", encoding="utf-8")
    link = tmp_path / "via-null"
    link.symlink_to("/dev/null")
    result = _run(
        tmp_path,
        "\n".join(
            [
                f'export CA_WORK="{tmp_path}"',
                f'export CA_FINGERPRINT_FILE="{planted}"',
                f'export CA_OWN_DIR="{tmp_path}"',
                "ca_load_fingerprints",
                'printf "%s\\n" "$CA_FINGERPRINT_FILE"',
                f'export CA_FINGERPRINT_FILE="{link}"',
                "ca_load_fingerprints",
                'printf "%s\\n" "$?"',
            ]
        ),
    )
    assert result.returncode == 0, result.stderr
    assert planted.read_text(encoding="utf-8") == AAA + "\n"
    assert link.is_symlink()
    assert os.readlink(link) == "/dev/null"
    path = result.stdout.splitlines()[0]
    assert path != str(planted)
    assert path != str(link)
    listed = Path(path).read_text(encoding="utf-8")
    assert {line.strip() for line in listed.splitlines() if line.strip()} == PUBLISHED


def test_a_certificate_without_the_root_name_is_not_refused(tmp_path: Path) -> None:
    pem = _cert(tmp_path, "mid.pem", "EM DV TLS CA - G3B")
    result = _run(tmp_path, f'bundle_has_root "{pem}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "1", result.stderr


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
    (certs / "root.der").write_text("x\n", encoding="utf-8")
    listed = tmp_path / "list"
    result = _run(
        tmp_path,
        f': > "{listed}"; ca_append_trust_files "{listed}" "{certs}"; cat "{listed}"',
    )
    assert result.returncode == 0, result.stderr
    names = {Path(line).name for line in result.stdout.splitlines() if line}
    assert names == {
        "064e0aa9.0", "064e0aa9.1", "064e0aa9.2", "064e0aa9.10",
        "064e0aa9.3", "root.der",
    }


def test_strip_drops_only_the_refused_certificate(tmp_path: Path) -> None:
    keep = _cert(tmp_path, "keep.pem", "Example Keep")
    drop = _cert(tmp_path, "drop.pem", "emSign ECC Root CA - G3")
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(keep.read_text(encoding="utf-8") + drop.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "out.pem"
    result = _run(
        tmp_path,
        f'ca_strip_refused "{bundle}" "{out}"; printf "%s\\n" "$?"',
    )
    assert result.stdout.strip() == "0", result.stderr
    text = out.read_text(encoding="utf-8")
    assert _body_line(keep) in text
    assert _body_line(drop) not in text


def test_a_hash_symlink_to_an_extensionless_certificate_is_removed(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    bad = _cert(tmp_path, "bad.pem", "emSign ECC Root CA - G3")
    target = certs / "em-dv-g3b"
    target.write_bytes(bad.read_bytes())
    link = certs / "064e0aa9.3"
    link.symlink_to("em-dv-g3b")
    keep = _cert(tmp_path, "keep.pem", "Example Keep")
    result = _run(tmp_path, f'ca_dispose_bundle "{link}" "{keep}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "0", result.stderr
    assert not link.exists()
    assert not link.is_symlink()
    text = target.read_text(encoding="utf-8")
    assert "emSign ECC Root CA - G3" not in text
    assert _body_line(keep) in text


def test_a_dangling_hash_symlink_is_not_a_certificate(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    link = certs / "064e0aa9.4"
    link.symlink_to("missing")
    keep = tmp_path / "keep.pem"
    keep.write_text("clean\n", encoding="utf-8")
    result = _run(
        tmp_path,
        f'bundle_has_root "{link}"; printf "%s\\n" "$?"; ca_dispose_bundle "{link}" "{keep}"; printf "%s\\n" "$?"',
    )
    assert result.stdout.splitlines() == ["1", "0"], result.stderr
    assert not link.is_symlink()


def test_a_der_certificate_with_the_root_name_is_refused(tmp_path: Path) -> None:
    pem = _cert(tmp_path, "named.pem", "emSign ECC Root CA - G3")
    der = tmp_path / "root.der"
    subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-outform", "DER", "-out", str(der)],
        check=True,
        capture_output=True,
    )
    result = _run(tmp_path, f'bundle_has_root "{der}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "0", result.stderr


def test_a_hash_symlink_to_a_library_is_not_overwritten(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    library = certs / "hf_xet.abi3.so"
    library.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    link = certs / "064e0aa9.9"
    link.symlink_to("hf_xet.abi3.so")
    keep = _cert(tmp_path, "keep.pem", "Example Keep")
    result = _run(tmp_path, f'ca_dispose_bundle "{link}" "{keep}"; printf "%s\\n" "$?"')
    assert result.stdout.strip() == "2", result.stderr
    assert library.read_text(encoding="utf-8") == "emSign ECC Root CA - G3\n"
    assert not link.is_symlink()


def test_embedded_carriers_are_text_scanned(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "hf_xet").mkdir(parents=True)
    library = tree / "hf_xet" / "hf_xet.abi3.so"
    library.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    rlib = tree / "libwebpki_roots.rlib"
    rlib.write_text("EM DV TLS CA - G3B\n", encoding="utf-8")
    store = tree / "cert9.db"
    store.write_text("clean nss\n", encoding="utf-8")
    note = tree / "note.txt"
    note.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    other = tree / "libllama-common.so.0"
    other.write_text("plain\n", encoding="utf-8")
    listed = tmp_path / "list"
    result = _run(
        tmp_path,
        "\n".join(
            [
                f': > "{listed}"',
                f'ca_append_embedded_files "{listed}" "{tree}"',
                f'cat "{listed}"',
                f'ca_clear_embedded "{library}"; printf "%s\\n" "$?"',
                f'ca_clear_embedded "{rlib}"; printf "%s\\n" "$?"',
                f'ca_clear_embedded "{store}"; printf "%s\\n" "$?"',
            ]
        ),
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    names = {Path(line).name for line in lines if line and not line.isdigit()}
    assert names == {"hf_xet.abi3.so", "libwebpki_roots.rlib", "cert9.db", "libllama-common.so.0"}
    assert "note.txt" not in names
    codes = [line for line in lines if line.isdigit()]
    assert codes == ["2", "0", "0"], result.stdout
    assert library.read_text(encoding="utf-8") == "emSign ECC Root CA - G3\n"
    assert not rlib.exists()
    assert store.read_text(encoding="utf-8") == "clean nss\n"


def test_the_known_rust_libraries_are_left_in_place(tmp_path: Path) -> None:
    harmony = tmp_path / "site-packages" / "openai_harmony" / "openai_harmony.abi3.so"
    harmony.parent.mkdir(parents=True)
    harmony.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    other = tmp_path / "site-packages" / "hf_xet" / "hf_xet.abi3.so"
    other.parent.mkdir(parents=True)
    other.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    result = _run(
        tmp_path,
        "\n".join(
            [
                f'ca_clear_embedded "{harmony}"; printf "%s\\n" "$?"',
                f'ca_embedded_refused "{harmony}"; printf "%s\\n" "$?"',
                f'ca_embedded_refused "{other}"; printf "%s\\n" "$?"',
            ]
        ),
    )
    assert result.stdout.splitlines() == ["0", "1", "0"], result.stderr
    assert harmony.read_text(encoding="utf-8") == "emSign ECC Root CA - G3\n"
    built = tmp_path / "sglang" / "rust" / "target" / "release" / "deps" / "libsglang_server.so"
    built.parent.mkdir(parents=True)
    built.write_text("emSign ECC Root CA - G3\n", encoding="utf-8")
    removed = _run(tmp_path, f'ca_clear_embedded "{built}"; printf "%s\\n" "$?"')
    assert removed.stdout.strip() == "0", removed.stderr
    assert not built.exists()


def test_the_image_build_and_the_serve_scan_use_the_same_check() -> None:
    serve = (ROOT / "harness" / "serve_compose.sh").read_text(encoding="utf-8")
    docker = (ROOT / "compose" / "refresh-ca.Dockerfile").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "ca-trust-check.sh" in serve
    assert "ca_append_trust_files" in serve
    assert "ca_append_embedded_files" in serve
    assert "ca_embedded_refused" in serve
    assert "ca_text_has_compromised" in script
    assert "ca_clear_embedded" not in serve
    assert "ca-trust-check.sh" in docker
    assert "ca_append_trust_files" in docker
    assert "ca_append_embedded_files" in docker
    assert "ca_dispose_bundle" in docker
    assert "ca_embedded_refused" in docker
    assert "ca_residual_library" in script
    assert "-name '*.0'" not in docker
    assert '-name "*.0"' not in serve
    assert "bugzilla.mozilla.org/show_bug.cgi?id=2083449" in script
    assert "GHSA-6q48-6c7w-9gw2" in script
