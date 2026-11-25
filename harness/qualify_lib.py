#!/usr/bin/env python3
"""Plan, status, and result helpers for the qualification runner.

The shell owns the process, the card lock, and the signals. This module owns
parsing, the profiling contract, and the files those decisions write. A run
records facts. The verdict string is always needs-review.
"""

from __future__ import annotations

import argparse
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
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)")
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


def kit_prompt_budget() -> tuple[int | None, str]:
    """Measured kit prompt, or unknown. This does not invent a token count."""
    raw = os.environ.get("A770B_QUALIFY_KIT_PROMPT_TOKENS")
    if raw is None or raw == "":
        return None, "no measured kit prompt total"
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise QualifyError(
            "A770B_QUALIFY_KIT_PROMPT_TOKENS must be a positive integer "
            f"(it is {raw!r})"
        )
    return int(raw), "A770B_QUALIFY_KIT_PROMPT_TOKENS"


def window_plan(ctx: int, profile_output) -> dict:
    """Usable window against the kit prompt plus the named headroom."""
    limit = output_limit(ctx, profile_output, os.environ.get("A770B_OUTPUT_TOKENS"))
    usable = int(ctx) - limit
    kit, kit_source = kit_prompt_budget()
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


def _refuse_key(tokens: list[str], where: str) -> None:
    for token in tokens:
        if token == "--api-key" or token.startswith("--api-key="):
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qualify start", add_help=False)
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
    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit as exc:
        raise QualifyError("qualify start needs --engine and --model") from exc
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
    kv_v = _same("--kv-v", args.kv_v, row.get("kv_v") if row else None)
    row_extra = row.get("extra") if row else None
    if row_extra is None:
        row_extra = ""
    if not isinstance(row_extra, str):
        raise QualifyError("profile extra is not a string")
    if args.extra and row and shlex.split(row_extra) != extra_tokens:
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

    reasoning = args.reasoning
    if reasoning is None and row and row.get("reasoning") in ("on", "off"):
        reasoning = row["reasoning"]
    if reasoning is None:
        reasoning = "off"
    budget = args.reasoning_budget
    end, gen = far_end_and_gen(ctx, reasoning, budget)
    if end <= 8000:
        raise QualifyError(
            f"the served window ({ctx}) leaves a far end of {end} tokens, "
            "at or below the sweep's 8000-token shallow point"
        )
    profile_output = row.get("output_tokens") if row else None
    window = window_plan(ctx, profile_output)
    fit_only = bool(args.fit_only)
    if window["action"] in ("refuse", "unknown") and not fit_only and not args.dry_run:
        raise QualifyError(window["message"])

    stages = [part for part in (args.stages or "").split(",") if part]
    ref_ids = _reference_ids()
    refs_apply = True
    if stages and not any(stage in ref_ids or stage.startswith("ref-") for stage in stages):
        refs_apply = False
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
    }


def _row_fields(row: dict | None) -> dict:
    if not row:
        return {}
    keep = (
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
    if plan["window"]["action"] in ("refuse", "unknown") and not plan["fit_only"]:
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
        data = {key: value for key, value in patch.items() if key not in ("trip", "_allow_terminal")}
        current.update(data)
        current["updated"] = _now()
        if "heartbeat" not in data:
            current.setdefault("heartbeat", current["updated"])
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
    tail = text.split(")", 1)[-1].split()
    if len(tail) < 20:
        return None
    return tail[19]


def pid_matches(pid, starttime) -> bool:
    if pid in (None, "", 0) or starttime in (None, ""):
        return False
    return proc_starttime(pid) == str(starttime)


def cmdline(pid) -> str:
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def runner_identity(pid, starttime, project: str) -> bool:
    """True when pid is this project's qualify.sh and the start time matches."""
    if not pid_matches(pid, starttime):
        return False
    text = cmdline(pid)
    script = os.path.join(project, "harness", "qualify.sh")
    return script in text


def _terminal(job: str) -> str | None:
    for name in TERMINAL_MARKERS:
        if os.path.exists(os.path.join(job, name)):
            return name
    return None


def status_show(job: str) -> dict:
    """Read status. A dead queued or running pid with no marker is written lost once."""
    path = os.path.join(job, "status.json")
    current = _read_status(path)
    if _terminal(job):
        return current
    state = current.get("state")
    if state in ("queued", "running") and not pid_matches(current.get("pid"), current.get("pid_starttime")):
        if current.get("state") != "lost":
            current = status_merge(job, {"state": "lost", "reason": "runner pid is gone"})
    return current


def job_dir(data_root: str, job_id: str) -> str:
    if not JOB_ID_RE.match(job_id or "") or ".." in job_id:
        raise QualifyError(f"refusing job id {job_id!r}")
    return os.path.join(data_root, "qualify", job_id)


def scrub_argv(argv: list) -> list:
    """Drop key switches. The key file stays the only place a key is stored."""
    cleaned = []
    skip = False
    for token in argv:
        text = str(token)
        if skip:
            skip = False
            continue
        if text == "--api-key":
            skip = True
            continue
        if text.startswith("--api-key="):
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
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        lines = stage.get("compaction_lines")
        requests = stage.get("requests")
        if lines is None or requests in (None, 0, "unknown"):
            return _check(
                "no-compaction-storm",
                "unknown",
                "evidence/suite.json",
                f"at most one compaction per {COMPACTION_PER_REQUESTS} requests",
                "a stage has no compaction count",
            )
        have = True
        worst = max(worst, float(lines) / float(requests))
    if not have:
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
    }
    for key, value in dotted.items():
        result[key] = value
    _write_private(os.path.join(job, "result.json"), json.dumps(result, indent=2) + "\n")
    _write_private(os.path.join(job, "report.md"), render_report(job, result))
    return result


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
    card = "card"
    mode = os.environ.get("A770B_CARD_MODE") or "inference"
    lines.append("")
    lines.append(f"Export with: local-build.sh qualify export {job_id}")
    lines.append(f"Registry file for a later merge: config/registry/{card}.{mode}.json")
    lines.append("Merging is a deliberate act. This runner does not add the profile.")
    lines.append("")
    return "\n".join(lines)


def _sentinel_row(row: dict) -> dict:
    """A copy that can pass the structural checker. The written row is unchanged."""
    check = json.loads(json.dumps(row))
    todos = {
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
        "depth_probe_100k": "not measured",
        "task_t1": "not measured",
        "kv": "f16",
        "flash_attention": "on",
        "reasoning": "off",
        "extra": "",
        "timeout_s": 60,
        "ctx": 8192,
        "useful_ctx": 8192,
        "vram_gib_after_load": 1.0,
        "ram_gb_extra": 0.0,
        "card": "a770",
        "backend": "vulkan",
        "mode": "inference",
        "fit": {"code": "not measured", "think": "not measured", "write": "not measured"},
    }
    import profiles

    required = profiles.PROFILE_KEYS - profiles.OPTIONAL_KEYS
    for key, value in todos.items():
        if key not in check and key not in required:
            continue
        current = check.get(key)
        if key not in check or current is None or (isinstance(current, str) and "__TODO__" in current):
            check[key] = value
    if not isinstance(check.get("capability"), dict):
        check["capability"] = {"published": 1}
    if not isinstance(check.get("requests"), dict) and "requests" in check:
        check["requests"] = {
            "max_prompt_tokens": 1,
            "max_completion_tokens": 1,
            "length_stops": 0,
            "prefill_tps": None,
            "decode_tps": None,
            "source": "suite",
        }
    speed = check.get("speed")
    if not isinstance(speed, dict):
        check["speed"] = {
            "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
            "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
        }
    return check


def _base_row(plan: dict, result: dict) -> dict:
    ctx = int(plan["ctx"])
    rates = {"8k": None, "32k": None, "64k": None, "100k": None}
    row = {
        "model": os.path.basename(plan["model"]),
        "ctx": ctx,
        "kv": plan.get("kv") or "f16",
        "flash_attention": "on",
        "reasoning": plan.get("reasoning") or "off",
        "extra": " ".join(plan.get("extra") or []),
        "timeout_s": 60,
        "vram_gib_after_load": 1.0,
        "ram_gb_extra": 0.0,
        "useful_ctx": ctx if isinstance(result.get("useful_ctx"), int) else ctx,
        "useful_ctx_rule": profiling_contract.RULE_NAME,
        "speed": {"decode_tps": dict(rates), "prefill_tps": dict(rates)},
        "instrument": "SUITE-1@1",
        "task": "__TODO__ — the task class this row wins",
        "evidence": "SUITE-1@1: suite result (this qualification run)",
        "measured_on": "__TODO__ — the card and build this was measured on",
        "use_for": "__TODO__ — write by hand from what this run showed",
        "fit": {"code": "not measured", "write": "__TODO__ — reviewer prose or not measured"},
        "capability": "__TODO__ — published evaluation, quoted verbatim",
        "source": "__TODO__",
        "family": "__TODO__",
        "architecture": "__TODO__",
        "quant": "__TODO__",
        "category": "dense",
        "weight_class": "9b",
        "capability_source": "__TODO__",
        "card": os.environ.get("A770B_CARD") or "a770",
        "backend": plan.get("backend") or "vulkan",
        "mode": os.environ.get("A770B_CARD_MODE") or "inference",
        "engine": plan["engine"],
    }
    if plan.get("kv_v"):
        row["kv_v"] = plan["kv_v"]
    fields = plan.get("row_fields") or {}
    for key in ("quant", "tool_parser", "reasoning_parser", "vllm_image", "family", "card", "backend", "mode"):
        if fields.get(key):
            row[key] = fields[key]
    if isinstance(result.get("requests.prefill_tps"), (int, float)) or result.get("requests.prefill_tps") is None:
        row["requests"] = {
            "max_prompt_tokens": None,
            "max_completion_tokens": None,
            "length_stops": result.get("requests.length_stops") if result.get("requests.length_stops") is not None else None,
            "prefill_tps": result.get("requests.prefill_tps") if isinstance(result.get("requests.prefill_tps"), (int, float)) else None,
            "decode_tps": result.get("requests.decode_tps") if isinstance(result.get("requests.decode_tps"), (int, float)) else None,
            "source": "suite",
        }
    delivered = result.get("speed.delivered")
    if isinstance(delivered, dict):
        row["speed"]["delivered"] = delivered
    elif delivered is None:
        row["speed"]["delivered"] = None
    if isinstance(result.get("profiling"), dict):
        row["profiling"] = result["profiling"]
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
    card = row.get("card") or "a770"
    mode = row.get("mode") or "inference"
    reg_name = f"{card}.{mode}.json"
    reg_path = os.path.join(scratch, "config", "registry", reg_name)
    existing = {"schema": 1, "card": card, "mode": mode, "profiles": {}}
    source = plan.get("profiles_file")
    if source and os.path.isfile(source) and not os.path.islink(source):
        try:
            loaded = _load_json(source)
            if loaded.get("card") == card and loaded.get("mode") == mode:
                existing = loaded
        except QualifyError:
            pass
    name = re.sub(r"[^a-z0-9]+", "-", os.path.basename(plan["model"]).lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "qualify-row"
    name = name[:48]
    check_row = _sentinel_row(row)
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: qualify_lib.py plan|flags|status-merge|status-show|init-job|summarise|export|preflight|confirm|identity", file=sys.stderr)
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
            job = rest[rest.index("--job") + 1]
            out = None
            if "--out" in rest:
                out = rest[rest.index("--out") + 1]
            export_job(job, out, "--allow-incomplete" in rest)
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
            pid = rest[rest.index("--pid") + 1]
            start = rest[rest.index("--starttime") + 1]
            if runner_identity(pid, start, PROJECT):
                return 0
            return 2
        if cmd == "starttime":
            print(proc_starttime(rest[0]) or "")
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
