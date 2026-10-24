"""Fictional remote authority lifecycle keeps transport, scope and unknown outcomes."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from whab_plugin_client.plugin import plugin as client_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    LedgerService,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    RemoteLedgerClient,
)
from whab.plugins.remote_transport import RemoteStore
from whab.services.ledger import RequestConflict, RevisionConflict

TOKEN = "fictional-client-" + "a" * 32
URL = "https://fictional-authority.example"
BUDGET = "fictional-budget"
PAYLOAD = {"name": "Fictional cash", "type": "depository", "balance": 125, "currency": "USD", "onBudget": True, "subtype": None}


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_URL", URL)
    monkeypatch.setenv("CLIENT_TOKEN", TOKEN)
    monkeypatch.setenv("CLIENT_BUDGET_ID", BUDGET)
    return Settings(tmp_path, 5173, "sandbox", "", "", "fictional-runtime-key", False, "", "", (), storage="client", ui="")


class Authority:
    def __init__(self, store):
        self.service = LedgerService(store, budget_id=BUDGET)
        self.commands = []
        self.drop = False
        self.entered = self.finish = None

    def __call__(self, request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        try:
            if request.method == "GET":
                operation = request.url.path.rsplit("/", 1)[-1]
                result = self.service.query(operation, request.url.params["workspace"], request.url.params.get("month"))
            else:
                body = json.loads(request.content)
                self.commands.append(body)
                if self.entered:
                    self.entered.set()
                    assert self.finish.wait(5)
                result = self.service.command(body["operation"], body["workspace"], body["payload"],
                    request_id=body["request_id"], expected_revision=body["expected_revision"])
                if self.drop:
                    self.drop = False
                    raise httpx.ReadTimeout("Fictional lost remote response")
            return httpx.Response(200, json={"budgetId": BUDGET, **result})
        except (RevisionConflict, RequestConflict):
            return httpx.Response(409, json={"error": "Fictional original revision no longer available"})


def application(configuration, authority):
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)))
    return app, remote


def test_pause_reenable_and_failed_binding_preparation_keep_same_host_objects(configuration, store, monkeypatch):
    authority = Authority(store)
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            ledger, tracked, transport = app.state.ledger_service, app.state.tracked_store, remote._client
            original = http.get("/api/storage/remote/status").json()
            assert original["enabled"] and not original["receipt_lookup"]
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            http.portal.call(app.state.remote_client.select, "client", False, original["revision"])
            paused = http.get("/api/storage/remote/status").json()
            assert not paused["enabled"]
            assert http.get("/api/workspace?workspace=personal").status_code == 400
            assert http.get("/api/storage/remote/status", headers={"Host": "foreign.example"}).status_code == 403
            assert http.get("http://127.0.0.1:5173/api/storage/remote/status").status_code == 400
            http.portal.call(app.state.remote_client.select, "client", True, paused["revision"])
            before = http.get("/api/storage/remote/status").json()
            monkeypatch.setenv("CLIENT_URL", "https://different-authority.example")
            with pytest.raises(PluginError):
                http.portal.call(app.state.remote_client.select, "client", True, before["revision"])
            assert http.get("/api/storage/remote/status").json()["revision"] == before["revision"]
            assert app.state.ledger_service is ledger and app.state.tracked_store is tracked
            assert tracked.store is remote and remote._client is transport
    finally:
        remote.close()


def test_unknown_remote_write_survives_client_restart_and_explicit_finish_uses_original_id_revision_and_body(configuration, store):
    authority = Authority(store)
    authority.drop = True
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            assert len(authority.commands) == 1 and len(store.accounts("personal")) == 1
            assert http.get("/api/storage/remote/status").json()["unknown_outcome"]
            raw = (configuration.home / "remote-client/remote-client.json").read_bytes()
            for private in (TOKEN, URL, BUDGET, "Fictional cash"):
                assert private.encode() not in raw
    finally:
        remote.close()
    app, reopened = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            assert len(authority.commands) == 1
            changed = http.post("/api/storage/remote/finish", json={"operation": "add_account", "workspace": "personal",
                "payload": {**PAYLOAD, "balance": 999}})
            assert changed.status_code == 400 and len(authority.commands) == 1
            body = {"operation": "add_account", "workspace": "personal", "payload": PAYLOAD}
            assert http.post("/api/storage/remote/finish", json=body).json() == {"complete": True, "acknowledged": True}
            assert authority.commands[0] == authority.commands[1]
            assert len(store.accounts("personal")) == 1
            assert http.post("/api/storage/remote/finish", json=body).json()["complete"]
            assert len(authority.commands) == 2
            assert not http.get("/api/storage/remote/status").json()["unknown_outcome"]
    finally:
        reopened.close()


def test_server_restart_409_cannot_clear_unknown_or_refresh_original_revision(configuration, store):
    authority = Authority(store)
    authority.drop = True
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            authority.service = LedgerService(store, budget_id=BUDGET)
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            body = {"operation": "add_account", "workspace": "personal", "payload": PAYLOAD}
            assert http.post("/api/storage/remote/finish", json=body).status_code == 400
            assert http.get("/api/storage/remote/status").json()["unknown_outcome"]
            assert authority.commands[0] == authority.commands[1]
            assert len(store.accounts("personal")) == 1
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            assert len(authority.commands) == 2
    finally:
        remote.close()


def test_admitted_financial_write_drains_before_disable_and_control_revision_ignores_outcome_bookkeeping(configuration, store):
    authority = Authority(store)
    authority.entered, authority.finish = Event(), Event()
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http, ThreadPoolExecutor() as pool:
            original = http.get("/api/storage/remote/status").json()
            write = pool.submit(http.post, "/api/accounts", json={"workspace": "personal", **PAYLOAD})
            assert authority.entered.wait(5)
            scheduled = http.post("/api/storage/remote/selection", json={"pluginId": "client", "enabled": False,
                "expectedRevision": original["revision"]})
            assert scheduled.status_code == 202
            http.portal.call(app.state.budget_runtime.wait_for_phase, "draining")
            assert not write.done()
            authority.finish.set()
            assert write.result(5).status_code == 200
            http.portal.call(app.state.budget_runtime.wait_for_phase, "active")
            assert not http.get("/api/storage/remote/status").json()["enabled"]
            assert len(store.accounts("personal")) == 1
    finally:
        authority.finish.set()
        remote.close()


def test_legacy_callable_storage_remains_restart_required_without_live_controls(configuration, store):
    authority = Authority(store)
    legacy = replace(client_plugin, storage=lambda context: RemoteStore(URL, TOKEN, BUDGET),
                     contract=PluginContract(authority="remote"))
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    try:
        app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((legacy,)))
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/storage/remote/status").status_code == 404
            assert app.state.remote_client is None
    finally:
        remote.close()


def test_same_binding_replacement_changes_plugin_identity_without_losing_unknown_command(configuration, store, monkeypatch):
    from types import SimpleNamespace

    other = Plugin("other-client", "Fictional replacement", storage=RemoteLedgerClient(
        url_setting="OTHER_CLIENT_URL", token_setting="OTHER_CLIENT_TOKEN", budget_setting="OTHER_CLIENT_BUDGET_ID"),
        contract=PluginContract(authority="remote", lifecycle="live"))
    monkeypatch.setenv("OTHER_CLIENT_URL", URL)
    monkeypatch.setenv("OTHER_CLIENT_TOKEN", TOKEN)
    monkeypatch.setenv("OTHER_CLIENT_BUDGET_ID", BUDGET)
    monkeypatch.setattr("whab.budgets.remote_runtime.installed_entrypoints", lambda: (
        SimpleNamespace(name=other.id, group="whab.plugins", load=lambda: other),))
    authority = Authority(store)
    authority.drop = True
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            original = json.loads(json.dumps(authority.commands[0]))
            ledger, transport = app.state.ledger_service, remote._client
            current = http.get("/api/storage/remote/status").json()
            http.portal.call(app.state.remote_client.select, other.id, True, current["revision"])
            assert http.get("/api/storage/remote/status").json()["plugin"] == other.id
            assert app.state.ledger_service is ledger and app.state.tracked_store.store is remote and remote._client is transport
            assert http.post("/api/storage/remote/finish", json={"operation": "add_account", "workspace": "personal", "payload": PAYLOAD}).json()["complete"]
            assert authority.commands[-1] == original and len(store.accounts("personal")) == 1
    finally:
        remote.close()
    app, reopened = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/storage/remote/status").json()["plugin"] == other.id
            assert http.get("/api/workspace?workspace=personal").status_code == 200
    finally:
        reopened.close()


@pytest.mark.parametrize("phase,chosen", [("before_replace", "active"), ("after_replace", "candidate"), ("after_directory_fsync", "candidate")])
def test_selection_publication_fault_gates_financial_admission_and_recovers_only_exact_durable_choice(configuration, store, phase, chosen):
    authority = Authority(store)
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            current = http.get("/api/storage/remote/status").json()
            faults = []
            def fault(boundary):
                if boundary == phase and not faults:
                    faults.append(True)
                    raise OSError("Fictional lost selection publication response")
            app.state.remote_client.owner.store.fault = fault
            with pytest.raises(PluginError) as caught:
                http.portal.call(app.state.remote_client.select, "client", False, current["revision"])
            assert caught.value.code == "commit_uncertain"
            assert app.state.budget_runtime.status()["admission"] == "gated"
            assert http.get("/api/storage/remote/status").status_code == 200
            assert http.get("/api/storage/remote/status", headers={"Origin": "https://foreign.example"}).status_code == 403
            other = "candidate" if chosen == "active" else "active"
            assert http.post("/api/plugins/management/recover", json={"authority": other}).status_code == 409
            assert http.post("/api/plugins/management/recover", json={"authority": chosen}).status_code == 200
            status = http.get("/api/storage/remote/status").json()
            assert status["enabled"] is (chosen == "active")
            assert app.state.budget_runtime.status()["admission"] == "open"
            latest = status["revision"]
            if chosen == "candidate":
                http.portal.call(app.state.remote_client.select, "client", True, latest)
                before = http.get("/api/storage/remote/status").json()
                http.portal.call(app.state.remote_client.select, "client", False, current["revision"])
                after = http.get("/api/storage/remote/status").json()
                assert before["revision"] == after["revision"] and after["enabled"]
    finally:
        remote.close()


def test_intent_failure_sends_no_http_and_captured_ack_failure_finish_never_resends(configuration, store):
    authority = Authority(store)
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            owner = app.state.remote_client.owner
            def fail_before_replace(phase):
                if phase == "before_replace":
                    raise OSError("Fictional unavailable disk")
            owner.store.fault = fail_before_replace
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            assert authority.commands == [] and store.accounts("personal") == []
            owner.store.fault = None
            commits = []
            def fail_ack(phase):
                if phase == "after_directory_fsync":
                    commits.append(True)
                elif phase == "before_replace" and commits:
                    raise OSError("Fictional acknowledgment failure")
            owner.store.fault = fail_ack
            assert http.post("/api/accounts", json={"workspace": "personal", **PAYLOAD}).status_code == 400
            assert len(authority.commands) == 1 and len(store.accounts("personal")) == 1
            assert http.get("/api/storage/remote/status").json()["unknown_outcome"]
            owner.store.fault = None
            body = {"operation": "add_account", "workspace": "personal", "payload": PAYLOAD}
            for payload in ({**PAYLOAD, "balance": True}, {**PAYLOAD, "private": "invalid"}):
                assert http.post("/api/storage/remote/finish", json={**body, "payload": payload}).status_code == 400
                assert len(authority.commands) == 1
            assert http.post("/api/storage/remote/finish", json=body).json()["complete"]
            assert len(authority.commands) == 1
            assert not http.get("/api/storage/remote/status").json()["unknown_outcome"]
    finally:
        remote.close()


def test_portable_replacement_preserves_original_connection_unknown_namespace_and_reopens_selected_authority(configuration, store, monkeypatch):
    from types import SimpleNamespace

    from whab.budgets.container import decode_container
    from whab.budgets.session import BudgetSession

    other = Plugin("other-client", "Fictional replacement", storage=RemoteLedgerClient(
        url_setting="OTHER_CLIENT_URL", token_setting="OTHER_CLIENT_TOKEN", budget_setting="OTHER_CLIENT_BUDGET_ID"),
        contract=PluginContract(authority="remote", lifecycle="live"))
    for name, value in {"OTHER_CLIENT_URL": URL, "OTHER_CLIENT_TOKEN": TOKEN, "OTHER_CLIENT_BUDGET_ID": BUDGET}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("whab.budgets.remote_runtime.installed_entrypoints", lambda: (
        SimpleNamespace(name=other.id, group="whab.plugins", load=lambda: other),))
    session = BudgetSession.create(configuration.home, "sandbox", "Fictional reference", ("unavailable-peer",),
                                   storage="client", path=configuration.home / "reference.whab")
    session.save_plugin_data("unavailable-peer", settings={"example": True}, state={"counter": 7})
    session.write_file("unavailable-peer", "opaque.bin", b"fictional opaque\x00\xff")
    authority = Authority(store)
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)), budget_session=session)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            connection = session.files["plugins/client/connection.json"]
            current = http.get("/api/storage/remote/status").json()
            http.portal.call(app.state.remote_client.select, other.id, True, current["revision"])
            saved = decode_container(session.path.read_bytes())
            assert saved.manifest["storage"] == other.id
            assert saved.files["plugins/client/connection.json"] == connection
            assert saved.files["plugins/other-client/connection.json"] == connection
            assert saved.files["plugins/unavailable-peer/opaque.bin"] == b"fictional opaque\x00\xff"
    finally:
        remote.close()
    reopened = BudgetSession.open(configuration.home, "sandbox", session.path)
    assert reopened.manifest["storage"] == other.id
    assert reopened.plugin_record("unavailable-peer")["state"] == {"counter": 7}
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)), budget_session=reopened)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/storage/remote/status").json()["plugin"] == other.id
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            assert reopened.files["plugins/unavailable-peer/opaque.bin"] == b"fictional opaque\x00\xff"
    finally:
        remote.close()


def test_remote_recovery_cannot_release_another_familys_uncertain_transition(configuration, store):
    from whab.budgets.runtime import RuntimeCandidate, RuntimeTransitionError

    authority = Authority(store)
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            runtime = app.state.budget_runtime
            async def unrelated_transition():
                async def prepare(preparation):
                    return RuntimeCandidate(preparation.active.services, preparation.active.resources)
                def fail(*_):
                    raise OSError("Fictional other-family publication failure")
                await runtime.transition(prepare, commit=fail, plugin_id="fictional-report")
            with pytest.raises(RuntimeTransitionError):
                http.portal.call(unrelated_transition)
            assert runtime.status()["admission"] == "gated"
            with pytest.raises(PluginError) as error:
                http.portal.call(app.state.remote_client.recover, "active")
            assert error.value.code == "remote_recovery_required"
            assert runtime.status()["admission"] == "gated"
    finally:
        remote.close()


def test_durable_remote_replacement_reopens_after_metadata_callback_dies_before_container_checkpoint(configuration, store, monkeypatch):
    from types import SimpleNamespace

    from whab.budgets.runtime import RuntimeTransitionError
    from whab.budgets.session import BudgetSession

    other = Plugin("other-client", "Fictional replacement", storage=RemoteLedgerClient(
        url_setting="OTHER_CLIENT_URL", token_setting="OTHER_CLIENT_TOKEN", budget_setting="OTHER_CLIENT_BUDGET_ID"),
        contract=PluginContract(authority="remote", lifecycle="live"))
    for name, value in {"OTHER_CLIENT_URL": URL, "OTHER_CLIENT_TOKEN": TOKEN, "OTHER_CLIENT_BUDGET_ID": BUDGET}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("whab.budgets.remote_runtime.installed_entrypoints", lambda: (
        SimpleNamespace(name=other.id, group="whab.plugins", load=lambda: other),))
    session = BudgetSession.create(configuration.home, "sandbox", "Fictional lost reply", ("unavailable-peer",),
                                   storage="client", path=configuration.home / "old-reference.whab")
    session.write_file("unavailable-peer", "opaque.bin", b"fictional preserved")
    authority = Authority(store)
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)), budget_session=session)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            before = session.path.read_bytes()
            def died(*_):
                raise OSError("Fictional post-decision failure")
            app.state.remote_client.selected = died
            current = http.get("/api/storage/remote/status").json()
            with pytest.raises(RuntimeTransitionError):
                http.portal.call(app.state.remote_client.select, other.id, True, current["revision"])
            assert session.path.read_bytes() == before
    finally:
        remote.close()
    reopened = BudgetSession.open(configuration.home, "sandbox", session.path)
    assert reopened.manifest["storage"] == other.id
    assert reopened.files["plugins/other-client/connection.json"] == reopened.files["plugins/client/connection.json"]
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    try:
        app = create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)), budget_session=reopened)
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/storage/remote/status").json()["plugin"] == other.id
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            assert reopened.files["plugins/unavailable-peer/opaque.bin"] == b"fictional preserved"
    finally:
        remote.close()


def test_conflicting_default_local_and_remote_authority_decisions_refuse_before_worker_or_financial_dispatch(configuration, store, monkeypatch):
    authority = Authority(store)
    app, remote = application(configuration, authority)
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173"):
            pass
    finally:
        remote.close()
    monkeypatch.setattr("whab.app.saved_storage_selection", lambda *args: (object(), {}, {}))
    monkeypatch.setattr("whab.budgets.remote_runtime._prepare_worker", lambda *args, **kwargs: pytest.fail("Conflicting authority executed a worker"))
    remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    try:
        with pytest.raises(PluginError) as error:
            create_app(store=remote, settings=configuration, plugins=PluginRegistry((client_plugin,)))
        assert error.value.code == "remote_recovery_required" and authority.commands == []
    finally:
        remote.close()
