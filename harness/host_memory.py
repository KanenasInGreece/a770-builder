#!/usr/bin/env python3
"""host_memory.py — the host RAM and swap conditions a ladder run records beside its model, backend and VRAM.

    host_memory.py sample --out <file> [--pid PID]
    host_memory.py monitor --out <jsonl> [--interval SECONDS] [--pid-file FILE]

`sample` writes one JSON document and `monitor` writes one per line until its owner stops it: kernel `/proc/meminfo`
totals (KiB), the collector cgroup clearly labelled as such, the serving process's cgroup v2 memory limits and
current charge (bytes) when its PID is known, the sandbox slice when that directory exists, and the configured
limits. The ladder runs the monitor before load
through the task rung. `ladder_row.py` folds the summary and receipt path into run JSON with
`conditions_from_samples()` and into `measured_on` with `clause()`.

Rules every value here keeps:

  * a value the source does not carry is None — never 0. A measured 0 (no swap, a swap limit of 0) stays 0;
  * a cgroup limit of "max" is no limit, which is not the same as a file that was not read (None);
  * configured limits (`configured`) are kept apart from sampled host values (`meminfo`, `target.cgroup`);
  * MemAvailable is the kernel's host-wide estimate. Its drop across a load is not a process RSS and not the
    RAM a model needs, and nothing here derives either from it. `ram_gb_extra` is the engine's own figure;
  * collector cgroup values are not described as the server's limits; only `target.cgroup` follows a serving PID.
"""

import argparse
import datetime
import ipaddress
import json
import os
import re
import sys
import time
from pathlib import Path

MEMINFO_FIELDS = {"MemTotal": "mem_total", "MemAvailable": "mem_available",
                  "SwapTotal": "swap_total", "SwapFree": "swap_free"}
CGROUP_FILES = {"memory.max": "memory_max", "memory.swap.max": "memory_swap_max",
                "memory.high": "memory_high", "memory.current": "memory_current"}
NOTE = ("MemAvailable is the kernel's host-wide estimate, moved by page cache and every other process: its drop "
        "is not a process RSS and not the RAM the model needs; ram_gb_extra is the engine's own figure")
NOTE = ("MemAvailable is the kernel's host-wide estimate, moved by page cache and every other process: its drop "
        "is not a process RSS and not the RAM the model needs; ram_gb_extra is the engine's own figure")
KIB_PER_GIB = 1024 ** 2
BYTES_PER_GIB = 1024 ** 3


def read_meminfo(path="/proc/meminfo") -> dict:
    """MemTotal, MemAvailable, SwapTotal and SwapFree in KiB; a line the file lacks (or an unreadable file) is None."""
    out = {"source": str(path), "unit": "KiB", **{key: None for key in MEMINFO_FIELDS.values()}}
    try:
        text = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        key = MEMINFO_FIELDS.get(name.strip())
        parts = rest.split()
        if key and parts and parts[0].isdigit():
            out[key] = int(parts[0])
    return out


def _cgroup_value(path: Path):
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if text == "max":
        return "max"
    return int(text) if text.isdigit() else None


def sandbox_slice_dir(cgroup_root="/sys/fs/cgroup", uid=None) -> Path:
    """Directory of a770b-sandbox.slice under this uid's user manager.

    systemd nests a slice name, so the unit lives under a770b.slice and not
    directly under user@uid.service. The caller treats a missing directory as
    null; this function only builds the path.
    """
    uid = os.getuid() if uid is None else int(uid)
    return (Path(cgroup_root) / "user.slice" / f"user-{uid}.slice"
            / f"user@{uid}.service" / "a770b.slice" / "a770b-sandbox.slice")


def read_sandbox_slice(cgroup_root="/sys/fs/cgroup", uid=None):
    """Memory files of the sandbox slice, or None when the directory is absent.

    A missing directory is not a measured 0. A present memory.current of 0 is.
    """
    directory = sandbox_slice_dir(cgroup_root, uid)
    if not directory.is_dir():
        return None
    out = {"source": str(directory), "unit": "bytes"}
    for name, key in CGROUP_FILES.items():
        out[key] = _cgroup_value(directory / name)
    return out


def read_cgroup(proc_cgroup="/proc/self/cgroup", root="/sys/fs/cgroup"):
    """The cgroup v2 memory limits of this process, or None when there is no v2 entry to read."""
    try:
        lines = Path(proc_cgroup).read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    rel = next((line[3:] for line in lines if line.startswith("0::")), None)
    if not rel:
        return None
    directory = Path(root) / rel.lstrip("/")
    out = {"source": str(directory), "path": rel, "unit": "bytes"}
    for name, key in CGROUP_FILES.items():
        out[key] = _cgroup_value(directory / name)
    return out


def _number(text, kind):
    try:
        value = kind(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def configured(env=None, min_floor_mib=None, min_floor_enforcement="none") -> dict:
    """The limits the harness was configured with, as configured — not measured. Unset or unparsable is None."""
    env = os.environ if env is None else env
    floor = _number(min_floor_mib if min_floor_mib is not None else env.get("A770B_MIN_AVAIL_MB"), int)
    cap = _number(env.get("A770B_VRAM_CAP_GIB"), float)
    return {
        "source": "environment",
        "A770B_CARD_MODE": env.get("A770B_CARD_MODE") or None,
        "A770B_VRAM_CAP_GIB": None if cap is None else {"value": cap, "unit": "GiB"},
        # compared against MemAvailable / 1024, so MiB whatever the variable's name says
        "A770B_MIN_AVAIL_MB": None if floor is None else {
            "value": floor, "unit": "MiB", "enforcement": min_floor_enforcement,
        },
    }


def sample(meminfo="/proc/meminfo", proc_cgroup="/proc/self/cgroup", cgroup_root="/sys/fs/cgroup", env=None,
           target_pid=None, min_floor_mib=None, min_floor_enforcement="none") -> dict:
    target = None
    if target_pid is not None:
        try:
            pid = int(target_pid)
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
            target = {"pid": pid, "process": comm,
                      "cgroup": read_cgroup(f"/proc/{pid}/cgroup", cgroup_root)}
        except (OSError, ValueError):
            target = {"pid": int(target_pid), "process": None, "cgroup": None}
    return {
        "sampled_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "meminfo": read_meminfo(meminfo),
        # Only target.cgroup describes the process serving the model. Keep collector scope explicit.
        "collector_cgroup": read_cgroup(proc_cgroup, cgroup_root),
        # Sandbox memory leaves the caller's cgroup. Absent is null, never 0.
        "sandbox_slice": read_sandbox_slice(cgroup_root),
        "target": target,
        "configured": configured(env, min_floor_mib, min_floor_enforcement),
    }


def process_matches_engine(pid, engine, proc_root="/proc") -> bool:
    """Return true only when the PID command line identifies the selected serving engine."""
    try:
        root = Path(proc_root) / str(int(pid))
        comm = (root / "comm").read_text(encoding="utf-8").strip().lower()
        argv = (root / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except (OSError, ValueError):
        return False
    engine = str(engine or "").strip().lower()
    if engine == "strata":
        return "serve.server" in argv and "--engine strata" in argv
    if engine in {"llama.cpp", "llama-cpp", "openvino"}:
        return comm in {"llama-server", "llama.cpp"} or "llama-server" in argv
    if engine == "vllm":
        return "vllm" in argv and ("api_server" in argv or "vllm serve" in argv)
    if engine == "sglang":
        return "sglang" in argv and ("launch_server" in argv or "http_server" in argv)
    return False


def endpoint_listener_pid(base_url, listeners):
    """Return the PID listening on the requested endpoint port, or None when it is not in `ss` output."""
    from urllib.parse import urlsplit

    try:
        endpoint = urlsplit(str(base_url))
        port = endpoint.port
    except ValueError:
        return None
    if port is None or not endpoint.hostname:
        return None
    endpoint_host = endpoint.hostname.lower()
    try:
        endpoint_address = ipaddress.ip_address(endpoint_host)
        if not endpoint_address.is_loopback:
            return None
        endpoint_addresses = {endpoint_address}
    except ValueError:
        if endpoint_host != "localhost":
            return None
        endpoint_address = None
        endpoint_addresses = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    # ss reports local listeners, not the remote host a client contacted. Only claim a
    # process for numeric loopback/localhost endpoints; remote and LAN URLs remain unknown.
    matching_pids = set()
    matched_unknown_owner = False
    for line in str(listeners or "").splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        local = fields[3]
        try:
            local_host, local_port_raw = local.rsplit(":", 1)
            local_port = int(local_port_raw)
        except (IndexError, ValueError):
            continue
        if local_port != port:
            continue
        local_host = local_host.strip("[]").lower()
        if local_host not in {"*", "0.0.0.0", "::"}:
            try:
                local_address = ipaddress.ip_address(local_host)
            except ValueError:
                continue
            if local_address not in endpoint_addresses:
                continue
        elif local_host != "*" and endpoint_host != "localhost":
            try:
                wildcard_family = 4 if local_host == "0.0.0.0" else 6
                if endpoint_address.version != wildcard_family:
                    continue
            except (AttributeError, ValueError):
                continue
        line_pids = {int(pid) for pid in re.findall(r"\bpid=([1-9][0-9]*)\b", line)}
        if not line_pids:
            matched_unknown_owner = True
        matching_pids.update(line_pids)
    return next(iter(matching_pids)) if len(matching_pids) == 1 and not matched_unknown_owner else None


def owner_alive(pid):
    """True while `pid` exists and is not a zombie. A dead owner never ran its exit trap to stop this monitor."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    fields = stat.rsplit(")", 1)[-1].split()
    return not fields or fields[0] not in {"Z", "X"}


def pid_starttime(pid, proc_root="/proc"):
    """Field 22 of /proc/<pid>/stat, or None. It tells a reused pid from the first one."""
    try:
        stat = (Path(proc_root) / str(int(pid)) / "stat").read_text(
            encoding="ascii", errors="replace")
    except (OSError, ValueError):
        return None
    # The command name may hold spaces; the numbered fields resume after its ")".
    fields = stat.rsplit(")", 1)[-1].split()
    return fields[19] if len(fields) > 19 else None


def monitor(out_path, interval=1.0, pid_file=None, meminfo="/proc/meminfo", cgroup_root="/sys/fs/cgroup",
            min_floor_mib=None, min_floor_enforcement="none", enforcement_file=None, owner_pid=None):
    """Append timestamped host and serving-process samples until the caller stops this monitor PID.

    With `owner_pid`, the monitor also ends on its own once that process is gone.
    """
    seen_pid = seen_start = None
    with open(out_path, "a", encoding="utf-8", buffering=1) as stream:
        while True:
            if owner_pid is not None and not owner_alive(owner_pid):
                return
            enforcement = min_floor_enforcement
            if enforcement_file:
                try:
                    receipt = json.loads(Path(enforcement_file).read_text(encoding="utf-8"))
                    recorded = receipt.get("enforcement") if isinstance(receipt, dict) else None
                    if enforcement == "unknown" and recorded in {"launch", "continuous"}:
                        enforcement = recorded
                except (OSError, ValueError):
                    pass
            target_pid = None
            if pid_file:
                try:
                    raw = Path(pid_file).read_text(encoding="ascii").strip()
                    target_pid = int(raw) if raw.isdigit() else None
                except OSError:
                    pass
            if target_pid is not None:
                started = pid_starttime(target_pid)
                if target_pid != seen_pid:
                    seen_pid, seen_start = target_pid, started
                elif started != seen_start:
                    # Another process now holds the number the pid file names.
                    target_pid = None
            stream.write(json.dumps(sample(meminfo, "/proc/self/cgroup", cgroup_root,
                                           target_pid=target_pid, min_floor_mib=min_floor_mib,
                                           min_floor_enforcement=enforcement),
                                   sort_keys=True) + "\n")
            time.sleep(interval)


CONFIGURED_SOURCE = "the prepared Strata config (configured, not observed)"
OBSERVED_SOURCE = "Strata's own engine log (observed at run time, not configured)"


def _engine_memory_block(engine_settings):
    """Configured parameters and the observed allocation, each labelled with its own source; either may be absent.

    The wrapped form is {"settings": <dict|None>, "observed_pinned_host_memory": <dict|None>}; a bare dict is the
    earlier form, the configured settings alone.
    """
    if not isinstance(engine_settings, dict):
        return None
    if "settings" in engine_settings or "observed_pinned_host_memory" in engine_settings:
        settings = engine_settings.get("settings")
        observed = engine_settings.get("observed_pinned_host_memory")
    else:
        settings, observed = engine_settings, None
    settings = settings if isinstance(settings, dict) else None
    observed = observed if isinstance(observed, dict) else None
    if settings is None and observed is None:
        return None
    return {"source": CONFIGURED_SOURCE if settings is not None else None, "settings": settings,
            "observed_pinned_host_memory": observed,
            "observed_source": OBSERVED_SOURCE if observed is not None else None}


def conditions(before, after, engine_settings):
    """The run JSON's `conditions` block, or None when the run recorded neither a host sample nor engine settings."""
    before = before if isinstance(before, dict) else None
    after = after if isinstance(after, dict) else None
    engine_settings = engine_settings if isinstance(engine_settings, dict) else None
    if not before and not after and not engine_settings:
        return None
    first = before or after
    host = None
    if first:
        mb = (before or {}).get("meminfo") or {}
        ma = (after or {}).get("meminfo") or {}
        drop = None
        if isinstance(mb.get("mem_available"), int) and isinstance(ma.get("mem_available"), int):
            drop = mb["mem_available"] - ma["mem_available"]
        host = {
            "coverage": "before and after two-point samples only; not a peak",
            "before_load": (before or {}).get("meminfo"),
            "after_load": (after or {}).get("meminfo"),
            "memavailable_drop_kib": drop,
            "collector_cgroup": first.get("collector_cgroup", first.get("cgroup")),
            "note": NOTE,
        }
    engine = _engine_memory_block(engine_settings)
    return {
        "configured": (first or {}).get("configured"),
        "host_memory": host,
        "engine_host_memory": engine,
    }


def conditions_from_samples(samples, engine_settings=None, source=None):
    """Summarize a timestamped JSONL host-memory receipt without calling point samples a peak."""
    valid = [s for s in samples if isinstance(s, dict) and isinstance(s.get("meminfo"), dict)]
    engine_settings = engine_settings if isinstance(engine_settings, dict) else None
    if not valid and not engine_settings and not source:
        return None
    first = valid[0] if valid else {}
    host = None
    if valid:
        mem = [s["meminfo"] for s in valid]
        available = [m["mem_available"] for m in mem if isinstance(m.get("mem_available"), int)]
        swap_free = [m["swap_free"] for m in mem if isinstance(m.get("swap_free"), int)]
        target_samples = [s.get("target") for s in valid if isinstance(s.get("target"), dict)]
        target_groups = [t.get("cgroup") for t in target_samples if isinstance(t.get("cgroup"), dict)]
        target = None
        if target_samples:
            currents = [g.get("memory_current") for g in target_groups if isinstance(g.get("memory_current"), int)]
            target = {
                "processes": [list(item) for item in sorted(
                    {(t.get("pid"), t.get("process")) for t in target_samples},
                    key=lambda item: (item[0] if isinstance(item[0], int) else -1, item[1] or ""),
                )],
                "cgroup": target_groups[0] if target_groups else None,
                "max_sampled_memory_current_bytes": max(currents) if currents else None,
                "sampled_process_cgroup_charge_note": "cgroup charge may include sibling processes in the same serving cgroup",
            }
        host = {
            "coverage": "continuous timestamped samples across the ladder; each sample is host-wide, not per-process RAM",
            "samples": len(valid), "first_sampled_at": valid[0].get("sampled_at"),
            "last_sampled_at": valid[-1].get("sampled_at"), "sample_log": source,
            "mem_total_kib": first["meminfo"].get("mem_total"),
            "min_mem_available_kib": min(available) if available else None,
            "swap_total_kib": first["meminfo"].get("swap_total"),
            "min_swap_free_kib": min(swap_free) if swap_free else None,
            "serving_cgroup": target,
            "note": NOTE,
        }
    elif source:
        host = {"coverage": "continuous sampling was configured but produced no readable samples",
                "samples": 0, "sample_log": source, "serving_cgroup": None, "note": NOTE}
    configured_limits = dict(first.get("configured") or {}) if first else None
    floor = configured_limits.get("A770B_MIN_AVAIL_MB") if configured_limits else None
    if isinstance(floor, dict):
        enforcement = [((s.get("configured") or {}).get("A770B_MIN_AVAIL_MB") or {}).get("enforcement")
                       for s in valid]
        floor = dict(floor)
        if "continuous" in enforcement:
            floor["enforcement"] = "continuous"
        elif "launch" in enforcement:
            floor["enforcement"] = "launch"
        elif "unknown" in enforcement:
            floor["enforcement"] = "unknown"
        else:
            floor["enforcement"] = "none"
        configured_limits["A770B_MIN_AVAIL_MB"] = floor
    engine = _engine_memory_block(engine_settings)
    return {"configured": configured_limits,
            "host_memory": host, "engine_host_memory": engine}


def _gib(value, per_gib) -> str:
    if value == "max":
        return "max"
    return f"{value / per_gib:.1f} GiB"


def clause(cond) -> str:
    """One concise line for a row's measured_on: what the run recorded, and nothing it did not."""
    if not cond:
        return ""
    parts = []
    host = cond.get("host_memory")
    if host:
        legacy = "before_load" in host or "after_load" in host
        mem = host.get("before_load") or host.get("after_load") or {}
        total = host.get("mem_total_kib", mem.get("mem_total"))
        swap_total = host.get("swap_total_kib", mem.get("swap_total"))
        ram = "RAM not read" if total is None else f"RAM {_gib(total, KIB_PER_GIB)}"
        swap = "swap not read" if swap_total is None else f"swap {_gib(swap_total, KIB_PER_GIB)}"
        avail = host.get("min_mem_available_kib")
        free_swap = host.get("min_swap_free_kib")
        if legacy:
            available_values = [x.get("mem_available") for x in (host.get("before_load"), host.get("after_load"))
                                if isinstance(x, dict) and isinstance(x.get("mem_available"), int)]
            swap_values = [x.get("swap_free") for x in (host.get("before_load"), host.get("after_load"))
                           if isinstance(x, dict) and isinstance(x.get("swap_free"), int)]
            avail = min(available_values) if available_values else None
            free_swap = min(swap_values) if swap_values else None
        resource_min = []
        if avail is not None:
            resource_min.append(f"minimum MemAvailable {_gib(avail, KIB_PER_GIB)}")
        if free_swap is not None:
            resource_min.append(f"minimum SwapFree {_gib(free_swap, KIB_PER_GIB)}")
        samples = host.get("samples", "two point") if legacy else host.get("samples")
        parts.append(f"{ram}, {swap} ({host.get('coverage')}; {samples} samples" +
                     (f", {', '.join(resource_min)}" if resource_min else "") + ")")
        cg = (host.get("serving_cgroup") or {}).get("cgroup")
        if cg:
            limits = [f"{name} {'not read' if cg.get(key) is None else _gib(cg[key], BYTES_PER_GIB)}"
                      for name, key in (("memory.max", "memory_max"), ("memory.swap.max", "memory_swap_max"))]
            parts.append("serving cgroup " + ", ".join(limits))
        else:
            parts.append("serving cgroup not measured")
    floor = (cond.get("configured") or {}).get("A770B_MIN_AVAIL_MB")
    if floor:
        qualifier = {"continuous": "guard enforced continuously", "launch": "launch guard only",
                     "unknown": "enforcement unknown"}.get(
            floor.get("enforcement"), "configured; not enforced")
        parts.append(f"MemAvailable floor {floor['value']} MiB ({qualifier})")
    cap = (cond.get("configured") or {}).get("A770B_VRAM_CAP_GIB")
    if cap:
        mode = (cond.get("configured") or {}).get("A770B_CARD_MODE")
        parts.append(f"configured VRAM cap {cap['value']} GiB" + (f" ({mode} mode)" if mode else ""))
    engine = cond.get("engine_host_memory")
    if engine:
        settings = engine.get("settings") or {}
        knobs = [f"{k}={v}" for k, v in (settings.get("env") or {}).items()]
        knobs += [k if v is True else f"{k} {v}" for k, v in (settings.get("args") or {}).items()]
        if knobs:
            parts.append("Strata config " + ", ".join(knobs))
        pinned = engine.get("observed_pinned_host_memory")
        if isinstance(pinned, dict) and isinstance(pinned.get("value_bytes"), int):
            value = pinned["value_bytes"] / BYTES_PER_GIB
            parts.append(f"Strata reported pinned host RAM {value:.2f} GiB")
    return "host memory: " + "; ".join(parts) if parts else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample the host RAM and swap conditions of a run")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample", help="Write one host-memory sample as JSON")
    s.add_argument("--out", required=True)
    s.add_argument("--meminfo", default="/proc/meminfo")
    s.add_argument("--proc-cgroup", default="/proc/self/cgroup")
    s.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    s.add_argument("--pid", type=int, help="serving process whose cgroup will be recorded separately")
    s.add_argument("--min-floor-mib", type=int,
                   help="resolved A770B_MIN_AVAIL_MB value, including a shell-only default")
    s.add_argument("--min-floor-enforcement", choices=("none", "launch", "continuous"), default="none",
                   help="whether the run skipped the floor, checked it before launch, or supervises it continuously")
    m = sub.add_parser("monitor", help="Append continuous timestamped samples as JSONL")
    m.add_argument("--out", required=True)
    m.add_argument("--interval", type=float, default=1.0)
    m.add_argument("--pid-file", help="read the serving PID from this file on every sample")
    m.add_argument("--meminfo", default="/proc/meminfo")
    m.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    m.add_argument("--min-floor-mib", type=int,
                   help="resolved A770B_MIN_AVAIL_MB value, including a shell-only default")
    m.add_argument("--min-floor-enforcement", choices=("none", "unknown", "launch", "continuous"), default="none",
                   help="whether the floor was skipped, not verified, checked before launch, or supervised continuously")
    m.add_argument("--enforcement-file", help="atomically updated gate receipt that refines unknown to observed enforcement")
    m.add_argument("--owner-pid", type=int, help="end the monitor once this process (the run that started it) is gone")
    args = parser.parse_args()
    if args.command == "monitor":
        if args.interval <= 0:
            parser.error("--interval must be positive")
        try:
            monitor(args.out, args.interval, args.pid_file, args.meminfo, args.cgroup_root,
                    args.min_floor_mib, args.min_floor_enforcement, args.enforcement_file, args.owner_pid)
        except OSError as exc:
            print(f"host_memory: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        return 0
    doc = sample(args.meminfo, args.proc_cgroup, args.cgroup_root, target_pid=args.pid,
                 min_floor_mib=args.min_floor_mib, min_floor_enforcement=args.min_floor_enforcement)
    try:
        Path(args.out).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"host_memory: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
