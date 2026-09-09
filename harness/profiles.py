#!/usr/bin/env python3
"""The profiles registry: validate, export, inspect and render `config/profiles.json`.

`config/profiles.json` is the single source of truth for the three seat profiles (fast,
long, serious). This tool: validates the file (`check`); prints the shell defaults the
harness `eval`s (`env`); prints the file as JSON, optionally with what is actually served
(`card`); and regenerates the profile table in the skill and the one-line snippet from the
data (`render`).

A row's `speed` object may carry two more, both optional and both filled by hand from a
measurement, never computed by this tool:

- `speed.bench`: the standard llama-bench instrument run at the row's own served flags
  (`harness/bench_speed.sh <profile>`) — `tool` (the build id llama-bench itself reports),
  `prompt`/`gen` (its `-p`/`-n`), `flags` (the full command line), `at_depth` (one entry per
  `-d` depth measured, keyed by the depth as a digit string, each `{"pp": ..., "tg": ...}`
  in tokens/second), and `source` (the results file under `A770B_DATA/results/` it came
  from).
- `speed.delivered`: the standard suite's own as-delivered medians (`harness/suite_report.py`
  on a `run_suite.sh` results file) — `prefill_tps`, `decode_tps`, and `source` (the suite
  results file).

`speed.decode_tps` and `speed.prefill_tps` keep their four keys (`8k`, `32k`, `64k`,
`100k`) exactly as before this pair was added; when a row also carries `speed.bench`, those
four keys MAY be filled from `bench.at_depth` by depth: `8k` from `at_depth["8192"]`, `32k`
from `at_depth["32768"]`, `64k` from `at_depth["65536"]`, `100k` from `at_depth["100000"]`
(or, on a window that ends before 110k, from whichever depth the row's own far end used) —
`decode_tps.<k>` from that depth's `tg`, `prefill_tps.<k>` from its `pp`. This tool never
performs that fill itself: a row's author reads `bench.at_depth` and writes the four keys by
hand, the same way every other number in the registry is a human transcription of a
measurement, not a derived value.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = REPO_ROOT / "config" / "profiles.json"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SPEED_KEYS = {"8k", "32k", "64k", "100k"}
KV_VALUES = {"f16", "q8_0", "q4_0"}
ON_OFF_VALUES = {"on", "off"}
MODE_VALUES = {"display", "inference"}
SAMPLING_NUMBER_KEYS = {"temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"}
SAMPLING_MODE_VALUES = {"thinking", "instruct"}
SAMPLING_KEYS = SAMPLING_NUMBER_KEYS | {"mode", "source"}
CATEGORY_VALUES = {"dense", "moe"}
WEIGHT_CLASS_RE = re.compile(r"^[0-9]+b(-[ae][0-9]+b)?$")
FAR_END_KEYS = {"tokens", "decode_tps", "prefill_tps", "ttft_s"}
BENCH_KEYS = {"tool", "prompt", "gen", "flags", "at_depth", "source"}
BENCH_AT_DEPTH_VALUE_KEYS = {"pp", "tg"}
DELIVERED_KEYS = {"prefill_tps", "decode_tps", "source"}
FIT_KEYS = {"code", "think", "write"}
SUITE_KEYS = {"briefs", "runs", "passed", "timeouts", "mean_wall_s", "source", "instrument", "stages", "reviewer"}
SUITE_REQUIRED_KEYS = {"briefs", "runs", "passed", "source"}
STAGE_KEYS = {
    "id", "working", "conformance", "lines", "budget_lines", "maintainable", "usable", "wall_s", "axes",
    "counts_toward_pass",
}
MAINTAINABLE_USABLE_VALUES = (0, 3, 5)
REVIEWER_KEYS = {"profile", "model", "ctx", "sampling", "rubric_sha256"}
TEMP_FLAG_RE = re.compile(r"(?:^|\s)--temp(?:\s|=)")
TOP_P_FLAG_RE = re.compile(r"(?:^|\s)--top-p(?:\s|=)")
TASK_T1_PASS_RE = re.compile(r"^pass(?:\s*\(.*\))?$", re.IGNORECASE)

STRING_KEYS = (
    "model", "source", "family", "architecture", "quant", "kv", "kv_v", "flash_attention",
    "reasoning", "extra", "capability_source", "use_for", "depth_probe_100k", "task_t1",
    "category", "weight_class", "measured_on",
)
INT_KEYS = ("ctx", "useful_ctx", "timeout_s", "output_tokens")
OTHER_KEYS = (
    "vram_gib_after_load", "ram_gb_extra", "params_b", "speed", "capability", "sampling", "fit", "suite",
    "instrument",
)
PROFILE_KEYS = set(STRING_KEYS) | set(INT_KEYS) | set(OTHER_KEYS)
OPTIONAL_KEYS = {"kv_v", "sampling", "output_tokens", "fit", "suite"}

SKILL_HEADER = "| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |"
SKILL_SEPARATOR = "|---|---|---|---|---|---|"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _fmt_sampling_num(v) -> str:
    """Minimal float text for a sampling number: 0.6, not 0.6000000000000001; 1.0, not 1."""
    return repr(float(v))


def sh_single_quote(s: str) -> str:
    """Quote `s` for a POSIX shell using single quotes, escaping any single quote it holds."""
    return "'" + s.replace("'", "'\\''") + "'"


def validate(data) -> list[str]:
    """Return a list of problem strings (without the `profiles: ` prefix), empty if valid."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["not a JSON object"]

    for k in data.keys():
        if k not in {"schema", "default", "notes", "profiles", "mode"}:
            errors.append(f"unknown key {k}")

    if data.get("schema") != 1:
        errors.append("schema: must be 1")

    if "mode" in data and data["mode"] not in MODE_VALUES:
        errors.append("mode: must be display or inference")

    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        errors.append("profiles: must be a non-empty object")
        profiles = {}

    default = data.get("default")
    if not isinstance(default, str) or default not in profiles:
        errors.append(f"default: no such profile {default!r}")

    for name, prof in profiles.items():
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{name}: invalid name")
        if not isinstance(prof, dict):
            errors.append(f"{name}: must be an object")
            continue

        for k in prof.keys():
            if k not in PROFILE_KEYS:
                errors.append(f"{name}: unknown key {k}")
        for k in PROFILE_KEYS:
            if k not in OPTIONAL_KEYS and k not in prof:
                errors.append(f"{name}: missing key {k}")

        for k in STRING_KEYS:
            if k in prof and not isinstance(prof[k], str):
                errors.append(f"{name}: {k} must be a string")

        if isinstance(prof.get("model"), str) and "/" in prof["model"]:
            errors.append(f"{name}: model must be a bare file name")

        for k in INT_KEYS:
            if k in prof and not _is_pos_int(prof[k]):
                errors.append(f"{name}: {k} must be a positive int")

        if _is_pos_int(prof.get("ctx")) and _is_pos_int(prof.get("useful_ctx")):
            if prof["useful_ctx"] > prof["ctx"]:
                errors.append(f"{name}: useful_ctx must be <= ctx")

        if "vram_gib_after_load" in prof:
            v = prof["vram_gib_after_load"]
            if not _is_number(v) or v <= 0:
                errors.append(f"{name}: vram_gib_after_load must be a positive number")

        if "ram_gb_extra" in prof:
            v = prof["ram_gb_extra"]
            if not _is_number(v) or v < 0:
                errors.append(f"{name}: ram_gb_extra must be a non-negative number")

        if "params_b" in prof:
            pb = prof["params_b"]
            if not isinstance(pb, dict) or set(pb.keys()) != {"total", "active"}:
                errors.append(f"{name}: params_b must be an object with total and active")
            else:
                total, active = pb.get("total"), pb.get("active")
                if not _is_number(total) or total <= 0:
                    errors.append(f"{name}: params_b.total must be a positive number")
                if not _is_number(active) or active <= 0:
                    errors.append(f"{name}: params_b.active must be a positive number")
                if _is_number(total) and _is_number(active) and active > total:
                    errors.append(f"{name}: params_b.active must be <= total")

        if "category" in prof and isinstance(prof["category"], str) and prof["category"] not in CATEGORY_VALUES:
            errors.append(f"{name}: category must be dense or moe")

        if "weight_class" in prof and isinstance(prof["weight_class"], str) and not WEIGHT_CLASS_RE.match(prof["weight_class"]):
            errors.append(f"{name}: weight_class must look like 9b, 35b-a3b or 8b-e4b")

        if "kv" in prof and isinstance(prof["kv"], str) and prof["kv"] not in KV_VALUES:
            errors.append(f"{name}: kv must be one of f16, q8_0, q4_0")

        if "kv_v" in prof and isinstance(prof["kv_v"], str) and prof["kv_v"] not in KV_VALUES:
            errors.append(f"{name}: kv_v must be one of f16, q8_0, q4_0")

        for k in ("flash_attention", "reasoning"):
            if k in prof and isinstance(prof[k], str) and prof[k] not in ON_OFF_VALUES:
                errors.append(f"{name}: {k} must be on or off")

        if "speed" in prof:
            speed = prof["speed"]
            speed_allowed = {"decode_tps", "prefill_tps", "far_end", "bench", "delivered"}
            if not isinstance(speed, dict) or not {"decode_tps", "prefill_tps"} <= set(speed.keys()) or set(speed.keys()) - speed_allowed:
                errors.append(f"{name}: speed must be an object with decode_tps and prefill_tps")
            else:
                for sk in ("decode_tps", "prefill_tps"):
                    sv = speed.get(sk)
                    if not isinstance(sv, dict) or set(sv.keys()) != SPEED_KEYS:
                        errors.append(f"{name}: speed.{sk} must have keys 8k, 32k, 64k, 100k")
                    else:
                        for kk, vv in sv.items():
                            if vv is not None and not _is_number(vv):
                                errors.append(f"{name}: speed.{sk}.{kk} must be a number or null")

                if "far_end" in speed:
                    far_end = speed["far_end"]
                    if not isinstance(far_end, dict) or set(far_end.keys()) != FAR_END_KEYS:
                        errors.append(f"{name}: speed.far_end must be an object with tokens, decode_tps, prefill_tps, ttft_s")
                    else:
                        if not _is_pos_int(far_end["tokens"]):
                            errors.append(f"{name}: speed.far_end.tokens must be a positive int")
                        for kk in ("decode_tps", "prefill_tps", "ttft_s"):
                            vv = far_end[kk]
                            if not _is_number(vv) or vv <= 0:
                                errors.append(f"{name}: speed.far_end.{kk} must be a positive number")

                if "bench" in speed:
                    bench = speed["bench"]
                    if not isinstance(bench, dict) or set(bench.keys()) != BENCH_KEYS:
                        errors.append(f"{name}: speed.bench must be an object with tool, prompt, gen, flags, at_depth, source")
                    else:
                        for kk in ("tool", "flags", "source"):
                            vv = bench.get(kk)
                            if not isinstance(vv, str) or not vv:
                                errors.append(f"{name}: speed.bench.{kk} must be a non-empty string")
                        for kk in ("prompt", "gen"):
                            if not _is_pos_int(bench.get(kk)):
                                errors.append(f"{name}: speed.bench.{kk} must be a positive int")
                        at_depth = bench.get("at_depth")
                        if not isinstance(at_depth, dict) or not at_depth:
                            errors.append(f"{name}: speed.bench.at_depth must be a non-empty object")
                        else:
                            for dk, dv in at_depth.items():
                                if not isinstance(dk, str) or not dk.isdigit():
                                    errors.append(f"{name}: speed.bench.at_depth: key {dk!r} must be a digit string")
                                if not isinstance(dv, dict) or set(dv.keys()) != BENCH_AT_DEPTH_VALUE_KEYS:
                                    errors.append(f"{name}: speed.bench.at_depth.{dk} must be an object with pp, tg")
                                else:
                                    for vk in BENCH_AT_DEPTH_VALUE_KEYS:
                                        vv = dv[vk]
                                        if vv is not None and not _is_number(vv):
                                            errors.append(f"{name}: speed.bench.at_depth.{dk}.{vk} must be a number or null")

                if "delivered" in speed:
                    delivered = speed["delivered"]
                    if not isinstance(delivered, dict) or set(delivered.keys()) != DELIVERED_KEYS:
                        errors.append(f"{name}: speed.delivered must be an object with prefill_tps, decode_tps, source")
                    else:
                        for kk in ("prefill_tps", "decode_tps"):
                            vv = delivered.get(kk)
                            if not _is_number(vv) or vv <= 0:
                                errors.append(f"{name}: speed.delivered.{kk} must be a positive number")
                        src = delivered.get("source")
                        if not isinstance(src, str) or not src:
                            errors.append(f"{name}: speed.delivered.source must be a non-empty string")

        if "capability" in prof:
            cap = prof["capability"]
            if not isinstance(cap, dict):
                errors.append(f"{name}: capability must be an object")
            else:
                for kk, vv in cap.items():
                    if not _is_number(vv):
                        errors.append(f"{name}: capability.{kk} must be a number")

        if "instrument" in prof:
            v = prof["instrument"]
            if not isinstance(v, str) or not v:
                errors.append(f"{name}: instrument must be a non-empty string")

        if "fit" in prof:
            fit = prof["fit"]
            if not isinstance(fit, dict):
                errors.append(f"{name}: fit must be an object")
            else:
                for kk in fit.keys():
                    if kk not in FIT_KEYS:
                        errors.append(f"{name}: fit: unknown key {kk}")
                for kk in FIT_KEYS:
                    if kk in fit:
                        vv = fit[kk]
                        if not isinstance(vv, str) or not vv:
                            errors.append(f"{name}: fit.{kk} must be a non-empty string")

        if "suite" in prof:
            suite = prof["suite"]
            if not isinstance(suite, dict):
                errors.append(f"{name}: suite must be an object")
            else:
                for kk in suite.keys():
                    if kk not in SUITE_KEYS:
                        errors.append(f"{name}: suite: unknown key {kk}")
                for kk in SUITE_REQUIRED_KEYS:
                    if kk not in suite:
                        errors.append(f"{name}: suite: missing key {kk}")
                for kk in ("briefs", "runs"):
                    if kk in suite and not _is_pos_int(suite[kk]):
                        errors.append(f"{name}: suite.{kk} must be a positive int")
                if "passed" in suite:
                    passed = suite["passed"]
                    if not isinstance(passed, int) or isinstance(passed, bool):
                        errors.append(f"{name}: suite.passed must be between 0 and runs")
                    else:
                        runs = suite.get("runs")
                        if _is_pos_int(runs):
                            if not (0 <= passed <= runs):
                                errors.append(f"{name}: suite.passed must be between 0 and runs")
                        elif passed < 0:
                            errors.append(f"{name}: suite.passed must be between 0 and runs")
                if "timeouts" in suite:
                    # a stage whose outcome is "timeout" (harness/suite_report.py's totals) — a slow model, not a
                    # wrong one; never more than the runs it came from.
                    timeouts = suite["timeouts"]
                    if not isinstance(timeouts, int) or isinstance(timeouts, bool):
                        errors.append(f"{name}: suite.timeouts must be between 0 and runs")
                    else:
                        runs = suite.get("runs")
                        if _is_pos_int(runs):
                            if not (0 <= timeouts <= runs):
                                errors.append(f"{name}: suite.timeouts must be between 0 and runs")
                        elif timeouts < 0:
                            errors.append(f"{name}: suite.timeouts must be between 0 and runs")
                if "mean_wall_s" in suite:
                    v = suite["mean_wall_s"]
                    if not _is_number(v) or v <= 0:
                        errors.append(f"{name}: suite.mean_wall_s must be a positive number")
                if "source" in suite:
                    v = suite["source"]
                    if not isinstance(v, str) or not v:
                        errors.append(f"{name}: suite.source must be a non-empty string")

                if "instrument" in suite:
                    v = suite["instrument"]
                    if not isinstance(v, str) or not v:
                        errors.append(f"{name}: suite.instrument must be a non-empty string")

                if "stages" in suite:
                    stages = suite["stages"]
                    if not isinstance(stages, list):
                        errors.append(f"{name}: suite.stages must be a list")
                    else:
                        for i, stage in enumerate(stages):
                            if not isinstance(stage, dict):
                                errors.append(f"{name}: suite.stages[{i}] must be an object")
                                continue
                            for kk in stage.keys():
                                if kk not in STAGE_KEYS:
                                    errors.append(f"{name}: suite.stages[{i}]: unknown key {kk}")
                            for kk in STAGE_KEYS:
                                if kk not in stage:
                                    errors.append(f"{name}: suite.stages[{i}]: missing key {kk}")

                            if "id" in stage and (not isinstance(stage["id"], str) or not stage["id"]):
                                errors.append(f"{name}: suite.stages[{i}].id must be a non-empty string")

                            if "working" in stage and not isinstance(stage["working"], bool):
                                errors.append(f"{name}: suite.stages[{i}].working must be a bool")

                            if "counts_toward_pass" in stage and not isinstance(stage["counts_toward_pass"], bool):
                                errors.append(f"{name}: suite.stages[{i}].counts_toward_pass must be a bool")

                            if "conformance" in stage:
                                v = stage["conformance"]
                                if v is not None and not isinstance(v, bool):
                                    errors.append(f"{name}: suite.stages[{i}].conformance must be a bool or null")

                            for lk in ("lines", "budget_lines"):
                                if lk in stage:
                                    v = stage[lk]
                                    if v is not None and not _is_int(v):
                                        errors.append(f"{name}: suite.stages[{i}].{lk} must be an int or null")

                            for sk in ("maintainable", "usable"):
                                if sk in stage:
                                    v = stage[sk]
                                    if v is not None and (isinstance(v, bool) or v not in MAINTAINABLE_USABLE_VALUES):
                                        errors.append(f"{name}: suite.stages[{i}].{sk} must be 0, 3, 5 or null")

                            if "wall_s" in stage and not _is_number(stage["wall_s"]):
                                errors.append(f"{name}: suite.stages[{i}].wall_s must be a number")

                            if "axes" in stage:
                                axes = stage["axes"]
                                if not isinstance(axes, list) or not all(isinstance(a, str) for a in axes):
                                    errors.append(f"{name}: suite.stages[{i}].axes must be a list of strings")

                if "reviewer" in suite:
                    reviewer = suite["reviewer"]
                    if not isinstance(reviewer, dict):
                        errors.append(f"{name}: suite.reviewer must be an object")
                    else:
                        for kk in reviewer.keys():
                            if kk not in REVIEWER_KEYS:
                                errors.append(f"{name}: suite.reviewer: unknown key {kk}")
                        for kk in REVIEWER_KEYS:
                            if kk in reviewer:
                                vv = reviewer[kk]
                                if isinstance(vv, bool) or not isinstance(vv, (str, int)):
                                    errors.append(f"{name}: suite.reviewer.{kk} must be a string or int")

        if "sampling" in prof:
            sampling = prof["sampling"]
            if not isinstance(sampling, dict):
                errors.append(f"{name}: sampling must be an object")
            else:
                for kk in sampling.keys():
                    if kk not in SAMPLING_KEYS:
                        errors.append(f"{name}: sampling: unknown key {kk}")
                for kk in SAMPLING_NUMBER_KEYS:
                    if kk in sampling and not _is_number(sampling[kk]):
                        errors.append(f"{name}: sampling.{kk} must be a number")
                if "mode" in sampling and sampling["mode"] not in SAMPLING_MODE_VALUES:
                    errors.append(f"{name}: sampling.mode must be thinking or instruct")
                source = sampling.get("source")
                if not isinstance(source, str) or not source:
                    errors.append(f"{name}: sampling.source must be a non-empty string")

                if _is_number(sampling.get("temperature")):
                    extra = prof.get("extra")
                    if not isinstance(extra, str) or not TEMP_FLAG_RE.search(extra):
                        errors.append(f"{name}: sampling.temperature is set but extra carries no --temp")

                if _is_number(sampling.get("top_p")):
                    extra = prof.get("extra")
                    if not isinstance(extra, str) or not TOP_P_FLAG_RE.search(extra):
                        errors.append(f"{name}: sampling.top_p is set but extra carries no --top-p")

    # Registry-wide rules, over the whole `profiles` dict rather than one profile at a time:
    # the project keeps one best row per (category, weight_class) class, and a registry is
    # measurements from one instrument, never a mix.
    category_class_seen: dict[tuple, str] = {}
    first_instrument = None
    first_instrument_owner = None
    for name, prof in profiles.items():
        if not isinstance(prof, dict):
            continue

        category = prof.get("category")
        weight_class = prof.get("weight_class")
        if isinstance(category, str) and isinstance(weight_class, str):
            key = (category, weight_class)
            if key in category_class_seen:
                errors.append(
                    f"{name}: category {category!r} + weight_class {weight_class!r} duplicates "
                    f"{category_class_seen[key]}'s -- a registry keeps one best row per class"
                )
            else:
                category_class_seen[key] = name

        instrument = prof.get("instrument")
        if isinstance(instrument, str) and instrument:
            if first_instrument is None:
                first_instrument = instrument
                first_instrument_owner = name
            elif instrument != first_instrument:
                errors.append(
                    f"{name}: instrument {instrument!r} differs from {first_instrument_owner}'s "
                    f"{first_instrument!r} -- a registry is one instrument"
                )

    return errors


def _load(path: Path):
    """Return (data, error). error is None on success, else a problem string."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"{path}: not valid JSON: {e}"


def cmd_check(args) -> int:
    path = Path(args.file)
    data, err = _load(path)
    if err is not None:
        print(f"profiles: {err}", file=sys.stderr)
        return 2
    errors = validate(data)
    if errors:
        for e in errors:
            print(f"profiles: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_env(args) -> int:
    rc = cmd_check(args)
    if rc != 0:
        return rc

    data, err = _load(Path(args.file))
    if err is not None:
        print(f"profiles: {err}", file=sys.stderr)
        return 2

    profiles = data["profiles"]
    names = list(profiles.keys())

    print(': "${A770B_PROFILES:=%s}"' % " ".join(names))
    print(': "${A770B_DEFAULT_PROFILE:=%s}"' % data["default"])

    for name, prof in profiles.items():
        upper = name.upper().replace("-", "_")
        print(': "${A770B_%s_MODEL:=%s}"' % (upper, prof["model"]))
        print(': "${A770B_%s_CTX:=%d}"' % (upper, prof["ctx"]))
        print(': "${A770B_%s_KV:=%s}"' % (upper, prof["kv"]))
        print(': "${A770B_%s_KV_V:=%s}"' % (upper, prof.get("kv_v", prof["kv"])))
        sampling = prof.get("sampling") or {}
        temp = sampling.get("temperature")
        top_p = sampling.get("top_p")
        temp_str = _fmt_sampling_num(temp) if _is_number(temp) else ""
        top_p_str = _fmt_sampling_num(top_p) if _is_number(top_p) else ""
        print(': "${A770B_%s_TEMPERATURE:=%s}"' % (upper, temp_str))
        print(': "${A770B_%s_TOP_P:=%s}"' % (upper, top_p_str))
        output_tokens = prof.get("output_tokens")
        output_tokens_str = str(output_tokens) if _is_pos_int(output_tokens) else ""
        print(': "${A770B_%s_OUTPUT_TOKENS:=%s}"' % (upper, output_tokens_str))
        print(': "${A770B_%s_REASONING:=%s}"' % (upper, prof["reasoning"]))
        print(': "${A770B_%s_TIMEOUT:=%d}"' % (upper, prof["timeout_s"]))
        print(
            '[ -n "${A770B_%s_EXTRA:-}" ] || A770B_%s_EXTRA=%s'
            % (upper, upper, sh_single_quote(prof["extra"]))
        )

    return 0


def _card_warnings(profiles: dict) -> dict:
    """Per-profile warning strings: a served file that inverts two profiles, or a served ctx above the file's."""
    file_model_owner = {p.get("model"): n for n, p in profiles.items()}
    warnings = {}
    for name in profiles:
        prof = profiles[name]
        upper = name.upper().replace("-", "_")
        w = []
        served_model = prof.get("served_model")
        file_model = prof.get("model")
        if served_model != file_model:
            other = file_model_owner.get(served_model)
            if other is not None and other != name:
                w.append(f"profile {name} serves {other}'s file ({served_model}): A770B_{upper}_MODEL comes from the environment or builder.env and inverts the profiles; an earlier release named them the other way round")
        served_ctx = prof.get("served_ctx")
        file_ctx = prof.get("ctx")
        if isinstance(served_ctx, int) and isinstance(file_ctx, int) and served_ctx > file_ctx:
            w.append(f"profile {name} serves {served_ctx} tokens, more than the registry's {file_ctx}: the card's numbers were measured at the smaller window")
        warnings[name] = w
    return warnings


def _builder_class(prof: dict) -> bool:
    """Whether a profile clears the builder-class bar: useful_ctx >= 81920 and a green task.

    Computed by `card`; never stored in the registry file itself. `axes` describes what a
    stage was scored on and never drives the tally -- a stage counts toward the pass ratio
    when its own `counts_toward_pass` is not false (the same rule `harness/suite_report.py`
    and `harness/run_suite.sh` honour; `axes` alone would undercount, since no stage in
    `kit/suite.json` puts "working" in `axes`).

    The suite is the authority whenever it is present: with a `suite` object, builder_class
    is exactly whether its counted pass ratio is at least 0.8 (from `suite.stages` when
    present, else `suite.passed` / `suite.runs`) -- an old `task_t1: "pass"` never overrides
    a suite that recorded failures. `task_t1` is consulted only when there is no `suite`
    object at all.
    """
    useful_ctx = prof.get("useful_ctx")
    if not _is_pos_int(useful_ctx) or useful_ctx < 81920:
        return False

    suite = prof.get("suite")
    if isinstance(suite, dict):
        stages = suite.get("stages")
        if isinstance(stages, list):
            counted_stages = [
                s for s in stages
                if isinstance(s, dict) and s.get("counts_toward_pass", True) is not False
            ]
            passed = sum(1 for s in counted_stages if s.get("working") is True)
            runs = len(counted_stages)
        else:
            passed, runs = suite.get("passed"), suite.get("runs")
            if not (isinstance(passed, int) and not isinstance(passed, bool) and _is_pos_int(runs)):
                passed, runs = None, None
        return bool(runs) and (passed / runs) >= 0.8

    task_t1 = prof.get("task_t1")
    return isinstance(task_t1, str) and bool(TASK_T1_PASS_RE.match(task_t1.strip()))


def cmd_card(args) -> int:
    path = Path(args.file)
    data, err = _load(path)
    if err is not None:
        print(f"profiles: {err}", file=sys.stderr)
        return 2

    data["mode"] = data.get("mode")
    data["registry"] = str(path.resolve())

    profiles = data.get("profiles", {})
    for name, prof in profiles.items():
        upper = name.upper().replace("-", "_")
        served = {
            "served_model": prof.get("model"),
            "served_ctx": prof.get("ctx"),
            "served_kv": prof.get("kv"),
            "served_kv_v": prof.get("kv_v", prof.get("kv")),
            "served_reasoning": prof.get("reasoning"),
        }
        if args.served:
            overrides = {
                "served_model": os.environ.get(f"A770B_{upper}_MODEL"),
                "served_ctx": (int(os.environ[f"A770B_{upper}_CTX"]) if os.environ.get(f"A770B_{upper}_CTX", "").isdigit() else None),
                "served_kv": os.environ.get(f"A770B_{upper}_KV"),
                "served_kv_v": os.environ.get(f"A770B_{upper}_KV_V"),
                "served_reasoning": os.environ.get(f"A770B_{upper}_REASONING"),
            }
            for k, v in overrides.items():
                if v is not None:
                    served[k] = v
        prof.update(served)
        prof["builder_class"] = _builder_class(prof)

    comparability_key_by_name = {
        n: (p.get("instrument"), p.get("measured_on")) for n, p in profiles.items()
    }
    for name, prof in profiles.items():
        key = comparability_key_by_name[name]
        prof["comparable_with"] = [
            n for n in profiles if n != name and comparability_key_by_name[n] == key
        ]

    warnings_by_profile = _card_warnings(profiles)
    data["warnings"] = [w for name in profiles for w in warnings_by_profile[name]]

    if args.name is not None:
        if args.name not in profiles:
            print(f"profiles: no profile {args.name}", file=sys.stderr)
            return 2
        out = dict(profiles[args.name])
        out["name"] = args.name
        out["mode"] = data["mode"]
        out["registry"] = data["registry"]
        out["warnings"] = warnings_by_profile.get(args.name, [])
        print(json.dumps(out, indent=2))
        return 0

    print(json.dumps(data, indent=2))
    return 0


def _short_model_name(model: str) -> str:
    if model.endswith(".gguf"):
        model = model[: -len(".gguf")]
    return model.replace("-it", "")


def _table_rows(data: dict) -> list[str]:
    """The skill table's header, separator, and one row per profile."""
    profiles = data["profiles"]
    default = data["default"]

    lines = [SKILL_HEADER, SKILL_SEPARATOR]
    for name, prof in profiles.items():
        ctx_fmt = f"{prof['ctx']:,}"
        useful_k = prof["useful_ctx"] // 1000
        decode8k = prof["speed"]["decode_tps"]["8k"]
        prefill8k = prof["speed"]["prefill_tps"]["8k"]

        display_name = name + (" (default)" if name == default else "")
        lines.append(
            f"| {display_name} | {prof['model']} | {ctx_fmt} (~{useful_k}k) | "
            f"{prof['vram_gib_after_load']} GiB | {decode8k} / {prefill8k} tok/s | {prof['use_for']} |"
        )
    return lines


def _snippet_sentence(data: dict) -> str:
    """The one-line snippet sentence for a registry."""
    profiles = data["profiles"]
    default = data["default"]

    segments = []
    for name, prof in profiles.items():
        ctx_fmt = f"{prof['ctx']:,}"
        useful_k = prof["useful_ctx"] // 1000
        decode8k = prof["speed"]["decode_tps"]["8k"]

        label = f"**--profile {name}**" + (" (default)" if name == default else "")
        decode_round = round(decode8k)
        segments.append(
            f"{label} = {_short_model_name(prof['model'])}, {ctx_fmt}-token window "
            f"(useful to ~{useful_k}k), ~{decode_round} tok/s"
        )

    return "; ".join(segments) + "."


def _find_markers(lines: list[str], marker: str) -> tuple[int, int] | None:
    """Return (begin_idx, end_idx) of the marker pair in `lines`, or None if either is absent."""
    begin_marker = f"<!-- {marker}:begin -->"
    end_marker = f"<!-- {marker}:end -->"

    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        if begin_idx is None and line.strip() == begin_marker:
            begin_idx = i
        elif begin_idx is not None and end_idx is None and line.strip() == end_marker:
            end_idx = i

    if begin_idx is None or end_idx is None:
        return None
    return begin_idx, end_idx


def _apply_between_markers(text: str, begin_idx: int, end_idx: int, generated_lines: list[str]) -> str:
    """Return `text` with the lines strictly between begin_idx and end_idx replaced."""
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    new_lines = lines[: begin_idx + 1] + list(generated_lines) + lines[end_idx:]
    new_text = "\n".join(new_lines)
    if ends_with_newline:
        new_text += "\n"
    return new_text


def cmd_render(args) -> int:
    data, err = _load(Path(args.file))
    if err is not None:
        print(f"profiles: {err}", file=sys.stderr)
        return 2

    table_lines = _table_rows(data)
    sentence = _snippet_sentence(data)

    inf_data = None
    if args.inference_file:
        inf_data, err = _load(Path(args.inference_file))
        if err is not None:
            print(f"profiles: {err}", file=sys.stderr)
            return 2

    if inf_data is None:
        snippet_lines = [sentence]
    else:
        inf_sentence = _snippet_sentence(inf_data)
        snippet_lines = [
            f"Display-safe (`A770B_CARD_MODE=display`, the default): {sentence}",
            f"Pure-inference (`A770B_CARD_MODE=inference`, a card that draws no desktop): {inf_sentence}",
        ]

    # Every (path, marker) pair this render needs to touch, in write order. The skill file
    # can appear twice (the "profiles" table and, with an inference file, "profiles-inference").
    targets: list[tuple[Path, str, list[str]]] = [(Path(args.skill), "profiles", table_lines)]
    if inf_data is not None:
        targets.append((Path(args.skill), "profiles-inference", _table_rows(inf_data)))
    targets.append((Path(args.snippet), "profiles", snippet_lines))

    texts: dict[Path, str] = {}
    for path, _marker, _lines in targets:
        if path not in texts:
            try:
                texts[path] = path.read_text(encoding="utf-8")
            except OSError as e:
                print(f"profiles: cannot read {path}: {e}", file=sys.stderr)
                return 2

    # Check every marker pair before writing anything: a missing pair in any target file
    # must leave every target file untouched, not just the ones after it.
    ok = True
    for path, marker, _lines in targets:
        if _find_markers(texts[path].splitlines(), marker) is None:
            print(f"profiles: no {marker} markers in {path}", file=sys.stderr)
            ok = False
    if not ok:
        return 2

    # All pairs confirmed present: apply the replacements in memory, then write once per
    # path. Re-find each marker's position on the current in-memory text, since an earlier
    # replacement on the same path (e.g. "profiles" before "profiles-inference") can shift
    # the line numbers a later marker sits at.
    new_texts = dict(texts)
    for path, marker, lines in targets:
        begin_idx, end_idx = _find_markers(new_texts[path].splitlines(), marker)
        new_texts[path] = _apply_between_markers(new_texts[path], begin_idx, end_idx, lines)

    for path, text in new_texts.items():
        path.write_text(text, encoding="utf-8")

    return 0


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", default=str(DEFAULT_FILE), help="Path to config/profiles.json")

    parser = argparse.ArgumentParser(description="Read and render the A770 builder profiles registry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", parents=[common], help="Validate the profiles file")

    subparsers.add_parser("env", parents=[common], help="Print the shell defaults the harness evals")

    card_parser = subparsers.add_parser("card", parents=[common], help="Print the profiles file as JSON")
    card_parser.add_argument("--name", help="Print only this profile's object")
    card_parser.add_argument("--served", action="store_true", help="Show what the environment actually serves")

    render_parser = subparsers.add_parser("render", parents=[common], help="Regenerate the skill table and snippet")
    render_parser.add_argument("--skill", required=True, help="Path to the skill file to update")
    render_parser.add_argument("--snippet", required=True, help="Path to the snippet file to update")
    render_parser.add_argument("--inference-file", help="Path to config/profiles.inference.json, to also render")

    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    elif args.command == "env":
        return cmd_env(args)
    elif args.command == "card":
        return cmd_card(args)
    elif args.command == "render":
        return cmd_render(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
