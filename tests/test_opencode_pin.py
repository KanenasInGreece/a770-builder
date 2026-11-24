"""Offline checks for the qualified OpenCode install.

A profile run with an empty directory selector installs 1.19.6 when PATH is not
OpenCode 1. These tests never download that tarball. An explicit directory is
left as given, and a bad archive leaves no binary.
"""
import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_SH = ROOT / "harness" / "env.sh"
PIN = ROOT / "harness" / "opencode_pin.py"
DOCTOR_SH = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"

V1 = """#!/bin/sh
if [ "${1:-}" = --version ]; then printf '%s\\n' '1.19.6'; exit 0; fi
if [ "${1:-}" = run ] && [ "${2:-}" = --help ]; then printf '%s\\n' '  --dir   directory to run in'; exit 0; fi
exit 0
"""
V2 = """#!/bin/sh
if [ "${1:-}" = --version ]; then printf '%s\\n' 'opencode v2.2.0'; exit 0; fi
exit 0
"""


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _reg(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o755
    info.size = len(data)
    return info, data


def _link(name: str, kind: bytes, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info, None


def _tar(path: Path, members: list) -> str:
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install(tarball: Path, dest: Path, sha: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PIN), "install", "--tarball", str(tarball), "--dest", str(dest), "--sha256", sha],
        capture_output=True, text=True,
    )


def _env(tmp_path: Path, **extra) -> dict:
    bindir = tmp_path / "path-bin"
    _exe(bindir / "opencode", V2)
    home = tmp_path / "oc-home"
    home.mkdir(exist_ok=True)
    data = tmp_path / "data"
    data.mkdir()
    env = dict(os.environ)
    env.update(
        A770B_PROJECT=str(ROOT),
        A770B_DATA=str(data),
        A770B_OPENCODE_BIN="",
        A770B_OPENCODE_HOME=str(home),
        A770B_OPENCODE_FETCH="0",
        A770B_OPENCODE_URL="",
        A770B_OPENCODE_SHA256="",
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
        XDG_DATA_HOME=str(tmp_path / "share"),
        HOME=str(tmp_path / "home"),
        PATH=f"{bindir}:{os.environ['PATH']}",
    )
    (tmp_path / "home").mkdir()
    (tmp_path / "xdg").mkdir()
    env.update(extra)
    return env


def _bash(tmp_path: Path, body: str, **extra) -> subprocess.CompletedProcess:
    script = f'. "{ENV_SH}"; {body}; rc=$?; printf "%s" "$A770B_OPENCODE_BIN"; exit $rc'
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=ROOT, env=_env(tmp_path, **extra),
    )


def test_asset_refuses_an_unknown_machine():
    result = subprocess.run(
        [sys.executable, str(PIN), "asset", "--system", "darwin", "--machine", "arm64"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""


def test_install_rejects_a_bad_checksum_and_leaves_no_binary(tmp_path):
    blob = tmp_path / "good.tar.gz"
    _tar(blob, [_reg("opencode", V1.encode())])
    dest = tmp_path / "dest"
    dest.mkdir()
    sentinel = dest / "opencode"
    sentinel.write_text("keep\n", encoding="utf-8")
    result = _install(blob, dest, "0" * 64)
    assert result.returncode == 2
    assert "checksum mismatch" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (dest / ".opencode.new").exists()


@pytest.mark.parametrize("member", [
    _link("opencode", tarfile.SYMTYPE, "elsewhere"),
    _link("opencode", tarfile.LNKTYPE, "elsewhere"),
    _reg("../opencode", b"nope"),
    _reg("/opencode", b"nope"),
])
def test_install_rejects_a_link_or_parent_member(tmp_path, member):
    blob = tmp_path / "bad.tar.gz"
    sha = _tar(blob, [member])
    dest = tmp_path / "dest"
    result = _install(blob, dest, sha)
    assert result.returncode == 2
    assert "tarball has no single opencode binary" in result.stderr
    assert not (dest / "opencode").exists()
    assert not (dest / ".opencode.new").exists()


def test_install_rejects_two_nested_binaries(tmp_path):
    blob = tmp_path / "two.tar.gz"
    sha = _tar(blob, [_reg("a/opencode", b"one"), _reg("b/opencode", b"two")])
    dest = tmp_path / "dest"
    result = _install(blob, dest, sha)
    assert result.returncode == 2
    assert not (dest / "opencode").exists()


def test_install_places_one_regular_file_and_ignores_a_readme(tmp_path):
    blob = tmp_path / "ok.tar.gz"
    sha = _tar(blob, [_reg("opencode", V1.encode()), _reg("README", b"notes")])
    dest = tmp_path / "dest"
    result = _install(blob, dest, sha)
    assert result.returncode == 0, result.stderr
    installed = dest / "opencode"
    assert installed.read_bytes() == V1.encode()
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    assert not (dest / "README").exists()
    assert not (dest / ".opencode.new").exists()


def test_fetch_selects_a_cached_binary_over_path_v2(tmp_path):
    home = tmp_path / "oc-home"
    marker = home / "opencode"
    _exe(marker, V1)
    before = marker.read_bytes()
    missing = tmp_path / "absent.tar.gz"
    result = _bash(
        tmp_path,
        "A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1",
        A770B_OPENCODE_FETCH="1",
        A770B_OPENCODE_URL=missing.as_uri(),
        A770B_OPENCODE_SHA256="0" * 64,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(home)
    assert "installed" not in result.stderr
    assert "could not install" not in result.stderr
    assert marker.read_bytes() == before
    assert str(home) not in result.stderr


def test_explicit_directory_is_not_replaced_by_a_cached_binary(tmp_path):
    home = tmp_path / "oc-home"
    _exe(home / "opencode", V1)
    chosen = tmp_path / "path-bin"
    result = _bash(
        tmp_path,
        "A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1",
        A770B_OPENCODE_BIN=str(chosen),
        A770B_OPENCODE_FETCH="1",
        A770B_OPENCODE_URL=(tmp_path / "absent.tar.gz").as_uri(),
    )
    assert result.returncode == 2
    assert "OpenCode 1 is required; 1.19.6 is the qualified release; A770B_OPENCODE_BIN must be that binary's directory" in result.stderr
    assert "could not install" not in result.stderr
    assert str(chosen) not in result.stderr
    assert str(home) not in result.stderr
    assert result.stdout == str(chosen)
    assert (home / "opencode").read_text(encoding="utf-8") == V1


def test_fetch_installs_from_a_local_tarball_when_path_is_not_opencode_1(tmp_path):
    blob = tmp_path / "pin.tar.gz"
    sha = _tar(blob, [_reg("opencode", V1.encode())])
    home = tmp_path / "oc-home"
    result = _bash(
        tmp_path,
        "A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1",
        A770B_OPENCODE_FETCH="1",
        A770B_OPENCODE_URL=blob.as_uri(),
        A770B_OPENCODE_SHA256=sha,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(home)
    assert (home / "opencode").read_bytes() == V1.encode()
    assert "OpenCode 1.19.6 installed for profile runs" in result.stderr
    assert str(home) not in result.stderr
    assert "A770B_OPENCODE_BIN must be that binary's directory" not in result.stderr


def test_require_without_fetch_does_not_download(tmp_path):
    home = tmp_path / "oc-home"
    missing = tmp_path / "absent.tar.gz"
    result = _bash(
        tmp_path,
        "a770b_require_opencode_v1",
        A770B_OPENCODE_URL=missing.as_uri(),
        A770B_OPENCODE_SHA256="0" * 64,
    )
    assert result.returncode == 2
    assert "A770B_OPENCODE_BIN must be that binary's directory" in result.stderr
    assert "could not install" not in result.stderr
    assert result.stdout == ""
    assert not (home / "opencode").exists()


def test_prepare_without_fetch_does_not_install(tmp_path):
    home = tmp_path / "oc-home"
    result = _bash(
        tmp_path,
        "a770b_prepare_opencode_v1",
        A770B_OPENCODE_URL=(tmp_path / "absent.tar.gz").as_uri(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (home / "opencode").exists()


def test_doctor_reports_a_cached_binary_and_keeps_an_explicit_mismatch(tmp_path):
    home = tmp_path / "oc-home"
    _exe(home / "opencode", V1)
    cache = tmp_path / "warm"
    cache.mkdir()
    uv = tmp_path / "path-bin" / "uv"
    _exe(uv, "#!/bin/sh\n[ \"${1:-}\" = --version ] && { echo 'uv 0.13.4'; exit 0; }\nexit 0\n")
    env = _env(
        tmp_path,
        PATH=f"{tmp_path / 'path-bin'}:/usr/bin:/bin",
        A770B_UV_CACHE=str(cache),
        A770B_SERVE="compose",
        A770B_DOCKER="docker",
        A770B_REFUSE=str(tmp_path / "nope"),
        A770B_COMPOSE_FILE=str(ROOT / "compose/a770-vulkan.yaml"),
        A770B_COMPOSE_ENV_FILE=str(tmp_path / "missing-compose.env"),
    )
    cached = subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"], capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert f"ok   opencode: {home / 'opencode'}" in cached.stdout
    assert "a profile run installs" not in cached.stdout

    chosen = tmp_path / "path-bin"
    env["A770B_OPENCODE_BIN"] = str(chosen)
    refused = subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"], capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert any(
        line.startswith("MISSING opencode:") and "OpenCode 1" in line for line in refused.stdout.splitlines()
    ), refused.stdout
    assert f"ok   opencode: {home / 'opencode'}" not in refused.stdout


def test_profile_entry_points_fetch_and_doctor_does_not():
    run = DOCTOR_SH.read_text(encoding="utf-8")
    assert run.count("A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1 || exit 2") == 1
    start = run.index("doctor(){")
    end = run.index('\nif [ "${BASH_SOURCE[0]}" = "$0" ]; then', start)
    assert "A770B_OPENCODE_FETCH=1" not in run[start:end]
    for name in ("harness/ladder.sh", "harness/build_local.sh", "harness/sandbox_run.sh", "harness/measure_overhead.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "A770B_OPENCODE_FETCH=1 a770b_require_opencode_v1 || exit 2" in text, name
