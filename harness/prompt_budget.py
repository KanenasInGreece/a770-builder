#!/usr/bin/env python3
"""prompt_budget.py — the two questions the ladder has to answer before it sends a long prompt.

  fit   How many characters of the corpus make N tokens FOR THIS MODEL?
        Every model tokenises differently. The kit's corpus runs about three characters to the
        token on the Qwen tokenisers, so the old rule of thumb — four characters a token — asked
        for 100,000 tokens and built a prompt of 132,865. On a model served at 131,072 the server
        rejected it outright; on one served wider it simply cost a third more time than anyone
        budgeted. This asks the server's own /tokenize endpoint instead of guessing, and returns a
        prompt whose token count is at or just under the target, never over it.

  time  How long should that request be allowed to take ON THIS CARD?
        Prefill gets slower the deeper the prompt goes, so the cost of an N-token prompt is the
        area under the curve of seconds-per-token against depth — not N divided by any single
        rate. llama-bench has already measured that curve at the ladder's own depths by the time
        the window and depth rungs run, so this integrates the measured points and adds a margin.
        Checked against the run of 2026-09-09: the measured points for the 9B predict 1,054 s for
        a 132,865-token prompt, and the depth probe took 1,055 s.

A budget is a deadline, not a promise. It is deliberately generous, because cutting a request
short throws away the whole rung; but it is finite, because a request that runs far past what the
card's own numbers say it should is a finding, not something to wait out.

Usage:
  prompt_budget.py fit  --corpus F --target N --out F [--plant-file F] [--plant-at 0.85]
                        [--overhead 128] [--url URL] [--key K]
  prompt_budget.py time --tokens N [--bench F] [--gen 320] [--safety 1.5] [--floor 300] [--cap 7200]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# With no measured curve there is nothing to extrapolate from, and guessing low is the failure this
# file exists to remove: a rung cut short is a rung wasted. Wait a long time and say the budget is
# unmeasured. 7200 is the cap, not a guess: the slowest profile measured on this card needs about
# 4,650 s for a 100,000-token prompt, and a fallback under that reintroduces the bug.
UNMEASURED_BUDGET_S = 7200


def _post(url, key, path, payload):
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def count_tokens(url, key, text):
    return len(_post(url, key, "/tokenize", {"content": text}).get("tokens", []))


def build_prompt(corpus, chars, plant, plant_at):
    """The corpus cut to `chars`, with the planted function dropped in at `plant_at` of the way."""
    chars = max(0, min(chars, len(corpus)))
    if not plant:
        return corpus[:chars]
    pos = int(chars * plant_at)
    return corpus[:pos] + plant + corpus[pos:chars]


def fit(args):
    url = args.url or f"http://{os.environ.get('A770B_HOST', '127.0.0.1')}:{os.environ.get('A770B_PORT', '7890')}"
    key = args.key or ""
    corpus = open(args.corpus, encoding="utf-8", errors="ignore").read()
    plant = open(args.plant_file, encoding="utf-8", errors="ignore").read() if args.plant_file else ""

    # what the corpus itself has to say goes in the prompt; the rest of the target is spent on the
    # chat template, the instruction and the questions wrapped around it by the caller
    target = args.target - args.overhead
    if target <= 0:
        print(f"⛔ target {args.target} leaves nothing after {args.overhead} tokens of wrapper", file=sys.stderr)
        return 2

    # converge from below: a prompt a shade under the target costs nothing, one over it is the failure this
    # whole file exists to remove. Keep the best candidate that came in under and stop as soon as it is close.
    chars = min(int(target * 3.2), len(corpus))  # a starting guess only; every step below is measured
    best = None
    for _ in range(8):
        text = build_prompt(corpus, chars, plant, args.plant_at)
        try:
            n = count_tokens(url, key, text)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"⛔ the server's /tokenize did not answer ({e}) — cannot size the prompt", file=sys.stderr)
            return 2
        if n == 0:
            print("⛔ /tokenize returned no tokens", file=sys.stderr)
            return 2
        if n <= target:
            best = (text, n)
            if n >= target * 0.985:
                break
        if chars >= len(corpus) and n < target:
            print(
                f"⛔ corpus has {len(corpus)} characters, which is only {n} tokens for this model — "
                f"need {target}; refusing rather than measuring a shorter prompt than asked for",
                file=sys.stderr,
            )
            return 2
        chars = min(int(chars * (target * 0.998) / n), len(corpus))

    if best is None:
        print(f"⛔ could not cut the corpus to {target} tokens or fewer in eight tries", file=sys.stderr)
        return 2
    text, achieved = best

    if achieved < target * 0.95:
        print(f"⛔ could only reach {achieved} of {target} tokens — refusing to measure a prompt far "
              f"shorter than the one asked for", file=sys.stderr)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(achieved + args.overhead)  # what the server will see, wrapper included
    return 0


def curve(bench_path):
    """llama-bench's measured points: {depth: {"pp": tok/s prefilling, "tg": tok/s generating}}."""
    try:
        doc = json.load(open(bench_path))
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    if isinstance(doc, list):
        rows = doc
    elif not isinstance(doc, dict):
        return {}
    else:
        rows = doc.get("raw") or []
    if isinstance(rows, dict):
        rows = rows.get("results", [])
    if not isinstance(rows, list):
        return {}
    at = {}
    for row in rows:
        d = int(row.get("n_depth") or row.get("d") or 0)
        ts = row.get("avg_ts") or row.get("t/s")
        if ts is None:
            continue
        slot = at.setdefault(d, {})
        slot["pp" if (row.get("n_prompt") or 0) > 0 else "tg"] = float(ts)
    return {d: v for d, v in at.items() if "pp" in v}


def prefill_seconds(at, n_tokens):
    """The area under seconds-per-token against depth, out to n_tokens.

    Returns (seconds, extrapolated) — extrapolated says the prompt runs deeper than llama-bench
    measured, so the tail of the estimate is a straight-line continuation rather than a reading.
    """
    spt = {d: 1.0 / at[d]["pp"] for d in at if "pp" in at[d] and at[d]["pp"] > 0}
    if len(spt) < 2:
        return None, False
    real_depths = sorted(spt)
    if real_depths[0] > 0:  # anchor the shallow end at the shallowest reading we have
        spt[0] = spt[real_depths[0]]
        real_depths = sorted(spt)

    total, extrapolated = 0.0, False
    for a, b in zip(real_depths, real_depths[1:]):
        if n_tokens <= a:
            break
        hi = min(b, n_tokens)
        frac = (hi - a) / (b - a)
        spt_hi = spt[a] + (spt[b] - spt[a]) * frac
        total += (spt[a] + spt_hi) / 2 * (hi - a)
    last = real_depths[-1]
    if n_tokens > last:
        extrapolated = True
        prev = real_depths[-2]
        slope = (spt[last] - spt[prev]) / (last - prev)
        spt_end = spt[last] + slope * (n_tokens - last)
        total += (spt[last] + max(spt_end, spt[last])) / 2 * (n_tokens - last)
    return total, extrapolated


def decode_seconds(at, n_tokens, gen):
    """Decode time from the slowest measured generation rate that is above zero."""
    with_tg = sorted(d for d in at if "tg" in at[d] and at[d]["tg"] > 0)
    if not with_tg:
        return 0.0
    slowest_tg = min(at[d]["tg"] for d in with_tg)
    return gen / slowest_tg


def budget(args):
    at = curve(args.bench) if args.bench else {}
    prefill, extrapolated = prefill_seconds(at, args.tokens) if at else (None, False)
    if prefill is None:
        print(
            f"budget for {args.tokens} tokens: {UNMEASURED_BUDGET_S}s — UNMEASURED "
            "(no llama-bench curve for this model; the deadline is a fixed generous one)",
            file=sys.stderr,
        )
        if args.bench:
            print(f"⛔ --bench {args.bench} could not be used", file=sys.stderr)
        print(UNMEASURED_BUDGET_S)
        return 0
    dec = decode_seconds(at, args.tokens, args.gen)
    # a straight-line continuation past the deepest reading deserves more room than an interpolation
    safety = args.safety * (1.2 if extrapolated else 1.0)
    seconds = int((prefill + dec) * safety) + 60
    seconds = max(args.floor, min(seconds, args.cap))
    print(
        f"budget for {args.tokens} tokens: {seconds}s "
        f"(measured curve: prefill {prefill:.0f}s + decode {dec:.0f}s, safety {safety:.2f}"
        f"{', EXTRAPOLATED past the deepest llama-bench depth' if extrapolated else ''})",
        file=sys.stderr,
    )
    print(seconds)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fit", help="cut the corpus to a real token count, using the server's own tokeniser")
    f.add_argument("--corpus", required=True)
    f.add_argument("--target", type=int, required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--plant-file")
    f.add_argument("--plant-at", type=float, default=0.85)
    f.add_argument("--overhead", type=int, default=128, help="tokens the caller's own wrapper will add")
    f.add_argument("--url")
    f.add_argument("--key")
    f.set_defaults(func=fit)

    t = sub.add_parser("time", help="seconds to allow for a prompt of N tokens, from the measured curve")
    t.add_argument("--tokens", type=int, required=True)
    t.add_argument("--bench", help="the profile's llama-bench results file (rung 3)")
    t.add_argument("--gen", type=int, default=320)
    t.add_argument("--safety", type=float, default=1.5)
    t.add_argument("--floor", type=int, default=300)
    t.add_argument("--cap", type=int, default=7200)
    t.set_defaults(func=budget)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
