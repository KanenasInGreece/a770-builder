#!/usr/bin/env python3
'''Forward the sandbox bridge and keep usage integers from the responses.

The listen socket is the host side of the bridge. Each connection is opened
to the upstream TCP server. Bytes are written onward as they arrive. A copy
of the server-to-client direction is scanned for one usage object per
/v1/chat/completions or /v1/completions response. The output file is a JSON
list of those integers. Headers, request bodies, and message text are not
written. A chat request is an empty object as soon as it is parsed, and the
response replaces that object when it finishes. A call that never
finishes stays empty, so a later reader cannot treat the gap as zero.
A response that carries no integer count stays empty too.
A write that fails removes the usage file, so an older complete list
cannot be read as this run.
'''

import argparse
import json
import os
import queue
import socket
import sys
import threading

_MAX_HEADERS = 65536
_MAX_BODY = 32 * 1024 * 1024
_CHAT_PATHS = ('/v1/chat/completions', '/v1/completions')
_COUNT_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens')


def is_count(value):
    '''True for an integer of at least zero that is not a boolean.'''
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def usage_fields(usage):
    '''The integer fields of one usage object. Anything else is left out.'''
    if not isinstance(usage, dict):
        return {}
    found = {}
    for key in _COUNT_KEYS:
        value = usage.get(key)
        if is_count(value):
            found[key] = value
    details = usage.get('prompt_tokens_details')
    if isinstance(details, dict) and is_count(details.get('cached_tokens')):
        found['cached_tokens'] = details['cached_tokens']
    if 'reasoning_tokens' not in found:
        details = usage.get('completion_tokens_details')
        if isinstance(details, dict) and is_count(details.get('reasoning_tokens')):
            found['reasoning_tokens'] = details['reasoning_tokens']
    return found


def record_from_body(body, content_type):
    '''One record for a chat-completions body.

    An empty dict means the call carried no integer count. A stream keeps the
    last usage object. A number that appears only inside message text is not
    a field of that object.
    '''
    if not isinstance(body, (bytes, bytearray)):
        return {}
    ctype = content_type.lower() if isinstance(content_type, str) else ''
    if 'text/event-stream' in ctype or bytes(body).lstrip().startswith(b'data:'):
        last = None
        for line in bytes(body).splitlines():
            stripped = line.strip()
            if not stripped.startswith(b'data:'):
                continue
            payload = stripped[5:].strip()
            if payload == b'' or payload == b'[DONE]':
                continue
            try:
                event = json.loads(payload.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(event, dict) and isinstance(event.get('usage'), dict):
                last = usage_fields(event['usage'])
        return {} if last is None else last
    try:
        obj = json.loads(bytes(body).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    if 'usage' not in obj and 'choices' not in obj:
        return {}
    usage = obj.get('usage')
    if not isinstance(usage, dict):
        return {}
    return usage_fields(usage)


def _status_code(start):
    parts = start.split(' ')
    if len(parts) < 2 or not parts[0].startswith('HTTP/'):
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


class MessageParser:
    '''One HTTP/1.x direction. Each finished message is handed to on_message.

    on_message(start, headers, body) sees the decoded body, or None when the
    body was too large to keep. Framing is still consumed. A parse that cannot
    continue sets abandoned and stops, so a later message is not read as if
    the framing were still aligned.
    '''

    def __init__(self, on_message, request=False):
        self.on_message = on_message
        self.request = request
        self.buf = bytearray()
        self.state = 'headers'
        self.start = ''
        self.headers = {}
        self.rest = 0
        self.body = bytearray()
        self.overflow = False
        self.abandoned = False

    def feed(self, data):
        if self.abandoned or self.state == 'done':
            return
        self.buf += data
        if self.state == 'eof-body':
            self._append_body(self.buf)
            self.buf.clear()
            return
        self._pump()

    def close(self):
        if self.abandoned or self.state == 'done':
            return
        if self.state == 'eof-body':
            self._append_body(self.buf)
            self.buf.clear()
            self._finish()
        self.state = 'done'

    def _pump(self):
        while not self.abandoned and self.state not in ('done', 'eof-body'):
            if self.state == 'headers':
                if not self._take_headers():
                    return
            elif self.state == 'length':
                if not self._take_length():
                    return
            elif self.state == 'chunk-size':
                if not self._take_chunk_size():
                    return
            elif self.state == 'chunk-data':
                if not self._take_chunk_data():
                    return
            elif self.state == 'chunk-crlf':
                if not self._take_chunk_crlf():
                    return
            elif self.state == 'trailers':
                if not self._take_trailers():
                    return
            else:
                return

    def _take_headers(self):
        idx = self.buf.find(b'\r\n\r\n')
        if idx < 0:
            if len(self.buf) > _MAX_HEADERS:
                self.abandoned = True
                self.buf.clear()
            return False
        raw = bytes(self.buf[:idx])
        del self.buf[:idx + 4]
        lines = raw.decode('iso-8859-1', 'replace').split('\r\n')
        self.start = lines[0] if lines else ''
        self.headers = {}
        for line in lines[1:]:
            if ':' not in line:
                continue
            name, value = line.split(':', 1)
            self.headers[name.strip().lower()] = value.strip()
        code = _status_code(self.start)
        if code is not None and 100 <= code <= 199:
            return True
        transfer = self.headers.get('transfer-encoding', '')
        if 'chunked' in transfer.lower():
            self._begin_body()
            self.state = 'chunk-size'
            return True
        length = self.headers.get('content-length')
        if length is not None and length.isdigit():
            self._begin_body()
            self.rest = int(length)
            self.state = 'length'
            return True
        # A request with no length and no chunking has no body. A 204 or 304
        # has none either, so the next response on a keep-alive connection stays
        # a separate message. Any other response with neither is delimited by
        # the close, which HTTP/1.1 uses for that case.
        if self.request or code in (204, 304):
            self._begin_body()
            self._finish()
            return True
        self._begin_body()
        self.state = 'eof-body'
        self._append_body(self.buf)
        self.buf.clear()
        return False

    def _begin_body(self):
        self.body = bytearray()
        self.overflow = False

    def _append_body(self, data):
        if self.overflow or not data:
            return
        if len(self.body) + len(data) > _MAX_BODY:
            self.overflow = True
            self.body = bytearray()
            return
        self.body += data

    def _take_length(self):
        take = min(self.rest, len(self.buf))
        self._append_body(self.buf[:take])
        del self.buf[:take]
        self.rest -= take
        if self.rest > 0:
            return False
        self._finish()
        return True

    def _take_chunk_size(self):
        idx = self.buf.find(b'\r\n')
        if idx < 0:
            if len(self.buf) > 256:
                self.abandoned = True
                self.buf.clear()
            return False
        line = bytes(self.buf[:idx]).split(b';', 1)[0].strip()
        del self.buf[:idx + 2]
        try:
            size = int(line, 16)
        except ValueError:
            self.abandoned = True
            self.buf.clear()
            return False
        if size == 0:
            self.state = 'trailers'
            return True
        self.rest = size
        self.state = 'chunk-data'
        return True

    def _take_chunk_data(self):
        take = min(self.rest, len(self.buf))
        self._append_body(self.buf[:take])
        del self.buf[:take]
        self.rest -= take
        if self.rest > 0:
            return False
        self.state = 'chunk-crlf'
        return True

    def _take_chunk_crlf(self):
        if len(self.buf) < 2:
            return False
        if self.buf[:2] != b'\r\n':
            self.abandoned = True
            self.buf.clear()
            return False
        del self.buf[:2]
        self.state = 'chunk-size'
        return True

    def _take_trailers(self):
        if self.buf.startswith(b'\r\n'):
            del self.buf[:2]
            self._finish()
            return True
        idx = self.buf.find(b'\r\n\r\n')
        if idx < 0:
            if len(self.buf) > _MAX_HEADERS:
                self.abandoned = True
                self.buf.clear()
            return False
        del self.buf[:idx + 4]
        self._finish()
        return True

    def _finish(self):
        body = None if self.overflow else bytes(self.body)
        start = self.start
        headers = self.headers
        self.body = bytearray()
        self.headers = {}
        self.start = ''
        self.state = 'headers'
        self.on_message(start, headers, body)


class UsageStore:
    '''The JSON list on disk. Each replacement is a whole file of integers.'''

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.rows = []
        self._write([])

    def _clean(self, row):
        if not isinstance(row, dict):
            row = {}
        clean = {}
        for key, value in row.items():
            if key in _COUNT_KEYS or key == 'cached_tokens':
                if is_count(value):
                    clean[key] = value
        return clean

    def add(self, row):
        clean = self._clean(row)
        with self.lock:
            self.rows.append(clean)
            self._write(self.rows)
            return len(self.rows) - 1

    def replace(self, index, row):
        clean = self._clean(row)
        with self.lock:
            if not isinstance(index, int) or isinstance(index, bool):
                return
            if index < 0 or index >= len(self.rows):
                return
            self.rows[index] = clean
            self._write(self.rows)

    def _write(self, rows):
        '''Replace the file. On failure the previous file is removed.

        The in-memory rows may already include the new one. Leaving the
        previous file would let a reader sum a shorter complete list.
        '''
        blob = json.dumps(rows, separators=(',', ':')).encode('ascii') + b'\n'
        tmp = self.path + '.tmp'
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, blob)
            finally:
                os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            for path in (self.path, tmp):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise


class BridgeSession:
    '''Pair requests with responses and record a chat-completions usage.'''

    def __init__(self, store):
        self.store = store
        self.flags = queue.Queue()
        self.wait = 2
        self.requests = MessageParser(self._on_request, request=True)
        self.responses = MessageParser(self._on_response, request=False)
        self.poisoned = False

    def feed_request(self, data):
        self.requests.feed(data)
        self._note_abandon(self.requests)

    def feed_response(self, data):
        self.responses.feed(data)
        self._note_abandon(self.responses)

    def close(self):
        self.requests.close()
        self.responses.close()
        self._note_abandon(self.requests)
        self._note_abandon(self.responses)
        while True:
            try:
                self.flags.get_nowait()
            except queue.Empty:
                return

    def _note_abandon(self, parser):
        if parser.abandoned:
            self._poison()

    def _poison(self):
        '''One empty record. A failure to write it does not escape.'''
        if self.poisoned:
            return
        self.poisoned = True
        try:
            self.store.add({})
        except Exception:
            pass

    def _on_request(self, start, headers, body):
        del headers, body
        parts = start.split(' ')
        method = parts[0].upper() if parts else ''
        target = parts[1] if len(parts) > 1 else ''
        path = target.split('?', 1)[0]
        chat = method == 'POST' and path in _CHAT_PATHS
        if not chat or self.poisoned:
            self.flags.put(None)
            return
        self.flags.put(self.store.add({}))

    def _on_response(self, start, headers, body):
        del start
        try:
            index = self.flags.get(timeout=self.wait)
        except queue.Empty:
            self._poison()
            return
        if index is None or self.poisoned:
            return
        if body is None:
            record = {}
        else:
            record = record_from_body(body, headers.get('content-type', ''))
        self.store.replace(index, record)


def handle_connection(client, upstream, store):
    '''Forward both ways. A parse error does not stop the bytes.'''
    try:
        remote = socket.create_connection(upstream, timeout=10)
    except OSError:
        client.close()
        return
    remote.settimeout(None)
    client.settimeout(None)
    session = BridgeSession(store)

    def pump(src, dst, feed):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
                try:
                    feed(data)
                except Exception:
                    session._poison()
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    client_to_server = threading.Thread(
        target=pump, args=(client, remote, session.feed_request), daemon=True)
    server_to_client = threading.Thread(
        target=pump, args=(remote, client, session.feed_response), daemon=True)
    client_to_server.start()
    server_to_client.start()
    client_to_server.join()
    server_to_client.join()
    try:
        session.close()
    except Exception:
        session._poison()
    client.close()
    remote.close()


def serve(sock_path, upstream, out_path, stop):
    '''Listen until stop is set. upstream is a (host, port) pair.'''
    store = UsageStore(out_path)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(16)
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=handle_connection, args=(client, upstream, store), daemon=True).start()
    finally:
        server.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


def main(argv):
    parser = argparse.ArgumentParser(description='Forward a bridge and record usage integers.')
    parser.add_argument('--listen', required=True)
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    host, sep, port = args.upstream.rpartition(':')
    if sep != ':' or host == '' or not port.isdigit():
        sys.stderr.write('usage_tap: --upstream must be host:port\n')
        return 2
    stop = threading.Event()
    try:
        serve(args.listen, (host, int(port)), args.out, stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
