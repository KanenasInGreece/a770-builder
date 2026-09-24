"""Tests for harness.engine_evidence parsers, GGUF reader, and CLI."""

import json
import struct
from pathlib import Path

from harness.engine_evidence import (
    gguf_tensor_mix,
    gguf_tensor_mix_path,
    main as main_cli,
    parse_llamacpp_log,
    parse_prometheus,
    parse_vllm_log,
    stage_counters,
    tensor_bytes,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "engine"
VLLM_LOG_FIXTURE = FIXTURES_DIR / "vllm-xpu-gptq.log"
VLLM_PROM_FIXTURE = FIXTURES_DIR / "vllm-metrics.prom"
LLAMACPP_SYCL_LV4_FIXTURE = FIXTURES_DIR / "llamacpp-sycl-lv4.log"


def test_vllm_log_fixture():
    """Verify parse_vllm_log on the real captured vLLM fixture."""
    text = VLLM_LOG_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_vllm_log(text)

    assert parsed["engine"] == "vllm"
    assert parsed["version"] == "0.21.1.dev18+g8df6feb7d"
    assert parsed["activation_dtype"] == "float16"
    assert parsed["quantization"] == "gptq"
    assert parsed["kv_cache_dtype"] == "auto"
    assert parsed["load_gib"] == 16.78
    assert parsed["kv_cache_tokens"] == 138692
    assert parsed["max_concurrency"] == 4.23
    assert parsed["compile_s"] == 68.57

    assert any("gptq_gemm" in line for line in parsed["kernel_lines"])
    assert not any("XPUwNa16LinearKernel" in line for line in parsed["kernel_lines"])
    assert parsed["attention_backend"] == "flash_attn 2"


def test_prometheus_fixture_and_counters():
    """Verify Prometheus metrics parsing and counter differencing."""
    text = VLLM_PROM_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_prometheus(text)

    assert isinstance(parsed, dict)
    assert len(parsed) > 0

    diff_zero = stage_counters(parsed, parsed, engine="vllm")
    assert diff_zero["restarted"] is False
    assert diff_zero["prompt_tokens"] == 0.0
    assert diff_zero["generation_tokens"] == 0.0
    assert diff_zero["requests"] == 0.0
    assert diff_zero["finished_requests"] == 0.0
    assert diff_zero["prefill_s"] == 0.0
    assert diff_zero["decode_s"] == 0.0

    # R1 test: on the fixture against empty, decode_tps == request_generation_tokens_sum / request_decode_time_seconds_sum
    diff_fixture = stage_counters({}, parsed, engine="vllm")
    gen_sum = [v for (n, _), v in parsed.items() if n == "vllm:request_generation_tokens_sum"][0]
    decode_sum = [v for (n, _), v in parsed.items() if n == "vllm:request_decode_time_seconds_sum"][0]
    prompt_sum = [v for (n, _), v in parsed.items() if n == "vllm:request_prompt_tokens_sum"][0]
    prefill_sum = [v for (n, _), v in parsed.items() if n == "vllm:request_prefill_time_seconds_sum"][0]

    assert diff_fixture["decode_tps"] == gen_sum / decode_sum
    assert diff_fixture["prefill_tps"] == prompt_sum / prefill_sum
    assert diff_fixture["finished_requests"] == 9.0
    assert diff_fixture["requests"] == 9.0
    assert diff_fixture["requests_by_reason"]["stop"] == 9.0
    assert sum(v for k, v in diff_fixture["requests_by_reason"].items() if k != "stop") == 0.0

    # Verify R4: duplicate keys dropped
    assert "requests_by_finished_reason" not in diff_fixture
    assert "ttft_p50_s" not in diff_fixture
    assert "itl_p50_s" not in diff_fixture
    assert "inter_token_latency_median_s" not in diff_fixture
    assert "inter_token_latency_p90_s" not in diff_fixture

    # Test synthetic delta on request generation histogram sum
    after_larger = dict(parsed)
    gen_sum_key = [k for k in parsed if k[0] == "vllm:request_generation_tokens_sum"][0]
    decode_key = [k for k in parsed if k[0] == "vllm:request_decode_time_seconds_sum"][0]
    after_larger[gen_sum_key] = parsed[gen_sum_key] + 100.0
    after_larger[decode_key] = parsed[decode_key] + 10.0

    diff_large = stage_counters(parsed, after_larger, engine="vllm")
    assert diff_large["request_generation_tokens"] == 100.0
    assert diff_large["decode_s"] == 10.0
    assert diff_large["decode_tps"] == 10.0

    after_smaller = dict(parsed)
    prompt_key = [k for k in parsed if k[0] == "vllm:prompt_tokens_total"][0]
    after_smaller[prompt_key] = parsed[prompt_key] - 50.0

    diff_restarted = stage_counters(parsed, after_smaller, engine="vllm")
    assert diff_restarted["restarted"] is True


def test_histogram_quantile_known_buckets():
    """Verify median and p90 calculation on known linear buckets."""
    prom_text = (
        'vllm:time_to_first_token_seconds_bucket{engine="0",le="10.0"} 0.0\n'
        'vllm:time_to_first_token_seconds_bucket{engine="0",le="20.0"} 2.0\n'
        'vllm:time_to_first_token_seconds_bucket{engine="0",le="40.0"} 3.0\n'
        'vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf"} 3.0\n'
    )
    parsed = parse_prometheus(prom_text)
    diff = stage_counters({}, parsed, engine="vllm")
    assert diff["ttft_median_s"] == 17.5
    assert diff["ttft_p90_s"] == 34.0


def _build_synthetic_gguf(include_unknown: bool = False) -> bytes:
    t_count = 4 if include_unknown else 3
    buf = bytearray()
    buf.extend(b"GGUF")
    buf.extend(struct.pack("<IQQ", 3, t_count, 2))

    k1 = b"general.architecture"
    buf.extend(struct.pack("<Q", len(k1)))
    buf.extend(k1)
    buf.extend(struct.pack("<I", 8))
    v1 = b"test_architecture"
    buf.extend(struct.pack("<Q", len(v1)))
    buf.extend(v1)

    k2 = b"general.file_type"
    buf.extend(struct.pack("<Q", len(k2)))
    buf.extend(k2)
    buf.extend(struct.pack("<I", 4))
    buf.extend(struct.pack("<I", 14))

    # Tensor 1: Q8_0 (64 elements -> 68 bytes)
    t1_name = b"blk.0.weight"
    buf.extend(struct.pack("<Q", len(t1_name)))
    buf.extend(t1_name)
    buf.extend(struct.pack("<I", 1))
    buf.extend(struct.pack("<Q", 64))
    buf.extend(struct.pack("<IQ", 8, 0))

    # Tensor 2: Q6_K (256 elements -> 210 bytes) with .nextn.
    t2_name = b"blk.64.nextn.weight"
    buf.extend(struct.pack("<Q", len(t2_name)))
    buf.extend(t2_name)
    buf.extend(struct.pack("<I", 1))
    buf.extend(struct.pack("<Q", 256))
    buf.extend(struct.pack("<IQ", 14, 68))

    # Tensor 3: F32 (8 elements -> 32 bytes)
    t3_name = b"output.weight"
    buf.extend(struct.pack("<Q", len(t3_name)))
    buf.extend(t3_name)
    buf.extend(struct.pack("<I", 1))
    buf.extend(struct.pack("<Q", 8))
    buf.extend(struct.pack("<IQ", 0, 278))

    if include_unknown:
        t4_name = b"unknown.weight"
        buf.extend(struct.pack("<Q", len(t4_name)))
        buf.extend(t4_name)
        buf.extend(struct.pack("<I", 1))
        buf.extend(struct.pack("<Q", 16))
        buf.extend(struct.pack("<IQ", 999, 310))

    return bytes(buf)


def test_gguf_tensor_mix(tmp_path):
    """Verify GGUF binary header and tensor info parsing."""
    raw = _build_synthetic_gguf(include_unknown=True)
    res = gguf_tensor_mix(raw)

    assert tensor_bytes(8, 64) == 68
    assert tensor_bytes(14, 256) == 210
    assert tensor_bytes(0, 8) == 32
    assert res["total_bytes"] == 310

    mix_sum = sum(res["mix"].values())
    assert abs(mix_sum - 100.0) <= 0.1

    assert res["file_type"] == 14
    assert res["architecture"] == "test_architecture"
    assert res["prediction_head_tensors"] == 1
    assert "type_999" in res["unknown_types"]

    test_file = tmp_path / "model.gguf"
    test_file.write_bytes(raw)
    file_res = gguf_tensor_mix_path(test_file)
    assert file_res["mix"] == res["mix"]
    assert file_res["total_bytes"] == res["total_bytes"]


def test_gguf_security_limits_refused(tmp_path):
    """Verify R2 & R3: corrupt or excessive lengths in GGUF stream are refused quickly."""
    # 1. Key length > 65536
    buf1 = bytearray(b"GGUF")
    buf1.extend(struct.pack("<IQQ", 3, 0, 1))
    buf1.extend(struct.pack("<Q", 70_000))
    buf1.extend(b"x" * 64)
    p1 = tmp_path / "huge_key.gguf"
    p1.write_bytes(buf1)

    res1 = gguf_tensor_mix_path(p1)
    assert res1["tensor_count"] is None
    assert res1["total_bytes"] is None
    assert res1["prediction_head_tensors"] is None
    assert any("exceeds limit" in n for n in res1["notes"])

    # 2. Tensor count > 1,000,000
    buf2 = bytearray(b"GGUF")
    buf2.extend(struct.pack("<IQQ", 3, 2_000_000, 0))
    p2 = tmp_path / "huge_tensor_count.gguf"
    p2.write_bytes(buf2)

    res2 = gguf_tensor_mix_path(p2)
    assert res2["tensor_count"] is None
    assert any("exceeds limit" in n for n in res2["notes"])

    # 3. KV count > 100,000
    buf3 = bytearray(b"GGUF")
    buf3.extend(struct.pack("<IQQ", 3, 0, 200_000))
    p3 = tmp_path / "huge_kv_count.gguf"
    p3.write_bytes(buf3)

    res3 = gguf_tensor_mix_path(p3)
    assert res3["tensor_count"] is None
    assert any("exceeds limit" in n for n in res3["notes"])

    # 4. Missing file yields None for counts/bytes (R3)
    res_missing = gguf_tensor_mix_path(tmp_path / "nonexistent.gguf")
    assert res_missing["file_type"] is None
    assert res_missing["architecture"] is None
    assert res_missing["tensor_count"] is None
    assert res_missing["total_bytes"] is None
    assert res_missing["prediction_head_tensors"] is None
    assert any("not found" in n for n in res_missing["notes"])


def test_llamacpp_log():
    """Verify parse_llamacpp_log on simulated real line excerpts."""
    sample = """
main: build: 10920 (eafe15a5e)
load_backend: loaded SYCL backend
llama_model_load: print_info: file type   = Q6_K
llama_model_load: offloaded 65/65 layers to GPU
llama_kv_cache: K (q8_0): 1024 MiB, V (q8_0): 1024 MiB
llama_model_load: flash_attn = 1
ggml_sycl: using device Intel GPU
"""
    parsed = parse_llamacpp_log(sample)
    assert parsed["engine"] == "llama.cpp"
    assert parsed["build"] == "10920 (eafe15a5e)"
    assert parsed["backend"] == "SYCL"
    assert parsed["offload"] == {"layers": 65, "of": 65}
    assert parsed["file_type"] == "Q6_K"
    assert parsed["kv_cache"] == {
        "k_type": "q8_0",
        "v_type": "q8_0",
        "k_mib": 1024,
        "v_mib": 1024,
    }
    assert parsed["flash_attn"] == "1"
    assert any("offloaded" in l for l in parsed["kernel_lines"])
    assert any("SYCL" in l for l in parsed["kernel_lines"])


def test_llamacpp_sycl_lv4_fixture():
    """Verify parse_llamacpp_log on the real llama.cpp b10920 SYCL log captured at -lv 4 (C11-W2 fixture): every
    field the new -lv 4 lines carry, asserted against what this real capture actually has — including the fields
    it does NOT have (backend, sampler), because the capture is a cold start with no request served."""
    text = LLAMACPP_SYCL_LV4_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_llamacpp_log(text)

    assert parsed["engine"] == "llama.cpp"
    # "common_params_print_info: build 10920 (eafe15a5e) with IntelLLVM 2025.3.3 for Linux x86_64"
    assert parsed["build"] == "10920 (eafe15a5e)"
    assert parsed["compiler"] == "IntelLLVM 2025.3.3"
    # this fixture never prints "load_backend: loaded <X> backend" at -lv 4 — a cold start with no request has no
    # such line, so backend stays None; asserting that is the point, not an oversight
    assert parsed["backend"] is None
    # "  - SYCL0   : Intel(R) Arc(TM) Pro B70 Graphics (32656 MiB, 32581 MiB free)"
    assert parsed["device"] == {"name": "Intel(R) Arc(TM) Pro B70 Graphics", "total_mib": 32656.0}
    # "load_tensors: offloaded 66/66 layers to GPU"
    assert parsed["offload"] == {"layers": 66, "of": 66}
    # "print_info: file type   = Q6_K"
    assert parsed["file_type"] == "Q6_K"
    # "llama_model_loader: - type <x>:  N tensors", one line per type; 866 tensors total (the loader's own count)
    assert parsed["tensor_type_counts"] == {
        "f32": 360, "q8_0": 154, "q4_K": 2, "q5_K": 104, "q6_K": 241, "iq4_nl": 2, "iq4_xs": 3,
    }
    assert sum(parsed["tensor_type_counts"].values()) == 866
    # "load_tensors: SYCL0 model buffer size = 19625.41 MiB" — the device-side buffer, not a host one
    assert parsed["model_buffer_mib"] == 19625.41
    # every "<name>_Host <kind> buffer size = N MiB" line: model 994.63, output 0.95, compute 118.03
    assert parsed["host_buffers"] == [
        {"name": "SYCL_Host model", "mib": 994.63},
        {"name": "SYCL_Host output", "mib": 0.95},
        {"name": "SYCL_Host compute", "mib": 118.03},
    ]
    assert parsed["host_buffers_mib"] == round(994.63 + 118.03 + 0.95, 2)
    # "llama_kv_cache: size = 3323.50 MiB (100096 cells, ...), K (q8_0): 1661.75 MiB, V (q8_0): 1661.75 MiB"
    assert parsed["kv_cache"] == {"k_type": "q8_0", "v_type": "q8_0", "k_mib": 1661.75, "v_mib": 1661.75}
    # "llama_context: flash_attn            = enabled"
    assert parsed["flash_attn"] == "enabled"
    # 15 "model has unused tensor ... (size = N bytes) -- ignoring" lines (the unused blk.64.* nextn tensors)
    assert parsed["unused_tensors"] == 15
    assert parsed["unused_bytes"] == 351008768
    # this fixture never serves a request, so llama.cpp never prints "sampler params:" — sampler stays None
    assert parsed["sampler"] is None
    assert any("offloaded" in line for line in parsed["kernel_lines"])
    # kernel_lines must carry the message, not the "0.00.949.864 I " elapsed-time/level prefix glued in front
    assert not any(line.split(" ", 1)[0][0].isdigit() for line in parsed["kernel_lines"])


def test_llamacpp_sampler_params_block_when_a_request_was_served():
    """The real fixture above never serves a request, so it has no "sampler params:" block to assert against —
    this drives that block synthetically (llama.cpp's own multi-line indented format) to prove the five sampling
    numbers this project tracks are read out of it correctly."""
    sample = """
main: build: 10920 (eafe15a5e)
sampler params:
\trepeat_last_n = 64, repeat_penalty = 1.100, frequency_penalty = 0.000, presence_penalty = 0.000
\ttop_k = 20, top_p = 0.950, min_p = 0.050, temp = 1.000
sampler chain: logits -> top-k -> top-p -> min-p -> temp -> softmax -> dist
"""
    parsed = parse_llamacpp_log(sample)
    assert parsed["sampler"] == {
        "top_k": 20, "top_p": 0.95, "min_p": 0.05, "temp": 1.0, "repeat_penalty": 1.1,
    }


def test_cli_log_reads_the_fixture_from_stdin_with_dash(tmp_path, monkeypatch, capsys):
    """--log - (C11-W2, for piping `docker logs` straight in) reads stdin instead of a file path."""
    import io
    out_file = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.stdin", io.StringIO(LLAMACPP_SYCL_LV4_FIXTURE.read_text(encoding="utf-8")))
    rc = main_cli([
        "log",
        "--engine", "llama.cpp",
        "--log", "-",
        "--out", str(out_file),
    ])
    assert rc == 0
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["source"] == "-"
    assert data["build"] == "10920 (eafe15a5e)"
    assert "notes" not in data


def test_cli_log_vllm_fixture(tmp_path):
    """Verify CLI log command outputs valid JSON with source."""
    out_file = tmp_path / "evidence.json"
    rc = main_cli([
        "log",
        "--engine", "vllm",
        "--log", str(VLLM_LOG_FIXTURE),
        "--out", str(out_file),
    ])
    assert rc == 0
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["source"] == str(VLLM_LOG_FIXTURE)
    assert data["version"] == "0.21.1.dev18+g8df6feb7d"


def test_cli_diff_fixture_against_itself(capsys):
    """Verify CLI diff on identical metrics prints zero differences."""
    rc = main_cli([
        "diff",
        "--engine", "vllm",
        str(VLLM_PROM_FIXTURE),
        str(VLLM_PROM_FIXTURE),
    ])
    assert rc == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["restarted"] is False
    assert data["prompt_tokens"] == 0.0
    assert data["generation_tokens"] == 0.0


def test_cli_missing_files_give_exit_0_and_notes(tmp_path, capsys):
    """Verify missing inputs produce exit 0 and note entries without crashing."""
    out_file = tmp_path / "missing_log_out.json"
    rc_log = main_cli([
        "log",
        "--engine", "vllm",
        "--log", "/nonexistent/path/server.log",
        "--out", str(out_file),
    ])
    assert rc_log == 0
    log_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert log_data["engine"] == "vllm"
    assert "notes" in log_data
    assert len(log_data["notes"]) > 0

    rc_diff = main_cli([
        "diff",
        "--engine", "vllm",
        "/nonexistent/path/before.prom",
        str(VLLM_PROM_FIXTURE),
    ])
    assert rc_diff == 0
    captured = capsys.readouterr().out
    diff_data = json.loads(captured)
    assert "notes" in diff_data
    assert len(diff_data["notes"]) > 0
