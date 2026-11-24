"""Install the qualified OpenCode 1.19.6 CLI into a private directory.

The tarball checksum is the upstream release asset. A member is accepted only when
it is a regular file named opencode, with no link and no parent-directory step.
"""
import argparse
import hashlib
import os
import sys
import tarfile
from pathlib import Path

ASSETS = {
    ("linux", "x86_64"): (
        "opencode-linux-x64.tar.gz",
        "47734e08968b30cc22a0bffb6d9feeed76ad4aa743213a12122040392d09a057",
    ),
    ("linux", "aarch64"): (
        "opencode-linux-arm64.tar.gz",
        "5b68c1f4c5e75b4134e664cc8f36d634d64208d4fcfe5bcf3b48bab699c2dbac",
    ),
}
URL = "https://github.com/anomalyco/opencode/releases/download/v1.19.6/"


def asset(system, machine):
    row = ASSETS.get((system, machine))
    if row is None:
        return None
    name, sha = row
    return name, sha, URL + name


def _regular_opencode(member):
    name = member.name
    parts = Path(name).parts
    if not parts or name.startswith("/") or ".." in parts:
        return False
    if not member.isfile() or member.issym() or member.islnk():
        return False
    return parts[-1] == "opencode"


def install(tarball, dest, sha):
    blob = Path(tarball).read_bytes()
    if hashlib.sha256(blob).hexdigest() != sha:
        print("checksum mismatch", file=sys.stderr)
        return 2
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o755)
    with tarfile.open(tarball, "r:*") as tar:
        matches = [m for m in tar.getmembers() if _regular_opencode(m)]
        top = [m for m in matches if m.name == "opencode"]
        if len(top) == 1:
            chosen = top[0]
        elif len(matches) == 1:
            chosen = matches[0]
        else:
            print("tarball has no single opencode binary", file=sys.stderr)
            return 2
        extracted = tar.extractfile(chosen)
        if extracted is None:
            print("tarball member is not a file", file=sys.stderr)
            return 2
        payload = extracted.read()
    tmp = dest / ".opencode.new"
    tmp.write_bytes(payload)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest / "opencode")
    return 0


def main(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("asset")
    show.add_argument("--system", default=os.uname().sysname.lower())
    show.add_argument("--machine", default=os.uname().machine)
    put = sub.add_parser("install")
    put.add_argument("--tarball", required=True)
    put.add_argument("--dest", required=True)
    put.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "asset":
        row = asset(args.system, args.machine)
        if row is None:
            return 2
        print("\n".join(row))
        return 0
    return install(args.tarball, args.dest, args.sha256)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
