"""Private authenticated HTTPS streams for every host-owned plugin worker.

The bootstrap/client path uses only the standard library so an approved older
report environment can retain its own SDK. Protocol frames and financial receipt
semantics stay in their existing adapters. No HTTP request is retried.
"""

import atexit
import hashlib
import http.client
import importlib
import json
import os
import secrets
import socket
import ssl
import sys
import tempfile
import threading
from collections import deque
from contextvars import Context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic

VERSION = 1
CHUNK = 256 * 1024
BUFFER_LIMIT = 4 * 1024 * 1024
FRAME_LIMIT = 320 * 1024 * 1024 + 64
BOOTSTRAP_LIMIT = 16 * 1024
_BROKER = None
_LOCK = threading.Lock()
_FAMILIES = frozenset({"process_worker", "report_worker", "storage_worker"})


class _Buffer:
    def __init__(self):
        self._condition = threading.Condition()
        self._parts, self._size, self._closed = deque(), 0, False

    def put(self, data, timeout=60):
        if type(data) is not bytes or not 0 < len(data) <= CHUNK:
            raise ValueError("Invalid plugin HTTPS chunk.")
        deadline = monotonic() + timeout
        with self._condition:
            while not self._closed and self._size + len(data) > BUFFER_LIMIT:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                self._condition.wait(remaining)
            if self._closed:
                raise EOFError()
            self._parts.append(data)
            self._size += len(data)
            self._condition.notify_all()

    def read(self, size, timeout=None):
        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while not self._parts and not self._closed:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError()
                self._condition.wait(remaining)
            if self._closed:
                return b""
            data = self._parts.popleft()
            result = data[:size]
            if len(data) > size:
                self._parts.appendleft(data[size:])
            self._size -= len(result)
            self._condition.notify_all()
            return result

    def close(self):
        with self._condition:
            self._closed = True
            self._parts.clear()
            self._size = 0
            self._condition.notify_all()


class Session:
    """One plugin instance's authority: its own two streams, never another's."""

    def __init__(self, broker, plugin_id, family):
        self.broker, self.plugin_id, self.family = broker, plugin_id, family
        self.process = None
        self.closed = False
        self._secret = secrets.token_urlsafe(32)
        self.digest = hashlib.sha256(self._secret.encode("ascii")).digest()
        self.incoming, self.outgoing = _Buffer(), _Buffer()
        self._send_lock, self._receive_lock = threading.Lock(), threading.Lock()
        self._write_lock = threading.Lock()
        self._delivery = threading.Condition()
        self._queued = self._offered = self._delivered = 0

    def bootstrap(self):
        if self._secret is None:
            raise ValueError("Plugin bootstrap credentials have already been delivered.")
        secret, self._secret = self._secret, None
        return json.dumps({"version": VERSION, "port": self.broker.server.server_port,
                           "ca": self.broker.ca, "token": secret,
                           "plugin": self.plugin_id, "family": self.family}).encode("utf-8")

    def read(self, size, timeout=None):
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0, deadline - monotonic())
            try:
                # Drain a final response before observing child exit. Idle
                # readers also detect a crashed child without waiting a full RPC.
                return self.incoming.read(size, 0.1 if remaining is None else min(0.1, remaining))
            except TimeoutError:
                if self.process is not None and self.process.poll() is not None:
                    self.close()
                    return b""
                if remaining is not None and monotonic() >= deadline:
                    raise

    def send_frame(self, raw, timeout=60):
        if type(raw) is not bytes or not 0 < len(raw) <= FRAME_LIMIT:
            raise ValueError("Invalid plugin HTTPS frame.")
        deadline = monotonic() + timeout
        if not self._write_lock.acquire(timeout=max(0, deadline - monotonic())):
            raise TimeoutError()
        try:
            with self._delivery:
                target = self._queued + len(raw)
                self._queued = target
            for offset in range(0, len(raw), CHUNK):
                self.outgoing.put(raw[offset:offset + CHUNK], max(0, deadline - monotonic()))
            # Dispatch completes only after the child has received the bytes.
            # This is a transport acknowledgment, never a native-write receipt.
            while True:
                with self._delivery:
                    if self.closed:
                        raise EOFError()
                    if self._delivered >= target:
                        break
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    self._delivery.wait(min(0.1, remaining))
                # A child can exit after receiving the acknowledgment response
                # while its HTTP handler is still publishing this counter. Its
                # final reply may already be buffered. Only delivery/closure or
                # the deadline decides this write; readers observe process exit.
        finally:
            self._write_lock.release()

    def close(self):
        with self.broker.lock:
            self.closed = True
            self._secret = None
            self.broker.sessions.pop(self.digest, None)
        self.incoming.close()
        self.outgoing.close()
        with self._delivery:
            self._delivery.notify_all()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass  # Paths, credentials, bodies and plugin errors never enter logs.

    def send_error(self, code, *_):
        self._reply(code, close=True)

    def _reply(self, code, body=b"", *, close=False, offset=None):
        self.send_response_only(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        if offset is not None:
            self.send_header("X-Whab-Offset", str(offset))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorize(self):
        if (self.request_version != "HTTP/1.1" or self.client_address[0] != "127.0.0.1"
                or self.headers.get_all("Host") != [f"127.0.0.1:{self.server.server_port}"]
                or any(name in self.headers for name in ("Origin", "Cookie", "Transfer-Encoding", "Expect", "Upgrade"))):
            self._reply(403, close=True)
            return None
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Bearer ") or len(values[0]) != 50:
            self._reply(401, close=True)
            return None
        digest = hashlib.sha256(values[0][7:].encode("ascii", errors="replace")).digest()
        with self.server.broker.lock:
            session = self.server.broker.sessions.get(digest)
        if session is None or session.closed:
            self._reply(401, close=True)
            return None
        if session.process is None or session.process.poll() is not None:
            session.close()
            self._reply(401, close=True)
            return None
        return session

    def do_GET(self):
        session = self._authorize()
        if session is None:
            return
        if self.path != "/v1/receive" or self.headers.get_all("Content-Length", ["0"]) != ["0"]:
            self._reply(404, close=True)
            return
        if not session._receive_lock.acquire(blocking=False):
            self._reply(409, close=True)
            return
        try:
            try:
                data = session.outgoing.read(CHUNK, timeout=1)
            except TimeoutError:
                self._reply(204)
                return
            with session._delivery:
                session._offered += len(data)
                offset = session._offered
            self._reply(200 if data else 410, data, close=not data, offset=offset)
        finally:
            session._receive_lock.release()

    def do_POST(self):
        session = self._authorize()
        if session is None:
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.path == "/v1/ack":
            values = self.headers.get_all("X-Whab-Received", [])
            if (lengths != ["0"] or len(values) != 1 or not values[0].isascii()
                    or not values[0].isdigit() or len(values[0]) > 20):
                self._reply(400, close=True)
                return
            received = int(values[0])
            with session._delivery:
                if not session._delivered <= received == session._offered:
                    self._reply(409, close=True)
                    return
            # No ownership lock is held during a network write. Flush the
            # acknowledgment response before waking the dispatching parent.
            self._reply(204)
            with session._delivery:
                session._delivered = received
                session._delivery.notify_all()
            return
        if (self.path != "/v1/send" or len(lengths) != 1 or not lengths[0].isascii()
                or not lengths[0].isdigit() or len(lengths[0]) > 10
                or self.headers.get_all("Content-Type") != ["application/octet-stream"]):
            self._reply(400, close=True)
            return
        size = int(lengths[0])
        if not 0 < size <= FRAME_LIMIT:
            self._reply(413, close=True)
            return
        if not session._send_lock.acquire(blocking=False):
            self._reply(409, close=True)
            return
        try:
            try:
                deadline = monotonic() + 60
                while size:
                    data = self.rfile.read(min(size, CHUNK))
                    if not data:
                        raise EOFError()
                    session.incoming.put(data, max(0, deadline - monotonic()))
                    size -= len(data)
            finally:
                # All bytes are ordered in the incoming stream before success
                # is visible. The next frame may arrive on another connection
                # as soon as the client receives that acknowledgment.
                session._send_lock.release()
            self._reply(204, close=True)
        except (OSError, EOFError):
            session.close()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, context):
        self.context = context
        self._slots = threading.BoundedSemaphore(512)
        self._connections = set()
        self._connections_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            return self.context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address
        except BaseException:
            connection.close()
            raise

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            threading.Thread(target=self.process_request_thread, args=(request, address), daemon=True,
                             name="whab-plugin-https-request", context=Context()).start()
        except BaseException:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()
            request.close()
            raise

    def process_request_thread(self, request, address):
        try:
            request.do_handshake()
            request.settimeout(65)
            super().process_request_thread(request, address)
        except (OSError, ValueError):
            self.shutdown_request(request)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()

    def handle_error(self, *_):
        pass


class Broker:
    def __init__(self):
        from whab.tls import prepare_local_tls

        self.pid, self.closed = os.getpid(), False
        self.lock, self.sessions = threading.RLock(), {}
        self._temporary = tempfile.TemporaryDirectory(prefix="whab-plugin-https-")
        try:
            home = Path(self._temporary.name)
            certificate, key = prepare_local_tls(home)
            self.ca = (home / "tls/ca.crt").read_text()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["http/1.1"])
            context.load_cert_chain(certificate, key)
            self.server = _Server(context)
            self.server.broker = self
            self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":0.05},
                                            daemon=True, name="whab-plugin-https", context=Context())
            self._thread.start()
        except BaseException:
            self._temporary.cleanup()
            raise
        atexit.register(self.close)

    def session(self, plugin_id, family):
        if family not in _FAMILIES or type(plugin_id) is not str or not plugin_id or len(plugin_id) > 80:
            raise ValueError("Invalid plugin HTTPS identity.")
        with self.lock:
            if self.closed or len(self.sessions) >= 256:
                raise OSError("Plugin HTTPS transport capacity is unavailable.")
            session = Session(self, plugin_id, family)
            self.sessions[session.digest] = session
            return session

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for session in tuple(self.sessions.values()):
                session.close()
        self.server.shutdown()
        self.server.server_close()
        with self.server._connections_lock:
            connections = tuple(self.server._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._thread.join(timeout=1)
        self._temporary.cleanup()
        atexit.unregister(self.close)


def broker():
    global _BROKER
    with _LOCK:
        if _BROKER is None or _BROKER.closed or _BROKER.pid != os.getpid():
            _BROKER = Broker()
        return _BROKER


def command(python, family, plugin_id):
    # Load only this stdlib transport from the current host. An approved older
    # environment still imports its own worker, plugin and SDK implementations.
    return [str(python), "-I", "-B", "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1])['run_worker'](sys.argv[2],sys.argv[3])",
            str(Path(__file__).resolve()), family, plugin_id]


def deliver(session, process):
    session.process = process
    raw = session.bootstrap()
    if len(raw) > BOOTSTRAP_LIMIT:
        raise ValueError("Invalid plugin HTTPS bootstrap.")
    process.stdin.write(raw)
    process.stdin.close()


class Client:
    """Only the assigned worker's authenticated channel; no proxy or redirects."""

    def __init__(self, value):
        if (type(value) is not dict or value.keys() != {"version", "port", "ca", "token", "plugin", "family"}
                or type(value["version"]) is not int or value["version"] != VERSION
                or type(value["port"]) is not int or not 1 <= value["port"] <= 65535
                or type(value["ca"]) is not str or len(value["ca"]) > 8192
                or type(value["token"]) is not str or len(value["token"]) != 43
                or type(value["plugin"]) is not str or type(value["family"]) is not str or value["family"] not in _FAMILIES):
            raise ValueError("Invalid plugin HTTPS bootstrap.")
        self.port, self._token = value["port"], value["token"]
        self.context = ssl.create_default_context(cadata=value["ca"])
        self.context.set_alpn_protocols(["http/1.1"])
        self._received = bytearray()
        self._offset = 0
        self._receiver = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=65)
        self.closed = False

    def _headers(self):
        return {"Authorization": "Bearer " + self._token, "Content-Type": "application/octet-stream"}

    def read(self, size):
        if size < 1:
            size = CHUNK
        while not self._received and not self.closed:
            try:
                self._receiver.request("GET", "/v1/receive", headers=self._headers())
                response = self._receiver.getresponse()
                if response.status not in {200, 204} or response.getheader("Content-Length") is None:
                    raise OSError("Plugin HTTPS channel is unavailable.")
                length = int(response.getheader("Content-Length"))
                if not 0 <= length <= CHUNK or response.status == 200 and length == 0:
                    raise OSError("Invalid plugin HTTPS response.")
                data = response.read(CHUNK + 1)
                if len(data) != length:
                    raise OSError("Invalid plugin HTTPS response.")
                if data:
                    self._offset += len(data)
                    if response.getheader("X-Whab-Offset") != str(self._offset):
                        raise OSError("Invalid plugin HTTPS stream offset.")
                    self._receiver.request("POST", "/v1/ack", b"", headers={
                        **self._headers(), "X-Whab-Received":str(self._offset)})
                    acknowledged = self._receiver.getresponse()
                    if acknowledged.status != 204 or acknowledged.getheader("Content-Length") != "0":
                        raise OSError("Plugin HTTPS channel is unavailable.")
                    acknowledged.read()
                self._received.extend(data)
            except (OSError, ValueError, http.client.HTTPException):
                self.close()
                raise OSError("Plugin HTTPS channel is unavailable.") from None
        data = bytes(self._received[:size])
        del self._received[:size]
        return data

    def send_frame(self, raw, timeout=60):
        if self.closed or type(raw) is not bytes or not 0 < len(raw) <= FRAME_LIMIT:
            raise OSError("Plugin HTTPS channel is unavailable.")
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=timeout)
        deadline = monotonic() + timeout
        try:
            connection.request("POST", "/v1/send", raw, headers={**self._headers(), "Connection":"close"})
            connection.sock.settimeout(max(0.001, deadline - monotonic()))
            response = connection.getresponse()
            if response.status != 204 or response.getheader("Content-Length") != "0":
                raise OSError("Plugin HTTPS channel is unavailable.")
            response.read()
        except (OSError, ValueError, http.client.HTTPException):
            self.close()
            raise OSError("Plugin HTTPS channel is unavailable.") from None
        finally:
            connection.close()

    def write(self, raw):
        self.send_frame(raw)
        return len(raw)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self._receiver.close()
        self._token = ""


def bootstrap_client(family, plugin_id):
    raw = sys.stdin.buffer.read(BOOTSTRAP_LIMIT + 1)
    sys.stdin.close()
    if len(raw) > BOOTSTRAP_LIMIT:
        raise ValueError("Invalid plugin HTTPS bootstrap.")
    value = json.loads(raw)
    if value.get("family") != family or value.get("plugin") != plugin_id:
        raise ValueError("Invalid plugin HTTPS identity.")
    # Reserve all ordinary output before importing any trusted plugin code.
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, sys.stdout.fileno())
    os.close(null)
    return Client(value)


def run_worker(family, plugin_id):
    if family not in _FAMILIES:
        raise ValueError("Invalid plugin HTTPS family.")
    client = bootstrap_client(family, plugin_id)
    try:
        importlib.import_module("whab.plugins." + family).serve(plugin_id, client, client)
    finally:
        client.close()
