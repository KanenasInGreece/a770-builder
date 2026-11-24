"""Tests for harness/host_memory.py — the host RAM and swap conditions a ladder run records.

What these hold still:

  * every value names its source and unit; a value the source does not carry is None, never 0, and a measured
    0 (no swap configured, a cgroup swap limit of 0) stays 0;
  * a cgroup limit of "max" is "max" (no limit), distinct from a missing file (not read);
  * configured limits (the harness's own environment) are kept apart from sampled host values;
  * nothing infers the RAM a model needs, or a process's RSS, from MemAvailable.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_MEMORY = ROOT / "harness" / "host_memory.py"
sys.path.insert(0, str(ROOT / "harness"))
import host_memory  # noqa: E402

MEMINFO = """\
MemTotal:       65738732 kB
MemFree:         1203344 kB
MemAvailable:   41234560 kB
Buffers:          123456 kB
SwapTotal:             0 kB
SwapFree:              0 kB
"""

MEMINFO_NO_SWAP_LINES = """\
MemTotal:       16384000 kB
MemAvailable:    8192000 kB
"""


def _cgroup_tree(tmp_path: Path, files: dict) -> tuple[Path, Path]:
    root = tmp_path / "cgroup"
    scope = root / "user.slice" / "trial.scope"
    scope.mkdir(parents=True)
    for name, text in files.items():
        (scope / name).write_text(text, encoding="ascii")
    proc = tmp_path / "proc-self-cgroup"
    proc.write_text("0::/user.slice/trial.scope\n", encoding="ascii")
    return root, proc


def test_meminfo_keeps_a_measured_zero_swap_apart_from_a_missing_line(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO, encoding="ascii")
    got = host_memory.read_meminfo(path)
    assert got["source"] == str(path) and got["unit"] == "KiB"
    assert got["mem_total"] == 65738732 and got["mem_available"] == 41234560
    assert got["swap_total"] == 0 and got["swap_free"] == 0
    assert type(got["swap_total"]) is int

    path.write_text(MEMINFO_NO_SWAP_LINES, encoding="ascii")
    got = host_memory.read_meminfo(path)
    assert got["swap_total"] is None and got["swap_free"] is None
    assert got["mem_total"] == 16384000


def test_unreadable_meminfo_is_missing_not_zero(tmp_path):
    got = host_memory.read_meminfo(tmp_path / "absent")
    assert got["mem_total"] is None and got["mem_available"] is None and got["swap_total"] is None


def test_cgroup_limits_keep_max_zero_and_missing_distinct(tmp_path):
    root, proc = _cgroup_tree(tmp_path, {"memory.max": "38654705664\n", "memory.swap.max": "0\n",
                                         "memory.high": "max\n"})
    got = host_memory.read_cgroup(proc, root)
    assert got["path"] == "/user.slice/trial.scope" and got["unit"] == "bytes"
    assert got["source"] == str(root / "user.slice" / "trial.scope")
    assert got["memory_max"] == 38654705664
    assert got["memory_swap_max"] == 0 and type(got["memory_swap_max"]) is int
    assert got["memory_high"] == "max"

    root2, proc2 = _cgroup_tree(tmp_path / "two", {"memory.max": "max\n"})
    got = host_memory.read_cgroup(proc2, root2)
    assert got["memory_max"] == "max"
    assert got["memory_swap_max"] is None and got["memory_high"] is None


def test_no_cgroup_v2_line_reads_as_not_read(tmp_path):
    proc = tmp_path / "proc-self-cgroup"
    proc.write_text("12:memory:/legacy\n", encoding="ascii")
    assert host_memory.read_cgroup(proc, tmp_path) is None
    assert host_memory.read_cgroup(tmp_path / "absent", tmp_path) is None


def test_configured_limits_come_from_the_environment_and_stay_none_when_unset():
    got = host_memory.configured({"A770B_MIN_AVAIL_MB": "10240", "A770B_VRAM_CAP_GIB": "28.0",
                                  "A770B_CARD_MODE": "inference"})
    assert got["source"] == "environment"
    assert got["A770B_MIN_AVAIL_MB"] == {"value": 10240, "unit": "MiB", "enforcement": "none"}
    assert got["A770B_VRAM_CAP_GIB"] == {"value": 28.0, "unit": "GiB"}
    assert got["A770B_CARD_MODE"] == "inference"
    enforced = host_memory.configured({}, min_floor_mib=20000, min_floor_enforcement="launch")
    assert enforced["A770B_MIN_AVAIL_MB"] == {"value": 20000, "unit": "MiB", "enforcement": "launch"}
    empty = host_memory.configured({})
    assert empty["A770B_MIN_AVAIL_MB"] is None and empty["A770B_VRAM_CAP_GIB"] is None
    assert empty["A770B_CARD_MODE"] is None
    assert host_memory.configured({"A770B_MIN_AVAIL_MB": "lots"})["A770B_MIN_AVAIL_MB"] is None
    assert host_memory.configured({}, min_floor_mib=20000, min_floor_enforcement="unknown")[
        "A770B_MIN_AVAIL_MB"]["enforcement"] == "unknown"


def test_monitor_reads_launch_guard_receipt_only_after_builtin_gate_records_it(tmp_path):
    out = tmp_path / "samples.jsonl"
    receipt = tmp_path / "guard.json"
    proc = subprocess.Popen(
        [sys.executable, str(HOST_MEMORY), "monitor", "--out", str(out), "--interval", "0.05",
         "--min-floor-mib", "1024", "--min-floor-enforcement", "unknown", "--enforcement-file", str(receipt)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not out.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert out.exists()
        receipt.write_text('{"enforcement":"launch"}\n', encoding="utf-8")
        time.sleep(0.12)
        receipt.write_text('{"enforcement":"continuous"}\n', encoding="utf-8")
        time.sleep(0.12)
    finally:
        proc.terminate()
        proc.wait(timeout=3)
    samples = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    modes = [sample["configured"]["A770B_MIN_AVAIL_MB"]["enforcement"] for sample in samples]
    assert "unknown" in modes
    assert "launch" in modes
    assert "continuous" in modes
    summary = host_memory.conditions_from_samples(samples)
    assert summary["configured"]["A770B_MIN_AVAIL_MB"]["enforcement"] == "continuous"
    assert "guard enforced continuously" in host_memory.clause(summary)


def test_requested_endpoint_listener_resolution_is_independent_of_managed_pid():
    listener = 'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("python",pid=4321,fd=9))'
    assert host_memory.endpoint_listener_pid("http://127.0.0.1:8000/v1", listener) == 4321
    assert host_memory.endpoint_listener_pid("http://localhost:8000/v1", listener) == 4321
    assert host_memory.endpoint_listener_pid("http://127.0.0.1:9000/v1", listener) is None
    assert host_memory.endpoint_listener_pid("http://model.example:8000/v1", listener) is None
    assert host_memory.endpoint_listener_pid("http://192.168.1.5:8000/v1", listener) is None
    assert host_memory.endpoint_listener_pid("not-an-url", listener) is None


def test_endpoint_listener_resolution_matches_address_family_and_refuses_ambiguous_localhost():
    wrong_address_first = '\n'.join([
        'LISTEN 0 128 127.0.0.2:8000 0.0.0.0:* users:(("python",pid=1111,fd=9))',
        'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("python",pid=2222,fd=9))',
    ])
    assert host_memory.endpoint_listener_pid("http://127.0.0.1:8000/v1", wrong_address_first) == 2222

    ipv6_only = 'LISTEN 0 128 [::1]:8000 [::]:* users:(("python",pid=3333,fd=9))'
    assert host_memory.endpoint_listener_pid("http://127.0.0.1:8000/v1", ipv6_only) is None
    assert host_memory.endpoint_listener_pid("http://[::1]:8000/v1", ipv6_only) == 3333

    both_families = '\n'.join([ipv6_only,
        'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("python",pid=4444,fd=9))'])
    assert host_memory.endpoint_listener_pid("http://localhost:8000/v1", both_families) is None
    assert host_memory.endpoint_listener_pid("http://127.0.0.1:8000/v1", both_families) == 4444


def test_localhost_requires_its_exact_addresses_and_every_matching_socket_owner():
    unrelated_loopback = 'LISTEN 0 128 127.0.0.2:8000 0.0.0.0:* users:(("llama-server",pid=4291,fd=3))'
    assert host_memory.endpoint_listener_pid("http://localhost:8000", unrelated_loopback) is None
    ambiguous_owner = "\n".join((
        'LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("llama-server",pid=4291,fd=3))',
        "LISTEN 0 128 [::1]:8000 [::]:*",
    ))
    assert host_memory.endpoint_listener_pid("http://localhost:8000", ambiguous_owner) is None


def test_sample_cli_writes_one_json_document(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEMINFO, encoding="ascii")
    root, proc = _cgroup_tree(tmp_path, {"memory.max": "max\n", "memory.swap.max": "max\n"})
    out = tmp_path / "sample.json"
    result = subprocess.run(
        [sys.executable, str(HOST_MEMORY), "sample", "--out", str(out), "--meminfo", str(meminfo),
         "--proc-cgroup", str(proc), "--cgroup-root", str(root)],
        capture_output=True, text=True, env={"A770B_MIN_AVAIL_MB": "20000", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["meminfo"]["mem_total"] == 65738732
    assert doc["collector_cgroup"]["memory_max"] == "max"
    assert doc["configured"]["A770B_MIN_AVAIL_MB"] == {"value": 20000, "unit": "MiB", "enforcement": "none"}
    assert isinstance(doc["sampled_at"], str) and doc["sampled_at"]


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _sample(mem_available, swap_free=0, cgroup=None, mem_total=65738732, swap_total=0):
    return {
        "sampled_at": "2026-11-23T10:00:00",
        "meminfo": {"source": "/proc/meminfo", "unit": "KiB", "mem_total": mem_total,
                    "mem_available": mem_available, "swap_total": swap_total, "swap_free": swap_free},
        "collector_cgroup": cgroup,
        "target": None,
        "configured": host_memory.configured({"A770B_MIN_AVAIL_MB": "10240", "A770B_VRAM_CAP_GIB": "28.0"}),
    }


def test_conditions_record_both_samples_and_say_what_they_cover():
    before = _sample(41234560)
    after = _sample(30000000)
    got = host_memory.conditions(before, after, None)
    hm = got["host_memory"]
    assert hm["before_load"]["mem_available"] == 41234560 and hm["after_load"]["mem_available"] == 30000000
    assert "before" in hm["coverage"] and "not a peak" in hm["coverage"]
    assert hm["memavailable_drop_kib"] == 41234560 - 30000000
    assert got["configured"]["A770B_MIN_AVAIL_MB"]["value"] == 10240
    # MemAvailable is host-wide: no field is presented as a process RSS or as the RAM the model requires
    assert not any(("rss" in k.lower() or "requir" in k.lower()) for k in _keys(got)), sorted(_keys(got))
    assert "host-wide" in hm["note"] and "not a process RSS" in hm["note"]


def test_conditions_leave_the_drop_unset_when_a_sample_is_missing():
    got = host_memory.conditions(_sample(41234560), None, None)
    assert got["host_memory"]["after_load"] is None
    assert got["host_memory"]["memavailable_drop_kib"] is None
    assert host_memory.conditions(None, None, None) is None
    # a sample file that parsed to something other than an object is not a sample
    assert host_memory.conditions([1, 2], "x", None) is None


def test_engine_settings_are_labelled_configured_not_observed():
    settings = {"env": {"STRATA_MIRROR_MIB": "28672"}, "args": {"--expert-cache": "auto", "--ple-io": "mmap"}}
    got = host_memory.conditions(None, None, settings)
    assert got["host_memory"] is None
    assert got["engine_host_memory"]["settings"] == settings
    assert "configured" in got["engine_host_memory"]["source"]


def test_strata_observed_pinned_memory_is_reported_separately_from_configured_settings():
    observed = {"value_bytes": round(27.70 * 1024 ** 3), "source": "engine.log",
                "reported_text": "mirrored in 27.70 GiB of pinned host RAM"}
    got = host_memory.conditions(None, None, {"settings": {"env": {"STRATA_MIRROR_MIB": "28672"}, "args": {}},
                                               "observed_pinned_host_memory": observed})
    engine = got["engine_host_memory"]
    assert engine["settings"]["env"]["STRATA_MIRROR_MIB"] == "28672"
    assert engine["observed_pinned_host_memory"] == observed
    assert "Strata reported pinned host RAM 27.70 GiB" in host_memory.clause(got)


def test_clause_is_concise_and_does_not_attribute_collector_cgroup_to_server():
    cgroup = {"source": "/sys/fs/cgroup/user.slice/trial.scope", "path": "/user.slice/trial.scope",
              "unit": "bytes", "memory_max": 36 * 1024 ** 3, "memory_swap_max": 0, "memory_high": "max"}
    settings = {"env": {"STRATA_MIRROR_MIB": "28672"}, "args": {"--expert-cache": "auto"}}
    text = host_memory.clause(host_memory.conditions(_sample(41234560, cgroup=cgroup), _sample(30000000), settings))
    assert "RAM 62.7 GiB" in text and "swap 0.0 GiB" in text
    assert "serving cgroup not measured" in text
    assert "MemAvailable floor 10240 MiB (configured; not enforced)" in text
    assert "STRATA_MIRROR_MIB=28672" in text and "--expert-cache auto" in text

    no_swap_line = _sample(8192000, swap_free=None, mem_total=16384000, swap_total=None)
    text = host_memory.clause(host_memory.conditions(no_swap_line, None, None))
    assert "swap not read" in text and "swap 0.0 GiB" not in text
    assert "serving cgroup not measured" in text

    unlimited = dict(cgroup, memory_max="max", memory_swap_max="max")
    text = host_memory.clause(host_memory.conditions(_sample(1, cgroup=unlimited), None, None))
    assert "serving cgroup not measured" in text
    assert host_memory.clause(None) == ""


def test_continuous_samples_summarize_host_minima_and_serving_cgroup_only():
    serving = {"source": "/sys/fs/cgroup/docker/abc", "path": "/docker/abc", "unit": "bytes",
               "memory_max": 36 * 1024 ** 3, "memory_swap_max": 0, "memory_high": "max",
               "memory_current": 24 * 1024 ** 3}
    samples = [
        {**_sample(41234560, swap_free=8192000), "target": {"pid": 10, "process": "llama-server", "cgroup": serving}},
        {**_sample(30000000, swap_free=7000000), "target": {"pid": 10, "process": "llama-server",
                                                                  "cgroup": {**serving, "memory_current": 26 * 1024 ** 3}}},
    ]
    got = host_memory.conditions_from_samples(samples, source="run.host-memory.jsonl")
    hm = got["host_memory"]
    assert hm["samples"] == 2 and hm["min_mem_available_kib"] == 30000000
    assert hm["min_swap_free_kib"] == 7000000
    assert hm["serving_cgroup"]["max_sampled_memory_current_bytes"] == 26 * 1024 ** 3
    assert hm["serving_cgroup"]["cgroup"]["memory_max"] == 36 * 1024 ** 3
    text = host_memory.clause(got)
    assert "serving cgroup memory.max 36.0 GiB" in text and "memory.swap.max 0.0 GiB" in text
    assert "minimum MemAvailable" in text and "minimum SwapFree" in text
    assert "not a process RSS" in hm["note"]


def test_continuous_samples_never_substitute_the_collector_cgroup():
    collector = {"source": "/sys/fs/cgroup/user.slice/ssh.scope", "path": "/user.slice/ssh.scope",
                 "unit": "bytes", "memory_max": 99 * 1024 ** 3, "memory_swap_max": "max"}
    sample = {**_sample(30000000), "collector_cgroup": collector, "target": None}
    got = host_memory.conditions_from_samples([sample])
    assert got["host_memory"]["serving_cgroup"] is None
    assert "serving cgroup not measured" in host_memory.clause(got)
    assert "99.0 GiB" not in host_memory.clause(got)


def test_dead_serving_pid_after_live_sample_does_not_break_summary():
    first = {**_sample(41234560), "target": {"pid": 10, "process": "llama-server", "cgroup": None}}
    exited = {**_sample(30000000), "target": {"pid": 10, "process": None, "cgroup": None}}
    got = host_memory.conditions_from_samples([first, exited], source="run.host-memory.jsonl")
    assert got["host_memory"]["samples"] == 2
    assert {tuple(item) for item in got["host_memory"]["serving_cgroup"]["processes"]} == {
        (10, "llama-server"), (10, None),
    }


def test_serving_pid_is_checked_against_selected_engine_before_cgroup_attribution(tmp_path):
    proc = tmp_path / "123"
    proc.mkdir()
    (proc / "comm").write_text("python3.12\n", encoding="ascii")
    (proc / "cmdline").write_bytes(b"python\0-m\0serve.server\0--engine\0strata\0")
    assert host_memory.process_matches_engine(123, "strata", tmp_path)
    assert not host_memory.process_matches_engine(123, "llama.cpp", tmp_path)
    (proc / "cmdline").write_bytes(b"python\0-m\0unrelated.proxy\0")
    assert not host_memory.process_matches_engine(123, "strata", tmp_path)


def test_missing_continuous_samples_are_reported_as_missing_not_omitted():
    got = host_memory.conditions_from_samples([], source="missing.jsonl")
    assert got["host_memory"]["samples"] == 0
    assert got["host_memory"]["sample_log"] == "missing.jsonl"
    assert "RAM not read" in host_memory.clause(got)


def test_monitor_appends_timestamped_host_samples_and_target_identity(tmp_path):
    pid_file = tmp_path / "serving.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
    out = tmp_path / "host.jsonl"
    proc = subprocess.Popen([sys.executable, str(HOST_MEMORY), "monitor", "--out", str(out), "--interval", "0.02",
                             "--pid-file", str(pid_file)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        import time
        time.sleep(0.08)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 2
    assert all(row["meminfo"]["mem_available"] is not None for row in rows)
    assert all(row["target"]["pid"] == os.getpid() for row in rows)
    assert all("collector_cgroup" in row and "cgroup" in row["target"] for row in rows)
