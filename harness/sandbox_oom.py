#!/usr/bin/env python3
"""Read a sandbox scope's OOM result after the command has already returned.

The caller runs this outside any timeout on the model command. Waiting for the
scope to leave active is not a clock on that command. Journal writes are
asynchronous, so an OOM in the last instant can still be missed and recorded
as no when a Started line was seen and the poll found nothing else.
"""

import json
import os
import re
import subprocess
import sys
import time

# systemd's own "kernel OOM killer" catalog entry.
MESSAGE_ID = "fe6faa94e7774663a0da52717891d8ef"
_SETTLE_S = 5.0
_JOURNAL_S = 2.0
# Bounds the systemctl and journalctl calls. It is not a limit on model code.
_TOOL_TIMEOUT_S = 5.0
_UNSETTLED = ("active", "activating", "reloading", "deactivating")


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
        if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code < 0:
            code = 128 + (-exit_code)
        elif isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code >= 128:
            code = exit_code
        else:
            code = 128
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


# sandbox_run.sh writes a770b-sbx-<pid>-<random>.scope. The opt-in OOM test
# rewrites that same suffix to a770b-test-. No other name is stopped or reset.
_OWNED_SCOPE = re.compile(r"^a770b-(?:sbx|test)-[1-9][0-9]*-[0-9]+\.scope$")


def _owned_scope(unit):
    return isinstance(unit, str) and _OWNED_SCOPE.fullmatch(unit) is not None


def _read_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_record(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
        fh.write("\n")


def _run(argv):
    """One systemctl or journalctl call. A stall returns None; it does not wait."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=_TOOL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _systemctl_show(unit):
    proc = _run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "Result"])
    if proc is None or proc.returncode != 0:
        return None
    shown = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and value:
            shown[key] = value
    # An empty or half-parsed reply is not a settled unit. Exit 0 alone is not.
    if not shown.get("ActiveState") or not shown.get("Result"):
        return None
    return shown


def _wait_settled(unit):
    """Poll until the scope is not active. The cap is not a model timeout.

    Returns (shown, kill_seen). shown is None when the show that ended the
    wait failed. kill_seen stays set once Result=oom-kill was readable, so a
    later failure or a different result cannot drop that observation.
    """
    deadline = time.monotonic() + _SETTLE_S
    kill_seen = False
    while True:
        shown = _systemctl_show(unit)
        if shown is not None and shown.get("Result") == "oom-kill":
            kill_seen = True
        if shown is None:
            return None, kill_seen
        if shown.get("ActiveState") not in _UNSETTLED:
            return shown, kill_seen
        if time.monotonic() >= deadline:
            return shown, kill_seen
        time.sleep(0.1)


def _journal_entries(unit):
    proc = _run(["journalctl", "--user", "-o", "json", "--no-pager", f"USER_UNIT={unit}"])
    if proc is None or proc.returncode != 0:
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


def _decide(policy, shown, exit_code, unit):
    # Kill policy trusts an observed oom-kill before the settle check. A scope
    # can still be deactivating when the helper reads it. Any other unsettled
    # result is unknown and does not read the journal. Exit 0 with a settled
    # result other than oom-kill is no, also without the journal.
    if policy == "kill" and shown is not None and shown.get("Result") == "oom-kill":
        return "yes"
    if shown is not None and shown.get("ActiveState") in _UNSETTLED:
        return "unknown"
    if policy == "kill" and shown is not None and exit_code == 0:
        return "no"
    return _journal_oom(unit)


def _unit_not_loaded(proc):
    detail = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    return "not loaded" in detail.lower()


def _report_cleanup(action, unit, proc):
    """A stall or a real refusal is printed. An already-unloaded unit is not."""
    if proc is None:
        print(f"sandbox oom: {action} of {unit} timed out", file=sys.stderr)
        return
    if proc.returncode != 0 and not _unit_not_loaded(proc):
        detail = (proc.stderr or proc.stdout or "").strip()
        print(
            f"sandbox oom: {action} of {unit} failed: {detail or proc.returncode}",
            file=sys.stderr,
        )


def _cleanup_scope(unit, shown):
    """Stop an owned scope that has not been shown to have exited, then reset it.

    A settled inactive scope is already unloaded; resetting it reports that
    the unit is not loaded. A failed show is not evidence the scope is gone,
    so the stop is still attempted. reset-failed does not stop an active unit.
    A name this sandbox did not generate is left untouched.
    """
    if not _owned_scope(unit):
        print(f"sandbox oom: refusing to stop or reset {unit}", file=sys.stderr)
        return
    if shown is None or shown.get("ActiveState") in _UNSETTLED:
        _report_cleanup("stop", unit, _run(["systemctl", "--user", "stop", "--", unit]))
    if shown is not None and shown.get("ActiveState") == "inactive":
        return
    _report_cleanup(
        "reset-failed", unit, _run(["systemctl", "--user", "reset-failed", "--", unit]),
    )


def update(path, exit_code):
    """Set oom to yes, no, or unknown, and reset an owned scope afterwards.

    An unreadable record, or one that is not a JSON object, is not a result.
    The caller must fail instead of publishing unknown.
    """
    body = _read_record(path)
    if not isinstance(body, dict):
        raise ValueError("sandbox oom record is not a JSON object")
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = 1
    oom = body.get("oom", "unknown")
    unit = body.get("unit") or ""
    if body.get("isolation") == "none" or not unit:
        oom = "unknown"
        body["oom"] = oom
        _write_record(path, body)
        return oom
    named = _unit_name(str(unit))
    shown = None
    try:
        shown, kill_seen = _wait_settled(named)
        policy = body.get("policy") or ""
        decision = shown
        # A kill-policy oom-kill seen while settling is the result, even when
        # the last show failed or came back as something else.
        if policy == "kill" and kill_seen:
            decision = dict(shown or {})
            decision["Result"] = "oom-kill"
        oom = _decide(policy, decision, code, named)
        body["oom"] = oom
        _write_record(path, body)
        return oom
    finally:
        # Stop and reset after the record is written, and still when the
        # settle, the decision, or the write raises.
        _cleanup_scope(named, shown)


def main(argv):
    if len(argv) != 3:
        print("usage: sandbox_oom.py <record file> <exit code>", file=sys.stderr)
        return 2
    try:
        update(argv[1], argv[2])
    except (OSError, ValueError) as exc:
        print(f"sandbox oom: could not publish the record: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
