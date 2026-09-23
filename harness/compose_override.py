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
import json
import sys


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
