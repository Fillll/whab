"""Real TLS, per-instance authentication, revocation and bounded duplex traffic."""

import http.client
import json
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from whab.plugins.https_transport import CHUNK, FRAME_LIMIT, Broker, Client


@pytest.fixture
def transport():
    broker = Broker()
    try:
        yield broker
    finally:
        broker.close()


def session(transport, plugin="fictional-plugin", family="process_worker"):
    channel = transport.session(plugin, family)
    channel.process = SimpleNamespace(poll=lambda: None)
    credential = json.loads(channel.bootstrap())
    return channel, credential


def request(transport, credential=None, *, method="GET", path="/v1/receive", headers=None, length=None):
    context = ssl.create_default_context(cadata=transport.ca)
    connection = http.client.HTTPSConnection("127.0.0.1", transport.server.server_port, context=context, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True)
        for key, value in headers or [("Host", f"127.0.0.1:{transport.server.server_port}")]:
            connection.putheader(key, value)
        if credential is not None:
            connection.putheader("Authorization", "Bearer " + credential["token"])
        if length is not None:
            connection.putheader("Content-Length", str(length))
            connection.putheader("Content-Type", "application/octet-stream")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def read_exact(source, size):
    result = bytearray()
    while len(result) < size:
        part = source.read(size - len(result))
        assert part
        result.extend(part)
    return bytes(result)


@pytest.mark.parametrize("family", ["process_worker", "report_worker", "storage_worker"])
def test_real_https_duplex_streams_preserve_large_binary_frames_and_instance_scope(transport, family):
    first, credential = session(transport, family=family)
    second, _ = session(transport, plugin="other-plugin")
    client = Client(credential)
    payload = b"\x00\xfffictional-private-data" * CHUNK
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            sender = executor.submit(first.send_frame, payload, 5)
            assert read_exact(client, len(payload)) == payload
            sender.result(timeout=5)
            sender = executor.submit(client.send_frame, payload, 5)
            assert read_exact(first, len(payload)) == payload
            sender.result(timeout=5)
        with pytest.raises(TimeoutError):
            second.read(1, timeout=0.02)
        assert first.broker is second.broker and first.digest != second.digest
    finally:
        client.close()


def test_missing_wrong_revoked_and_previous_instance_tokens_have_no_access(transport, capfd):
    channel, credential = session(transport)
    assert request(transport) == (401, b"")
    wrong = {**credential, "token":"x" * 43}
    assert request(transport, wrong) == (401, b"")
    channel.close()
    replacement, current = session(transport)
    assert request(transport, credential) == (401, b"")
    assert request(transport, current) == (204, b"")
    replacement.process = SimpleNamespace(poll=lambda: 0)
    assert request(transport, current) == (401, b"")
    assert replacement.closed and not transport.sessions
    assert credential["token"] not in repr(channel)
    output = capfd.readouterr()
    assert credential["token"] not in output.out + output.err


@pytest.mark.parametrize("headers", [
    [("Host", "foreign.example")],
    [("Host", "127.0.0.1:PORT"), ("Host", "127.0.0.1:PORT")],
    [("Host", "127.0.0.1:PORT"), ("Origin", "https://foreign.example")],
    [("Host", "127.0.0.1:PORT"), ("Origin", "https://127.0.0.1:PORT")],
    [("Host", "127.0.0.1:PORT"), ("Cookie", "fictional-browser-cookie")],
    [("Host", "127.0.0.1:PORT"), ("Transfer-Encoding", "chunked")],
    [("Host", "127.0.0.1:PORT"), ("Expect", "100-continue")],
    [("Host", "127.0.0.1:PORT"), ("Upgrade", "websocket")],
])
def test_browser_origins_ambiguous_headers_and_host_spoofing_are_rejected(transport, headers):
    _, credential = session(transport)
    headers = [(key, value.replace("PORT", str(transport.server.server_port))) for key, value in headers]
    assert request(transport, credential, headers=headers) == (403, b"")


def test_https_rejects_untrusted_certificates_wrong_names_and_plaintext(transport):
    port = transport.server.server_port
    connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=3)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            connection.request("GET", "/v1/receive")
    finally:
        connection.close()
    context = ssl.create_default_context(cadata=transport.ca)
    with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(raw, server_hostname="foreign.example")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        with pytest.raises((OSError, http.client.HTTPException)):
            connection.request("GET", "/v1/receive")
            connection.getresponse()
    finally:
        connection.close()


def test_body_limits_and_unknown_routes_are_rejected_before_consuming_bodies(transport):
    _, credential = session(transport)
    assert request(transport, credential, method="POST", path="/v1/send", length=FRAME_LIMIT + 1) == (413, b"")
    assert request(transport, method="POST", path="/v1/send", length=FRAME_LIMIT) == (401, b"")
    assert request(transport, credential, method="POST", path="/v1/send", length=0) == (413, b"")
    assert request(transport, credential, path="/other-session/v1/receive") == (404, b"")
    assert request(transport, credential, path="/v1/receive?token=ignored") == (404, b"")


def test_revocation_wakes_blocked_host_read_and_never_reuses_a_bootstrap(transport):
    channel, _ = session(transport)
    with pytest.raises(ValueError, match="already been delivered"):
        channel.bootstrap()
    with ThreadPoolExecutor(max_workers=1) as executor:
        receiver = executor.submit(channel.read, 1)
        channel.close()
        assert receiver.result(timeout=1) == b""
    with pytest.raises(EOFError):
        channel.send_frame(b"fictional")


def test_client_never_retries_an_unacknowledged_post(transport, monkeypatch):
    _, credential = session(transport)
    client = Client(credential)
    calls = []

    class LostReply:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            calls.append(args)
            raise OSError("fictional lost write acknowledgment")

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", LostReply)
    with pytest.raises(OSError, match="channel is unavailable"):
        client.send_frame(b"fictional-write")
    assert len(calls) == 1 and client.closed


def test_listener_has_only_loopback_address_and_its_temporary_keys_are_removed(transport):
    assert transport.server.server_address[0] == "127.0.0.1"
    from pathlib import Path

    directory = Path(transport._temporary.name)
    assert directory.is_dir()
    transport.close()
    assert not directory.exists()


def test_real_worker_bootstrap_secret_is_absent_from_arguments_environment_and_status(monkeypatch):
    from whab.plugins import process_worker
    from whab.plugins.https_transport import Session

    observed = {}
    bootstrap = Session.bootstrap
    popen = process_worker.subprocess.Popen

    def capture_bootstrap(channel):
        raw = bootstrap(channel)
        observed["credential"] = json.loads(raw)
        return raw

    def capture_process(args, **kwargs):
        observed["args"], observed["env"] = args, kwargs["env"]
        return popen(args, **kwargs)

    monkeypatch.setattr(Session, "bootstrap", capture_bootstrap)
    monkeypatch.setattr(process_worker.subprocess, "Popen", capture_process)
    worker = process_worker.PluginProcess("example-api")
    try:
        token = observed["credential"]["token"]
        assert token not in str(observed["args"]) and token not in str(observed["env"])
        assert token not in str(worker.info) and token not in repr(worker.channel)
        assert worker._process.stdin.closed and worker._process.stdout is None
        assert worker.channel._secret is None
    finally:
        worker.close()
    assert request(worker.channel.broker, observed["credential"]) == (401, b"")


def test_a_stalled_tls_handshake_does_not_block_authenticated_workers(transport):
    _, credential = session(transport)
    with socket.create_connection(("127.0.0.1", transport.server.server_port), timeout=3):
        assert request(transport, credential) == (204, b"")


def test_thread_admission_failure_closes_the_socket_and_releases_capacity(transport, monkeypatch):
    import threading

    capacity = transport.server._slots._value

    def exhausted(_):
        raise RuntimeError("fictional thread capacity exhausted")

    monkeypatch.setattr(threading.Thread, "start", exhausted)
    with socket.socket() as connection:
        with pytest.raises(RuntimeError):
            transport.server.process_request(connection, ("127.0.0.1", 1))
        assert connection.fileno() == -1
    assert transport.server._slots._value == capacity
    assert not transport.server._connections
