"""Offline watchdog cases on synthetic logs. No card, no private host log."""

import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
WATCH = os.path.join(PROJECT, "harness", "qualify_watch.sh")
FAKE = os.path.join(HERE, "fixtures", "qualify", "fakedocker.sh")
BASE = None


def arm_clock():
    """Stamp synthetic engine lines ahead of the scanner clock for this test."""
    global BASE
    BASE = datetime.now(timezone.utc) + timedelta(minutes=30)


def ts(off):
    if BASE is None:
        arm_clock()
    return (BASE + timedelta(seconds=off)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def vl(off, tps, run, pp=0.0):
    return (
        f"{ts(off)} (APIServer pid=1) INFO Engine 000: "
        f"Avg prompt throughput: {pp} tokens/s, "
        f"Avg generation throughput: {tps} tokens/s, Running: {run} reqs, Waiting: 0 reqs"
    )


def ll(off, tg):
    return (
        f"{ts(off)} 2.13 I slot print_timing: id  0 | task 599 | "
        f"n_gen =    251, tg =  {tg} t/s"
    )


def cum(builder, count):
    current = []
    logs = []
    for index in range(1, count + 1):
        current = current + builder(index)
        logs.append(list(current))
    return logs


def run_case(tmp, name, logs, build=None, build_age_s=0, expect=0, step=60, warn=None, running="true", run_pid=None, suite_pid=None, stage_start=None, stage_file=None, suite_start=None, live_cid=None, docker_log=None):
    arm = tmp / name
    fake = arm / "fake"
    logs_dir = arm / "logs"
    arm.mkdir()
    fake.mkdir()
    logs_dir.mkdir()
    (arm / "WATCH").write_text("")
    (arm / "cid").write_text("deadbeef\n")
    now = int(time.time())
    (fake / "clock0").write_text(str(now))
    (fake / "now").write_text(str(now))
    for index, lines in enumerate(logs, 1):
        (fake / f"log.{index}").write_text("\n".join(lines) + "\n")
    if build is not None:
        stamp = (datetime.now() - timedelta(seconds=build_age_s)).strftime("%Y%m%d-%H%M%S")
        (logs_dir / f"build-{stamp}.log").write_text(build)
    stop = arm / "stop.sh"
    stop.write_text("#!/bin/sh\necho STUB-STOP-RUN >> \"$1\"\n")
    # The stop hook receives no path; it appends beside the script via the env file.
    stop.write_text(f"#!/bin/sh\necho STUB-STOP-RUN >> {arm / 'stopcalls.txt'}\n")
    stop.chmod(0o700)
    true_bin = arm / "running-true.sh"
    true_bin.write_text("#!/bin/sh\nexit 0\n")
    true_bin.chmod(0o700)
    false_bin = arm / "running-false.sh"
    false_bin.write_text("#!/bin/sh\nexit 1\n")
    false_bin.chmod(0o700)
    if suite_pid:
        (arm / "suite.pid").write_text(str(suite_pid))
        sys.path.insert(0, os.path.join(PROJECT, "harness"))
        import qualify_lib

        if suite_start is not None:
            (arm / "suite.start").write_text(str(suite_start) + "\n")
        else:
            start = qualify_lib.proc_starttime(suite_pid)
            if start:
                (arm / "suite.start").write_text(start + "\n")
    env = os.environ.copy()
    env.update(
        {
            "A770B_PROJECT": PROJECT,
            "A770B_DATA": str(arm / "data"),
            "XDG_CONFIG_HOME": str(arm / "xdg"),
            "A770B_QUALIFY_OPENCODE_VERSION": "unknown",
            "WD_POLL": "0",
            "WD_NOSLEEP": "1",
            "WD_DOCKER": FAKE,
            "FAKE_DIR": str(fake),
            "FAKE_STEP": str(step),
            "WD_STOP_BIN": str(stop),
            "WD_LOGDIR": str(logs_dir),
            "WD_NOWFILE": str(fake / "now"),
            "WD_MAX_POLLS": str(len(logs)),
            "WD_NOFINALSLEEP": "1",
            "WD_RUNPID": str(arm / "run.pid"),
        }
    )
    if stage_start is not None:
        env["QUALIFY_STAGE_START"] = str(stage_start)
    if stage_file is not None:
        (arm / "stage-start").write_text(str(stage_file) + "\n")
    env.pop("A770B_QUALIFY_STUB", None)
    if live_cid:
        live_dir = arm / "data" / "logs"
        live_dir.mkdir(parents=True)
        (live_dir / "llamacpp-a770.cid").write_text(live_cid + "\n")
        (arm / "status.json").write_text('{"phase": "suite", "state": "running"}\n')
    if running == "true":
        env["WD_RUNNING_BIN"] = str(true_bin)
    elif running == "false":
        env["WD_RUNNING_BIN"] = str(false_bin)
    else:
        env.pop("WD_RUNNING_BIN", None)
    if run_pid:
        (arm / "run.pid").write_text(str(run_pid))
    (arm / "data").mkdir(exist_ok=True)
    (arm / "xdg").mkdir(exist_ok=True)
    if docker_log:
        wrapper = arm / "docker-wrap.sh"
        wrapper.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {docker_log}\nexec {FAKE} \"$@\"\n")
        wrapper.chmod(0o755)
        env["WD_DOCKER"] = str(wrapper)
    subprocess.run(["bash", WATCH, str(arm), str(arm / "cid")], env=env, timeout=60, check=False)
    trips_path = arm / "watchdog-trips.log"
    trips = trips_path.read_text().splitlines() if trips_path.exists() else []
    stops = (arm / "stopcalls.txt").read_text().count("STUB") if (arm / "stopcalls.txt").exists() else 0
    log = (arm / "watchdog.log").read_text() if (arm / "watchdog.log").exists() else ""
    warns = log.count("WARN: stage wall")
    assert len(trips) == expect, name
    assert stops == len(trips), name
    if warn is not None:
        assert warns == warn, name
    return arm


PROSE = "\n".join(["The model keeps repeating this same sentence today"] * 8) + "\n"
VARIED = "A first unique line is written here now.\nA second unique line is written here now.\n← Edit src/file.py\n"


def test_watch_script_has_no_eval_or_pgrep():
    text = open(WATCH, encoding="utf-8").read()
    assert "eval " not in text
    assert "pgrep" not in text
    assert "pkill" not in text
    assert "killall" not in text


def test_i7_ported_cases(tmp_path):
    arm_clock()
    stale = f"{ts(-3600)} ERROR UR_RESULT_ERROR_OUT_OF_RESOURCES in graph capture"
    fresh = lambda i: [vl(i, 19.7, 1)] + (
        [f"{ts(i + 0.5)} ERROR UR_RESULT_ERROR_OUT_OF_RESOURCES graph capture failed"] if i == 3 else []
    )
    run_case(tmp_path, "A", [[stale] + [vl(i, 19.7, 1) for i in range(1, k + 1)] for k in range(1, 7)], expect=0)
    run_case(tmp_path, "B", cum(lambda i: [vl(i, 0.0, 0)], 8), expect=0)
    run_case(tmp_path, "D", cum(fresh, 7), expect=1)
    timing = [ll(i, 20.0) for i in range(60)]
    run_case(tmp_path, "E", [timing[: 10 * k] for k in range(1, 7)], expect=0)
    run_case(tmp_path, "G", [[vl(i, 19.7, 1)] for i in range(1, 6)], build=PROSE, step=400, expect=1)
    run_case(tmp_path, "H", [[vl(i, 19.7, 1)] for i in range(1, 6)], build=VARIED, step=400, expect=0)
    run_case(tmp_path, "S1", cum(lambda i: [vl(i, 2.0, 1)], 30), expect=0)
    run_case(tmp_path, "S1b", cum(lambda i: [ll(i, 0.3)], 30), expect=0)
    run_case(tmp_path, "S2", cum(lambda i: [vl(i, 0.0, 1)], 10), expect=1)
    run_case(tmp_path, "S2b", cum(lambda i: [vl(i, 0.0, 1)], 9), expect=0)
    run_case(tmp_path, "S2c", cum(lambda i: [vl(i, 0.0, 1, pp=2000.0)], 20), expect=0)
    seq = [0] * 9 + [None] + [0] * 9

    def idle_reset(i):
        return [vl(i, 0.0, 0)] if seq[i - 1] is None else [vl(i, 0.0, 1)]

    run_case(tmp_path, "S3", cum(idle_reset, 19), expect=0)
    launch = lambda i: [f"{ts(1)} I slot launch_slot_: id  0 | task {i} | processing task, is_child = 0"]
    run_case(tmp_path, "S4", [launch(1)] * 12, expect=1)
    released = launch(1) + [
        f"{ts(1.2)} I slot      release: id  0 | task 1 | stop processing: n_tokens = 633, truncated = 0"
    ]
    run_case(tmp_path, "S4b", [released] * 12, expect=0)
    run_case(tmp_path, "W1", [[vl(i, 19.7, 1)] for i in range(1, 4)], build=VARIED, build_age_s=7500, expect=0, warn=1)
    run_case(tmp_path, "W2", [[vl(i, 19.7, 1)] for i in range(1, 4)], build=VARIED, build_age_s=15000, expect=0, warn=2)
    run_case(tmp_path, "B3", [[vl(i, 19.7, 1)] for i in range(1, 18)], build=VARIED, step=900, expect=1)
    run_case(tmp_path, "B3b", [[vl(i, 0.0, 0)] for i in range(1, 18)], build=VARIED, step=900, expect=0)


def test_i7_no_run_and_identity(tmp_path):
    arm_clock()
    fresh = [vl(1, 19.7, 1), f"{ts(2)} ERROR UR_RESULT_ERROR_OUT_OF_RESOURCES graph capture failed"]
    run_case(tmp_path, "R2", [fresh + [vl(i, 0.0, 1) for i in range(3, 15)]] * 12, build=PROSE, step=400, running=None, expect=0)
    script = tmp_path / "sandbox_run.sh"
    script.write_text("#!/bin/sh\nsleep 120\n")
    script.chmod(0o700)
    proc = subprocess.Popen([str(script)])
    try:
        run_case(tmp_path, "R1", [fresh] * 3, running=None, run_pid=proc.pid, expect=1)
    finally:
        proc.kill()
        proc.wait(timeout=5)
    # The command line names local-build.sh run. That string is not a run leader.
    decoy = subprocess.Popen(["bash", "-c", "exec sleep 120", "local-build.sh", "run"])
    try:
        run_case(tmp_path, "R3", [fresh] * 3, build=PROSE, step=400, running=None, run_pid=decoy.pid, expect=0)
    finally:
        decoy.kill()
        decoy.wait(timeout=5)


def test_i7_driver_ends_suite_prose_does_not(tmp_path):
    arm_clock()
    sleeper = subprocess.Popen(["sleep", "120"])
    try:
        fresh = [vl(1, 19.7, 1), f"{ts(2)} ERROR SIGSEGV in the driver"]
        arm = run_case(tmp_path, "driver", [fresh], expect=1, suite_pid=sleeper.pid)
        sleeper.wait(timeout=5)
        assert (arm / "DRIVER_ERROR").is_file()
        assert (arm / "SUITE_SKIP").is_file()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
    other = subprocess.Popen(["sleep", "120"])
    try:
        arm = run_case(
            tmp_path,
            "prose",
            [[vl(i, 19.7, 1)] for i in range(1, 6)],
            build=PROSE,
            step=400,
            expect=1,
            suite_pid=other.pid,
        )
        assert other.poll() is None
        assert not (arm / "DRIVER_ERROR").exists()
    finally:
        other.kill()
        other.wait(timeout=5)


def test_old_stage_log_is_ignored(tmp_path):
    arm_clock()
    future = int(time.time()) + 100000
    run_case(
        tmp_path,
        "old",
        [[vl(i, 19.7, 1)] for i in range(1, 6)],
        build=PROSE,
        step=400,
        expect=0,
        stage_start=future,
    )
    # qualify.sh writes this file. The watch must read it when the environment is unset.
    run_case(
        tmp_path,
        "from-file",
        [[vl(i, 19.7, 1)] for i in range(1, 6)],
        build=PROSE,
        step=400,
        expect=0,
        stage_file=future,
    )


def test_phase5_reads_the_live_container_id(tmp_path):
    arm_clock()
    recorded = tmp_path / "docker-args.txt"
    run_case(
        tmp_path,
        "phase5",
        [[vl(1, 19.7, 1)]],
        expect=0,
        live_cid="phase5cid",
        docker_log=recorded,
    )
    text = recorded.read_text()
    assert "phase5cid" in text
    assert "deadbeef" not in text


def test_driver_does_not_signal_a_reused_pid(tmp_path):
    arm_clock()
    other = subprocess.Popen(["sleep", "120"])
    try:
        fresh = [vl(1, 19.7, 1), f"{ts(2)} ERROR SIGSEGV in the driver"]
        run_case(tmp_path, "wrong-start", [fresh], expect=1, suite_pid=other.pid, suite_start="1")
        assert other.poll() is None
    finally:
        if other.poll() is None:
            other.kill()
            other.wait(timeout=5)
