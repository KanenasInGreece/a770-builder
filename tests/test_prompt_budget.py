"""Tests for harness/prompt_budget.py, the estimator behind every long prompt the ladder sends.

`curve` reads llama-bench's own results file in each of the shapes those files come in: a top-level list, an
object carrying `raw`, rows naming their speed `avg_ts` or `t/s`. A document that is none of those, or a file
that cannot be read at all, gives an empty curve rather than an exception.

`prefill_seconds` integrates seconds-per-token against depth. It needs two depths that were really measured, and
counts only readings above zero, so a curve with one usable depth reports no estimate instead of extrapolating
from the synthetic shallow anchor at the shallow rate. On the curve the shipped 9B row was measured from, it
returns the prefill times that model's own runs took.

`decode_seconds` budgets generation at the slowest measured rate above zero, so a deep prompt is never
provisioned at a shallow depth's decode speed, and a reading of 0.0 is ignored rather than dividing by zero or
discarding the readings that are usable.

`budget` prints a deadline and says how it got it. With no usable curve the deadline is the fixed generous
`UNMEASURED_BUDGET_S`, which stands above the slowest profile's own time for a 100,000-token prompt, and a
`--bench` file that was given and could not be used says so instead of quietly becoming that same fallback.

`fit` cuts the corpus to a real token count with the server's own tokeniser. It is bounded on both sides: a
prompt over the target is refused, and so is one far under it, so no run reports a prompt as the size it asked
for when the tokeniser says otherwise.

Offline: synthetic bench files and a stubbed token count, no card and no server.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

MOD = pathlib.Path(__file__).resolve().parent.parent / "harness" / "prompt_budget.py"


def load():
    spec = importlib.util.spec_from_file_location("prompt_budget_under_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NINE_B = [  # the 9B's own llama-bench rows, as that model measured on this card
    {"n_depth": 0, "n_prompt": 8192, "n_gen": 0, "avg_ts": 575.906343},
    {"n_depth": 0, "n_prompt": 0, "n_gen": 128, "avg_ts": 45.772166},
    {"n_depth": 8192, "n_prompt": 8192, "n_gen": 0, "avg_ts": 414.080038},
    {"n_depth": 8192, "n_prompt": 0, "n_gen": 128, "avg_ts": 36.399182},
    {"n_depth": 32768, "n_prompt": 8192, "n_gen": 0, "avg_ts": 218.792395},
    {"n_depth": 32768, "n_prompt": 0, "n_gen": 128, "avg_ts": 23.11061},
    {"n_depth": 100000, "n_prompt": 8192, "n_gen": 0, "avg_ts": 89.263201},
    {"n_depth": 100000, "n_prompt": 0, "n_gen": 128, "avg_ts": 11.621887},
]


def write(tmp_path, name, doc):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return str(p)


def test_a_bench_file_that_is_a_bare_list_is_read_not_crashed_on(tmp_path):
    """A llama-bench results file can be a top-level list, the shape the serious profile's own file comes in."""
    m = load()
    at = m.curve(write(tmp_path, "list.json", NINE_B))
    assert at, "a top-level-list bench file must produce a curve"
    assert 8192 in at and at[8192]["pp"] == pytest.approx(414.080038)


def test_the_unmeasured_fallback_is_not_shorter_than_a_real_deep_run(tmp_path):
    """The slowest profile needs about 4,650 s for a 100,000-token prompt: the fallback stands above that."""
    m = load()
    assert m.UNMEASURED_BUDGET_S >= 4700


def test_one_measured_depth_is_refused_not_extrapolated_flat(tmp_path):
    """The slope comes from readings that were really taken, never from the synthetic depth-zero anchor."""
    m = load()
    at = m.curve(write(tmp_path, "one.json", [{"n_depth": 8192, "n_prompt": 8192, "n_gen": 0, "avg_ts": 414.08}]))
    seconds, _ = m.prefill_seconds(at, 100000)
    assert seconds is None, "a single measured depth cannot be extrapolated; it must report no estimate"


def test_two_measured_depths_still_estimate(tmp_path):
    m = load()
    at = m.curve(write(tmp_path, "two.json", NINE_B[:6]))
    seconds, extrapolated = m.prefill_seconds(at, 100000)
    assert seconds is not None and seconds > 0
    assert extrapolated is True


def test_the_measured_nine_b_curve_still_integrates_to_what_the_card_measured(tmp_path):
    """The measured 9B curve integrates to the prefill times that model's own runs took."""
    m = load()
    at = m.curve(write(tmp_path, "9b.json", NINE_B))
    at_100k, _ = m.prefill_seconds(at, 100000)
    at_132k, _ = m.prefill_seconds(at, 132865)
    assert at_100k == pytest.approx(633, abs=25), f"100k prefill should be ~633 s, got {at_100k}"
    assert at_132k == pytest.approx(1055, abs=40), f"132,865 prefill should be ~1055 s, got {at_132k}"


def test_decode_is_budgeted_at_the_slowest_measured_rate(tmp_path):
    """A deep prompt is budgeted at the slowest measured decode rate, not at the rate nearest below its depth."""
    m = load()
    at = m.curve(write(tmp_path, "9b.json", NINE_B))
    slowest = min(v["tg"] for v in at.values() if "tg" in v)
    assert m.decode_seconds(at, 99000, 320) == pytest.approx(320 / slowest, rel=0.01)


def test_fit_refuses_a_prompt_far_under_the_target(monkeypatch, tmp_path):
    """`fit` is bounded below as well as above: a prompt far under the target is refused, never written."""
    m = load()
    corpus = tmp_path / "c.py"
    corpus.write_text("def a():\n    return 1\n" * 20000)
    out = tmp_path / "out.txt"

    calls = {"n": 0}

    def liar(url, key, text):  # honest when small, wild when large: a token count that runs away with size
        calls["n"] += 1
        return len(text) // 3 if len(text) < 5000 else 10 ** 7

    monkeypatch.setattr(m, "count_tokens", liar)
    args = type("A", (), {"corpus": str(corpus), "target": 100000, "out": str(out),
                          "plant_file": None, "plant_at": 0.85, "overhead": 256,
                          "url": "http://127.0.0.1:1", "key": ""})()
    assert m.fit(args) == 2, "a prompt far under the target must be refused, not written and reported as the target"


def test_fit_never_exceeds_the_target(monkeypatch, tmp_path):
    m = load()
    corpus = tmp_path / "c.py"
    corpus.write_text("def a():\n    return 1\n" * 40000)
    out = tmp_path / "out.txt"
    monkeypatch.setattr(m, "count_tokens", lambda url, key, text: len(text) // 3)
    args = type("A", (), {"corpus": str(corpus), "target": 20000, "out": str(out),
                          "plant_file": None, "plant_at": 0.85, "overhead": 64,
                          "url": "http://127.0.0.1:1", "key": ""})()
    assert m.fit(args) == 0
    assert len(out.read_text()) // 3 <= 20000 - 64


def test_a_bench_that_was_given_but_is_unusable_is_not_silently_the_same_as_none(tmp_path, capsys):
    """A --bench file that was given and could not be used says so, rather than quietly becoming the fallback."""
    m = load()
    args = type("A", (), {"bench": str(tmp_path / "does-not-exist.json"), "tokens": 98000,
                          "gen": 320, "safety": 1.5, "floor": 300, "cap": 7200})()
    m.budget(args)
    err = capsys.readouterr().err
    assert "UNMEASURED" in err
    assert "does-not-exist.json" in err or "no usable curve" in err.lower(), \
        "the note must name that a --bench was given and could not be used"


def test_rows_using_the_t_per_s_field_name_are_read(tmp_path):
    """The same results files name their speed avg_ts or t/s, and the estimator reads either one."""
    m = load()
    rows = [{"n_depth": d, "n_prompt": 8192, "n_gen": 0, "t/s": v} for d, v in ((0, 575.9), (8192, 414.1))]
    at = m.curve(write(tmp_path, "ts.json", rows))
    assert at and at[8192]["pp"] == pytest.approx(414.1)


# ── the readings a curve must survive: a rate of zero, and a document that is no bench file at all ──────

def test_decode_survives_a_measured_rate_of_zero():
    """A generation rate of 0.0 is unusable, not authoritative.

    It is ignored rather than divided by, and it never discards the readings that are usable. A curve
    whose only generation reading is 0.0 budgets no decode time at all.
    """
    m = load()
    at = {0: {"pp": 500.0, "tg": 40.0}, 8192: {"pp": 400.0, "tg": 0.0}}
    assert m.decode_seconds(at, 5000, 320) == pytest.approx(320 / 40.0)
    at3 = {0: {"pp": 500.0, "tg": 40.0}, 8192: {"pp": 400.0, "tg": 0.0}, 32768: {"pp": 200.0, "tg": 20.0}}
    assert m.decode_seconds(at3, 99000, 320) == pytest.approx(320 / 20.0)
    assert m.decode_seconds({0: {"pp": 500.0, "tg": 0.0}}, 5000, 320) == 0.0


def test_curve_survives_a_bench_file_that_is_neither_list_nor_object(tmp_path):
    """A document that is neither list nor object gives an empty curve, never an AttributeError."""
    m = load()
    for doc in (123, "not a bench file", None):
        p = tmp_path / "odd.json"
        p.write_text(json.dumps(doc))
        assert m.curve(str(p)) == {}


def test_a_zero_speed_reading_cannot_revive_the_flat_extrapolation(tmp_path):
    """The two-real-depths guard applies after the filter that drops rates of zero, so a curve left with
    one usable depth reports no estimate instead of extrapolating from the anchor at the shallow rate."""
    m = load()
    at = {8192: {"pp": 414.08}, 32768: {"pp": 0.0}}
    seconds, _ = m.prefill_seconds(at, 100000)
    assert seconds is None, "only one usable depth remains after filtering — that cannot be extrapolated"
