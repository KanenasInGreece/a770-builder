#!/usr/bin/env python3
"""Launch and identify one Linux-owned Strata v0.2.23 text server."""
import argparse
import ctypes as c
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

from strata_attach import Endpoint, attach_check, read_key


PIN = "4560d03793482ef367294cfbd86a4df8d5679e72"
STOP_TIMEOUT = 48
# The supervisor's waits for its server: after owner EOF, then after SIGTERM and SIGKILL.
# With the engine waits they total under 30 s, inside STOP_TIMEOUT.
SERVER_EOF_S = 10
SERVER_SIGNAL_S = 5
ENGINE_SIGNAL_S = 3
INTERRUPTS = (signal.SIGTERM, signal.SIGINT)
SERVER_GATE = """
import os
import sys

with os.fdopen(int(sys.argv[1]), "rb") as gate:
    if gate.read(1) != b"1":
        sys.exit(0)
command = sys.argv[2:]
os.execv(command[0], command)
"""


class Interrupted(BaseException):
    """SIGTERM or SIGINT during start. Not an Exception, so no handler swallows it."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


def interrupt(signum, _frame):
    # Ignore both first: a second signal must not cut the cleanup short.
    for other in INTERRUPTS:
        signal.signal(other, signal.SIG_IGN)
    raise Interrupted(signum)


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] not in ("Z", "X") else None
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_alive(state):
    return state["start"] is not None and process_identity(state["pid"]) == state["start"]


def device_override(name):
    return name.startswith(("ZE_", "UR_")) or name in {
        "SYCL_DEVICE_FILTER", "SYCL_DEVICE_ALLOWLIST", "SYCL_DEVICE_WHITELIST",
    }


# serve/server_parts/http_api.py split_joined_args: one element "--flag value" or "--flag=value" becomes two,
# except an element sitting in the value place of a bare flag. The terminal then reads the split list.
_JOINED_ARG = re.compile(r"^(--[A-Za-z0-9][A-Za-z0-9-]*)(?:\s+|=)(\S.*)$")
_BARE_FLAG = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9-]*$")
# Arch values that select a shape engine. hy4flash and inkling2_loop are the ARCH constants
# (serve/hy4_kind.py, serve/inkling_kind.py, strata_control/inkling_family.py). The other two are the
# remaining inkling names serve uses for that family (serve/server_parts/engines.py, http_api.py).
SHAPE_ENGINE_ARCH = frozenset({"hy4flash", "inkling2_loop", "inkling2", "inkling2_loop_text"})


def split_joined_args(args):
    """Split joined engine arguments the way v0.2.23 serve does, without changing the caller's list."""
    out = []
    for item in args:
        match = _JOINED_ARG.match(item) if isinstance(item, str) else None
        previous = out[-1] if out else None
        in_value_place = isinstance(previous, str) and _BARE_FLAG.match(previous) is not None
        if match is None or in_value_place:
            out.append(item)
            continue
        out += [match.group(1), match.group(2).strip()]
    return out


def terminal_replaces_generic_engine(exe, args):
    """True when v0.2.23 would replace a generic strata executable with a shape engine.

    The decision matches ``_autocorrect_shape_config``: arguments already split as ``split_joined_args``
    splits them, the first ``--native`` or ``--model`` (a value that starts with ``--`` still counts),
    the basename after ``\\`` is turned into ``/``, and ``--engram`` / ``--dspark`` only as exact tokens.
    """
    exe_base = str(exe).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if exe_base not in {"strata", "strata.exe"}:
        return False
    args = split_joined_args(args)
    model = None
    for i, tok in enumerate(args):
        if tok in {"--native", "--model"} and i + 1 < len(args):
            model = str(args[i + 1])
            break
    if not model:
        return False
    name = model.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if (name.startswith("hy4-flash") and name.endswith(".strata")) or "--engram" in args:
        return True
    return ("inkling" in name and name.endswith((".strata", ".gguf"))) or "--dspark" in args


def stated_engine_args(args):
    """The engine arguments the config states, in the form the terminal reads them.

    The terminal splits joined elements, then moves ``--reasoning-effort`` out of the engine arguments into
    its own ``reasoning_effort`` key (serve/server_parts/http_api.py lift_reasoning_effort_arg).
    """
    split = split_joined_args(args)
    out, i = [], 0
    while i < len(split):
        if split[i] == "--reasoning-effort" and i + 1 < len(split):
            i += 2
            continue
        if not split[i].startswith("--reasoning-effort="):
            out.append(split[i])
        i += 1
    return out


def engine_flag_groups(args):
    """Each flag's values, one list per occurrence in order. ``--flag=value`` and ``--flag value`` read the same."""
    found, values = {}, None
    for item in args:
        if item.startswith("--"):
            flag, equal, inline = item.partition("=")
            values = [inline] if equal else []
            found.setdefault(flag, []).append(values)
            continue
        if values is None:
            values = []
            found.setdefault("", []).append(values)
        values.append(item)
    return found


def prepare_config(path):
    raw = path.read_bytes()
    doc = json.loads(raw)
    if not isinstance(doc, dict) or doc.get("backend") not in ("intel", "sycl"):
        raise ValueError("prepared config must name the Intel backend")
    for field in ("exe", "cwd", "tokenizer", "model_name"):
        if not isinstance(doc.get(field), str) or not doc[field]:
            raise ValueError(f"prepared config requires {field}")
    exe, cwd, tok = (Path(doc[field]) for field in ("exe", "cwd", "tokenizer"))
    if not all(p.is_absolute() for p in (exe, cwd, tok)) or not cwd.is_dir() or not os.access(exe, os.X_OK):
        raise ValueError("prepared config needs an absolute executable, cwd and tokenizer directory")
    if not all((tok / name).is_file() for name in ("vocab.json", "tokenizer.json")):
        raise ValueError("prepared tokenizer needs vocab.json and tokenizer.json")
    engine_sha = file_digest(exe)
    if not hmac.compare_digest(engine_sha, os.environ.get("A770B_STRATA_ENGINE_SHA256", "")):
        raise ValueError("engine does not match A770B_STRATA_ENGINE_SHA256")
    if not re.fullmatch(r"[\w.-][\w./:-]*", doc["model_name"]):
        raise ValueError("prepared model_name is not a plain model identifier")
    for field in ("gpu", "replicas", "layer_split", "split_serve", "fallback", "vision", "image", "images",
                  "mcp", "mcp_servers", "mcp_config", "before_load", "api_key", "api_keys", "telemetry_key",
                  "telemetry_read_key", "lazy_load", "user_args", "idle_unload_s"):
        if doc.get(field) not in (None, "", [], {}, False):
            raise ValueError(f"prepared config must leave {field} disabled")
    if type(doc.get("parallel", 1)) is not int or doc.get("parallel", 1) != 1:
        raise ValueError("managed Strata requires one slot")
    args = doc.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("prepared config needs an engine args array")
    if doc.get("args_written", args) != args:
        raise ValueError("prepared args differ from args_written")
    # Same split the terminal applies before it reads flags. doc["args"] stays as written.
    split = split_joined_args(args)
    draft_pins = doc.pop("draft_asset_sha256", {})
    if not isinstance(draft_pins, dict):
        raise ValueError("draft_asset_sha256 must map absolute draft asset paths to SHA-256")
    drafts = {}
    for i, item in enumerate(split):
        flag, equal, inline = item.partition("=")
        if flag in {"--batch", "--slots", "--gpu", "--layer-split", "--device", "--devices", "--serve"}:
            raise ValueError(f"prepared config contains unsupported engine flag {flag}")
        value = inline if equal else split[i + 1] if i + 1 < len(split) else ""
        if flag in {"--spec", "--draft-model", "--draft-conf"}:
            if not value or value.startswith("--"):
                raise ValueError(f"{flag} requires a value")
        if flag in {"--draft-model", "--mtp-draft-vocab", "--mtp"}:
            asset = Path(value)
            if flag == "--mtp" and asset.is_dir():
                asset /= "draft_vocab.bin"
            if not asset.is_absolute() or not asset.is_file():
                raise ValueError(f"{flag} needs a prepared local asset")
            sha = file_digest(asset)
            pin = draft_pins.get(str(asset))
            if not isinstance(pin, str) or not hmac.compare_digest(sha, pin):
                raise ValueError(f"{flag} needs a matching draft_asset_sha256 pin")
            drafts[str(asset)] = sha
    if doc.get("drafting") == "dflash2" and not any(a.partition("=")[0] == "--draft-model" for a in split):
        raise ValueError("explicit DFlash2 needs a pinned --draft-model")
    if doc.get("arch") in SHAPE_ENGINE_ARCH:
        raise ValueError("prepared config arch selects a shape engine")
    if terminal_replaces_generic_engine(doc["exe"], args):
        raise ValueError("prepared config names a shape model the terminal would start with another engine")
    # user_args is the terminal's contract for options a person set (strata_control/user_args.py): its start-time
    # rewrites that honour the contract leave them alone. Every stated option goes there; check_engine_args
    # catches the rewrites that do not honour it.
    stated = stated_engine_args(args)
    first = next((i for i, item in enumerate(stated) if item.startswith("--")), None)
    if first is not None:
        doc["user_args"] = stated[first:]
    selector = os.environ.get("A770B_ONEAPI_DEVICE_SELECTOR", "")
    env = doc.get("env", {})
    if not re.fullmatch(r"level_zero:[0-9]+", selector):
        raise ValueError("managed Strata requires a single indexed Level Zero device selector")
    if not isinstance(env, dict) or env.get("ONEAPI_DEVICE_SELECTOR", selector) != selector:
        raise ValueError("prepared config changes the selected card")
    guarded = {"LD_PRELOAD", "STRATA_PRIMARY_DEVICE", "STRATA_NO_ACQUIRE", "STRATA_NO_PRESET",
               "STRATA_NO_AUTO_CONVERSATION_CACHE", "STRATA_KEEP_LEFTOVERS", "HOME"}
    if env.keys() & guarded or any(device_override(k) or k.startswith((
            "STRATA_HOME", "STRATA_SETTINGS_", "STRATA_PROFILE_", "STRATA_CONNECTION_",
            "STRATA_RUNTIME_", "XDG_")) for k in env):
        raise ValueError("prepared config cannot override the isolated runtime or card selection")
    doc["env"] = {**env, "ONEAPI_DEVICE_SELECTOR": selector}
    return doc, {"engine": "strata", "source_commit": PIN, "engine_sha256": engine_sha,
                 "config_sha256": hashlib.sha256(raw).hexdigest(), "tokenizer_sha256": file_digest(tok / "tokenizer.json"),
                 "model_id": doc["model_name"], "selector": selector, "config": str(path), "draft_sha256": drafts,
                 "host_memory_settings": host_memory_settings(env, args)}


# The config's host-memory knobs (docs/OPERATING.md, Managed Strata): the pinned host mirror and its guard, and the
# engine flags that place experts and per-layer embeddings in host memory. A VRAM reserve is not one of them.
HOST_MEMORY_ENV = ("STRATA_MIRROR_MIB", "STRATA_VERIFY_NO_HOST")
HOST_MEMORY_FLAGS = ("--expert-cache", "--stream-experts", "--ple-io")


def host_memory_settings(env, args):
    """The host-memory settings as the config writes them: values verbatim, a bare flag True, an absent one absent.

    Flags are read after ``split_joined_args``, so one joined element matches the two-element form.
    """
    found = {}
    split = split_joined_args(args)
    for i, item in enumerate(split):
        flag, equal, inline = item.partition("=")
        if flag not in HOST_MEMORY_FLAGS:
            continue
        following = split[i + 1] if i + 1 < len(split) else None
        if equal:
            found[flag] = inline
        elif following is not None and not following.startswith("--"):
            found[flag] = following
        else:
            found[flag] = True
    return {"env": {k: env[k] for k in HOST_MEMORY_ENV if k in env}, "args": found}


def launch_evidence_matches(old, current):
    """Compare launch identity while allowing older state files to lack reporting-only metadata.

    The complete prepared config digest remains part of the identity, so changes to Strata memory knobs still
    require a restart. ``host_memory_settings`` is a derived explanation of that already-hashed config.
    """
    return all(old.get(key) == value for key, value in current.items() if key != "host_memory_settings")


def level_zero_pci_ids():
    """Read the PCI IDs in the single Level Zero driver's device order."""
    overrides = sorted(name for name, value in os.environ.items() if value and device_override(name))
    if overrides:
        raise ValueError(f"clear {', '.join(overrides)} before selecting an indexed Level Zero card")
    ze = c.CDLL("libze_loader.so.1")
    handle = c.c_void_p
    count_type = c.c_uint32

    def checked(function, *arguments):
        code = function(*arguments)
        if code:
            raise ValueError(f"Level Zero device preflight failed (0x{code:08x})")

    checked(ze.zeInit, 1)  # ZE_INIT_FLAG_GPU_ONLY
    drivers = count_type()
    checked(ze.zeDriverGet, c.byref(drivers), None)
    if drivers.value != 1:
        raise ValueError("managed Strata needs one Level Zero GPU driver to resolve device order")
    driver = handle()
    checked(ze.zeDriverGet, c.byref(drivers), c.byref(driver))
    count = count_type()
    checked(ze.zeDeviceGet, driver, c.byref(count), None)
    devices = (handle * count.value)()
    checked(ze.zeDeviceGet, driver, c.byref(count), devices)

    class DeviceProperties(c.Structure):
        # ze_device_properties_t from the Level Zero API. Keep the ABI layout for fields the loader writes.
        _fields_ = [("stype", count_type), ("next", handle), ("type", count_type),
                    ("vendor", count_type), ("device", count_type), ("flags", count_type),
                    ("subdevice", count_type), ("clock", count_type), ("max_allocation", c.c_uint64),
                    ("topology", count_type * 7), ("timer_resolution", c.c_uint64),
                    ("timestamps", count_type * 2), ("uuid", c.c_uint8 * 16), ("name", c.c_char * 256)]

    ids = []
    for device in devices:
        properties = DeviceProperties(stype=3)  # ZE_STRUCTURE_TYPE_DEVICE_PROPERTIES
        checked(ze.zeDeviceGetProperties, handle(device), c.byref(properties))
        ids.append((properties.vendor, properties.device))
    return ids


def require_selected_card(selector):
    cards = json.loads((Path(__file__).resolve().parents[1] / "config/cards.json").read_text())["cards"]
    card = cards.get(os.environ.get("A770B_CARD"))
    if not card or card.get("gpu_match") != os.environ.get("A770B_GPU_MATCH"):
        raise ValueError("managed Strata needs the selected card's nvtop match from config/cards.json")
    pci = tuple(int(part, 16) for part in card["pci"].split(":"))
    index = int(selector.split(":")[1])
    ids = level_zero_pci_ids()
    if index >= len(ids) or ids[index] != pci:
        raise ValueError(f"{selector} does not select the configured card ({card['pci']})")


def source_inputs():
    root = Path(os.environ["A770B_STRATA_ROOT"])
    python = Path(os.environ["A770B_STRATA_PYTHON"])
    if not root.is_absolute() or not python.is_absolute() or not os.access(python, os.X_OK):
        raise ValueError("set absolute A770B_STRATA_ROOT and A770B_STRATA_PYTHON paths")
    git = ["git", "-C", str(root)]
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    changed = subprocess.check_output([*git, "status", "--porcelain", "--untracked-files=normal", "--", "terminal"],
                                      text=True).strip()
    if commit != PIN or changed or not (root / "terminal/serve/server.py").is_file():
        raise ValueError("Strata terminal source must be a clean v0.2.23 checkout")
    return root / "terminal", python


def read_state(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def write_state(path, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(state, stream)
            stream.write("\n")
            stream.flush()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def server_alive(state):
    started = state.get("server_start")
    return started is not None and process_identity(state["server_pid"]) == started


def engine_processes(state):
    """Yield (pid, start time) of each live engine recorded for this launch's server."""
    started = state.get("server_start")
    if started is None:
        return
    parent = {"pid": state["server_pid"], "token": f"l:{started}"}
    # Strata gives its engine a separate process group and records it under this launch's private XDG state.
    for path in (Path(state["run"]) / "xdg-state/strata/procs").glob("*.json"):
        record = read_state(path)
        if record is None or record.get("role") != "engine" or record.get("parent") != parent:
            continue
        identity = process_identity(record["pid"])
        if identity is not None and record["token"] == f"l:{identity}":
            yield record["pid"], identity


def engine_alive(state):
    return any(True for _ in engine_processes(state))


def engine_argv(state):
    """The argv of this launch's one live engine, from its /proc cmdline."""
    engines = list(engine_processes(state))
    if len(engines) != 1:
        raise ValueError(f"cannot verify Strata engine arguments: found {len(engines)} owned engines, not one")
    pid, started = engines[0]
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    argv = [os.fsdecode(item) for item in raw.split(b"\0")[:-1]]
    if process_identity(pid) != started or "--serve" not in argv:
        raise ValueError("cannot verify Strata engine arguments: the owned engine's command line is not readable")
    return argv


def check_engine_args(state):
    """Refuse an engine that lacks or changes a flag the prepared config states; return what to record.

    Flags the terminal adds are not refused. They are listed in ``engine_args_added``.
    """
    argv = engine_argv(state)
    config = json.loads(Path(state["snapshot"]).read_text())
    stated = engine_flag_groups(stated_engine_args(config["args"]))
    actual = engine_flag_groups(argv[argv.index("--serve") + 1:])

    def shown(flag, occurrences):
        return ", ".join(" ".join(filter(None, (flag, *values))) for values in occurrences)

    wrong = [f"{flag or 'positional arguments'}: config states '{shown(flag, wanted)}', "
             + (f"engine has '{shown(flag, actual[flag])}'" if flag in actual else "engine does not have it")
             for flag, wanted in stated.items() if actual.get(flag) != wanted]
    if wrong:
        raise ValueError("Strata engine arguments differ from the prepared config: " + "; ".join(wrong))
    return {"engine_argv": argv, "engine_args_added": sorted(actual.keys() - stated.keys())}


def occupied(state):
    return process_alive(state) or server_alive(state) or engine_alive(state)


def signal_and_wait(pid, started, signum, timeout):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        if process_identity(pid) != started:
            return True
        signal.pidfd_send_signal(fd, signum)
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        return bool(poller.poll(timeout * 1000))
    except ProcessLookupError:
        return True
    finally:
        os.close(fd)


def end_engines(state, grace):
    """SIGTERM, then SIGKILL, this launch's verified engines, waiting after each."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        # Signal every engine before waiting, so they share one grace period.
        for pid, started in list(engine_processes(state)):
            signal_and_wait(pid, started, signum, 0)
        deadline = time.monotonic() + grace
        while engine_alive(state) and time.monotonic() < deadline:
            time.sleep(0.1)


# Engine 0.1.69 (and 0.1.63) prints the GGUF expert mirror as
# "mirrored in pinned host memory (%.2f GiB, %.1f s)" — gguf_src.mirrored_bytes(), sycl generate.cpp.
# Two other lines in that binary report pinned memory this figure does not include, so they are not added:
# "the K/V in %.2f GiB of pinned RAM" is qsa_kv_host_bytes() when KV streaming is on, and
# "resident %.2f GiB, pinned %.2f GiB" is FileExpertSource's complement pinned_bytes().
_MIRROR_LINE = re.compile(
    r"mirrored in pinned host memory \((?P<amount>\d+(?:\.\d+)?) GiB, \d+(?:\.\d+)? s\)")
_OTHER_PINNED_LINES = (
    ("kv_streaming", re.compile(r"the K/V in (?P<amount>\d+(?:\.\d+)?) GiB of pinned RAM")),
    ("file_expert_complement", re.compile(
        r"resident \d+(?:\.\d+)? GiB, pinned (?P<amount>\d+(?:\.\d+)?) GiB")),
)
_PINNED_MIRROR_NOTE = (
    "The figure is the GGUF expert mirror only (mirrored_bytes). Those other lines are not added: "
    "a KV-streaming line reports qsa_kv_host_bytes, and a FileExpertSource complement line reports that complement's pinned_bytes."
)


def _gib_line(amount):
    return round(float(amount) * 1024 ** 3)


def observed_pinned_host_memory(log_path):
    """Read the engine's GGUF expert-mirror line. Other pinned-memory lines are kept beside it and not added in."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    mirror = None
    for line in reversed(lines):
        match = _MIRROR_LINE.search(line)
        if match:
            mirror = (match, line)
            break
    if mirror is None:
        return None
    match, line = mirror
    others = []
    for raw in lines:
        for kind, pattern in _OTHER_PINNED_LINES:
            found = pattern.search(raw)
            if found:
                others.append({"kind": kind, "value_bytes": _gib_line(found.group("amount")),
                               "reported_text": raw.strip()[-300:], "included_in_value_bytes": False})
    return {"value_bytes": _gib_line(match.group("amount")), "source": str(log_path),
            "reported_text": line.strip()[-300:], "note": _PINNED_MIRROR_NOTE,
            "other_pinned_lines": others}


def observe_host_memory(evidence_path):
    """Persist Strata's reported pinned allocation before the ladder renders its profile row."""
    path = Path(evidence_path)
    evidence = read_state(path)
    if not isinstance(evidence, dict) or evidence.get("engine") != "strata":
        raise ValueError("Strata evidence is missing or belongs to another engine")
    log_dir = evidence.get("log_dir")
    if not isinstance(log_dir, str) or not Path(log_dir).is_absolute():
        raise ValueError("Strata evidence has no absolute log directory")
    observed = observed_pinned_host_memory(Path(log_dir) / "engine.log")
    if observed is not None:
        evidence["observed_pinned_host_memory"] = observed
        write_state(path, evidence)
    return path


def stop(path, expected=None):
    state = read_state(path)
    if state is None:
        return
    if expected is not None and expected != state["id"]:
        raise ValueError("Strata instance changed; refusing to stop it")
    if process_alive(state) and not signal_and_wait(state["pid"], state["start"], signal.SIGTERM, STOP_TIMEOUT):
        signal_and_wait(state["pid"], state["start"], signal.SIGKILL, 3)
    # The supervisor can add the server identity while handling the foreground stop.
    current = read_state(path)
    if current is None:
        # Another stopper removed the record. That is success only if nothing is left.
        if occupied(state):
            raise ValueError("owned Strata processes remain alive after their record was removed")
        return
    if current["id"] != state["id"]:
        raise ValueError("Strata ownership changed during stop")
    if server_alive(current):
        pid, started = current["server_pid"], current["server_start"]
        if not signal_and_wait(pid, started, signal.SIGTERM, 3):
            signal_and_wait(pid, started, signal.SIGKILL, 3)
    end_engines(current, min(ENGINE_SIGNAL_S, STOP_TIMEOUT))
    if occupied(current):
        raise ValueError("owned Strata processes remain alive; retaining ownership record")
    evidence_path = Path(current["run"]) / "evidence.json"
    evidence = read_state(evidence_path)
    observed = observed_pinned_host_memory(Path(current["run"]) / "engine.log")
    if evidence is not None and observed is not None:
        evidence["observed_pinned_host_memory"] = observed
        write_state(evidence_path, evidence)
    latest = read_state(path)
    if latest is not None and latest["id"] != state["id"]:
        raise ValueError("Strata ownership changed during stop")
    path.unlink(missing_ok=True)


def stop_owned(path, instance):
    """Stop this instance if it still holds the record; leave any other one alone."""
    state = read_state(path)
    if state is not None and state["id"] == instance:
        stop(path, instance)


def record_owned(owned, instance):
    """Append the id to the caller's stop list, which must already be a regular file."""
    # O_NONBLOCK makes a FIFO with no reader fail instead of blocking the start.
    fd = os.open(owned, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("A770B_STRATA_OWNED_FILE is not a regular file")
        line = f"{instance}\n".encode()
        if os.write(fd, line) != len(line):
            raise ValueError("could not record the Strata instance id")
    finally:
        os.close(fd)


def close_quietly(fd):
    # A signal can land between a close and the name being cleared.
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def launch_mac(key, config, argv):
    args = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode()
    message = b"strata-owned-launch-v1\0" + len(config).to_bytes(8, "big") + config + len(args).to_bytes(8, "big") + args
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def health_matches(state, key):
    if not key or "server_pid" not in state:
        return False
    if state["server_start"] is None or process_identity(state["server_pid"]) != state["server_start"]:
        return False
    try:
        ready = read_state(Path(state["run"]) / "ready.json")
    except json.JSONDecodeError:
        # The pinned Terminal can expose its ready file before the JSON write completes.
        return False
    if not isinstance(ready, dict) or (ready.get("pid"), ready.get("bound_host"), ready.get("bound_port"),
                                      ready.get("model_id"), ready.get("model_state")) != (
            state["server_pid"], "127.0.0.1", state["port"], state["model_id"], "ready"):
        return False
    instance_id = ready.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        return False
    ep = Endpoint(f"http://127.0.0.1:{state['port']}", key)
    verdict = attach_check(ep, state["model_id"], state["ctx"], "key")
    if not verdict["ok"]:
        return False
    status, doc, err = ep.get_json("/v1/telemetry")
    instance = doc.get("instance") if isinstance(doc, dict) else None
    mac = instance.get("launch_attestation") if isinstance(instance, dict) else None
    return (not err and status == 200 and isinstance(mac, str) and instance.get("id") == instance_id
            and hmac.compare_digest(mac, state["attestation"]))


def memavailable_mib():
    """Whole MiB of MemAvailable. None when the file has no reading, so the floor still refuses."""
    try:
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


_UNSET = object()


def ram_check(minimum):
    """(ok, available MiB). ok is false when the reading is missing or short."""
    available = memavailable_mib()
    return (available is not None and available >= minimum, available)


def ram_ok(minimum):
    return ram_check(minimum)[0]


def write_floor_stop(phase, floor_mib, instance, path=None, available_mib=_UNSET):
    """Replace the caller's floor file. A non-empty file is the runner's own outcome."""
    target = path if path else os.environ.get("A770B_STRATA_FLOOR_FILE")
    if not target:
        return
    dest = Path(target)
    if dest.is_symlink():
        raise ValueError("refusing a symlink floor file")
    if not dest.parent.is_dir():
        return
    if available_mib is _UNSET:
        available_mib = memavailable_mib()
    write_state(dest, {
        "phase": phase,
        "available_mib": available_mib,
        "floor_mib": int(floor_mib),
        "instance": instance,
    })


def write_enforcement_receipt(enforcement):
    """Atomically report a Strata memory-floor check to the active kit monitor, if requested."""
    if enforcement not in {"launch", "continuous"}:
        raise ValueError("unsupported memory-floor enforcement receipt")
    target = os.environ.get("A770B_BUDGET_GATE_RECEIPT")
    if not target:
        return False
    path = Path(target)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"enforcement": enforcement, "recorded_at": time.time()}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except OSError as exc:
        print(f"Strata could not record the host RAM floor receipt: {exc}", file=sys.stderr, flush=True)
        return False
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def end_launch(server, plan):
    """End the server after owner EOF, then this run's engines. Nothing escapes."""
    try:
        try:
            server.wait(timeout=SERVER_EOF_S)
        except subprocess.TimeoutExpired:
            server.terminate()
            try:
                server.wait(timeout=SERVER_SIGNAL_S)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=SERVER_SIGNAL_S)
    except Exception as exc:  # The engines below must still be ended.
        print(f"Strata server did not end: {exc}", file=sys.stderr, flush=True)
    try:
        end_engines(plan, ENGINE_SIGNAL_S)
    except Exception as exc:
        print(f"Strata engines did not end: {exc}", file=sys.stderr, flush=True)


def supervise(plan_path, gate_fd):
    # EOF means the foreground exited before recording ownership.
    with os.fdopen(gate_fd, "rb") as gate:
        if gate.read(1) != b"1":
            return
    plan = read_state(plan_path)
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    key = read_key()
    if not key:
        raise ValueError("managed Strata requires the builder key")
    read_fd, write_fd = os.pipe()
    run = Path(plan["run"])
    argv = ["--engine", "strata", "--config", plan["snapshot"], "--host", "127.0.0.1",
            "--port", str(plan["port"]), "--parallel", "1", "--offline", "--ready-file", str(run / "ready.json"),
            "--shutdown-timeout", "1", "--owner-fd", str(read_fd)]
    attestation_key = secrets.token_bytes(32)
    plan["attestation"] = launch_mac(attestation_key, Path(plan["snapshot"]).read_bytes(), argv)
    plan.update(pid=os.getpid(), start=process_identity(os.getpid()))
    write_state(Path(plan["state"]), plan)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STRATA_", "PYTHON"))
           and not device_override(k)}
    # STRATA_NO_AUTO_CONVERSATION_CACHE stops the terminal from raising the conversation-cache budget
    # above the engine's own limit (8 GiB). The engine's automatic cache stays on: 40% of free RAM, capped at 8 GiB.
    env.update(STRATA_KEEP_LEFTOVERS="1", STRATA_NO_PRESET="1", STRATA_NO_ACQUIRE="1",
               STRATA_NO_AUTO_CONVERSATION_CACHE="1",
               STRATA_API_KEY=key, STRATA_OWNER_ATTEST_KEY=attestation_key.hex(),
               PYTHONDONTWRITEBYTECODE="1", ONEAPI_DEVICE_SELECTOR=plan["selector"], STRATA_HOME=str(run),
               STRATA_SETTINGS_FILE=str(run / "settings.json"), STRATA_PROFILE_DIR=str(run / "profiles"),
               STRATA_RUNTIME_DIR=str(run / "runtime"), STRATA_CONNECTION_DIR=str(run / "connections"),
               XDG_CONFIG_HOME=str(run / "xdg-config"), XDG_STATE_HOME=str(run / "xdg-state"),
               XDG_CACHE_HOME=str(run / "xdg-cache"), XDG_DATA_HOME=str(run / "xdg-data"))
    server = None
    gate_read, gate_write = os.pipe()
    try:
        if stopping:
            return
        command = [plan["python"], "-s", "-m", "serve.server", *argv]
        gate_command = [plan["python"], "-I", "-S", "-c", SERVER_GATE, str(gate_read)]
        server = subprocess.Popen([*gate_command, *command], cwd=plan["terminal"], env=env,
                                  pass_fds=(read_fd, gate_read), start_new_session=True)
        os.close(read_fd)
        read_fd = None
        os.close(gate_read)
        gate_read = None
        plan.update(server_pid=server.pid, server_start=process_identity(server.pid))
        if plan["server_start"] is None:
            raise ValueError("Strata server exited before ownership was recorded")
        write_state(Path(plan["state"]), plan)
        if stopping:
            return
        os.write(gate_write, b"1")
        os.close(gate_write)
        gate_write = None
        while server.poll() is None and not stopping:
            ok, avail = ram_check(plan["min_ram_mib"])
            if not ok:
                if plan.get("floor_file"):
                    write_floor_stop(
                        "running", plan["min_ram_mib"], plan.get("id"), plan.get("floor_file"),
                        available_mib=avail,
                    )
                print("Strata stopped: host MemAvailable fell below the configured floor", file=sys.stderr, flush=True)
                break
            time.sleep(0.25)
    finally:
        for fd in (read_fd, gate_read, gate_write):
            if fd is not None:
                os.close(fd)
        os.close(write_fd)  # EOF requests Strata's owner-guard shutdown.
        if server is not None:
            end_launch(server, plan)


def start(path, config, ctx, timeout):
    doc, evidence = prepare_config(config)
    terminal, python = source_inputs()
    require_selected_card(evidence["selector"])
    if os.environ.get("A770B_HOST") != "127.0.0.1" or ctx < 1:
        raise ValueError("managed Strata requires numeric loopback and a positive context")
    port = int(os.environ["A770B_PORT"])
    minimum = int(os.environ["A770B_MIN_AVAIL_MB"])
    if not 1 <= port <= 65535 or minimum < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid port, host memory floor or ready timeout")
    floor_path = os.environ.get("A770B_STRATA_FLOOR_FILE") or None
    ok, avail = ram_check(minimum)
    if not ok:
        write_floor_stop("launch", minimum, None, floor_path, available_mib=avail)
        raise ValueError("host MemAvailable is below the configured floor")
    write_enforcement_receipt("launch")
    key = read_key()
    if not key:
        raise ValueError("managed Strata requires the builder key")
    old = read_state(path)
    if old and occupied(old):
        same = launch_evidence_matches(old, evidence)
        requested = (port, ctx, minimum, str(python), str(terminal))
        recorded = (old["port"], old["ctx"], old["min_ram_mib"], old["python"], old["terminal"])
        if same and recorded == requested and process_alive(old) and health_matches(old, key):
            try:
                checked = check_engine_args(old)
            except ValueError as exc:
                raise ValueError(f"{exc}; the reused Strata instance is left running") from None
            evidence_path = Path(old["run"]) / "evidence.json"
            written = read_state(evidence_path)
            if written is not None:
                write_state(evidence_path, {**written, **checked})
            return old
        raise ValueError("an owned Strata server is up with a different launch or health; stop it explicitly")
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    instance = secrets.token_hex(16)
    run = path.parent / f"strata-{instance}"
    run.mkdir(mode=0o700)
    snapshot = run / "config.json"
    doc["log"] = str(run / "engine.log")
    snapshot.write_text(json.dumps(doc))
    plan = {**evidence, "id": instance, "state": str(path), "snapshot": str(snapshot), "ctx": ctx, "port": port,
            "python": str(python), "terminal": str(terminal), "min_ram_mib": minimum, "run": str(run)}
    if floor_path:
        plan["floor_file"] = floor_path
    plan_path = run / "launch.json"
    write_state(plan_path, plan)
    gate_read = gate_write = proc = None
    previous = {}
    try:
        try:
            for signum in INTERRUPTS:
                previous[signum] = signal.signal(signum, interrupt)
            gate_read, gate_write = os.pipe()
            with (run / "server.log").open("ab") as log:
                proc = subprocess.Popen([sys.executable, __file__, "supervise", str(plan_path),
                                         "--gate-fd", str(gate_read)], start_new_session=True,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, pass_fds=(gate_read,))
            os.close(gate_read)
            gate_read = None
            identity = process_identity(proc.pid)
            if identity is None:
                raise ValueError("Strata supervisor exited before launch")
            write_state(path, {**plan, "pid": proc.pid, "start": identity})
            owned = os.environ.get("A770B_STRATA_OWNED_FILE")
            if owned:
                record_owned(owned, instance)
            os.write(gate_write, b"1")
            os.close(gate_write)
            gate_write = None
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and proc.poll() is None:
                state = read_state(path)
                if state and state["id"] == instance and process_alive(state) and health_matches(state, key):
                    checked = check_engine_args(state)
                    write_state(run / "evidence.json", {**evidence, **checked, "ctx": ctx, "port": port, "log_dir": str(run),
                               "host_memavailable_floor_mib": minimum, "card_mode": os.environ.get("A770B_CARD_MODE"),
                               "configured_vram_cap_gib": os.environ.get("A770B_VRAM_CAP_GIB"),
                               "gpu_match": os.environ.get("A770B_GPU_MATCH")})
                    return state
                time.sleep(0.25)
            raise ValueError(f"owned Strata failed readiness; see {run / 'server.log'}")
        except BaseException:
            for signum in previous:
                signal.signal(signum, signal.SIG_IGN)
            # A supervisor still waiting on the gate exits on this EOF.
            close_quietly(gate_write)
            gate_write = None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass  # stop() below escalates.
            state = read_state(path)
            if state and state["id"] == instance:
                stop(path, instance)
            raise
        finally:
            close_quietly(gate_read)
            close_quietly(gate_write)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)


def gate(config):
    """Check the checkout, the interpreter and the card, then name the engine digest."""
    _doc, evidence = prepare_config(Path(config))
    source_inputs()
    require_selected_card(evidence["selector"])
    print(f"strata@sha256:{evidence['engine_sha256']}")


def admit(path):
    """Record continuous floor enforcement only after the supervised server is verified ready."""
    state = read_state(path)
    key = read_key()
    if not state or not process_alive(state) or not health_matches(state, key):
        raise ValueError("managed Strata instance is not verified healthy")
    ok, avail = ram_check(state["min_ram_mib"])
    if not ok:
        write_floor_stop("admit", state["min_ram_mib"], state.get("id"), available_mib=avail)
        return False
    write_enforcement_receipt("continuous")
    return True


def check_floor():
    """Record a launch floor when MemAvailable is under A770B_MIN_AVAIL_MB. Internal."""
    minimum = int(os.environ["A770B_MIN_AVAIL_MB"])
    if minimum < 1:
        raise ValueError("invalid host memory floor")
    ok, avail = ram_check(minimum)
    if ok:
        return 0
    write_floor_stop(
        "launch", minimum, None,
        os.environ.get("A770B_STRATA_FLOOR_FILE") or None,
        available_mib=avail,
    )
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "status", "stop", "id", "pid", "model", "tokenizer",
                                             "config-model", "verify", "observe", "admit", "supervise",
                                             "stop-owned", "gate", "check-floor"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--gate-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--ctx", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    try:
        if args.command == "check-floor":
            return check_floor()
        if args.command == "supervise":
            if args.gate_fd is None:
                raise ValueError("supervisor requires foreground launch authorization")
            supervise(Path(args.value), args.gate_fd)
            return 0
        if args.command in ("tokenizer", "config-model"):
            config, _ = prepare_config(Path(args.value).resolve())
            print(config["tokenizer" if args.command == "tokenizer" else "model_name"])
            return 0
        if args.command == "observe":
            print(observe_host_memory(Path(args.value).resolve()))
            return 0
        if args.command == "gate":
            if not args.value:
                raise ValueError("gate needs a prepared Strata config")
            gate(Path(args.value))
            return 0
        path = Path(os.environ["A770B_DATA"]).resolve() / "logs/strata-instance.json"
        if args.command == "admit":
            return 0 if admit(path) else 1
        if args.command == "start":
            state = start(path, Path(args.value).resolve(), args.ctx, args.timeout)
            print(Path(state["run"]) / "evidence.json")
            return 0
        if args.command == "stop":
            stop(path, args.value)
            return 0
        if args.command == "stop-owned":
            # stop() with no id would stop any instance.
            if not args.value:
                raise ValueError("stop-owned needs an instance id")
            stop_owned(path, args.value)
            return 0
        state = read_state(path)
        if not state or not occupied(state):
            return 1
        if args.command in ("id", "model"):
            print(state["id" if args.command == "id" else "model_id"])
            return 0
        if args.command == "pid":
            if not server_alive(state):
                return 1
            print(state["server_pid"])
            return 0
        ready = process_alive(state) and health_matches(state, read_key())
        if args.command == "verify":
            if ready:
                check_engine_args(state)
            return 0 if ready and state["port"] == int(os.environ["A770B_PORT"]) and \
                state["model_id"] == args.value and state["ctx"] == args.ctx else 2
        print(f"server: {'UP' if ready else 'STARTING OR UNHEALTHY'} · Strata · {state['model_id']} · 127.0.0.1:{state['port']}")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"managed Strata: {exc}", file=sys.stderr)
        return 2
    except Interrupted as exc:
        print("managed Strata: start was interrupted", file=sys.stderr)
        return 128 + exc.signum


if __name__ == "__main__":
    sys.exit(main())
