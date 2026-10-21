#!/usr/bin/env python3
"""compose_override.py — write the runtime compose override that carries one profile's llama-server argv.

The tracked compose file is the ENVELOPE only (image, devices, groups, mounts, port, entrypoint); it never bakes a
model or a context, because a model written into YAML would be a second registry beside config/registry/.
harness/serve_compose.sh builds the argv from the registry (the same fields the host server reads) and this helper
writes it into a small override file that is merged over the envelope with a second `-f`.

The output is JSON, which is valid YAML, so there is no YAML emitter and no quoting bug: an `extra` word such as
--chat-template-kwargs '{"reasoning_effort":"low"}' round-trips exactly, where a hand-built YAML list would have to
re-quote it. The file lives under $A770B_DATA/logs and is gitignored by its location, not by the repository.

Usage:
  compose_override.py --out FILE -- <argv...>
"""

import argparse
import hashlib
import json
import sys


def argv_signature(entrypoint, argv):
    """sha256 hex of the entrypoint string, then each argv word, joined by NUL.

    An empty entrypoint is an empty first field. It is not omitted.
    """
    fields = ["" if entrypoint is None else str(entrypoint)]
    fields.extend(str(word) for word in argv)
    return hashlib.sha256(b"\0".join(field.encode() for field in fields)).hexdigest()


def llama_service_signature(service):
    """The argv signature of one compose services.llama object.

    A missing entrypoint is empty. command is the argv word list.
    """
    if not isinstance(service, dict):
        raise ValueError("services.llama is not an object")
    entrypoint = service.get("entrypoint", None)
    if entrypoint is None:
        entrypoint_s = ""
    elif isinstance(entrypoint, list):
        entrypoint_s = " ".join(str(part) for part in entrypoint)
    elif isinstance(entrypoint, str):
        entrypoint_s = entrypoint
    else:
        raise ValueError("services.llama.entrypoint is not a string or a list")
    command = service.get("command")
    if not isinstance(command, list):
        raise ValueError("services.llama.command is not a list")
    return argv_signature(entrypoint_s, command)


def override(argv, entrypoint=None):
    """The compose override document for an argv list."""
    svc = {}
    if entrypoint:
        ep = list(entrypoint) if isinstance(entrypoint, (list, tuple)) else entrypoint.split()
        if ep:
            svc["entrypoint"] = ep
    svc["command"] = list(argv)
    return {"services": {"llama": svc}}


def main(argv):
    parser = argparse.ArgumentParser(prog="compose_override.py")
    parser.add_argument("--out", required=True, help="the override file to write")
    parser.add_argument("--entrypoint", default=None, help="the container entrypoint")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="the llama-server argv, after --")
    args = parser.parse_args(argv[1:])
    words = args.argv
    if words and words[0] == "--":
        words = words[1:]
    if not words:
        print("compose_override.py: refusing an empty argv", file=sys.stderr)
        return 2
    ep = args.entrypoint.split() if (args.entrypoint and args.entrypoint.strip()) else None
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(override(words, entrypoint=ep), fh, indent=2)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
