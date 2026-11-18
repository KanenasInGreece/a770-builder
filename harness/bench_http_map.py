#!/usr/bin/env python3
"""Map a llama-benchy JSON file onto the four prompt sizes this harness records.

A row counts only when context_size is 0, prompt_size is 8192, 32768, 65536, or
100000, response_size is 128, concurrency is 1, and is_context_prefill_phase is
false. pp_throughput.mean is prefill. tg_throughput.mean is decode. A null
throughput stays null. Every other row is ignored.
"""
import argparse
import json
import os
import sys

PROMPT_SIZES = (8192, 32768, 65536, 100000)


def _mean(metric):
    if not isinstance(metric, dict):
        return None
    value = metric.get("mean")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def map_document(doc):
    """Prompt size to {"pp", "tg"}. A missing throughput is None, never 0."""
    rows = doc.get("benchmarks") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return {}
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("context_size") != 0 or row.get("is_context_prefill_phase") is not False:
            continue
        if row.get("concurrency") != 1 or row.get("response_size") != 128:
            continue
        size = row.get("prompt_size")
        if size not in PROMPT_SIZES:
            continue
        found[str(size)] = {
            "pp": _mean(row.get("pp_throughput")),
            "tg": _mean(row.get("tg_throughput")),
        }
    return found


def mapped_bench(doc, source_name, flags, require_pp=None):
    at_depth = map_document(doc)
    out = {
        "instrument": "llama-benchy",
        "tool": "llama-benchy",
        "prompt": 8192,
        "gen": 128,
        "flags": flags,
        "at_depth": at_depth,
        "source": source_name,
    }
    if require_pp is not None:
        req = [int(d) for d in require_pp]
        missing = []
        for d in req:
            pair = at_depth.get(str(d))
            if not isinstance(pair, dict) or pair.get("pp") is None or pair.get("tg") is None:
                missing.append(d)
        out["requested_depths"] = req
        out["missing_depths"] = missing
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw")
    ap.add_argument("--out", required=True)
    ap.add_argument("--flags", default="")
    ap.add_argument("--require-pp", nargs="+", type=int, default=None,
                    help="prompt sizes that must each have non-null pp and tg")
    args = ap.parse_args()
    with open(args.raw, encoding="utf-8") as handle:
        doc = json.load(handle)
    out = mapped_bench(doc, os.path.basename(args.raw), args.flags, require_pp=args.require_pp)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
        handle.write("\n")
    if args.require_pp is not None:
        missing = out.get("missing_depths") or []
        if missing:
            print(f"missing or incomplete benchmark samples at depths: {missing}", file=sys.stderr)
            return 1
        return 0
    if not out["at_depth"]:
        print("no prompt size matched the four sizes at depth 0", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
