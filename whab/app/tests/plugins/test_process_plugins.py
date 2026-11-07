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
    assert all(plugin._process._process.stdin.closed and plugin._process._process.stdout is None for plugin in plugins)
    assert all(plugin._process.channel.family == "process_worker" and plugin._process.channel._secret is None for plugin in plugins)
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


def test_discarding_an_unused_descriptor_reaps_its_idle_discovery_process():
    result = fresh_python('''
import gc, json, weakref
from whab.plugins.runtime import load_descriptors
descriptor, = load_descriptors(("example-api",))
owner = weakref.ref(descriptor._process)
child = descriptor._process._process
del descriptor
gc.collect()
child.wait(timeout=5)
assert owner() is None
print(json.dumps({"reaped":True}))
''')
    assert result == {"reaped": True}


def test_restart_selection_saves_installed_bank_and_import_worker_resources():
    result = fresh_python('''
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from whab.config import Settings
from whab.plugins import PluginRegistry
from whab.plugins.configuration import read_selection
from whab.plugins.management import PluginManager
from whab.plugins.runtime import load_descriptors
required = load_descriptors(("sqlite", "web"))
try:
    with TemporaryDirectory() as directory:
        home = Path(directory)
        settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="web")
        manager = PluginManager(settings, PluginRegistry(required))
        state = manager.save(("plaid", "ynab"))
        assert read_selection(home) == ("plaid", "ynab")
        assert state["configured"] == ["plaid", "ynab"]
        assert state["active"] == [] and state["restart_required"]
        assert [plugin.id for plugin in manager.plugins.descriptors()] == ["sqlite", "web"]
        assert sorted(path.name for path in home.iterdir()) == ["plugins.json"]
        print(json.dumps({"saved": state["configured"]}))
finally:
    for plugin in required:
        plugin._process.close()
''')
    assert result == {"saved": ["plaid", "ynab"]}


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


def test_legacy_storage_unknown_write_is_fenced_without_repeating_it():
    from whab.plugins import PluginError

    class Backend:
        writes = 0

        def accounts(self, workspace):
            return self.writes

        def categories(self, workspace):
            return []

        def add_account(self, workspace, account):
            self.writes += 1
            raise RuntimeError("FICTIONAL_LOST_REPLY_AFTER_SAVE")

    with peers(lambda _: Backend()) as (host, _):
        backend = host.call("root", "storage")
        for _ in range(2):
            with pytest.raises(PluginError) as error:
                backend.add_account("personal", {"name": "Fictional"})
            assert error.value.code == "storage_write_unknown"
        assert backend.uncertain
        assert backend.accounts("personal") == 1


def test_plugin_environment_preserves_only_declared_configuration_and_password_exclusion(monkeypatch, tmp_path):
    from whab.plugins.process_worker import plugin_environment

    monkeypatch.setenv("AI_CREDENTIALS_DIR", str(tmp_path / "fictional-credentials"))
    monkeypatch.setenv("UNDECLARED_PRIVATE_SETTING", "FICTIONAL_PRIVATE_VALUE")
    assert "AI_CREDENTIALS_DIR" in plugin_environment("subscriptions-ai")
    assert "AI_CREDENTIALS_DIR" not in plugin_environment("web")
    assert "UNDECLARED_PRIVATE_SETTING" not in plugin_environment("subscriptions-ai")
    monkeypatch.setenv("WHAB_BUDGET_PASSWORD_ENV", "AI_CREDENTIALS_DIR")
    assert "AI_CREDENTIALS_DIR" not in plugin_environment("subscriptions-ai")


def test_failed_application_startup_reaps_all_discovery_children():
    result = fresh_python('''
import json, os, subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginError
original = subprocess.Popen
children = []
def capture(*args, **kwargs):
    process = original(*args, **kwargs)
    children.append(process)
    return process
subprocess.Popen = capture
with TemporaryDirectory(prefix="whab-process-startup-fault-") as temporary:
    try:
        create_app(settings=Settings(Path(temporary),5173,"sandbox","","","",False,"","",("web",),ui=""))
    except PluginError:
        pass
    else:
        raise AssertionError("Conflicting visual selection must fail")
    assert children and all(process.poll() is not None for process in children)
    print(json.dumps({"reaped":True}))
''')
    assert result == {"reaped": True}


@pytest.mark.parametrize("storage", ["sqlite", "json"])
def test_default_native_worker_recovers_lost_write_reply_without_code_upgrade_or_replay(storage):
    result = fresh_python('''
import asyncio, json
from pathlib import Path
from tempfile import TemporaryDirectory
from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginError
from whab.plugins.storage_worker import storage_error
async def scenario(home):
    app = create_app(settings=Settings(home,5173,"sandbox","","","",False,"","",(),storage=STORAGE,ui=""))
    async with app.router.lifespan_context(app):
        service = app.state.ledger_service
        worker = app.state.store.worker
        original = worker.call
        revision = service.revision
        payload = {"name":"Fictional acknowledged account","type":"depository","balance":123}
        def lost_reply(method, *args, **kwargs):
            result = original(method, *args, **kwargs)
            if method == "execute_operation":
                worker._unknown = True
                raise storage_error(worker.plugin_id, "storage_write_unknown")
            return result
        worker.call = lost_reply
        try:
            service.command("add_account","personal",payload,request_id="same-fictional-intent",expected_revision=revision)
        except PluginError:
            pass
        else:
            raise AssertionError("The lost acknowledgment must fence the budget")
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
        assert app.state.storage_upgrade.journal.snapshot()["active"] is None
        await app.state.storage_upgrade.recover()
        assert app.state.budget_runtime.status()["health"] == "ready"
        service.command("add_account","personal",payload,request_id="same-fictional-intent",expected_revision=revision)
        assert len(service.query("accounts","personal")["data"]) == 1
    print(json.dumps({"recovered":True}))
with TemporaryDirectory(prefix="whab-default-receipt-") as temporary:
    asyncio.run(scenario(Path(temporary)))
'''.replace("STORAGE", repr(storage)))
    assert result == {"recovered": True}


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


def test_legacy_training_models_remain_opaque_and_save_in_the_owning_child():
    model = object()

    class Engine:
        defaults = {}

        def predict(self, rows):
            return []

        def train(self, rows, parameters):
            yield {"progress": 50}
            return model

        def save(self, selected, name):
            assert selected is model
            return name

    with peers(lambda _: Engine()) as (host, _):
        engine = host.call("root", "engine")
        training = engine.train([], {})
        assert next(training) == {"progress": 50}
        with pytest.raises(StopIteration) as completed:
            next(training)
        with pytest.raises(AttributeError):
            completed.value.value.private_model_state
        assert engine.save(completed.value.value, "Fictional model") == "Fictional model"


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
        identifier = service.query("accounts", "personal")["data"][0]["id"]
        service.command("add_transaction", "personal", {"accountId":identifier,"date":"2026-11-01",
                        "merchant":"Fictional membership","amount":1200,"categoryId":None},
                        request_id="fictional-payment", expected_revision=service.revision)
        feature = {"pluginId":"subscriptions","action":"report","parameters":{"month":"2026-11","currency":"USD"}}
        report = service.query("feature", "personal", parameters=feature)
        row = report["data"]["transactions"][0]
        label = {"pluginId":"subscriptions","action":"set_label","parameters":{
            "transactionId":row["id"],"fingerprint":row["fingerprint"],"isSubscription":True,"cadence":"monthly"}}
        saved = service.command("feature", "personal", label, request_id="fictional-label", expected_revision=report["revision"])
        assert saved["data"]["saved"]
        assert service.command("feature", "personal", label, request_id="fictional-label", expected_revision=report["revision"]) == saved
        assert service.query("feature", "personal", parameters=feature)["data"]["transactions"][0]["source"] == "manual"
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
        assert reopened.state.ledger_service.query("feature", "personal", parameters=feature)["data"]["transactions"][0]["source"] == "manual"
    print(json.dumps({"storage":STORAGE,"saved":True}))
'''.replace("STORAGE", repr(storage)))
    assert result == {"storage": storage, "saved": True}
