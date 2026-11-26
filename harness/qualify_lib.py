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
        else:
            try:
                number = int(budget)
            except (TypeError, ValueError):
                raise QualifyError(f"--reasoning-budget must be an integer (it is {budget!r})")
            if number == 0:
                gen = 320
            else:
                gen = number + 320
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
    """The row's single-request peak, when a previous run recorded one.

    measured_tasks.context_tokens is the cumulative prompt total of a stage, not
    one request. Using it refuses rows that already passed the kit.
    """
    del stages
    if not isinstance(row, dict):
        return None
    requests = row.get("requests")
    if not isinstance(requests, dict):
        return None
    return _positive_token(requests.get("max_prompt_tokens"))


def _tokenizer_json(tokenizer: str | None) -> str | None:
    """A directory or a tokenizer.json file. A symlink to a real file is allowed."""
    raw = str(tokenizer or "").strip()
    if not raw:
        return None
    path = raw
    if os.path.isdir(path):
        path = os.path.join(path, "tokenizer.json")
    if os.path.isfile(path) and os.path.basename(path) == "tokenizer.json":
        return path
    return None


def _token_counts(tokenizer_json: str, texts: list[str]) -> list[int] | None:
    """Count each text. System python without tokenizers falls through to one uv child."""
    if not texts:
        return None
    try:
        from tokenizers import Tokenizer

        loaded = Tokenizer.from_file(tokenizer_json)
        return [len(loaded.encode(text, add_special_tokens=False).ids) for text in texts]
    except ImportError:
        pass
    except (OSError, ValueError, RuntimeError):
        return None
    if os.environ.get("A770B_QUALIFY_TOKENIZERS_CHILD") or shutil.which("uv") is None:
        return None
    script = (
        "import json,sys\n"
        "from tokenizers import Tokenizer\n"
        "raw=json.load(sys.stdin)\n"
        "loaded=Tokenizer.from_file(raw['tokenizer'])\n"
        "json.dump([len(loaded.encode(text, add_special_tokens=False).ids) for text in raw['texts']], sys.stdout)\n"
    )
    try:
        run = subprocess.run(
            ["uv", "run", "--with", "tokenizers", "python3", "-c", script],
            input=json.dumps({"tokenizer": tokenizer_json, "texts": texts}),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "A770B_QUALIFY_TOKENIZERS_CHILD": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if run.returncode != 0:
        return None
    try:
        counts = json.loads(run.stdout)
    except ValueError:
        return None
    if not isinstance(counts, list) or len(counts) != len(texts):
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts):
        return None
    return counts


def _project_text(path: str) -> str | None:
    """Read a kit file. The resolved path must stay inside this project."""
    if not path or not os.path.isfile(path):
        return None
    root = os.path.realpath(PROJECT)
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        return None
    try:
        return open(real, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def _directory_text(directory: str) -> str:
    if not directory or not os.path.isdir(directory):
        return ""
    root = os.path.realpath(PROJECT)
    parts = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [name for name in dirnames if not name.startswith(".") and name != "__pycache__"]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue
            real = os.path.realpath(full)
            if real != root and not real.startswith(root + os.sep):
                continue
            text = _project_text(full)
            if text:
                parts.append(text)
    return "\n".join(parts)


def _kit_stage_records(wanted: set[str]) -> list[dict]:
    records = []
    suite_path = os.path.join(PROJECT, "kit", "suite.json")
    reference_path = os.path.join(PROJECT, "kit", "reference", "reference.json")
    try:
        suite = _load_json(suite_path)
    except (OSError, ValueError, QualifyError):
        suite = {}
    for stage in suite.get("stages") or []:
        if isinstance(stage, dict):
            records.append(stage)
    try:
        reference = _load_json(reference_path)
    except (OSError, ValueError, QualifyError):
        reference = {}
    for stage in reference.get("entries") or []:
        if isinstance(stage, dict):
            records.append(stage)
    if not wanted:
        return [stage for stage in records if not str(stage.get("id") or "").startswith("ref-")]
    return [stage for stage in records if stage.get("id") in wanted]


def _tokenize_kit(stages: list[str] | None, tokenizer: str | None) -> tuple[int | None, str]:
    """The largest single stage prompt: brief, seat or exercise, and the wrapper.

    Each stage starts a fresh session, so the budget is the max, not the sum.
    """
    path = _tokenizer_json(tokenizer)
    if path is None:
        return None, ""
    wanted = set(stages or [])
    selected = _kit_stage_records(wanted)
    if not selected:
        return None, ""
    try:
        from render_profile import DEFAULT_PROMPT
    except ImportError:
        return None, ""
    seat_cache: dict[str, str] = {}
    texts = []
    for stage in selected:
        brief = stage.get("brief")
        if not isinstance(brief, str) or not brief:
            continue
        brief_path = brief if os.path.isabs(brief) else os.path.join(PROJECT, brief)
        brief_text = _project_text(brief_path)
        if brief_text is None:
            continue
        parts = [brief_text, DEFAULT_PROMPT]
        seat = stage.get("seat") or "kit/seat"
        if not str(stage.get("id") or "").startswith("ref-"):
            seat_path = seat if os.path.isabs(str(seat)) else os.path.join(PROJECT, str(seat))
            if seat_path not in seat_cache:
                seat_cache[seat_path] = _directory_text(seat_path)
            if seat_cache[seat_path]:
                parts.append(seat_cache[seat_path])
        else:
            spec = stage.get("spec")
            if isinstance(spec, str) and spec:
                spec_path = spec if os.path.isabs(spec) else os.path.join(PROJECT, spec)
                spec_text = _project_text(spec_path)
                if spec_text:
                    parts.append(spec_text)
            language = stage.get("language")
            slug = stage.get("slug")
            if isinstance(language, str) and isinstance(slug, str):
                exercise = os.path.join(PROJECT, "kit", "reference", language, slug)
                exercise_text = _directory_text(exercise)
                if exercise_text:
                    parts.append(exercise_text)
        texts.append("\n".join(parts))
    if not texts:
        return None, ""
    counts = _token_counts(path, texts)
    if not counts:
        return None, ""
    return max(counts), "max tokenized stage (brief, seat or exercise, OpenCode wrapper)"


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
        return peak, "requests.max_prompt_tokens"
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


def _model_host_path(name: str) -> str:
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    models = os.environ.get("A770B_MODELS") or ""
    return os.path.join(models, name) if models else name


def _directory_tokenizer(path: str) -> str | None:
    if not path or os.path.islink(path) or not os.path.isdir(path):
        return None
    if os.path.isfile(os.path.join(path, "tokenizer.json")) or os.path.isfile(os.path.join(path, "tokenizer_config.json")):
        return path
    return None


def _resolve_plan_tokenizer(explicit: str | None, engine: str, row: dict | None) -> str | None:
    """Same order as qualify.sh resolve_tokenizer, at plan time."""
    if explicit:
        return explicit
    env = os.environ.get("A770B_TOKENIZER_DIR") or ""
    if env:
        return env
    model = ""
    if isinstance(row, dict) and isinstance(row.get("model"), str):
        model = row["model"]
    host = _model_host_path(model)
    if engine == "strata" and host and os.path.isfile(host) and not os.path.islink(host):
        try:
            cfg = _load_json(host)
        except QualifyError:
            cfg = {}
        tok = cfg.get("tokenizer") if isinstance(cfg, dict) else None
        if isinstance(tok, str) and tok.strip():
            return tok.strip()
    return _directory_tokenizer(host)


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

    tokenizer = _resolve_plan_tokenizer(args.tokenizer, engine, row)
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


_PHASE_ORDER = ("preflight", "pre-hook", "serve", "smoke", "speed", "window", "depth", "suite")
_CHECK_PHASE = {
    "load-ok": "serve",
    "fit-under-cap": "serve",
    "smoke-tool-call": "smoke",
    "window-no-resets": "window",
    "depth-probe": "depth",
    "suite-completed": "suite",
    "counted-stages-pass": "suite",
    "references-pass": "suite",
    "switches-confirmed": "serve",
    "no-compaction-storm": "suite",
}


def _abort_phase(job: str) -> str | None:
    status = _read_status(os.path.join(job, "status.json"))
    if status.get("state") != "failed":
        return None
    reason = status.get("reason")
    if reason == "load":
        return "serve"
    if reason in _PHASE_ORDER:
        return reason
    return None


def _note_skipped(job: str, result: dict, checks: list, by_phase: dict, annotated: set) -> None:
    """Name the phase that aborted when a later check never ran."""
    abort = _abort_phase(job)
    if abort not in _PHASE_ORDER:
        return
    start = _PHASE_ORDER.index(abort)
    later = set(_PHASE_ORDER[start:])
    unmeasured = result.get("unmeasured") if isinstance(result.get("unmeasured"), list) else []
    have = {item.get("phase") for item in unmeasured if isinstance(item, dict)}
    for phase in _PHASE_ORDER[start + 1:]:
        if phase in have:
            continue
        if _phase_measured(phase, _evidence(job, f"{phase}.json")):
            continue
        item = {
            "command": "not run",
            "rc": 0,
            "log": f"phases/{phase}.log",
            "reason": f"skipped because {abort} failed",
            "phase": phase,
        }
        unmeasured.append(item)
        by_phase[phase] = item
        have.add(phase)
    result["unmeasured"] = unmeasured
    for check in checks:
        if check.get("result") != "unknown" or check.get("name") in annotated:
            continue
        phase = _CHECK_PHASE.get(check.get("name") or "")
        if phase not in later:
            continue
        why = by_phase.get(phase) or {
            "command": "not run",
            "rc": 0,
            "log": f"phases/{phase}.log",
            "reason": f"skipped because {abort} failed",
        }
        check["reason"] = (
            f"{why.get('reason')} (command {why.get('command')}, "
            f"exit {why.get('rc')}, log {why.get('log')})"
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
    resets = collected.get("window_resets")
    if plan.get("skip_window"):
        window_check = _check("window-no-resets", "unknown", None, "the sweep recorded no reset", "window sweep skipped")
    elif isinstance(resets, int) and not isinstance(resets, bool) and resets <= 0:
        # kernel_resets_since can report -1 when an older reset ages out of the window.
        window_check = _check("window-no-resets", "pass", "evidence/window.json", "the sweep recorded no new reset")
    elif isinstance(resets, int) and not isinstance(resets, bool):
        window_check = _check("window-no-resets", "fail", "evidence/window.json", "the sweep recorded no new reset")
    else:
        window_check = _check("window-no-resets", "unknown", None, "the sweep recorded no reset", "no sweep record")
    if collected.get("depth_pass") is True:
        depth_check = _check("depth-probe", "pass", "evidence/depth.json", "the depth probe scored a pass")
    elif collected.get("depth_pass") is False:
        depth_check = _check("depth-probe", "fail", "evidence/depth.json", "the depth probe scored a pass")
    elif collected.get("depth_not_measured"):
        depth_check = _check(
            "depth-probe",
            "unknown",
            "evidence/depth.json",
            "the depth probe scored a pass",
            str(collected.get("depth_not_measured")),
        )
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
    elif containers == 0 and isinstance(now_vram, (int, float)) and isinstance(idle, (int, float)) and not isinstance(now_vram, bool) and not isinstance(idle, bool):
        ok = now_vram <= idle + IDLE_VRAM_TOLERANCE_GIB
        card_check = _check(
            "card-free-after",
            "pass" if ok else "fail",
            "evidence/card.json",
            f"VRAM at or below idle plus {IDLE_VRAM_TOLERANCE_GIB} GiB and no project container",
        )
    elif containers == 0 and isinstance(now_vram, (int, float)) and not isinstance(now_vram, bool) and idle is None:
        card_check = _check(
            "card-free-after",
            "unknown",
            "evidence/card.json",
            "VRAM at or below idle plus tolerance",
            "no idle VRAM baseline",
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
    measured = dict(collected)
    if isinstance(absorb_pair, dict):
        measured["absorb"] = absorb_pair
    statistics = _statistics_from(measured, plan, dotted, useful)
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
    unmeasured = collected.get("unmeasured") if isinstance(collected.get("unmeasured"), list) else []
    result["unmeasured"] = unmeasured
    result["bench"] = collected.get("bench")
    result["far_speed"] = collected.get("far_end")
    result["measured_tasks"] = collected.get("measured_tasks")
    empties = [
        {
            "stage": stage.get("id"),
            "label": stage.get("label"),
            "evidence": stage.get("empty_patch"),
        }
        for stage in stages
        if isinstance(stage, dict) and stage.get("outcome") == "empty_patch"
    ]
    result["empty_patches"] = empties
    by_phase = {}
    for item in unmeasured:
        if isinstance(item, dict) and item.get("phase"):
            by_phase[item["phase"]] = item
    annotated = set()
    for check in checks:
        if check.get("result") != "unknown":
            continue
        why = by_phase.get(_CHECK_PHASE.get(check.get("name") or ""))
        if not isinstance(why, dict) or not why.get("reason"):
            continue
        check["reason"] = (
            f"{why.get('reason')} (command {why.get('command')}, "
            f"exit {why.get('rc')}, log {why.get('log')})"
        )
        annotated.add(check.get("name"))
    _note_skipped(job, result, checks, by_phase, annotated)
    if empties:
        result["checks"] = checks
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
        if stage.get("counts_toward_pass", True) is False:
            continue
        if isinstance(wall, bool) or not isinstance(wall, int) or wall < 1:
            continue
        walls.append(wall)
    if not walls:
        return "unknown"
    return max(walls)


def _stat_unknown(value) -> bool:
    return value in (None, "unknown")


def _fill_stat(stats: dict, key: str, value) -> None:
    """A supplied statistics dict wins. A real measurement fills only an unknown."""
    if not _stat_unknown(stats.get(key)) or _stat_unknown(value):
        return
    stats[key] = value


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
        _fill_stat(stats, "profiling", "recorded")
    vram = collected.get("vram_after_load")
    if isinstance(vram, (int, float)) and not isinstance(vram, bool) and vram > 0:
        _fill_stat(stats, "vram_gib_after_load", vram)
    ram = collected.get("ram_gb_extra")
    if isinstance(ram, (int, float)) and not isinstance(ram, bool) and ram >= 0:
        _fill_stat(stats, "ram_gb_extra", ram)
    engine = plan.get("engine")
    if engine and engine != "strata":
        extra = " ".join(plan.get("extra") or [])
        flash = "off" if re.search(r"(?:^|\s)-fa\s+off(?:\s|$)", extra) else "on"
        _fill_stat(stats, "flash_attention", flash)
    profile = collected.get("profile_speed") if isinstance(collected.get("profile_speed"), dict) else {}
    have_rate = False
    for side in ("decode_tps", "prefill_tps"):
        cells = profile.get(side)
        if isinstance(cells, dict) and any(_number(value) is not None for value in cells.values()):
            have_rate = True
    if have_rate:
        _fill_stat(stats, "speed.decode_tps / speed.prefill_tps", "recorded")
    if isinstance(collected.get("bench"), dict):
        _fill_stat(stats, "speed.bench", "recorded")
    far = collected.get("far_end")
    if isinstance(far, dict) and _number(far.get("ttft_s")) is not None:
        _fill_stat(stats, "speed.far_end", "recorded")
    delivered = collected.get("delivered")
    if isinstance(delivered, dict) and _number(delivered.get("prefill_tps")) and _number(delivered.get("decode_tps")):
        _fill_stat(stats, "speed.delivered", "recorded")
    suite = collected.get("suite") if isinstance(collected.get("suite"), dict) else {}
    exported_suite = _export_suite({"suite": suite})
    if exported_suite is not None:
        _fill_stat(stats, "suite", "recorded")
        _fill_stat(stats, "fit.code", "recorded")
    requests = collected.get("requests") if isinstance(collected.get("requests"), dict) else {}
    if isinstance(requests.get("max_prompt_tokens"), int):
        _fill_stat(stats, "requests", "recorded")
    if isinstance(collected.get("measured_tasks"), dict) and collected.get("measured_tasks"):
        _fill_stat(stats, "measured_tasks", "recorded")
    absorb = collected.get("absorb")
    if isinstance(absorb, dict) and (absorb.get("8k") is not None or absorb.get("far_end") is not None):
        _fill_stat(stats, "speed.absorb_s", "recorded")
    try:
        import profiles

        _fill_stat(stats, "instrument", profiles.kit_instrument())
    except (OSError, ValueError, ImportError):
        pass
    if exported_suite is not None and stats.get("instrument") not in (None, "unknown"):
        _fill_stat(stats, "evidence", "recorded")
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
    plan_mode = None
    plan_path = os.path.join(job, "plan.json")
    if os.path.isfile(plan_path) and not os.path.islink(plan_path):
        try:
            fields = _load_json(plan_path).get("row_fields") or {}
            plan_card = fields.get("card")
            plan_mode = fields.get("mode")
        except QualifyError:
            plan_card = None
            plan_mode = None
    card = plan_card or os.environ.get("A770B_CARD") or "<card>"
    mode = plan_mode or os.environ.get("A770B_CARD_MODE") or "inference"
    empties = result.get("empty_patches") or []
    if empties:
        lines.append("empty patch (the model ran and changed nothing):")
        for item in empties:
            lines.append(f"- {item.get('stage')}: {item.get('evidence')}")
    unmeasured = result.get("unmeasured") or []
    if unmeasured:
        lines.append("unmeasured:")
        for item in unmeasured:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('phase')}: {item.get('reason')} "
                    f"(exit {item.get('rc')}, log {item.get('log')})"
                )
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
    else:
        for key in ("bench", "far_end", "absorb_s", "delivered"):
            if speed.get(key) is None:
                speed.pop(key, None)
        speed.pop("decode_tps / speed.prefill_tps", None)
    for key in ("suite", "requests", "measured_tasks", "fit"):
        if check.get(key) is None:
            check.pop(key, None)
    return check


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _registry_stage(stage: dict) -> dict | None:
    working = stage.get("working")
    wall = stage.get("wall_s")
    name = stage.get("id")
    if not isinstance(working, bool) or not isinstance(name, str) or not name:
        return None
    if isinstance(wall, bool) or not isinstance(wall, (int, float)):
        return None
    axes = stage.get("axes")
    if not isinstance(axes, list) or not all(isinstance(item, str) for item in axes):
        axes = []
    lines = stage.get("lines")
    budget = stage.get("budget_lines")
    return {
        "id": name,
        "working": working,
        "conformance": stage.get("conformance") if isinstance(stage.get("conformance"), bool) else None,
        "lines": lines if isinstance(lines, int) and not isinstance(lines, bool) else None,
        "budget_lines": budget if isinstance(budget, int) and not isinstance(budget, bool) else None,
        "maintainable": stage.get("maintainable") if stage.get("maintainable") in (0, 3, 5) else None,
        "usable": stage.get("usable") if stage.get("usable") in (0, 3, 5) else None,
        "wall_s": wall,
        "axes": axes,
        "counts_toward_pass": stage.get("counts_toward_pass") is not False,
    }


def _export_suite(result: dict) -> dict | None:
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    stages = [stage for stage in suite.get("stages") or [] if isinstance(stage, dict)]
    if not stages:
        return None
    import suite_report

    totals = suite_report.totals({"stages": stages}, "qualification")
    runs = totals.get("runs")
    briefs = totals.get("briefs")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        return None
    if not isinstance(briefs, int) or isinstance(briefs, bool) or briefs < 1:
        return None
    body = {
        "briefs": briefs,
        "runs": runs,
        "passed": totals.get("passed") if isinstance(totals.get("passed"), int) else 0,
        "source": "qualification",
    }
    if isinstance(totals.get("timeouts"), int) and not isinstance(totals.get("timeouts"), bool):
        body["timeouts"] = totals["timeouts"]
    if isinstance(totals.get("mean_wall_s"), (int, float)) and not isinstance(totals.get("mean_wall_s"), bool):
        body["mean_wall_s"] = totals["mean_wall_s"]
    try:
        import profiles

        body["instrument"] = profiles.kit_instrument()
    except (OSError, ValueError, ImportError):
        pass
    copied = [item for item in (_registry_stage(stage) for stage in stages) if item]
    if copied:
        body["stages"] = copied
    return body


def _export_tasks(result: dict, plan: dict) -> dict | None:
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    thinking = "on" if plan.get("reasoning") == "on" else "off"
    tasks = {}
    for stage in suite.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("counts_toward_pass", True) is False:
            continue
        if stage.get("working") not in (True, False):
            continue
        wall = stage.get("wall_s")
        name = stage.get("id")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(wall, bool) or not isinstance(wall, int) or wall < 1:
            continue
        tokens = stage.get("prompt_tokens")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            tokens = None
        tasks[name] = {
            "quality": "pass" if stage.get("working") is True else "fail",
            "wall_s": wall,
            "context_tokens": tokens,
            "thinking": thinking,
        }
    return tasks or None


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
        word = "pass" if score >= 2 else "fail"
        row["depth_probe_100k"] = f"{word} ({score}/3)"
    else:
        row["depth_probe_100k"] = "not measured"
    bench = result.get("bench")
    if isinstance(bench, dict) and bench.get("tool") and isinstance(bench.get("at_depth"), dict) and bench.get("at_depth"):
        row["speed"]["bench"] = {
            "tool": bench.get("tool"),
            "prompt": bench.get("prompt"),
            "gen": bench.get("gen"),
            "flags": bench.get("flags") if isinstance(bench.get("flags"), str) and bench.get("flags") else "default",
            "at_depth": bench.get("at_depth"),
            "source": bench.get("source") if isinstance(bench.get("source"), str) else "bench",
        }
    far_speed = result.get("far_speed")
    if isinstance(far_speed, dict):
        tokens = far_speed.get("tokens")
        prefill = _number(far_speed.get("prefill_tps"))
        decode = _number(far_speed.get("decode_tps"))
        ttft = _number(far_speed.get("ttft_s"))
        if isinstance(tokens, int) and tokens > 0 and prefill and prefill > 0 and decode and decode > 0 and ttft and ttft > 0:
            row["speed"]["far_end"] = {
                "tokens": tokens,
                "prefill_tps": prefill,
                "decode_tps": decode,
                "ttft_s": ttft,
            }
    exported_suite = _export_suite(result)
    if exported_suite:
        row["suite"] = exported_suite
        mean = exported_suite.get("mean_wall_s")
        code = f"SUITE-1: {exported_suite['passed']}/{exported_suite['runs']} stages passed"
        if mean is not None:
            code += f", mean wall {mean}s"
        code += " (this qualification)"
        row["fit"] = {"code": code}
    tasks = _export_tasks(result, plan)
    if tasks:
        row["measured_tasks"] = tasks
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
        top_level = {
            "vram_gib_after_load": "vram_gib_after_load",
            "ram_gb_extra": "ram_gb_extra",
            "useful_ctx": "useful_ctx",
            "flash_attention": "flash_attention",
            "useful_ctx_rule": "useful_ctx_rule",
            "instrument": "instrument",
            "evidence": "evidence",
        }
        for field in missing:
            if field in top_level:
                key = top_level[field]
                if key not in row:
                    row[key] = None
            elif field == "speed.decode_tps / speed.prefill_tps":
                speed = row.setdefault("speed", {})
                speed.setdefault("decode_tps", {"8k": None, "32k": None, "64k": None, "100k": None})
                speed.setdefault("prefill_tps", {"8k": None, "32k": None, "64k": None, "100k": None})
            elif field in row and not isinstance(row.get(field), dict):
                row[field] = None
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
    if body.get("why") or body.get("skipped"):
        return body
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
        target, toks, ttft, pp, tg, vmax, resets = match.groups()
        points[int(target)] = {
            "tokens": int(toks),
            "ttft_s": float(ttft),
            "prefill_tps": float(pp),
            "decode_tps": float(tg),
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
    """The grading footer is the last line. Model text can quote an earlier one."""
    body: dict = {}
    found = re.findall(r"^depth probe at (\d+): ([0-3])/3$", text or "", re.MULTILINE)
    if found:
        tokens, score = found[-1]
        body["depth_tokens"] = int(tokens)
        body["score"] = int(score)
        body["passed"] = int(score) >= 2
        return body
    missing = re.findall(r"^depth probe at (\d+): not measured — (.+)$", text or "", re.MULTILINE)
    if missing:
        tokens, reason = missing[-1]
        body["depth_tokens"] = int(tokens)
        body["passed"] = None
        body["score"] = None
        body["unanswered"] = True
        body["not_measured"] = reason.strip()
    return body


def _mapped_speed_path(job: str, text: str) -> str | None:
    match = re.search(r"mapped →\s*(\S+)", text or "")
    if match:
        allowed = _allowed_evidence_file(job, match.group(1))
        if allowed:
            return allowed
    # bench_http.sh writes the mapped file, then exits 1 on a missing depth
    # before it echoes the path. The file is still the measurement.
    fallback = os.path.join(job, "evidence", "speed-bench.mapped.json")
    if os.path.isfile(fallback) and not os.path.islink(fallback):
        return os.path.realpath(fallback)
    return None


def _log_rates(text: str) -> tuple[dict[int, float], dict[int, float]]:
    """llama.cpp timing lines. Decode is keyed by the preceding prompt depth.

    The eval-time token count is n_gen, not the prompt. A prompt-eval count
    one off the requested depth (a BOS token) still names that depth.
    """
    import engine_record

    prefill: dict[int, float] = {}
    decode: dict[int, float] = {}
    pending = None
    for line in (text or "").splitlines():
        if line.startswith(("+", "-")):
            continue
        parsed = engine_record.parse_timing(line)
        if not parsed:
            continue
        tokens = int(parsed[1])
        rate = parsed[3]
        if "prompt eval time" in line:
            prefill[tokens] = rate
            pending = tokens
        elif "eval time" in line:
            decode[pending if pending is not None else tokens] = rate
            pending = None
    return prefill, decode


def _rate_at(table: dict, depth: int):
    for key in (depth, depth - 1, depth + 1):
        if key in table:
            return table[key]
    return None


def _metrics_rates(job: str, phase: str, engine: str) -> dict | None:
    before = os.path.join(job, "evidence", f"metrics-before-{phase}.prom")
    after = os.path.join(job, "evidence", f"metrics-after-{phase}.prom")
    if engine not in ("vllm", "llama.cpp"):
        return None
    if not os.path.isfile(before) or not os.path.isfile(after):
        return None
    if os.path.islink(before) or os.path.islink(after):
        return None
    import engine_evidence

    try:
        left = engine_evidence.parse_prometheus(open(before, encoding="utf-8", errors="replace").read())
        right = engine_evidence.parse_prometheus(open(after, encoding="utf-8", errors="replace").read())
        return engine_evidence.stage_counters(left, right, engine)
    except (OSError, ValueError):
        return None


def _parse_speed(job: str, text: str) -> dict:
    path = _mapped_speed_path(job, text)
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
                "engine_source": "unknown",
            }
        )
        label = labels.get(depth)
        if label:
            profile["prefill_tps"][label] = prefill
            profile["decode_tps"][label] = decode
    slice_path = os.path.join(job, "evidence", "engine-speed.log")
    if os.path.isfile(slice_path) and not os.path.islink(slice_path):
        try:
            prefill_by, decode_by = _log_rates(open(slice_path, encoding="utf-8", errors="replace").read())
        except OSError:
            prefill_by, decode_by = {}, {}
        for row in rows:
            depth = row["depth_tokens"]
            prefill = _rate_at(prefill_by, depth)
            decode = _rate_at(decode_by, depth)
            if prefill is not None or decode is not None:
                row["engine_prefill"] = prefill
                row["engine_decode"] = decode
                row["engine_source"] = "server log"
    plan_engine = ""
    plan_path = os.path.join(job, "plan.json")
    if os.path.isfile(plan_path) and not os.path.islink(plan_path):
        try:
            plan_engine = _load_json(plan_path).get("engine") or ""
        except QualifyError:
            plan_engine = ""
    metrics = _metrics_rates(job, "speed", plan_engine)
    if isinstance(metrics, dict) and len(rows) == 1 and rows[0]["engine_source"] == "unknown":
        prefill = _number(metrics.get("prefill_tps"))
        decode = _number(metrics.get("decode_tps"))
        if prefill is not None or decode is not None:
            rows[0]["engine_prefill"] = prefill
            rows[0]["engine_decode"] = decode
            rows[0]["engine_source"] = "/metrics"
    elif len(rows) != 1 and plan_engine in ("vllm", "llama.cpp"):
        for row in rows:
            if row["engine_source"] == "unknown":
                row["engine_reason"] = "one /metrics bracket covers more than one depth"
    for row in rows:
        if row["engine_source"] == "unknown" and not row.get("engine_reason"):
            row["engine_reason"] = "no per-depth rate source"
    body = {"rows": rows, "profile_speed": profile, "at_depth": at_depth}
    prompt = doc.get("prompt")
    gen = doc.get("gen")
    if (
        isinstance(doc.get("tool"), str)
        and doc.get("tool")
        and isinstance(prompt, int)
        and not isinstance(prompt, bool)
        and prompt > 0
        and isinstance(gen, int)
        and not isinstance(gen, bool)
        and gen > 0
        and at_depth
    ):
        body["bench"] = {
            "tool": doc.get("tool"),
            "prompt": prompt,
            "gen": gen,
            "flags": doc.get("flags") if isinstance(doc.get("flags"), str) and doc.get("flags") else "default",
            "at_depth": at_depth,
            "source": os.path.basename(path),
        }
    return body


_COMPACT_RE = re.compile(r"(?i)\bcompact(?:ing|ed|ion)\b")
_KEEP_STAGE = (
    "id",
    "working",
    "counts_toward_pass",
    "requests",
    "prompt_tokens",
    "gen_tokens",
    "wall_s",
    "max_prompt_tokens",
    "max_completion_tokens",
    "length_stops",
    "outcome",
    "verify",
    "conformance_exit",
    "lines",
    "label",
    "serve_evidence",
    "language",
    "budget_lines",
    "axes",
)


def _compaction_count(stage: dict) -> int | None:
    """A present capture with no compaction line is zero. A missing capture is unknown."""
    data = os.environ.get("A770B_DATA") or ""
    if not data:
        return None
    import suite_report

    path = suite_report.capture_path_for_stage(stage, data)
    if not path or os.path.islink(path) or not os.path.isfile(path):
        return None
    count = 0
    in_transcript = False
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            heading = line.rstrip("\r\n")
            if heading.startswith("## "):
                if in_transcript:
                    break
                in_transcript = heading == "## opencode transcript tail"
                continue
            if not in_transcript:
                continue
            if line.startswith(("+", "-")):
                continue
            if _COMPACT_RE.search(line):
                count += 1
    except OSError:
        return None
    return count


def _empty_patch_text(stage: dict) -> str | None:
    label = stage.get("label")
    data = os.environ.get("A770B_DATA") or ""
    if not data or not isinstance(label, str) or not label or "/" in label or label.startswith("."):
        return None
    path = os.path.join(data, "results", f"{label}.verify.json")
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        doc = _load_json(path)
    except QualifyError:
        return None
    error = doc.get("error")
    if not isinstance(error, str):
        return None
    if "patch is empty" in error or "changed nothing" in error:
        return error
    return None


def _stage_copy(stage: dict) -> dict:
    item = {}
    for key in _KEEP_STAGE:
        if key in stage:
            item[key] = stage.get(key)
    item.setdefault("counts_toward_pass", stage.get("counts_toward_pass", True))
    counted = _compaction_count(stage)
    if counted is not None:
        item["compaction_lines"] = counted
    elif "compaction_lines" in stage:
        item["compaction_lines"] = stage.get("compaction_lines")
    evidence = _empty_patch_text(stage)
    if evidence:
        item["outcome"] = "empty_patch"
        item["empty_patch"] = evidence
    return item


def _suite_result_path(job: str, text: str) -> tuple[str | None, str]:
    matches = list(
        re.finditer(
            r"(suite complete:|suite incomplete — results:|partial results:)\s*(\S+)",
            text or "",
        )
    )
    if not matches:
        return None, ""
    last = matches[-1]
    kind = "complete" if last.group(1) == "suite complete:" else "incomplete"
    return _allowed_evidence_file(job, last.group(2)), kind


def _parse_suite(job: str, text: str) -> dict:
    path, kind = _suite_result_path(job, text)
    body: dict = {"completed": False, "stages": []}
    if path is None:
        return body
    try:
        doc = _load_json(path)
    except QualifyError:
        return body
    raw_stages = [stage for stage in doc.get("stages") or [] if isinstance(stage, dict)]
    stages = [_stage_copy(stage) for stage in raw_stages]
    body["completed"] = kind == "complete"
    body["stages"] = stages
    body["source"] = os.path.basename(path)
    if isinstance(doc.get("instrument"), str):
        body["instrument"] = doc["instrument"]
    refs = [stage for stage in stages if str(stage.get("id") or "").startswith("ref-")]
    if refs:
        body["references"] = {
            "passed": sum(1 for stage in refs if stage.get("working") is True),
            "attempts": len(refs),
        }
    prompts = [
        stage.get("max_prompt_tokens")
        for stage in stages
        if isinstance(stage.get("max_prompt_tokens"), int) and not isinstance(stage.get("max_prompt_tokens"), bool)
    ]
    gens = [
        stage.get("max_completion_tokens")
        for stage in stages
        if isinstance(stage.get("max_completion_tokens"), int) and not isinstance(stage.get("max_completion_tokens"), bool)
    ]
    stops = [
        stage.get("length_stops")
        for stage in stages
        if isinstance(stage.get("length_stops"), int) and not isinstance(stage.get("length_stops"), bool)
    ]
    import suite_report

    _pairs, medians = suite_report.delivered({"stages": raw_stages}, os.environ.get("A770B_DATA") or "")
    prefill = medians.get("prefill_tps") if isinstance(medians, dict) else None
    decode = medians.get("decode_tps") if isinstance(medians, dict) else None
    if prompts or gens or stops or medians:
        body["requests"] = {
            "max_prompt_tokens": max(prompts) if prompts else None,
            "max_completion_tokens": max(gens) if gens else None,
            "length_stops": sum(stops) if stops else None,
            "prefill_tps": prefill,
            "decode_tps": decode,
            "source": os.path.basename(path),
        }
        if prompts:
            body["peak_prompt_tokens"] = max(prompts)
    if isinstance(medians, dict) and prefill and decode:
        body["delivered"] = {
            "prefill_tps": prefill,
            "decode_tps": decode,
            "source": os.path.basename(path),
        }
    return body


def _merge_suite(previous: dict, body: dict) -> dict:
    """Keep every pass. A later failure does not erase an earlier success, and does not invent one."""
    merged = dict(body)
    prev_stages = previous.get("stages") if isinstance(previous.get("stages"), list) else []
    new_stages = body.get("stages") if isinstance(body.get("stages"), list) else []
    merged["stages"] = list(prev_stages) + list(new_stages)
    merged["attempts"] = int(previous.get("attempts") or 0) + 1
    merged["completed"] = bool(previous.get("completed") and body.get("completed"))
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
            values = [item.get(key) for item in (left, right) if isinstance(item.get(key), int) and not isinstance(item.get(key), bool)]
            merged["requests"][key] = max(values) if values else None
        stops = [
            item.get("length_stops")
            for item in (left, right)
            if isinstance(item.get("length_stops"), int) and not isinstance(item.get("length_stops"), bool)
        ]
        merged["requests"]["length_stops"] = sum(stops) if stops else None
        for key in ("prefill_tps", "decode_tps"):
            if right.get(key) is None and left.get(key) is not None:
                merged["requests"][key] = left.get(key)
    if body.get("delivered") is None and isinstance(previous.get("delivered"), dict):
        merged["delivered"] = previous["delivered"]
    peaks = [
        value
        for value in (previous.get("peak_prompt_tokens"), body.get("peak_prompt_tokens"))
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if peaks:
        merged["peak_prompt_tokens"] = max(peaks)
    return merged


def _parse_serve(text: str, rc: int) -> dict:
    """serve_compose.sh prints prose. A healthy start names the container and the VRAM line."""
    body: dict = {}
    blob = text or ""
    started = (
        "started server container" in blob
        or "· Strata" in blob
        or "already up (" in blob
    )
    body["load_ok"] = bool(rc == 0 and started)
    found = re.search(
        r"VRAM after load:\s*([0-9]+(?:\.[0-9]+)?)\s*GiB\s*≤\s*.*?\s+([0-9]+(?:\.[0-9]+)?)\s*GiB",
        text or "",
    )
    if found:
        body["vram_after_load"] = float(found.group(1))
        body["vram_cap"] = float(found.group(2))
    times = re.findall(r"^phase_s\s+(\d+)\s*$", text or "", re.MULTILINE)
    if times:
        body["load_s"] = int(times[-1])
    return body


def _command_line(job: str, phase: str) -> str:
    path = os.path.join(job, "evidence", "argv.log")
    if not os.path.isfile(path) or os.path.islink(path):
        return ""
    last = ""
    prefix = phase + ":"
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.startswith(prefix):
                last = line.strip()
    except OSError:
        return ""
    return last


def _phase_measured(phase: str, body: dict) -> bool:
    if phase == "serve":
        return body.get("load_ok") is True or isinstance(body.get("vram_after_load"), (int, float))
    if phase == "speed":
        return isinstance(body.get("rows"), list) and bool(body.get("rows"))
    if phase == "window":
        return isinstance(body.get("resets"), int) or body.get("ttft_8k") is not None
    if phase == "depth":
        # unanswered is a measured failure to measure. It still needs a why.
        return isinstance(body.get("score"), int) or body.get("passed") is True
    if phase == "suite":
        return bool(body.get("stages")) or body.get("completed") is True
    if phase == "smoke":
        return any(isinstance(body.get(key), bool) for key in ("models", "tool", "completion"))
    return True


def _attach_why(job: str, phase: str, body: dict, rc: int, log: str | None = None) -> dict:
    if _phase_measured(phase, body):
        return body
    rel = log or f"phases/{phase}.log"
    why = body.get("why")
    if isinstance(why, dict) and why.get("reason"):
        why.setdefault("command", _command_line(job, phase) or phase)
        why.setdefault("rc", rc)
        why.setdefault("log", rel)
        body["why"] = why
        return body
    reason = body.get("not_measured") or f"{phase} produced no measurement"
    body["why"] = {
        "command": _command_line(job, phase) or phase,
        "rc": rc,
        "log": rel,
        "reason": reason,
    }
    return body


def apply_phase(job: str, phase: str, text: str, rc: int, log: str | None = None) -> None:
    """Store a phase. A stub JSON line wins; otherwise the real harness text is parsed."""
    body = _stub_body(phase, text)
    if body is None:
        if phase == "window":
            body = _parse_window(job, text)
        elif phase == "depth":
            body = _parse_depth(text)
        elif phase == "speed":
            body = _parse_speed(job, text)
        elif phase == "suite":
            body = _parse_suite(job, text)
        elif phase == "serve":
            body = _parse_serve(text, rc)
        else:
            body = {}
    body = _attach_why(job, phase, body, rc, log)
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
        if previous:
            body = _merge_suite(previous, body)
        else:
            body["attempts"] = 1
        write_note(job, "suite.json", body)
        return
    write_note(job, f"{phase}.json", body)


def _vram_peak(job: str):
    path = os.path.join(job, "evidence", "vram-samples.txt")
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    peak = None
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                value = float(parts[-1])
            except ValueError:
                continue
            peak = value if peak is None else max(peak, value)
    except OSError:
        return None
    return peak


def _ram_from_evidence(job: str, load_ok: bool = False):
    candidates = [os.path.join(job, "evidence", "engine-evidence.json")]
    # A global pointer from another run is not this job's measurement.
    if load_ok is True:
        data = os.environ.get("A770B_DATA") or ""
        pointer = os.path.join(data, "logs", "serve-evidence.path") if data else ""
        if pointer and os.path.isfile(pointer) and not os.path.islink(pointer):
            try:
                target = open(pointer, encoding="utf-8").read().strip()
            except OSError:
                target = ""
            if target:
                candidates.insert(0, target)
    for path in candidates:
        if not path or os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            doc = _load_json(path)
        except QualifyError:
            continue
        mib = doc.get("host_buffers_mib")
        if isinstance(mib, (int, float)) and not isinstance(mib, bool) and mib >= 0:
            return mib / 1024.0
    return None


def _far_speed(job: str, window: dict) -> dict | None:
    points = window.get("points") if isinstance(window, dict) else None
    if not isinstance(points, dict):
        return None
    plan_path = os.path.join(job, "plan.json")
    if not os.path.isfile(plan_path) or os.path.islink(plan_path):
        return None
    try:
        far = _load_json(plan_path).get("far_end")
    except QualifyError:
        return None
    point = points.get(str(far))
    if not isinstance(point, dict):
        return None
    return {
        "tokens": point.get("tokens"),
        "prefill_tps": point.get("prefill_tps"),
        "decode_tps": point.get("decode_tps"),
        "ttft_s": point.get("ttft_s"),
    }


def _unmeasured(serve, smoke, speed, window, depth, suite) -> list:
    notes = {
        "serve": serve,
        "smoke": smoke,
        "speed": speed,
        "window": window,
        "depth": depth,
        "suite": suite,
    }
    found = []
    for phase, note in notes.items():
        why = note.get("why") if isinstance(note, dict) else None
        if isinstance(why, dict) and why.get("reason"):
            item = dict(why)
            item["phase"] = phase
            found.append(item)
    return found


def _tasks_from_stages(stages: list) -> dict | None:
    return _export_tasks({"suite": {"stages": stages}}, {})


def _profiling_recorded(job: str) -> bool:
    path = os.path.join(job, "evidence", "profiling-start.json")
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    try:
        confirmed = _load_json(path).get("confirmed")
    except QualifyError:
        return False
    if not isinstance(confirmed, dict):
        return False
    return all(value is True for value in confirmed.values())


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
        "delivered": suite.get("delivered") if isinstance(suite.get("delivered"), dict) else serve.get("delivered"),
        "hooks": hooks,
        "statistics": suite.get("statistics") if isinstance(suite.get("statistics"), dict) else {},
        "bench": speed.get("bench") if isinstance(speed.get("bench"), dict) else None,
        "card_free": {
            "containers": card.get("containers"),
            "vram_gib": card.get("vram_gib"),
            "idle_gib": card.get("idle_gib"),
        },
    }
    peak = _vram_peak(job)
    if peak is not None:
        collected["vram_peak"] = peak
    if collected.get("ram_gb_extra") is None:
        ram = _ram_from_evidence(job, collected.get("load_ok") is True)
        if ram is not None:
            collected["ram_gb_extra"] = ram
    far_point = _far_speed(job, window)
    if far_point:
        collected["far_end"] = far_point
    collected["unmeasured"] = _unmeasured(serve, smoke, speed, window, depth, suite)
    if _profiling_recorded(job):
        collected["profiling_ok"] = True
    tasks = _tasks_from_stages(suite.get("stages") if isinstance(suite.get("stages"), list) else [])
    if tasks:
        collected["measured_tasks"] = tasks
    if isinstance(collected.get("delivered"), dict):
        pass
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
            log_rel = _flag(rest, "--log")
            if not job or not phase:
                raise QualifyError("apply-phase needs --job and --phase")
            apply_phase(job, phase, sys.stdin.read(), int(rc_text), log_rel)
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
