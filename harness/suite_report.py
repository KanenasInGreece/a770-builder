#!/usr/bin/env python3
"""suite_report.py — read one harness/run_suite.sh results file and report on it.

Usage: suite_report.py <results.json> [--json]

Default: an "instrument:" header, a table (stage, working, conformance, lines/budget, maintainable, usable, wall,
tokens), then the totals the registry's `suite` object takes (config/profiles.json SUITE_KEYS: briefs, runs, passed,
mean_wall_s, source).

--json: prints ONLY those five totals as a JSON object — pasteable straight into a profile's `suite` field in
config/profiles.json — and nothing else on stdout (the instrument line, if wanted, goes to stderr so it never lands
in the JSON a caller parses).
"""

import argparse
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def totals(doc, source_name):
    # a stage carrying "counts_toward_pass": false (S0's design note, or any other commentary-only stage,
    # per kit/SUITE.md) is excluded from briefs/runs/passed/mean_wall_s entirely, not just from "passed" —
    # otherwise a perfect model could never reach 100% (the reviewer's rubric axes need no flag of their own:
    # they live in "score", never in "working", so they were never part of "passed" to begin with).
    stages = doc.get("stages") or []
    counted = [s for s in stages if s.get("counts_toward_pass", True) is not False]
    briefs = len(counted)
    runs = sum(1 for s in counted if s.get("label"))
    passed = sum(1 for s in counted if s.get("working") is True)
    walls = [s["wall_s"] for s in counted if isinstance(s.get("wall_s"), (int, float))]
    mean_wall_s = round(sum(walls) / len(walls), 1) if walls else None
    t = {"briefs": briefs, "runs": runs, "passed": passed, "source": source_name}
    if mean_wall_s is not None:
        t["mean_wall_s"] = mean_wall_s
    return t


def fmt(v, suffix=""):
    return "-" if v is None else f"{v}{suffix}"


def print_table(doc):
    cols = ["stage", "working", "conformance", "lines/budget", "maintainable", "usable", "wall", "tokens"]
    rows = []
    for s in doc.get("stages") or []:
        working = "PASS" if s.get("working") is True else "FAIL" if s.get("working") is False else "-"
        conformance = fmt(s.get("conformance_exit"))
        lines = s.get("lines")
        budget = s.get("budget_lines")
        lb = f"{fmt(lines)}/{fmt(budget)}"
        score = s.get("score") or {}
        maintainable = fmt(score.get("maintainable"))
        usable = fmt(score.get("usable"))
        wall = fmt(s.get("wall_s"), "s")
        ptok, gtok = s.get("prompt_tokens"), s.get("gen_tokens")
        tokens = "-" if ptok is None and gtok is None else f"{fmt(ptok)}+{fmt(gtok)}"
        rows.append([s.get("id", "-"), working, conformance, lb, maintainable, usable, wall, tokens])
    widths = [max(len(cols[i]), *(len(r[i]) for r in rows)) if rows else len(cols[i]) for i in range(len(cols))]
    def line(vals):
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(vals))
    print(line(cols))
    for r in rows:
        print(line(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    doc = load(args.results)
    source_name = os.path.basename(args.results)
    instrument = doc.get("instrument", "")
    t = totals(doc, source_name)

    if args.json:
        if instrument:
            print(f"instrument: {instrument}", file=sys.stderr)
        print(json.dumps(t))
        return

    if instrument:
        print(f"instrument: {instrument}")
    seat = doc.get("seat")
    if seat:
        print(f"seat: {seat}")
    print_table(doc)
    print()
    parts = [f"{k}={t[k]}" for k in ("briefs", "runs", "passed", "mean_wall_s", "source") if k in t]
    print("totals: " + " ".join(parts))


if __name__ == "__main__":
    main()
