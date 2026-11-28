#!/usr/bin/env python3
"""Strata attach, caller and kit slice — fixtures only.

A fake 127.0.0.1 endpoint serves tests/fixtures/strata/ (the pinned v0.2.16 /health and /v1/models shapes).
Nothing here starts a container, touches a GPU, needs Strata installed, or runs OpenCode: the run_suite tests
are dry runs, and the probe tests drive strata_attach.py directly.
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "strata"
LOCAL_BUILD = ROOT / "skills" / "local-build" / "scripts" / "local-build.sh"
RUN_SUITE_SH = ROOT / "harness" / "run_suite.sh"
CAPTURE_SH = ROOT / "harness" / "capture_task.sh"
ENV_SH = ROOT / "harness" / "env.sh"
sys.path.insert(0, str(ROOT / "harness"))

import strata_attach  # noqa: E402
from usage_tap import BridgeSession  # noqa: E402

MODEL = "qwen-candidate"


# ---------------------------------------------------------------- fake endpoint

def _sse(events, done=True):
    out = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    return out + (b"data: [DONE]\n\n" if done else b"")


class FakeStrata:
    """One slot. /health is open; /v1/models and POSTs need the key when one is set. Every request is logged."""

    def __init__(self, key="sekret", health="health.json", models="models.json", health_doc=None,
                 health_status=200, models_status=200, tool_sse="tool-call.sse", followup="echo",
                 abort_stream=None, followup_status=200, followup_responses=None, prompt_tokens=None):
        self.key = key
        self.health = json.dumps(health_doc).encode() if health_doc is not None else (FIX / health).read_bytes()
        self.models = (FIX / models).read_bytes()
        self.health_status, self.models_status = health_status, models_status
        self.call_id = "call_" + secrets.token_hex(6)
        self.tool_sse = (FIX / tool_sse).read_bytes().replace(b'"call_0"', json.dumps(self.call_id).encode())
        self.followup = followup
        self.abort_stream = abort_stream
        self.followup_status = followup_status
        self.followup_responses = iter(followup_responses) if followup_responses is not None else None
        self.prompt_tokens = prompt_tokens
        self.slot = threading.Semaphore(1)
        self.log = []
        self.health_auth = []   # the Authorization header of each GET /health (None when absent)
        self.models_auth = []   # the same for each GET /v1/models
        self.post_auth = []
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _authed(self):
                return not fake.key or self.headers.get("Authorization") == "Bearer " + fake.key

            def _send(self, status, body, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.log.append(("GET", self.path))
                if self.path == "/health":
                    fake.health_auth.append(self.headers.get("Authorization"))
                    return self._send(fake.health_status, fake.health)
                if self.path == "/v1/models":
                    fake.models_auth.append(self.headers.get("Authorization"))
                if not self._authed():
                    return self._send(401, b'{"error":"unauthorized"}')
                if self.path == "/v1/models":
                    return self._send(fake.models_status, fake.models)
                return self._send(404, b"{}")

            def do_POST(self):
                fake.log.append(("POST", self.path))
                fake.post_auth.append(self.headers.get("Authorization"))
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                if not self._authed():
                    return self._send(401, b'{"error":"unauthorized"}')
                if not fake.slot.acquire(blocking=False):
                    return self._send(503, b'{"error":"busy"}')
                try:
                    msgs = body.get("messages") or []
                    if not body.get("stream"):
                        if msgs == [{"role": "user", "content": "Say ok."}]:
                            if fake.followup_responses is not None:
                                reply = next(fake.followup_responses, None)
                                if reply is None:
                                    self.close_connection = True
                                    return
                                return self._send(*reply)
                            if fake.followup_status != 200:
                                return self._send(fake.followup_status, b'{"error":"unavailable"}')
                        words = len((msgs[-1]["content"] if msgs else "").split()) + 20
                        if body.get("tools") and body.get("tool_choice") != "none":
                            words += len(json.dumps(body["tools"]).split())
                        pt = fake.prompt_tokens if fake.prompt_tokens is not None else words
                        out = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": pt, "completion_tokens": 1, "total_tokens": pt + 1}}
                        return self._send(200, json.dumps(out).encode())
                    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
                    if tool_msgs:
                        return self._send(200, self._answer(tool_msgs[-1]), "text/event-stream")
                    if body.get("tools"):
                        return self._send(200, fake.tool_sse, "text/event-stream")
                    if fake.abort_stream is not None:
                        return self._send(200, fake.abort_stream, "text/event-stream")
                    # a long stream that runs until the client hangs up
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    try:
                        for i in range(400):
                            chunk = {"model": MODEL, "choices": [{"index": 0, "delta": {"content": "%d " % i}}]}
                            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                            self.wfile.flush()
                            time.sleep(0.02)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    self.close_connection = True
                finally:
                    fake.slot.release()

            def _answer(self, tool_msg):
                mode = fake.followup
                if tool_msg.get("tool_call_id") != fake.call_id:
                    text = "unknown call"
                elif mode == "unrelated":
                    text = "I cannot help with that."
                else:
                    text = "The code is " + tool_msg.get("content", "")
                events = [{"model": MODEL, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}]
                if mode == "truncated":
                    return _sse(events, done=False)
                events.append({"model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                if mode != "no-usage":
                    events.append({"model": MODEL, "choices": [],
                                   "usage": {"prompt_tokens": 450, "completion_tokens": 9, "total_tokens": 459}})
                return _sse(events)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d/v1" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def posts(self):
        return sum(1 for method, _ in self.log if method == "POST")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


def _ep(fake, key="sekret"):
    return strata_attach.Endpoint(fake.url, key)


def _doc(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the pinned contract

def test_resolve_model_takes_the_requested_text_row_not_the_first():
    reason, m = strata_attach.resolve_model(_doc("models.json"), MODEL)
    assert reason is None and m == {"id": MODEL, "alias": False, "n_ctx": 32768}


def test_resolve_model_follows_an_alias_to_its_text_row():
    reason, m = strata_attach.resolve_model(_doc("models.json"), "qwen-alias")
    assert reason is None and m["id"] == MODEL and m["alias"] is True


@pytest.mark.parametrize("doc,name,word", [
    (_doc("models.json"), "img-gen", "not a text model"),
    (_doc("models.json"), "other-model", "unloaded"),
    (_doc("models.json"), "not-served", "mismatched"),
    (_doc("models-unloaded.json"), MODEL, "unloaded"),
    (_doc("models-flat-status.json"), MODEL, "unloaded"),
    (_doc("models-missing-meta.json"), MODEL, "meta.n_ctx"),
    ({"data": [{"id": "a", "alias_of": "gone"}]}, "a", "alias"),
    ({"data": [{"id": MODEL, "status": {"value": "loaded"}, "meta": {"n_ctx": 1}}]}, MODEL, "not a text model"),
    ({"data": []}, MODEL, "unavailable"),
    (None, MODEL, "unavailable"),
])
def test_resolve_model_refusals(doc, name, word):
    reason, m = strata_attach.resolve_model(doc, name)
    assert m is None and word in reason


@pytest.mark.parametrize("status,doc,word", [
    (500, _doc("health.json"), "HTTP 500"),
    (200, dict(_doc("health.json"), service="llama.cpp"), "mismatched"),
    (200, _doc("health-unloaded.json"), "unloaded"),
    (200, {k: v for k, v in _doc("health.json").items() if k != "max_context"}, "max_context"),
    (200, dict(_doc("health.json"), max_context=0), "max_context"),
    (200, [], "not a JSON object"),
])
def test_check_health_refusals(status, doc, word):
    reason, h = strata_attach.check_health(status, doc)
    assert h is None and word in reason


def test_check_health_accepts_the_pinned_answer():
    assert strata_attach.check_health(200, _doc("health.json")) == (None, {"model": MODEL, "max_context": 32768})


# ---------------------------------------------------------------- attach_check (GETs only)

@pytest.mark.parametrize("health,ctx,ok", [
    ("health.json", 32768, True),
    ("health.json", 32769, False),
    ("health-small-window.json", 8192, True),
    ("health-small-window.json", 8193, False),
])
def test_attach_window_is_the_smaller_of_max_context_and_n_ctx(health, ctx, ok):
    with FakeStrata(health=health) as fake:
        v = strata_attach.attach_check(_ep(fake), MODEL, ctx, "key")
    assert v["ok"] is ok, v
    if not ok:
        assert v["reason"].startswith("window:")
    assert fake.posts == 0


def test_attach_accepts_an_alias_of_the_loaded_model():
    with FakeStrata() as fake:
        v = strata_attach.attach_check(_ep(fake), "qwen-alias", 8192, "key")
    assert v["ok"] and v["model"] == MODEL and v["usable"] == 32768


@pytest.mark.parametrize("kw,name,word", [
    ({"models_status": 500}, MODEL, "HTTP 500"),            # an HTTP error whose body is otherwise valid
    ({"health_status": 503}, MODEL, "HTTP 503"),
    ({"health": "health-unloaded.json"}, MODEL, "unloaded"),
    ({"health_doc": {"service": "strata", "loaded": True, "model": "other", "max_context": 32768}}, MODEL,
     "mismatched: /health has"),
    ({}, "img-gen", "not a text model"),
    ({"key": "other"}, MODEL, "unauthorized"),
])
def test_attach_refusals(kw, name, word):
    with FakeStrata(**kw) as fake:
        v = strata_attach.attach_check(_ep(fake), name, 8192, "key")
    assert not v["ok"] and word in v["reason"], v
    assert fake.posts == 0


@pytest.mark.parametrize("url", [
    "http://localhost:8080/v1", "http://[::1]:8080/v1", "http://10.0.0.5:8080/v1", "http://127.0.0.2:8080",
    "https://127.0.0.1:8080", "http://127.0.0.1:8080/v1/x", "http://u:p@127.0.0.1:8080", "http://127.0.0.1",
])
def test_endpoint_accepts_only_numeric_ipv4_loopback(url):
    with pytest.raises(ValueError):
        strata_attach.Endpoint(url)


@pytest.mark.parametrize("requested,server_key,file_key,expected,headers", [
    ("auto", "", "sekret", "none", [None]),
    ("auto", "sekret", "sekret", "key", [None, "Bearer sekret"]),
    ("auto", "sekret", "", None, [None]),
    ("key", "", "sekret", None, [None]),
    ("key", "sekret", "sekret", "key", [None, "Bearer sekret"]),
    ("none", "sekret", "sekret", None, [None]),
    ("none", "", "sekret", "none", [None]),
])
def test_attach_cli_negotiates_auth_before_sending_credentials(
        requested, server_key, file_key, expected, headers, tmp_path):
    env = _env(tmp_path, key=file_key)
    with FakeStrata(key=server_key) as fake:
        r = subprocess.run([sys.executable, str(ROOT / "harness" / "strata_attach.py"), "attach", "--base-url",
                            fake.url, "--model", MODEL, "--ctx", "8192", "--auth", requested],
                           capture_output=True, text=True, env=env)
    if expected is not None:
        assert r.returncode == 0 and r.stdout.strip() == f"ok {expected} 32768"
    else:
        assert r.returncode == 1
    assert "sekret" not in r.stdout + r.stderr
    assert fake.health_auth == [None] and fake.models_auth == headers and fake.posts == 0
    assert Path(env["A770B_API_KEY_FILE"]).exists() == bool(file_key)


def test_attach_cli_ignores_environment_proxies(tmp_path):
    keyfile = tmp_path / "api.key"
    keyfile.write_text("sekret\n", encoding="utf-8")
    dead = "http://127.0.0.1:9"
    env = dict(os.environ, A770B_API_KEY_FILE=str(keyfile), http_proxy=dead, HTTP_PROXY=dead, all_proxy=dead,
               ALL_PROXY=dead)
    env.pop("no_proxy", None)
    env.pop("NO_PROXY", None)
    with FakeStrata() as fake:
        r = subprocess.run([sys.executable, str(ROOT / "harness" / "strata_attach.py"), "attach", "--base-url",
                            fake.url, "--model", MODEL, "--ctx", "8192"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == "ok key 32768", r.stdout + r.stderr


# ---------------------------------------------------------------- probe

def _tok(tmp_path):
    d = tmp_path / "tok"
    d.mkdir(exist_ok=True)
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    return str(d)


def _words(text, tok):
    return len(text.split())


def _probe(fake, tmp_path, mode="key", key="sekret", **kw):
    kw.setdefault("abort_timeout", 10)
    return strata_attach.probe(_ep(fake, key), MODEL, mode, _tok(tmp_path), "fixture@0", local_count=_words, **kw)


def test_probe_passes_and_keeps_no_returned_text(tmp_path):
    marker = "m" + secrets.token_hex(8)
    with FakeStrata() as fake:
        ev = _probe(fake, tmp_path, marker=marker)
    assert ev["ok"] and ev["smoke_ok"], json.dumps(ev["checks"], indent=2)
    c = ev["checks"]
    assert c["auth"] == {"ok": True, "mode": "key", "without_key": 401}
    assert c["tool_call"]["id_present"] and c["followup"]["marker_found"]
    assert ev["usage"]["tool_call"]["prompt_tokens"] == 412 and ev["usage"]["followup"]["prompt_tokens"] == 450
    assert c["count"]["api"]["bare"] - c["count"]["local"]["bare"] == 20
    assert c["count"]["api"]["tools"] - c["count"]["api"]["bare"] == len(strata_attach.TOOLS_TEXT.split())
    assert c["count"]["tokenizer"]["checkpoint"] == "fixture@0"
    assert len(c["count"]["tokenizer"]["tokenizer_json_sha256"]) == 64
    assert c["abort"]["ok"]
    assert c["abort"]["stream_status"] == 200
    assert c["abort"]["stream_outcome"] == "disconnected_after_progress"
    assert c["abort"]["followup_status"] == 200
    assert c["abort"]["followup_http_200_after_s"] >= 0
    assert "released_after_s" not in c["abort"]
    text = json.dumps(ev)
    assert marker not in text and "sekret" not in text and '"arguments"' not in text


def test_probe_unauthenticated_loopback(tmp_path):
    with FakeStrata(key="") as fake:
        ev = _probe(fake, tmp_path, mode="none", key="")
    assert ev["ok"] and ev["checks"]["auth"]["mode"] == "none"


@pytest.mark.parametrize("server_key,mode,model_headers,post_header", [
    ("", "none", [None], None),
    ("sekret", "key", [None, "Bearer sekret"], "Bearer sekret"),
])
def test_probe_auto_reuses_verified_access(tmp_path, server_key, mode, model_headers, post_header):
    with FakeStrata(key=server_key) as fake:
        ev = _probe(fake, tmp_path, mode="auto")
    assert ev["ok"] and ev["auth"] == mode and ev["checks"]["auth"]["mode"] == mode
    assert fake.models_auth == model_headers
    assert fake.post_auth and all(header == post_header for header in fake.post_auth)


@pytest.mark.parametrize("kw,name,key", [
    ({"health": "health-unloaded.json"}, MODEL, "sekret"),
    ({"models": "models-unloaded.json"}, MODEL, "sekret"),
    ({"models_status": 500}, MODEL, "sekret"),
    ({}, "img-gen", "sekret"),
    ({}, "not-served", "sekret"),
    ({"key": "other"}, MODEL, "sekret"),
    ({"key": ""}, MODEL, "sekret"),        # key mode, but the endpoint answers without one
])
def test_a_failed_preflight_sends_no_post(tmp_path, kw, name, key):
    with FakeStrata(**kw) as fake:
        ev = strata_attach.probe(_ep(fake, key), name, "key", _tok(tmp_path), "fixture@0", local_count=_words)
    assert not ev["ok"] and not ev["smoke_ok"]
    assert fake.posts == 0 and ev["posts"] == 0
    assert "tool_call" not in ev["checks"]


def test_key_mode_never_sends_the_key_to_health_and_only_to_a_confirmed_strata(tmp_path):
    wrong = dict(_doc("health.json"), service="llama.cpp")
    with FakeStrata(health_doc=wrong) as fake:
        ev = strata_attach.probe(_ep(fake), MODEL, "key", _tok(tmp_path), "fixture@0", local_count=_words)
    assert not ev["ok"] and "mismatched" in ev["checks"]["attach"]["reason"]
    assert fake.health_auth == [None] and fake.models_auth == [] and fake.posts == 0
    with FakeStrata() as fake:
        v = strata_attach.attach_check(_ep(fake), MODEL, 8192, "key")
    assert v["ok"] and fake.health_auth == [None] and fake.models_auth == [None, "Bearer sekret"]


def test_probe_refuses_a_tool_call_without_an_id(tmp_path):
    with FakeStrata(tool_sse="tool-call-no-id.sse") as fake:
        ev = _probe(fake, tmp_path)
    assert ev["checks"]["tool_call"]["reason"] == "the tool call carries no id"
    assert not ev["checks"]["followup"]["ok"] and not ev["smoke_ok"]


@pytest.mark.parametrize("mode,word", [
    ("unrelated", "marker"),
    ("truncated", "did not complete"),
    ("no-usage", "no usage"),
])
def test_probe_followup_must_complete_carry_the_marker_and_usage(tmp_path, mode, word):
    with FakeStrata(followup=mode) as fake:
        ev = _probe(fake, tmp_path)
    assert word in ev["checks"]["followup"]["reason"] and not ev["smoke_ok"]


def test_probe_records_unavailable_followup_after_disconnect(tmp_path):
    with FakeStrata(followup_status=503) as fake:
        ev = _probe(fake, tmp_path, abort_timeout=0.5)
    assert not ev["ok"]
    assert ev["checks"]["abort"] == {
        "ok": False, "stream_status": 200, "stream_outcome": "disconnected_after_progress",
        "followup_status": 503, "followup_http_200_after_s": None,
    }


@pytest.mark.parametrize("body", [
    b"not json",
    b'{"error":{"message":"unavailable"}}',
    b"{}",
    b'{"choices":[{"message":{},"finish_reason":"stop"}]}',
    b'{"choices":[{"message":{"role":"user","content":"ok"},"finish_reason":"stop"}]}',
    b'{"choices":[{"message":{"role":"assistant"},"finish_reason":"stop"}]}',
    b'{"choices":[{"message":{"role":"assistant","content":1},"finish_reason":"stop"}]}',
    b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"tool_calls"}]}',
])
def test_probe_rejects_unusable_http_200_after_disconnect(tmp_path, body):
    with FakeStrata(followup_responses=[(200, body)] * 5) as fake:
        ev = _probe(fake, tmp_path, abort_timeout=0.5)
    check = ev["checks"]["abort"]
    assert not check["ok"] and check["followup_status"] == 200
    assert check["followup_http_200_after_s"] is None


@pytest.mark.parametrize("content", [None, ""])
def test_probe_accepts_reasoning_completion_without_text_after_disconnect(tmp_path, content):
    body = json.dumps({"choices": [{
        "message": {"role": "assistant", "content": content, "reasoning_content": "Thinking"},
        "finish_reason": "length",
    }]}).encode()
    with FakeStrata(followup_responses=[(200, body)]) as fake:
        ev = _probe(fake, tmp_path)
    check = ev["checks"]["abort"]
    assert check["ok"] and check["followup_status"] == 200
    assert check["followup_http_200_after_s"] >= 0


def test_probe_clears_http_status_when_last_attempt_loses_connection(tmp_path):
    with FakeStrata(followup_responses=[(503, b"{}")]) as fake:
        ev = _probe(fake, tmp_path, abort_timeout=0.5)
    check = ev["checks"]["abort"]
    assert not check["ok"] and check["followup_status"] is None


_PROGRESS = {"choices": [{"delta": {"content": "one"}, "finish_reason": None}]}
_FINISH = {"choices": [{"delta": {}, "finish_reason": "length"}]}


@pytest.mark.parametrize("body,outcome", [
    (_sse([]), "terminal_event"),
    (_sse([_PROGRESS]), "terminal_event"),
    (_sse([_PROGRESS, _FINISH], done=False), "terminal_event"),
    (_sse([_PROGRESS], done=False), "body_ended"),
    (_sse([{"choices": [{"delta": {"role": "assistant"}}]}], done=False), "body_ended"),
    (b": a comment with data: [DONE]\n\n", "body_ended"),
    (_sse([{"choices": [42]}], done=False), "invalid_event"),
    (_sse([{"error": {"message": "unavailable"}}], done=False), "error_event"),
])
def test_probe_rejects_streams_without_a_point_to_disconnect(body, outcome, tmp_path):
    with FakeStrata(abort_stream=body) as fake:
        ev = _probe(fake, tmp_path)
    assert not ev["ok"]
    assert ev["checks"]["abort"] == {
        "ok": False, "stream_status": 200, "stream_outcome": outcome,
        "followup_status": None, "followup_http_200_after_s": None,
    }
    assert fake.posts == 5  # tool call, followup, disconnect candidate and two count requests


def test_probe_count_fails_on_undercut_and_without_tokenizer(tmp_path):
    with FakeStrata(prompt_tokens=3) as fake:
        ev = _probe(fake, tmp_path)
    assert ev["checks"]["count"]["ok"] is False
    with FakeStrata() as fake:
        ev = strata_attach.probe(_ep(fake), MODEL, "key", abort_timeout=10)
    assert ev["checks"]["count"]["reason"] == "no tokenizer directory given" and not ev["ok"]


# ---------------------------------------------------------------- SSE and the usage tap

def test_parse_sse_assembles_split_arguments_and_the_call_id():
    parsed = strata_attach.parse_sse((FIX / "tool-call.sse").read_text(encoding="utf-8").splitlines())
    assert strata_attach.tool_call_check(parsed, {MODEL}) == (True, "ok")
    assert parsed["tool_calls"][0]["id"] == "call_0" and parsed["done"] is True


def _tap(sse_name):
    rows = []

    class Store:
        def add(self, row):
            rows.append(row)
            return len(rows) - 1

        def replace(self, i, row):
            rows[i] = row

    raw = (FIX / sse_name).read_bytes()
    session = BridgeSession(Store())
    req = b'{"stream":true}'
    session.feed_request(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                         b"Content-Length: %d\r\n\r\n%s" % (len(req), req))
    session.feed_response(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                          + b"%x\r\n" % len(raw) + raw + b"\r\n0\r\n\r\n")
    session.close()
    return rows


def test_usage_tap_reads_the_strata_tool_call_stream():
    rows = _tap("tool-call.sse")
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")} == {
        "prompt_tokens": 412, "completion_tokens": 21, "total_tokens": 433}


def test_usage_tap_marks_a_stream_without_usage_unmeasured_not_zero():
    assert _tap("followup.sse") == [{"poison": 1}]


# ---------------------------------------------------------------- local-build.sh attach

def _env(tmp_path, key="sekret", **extra):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    (data / "results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "models").mkdir(exist_ok=True)
    keyfile = tmp_path / "api.key"
    if key:
        keyfile.write_text(key + "\n", encoding="utf-8")
    env = dict(os.environ)
    for k in ("A770B_CARD_LOCK_PATH", "A770B_SEAT_LOCK_PATH", "A770B_ATTACHED", "A770B_SERVED_ENGINE",
              "A770B_STRATA_AUTH"):
        env.pop(k, None)
    env.update(HOME=str(tmp_path / "home"), A770B_PROJECT=str(ROOT), A770B_DATA=str(data),
               A770B_REFUSE=str(tmp_path / "refused"), A770B_MODELS=str(tmp_path / "models"),
               A770B_ALLOW_NO_NVTOP="1", A770B_GPU_MATCH="DG2", A770B_VRAM_CAP_GIB="15.3",
               A770B_MIN_AVAIL_MB="1", A770B_API_KEY_FILE=str(keyfile), A770B_CARD_MODE="display")
    env.update(extra)
    return env


def _attach(env, url, name, engine="", ctx=""):
    script = (f'. "{LOCAL_BUILD}"; _attach_running_server "{url}" "{name}" "{engine}" "{ctx}" '
              f'&& echo "ENGINE=$A770B_SERVED_ENGINE AUTH=${{A770B_STRATA_AUTH:-}}"')
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, cwd=ROOT)


def test_attach_strata_key_mode(tmp_path):
    with FakeStrata() as fake:
        r = _attach(_env(tmp_path), fake.url, MODEL, "strata", "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ENGINE=strata AUTH=key" in r.stdout and fake.posts == 0


@pytest.mark.parametrize("key", ["", "sekret"])
def test_attach_strata_none_mode_ignores_workstation_key(tmp_path, key):
    with FakeStrata(key="") as fake:
        r = _attach(_env(tmp_path, key=key), fake.url, MODEL, "strata", "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ENGINE=strata AUTH=none" in r.stdout
    assert (tmp_path / "api.key").exists() == bool(key)
    assert fake.models_auth == [None] and fake.posts == 0


@pytest.mark.parametrize("kw,name,ctx,word", [
    ({}, "not-served", "8192", "mismatched"),
    ({"models": "models-unloaded.json"}, MODEL, "8192", "unloaded"),
    ({"health": "health-unloaded.json"}, MODEL, "8192", "unloaded"),
    ({}, "img-gen", "8192", "not a text model"),
    ({}, MODEL, "65536", "window"),
    ({"models_status": 500}, MODEL, "8192", "HTTP 500"),
    ({"key": "other"}, MODEL, "8192", "unauthorized"),
    ({}, MODEL, "", "positive integer"),
])
def test_attach_strata_refusals(tmp_path, kw, name, ctx, word):
    with FakeStrata(**kw) as fake:
        r = _attach(_env(tmp_path), fake.url, name, "strata", ctx)
    assert r.returncode == 2 and word in r.stderr, r.stdout + r.stderr
    assert fake.posts == 0


@pytest.mark.parametrize("host", ["localhost", "[::1]", "10.0.0.5"])
def test_attach_strata_refuses_other_host_spellings_before_any_request(tmp_path, host):
    with FakeStrata() as fake:
        r = _attach(_env(tmp_path), "http://%s:%d/v1" % (host, fake.port), MODEL, "strata", "8192")
    assert r.returncode == 2 and "127.0.0.1" in r.stderr
    assert fake.log == []


def test_attach_strata_keeps_the_key_off_argv(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "argv.log"
    (bin_ / "python3").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{log}"\nexec "{sys.executable}" "$@"\n',
                                  encoding="utf-8")
    (bin_ / "python3").chmod(0o755)
    env = _env(tmp_path, key="sekret-argv-check", PATH=f"{bin_}:{os.environ['PATH']}")
    with FakeStrata(key="sekret-argv-check") as fake:
        r = _attach(env, fake.url, MODEL, "strata", "8192")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sekret-argv-check" not in log.read_text(encoding="utf-8")


def test_attach_refuses_an_unknown_explicit_engine(tmp_path):
    with FakeStrata() as fake:
        r = _attach(_env(tmp_path), fake.url, MODEL, "llama.cpp", "8192")
    assert r.returncode == 2 and "--engine" in r.stderr


def test_attach_without_engine_never_names_strata(tmp_path):
    with FakeStrata() as fake:
        r = _attach(_env(tmp_path), fake.url, MODEL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ENGINE= AUTH=" in r.stdout and "not named" in r.stderr


def test_render_strata_none_uses_the_placeholder_and_applies_the_spec(tmp_path):
    seat = tmp_path / "seat"
    seat.mkdir()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"card": {"text": "STRATA-CARD"}, "scope": {"edit": ["ANSWER.txt"]}}), encoding="utf-8")
    out = tmp_path / "data" / "logs" / "opencode.candidate.jsonc"
    env = _env(tmp_path, key="", A770B_SERVED_ENGINE="strata", A770B_STRATA_AUTH="none", A770B_ALIAS=MODEL,
               A770B_SPEC=str(spec), A770B_RENDER_SEAT=str(seat))
    r = subprocess.run(["bash", "-c", f'. "{ENV_SH}"; a770b_render_profile candidate 8192 "{out}"'],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    text = out.read_text(encoding="utf-8")
    assert '"apiKey": "no-key-for-this-run"' in text and '"local-a770/%s"' % MODEL in text
    assert "STRATA-CARD" in text and '"ANSWER.txt": "allow"' in text
    assert not (tmp_path / "api.key").exists()


def test_run_refuses_engine_without_base_url(tmp_path):
    brief = tmp_path / "b.md"
    brief.write_text("# t\n", encoding="utf-8")
    r = subprocess.run(["bash", str(LOCAL_BUILD), "run", str(brief), "--engine", "strata"],
                       capture_output=True, text=True, env=_env(tmp_path), cwd=ROOT)
    assert r.returncode == 2 and "--engine needs --base-url" in r.stderr


# ---------------------------------------------------------------- kit caller (dry run) and capture

def test_run_suite_forwards_engine_strata_in_the_attach_path(tmp_path):
    env = _env(tmp_path)
    r = subprocess.run(["bash", str(RUN_SUITE_SH), "--base-url", "http://127.0.0.1:9/v1",
                        "--served-model-name", "qwen-candidate", "--engine", "strata", "--ctx", "8192",
                        str(tmp_path / "seat"), "--stages", "s1-frontend", "--dry-run"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--profile candidate --base-url http://127.0.0.1:9/v1 --served-model-name qwen-candidate --engine strata" in r.stdout
    assert "verify <label>" in r.stdout


def test_run_suite_without_strata_is_unchanged(tmp_path):
    env = _env(tmp_path)
    r = subprocess.run(["bash", str(RUN_SUITE_SH), "--base-url", "http://127.0.0.1:9/v1",
                        "--served-model-name", "m", "--ctx", "8192", str(tmp_path / "seat"),
                        "--stages", "s1-frontend", "--dry-run"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--engine" not in r.stdout
    assert "--served-model-name m" in r.stdout
    import profiles

    assert f"instrument: {profiles.kit_instrument()}" in r.stdout


@pytest.mark.parametrize("args,word", [
    (["--engine", "strata", "--model", "/x.gguf", "--ctx", "8192"], "--engine needs --base-url"),
    (["--base-url", "http://127.0.0.1:9/v1", "--served-model-name", "m", "--ctx", "8192",
      "--engine", "vllm"], "--engine"),
])
def test_run_suite_refuses_engine_misuse(tmp_path, args, word):
    r = subprocess.run(["bash", str(RUN_SUITE_SH), *args, "--dry-run"],
                       capture_output=True, text=True, env=_env(tmp_path), cwd=ROOT)
    assert r.returncode == 2 and word in r.stderr, r.stdout + r.stderr


def test_capture_reads_the_usage_file_for_strata_and_names_the_engine(tmp_path):
    env = _env(tmp_path, A770B_ATTACHED="1", A770B_SERVED_ENGINE="strata",
               A770B_DOCKER=str(tmp_path / "no-docker"))
    seat = tmp_path / "seat"
    seat.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.name", "t"], ["git", "config", "user.email", "t@t"]):
        subprocess.run(cmd, cwd=seat, check=True)
    (seat / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=seat, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=seat, check=True)
    log = tmp_path / "build.log"
    log.write_text("1 passed\n", encoding="utf-8")
    data = Path(env["A770B_DATA"])
    (data / "logs" / "usage.json").write_text(
        json.dumps([{"prompt_tokens": 412, "completion_tokens": 21, "total_tokens": 433}]), encoding="utf-8")
    r = subprocess.run(["bash", str(CAPTURE_SH), "strata-cap", str(seat), str(log)],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (data / "results" / "strata-cap.task.md").read_text(encoding="utf-8")
    assert "requests=1 prompt_tokens_total=412 gen_tokens_total=21" in text
    assert "engine: strata (attached)" in text


def test_sandbox_bridges_strata_through_the_usage_tap():
    text = (ROOT / "harness" / "sandbox_run.sh").read_text(encoding="utf-8")
    assert "sglang|vllm|strata)" in text


def test_strata_is_an_admitted_registry_engine():
    """Registry rows wait for a live ladder; the attach identity does not widen the schema."""
    from profiles import ENGINE_VALUES
    assert "strata" in ENGINE_VALUES
