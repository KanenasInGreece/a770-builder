#!/usr/bin/env python3
"""The four surfaces the ladder asks for. Engine names come from profiles.ENGINE_VALUES."""
import sys

from profiles import ENGINE_VALUES

_TIMINGS = "engine_record"


def _admitted():
    return sorted(ENGINE_VALUES)


def _require(engine):
    if engine not in ENGINE_VALUES:
        print(" ".join(_admitted()))
        raise SystemExit(2)


def bring(engine):
    """start for every admitted engine, including sglang."""
    _require(engine)
    return "start"


def speed(engine):
    """Every admitted engine's speed rung is bench_http.sh."""
    _require(engine)
    return "bench_http.sh"


def count(engine):
    """The deadline is the unmeasured cap. This does not walk the filesystem.

    Tokens are counted from the directory tokenizer_dir prints. The refusal,
    printed by the ladder, is that neither tokenizer.json nor tokenizer_config.json
    is in that directory.
    """
    _require(engine)
    return "unmeasured"


def kit():
    return "run_suite.sh"


def timings(which):
    """Name which capture_task.sh sentence applies. Does not read a log or invent a rate."""
    if which == "attached":
        return _TIMINGS
    if which == "started":
        return _TIMINGS
    print("usage: roles.py timings attached|started", file=sys.stderr)
    raise SystemExit(2)


def main(argv):
    if len(argv) < 2:
        print(
            "usage: roles.py bring|speed|count <engine> | kit | timings attached|started",
            file=sys.stderr,
        )
        return 2
    cmd = argv[1]
    if cmd == "kit":
        if len(argv) != 2:
            print("usage: roles.py kit", file=sys.stderr)
            return 2
        print(kit())
        return 0
    if cmd == "timings":
        if len(argv) != 3:
            print("usage: roles.py timings attached|started", file=sys.stderr)
            return 2
        print(timings(argv[2]))
        return 0
    if cmd in ("bring", "speed", "count"):
        if len(argv) != 3:
            print(f"usage: roles.py {cmd} <engine>", file=sys.stderr)
            return 2
        answer = {"bring": bring, "speed": speed, "count": count}[cmd](argv[2])
        print(answer)
        return 0
    print(
        "usage: roles.py bring|speed|count <engine> | kit | timings attached|started",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
