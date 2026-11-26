#!/usr/bin/env python3
"""Read a sandbox scope's OOM result after the command has already returned.

The caller runs this outside any timeout on the model command. Waiting for the
scope to leave active is not a clock on that command. Journal writes are
asynchronous, so an OOM in the last instant can still be missed and recorded
as no when a Started line was seen and the poll found nothing else.
"""

import json
import os
import subprocess
import sys
import time

# systemd's own "kernel OOM killer" catalog entry.
MESSAGE_ID = "fe6faa94e7774663a0da52717891d8ef"
_SETTLE_S = 5.0
_JOURNAL_S = 2.0


def normalize_status(rc):
    """A negative Popen status is signal N. Publishing must use 128+N."""
    if isinstance(rc, int) and not isinstance(rc, bool) and rc < 0:
        return 128 + (-rc), True
    return rc, False


def grade(exit_code, oom, signaled=False, timed_out=False):
    """Return status, exit code, and reason.

    Timeout stays timeout. A signal that killed sandbox_run.sh itself is
    infra_error. oom=yes is fail, and a command exit of 0 becomes 137.
    """
    if timed_out:
        return "timeout", 124, None
    if signaled:
        code = exit_code if isinstance(exit_code, int) and exit_code >= 128 else 128
        return "infra_error", code, None
    if oom == "yes":
        code = 137 if exit_code == 0 else exit_code
        return "fail", code, "oom"
    if exit_code == 0:
        return "pass", 0, None
    return "fail", exit_code, None


def _unit_name(unit):
    if unit.endswith(".scope") or unit.endswith(".service"):
        return unit
    return unit + ".scope"


def _read_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_record(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
        fh.write("\n")


def _systemctl_show(unit):
    proc = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "Result"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    shown = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            shown[key] = value
    return shown


def _wait_settled(unit):
    """Poll until the scope is not active. The cap is not a model timeout."""
    deadline = time.monotonic() + _SETTLE_S
    shown = None
    while True:
        shown = _systemctl_show(unit)
        if shown is None:
            return None
        if shown.get("ActiveState") not in ("active", "deactivating"):
            return shown
        if time.monotonic() >= deadline:
            return shown
        time.sleep(0.1)


def _journal_entries(unit):
    proc = subprocess.run(
        ["journalctl", "--user", "-o", "json", "--no-pager", f"USER_UNIT={unit}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    yes = False
    started = False
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        message = obj.get("MESSAGE", "")
        if not isinstance(message, str):
            message = str(message)
        ident = obj.get("SYSLOG_IDENTIFIER") or obj.get("_COMM") or ""
        if obj.get("MESSAGE_ID") == MESSAGE_ID or (ident == "systemd" and "OOM killer" in message):
            yes = True
        if ident == "systemd" and "Started" in message:
            started = True
    return yes, started


def _journal_oom(unit):
    # Poll about 2s. A late journal write can still be missed: no means
    # "no OOM entry was readable", not "the kernel cannot have killed anyone".
    deadline = time.monotonic() + _JOURNAL_S
    started = False
    while True:
        found = _journal_entries(unit)
        if found is None:
            return "unknown"
        yes, saw_started = found
        started = started or saw_started
        if yes:
            return "yes"
        if time.monotonic() >= deadline:
            return "no" if started else "unknown"
        time.sleep(0.2)


def _decide(policy, unit, exit_code):
    shown = _wait_settled(unit)
    if shown is not None and shown.get("ActiveState") in ("active", "deactivating"):
        return "unknown"
    if policy == "kill" and shown is not None:
        result = shown.get("Result", "")
        # Result=oom-kill with a non-zero exit is yes. Exit 0 with any other
        # result is no. Both at once (oom-kill and exit 0) is unknown, and
        # that contradiction is not resolved from the journal.
        if result == "oom-kill" and exit_code == 0:
            return "unknown"
        if result == "oom-kill" and exit_code != 0:
            return "yes"
        if exit_code == 0 and result != "oom-kill":
            return "no"
    return _journal_oom(unit)


def update(path, exit_code):
    """Set oom to yes, no, or unknown, then reset-failed the scope unit."""
    try:
        body = _read_record(path)
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(body, dict):
        return "unknown"
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = 1
    oom = body.get("oom", "unknown")
    unit = body.get("unit") or ""
    if body.get("isolation") == "none" or not unit:
        oom = "unknown"
    else:
        oom = _decide(body.get("policy") or "", _unit_name(str(unit)), code)
        subprocess.run(
            ["systemctl", "--user", "reset-failed", _unit_name(str(unit))],
            capture_output=True, text=True,
        )
    body["oom"] = oom
    try:
        _write_record(path, body)
    except OSError:
        return oom
    return oom


def main(argv):
    if len(argv) != 3:
        print("usage: sandbox_oom.py <record file> <exit code>", file=sys.stderr)
        return 2
    update(argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
