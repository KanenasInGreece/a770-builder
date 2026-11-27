"""Managed Strata contracts with local fixture processes and no GPU."""
import json
import os
from pathlib import Path
import signal
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import strata_managed as managed


def test_process_identity_rejects_stale_start_time():
    identity = managed.process_identity(os.getpid())
    assert identity
    assert managed.process_alive({"pid": os.getpid(), "start": identity})
    assert not managed.process_alive({"pid": os.getpid(), "start": "0"})


@pytest.fixture
def config(tmp_path, monkeypatch):
    exe = tmp_path / "strata"
    exe.write_text("#!/bin/sh\nexit 1\n")
    exe.chmod(0o755)
    for name in ("tokenizer.json", "vocab.json"):
        (tmp_path / name).write_text("{}")
    model = tmp_path / "strata.json"
    model.write_text(json.dumps({"exe": str(exe), "cwd": str(tmp_path), "tokenizer": str(tmp_path),
                                 "backend": "intel", "model_name": "flash-next", "drafting": "auto",
                                 "env": {"STRATA_MIRROR_MIB": "0"},
                                 "args": ["--spec", "2", "--reserve-mib", "2048"]}))
    monkeypatch.setenv("A770B_ONEAPI_DEVICE_SELECTOR", "level_zero:1")
    monkeypatch.setenv("A770B_STRATA_ENGINE_SHA256", managed.file_digest(exe))
    return model


def test_prepared_config_pins_one_card_and_preserves_native_spec(config):
    doc, evidence = managed.prepare_config(config)
    assert doc["env"]["ONEAPI_DEVICE_SELECTOR"] == "level_zero:1"
    assert doc["args"][:2] == ["--spec", "2"] and evidence["model_id"] == "flash-next"
    assert evidence["engine_sha256"] == managed.file_digest(Path(doc["exe"]))


def test_prepared_config_records_its_host_memory_settings_as_configured(config):
    """The config's host-memory knobs are carried into the launch evidence as written: a configured 0 stays
    "0", a bare flag is True, and an absent knob is absent — not a default the harness guessed."""
    _, evidence = managed.prepare_config(config)
    assert evidence["host_memory_settings"] == {"env": {"STRATA_MIRROR_MIB": "0"}, "args": {}}

    doc = json.loads(config.read_text())
    doc["env"] = {"STRATA_MIRROR_MIB": "28672", "STRATA_VERIFY_NO_HOST": "1", "UNRELATED": "x"}
    doc["args"] = ["--spec", "2", "--expert-cache", "auto", "--stream-experts", "--ple-io=mmap",
                   "--vram-reserve-mib", "5120"]
    config.write_text(json.dumps(doc))
    _, evidence = managed.prepare_config(config)
    assert evidence["host_memory_settings"] == {
        "env": {"STRATA_MIRROR_MIB": "28672", "STRATA_VERIFY_NO_HOST": "1"},
        "args": {"--expert-cache": "auto", "--stream-experts": True, "--ple-io": "mmap"},
    }


def test_launch_identity_ignores_reporting_only_host_memory_metadata():
    old = {"config_sha256": "same-config", "engine_sha256": "same-engine", "model_id": "m"}
    new = {**old, "host_memory_settings": {"env": {"STRATA_MIRROR_MIB": "28672"}, "args": {}}}
    assert managed.launch_evidence_matches(old, new)
    assert not managed.launch_evidence_matches(old, {**new, "config_sha256": "different-config"})


def test_observed_pinned_host_memory_is_parsed_from_engine_output(tmp_path):
    log = tmp_path / "engine.log"
    log.write_text("loaded experts; mirrored in 27.70 GiB of pinned host RAM\n", encoding="utf-8")
    observed = managed.observed_pinned_host_memory(log)
    assert observed == {"value_bytes": round(27.70 * 1024 ** 3), "source": str(log),
                        "reported_text": "loaded experts; mirrored in 27.70 GiB of pinned host RAM"}
    log.write_text("no pinned allocation line\n", encoding="utf-8")
    assert managed.observed_pinned_host_memory(log) is None


def test_observe_host_memory_persists_the_measurement_before_profile_rendering(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "engine.log").write_text("mirrored in 27.70 GiB of pinned host RAM\n", encoding="utf-8")
    evidence = run / "evidence.json"
    evidence.write_text(json.dumps({"engine": "strata", "log_dir": str(run), "host_memory_settings": {"env": {}, "args": {}}}))
    assert managed.observe_host_memory(evidence) == evidence
    saved = json.loads(evidence.read_text(encoding="utf-8"))
    assert saved["observed_pinned_host_memory"]["value_bytes"] == round(27.70 * 1024 ** 3)


def test_continuous_enforcement_receipt_is_written_atomically_only_when_requested(tmp_path, monkeypatch):
    receipt = tmp_path / "ram-guard.receipt"
    monkeypatch.setenv("A770B_BUDGET_GATE_RECEIPT", str(receipt))
    assert managed.write_enforcement_receipt("continuous") is True
    assert json.loads(receipt.read_text(encoding="utf-8"))["enforcement"] == "continuous"
    assert not list(tmp_path.glob("*.tmp.*"))


def test_post_check_refusal_does_not_promote_launch_receipt_to_continuous(tmp_path, monkeypatch):
    receipt = tmp_path / "ram-guard.receipt"
    monkeypatch.setenv("A770B_BUDGET_GATE_RECEIPT", str(receipt))
    assert managed.write_enforcement_receipt("launch") is True
    monkeypatch.setattr(managed, "read_state", lambda _path: {"min_ram_mib": 20000})
    monkeypatch.setattr(managed, "read_key", lambda: "key")
    monkeypatch.setattr(managed, "process_alive", lambda _state: True)
    monkeypatch.setattr(managed, "health_matches", lambda _state, _key: True)
    monkeypatch.setattr(managed, "ram_ok", lambda _minimum: False)
    assert managed.admit(tmp_path / "state.json") is False
    assert json.loads(receipt.read_text(encoding="utf-8"))["enforcement"] == "launch"


def test_verified_healthy_admission_promotes_receipt_to_continuous(tmp_path, monkeypatch):
    receipt = tmp_path / "ram-guard.receipt"
    monkeypatch.setenv("A770B_BUDGET_GATE_RECEIPT", str(receipt))
    assert managed.write_enforcement_receipt("launch") is True
    monkeypatch.setattr(managed, "read_state", lambda _path: {"min_ram_mib": 20000})
    monkeypatch.setattr(managed, "read_key", lambda: "key")
    monkeypatch.setattr(managed, "process_alive", lambda _state: True)
    monkeypatch.setattr(managed, "health_matches", lambda _state, _key: True)
    monkeypatch.setattr(managed, "ram_ok", lambda _minimum: True)
    assert managed.admit(tmp_path / "state.json") is True
    assert json.loads(receipt.read_text(encoding="utf-8"))["enforcement"] == "continuous"


def test_continuous_enforcement_is_written_only_after_admission():
    source = (ROOT / "harness" / "strata_managed.py").read_text(encoding="utf-8")
    supervisor = source[source.index("def supervise("):source.index("def start(")]
    assert 'write_enforcement_receipt("continuous")' not in supervisor
    start = source[source.index("def start("):source.index("def admit(")]
    assert 'write_enforcement_receipt("continuous")' not in start
    admit = source[source.index("def admit("):source.index("def main(")]
    assert admit.index("not health_matches(state, key)") < admit.index('write_enforcement_receipt("continuous")')
    serve = (ROOT / "harness" / "serve_strata.sh").read_text(encoding="utf-8")
    vram_check = serve.index('"$used" "$A770B_VRAM_CAP_GIB"')
    assert vram_check < serve.index('"${managed[@]}" admit')


def test_strata_receipt_is_written_only_after_admission(tmp_path):
    """A stale receipt is removed before start. Admission failure leaves none. Success writes it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    started = tmp_path / "started"
    evidence = tmp_path / "data" / "logs" / "serve-strata.evidence.json"
    python = bin_dir / "python3"
    python.write_text(
        "#!/bin/bash\n"
        "if [[ \"$1\" == *strata_managed.py ]]; then\n"
        "  shift\n"
        "  printf '%s\\n' \"$1\" >> \"$STRATA_CALLS\"\n"
        "  case \"$1\" in\n"
        "    tokenizer) exit 0 ;;\n"
        "    id)\n"
        "      if [ -f \"$STRATA_STARTED\" ]; then printf '%s\\n' strata-id; exit 0; fi\n"
        "      exit 1 ;;\n"
        "    start)\n"
        "      if [ -e \"$A770B_DATA/logs/profiling.json\" ]; then\n"
        "        echo present > \"$STRATA_AT_START\"\n"
        "      else\n"
        "        echo absent > \"$STRATA_AT_START\"\n"
        "      fi\n"
        "      printf '%s\\n' '{\"engine\":\"strata\"}' > \"$STRATA_EVIDENCE\"\n"
        "      : > \"$STRATA_STARTED\"\n"
        "      printf '%s\\n' \"$STRATA_EVIDENCE\"\n"
        "      exit 0 ;;\n"
        "    admit) exit \"${STRATA_ADMIT_RC:-0}\" ;;\n"
        "    stop) exit 0 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exec /usr/bin/python3 \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    (bin_dir / "nvtop").write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' '[{\"device_name\":\"Intel Arc DG2\",\"mem_used\":0,"
        "\"mem_total\":17179869184,\"mem_free\":17179869184}]'\n",
        encoding="utf-8",
    )
    (bin_dir / "nvtop").chmod(0o755)
    (bin_dir / "docker").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    gate = tmp_path / "gate.sh"
    gate.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    gate.chmod(0o755)
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    receipt = data / "logs" / "profiling.json"
    receipt.write_text('{"stale": true}\n', encoding="utf-8")
    key = tmp_path / "api.key"
    key.write_text("a770b-test\n", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        "A770B_PROJECT": str(ROOT),
        "A770B_DATA": str(data),
        "A770B_HOST": "127.0.0.1",
        "A770B_PORT": "9",
        "A770B_VRAM_CAP_GIB": "16",
        "A770B_CARD_VRAM_TOTAL": "16",
        "A770B_GPU_MATCH": "DG2",
        "A770B_ALLOW_NO_NVTOP": "1",
        "A770B_MIN_AVAIL_MB": "1",
        "A770B_BUDGET_GATE": str(gate),
        "A770B_API_KEY_FILE": str(key),
        "A770B_DOCKER": "docker",
        "A770B_FRAMEWORK_PORTS": "",
        "A770B_SERVE": "compose",
        "A770B_COMPOSE_PROJECT": "a770-builder",
        "A770B_CARD": "",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "STRATA_CALLS": str(calls),
        "STRATA_STARTED": str(started),
        "STRATA_EVIDENCE": str(evidence),
        "STRATA_AT_START": str(tmp_path / "at-start.txt"),
        "STRATA_ADMIT_RC": "1",
    })
    (tmp_path / "xdg").mkdir()
    script = ROOT / "harness" / "serve_strata.sh"
    failed = __import__("subprocess").run(
        ["bash", str(script), "start", str(tmp_path / "prepared.json"), "8192"],
        env=env, capture_output=True, text=True,
    )
    assert failed.returncode == 3, failed.stdout + failed.stderr
    assert not receipt.exists()
    assert (tmp_path / "at-start.txt").read_text(encoding="utf-8").strip() == "absent"
    assert not evidence.exists() or "profiling" not in evidence.read_text(encoding="utf-8")

    receipt.write_text('{"stale": true}\n', encoding="utf-8")
    started.unlink()
    env["STRATA_ADMIT_RC"] = "0"
    ok = __import__("subprocess").run(
        ["bash", str(script), "start", str(tmp_path / "prepared.json"), "8192"],
        env=env, capture_output=True, text=True,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ok.stdout.strip() == str(evidence)
    written = json.loads(receipt.read_text(encoding="utf-8"))
    assert written["engine"] == "strata" and written["flags"] == []
    merged = json.loads(evidence.read_text(encoding="utf-8"))
    assert merged["profiling"]["engine"] == "strata"


def test_strata_shell_dispatches_the_supported_pid_command(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    recorder = tmp_path / "called.txt"
    python3 = fake_bin / "python3"
    python3.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{recorder}'\nexit 0\n", encoding="utf-8")
    python3.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}", A770B_PROJECT=str(Path(__file__).resolve().parents[1]),
               A770B_DATA=str(tmp_path / "data"), A770B_MODELS=str(tmp_path / "models"), A770B_CARD="b70")
    result = __import__("subprocess").run(["bash", str(Path(__file__).resolve().parents[1] / "harness/serve_strata.sh"),
                                           "pid"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "strata_managed.py pid" in recorder.read_text(encoding="utf-8")


@pytest.mark.parametrize("key,value", [
    ("drafting", "dflash2"), ("args", ["--draft-model", "missing.pack"]),
    ("gpu", [0, 1]), ("parallel", 2), ("replicas", 2), ("before_load", "some-script"),
    ("env", {"ONEAPI_DEVICE_SELECTOR": "level_zero:*"}), ("api_key", "foreign-key"),
    ("env", {"STRATA_PROFILE_DIR": "/other/location"}),
])
def test_prepared_config_refuses_unsafe_overrides(config, key, value):
    doc = json.loads(config.read_text())
    doc[key] = value
    config.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        managed.prepare_config(config)


# This substitute only implements the terminal launch boundary. It exits when
# the supervisor closes its write end of the owner pipe.
SERVER = """
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import threading

parser = argparse.ArgumentParser()
for name in ('engine', 'config', 'host', 'port', 'parallel', 'shutdown-timeout', 'owner-fd', 'ready-file'):
    parser.add_argument('--' + name)
parser.add_argument('--offline', action='store_true')
args = parser.parse_args()
assert args.host == '127.0.0.1' and args.offline and args.parallel == '1'
assert os.environ['STRATA_KEEP_LEFTOVERS'] == '1' and os.environ['STRATA_NO_PRESET'] == '1'
assert os.environ['STRATA_NO_ACQUIRE'] == '1'
assert Path(os.environ['STRATA_HOME']) == Path(args.config).parent
assert Path(os.environ['XDG_CONFIG_HOME']).parent == Path(args.config).parent
config = Path(args.config).read_bytes()
model = json.loads(config)['model_name']
argv = json.dumps(sys.argv[1:], ensure_ascii=False, separators=(',', ':')).encode()
prefix = b'strata-owned-launch-v1' + bytes(1)
message = prefix + len(config).to_bytes(8, 'big') + config + len(argv).to_bytes(8, 'big') + argv
proof = hmac.new(bytes.fromhex(os.environ['STRATA_OWNER_ATTEST_KEY']), message, hashlib.sha256).hexdigest()
key = os.environ['STRATA_API_KEY']

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200
        if self.path == '/health':
            body = dict(service='strata', loaded=True, model=model, max_context=32768)
        elif self.headers.get('Authorization') != 'Bearer ' + key:
            code, body = 401, {}
        elif self.path == '/v1/models':
            body = dict(data=[dict(id=model, status=dict(value='loaded'), meta=dict(n_ctx=32768),
                                   architecture=dict(output_modalities=['text']))])
        elif self.path == '/v1/telemetry':
            body = dict(instance=dict(id="owned-fixture", launch_attestation=proof))
        else:
            code, body = 404, {}
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

with ThreadingHTTPServer((args.host, int(args.port)), Handler) as server:
    Path(args.ready_file).write_text(json.dumps(dict(
        pid=os.getpid(), bound_host=args.host, bound_port=int(args.port), model_id=model,
        model_state="ready", instance_id="owned-fixture",
    )))
    def owner():
        os.read(int(args.owner_fd), 1)
        server.shutdown()
    threading.Thread(target=owner, daemon=True).start()
    server.serve_forever()
"""


def test_start_reuse_and_exact_stop_with_owned_fixture(config, tmp_path, monkeypatch):
    import socket

    terminal = tmp_path / "terminal"
    (terminal / "serve").mkdir(parents=True)
    (terminal / "serve/server.py").write_text(SERVER)
    monkeypatch.setattr(managed, "source_inputs", lambda: (terminal, Path(sys.executable)))
    monkeypatch.setenv("A770B_CARD", "b70")
    monkeypatch.setenv("A770B_GPU_MATCH", "G31")
    monkeypatch.setattr(managed, "level_zero_pci_ids", lambda: [(0x8086, 0xe20b), (0x8086, 0xe223)])
    keyfile = tmp_path / "api.key"
    keyfile.write_text("fixture-key\n")
    monkeypatch.setenv("A770B_API_KEY_FILE", str(keyfile))
    monkeypatch.setenv("A770B_HOST", "127.0.0.1")
    monkeypatch.setenv("A770B_MIN_AVAIL_MB", "1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("A770B_PORT", str(port))
    state_path = tmp_path / "strata-instance.json"
    try:
        state = managed.start(state_path, config, 32768, 5)
        assert managed.health_matches(state, "fixture-key")
        assert not managed.health_matches({**state, "attestation": "0" * 64}, "fixture-key")
        assert managed.start(state_path, config, 32768, 5)["pid"] == state["pid"]
        monkeypatch.setenv("A770B_MIN_AVAIL_MB", "2")
        with pytest.raises(ValueError, match="different launch"):
            managed.start(state_path, config, 32768, 5)
        evidence = (Path(state["run"]) / "evidence.json").read_text()
        assert "fixture-key" not in evidence and "engine_sha256" in evidence
        with pytest.raises(ValueError, match="instance changed"):
            managed.stop(state_path, "some-other-id")
        assert managed.process_alive(state)
        managed.stop(state_path, state["id"])
        assert not managed.process_alive(state) and not state_path.exists()
    finally:
        managed.stop(state_path)


def test_candidate_ladder_dry_run_routes_all_rungs_without_key(config, tmp_path, monkeypatch):
    import subprocess

    root = Path(managed.__file__).resolve().parents[1]
    env = dict(os.environ, A770B_PROJECT=str(root), A770B_DATA=str(tmp_path / "data"), A770B_CARD="b70",
               A770B_CARD_MODE="inference", A770B_STRATA_CONFIG=str(config), A770B_STRATA_CTX="32768",
               A770B_API_KEY_FILE=str(tmp_path / "api.key"))
    env.pop("A770B_PROFILES_FILE", None)
    run = subprocess.run(["bash", str(root / "harness/ladder.sh"), "strata-candidate", "--dry-run"],
                         env=env, text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "harness/serve_strata.sh start" in run.stdout
    assert "A770B_SWEEP_STREAM=1" in run.stdout and "--model flash-next" in run.stdout
    assert "--served-model-name flash-next" in run.stdout
    assert "rung 5/6: bash harness/depth_probe.sh" in run.stdout and "rung 6/6: bash harness/run_suite.sh" in run.stdout
    assert not (tmp_path / "api.key").exists()


def test_explicit_draft_file_needs_matching_pin(config, tmp_path):
    draft = tmp_path / "draft.pack"
    draft.write_bytes(b"prepared draft fixture")
    doc = json.loads(config.read_text())
    doc["drafting"] = "dflash2"
    doc["args"].extend(["--draft-model", str(draft)])
    config.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="matching draft_asset_sha256"):
        managed.prepare_config(config)
    doc["draft_asset_sha256"] = {str(draft): managed.file_digest(draft)}
    config.write_text(json.dumps(doc))
    prepared, evidence = managed.prepare_config(config)
    assert prepared["user_args"][-2:] == ["--draft-model", str(draft)]
    assert evidence["draft_sha256"] == doc["draft_asset_sha256"]


def test_level_zero_selector_rejects_desktop_card(monkeypatch):
    monkeypatch.setenv("A770B_CARD", "b70")
    monkeypatch.setenv("A770B_GPU_MATCH", "G31")
    monkeypatch.setattr(managed, "level_zero_pci_ids", lambda: [(0x8086, 0xe20b), (0x8086, 0xe223)])
    with pytest.raises(ValueError, match="does not select"):
        managed.require_selected_card("level_zero:0")
    managed.require_selected_card("level_zero:1")


def test_supervisor_does_not_start_without_foreground_authorization(tmp_path):
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    managed.supervise(tmp_path / "missing-launch.json", read_fd)


@pytest.mark.parametrize("name", ["ZE_ENABLE_PCI_ID_DEVICE_ORDER", "ZE_FLAT_DEVICE_HIERARCHY",
                                   "UR_LOADER_USE_OLD_ADAPTER", "SYCL_DEVICE_ALLOWLIST"])
def test_config_cannot_change_device_enumeration(config, name):
    doc = json.loads(config.read_text())
    doc["env"] = {name: "1"}
    config.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="card selection"):
        managed.prepare_config(config)


def test_partial_readiness_waits_for_complete_json(tmp_path):
    (tmp_path / "ready.json").write_text("{")
    state = {"run": str(tmp_path), "server_pid": os.getpid(),
             "server_start": managed.process_identity(os.getpid())}
    assert not managed.health_matches(state, "fixture-key")


@pytest.mark.parametrize("name", ["SYCL_DEVICE_ALLOWLIST", "UR_ENABLE_LAYERS", "ZE_ENABLE_PCI_ID_DEVICE_ORDER"])
def test_preflight_rejects_ambient_device_controls(monkeypatch, name):
    monkeypatch.setenv(name, "1")
    with pytest.raises(ValueError, match=name):
        managed.level_zero_pci_ids()


def test_stop_cleans_server_after_supervisor_death(tmp_path):
    import subprocess

    path = tmp_path / "instance.json"
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                          start_new_session=True) as server:
        state = {"id": "fixture", "pid": -1, "start": None, "run": str(tmp_path),
                 "server_pid": server.pid, "server_start": managed.process_identity(server.pid)}
        managed.write_state(path, state)
        try:
            managed.stop(path, "fixture")
            assert server.poll() is not None
            assert not path.exists()
        finally:
            if server.poll() is None:
                server.kill()


def test_stop_retains_ownership_if_engine_survives(tmp_path, monkeypatch):
    path = tmp_path / "instance.json"
    state = {"id": "fixture", "pid": -1, "start": None, "run": str(tmp_path),
             "server_pid": 100, "server_start": "abc"}
    managed.write_state(path, state)
    procs = tmp_path / "xdg-state" / "strata" / "procs"
    procs.mkdir(parents=True)
    managed.write_state(procs / "101.json", {"pid": 101, "role": "engine", "token": "l:def",
                                             "parent": {"pid": 100, "token": "l:abc"}})
    monkeypatch.setattr(managed, "process_identity", lambda pid: "def" if pid == 101 else None)
    monkeypatch.setattr(managed, "STOP_TIMEOUT", 0)
    # 101 is a made-up engine; the real pid of that number must never be signalled.
    sent = []
    monkeypatch.setattr(managed, "signal_and_wait", lambda *args: sent.append(args[:3]))
    with pytest.raises(ValueError, match="retaining ownership"):
        managed.stop(path, "fixture")
    assert path.exists()
    assert sent == [(101, "def", signal.SIGTERM), (101, "def", signal.SIGKILL)]


def test_server_entrypoint_waits_for_recorded_ownership(tmp_path, monkeypatch):
    import subprocess

    terminal = tmp_path / "terminal"
    (terminal / "serve").mkdir(parents=True)
    marker = tmp_path / "server-entrypoint-ran"
    (terminal / "serve/server.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    state = tmp_path / "instance.json"
    plan = tmp_path / "plan.json"
    snapshot = tmp_path / "config.json"
    snapshot.write_text("{}")
    key = tmp_path / "key"
    key.write_text("fixture-key\n")
    monkeypatch.setenv("A770B_API_KEY_FILE", str(key))
    managed.write_state(plan, {"run": str(tmp_path), "snapshot": str(snapshot), "port": 30261,
                               "state": str(state), "selector": "level_zero:0", "min_ram_mib": 1,
                               "python": sys.executable, "terminal": str(terminal)})
    launcher = """
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import strata_managed as managed

def write_state(path, state):
    if "server_pid" in state:
        # Give a child without an authorization gate a chance to run.
        managed.time.sleep(0.4)
        os._exit(42)
    original(path, state)

original = managed.write_state
managed.write_state = write_state
read, write = os.pipe()
os.write(write, b"1")
os.close(write)
managed.supervise(Path(sys.argv[2]), read)
"""
    result = subprocess.run([sys.executable, "-c", launcher, str(Path(managed.__file__).parent),
                             str(plan)], timeout=5, check=False)
    assert result.returncode == 42
    assert not marker.exists()
