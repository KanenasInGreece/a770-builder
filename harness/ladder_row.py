#!/usr/bin/env python3
# ladder_row.py — the last step of harness/ladder.sh, which the ladder runs after its six rungs: it reads every
# rung's own output, computes the fields no single rung produces (useful_ctx, ram_gb_extra, the speed table), writes
# the ladder's ONE results JSON, and prints the REGISTRY ROW an operator pastes into config/profiles.json. It is a
# file rather than a heredoc so it can be called — by a test with a real run's rung outputs, and by hand to recover
# the numbers of a run that was terminated before the ladder reached this step.
#
#   ladder_row.py <out.json> <name> <gguf> <ctx> <kv> <kv_v> <reasoning> <extra> <timeout_s> <profile_name>
#                 <instrument> <far_end> <avail_before_mib> <avail_after_mib>
#                 <bench_model.json> <bench_speed.json> <ctx_sweep.log> <depth_probe.log>
#                 <suite_report_json> <suite_results.json> <fail_rung> <fail_msg> <generated>
#
# The arguments are ladder.sh's own, in its own order; every one may be empty, and a rung that did not run is
# passed as an empty string. <generated> is the timestamp stamped into the results document: the ladder passes the
# current time, a test passes a fixed one. Exits 1 when <fail_rung> is set, as the ladder's own step did.

import json, os, re, sys

(out_path, name, gguf, ctx_s, kv, kv_v, reasoning, extra, timeout_s, profile_name,
 instrument, far_end_s, avail_before_s, avail_after_s,
 bench_model_path, bench_speed_path, ctx_sweep_log, depth_log,
 suite_json_s, suite_results_path, fail_rung, fail_msg, generated) = sys.argv[1:24]

ctx = int(ctx_s)
far_end = int(far_end_s)


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def read_text(path):
    if not path or not os.path.isfile(path):
        return ""
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


bench_model = load_json(bench_model_path)
bench_speed = load_json(bench_speed_path) if bench_speed_path else None

# ram_gb_extra: the host MemAvailable drop across the load (rung 1), in GB
ram_gb_extra = None
try:
    ram_gb_extra = round((int(avail_before_s) - int(avail_after_s)) / 1024.0, 2)
except (ValueError, TypeError):
    pass

vram_gib_after_load = (bench_model or {}).get("vram_gib_after_load")

# ── ctx_sweep.sh's own table: "target tokens ttft_s prefill_tps decode_tps vram_max resets" ────────────────────
sweep_points = {}
sweep_text = read_text(ctx_sweep_log)
for m in re.finditer(
    r'^(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)GiB\s+(-?\d+)\s*$', sweep_text, re.MULTILINE
):
    target, toks, ttft, pp, tg, vmax, resets = m.groups()
    sweep_points[int(target)] = {
        "tokens": int(toks), "ttft_s": float(ttft), "prefill_tps": float(pp),
        "decode_tps": float(tg), "vram_max_gib": float(vmax), "resets": int(resets),
    }
p8, pfar = sweep_points.get(8000), sweep_points.get(far_end)

# ── the depth probe's own verdict line: "depth probe at <tokens>: <n>/3" ────────────────────────────────────
depth_score = None
depth_text = read_text(depth_log)
m = re.search(r'depth probe at \d+: (\d)/3', depth_text)
if m:
    depth_score = int(m.group(1))
depth_pass = None if depth_score is None else depth_score >= 2
# "not measured" is not a score of zero: the server turned the prompt down or the deadline cut it off, so the model
# was never asked. Left as None, it stops the 8,000 cap below from writing a useful_ctx nobody measured.
m_nm = re.search(r'depth probe at \d+: not measured — (.+)', depth_text)
depth_not_measured = m_nm.group(1).strip() if m_nm else None

# ── useful_ctx: the four-tokens-a-second rule (AGENTS.md) — decode time-per-token at 8k and the far end,
# extended linearly between them, capped by the depth probe's last passing depth (8k when the far probe failed,
# since 8k is the only other point this ladder actually probed the model's quality at) ──────────────────────
useful_ctx = None
if p8 and pfar and p8["decode_tps"] > 0 and pfar["decode_tps"] > 0:
    t1, t2 = 1.0 / p8["decode_tps"], 1.0 / pfar["decode_tps"]
    if far_end == 8000 or t2 <= t1:
        useful_ctx = far_end if t2 <= 0.25 else 8000
    else:
        slope = (t2 - t1) / (far_end - 8000)
        d_star = 8000 + (0.25 - t1) / slope
        useful_ctx = max(0, int(d_star))
if depth_pass is False:
    cap = 8000
    useful_ctx = cap if useful_ctx is None else min(useful_ctx, cap)
# a probe the server never answered measured nothing at all, so no window here has any quality behind it:
# report none rather than the sweep's own figure, which would read as if the depth had been checked
if depth_not_measured:
    useful_ctx = None
if useful_ctx is not None:
    useful_ctx = max(0, min(useful_ctx, ctx))

# ── speed.bench: llama-bench's own at_depth, re-derived from bench_speed.sh's raw output the same way it built it
def bench_at_depth(bench):
    if not bench:
        return None
    raw = bench.get("raw")
    rows = raw if isinstance(raw, list) else (raw or {}).get("results", [])

    def field(row, *names):
        for n in names:
            if n in row and row[n] is not None:
                return row[n]
        return None

    depths = sorted({int(field(row, "n_depth", "d") or 0) for row in rows})
    at_depth = {}
    for d in depths:
        pp = tg = None
        for row in rows:
            if int(field(row, "n_depth", "d") or 0) != d:
                continue
            ts = field(row, "avg_ts", "t/s")
            if int(field(row, "n_gen", "n") or 0) == 0 and ts is not None:
                pp = ts
            elif int(field(row, "n_prompt", "p") or 0) == 0 and ts is not None:
                tg = ts
        at_depth[str(d)] = {"pp": pp, "tg": tg}
    return at_depth


speed = {
    "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
    "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
}
at_depth = bench_at_depth(bench_speed)
if at_depth:
    def pick(field_key, depth):
        v = at_depth.get(str(depth))
        return v.get(field_key) if v else None

    for reg_key, bench_field in (("decode_tps", "tg"), ("prefill_tps", "pp")):
        speed[reg_key]["8k"] = pick(bench_field, 8192)
        speed[reg_key]["32k"] = pick(bench_field, 32768)
        speed[reg_key]["64k"] = pick(bench_field, 65536)
        hundred_k_depth = far_end if far_end not in (8192, 32768, 65536) else 100000
        speed[reg_key]["100k"] = pick(bench_field, hundred_k_depth)

    m_flags = re.search(r'-p\s+(\d+)', bench_speed.get("flags") or "")
    n_flags = re.search(r'-n\s+(\d+)', bench_speed.get("flags") or "")
    bench_out = {
        "tool": bench_speed.get("build") or "unknown",
        "prompt": int(m_flags.group(1)) if m_flags else 0,
        "gen": int(n_flags.group(1)) if n_flags else 0,
        "flags": bench_speed.get("flags") or "",
        "at_depth": at_depth,
        "source": os.path.basename(bench_speed_path),
    }
    if bench_out["prompt"] > 0 and bench_out["gen"] > 0 and bench_out["at_depth"]:
        speed["bench"] = bench_out

if pfar:
    speed["far_end"] = {
        "tokens": pfar["tokens"], "decode_tps": pfar["decode_tps"],
        "prefill_tps": pfar["prefill_tps"], "ttft_s": pfar["ttft_s"],
    }

suite = json.loads(suite_json_s) if suite_json_s else None
if suite is not None and suite_results_path:
    suite["source"] = os.path.basename(suite_results_path)

flash_attention = "off" if re.search(r'(?:^|\s)-fa\s+off(?:\s|$)', extra) else "on"

# ── the ladder JSON: every rung's own numbers, whatever ran ─────────────────────────────────────────────────
ladder_doc = {
    "instrument": instrument,
    "generated": generated,
    "input": {
        "profile": profile_name or None, "model": gguf, "ctx": ctx, "kv": kv, "kv_v": kv_v,
        "reasoning": reasoning, "extra": extra, "timeout_s": int(timeout_s) if timeout_s.isdigit() else timeout_s,
        "far_end": far_end,
    },
    "rungs": {
        "load_and_probes": bench_model,
        "standard_speed": bench_speed,
        "window_sweep": sweep_points or None,
        "depth_probe": {"log": depth_log or None, "score": depth_score, "pass": depth_pass,
                        "not_measured": depth_not_measured},
        "task_suite": suite,
    },
    "computed": {
        "useful_ctx": useful_ctx, "ram_gb_extra": ram_gb_extra, "vram_gib_after_load": vram_gib_after_load,
        "speed": speed,
    },
    "stopped_at_rung": fail_rung or None,
    "failure": fail_msg or None,
}
with open(out_path, "w") as f:
    json.dump(ladder_doc, f, indent=2)
    f.write("\n")
print(f"\n▶ ladder results: {out_path}")

# ── the printed row: `delivered` moves from the suite totals to `speed` ─────────────────────────────────────
# suite_report.py folds the suite's own as-delivered medians into its totals object, and other callers read that
# output as it is — but the registry's only home for them is speed.delivered (harness/profiles.py: `speed`
# allows decode_tps, prefill_tps, far_end, bench, delivered; `suite` allows SUITE_KEYS and rejects anything
# else). Left under `suite`, the printed row is refused by `profiles.py check` and has to be hand-edited before
# it can be pasted, which is the one thing a printed row exists to avoid. Both objects are copied here, so the
# ladder JSON written above keeps each rung's output exactly as its producer wrote it.
row_speed = dict(speed)
row_suite = dict(suite) if suite is not None else None
if row_suite is not None:
    row_delivered = row_suite.pop("delivered", None)
    if row_delivered is not None:
        row_speed["delivered"] = row_delivered

row = {
    "model": os.path.basename(gguf), "ctx": ctx, "kv": kv, "kv_v": kv_v,
    "flash_attention": flash_attention, "reasoning": reasoning, "extra": extra,
    "timeout_s": int(timeout_s) if timeout_s.isdigit() else timeout_s,
    "vram_gib_after_load": vram_gib_after_load, "ram_gb_extra": ram_gb_extra,
    "useful_ctx": useful_ctx, "speed": row_speed, "instrument": instrument,
    "measured_on": "__TODO__ — the card and build this was measured on, e.g. \"Arc A770 16 GB, llama.cpp b10805 Vulkan\" (kit/PROFILE.md: never filled by ladder.sh)",
    "use_for": "__TODO__ — write by hand from what this ladder run showed (kit/PROFILE.md: never derived from a measurement)",
    "fit": {
        "code": (
            f"SUITE-1: {suite['passed']}/{suite['runs']} stages passed"
            + (f", mean wall {suite['mean_wall_s']}s" if suite.get("mean_wall_s") is not None else "")
            + " (this ladder run)"
        ) if suite and suite.get("runs") else "not measured",
        "write": "__TODO__ — a reviewer-graded prose brief, or state 'not measured'",
    },
    "capability": "__TODO__ — the model's or the quantiser's own published evaluation, quoted verbatim, never re-measured here",
}
if row_suite is not None:
    row["suite"] = row_suite
if far_end >= 90000 and depth_score is not None:
    row["depth_probe_100k"] = f"{'pass' if depth_pass else 'fail'} ({depth_score}/3)"

print("\n── REGISTRY ROW — paste under profiles.<name> in config/profiles.json (or .inference.json) ──")
print(json.dumps(row, indent=2))
print(
    "\nNot filled above — transcribe once by hand from the GGUF's own metadata and the model's public card "
    "(kit/PROFILE.md §2, \"identity fields, not measurements\"): source, family, architecture, quant, category, "
    "weight_class, params_b, capability_source, and sampling. measured_on is printed above as a placeholder "
    "(__TODO__) rather than omitted, so a row pasted straight from this output still has the key check requires "
    "— replace its text with the card and the serving build."
)
if not (far_end >= 90000 and depth_score is not None):
    if depth_not_measured:
        note = f"the depth probe at {far_end} tokens was never answered — {depth_not_measured}"
    else:
        note = f"depth probe ran at {far_end} tokens (not ~100k)"
    if depth_score is not None:
        note += f": {depth_score}/3 ({'pass' if depth_pass else 'fail'})"
    print(f"depth_probe_100k: not filled — {note}; record it by hand if that is close enough to call the 100k rung.")
print("task_t1: not filled — superseded by suite for a row measured on the kit (kit/PROFILE.md §2).")
print("fit.think: not filled — a reasoning arm measured on this card, or else the model card's own GPQA/AIME figure, named as the card's.")

if fail_rung:
    print(f"\n⛔ ladder stopped at rung '{fail_rung}': {fail_msg}", file=sys.stderr)
    sys.exit(1)
