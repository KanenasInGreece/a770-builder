#!/usr/bin/env python3
"""Resolver tests for skills/local-build/scripts/profile_engine.sh. No network, no docker."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "skills" / "local-build" / "scripts" / "profile_engine.sh"

HARNESS = r"""
set -uo pipefail
die(){ echo "$*" >&2; return 2; }
a770b_profile_var(){
  case "$2" in
    ENGINE) printf '%s\n' "${STUB_ENGINE}";;
    BACKEND) printf '%s\n' "${STUB_BACKEND}";;
    *) printf '\n';;
  esac
}
. "$RESOLVER"
_profile_engine "$PROFILE_NAME"
"""


def resolve(engine, backend, name="row-a"):
    return subprocess.run(
        ["bash", "-c", HARNESS],
        capture_output=True,
        text=True,
        env={
            "RESOLVER": str(RESOLVER),
            "STUB_ENGINE": engine,
            "STUB_BACKEND": backend,
            "PROFILE_NAME": name,
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.mark.parametrize(
    "engine,backend,expected",
    [
        ("", "", "llama.cpp"),
        ("", "vulkan", "llama.cpp"),
        ("", "sycl", "llama.cpp"),
        ("", "vllm", "vllm"),
        ("llama.cpp", "", "llama.cpp"),
        ("llama.cpp", "vulkan", "llama.cpp"),
        ("llama.cpp", "sycl", "llama.cpp"),
        ("vllm", "vllm", "vllm"),
    ],
)
def test_profile_engine_prints(engine, backend, expected):
    result = resolve(engine, backend)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    assert result.stderr == ""


def test_empty_engine_refuses_unknown_backend():
    result = resolve("", "cuda")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "backend 'cuda' is not one this harness serves (vulkan, sycl, vllm)\n"


@pytest.mark.parametrize("backend", ["vllm", "cuda"])
def test_llamacpp_refuses_other_backend(backend):
    result = resolve("llama.cpp", backend, name="qwen")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"profile qwen engine llama.cpp does not match backend {backend}\n"


@pytest.mark.parametrize("backend", ["", "vulkan", "sycl", "cuda"])
def test_vllm_refuses_other_backend(backend):
    result = resolve("vllm", backend, name="exl")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"profile exl engine vllm does not match backend {backend}\n"


@pytest.mark.parametrize(
    "engine,backend",
    [
        ("sglang", "vulkan"),
        ("sglang", ""),
        ("sglang", "vllm"),
        ("exllamav2", "sycl"),
    ],
)
def test_unknown_engine_is_refused_not_defaulted(engine, backend):
    result = resolve(engine, backend, name="row-a")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        f"profile row-a engine '{engine}' is not one this skill serves (llama.cpp, vllm)\n"
    )
