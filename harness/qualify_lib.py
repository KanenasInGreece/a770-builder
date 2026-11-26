#!/usr/bin/env python3
"""Plan, status, and result helpers for the qualification runner.

The shell owns the process, the card lock, and the signals. This module owns
parsing, the profiling contract, and the files those decisions write. A run
records facts. The verdict string is always needs-review.
"""

from __future__ import annotations

import argparse
import calendar
import fcntl
import glob
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import profiling_contract

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
PROFILES_PY = os.path.join(HERE, "profiles.py")

WINDOW_HEADROOM = 0.25
COMPACTION_PER_REQUESTS = 4
IDLE_VRAM_TOLERANCE_GIB = 0.5
HEARTBEAT_S = 30
VERDICT = "needs-review"
TERMINAL_MARKERS = ("DONE", "FAILED", "STOPPED")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENGINE_MAP = {
    "llama": "llama.cpp",
    "llama.cpp": "llama.cpp",
    "vllm": "vllm",
    "sglang": "sglang",
    "strata": "strata",
}
NO_ROW = (
    "A model with no registry row yet on one of these engines has no kit path "
    "(the kit builds an ephemeral candidate only from a GGUF): the person writes "
    "the candidate row, outside the checkout, in the registry format and passes "
    "it with --profiles-file. Without a row or a file, this engine is refused."
)

# field, who writes it, conditional, source rung. Order matches kit/PROFILE.md §2.
REQUIRED_STATISTICS = (
    {"field": "ctx", "who": "human", "conditional": False, "source": "rung 1"},
    {"field": "useful_ctx", "who": "ladder", "conditional": False, "source": "rung 4"},
    {"field": "kv, kv_v", "who": "human", "conditional": False, "source": "rung 1"},
    {"field": "flash_attention", "who": "ladder", "conditional": False, "source": "rung 1"},
    {"field": "reasoning", "who": "human", "conditional": False, "source": "rung 1"},
    {"field": "extra", "who": "human", "conditional": False, "source": "rung 1"},
    {"field": "sampling", "who": "human", "conditional": False, "source": "card"},
    {"field": "output_tokens", "who": "human", "conditional": False, "source": "knob"},
    {"field": "timeout_s", "who": "human", "conditional": False, "source": "observed wall"},
    {"field": "vram_gib_after_load", "who": "ladder", "conditional": False, "source": "rung 1"},
    {"field": "ram_gb_extra", "who": "ladder", "conditional": False, "source": "rung 1"},
    {
        "field": "speed.decode_tps / speed.prefill_tps",
        "who": "ladder",
        "conditional": False,
        "source": "rung 3",
    },
    {"field": "speed.bench", "who": "ladder", "conditional": False, "source": "rung 3"},
    {"field": "speed.delivered", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "speed.far_end", "who": "ladder", "conditional": False, "source": "rung 4"},
    {"field": "depth_probe_100k", "who": "ladder", "conditional": True, "source": "rung 5"},
    {"field": "task_t1", "who": "human", "conditional": False, "source": "sibling instrument"},
    {"field": "suite", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "requests", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "measured_tasks", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "fit.code", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "fit.think, fit.write", "who": "human", "conditional": False, "source": "human"},
    {
        "field": "capability, capability_source",
        "who": "human",
        "conditional": False,
        "source": "model card",
    },
    {"field": "instrument", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "placement", "who": "human", "conditional": False, "source": "human"},
    {"field": "measured_on", "who": "human", "conditional": False, "source": "human"},
    {"field": "card", "who": "human", "conditional": False, "source": "host"},
    {"field": "backend", "who": "human", "conditional": False, "source": "host"},
    {"field": "tool_parser", "who": "human", "conditional": False, "source": "row"},
    {"field": "engine", "who": "human", "conditional": False, "source": "row"},
    {"field": "checkpoint", "who": "human", "conditional": True, "source": "row"},
    {"field": "kernel", "who": "human", "conditional": False, "source": "log"},
    {"field": "mode", "who": "human", "conditional": False, "source": "host"},
    {"field": "task", "who": "human", "conditional": False, "source": "human"},
    {"field": "evidence", "who": "ladder", "conditional": False, "source": "rung 6"},
    {"field": "builder_class", "who": "computed", "conditional": False, "source": "read time"},
    {"field": "comparable_with", "who": "computed", "conditional": False, "source": "read time"},
    {"field": "use_for", "who": "human", "conditional": False, "source": "human"},
    {"field": "useful_ctx_rule", "who": "ladder", "conditional": False, "source": "rung 4"},
    {"field": "absorb_max_s", "who": "ladder", "conditional": True, "source": "rung 4"},
    {"field": "speed.absorb_s", "who": "ladder", "conditional": False, "source": "rung 4"},
    {"field": "profiling", "who": "ladder", "conditional": False, "source": "serve"},
)

LADDER_HUMAN_NAMES = (
    "source",
    "family",
    "architecture",
    "quant",
    "category",
    "weight_class",
    "params_b",
    "capability_source",
    "sampling",
    "task",
    "measured_on",
    "task_t1",
    "fit.think",
)

NULL_DOTTED = (
    "requests.prefill_tps",
    "requests.decode_tps",
    "requests.length_stops",
    "speed.delivered",
)


class QualifyError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def required_statistic_fields():
    """Ladder fields a complete export must have. Conditional ones may be absent."""
    return [
        row["field"]
        for row in REQUIRED_STATISTICS
        if row["who"] == "ladder" and not row["conditional"]
    ]


def counted_pass_bar() -> float:
    """The pass ratio profiles.py uses. Read from that function, not copied."""
    import inspect

    import profiles

    source = inspect.getsource(profiles._builder_class)
    match = re.search(r">=\s*(0\.\d+)", source)
    if not match:
        raise QualifyError("counted-stage pass bar is not in profiles.py")
    return float(match.group(1))


def canonical_engine(name: str) -> str:
    """Map the CLI spelling to the contract key. Unmapped names never reach it."""
    key = ENGINE_MAP.get(name)
    if key is None:
        raise QualifyError(f"unknown engine {name!r}")
    return key


def output_limit(ctx: int, profile_output, env_output) -> int:
    """The output cap a profile render would write. The rule is not changed here."""
    if profile_output not in (None, ""):
        return int(profile_output)
    if env_output not in (None, ""):
        return int(env_output)
    output = int(ctx) // 2
    if output > 32000:
        return 32000
    if output < 4096 and int(ctx) >= 8192:
        return 4096
    return output


def far_end_and_gen(ctx: int, reasoning: str, budget) -> tuple[int, int]:
    """Same far end and reply budget as harness/ladder.sh."""
    gen = 320
    if reasoning == "on":
        if budget in (None, "", "-1", -1):
            gen = 4096
        elif int(budget) == 0:
            gen = 320
        else:
            gen = int(budget) + 320
    head = max(gen + 64, 1024)
    end = min(100000, int(ctx) - head)
    return end, gen


def speed_sizes(ctx: int) -> list[int]:
    room = int(ctx) - 1024
    return [size for size in (8192, 32768, 65536, 100000) if size <= room]


def _positive_token(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _peak_from_row(row: dict | None, stages: list[str] | None) -> int | None:
    """The row's own measured prompt peak for the stages this run will execute."""
    if not isinstance(row, dict):
        return None
    wanted = set(stages or [])
    peak = None
    tasks = row.get("measured_tasks")
    if isinstance(tasks, dict):
        for name, entry in tasks.items():
            if wanted and name not in wanted:
                continue
            if not isinstance(entry, dict):
                continue
            tokens = _positive_token(entry.get("context_tokens"))
            if tokens is not None:
                peak = tokens if peak is None else max(peak, tokens)
    # A stage filter with per-stage counts does not fall back to the whole-suite peak.
    if peak is None or not wanted:
        requests = row.get("requests")
        if isinstance(requests, dict):
            tokens = _positive_token(requests.get("max_prompt_tokens"))
            if tokens is not None:
                peak = tokens if peak is None else max(peak, tokens)
    return peak


def _token_count(tokenizer_json: str, text: str) -> int | None:
    """Count with the local tokenizer file. A missing library is unknown, not a guess."""
    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None
    try:
        loaded = Tokenizer.from_file(tokenizer_json)
        return len(loaded.encode(text, add_special_tokens=False).ids)
    except (OSError, ValueError, RuntimeError):
        return None


def _tokenize_kit(stages: list[str] | None, tokenizer: str | None) -> tuple[int | None, str]:
    """Briefs, the starter seat, and the OpenCode template. A partial set is not a budget."""
    raw = str(tokenizer or "").strip()
    if not raw:
        return None, ""
    path = raw
    if os.path.isdir(path):
        path = os.path.join(path, "tokenizer.json")
    if not os.path.isfile(path) or os.path.islink(path):
        return None, ""
    suite_path = os.path.join(PROJECT, "kit", "suite.json")
    seat = os.path.join(PROJECT, "kit", "seat")
    template = os.path.join(PROJECT, "config", "opencode.profile.template.jsonc")
    if not os.path.isfile(suite_path) or not os.path.isdir(seat) or not os.path.isfile(template):
        return None, ""
    try:
        suite = _load_json(suite_path)
    except (OSError, ValueError, QualifyError):
        return None, ""
    wanted = set(stages or [])
    parts = []
    briefs = 0
    for stage in suite.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        name = stage.get("id")
        if wanted and name not in wanted:
            continue
        brief = stage.get("brief")
        if not isinstance(brief, str) or not brief:
            continue
        brief_path = brief if os.path.isabs(brief) else os.path.join(PROJECT, brief)
        if not os.path.isfile(brief_path) or os.path.islink(brief_path):
            return None, ""
        try:
            parts.append(open(brief_path, encoding="utf-8", errors="replace").read())
        except OSError:
            return None, ""
        briefs += 1
    if briefs == 0:
        return None, ""
    for dirpath, dirnames, filenames in os.walk(seat):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                parts.append(open(full, encoding="utf-8", errors="replace").read())
            except OSError:
                return None, ""
    try:
        parts.append(open(template, encoding="utf-8", errors="replace").read())
    except OSError:
        return None, ""
    counted = _token_count(path, "\n".join(parts))
    if counted is None:
        return None, ""
    return counted, "tokenized stage briefs, starter seat, and OpenCode template"


def kit_prompt_budget(stages: list[str] | None = None, row: dict | None = None, tokenizer: str | None = None) -> tuple[int | None, str]:
    """Measured kit prompt, or unknown. This does not invent a token count."""
    raw = os.environ.get("A770B_QUALIFY_KIT_PROMPT_TOKENS")
    if raw is not None and raw != "":
        if not re.fullmatch(r"[1-9][0-9]*", raw):
            raise QualifyError(
                "A770B_QUALIFY_KIT_PROMPT_TOKENS must be a positive integer "
                f"(it is {raw!r})"
            )
        return int(raw), "A770B_QUALIFY_KIT_PROMPT_TOKENS"
    peak = _peak_from_row(row, stages)
    if peak is not None:
        return peak, "profile measured_tasks context_tokens or requests.max_prompt_tokens"
    counted, source = _tokenize_kit(stages, tokenizer)
    if counted is not None:
        return counted, source
    return None, "no measured kit prompt total"


def window_plan(ctx: int, profile_output, stages: list[str] | None = None, row: dict | None = None, tokenizer: str | None = None) -> dict:
    """Usable window against the kit prompt plus the named headroom."""
    limit = output_limit(ctx, profile_output, os.environ.get("A770B_OUTPUT_TOKENS"))
    usable = int(ctx) - limit
    kit, kit_source = kit_prompt_budget(stages, row, tokenizer)
    headroom = WINDOW_HEADROOM
    need = None if kit is None else int(math.ceil(kit * (1.0 + headroom)))
    left = None if kit is None else usable - kit
    if kit is None:
        action = "unknown"
        message = (
            f"kit prompt budget is unknown ({kit_source}); "
            f"served ctx {ctx}, output limit {limit}, usable window {usable}"
        )
    elif usable < need:
        action = "refuse"
        message = (
            f"usable window {usable} tokens cannot hold the kit prompt {kit} "
            f"plus {headroom:.0%} headroom (need {need}); "
            f"served ctx {ctx}, output limit {limit}"
        )
    else:
        action = "ok"
        message = (
            f"usable window {usable} holds kit prompt {kit} plus {headroom:.0%} "
            f"headroom (need {need}); served ctx {ctx}, output limit {limit}"
        )
    return {
        "served_ctx": int(ctx),
        "output_limit": limit,
        "usable": usable,
        "kit_tokens": kit,
        "kit_source": kit_source,
        "headroom_ratio": headroom,
        "need": need,
        "headroom_left": left,
        "action": action,
        "message": message,
    }


def _positive_int(text: str, label: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", text or ""):
        raise QualifyError(f"{label} must be a positive integer (it is {text!r})")
    return int(text)


def _split_extra(text: str) -> list[str]:
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError as exc:
        raise QualifyError(f"could not parse --extra ({exc})") from exc


def _option_name(token: str) -> str:
    """Long options compare with '_' folded to '-', the way vLLM and SGLang do."""
    head = token.split("=", 1)[0]
    if head.startswith("--"):
        return "--" + head[2:].replace("_", "-")
    return head


def _refuse_key(tokens: list[str], where: str) -> None:
    for token in tokens:
        name = _option_name(token)
        if name == "--api-key":
            raise QualifyError(
                f"{where} must not carry --api-key; the harness owns "
                "authentication through its key file"
            )


def _refuse_contract(engine: str, tokens: list[str], where: str) -> None:
    message = profiling_contract.refusal(engine, tokens)
    if message:
        raise QualifyError(f"{where}: {message}")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise QualifyError(f"{path} is not a JSON object")
    return data


def _registry_files(profiles_file: str | None) -> list[str]:
    if profiles_file:
        return [profiles_file]
    directory = os.path.join(PROJECT, "config", "registry")
    return sorted(glob.glob(os.path.join(directory, "*.json")))


def find_profile(name: str, profiles_file: str | None) -> tuple[dict | None, str | None]:
    """Return the row and the file that holds it, or (None, None)."""
    for path in _registry_files(profiles_file):
        if not os.path.isfile(path):
            continue
        try:
            data = _load_json(path)
        except (OSError, ValueError) as exc:
            raise QualifyError(f"cannot read profiles file {path}: {exc}") from exc
        profiles = data.get("profiles")
        if isinstance(profiles, dict) and name in profiles and isinstance(profiles[name], dict):
            return profiles[name], path
    return None, None


def _check_profiles_file(path: str) -> None:
    if os.path.islink(path):
        raise QualifyError(f"profiles file is a symlink: {path}")
    run = subprocess.run(
        [sys.executable, PROFILES_PY, "check", "--file", path],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "profiles check failed").strip()
        raise QualifyError(f"profiles file failed check: {detail}")


def _reference_ids() -> set[str]:
    path = os.path.join(PROJECT, "kit", "reference", "reference.json")
    try:
        data = _load_json(path)
    except (OSError, ValueError, QualifyError):
        return set()
    found = set()
    for entry in data.get("entries") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            found.add(entry["id"])
    return found


class _PlanParser(argparse.ArgumentParser):
    def error(self, message):
        raise QualifyError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _PlanParser(prog="qualify start", add_help=False)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--profiles-file")
    parser.add_argument("--ctx")
    parser.add_argument("--backend", choices=("vulkan", "sycl"))
    parser.add_argument("--tokenizer")
    parser.add_argument("--kv")
    parser.add_argument("--kv-v")
    parser.add_argument("--extra", default="")
    parser.add_argument("--reasoning", choices=("on", "off"))
    parser.add_argument("--reasoning-budget")
    parser.add_argument("--stages")
    parser.add_argument("--refs-repeat", default="1")
    parser.add_argument("--absorb-max-s")
    parser.add_argument("--skip-window", action="store_true")
    parser.add_argument("--no-suite", action="store_true")
    parser.add_argument("--fit-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    return parser


def build_plan(argv: list[str]) -> dict:
    """Validate a start line. Creates nothing. Unmapped engines never hit the contract."""
    parser = _parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        raise QualifyError(f"unknown qualify option {unknown[0]}")
    engine = canonical_engine(args.engine)
    refs = _positive_int(args.refs_repeat, "--refs-repeat")
    if args.absorb_max_s is None:
        absorb = None
    else:
        absorb = profiling_contract.parse_absorb_limit(args.absorb_max_s)
    extra_tokens = _split_extra(args.extra)
    _refuse_key(extra_tokens, "--extra")
    _refuse_contract(engine, extra_tokens, "--extra")

    gguf = args.model.lower().endswith(".gguf")
    row = None
    row_file = None
    if args.profiles_file:
        _check_profiles_file(args.profiles_file)
    if engine != "llama.cpp" or not gguf:
        row, row_file = find_profile(args.model, args.profiles_file)
        if row is None:
            if engine != "llama.cpp":
                raise QualifyError(NO_ROW)
            raise QualifyError(f"no registry row named {args.model!r}")
    if row and isinstance(row.get("engine"), str) and row["engine"] != engine:
        raise QualifyError(
            f"--engine {engine} is not the profile engine {row['engine']}"
        )
    if gguf and engine != "llama.cpp":
        if row is None:
            raise QualifyError(NO_ROW)
    if gguf and engine == "llama.cpp" and not args.ctx and row is None:
        raise QualifyError("a bare GGUF needs --ctx")

    ctx = int(row["ctx"]) if row and isinstance(row.get("ctx"), int) else None
    if args.ctx:
        asked = _positive_int(args.ctx, "--ctx")
        if ctx is not None and asked != ctx:
            raise QualifyError(f"--ctx {asked} is not the profile ctx {ctx}")
        ctx = asked
    if ctx is None:
        raise QualifyError("ctx is not set")

    def _same(flag, asked, have):
        if asked is None:
            return have
        if have is not None and str(asked) != str(have):
            raise QualifyError(f"{flag} {asked} is not the profile value {have}")
        return asked

    kv = _same("--kv", args.kv, row.get("kv") if row else None)
    have_kv_v = None
    if row:
        have_kv_v = row.get("kv_v")
        if have_kv_v is None:
            have_kv_v = row.get("kv")
    kv_v = _same("--kv-v", args.kv_v, have_kv_v)
    row_extra = row.get("extra") if row else None
    if row_extra is None:
        row_extra = ""
    if not isinstance(row_extra, str):
        raise QualifyError("profile extra is not a string")
    if args.extra and row and _split_extra(row_extra) != extra_tokens:
        raise QualifyError("--extra is not the profile extra")
    if row and not args.extra:
        extra_tokens = _split_extra(row_extra)
    _refuse_key(extra_tokens, "profile extra")
    _refuse_contract(engine, extra_tokens, "profile extra")

    backend = args.backend
    if row and isinstance(row.get("backend"), str):
        if backend and backend != row["backend"] and row["backend"] in ("vulkan", "sycl"):
            raise QualifyError(
                f"--backend {backend} is not the profile backend {row['backend']}"
            )
        if backend is None and row["backend"] in ("vulkan", "sycl"):
            backend = row["backend"]
    if engine == "llama.cpp" and backend is None:
        backend = os.environ.get("A770B_SERVED_BACKEND") or None
    if engine == "llama.cpp" and backend not in ("vulkan", "sycl"):
        raise QualifyError(
            "llama.cpp needs --backend vulkan|sycl or A770B_SERVED_BACKEND"
        )

    tokenizer = args.tokenizer or os.environ.get("A770B_TOKENIZER_DIR") or None
    if gguf and engine == "llama.cpp" and not tokenizer:
        raise QualifyError(
            "a bare GGUF needs --tokenizer or A770B_TOKENIZER_DIR"
        )

    thinking = row.get("thinking") if row and isinstance(row.get("thinking"), dict) else {}
    reasoning = args.reasoning
    if reasoning is None and thinking.get("mode") in ("on", "off"):
        reasoning = thinking["mode"]
    if reasoning is None and row and row.get("reasoning") in ("on", "off"):
        reasoning = row["reasoning"]
    if reasoning is None:
        reasoning = "off"
    budget = args.reasoning_budget
    if budget is None and "budget" in thinking:
        budget = thinking.get("budget")
    end, gen = far_end_and_gen(ctx, reasoning, budget)
    if end <= 8000:
        raise QualifyError(
            f"the served window ({ctx}) leaves a far end of {end} tokens, "
            "at or below the sweep's 8000-token shallow point"
        )
    profile_output = row.get("output_tokens") if row else None
    stages = [part for part in (args.stages or "").split(",") if part]
    window = window_plan(ctx, profile_output, stages, row, tokenizer)
    fit_only = bool(args.fit_only)
    suite_skipped = fit_only or bool(args.no_suite)
    if window["action"] in ("refuse", "unknown") and not suite_skipped and not args.dry_run:
        raise QualifyError(window["message"])

    ref_ids = _reference_ids()
    refs_apply = True
    if stages and not any(stage in ref_ids or stage.startswith("ref-") for stage in stages):
        refs_apply = False
    reference_stages = []
    for ref in sorted(ref_ids):
        prefix = ref.split("-", 1)[0]
        if not stages or ref in stages or prefix in stages:
            reference_stages.append(ref)
    flags = profiling_contract.flags(engine)
    serve_argv = ["serve", args.model, "--ctx", str(ctx)]
    if backend:
        serve_argv += ["--backend", backend]
    serve_argv += extra_tokens
    serve_argv += flags
    phases = ["preflight", "pre-hook", "serve", "smoke", "speed"]
    if not args.skip_window:
        phases.append("window")
    phases += ["depth", "memory", "stop-server"]
    if not fit_only and not args.no_suite:
        phases.append("suite")
    phases += ["summarise", "post-hook"]
    slug_src = f"{engine}-{os.path.basename(args.model)}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug_src).strip("-")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    job_id = f"{stamp}-{slug}"[:64].strip("-")
    return {
        "engine": engine,
        "engine_cli": args.engine,
        "model": args.model,
        "gguf": bool(gguf and engine == "llama.cpp"),
        "profile": None if row is None else args.model,
        "profiles_file": args.profiles_file or row_file,
        "ctx": ctx,
        "backend": backend,
        "tokenizer": tokenizer,
        "kv": kv,
        "kv_v": kv_v,
        "extra": extra_tokens,
        "reasoning": reasoning,
        "reasoning_budget": budget,
        "stages": stages,
        "refs_repeat": refs,
        "refs_apply": refs_apply,
        "reference_stages": reference_stages,
        "absorb_max_s": absorb,
        "skip_window": bool(args.skip_window),
        "no_suite": bool(args.no_suite),
        "fit_only": fit_only,
        "dry_run": bool(args.dry_run),
        "foreground": bool(args.foreground),
        "profiling": {"flags": flags},
        "serve_argv": serve_argv,
        "speed_sizes": speed_sizes(ctx),
        "far_end": end,
        "gen": gen,
        "window": window,
        "phases": phases,
        "hooks": {
            "pre": os.environ.get("A770B_QUALIFY_PRE") or None,
            "post": os.environ.get("A770B_QUALIFY_POST") or None,
        },
        "row_fields": _row_fields(row),
        "job_id": job_id,
        "useful_ctx_rule": profiling_contract.RULE_NAME,
        "reference_ids": sorted(ref_ids),
        "artifact": row.get("model") if row and isinstance(row.get("model"), str) else os.path.basename(args.model),
    }


def _row_fields(row: dict | None) -> dict:
    if not row:
        return {}
    keep = (
        "model",
        "quant",
        "tool_parser",
        "reasoning",
        "reasoning_parser",
        "vllm_image",
        "ctx",
        "kv",
        "kv_v",
        "extra",
        "card",
        "backend",
        "mode",
        "engine",
        "family",
        "output_tokens",
    )
    return {key: row.get(key) for key in keep if key in row}


def format_plan(plan: dict) -> str:
    """One readable plan. Measurement switches appear only in the serve argv."""
    lines = [
        "qualify dry-run" if plan.get("dry_run") else "qualify plan",
        f"engine: {plan['engine']}",
        f"model: {plan['model']}",
        "phases: " + ", ".join(plan["phases"]),
        "serve-argv: " + " ".join(shlex.quote(str(part)) for part in plan["serve_argv"]),
        f"speed sizes: {plan['speed_sizes']} (each size is at most ctx-1024)",
        f"far end: {plan['far_end']} tokens (generation budget {plan['gen']})",
        f"window: {plan['window']['message']}",
        f"hooks: pre={plan['hooks']['pre'] or 'unset'} post={plan['hooks']['post'] or 'unset'}",
        f"job id would be: {plan['job_id']}",
        "starts nothing" if plan.get("dry_run") else "ready",
    ]
    if plan.get("absorb_max_s") is not None:
        lines.append(f"absorb max: {plan['absorb_max_s']} seconds")
    else:
        lines.append("absorb max: no limit (decode is not read)")
    if plan.get("fit_only"):
        lines.append("fit-only: the suite is skipped")
    return "\n".join(lines) + "\n"


def cmd_plan(argv: list[str]) -> int:
    emit = None
    if argv and argv[0] == "--emit":
        if len(argv) < 2:
            raise QualifyError("plan --emit needs a path")
        emit = argv[1]
        argv = argv[2:]
    plan = build_plan(argv)
    if plan["dry_run"]:
        sys.stdout.write(format_plan(plan))
        return 0
    if plan["window"]["action"] in ("refuse", "unknown") and not plan["fit_only"] and not plan["no_suite"]:
        raise QualifyError(plan["window"]["message"])
    payload = json.dumps(plan, indent=2) + "\n"
    if emit:
        if os.path.islink(emit):
            raise QualifyError("refusing to write a symlink")
        _write_private(emit, payload)
    else:
        sys.stdout.write(payload)
    return 0


def _write_private(path: str, text: str) -> None:
    """Create or replace a 0600 file. A symlink is left untouched."""
    if os.path.islink(path):
        raise QualifyError(f"refusing to write a symlink: {path}")
    parent = os.path.dirname(path) or "."
    if os.path.islink(parent):
        raise QualifyError(f"refusing to write through a symlink: {parent}")
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=parent)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.islink(path):
            raise QualifyError(f"refusing to write a symlink: {path}")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        tmp = ""
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _read_status(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("trips", [])
    return data


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def status_merge(job: str, patch: dict) -> dict:
    """Lock, merge, and replace status.json. A terminal marker blocks writers."""
    if os.path.islink(job):
        raise QualifyError("refusing a symlink job directory")
    os.makedirs(job, mode=0o700, exist_ok=True)
    lock_path = os.path.join(job, ".status.lock")
    if os.path.islink(lock_path):
        raise QualifyError("refusing a symlink status lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        status_path = os.path.join(job, "status.json")
        if os.path.islink(status_path):
            raise QualifyError("refusing a symlink status file")
        current = _read_status(status_path)
        marked = any(os.path.exists(os.path.join(job, name)) for name in TERMINAL_MARKERS)
        if marked and not patch.get("_allow_terminal"):
            return current
        trip = patch.get("trip")
        event = patch.get("event")
        data = {
            key: value
            for key, value in patch.items()
            if key not in ("trip", "_allow_terminal", "event")
        }
        current.update(data)
        current["updated"] = _now()
        if "heartbeat" not in data:
            current.setdefault("heartbeat", current["updated"])
        if event:
            _append_event(job, f"{current['updated']} {event}")
        if trip:
            current.setdefault("trips", [])
            current["trips"].append(trip)
            _append_event(job, f"{trip.get('time', current['updated'])} {trip.get('rule', '')} {trip.get('line', '')}")
        current.setdefault("trips", [])
        _write_private(status_path, json.dumps(current, indent=2) + "\n")
        return current
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _append_event(job: str, line: str) -> None:
    path = os.path.join(job, "events.log")
    if os.path.islink(path):
        raise QualifyError("refusing a symlink events log")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (line.rstrip("\n") + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def proc_starttime(pid) -> str | None:
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError):
        return None
    # comm can contain ")". The state is the first field after the last ")".
    tail = text.rsplit(")", 1)[-1].split()
    if len(tail) < 20:
        return None
    if tail[0] in ("Z", "X"):
        return None
    return tail[19]


def pid_matches(pid, starttime) -> bool:
    if pid in (None, "", 0) or starttime in (None, ""):
        return False
    return proc_starttime(pid) == str(starttime)


def cmdline(pid) -> str:
    return " ".join(cmdline_argv(pid))


def cmdline_argv(pid) -> list[str]:
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return []
    parts = raw.split(b"\0")
    return [part.decode("utf-8", "replace") for part in parts if part]


def runner_identity(pid, starttime, project: str, job: str | None = None) -> bool:
    """True when pid is this project's qualify.sh run and the start time matches."""
    if not pid_matches(pid, starttime):
        return False
    argv = cmdline_argv(pid)
    script = os.path.join(project, "harness", "qualify.sh")
    if len(argv) < 4:
        return False
    if os.path.basename(argv[0]) != "bash":
        return False
    if os.path.realpath(argv[1]) != os.path.realpath(script):
        return False
    if argv[2] != "run":
        return False
    if job is not None:
        if len(argv) < 5 or argv[3] != "--job":
            return False
        if os.path.realpath(argv[4]) != os.path.realpath(job):
            return False
    return True


def _terminal(job: str) -> str | None:
    for name in TERMINAL_MARKERS:
        if os.path.exists(os.path.join(job, name)):
            return name
    return None


def _age_seconds(stamp) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return time.time() - calendar.timegm(parsed)


def _systemd_unit_active(job: str) -> bool:
    """True only when this job's unit is active. A missing systemctl is not active."""
    path = os.path.join(job, "job.json")
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    try:
        document = _load_json(path)
    except QualifyError:
        return False
    if document.get("launch") != "systemd":
        return False
    unit = "qualify-" + os.path.basename(job.rstrip("/"))
    try:
        run = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0 and run.stdout.strip() == "active"


def status_show(job: str) -> dict:
    """Read status. A dead running pid is lost. A queued pid-less job has a handshake grace."""
    path = os.path.join(job, "status.json")
    current = _read_status(path)
    if _terminal(job):
        return current
    state = current.get("state")
    if state not in ("queued", "running"):
        return current
    if pid_matches(current.get("pid"), current.get("pid_starttime")):
        return current
    # init_job writes queued with no pid. status during the 10s handshake must not call it lost.
    if state == "queued" and not current.get("pid"):
        age = _age_seconds(current.get("started"))
        if age is None or age <= 15:
            return current
        if _systemd_unit_active(job):
            return current
    if current.get("state") != "lost":
        current = status_merge(job, {"state": "lost", "reason": "runner pid is gone"})
    return current


def job_dir(data_root: str, job_id: str) -> str:
    if not JOB_ID_RE.match(job_id or "") or ".." in job_id:
        raise QualifyError(f"refusing job id {job_id!r}")
    return os.path.join(data_root, "qualify", job_id)


def scrub_argv(argv: list) -> list:
    """Drop key switches. Underscores fold the same way vLLM and SGLang do."""
    cleaned = []
    skip = False
    for token in argv:
        text = str(token)
        if skip:
            skip = False
            continue
        name = _option_name(text)
        if name == "--api-key":
            if "=" not in text:
                skip = True
            continue
        cleaned.append(token)
    return cleaned


def git_revision(project: str) -> dict:
    try:
        rev = subprocess.run(
            ["git", "-C", project, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        dirty = subprocess.run(
            ["git", "-C", project, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"revision": "unknown", "dirty": "unknown"}
    revision = rev.stdout.strip() if rev.returncode == 0 else "unknown"
    flag = "unknown" if dirty.returncode != 0 else bool(dirty.stdout.strip())
    return {"revision": revision, "dirty": flag}


def opencode_version(project: str) -> str:
    """Version from the project helper after its version-1 check. Else unknown."""
    forced = os.environ.get("A770B_QUALIFY_OPENCODE_VERSION")
    if forced:
        return forced
    script = """
set -uo pipefail
source "$1/harness/env.sh" || exit 1
if ! a770b_opencode_v1_ok; then
  share=${A770B_OPENCODE_HOME:-}
  if [ -n "$share" ] && [ -x "$share/opencode" ]; then
    A770B_OPENCODE_BIN=$share
    export A770B_OPENCODE_BIN
  fi
fi
a770b_opencode_v1_ok || exit 1
bin=$(a770b_opencode_bin) || exit 1
"$bin/opencode" --version | head -n 1
"""
    try:
        run = subprocess.run(
            ["bash", "-c", script, "oc", project],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if run.returncode != 0:
        return "unknown"
    return run.stdout.strip() or "unknown"


def init_job(job: str, plan: dict, launch: str) -> None:
    """Write job.json and the first status. The directory already exists."""
    if os.path.islink(job):
        raise QualifyError("refusing a symlink job directory")
    for name in ("evidence", "scratch", "phases"):
        path = os.path.join(job, name)
        if os.path.islink(path):
            raise QualifyError(f"refusing a symlink: {path}")
        os.makedirs(path, mode=0o700, exist_ok=True)
    started = _now()
    document = {
        "inputs": {
            "engine": plan["engine"],
            "model": plan["model"],
            "ctx": plan["ctx"],
            "backend": plan["backend"],
            "kv": plan["kv"],
            "kv_v": plan["kv_v"],
            "extra": plan["extra"],
            "reasoning": plan["reasoning"],
            "reasoning_budget": plan["reasoning_budget"],
            "stages": plan["stages"],
            "refs_repeat": plan["refs_repeat"],
            "absorb_max_s": plan["absorb_max_s"],
            "fit_only": plan["fit_only"],
            "skip_window": plan["skip_window"],
            "no_suite": plan["no_suite"],
            "tokenizer": plan["tokenizer"],
            "row_fields": plan.get("row_fields") or {},
        },
        "argv": scrub_argv(plan["serve_argv"]),
        "profiling": {"flags": list(plan["profiling"]["flags"])},
        "git": git_revision(PROJECT),
        "image": "unknown",
        "opencode": opencode_version(PROJECT),
        "started": started,
        "engine": plan["engine"],
        "window": plan["window"],
        "launch": launch,
        "phases": plan["phases"],
        "useful_ctx_rule": plan["useful_ctx_rule"],
        "far_end": plan["far_end"],
        "speed_sizes": plan["speed_sizes"],
    }
    label = os.environ.get("A770B_HOST_LABEL")
    if label:
        document["host_label"] = label
    _write_private(os.path.join(job, "job.json"), json.dumps(document, indent=2) + "\n")
    _write_private(os.path.join(job, "plan.json"), json.dumps(plan, indent=2) + "\n")
    status_merge(
        job,
        {
            "state": "queued",
            "phase": "preflight",
            "started": started,
            "heartbeat": started,
            "pid": None,
            "pid_starttime": None,
            "reason": None,
            "trips": [],
        },
    )


def free_gib(path: str) -> float:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize / float(1024 ** 3)


def preflight(job: str) -> dict:
    """Free-space floor and, for a GGUF, the weights file. The gate is the shell's."""
    plan = _load_json(os.path.join(job, "plan.json"))
    data = os.environ.get("A770B_DATA") or os.path.dirname(os.path.dirname(job))
    floor = float(os.environ.get("A770B_QUALIFY_MIN_FREE_GIB") or "2")
    free = free_gib(data)
    if free < floor:
        raise QualifyError(
            f"free space {free:.2f} GiB on the data directory is below {floor} GiB"
        )
    weights = "not-a-gguf"
    if plan.get("gguf"):
        models = os.environ.get("A770B_MODELS") or ""
        weights = os.path.join(models, os.path.basename(plan["model"]))
        if not os.path.isfile(weights) or os.path.islink(weights):
            raise QualifyError(f"weights are not a regular file under the models directory")
    report = {
        "free_gib": round(free, 3),
        "floor_gib": floor,
        "weights": os.path.basename(weights) if weights != "not-a-gguf" else weights,
        "revision": git_revision(PROJECT),
        "window": plan.get("window"),
    }
    _write_private(
        os.path.join(job, "evidence", "preflight.json"),
        json.dumps(report, indent=2) + "\n",
    )
    return report


def confirm_server(job: str, log_text: str, host: str, port: int) -> dict:
    """Call confirm_started and store the receipt. Pending is not invented."""
    plan = _load_json(os.path.join(job, "plan.json"))
    engine = plan["engine"]
    confirmed = profiling_contract.confirm_started(
        engine,
        host,
        int(port),
        log_text,
        os.environ.get("A770B_API_KEY_FILE"),
    )
    body = profiling_contract.receipt(engine, confirmed)
    path = os.path.join(job, "evidence", "profiling-start.json")
    profiling_contract.write_receipt(path, body)
    os.chmod(path, 0o600)
    return body


def _figure(value, unit: str, depth, source: str, reason: str | None = None) -> dict:
    if value is None or value == "unknown":
        return {
            "value": "unknown",
            "unit": unit,
            "depth_tokens": depth,
            "source": source,
            "reason": reason or "not measured",
        }
    return {
        "value": value,
        "unit": unit,
        "depth_tokens": depth,
        "source": source,
    }


def _check(name, result, evidence, rule, reason=None) -> dict:
    item = {"name": name, "result": result, "evidence": evidence, "rule": rule}
    if result not in ("pass", "fail", "unknown"):
        item["result"] = "unknown"
        item["reason"] = "bad check value"
        return item
    if result in ("pass", "fail") and not evidence:
        item["result"] = "unknown"
        item["reason"] = "no evidence path"
    elif reason:
        item["reason"] = reason
    elif result == "unknown":
        item["reason"] = "not established"
    return item


def _switches(engine: str, receipt: dict | None) -> dict:
    if not isinstance(receipt, dict) or "confirmed" not in receipt:
        return _check(
            "switches-confirmed",
            "unknown",
            None,
            "every contract confirm key is present and true",
            "absent or pending receipt",
        )
    confirmed = receipt.get("confirmed")
    if not isinstance(confirmed, dict):
        confirmed = {}
    if any(value == "pending" for value in confirmed.values()):
        return _check(
            "switches-confirmed",
            "unknown",
            "evidence/profiling-start.json",
            "every contract confirm key is present and true",
            "pending receipt",
        )
    expected = profiling_contract.TABLE[engine]["confirm"]
    ok = all(confirmed.get(key) is True for key in expected)
    return _check(
        "switches-confirmed",
        "pass" if ok else "fail",
        "evidence/profiling-start.json",
        "every contract confirm key is present and true",
    )


def _apply_nulls(engine: str, receipt, requests: dict, delivered) -> dict:
    """Return the four dotted fields after the verbosity rule. Others stay put."""
    row = {
        "profiling": receipt if isinstance(receipt, dict) else {"engine": engine},
        "requests": dict(requests),
        "speed": {"delivered": delivered},
    }
    if not isinstance(row["profiling"], dict):
        row["profiling"] = {"engine": engine}
    row["profiling"].setdefault("engine", engine)
    profiling_contract.null_unconfirmed_verbosity(row)
    req = row.get("requests") or {}
    speed = row.get("speed") or {}
    return {
        "requests.prefill_tps": req.get("prefill_tps", "unknown"),
        "requests.decode_tps": req.get("decode_tps", "unknown"),
        "requests.length_stops": req.get("length_stops", "unknown"),
        "speed.delivered": speed.get("delivered", "unknown"),
    }


def _compaction(stages) -> dict:
    """Fail when a stage averages more than one compaction per four requests."""
    if not isinstance(stages, list) or not stages:
        return _check(
            "no-compaction-storm",
            "unknown",
            None,
            f"at most one compaction per {COMPACTION_PER_REQUESTS} requests",
            "the suite did not record compaction lines",
        )
    worst = 0.0
    have = False
    any_missing = False
    storm = False
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        lines = stage.get("compaction_lines")
        requests = stage.get("requests")
        # bool is an int. A flag is not a count.
        if isinstance(lines, bool) or isinstance(requests, bool):
            any_missing = True
            continue
        if lines is None or requests in (None, 0, "unknown"):
            any_missing = True
            continue
        try:
            lines_n = float(lines)
            requests_n = float(requests)
        except (TypeError, ValueError):
            any_missing = True
            continue
        if requests_n <= 0:
            any_missing = True
            continue
        have = True
        ratio = lines_n / requests_n
        worst = max(worst, ratio)
        if lines_n * COMPACTION_PER_REQUESTS > requests_n:
            storm = True
    if storm:
        return _check(
            "no-compaction-storm",
            "fail",
            "evidence/suite.json",
            f"at most one compaction per {COMPACTION_PER_REQUESTS} requests",
        )
    if any_missing or not have:
        return _check(
            "no-compaction-storm",
            "unknown",
            "evidence/suite.json",
            f"at most one compaction per {COMPACTION_PER_REQUESTS} requests",
            "no stage recorded compaction",
        )
    ok = worst * COMPACTION_PER_REQUESTS <= 1.0
    return _check(
        "no-compaction-storm",
        "pass" if ok else "fail",
        "evidence/suite.json",
        f"at most one compaction per {COMPACTION_PER_REQUESTS} requests",
    )


def summarise(job: str) -> dict:
    """Build result.json and report.md from the job's evidence. No verdict is decided."""
    plan = _load_json(os.path.join(job, "plan.json"))
    job_doc = _load_json(os.path.join(job, "job.json"))
    collected_path = os.path.join(job, "evidence", "collected.json")
    collected = _load_json(collected_path) if os.path.isfile(collected_path) else {}
    receipt = None
    receipt_path = os.path.join(job, "evidence", "profiling-start.json")
    if os.path.isfile(receipt_path):
        try:
            receipt = _load_json(receipt_path)
        except QualifyError:
            receipt = None
    engine = plan["engine"]
    ctx = plan["ctx"]
    fit_only = bool(plan.get("fit_only"))
    window = plan.get("window") or {}
    depth = window.get("served_ctx", ctx)

    def unknown(unit, reason, source):
        return _figure("unknown", unit, depth, source, reason)

    yields = profiling_contract.TABLE[engine]["yields"]
    cannot_prefill = "prefill_tps" not in yields and engine != "llama.cpp"
    if engine == "strata":
        cannot_prefill = True
    speed_rows = collected.get("speed") if isinstance(collected.get("speed"), list) else []
    rendered_speed = []
    for row in speed_rows:
        if not isinstance(row, dict):
            continue
        at = row.get("depth_tokens")
        rendered_speed.append(
            {
                "depth_tokens": at,
                "engine_prefill": _figure(
                    row.get("engine_prefill"),
                    "tok/s",
                    at,
                    row.get("engine_source") or "engine",
                    row.get("engine_reason"),
                ),
                "engine_decode": _figure(
                    row.get("engine_decode"),
                    "tok/s",
                    at,
                    row.get("engine_source") or "engine",
                    row.get("engine_reason"),
                ),
                "client_prefill": _figure(
                    row.get("client_prefill"),
                    "tok/s",
                    at,
                    "bench client",
                ),
                "client_decode": _figure(
                    row.get("client_decode"),
                    "tok/s",
                    at,
                    "bench client",
                ),
            }
        )
    if engine == "strata":
        for row in rendered_speed:
            for key in ("engine_prefill", "engine_decode"):
                row[key] = _figure("unknown", "tok/s", row["depth_tokens"], "usage object", "usage object only")

    requests = collected.get("requests") if isinstance(collected.get("requests"), dict) else {}
    for key in ("prefill_tps", "decode_tps", "length_stops", "max_prompt_tokens", "max_completion_tokens"):
        requests.setdefault(key, None if engine == "llama.cpp" else "unknown")
    if engine == "strata":
        requests["prefill_tps"] = None
        requests["decode_tps"] = None
    delivered = collected.get("delivered", "unknown")
    dotted = _apply_nulls(engine, receipt, requests, delivered)

    suite = collected.get("suite") if isinstance(collected.get("suite"), dict) else {}
    if isinstance(collected.get("requests"), dict) and collected.get("requests"):
        suite = dict(suite)
        suite["requests"] = collected["requests"]
    stages = suite.get("stages") if isinstance(suite.get("stages"), list) else []
    bar = counted_pass_bar()
    counted = [s for s in stages if isinstance(s, dict) and s.get("counts_toward_pass", True) is not False]
    if counted:
        passed = sum(1 for s in counted if s.get("working") is True)
        ratio = passed / len(counted)
        counted_result = "pass" if ratio >= bar else "fail"
        counted_reason = None
    else:
        passed = None
        ratio = None
        counted_result = "unknown"
        counted_reason = "no counted stages" if not fit_only else "suite skipped"
    refs = suite.get("references") if isinstance(suite.get("references"), dict) else {}
    attempts = refs.get("attempts")
    ref_passed = refs.get("passed")
    if not plan.get("refs_apply"):
        ref_check = _check("references-pass", "unknown", None, "every reference attempt passed", "no reference stage selected")
    elif attempts in (None, "unknown"):
        ref_check = _check("references-pass", "unknown", "evidence/suite.json" if suite else None, "every reference attempt passed", "reference attempts were not recorded")
    elif attempts == 0:
        ref_check = _check("references-pass", "unknown", "evidence/suite.json", "every reference attempt passed", "no reference attempt")
    else:
        ok = ref_passed == attempts
        ref_check = _check(
            "references-pass",
            "pass" if ok else "fail",
            "evidence/suite.json",
            "every reference attempt passed",
            None if ok else f"{ref_passed} of {attempts} passed",
        )

    hooks = collected.get("hooks") if isinstance(collected.get("hooks"), dict) else {}
    if not hooks.get("pre_started"):
        post = _check("post-hook-ok", "unknown", None, "post-hook exit", "not-run")
    else:
        code = hooks.get("post_code")
        if code == 0:
            post = _check("post-hook-ok", "pass", "evidence/hooks.json", "post-hook exit")
        elif isinstance(code, int):
            post = _check("post-hook-ok", "fail", "evidence/hooks.json", "post-hook exit", hooks.get("post_line"))
        else:
            post = _check("post-hook-ok", "unknown", "evidence/hooks.json", "post-hook exit", "post-hook result missing")

    vram = collected.get("vram_after_load")
    cap = collected.get("vram_cap")
    if isinstance(vram, (int, float)) and isinstance(cap, (int, float)) and not isinstance(vram, bool):
        fit = _check(
            "fit-under-cap",
            "pass" if vram <= cap else "fail",
            "evidence/serve.json",
            "VRAM after load is at or under the cap recorded in the serve evidence",
        )
    else:
        fit = _check(
            "fit-under-cap",
            "unknown",
            "evidence/serve.json" if os.path.isfile(os.path.join(job, "evidence", "serve.json")) else None,
            "VRAM after load is at or under the cap recorded in the serve evidence",
            "serve evidence has no VRAM reading or no cap",
        )
    load_ok = collected.get("load_ok")
    if load_ok is True:
        load = _check("load-ok", "pass", "evidence/serve.json", "the server became healthy")
    elif load_ok is False:
        load = _check("load-ok", "fail", "evidence/serve.json", "the server became healthy")
    else:
        load = _check("load-ok", "unknown", None, "the server became healthy", "no serve health record")
    smoke = collected.get("smoke") if isinstance(collected.get("smoke"), dict) else {}
    if smoke.get("tool") is True:
        smoke_check = _check("smoke-tool-call", "pass", "evidence/smoke.json", "one tool call returned")
    elif smoke.get("tool") is False:
        smoke_check = _check("smoke-tool-call", "fail", "evidence/smoke.json", "one tool call returned")
    else:
        smoke_check = _check("smoke-tool-call", "unknown", None, "one tool call returned", "smoke did not record a tool call")
    if plan.get("skip_window"):
        window_check = _check("window-no-resets", "unknown", None, "the sweep recorded no reset", "window sweep skipped")
    elif collected.get("window_resets") == 0:
        window_check = _check("window-no-resets", "pass", "evidence/window.json", "the sweep recorded no reset")
    elif isinstance(collected.get("window_resets"), int):
        window_check = _check("window-no-resets", "fail", "evidence/window.json", "the sweep recorded no reset")
    else:
        window_check = _check("window-no-resets", "unknown", None, "the sweep recorded no reset", "no sweep record")
    if collected.get("depth_pass") is True:
        depth_check = _check("depth-probe", "pass", "evidence/depth.json", "the depth probe scored a pass")
    elif collected.get("depth_pass") is False:
        depth_check = _check("depth-probe", "fail", "evidence/depth.json", "the depth probe scored a pass")
    else:
        depth_check = _check("depth-probe", "unknown", None, "the depth probe scored a pass", "no depth score")
    if fit_only or plan.get("no_suite"):
        suite_check = _check("suite-completed", "unknown", None, "the scheduled suite finished", "suite skipped")
    elif suite.get("completed") is True:
        suite_check = _check("suite-completed", "pass", "evidence/suite.json", "the scheduled suite finished")
    elif suite.get("completed") is False:
        suite_check = _check("suite-completed", "fail", "evidence/suite.json", "the scheduled suite finished")
    else:
        suite_check = _check("suite-completed", "unknown", None, "the scheduled suite finished", "no suite record")
    trips = _read_status(os.path.join(job, "status.json")).get("trips") or []
    fence = [t for t in trips if isinstance(t, dict)]
    if fence:
        fence_check = _check("no-fence-trips", "fail", "events.log", "the watchdog recorded no trip")
    else:
        fence_check = _check("no-fence-trips", "pass", "events.log", "the watchdog recorded no trip")
    hold = window.get("action")
    if hold == "ok":
        hold_check = _check(
            "usable-window-holds-kit",
            "pass",
            "job.json",
            "usable window holds the kit prompt plus headroom",
        )
    elif hold == "refuse":
        hold_check = _check(
            "usable-window-holds-kit",
            "fail",
            "job.json",
            "usable window holds the kit prompt plus headroom",
        )
    else:
        hold_check = _check(
            "usable-window-holds-kit",
            "unknown",
            "job.json",
            "usable window holds the kit prompt plus headroom",
            window.get("message"),
        )
    card = collected.get("card_free") if isinstance(collected.get("card_free"), dict) else {}
    containers = card.get("containers")
    idle = card.get("idle_gib")
    now_vram = card.get("vram_gib")
    if containers == 0 and now_vram is None:
        card_check = _check(
            "card-free-after",
            "pass",
            "evidence/card.json",
            "no project container is running; VRAM is at or below idle plus tolerance when a reading exists",
            "no nvtop reading",
        )
    elif containers == 0 and isinstance(now_vram, (int, float)) and isinstance(idle, (int, float)):
        ok = now_vram <= idle + IDLE_VRAM_TOLERANCE_GIB
        card_check = _check(
            "card-free-after",
            "pass" if ok else "fail",
            "evidence/card.json",
            f"VRAM at or below idle plus {IDLE_VRAM_TOLERANCE_GIB} GiB and no project container",
        )
    elif containers not in (0, None):
        card_check = _check(
            "card-free-after",
            "fail",
            "evidence/card.json",
            "no project container is running",
        )
    else:
        card_check = _check(
            "card-free-after",
            "unknown",
            None,
            "no project container is running",
            "container state was not recorded",
        )

    useful, absorb_pair, limit = profiling_contract.absorb_window(
        ctx,
        plan.get("far_end"),
        collected.get("ttft_8k"),
        collected.get("ttft_far"),
        plan.get("absorb_max_s"),
        collected.get("depth_pass") if collected.get("depth_pass") is not None else None,
        bool(collected.get("unanswered")),
    )
    statistics = _statistics_from(collected, plan, dotted, useful)
    checks = [
        fit,
        load,
        smoke_check,
        window_check,
        depth_check,
        suite_check,
        _check(
            "counted-stages-pass",
            counted_result,
            "evidence/suite.json" if counted else None,
            f"counted stage pass ratio >= {bar}",
            counted_reason,
        ),
        ref_check,
        fence_check,
        _switches(engine, receipt),
        hold_check,
        _compaction(stages),
        post,
        card_check,
    ]
    missing = [
        field
        for field in required_statistic_fields()
        if statistics.get(field) in (None, "unknown") or field not in statistics
    ]
    result = {
        "verdict": VERDICT,
        "fit_result": fit_only,
        "engine": engine,
        "model": plan["model"],
        "ctx": ctx,
        "useful_ctx": useful if useful is not None else "unknown",
        "useful_ctx_rule": profiling_contract.RULE_NAME,
        "absorb_max_s": plan.get("absorb_max_s"),
        "absorb": absorb_pair,
        "decode_not_used_for_window": True,
        "window": window,
        "peak_prompt_tokens": collected.get("peak_prompt_tokens", "unknown"),
        "headroom_left": window.get("headroom_left"),
        "compaction": [
            {
                "stage": s.get("id"),
                "compaction_lines": s.get("compaction_lines", "unknown"),
                "requests": s.get("requests", "unknown"),
            }
            for s in stages
            if isinstance(s, dict)
        ],
        "speed": rendered_speed,
        "vram_after_load": _figure(vram, "GiB", depth, "serve evidence") if isinstance(vram, (int, float)) else unknown("GiB", "no reading", "serve evidence"),
        "vram_peak": _figure(collected.get("vram_peak"), "GiB", depth, "nvtop") if isinstance(collected.get("vram_peak"), (int, float)) else unknown("GiB", "no reading", "nvtop"),
        "vram_cap": _figure(cap, "GiB", depth, "serve evidence") if isinstance(cap, (int, float)) else unknown("GiB", "cap not in serve evidence", "serve evidence"),
        "load_time_s": _figure(collected.get("load_s"), "s", depth, "serve") if isinstance(collected.get("load_s"), (int, float)) else unknown("s", "no load time", "serve"),
        "depth_score": collected.get("depth_score", "unknown"),
        "depth_tokens": collected.get("depth_tokens", plan.get("far_end")),
        "suite": suite,
        "counted": {"passed": passed, "runs": len(counted) if counted else 0, "bar": bar},
        "trips": trips,
        "profiling": receipt,
        "hooks": hooks,
        "revision": job_doc.get("git"),
        "checks": checks,
        "statistics": statistics,
        "missing_statistics": missing,
        "image": collected.get("image") or job_doc.get("image") or "unknown",
        "opencode": job_doc.get("opencode", "unknown"),
        "artifact": plan.get("artifact") or os.path.basename(str(plan.get("model") or "")),
        "ram_gb_extra": collected.get("ram_gb_extra", "unknown"),
        "timeout_s": _timeout_seconds(stages),
        "profile_speed": collected.get("profile_speed") if isinstance(collected.get("profile_speed"), dict) else {},
        "depth_not_measured": collected.get("depth_not_measured"),
        "card_free": collected.get("card_free") if isinstance(collected.get("card_free"), dict) else {},
    }
    for key, value in dotted.items():
        result[key] = value
    _write_private(os.path.join(job, "result.json"), json.dumps(result, indent=2) + "\n")
    _write_private(os.path.join(job, "report.md"), render_report(job, result))
    return result


def _timeout_seconds(stages) -> int | str:
    """The longest counted stage wall, when the suite recorded one. Else unknown."""
    walls = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        wall = stage.get("wall_s")
        if isinstance(wall, bool) or not isinstance(wall, int) or wall < 1:
            continue
        walls.append(wall)
    if not walls:
        return "unknown"
    return max(walls)


def _statistics_from(collected, plan, dotted, useful) -> dict:
    """One entry per required field. Unknown stays unknown, never zero."""
    stats = {row["field"]: "unknown" for row in REQUIRED_STATISTICS}
    given = collected.get("statistics")
    if isinstance(given, dict):
        stats.update(given)
    if useful is not None:
        stats["useful_ctx"] = useful
    stats["useful_ctx_rule"] = profiling_contract.RULE_NAME
    if plan.get("absorb_max_s") is not None:
        stats["absorb_max_s"] = plan["absorb_max_s"]
    if collected.get("profiling_ok") is True:
        stats["profiling"] = "recorded"
    return stats


def render_report(job: str, result: dict) -> str:
    """The same facts as result.json, each rate or memory figure naming its depth."""
    lines = []
    if result.get("fit_result"):
        lines.append("FIT RESULT. This is a fit measurement, never a quality pass.")
        lines.append("")
    lines.append(f"verdict: {VERDICT}")
    lines.append(
        f"engine {result.get('engine')} model {result.get('model')} "
        f"at {result.get('ctx')} served tokens"
    )
    window = result.get("window") or {}
    lines.append(
        f"usable window {window.get('usable')} tokens at served ctx {window.get('served_ctx')} "
        f"(output limit {window.get('output_limit')} tokens)"
    )
    lines.append(f"useful_ctx: {result.get('useful_ctx')} at served ctx {result.get('ctx')} ({result.get('useful_ctx_rule')})")
    for row in result.get("speed") or []:
        for key in ("engine_prefill", "engine_decode", "client_prefill", "client_decode"):
            fig = row.get(key) or {}
            value = fig.get("value")
            depth = fig.get("depth_tokens")
            if value == "unknown" or value is None:
                lines.append(f"{key}: unknown at {depth} tokens ({fig.get('reason', 'not measured')})")
            else:
                lines.append(f"{key}: {value} {fig.get('unit', 'tok/s')} at {depth} tokens")
    for label in ("vram_after_load", "vram_peak", "vram_cap", "load_time_s"):
        fig = result.get(label) or {}
        value = fig.get("value")
        depth = fig.get("depth_tokens")
        if value == "unknown" or value is None:
            lines.append(f"{label}: unknown at {depth} tokens ({fig.get('reason', 'not measured')})")
        else:
            lines.append(f"{label}: {value} {fig.get('unit', '')} at {depth} tokens".replace("  ", " "))
    for key in NULL_DOTTED:
        value = result.get(key)
        lines.append(f"{key}: {value if value is not None else 'null'} at served ctx {result.get('ctx')}")
    lines.append("checks:")
    for item in result.get("checks") or []:
        reason = f" ({item['reason']})" if item.get("reason") else ""
        lines.append(f"- {item['name']}: {item['result']}{reason}")
    missing = result.get("missing_statistics") or []
    if missing:
        lines.append("missing statistics: " + ", ".join(missing))
    job_id = os.path.basename(job.rstrip("/"))
    plan_card = None
    plan_path = os.path.join(job, "plan.json")
    if os.path.isfile(plan_path) and not os.path.islink(plan_path):
        try:
            plan_card = (_load_json(plan_path).get("row_fields") or {}).get("card")
        except QualifyError:
            plan_card = None
    card = plan_card or os.environ.get("A770B_CARD") or "<card>"
    mode = os.environ.get("A770B_CARD_MODE") or "inference"
    lines.append("")
    lines.append(f"Export with: local-build.sh qualify export {job_id}")
    lines.append(f"Registry file for a later merge: config/registry/{card}.{mode}.json")
    lines.append("Merging is a deliberate act. This runner does not add the profile.")
    lines.append("")
    return "\n".join(lines)


# Human fields the checker may see filled. The written row keeps __TODO__.
# Runner measurements are not in this map. allow_incomplete is the only
# path that may fill a missing runner field, and only on the check copy.
_HUMAN_FILL = {
    "task": "code-edit",
    "capability": {"published": 1},
    "source": "model card",
    "family": "family",
    "architecture": "arch",
    "quant": "Q4_K_M",
    "category": "dense",
    "weight_class": "9b",
    "params_b": {"total": 1, "active": 1},
    "capability_source": "model card",
    "sampling": {"temperature": 0.6, "source": "model card"},
    "measured_on": "operator card and build at 8192 tokens",
    "use_for": "to be written by hand",
    "task_t1": "not measured",
    "timeout_s": 60,
    "fit": {"code": "not measured", "think": "not measured", "write": "not measured"},
}
_RUNNER_FILL = {
    "ctx": 8192,
    "useful_ctx": 8192,
    "vram_gib_after_load": 1.0,
    "ram_gb_extra": 0.0,
    "kv": "q8_0",
    "flash_attention": "on",
    "reasoning": "off",
    "extra": "",
    "card": "a770",
    "backend": "vulkan",
    "mode": "inference",
    "depth_probe_100k": "not measured",
}


def _needs_fill(current) -> bool:
    if current is None:
        return True
    if isinstance(current, str) and "__TODO__" in current:
        return True
    return False


def _sentinel_row(row: dict, allow_incomplete: bool = False) -> dict:
    """A check copy. Human TODO fields are filled. Runner fields stay unless incomplete."""
    check = json.loads(json.dumps(row))
    import profiles

    required = profiles.PROFILE_KEYS - profiles.OPTIONAL_KEYS
    if check.get("engine") == "strata":
        required = set(required) - {"flash_attention"}
    for key, value in _HUMAN_FILL.items():
        if key not in check and key not in required:
            continue
        if key not in check or _needs_fill(check.get(key)):
            check[key] = value
    if allow_incomplete:
        for key, value in _RUNNER_FILL.items():
            if key not in check and key not in required:
                continue
            if key not in check or check.get(key) is None or _needs_fill(check.get(key)):
                check[key] = value
    if not isinstance(check.get("capability"), dict) and (
        "capability" not in check or _needs_fill(check.get("capability")) or allow_incomplete
    ):
        if "capability" in required or "capability" in check:
            check["capability"] = {"published": 1}
    speed = check.get("speed")
    if not isinstance(speed, dict):
        check["speed"] = {
            "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
            "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
        }
    return check


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _base_row(plan: dict, result: dict) -> dict:
    """Copy measured fields. A missing measurement is omitted, never a stand-in number."""
    import profiles

    ctx = int(plan["ctx"])
    rates = {"8k": None, "32k": None, "64k": None, "100k": None}
    fields = plan.get("row_fields") or {}
    artifact = result.get("artifact") or plan.get("artifact") or os.path.basename(str(plan.get("model") or ""))
    engine = plan["engine"]
    extra = " ".join(plan.get("extra") or [])
    # llama.cpp's serve sends -ctk/-ctv q8_0 when the plan did not set KV. That is the
    # server input, not a copied measurement. Other engines omit an unknown cache type.
    kv = plan.get("kv")
    if not kv and engine == "llama.cpp":
        kv = "q8_0"
    flash = None
    if engine != "strata":
        flash = "off" if re.search(r"(?:^|\s)-fa\s+off(?:\s|$)", extra) else "on"
    useful = result.get("useful_ctx")
    if not isinstance(useful, int) or isinstance(useful, bool):
        useful = None
    vram = result.get("vram_after_load")
    vram_value = _number(vram.get("value")) if isinstance(vram, dict) else _number(vram)
    ram = _number(result.get("ram_gb_extra"))
    timeout = result.get("timeout_s")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        timeout = "__TODO__ — longest observed stage wall"
    card = fields.get("card") or os.environ.get("A770B_CARD") or None
    try:
        instrument = profiles.kit_instrument()
    except (OSError, ValueError):
        instrument = None
    row = {
        "model": os.path.basename(str(artifact)),
        "ctx": ctx,
        "reasoning": plan.get("reasoning") or "off",
        "extra": extra,
        "useful_ctx_rule": profiling_contract.RULE_NAME,
        "speed": {"decode_tps": dict(rates), "prefill_tps": dict(rates)},
        "task": "__TODO__ — the task class this row wins",
        "measured_on": "__TODO__ — the card and build this was measured on",
        "use_for": "__TODO__ — write by hand from what this run showed",
        "capability": "__TODO__ — published evaluation, quoted verbatim",
        "source": "__TODO__",
        "family": "__TODO__",
        "architecture": "__TODO__",
        "quant": "__TODO__",
        "category": "__TODO__",
        "weight_class": "__TODO__",
        "capability_source": "__TODO__",
        "engine": engine,
    }
    if instrument:
        row["instrument"] = instrument
        row["evidence"] = instrument + " qualification result"
    if kv:
        row["kv"] = kv
    if plan.get("kv_v") and plan.get("kv_v") != kv:
        row["kv_v"] = plan["kv_v"]
    if flash is not None:
        row["flash_attention"] = flash
    if isinstance(timeout, int):
        row["timeout_s"] = timeout
    else:
        row["timeout_s"] = timeout
    if useful is not None:
        row["useful_ctx"] = useful
    if vram_value is not None and vram_value > 0:
        row["vram_gib_after_load"] = vram_value
    if ram is not None and ram >= 0:
        row["ram_gb_extra"] = ram
    if card:
        row["card"] = card
    backend = fields.get("backend") or plan.get("backend")
    if backend:
        row["backend"] = backend
    mode = fields.get("mode") or os.environ.get("A770B_CARD_MODE")
    if mode:
        row["mode"] = mode
    for key in ("quant", "tool_parser", "reasoning_parser", "vllm_image", "family"):
        if fields.get(key):
            row[key] = fields[key]
    profile_speed = result.get("profile_speed") if isinstance(result.get("profile_speed"), dict) else {}
    for side in ("decode_tps", "prefill_tps"):
        cells = profile_speed.get(side)
        if not isinstance(cells, dict):
            continue
        for label in rates:
            number = _number(cells.get(label))
            if number is not None:
                row["speed"][side][label] = number
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    requests = suite.get("requests") if isinstance(suite.get("requests"), dict) else {}
    if requests:
        length = result.get("requests.length_stops")
        prefill = result.get("requests.prefill_tps")
        decode = result.get("requests.decode_tps")
        row["requests"] = {
            "max_prompt_tokens": requests.get("max_prompt_tokens") if isinstance(requests.get("max_prompt_tokens"), int) else None,
            "max_completion_tokens": requests.get("max_completion_tokens") if isinstance(requests.get("max_completion_tokens"), int) else None,
            "length_stops": length if isinstance(length, int) and not isinstance(length, bool) else None,
            "prefill_tps": prefill if _number(prefill) is not None else None,
            "decode_tps": decode if _number(decode) is not None else None,
            "source": requests.get("source") if isinstance(requests.get("source"), str) and "/" not in requests.get("source", "") else "suite",
        }
    delivered = result.get("speed.delivered")
    if isinstance(delivered, dict):
        row["speed"]["delivered"] = delivered
    elif delivered is None and requests:
        row["speed"]["delivered"] = None
    if isinstance(result.get("profiling"), dict):
        row["profiling"] = result["profiling"]
    far = plan.get("far_end")
    score = result.get("depth_score")
    if isinstance(far, int) and far >= 90000 and isinstance(score, int) and not isinstance(score, bool):
        row["depth_probe_100k"] = f"{score}/3"
    elif result.get("depth_not_measured"):
        row["depth_probe_100k"] = "not measured"
    else:
        row["depth_probe_100k"] = "not measured"
    if plan.get("absorb_max_s") is not None:
        row["absorb_max_s"] = plan["absorb_max_s"]
    absorb = result.get("absorb")
    if isinstance(absorb, dict) and (absorb.get("8k") is not None or absorb.get("far_end") is not None):
        row["speed"]["absorb_s"] = {"8k": absorb.get("8k"), "far_end": absorb.get("far_end")}
    profiling_contract.null_unconfirmed_verbosity(row)
    return row


def _collision(errors: list[str]) -> bool:
    if not errors:
        return False
    for line in errors:
        if "duplicates" in line or "one instrument" in line:
            continue
        return False
    return True


def export_job(job: str, out: str | None, allow_incomplete: bool) -> tuple[str, str]:
    """Write the row and the todo file. The project registry is not touched."""
    plan = _load_json(os.path.join(job, "plan.json"))
    result_path = os.path.join(job, "result.json")
    if not os.path.isfile(result_path):
        raise QualifyError("no result.json", 4)
    result = _load_json(result_path)
    stats = result.get("statistics") if isinstance(result.get("statistics"), dict) else {}
    missing = [
        field
        for field in required_statistic_fields()
        if field not in stats or stats.get(field) in (None, "unknown")
    ]
    if missing and not allow_incomplete:
        raise QualifyError("missing required statistic: " + ", ".join(missing), 4)
    if out is None:
        out = os.path.join(job, "profile-row.json")
    _refuse_out(out)
    row = _base_row(plan, result)
    if allow_incomplete:
        for field in missing:
            if field in row:
                row[field] = None
            elif field.startswith("speed."):
                row.setdefault("speed", {})
                row["speed"][field.split(".", 1)[1]] = None
    names = list(LADDER_HUMAN_NAMES)
    if isinstance(row.get("timeout_s"), str):
        names.append("timeout_s")
    if not (plan.get("far_end", 0) >= 90000 and result.get("depth_score") not in (None, "unknown")):
        if "depth_probe_100k" not in names:
            names.append("depth_probe_100k")
    todo = {"fields": names}
    if allow_incomplete:
        todo["allow_incomplete"] = True
        todo["missing_statistics"] = missing
    scratch = os.path.join(job, "scratch", "registry-root")
    if os.path.isdir(scratch):
        shutil.rmtree(scratch)
    os.makedirs(os.path.join(scratch, "config", "registry"), mode=0o700, exist_ok=True)
    cards_src = os.path.join(PROJECT, "config", "cards.json")
    shutil.copy(cards_src, os.path.join(scratch, "config", "cards.json"))
    card = row.get("card")
    mode = row.get("mode")
    if not card or not mode:
        raise QualifyError("export needs a card and a mode from the plan or the environment", 4)
    reg_name = f"{card}.{mode}.json"
    reg_path = os.path.join(scratch, "config", "registry", reg_name)
    existing = {"schema": 1, "card": card, "mode": mode, "profiles": {}}
    source = plan.get("profiles_file")
    if not source:
        source = os.path.join(PROJECT, "config", "registry", reg_name)
    if source and os.path.isfile(source) and not os.path.islink(source):
        try:
            loaded = _load_json(source)
            if loaded.get("card") == card and loaded.get("mode") == mode:
                existing = loaded
        except QualifyError:
            pass
    name_src = plan.get("artifact") or plan["model"]
    name = re.sub(r"[^a-z0-9]+", "-", os.path.basename(str(name_src)).lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "qualify-row"
    name = name[:48]
    check_row = _sentinel_row(row, allow_incomplete)
    profiles = dict(existing.get("profiles") or {})
    profiles[name] = check_row
    document = {
        "schema": 1,
        "card": card,
        "mode": mode,
        "profiles": profiles,
    }
    _write_private(reg_path, json.dumps(document, indent=2) + "\n")
    warnings = []
    code, errors = _run_check(scratch)
    if code != 0 and _collision(errors):
        warnings.append("scratch copy collided with another instrument or identity; the new row was checked alone")
        document["profiles"] = {name: check_row}
        _write_private(reg_path, json.dumps(document, indent=2) + "\n")
        code, errors = _run_check(scratch)
    shutil.rmtree(scratch, ignore_errors=True)
    if code != 0:
        raise QualifyError("profiles check failed: " + " | ".join(errors), 4)
    if warnings:
        todo["warnings"] = warnings
    _write_private(out, json.dumps(row, indent=2) + "\n")
    todo_path = out + ".todo.json"
    _write_private(todo_path, json.dumps(todo, indent=2) + "\n")
    return out, todo_path


def _refuse_out(out: str) -> None:
    if os.path.islink(out):
        raise QualifyError("--out is a symlink")
    parent = os.path.dirname(os.path.abspath(out)) or "."
    if os.path.islink(parent):
        raise QualifyError("--out parent is a symlink")
    real_parent = os.path.realpath(parent)
    project = os.path.realpath(PROJECT)
    registry = os.path.realpath(os.path.join(PROJECT, "config", "registry"))
    candidate = os.path.join(real_parent, os.path.basename(out))
    if candidate == project or candidate.startswith(project + os.sep):
        raise QualifyError("--out must be outside the project")
    if candidate == registry or candidate.startswith(registry + os.sep):
        raise QualifyError("--out must be outside the registry directory")


def _run_check(root: str) -> tuple[int, list[str]]:
    run = subprocess.run(
        [sys.executable, PROFILES_PY, "check", "--root", root],
        capture_output=True,
        text=True,
    )
    text = (run.stderr or "") + (run.stdout or "")
    errors = [line.strip() for line in text.splitlines() if line.strip()]
    return run.returncode, errors


def _load_patch(argv: list[str]) -> tuple[str, dict]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--job", required=True)
    parser.add_argument("--trip-rule")
    args, rest = parser.parse_known_args(argv)
    raw = sys.stdin.read()
    if args.trip_rule:
        line = raw.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and "line" in parsed:
                line = str(parsed["line"])
        return args.job, {
            "trip": {"time": _now(), "rule": args.trip_rule, "line": line},
        }
    patch = json.loads(raw) if raw.strip() else {}
    if not isinstance(patch, dict):
        raise QualifyError("status patch must be a JSON object")
    return args.job, patch


_SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASS|AUTH)(?:_|$)",
    re.IGNORECASE,
)


def iter_forward_env():
    """PATH, HOME, and A770B_* except a secret name that is not a file path."""
    chosen = []
    for name in os.environ:
        if name not in ("PATH", "HOME") and not name.startswith("A770B_"):
            continue
        if name.endswith("_FILE"):
            chosen.append(name)
            continue
        if _SECRET_NAME.search(name):
            continue
        chosen.append(name)
    for name in sorted(chosen):
        value = os.environ.get(name, "")
        if "\n" in value or "\0" in value:
            continue
        yield name, value


def pick_job(data_root: str) -> str:
    """The active job, or the latest one when none is running."""
    root = os.path.join(data_root, "qualify")
    if not os.path.isdir(root) or os.path.islink(root):
        raise QualifyError("no qualification job")
    names = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if JOB_ID_RE.match(name) and os.path.isdir(path) and not os.path.islink(path):
            names.append(name)
    if not names:
        raise QualifyError("no qualification job")
    names.sort()
    active = []
    for name in names:
        path = os.path.join(root, name)
        if _terminal(path):
            continue
        state = _read_status(os.path.join(path, "status.json")).get("state")
        if state in ("queued", "running"):
            active.append(name)
    return active[-1] if active else names[-1]


def _evidence(job: str, name: str) -> dict:
    if "/" in name or name.startswith("."):
        raise QualifyError("bad evidence name")
    path = os.path.join(job, "evidence", name)
    if not os.path.isfile(path) or os.path.islink(path):
        return {}
    try:
        data = _load_json(path)
    except QualifyError:
        return {}
    return data if isinstance(data, dict) else {}


def write_note(job: str, name: str, body: dict) -> None:
    """Write one evidence JSON file. A symlink is refused."""
    if not name.endswith(".json") or "/" in name or name.startswith("."):
        raise QualifyError("bad evidence name")
    path = os.path.join(job, "evidence", name)
    _write_private(path, json.dumps(body, indent=2) + "\n")


def set_image(job: str, text: str) -> None:
    """Record a digest when the gate printed one, otherwise the gate's own word."""
    found = re.search(r"sha256:[0-9a-fA-F]+", text or "")
    value = found.group(0) if found else (text or "").strip().splitlines()[-1:] or ["unknown"]
    image = value if isinstance(value, str) else value[0]
    if not image:
        image = "unknown"
    path = os.path.join(job, "job.json")
    if os.path.islink(path):
        raise QualifyError("refusing a symlink job file")
    document = _load_json(path)
    document["image"] = image[:200]
    _write_private(path, json.dumps(document, indent=2) + "\n")


def _json_line(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


_PHASE_KEYS = {
    "gate": ("digest", "image", "admitted"),
    "serve": ("load_ok", "cid", "vram_after_load", "vram_cap"),
    "speed": ("rows", "at_depth"),
    "window": ("resets", "ttft_8k"),
    "depth": ("passed", "score"),
    "suite": ("stages", "completed", "references"),
    "smoke": ("models", "tool", "completion"),
    "memory": ("peak", "samples"),
}


def _stub_body(phase: str, text: str) -> dict | None:
    body = _json_line(text)
    if not body:
        return None
    keys = _PHASE_KEYS.get(phase)
    if keys is None:
        return body
    if any(key in body for key in keys):
        return body
    return None


def _allowed_evidence_file(job: str, raw: str | None) -> str | None:
    """A path from a log is readable only inside the job or the data directory."""
    if not raw or raw.startswith("-"):
        return None
    if os.path.islink(raw):
        return None
    if not os.path.isfile(raw):
        return None
    real = os.path.realpath(raw)
    roots = [os.path.realpath(job), os.path.realpath(os.path.join(job, "evidence"))]
    data = os.environ.get("A770B_DATA")
    if data:
        roots.append(os.path.realpath(data))
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return real
    return None


def _parse_window(job: str, text: str) -> dict:
    points = {}
    for match in re.finditer(
        r"^[ \t]*(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(?:([\d.]+)GiB|unavailable)\s+(-?\d+)\s*$",
        text or "",
        re.MULTILINE,
    ):
        target, _toks, ttft, _pp, _tg, vmax, resets = match.groups()
        points[int(target)] = {
            "ttft_s": float(ttft),
            "vram_max_gib": float(vmax) if vmax is not None else None,
            "resets": int(resets),
        }
    far = None
    plan_path = os.path.join(job, "plan.json")
    if os.path.isfile(plan_path) and not os.path.islink(plan_path):
        try:
            far = _load_json(plan_path).get("far_end")
        except QualifyError:
            far = None
    shallow = points.get(8000)
    far_point = points.get(far) if isinstance(far, int) else None
    resets = None
    if points:
        resets = max(point["resets"] for point in points.values())
    return {
        "resets": resets,
        "ttft_8k": shallow["ttft_s"] if shallow else None,
        "ttft_far": far_point["ttft_s"] if far_point else None,
        "points": {str(key): value for key, value in points.items()},
    }


def _parse_depth(text: str) -> dict:
    body: dict = {}
    match = re.search(r"depth probe at (\d+): (\d)/3", text or "")
    if match:
        body["depth_tokens"] = int(match.group(1))
        body["score"] = int(match.group(2))
        body["passed"] = int(match.group(2)) >= 2
        return body
    missing = re.search(r"depth probe at (\d+): not measured — (.+)", text or "")
    if missing:
        body["depth_tokens"] = int(missing.group(1))
        body["passed"] = None
        body["score"] = None
        body["unanswered"] = True
        body["not_measured"] = missing.group(2).strip()
    return body


def _parse_speed(job: str, text: str) -> dict:
    match = re.search(r"mapped →\s*(\S+)", text or "")
    path = _allowed_evidence_file(job, match.group(1)) if match else None
    if path is None:
        return {}
    try:
        doc = _load_json(path)
    except QualifyError:
        return {}
    at_depth = doc.get("at_depth") if isinstance(doc.get("at_depth"), dict) else {}
    labels = {8192: "8k", 32768: "32k", 65536: "64k", 100000: "100k"}
    profile = {
        "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
        "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
    }
    rows = []
    for key, cell in at_depth.items():
        if not isinstance(cell, dict):
            continue
        try:
            depth = int(key)
        except (TypeError, ValueError):
            continue
        prefill = _number(cell.get("pp"))
        decode = _number(cell.get("tg"))
        rows.append(
            {
                "depth_tokens": depth,
                "client_prefill": prefill,
                "client_decode": decode,
                "engine_prefill": None,
                "engine_decode": None,
                "engine_source": "bench client",
            }
        )
        label = labels.get(depth)
        if label:
            profile["prefill_tps"][label] = prefill
            profile["decode_tps"][label] = decode
    return {"rows": rows, "profile_speed": profile, "at_depth": at_depth}


def _stage_copy(stage: dict) -> dict:
    item = {
        "id": stage.get("id"),
        "working": stage.get("working"),
        "counts_toward_pass": stage.get("counts_toward_pass", True),
        "requests": stage.get("requests"),
        "prompt_tokens": stage.get("prompt_tokens"),
        "wall_s": stage.get("wall_s"),
    }
    if "gen_tokens" in stage:
        item["gen_tokens"] = stage.get("gen_tokens")
    if "compaction_lines" in stage:
        item["compaction_lines"] = stage.get("compaction_lines")
    return item


def _parse_suite(job: str, text: str) -> dict:
    match = re.search(r"suite complete:\s*(\S+)", text or "")
    path = _allowed_evidence_file(job, match.group(1)) if match else None
    body: dict = {"completed": False, "stages": []}
    if path is None:
        return body
    try:
        doc = _load_json(path)
    except QualifyError:
        return body
    stages = [_stage_copy(stage) for stage in doc.get("stages") or [] if isinstance(stage, dict)]
    body["completed"] = True
    body["stages"] = stages
    if isinstance(doc.get("instrument"), str):
        body["instrument"] = doc["instrument"]
    if isinstance(doc.get("references"), dict):
        body["references"] = {
            "passed": doc["references"].get("passed"),
            "attempts": doc["references"].get("attempts"),
        }
    prompts = [stage.get("prompt_tokens") for stage in stages if isinstance(stage.get("prompt_tokens"), int)]
    gens = [stage.get("gen_tokens") for stage in stages if isinstance(stage.get("gen_tokens"), int)]
    if prompts or gens:
        body["requests"] = {
            "max_prompt_tokens": max(prompts) if prompts else None,
            "max_completion_tokens": max(gens) if gens else None,
            "length_stops": None,
            "prefill_tps": None,
            "decode_tps": None,
            "source": "suite",
        }
        body["peak_prompt_tokens"] = max(prompts) if prompts else None
    return body


def _merge_suite(previous: dict, body: dict) -> dict:
    """Keep every pass. A later reference pass does not replace counted stages."""
    merged = dict(body)
    prev_stages = previous.get("stages") if isinstance(previous.get("stages"), list) else []
    new_stages = body.get("stages") if isinstance(body.get("stages"), list) else []
    merged["stages"] = list(prev_stages) + list(new_stages)
    merged["attempts"] = int(previous.get("attempts") or 0) + 1
    prev_refs = previous.get("references") if isinstance(previous.get("references"), dict) else {}
    new_refs = body.get("references") if isinstance(body.get("references"), dict) else {}
    if prev_refs or new_refs:
        def _count(item, key):
            value = item.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        merged["references"] = {
            "passed": _count(prev_refs, "passed") + _count(new_refs, "passed"),
            "attempts": _count(prev_refs, "attempts") + _count(new_refs, "attempts"),
        }
    if previous.get("requests") and not body.get("requests"):
        merged["requests"] = previous["requests"]
    elif isinstance(previous.get("requests"), dict) and isinstance(body.get("requests"), dict):
        left = previous["requests"]
        right = body["requests"]
        merged["requests"] = dict(right)
        for key in ("max_prompt_tokens", "max_completion_tokens"):
            values = [item.get(key) for item in (left, right) if isinstance(item.get(key), int)]
            merged["requests"][key] = max(values) if values else None
    if previous.get("completed") is True:
        merged["completed"] = True
    return merged


def apply_phase(job: str, phase: str, text: str, rc: int) -> None:
    """Store a phase. A stub JSON line wins; otherwise the real harness text is parsed."""
    body = _stub_body(phase, text)
    parsed = body is not None
    if body is None:
        if phase == "window":
            body = _parse_window(job, text)
        elif phase == "depth":
            body = _parse_depth(text)
        elif phase == "speed":
            body = _parse_speed(job, text)
        elif phase == "suite":
            body = _parse_suite(job, text)
        else:
            body = {}
    body["rc"] = rc
    if phase == "gate":
        set_image(job, text)
        write_note(job, "gate.json", body)
        return
    if phase == "serve":
        cid = str(body.get("cid") or "")
        if cid and re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", cid):
            cid_path = os.path.join(job, "cid")
            if not os.path.islink(cid_path):
                _write_private(cid_path, cid + "\n")
        previous = _evidence(job, "serve.json")
        merged = dict(previous)
        for key, value in body.items():
            if key == "rc" or value is not None:
                merged[key] = value
        write_note(job, "serve.json", merged)
        return
    if phase == "suite":
        previous = _evidence(job, "suite.json")
        if previous and not parsed:
            body = _merge_suite(previous, body)
        elif previous and parsed:
            body = _merge_suite(previous, body)
        else:
            body["attempts"] = 1
        write_note(job, "suite.json", body)
        return
    write_note(job, f"{phase}.json", body)


def assemble(job: str) -> None:
    """Build evidence/collected.json from the phase notes. Missing stays absent."""
    serve = _evidence(job, "serve.json")
    smoke = _evidence(job, "smoke.json")
    speed = _evidence(job, "speed.json")
    window = _evidence(job, "window.json")
    depth = _evidence(job, "depth.json")
    suite = _evidence(job, "suite.json")
    hooks = _evidence(job, "hooks.json")
    card = _evidence(job, "card.json")
    collected = {
        "load_ok": serve.get("load_ok"),
        "vram_after_load": serve.get("vram_after_load"),
        "vram_cap": serve.get("vram_cap"),
        "vram_peak": serve.get("vram_peak"),
        "load_s": serve.get("load_s"),
        "ram_gb_extra": serve.get("ram_gb_extra"),
        "smoke": {
            "models": smoke.get("models"),
            "completion": smoke.get("completion"),
            "tool": smoke.get("tool"),
        },
        "speed": speed.get("rows") if isinstance(speed.get("rows"), list) else [],
        "profile_speed": speed.get("profile_speed") if isinstance(speed.get("profile_speed"), dict) else {},
        "window_resets": window.get("resets"),
        "ttft_8k": window.get("ttft_8k"),
        "ttft_far": window.get("ttft_far"),
        "depth_pass": depth.get("passed"),
        "depth_score": depth.get("score"),
        "depth_tokens": depth.get("depth_tokens"),
        "unanswered": bool(depth.get("unanswered")),
        "depth_not_measured": depth.get("not_measured"),
        "suite": {
            "completed": suite.get("completed"),
            "stages": suite.get("stages") if isinstance(suite.get("stages"), list) else [],
            "references": suite.get("references")
            if isinstance(suite.get("references"), dict)
            else {},
            "requests": suite.get("requests") if isinstance(suite.get("requests"), dict) else {},
        },
        "requests": suite.get("requests") if isinstance(suite.get("requests"), dict) else {},
        "peak_prompt_tokens": suite.get("peak_prompt_tokens", "unknown"),
        "delivered": serve.get("delivered", "unknown"),
        "hooks": hooks,
        "statistics": suite.get("statistics") if isinstance(suite.get("statistics"), dict) else {},
        "card_free": {
            "containers": card.get("containers"),
            "vram_gib": card.get("vram_gib"),
            "idle_gib": card.get("idle_gib"),
        },
    }
    write_note(job, "collected.json", collected)


def _flag(argv: list[str], name: str) -> str | None:
    if name not in argv:
        return None
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise QualifyError(f"{name} needs a value")
    return argv[index + 1]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: qualify_lib.py plan|flags|status-merge|status-show|init-job|"
            "summarise|export|preflight|confirm|identity|forward-env|job-dir|pick|"
            "apply-phase|assemble|set-image",
            file=sys.stderr,
        )
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "plan":
            return cmd_plan(rest)
        if cmd == "flags":
            if not rest:
                raise QualifyError("flags needs an engine")
            engine = canonical_engine(rest[0])
            for token in profiling_contract.flags(engine):
                print(token)
            return 0
        if cmd == "status-merge":
            job, patch = _load_patch(rest)
            status_merge(job, patch)
            return 0
        if cmd == "status-show":
            job = rest[rest.index("--job") + 1]
            json.dump(status_show(job), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        if cmd == "init-job":
            job = rest[rest.index("--job") + 1]
            plan_path = rest[rest.index("--plan") + 1]
            launch = "setsid"
            if "--launch" in rest:
                launch = rest[rest.index("--launch") + 1]
            init_job(job, _load_json(plan_path), launch)
            return 0
        if cmd == "preflight":
            job = rest[rest.index("--job") + 1]
            preflight(job)
            return 0
        if cmd == "summarise":
            job = rest[rest.index("--job") + 1]
            summarise(job)
            return 0
        if cmd == "export":
            parser = _PlanParser(add_help=False)
            parser.add_argument("--job", required=True)
            parser.add_argument("--out")
            parser.add_argument("--allow-incomplete", action="store_true")
            args = parser.parse_args(rest)
            export_job(args.job, args.out, args.allow_incomplete)
            return 0
        if cmd == "confirm":
            job = rest[rest.index("--job") + 1]
            host = rest[rest.index("--host") + 1] if "--host" in rest else "127.0.0.1"
            port = rest[rest.index("--port") + 1] if "--port" in rest else "9"
            log_path = rest[rest.index("--log") + 1] if "--log" in rest else ""
            log_text = ""
            if log_path and os.path.isfile(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    log_text = handle.read()
            confirm_server(job, log_text, host, int(port))
            return 0
        if cmd == "identity":
            parser = _PlanParser(add_help=False)
            parser.add_argument("--pid", required=True)
            parser.add_argument("--starttime", required=True)
            parser.add_argument("--job")
            args = parser.parse_args(rest)
            if runner_identity(args.pid, args.starttime, PROJECT, args.job):
                return 0
            return 2
        if cmd == "starttime":
            print(proc_starttime(rest[0]) or "")
            return 0
        if cmd == "forward-env":
            for name, value in iter_forward_env():
                sys.stdout.buffer.write(f"{name}={value}\0".encode())
            return 0
        if cmd == "job-dir":
            print(job_dir(os.environ["A770B_DATA"], rest[0]))
            return 0
        if cmd == "pick":
            print(pick_job(os.environ["A770B_DATA"]))
            return 0
        if cmd == "apply-phase":
            job = _flag(rest, "--job")
            phase = _flag(rest, "--phase")
            rc_text = _flag(rest, "--rc") or "0"
            if not job or not phase:
                raise QualifyError("apply-phase needs --job and --phase")
            apply_phase(job, phase, sys.stdin.read(), int(rc_text))
            return 0
        if cmd == "assemble":
            assemble(_flag(rest, "--job") or "")
            return 0
        if cmd == "set-image":
            set_image(_flag(rest, "--job") or "", sys.stdin.read())
            return 0
        if cmd == "note":
            job = _flag(rest, "--job")
            name = _flag(rest, "--name")
            if not job or not name:
                raise QualifyError("note needs --job and --name")
            body = json.loads(sys.stdin.read() or "{}")
            if not isinstance(body, dict):
                raise QualifyError("note body must be a JSON object")
            write_note(job, name, body)
            return 0
        raise QualifyError(f"unknown qualify_lib command {cmd}")
    except QualifyError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return exc.code
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
