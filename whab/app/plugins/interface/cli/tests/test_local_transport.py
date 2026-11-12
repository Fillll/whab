"""The local console's small transport preserves TLS and failure semantics."""

import io
import ssl
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli.local_transport import LocalClient, Response


class Reply:
    status = 200

    def __init__(self, raw=b'{"ok":true}'):
        self.body = io.BytesIO(raw)
        self.closed = False

    def read(self, size=None):
        return self.body.read() if size is None else self.body.read(size)

    def isclosed(self):
        return self.closed or self.body.tell() == len(self.body.getvalue())

    def close(self):
        self.closed = True


@pytest.fixture
def connection(monkeypatch):
    calls = []
    connection = SimpleNamespace(sock=object(), reply=Reply(), closed=False)
    connection.connect = lambda: setattr(connection, 'sock', object())
    connection.close = lambda: setattr(connection, 'closed', True)
    connection.request = lambda *a, **kw: calls.append((a, kw))
    connection.getresponse = lambda: connection.reply
    def factory(host, port, *, context, timeout):
        assert host == '127.0.0.1' and port == 5173 and isinstance(context, ssl.SSLContext) and timeout == 60
        return connection
    monkeypatch.setattr('http.client.HTTPSConnection', factory)
    return connection, calls


def client():
    return LocalClient(base_url='https://127.0.0.1:5173', verify=ssl.create_default_context(),
                       headers={'Authorization': 'Bearer fictional'}, timeout=60, trust_env=False, follow_redirects=False)


def test_verified_transport_preserves_headers_json_and_repeated_query_parameters(connection):
    native, calls = connection
    transport = client()
    result = transport.post('/fixture', json={'name': 'Fictional 家庭'})
    assert result.status_code == 200 and result.json() == {'ok': True}
    assert calls[-1][1]['headers']['Authorization'] == 'Bearer fictional'
    assert calls[-1][1]['headers']['Content-Type'] == 'application/json'
    native.reply = Reply(b'chunk')
    with transport.stream('GET', '/fixture?selected=1', params={'merchant': ['A', 'B']}) as response:
        assert b''.join(response.iter_bytes()) == b'chunk'
    assert calls[-1][0][1] == '/fixture?selected=1&merchant=A&merchant=B'
    native.reply = Reply()
    assert transport.delete('/lease').json() == {'ok': True}
    native.reply = Reply(b'large response')
    with transport.stream('GET', '/fixture', params={'limit': 1}):
        pass
    assert native.closed
    transport.close()


@pytest.mark.parametrize('error', [ConnectionRefusedError(), OSError(), httpx.ConnectError('fixture')])
def test_startup_connection_failure_never_submits_an_operation(connection, error):
    native, calls = connection
    native.sock = None
    def fail():
        raise error
    native.connect = fail
    with pytest.raises(httpx.ConnectError):
        client().post('/leases', json={'pid': 1})
    assert not calls


def test_lost_request_reply_is_reported_without_replay(connection):
    native, calls = connection
    native.getresponse = lambda: (_ for _ in ()).throw(OSError('fictional disconnect'))
    with pytest.raises(httpx.TransportError):
        client().post('/commands', json={'operation': 'fictional'})
    assert len(calls) == 1 and native.closed
    native.getresponse = lambda: (_ for _ in ()).throw(ConnectionRefusedError())
    with pytest.raises(httpx.ConnectError):
        client().post('/leases', json={'pid': 1})


def test_interrupted_response_never_returns_partial_json():
    reply = Reply()
    reply.read = lambda *a: (_ for _ in ()).throw(OSError('fixture'))
    with pytest.raises(httpx.ReadError):
        Response(reply).json()


def test_transport_refuses_insecure_or_ambient_connections():
    for updates in ({'base_url': 'http://127.0.0.1:5173'}, {'trust_env': True}, {'follow_redirects': True}):
        arguments = dict(base_url='https://127.0.0.1:5173', verify=ssl.create_default_context(), headers={}, timeout=60,
                         trust_env=False, follow_redirects=False)
        arguments.update(updates)
        with pytest.raises(ValueError):
            LocalClient(**arguments)
