#!/usr/bin/env python3
"""The profiles registry: validate, export, inspect and render `config/profiles.json`.

`config/profiles.json` is the single source of truth for the three seat profiles (fast,
long, serious). This tool: validates the file (`check`); prints the shell defaults the
harness `eval`s (`env`); prints the file as JSON, optionally with what is actually served
(`card`); and regenerates the profile table in the skill and the one-line snippet from the
data (`render`).
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

STRING_KEYS = (
    "model", "source", "family", "architecture", "quant", "kv", "kv_v", "flash_attention",
    "reasoning", "extra", "capability_source", "use_for", "depth_probe_100k", "task_t1",
)
INT_KEYS = ("ctx", "useful_ctx", "timeout_s")
OTHER_KEYS = ("vram_gib_after_load", "ram_gb_extra", "params_b", "speed", "capability", "sampling")
PROFILE_KEYS = set(STRING_KEYS) | set(INT_KEYS) | set(OTHER_KEYS)
OPTIONAL_KEYS = {"kv_v", "sampling"}

SKILL_HEADER = "| profile | model | window (useful) | VRAM | decode / prefill at 8k | use for |"
SKILL_SEPARATOR = "|---|---|---|---|---|---|"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


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

        if "kv" in prof and isinstance(prof["kv"], str) and prof["kv"] not in KV_VALUES:
            errors.append(f"{name}: kv must be one of f16, q8_0, q4_0")

        if "kv_v" in prof and isinstance(prof["kv_v"], str) and prof["kv_v"] not in KV_VALUES:
            errors.append(f"{name}: kv_v must be one of f16, q8_0, q4_0")

        for k in ("flash_attention", "reasoning"):
            if k in prof and isinstance(prof[k], str) and prof[k] not in ON_OFF_VALUES:
                errors.append(f"{name}: {k} must be on or off")

        if "speed" in prof:
            speed = prof["speed"]
            if not isinstance(speed, dict) or set(speed.keys()) != {"decode_tps", "prefill_tps"}:
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

        if "capability" in prof:
            cap = prof["capability"]
            if not isinstance(cap, dict):
                errors.append(f"{name}: capability must be an object")
            else:
                for kk, vv in cap.items():
                    if not _is_number(vv):
                        errors.append(f"{name}: capability.{kk} must be a number")

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
                    if not isinstance(extra, str) or "--temp " not in extra:
                        errors.append(f"{name}: sampling.temperature is set but extra carries no --temp")

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

        if name == default:
            label = f"**{name}** (default)"
        else:
            label = f"**--{name}**"
        decode_round = round(decode8k)
        segments.append(
            f"{label} = {_short_model_name(prof['model'])}, {ctx_fmt}-token window "
            f"(useful to ~{useful_k}k), ~{decode_round} tok/s"
        )

    return "; ".join(segments) + "."


def _replace_between_markers(path: Path, generated_lines: list[str], marker: str = "profiles") -> bool:
    """Replace the lines strictly between the named begin/end markers with `generated_lines`.

    Returns True if the markers were found and the file was rewritten, False if the file
    lacks either marker (in which case it is left unchanged and a message is printed).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"profiles: cannot read {path}: {e}", file=sys.stderr)
        return False

    begin_marker = f"<!-- {marker}:begin -->"
    end_marker = f"<!-- {marker}:end -->"

    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()

    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        if begin_idx is None and line.strip() == begin_marker:
            begin_idx = i
        elif begin_idx is not None and end_idx is None and line.strip() == end_marker:
            end_idx = i

    if begin_idx is None or end_idx is None:
        print(f"profiles: no {marker} markers in {path}", file=sys.stderr)
        return False

    new_lines = lines[: begin_idx + 1] + list(generated_lines) + lines[end_idx:]
    new_text = "\n".join(new_lines)
    if ends_with_newline:
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    return True


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

    ok = True

    if not _replace_between_markers(Path(args.skill), table_lines, marker="profiles"):
        ok = False

    if inf_data is not None:
        inf_table_lines = _table_rows(inf_data)
        if not _replace_between_markers(Path(args.skill), inf_table_lines, marker="profiles-inference"):
            ok = False

    if inf_data is None:
        snippet_lines = [sentence]
    else:
        inf_sentence = _snippet_sentence(inf_data)
        snippet_lines = [
            f"Display-safe (`A770B_CARD_MODE=display`, the default): {sentence}",
            f"Pure-inference (`A770B_CARD_MODE=inference`, a card that draws no desktop): {inf_sentence}",
        ]

    if not _replace_between_markers(Path(args.snippet), snippet_lines, marker="profiles"):
        ok = False

    return 0 if ok else 2


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
