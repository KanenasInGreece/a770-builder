#!/usr/bin/env python3
"""Evidence extraction module: parse engine logs, Prometheus metrics, and GGUF headers.

Extracts runtime parameters, kernel lines, Prometheus counters and GGUF weight
mixes into machine-readable evidence without external dependencies (stdlib only).
"""

import argparse
import io
import json
import re
import struct
import sys
from pathlib import Path
from typing import Optional, Union

VLLM_KERNEL_PATTERNS = [
    r"gptq_gemm",
    r"XPUwNa16LinearKernel",
    r"int4_gemm_w4a16",
    r"Marlin",
    r"Using .*Attention backend",
    r"FlashAttention version",
    r"GDN prefill kernel",
    r"XPU Graph",
    r"Falling back to PyTorch-native",
    r"Triton kernel JIT compilation",
    r"speculative_config=(?!None)",
]

LLAMACPP_KERNEL_PATTERNS = [
    r"matrix cores",
    r"int dot",
    r"load_backend",
    r"offloaded",
    r"XMX",
    r"ggml_sycl",
    r"ggml_vulkan",
    r"using device",
]

GGML_TYPES = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
}

# GGUF stream boundary limits to protect memory from corrupted headers (R2)
GGUF_MAX_KEY_LENGTH = 65536
GGUF_MAX_STRING_LENGTH = 16 * 1024 * 1024  # 16 MiB
GGUF_MAX_ARRAY_COUNT = 16_000_000
GGUF_MAX_TENSOR_COUNT = 1_000_000
GGUF_MAX_KV_COUNT = 100_000
GGUF_MAX_DIMS = 8

VLLM_COUNTERS = {
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:request_success_total",
    "vllm:time_to_first_token_seconds_sum",
    "vllm:time_to_first_token_seconds_count",
    "vllm:request_prefill_time_seconds_sum",
    "vllm:request_prefill_time_seconds_count",
    "vllm:request_decode_time_seconds_sum",
    "vllm:request_decode_time_seconds_count",
    "vllm:request_prompt_tokens_sum",
    "vllm:request_prompt_tokens_count",
    "vllm:request_generation_tokens_sum",
    "vllm:request_generation_tokens_count",
    "vllm:inter_token_latency_seconds_sum",
    "vllm:inter_token_latency_seconds_count",
}

LLAMACPP_COUNTERS = {
    "llamacpp:prompt_tokens_total",
    "llamacpp:tokens_predicted_total",
    "llamacpp:prompt_seconds_total",
    "llamacpp:tokens_predicted_seconds_total",
    "llamacpp:n_decode_total",
}


def tensor_bytes(ggml_type: int, n_elements: int) -> Optional[int]:
    """Compute tensor byte footprint from element count and GGML type."""
    if ggml_type not in GGML_TYPES:
        return None
    _, block_size, type_size = GGML_TYPES[ggml_type]
    return (n_elements // block_size) * type_size


def _extract_config_kv(config_text: str, key: str) -> Optional[str]:
    """Extract a name=value pair from a vLLM engine config line."""
    m = re.search(r"\b" + re.escape(key) + r"=(?:'([^']*)'|\"([^\"]*)\"|([^,)]*))", config_text)
    if not m:
        return None
    val = m.group(1) or m.group(2) or m.group(3)
    if val is None:
        return None
    val = val.strip()
    return None if val == "None" else val


def parse_vllm_log(text: str) -> dict:
    """Parse vLLM server log output into an evidence dictionary."""
    result = {
        "engine": "vllm",
        "version": None,
        "activation_dtype": None,
        "quantization": None,
        "kv_cache_dtype": None,
        "max_seq_len": None,
        "enforce_eager": None,
        "device": None,
        "load_gib": None,
        "kv_cache_gib": None,
        "kv_cache_tokens": None,
        "max_concurrency": None,
        "init_s": None,
        "compile_s": None,
        "kernel_lines": [],
        "attention_backend": None,
    }
    if not text:
        return result

    m_ver = re.search(r"Initializing a V1 LLM engine \((?:v)?([^)]+)\)", text)
    if m_ver:
        result["version"] = m_ver.group(1).strip()

    config_line = ""
    for line in text.splitlines():
        if "Initializing a V1 LLM engine" in line and "with config:" in line:
            config_line = line
            break

    if config_line:
        dtype_val = _extract_config_kv(config_line, "dtype")
        if dtype_val:
            if dtype_val.startswith("torch."):
                dtype_val = dtype_val[len("torch."):]
            result["activation_dtype"] = dtype_val
        result["quantization"] = _extract_config_kv(config_line, "quantization")
        result["kv_cache_dtype"] = _extract_config_kv(config_line, "kv_cache_dtype")
        max_seq = _extract_config_kv(config_line, "max_seq_len")
        if max_seq and max_seq.isdigit():
            result["max_seq_len"] = int(max_seq)
        eager_val = _extract_config_kv(config_line, "enforce_eager")
        if eager_val in ("True", "False"):
            result["enforce_eager"] = eager_val == "True"
        result["device"] = _extract_config_kv(config_line, "device_config") or _extract_config_kv(config_line, "device")

    m_load = re.search(r"Model loading took\s+([0-9.]+)\s+GiB", text)
    if m_load:
        result["load_gib"] = float(m_load.group(1))
    m_kv_gib = re.search(r"Available KV cache memory:\s*([0-9.]+)\s*GiB", text)
    if m_kv_gib:
        result["kv_cache_gib"] = float(m_kv_gib.group(1))
    m_kv_tok = re.search(r"GPU KV cache size:\s*([0-9,]+)\s*tokens", text)
    if m_kv_tok:
        result["kv_cache_tokens"] = int(m_kv_tok.group(1).replace(",", ""))
    m_conc = re.search(r"Maximum concurrency for .*?:\s*([0-9.]+)x", text)
    if m_conc:
        result["max_concurrency"] = float(m_conc.group(1))
    m_init = re.search(r"init engine .*? took\s+([0-9.]+)\s+s", text)
    if m_init:
        result["init_s"] = float(m_init.group(1))
    m_comp = re.search(r"\(compilation:\s*([0-9.]+)\s+s\)", text)
    if m_comp:
        result["compile_s"] = float(m_comp.group(1))

    compiled_vllm = [re.compile(p) for p in VLLM_KERNEL_PATTERNS]
    seen_messages = set()
    kernel_lines = []
    for line in text.splitlines():
        if any(p.search(line) for p in compiled_vllm):
            m_pref = re.search(r"\[[a-zA-Z0-9_.-]+:\d+\]\s*(.*)", line)
            msg = m_pref.group(1).strip() if m_pref else line.strip()
            if msg and msg not in seen_messages:
                seen_messages.add(msg)
                kernel_lines.append(msg)
                if len(kernel_lines) >= 40:
                    break
    result["kernel_lines"] = kernel_lines

    backend_word = None
    m_back = re.search(r"Using\s+(.*?)\s+(?:backend|Attention backend)", text, re.IGNORECASE)
    if m_back:
        raw_b = m_back.group(1).strip().lower()
        backend_word = "flash_attn" if "flash" in raw_b else raw_b.replace(" ", "_")
    m_ver_attn = re.search(r"(?:FlashAttention|backend)\s+version\s*([0-9a-zA-Z._-]+)", text, re.IGNORECASE)
    ver_word = m_ver_attn.group(1).strip() if m_ver_attn else None

    if backend_word and ver_word:
        result["attention_backend"] = f"{backend_word} {ver_word}"
    elif backend_word:
        result["attention_backend"] = backend_word
    elif ver_word:
        result["attention_backend"] = ver_word

    return result


LLAMACPP_LINE_PREFIX_RE = re.compile(r"^[0-9][0-9.]*[ \t]+[A-Za-z][ \t]+", re.MULTILINE)


def parse_llamacpp_log(text: str) -> dict:
    """Parse llama.cpp server log output into an evidence dictionary."""
    result = {
        "engine": "llama.cpp",
        "build": None,
        "compiler": None,
        "backend": None,
        "device": None,
        "offload": None,
        "file_type": None,
        "tensor_type_counts": {},
        "model_buffer_mib": None,
        "host_buffers_mib": None,
        "host_buffers": [],
        "kv_cache": None,
        "flash_attn": None,
        "unused_tensors": None,
        "unused_bytes": None,
        "sampler": None,
        "kernel_lines": [],
    }
    if not text:
        return result

    # llama.cpp's -lv 4 (and higher) log lines carry a "0.00.186.536 I " elapsed-time/level prefix before the
    # module name; strip it so every regex below (and kernel_lines) reads the message, not the timestamp
    text = LLAMACPP_LINE_PREFIX_RE.sub("", text)

    m_build = re.search(r"\bbuild(?:\s*[:=]|\s+)\s*([0-9]+(?:\s*\([0-9a-fA-F]+\))?)", text)
    if m_build:
        result["build"] = m_build.group(1).strip()

    # "build 10920 (eafe15a5e) with IntelLLVM 2025.3.3 for Linux x86_64" — the compiler name+version between
    # "with" and "for"
    m_compiler = re.search(r"\bwith\s+(\S+\s+[0-9][0-9.]*)\s+for\b", text)
    if m_compiler:
        result["compiler"] = m_compiler.group(1).strip()

    loaded = re.findall(r"load_backend:\s*loaded\s+(\w+)\s+backend", text, re.IGNORECASE)
    gpu_names = {"sycl", "vulkan", "cuda", "rocm", "metal", "opencl", "kompute"}
    chosen = None
    for b in loaded:
        if b.lower() in gpu_names:
            chosen = b
            break
    if chosen is None and loaded:
        chosen = loaded[0]
    result["backend"] = chosen

    # the device_info line: "  - SYCL0   : Intel(R) Arc(TM) Pro B70 Graphics (32656 MiB, 32581 MiB free)" —
    # device name and total MiB (the CPU line on the row above/below it never matches, having no SYCL/Vulkan id)
    m_dev = re.search(
        r"(SYCL|Vulkan|CUDA)\d*\s*:\s*(.+?)\s*\((\d+(?:\.\d+)?)\s*MiB,\s*\d+(?:\.\d+)?\s*MiB free\)", text
    )
    if m_dev:
        result["device"] = {"name": m_dev.group(2).strip(), "total_mib": float(m_dev.group(3))}
        # at -lv 4 this build prints no load_backend line; the device id (SYCL0, Vulkan0) names the backend
        if result["backend"] is None:
            result["backend"] = m_dev.group(1)

    m_off = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers", text)
    if m_off:
        result["offload"] = {"layers": int(m_off.group(1)), "of": int(m_off.group(2))}

    m_ft = re.search(r"file type\s*=\s*([A-Za-z0-9_]+)", text)
    if m_ft:
        result["file_type"] = m_ft.group(1).strip()

    # the model-loader tensor listing: "llama_model_loader: - type q6_K:  241 tensors" — one line per type
    tensor_counts = {}
    for tname, tcount in re.findall(r"-\s*type\s+(\S+):\s*(\d+)\s+tensors", text):
        tensor_counts[tname] = int(tcount)
    result["tensor_type_counts"] = tensor_counts

    # the device-side model buffer: "load_tensors: SYCL0 model buffer size = 19625.41 MiB" — the first match
    # whose device name is not a *_Host entry (those are host_buffers, below)
    for dname, dval in re.findall(r"(\S+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB", text):
        if not dname.endswith("_Host"):
            result["model_buffer_mib"] = float(dval)
            break

    # every "<name>_Host <kind> buffer size = N MiB" line (model/output/compute…) — summed into host_buffers_mib
    # (this project's ram_gb_extra) and kept individually as host_buffers
    host_matches = re.findall(r"(\S+_Host)\s+([a-z]+)\s+buffer size\s*=\s*([\d.]+)\s*MiB", text)
    if host_matches:
        host_buffers = [(f"{hname} {hkind}", float(hval)) for hname, hkind, hval in host_matches]
        result["host_buffers"] = [{"name": n, "mib": v} for n, v in host_buffers]
        result["host_buffers_mib"] = round(sum(v for _, v in host_buffers), 2)

    m_kv = re.search(r"K\s*\(([^)]+)\):\s*([0-9.]+)\s*MiB.*?V\s*\(([^)]+)\):\s*([0-9.]+)\s*MiB", text)
    if m_kv:
        def _parse_num(s: str):
            f = float(s)
            return int(f) if f.is_integer() else f
        result["kv_cache"] = {
            "k_type": m_kv.group(1).strip(),
            "v_type": m_kv.group(3).strip(),
            "k_mib": _parse_num(m_kv.group(2)),
            "v_mib": _parse_num(m_kv.group(4)),
        }

    m_fa = re.search(r"\bflash_attn\s*=\s*(\S+)", text)
    if m_fa:
        result["flash_attn"] = m_fa.group(1).strip()

    # "model has unused tensor blk.64.attn_norm.weight (size = 20480 bytes) -- ignoring" — one warning per
    # tensor a shorter run (fewer nextn layers, MTP disabled, …) leaves unloaded
    unused_sizes = [int(b) for b in re.findall(r"model has unused tensor \S+ \(size = (\d+) bytes\)", text)]
    if unused_sizes:
        result["unused_tensors"] = len(unused_sizes)
        result["unused_bytes"] = sum(unused_sizes)

    # "sampler params: \n\ttop_k = 40, top_p = 0.950, min_p = 0.050, temp = 0.800\n\trepeat_penalty = 1.000, …"
    # — llama.cpp prints this once per request (never at server startup with no request served), as an indented
    # block; the five sampling numbers this project tracks, wherever they land in that block
    m_samp = re.search(r"sampler params:\s*\n((?:[ \t]+\S.*\n?)+)", text)
    if m_samp:
        block = m_samp.group(1)

        def _sampler_num(key: str):
            mm = re.search(r"\b" + re.escape(key) + r"\s*=\s*(-?[0-9.]+)", block)
            return float(mm.group(1)) if mm else None

        top_k = _sampler_num("top_k")
        result["sampler"] = {
            "top_k": int(top_k) if top_k is not None else None,
            "top_p": _sampler_num("top_p"),
            "min_p": _sampler_num("min_p"),
            "temp": _sampler_num("temp"),
            "repeat_penalty": _sampler_num("repeat_penalty"),
        }

    compiled_patterns = [re.compile(p, re.IGNORECASE) for p in LLAMACPP_KERNEL_PATTERNS]
    seen_messages = set()
    kernel_lines = []
    for line in text.splitlines():
        if any(p.search(line) for p in compiled_patterns):
            prefix_match = re.search(r"\[[a-zA-Z0-9_.-]+:\d+\]\s*(.*)", line)
            msg = prefix_match.group(1).strip() if prefix_match else line.strip()
            if msg and msg not in seen_messages:
                seen_messages.add(msg)
                kernel_lines.append(msg)
                if len(kernel_lines) >= 40:
                    break
    result["kernel_lines"] = kernel_lines
    return result


def _read_exact(stream, n: int) -> bytes:
    data = stream.read(n)
    if len(data) < n:
        raise EOFError(f"Unexpected end of stream: expected {n} bytes, got {len(data)}")
    return data


def _read_gguf_val(stream, vtype: int):
    if vtype == 0:
        return struct.unpack("<B", _read_exact(stream, 1))[0]
    elif vtype == 1:
        return struct.unpack("<b", _read_exact(stream, 1))[0]
    elif vtype == 2:
        return struct.unpack("<H", _read_exact(stream, 2))[0]
    elif vtype == 3:
        return struct.unpack("<h", _read_exact(stream, 2))[0]
    elif vtype == 4:
        return struct.unpack("<I", _read_exact(stream, 4))[0]
    elif vtype == 5:
        return struct.unpack("<i", _read_exact(stream, 4))[0]
    elif vtype == 6:
        return struct.unpack("<f", _read_exact(stream, 4))[0]
    elif vtype == 7:
        return bool(_read_exact(stream, 1)[0])
    elif vtype == 8:
        slen = struct.unpack("<Q", _read_exact(stream, 8))[0]
        if slen > GGUF_MAX_STRING_LENGTH:
            raise ValueError(f"GGUF string value length exceeds limit: {slen} > {GGUF_MAX_STRING_LENGTH}")
        return _read_exact(stream, slen).decode("utf-8", errors="replace")
    elif vtype == 9:
        elem_type = struct.unpack("<I", _read_exact(stream, 4))[0]
        count = struct.unpack("<Q", _read_exact(stream, 8))[0]
        if count > GGUF_MAX_ARRAY_COUNT:
            raise ValueError(f"GGUF array count exceeds limit: {count} > {GGUF_MAX_ARRAY_COUNT}")
        return [_read_gguf_val(stream, elem_type) for _ in range(count)]
    elif vtype == 10:
        return struct.unpack("<Q", _read_exact(stream, 8))[0]
    elif vtype == 11:
        return struct.unpack("<q", _read_exact(stream, 8))[0]
    elif vtype == 12:
        return struct.unpack("<d", _read_exact(stream, 8))[0]
    raise ValueError(f"Unknown GGUF value type {vtype}")


def _parse_gguf_stream(stream) -> dict:
    magic = _read_exact(stream, 4)
    if magic != b"GGUF":
        raise ValueError(f"Not a GGUF file: magic is {magic!r}")
    version = struct.unpack("<I", _read_exact(stream, 4))[0]
    if version not in (2, 3):
        raise ValueError(f"Unsupported GGUF version: {version}")

    tensor_count = struct.unpack("<Q", _read_exact(stream, 8))[0]
    if tensor_count > GGUF_MAX_TENSOR_COUNT:
        raise ValueError(f"GGUF tensor_count exceeds limit: {tensor_count} > {GGUF_MAX_TENSOR_COUNT}")

    kv_count = struct.unpack("<Q", _read_exact(stream, 8))[0]
    if kv_count > GGUF_MAX_KV_COUNT:
        raise ValueError(f"GGUF kv_count exceeds limit: {kv_count} > {GGUF_MAX_KV_COUNT}")

    file_type = None
    architecture = None
    for _ in range(kv_count):
        klen = struct.unpack("<Q", _read_exact(stream, 8))[0]
        if klen > GGUF_MAX_KEY_LENGTH:
            raise ValueError(f"GGUF key length exceeds limit: {klen} > {GGUF_MAX_KEY_LENGTH}")
        key = _read_exact(stream, klen).decode("utf-8", errors="replace")
        vtype = struct.unpack("<I", _read_exact(stream, 4))[0]
        val = _read_gguf_val(stream, vtype)
        if key == "general.file_type":
            file_type = int(val)
        elif key == "general.architecture":
            architecture = str(val)

    tensor_type_bytes = {}
    unknown_types = []
    prediction_head_tensors = 0

    for _ in range(tensor_count):
        nlen = struct.unpack("<Q", _read_exact(stream, 8))[0]
        if nlen > GGUF_MAX_KEY_LENGTH:
            raise ValueError(f"GGUF tensor name length exceeds limit: {nlen} > {GGUF_MAX_KEY_LENGTH}")
        tname = _read_exact(stream, nlen).decode("utf-8", errors="replace")
        n_dims = struct.unpack("<I", _read_exact(stream, 4))[0]
        if n_dims > GGUF_MAX_DIMS:
            raise ValueError(f"GGUF n_dims exceeds limit: {n_dims} > {GGUF_MAX_DIMS}")
        dims = [struct.unpack("<Q", _read_exact(stream, 8))[0] for _ in range(n_dims)]
        ggml_type = struct.unpack("<I", _read_exact(stream, 4))[0]
        _read_exact(stream, 8)

        if ".nextn." in tname:
            prediction_head_tensors += 1

        n_elements = 1
        for d in dims:
            n_elements *= d

        if ggml_type in GGML_TYPES:
            tname_type, block_size, type_size = GGML_TYPES[ggml_type]
            t_bytes = (n_elements // block_size) * type_size
            tensor_type_bytes[tname_type] = tensor_type_bytes.get(tname_type, 0) + t_bytes
        else:
            uk = f"type_{ggml_type}"
            if uk not in unknown_types:
                unknown_types.append(uk)

    total_bytes = sum(tensor_type_bytes.values())
    mix = {}
    if total_bytes > 0:
        for tname_type, b in tensor_type_bytes.items():
            mix[tname_type] = round((b / total_bytes) * 100.0, 1)

    mix = dict(sorted(mix.items(), key=lambda item: (-item[1], item[0])))
    return {
        "file_type": file_type,
        "architecture": architecture,
        "tensor_count": tensor_count,
        "total_bytes": total_bytes,
        "type_bytes": tensor_type_bytes,
        "mix": mix,
        "prediction_head_tensors": prediction_head_tensors,
        "unknown_types": unknown_types,
    }


def gguf_tensor_mix(data: bytes) -> dict:
    """Calculate the tensor-type byte mix from in-memory GGUF bytes."""
    return _parse_gguf_stream(io.BytesIO(data))


def gguf_tensor_mix_path(path: Union[str, Path]) -> dict:
    """Read only the GGUF header and tensor info chunks from disk."""
    path_obj = Path(path)
    if not path_obj.is_file():
        return {
            "file_type": None,
            "architecture": None,
            "tensor_count": None,
            "total_bytes": None,
            "type_bytes": {},
            "mix": {},
            "prediction_head_tensors": None,
            "unknown_types": [],
            "notes": [f"file not found: {path}"],
        }
    try:
        with open(path_obj, "rb") as f:
            return _parse_gguf_stream(f)
    except Exception as e:
        return {
            "file_type": None,
            "architecture": None,
            "tensor_count": None,
            "total_bytes": None,
            "type_bytes": {},
            "mix": {},
            "prediction_head_tensors": None,
            "unknown_types": [],
            "notes": [f"cannot parse GGUF {path}: {e}"],
        }


def parse_prometheus(text: str) -> dict:
    """Parse Prometheus text lines into {(name, sorted_labels_tuple): float}."""
    parsed = {}
    if not text:
        return parsed
    line_re = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([^\s]+)(?:\s+\d+)?$")
    label_re = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = line_re.match(line)
        if not m:
            continue
        name, raw_labels, val_str = m.group(1), m.group(2), m.group(3)
        labels = []
        if raw_labels:
            for km, vm in label_re.findall(raw_labels):
                cleaned_val = vm.replace(r"\"", '"').replace(r"\\", "\\").replace(r"\n", "\n")
                labels.append((km, cleaned_val))
        labels_tuple = tuple(sorted(labels))
        try:
            parsed[(name, labels_tuple)] = float(val_str)
        except ValueError:
            continue
    return parsed


def _extract_buckets(before: dict, after: dict, metric_name: str, restarted: bool) -> list[tuple[float, float]]:
    le_bounds = set()
    for name, labels in list(before.keys()) + list(after.keys()):
        if name == metric_name:
            ld = dict(labels)
            if "le" in ld:
                le_str = ld["le"]
                le_val = float("inf") if le_str in ("+Inf", "inf", "+inf") else float(le_str)
                le_bounds.add((le_val, le_str))
    if not le_bounds:
        return []

    buckets = []
    for le_val, le_str in sorted(le_bounds, key=lambda x: x[0]):
        b_sum = sum(v for (n, l), v in before.items() if n == metric_name and dict(l).get("le") == le_str)
        a_sum = sum(v for (n, l), v in after.items() if n == metric_name and dict(l).get("le") == le_str)
        diff = a_sum if restarted else (a_sum - b_sum)
        buckets.append((le_val, max(0.0, diff)))
    return buckets


def _histogram_quantile(phi: float, buckets: list[tuple[float, float]]) -> Optional[float]:
    if not buckets:
        return None
    total_count = buckets[-1][1]
    if total_count <= 0:
        return None
    target = phi * total_count
    prev_bound = 0.0
    prev_count = 0.0
    for bound, count in buckets:
        if count >= target:
            bucket_count = count - prev_count
            if bound == float("inf"):
                return prev_bound
            if bucket_count <= 0:
                return bound
            fraction = (target - prev_count) / bucket_count
            return prev_bound + (bound - prev_bound) * fraction
        prev_bound = bound
        prev_count = count
    return buckets[-1][0] if buckets[-1][0] != float("inf") else prev_bound


def stage_counters(before: dict, after: dict, engine: str) -> dict:
    """Calculate counter differences between before and after snapshots."""
    restarted = False
    counter_names = VLLM_COUNTERS if engine == "vllm" else LLAMACPP_COUNTERS

    for (name, labels), b_val in before.items():
        if name in counter_names or (engine == "vllm" and name.endswith("_bucket")):
            if (name, labels) in after and after[(name, labels)] < b_val:
                restarted = True
                break

    def _diff_metric(metric_name: str) -> Optional[float]:
        in_after = any(n == metric_name for n, _ in after)
        in_before = any(n == metric_name for n, _ in before)
        if not in_after and not in_before:
            return None
        sum_a = sum(v for (n, _), v in after.items() if n == metric_name)
        sum_b = sum(v for (n, _), v in before.items() if n == metric_name)
        return sum_a if restarted else (sum_a - sum_b)

    res = {
        "engine": engine,
        "restarted": restarted,
        "prompt_tokens": None,
        "generation_tokens": None,
        "requests": None,
        "finished_requests": None,
        "prefill_s": None,
        "decode_s": None,
        "prefill_tps": None,
        "decode_tps": None,
    }

    if engine == "vllm":
        res["prompt_tokens"] = _diff_metric("vllm:prompt_tokens_total")
        res["generation_tokens"] = _diff_metric("vllm:generation_tokens_total")
        res["prefill_s"] = _diff_metric("vllm:request_prefill_time_seconds_sum")
        res["decode_s"] = _diff_metric("vllm:request_decode_time_seconds_sum")

        req_prompt_tokens = _diff_metric("vllm:request_prompt_tokens_sum")
        req_gen_tokens = _diff_metric("vllm:request_generation_tokens_sum")
        res["request_prompt_tokens"] = req_prompt_tokens
        res["request_generation_tokens"] = req_gen_tokens

        finished_count = _diff_metric("vllm:request_generation_tokens_count")
        if finished_count is None:
            finished_count = _diff_metric("vllm:request_decode_time_seconds_count")
        if finished_count is None:
            finished_count = _diff_metric("vllm:request_prompt_tokens_count")
        if finished_count is None:
            finished_count = _diff_metric("vllm:request_prefill_time_seconds_count")
        res["finished_requests"] = finished_count

        req_present = any(n == "vllm:request_success_total" for n, _ in after) or any(
            n == "vllm:request_success_total" for n, _ in before
        )
        if req_present:
            reasons = set()
            for (n, l) in list(before.keys()) + list(after.keys()):
                if n == "vllm:request_success_total":
                    reasons.add(dict(l).get("finished_reason", "unknown"))
            by_reason = {}
            for r in sorted(reasons):
                sa = sum(v for (n, l), v in after.items() if n == "vllm:request_success_total" and dict(l).get("finished_reason") == r)
                sb = sum(v for (n, l), v in before.items() if n == "vllm:request_success_total" and dict(l).get("finished_reason") == r)
                by_reason[r] = sa if restarted else (sa - sb)
            res["requests_by_reason"] = by_reason
            res["requests"] = sum(by_reason.values())
        else:
            res["requests_by_reason"] = None
            res["requests"] = None

        ttft_sum = _diff_metric("vllm:time_to_first_token_seconds_sum")
        ttft_count = _diff_metric("vllm:time_to_first_token_seconds_count")
        res["ttft_sum_s"] = ttft_sum
        res["ttft_count"] = ttft_count
        if ttft_count is not None and ttft_count > 0 and ttft_sum is not None:
            res["ttft_mean_s"] = ttft_sum / ttft_count
        else:
            res["ttft_mean_s"] = None

        ttft_buckets = _extract_buckets(before, after, "vllm:time_to_first_token_seconds_bucket", restarted)
        res["ttft_median_s"] = _histogram_quantile(0.5, ttft_buckets)
        res["ttft_p90_s"] = _histogram_quantile(0.9, ttft_buckets)

        itl_buckets = _extract_buckets(before, after, "vllm:inter_token_latency_seconds_bucket", restarted)
        res["itl_median_s"] = _histogram_quantile(0.5, itl_buckets)
        res["itl_p90_s"] = _histogram_quantile(0.9, itl_buckets)

        if req_prompt_tokens is not None and res["prefill_s"] is not None and res["prefill_s"] > 0:
            res["prefill_tps"] = req_prompt_tokens / res["prefill_s"]
        else:
            res["prefill_tps"] = None

        if req_gen_tokens is not None and res["decode_s"] is not None and res["decode_s"] > 0:
            res["decode_tps"] = req_gen_tokens / res["decode_s"]
        else:
            res["decode_tps"] = None

    elif engine == "llama.cpp":
        res["prompt_tokens"] = _diff_metric("llamacpp:prompt_tokens_total")
        res["generation_tokens"] = _diff_metric("llamacpp:tokens_predicted_total")
        res["prefill_s"] = _diff_metric("llamacpp:prompt_seconds_total")
        res["decode_s"] = _diff_metric("llamacpp:tokens_predicted_seconds_total")
        # llama.cpp's counters have no request count: n_decode_total counts decode calls (about one per generated
        # token), so it is kept under its own name and requests stay unknown here (the log parse counts requests)
        res["decode_calls"] = _diff_metric("llamacpp:n_decode_total")
        res["requests"] = None
        res["finished_requests"] = None

        if res["prompt_tokens"] is not None and res["prefill_s"] is not None and res["prefill_s"] > 0:
            res["prefill_tps"] = res["prompt_tokens"] / res["prefill_s"]
        else:
            res["prefill_tps"] = None

        if res["generation_tokens"] is not None and res["decode_s"] is not None and res["decode_s"] > 0:
            res["decode_tps"] = res["generation_tokens"] / res["decode_s"]
        else:
            res["decode_tps"] = None

    return res


def _empty_log_result(engine: str) -> dict:
    if engine == "vllm":
        return {
            "engine": "vllm",
            "version": None,
            "activation_dtype": None,
            "quantization": None,
            "kv_cache_dtype": None,
            "max_seq_len": None,
            "enforce_eager": None,
            "device": None,
            "load_gib": None,
            "kv_cache_gib": None,
            "kv_cache_tokens": None,
            "max_concurrency": None,
            "init_s": None,
            "compile_s": None,
            "kernel_lines": [],
            "attention_backend": None,
        }
    return {
        "engine": "llama.cpp",
        "build": None,
        "compiler": None,
        "backend": None,
        "device": None,
        "offload": None,
        "file_type": None,
        "tensor_type_counts": {},
        "model_buffer_mib": None,
        "host_buffers_mib": None,
        "host_buffers": [],
        "kv_cache": None,
        "flash_attn": None,
        "unused_tensors": None,
        "unused_bytes": None,
        "sampler": None,
        "kernel_lines": [],
    }


def _read_checkpoint_config(model_dir: Path) -> tuple[Optional[dict], Optional[str]]:
    q_file = model_dir / "quantization_config.json"
    if q_file.is_file():
        try:
            return json.loads(q_file.read_text(encoding="utf-8")), str(q_file)
        except Exception:
            pass
    c_file = model_dir / "config.json"
    if c_file.is_file():
        try:
            data = json.loads(c_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "quantization_config" in data:
                cfg = data["quantization_config"]
                if isinstance(cfg, dict):
                    return cfg, str(c_file)
        except Exception:
            pass
    return None, None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract engine evidence and diff Prometheus counters.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    log_parser = subparsers.add_parser("log", help="Parse engine log and optional model metadata into evidence JSON.")
    log_parser.add_argument("--engine", choices=["vllm", "llama.cpp"], default="vllm", help="Inference engine name")
    log_parser.add_argument("--log", required=True, help="Path to server log file, or - for stdin")
    log_parser.add_argument("--model", default=None, help="Path to model file (.gguf) or directory")
    log_parser.add_argument("--out", default=None, help="Output file path (default stdout)")

    diff_parser = subparsers.add_parser("diff", help="Calculate counter differences between two Prometheus snapshots.")
    diff_parser.add_argument("--engine", choices=["vllm", "llama.cpp"], default="vllm", help="Inference engine name")
    diff_parser.add_argument("before", help="Path to BEFORE.prom")
    diff_parser.add_argument("after", help="Path to AFTER.prom")
    diff_parser.add_argument("--out", default=None, help="Output file path (default stdout)")

    args = parser.parse_args(argv)

    if args.command == "log":
        notes = []
        if args.log == "-":
            try:
                log_text = sys.stdin.read()
                data = parse_vllm_log(log_text) if args.engine == "vllm" else parse_llamacpp_log(log_text)
            except Exception as e:
                notes.append(f"cannot read log from stdin: {e}")
                data = _empty_log_result(args.engine)
            log_path = None
        else:
            log_path = Path(args.log)
        if log_path is not None and not log_path.is_file():
            notes.append(f"log file not found: {args.log}")
            data = _empty_log_result(args.engine)
        elif log_path is not None:
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                data = parse_vllm_log(log_text) if args.engine == "vllm" else parse_llamacpp_log(log_text)
            except Exception as e:
                notes.append(f"cannot read log {args.log}: {e}")
                data = _empty_log_result(args.engine)

        data["source"] = str(args.log)
        if args.model:
            model_p = Path(args.model)
            if args.model.endswith(".gguf") or model_p.is_file():
                g_info = gguf_tensor_mix_path(model_p)
                g_info["source"] = str(args.model)
                data["gguf"] = g_info
                if "notes" in g_info:
                    notes.extend(g_info["notes"])
            elif model_p.is_dir():
                cfg, cfg_src = _read_checkpoint_config(model_p)
                if cfg is not None:
                    cfg["source"] = cfg_src
                    data["checkpoint_config"] = cfg
                else:
                    notes.append(f"no quantization_config found in {args.model}")
                    data["checkpoint_config"] = None
            else:
                notes.append(f"model path not found: {args.model}")

        if notes:
            data["notes"] = notes
        rendered = json.dumps(data, indent=2)
        if args.out:
            out_p = Path(args.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(rendered, encoding="utf-8")
        else:
            print(rendered)
        return 0

    elif args.command == "diff":
        notes = []
        p_before = Path(args.before)
        p_after = Path(args.after)
        before_data = {}
        if not p_before.is_file():
            notes.append(f"before file not found: {args.before}")
        else:
            try:
                before_data = parse_prometheus(p_before.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                notes.append(f"cannot read before file {args.before}: {e}")

        after_data = {}
        if not p_after.is_file():
            notes.append(f"after file not found: {args.after}")
        else:
            try:
                after_data = parse_prometheus(p_after.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                notes.append(f"cannot read after file {args.after}: {e}")

        diff_res = stage_counters(before_data, after_data, args.engine)
        diff_res["source_before"] = str(args.before)
        diff_res["source_after"] = str(args.after)
        if notes:
            diff_res["notes"] = notes

        rendered = json.dumps(diff_res, indent=2)
        if args.out:
            out_p = Path(args.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(rendered, encoding="utf-8")
        else:
            print(rendered)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
