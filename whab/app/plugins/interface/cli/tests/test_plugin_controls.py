"""Protected plugin failures remain nonzero, readable and free of server records."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import client, console


@pytest.mark.parametrize("adapter", ["console", "client"])
@pytest.mark.parametrize("status,raw,expected", [
    (409, b'{"code":"required_plugin","error":"PRIVATE_SERVER_DETAIL"}', "primary storage is required"),
    (400, b'{"code":"management_tool","error":"PRIVATE_SERVER_DETAIL"}', "installed management tool"),
    (409, b'{"code":"unknown","error":"PRIVATE_SERVER_DETAIL"}', None),
    (409, b'not JSON PRIVATE_SERVER_DETAIL', None),
    (409, b'["PRIVATE_SERVER_DETAIL"]', None),
    (500, b'{"code":"required_plugin","error":"PRIVATE_SERVER_DETAIL"}', None),
])
def test_rejected_controls_are_not_retried_or_echoed(monkeypatch, tmp_path, capsys, adapter, status, raw, expected):
    from whab.plugins import local

    writes = []
    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"budgets": [{"id": "fictional", "name": "Fictional"}]})
        writes.append(request)
        return httpx.Response(status, content=raw)

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(respond)) as session:
            yield session
    monkeypatch.setattr(console, "local_connection", connection)
    monkeypatch.setattr(local, "local_connection", connection)
    arguments = ["--home", str(tmp_path)]
    arguments += (["Fictional", "plugin"] if adapter == "console" else ["--budget", "fictional", "plugins"])
    assert getattr({"console": console, "client": client}[adapter], "main")([*arguments, "disable", "sqlite"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "PRIVATE_SERVER_DETAIL" not in captured.err
    error = json.loads(captured.err)["error"]
    if expected:
        assert expected in error
    assert len(writes) == 1
