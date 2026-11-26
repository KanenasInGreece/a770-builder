#!/usr/bin/env python3
"""Read timestamped engine lines and report what is new since the last poll.

The caller passes the previous timestamp. Lines that are not strictly newer
are ignored, so a line already seen cannot trip again.
"""

import json
import re
import sys

ERR = re.compile(
    r"OUT_OF_RESOURCES|DEVICE_LOST|UR_RESULT_ERROR|SIGSEGV|"
    r"GGML_ASSERT|EngineDeadError|OutOfMemoryError"
)
TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z?\s?(.*)$", re.S)
VLLM = re.compile(
    r"Avg generation throughput:\s*([0-9.]+) tokens/s.*?Running:\s*(\d+) reqs"
)
VLLMP = re.compile(r"Avg prompt throughput:\s*([0-9.]+) tokens/s")
LAUNCH = re.compile(r"slot launch_slot_:.*processing task")
RELEASE = re.compile(r"slot\s+release:")
LTG = re.compile(
    r"slot print_timing:.*\|\s*n_gen\s*=\s*\d+,\s*tg\s*=\s*([0-9.]+)\s*t/s"
)
LEV = re.compile(r"slot print_timing:.*\|\s+eval time\s*=.*?([0-9.]+) tokens per second")


def key(ts):
    match = TS.match(ts)
    if not match:
        return None
    sec, frac, _body = match.groups()
    return sec + "." + (frac or "").ljust(9, "0")[:9]


def main():
    last = key(sys.argv[1]) or ""
    newest = last
    out = {
        "last_ts": sys.argv[1],
        "new_lines": 0,
        "error": None,
        "decode": [],
        "clock": None,
        "zero_inflight": 0,
        "moving_inflight": 0,
        "launches": 0,
        "releases": 0,
    }
    for raw in sys.stdin:
        raw = raw.rstrip("\n")
        match = TS.match(raw)
        if not match:
            continue
        stamp = key(raw)
        if stamp <= last:
            continue
        body = match.group(3)
        out["new_lines"] += 1
        if stamp > newest:
            newest = stamp
            out["last_ts"] = raw.split(" ", 1)[0]
        if out["error"] is None and ERR.search(body):
            out["error"] = body[:240]
        vllm = VLLM.search(body)
        if vllm:
            out["clock"] = "vllm"
            if int(vllm.group(2)) >= 1:
                out["decode"].append(float(vllm.group(1)))
                prompt = VLLMP.search(body)
                if float(vllm.group(1)) == 0.0 and (not prompt or float(prompt.group(1)) == 0.0):
                    out["zero_inflight"] += 1
                else:
                    out["moving_inflight"] += 1
            continue
        if LAUNCH.search(body):
            out["launches"] += 1
            out["clock"] = "llama"
        if RELEASE.search(body):
            out["releases"] += 1
            out["clock"] = "llama"
        generated = LTG.search(body) or LEV.search(body)
        if generated and "prompt eval time" not in body:
            out["clock"] = "llama"
            out["decode"].append(float(generated.group(1)))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
