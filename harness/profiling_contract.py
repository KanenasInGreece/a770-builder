#!/usr/bin/env python3
"""Measurement switches fixed per engine, and the prefill window rule.

Every start of an engine passes that engine's own switches from this table,
so a row's numbers are the engine's own. The row records the switches and
whether each one took effect. Decode speed is recorded and does not set the
useful window.
"""

import io
import json
import os
import re
import sys
import tokenize
import urllib.error
import urllib.request

CONTRACT_VERSION = 1

# vLLM prints non-default arguments as a Python dict. This substring is the
# proof the per-request switch was accepted.
VLLM_PER_REQUEST_ECHO = "'enable_per_request_metrics': True"

VERBOSITY_SWITCH = "-lv 4"
RULE_NAME = "prefill-absorb-v1"
SHALLOW_DEPTH = 8000
_ABSORB_EPS = 1e-6

TABLE = {
    "llama.cpp": {
        "flags": ["--metrics", "-lv", "4"],
        "forbidden": [
            "--no-metrics",
            "--metrics",
            "--log-disable",
            "-lv",
            "--log-verbosity",
            "--verbosity",
        ],
        "yields": {
            "stage_counters": {"source": "/metrics", "consumed": True},
            "host_buffer_sizes": {
                "source": "server log at verbosity 4",
                "consumed": True,
            },
            "prefill_tps": {"source": "server log timings", "consumed": True},
            "decode_tps": {"source": "server log timings", "consumed": True},
            "length_stops": {"source": "server log", "consumed": True},
        },
        "confirm": {
            "--metrics": (
                "GET /metrics returns 200 and the body contains llamacpp: counters"
            ),
            "-lv 4": "the server log shows verbosity = 4",
        },
        "status": "applied",
    },
    "vllm": {
        "flags": ["--enable-per-request-metrics"],
        "forbidden": [
            "--disable-log-stats",
            "--no-enable-per-request-metrics",
            "--enable-per-request-metrics",
        ],
        "yields": {
            "metrics_aggregates": {"source": "/metrics", "consumed": True},
            "per_request_fields": {"source": "completion body", "consumed": False},
        },
        "confirm": {
            "--enable-per-request-metrics": (
                "startup echo contains the per-request key set true"
            ),
        },
        "status": "applied",
    },
    "sglang": {
        "flags": [],
        "future_flags": [
            "--export-metrics-to-file",
            "--export-metrics-to-file-dir",
        ],
        "forbidden": ["--log-requests"],
        "yields": {
            "export_file": {
                "source": "export-metrics-to-file",
                "consumed": False,
            },
        },
        "confirm": {},
        "status": "deferred",
    },
    "strata": {
        "flags": [],
        "forbidden": [],
        "yields": {
            "usage": {"source": "completion usage object", "consumed": True},
        },
        "confirm": {},
        "status": "applied",
    },
}


def flags(engine):
    """The argv words this engine always receives. Empty when none are applied."""
    return list(TABLE[engine]["flags"])


# vLLM and SGLang fold "_" and "-" in a long option before the value. llama.cpp does not.
_FOLD_UNDERSCORES = {"vllm", "sglang"}


def _long_name(token):
    head = token.split("=", 1)[0]
    if head.startswith("--"):
        return "--" + head[2:].replace("_", "-")
    return head


def _matches(token, name, fold_underscores=False):
    if fold_underscores and token.startswith("--") and name.startswith("--"):
        return _long_name(token) == _long_name(name)
    return token == name or token.startswith(name + "=")


def refusal(engine, argv):
    """A message naming the flag and the engine, or None when the extra is allowed.

    A token matches a forbidden flag as itself or as --flag=value. A longer
    name such as --metrics-extra does not match --metrics. On vLLM and SGLang
    a long option's underscores are hyphens, for the name before "=" only.
    """
    if engine not in TABLE:
        return None
    fold = engine in _FOLD_UNDERSCORES
    for token in argv:
        if not isinstance(token, str):
            continue
        for name in TABLE[engine]["forbidden"]:
            if _matches(token, name, fold):
                return f"{engine} refuses {name}"
    return None


def receipt(engine, confirmed):
    """The profiling object a start writes. confirmed maps a switch to its result."""
    return {
        "contract": CONTRACT_VERSION,
        "engine": engine,
        "flags": flags(engine),
        "confirmed": dict(confirmed),
    }


def confirm_llama(status, body, log_text):
    metrics_ok = status == 200 and isinstance(body, str) and "llamacpp:" in body
    verbose_ok = bool(re.search(r"verbosity\s*=\s*4\b", log_text or ""))
    return {"--metrics": metrics_ok, VERBOSITY_SWITCH: verbose_ok}


def confirm_vllm(log_text):
    present = VLLM_PER_REQUEST_ECHO in (log_text or "")
    return {TABLE["vllm"]["flags"][0]: present}


def fetch_metrics(host, port, key_file=None, timeout=2.0):
    """GET /metrics. A refused connection is status 0, not an exception for the caller."""
    url = f"http://{host}:{port}/metrics"
    req = urllib.request.Request(url)
    if key_file and os.path.isfile(key_file):
        try:
            with open(key_file, encoding="utf-8") as handle:
                key = handle.readline().strip()
        except OSError:
            key = ""
        if key:
            req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(65536)
            return int(resp.status), raw.decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        return 0, ""


def confirm_started(engine, host, port, log_text, key_file=None, timeout=2.0):
    """Confirm the switches a healthy start can prove without a later request.

    A failed read is false. Nothing here is left pending: both proofs are
    available as soon as the server is healthy.
    """
    if engine == "llama.cpp":
        status, body = fetch_metrics(host, port, key_file, timeout)
        return confirm_llama(status, body, log_text)
    if engine == "vllm":
        return confirm_vllm(log_text)
    return {}


def write_receipt(path, obj):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
        handle.write("\n")


def merge_receipt_file(evidence_path, receipt_path):
    """Copy a profiling receipt onto an evidence JSON object. False if either is unreadable."""
    try:
        with open(receipt_path, encoding="utf-8") as handle:
            receipt = json.load(handle)
        with open(evidence_path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return False
    if not isinstance(receipt, dict) or not isinstance(doc, dict):
        return False
    doc["profiling"] = receipt
    with open(evidence_path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
        handle.write("\n")
    return True


def load_saved_profiling(data_dir):
    """The profiling object from serve evidence, or the receipt written at start."""
    if not data_dir:
        return None
    pointer = os.path.join(data_dir, "logs", "serve-evidence.path")
    try:
        with open(pointer, encoding="utf-8") as handle:
            evidence_path = handle.read().strip()
        with open(evidence_path, encoding="utf-8") as handle:
            doc = json.load(handle)
        if isinstance(doc, dict) and isinstance(doc.get("profiling"), dict):
            return doc["profiling"]
    except (OSError, ValueError):
        pass
    receipt_path = os.path.join(data_dir, "logs", "profiling.json")
    try:
        with open(receipt_path, encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(receipt, dict) and "contract" in receipt:
        return receipt
    return None


def null_unconfirmed_verbosity(row):
    """Null the four fields a verbosity-4 log supplies when that switch is not confirmed.

    pending does not null. An absent profiling object, and any engine other
    than llama.cpp, leaves the row alone. Fields that are not on the row are
    not invented.
    """
    if not isinstance(row, dict):
        return row
    profiling = row.get("profiling")
    if not isinstance(profiling, dict) or profiling.get("engine") != "llama.cpp":
        return row
    confirmed = profiling.get("confirmed")
    if not isinstance(confirmed, dict):
        confirmed = {}
    state = confirmed.get(VERBOSITY_SWITCH)
    # Same rule as the row validator: only a real True or "pending" keeps the fields.
    # The number 1 compares equal to True and must still null.
    if state is True or state == "pending":
        return row
    requests = row.get("requests")
    if isinstance(requests, dict):
        for key in ("prefill_tps", "decode_tps", "length_stops"):
            if key in requests:
                requests[key] = None
    speed = row.get("speed")
    if isinstance(speed, dict) and "delivered" in speed:
        speed["delivered"] = None
    return row


def parse_absorb_limit(raw=None):
    """Positive integer seconds, or None when unset or empty. A bad value exits 2."""
    if raw is None:
        raw = os.environ.get("A770B_ABSORB_MAX_S")
    if raw is None or raw == "":
        return None
    if re.fullmatch(r"[1-9][0-9]*", raw):
        return int(raw)
    print(
        "A770B_ABSORB_MAX_S must be a positive integer number of seconds "
        f"(it is {raw!r})",
        file=sys.stderr,
    )
    raise SystemExit(2)


def ttft_at(depth, t1, t2, far_end, shallow=SHALLOW_DEPTH):
    """Interpolated time to first token. Depths are the sweep targets.

    Below the shallow point, time scales from zero. Between the points it is
    linear. Past the far end the same slope continues only while time rises;
    otherwise the far-end time is held flat.
    """
    depth = float(depth)
    if depth <= 0:
        return 0.0
    shallow = float(shallow)
    if depth <= shallow or far_end <= shallow:
        if depth <= shallow:
            return float(t1) * (depth / shallow)
        return float(t1) if t2 > t1 else float(t2)
    if depth <= far_end:
        span = float(far_end) - shallow
        return float(t1) + (float(t2) - float(t1)) * ((depth - shallow) / span)
    if t2 <= t1:
        return float(t2)
    slope = (float(t2) - float(t1)) / (float(far_end) - shallow)
    return float(t2) + slope * (depth - float(far_end))


def deepest_absorb(t1, t2, limit, cap, far_end, shallow=SHALLOW_DEPTH):
    """Largest integer depth at most cap whose interpolated ttft is within limit."""
    cap = int(cap)
    if cap <= 0:
        return 0
    if ttft_at(cap, t1, t2, far_end, shallow) <= float(limit) + _ABSORB_EPS:
        return cap
    lo, hi = 0, cap
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ttft_at(mid, t1, t2, far_end, shallow) <= float(limit) + _ABSORB_EPS:
            lo = mid
        else:
            hi = mid - 1
    return lo


def absorb_window(ctx, far_end, ttft_8k, ttft_far, limit, depth_pass, unanswered):
    """useful_ctx under prefill-absorb-v1, the absorb pair, and the limit or None.

    With no limit the served context is the window, except a failed probe caps
    it at 8,000 and an unanswered probe or a sweep that lacks a point leaves
    it unset. Decode is not an input. With a limit, a missing ttft leaves the
    window unset. The result is never above ctx.
    """
    absorb = {"8k": ttft_8k, "far_end": ttft_far}
    if unanswered:
        return None, absorb, limit
    have_pair = ttft_8k is not None and ttft_far is not None
    if not have_pair:
        useful = None
    elif limit is None:
        useful = ctx
    else:
        useful = deepest_absorb(ttft_8k, ttft_far, limit, ctx, far_end)
    if depth_pass is False and useful is not None:
        useful = min(useful, SHALLOW_DEPTH)
    if useful is not None:
        useful = max(0, min(int(useful), int(ctx)))
    return useful, absorb, limit


def applied_spellings():
    """Flag tokens this table applies. The bare value 4 is not a spelling."""
    found = []
    for entry in TABLE.values():
        for flag in entry.get("flags", []):
            if isinstance(flag, str) and flag.startswith("-") and flag not in found:
                found.append(flag)
    return found


def _bash_code(text):
    out = []
    i = 0
    n = len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote != "'" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _python_code(text):
    pieces = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                continue
            pieces.append(tok.string)
            if tok.type in (tokenize.NL, tokenize.NEWLINE):
                pieces.append("\n")
    except tokenize.TokenError:
        return text
    return " ".join(pieces)


def _spelling_hits(text, spelling):
    pattern = r"(?<![\w-])" + re.escape(spelling) + r"(?![\w-])"
    return re.findall(pattern, text)


def spellings_outside_contract(harness_dir):
    """Files under harness, other than this module, whose code spells an applied flag."""
    here = os.path.join(os.path.abspath(harness_dir), "profiling_contract.py")
    hits = []
    for name in sorted(os.listdir(harness_dir)):
        if not name.endswith((".py", ".sh")):
            continue
        path = os.path.join(harness_dir, name)
        if os.path.abspath(path) == here or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        code = _python_code(text) if name.endswith(".py") else _bash_code(text)
        for spelling in applied_spellings():
            if _spelling_hits(code, spelling):
                hits.append(f"{name}: {spelling}")
    return hits


def main():
    json.dump(TABLE, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
