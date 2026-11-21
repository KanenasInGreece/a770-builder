#!/usr/bin/env python3
"""strata_attach.py — the attach contract for one externally managed Strata endpoint. Stdlib only.

Strata is an explicit identity: a caller names it (`--engine strata`), and nothing here classifies a
server from what it reports. This slice attaches; it never starts, stops or signals a server, and the
vLLM, SGLang and llama.cpp attach paths do not use this file.

The contract is Strata v0.2.16 (terminal/serve/server.py, the /health and /v1/models routes):
  /health     {"service": "strata", "loaded": bool, "model": str, "max_context": int}
  /v1/models  {"data": [row, ...]}: a row has "id", a nested "status": {"value": ...}, "meta": {"n_ctx": int},
              an alias row "alias_of": <id>, and "architecture": {"input_modalities": [...],
              "output_modalities": [...]}. Text and image rows share the listing; a row whose
              architecture.output_modalities does not name "text" is not a text model (an image row
              names ["image"] and may carry no meta).
Attach needs a successful HTTP status on both, a loaded Strata, a requested id that is one loaded text row
(itself or through alias_of) and is the model /health has loaded, and a caller window no larger than the
usable context (the smaller of max_context and meta.n_ctx). Anything else is a refusal.

  attach --base-url URL --model NAME [--ctx N] [--auth auto|key|none]
        prints "ok <auth> <usable>" and exits 0, or one refusal line and exits 1.
  probe  --base-url URL --model NAME --tokenizer DIR --checkpoint REF [--auth auto|key|none]
         [--count-slack N] [--abort-timeout S] [--evidence F]
        the optional tool smoke against the live endpoint; JSON evidence, exit 1 when any check fails.

auto tries /v1/models without a key first. A 401 or 403 permits a retry with A770B_API_KEY_FILE, which is never
created. Transport uses http.client to 127.0.0.1 only, so no environment proxy or curl configuration applies.
The key never goes on argv or into evidence. A missing measurement is null, never zero.
"""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_budget  # noqa: E402  the local tokenizer path, and its one uv re-exec
from usage_tap import record_from_body  # noqa: E402  the usage integers the tap records

HOST = "127.0.0.1"
CHAT = "/v1/chat/completions"
MAX_JSON = 1 << 20      # one /health, /v1/models or non-streamed answer
MAX_STREAM = 4 << 20    # one streamed answer
GET_TIMEOUT = 10
POST_TIMEOUT = 120
FINISH_WORDS = ("stop", "tool_calls", "length", "content_filter")

NOTE = "NOTE.txt"
TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file in the working tree and return its text.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path"}},
            "required": ["path"],
        },
    },
}
TOOL_PROMPT = ("Call read_file once with path %s. After the tool result, reply with the code it holds "
               "and nothing else." % NOTE)
TOOL_RESULT = "%s holds one line: the code %%s" % NOTE
COUNT_TEXT = ("The quick brown fox jumps over the lazy dog. "
              "Pack my box with five dozen liquor jugs. ") * 8
TOOLS_TEXT = json.dumps([TOOL])


def _say(value, limit=60):
    """A server-chosen value, quoted and cut, for a refusal line."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def shape(value):
    """The JSON shape: keys kept, leaves replaced by their type name. No returned text survives."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


# ---------------------------------------------------------------- the pinned contract

def check_health(status, doc):
    """(None, health) for the pinned Strata /health with a model loaded, else (reason, None)."""
    if not isinstance(status, int) or not 200 <= status < 300:
        return "unavailable: /health answered HTTP %s" % status, None
    if not isinstance(doc, dict):
        return "unavailable: /health is not a JSON object", None
    if doc.get("service") != "strata":
        return "mismatched: /health service is %s, not 'strata'" % _say(doc.get("service")), None
    if doc.get("loaded") is not True:
        return "unloaded: /health says no model is loaded", None
    if not isinstance(doc.get("model"), str) or not doc["model"]:
        return "unavailable: /health names no loaded model", None
    if not _positive_int(doc.get("max_context")):
        return "unavailable: /health max_context is not a positive integer", None
    return None, {"model": doc["model"], "max_context": doc["max_context"]}


def _meta(row):
    meta = row.get("meta")
    return meta if isinstance(meta, dict) else {}


def _is_text(row):
    """A text model names "text" in architecture.output_modalities; an image model names ["image"]."""
    arch = row.get("architecture")
    mods = arch.get("output_modalities") if isinstance(arch, dict) else None
    return isinstance(mods, list) and "text" in mods


def _loaded(row):
    status = row.get("status")
    return isinstance(status, dict) and status.get("value") == "loaded"


def resolve_model(doc, name):
    """(None, model) for the one loaded text row `name` names, directly or through alias_of; else (reason, None)."""
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        return "unavailable: /v1/models listed no models", None
    rows = [r for r in rows if isinstance(r, dict)]

    def by_id(ident):
        return [r for r in rows if r.get("id") == ident]

    hit = by_id(name)
    if not hit:
        return "mismatched: %s is not listed" % _say(name), None
    if len(hit) > 1:
        return "mismatched: %s is listed %d times" % (_say(name), len(hit)), None
    target = hit[0]
    alias = target.get("alias_of")
    if alias is not None:
        found = by_id(alias) if isinstance(alias, str) and alias else []
        if len(found) != 1 or found[0].get("alias_of") is not None:
            return "mismatched: %s is an alias of %s, which is not one listed model" % (_say(name), _say(alias)), None
        target = found[0]
    canonical = target.get("id")
    if not _is_text(target):
        return "mismatched: %s is not a text model" % _say(canonical), None
    if not _loaded(target):
        return "unloaded: %s is listed but its status.value is not 'loaded'" % _say(canonical), None
    n_ctx = _meta(target).get("n_ctx")
    if not _positive_int(n_ctx):
        return "unavailable: %s has no positive meta.n_ctx" % _say(canonical), None
    return None, {"id": canonical, "alias": alias is not None, "n_ctx": n_ctx}


# ---------------------------------------------------------------- transport

def read_key():
    """The first line of A770B_API_KEY_FILE, or empty. Never creates the file."""
    path = os.environ.get("A770B_API_KEY_FILE") or ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().rstrip("\r\n")
    except (OSError, UnicodeError):
        return ""


class Endpoint:
    """HTTP to one 127.0.0.1 port in one verified auth mode. http.client reads no proxy setting."""

    def __init__(self, base_url, key=None):
        raw = base_url[:-1] if base_url.endswith("/") else base_url
        u = urlsplit(raw)
        try:
            port = u.port
        except ValueError:
            port = None
        if (u.scheme != "http" or u.username is not None or u.password is not None or u.query or u.fragment
                or u.path not in ("", "/v1") or u.hostname != HOST or port is None or u.netloc != "%s:%d" % (HOST, port)):
            raise ValueError("Strata attach accepts only http://127.0.0.1:<port>[/v1]")
        self.port = port
        self.key = key
        self.posts = 0

    def _headers(self, auth, has_body):
        headers = {"Accept": "application/json, text/event-stream"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if auth and self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def request(self, method, path, payload=None, auth=True, timeout=GET_TIMEOUT, limit=MAX_JSON):
        """(status, content type, body). A body over `limit` is an error, never a cut answer."""
        body = None if payload is None else json.dumps(payload).encode()
        if method == "POST":
            self.posts += 1
        conn = http.client.HTTPConnection(HOST, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=self._headers(auth, body is not None))
            resp = conn.getresponse()
            data = resp.read(limit + 1)
            if len(data) > limit:
                raise ValueError("answer over %d bytes" % limit)
            return resp.status, resp.getheader("Content-Type", ""), data
        finally:
            conn.close()

    def get_json(self, path, auth=True):
        """(status, document or None, refusal or None)."""
        try:
            status, _ctype, data = self.request("GET", path, auth=auth)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            return None, None, "unavailable: GET %s failed (%s)" % (path, type(exc).__name__)
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            doc = None
        return status, doc, None

    def abort_one(self, payload, timeout):
        """Close the response after observing generation progress. Return (outcome, HTTP status)."""
        self.posts += 1
        status = None
        conn = http.client.HTTPConnection(HOST, self.port, timeout=timeout)
        try:
            headers = self._headers(auth=True, has_body=True)
            headers.update({"Accept": "text/event-stream", "Connection": "close"})
            conn.request("POST", CHAT, body=json.dumps(payload).encode(), headers=headers)
            with conn.getresponse() as resp:
                status = resp.status
                if status != 200:
                    return "http_error", status
                if resp.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                    return "not_event_stream", status
                pending = b""
                remaining = 65536
                while remaining:
                    chunk = resp.read1(min(4096, remaining))
                    if not chunk:
                        return "body_ended", status
                    remaining -= len(chunk)
                    *events, pending = (pending + chunk).replace(b"\r\n", b"\n").split(b"\n\n")
                    progressed = False
                    # Inspect every complete event already read before deciding to disconnect.
                    for event in events:
                        data = b"\n".join(line[5:].lstrip(b" ") for line in event.split(b"\n")
                                          if line.startswith(b"data:"))
                        if data == b"[DONE]":
                            return "terminal_event", status
                        if not data:
                            continue
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            return "invalid_event", status
                        if not isinstance(obj, dict):
                            return "invalid_event", status
                        if obj.get("error") is not None:
                            return "error_event", status
                        choices = obj.get("choices", [])
                        if not isinstance(choices, list):
                            return "invalid_event", status
                        for choice in choices:
                            if not isinstance(choice, dict):
                                return "invalid_event", status
                            if choice.get("finish_reason") is not None:
                                return "terminal_event", status
                            delta = choice.get("delta", {})
                            if not isinstance(delta, dict):
                                return "invalid_event", status
                            for key in ("content", "reasoning_content"):
                                value = delta.get(key)
                                if isinstance(value, str) and value:
                                    progressed = True
                    if resp.isclosed():
                        return "body_ended", status
                    if progressed:
                        return "disconnected_after_progress", status
                return "read_limit", status
        except (OSError, http.client.HTTPException):
            return "read_failed", status
        finally:
            conn.close()


def attach_check(ep, name, ctx=None, mode="none"):
    """{"ok", "reason", ...}: health, then the model row, then the window. GETs only."""
    def refuse(reason):
        return {"ok": False, "reason": reason}

    if ctx is not None and not _positive_int(ctx):
        return refuse("window: --ctx must be a positive integer")
    status, doc, err = ep.get_json("/health", auth=False)  # the key goes only to a confirmed Strata
    if err:
        return refuse(err)
    reason, health = check_health(status, doc)
    if reason:
        return refuse(reason)
    health_shape = shape(doc)
    without_key, doc, err = ep.get_json("/v1/models", auth=False)
    if err:
        return refuse(err)
    status = without_key
    verified = "none"
    if without_key in (401, 403) and mode != "none":
        if ep.key is None:
            ep.key = read_key()
        if not ep.key:
            return refuse("unauthorized: /v1/models requires a key in A770B_API_KEY_FILE")
        status, doc, err = ep.get_json("/v1/models")
        if err:
            return refuse(err)
        verified = "key"
    if status in (401, 403):
        return refuse("unauthorized: /v1/models answered HTTP %d in auth mode %s" % (status, mode))
    if not isinstance(status, int) or not 200 <= status < 300:
        return refuse("unavailable: /v1/models answered HTTP %s" % status)
    if mode == "key" and verified == "none":
        return refuse("auth: key mode requires /v1/models to refuse requests without a key")
    reason, model = resolve_model(doc, name)
    if reason:
        return refuse(reason)
    if health["model"] not in (name, model["id"]):
        return refuse("mismatched: /health has %s loaded, not %s" % (_say(health["model"]), _say(name)))
    usable = min(health["max_context"], model["n_ctx"])
    if ctx is not None and ctx > usable:
        return refuse("window: --ctx %d exceeds the usable context %d (max_context %d, meta.n_ctx %d)"
                      % (ctx, usable, health["max_context"], model["n_ctx"]))
    if verified == "none":
        ep.key = None
    return {"ok": True, "reason": "ok", "usable": usable, "model": model["id"], "alias": model["alias"],
            "auth": verified, "without_key": without_key, "shapes": {"health": health_shape, "models": shape(doc)}}


# ---------------------------------------------------------------- the tool smoke

def parse_sse(lines):
    """One streamed chat completion: tool calls, text, finish reason, [DONE], the model names the chunks carry."""
    calls = {}
    text = []
    finish = None
    done = False
    models = set()
    first = None
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if first is None:
            first = event
        if isinstance(event.get("model"), str):
            models.add(event["model"])
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                index = tc.get("index", 0)
                index = index if isinstance(index, int) and not isinstance(index, bool) else 0
                slot = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                if isinstance(tc.get("id"), str) and tc["id"]:
                    slot["id"] = tc["id"]
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if isinstance(fn.get("name"), str):
                    slot["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["arguments"] += fn["arguments"]
    tool_calls = []
    for index in sorted(calls):
        slot = calls[index]
        try:
            args = json.loads(slot["arguments"]) if slot["arguments"] else None
        except ValueError:
            args = None
        tool_calls.append({"id": slot["id"], "name": slot["name"], "arguments_raw": slot["arguments"],
                           "arguments": args})
    return {"tool_calls": tool_calls, "content": "".join(text), "finish_reason": finish, "done": done,
            "models": models, "chunk_shape": shape(first) if first is not None else None}


def measured_usage(body, ctype):
    """The answer's usage integers, or None when prompt or completion tokens are missing."""
    rec = record_from_body(body, ctype)
    if "prompt_tokens" not in rec or "completion_tokens" not in rec:
        return None
    return {k: rec.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _finish_word(value):
    return value if value in FINISH_WORDS else (None if value is None else "other")


def tool_call_check(parsed, names):
    """(ok, reason). Reasons are fixed words: no returned text or arguments are kept."""
    calls = parsed["tool_calls"]
    if not parsed["done"]:
        return False, "the stream did not complete"
    if len(calls) != 1:
        return False, "expected one tool call, got %d" % len(calls)
    call = calls[0]
    if not isinstance(call["id"], str) or not call["id"].strip():
        return False, "the tool call carries no id"
    if call["name"] != "read_file":
        return False, "the tool call is not read_file"
    if call["arguments"] != {"path": NOTE}:
        return False, "the tool arguments are not the requested path"
    if parsed["finish_reason"] != "tool_calls":
        return False, "finish_reason is not tool_calls"
    if not parsed["models"] <= names:
        return False, "a chunk names another model"
    return True, "ok"


def followup_check(parsed, marker, names):
    if not parsed["done"]:
        return False, "the stream did not complete"
    if parsed["tool_calls"]:
        return False, "the answer called a tool again"
    if parsed["finish_reason"] != "stop":
        return False, "finish_reason is not stop"
    if marker not in parsed["content"]:
        return False, "the answer does not carry the tool result's marker"
    if not parsed["models"] <= names:
        return False, "a chunk names another model"
    return True, "ok"


def _stream(ep, payload):
    """(parsed, usage, refusal) for one streamed POST."""
    try:
        status, ctype, body = ep.request("POST", CHAT, payload, timeout=POST_TIMEOUT, limit=MAX_STREAM)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        return None, None, "the request failed (%s)" % type(exc).__name__
    if status != 200:
        return None, None, "HTTP %s" % status
    return parse_sse(body.decode("utf-8", "replace").splitlines()), measured_usage(body, ctype), None


def _count_post(ep, model, tools):
    payload = {"model": model, "messages": [{"role": "user", "content": COUNT_TEXT}], "max_tokens": 1,
               "temperature": 0}
    if tools:
        payload.update(tools=[TOOL], tool_choice="auto")
    try:
        status, ctype, body = ep.request("POST", CHAT, payload, timeout=POST_TIMEOUT)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    return record_from_body(body, ctype).get("prompt_tokens") if status == 200 else None


def _local_count(text, tokenizer):
    return prompt_budget.count_tokens("", "", text, tokenize="openai", tokenizer=tokenizer)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _completion_body_ok(body):
    try:
        doc = json.loads(body)
    except ValueError:
        return False
    if not isinstance(doc, dict) or doc.get("error") is not None:
        return False
    choices = doc.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    message = choice.get("message")
    return (choice.get("finish_reason") in ("stop", "length")
            and isinstance(message, dict) and message.get("role") == "assistant"
            and "content" in message and isinstance(message["content"], (str, type(None))))


def probe(ep, model, mode, tokenizer="", checkpoint="", count_slack=64, abort_timeout=30.0,
          local_count=_local_count, marker=None):
    checks = {}
    usage = {}
    ev = {"engine": "strata", "attach": "external", "host": HOST, "port": ep.port, "model": model, "auth": None,
          "checks": checks, "usage": usage, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def finish():
        ev["posts"] = ep.posts
        ev["smoke_ok"] = bool(checks.get("tool_call", {}).get("ok") and checks.get("followup", {}).get("ok"))
        ev["ok"] = all(c["ok"] for c in checks.values())
        return ev

    # 1. The local count comes first: the tokenizer's one uv re-exec replays this process before any traffic.
    local = None
    count = {"ok": False, "slack": count_slack, "tokenizer": None, "local": None, "api": None}
    if not tokenizer:
        count["reason"] = "no tokenizer directory given"
    else:
        try:
            tj = prompt_budget._tokenizer_json(tokenizer)  # noqa: SLF001 — the same resolution count_tokens uses
            count["tokenizer"] = {"checkpoint": checkpoint or None, "tokenizer_json_sha256": _sha256(tj)}
            local = {"bare": local_count(COUNT_TEXT, tokenizer), "tool_json": local_count(TOOLS_TEXT, tokenizer)}
            count["local"] = local
        except (ValueError, OSError) as exc:
            count["reason"] = "local tokenizer: %s" % exc
    checks["count"] = count

    # 2. Readiness, identity and auth. A refusal returns here, before the first POST.
    verdict = attach_check(ep, model, None, mode)
    checks["attach"] = {"ok": verdict["ok"], "reason": verdict["reason"]}
    ev["shapes"] = verdict.get("shapes", {})
    if not verdict["ok"]:
        return finish()
    ev["auth"] = verdict["auth"]
    checks["auth"] = {"ok": True, "mode": verdict["auth"], "without_key": verdict["without_key"]}
    names = {model, verdict["model"]}

    # 3. The tool call, then the answer to its result. The marker is new per probe.
    marker = marker or secrets.token_hex(8)
    base = {"model": model, "stream": True, "stream_options": {"include_usage": True}, "temperature": 0,
            "max_tokens": 256}
    msgs = [{"role": "user", "content": TOOL_PROMPT}]
    parsed, used, err = _stream(ep, dict(base, messages=msgs, tools=[TOOL], tool_choice="auto"))
    if err:
        checks["tool_call"] = {"ok": False, "reason": err}
    else:
        ok, why = tool_call_check(parsed, names)
        if ok and used is None:
            ok, why = False, "the stream carried no usage"
        checks["tool_call"] = {"ok": ok, "reason": why, "done": parsed["done"],
                               "finish_reason": _finish_word(parsed["finish_reason"]),
                               "id_present": bool(parsed["tool_calls"] and parsed["tool_calls"][0]["id"])}
        ev["shapes"]["tool_call_chunk"] = parsed["chunk_shape"]
    usage["tool_call"] = used
    if checks["tool_call"]["ok"]:
        call = parsed["tool_calls"][0]
        follow = msgs + [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments_raw"]}}]},
            {"role": "tool", "tool_call_id": call["id"], "content": TOOL_RESULT % marker},
        ]
        fparsed, fused, err = _stream(ep, dict(base, messages=follow, tools=[TOOL]))
        if err:
            checks["followup"] = {"ok": False, "reason": err}
        else:
            ok, why = followup_check(fparsed, marker, names)
            if ok and fused is None:
                ok, why = False, "the stream carried no usage"
            checks["followup"] = {"ok": ok, "reason": why, "done": fparsed["done"],
                                  "finish_reason": _finish_word(fparsed["finish_reason"]),
                                  "marker_found": marker in fparsed["content"],
                                  "content_chars": len(fparsed["content"])}
        usage["followup"] = fused
    else:
        checks["followup"] = {"ok": False, "reason": "no tool call to answer"}
        usage["followup"] = None

    # 4. Observe stream progress, disconnect, then measure availability with a fresh request.
    outcome, stream_status = ep.abort_one(
        dict(base, messages=[{"role": "user", "content": "Count to one thousand."}], max_tokens=4096),
        timeout=min(60.0, POST_TIMEOUT))
    followup_status = None
    followup_after = None
    if outcome == "disconnected_after_progress":
        t0 = time.monotonic()
        deadline = t0 + abort_timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                followup_status, _ctype, body = ep.request(
                    "POST", CHAT, {"model": model, "max_tokens": 4, "temperature": 0,
                                   "messages": [{"role": "user", "content": "Say ok."}]}, timeout=remaining)
            except (OSError, http.client.HTTPException, ValueError):
                followup_status = None
            else:
                if followup_status == 200 and _completion_body_ok(body):
                    followup_after = round(time.monotonic() - t0, 3)
                    break
            time.sleep(max(0, min(0.2, deadline - time.monotonic())))
    checks["abort"] = {"ok": followup_after is not None, "stream_status": stream_status,
                       "stream_outcome": outcome, "followup_status": followup_status,
                       "followup_http_200_after_s": followup_after}

    # 5. API prompt tokens against the local count, bare and with the tool definitions.
    if local is not None:
        api = {"bare": _count_post(ep, model, tools=False), "tools": _count_post(ep, model, tools=True)}
        count["api"] = api
        if api["bare"] is None or api["tools"] is None:
            count["reason"] = "the API reported no prompt_tokens"
        elif not 0 <= api["bare"] - local["bare"] <= count_slack:
            count["reason"] = "bare prompt: API %d against local %d is outside 0..%d" % (
                api["bare"], local["bare"], count_slack)
        elif api["tools"] - api["bare"] < 1:
            count["reason"] = "the tool definitions added no prompt tokens"
        else:
            count["ok"], count["reason"] = True, "ok"
    return finish()


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="strata_attach.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("attach", "probe"):
        p = sub.add_parser(name)
        p.add_argument("--base-url", required=True)
        p.add_argument("--model", required=True)
        p.add_argument("--auth", choices=("auto", "key", "none"), default="auto")
        if name == "attach":
            p.add_argument("--ctx", type=int)
        else:
            p.add_argument("--tokenizer", required=True)
            p.add_argument("--checkpoint", required=True, help="the checkpoint the tokenizer belongs to, e.g. repo@revision")
            p.add_argument("--count-slack", type=int, default=64)
            p.add_argument("--abort-timeout", type=float, default=30.0)
            p.add_argument("--evidence")
    args = ap.parse_args(argv)

    try:
        ep = Endpoint(args.base_url)
    except ValueError as exc:
        print(exc)
        return 1
    if args.cmd == "attach":
        verdict = attach_check(ep, args.model, args.ctx, args.auth)
        print("ok %s %d" % (verdict["auth"], verdict["usable"]) if verdict["ok"] else verdict["reason"])
        return 0 if verdict["ok"] else 1
    ev = probe(ep, args.model, args.auth, args.tokenizer, args.checkpoint, args.count_slack, args.abort_timeout)
    text = json.dumps(ev, indent=2, sort_keys=True)
    if args.evidence:
        with open(args.evidence, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if ev["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
