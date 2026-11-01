"""Real subprocess boundaries, scoped callbacks and interruption regressions."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from threading import RLock, get_ident

import pytest

from whab.plugins.process_rpc import Peer, ProcessUnavailable
from whab.plugins.process_wire import HEADER, VERSION, Codec, lengths
from whab.plugins.wire import WireError


@contextmanager
def peers(root):
    first_read, second_write = os.pipe()
    second_read, first_write = os.pipe()
    streams = [os.fdopen(first_read, "rb", buffering=0), os.fdopen(first_write, "wb", buffering=0),
               os.fdopen(second_read, "rb", buffering=0), os.fdopen(second_write, "wb", buffering=0)]
    first = Peer(*streams[:2], timeout=3)
    second = Peer(*streams[2:], timeout=3, root=root)
    try:
        yield first, second
    finally:
        first.close()
        second.close()
        for stream in (streams[1], streams[3], streams[0], streams[2]):
            stream.close()


def fresh_python(source, *, timeout=90):
    result = subprocess.run([sys.executable, "-I", "-B", "-c", source], text=True,
                            capture_output=True, timeout=timeout, env={
                                key: value for key, value in os.environ.items()
                                if key in {"PATH", "TMPDIR", "SYSTEMROOT", "LANG"}})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_installed_plugin_is_discovered_in_its_own_process_without_parent_imports():
    result = fresh_python('''
import json, os, sys
from importlib.metadata import entry_points
from whab.plugins.runtime import load_descriptors
entries = tuple(entry_points(group="whab.plugins"))
plugins = load_descriptors(tuple(sorted(entry.name for entry in entries)))
try:
    pids = [plugin._process._process.pid for plugin in plugins]
    assert all(plugin._process.healthy for plugin in plugins)
    assert len(set(pids)) == len(plugins)
    assert os.getpid() not in pids
    assert not any(name.startswith("whab_plugin_") for name in sys.modules)
    print(json.dumps({"plugins": [plugin.id for plugin in plugins]}))
finally:
    for plugin in plugins:
        plugin._process.close()
''')
    assert {"sqlite", "json", "web", "plaid", "server", "cli", "client", "ynab", "bank-logos",
            "subscriptions", "subscriptions-ai", "ai-credentials", "llm-categorization",
            "random-forest-categorization"} <= set(result["plugins"])


def test_reentrant_callbacks_execute_on_waiting_thread_and_keep_lock_ownership():
    seen = []
    lock = RLock()

    def root(operation, callback=None):
        if operation == "inner":
            return 42
        return callback()

    with peers(root) as (host, _):
        def callback():
            seen.append(get_ident())
            with lock:
                return host.call("root", "inner")

        with lock:
            assert host.call("root", "outer", (callback,)) == 42
        assert seen == [get_ident()]


def test_callback_lock_is_released_when_plugin_returns_without_exiting_it():
    lock = RLock()

    def root(_, remote_lock):
        remote_lock.__enter__()
        return "done"

    with peers(root) as (host, _):
        assert host.call("root", "leak", (lock,)) == "done"
        assert not lock._is_owned()


def test_private_host_attributes_cannot_be_read_through_service_capabilities():
    class Service:
        _store = "FICTIONAL_PRIVATE_STORE"

        def register_feature(self, *_):
            pass

        def query(self, operation):
            return operation

    def root(_, service):
        with pytest.raises(AttributeError):
            service._store
        return service.query("public")

    with peers(root) as (host, _):
        assert host.call("root", "service", (Service(),)) == "public"


def test_child_errors_do_not_cross_the_pipe_as_private_exception_text():
    def root(_):
        raise RuntimeError("FICTIONAL_PRIVATE_EXCEPTION")

    with peers(root) as (host, _):
        with pytest.raises(ProcessUnavailable) as error:
            host.call("root", "fail")
        assert "FICTIONAL_PRIVATE" not in str(error.value)


def test_large_binary_calls_handle_partial_pipe_writes():
    raw = b"fictional-bulk-data" * 100000
    with peers(lambda _, value: value) as (host, _):
        assert host.call("root", "echo", (raw,)) == raw


def test_public_records_and_capability_identity_round_trip():
    from whab.plugins import BudgetContext, Insight

    def root(_, context):
        context.save_state({"updated": True})
        return context.save_state, context.save_state, (Insight("Result", "ok"),)

    states = []
    context = BudgetContext("fictional", {}, {}, states.append)
    with peers(root) as (host, _):
        first, second, insights = host.call("root", "context", (context,))
        assert first is second
        assert insights == (Insight("Result", "ok"),)
        assert states == [{"updated": True}]


@pytest.mark.parametrize("raw", [
    ["record", "untrusted.module.Type", {}],
    ["dict", [["duplicate", ["value", 1]], ["duplicate", ["value", 2]]]],
    ["bytes", 0, 1, "wrong-digest"],
    ["capability", {"id": "a" * 32, "kind": "service", "methods": ["_store"],
                    "attributes": [], "setters": [], "async": []}],
])
def test_invalid_or_executable_wire_values_are_rejected(raw):
    codec = Codec(lambda _: (_ for _ in ()).throw(WireError()), lambda _: (_ for _ in ()).throw(WireError()))
    metadata = json.dumps(raw).encode()
    with pytest.raises(WireError):
        codec.decode(HEADER.pack(b"WHPR", VERSION, len(metadata), 0) + metadata)


def test_wrong_protocol_and_oversized_frames_are_refused_before_body_reads():
    for header in (HEADER.pack(b"WHPR", VERSION + 1, 2, 0), HEADER.pack(b"WHPR", VERSION, 2**31, 0),
                   HEADER.pack(b"WHPR", VERSION, 2, 2**31)):
        with pytest.raises(WireError):
            lengths(header)


@pytest.mark.parametrize("storage", ["sqlite", "json"])
def test_full_process_app_keeps_financial_commands_settings_and_saved_labels_working(storage):
    result = fresh_python('''
import json, sys
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from whab.app import create_app
from whab.config import Settings
with TemporaryDirectory(prefix="whab-process-contract-") as temporary:
    settings = Settings(Path(temporary),5173,"sandbox","","","",False,"","",
                        ("subscriptions","ai-credentials","subscriptions-ai","cli"), storage=STORAGE, ui="")
    app = create_app(settings=settings)
    processes = [plugin._process for plugin in app.state.plugins.descriptors()]
    with TestClient(app, base_url="https://localhost:5173") as client:
        service = app.state.ledger_service
        account = service.command("add_account", "personal", {"name":"Fictional account","type":"depository","balance":10000},
                                  request_id="fictional-account", expected_revision=service.revision)
        assert account["data"]
        assert client.get("/api/plugin-settings/cli").status_code == 200
        response = client.post("/api/plugin-settings/subscriptions", json={
            "workspace":"personal","action":"status","month":"2026-11","currency":"USD"})
        assert response.status_code == 200, response.text
        assert response.json()["aiAvailable"] is True
        assert service.query("features", "personal")["data"]["subscriptions"]["commands"] == ["set_label"]
        assert client.post("/api/plugin-settings/ai-credentials", json={"workspace":"demo","action":"status"}).status_code == 200
        assert client.post("/api/plugin-settings/subscriptions", json={}, headers={"origin":"https://foreign.example"}).status_code == 403
        assert not any(name.startswith("whab_plugin_") for name in sys.modules)
    assert all(not process.healthy and not process.cleanup_pending for process in processes)
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://localhost:5173"):
        assert any(row["name"] == "Fictional account" for row in reopened.state.ledger_service.query("accounts", "personal")["data"])
    print(json.dumps({"storage":STORAGE,"saved":True}))
'''.replace("STORAGE", repr(storage)))
    assert result == {"storage": storage, "saved": True}
