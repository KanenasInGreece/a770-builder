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


def mapped_bench(doc, source_name, flags):
    return {
        "instrument": "llama-benchy",
        "tool": "llama-benchy",
        "prompt": 8192,
        "gen": 128,
        "flags": flags,
        "at_depth": map_document(doc),
        "source": source_name,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw")
    ap.add_argument("--out", required=True)
    ap.add_argument("--flags", default="")
    args = ap.parse_args()
    with open(args.raw, encoding="utf-8") as handle:
        doc = json.load(handle)
    out = mapped_bench(doc, os.path.basename(args.raw), args.flags)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
        handle.write("\n")
    if not out["at_depth"]:
        print("no prompt size matched the four sizes at depth 0", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
