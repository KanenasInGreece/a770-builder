"""Tests for harness/usage_tap.py. No live model server.

The socket check stands up its own upstream. A secret in the request and in
the response text must not appear in the usage file.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "harness"))

import usage_tap  # noqa: E402
from usage_tap import BridgeSession, UsageStore, handle_connection, record_from_body, serve  # noqa: E402


def request(path, body, auth="Bearer supersecret"):
    raw = body.encode("utf-8")
    head = (
        "POST {0} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Authorization: {1}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {2}\r\n"
        "Connection: keep-alive\r\n\r\n"
    ).format(path, auth, len(raw))
    return head.encode("ascii") + raw


def response(body, content_type="application/json", status="200 OK"):
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    head = (
        "HTTP/1.1 {0}\r\n"
        "Content-Type: {1}\r\n"
        "Content-Length: {2}\r\n"
        "Connection: keep-alive\r\n\r\n"
    ).format(status, content_type, len(raw))
    return head.encode("ascii") + raw


def chunked_response(body, content_type="text/event-stream"):
    raw = body.encode("utf-8")
    framed = "{0:x}\r\n".format(len(raw)).encode("ascii") + raw + b"\r\n0\r\n\r\n"
    head = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: {0}\r\n"
        "Transfer-Encoding: chunked\r\n\r\n"
    ).format(content_type)
    return head.encode("ascii") + framed


class Memory:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)
        return len(self.rows) - 1

    def replace(self, index, row):
        self.rows[index] = row


class RecordShape(unittest.TestCase):
    def test_a_json_body_keeps_integers_and_cached_tokens(self):
        body = json.dumps({
            "choices": [{"message": {"content": "prompt_tokens 999"}}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 2,
                "total_tokens": 9,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        })
        self.assertEqual(record_from_body(body.encode("utf-8"), "application/json"), {
            "prompt_tokens": 7,
            "completion_tokens": 2,
            "total_tokens": 9,
            "cached_tokens": 4,
            "reasoning_tokens": 1,
        })

    def test_a_stream_keeps_the_last_usage_and_an_omitted_one_is_empty(self):
        first = 'data: {"choices":[{"delta":{"content":"prompt_tokens 999"}}]}\n\n'
        early = 'data: {"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
        second = 'data: {"usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14}}\n\n'
        done = "data: [DONE]\n\n"
        found = record_from_body((first + early + second + done).encode("utf-8"), "text/event-stream")
        self.assertEqual(found["prompt_tokens"], 11)
        self.assertEqual(found["completion_tokens"], 3)
        empty = record_from_body(first.encode("utf-8"), "text/event-stream")
        self.assertEqual(empty, {})

    def test_a_boolean_is_not_a_count(self):
        body = json.dumps({"usage": {"prompt_tokens": True, "completion_tokens": 1}})
        self.assertEqual(
            record_from_body(body.encode("utf-8"), "application/json"),
            {"completion_tokens": 1})


class SessionRecords(unittest.TestCase):
    def test_two_keep_alive_completions_and_a_models_call_is_not_one(self):
        store = Memory()
        session = BridgeSession(store)
        session.feed_request(request("/v1/models", "{}"))
        session.feed_response(response('{"data":[{"id":"local-builder"}]}'))
        one = {
            "choices": [{"message": {"content": "SECRET-ANSWER"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
        }
        two = {
            "choices": [{"message": {"content": "again"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11,
                      "reasoning_tokens": 0},
        }
        session.feed_request(request("/v1/chat/completions", json.dumps({"messages": "SECRET-PROMPT"})))
        session.feed_response(response(json.dumps(one)))
        session.feed_request(request("/v1/chat/completions", "{}"))
        session.feed_response(response(json.dumps(two)))
        session.close()
        self.assertEqual(store.rows, [
            {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
            {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11,
             "reasoning_tokens": 0},
        ])

    def test_a_chunked_stream_without_usage_is_an_empty_record(self):
        store = Memory()
        session = BridgeSession(store)
        body = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        session.feed_request(request("/v1/chat/completions", "{}"))
        session.feed_response(chunked_response(body))
        session.close()
        self.assertEqual(store.rows, [{}])

    def test_a_chat_request_with_no_response_is_an_empty_record(self):
        store = Memory()
        session = BridgeSession(store)
        session.feed_request(request("/v1/chat/completions", "{}"))
        self.assertEqual(store.rows, [{}])

    def test_a_cut_off_response_is_an_empty_record(self):
        store = Memory()
        session = BridgeSession(store)
        session.feed_request(request("/v1/chat/completions", "{}"))
        session.feed_response(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello")
        session.close()
        self.assertEqual(store.rows, [{}])

    def test_a_no_body_status_does_not_swallow_the_next_response(self):
        store = Memory()
        session = BridgeSession(store)
        session.feed_request(request("/v1/models", "{}"))
        session.feed_response(b"HTTP/1.1 204 No Content\r\nConnection: keep-alive\r\n\r\n")
        body = json.dumps({
            "choices": [],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        })
        session.feed_request(request("/v1/chat/completions", "{}"))
        session.feed_response(response(body))
        session.close()
        self.assertEqual(store.rows, [{
            "prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5,
        }])

    def test_a_response_before_its_request_is_not_paired_later(self):
        store = Memory()
        session = BridgeSession(store)
        session.wait = 0
        body = json.dumps({
            "choices": [],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        })
        session.feed_response(response(body))
        session.feed_request(request("/v1/chat/completions", "{}"))
        session.feed_response(response(body))
        session.close()
        self.assertEqual(store.rows, [{}])

class SocketBridge(unittest.TestCase):
    def test_the_client_gets_the_bytes_and_the_file_keeps_integers_only(self):
        secret = "SECRET-PROMPT"
        answer = "SECRET-ANSWER"
        token = "supersecret"
        payload = json.dumps({
            "model": "local-builder",
            "messages": [{"role": "user", "content": secret}],
        })
        usage = {"prompt_tokens": 13, "completion_tokens": 4, "total_tokens": 17, "reasoning_tokens": 0}
        reply_obj = {
            "choices": [{"message": {"content": answer}}],
            "usage": usage,
        }
        reply = json.dumps(reply_obj).encode("utf-8")
        hold = threading.Event()
        ready = []

        def upstream():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            ready.append(server.getsockname())
            conn, _addr = server.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            length = 0
            for line in data.split(b"\r\n\r\n", 1)[0].split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            body = data.split(b"\r\n\r\n", 1)[1]
            while len(body) < length:
                body += conn.recv(4096)
            head = (
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                "Content-Length: {0}\r\nConnection: close\r\n\r\n"
            ).format(len(reply)).encode("ascii")
            conn.sendall(head + reply[:8])
            hold.wait(timeout=5)
            conn.sendall(reply[8:])
            conn.close()
            server.close()

        threading.Thread(target=upstream, daemon=True).start()
        for _ in range(50):
            if ready:
                break
            threading.Event().wait(0.05)
        self.assertTrue(ready)
        tmp = tempfile.TemporaryDirectory(prefix=".a770b-usage-tap-")
        self.addCleanup(tmp.cleanup)
        sock_path = os.path.join(tmp.name, "bridge.sock")
        out_path = os.path.join(tmp.name, "usage.json")
        stop = threading.Event()
        threading.Thread(
            target=serve, args=(sock_path, ready[0], out_path, stop), daemon=True).start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            threading.Event().wait(0.05)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(sock_path)
        client.sendall(request("/v1/chat/completions", payload, auth="Bearer " + token))
        got = b""
        client.settimeout(2)
        while reply[:8] not in got:
            got += client.recv(4096)
        self.assertIn(reply[:8], got)
        hold.set()
        while len(got) < len(reply) + got.find(reply[:8]) + 0:
            # Read until the JSON body has arrived. The headers precede it.
            chunk = client.recv(4096)
            if not chunk:
                break
            got += chunk
            if reply in got:
                break
        client.close()
        text = ""
        for _ in range(50):
            if os.path.exists(out_path):
                with open(out_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
                if "prompt_tokens" in text:
                    break
            threading.Event().wait(0.05)
        stop.set()
        self.assertIn(reply, got)
        self.assertNotIn(secret, text)
        self.assertNotIn(answer, text)
        self.assertNotIn(token, text)
        self.assertNotIn("Authorization", text)
        rows = json.loads(text)
        self.assertEqual(rows, [usage])

    def test_a_failing_usage_write_still_forwards_the_body(self):
        class Boom(Memory):
            def add(self, row):
                raise OSError("full")

            def replace(self, index, row):
                raise OSError("full")

        reply = json.dumps({
            "choices": [{"message": {"content": "SECRET-ANSWER"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }).encode("utf-8")
        ready = []

        def upstream():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            ready.append(server.getsockname())
            conn, _addr = server.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            conn.sendall((
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                "Content-Length: {0}\r\nConnection: close\r\n\r\n"
            ).format(len(reply)).encode("ascii") + reply)
            conn.close()
            server.close()

        threading.Thread(target=upstream, daemon=True).start()
        for _ in range(50):
            if ready:
                break
            threading.Event().wait(0.05)
        self.assertTrue(ready)
        left, right = socket.socketpair()
        threading.Thread(
            target=handle_connection, args=(right, ready[0], Boom()), daemon=True).start()
        left.sendall(request("/v1/chat/completions", "{}"))
        left.settimeout(2)
        got = b""
        while reply not in got:
            chunk = left.recv(4096)
            if not chunk:
                break
            got += chunk
        left.close()
        self.assertIn(reply, got)

    def test_a_failed_response_write_still_forwards_the_next_response(self):
        class ReplaceBoom(Memory):
            def replace(self, index, row):
                raise OSError("full")

        def body_for(n):
            return json.dumps({
                "choices": [{"message": {"content": "part-{0}".format(n)}}],
                "usage": {"prompt_tokens": n, "completion_tokens": 1, "total_tokens": n + 1},
            }).encode("utf-8")

        first = body_for(1)
        second = body_for(2)
        ready = []
        # The second response is withheld until the client has the first body.
        # Otherwise both can share the read that is forwarded before replace runs.
        seen_first = threading.Event()

        def read_one(conn, pending):
            # Both requests can arrive in one recv. Bytes after this request stay for the next.
            while b"\r\n\r\n" not in pending:
                chunk = conn.recv(4096)
                if not chunk:
                    return None, pending
                pending += chunk
            head, _, rest = pending.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            while len(rest) < length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                rest += chunk
            return rest[length:], rest[:length]

        def upstream():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            ready.append(server.getsockname())
            conn, _addr = server.accept()
            try:
                pending = b""
                sent_first = False
                for body in (first, second):
                    pending, _got = read_one(conn, pending)
                    if pending is None:
                        return
                    if sent_first and not seen_first.wait(timeout=2):
                        return
                    conn.sendall((
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        "Content-Length: {0}\r\nConnection: keep-alive\r\n\r\n"
                    ).format(len(body)).encode("ascii") + body)
                    sent_first = True
            finally:
                conn.close()
                server.close()

        threading.Thread(target=upstream, daemon=True).start()
        for _ in range(50):
            if ready:
                break
            threading.Event().wait(0.05)
        self.assertTrue(ready)
        left, right = socket.socketpair()
        try:
            threading.Thread(
                target=handle_connection, args=(right, ready[0], ReplaceBoom()), daemon=True).start()
            left.sendall(request("/v1/chat/completions", "{}"))
            left.sendall(request("/v1/chat/completions", "{}"))
            left.settimeout(3)
            got = b""
            while second not in got:
                chunk = left.recv(4096)
                if not chunk:
                    break
                got += chunk
                if first in got:
                    seen_first.set()
        finally:
            left.close()
        self.assertIn(first, got)
        self.assertIn(second, got)

    def test_the_empty_record_is_written_before_the_server_has_the_request(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "usage.json")
        store = UsageStore(path)
        ready = []
        seen = {}

        def upstream():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            ready.append(server.getsockname())
            conn, _addr = server.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            while len(rest) < length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                rest += chunk
            with open(path, "r", encoding="utf-8") as handle:
                seen["when_request_arrived"] = handle.read()
            body = b'{"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
            conn.sendall((
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                "Content-Length: {0}\r\nConnection: close\r\n\r\n"
            ).format(len(body)).encode("ascii") + body)
            conn.close()
            server.close()

        threading.Thread(target=upstream, daemon=True).start()
        for _ in range(50):
            if ready:
                break
            threading.Event().wait(0.05)
        self.assertTrue(ready)
        left, right = socket.socketpair()
        threading.Thread(
            target=handle_connection, args=(right, ready[0], store), daemon=True).start()
        left.sendall(request("/v1/chat/completions", "{}"))
        left.settimeout(2)
        got = b""
        while b'"prompt_tokens"' not in got:
            chunk = left.recv(4096)
            if not chunk:
                break
            got += chunk
        left.close()
        self.assertEqual(json.loads(seen["when_request_arrived"]), [{}])
        self.assertIn(b'"prompt_tokens"', got)

    def test_a_poisoned_session_does_not_wait_on_the_next_response(self):
        session = BridgeSession(Memory())
        session.wait = 30
        session._poison()
        started = time.monotonic()
        session._on_response(
            "HTTP/1.1 200 OK",
            {"content-type": "application/json"},
            b'{"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        )
        self.assertLess(time.monotonic() - started, 1)

    def test_a_failed_placeholder_does_not_make_the_response_wait(self):
        class Boom(Memory):
            def add(self, row):
                raise OSError("full")

        session = BridgeSession(Boom())
        session.wait = 30
        try:
            session._on_request("POST /v1/chat/completions HTTP/1.1", {}, b"{}")
        except OSError:
            pass
        started = time.monotonic()
        session._on_response(
            "HTTP/1.1 200 OK",
            {"content-type": "application/json"},
            b'{"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        )
        self.assertLess(time.monotonic() - started, 1)

    def test_a_failed_write_removes_the_usage_file(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "usage.json")
        real_write = usage_tap.os.write
        calls = {"n": 0}

        def flaky(fd, data):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise OSError("full")
            return real_write(fd, data)

        usage_tap.os.write = flaky
        try:
            store = UsageStore(path)
            store.add({"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13})
            with open(path, "rb") as handle:
                self.assertIn(b"prompt_tokens", handle.read())
            with self.assertRaises(OSError):
                store.add({"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5})
            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(path + ".tmp"))
            listed = os.listdir(directory)
            self.assertEqual(listed, [])
        finally:
            usage_tap.os.write = real_write


if __name__ == "__main__":
    unittest.main(verbosity=2)
