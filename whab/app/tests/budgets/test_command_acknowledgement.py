"""Native commit receipts survive a failed portable save and exact recovery."""

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.plugins.test_storage_proxy_outcomes import FaultWorker
from whab import hosting
from whab.app import create_app
from whab.budgets import session as module
from whab.budgets.container import decode_container
from whab.budgets.session import BudgetSession, TrackedStorage
from whab.config import Settings
from whab.domain.errors import StoreError
from whab.plugins import (
    InterfaceContext,
    PluginRegistry,
    RemoteLedgerInterface,
    StorageContext,
    load_plugins,
)
from whab.plugins.storage import open_storage
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker_codec import METHODS
from whab.services.ledger import (
    CommandSavePending,
    LedgerService,
    RevisionConflict,
)
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


class NativeWorker(FaultWorker):
    """Synchronous transport around real native storage and durable intents."""

    def open(self, context):
        evidence = super().open(context)
        self.methods = tuple(name for name in METHODS if callable(getattr(self.backend, name, None)))
        return evidence


@pytest.mark.parametrize("kind", ["sqlite", "json"])
@pytest.mark.parametrize("failure", ["export", "primary"])
@pytest.mark.parametrize("restart", [False, True])
def test_pending_save_retries_checkpoint_without_native_reexecution(tmp_path, monkeypatch, kind, failure, restart):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", (),
        path=tmp_path / "fictional.whab", storage=kind)
    plugin = load_plugins((kind,)).storage_plugin(kind)
    session.storage_payload_paths = plugin.storage_payload_paths
    context = StorageContext(session.storage_home, "sandbox", {})

    def open_proxy():
        return StorageWorkerProxy(NativeWorker(plugin, "healthy"), context, tmp_path / "intents")

    proxy = open_proxy()
    try:
        session.bind(proxy)
        saved_file = session.path.read_bytes()
        tracked = TrackedStorage(proxy, session)
        service = LedgerService(tracked, lock=tracked.lock, confirm_saved=tracked.confirm_saved)
        tracked.on_mutation = service.invalidate_revision
        original = service.revision
        before = proxy.commit_evidence()
        checkpoint = session.checkpoint
        checkpoints = []

        def counted_checkpoint():
            checkpoints.append(True)
            checkpoint()

        monkeypatch.setattr(session, "checkpoint", counted_checkpoint)
        original_bundle, original_write = session.bundle, module.atomic_write
        failing = [True]

        def bundle(*args, **kwargs):
            if failing[0] and failure == "export":
                raise OSError("Fictional export fault")
            return original_bundle(*args, **kwargs)

        def write(path, raw):
            if failing[0] and failure == "primary" and path == session.path:
                raise OSError("Fictional primary file fault")
            return original_write(path, raw)

        monkeypatch.setattr(session, "bundle", bundle)
        monkeypatch.setattr(module, "atomic_write", write)
        arguments = ("add_account", "personal", {"name": "Committed once", "type": "other", "balance": 123})
        intent = {"request_id": "original-request", "expected_revision": original}
        with pytest.raises(CommandSavePending, match="Command committed to native storage"):
            service.command(*arguments, **intent)
        committed = proxy.commit_evidence()
        assert committed.generation == before.generation + 1
        assert session.status == "pending" and session.path.read_bytes() == saved_file
        assert len(checkpoints) == 1
        assert proxy._scope is None and proxy._history_context is None
        native = proxy.export_budget()
        assert len(native["accounts"]) == 1
        assert len(native["history"]["entries"]) == 1
        response = service._receipts[intent["request_id"]][1]

        if restart:
            proxy.close()
            proxy = open_proxy()
            session.store = proxy
            tracked = TrackedStorage(proxy, session)
            service = LedgerService(tracked, lock=tracked.lock, confirm_saved=tracked.confirm_saved)
            tracked.on_mutation = service.invalidate_revision
            assert not service._receipts

        # A still-failing exact replay is never mislabeled a 400/409 refusal.
        with pytest.raises(CommandSavePending):
            service.command(*arguments, **intent)
        assert len(checkpoints) == 2 and proxy.commit_evidence() == committed
        with pytest.raises(StoreError, match="[Rr]equest ID"):
            service.command(*arguments, **{**intent, "expected_revision": "changed-intent"})
        with pytest.raises(RevisionConflict):
            service.command(*arguments, **{**intent, "request_id": "fresh-request"})
        assert len(checkpoints) == 2

        failing[0] = False
        assert service.command(*arguments, **intent) == response
        assert session.status == "saved" and len(checkpoints) == 3
        restored = open_storage(plugin, StorageContext(tmp_path / "independent-restore", "sandbox", {}))
        try:
            prefix = f"plugins/{kind}/"
            files = decode_container(session.path.read_bytes()).files
            restored.restore_budget_files("personal", {
                name.removeprefix(prefix): raw for name, raw in files.items() if name.startswith(prefix)})
            assert restored.export_budget() == native
        finally:
            restored.close()
        assert proxy.commit_evidence() == committed
        assert service.command(*arguments, **intent) == response
        assert len(checkpoints) == 3 and proxy.export_budget() == native
    finally:
        proxy.close()


def test_acknowledgement_waits_for_durable_and_history_context_exit(store, monkeypatch):
    stages = []
    history_scope = store.history_scope

    @contextmanager
    def durable(*args):
        stages.append("enter")
        yield
        stages.append("committed")

    @contextmanager
    def history(metadata):
        stages.append("history-enter")
        with history_scope(metadata):
            yield
        stages.append("history-exit")

    def confirm(replayed):
        assert stages == ["enter", "history-enter", "history-exit", "committed"]
        assert not replayed
        return True

    monkeypatch.setattr(store, "durable_command_scope", durable, raising=False)
    monkeypatch.setattr(store, "history_scope", history)
    service = LedgerService(store, confirm_saved=confirm)
    service.add_account("personal", {"name": "Fictional", "type": "other", "balance": 0})


def test_acknowledgement_without_file_and_during_namespace_materialization(store, tmp_path):
    detached = TrackedStorage(store)
    assert detached.confirm_saved(False) and detached.confirm_saved(True)
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", ())
    session.bind(store)
    tracked = TrackedStorage(store, session)
    with session.namespace_materialization():
        tracked.add_account("personal", {"name": "Staged namespace", "type": "other", "balance": 0})
        assert not tracked.confirm_saved(False)
        assert not tracked.confirm_saved(True)
    assert session.status == "saved"
    assert tracked.confirm_saved(False) and tracked.confirm_saved(True)


@pytest.mark.parametrize("surface", ["local", "remote", "web"])
def test_public_routes_never_acknowledge_pending_file_as_success(store, tmp_path, monkeypatch, surface):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", (), path=tmp_path / "fictional.whab")
    settings = Settings(session.home, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()), budget_session=session)
    service = app.state.ledger_service
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    token = "fictional-remote-token-" * 3
    binding = BoundRemoteInterface(RemoteLedgerInterface(), InterfaceContext(
        service, {"SERVER_TOKEN": token}, "sandbox", session.id))
    request_app = app
    if surface == "remote":
        request_app = FastAPI()
        request_app.include_router(create_remote_router(lambda: binding))
    saved_file = session.path.read_bytes()

    def fail_export(*args, **kwargs):
        raise OSError("Fictional private failure")

    monkeypatch.setattr(session, "bundle", fail_export)
    with TestClient(request_app, base_url="https://localhost:5173") as client:
        body = {"operation": "add_account", "workspace": "personal", "payload": {
            "name": "Committed", "type": "other", "balance": 0},
            "request_id": "same-intent", "expected_revision": service.revision}
        if surface == "local":
            client.headers["Authorization"] = "Bearer " + owner.token
            url = f"/budgets/{session.id}/api/local/v1/commands"
        elif surface == "remote":
            client.headers["Authorization"] = "Bearer " + token
            url = f"/remote/v1/budgets/{session.id}/commands"
        else:
            url = f"/budgets/{session.id}/api/accounts"
            body = {"workspace": "personal", **body["payload"]}
        response = client.post(url, json=body)
        assert response.status_code == 503, response.text
        assert "Command committed to native storage" in response.text
        assert "Fictional private failure" not in response.text
        assert len(store.accounts("personal")) == 1
        assert session.status == "pending" and session.path.read_bytes() == saved_file
        if surface != "web":
            replay = client.post(url, json=body)
            assert replay.status_code == 503
            assert len(store.accounts("personal")) == 1
