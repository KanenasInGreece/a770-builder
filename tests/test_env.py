#!/usr/bin/env python3
"""Tests for the three adversarial-review fixes owned by this builder:
  (1) the corpus is part of the instrument — a missing A770B_CORPUS_FILE is GENERATED (kit/corpus.py), never
      silently swapped for a different corpus (harness/guard.sh a770b_ensure_corpus_file, used by ctx_sweep.sh).
  (2) A770B_RESET_PATTERN — the kernel-log reset regex is a configurable knob (harness/env.sh), read by the one
      shared grep (harness/guard.sh kernel_resets_since), not a hard-coded Intel-only string.
  (3) local-build.sh doctor states the card-pinning defaults (A770B_VK_DEVICE_SELECT, A770B_GPU_MATCH,
      A770B_VRAM_CAP_GIB, A770B_UBATCH) as inputs the user must set for their own card.
Runs entirely offline against files already in this clone; no server, no GPU, no real journalctl/nvtop/llama-server
needed (all faked). Real corpus generation IS exercised (it is deterministic, offline, and already proven to reach
the shipped floor by test_kit.py's own test_check_passes_at_the_shipped_default).
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_SH = ROOT / "harness" / "env.sh"
GUARD_SH = ROOT / "harness" / "guard.sh"
CTX_SWEEP_SH = ROOT / "harness" / "ctx_sweep.sh"
BENCH_MODEL_SH = ROOT / "harness" / "bench_model.sh"
CORPUS_PY = ROOT / "kit" / "corpus.py"
DOCTOR_SH = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"


def _make_bin(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def run_bash(script: str, env: dict, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=cwd, env=env
    )


# ── (2) A770B_RESET_PATTERN is a real, honoured knob ─────────────────────────────────────────────────────────


def test_env_sh_defaults_reset_pattern_to_the_intel_wording(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"))  # never touch the real ~/local-ai
    r = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_RESET_PATTERN"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "engine reset|timedout"


def test_kernel_resets_since_uses_the_configured_pattern_not_a_hard_coded_one(tmp_path):
    # a fake journalctl that emits one line matching a NON-Intel driver's wording ("amdgpu: GPU reset") and one
    # matching nothing at all; kernel_resets_since must count only what A770B_RESET_PATTERN names.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _make_bin(
        fake_bin,
        "journalctl",
        'printf "%s\\n" "kernel: amdgpu: GPU reset in progress" "kernel: unrelated line"',
    )
    env = _base_env()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    # default pattern (Intel wording): matches neither fake line
    env["A770B_RESET_PATTERN"] = "engine reset|timedout"
    r = run_bash(f'. "{GUARD_SH}"; kernel_resets_since "-1min"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "0"

    # a non-Intel driver's own wording, set via the knob: matches the amdgpu line
    env["A770B_RESET_PATTERN"] = "amdgpu: GPU reset"
    r = run_bash(f'. "{GUARD_SH}"; kernel_resets_since "-1min"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "1"


def test_every_reset_watch_this_builder_owns_reads_the_shared_helper_not_a_literal_grep():
    # ctx_sweep.sh and bench_model.sh must call the one shared function rather than repeating their own
    # 'engine reset|timedout' literal (which is what made the pattern un-configurable in the first place).
    for f in (CTX_SWEEP_SH, BENCH_MODEL_SH):
        text = f.read_text(encoding="utf-8")
        assert "kernel_resets_since" in text, f"{f} does not use the shared kernel_resets_since helper"
        assert "engine reset|timedout" not in text, f"{f} still hard-codes the Intel-only reset pattern"


def test_builder_env_example_documents_the_reset_pattern_knob():
    text = (ROOT / "config" / "builder.env.example").read_text(encoding="utf-8")
    assert "A770B_RESET_PATTERN" in text
    # names where to find another driver's own wording
    assert "journalctl" in text or "dmesg" in text


# ── (1) the corpus is part of the instrument: generate, or refuse — never a silent different corpus ──────────


def _base_env(**overrides) -> dict:
    env = dict(os.environ)
    env.update(overrides)
    return env


def test_ensure_corpus_file_generates_it_when_missing(tmp_path):
    target = tmp_path / "generated-corpus.py"
    assert not target.exists()
    env = _base_env(A770B_PROJECT=str(ROOT), A770B_CORPUS_FILE=str(target))
    r = run_bash(f'. "{GUARD_SH}"; a770b_ensure_corpus_file', env=env)
    assert r.returncode == 0, r.stderr
    assert target.exists() and target.stat().st_size > 0
    # says what it did: names kit/corpus.py generate and the target file
    assert "kit/corpus.py" in r.stdout
    assert str(target) in r.stdout
    # byte-identical to calling kit/corpus.py generate directly (same command the docstring names)
    direct = tmp_path / "direct-corpus.py"
    subprocess.run(
        [sys.executable, str(CORPUS_PY), "generate", "--out", str(direct)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert target.read_bytes() == direct.read_bytes()


def test_ensure_corpus_file_leaves_an_existing_file_untouched(tmp_path):
    target = tmp_path / "already-there.py"
    target.write_bytes(b"### FILE sentinel.py\nsentinel content, not regenerated\n")
    before = target.read_bytes()
    env = _base_env(A770B_PROJECT=str(ROOT), A770B_CORPUS_FILE=str(target))
    r = run_bash(f'. "{GUARD_SH}"; a770b_ensure_corpus_file', env=env)
    assert r.returncode == 0, r.stderr
    assert target.read_bytes() == before
    assert "already generated" in r.stdout


def test_ensure_corpus_file_refuses_with_the_exact_command_when_generation_fails(tmp_path):
    # a stub kit/corpus.py that always fails, standing in for "the real source cannot reach the floor"
    fake_project = tmp_path / "project"
    (fake_project / "kit").mkdir(parents=True)
    (fake_project / "kit" / "corpus.py").write_text(
        "import sys\nprint('cannot reach the floor', file=sys.stderr)\nsys.exit(2)\n", encoding="utf-8"
    )
    target = tmp_path / "would-be-corpus.py"
    env = _base_env(A770B_PROJECT=str(fake_project), A770B_CORPUS_FILE=str(target))
    r = run_bash(f'. "{GUARD_SH}"; a770b_ensure_corpus_file', env=env)
    assert r.returncode == 2
    assert not target.exists()
    # names the exact command to run by hand
    assert f"python3 {fake_project}/kit/corpus.py generate --out {target}" in r.stderr


def test_ctx_sweep_no_longer_falls_back_to_a_seat_glob_corpus():
    text = CTX_SWEEP_SH.read_text(encoding="utf-8")
    assert "a770b_ensure_corpus_file" in text
    assert "today's fallback" not in text
    assert "seat glob" not in text


def test_corpus_py_docstring_states_it_is_part_of_the_instrument():
    text = CORPUS_PY.read_text(encoding="utf-8")
    assert "PART OF THE MEASURING INSTRUMENT" in text or "part of the instrument" in text.lower()


# ── (3) doctor states the card-pinning defaults as inputs the user must set for their own card ────────────────


def _doctor_env(tmp_path, **overrides) -> dict:
    env = dict(os.environ)
    env.update(
        A770B_PROJECT=str(ROOT),
        A770B_REFUSE="/nonexistent",
        A770B_DATA=str(tmp_path / "data"),
    )
    env.update(overrides)
    return env


def _run_doctor(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"], capture_output=True, text=True, cwd=ROOT, env=env
    )


def test_doctor_states_each_card_pinning_knob_as_an_input(tmp_path):
    r = _run_doctor(_doctor_env(tmp_path))
    out = r.stdout
    for var in ("A770B_VK_DEVICE_SELECT", "A770B_GPU_MATCH", "A770B_VRAM_CAP_GIB", "A770B_UBATCH"):
        lines = [ln for ln in out.splitlines() if ln.startswith("ok   input " + var + "=")]
        assert lines, f"doctor printed no 'input {var}=' line:\n{out}"
        assert "this project's default" in lines[0] or "your own override" in lines[0]


def test_doctor_labels_an_overridden_knob_as_the_users_own():
    r = subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"],
        capture_output=True, text=True, cwd=ROOT,
        env=dict(os.environ, A770B_PROJECT=str(ROOT), A770B_REFUSE="/nonexistent",
                  A770B_GPU_MATCH="SomeOtherCard"),
    )
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("ok   input A770B_GPU_MATCH=")]
    assert lines, r.stdout
    assert "your own override" in lines[0]
    assert "this project's default" not in lines[0]


# ── A770B_SERVE: host (default) or compose ───────────────────────────────────────────────────────────────────


def test_env_sh_defaults_serve_to_host(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="")
    r = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_SERVE"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "host"


def test_env_sh_refuses_an_unknown_serve_value(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="bogus")
    r = run_bash(f'set -e; . "{ENV_SH}"', env=env)
    assert r.returncode != 0
    assert "A770B_SERVE must be host or compose" in r.stderr


def test_env_sh_selects_the_compose_serve_script(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="compose")
    r = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_SERVE_SCRIPT"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.endswith("harness/serve_compose.sh")


def _fake_docker_bin(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    _make_bin(bin_, "docker", 'case " $* " in *" compose version "*) echo "Docker Compose version v2.30.0"; exit 0;; esac; exit 0')
    return bin_


def test_doctor_compose_does_not_require_a_host_llama_server(tmp_path):
    bin_ = _fake_docker_bin(tmp_path)
    env = _doctor_env(tmp_path, A770B_SERVE="compose", A770B_ALLOW_NO_NVTOP="1",
                      A770B_COMPOSE_FILE=str(ROOT / "compose" / "a770-vulkan.yaml"))
    env["PATH"] = f"{bin_}:{env['PATH']}"
    r = _run_doctor(env)
    assert "MISSING llama-server" not in r.stdout, r.stdout
    assert any(ln.startswith("ok   docker compose") for ln in r.stdout.splitlines()), r.stdout
    assert any(ln.startswith("ok   compose envelope") for ln in r.stdout.splitlines()), r.stdout


def test_doctor_compose_requires_the_container_runtime(tmp_path):
    env = _doctor_env(tmp_path, A770B_SERVE="compose", A770B_ALLOW_NO_NVTOP="1",
                      A770B_DOCKER="docker-absent-xyz")
    r = _run_doctor(env)
    assert any(ln.startswith("MISSING docker-absent-xyz compose") for ln in r.stdout.splitlines()), r.stdout


def test_doctor_compose_flags_a_missing_env_file(tmp_path):
    # the envelope cannot interpolate without its image/DRM/GID values: a missing file with the values unset is
    # a MISSING check, not a note (a false green here let `serve` fail later inside docker compose)
    env = _doctor_env(tmp_path, A770B_SERVE="compose", A770B_ALLOW_NO_NVTOP="1",
                      A770B_DOCKER="docker-absent-xyz", A770B_COMPOSE_ENV_FILE=str(tmp_path / "nope.env"))
    for v in ("A770B_LLAMA_IMAGE", "A770B_DRM_CARD", "A770B_DRM_RENDER", "A770B_RENDER_GID", "A770B_VIDEO_GID"):
        env.pop(v, None)
    r = _run_doctor(env)
    assert any(ln.startswith("MISSING compose env") for ln in r.stdout.splitlines()), r.stdout


def test_doctor_flags_missing_when_the_a770_defaults_are_in_force_on_a_non_matching_card(tmp_path):
    # fake nvtop (no device names it as DG2) and a fake llama-server whose --list-devices pins nothing:
    # both A770B_GPU_MATCH and A770B_VK_DEVICE_SELECT are left at this project's default, so doctor must say so.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_bin(bin_dir, "nvtop", 'printf \'[{"device_name": "NotTheA770", "mem_total": 8000000000, "mem_used": 100, "mem_free": 7999999900}]\'')
    llama = _make_bin(bin_dir, "llama-server", 'exit 0')  # --list-devices prints nothing: 0 Vulkan lines
    env = _doctor_env(tmp_path, A770B_LLAMA_BIN=str(llama))
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    r = _run_doctor(env)
    out = r.stdout
    assert any(
        ln.startswith("MISSING input A770B_GPU_MATCH=DG2") for ln in out.splitlines()
    ), out
    assert any(
        ln.startswith("MISSING input A770B_VK_DEVICE_SELECT=8086:56a0!") for ln in out.splitlines()
    ), out
