"""Tests for harness/engine_record.py: completion, stream, log, capture, window, health.

Every check invokes the module the way every caller does — ``python3
"$A770B_PROJECT/harness/engine_record.py" <command>`` — with temp files and stdin
only. No live port, no shipped registry as a fixture, no clock read by the module.

pytest is not installed here, so the file is plain stdlib unittest and runs as:

    python3 tests/test_engine_record.py

It is also collected by pytest unchanged, for the merger's ``pytest -q tests/``.

Only the module is under test in this file. The caller-side checks of the brief
(roles.py timings, the local-build.sh wait loop, ctx_sweep.sh's branches,
capture_task.sh's deleted inline Python, depth_probe.sh's formatting) are part of
this pass too, but they are asserted by ``bash tests/selftest.sh``, which runs
beside this file: a missing caller edit fails that script, not these tests.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(PROJECT, "harness", "engine_record.py")

NO_RATE_LINE = "timings unavailable (log has no llama.cpp timing lines)"
NO_TOKEN_LINE = "context was not measured"

COMPLETION_KEYS = {
    "text", "prompt_tokens", "completion_tokens",
    "prefill_tps", "decode_tps", "rate_source",
}
STREAM_KEYS = COMPLETION_KEYS | {"first_delta_s", "end_s"}
LOG_KEYS = {
    "requests_line", "prompt_tokens_total", "gen_tokens_total",
    "prefill_tps", "decode_tps", "detail",
}

# The container-log shape harness/capture_task.sh already reads (llama.cpp -lv 2).
PROMPT_EVAL_LINE = ("prompt eval time =     15880.00 ms /  1601 tokens "
                    "(    9.92 ms per token,   100.80 tokens per second)")
EVAL_LINE = ("eval time =     2245000.00 ms / 10082 tokens "
             "(  222.67 ms per token,     4.49 tokens per second)")
NGEN_LINE = "print_timing: id  0 | task 7 | n_gen =    100, tg =   4.49 t/s"
NGEN_LINE_LONG = "print_timing: id  0 | task 9 | n_gen =  10082, tg =   4.49 t/s"
SLOT_LINE = "slot release: id  0 | task 7 | stop processing: n_tokens = 21446, truncated = 0"


def invoke(args, stdin=None):
    """(exit code, stdout, stderr) for one module run. No stdin handle is opened for
    a command that does not read it (capture, health), except when asked for."""
    argv = [sys.executable, MODULE] + list(args)
    if stdin is None:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PROJECT)
    else:
        payload = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        proc = subprocess.run(argv, input=payload,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PROJECT)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def silent_json(command, stdin_text):
    """The parsed JSON object a stdin command printed, asserting it printed exactly
    one object, exited 0, and raised nothing."""
    code, out, err = invoke([command], stdin=stdin_text)
    assert code == 0, "{0} exited {1} on {2!r}".format(command, code, stdin_text)
    assert err == "", "{0} wrote to stderr: {1!r}".format(command, err)
    assert out.count("\n") == 1, "{0} printed more than one object: {1!r}".format(command, out)
    return json.loads(out)


def human(command, stdin_text):
    """The human lines a stdin command printed, as a list, one entry per line."""
    code, out, err = invoke([command], stdin=stdin_text)
    assert code == 0, "{0} exited {1} on {2!r}".format(command, code, stdin_text)
    assert err == "", "{0} wrote to stderr: {1!r}".format(command, err)
    return out.splitlines()


def body(**fields):
    """One completion body whose message says "x", plus the given top-level fields."""
    record = {"choices": [{"message": {"content": "x"}}]}
    record.update(fields)
    return json.dumps(record)


def usage_body(prompt_tokens="absent", completion_tokens="absent", **fields):
    """A completion body with a usage object holding exactly the keys given."""
    usage = {}
    if prompt_tokens != "absent":
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens != "absent":
        usage["completion_tokens"] = completion_tokens
    return body(usage=usage, **fields)


def timing_body(prompt_per_second="absent", predicted_per_second="absent", **fields):
    """A completion body with a timings object holding exactly the keys given."""
    timings = {}
    if prompt_per_second != "absent":
        timings["prompt_per_second"] = prompt_per_second
    if predicted_per_second != "absent":
        timings["predicted_per_second"] = predicted_per_second
    return body(timings=timings, **fields)


def samples(events):
    """The stdin text for `stream`: a JSON list of [seconds, event]."""
    return json.dumps(events)


class TempTree(unittest.TestCase):
    """A throwaway directory for the log files one capture check needs."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix=".a770b-engine-record-", dir=PROJECT)
        self.tmp = holder.name
        self.addCleanup(holder.cleanup)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def missing(self, name):
        return os.path.join(self.tmp, name)

    def capture(self, container_text, build_text, container_name="container.log",
                build_name="build.log"):
        """capture on two temp paths: the container log holds container_text (None
        means the path does not exist), the build log holds build_text likewise."""
        container = (self.write(container_name, container_text)
                     if container_text is not None else self.missing(container_name))
        build = (self.write(build_name, build_text)
                 if build_text is not None else self.missing(build_name))
        code, out, err = invoke(["capture", container, build])
        return code, out, err, out.splitlines()


class CompletionChecks(unittest.TestCase):
    """Brief checks 1-8, 12, 25 on `completion`, plus the shape rules beside them."""

    def test_check_01_reasoning_content_when_content_is_blank(self):
        record = silent_json("completion", json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "because"}}],
            "usage": {"prompt_tokens": 99789},
        }))
        self.assertEqual(set(record), COMPLETION_KEYS)
        self.assertEqual(record["text"], "because")
        self.assertEqual(record["prompt_tokens"], 99789)
        self.assertIsNone(record["completion_tokens"])
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])

    def test_check_02_content_wins_when_it_is_non_blank(self):
        record = silent_json("completion", json.dumps({
            "choices": [{"message": {"content": "the answer", "reasoning_content": "because"}}],
            "usage": {"prompt_tokens": 99789},
        }))
        self.assertEqual(record["text"], "the answer")

    def test_check_03_whitespace_content_is_blank(self):
        record = silent_json("completion", json.dumps({
            "choices": [{"message": {"content": "   ", "reasoning_content": "",
                                     "reasoning": "via reasoning"}}],
        }))
        self.assertEqual(record["text"], "via reasoning")

    def test_check_04_null_content_yields_empty_text_and_exit_0(self):
        code, out, err = invoke(["completion"], stdin=json.dumps(
            {"choices": [{"message": {"content": None}}]}))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        record = json.loads(out)
        self.assertEqual(record["text"], "")
        # Absent is JSON null on stdout, never the digit 0.
        self.assertIn('"prompt_tokens": null', out)
        self.assertIn('"prefill_tps": null', out)

    def test_check_05_empty_choices_and_other_shapes_never_raise(self):
        cases = [
            '{"choices": []}',                          # empty choices
            '{}',                                       # missing choices
            '{"choices": "nope"}',                      # choices is not a list
            '{"choices": [7]}',                         # first item is not an object
            '{"choices": [{"message": "nope"}]}',       # message is not an object
            '{"choices": [{}]}',                        # message is absent
            '{"choices": [{"delta": {"content": "x"}}]}',  # delta is not a message
        ]
        for text in cases:
            code, out, err = invoke(["completion"], stdin=text)
            self.assertEqual(code, 0, text)
            self.assertEqual(err, "", text)                          # no traceback
            self.assertEqual(json.loads(out)["text"], "", text)

    def test_check_06_only_a_json_integer_count_survives(self):
        for label, text in [
            ("usage missing", body()),
            ("prompt_tokens null", usage_body(prompt_tokens=None)),
            ("prompt_tokens 1.0", usage_body(prompt_tokens=1.0)),
            ("prompt_tokens true", usage_body(prompt_tokens=True)),
            ("prompt_tokens false", usage_body(prompt_tokens=False)),
            ("prompt_tokens -1", usage_body(prompt_tokens=-1)),
            ("usage is not an object", '{"usage": "nope"}'),
        ]:
            record = silent_json("completion", text)
            self.assertIsNone(record["prompt_tokens"], label)
            self.assertNotIn('"prompt_tokens": 0', json.dumps(record), label)

    def test_check_06_booleans_are_absent_on_both_counts(self):
        record = silent_json("completion", usage_body(prompt_tokens=True,
                                                      completion_tokens=False))
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["completion_tokens"])

    def test_check_07_a_real_zero_count_is_kept(self):
        code, out, err = invoke(["completion"], stdin=usage_body(prompt_tokens=0))
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)
        self.assertEqual(record["prompt_tokens"], 0)
        self.assertIsInstance(record["prompt_tokens"], int)
        self.assertNotIsInstance(record["prompt_tokens"], bool)
        self.assertIn('"prompt_tokens": 0', out)

    def test_check_08_a_timings_object_needs_both_rates(self):
        record = silent_json("completion", timing_body(prompt_per_second=10,
                                                       predicted_per_second=None))
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])

    def test_check_08_a_reported_zero_rate_is_a_real_zero(self):
        for prompt, decode in [(0, 42.5), (0.0, 42.5), (42.5, 0), (42.5, 0.0)]:
            record = silent_json("completion", timing_body(prompt_per_second=prompt,
                                                           predicted_per_second=decode))
            self.assertEqual(record["rate_source"], "timings", (prompt, decode))
            self.assertEqual(record["prefill_tps"], prompt)
            self.assertEqual(record["decode_tps"], decode)

    def test_check_08_a_boolean_and_a_non_finite_number_are_not_rates(self):
        for prompt, decode in [(True, 42.5), (42.5, True),
                               (float("inf"), 42.5), (42.5, float("inf")),
                               (float("nan"), 42.5), (-1, 42.5), (42.5, -0.5),
                               ("10", 42.5)]:
            record = silent_json("completion", json.dumps({
                "timings": {"prompt_per_second": prompt, "predicted_per_second": decode},
            }))
            self.assertIsNone(record["prefill_tps"], (prompt, decode))
            self.assertIsNone(record["decode_tps"], (prompt, decode))
            self.assertIsNone(record["rate_source"], (prompt, decode))

    def test_a_missing_or_partial_timings_object_has_no_rates(self):
        for text in ['{}', '{"timings": null}', '{"timings": "nope"}',
                     '{"timings": {}}', '{"timings": {"predicted_per_second": 40}}']:
            record = silent_json("completion", text)
            self.assertIsNone(record["prefill_tps"], text)
            self.assertIsNone(record["rate_source"], text)

    def test_prompt_ms_is_not_a_rate(self):
        record = silent_json("completion", json.dumps({
            "timings": {"prompt_ms": 1234.0, "eval_ms": 500.0,
                        "predicted_per_second": 40.0},
        }))
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])

    def test_text_is_the_raw_string_not_the_stripped_one(self):
        record = silent_json("completion", json.dumps(
            {"choices": [{"message": {"content": " keep  whitespace "}}]}))
        self.assertEqual(record["text"], " keep  whitespace ")

    def test_a_non_string_text_value_is_skipped(self):
        for content in [5, ["x"], {}, 0]:
            record = silent_json("completion", json.dumps(
                {"choices": [{"message": {"content": content, "reasoning": "via"}}]}))
            self.assertEqual(record["text"], "via", content)

    def test_check_12_rates_come_from_timings_and_rate_source_says_so(self):
        record = silent_json("completion", timing_body(prompt_per_second=612.4,
                                                       predicted_per_second=45.5,
                                                       usage={"prompt_tokens": 9000}))
        self.assertEqual(record["rate_source"], "timings")
        self.assertEqual(record["prefill_tps"], 612.4)     # the JSON keeps the float
        self.assertEqual(record["decode_tps"], 45.5)
        self.assertEqual(record["prompt_tokens"], 9000)

    def test_check_12_there_is_no_pick_command(self):
        code, out, err = invoke(["pick"], stdin="{}")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn("pick", err)

    def test_check_25_a_json_array_is_a_value_that_is_not_an_object(self):
        code, out, err = invoke(["completion"], stdin="[]")
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)
        self.assertEqual(record["text"], "")
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["completion_tokens"])
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])

    def test_check_25_non_json_exits_2_and_prints_nothing(self):
        for text in ["not-json", "", "{", '{"choices":']:
            code, out, err = invoke(["completion"], stdin=text)
            self.assertEqual(code, 2, text)
            self.assertEqual(out, "", text)

    def test_any_other_json_type_is_an_object_absent_record(self):
        for text in ['null', 'true', 'false', '5', '1.5', '"a string"', '[{"usage": 1}]']:
            record = silent_json("completion", text)
            self.assertEqual(record["text"], "", text)
            self.assertIsNone(record["prompt_tokens"], text)
            self.assertIsNone(record["rate_source"], text)


class StreamChecks(unittest.TestCase):
    """Brief checks 10, 11, 26 on `stream`, plus the clock rules beside them."""

    def test_check_10_the_last_usage_object_wins_even_when_it_is_blank(self):
        record = silent_json("stream", samples([
            [0.5, {"choices": [{"delta": {"content": "a"}}]}],
            [1.5, {"choices": [{"delta": {"content": "b"}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 4}}],
            [1.6, {"usage": {"prompt_tokens": None}}],
        ]))
        self.assertEqual(set(record), STREAM_KEYS)
        self.assertEqual(record["text"], "ab")
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["completion_tokens"])
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])
        self.assertEqual(record["first_delta_s"], 0.5)
        self.assertEqual(record["end_s"], 1.6)

    def test_check_11_the_clock_rates_when_the_counts_and_the_window_hold(self):
        record = silent_json("stream", samples([
            [1.0, {"choices": [{"delta": {"content": "a"}}]}],
            [2.0, {"usage": {"prompt_tokens": 100, "completion_tokens": 4}}],
        ]))
        self.assertEqual(record["prompt_tokens"], 100)
        self.assertEqual(record["rate_source"], "clock")
        self.assertEqual(record["prefill_tps"], 100)        # 100 / 1.0, and 100 == 100.0
        self.assertEqual(record["decode_tps"], 4)           # 4 / (2.0 - 1.0)
        self.assertEqual(record["first_delta_s"], 1.0)
        self.assertEqual(record["end_s"], 2.0)

    def test_the_json_keeps_the_unrounded_rate(self):
        record = silent_json("stream", samples([
            [3.0, {"choices": [{"delta": {"content": "a"}}]}],
            [4.0, {"usage": {"prompt_tokens": 100, "completion_tokens": 3}}],
        ]))
        self.assertEqual(record["prefill_tps"], 100 / 3)
        self.assertEqual(record["decode_tps"], 3.0)

    def test_check_11_a_zero_first_delta_never_divides(self):
        code, out, err = invoke(["stream"], stdin=
                                samples([[0, {"choices": [{"delta": {"content": "a"}}]}]]))
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])
        self.assertEqual(record["first_delta_s"], 0)

    def test_check_11_an_empty_list_exits_0_with_null_times(self):
        code, out, err = invoke(["stream"], stdin="[]")
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)
        self.assertEqual(set(record), STREAM_KEYS)
        self.assertEqual(record["text"], "")
        for key in ("prompt_tokens", "completion_tokens", "prefill_tps", "decode_tps",
                    "rate_source", "first_delta_s", "end_s"):
            self.assertIsNone(record[key], key)

    def test_check_11_stdin_that_is_not_a_list_exits_2(self):
        for text in ["{}", '{"choices": []}', '"a string"', 'null', 'not-json', '']:
            code, out, err = invoke(["stream"], stdin=text)
            self.assertEqual(code, 2, text)
            self.assertEqual(out, "", text)

    def test_an_element_that_is_not_a_two_item_list_exits_2(self):
        for text in ["[[1.0]]", "[1.0, {}]", "[[1.0, {}, 3]]", '[["seconds", {}]]']:
            code, out, err = invoke(["stream"], stdin=text)
            self.assertEqual(code, 2, text)
            self.assertEqual(out, "", text)

    def test_seconds_must_be_a_finite_number(self):
        for seconds in ['"1.0"', "null", "true", "NaN", "Infinity", "-Infinity", "[0]"]:
            code, out, err = invoke(["stream"], stdin="[[{0}, {{}}]]".format(seconds))
            self.assertEqual(code, 2, seconds)
            self.assertEqual(out, "", seconds)

    def test_check_26_every_text_delta_of_the_chosen_key_is_kept_in_order(self):
        record = silent_json("stream", samples([
            [0.4, {"choices": [{"delta": {"content": "hello"}}]}],
            [0.5, {"choices": [{"delta": {"content": " "}}]}],
            [1.2, {"choices": [{"delta": {"content": "world"}}]}],
            [1.4, {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}],
        ]))
        self.assertEqual(record["text"], "hello world")      # the whitespace delta is kept
        self.assertEqual(record["first_delta_s"], 0.4)

    def test_a_blank_delta_does_not_choose_the_key_but_is_still_concatenated(self):
        record = silent_json("stream", samples([
            [0.1, {"choices": [{"delta": {"content": "   "}}]}],
            [0.2, {"choices": [{"delta": {"content": "tail"}}]}],
        ]))
        self.assertEqual(record["text"], "   tail")
        self.assertEqual(record["first_delta_s"], 0.2)       # the first non-blank delta

    def test_check_26_the_clock_starts_at_the_first_non_blank_delta_of_any_key(self):
        record = silent_json("stream", samples([
            [0.3, {"choices": [{"delta": {"reasoning_content": "hmm"}}]}],
            [0.9, {"choices": [{"delta": {"content": "yes"}}]}],
            [1.3, {"usage": {"prompt_tokens": 90, "completion_tokens": 8}}],
        ]))
        self.assertEqual(record["first_delta_s"], 0.3)       # the reasoning delta's time
        self.assertEqual(record["text"], "yes")              # content is the text key
        self.assertEqual(record["prefill_tps"], 90 / 0.3)
        self.assertEqual(record["decode_tps"], 8 / (1.3 - 0.3))

    def test_only_the_chosen_key_is_concatenated(self):
        record = silent_json("stream", samples([
            [0.1, {"choices": [{"delta": {"reasoning_content": "think "}}]}],
            [0.2, {"choices": [{"delta": {"content": "ignored"}}]}],
            [0.3, {"choices": [{"delta": {"reasoning_content": "again"}}]}],
        ]))
        self.assertEqual(record["text"], "ignored")           # content beats reasoning
        self.assertEqual(record["first_delta_s"], 0.1)         # any text key starts the clock

    def test_a_delta_that_is_not_a_string_is_skipped(self):
        record = silent_json("stream", samples([
            [0.1, {"choices": [{"delta": {"content": 5}}]}],
            [0.2, {"choices": [{"delta": {"content": " real "}}]}],
            [0.3, {"choices": [{"delta": {"content": None}}]}],
        ]))
        self.assertEqual(record["text"], " real ")
        self.assertEqual(record["first_delta_s"], 0.2)

    def test_an_event_without_ausable_delta_shape_is_skipped(self):
        record = silent_json("stream", samples([
            [0.1, {"choices": "nope"}],
            [0.2, {"choices": [{"delta": "nope"}]}],
            [0.3, {"choices": []}],
            [0.4, {}],
            [0.5, "not an object"],
            [0.6, {"choices": [{"delta": {"content": "y"}}]}],
        ]))
        self.assertEqual(record["text"], "y")
        self.assertEqual(record["first_delta_s"], 0.6)
        self.assertEqual(record["end_s"], 0.6)

    def test_the_last_object_without_the_other_count_makes_it_absent(self):
        record = silent_json("stream", samples([
            [1.0, {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}],
            [2.0, {"usage": {"prompt_tokens": 20}}],
        ]))
        self.assertEqual(record["prompt_tokens"], 20)
        self.assertIsNone(record["completion_tokens"])
        self.assertIsNone(record["prefill_tps"])

    def test_no_clock_rates_when_the_window_is_not_strictly_growing(self):
        for first, last in [(1.0, 1.0), (0.5, 0.4)]:
            record = silent_json("stream", samples([
                [first, {"choices": [{"delta": {"content": "a"}}]}],
                [last, {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}],
            ]))
            self.assertIsNone(record["prefill_tps"], (first, last))
            self.assertIsNone(record["decode_tps"], (first, last))
            self.assertIsNone(record["rate_source"], (first, last))

    def test_no_clock_rates_without_a_delta_or_without_a_count(self):
        record = silent_json("stream", samples(
            [[0.5, {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}]]))
        self.assertIsNone(record["first_delta_s"])
        self.assertIsNone(record["prefill_tps"])
        record = silent_json("stream", samples([
            [0.5, {"choices": [{"delta": {"content": "a"}}]}],
            [1.5, {"usage": {"prompt_tokens": None, "completion_tokens": 2}}],
        ]))
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["rate_source"])

    def test_a_clock_rate_that_rounds_to_zero_is_reported_as_absent(self):
        # round(decode, 1) is 0 for 0 tokens, so neither clock rate is kept.
        record = silent_json("stream", samples([
            [1.0, {"choices": [{"delta": {"content": "a"}}]}],
            [2.0, {"usage": {"prompt_tokens": 100, "completion_tokens": 0}}],
        ]))
        self.assertEqual(record["prompt_tokens"], 100)
        self.assertEqual(record["completion_tokens"], 0)
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertIsNone(record["rate_source"])
        # and a decode that rounds to a real tenth is kept
        record = silent_json("stream", samples([
            [1.0, {"choices": [{"delta": {"content": "a"}}]}],
            [2.0, {"usage": {"prompt_tokens": 100, "completion_tokens": 1}}],
        ]))
        self.assertEqual(record["decode_tps"], 1.0)
        self.assertEqual(record["rate_source"], "clock")

    def test_stream_never_reads_the_events_as_a_completion_body(self):
        # A streamed event has message nowhere: the text comes only from delta keys.
        record = silent_json("stream", samples([
            [0.1, {"choices": [{"message": {"content": "not a delta"}}]}],
        ]))
        self.assertEqual(record["text"], "")


class LogChecks(unittest.TestCase):
    """Brief check 9 on `log`: the timing regex is the only match."""

    def test_check_09_one_prompt_eval_line_gives_a_requests_line_and_rates(self):
        record = silent_json("log", "\n".join([NGEN_LINE, SLOT_LINE,
                                               PROMPT_EVAL_LINE, EVAL_LINE]))
        self.assertEqual(set(record), LOG_KEYS)
        self.assertTrue(record["requests_line"].startswith("requests="),
                        record["requests_line"])
        self.assertNotIn("requests=0", record["requests_line"])
        self.assertEqual(record["requests_line"],
                         "requests=1 prompt_tokens_total=1601 gen_tokens_total=10082")
        self.assertEqual(record["prompt_tokens_total"], 1601)
        self.assertEqual(record["gen_tokens_total"], 10082)
        self.assertIsNotNone(record["prefill_tps"])
        self.assertAlmostEqual(record["prefill_tps"], 1601 / 15.88, places=9)
        self.assertAlmostEqual(record["decode_tps"], 1000 / 222.67, places=9)
        lines = record["detail"].splitlines()
        self.assertTrue(lines[0].startswith("TTFT ms (prompt eval):"), lines)
        self.assertTrue(lines[1].startswith("TPOT ms:"), lines)
        self.assertTrue(lines[2].startswith("prefill tok/s over all prompts="), lines)
        self.assertIn("turns_with_n_gen=1 n_gen_max=100 n_gen_median=100", record["detail"])
        self.assertIn("turns_n_gen_ge_10000=0", record["detail"])

    def test_two_requests_sum_and_do_not_double_count_the_prompt_line(self):
        second_prompt = ("prompt eval time =      2000.00 ms /   400 tokens "
                         "(    5.00 ms per token,   200.00 tokens per second)")
        second_eval = ("eval time =      1000.00 ms /    8 tokens "
                       "(  125.00 ms per token,     8.00 tokens per second)")
        record = silent_json("log", "\n".join([PROMPT_EVAL_LINE, EVAL_LINE,
                                               second_prompt, second_eval]))
        self.assertEqual(record["requests_line"],
                         "requests=2 prompt_tokens_total=2001 gen_tokens_total=10090")
        self.assertEqual(record["prompt_tokens_total"], 2001)
        self.assertEqual(record["gen_tokens_total"], 10090)

    def test_check_09_a_scheduler_line_is_not_a_match(self):
        for line in ["scheduler #new-token batch=4",
                     "sched: decode 4 tokens, prompt 12 tokens, 9.9 ms"]:
            record = silent_json("log", line)
            self.assertIsNone(record["requests_line"], line)
            self.assertIsNone(record["prompt_tokens_total"], line)
            self.assertIsNone(record["gen_tokens_total"], line)
            self.assertIsNone(record["prefill_tps"], line)
            self.assertIsNone(record["decode_tps"], line)
            self.assertEqual(record["detail"], "", line)

    def test_a_line_that_merely_holds_the_words_is_not_a_match(self):
        record = silent_json("log", "prompt eval time is not measured here\neval time lost\n")
        self.assertIsNone(record["requests_line"])
        self.assertIsNone(record["prefill_tps"])
        self.assertEqual(record["detail"], "")

    def test_no_prompt_eval_match_leaves_n_gen_detail_and_null_numbers(self):
        record = silent_json("log", "\n".join([NGEN_LINE, NGEN_LINE_LONG]))
        self.assertIsNone(record["requests_line"])       # never requests=0
        self.assertIsNone(record["prompt_tokens_total"])
        self.assertIsNone(record["gen_tokens_total"])
        self.assertIsNone(record["prefill_tps"])
        self.assertIsNone(record["decode_tps"])
        self.assertEqual(record["detail"],
                         "turns_with_n_gen=2 n_gen_max=10082 n_gen_median=10082\n"
                         "turns_n_gen_ge_10000=1")

    def test_an_empty_log_is_an_empty_record(self):
        for text in ["", "\n", "   \n\t\n"]:
            record = silent_json("log", text)
            self.assertIsNone(record["requests_line"], text)
            self.assertEqual(record["detail"], "", text)

    def test_the_last_n_gen_per_slot_task_is_the_one_recorded(self):
        record = silent_json("log", "\n".join([
            "print_timing: id  0 | task 7 | n_gen =     12, tg =   4.49 t/s",
            "print_timing: id  0 | task 7 | n_gen =  10082, tg =   4.49 t/s",
        ]))
        self.assertIn("turns_with_n_gen=1 n_gen_max=10082", record["detail"])


class CaptureChecks(TempTree):
    """Brief checks 13-16, 28-30 on `capture`: human lines, container log first."""

    def test_check_13_a_container_prompt_eval_line_ends_the_command(self):
        code, out, err, lines = self.capture(
            PROMPT_EVAL_LINE + "\n" + EVAL_LINE + "\n",
            '{"usage":{"prompt_tokens":99999}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines[0], "requests=1 prompt_tokens_total=1601 gen_tokens_total=10082")
        self.assertTrue(any(line.startswith("TTFT ms (prompt eval):") for line in lines), lines)
        self.assertTrue(any(line.startswith("prefill tok/s over all prompts=") for line in lines),
                        lines)
        self.assertNotIn("99999", out)                       # the build log was not read
        self.assertNotIn(NO_TOKEN_LINE, out)
        self.assertFalse([line for line in lines
                          if line.startswith("prompt_tokens_total=")], lines)
        self.assertNotIn("prefill_tps=", out)                # no second rate line either

    def test_check_13_the_build_log_is_not_even_opened_on_that_branch(self):
        code, out, err, lines = self.capture(PROMPT_EVAL_LINE + "\n" + EVAL_LINE + "\n", None)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines[0], "requests=1 prompt_tokens_total=1601 gen_tokens_total=10082")
        self.assertEqual(out.splitlines(), lines)

    def test_check_14_two_plain_usage_objects_sum(self):
        code, out, err, lines = self.capture(
            "", '{"usage":{"prompt_tokens":100}}\n{"usage":{"prompt_tokens":23}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=123"])
        self.assertNotIn("requests=", out)

    def test_check_15_a_completion_without_a_count_makes_the_sentence_absent(self):
        for label, build in [
            ("separate lines", '{"usage":{"prompt_tokens":1}}\n'
                               '{"choices":[{"message":{"content":"z"}}]}\n'),
            ("one line", '{"usage":{"prompt_tokens":1}} '
                         '{"choices":[{"message":{"content":"z"}}]}\n'),
        ]:
            code, out, err, lines = self.capture("", build)
            self.assertEqual((code, err), (0, ""), label)
            self.assertEqual(lines[0], NO_RATE_LINE, label)
            self.assertEqual(lines[1], NO_TOKEN_LINE, label)
            self.assertNotIn("prompt_tokens_total=", out, label)

    def test_check_16_an_sse_run_is_one_completion_with_its_last_usage(self):
        first = 'data: {"choices":[{"delta":{"content":"a"}}],"usage":{"prompt_tokens":7}}'
        last = 'data: {"choices":[{"delta":{"content":"b"}}],"usage":{"prompt_tokens":50}}'
        for label, build in [
            ("adjacent", first + "\n" + last + "\n"),
            ("blank line between", first + "\n\n" + last + "\n"),
        ]:
            code, out, err, lines = self.capture("", build)
            self.assertEqual((code, err), (0, ""), label)
            self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=50"], label)

    def test_check_16_a_blank_line_between_two_plain_objects_leaves_them_two_completions(self):
        # A blank line holds no data, so a run of nothing but blank lines is no completion of its own whose
        # count is absent: two plain usage objects that nothing but a blank line separates stay two counted
        # completions, and their counts sum.
        code, out, err, lines = self.capture(
            "", '{"usage":{"prompt_tokens":7}}\n\n{"usage":{"prompt_tokens":50}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=57"])
        self.assertNotIn(NO_TOKEN_LINE, out)

    def test_blank_lines_alone_are_no_completion(self):
        # the blank lines a file leads or ends with, and the ones between two completions, add no completion
        for label, build, total in [
            ("trailing blank lines", '{"usage":{"prompt_tokens":7}}\n\n   \n', "7"),
            ("leading blank lines", '\n\t\n{"usage":{"prompt_tokens":7}}\n', "7"),
            ("blank lines round a data run", '\ndata: {"usage":{"prompt_tokens":9}}\n\n', "9"),
            ("blank lines between a run and a plain line",
             'data: {"usage":{"prompt_tokens":4}}\n\n\n{"usage":{"prompt_tokens":2}}\n', "6"),
        ]:
            code, out, err, lines = self.capture("", build)
            self.assertEqual((code, err), (0, ""), label)
            self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=" + total], label)
            self.assertNotIn(NO_TOKEN_LINE, out, label)
        # a build log of nothing but blank lines holds no recognised completion, so it is not measured
        for label, build in [("no lines at all", ""), ("blank lines only", "\n   \n\t\n")]:
            code, out, err, lines = self.capture("", build)
            self.assertEqual((code, err), (0, ""), label)
            self.assertEqual(lines, [NO_RATE_LINE, NO_TOKEN_LINE], label)

    def test_check_16_a_prose_line_between_two_runs_splits_them(self):
        # the prose line is no completion at all, so it splits the runs and the two counts still sum
        build = ('data: {"usage":{"prompt_tokens":7}}\n'
                 'prose here\n'
                 'data: {"usage":{"prompt_tokens":50}}\n')
        code, out, err, lines = self.capture("", build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=57"])

    def test_a_done_line_is_neither_an_event_nor_a_usage(self):
        code, out, err, lines = self.capture(
            "", 'data: {"usage":{"prompt_tokens":33}}\ndata: [DONE]\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=33"])
        # a plain `Done.` is prose: it ends the run, it is not a completion, the count stays measured
        code, out, err, lines = self.capture(
            "", 'data: {"usage":{"prompt_tokens":4}}\nDone.\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=4"])

    def test_a_data_payload_that_is_not_an_object_is_skipped(self):
        code, out, err, lines = self.capture(
            "", 'data: not json\ndata: 7\ndata: {"usage":{"prompt_tokens":9}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=9"])

    def test_a_plain_line_that_holds_two_objects_reports_both(self):
        code, out, err, lines = self.capture(
            "", '{"usage":{"prompt_tokens":3}} {"usage":{"prompt_tokens":4}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=7"])

    def test_a_nested_usage_is_not_its_own_completion(self):
        build = ('{"choices":[{"message":{"content":"z"}}],"usage":{"prompt_tokens":8},'
                 '"meta":{"usage":{"prompt_tokens":500}}}\n')
        code, out, err, lines = self.capture("", build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=8"])
        self.assertNotIn("500", out)

    def test_check_28_a_missing_container_log_is_an_empty_log(self):
        for label, container in [("absent path", None), ("empty file", "")]:
            code, out, err, lines = self.capture(container,
                                                 '{"usage":{"prompt_tokens":4}}\n')
            self.assertEqual((code, err), (0, ""), label)
            self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=4"], label)

    def test_an_unreadable_container_log_is_an_empty_log(self):
        directory = os.path.join(self.tmp, "container.log")
        os.makedirs(directory)
        code, out, err, lines = self.capture(None, '{"usage":{"prompt_tokens":4}}\n')
        self.assertEqual((code, err), (0, ""), )
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=4"])
        self.assertTrue(os.path.isdir(directory))

    def test_a_missing_build_log_on_that_branch_exits_2(self):
        code, out, err = self.capture("", None)[:3]
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn(NO_TOKEN_LINE, err)

    def test_capture_needs_two_paths(self):
        code, out, err = invoke(["capture", self.write("only.log", "")])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_check_29_two_zero_counts_print_a_measured_zero(self):
        code, out, err, lines = self.capture(
            "", '{"usage":{"prompt_tokens":0}}\n{"usage":{"prompt_tokens":0}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=0"])

    def test_check_29_n_gen_lines_alone_never_print_requests_zero(self):
        code, out, err, lines = self.capture(NGEN_LINE + "\n",
                                             '{"usage":{"prompt_tokens":100}}\n'
                                             '{"usage":{"prompt_tokens":23}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("requests=", out)
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=123"])

    def test_check_30_a_wrapper_object_is_not_a_completion(self):
        build = ('{"response":{"choices":[{"message":{"content":"z"}}],'
                 '"usage":{"prompt_tokens":8}}}\n')
        code, out, err, lines = self.capture("", build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, NO_TOKEN_LINE])
        self.assertNotIn("prompt_tokens_total=", out)
        # beside a real completion the wrapper is still no completion at all: it neither counts
        # nor turns the sentence absent, so the completion that does hold usage prints on its own
        code, out, err, lines = self.capture("", build + '{"usage":{"prompt_tokens":20}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, "prompt_tokens_total=20"])
        self.assertNotIn("=8", out)

    def test_the_rate_line_is_the_last_completion_that_has_both_rates(self):
        build = ('{"choices":[{"message":{"content":"a"}}],"usage":{"prompt_tokens":10},'
                 '"timings":{"prompt_per_second":612.4,"predicted_per_second":45.5}}\n'
                 '{"choices":[{"message":{"content":"b"}}],"usage":{"prompt_tokens":20},'
                 '"timings":{"prompt_ms":500,"prompt_per_second":1234.6,'
                 '"predicted_per_second":40.04}}\n')
        code, out, err, lines = self.capture("", build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, ["prefill_tps=1235 decode_tps=40.0", "prompt_tokens_total=30"])

    def test_a_partial_timings_object_on_the_last_event_does_not_qualify(self):
        both = ('data: {"choices":[{"delta":{"content":"a"}}],'
                '"usage":{"prompt_tokens":5},'
                '"timings":{"prompt_per_second":900,"predicted_per_second":30}}')
        partial = ('data: {"choices":[{"delta":{"content":"b"}}],"usage":{"prompt_tokens":6},'
                   '"timings":{"prompt_per_second":1200.0}}')
        code, out, err, lines = self.capture("", both + "\n" + partial + "\n")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines[0], NO_RATE_LINE)
        self.assertEqual(lines[1], "prompt_tokens_total=6")

    def test_an_sse_run_reads_the_timings_of_its_last_timings_event(self):
        earlier = ('data: {"choices":[{"delta":{"content":"a"}}],"usage":{"prompt_tokens":5},'
                   '"timings":{"prompt_per_second":900,"predicted_per_second":30}}')
        later = ('data: {"choices":[{"delta":{"content":"b"}}],"usage":{"prompt_tokens":6},'
                 '"timings":{"prompt_per_second":1200.0,"predicted_per_second":41.06}}')
        code, out, err, lines = self.capture("", earlier + "\n" + later + "\n")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, ["prefill_tps=1200 decode_tps=41.1", "prompt_tokens_total=6"])

    def test_a_reported_zero_rate_prints_a_real_zero(self):
        build = ('{"choices":[{}],"usage":{"prompt_tokens":3},'
                 '"timings":{"prompt_per_second":0.0,"predicted_per_second":0.0}}\n')
        code, out, err, lines = self.capture("", build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, ["prefill_tps=0 decode_tps=0.0", "prompt_tokens_total=3"])

    def test_an_empty_build_log_is_not_measured(self):
        code, out, err, lines = self.capture("", "")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines, [NO_RATE_LINE, NO_TOKEN_LINE])

    def test_capture_prints_human_lines_only(self):
        code, out, err, lines = self.capture("", '{"usage":{"prompt_tokens":12}}\n')
        self.assertEqual((code, err), (0, ""))
        self.assertFalse([line for line in lines if line.lstrip().startswith("{")], lines)
        self.assertNotIn('"prompt_tokens"', out)

    def test_the_container_rates_are_not_added_to_the_build_log_rates(self):
        container = PROMPT_EVAL_LINE + "\n" + EVAL_LINE + "\n"
        build = ('{"choices":[{"message":{"content":"x"}}],"usage":{"prompt_tokens":10},'
                 '"timings":{"prompt_per_second":1000,"predicted_per_second":50}}\n')
        code, out, err, lines = self.capture(container, build)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines[0], "requests=1 prompt_tokens_total=1601 gen_tokens_total=10082")
        self.assertNotIn("1000", out)         # the build-log timings are not the rate here
        self.assertNotIn("prefill_tps=", out)


class WindowChecks(unittest.TestCase):
    """Brief checks 17-19 and 27 on `window`: one line, and 0 0 0 0 is never it."""

    def window(self, text):
        lines = human("window", text)
        self.assertEqual(len(lines), 1, text)          # stdout is one line
        self.assertNotEqual(lines[0], "0 0 0 0", text)
        return lines[0]

    def test_check_17_an_empty_body_and_an_error_object_are_no_answer(self):
        for text in ["", "   ", "\n\t\n", '{"error":{"message":"no"}}']:
            self.assertEqual(self.window(text), "NO_ANSWER", text)

    def test_check_27_a_body_that_is_not_an_object_is_no_answer(self):
        for text in ["[]", "null", '[{"choices": []}]', "5", '"a string"',
                     "true", "false"]:
            self.assertEqual(self.window(text), "NO_ANSWER", text)

    def test_check_18_a_completion_with_no_count_is_unmeasured(self):
        self.assertEqual(self.window('{"choices":[{"message":{"content":"x"}}]}'),
                         "UNMEASURED")

    def test_an_error_alongside_choices_is_still_an_answer(self):
        self.assertEqual(self.window('{"error":{"message":"no"},'
                                     '"choices":[{"message":{"content":"x"}}]}'),
                         "UNMEASURED")

    def test_an_absent_count_is_unmeasured_and_never_zero(self):
        for text in [usage_body(), usage_body(prompt_tokens=None),
                     usage_body(prompt_tokens=1.0), usage_body(prompt_tokens=True),
                     usage_body(prompt_tokens=-1), '{"usage":"nope"}']:
            self.assertEqual(self.window(text), "UNMEASURED", text)

    def test_check_19_a_count_with_no_rates(self):
        self.assertEqual(self.window(usage_body(prompt_tokens=10)),
                         "10 MISSING MISSING MISSING")
        self.assertEqual(self.window(timing_body(prompt_per_second=612.4,
                                                 usage={"prompt_tokens": 10})),
                         "10 MISSING MISSING MISSING")

    def test_a_measured_zero_count_is_not_no_answer(self):
        self.assertEqual(self.window(timing_body(prompt_per_second=900,
                                                 predicted_per_second=40,
                                                 usage={"prompt_tokens": 0})),
                         "0 MISSING 900 40")

    def test_both_rates_present_print_the_four_fields(self):
        # round() is half to even: 1234.5 -> 1234, 40.04 -> 40.0, 1234/1000 -> 1.2
        text = ('{"choices":[{"message":{"content":"x"}}],"usage":{"prompt_tokens":10},'
                '"timings":{"prompt_ms":1234,"prompt_per_second":1234.5,'
                '"predicted_per_second":40.04}}')
        self.assertEqual(self.window(text), "10 1.2 1234 40.0")

    def test_half_to_even_rounding_reaches_the_human_line(self):
        text = ('{"choices":[{"message":{"content":"x"}}],"usage":{"prompt_tokens":9},'
                '"timings":{"prompt_ms":125,"prompt_per_second":100.5,'
                '"predicted_per_second":10.25}}')
        self.assertEqual(self.window(text), "9 0.1 100 10.2")

    def test_ttft_comes_from_prompt_ms_and_a_real_zero_is_kept(self):
        for prompt_ms, expected in [(0, "0.0"), (500, "0.5"), (1234, "1.2")]:
            text = json.dumps({"choices": [{"message": {"content": "x"}}],
                               "usage": {"prompt_tokens": 10},
                               "timings": {"prompt_ms": prompt_ms,
                                           "prompt_per_second": 900,
                                           "predicted_per_second": 40}})
            self.assertEqual(self.window(text), "10 {0} 900 40".format(expected), prompt_ms)

    def test_a_zero_prompt_ms_is_printed_and_an_absent_or_negative_one_is_missing(self):
        for prompt_ms in ["null", "-5", '"500"', "true", "NaN", "Infinity"]:
            text = ('{"choices":[{"message":{"content":"x"}}],"usage":{"prompt_tokens":10},'
                    '"timings":{"prompt_ms":__MS__,"prompt_per_second":900,'
                    '"predicted_per_second":40}}').replace("__MS__", prompt_ms)
            self.assertEqual(self.window(text), "10 MISSING 900 40", prompt_ms)


class HealthChecks(unittest.TestCase):
    """Brief check 21's module half: `health` reads one argument, never a body."""

    def test_a_two_xx_code_is_ok(self):
        for code in ["200", "204", "299", "0204"]:
            rc, out, err = invoke(["health", code])
            self.assertEqual((rc, out, err), (0, "ok\n", ""), code)

    def test_any_other_code_is_no(self):
        for code in ["199", "300", "302", "500", "0", "00", "1000"]:
            rc, out, err = invoke(["health", code])
            self.assertEqual((rc, out, err), (1, "no\n", ""), code)

    def test_an_argument_that_is_not_digits_is_no(self):
        for code in ["ok", "", "-1", "+200", " 200", "200 ", "200x", "2o4", "2e2",
                     "0x1", "200.0"]:
            rc, out, err = invoke(["health", code])
            self.assertEqual((rc, out, err), (1, "no\n", ""), repr(code))

    def test_no_argument_is_no_and_does_not_raise(self):
        rc, out, err = invoke(["health"])
        self.assertEqual((rc, out, err), (1, "no\n", ""))

    def test_the_body_is_not_an_argument(self):
        rc, out, err = invoke(["health", "200", '{"status":"ok"}'])
        self.assertEqual((rc, out, err), (1, "no\n", ""))
        rc, out, err = invoke(["health", "204", ""])
        self.assertEqual((rc, out, err), (1, "no\n", ""))

    def test_health_does_not_read_stdin(self):
        code, out, err = invoke(["health", "204"], stdin=b"")
        self.assertEqual((code, out, err), (0, "ok\n", ""))

    def test_a_long_digit_string_does_not_raise(self):
        rc, out, err = invoke(["health", ("0" * 4400) + "200"])
        self.assertEqual((rc, out, err), (0, "ok\n", ""))
        rc, out, err = invoke(["health", "9" * 5000])
        self.assertEqual((rc, out, err), (1, "no\n", ""))
        self.assertNotIn("Traceback", err)


class ModuleHygiene(unittest.TestCase):
    """Brief check 12's and 23's clause on the file itself: no engine, no clock."""

    def setUp(self):
        with open(MODULE, "r", encoding="utf-8") as handle:
            self.source = handle.read()

    def test_check_23_no_engine_or_context_size_vocabulary(self):
        for forbidden in ["useful_ctx", "llama-benchy", "8192", "ENGINE_VALUES"]:
            self.assertNotIn(forbidden, self.source)

    def test_no_branch_on_a_backend_name(self):
        for forbidden in ["vllm", "sglang", "bench_http", "benchy"]:
            self.assertNotIn(forbidden, self.source)

    def test_no_engine_is_imported_and_no_clock_is_read(self):
        for forbidden in ["import time", "from time", "monotonic", "perf_counter",
                          "datetime", "import subprocess"]:
            self.assertNotIn(forbidden, self.source)

    def test_no_pick_command_exists(self):
        self.assertNotIn("pick", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
