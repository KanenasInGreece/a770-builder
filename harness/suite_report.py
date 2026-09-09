#!/usr/bin/env python3
"""suite_report.py — read one harness/run_suite.sh results file and report on it.

Usage: suite_report.py <results.json> [--json]

Default: an "instrument:" header, a table (stage, outcome, conformance, lines/budget, maintainable, usable, wall,
tokens), then the totals the registry's `suite` object takes (config/profiles.json SUITE_KEYS: briefs, runs, passed,
timeouts, mean_wall_s, source), then the suite's own delivered speed (below). The outcome column reads PASS, FAIL,
or TIMEOUT — a stage whose run_suite.sh "outcome" is "timeout" (its wall time reached the profile's own timeout
budget) is never shown as a plain FAIL: a reader should be able to tell a slow model from a wrong one at a glance.
A results file recorded before "outcome" existed falls back to the "working" boolean alone (PASS/FAIL/-).

--json: prints ONLY those six totals, plus `delivered` when at least one stage's capture yields a reading, as a
JSON object — pasteable straight into a profile's `suite` field (and `speed.delivered`) in config/profiles.json —
and nothing else on stdout (the instrument line, if wanted, goes to stderr so it never lands in the JSON a caller
parses).

Delivered speed: a stage's capture file (harness/capture_task.sh's `<label>.task.md`) carries the server-side timing
lines `prefill tok/s over all prompts=<x>` and `... decode tok/s median=<y>`. A results JSON stage that names its own
capture path (a future runner extension) is read from that path directly; today's `run_suite.sh` records no such
path, so the fallback is the stage's own `label`: `$A770B_DATA/results/<label>.task.md`, `A770B_DATA` defaulting the
same way `harness/env.sh` does. The per-stage readings are printed and their MEDIANS across stages are what
`delivered` reports — the suite's own as-delivered speed, beside (never instead of) the standard llama-bench number
`harness/bench_speed.sh` measures at the row's own served flags.

Contamination pair: kit/reference/reference.json's own entries (ids starting "ref-", one per language) are graded by
`local-build.sh verify` running the exercise's own public command FIRST, then — only if that succeeds — the fresh
hidden variant, as one `&&`-chained command (kit/SUITE.md, AGENTS.md: "the pair is a contamination indicator, not
two correctness scores"). `verify` writes that stage's own `<label>.verify.md` beside the capture, with a "hidden
tests: <names or none>" line, the chain's own exit-code verdict, and the last pytest-shaped summary line reported —
enough to tell public-passed-hidden-failed apart from a plain public failure, even though the two ran as one exit
code. When that file is found for a reference stage, its language is printed beside the pair (never in --json: the
registry's `suite` object has no field for it — see config/profiles.json SUITE_KEYS).
"""

import argparse
import json
import os
import re
import statistics
import sys

PREFILL_RE = re.compile(r"prefill tok/s over all prompts=([0-9]+(?:\.[0-9]+)?)")
DECODE_RE = re.compile(r"decode tok/s median=([0-9]+(?:\.[0-9]+)?)")
HIDDEN_TESTS_RE = re.compile(r"^hidden tests: (.*)$", re.MULTILINE)
VERDICT_RE = re.compile(r"^verdict: (PASS \(exit 0\)|FAIL \(exit -?\d+\)|TIMEOUT after \d+s)", re.MULTILINE)
REPORTED_RE = re.compile(r"^reported: (.*) \(quoted from output", re.MULTILINE)


def load(path):
    with open(path) as f:
        return json.load(f)


def capture_path_for_stage(stage, a770b_data):
    """The capture file a stage's delivered speed is read from, or None. Prefers a capture path the results JSON
    names on the stage itself (a future runner extension); falls back to `<A770B_DATA>/results/<label>.task.md`,
    today's `run_suite.sh`'s only per-stage record of where its capture landed."""
    named = stage.get("capture") or stage.get("capture_path")
    if isinstance(named, str) and named:
        return named
    label = stage.get("label")
    if isinstance(label, str) and label:
        return os.path.join(a770b_data, "results", f"{label}.task.md")
    return None


def delivered_for_stage(stage, a770b_data):
    """(prefill_tps, decode_tps) read from a stage's own capture file, either value None when its capture is
    missing or carries no matching line."""
    path = capture_path_for_stage(stage, a770b_data)
    if not path:
        return None, None
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None, None
    pm = PREFILL_RE.search(text)
    dm = DECODE_RE.search(text)
    prefill = float(pm.group(1)) if pm else None
    decode = float(dm.group(1)) if dm else None
    return prefill, decode


def verify_path_for_stage(stage, a770b_data):
    """The `<label>.verify.md` `local-build.sh verify` wrote for this stage, or None. Same fallback shape as
    capture_path_for_stage: a results JSON records no verify path of its own, so this is `<label>.verify.md`
    beside the stage's own `<label>.task.md`."""
    label = stage.get("label")
    if isinstance(label, str) and label:
        return os.path.join(a770b_data, "results", f"{label}.verify.md")
    return None


def contamination_pair_for_stage(stage, a770b_data):
    """The public/hidden pair for one reference stage (kit/reference/reference.json, ids "ref-*"), read from its
    own `<label>.verify.md`. `local-build.sh verify` runs the exercise's own public command, and — only if that
    succeeds — the fresh hidden variant, chained as `TEST && pytest ...`; the whole chain is one exit code, so the
    three outcomes are told apart from the file `verify` itself wrote:
      - the chain's own verdict is PASS  => both ran and both passed (the `&&` cannot reach the hidden half otherwise)
      - the chain FAILED, but a pytest-shaped summary line was reported => the public half passed (it must have,
        to reach the hidden half), the hidden half failed
      - the chain FAILED with no such summary line => the public half itself failed; the hidden half never ran
    Returns None when the stage has no verify.md, or when it names no hidden tests at all (a stage's own verify.md
    where `hidden tests: none` is not a reference-with-hidden pair — nothing to report here)."""
    if not str(stage.get("id", "")).startswith("ref-"):
        return None
    path = verify_path_for_stage(stage, a770b_data)
    if not path:
        return None
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None
    hm = HIDDEN_TESTS_RE.search(text)
    if not hm or hm.group(1).strip() in ("", "none"):
        return None
    vm = VERDICT_RE.search(text)
    if not vm:
        return None
    verdict = vm.group(1)
    rm = REPORTED_RE.search(text)
    reported = rm.group(1).strip() if rm else ""
    hidden_ran = bool(reported) and reported != "no pytest summary line"
    if verdict.startswith("PASS"):
        public, hidden = "PASS", "PASS"
    elif verdict.startswith("TIMEOUT"):
        public, hidden = "TIMEOUT", "TIMEOUT"
    elif hidden_ran:
        public, hidden = "PASS", "FAIL"
    else:
        public, hidden = "FAIL", "-"
    return {"public": public, "hidden": hidden, "hidden_summary": reported or None}


def contamination_pairs(doc, a770b_data):
    """(id, language, pair) for every reference stage whose verify.md yields a pair, in stage order."""
    out = []
    for s in doc.get("stages") or []:
        pair = contamination_pair_for_stage(s, a770b_data)
        if pair is not None:
            out.append((s.get("id", "-"), s.get("language"), pair))
    return out


def delivered(doc, a770b_data):
    """Per-stage (id, prefill_tps, decode_tps) readings and the medians across stages, or None if none read."""
    per_stage = []
    for s in doc.get("stages") or []:
        prefill, decode = delivered_for_stage(s, a770b_data)
        if prefill is not None or decode is not None:
            per_stage.append((s.get("id", "-"), prefill, decode))
    prefills = [p for _, p, _ in per_stage if p is not None]
    decodes = [d for _, _, d in per_stage if d is not None]
    medians = None
    if prefills and decodes:
        medians = {"prefill_tps": round(statistics.median(prefills), 1), "decode_tps": round(statistics.median(decodes), 1)}
    return per_stage, medians


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
    # a stage whose run_suite.sh outcome reads "timeout" (its wall time reached the profile's own timeout
    # budget — see run_suite.sh's stage_outcome) is never a plain FAIL: it is a slow model, not a wrong one, and
    # is counted here on its own so a caller does not have to re-derive it from wall_s and a profile registry.
    timeouts = sum(1 for s in counted if s.get("outcome") == "timeout")
    walls = [s["wall_s"] for s in counted if isinstance(s.get("wall_s"), (int, float))]
    mean_wall_s = round(sum(walls) / len(walls), 1) if walls else None
    t = {"briefs": briefs, "runs": runs, "passed": passed, "timeouts": timeouts, "source": source_name}
    if mean_wall_s is not None:
        t["mean_wall_s"] = mean_wall_s
    return t


def fmt(v, suffix=""):
    return "-" if v is None else f"{v}{suffix}"


def outcome_label(s):
    """PASS/FAIL/TIMEOUT/- for one stage: the "outcome" field run_suite.sh records (pass/fail/timeout) when
    present, so a timed-out stage reads distinctly from a plain failure; a results file recorded before
    "outcome" existed falls back to the "working" boolean alone (PASS/FAIL/-, as before)."""
    o = s.get("outcome")
    if o == "timeout":
        return "TIMEOUT"
    if o == "pass":
        return "PASS"
    if o == "fail":
        return "FAIL"
    w = s.get("working")
    return "PASS" if w is True else "FAIL" if w is False else "-"


def print_table(doc):
    cols = ["stage", "outcome", "conformance", "lines/budget", "maintainable", "usable", "wall", "tokens"]
    rows = []
    for s in doc.get("stages") or []:
        working = outcome_label(s)
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
    a770b_data = os.environ.get("A770B_DATA", os.path.expanduser("~/local-ai"))
    per_stage, medians = delivered(doc, a770b_data)

    if args.json:
        if instrument:
            print(f"instrument: {instrument}", file=sys.stderr)
        if medians is not None:
            t["delivered"] = dict(medians, source=source_name)
        print(json.dumps(t))
        return

    if instrument:
        print(f"instrument: {instrument}")
    seat = doc.get("seat")
    if seat:
        print(f"seat: {seat}")
    print_table(doc)
    print()
    parts = [f"{k}={t[k]}" for k in ("briefs", "runs", "passed", "timeouts", "mean_wall_s", "source") if k in t]
    print("totals: " + " ".join(parts))
    for stage_id, prefill, decode in per_stage:
        print(f"delivered ({stage_id}): prefill {fmt(prefill, ' tok/s')} · decode {fmt(decode, ' tok/s')}")
    if medians is not None:
        print(f"delivered: prefill {medians['prefill_tps']} tok/s · decode {medians['decode_tps']} tok/s")
    for stage_id, language, pair in contamination_pairs(doc, a770b_data):
        summary = f" ({pair['hidden_summary']})" if pair.get("hidden_summary") else ""
        lang = f", {language}" if language else ""
        print(f"contamination ({stage_id}{lang}): public {pair['public']} · hidden {pair['hidden']}{summary}")


if __name__ == "__main__":
    main()
