"""Qualification runner with stubs. No card, no container, no systemd unit."""

import hashlib
import json
import os
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
QUALIFY = os.path.join(PROJECT, "harness", "qualify.sh")
SECRET = "qualify-sentinel-7f3a"
SKIP_BIN = {"systemd-run", "systemctl", "docker", "podman"}


def _min_row(engine):
    backend = {"llama.cpp": "vulkan", "vllm": "vllm", "sglang": "sglang", "strata": "strata"}[engine]
    row = {
        "model": "sample.gguf",
        "source": "card",
        "family": "fam",
        "architecture": "arch",
        "quant": "Q4_K_M",
        "kv": "f16",
        "flash_attention": "on",
        "reasoning": "off",
        "extra": "",
        "capability_source": "card",
        "use_for": "tests",
        "depth_probe_100k": "pass (3/3)",
        "category": "dense",
        "weight_class": "9b",
        "measured_on": "test card at 16384 served",
        "card": "a770",
        "backend": backend,
        "mode": "inference",
        "ctx": 16384,
        "useful_ctx": 16384,
        "timeout_s": 60,
        "vram_gib_after_load": 8.0,
        "ram_gb_extra": 1.0,
        "params_b": {"total": 9, "active": 9},
        "speed": {
            "decode_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
            "prefill_tps": {"8k": None, "32k": None, "64k": None, "100k": None},
        },
        "capability": {"published": 1},
        "task": "code-edit",
        "evidence": "suite pass at 16384",
        "engine": engine,
        "instrument": "SUITE-1@1",
    }
    if engine == "strata":
        row.pop("flash_attention")
    if engine == "sglang":
        row["tool_parser"] = "qwen"
        row["reasoning_parser"] = "qwen"
        row["kv"] = "fp8_e4m3"
    if engine == "vllm":
        row["tool_parser"] = "hermes"
    return row


def _wrap(row, name):
    return {"schema": 1, "card": "a770", "mode": "inference", "profiles": {name: row}}


STUB = textwrap.dedent(
    """\
    #!/bin/sh
    printf '%s\\n' "$0 $*" >> "${A770B_QUALIFY_ARGV:?}"
    base=$(basename "$0")
    case "$base" in
      a770b-qualify-suite)
        printf 'PROFILES %s\\n' "$A770B_PROFILES_FILE" >> "${A770B_QUALIFY_ARGV}"
        if [ -n "${A770B_QUALIFY_SUITE_SLEEP:-}" ]; then sleep "$A770B_QUALIFY_SUITE_SLEEP"; fi
        printf '%s\\n' '{"completed": true, "stages": [], "references": {"passed": 0, "attempts": 0}}'
        ;;
      a770b-qualify-serve)
        printf '%s\\n' '{"load_ok": true, "cid": "stubcid", "vram_after_load": 1.5, "vram_cap": 8.0}'
        ;;
      a770b-qualify-smoke)
        printf '%s\\n' '{"models": true, "completion": true, "tool": true}'
        ;;
      a770b-qualify-speed)
        if [ -n "${A770B_QUALIFY_SPEED_SLEEP:-}" ]; then sleep "$A770B_QUALIFY_SPEED_SLEEP"; fi
        printf '%s\\n' '{"rows": []}'
        ;;
      a770b-qualify-sweep) printf '%s\\n' '{"resets": 0}' ;;
      a770b-qualify-depth) printf '%s\\n' '{"passed": true, "score": "1/3"}' ;;
      a770b-qualify-gate) printf '%s\\n' "image sha256:0123456789abcdef" ;;
    esac
    exit 0
    """
)


def _secret_name(name):
    if name.endswith("_FILE"):
        return False
    return name.endswith(("KEY", "TOKEN", "SECRET", "PASSWORD")) or any(
        part in name for part in ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
    )


def _make_bin(path, scripts):
    path.mkdir()
    for directory in ("/usr/bin", "/bin"):
        for name in os.listdir(directory):
            if name in SKIP_BIN or name in scripts:
                continue
            src = os.path.join(directory, name)
            dest = path / name
            if dest.exists() or not os.path.isfile(src):
                continue
            os.symlink(src, dest)
    for name, body in scripts.items():
        dest = path / name
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.write_text(body)
        dest.chmod(0o755)


def _env(tmp, bindir):
    data = tmp / "data"
    models = tmp / "models"
    xdg = tmp / "xdg"
    tok = tmp / "tok"
    for path in (data, models, xdg, tok):
        path.mkdir()
    (models / "sample.gguf").write_text("weights")
    key = tmp / "api.key"
    key.write_text(SECRET + "\n")
    key.chmod(0o600)
    argv = tmp / "argv.log"
    argv.write_text("")
    env = {key: value for key, value in os.environ.items() if not _secret_name(key)}
    env.update(
        {
            "HOME": env.get("HOME", str(tmp)),
            "A770B_DATA": str(data),
            "A770B_PROJECT": PROJECT,
            "A770B_MODELS": str(models),
            "A770B_API_KEY_FILE": str(key),
            "A770B_QUALIFY_OPENCODE_VERSION": "0-test",
            "A770B_QUALIFY_KIT_PROMPT_TOKENS": "1000",
            "A770B_TOKENIZER_DIR": str(tok),
            "A770B_QUALIFY_ARGV": str(argv),
            "A770B_QUALIFY_STUB": "1",
            "A770B_LLAMA_IMAGE": "example.invalid/llama:test",
            "A770B_VLLM_IMAGE": "example.invalid/vllm:test",
            "A770B_SGLANG_IMAGE": "example.invalid/sglang:test",
            "XDG_CONFIG_HOME": str(xdg),
            "PATH": str(bindir),
            "A770B_HOST_LABEL": "test-host",
        }
    )
    for name in (
        "A770B_PROFILES_FILE",
        "A770B_CARD_MODE",
        "A770B_CORPUS_FILE",
        "A770B_QUALIFY_PRE",
        "A770B_QUALIFY_POST",
        "A770B_API_KEY",
        "A770B_QUALIFY_PAUSE_PHASE",
        "A770B_QUALIFY_HOLD_S",
        "A770B_QUALIFY_SUITE_SLEEP",
    ):
        env.pop(name, None)
    return env


def _scripts():
    names = ("gate", "serve", "suite", "smoke", "speed", "sweep", "depth", "stop", "memory")
    return {f"a770b-qualify-{name}": STUB for name in names}


@pytest.fixture
def box(tmp_path):
    bindir = tmp_path / "bin"
    _make_bin(bindir, _scripts())
    env = _env(tmp_path, bindir)
    yield env
    root = Path(env["A770B_DATA"]) / "qualify"
    if not root.exists():
        return
    for job in root.iterdir():
        status = job / "status.json"
        if not status.is_file():
            continue
        try:
            pid = json.loads(status.read_text()).get("pid")
        except ValueError:
            continue
        if not pid:
            continue
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def _gguf(env, extra=None):
    args = [
        "--engine",
        "llama.cpp",
        "--model",
        "sample.gguf",
        "--ctx",
        "16384",
        "--backend",
        "vulkan",
        "--tokenizer",
        env["A770B_TOKENIZER_DIR"],
    ]
    if extra:
        args.extend(extra)
    return args


def _run(env, args, timeout=90):
    return subprocess.run(
        ["bash", QUALIFY, *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _jobs(env):
    root = Path(env["A770B_DATA"]) / "qualify"
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _one(env):
    found = _jobs(env)
    assert len(found) == 1
    return found[0]


def _registry():
    reg = os.path.join(PROJECT, "config", "registry")
    names = sorted(os.listdir(reg))
    hashes = []
    for name in names:
        path = os.path.join(reg, name)
        if os.path.isfile(path) and not os.path.islink(path):
            digest = hashlib.sha256(path and open(path, "rb").read()).hexdigest()
            hashes.append(digest + " " + name)
    return names, hashes


def _wait_file(path, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def _wait_status(job, timeout=20, **expect):
    """Poll status.json. The terminal marker is written before that publish."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = json.loads((job / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        if all(last.get(key) == value for key, value in expect.items()):
            return last
        time.sleep(0.05)
    return last


def _hook(path, label, code=0):
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' {label} >> \"$1/hook.log\"\nexit {code}\n")
    path.chmod(0o755)


def test_i5_refusal_creates_no_job_directory(box, tmp_path):
    before = _jobs(box)
    refused = _run(box, ["start", *_gguf(box, ["--not-a-real-option"])])
    assert refused.returncode == 2
    assert _jobs(box) == before
    flag = "--metrics"
    refused = _run(box, ["start", "--foreground", *_gguf(box, ["--extra=" + flag])])
    assert refused.returncode == 2
    assert _jobs(box) == before
    data = Path(box["A770B_DATA"])
    (data / "logs").mkdir(exist_ok=True)
    lock = data / "logs" / "local-build.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        held = _run(box, ["start", "--foreground", *_gguf(box)])
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert held.returncode == 2
    assert _jobs(box) == before
    leader = tmp_path / "sandbox_run.sh"
    leader.write_text("#!/bin/bash\nsleep 30\n")
    leader.chmod(0o755)
    (data / "logs").mkdir(exist_ok=True)
    proc = subprocess.Popen(["bash", str(leader)], start_new_session=True)
    (data / "logs" / "run.pid").write_text(str(proc.pid) + "\n")
    try:
        busy = _run(box, ["start", "--foreground", *_gguf(box)])
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    assert busy.returncode == 2
    assert _jobs(box) == before


def test_i12_and_foreground_result(box):
    data = Path(box["A770B_DATA"])
    dry = _run(box, ["start", "--dry-run", *_gguf(box)])
    assert dry.returncode == 0
    assert "serve-argv:" in dry.stdout
    assert _jobs(box) == []
    assert not (data / "logs").exists()
    assert not (data / "results").exists()
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    job = _one(box)
    assert (job / "DONE").is_file()
    result = json.loads((job / "result.json").read_text())
    assert result["verdict"] == "needs-review"
    names = {item["name"] for item in result["checks"]}
    assert "usable-window-holds-kit" in names
    assert "no-compaction-storm" in names
    report = (job / "report.md").read_text()
    assert "needs-review" in report
    assert (job / "stage-start").is_file()
    assert (job / "stage-start").read_text().strip().isdigit()
    card = json.loads((job / "evidence" / "card.json").read_text())
    assert card["containers"] is None
    suite = next(line for line in (job / "evidence" / "argv.log").read_text().splitlines() if line.startswith("suite:"))
    assert "--fresh" in suite
    assert "--reasoning" in suite
    shown = _run(box, ["result", job.name])
    assert shown.returncode == 0
    assert "needs-review" in shown.stdout


def test_i14_and_i26_stub_serve_and_gate(box):
    import sys

    sys.path.insert(0, os.path.join(PROJECT, "harness"))
    import profiling_contract as pc

    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    job = _one(box)
    document = json.loads((job / "job.json").read_text())
    argv = document["argv"]
    for token in pc.flags("llama.cpp"):
        assert argv.count(token) == 1
    log = (job / "evidence" / "argv.log").read_text()
    serve = next(line for line in log.splitlines() if line.startswith("serve:"))
    for token in pc.flags("llama.cpp"):
        assert serve.split().count(token) == 1
    gate = next(line for line in log.splitlines() if line.startswith("gate:"))
    assert "example.invalid/llama:test" in gate
    assert "llama.cpp" in gate


def test_i8_registry_unchanged_for_both_engines(box, tmp_path):
    before = _registry()
    first = _run(box, ["start", "--foreground", *_gguf(box)])
    assert first.returncode == 0, first.stderr
    path = tmp_path / "vllm.json"
    path.write_text(json.dumps(_wrap(_min_row("vllm"), "sample-vllm")))
    second = _run(
        box,
        [
            "start",
            "--foreground",
            "--engine",
            "vllm",
            "--model",
            "sample-vllm",
            "--profiles-file",
            str(path),
            "--tokenizer",
            box["A770B_TOKENIZER_DIR"],
        ],
    )
    assert second.returncode == 0, second.stderr
    assert _registry() == before
    for job in _jobs(box):
        text = (job / "evidence" / "argv.log").read_text()
        document = json.loads((job / "job.json").read_text())
        assert "--base-url" not in text
        if document.get("engine") == "vllm":
            assert "scratch/profiles.json" in text


def test_i11_sglang_strata_and_missing_row(box, tmp_path):
    missing = _run(
        box,
        ["start", "--engine", "sglang", "--model", "missing-row", "--tokenizer", box["A770B_TOKENIZER_DIR"]],
    )
    assert missing.returncode == 2
    assert "no kit path" in missing.stderr
    assert _jobs(box) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    refused = _run(
        box,
        ["start", "--engine", "strata", "--model", "sample-row", "--profiles-file", str(bad)],
    )
    assert refused.returncode == 2
    assert _jobs(box) == []
    path = tmp_path / "sglang.json"
    path.write_text(json.dumps(_wrap(_min_row("sglang"), "sample-row")))
    done = _run(
        box,
        [
            "start",
            "--foreground",
            "--engine",
            "sglang",
            "--model",
            "sample-row",
            "--profiles-file",
            str(path),
            "--tokenizer",
            box["A770B_TOKENIZER_DIR"],
        ],
    )
    assert done.returncode == 0, done.stderr
    strata = tmp_path / "strata.json"
    strata.write_text(json.dumps(_wrap(_min_row("strata"), "sample-row")))
    before = set(Path(box["A770B_DATA"]).rglob("*"))
    refused_row = _run(
        box,
        [
            "start",
            "--dry-run",
            "--engine",
            "vllm",
            "--model",
            "sample-row",
            "--profiles-file",
            str(strata),
            "--tokenizer",
            box["A770B_TOKENIZER_DIR"],
        ],
    )
    assert refused_row.returncode == 2
    assert "not yet supported by qualify" in refused_row.stderr
    assert "manual ladder" in refused_row.stderr
    assert set(Path(box["A770B_DATA"]).rglob("*")) == before
    assert len(_jobs(box)) == 1


def test_i4_post_hook_and_term_during_suite_sleep(box, tmp_path):
    plain = _run(box, ["start", "--foreground", *_gguf(box)])
    assert plain.returncode == 0, plain.stderr
    result = json.loads((_one(box) / "result.json").read_text())
    post = next(item for item in result["checks"] if item["name"] == "post-hook-ok")
    assert post["result"] == "unknown"
    assert post["reason"] == "not-run"

    pre = tmp_path / "pre.sh"
    post_hook = tmp_path / "post.sh"
    _hook(pre, "PRE", 0)
    _hook(post_hook, "POST", 0)
    box["A770B_QUALIFY_PRE"] = str(pre)
    box["A770B_QUALIFY_POST"] = str(post_hook)
    # A second data directory keeps the first job's result.
    data = tmp_path / "data-ok"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    (tmp_path / "models").mkdir(exist_ok=True)
    good = _run(box, ["start", "--foreground", *_gguf(box)])
    assert good.returncode == 0, good.stderr
    job = _one(box)
    assert "PRE" in (job / "hook.log").read_text()
    assert "POST" in (job / "hook.log").read_text()
    checks = json.loads((job / "result.json").read_text())["checks"]
    assert next(item for item in checks if item["name"] == "post-hook-ok")["result"] == "pass"

    _hook(pre, "PRE", 1)
    data2 = tmp_path / "data-fail"
    data2.mkdir()
    box["A770B_DATA"] = str(data2)
    failed = _run(box, ["start", "--foreground", *_gguf(box)])
    assert failed.returncode == 4
    job = _one(box)
    assert (job / "FAILED").is_file()
    status = json.loads((job / "status.json").read_text())
    assert status["reason"] == "pre-hook"
    assert "POST" in (job / "hook.log").read_text()

    _hook(pre, "PRE", 0)
    data3 = tmp_path / "data-term"
    data3.mkdir()
    box["A770B_DATA"] = str(data3)
    box["A770B_QUALIFY_SUITE_SLEEP"] = "30"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    job = None
    deadline = time.time() + 20
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "suite.pid").exists():
            job = found[0]
            break
        time.sleep(0.1)
    assert job is not None
    pid = json.loads((job / "status.json").read_text())["pid"]
    os.kill(int(pid), signal.SIGTERM)
    proc.wait(timeout=20)
    assert _wait_file(job / "STOPPED")
    assert "POST" in (job / "hook.log").read_text()


def test_i1_start_handshake_survives_caller_group(box, tmp_path):
    box["A770B_QUALIFY_HOLD_S"] = "1"
    marker = tmp_path / "returned"
    script = tmp_path / "caller.sh"
    script.write_text(
        "#!/bin/bash\n"
        f"bash {QUALIFY} start --engine llama.cpp --model sample.gguf --ctx 16384 "
        f"--backend vulkan --tokenizer {box['A770B_TOKENIZER_DIR']} > {tmp_path / 'out.txt'}\n"
        f"printf returned > {marker}\n"
        "sleep 30\n"
    )
    script.chmod(0o755)
    proc = subprocess.Popen(["bash", str(script)], env=box, start_new_session=True)
    assert _wait_file(marker, timeout=12)
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    assert _wait_file(_one(box) / "DONE", timeout=20)


def test_i2_lost_when_runner_dies_and_finished_is_not_edited(box, tmp_path):
    box["A770B_QUALIFY_PAUSE_PHASE"] = "serve"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 15
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "status.json").exists():
            state = json.loads((found[0] / "status.json").read_text()).get("state")
            if state == "running":
                job = found[0]
                break
        time.sleep(0.1)
    assert job is not None
    pid = json.loads((job / "status.json").read_text())["pid"]
    os.kill(int(pid), signal.SIGKILL)
    proc.wait(timeout=10)
    shown = _run(box, ["status", job.name])
    assert shown.returncode == 0
    assert json.loads(shown.stdout)["state"] == "lost"
    first = (job / "status.json").read_bytes()
    again = _run(box, ["status", job.name])
    assert again.returncode == 0
    assert (job / "status.json").read_bytes() == first
    # A finished job is not rewritten.
    box.pop("A770B_QUALIFY_PAUSE_PHASE")
    data = tmp_path / "data-done"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    finished = _one(box)
    blob = (finished / "status.json").read_bytes()
    assert _run(box, ["status", finished.name]).returncode == 0
    assert (finished / "status.json").read_bytes() == blob


def test_i21_window_refuses_and_fit_only_skips_suite(box):
    box["A770B_QUALIFY_KIT_PROMPT_TOKENS"] = "100000"
    refused = _run(box, ["start", "--foreground", *_gguf(box)])
    assert refused.returncode == 2
    assert _jobs(box) == []
    fit = _run(box, ["start", "--foreground", "--fit-only", *_gguf(box)])
    assert fit.returncode == 0, fit.stderr
    job = _one(box)
    report = (job / "report.md").read_text().splitlines()
    assert report[0].startswith("FIT RESULT")
    assert "a770b-qualify-suite" not in (job / "evidence" / "argv.log").read_text()


def test_i23_stop_phase3_and_phase5_and_wrong_identity(box, tmp_path):
    pre = tmp_path / "pre.sh"
    post = tmp_path / "post.sh"
    _hook(pre, "PRE", 0)
    _hook(post, "POST", 0)
    box["A770B_QUALIFY_PRE"] = str(pre)
    box["A770B_QUALIFY_POST"] = str(post)
    box["A770B_QUALIFY_PAUSE_PHASE"] = "serve"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 15
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "cid").exists() and (found[0] / "cid").read_text().strip():
            job = found[0]
            break
        time.sleep(0.1)
    assert job is not None
    stopped = _run(box, ["stop", job.name], timeout=30)
    proc.wait(timeout=20)
    assert stopped.returncode == 0, stopped.stderr
    assert (job / "STOPPED").is_file()
    assert "POST" in (job / "hook.log").read_text()
    assert "a770b-qualify-stop" in (job / "evidence" / "argv.log").read_text()
    # The lock is released when the runner exits.
    import fcntl

    lock = Path(box["A770B_DATA"]) / "logs" / "local-build.lock"
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    data = tmp_path / "data-suite"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    box["A770B_QUALIFY_PAUSE_PHASE"] = "suite"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 20
    while time.time() < deadline:
        pidfile = data / "logs" / "run.pid"
        if pidfile.exists() and pidfile.read_text().strip():
            found = _jobs(box)
            if found:
                job = found[0]
                break
        time.sleep(0.1)
    assert job is not None
    suite_pid = int((data / "logs" / "run.pid").read_text().strip())
    stopped = _run(box, ["stop", job.name], timeout=40)
    proc.wait(timeout=20)
    assert stopped.returncode == 0, stopped.stderr
    assert (job / "STOPPED").is_file()
    assert "POST" in (job / "hook.log").read_text()
    deadline = time.time() + 5
    while time.time() < deadline and _alive(suite_pid):
        time.sleep(0.1)
    assert not _alive(suite_pid)

    data = tmp_path / "data-wrong"
    data.mkdir()
    job = data / "qualify" / "wrongjob"
    job.mkdir(parents=True, mode=0o700)
    sleeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
    sys_path = os.path.join(PROJECT, "harness")
    import sys

    sys.path.insert(0, sys_path)
    import qualify_lib

    start = qualify_lib.proc_starttime(sleeper.pid)
    (job / "status.json").write_text(json.dumps({"pid": sleeper.pid, "pid_starttime": start, "state": "running"}))
    (job / "job.json").write_text(json.dumps({"launch": "setsid"}))
    box["A770B_DATA"] = str(data)
    wrong = _run(box, ["stop", "wrongjob"])
    assert wrong.returncode == 2
    assert _alive(sleeper.pid)
    os.kill(sleeper.pid, signal.SIGTERM)
    sleeper.wait(timeout=5)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_i24_result_and_wait_exit_codes(box, tmp_path):
    bad = _run(box, ["wait", "../nope"])
    assert bad.returncode == 2
    missing = _run(box, ["wait", "missing-job"])
    assert missing.returncode == 2
    box["A770B_QUALIFY_PAUSE_PHASE"] = "serve"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 15
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "status.json").exists():
            if json.loads((found[0] / "status.json").read_text()).get("state") == "running":
                job = found[0]
                break
        time.sleep(0.1)
    assert job is not None
    running = _run(box, ["result", job.name])
    assert running.returncode == 3
    waited = _run(box, ["wait", job.name, "--timeout", "1"])
    assert waited.returncode == 124
    stopped = _run(box, ["stop", job.name], timeout=30)
    proc.wait(timeout=20)
    assert stopped.returncode == 0
    final = _run(box, ["result", job.name])
    assert final.returncode == 4
    box.pop("A770B_QUALIFY_PAUSE_PHASE")
    data = tmp_path / "data-ok"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    job = _one(box)
    assert _run(box, ["result", job.name]).returncode == 0
    assert _run(box, ["wait", job.name, "--timeout", "2"]).returncode == 0


def test_i25_permissions_key_and_job_id(box, tmp_path):
    box["A770B_API_KEY"] = SECRET
    dot = _run(box, ["status", "../secret"])
    assert dot.returncode == 2
    key = _run(box, ["start", *_gguf(box, ["--extra=--api-key"])])
    assert key.returncode == 2
    assert _jobs(box) == []
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    job = _one(box)
    mode = stat.S_IMODE(job.stat().st_mode)
    assert mode == 0o700
    for path in job.rglob("*"):
        if path.is_symlink():
            pytest.fail(str(path))
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    blob = "\n".join(
        (job / name).read_text(errors="replace")
        for name in ("job.json", "report.md", "events.log", "evidence/argv.log")
        if (job / name).exists()
    )
    assert SECRET not in blob
    smoke = tmp_path / "curl-argv.txt"
    # The smoke script's real curl path, with a curl stub, never puts the key on the command.
    curl = tmp_path / "bin-curl"
    curl.mkdir()
    for name in ("python3", "bash", "chmod", "mkdir"):
        os.symlink(os.path.join("/usr/bin", name), curl / name)
    logger = curl / "curl"
    log = tmp_path / "curl.log"
    logger.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n")
    logger.chmod(0o755)
    env = dict(box)
    env["PATH"] = str(curl)
    env.pop("A770B_QUALIFY_STUB", None)
    subprocess.run(
        ["bash", os.path.join(PROJECT, "harness", "qualify_smoke.sh"), str(job)],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    recorded = log.read_text()
    assert "--config" in recorded
    assert SECRET not in recorded


def test_i27_systemd_or_setsid(box, tmp_path):
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    # Foreground is explicit. A detached start without the stubs uses setsid.
    data = tmp_path / "data-setsid"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    launched = _run(box, ["start", *_gguf(box)])
    assert launched.returncode == 0, launched.stderr
    job = _one(box)
    assert _wait_file(job / "DONE")
    assert json.loads((job / "job.json").read_text())["launch"] == "setsid"

    data = tmp_path / "data-unit"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    unit = tmp_path / "unit.log"
    systemd = tmp_path / "bin" / "systemd-run"
    systemctl = tmp_path / "bin" / "systemctl"
    systemd.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {unit}\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = -- ]; then shift; setsid \"$@\" </dev/null >/dev/null 2>&1 & exit 0; fi\n"
        "  shift\ndone\nexit 0\n"
    )
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {unit}\n"
        "exit 0\n"
    )
    systemd.chmod(0o755)
    systemctl.chmod(0o755)
    box["A770B_API_KEY"] = SECRET
    launched = _run(box, ["start", *_gguf(box)])
    assert launched.returncode == 0, launched.stderr
    text = unit.read_text()
    assert "--user" in text
    assert "--collect" in text
    assert "KillMode=mixed" in text
    assert "TimeoutStopSec=120" in text
    assert "--working-directory=" in text
    assert "qualify-" in text
    assert "qualify.sh" in text
    assert SECRET not in text
    job = _one(box)
    assert json.loads((job / "job.json").read_text())["launch"] == "systemd"
    assert _wait_file(job / "DONE")


def test_real_serve_lines_use_the_alias_and_not_admit():
    text = open(QUALIFY, encoding="utf-8").read()
    assert 'serve_strata.sh" admit' not in text
    assert 'serve_strata.sh" config-model' in text
    assert 'serve_strata.sh" stop-id' in text
    assert '--served-model-name "$alias"' in text
    assert "--tokenize openai" in text
    assert "A770B_SWEEP_STREAM=1" in text
    assert '--base-url "http://$A770B_HOST:$A770B_PORT"' in text
    assert "http://$A770B_HOST:$A770B_PORT/v1" not in text


def test_i4_failing_post_hook_and_a_large_successful_one(box, tmp_path):
    pre = tmp_path / "pre.sh"
    post = tmp_path / "post.sh"
    _hook(pre, "PRE", 0)
    post.write_text("#!/bin/sh\nprintf '%s\\n' POST-FAILED\nexit 1\n")
    post.chmod(0o755)
    box["A770B_QUALIFY_PRE"] = str(pre)
    box["A770B_QUALIFY_POST"] = str(post)
    failed = _run(box, ["start", "--foreground", *_gguf(box)])
    # A failing post-hook is a check failure. The job itself already finished.
    assert failed.returncode == 0, failed.stderr
    job = _one(box)
    check = next(item for item in json.loads((job / "result.json").read_text())["checks"] if item["name"] == "post-hook-ok")
    assert check["result"] == "fail"
    assert "POST-FAILED" in (check.get("reason") or "")

    post.write_text("#!/bin/sh\ndd if=/dev/zero bs=1024 count=128 2>/dev/null | tr '\\0' x\nprintf '\\nPOST-OK\\n'\nexit 0\n")
    data = tmp_path / "data-big"
    data.mkdir()
    box["A770B_DATA"] = str(data)
    good = _run(box, ["start", "--foreground", *_gguf(box)])
    assert good.returncode == 0, good.stderr
    job = _one(box)
    check = next(item for item in json.loads((job / "result.json").read_text())["checks"] if item["name"] == "post-hook-ok")
    assert check["result"] == "pass", check


def test_i23_term_during_a_long_speed_child(box, tmp_path):
    box["A770B_QUALIFY_SPEED_SLEEP"] = "30"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 20
    while time.time() < deadline:
        found = _jobs(box)
        if found:
            log = found[0] / "evidence" / "argv.log"
            if log.is_file() and any(line.startswith("speed:") for line in log.read_text().splitlines()):
                job = found[0]
                break
        time.sleep(0.1)
    assert job is not None
    pid = json.loads((job / "status.json").read_text())["pid"]
    os.kill(int(pid), signal.SIGTERM)
    proc.wait(timeout=20)
    assert _wait_file(job / "STOPPED", timeout=15)
    assert _wait_status(job, state="stopped")["state"] == "stopped"


def test_i25_symlink_phase_log_is_refused(box, tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("untouched")
    pre = tmp_path / "pre.sh"
    pre.write_text(f"#!/bin/sh\nln -s {sentinel} \"$1/phases/serve.log\"\nexit 0\n")
    pre.chmod(0o755)
    box["A770B_QUALIFY_PRE"] = str(pre)
    refused = _run(box, ["start", "--foreground", *_gguf(box)])
    assert refused.returncode == 4
    job = _one(box)
    assert (job / "FAILED").is_file()
    assert sentinel.read_text() == "untouched"
    assert "symlink" in refused.stderr


def test_i6_unknown_options_and_json_result(box, tmp_path):
    missing = _run(box, ["export"])
    assert missing.returncode == 2
    assert _run(box, ["stop"]).returncode == 2
    assert _run(box, ["wait"]).returncode == 2
    assert _run(box, ["result", "--nope"]).returncode == 2
    done = _run(box, ["start", "--foreground", *_gguf(box)])
    assert done.returncode == 0, done.stderr
    job = _one(box)
    assert _run(box, ["export", job.name, "--not-a-flag"]).returncode == 2
    assert _run(box, ["stop", job.name, "--extra"]).returncode == 2
    assert _run(box, ["wait", job.name, "--timeout"]).returncode == 2
    (job / "result.json").unlink()
    shown = _run(box, ["result", job.name, "--json"])
    assert shown.returncode == 4
    assert "report.md" not in shown.stdout
    assert "needs-review" not in shown.stdout


def test_i3_handshake_of_a_fast_preflight_failure_is_not_a_lock(box):
    box["A770B_QUALIFY_MIN_FREE_GIB"] = "1000000"
    launched = _run(box, ["start", *_gguf(box)])
    assert launched.returncode == 0, launched.stderr
    assert "job " in launched.stdout
    assert "refused the lock" not in launched.stderr
    job = _one(box)
    assert _wait_file(job / "FAILED", timeout=20)
    assert _wait_status(job, state="failed", reason="preflight").get("reason") == "preflight"


def test_stop_systemd_kill_reaches_the_runner(box, tmp_path):
    box["A770B_QUALIFY_PAUSE_PHASE"] = "serve"
    proc = subprocess.Popen(
        ["bash", QUALIFY, "start", *_gguf(box)],
        env=box,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job = None
    deadline = time.time() + 15
    while time.time() < deadline:
        found = _jobs(box)
        if found and (found[0] / "cid").is_file() and (found[0] / "cid").read_text().strip():
            job = found[0]
            break
        time.sleep(0.1)
    assert job is not None
    document = json.loads((job / "job.json").read_text())
    document["launch"] = "systemd"
    (job / "job.json").write_text(json.dumps(document))
    pid = json.loads((job / "status.json").read_text())["pid"]
    log = tmp_path / "systemctl.log"
    stub = Path(box["PATH"].split(":")[0]) / "systemctl"
    stub.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        'if [ "$1" = --user ] && [ "$2" = kill ]; then\n'
        f"  kill -TERM {int(pid)}\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    stopped = _run(box, ["stop", job.name], timeout=30)
    proc.wait(timeout=20)
    assert stopped.returncode == 0, stopped.stderr
    assert "kill -s TERM --kill-who=main" in log.read_text()
    assert (job / "STOPPED").is_file()


def test_wp1_smoke_reads_a_local_http_stub(box, tmp_path):
    """The real smoke script, against a loopback HTTP stub. Not a model server."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"data":[{"id":"local-builder"}]}'
            self._send(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if payload.get("tools"):
                body = json.dumps(
                    {"choices": [{"message": {"tool_calls": [{"id": "1", "type": "function"}]}}]}
                ).encode()
            else:
                body = b'{"choices":[{"message":{"content":"ok"}}]}'
            self._send(body)

        def _send(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        job = tmp_path / "smoke-job"
        for name in ("evidence", "scratch", "phases"):
            (job / name).mkdir(parents=True)
        (job / "plan.json").write_text(json.dumps({"engine": "vllm"}))
        env = dict(box)
        env.pop("A770B_QUALIFY_STUB", None)
        env["PATH"] = "/usr/bin:/bin"
        env["A770B_HOST"] = "127.0.0.1"
        env["A770B_PORT"] = str(port)
        env["A770B_ALIAS"] = "local-builder"
        run = subprocess.run(
            ["bash", os.path.join(PROJECT, "harness", "qualify_smoke.sh"), str(job)],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == {"models": True, "completion": True, "tool": True}
        assert SECRET not in run.stdout
        fields = json.loads((job / "evidence" / "per-request-fields.json").read_text())
        assert fields["choices"][0]["message"]["content"] == "ok"
        assert "Authorization" not in json.dumps(fields)
    finally:
        server.shutdown()
        server.server_close()
