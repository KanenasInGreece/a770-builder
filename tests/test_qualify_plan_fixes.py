"""Plan, serve-environment and measurement invariants of a qualification.

Docker and the engine API are stand-ins; nothing here starts a server or a card job.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# test_qualify_lib puts harness/ on sys.path, so it is imported before qualify_lib.
from test_qualify_lib import _min_row, _wrap
import host_memory
import prompt_budget
import qualify_lib as ql
from test_qualify_e2e import QUALIFY, _run as _run_e2e, _serve_engine, _world
from test_qualify_run import _jobs, _run, box  # noqa: F401  (box is a fixture)
from test_review_v059_fixes import RUN_SUITE_SH, _base_env, _make_exec

THINKING_NAMES = (
    "THINKING_MODE",
    "THINKING_EFFORT",
    "THINKING_BUDGET",
    "THINKING_BUDGET_MESSAGE",
    "THINKING_PRESERVE",
)


def _row_file(tmp_path: Path, thinking, engine="llama.cpp", **fields) -> Path:
    row = _min_row(engine)
    row["ctx"] = row["useful_ctx"] = 16384
    if thinking is not None:
        row["thinking"] = thinking
        # The registry refuses a mode that contradicts the row's reasoning.
        if thinking.get("mode") in ("on", "off"):
            row["reasoning"] = thinking["mode"]
    row.update(fields)
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(_wrap(row, "e2e-row")) + "\n", encoding="utf-8")
    return path


def _record_up(env, record: Path) -> None:
    """The fake `compose up` saves its environment and override files, then fails."""
    script = Path(env["PATH"].split(":")[0]) / "docker"
    text = script.read_text(encoding="utf-8")
    needle = '    if cmd == "up":\n'
    assert text.count(needle) == 1
    save = (
        f"        Path({str(record)!r}).write_text(json.dumps({{\n"
        '            "env": {k: v for k, v in os.environ.items()\n'
        '                    if k.startswith("THINKING_") or k == "REASONING"},\n'
        '            "files": [Path(f).read_text(encoding="utf-8") for f in files],\n'
        "        }), encoding=\"utf-8\")\n"
        "        sys.exit(1)\n"
    )
    script.write_text(text.replace(needle, needle + save, 1), encoding="utf-8")


def _phase3_serve(tmp_path: Path, thinking, ambient: dict):
    """Run the real runner up to the llama.cpp Phase 3 `compose up`."""
    env = _world(tmp_path, port=9, engine="llama.cpp")
    record = tmp_path / "up.json"
    _record_up(env, record)
    profiles = _row_file(tmp_path, thinking)
    for name in THINKING_NAMES:
        env.pop(name, None)
    env.update(ambient)
    server = _serve_engine()
    try:
        env["A770B_PORT"] = str(server.server_address[1])
        args = [
            "bash", str(QUALIFY), "start", "--foreground", "--engine", "llama.cpp",
            "--model", "e2e-row", "--profiles-file", str(profiles),
            "--tokenizer", env["_E2E_TOKENIZER"], "--fit-only",
        ]
        code, out, err = _run_e2e(env, args, 120)
    finally:
        server.shutdown()
        server.server_close()
    assert record.is_file(), f"compose up never ran (exit {code})\n{out}\n{err[-3000:]}"
    job = Path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("directory ")))
    seen = json.loads(record.read_text(encoding="utf-8"))
    return seen["env"], "\n".join(seen["files"]), job


def test_t1_row_thinking_reaches_the_phase3_serve(tmp_path):
    thinking = {"effort": "low", "preserve": False}
    seen, argv, job = _phase3_serve(tmp_path, thinking, {})
    assert seen["THINKING_EFFORT"] == "low"
    assert seen["THINKING_PRESERVE"] == "false"
    assert "THINKING_MODE" not in seen and seen["REASONING"] == "off"
    assert "--no-reasoning-preserve" in argv and "--reasoning-effort" in argv
    want = {"mode": None, "effort": "low", "budget": None, "budget_message": None,
            "preserve": False}
    assert json.loads((job / "plan.json").read_text())["thinking"] == want
    assert json.loads((job / "result.json").read_text())["thinking"] == want


def test_t1_t2_ambient_thinking_does_not_arrive(tmp_path):
    ambient = {
        "THINKING_EFFORT": "high",
        "THINKING_BUDGET": "77",
        "THINKING_BUDGET_MESSAGE": "ambient message",
        "THINKING_PRESERVE": "false",
    }
    seen, argv, _job = _phase3_serve(tmp_path, {"mode": "auto"}, ambient)
    assert seen == {"THINKING_MODE": "auto", "REASONING": "off"}
    assert "--reasoning-budget" not in argv
    assert "--reasoning-budget-message" not in argv
    assert "--reasoning-effort" not in argv


@pytest.mark.parametrize(
    "message,sent",
    [(None, "budget spent, answer now"), ("stop thinking now", "stop thinking now")],
)
def test_set_budget_with_and_without_a_message(tmp_path, message, sent):
    thinking = {"mode": "on", "budget": 128}
    if message:
        thinking["budget_message"] = message
    ambient = {"THINKING_BUDGET_MESSAGE": "ambient message"}
    seen, argv, _job = _phase3_serve(tmp_path, thinking, ambient)
    assert seen["THINKING_BUDGET"] == "128"
    assert seen.get("THINKING_BUDGET_MESSAGE") == message
    assert "--reasoning-budget-message" in argv and sent in argv
    assert "ambient message" not in argv


def test_bare_gguf_plan_records_its_flags(monkeypatch):
    monkeypatch.setenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", "1000")
    plan = ql.build_plan([
        "--engine", "llama.cpp", "--model", "sample.gguf", "--ctx", "16384",
        "--backend", "vulkan", "--tokenizer", "/tmp/tokenizer-dir",
        "--reasoning", "on", "--reasoning-budget", "64",
    ])
    assert plan["thinking"] == {"mode": "on", "effort": None, "budget": "64",
                                "budget_message": None, "preserve": None}


def test_bare_gguf_candidate_clears_inherited_thinking(tmp_path):
    env = _base_env(tmp_path)
    (tmp_path / "seat").mkdir()
    (tmp_path / "seat" / "README.md").write_text("fixture\n", encoding="utf-8")
    for name in ("brief.md", "spec.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"suite": "SUITE-TEST", "kit_version": "1", "stages": [{
        "id": "s1-frontend", "language": "python", "brief": str(tmp_path / "brief.md"),
        "spec": str(tmp_path / "spec.json"),
        "grader": {"working": "true", "budget_lines": 50},
    }]}), encoding="utf-8")
    record = tmp_path / "candidate.env"
    fake = tmp_path / "fake-lb.sh"
    _make_exec(
        fake,
        f'if [ "$1" = run ]; then env | grep "^A770B_CANDIDATE_THINKING_" > {record}; '
        "exit 1; fi; exit 0",
    )
    names = ("MODE", "EFFORT", "BUDGET_MESSAGE", "PRESERVE")
    env.update({f"A770B_CANDIDATE_THINKING_{n}": "inherited" for n in names})
    env["A770B_LOCAL_BUILD"] = str(fake)
    run = _run_suite(env, [
        "--model", "sample.gguf", "--ctx", "8192", str(tmp_path / "suite-seat"),
        "--suite", str(suite), "--stages", "s1-frontend",
    ])
    assert record.is_file(), run.stdout + run.stderr
    got = dict(l.split("=", 1) for l in record.read_text().splitlines())
    for name in names:
        assert got[f"A770B_CANDIDATE_THINKING_{name}"] == ""


def _run_suite(env, args):
    return subprocess.run(
        ["bash", str(RUN_SUITE_SH), *args], capture_output=True, text=True,
        env=env, cwd=Path(__file__).resolve().parents[1], timeout=120,
    )


def _named(env, profiles, *flags):
    return _run(env, [
        "start", "--dry-run", "--engine", "llama.cpp", "--model", "e2e-row",
        "--profiles-file", str(profiles), "--tokenizer", env["A770B_TOKENIZER_DIR"],
        *flags,
    ])


@pytest.mark.parametrize(
    "thinking,flags,message",
    [
        ({"mode": "on"}, ["--reasoning", "off"],
         "--reasoning off is not the profile value on"),
        ({"mode": "auto"}, ["--reasoning", "on"],
         "--reasoning on is not the profile value auto"),
        (None, ["--reasoning", "on"], "--reasoning on is not the profile value off"),
        ({"mode": "on", "budget": 128}, ["--reasoning-budget", "64"],
         "--reasoning-budget 64 is not the profile value 128"),
        ({"mode": "on", "budget": 0}, ["--reasoning-budget", "-1"],
         "--reasoning-budget -1 is not the profile value 0"),
        ({"mode": "on"}, ["--reasoning-budget", "64"],
         "--reasoning-budget 64 is not the profile value none"),
    ],
)
def test_t3_a_disagreeing_flag_is_refused(box, tmp_path, thinking, flags, message):
    got = _named(box, _row_file(tmp_path, thinking), *flags)
    assert got.returncode == 2, got.stdout + got.stderr
    assert message in got.stderr
    assert not _jobs(box)


@pytest.mark.parametrize("budget", [-1, 0, 256])
def test_t3_flags_equal_to_the_row_are_accepted(box, tmp_path, budget):
    profiles = _row_file(tmp_path, {"mode": "on", "budget": budget})
    got = _named(box, profiles, "--reasoning", "on", "--reasoning-budget", str(budget))
    assert got.returncode == 0, got.stdout + got.stderr
    assert not _jobs(box)


LIMIT = 8
LONG_TEXT = "word " * 400


def _tokenizer_dir(tmp_path: Path, name: str, *, truncation=None, padding=None) -> str:
    """A word-level tokenizer.json written by hand, one id per word."""
    directory = tmp_path / name
    directory.mkdir()
    document = {
        "version": "1.0",
        "truncation": truncation,
        "padding": padding,
        "added_tokens": [],
        "normalizer": None,
        "pre_tokenizer": {"type": "Whitespace"},
        "post_processor": None,
        "decoder": None,
        "model": {"type": "WordLevel", "vocab": {"[UNK]": 0}, "unk_token": "[UNK]"},
    }
    (directory / "tokenizer.json").write_text(json.dumps(document), encoding="utf-8")
    return str(directory)


def _truncating(tmp_path: Path) -> str:
    return _tokenizer_dir(tmp_path, "cut", truncation={
        "direction": "Right", "max_length": LIMIT, "strategy": "LongestFirst",
        "stride": 0,
    })


def _padding(tmp_path: Path) -> str:
    return _tokenizer_dir(tmp_path, "pad", padding={
        "strategy": {"Fixed": 1000}, "direction": "Right", "pad_to_multiple_of": None,
        "pad_id": 0, "pad_type_id": 0, "pad_token": "[UNK]",
    })


@pytest.mark.parametrize("child", [False, True], ids=["in-process", "uv-child"])
def test_t4_saved_truncation_and_padding_do_not_change_a_count(
        tmp_path, monkeypatch, child):
    pytest.importorskip("tokenizers")
    paths = [_truncating(tmp_path), _padding(tmp_path),
             _tokenizer_dir(tmp_path, "plain")]
    if child:
        # A None entry makes the in-process import fail, as on a bare system python.
        monkeypatch.setitem(sys.modules, "tokenizers", None)
        monkeypatch.delenv("A770B_QUALIFY_TOKENIZERS_CHILD", raising=False)
    cut, pad, plain = (
        ql._token_counts(os.path.join(p, "tokenizer.json"), [LONG_TEXT, "two words"])
        for p in paths
    )
    assert plain == [400, 2] and plain[0] > LIMIT
    assert cut == plain and pad == plain


def test_t4_window_plan_action_ignores_saved_truncation(tmp_path, monkeypatch):
    pytest.importorskip("tokenizers")
    monkeypatch.delenv("A770B_QUALIFY_KIT_PROMPT_TOKENS", raising=False)
    monkeypatch.delenv("A770B_OUTPUT_TOKENS", raising=False)
    plain = ql.window_plan(2048, None, [], None, _tokenizer_dir(tmp_path, "plain"))
    cut = ql.window_plan(2048, None, [], None, _truncating(tmp_path))
    assert plain["action"] == "refuse" and plain["kit_tokens"] > LIMIT
    assert cut["action"] == plain["action"]
    assert cut["kit_tokens"] == plain["kit_tokens"]


@pytest.mark.parametrize("make", [_truncating, _padding])
def test_t4_prompt_budget_counts_the_whole_text(tmp_path, make):
    pytest.importorskip("tokenizers")
    got = prompt_budget.count_tokens(
        "", "", LONG_TEXT, tokenize="openai", tokenizer=make(tmp_path))
    assert got == 400


def test_t5_monitor_records_no_target_once_the_start_time_changes(tmp_path, monkeypatch):
    pid_file = tmp_path / "serving.pid"
    out = tmp_path / "memory.jsonl"
    me, parent = os.getpid(), os.getppid()
    # (pid in the file, start time the stubbed reader gives) for each sample.
    steps = [(me, "100"), (me, "100"), (me, "200"), (me, "200"), (parent, "300"),
             (parent, "300")]
    at = {"i": 0}
    pid_file.write_text(f"{steps[0][0]}\n", encoding="ascii")
    monkeypatch.setattr(host_memory, "pid_starttime",
                        lambda pid: steps[at["i"]][1], raising=False)

    class Done(Exception):
        pass

    def next_step(_seconds):
        at["i"] += 1
        if at["i"] == len(steps):
            raise Done
        pid_file.write_text(f"{steps[at['i']][0]}\n", encoding="ascii")

    monkeypatch.setattr(host_memory.time, "sleep", next_step)
    with pytest.raises(Done):
        host_memory.monitor(out, pid_file=pid_file)
    targets = [json.loads(line)["target"] for line in out.read_text().splitlines()]
    assert [t and t["pid"] for t in targets] == [me, me, None, None, parent, parent]


def test_start_time_reader_reads_field_22(tmp_path):
    proc = tmp_path / "7"
    proc.mkdir()
    fields = " ".join(str(n) for n in range(3, 53))
    (proc / "stat").write_text(f"7 (a b) c) {fields}\n", encoding="ascii")
    assert host_memory.pid_starttime(7, tmp_path) == "22"
    assert host_memory.pid_starttime(8, tmp_path) is None


def test_t6_a_replaced_shared_cid_does_not_move_the_sampling(tmp_path):
    first, second = "a" * 64, "b" * 64
    job = tmp_path / "job"
    for name in ("phases", "evidence"):
        (job / name).mkdir(parents=True)
    (job / "cid").write_text("", encoding="utf-8")
    (job / "phases" / "serve.log").write_text("server already up (x)\n", encoding="utf-8")
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    shared = data / "logs" / "llamacpp-a770.cid"
    shared.write_text(first + "\n", encoding="utf-8")
    docker = tmp_path / "docker"
    asked = tmp_path / "asked.log"
    _make_exec(docker, f'cid="${{@: -1}}"; echo "$cid" >> {asked}; '
                       f'case "$cid" in {first}) echo 111;; *) echo 222;; esac')
    # The real tracker, cut out of qualify.sh with the two helpers it calls.
    script = tmp_path / "track.sh"
    script.write_text(f'''set -u
for fn in refuse_link plain_trunc start_observers; do
  eval "$(sed -n "/^$fn() {{/,/^}}/p" {QUALIFY})"
done
gpu_used_gib() {{ return 1; }}
job={job}; here={tmp_path}; engine=llama.cpp
export A770B_DATA={data} A770B_DOCKER={docker}
start_observers
wait_for() {{ for _ in $(seq 200); do eval "$1" && return 0; sleep 0.1; done; return 1; }}
wait_for '[ "$(cat "$job/evidence/serving.pid")" = 111 ]' || exit 3
printf '%s\\n' {second} > {shared}
sleep 2
cp "$job/evidence/serving.pid" {tmp_path}/published
: > "$job/SERVING_CLEARED"
rm -f "$job/VRAM_SAMPLE"
kill "$VRAM_PID" 2>/dev/null
wait "$TRACKER" "$MEM" "$VRAM_PID" 2>/dev/null
exit 0
''', encoding="utf-8")
    (tmp_path / "host_memory.py").write_text("", encoding="utf-8")
    run = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                         timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (tmp_path / "published").read_text().strip() == "111"
    assert set(asked.read_text().split()) == {first}
