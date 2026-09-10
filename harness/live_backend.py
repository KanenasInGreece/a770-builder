#!/usr/bin/env python3
"""live_backend.py — identity of the llama.cpp server that is up, as a JSON sidecar.

`/health` proves a server answers. It does not prove which image is answering: the same
port can be Vulkan or another backend, display or inference, one GGUF or another.
The menu that splits ready from also rows needs the identity written when the server
starts and removed when it stops.

The file is `$A770B_DATA/logs/live-backend.json`. This module takes the path as an
argument; it never reads `/health` and never calls curl. A read is the object only
when the file is valid JSON of the contract and the pidfile names a live pid;
otherwise it returns None and unlinks the sidecar, so a stale identity cannot linger.

Usage:
  live_backend.py write  --path P --card C --backend B --mode M --model F --profile N --pid PID
  live_backend.py clear  --path P
  live_backend.py read   --path P --pidfile F
"""

import argparse
import json
import os
import re
import sys

KEYS = ("card", "backend", "mode", "model", "profile", "pid")
TOKEN = re.compile(r"^[a-z][a-z0-9-]*$")
MODES = ("display", "inference")


def _validate(record):
    """Raise ValueError unless record is exactly the sidecar contract."""
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    if set(record) != set(KEYS):
        raise ValueError("record keys must be exactly card, backend, mode, model, profile, pid")
    for key in ("card", "backend", "profile"):
        val = record[key]
        if not isinstance(val, str) or not TOKEN.fullmatch(val):
            raise ValueError(f"bad {key}")
    if record["mode"] not in MODES:
        raise ValueError("bad mode")
    model = record["model"]
    if not isinstance(model, str) or not model or "/" in model:
        raise ValueError("bad model")
    pid = record["pid"]
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise ValueError("bad pid")


def _alive(pid):
    """True when os.kill(pid, 0) succeeds (the pid is live)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write(path, record):
    """Validate, create parent dirs, write JSON plus a trailing newline."""
    _validate(record)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {k: record[k] for k in KEYS}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def clear(path):
    """Unlink path if it exists; not an error if missing."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def read(path, pidfile):
    """The sidecar object, or None (and the sidecar gone) when identity is not live."""
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        _validate(record)
        with open(pidfile, encoding="utf-8") as fh:
            live = int(fh.read().strip())
    except (OSError, ValueError, TypeError):
        clear(path)
        return None
    if not _alive(live):
        clear(path)
        return None
    return {k: record[k] for k in KEYS}


def main(argv):
    parser = argparse.ArgumentParser(prog="live_backend.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--path", required=True)
    w.add_argument("--card", required=True)
    w.add_argument("--backend", required=True)
    w.add_argument("--mode", required=True)
    w.add_argument("--model", required=True)
    w.add_argument("--profile", required=True)
    w.add_argument("--pid", required=True, type=int)
    c = sub.add_parser("clear")
    c.add_argument("--path", required=True)
    r = sub.add_parser("read")
    r.add_argument("--path", required=True)
    r.add_argument("--pidfile", required=True)
    args = parser.parse_args(argv[1:])
    if args.cmd == "write":
        write(args.path, {
            "card": args.card,
            "backend": args.backend,
            "mode": args.mode,
            "model": args.model,
            "profile": args.profile,
            "pid": args.pid,
        })
        return 0
    if args.cmd == "clear":
        clear(args.path)
        return 0
    obj = read(args.path, args.pidfile)
    if obj is None:
        return 1
    print(json.dumps(obj))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
