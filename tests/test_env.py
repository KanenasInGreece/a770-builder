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

import json
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
        # isolate from the operator's own ~/.config/a770-builder/builder.env (which may set A770B_CARD and friends):
        # a doctor test asserts what happens when the card knobs are unset, so it must not inherit a real config
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
    )
    env.update(overrides)
    return env


def _run_doctor(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"], capture_output=True, text=True, cwd=ROOT, env=env
    )


def test_doctor_refuses_the_card_knobs_when_unset(tmp_path):
    r = _run_doctor(_doctor_env(tmp_path))
    out = r.stdout
    for var in ("A770B_VK_DEVICE_SELECT", "A770B_GPU_MATCH", "A770B_VRAM_CAP_GIB", "A770B_CARD"):
        assert any(ln.startswith("MISSING input " + var + " is unset") for ln in out.splitlines()), out
    # A770B_UBATCH is a fixed default, not a card knob: it still reports as an input, never MISSING
    assert any(ln.startswith("ok   input A770B_UBATCH=") for ln in out.splitlines()), out
    assert "checkout predates A770B_SERVE" not in r.stderr
    assert "checkout predates A770B_SERVE" not in r.stdout


def test_doctor_accepts_a_set_card_knob():
    r = subprocess.run(
        ["bash", str(DOCTOR_SH), "doctor"],
        capture_output=True, text=True, cwd=ROOT,
        env=dict(os.environ, A770B_PROJECT=str(ROOT), A770B_REFUSE="/nonexistent",
                  A770B_GPU_MATCH="SomeOtherCard"),
    )
    out = r.stdout
    assert any(ln.startswith("ok   input A770B_GPU_MATCH=SomeOtherCard") for ln in out.splitlines()), out
    assert not any(ln.startswith("MISSING input A770B_GPU_MATCH") for ln in out.splitlines()), out


# ── A770B_SERVE: compose only ───────────────────────────────────────────────────────────────────────────────


def test_env_sh_defaults_serve_to_compose(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="")
    r = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_SERVE"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "compose"


def test_env_sh_refuses_an_unknown_serve_value(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="bogus")
    r = run_bash(f'set -e; . "{ENV_SH}"', env=env)
    assert r.returncode != 0
    assert "A770B_SERVE must be compose" in r.stderr


def test_env_sh_refuses_host(tmp_path):
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_SERVE="host")
    r = run_bash(f'set -e; . "{ENV_SH}"', env=env)
    assert r.returncode != 0
    assert "A770B_SERVE must be compose" in r.stderr


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


def test_doctor_refuses_an_unset_gpu_match_instead_of_matching_everything(tmp_path):
    # an unset A770B_GPU_MATCH must never be matched: '' is a substring of every device name, so on a one-card
    # machine it would read as "exactly one match" and pass — a false green. Doctor refuses it by name instead.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_bin(bin_dir, "nvtop", 'printf \'[{"device_name": "OnlyOneCard", "mem_total": 8000000000, "mem_used": 100, "mem_free": 7999999900}]\'')
    env = _doctor_env(tmp_path, A770B_ALLOW_NO_NVTOP="0")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    r = _run_doctor(env)
    out = r.stdout
    assert any(ln.startswith("MISSING input A770B_GPU_MATCH is unset") for ln in out.splitlines()), out
    assert not any(ln.startswith("ok   card: exactly one") for ln in out.splitlines()), out


# ── A770B_CARD injection: config/cards.json ─────────────────────────────────────────────────────────────────


def _cards_json(cards: dict) -> str:
    return json.dumps({"schema": 1, "cards": cards})


def test_card_injection_exports_values_from_row(tmp_path):
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        _cards_json({
            "b70": {
                "pci": "8086:e223",
                "vram_gib": 32,
                "gpu_match": "G31",
                "generation": "xe2",
                "vk_device_select": "8086:e223!",
                "oneapi_selector": "level_zero:gpu",
            }
        }),
        encoding="utf-8",
    )
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_CARDS_FILE=str(cards_file), A770B_CARD="b70")
    for k in ("A770B_VK_DEVICE_SELECT", "A770B_GPU_MATCH", "A770B_CARD_VRAM_TOTAL"):
        env.pop(k, None)
    # Child bash checks that variables are exported across the process boundary
    r = run_bash(
        f'. "{ENV_SH}"; bash -c \'printf "%s|%s|%s" "$A770B_VK_DEVICE_SELECT" "$A770B_GPU_MATCH" "$A770B_CARD_VRAM_TOTAL"\'',
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "8086:e223!|G31|32"


def test_card_injection_fails_on_unknown_card(tmp_path):
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        _cards_json({
            "b70": {
                "pci": "8086:e223",
                "vram_gib": 32,
                "gpu_match": "G31",
                "generation": "xe2",
                "vk_device_select": "8086:e223!",
                "oneapi_selector": "level_zero:gpu",
            }
        }),
        encoding="utf-8",
    )
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_CARDS_FILE=str(cards_file), A770B_CARD="unknown_card")
    r = run_bash(f'set -e; . "{ENV_SH}"', env=env)
    assert r.returncode == 2
    assert "A770B_CARD 'unknown_card' is not known" in r.stderr


def test_card_injection_environment_override_wins(tmp_path):
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        _cards_json({
            "b70": {
                "pci": "8086:e223",
                "vram_gib": 32,
                "gpu_match": "G31",
                "generation": "xe2",
                "vk_device_select": "8086:e223!",
                "oneapi_selector": "level_zero:gpu",
            }
        }),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        A770B_DATA=str(tmp_path / "data"),
        A770B_CARDS_FILE=str(cards_file),
        A770B_CARD="b70",
        A770B_VK_DEVICE_SELECT="custom:vk!",
        A770B_GPU_MATCH="CustomGPU",
        A770B_CARD_VRAM_TOTAL="64",
    )
    r = run_bash(
        f'. "{ENV_SH}"; bash -c \'printf "%s|%s|%s" "$A770B_VK_DEVICE_SELECT" "$A770B_GPU_MATCH" "$A770B_CARD_VRAM_TOTAL"\'',
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "custom:vk!|CustomGPU|64"


def test_card_injection_refuses_non_builder_role(tmp_path):
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        _cards_json({
            "b580": {
                "pci": "8086:e20b",
                "vram_gib": 12,
                "gpu_match": "G21",
                "generation": "xe2",
                "role": "encoder",
                "vk_device_select": "8086:e20b!",
                "oneapi_selector": "level_zero:gpu",
            },
            "b70": {
                "pci": "8086:e223",
                "vram_gib": 32,
                "gpu_match": "G31",
                "generation": "xe2",
                "role": "builder",
                "vk_device_select": "8086:e223!",
                "oneapi_selector": "level_zero:gpu",
            },
        }),
        encoding="utf-8",
    )
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_CARDS_FILE=str(cards_file), A770B_CARD="b580")
    r = run_bash(f'set -e; . "{ENV_SH}"', env=env)
    assert r.returncode == 2
    assert "A770B_CARD 'b580' has role 'encoder'" in r.stderr
    assert "must be a card with role 'builder'" in r.stderr


def test_card_role_override_accepts_an_encoder_card(tmp_path):
    """A770B_CARD_ROLE overrides the file's role: a community B580 builder sets it to builder and
    the encoder card is accepted, its selectors supplied from its cards.json row."""
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        _cards_json({
            "b580": {
                "pci": "8086:e20b",
                "vram_gib": 12,
                "gpu_match": "G21",
                "generation": "xe2",
                "role": "encoder",
                "vk_device_select": "8086:e20b!",
                "oneapi_selector": "level_zero:gpu",
            },
        }),
        encoding="utf-8",
    )
    env = dict(os.environ, A770B_DATA=str(tmp_path / "data"), A770B_CARDS_FILE=str(cards_file),
               A770B_CARD="b580", A770B_CARD_ROLE="builder")
    for k in ("A770B_VK_DEVICE_SELECT", "A770B_GPU_MATCH", "A770B_CARD_VRAM_TOTAL"):
        env.pop(k, None)
    r = run_bash(
        f'. "{ENV_SH}"; bash -c \'printf "%s|%s" "$A770B_VK_DEVICE_SELECT" "$A770B_GPU_MATCH"\'',
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "8086:e20b!|G21"


def test_env_resolves_only_the_installed_cards_registry(tmp_path):
    """I1 — a temp registry with files for two cards: env.sh under each lists only its own card's
    rows, so a B70 host never sees the A770's measured rows."""
    proj = tmp_path / "proj"
    (proj / "config" / "registry").mkdir(parents=True)
    (proj / "harness").mkdir(parents=True)
    for rel in ("harness/profiles.py", "harness/live_backend.py"):
        (proj / rel).write_bytes((ROOT / rel).read_bytes())
    display = json.loads((ROOT / "config" / "registry" / "a770.display.json").read_text(encoding="utf-8"))

    (proj / "config" / "registry" / "a770.display.json").write_text(json.dumps(display), encoding="utf-8")

    b70 = dict(display)
    b70["card"] = "b70"
    row = b70["profiles"].pop("qwen35-9b-q4km-vulkan")
    row["card"] = "b70"
    b70["profiles"] = {"beta-9b-vulkan": row}
    (proj / "config" / "registry" / "b70.display.json").write_text(json.dumps(b70), encoding="utf-8")

    (proj / "config" / "cards.json").write_text(
        json.dumps({"schema": 1, "cards": {"a770": {"role": "builder"}, "b70": {"role": "builder"}}}),
        encoding="utf-8",
    )

    base = dict(os.environ, A770B_PROJECT=str(proj), A770B_DATA=str(tmp_path / "data"),
                A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))

    r_first = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_PROFILES"', env=dict(base, A770B_CARD="a770"))
    assert r_first.returncode == 0, r_first.stderr
    assert "qwen35-9b-q4km-vulkan" in r_first.stdout
    assert "beta-9b-vulkan" not in r_first.stdout

    r_second = run_bash(f'. "{ENV_SH}"; printf "%s" "$A770B_PROFILES"', env=dict(base, A770B_CARD="b70"))
    assert r_second.returncode == 0, r_second.stderr
    assert "beta-9b-vulkan" in r_second.stdout
    assert "qwen35-9b-q4km-vulkan" not in r_second.stdout


def test_env_missing_registry_is_not_an_error(tmp_path):
    """I5 — a card with no registry file sources cleanly: an empty A770B_PROFILES, no refusal."""
    env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"),
               A770B_CARD="b70", A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    r = run_bash(f'. "{ENV_SH}"; printf "[%s]" "$A770B_PROFILES"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "[]"


def test_env_unset_card_is_not_an_error(tmp_path):
    """I5 — no A770B_CARD sources cleanly: an empty A770B_PROFILES, no refusal."""
    env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"),
               A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    env.pop("A770B_CARD", None)
    r = run_bash(f'. "{ENV_SH}"; printf "[%s]" "$A770B_PROFILES"', env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "[]"


def _one_card_project(tmp_path, registry_text: str) -> Path:
    """A temp project whose a770.display.json holds `registry_text` verbatim."""
    proj = tmp_path / "proj"
    (proj / "config" / "registry").mkdir(parents=True)
    (proj / "harness").mkdir(parents=True)
    for rel in ("harness/profiles.py", "harness/live_backend.py"):
        (proj / rel).write_bytes((ROOT / rel).read_bytes())
    (proj / "config" / "cards.json").write_text(
        json.dumps({"schema": 1, "cards": {"a770": {"role": "builder"}}}), encoding="utf-8")
    (proj / "config" / "registry" / "a770.display.json").write_text(registry_text, encoding="utf-8")
    return proj


def test_env_invalid_registry_still_fails_hard(tmp_path):
    """I5 / F4 — only a MISSING registry is empty; an existing file that does not check stops env.sh."""
    proj = _one_card_project(tmp_path, "{ not json")
    env = dict(os.environ, A770B_PROJECT=str(proj), A770B_DATA=str(tmp_path / "data"), A770B_CARD="a770",
               A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    r = run_bash(f'. "{ENV_SH}" || exit $?; echo sourced', env=env)
    assert r.returncode != 0
    assert "sourced" not in r.stdout
    assert "is invalid" in r.stderr


def test_env_explicit_registry_file_must_exist(tmp_path):
    """F5 — an A770B_PROFILES_FILE the caller named must exist; only the path resolved from the card may be missing."""
    env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"), A770B_CARD="a770",
               A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"),
               A770B_PROFILES_FILE=str(ROOT / "config" / "profiles.json"))
    r = run_bash(f'. "{ENV_SH}" || exit $?; echo sourced', env=env)
    assert r.returncode != 0
    assert "sourced" not in r.stdout
    assert "does not exist" in r.stderr


def test_env_refuses_a_card_that_is_not_a_card_id(tmp_path):
    """F2 — the card becomes part of a path, so a value with a separator, a dot-dot, a space or a leading hyphen is
    refused before any path is built."""
    for card in ("../../tmp/evil", "a/b", "a 7", "-x", "A770"):
        env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"), A770B_CARD=card,
                   A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
        env.pop("A770B_PROFILES_FILE", None)
        r = run_bash(f'. "{ENV_SH}" || exit $?; echo sourced', env=env)
        assert r.returncode != 0, card
        assert "not a card id" in r.stderr, (card, r.stderr)


def test_env_doctor_builds_no_registry_path_from_a_bad_card(tmp_path):
    """F2 — doctor carries on past a bad or unknown card, but resolves no registry path from it."""
    for card in ("../../tmp/evil", "zebra"):
        env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"), A770B_CARD=card,
                   A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
        env.pop("A770B_PROFILES_FILE", None)
        r = run_bash(f'set -- doctor; . "{ENV_SH}"; printf "[%s][%s]" "${{A770B_PROFILES_FILE:-}}" "$A770B_PROFILES"',
                     env=env)
        assert r.returncode == 0, (card, r.stderr)
        assert r.stdout == "[][]", (card, r.stdout)


def test_doctor_reports_missing_for_unknown_card(tmp_path):
    env = _doctor_env(tmp_path, A770B_CARD="nonexistent_card")
    r = _run_doctor(env)
    assert r.returncode != 0
    assert any(ln.startswith("MISSING input A770B_CARD=nonexistent_card is unknown") for ln in r.stdout.splitlines()), r.stdout
    assert "checkout predates A770B_SERVE" not in r.stderr
    assert "checkout predates A770B_SERVE" not in r.stdout


def test_doctor_reports_missing_for_encoder_card(tmp_path):
    # b580 has role encoder, so doctor must report it unknown / not a builder card
    env = _doctor_env(tmp_path, A770B_CARD="b580")
    r = _run_doctor(env)
    assert r.returncode != 0
    assert any(ln.startswith("MISSING input A770B_CARD=b580 is unknown") for ln in r.stdout.splitlines()), r.stdout
    assert "checkout predates A770B_SERVE" not in r.stderr
    assert "checkout predates A770B_SERVE" not in r.stdout


def test_doctor_accepts_an_encoder_card_with_role_override(tmp_path):
    # A770B_CARD_ROLE=builder overrides the file's encoder role, so doctor accepts the card as the builder.
    env = _doctor_env(tmp_path, A770B_CARD="b580", A770B_CARD_ROLE="builder")
    r = _run_doctor(env)
    assert any(ln.startswith("ok   input A770B_CARD=b580") for ln in r.stdout.splitlines()), r.stdout


def test_doctor_in_vllm_named_directory_does_not_misfire_as_vllm(tmp_path):
    # If the project clone or compose file is inside a directory whose path contains 'vllm'
    vllm_dir = tmp_path / "my-vllm-workspace" / "a770-builder"
    vllm_compose = vllm_dir / "compose" / "a770-vulkan.yaml"
    env = _doctor_env(
        tmp_path,
        A770B_SERVE="compose",
        A770B_ALLOW_NO_NVTOP="1",
        A770B_COMPOSE_FILE=str(vllm_compose),
        A770B_SERVED_BACKEND="vulkan",
    )
    r = _run_doctor(env)
    out = r.stdout + r.stderr
    assert "A770B_VLLM_IMAGE" not in out
    assert "A770B_BY_PATH_DIR" not in out


def test_menu_with_no_registry_keeps_the_json_shape():
    """F10 — a card with no registry gets the same menu JSON, empty, with the one line under "note"."""
    r = subprocess.run([sys.executable, str(ROOT / "harness" / "profiles.py"), "menu", "--card", "b70", "--mode", "inference"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ready"] == [] and out["also"] == [] and out["slice"] == []
    assert out["note"] == "no measured models for card b70 in inference mode — climb one with ladder.sh <gguf> --ctx N"


def test_bench_speed_refuses_with_the_one_line(tmp_path):
    """F10 — bench_speed.sh refuses a profile this card does not have with the shared refusal, not its own words."""
    env = dict(os.environ, A770B_PROJECT=str(ROOT), A770B_DATA=str(tmp_path / "data"), A770B_REFUSE="/nonexistent",
               A770B_CARD="b70", A770B_CARD_MODE="display", XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    env.pop("A770B_PROFILES_FILE", None)
    r = subprocess.run(["bash", str(ROOT / "harness" / "bench_speed.sh"), "qwen35-9b-q4km-vulkan", "--dry-run"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 2
    assert "no measured models for card b70 in display mode" in r.stderr
